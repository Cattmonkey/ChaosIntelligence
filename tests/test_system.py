import concurrent.futures
import copy
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from chaos.examples import initial_graph
from chaos.graph import validate, apply_patch, GraphError
from chaos.language import Evaluator, LanguageError, serializable
from chaos.runtime import Engine, Context, SAFE_TOOLS
from chaos.server import make_server
from chaos.store import Store, Conflict


def run(source, **kwargs):
    return Evaluator(**kwargs).run(source)


def simple_graph(source='input'):
    return {'nodes': [{'id': 'i', 'kind': 'input', 'config': {}}, {'id': 'f', 'kind': 'function', 'config': {'source': source}}, {'id': 'o', 'kind': 'output', 'config': {}}], 'edges': [{'id': 'if', 'source': 'i', 'target': 'f'}, {'id': 'fo', 'source': 'f', 'target': 'o'}]}


class LanguageTests(unittest.TestCase):
    def test_precedence_and_immutable_records(self):
        self.assertEqual(run('let a = {x: [1, 2, 3]}; let b=set(a,"z",4); [1+2*3,a.x[1],get(a,"z"),b.z]'), [7, 2, None, 4])

    def test_lexical_closure(self):
        self.assertEqual(run('let x=1; fn outer(){let f=fn(){x}; let x=2; f()}; outer()'), 1)
        self.assertEqual(run('fn add(x){ fn(y){x+y} }; add(3)(4)'), 7)

    def test_binding_is_immutable(self):
        with self.assertRaises(LanguageError):
            run('let x=1; let x=2; x')

    def test_recursion(self):
        self.assertEqual(run('fn fact(n:Int)->Int {if n<=1 {1} else {n*fact(n-1)}} fact(8)'), 40320)

    def test_tail_recursion(self):
        self.assertEqual(run('fn sumTo(n,a){ if n==0 {a} else {sumTo(n-1,a+n)} };sumTo(1500,0)', step_limit=100000), 1125750)

    def test_short_circuit_tail_recursion(self):
        self.assertTrue(run('fn done(n)->Bool { n==0 || done(n-1) }; done(1500)', step_limit=100000))

    def test_short_circuit_no_effect(self):
        calls=[]
        self.assertEqual(run('false && tool("x",0); true || tool("x",0); if true {42} else {tool("x",0)}', tool_handler=lambda *a:calls.append(a)),42)
        self.assertEqual(calls,[])

    def test_higher_order_and_pipeline(self):
        self.assertEqual(run('let inc=fn(x){x+1}; let double=fn(x){x*2}; [3 |> compose(inc,double), fold(fn(a,b){a+b},0,filter(fn(x){x>2},map(inc,[1,2,3]))), partial(fn(a,b){a+b},10)(5)]'),[7,7,15])

    def test_contracts(self):
        for source in ['fn x(a:Int){a};x(true)', 'fn x()->String {1};x()', 'if 1 {2}else{3}', 'fn x(a,a){a}', 'fn x()->Missing {1}', 'fn f()->String{g()}; fn g(){1}; f()']:
            with self.subTest(source=source), self.assertRaises(LanguageError):run(source)

    def test_arity_and_syntax(self):
        for source in ['fn f(x){x}; f()', 'let a = @', 'let a={x:1,x:2}', '[1, 2', 'false + 1']:
            with self.subTest(source=source),self.assertRaises(LanguageError):run(source)

    def test_budget(self):
        with self.assertRaisesRegex(LanguageError,'步数'):run('fn forever(){forever()};forever()',step_limit=100)
        with self.assertRaisesRegex(LanguageError,'取消'):run('1+1',cancelled=lambda:True)

    def test_resource_guards(self):
        for source in ['number("Infinity")','number("NaN")','append(range(10000), 1)','fn tree(n){if n==0{0}else{let x=tree(n-1);[x,x]}};str(tree(20))','join("", map(fn(x){"a" + "b"},range(10000))) |> fn(x){ join(x, range(100)) }']:
            with self.subTest(source=source),self.assertRaises(LanguageError):run(source,step_limit=200000)

    def test_single_quote_escaping(self):
        self.assertEqual(run("'a\\\"b'"), 'a"b')

    def test_callable_cross_context_is_rejected(self):
        a=Evaluator(tool_handler=lambda *args:42)
        with self.assertRaisesRegex(LanguageError,'跨执行上下文'):
            Evaluator().run('injected("x",null)',{'injected':a.env.get('tool')})

    def test_explicit_effects(self):
        with self.assertRaisesRegex(LanguageError,'工具效果'):run('tool("text.stats","x")')
        self.assertEqual(run('let f=partial(tool,"text.stats"); f("hi")',tool_handler=lambda name,value:len(value)),2)

    def test_machine_encoding(self):
        # Two-counter machine: move A into B via conditional decrement/increment.
        source='fn machine(pc,a,b){if pc==0 {if a==0 {machine(2,a,b)} else {machine(1,a-1,b)}} else {if pc==1 {machine(0,a,b+1)} else {{a:a,b:b}}}};machine(0,37,5)'
        self.assertEqual(run(source),{'a':0,'b':42})


class GraphTests(unittest.TestCase):
    def test_certificate(self):
        cert=validate(initial_graph(),True)
        self.assertTrue(cert['closed']);self.assertTrue(all(cert['checks'].values()))

    def test_invalid_cycle_and_ports(self):
        graph=simple_graph();graph['edges'].append({'id':'cycle','source':'f','target':'f','targetPort':'loop'})
        with self.assertRaises(GraphError):validate(graph)
        graph=simple_graph();graph['edges'].append({'id':'duplicate','source':'i','target':'f'})
        with self.assertRaises(GraphError):validate(graph)

    def test_uncovered_draft(self):
        graph=simple_graph();graph['nodes'].append({'id':'orphan','kind':'function','config':{}})
        self.assertFalse(validate(graph)['closed'])
        with self.assertRaises(GraphError):validate(graph,True)

    def test_hierarchy_and_platform_data_edges(self):
        graph=initial_graph();graph['nodes'][0]['parentId']='analyst'
        with self.assertRaises(GraphError):validate(graph)
        graph=initial_graph();graph['edges'].append({'id':'bad','source':'platform','target':'analyst','targetPort':'x'})
        with self.assertRaises(GraphError):validate(graph)

    def test_platform_permissions(self):
        graph=initial_graph()
        with self.assertRaises(GraphError):apply_patch(graph,[{'op':'remove_node','id':'normalize'}],'analyst')
        with self.assertRaises(GraphError):apply_patch(graph,[{'op':'update_node','id':'analyst','changes':{'config':{'level':'platform'}}}],'platform')

    def test_cross_scope_implicit_edge_removal(self):
        graph=simple_graph();graph['nodes'] += [{'id':'p1','kind':'agent','config':{'level':'platform','tools':['graph.patch']}},{'id':'p2','kind':'agent','config':{'level':'platform','tools':['graph.patch']}}]
        for n in graph['nodes'][:3]:n['parentId']='p1' if n['id']!='o' else 'p2'
        graph['edges'].append({'id':'io','source':'i','target':'o','targetPort':'second'})
        with self.assertRaises(GraphError):apply_patch(graph,[{'op':'remove_node','id':'f'}],'p1')


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(':memory:');self.engine=Engine(self.store)

    def tearDown(self):
        for cancel,_ in list(self.engine.active.values()):cancel.set()
        until=time.monotonic()+3
        while self.engine.active and time.monotonic()<until:time.sleep(.01)
        self.store.db.close()

    def wait(self,run):
        for _ in range(300):
            result=self.store.run(run['id'])
            if result['status'] not in ('queued','running'):return result
            time.sleep(.01)
        self.fail('运行未及时终止')

    def set_graph(self,graph,source='input'):
        p=self.store.project();return self.store.save(p['id'],p['version'],graph,source,p['name'],from_view='graph')

    def test_end_to_end_auto_topology(self):
        result=self.wait(self.engine.start('workspace',1,'hello world'))
        self.assertEqual(result['status'],'completed',result['error'])
        self.assertEqual(result['version'],2);self.assertEqual(len(result['snapshot']['graph']['nodes']),8)
        self.assertIn('review',result['output']);self.assertEqual(self.engine.optimize('workspace',2)['changed'],False)

    def test_source_program(self):
        p=self.store.project()
        p=self.store.save('workspace',p['version'],p['graph'],p['legacySource'],p['name'],from_view='source')
        result=self.wait(self.engine.start('workspace',p['version'],' example ',mode='source'))
        self.assertEqual(result['status'],'completed',result['error'])
        self.assertEqual(result['output']['factorials'],[1,2,6,24,120,720]);self.assertEqual(result['output']['composition'],42)

    def test_falsey_values(self):
        for source,expected in [('false',False),('0',0),('null',None),('[]',[])]:
            p=self.set_graph(simple_graph(source));result=self.wait(self.engine.start('workspace',p['version'],'x',auto=False));self.assertEqual(result['output'],expected)

    def test_function_node_purity(self):
        p=self.set_graph(simple_graph('agent x {provider:"model",tools:[]}; x(input)'))
        with patch.object(Context,'model_agent',return_value=42) as model:
            result=self.wait(self.engine.start('workspace',p['version'],'x',auto=False))
        self.assertEqual(result['status'],'failed');model.assert_not_called()

    def test_capability_escalation_rejected(self):
        ctx=Context(self.engine,'test',self.store.project(),threading.Event())
        with self.assertRaises(LanguageError):ctx.agent('x',{'tools':['memory.put']},'x',set())

    def test_atomic_patch_rollback_and_conflict(self):
        before=self.store.project();events=self.store.events('workspace')
        with self.assertRaises(GraphError):self.store.patch('workspace',1,[{'op':'remove_node','id':'output'}],'platform','bad')
        self.assertEqual(self.store.project(),before);self.assertEqual(self.store.events('workspace'),events)
        self.engine.optimize('workspace',1)
        with self.assertRaises(Conflict):self.engine.optimize('workspace',1)

    def test_snapshot_isolation(self):
        start=threading.Event();release=threading.Event();original=self.engine.execute_node
        def slow(ctx,node,value):
            if node['kind']=='input':start.set();release.wait(2)
            return original(ctx,node,value)
        with patch.object(self.engine,'execute_node',side_effect=slow):
            r=self.engine.start('workspace',1,'x',auto=False);self.assertTrue(start.wait(1));self.engine.optimize('workspace',1);release.set();result=self.wait(r)
        self.assertEqual(result['version'],1);self.assertEqual(len(result['snapshot']['graph']['nodes']),7);self.assertEqual(self.store.project()['version'],2)

    def test_parallel_join_and_failure(self):
        graph={'nodes':[{'id':n,'kind':'input' if n=='i' else 'output' if n=='o' else 'function','config':{'source':'input'}} for n in ['i','b','c','o']], 'edges':[{'id':'ib','source':'i','target':'b'},{'id':'ic','source':'i','target':'c'},{'id':'bo','source':'b','target':'o','targetPort':'b'},{'id':'co','source':'c','target':'o','targetPort':'c'}]}
        for failing in (False,True):
            p=self.set_graph(graph);barrier=threading.Barrier(2);completed=[];original=self.engine.execute_node
            def execute(ctx,node,value):
                if node['id'] in ('b','c'):
                    barrier.wait(timeout=2)
                    if failing and node['id']=='b':raise LanguageError('branch failed')
                    completed.append(node['id'])
                return original(ctx,node,value)
            with patch.object(self.engine,'execute_node',side_effect=execute):result=self.wait(self.engine.start('workspace',p['version'],42,auto=False))
            self.assertIn('c',completed)
            if failing:
                self.assertEqual(result['status'],'failed');self.assertTrue(any(e['type']=='node.skipped' and e['payload']['nodeId']=='o' for e in self.store.events('workspace') if e['run_id']==result['id']))
            else:self.assertEqual(result['output'],{'b':42,'c':42})

    def test_cancellation(self):
        started=threading.Event();release=threading.Event();original=self.engine.execute_node
        def slow(ctx,node,value):
            if node['kind']=='input':started.set();release.wait(2)
            return original(ctx,node,value)
        with patch.object(self.engine,'execute_node',side_effect=slow):
            r=self.engine.start('workspace',1,'x',auto=False);self.assertTrue(started.wait(1));self.assertTrue(self.engine.cancel(r['id']));release.set();result=self.wait(r)
        self.assertEqual(result['status'],'cancelled');self.assertIsNone(result['output'])
        completed=[e for e in self.store.events('workspace') if e['type']=='node.completed'];self.assertEqual(completed,[])

    def test_memory_and_terminal_events(self):
        ctx=Context(self.engine,'test',self.store.project(),threading.Event());ctx.tool('memory.put',{'key':'x','value':False},SAFE_TOOLS,'x');self.assertIs(ctx.tool('memory.get','x',SAFE_TOOLS,'x'),False)
        r=self.wait(self.engine.start('workspace',1,'x',auto=False));terminal=[e for e in self.store.events('workspace') if e['run_id']==r['id'] and e['type']=='run.completed'];self.assertEqual(len(terminal),1)

    def test_lifecycle_transaction_rolls_back(self):
        self.store.create_run('test',self.store.project(),'source',1)
        with patch.object(self.store,'_event',side_effect=RuntimeError('write failure')):
            with self.assertRaises(RuntimeError):self.store.transition_run('test','completed',{},42)
        self.assertEqual(self.store.run('test')['status'],'queued')

    def test_model_platform_proposals(self):
        graph=initial_graph();graph['nodes'][0]['config'].update(provider='model',policy='model-plan')
        p=self.set_graph(graph)
        proposal={'reason':'move input','operations':[{'op':'update_node','id':'input','changes':{'x':77}}]}
        with patch.object(Context,'model_agent',return_value=proposal):
            result=self.engine.optimize('workspace',p['version'])
        self.assertEqual(result['project']['graph']['nodes'][1]['x'],77)
        bad={'reason':'escalate','operations':[{'op':'update_node','id':'analyst','changes':{'config':{'level':'platform'}}}]}
        version=result['project']['version']
        with patch.object(Context,'model_agent',return_value=bad),self.assertRaises(GraphError):self.engine.optimize('workspace',version)
        self.assertEqual(self.store.project()['version'],version)

    def test_model_platform_cas_and_nonblocking_cancel(self):
        graph=initial_graph();graph['nodes'][0]['config'].update(provider='model',policy='model-plan')
        p=self.set_graph(graph);entered=threading.Event();release=threading.Event()
        self.store.create_run('active',p,'source',1);cancel=threading.Event();self.engine.active['active']=(cancel,'workspace')
        def plan(*args):
            entered.set();release.wait(2)
            return {'reason':'move','operations':[{'op':'update_node','id':'input','changes':{'x':99}}]}
        with concurrent.futures.ThreadPoolExecutor() as pool,patch.object(Context,'model_agent',side_effect=plan):
            future=pool.submit(self.engine.optimize,'workspace',p['version']);self.assertTrue(entered.wait(1))
            self.assertTrue(self.engine.cancel('active'));self.assertTrue(cancel.is_set())
            self.store.save('workspace',p['version'],p['graph'],p['source'],p['name']);release.set()
            with self.assertRaises(Conflict):future.result(timeout=2)
        self.engine.active.pop('active',None)

    def test_persistence_and_restart_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.db';s=Store(path);s.memory_put('workspace','persist',42);s.create_run('lost',s.project(),'source','x');s.db.close();s=Store(path);Engine(s)
            self.assertEqual(s.memory_get('workspace','persist'),42);self.assertEqual(s.run('lost')['status'],'interrupted');self.assertEqual(s.events('workspace')[-1]['type'],'run.interrupted');s.db.close()


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory();cls.server=make_server(port=0,db=Path(cls.folder.name)/'http.db',settings=__import__('chaos.settings',fromlist=['Settings']).Settings(':memory:'));cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.base='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.server.store.db.close();cls.folder.cleanup()

    def request(self,path,body=None,headers=None):
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json',**(headers or {})})
        return urllib.request.urlopen(req,timeout=3)

    def test_health_and_static(self):
        with self.request('/api/health') as r:self.assertEqual(json.load(r)['status'],'ok')
        with self.request('/') as r:self.assertIn('Content-Security-Policy',r.headers);self.assertIn('Chaos'.encode(),r.read())

    def test_host_and_origin_rejection(self):
        for headers in [{'Host':'attacker.example'}, {'Origin':'http://attacker.example'}, {'Origin':'null'}]:
            with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/bootstrap',headers=headers)
            self.assertEqual(e.exception.code,403)

    def test_invalid_json_and_version(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/runs',{'version':999,'input':'x','autoOptimize':False})
        self.assertEqual(e.exception.code,409)

    def test_sse_resume(self):
        s=self.server.store;first=s.event('workspace','test.first',{});second=s.event('workspace','test.second',{})
        with self.request('/api/events',headers={'Last-Event-ID':str(first)}) as r:
            self.assertEqual(r.headers['Content-Type'],'text/event-stream; charset=utf-8')
            line=r.readline().decode();self.assertEqual(line.strip(),'id: '+str(second));self.assertIn('test.second',r.readline().decode())

    def test_provider_protocol_and_redirect(self):
        calls=[]
        class Mock(BaseHTTPRequestHandler):
            mode='final'
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']));calls.append(self.headers.get('Authorization'))
                if self.mode=='redirect':self.send_response(302);self.send_header('Location','http://127.0.0.1:1/leak');self.end_headers();return
                reply='The answer is 42.' if self.mode=='final' else None
                raw=json.dumps({'choices':[{'message':{'content':reply}}]}).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        mock=ThreadingHTTPServer(('127.0.0.1',0),Mock);threading.Thread(target=mock.serve_forever,daemon=True).start()
        ctx=Context(self.server.engine,'model-test',self.server.store.project(),threading.Event())
        try:
            with patch.dict(os.environ,{'CHAOS_MODEL_URL':f'http://127.0.0.1:{mock.server_port}/v1/chat/completions','CHAOS_MODEL_NAME':'test','CHAOS_MODEL_KEY':'sentinel'}):
                self.assertEqual(ctx.agent('model',{'provider':'model','tools':[]},'hi',SAFE_TOOLS),'The answer is 42.')
                Mock.mode='redirect'
                with self.assertRaisesRegex(LanguageError,'HTTP 302'):ctx.agent('model',{'provider':'model','tools':[]},'hi',SAFE_TOOLS)
                Mock.mode='array'
                with self.assertRaisesRegex(LanguageError,'neither an answer'):ctx.agent('model',{'provider':'model','tools':[]},'hi',SAFE_TOOLS)
        finally:mock.shutdown();mock.server_close()
        self.assertEqual(len(calls),3)


if __name__=='__main__':unittest.main()
