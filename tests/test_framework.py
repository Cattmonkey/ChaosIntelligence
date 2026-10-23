import concurrent.futures
import copy
import json
import math
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from chaos import (Framework, Client, Workflow, Registry, ToolSpec, ProviderSpec, ToolCall, Final,
                   RunFailed, ValidationError, NotFoundError, ConflictError, APIError)
from chaos.extensions import json_copy
from chaos.framework import wait_for
from chaos.graph import GraphError, validate, apply_patch
from chaos.language import Evaluator, LanguageError
from chaos.runtime import Context, Cancelled
from chaos.server import make_server
from chaos.store import Store, Conflict


def extension_registry(calls=None):
    registry = Registry()
    @registry.tool('app.double', input_type='Int', output_type='Int', effect='pure', description='Double an integer')
    def double(value, context):
        context.checkpoint()
        if calls is not None:
            calls.append((value, context.project_id))
        return value * 2
    @registry.provider('app.planner')
    def planner(request, context):
        context.checkpoint()
        if request.step == 1:
            return ToolCall('app.double', request.input)
        return Final({'answer': request.history[-1]['output'], 'prompt': request.prompt})
    return registry


def custom_graph():
    return (Workflow().input().agent('worker', 'Double task input', provider='app.planner', tools=['app.double'])
            .output().connect('input','worker').connect('worker','output'))


class FrameworkTests(unittest.TestCase):
    def test_public_embedding_tools_provider_and_source(self):
        calls=[]
        with Framework(registry=extension_registry(calls)) as app:
            p=app.create_project('application',graph=custom_graph(),capabilities=['app.double'])
            app.validate(p['id'])
            handle=app.start(p['id'],p['version'],21)
            self.assertEqual(handle.result(),{'answer':42,'prompt':'Double task input'})
            trace=list(handle.watch());self.assertEqual(trace[-1]['type'],'run.completed')
            self.assertEqual([e['type'] for e in trace].count('tool.completed'),1)
            p['source']='agent a {provider:"app.planner", prompt:"source", tools:["app.double"]}; a(input)'
            p=app.save_project(p)
            self.assertEqual(app.start(p['id'],p['version'],20,mode='source').result()['answer'],40)
        self.assertEqual(calls,[(21,'application'),(20,'application')])

    def test_function_node_and_capability_failure(self):
        calls=[]
        with Framework(registry=extension_registry(calls)) as app:
            graph=(Workflow().input().function('f','tool("app.double",input)',tools=['app.double']).output().connect('input','f').connect('f','output'))
            p=app.create_project('functions',graph=graph,capabilities=['app.double'])
            self.assertEqual(app.start('functions',1,21).result(),42)
            p['capabilities']=[];p=app.save_project(p)
            with self.assertRaises(LanguageError):app.start('functions',p['version'],21)
            p['source']='agent a {provider:"app.planner",tools:["app.double"]};a(input)';p=app.save_project(p)
            with self.assertRaises(RunFailed):app.start('functions',p['version'],21,mode='source').result()
            self.assertEqual(len(calls),1)

    def test_provider_cannot_invoke_undeclared_tool(self):
        calls=[]
        with Framework(registry=extension_registry(calls)) as app:
            p=app.create_project('deny',source='agent a {provider:"app.planner",tools:[]};a(input)',capabilities=['app.double'])
            with self.assertRaises(RunFailed):app.start('deny',1,21,mode='source').result()
            self.assertEqual(calls,[])

    def test_registration_snapshot_and_reserved_names(self):
        registry=extension_registry()
        for spec in [ToolSpec('app.double',lambda v,c:v),ToolSpec('graph.patch',lambda v,c:v)]:
            with self.assertRaises(ValueError):registry.register_tool(spec)
        with self.assertRaises(ValueError):registry.register_provider(ProviderSpec('local',lambda r,c:Final(1)))
        with Framework(registry=registry) as one,Framework() as two:
            registry.register_tool(ToolSpec('app.later',lambda v,c:v))
            self.assertIn('app.double',one.capabilities()['tools'])
            self.assertNotIn('app.later',one.capabilities()['tools'])
            self.assertNotIn('app.double',two.capabilities()['tools'])
            desc=one.capabilities();desc['tools']['app.double']['effect']='topology'
            self.assertEqual(one.capabilities()['tools']['app.double']['effect'],'pure')

    def test_extension_input_output_contracts_and_falsey(self):
        registry=Registry()
        registry.register_tool(ToolSpec('app.bad',lambda v,c:True,output_type='Int'))
        registry.register_tool(ToolSpec('app.identity',lambda v,c:v))
        registry.register_tool(ToolSpec('app.inf',lambda v,c:float('inf')))
        registry.register_tool(ToolSpec('app.raise',lambda v,c:(_ for _ in ()).throw(ValueError('callback failed'))))
        with Framework(registry=registry) as app:
            p=app.create_project('contracts',source='tool("app.bad",input)',capabilities=['app.bad','app.identity','app.inf','app.raise'])
            for tool in ('app.bad','app.inf','app.raise'):
                p['source']='tool("'+tool+'",input)';p=app.save_project(p)
                handle=app.start('contracts',p['version'],0,mode='source')
                with self.assertRaises(RunFailed):handle.result()
                self.assertIn('tool.failed',[e['type'] for e in handle.watch()])
            p['source']='tool("app.identity",input)';p=app.save_project(p)
            for value in (False,0,None,[],{}):
                self.assertEqual(app.start('contracts',p['version'],value,mode='source').result(),value)

    def test_unserializable_result_never_strands_run(self):
        registry=Registry()
        registry.register_tool(ToolSpec('app.huge',lambda v,c:10**4500))
        registry.register_tool(ToolSpec('app.unicode',lambda v,c:chr(0xd800)))
        with Framework(registry=registry) as app:
            p=app.create_project('wire',capabilities=['app.huge','app.unicode'])
            for name in ('app.huge','app.unicode'):
                p['source']='tool("'+name+'",input)';p=app.save_project(p)
                handle=app.start('wire',p['version'],0,mode='source')
                run=handle.wait(2);self.assertEqual(run['status'],'failed');self.assertIsNone(run['output'])
                events=list(handle.watch());self.assertEqual(events[-1]['type'],'run.failed')

    def test_final_checkpoint_cancellation_discards_output(self):
        from chaos.runtime import Budget
        original=Budget.tick
        app=Framework();p=app.create_project('checkpoint',source='42')
        def evaluator(ctx,*args,**kwargs):
            class Result:
                def run(self,*args,**kwargs):ctx.cancel.set();return 42
            return Result()
        try:
            with patch.object(Context,'evaluator',evaluator):
                run=app.start('checkpoint',1,None,mode='source').wait(2)
            self.assertEqual(run['status'],'cancelled');self.assertIsNone(run['output'])
        finally:app.close()

    def test_json_boundaries_protect_caller_and_language_values(self):
        registry=Registry();shared={'value':0}
        def mutate(v,c):v['value']=99;return v
        def output(v,c):shared['value']+=1;return shared
        registry.register_tool(ToolSpec('app.mutate',mutate));registry.register_tool(ToolSpec('app.shared',output))
        with Framework(registry=registry) as app:
            p=app.create_project('copies',source='let x=input;tool("app.mutate",x);let y=tool("app.shared",null);tool("app.shared",null);{x:x,y:y}',capabilities=['app.mutate','app.shared'])
            value={'value':1};handle=app.start('copies',1,value,mode='source');result=handle.result()
            self.assertEqual(value,{'value':1});self.assertEqual(result,{'x':{'value':1},'y':{'value':1}})
            snapshot=handle.get();snapshot['snapshot']['source']='999'
            self.assertEqual(handle.get()['snapshot']['source'],p['source'])

    def test_project_memory_event_isolation(self):
        with Framework() as app:
            source='tool("memory.put",{key:"same",value:input});tool("memory.get","same")'
            for name in ('alpha','beta'):app.create_project(name,source=source,capabilities=['memory.put','memory.get'])
            a=app.start('alpha',1,{'a':1},mode='source');b=app.start('beta',1,False,mode='source')
            self.assertEqual(a.result(),{'a':1});self.assertIs(b.result(),False)
            for name in ('alpha','beta'):
                self.assertTrue(all(e['project_id']==name for e in app.events(name)['items']))
                self.assertEqual(len(app.runs(name)['items']),1)
            with self.assertRaises(KeyError):app.events('alpha',run_id=b.id)

    def test_wait_timeout_does_not_cancel_and_late_result_discarded(self):
        entered,release=threading.Event(),threading.Event();registry=Registry()
        def delayed(v,c):entered.set();release.wait(2);return 42
        registry.register_tool(ToolSpec('app.delayed',delayed))
        with Framework(registry=registry) as app:
            app.create_project('slow',source='tool("app.delayed",input)',capabilities=['app.delayed'])
            handle=app.start('slow',1,None,mode='source');self.assertTrue(entered.wait(1))
            with self.assertRaises(TimeoutError):handle.wait(.01)
            self.assertEqual(handle.get()['status'],'running');self.assertTrue(handle.cancel());release.set()
            result=handle.wait();self.assertEqual(result['status'],'cancelled');self.assertIsNone(result['output'])
            trace=list(handle.watch());self.assertEqual([e['type'] for e in trace].count('run.cancelled'),1)
            self.assertNotIn('tool.completed',[e['type'] for e in trace])

    def test_close_waits_for_cooperative_callback(self):
        entered=threading.Event();registry=Registry()
        def looping(v,c):
            entered.set()
            while True:c.checkpoint();time.sleep(.005)
        registry.register_tool(ToolSpec('app.loop',looping));app=Framework(registry=registry)
        app.create_project('closing',source='tool("app.loop",input)',capabilities=['app.loop']);app.start('closing',1,0,mode='source');self.assertTrue(entered.wait(1));app.close(1)
        self.assertTrue(app._closed)
        with self.assertRaises(RuntimeError):app.start('closing',1,0)

    def test_close_timeout_keeps_store_owned_until_drain(self):
        entered,release=threading.Event(),threading.Event();registry=Registry()
        registry.register_tool(ToolSpec('app.block',lambda v,c:(entered.set(),release.wait(2),v)[2]))
        app=Framework(registry=registry)
        app.create_project('closing',source='tool("app.block",input)',capabilities=['app.block']);app.start('closing',1,0,mode='source');self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(TimeoutError):app.close(.01)
            self.assertFalse(app._closed);self.assertTrue(app._store.runtime_owned)
            self.assertIsNotNone(app._store.project('closing'))
        finally:release.set();app.close(2)

    def test_second_runtime_cannot_recover_live_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db.sqlite'
            with Framework(path) as first:
                first.create_project('one')
                with self.assertRaisesRegex(RuntimeError,'active Chaos runtime'):Framework(path)
                self.assertEqual(first.get_project('one')['version'],1)
            with Framework(path) as reopened:self.assertEqual(reopened.get_project('one')['version'],1)

    def test_cross_connection_cas(self):
        with tempfile.TemporaryDirectory() as folder:
            a=Store(Path(folder)/'db.sqlite');b=Store(Path(folder)/'db.sqlite');project=a.project();barrier=threading.Barrier(2)
            def save(store,name):
                barrier.wait(2)
                try:return store.save('workspace',1,project['graph'],project['source'],name)['version']
                except Conflict:return 'conflict'
            try:
                with concurrent.futures.ThreadPoolExecutor(2) as pool:
                    futures=[pool.submit(save,a,'A'),pool.submit(save,b,'B')];results=[f.result() for f in futures]
                self.assertCountEqual(results,[2,'conflict']);self.assertEqual(len(a.events('workspace')),1)
            finally:a.close();b.close()

    def test_standalone_optimizer_is_drained(self):
        from chaos.examples import initial_graph
        entered,release=threading.Event(),threading.Event();app=Framework()
        graph=initial_graph();graph['nodes'][0]['config'].update(provider='model',policy='model-plan')
        app.create_project('opt',graph=graph)
        def provider(*args):entered.set();release.wait(2);return {'reason':'none','operations':[]}
        try:
            with concurrent.futures.ThreadPoolExecutor(1) as pool,patch.object(Context,'model_agent',side_effect=provider):
                future=pool.submit(app.optimize,'opt',1);self.assertTrue(entered.wait(1))
                with self.assertRaises(TimeoutError):app.close(.01)
                self.assertTrue(app._store.runtime_owned);release.set()
                with self.assertRaises(Cancelled):future.result(timeout=2)
                app.close(2)
        finally:release.set();app.close(2)

    def test_workflow_builder_atomic_validation(self):
        workflow=Workflow.identity();before=workflow.build()
        with self.assertRaises(GraphError):workflow.connect('output','input')
        self.assertEqual(workflow.build(),before)
        exported=workflow.build();exported['nodes'].clear();self.assertEqual(len(workflow.build()['nodes']),2)


class LanguageBoundaryTests(unittest.TestCase):
    def test_structural_equality_shared_values_is_bounded(self):
        source='fn nest(n){if n==0{[1]}else{let a=nest(n-1);[a,a]}};nest(35)==nest(35)'
        self.assertIs(Evaluator(step_limit=2000).run(source),True)
        self.assertIs(Evaluator().run('[true]==[1]'),False)
        self.assertIs(Evaluator().run('{x:[true]}=={x:[1]}'),False)

    def test_min_max_reject_structures_and_bool_conversion(self):
        for source in ('min([[1],[2]])','max([[1],[2]])','number(true)'):
            with self.assertRaises(LanguageError):Evaluator().run(source)
        self.assertEqual(Evaluator().run('min([3,1,2])+max([3,1,2])'),4)

    def test_raw_agent_output_is_copied(self):
        shared={'x':0}
        def agent(*args):shared['x']+=1;return shared
        self.assertEqual(Evaluator(agent_handler=agent).run('agent a {};let x=a(null);a(null);x.x'),1)

    def test_malformed_graph_and_patches_normalized(self):
        for field,value in [('kind',[]),('id',None),('parentId',[]),('kind',True)]:
            graph=Workflow.identity().build();graph['nodes'][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(GraphError):validate(graph)
        for ops in ([None],[{'op':'add_node'}],[{'op':'add_edge','edge':{}}],[{'op':'update_node','id':'input','changes':None}]):
            with self.subTest(ops=ops),self.assertRaises(GraphError):apply_patch(Workflow.identity().build(),ops)
        with self.assertRaises(GraphError):apply_patch(Workflow.identity().build(),[{'op':'remove_node','id':'input'}],actor=[])

    def test_json_boundary_rejects_cycles_nonfinite_and_objects(self):
        cycle=[];cycle.append(cycle)
        for value in (cycle,float('nan'),{1:'x'},lambda:1):
            with self.assertRaises(ValueError):json_copy(value)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.app=Framework(registry=extension_registry())
        self.server=make_server(port=0,framework=self.app)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.client=Client('http://127.0.0.1:'+str(self.server.server_port))

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.app.close()

    def test_http_sdk_custom_workflow_source_wait_and_watch(self):
        p=self.client.create_project('remote',graph=custom_graph(),capabilities=['app.double'])
        self.assertTrue(self.client.validate('remote')['closed'])
        handle=self.client.start('remote',p['version'],21)
        self.assertEqual(handle.result()['answer'],42)
        events=list(handle.watch());self.assertEqual(events[-1]['type'],'run.completed')
        self.assertEqual(self.client.runs('remote')['items'][0]['id'],handle.id)
        self.assertIn('app.double',self.client.capabilities()['tools'])
        p['source']='tool("app.double",input)';p=self.client.save_project(p)
        self.assertEqual(self.client.start('remote',p['version'],4,mode='source').result(),8)

    def test_typed_errors_versions_and_methods(self):
        with self.assertRaises(NotFoundError) as e:self.client.get_project('missing')
        self.assertEqual(e.exception.status,404);self.assertEqual(e.exception.code,'not_found')
        p=self.client.create_project('errors')
        with self.assertRaises(ConflictError):self.client.create_project('errors')
        updated=self.client.save_project(p)
        with self.assertRaises(ConflictError):self.client.save_project(p)
        for version in (True,1.0,-1):
            with self.assertRaises(ValidationError):self.client.start('errors',version,0)
            with self.assertRaises(ValidationError):self.client.optimize('errors',version)
        missing={**p,'id':'absent'}
        with self.assertRaises(NotFoundError):self.client.save_project(missing)
        with self.assertRaises(APIError) as e:self.client._request('PUT','/runs/fake/cancel',{})
        self.assertEqual(e.exception.status,405)
        with self.assertRaises(ValidationError):self.client._request('POST','/runs',{})
        self.assertEqual(updated['version'],2)

    def test_event_and_run_pagination_no_loss(self):
        self.client.create_project('pages')
        for i in range(340):self.app._store.event('pages','test.item',{'n':i})
        ids=[];cursor=0
        while True:
            page=self.client.events('pages',after=cursor,limit=37);ids.extend(e['id'] for e in page['items'])
            if page['nextCursor'] is None:break
            cursor=page['nextCursor']
        self.assertEqual(len(ids),341);self.assertEqual(len(ids),len(set(ids)))
        for i in range(35):
            self.app._store.create_run('page_'+str(i),self.app.get_project('pages'),'source',i)
            self.app._store.transition_run('page_'+str(i),'completed',{'output':i},i)
        runs=[];cursor=0
        while True:
            page=self.client.runs('pages',after=cursor,limit=8);runs.extend(x['id'] for x in page['items'])
            if page['nextCursor'] is None:break
            cursor=page['nextCursor']
        self.assertEqual(len(runs),35);self.assertEqual(len(set(runs)),35)
        self.assertEqual(len(self.client.events('workspace')['items']),0)

    def test_project_pagination_and_json_errors(self):
        for name in ('a','b','c'):self.client.create_project(name)
        first=self.client.projects(limit=2);second=self.client.projects(after=first['nextCursor'],limit=2)
        self.assertEqual([p['id'] for p in first['items']+second['items']],['a','b','c','workspace'])
        for query in ({'projectId':'a','after':-1},{'projectId':'a','limit':0},{'projectId':'none'}):
            with self.assertRaises((ValidationError,NotFoundError)):self.client._request('GET','/events',query=query)

    def test_v1_sse_resume_and_openapi_contract(self):
        p=self.client.create_project('sse');first=self.app._store.event('sse','test.first',{});second=self.app._store.event('sse','test.second',{})
        url=self.client.base_url+'/api/v1/events/stream?projectId=sse'
        with urllib.request.urlopen(urllib.request.Request(url,headers={'Last-Event-ID':str(first)}),timeout=2) as r:
            self.assertEqual(r.readline().decode().strip(),'id: '+str(second));self.assertIn('test.second',r.readline().decode())
        schema=self.client._request('GET','/openapi.json');self.assertEqual(schema['openapi'],'3.1.0')
        self.assertEqual(schema['servers'],[{'url':'/api/v1'}]);self.assertIn('/projects/{projectId}',schema['paths'])
        ids=[op['operationId'] for path in schema['paths'].values() for op in path.values()];self.assertEqual(len(ids),len(set(ids)))

    def test_borrowed_server_close_does_not_close_framework(self):
        self.server.shutdown();self.server.server_close()
        self.assertEqual(self.app.get_project('workspace')['version'],1)


class StandaloneLanguageTests(unittest.TestCase):
    def test_cli_executes_language_without_web_server(self):
        import subprocess,sys
        source='fn fact(n){if n<=1{1}else{n*fact(n-1)}};fact(input)'
        result=subprocess.run([sys.executable,'-m','chaos','run','-','--input-json','6'],input=source,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),720)
        result=subprocess.run([sys.executable,'-m','chaos','check','-'],input='fn broken(',capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,1);self.assertIn('error',json.loads(result.stderr))


if __name__=='__main__':unittest.main()
