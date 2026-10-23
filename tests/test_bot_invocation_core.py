"""Real native invocations and guarded host boundaries; no model/device calls."""
import copy
import json
import time
import threading
import unittest
import uuid

from chaos import Framework, Registry, Settings, ToolSpec
from chaos.bot_host import control, BotStale
from chaos.language import LanguageError
from chaos.runtime import Context


VERIFY='fn(s){{done:true,context:null,output:s.evidence[0]}}'


class BotInvocationCore(unittest.TestCase):
    def app(self,source,registry=None,caps=()):
        app=Framework(registry=registry,settings=Settings(':memory:'));self.addCleanup(app.close)
        app.create_project('p',source=source,capabilities=list(caps))
        return app

    def evaluate(self,source,registry=None,caps=(),*,mode="source"):
        app=self.app(source,registry,caps)
        return app,app.start('p',1,None,mode=mode,trace=False).result(10)

    def rig(self):
        effects=[];registry=Registry()
        registry.register_tool(ToolSpec('probe',lambda value,ctx:effects.append(value)or value))
        registry.register_tool(ToolSpec('secret',lambda value,ctx:effects.append(value)or value))
        app=self.app('await sleep(20)',registry,['probe','secret']);run=app.start('p',1,None,mode='source',trace=False)
        ctx=Context(app._engine,run.id,app.get_project('p'),app._engine.active[run.id][0])
        def open_scope(payload):
            opened=control(ctx,'bot_open',payload)
            packet=control(ctx,'bot_decision',{'scopeId':opened['scopeId'],'revision':opened['revision'],'phase':'execution'})
            guard={key:packet[key]for key in ('decisionId','revision','ticket')};guard['scopeId']=opened['scopeId']
            if ctx.bot_guard is not None:
                parent=copy.deepcopy(ctx.bot_guard);ancestors=parent.pop('ancestors',[])
                guard['ancestors']=ancestors+[parent]
            return guard
        ctx.agent_capabilities={'probe'}
        parent=open_scope({'scopeId':'bot-scope-'+uuid.uuid4().hex});ctx.bot_guard=parent
        ctx.agent_capabilities=None
        child=open_scope({'scopeId':'bot-scope-'+uuid.uuid4().hex,'botId':'bot-child','mailbox':'auto'})
        ctx.bot_guard=child
        return app,run,ctx,parent,child,effects

    def test_actual_nested_auto_child_returns_42_with_real_scope_provenance(self):
        source=('let child=bot("child",fn(s){s.input},fn(p,s){p+2},'+VERIFY+');'
                'let parent=bot("parent",fn(s){s.input},async fn(p,s){(await child(p)).output},'+VERIFY+');'
                '(await parent(40)).output')
        for mode in ("source","graph"):
            with self.subTest(mode=mode):
                app,value=self.evaluate(source,mode=mode);self.assertEqual(value,42)
                events=app.events('p',limit=300)['items'];opened=[row['payload']for row in events if row['type']=='bot.scope.opened']
                self.assertEqual(len(opened),2);root,child=opened
                self.assertIsNone(root['parentScopeId']);self.assertEqual(root['mailboxKind'],'run')
                self.assertEqual(child['parentScopeId'],root['scopeId']);self.assertEqual(child['rootScopeId'],root['scopeId'])
                self.assertEqual(child['mailboxKind'],'scoped');self.assertNotEqual(child['botId'],root['botId'])
                self.assertEqual(sum(row['type']=='bot.scope.closed'for row in events),2)

    def test_scoped_siblings_really_overlap_without_callback_or_output_mix(self):
        barrier=threading.Barrier(2);calls=[];registry=Registry()
        def effect(value,ctx):
            calls.append(value);barrier.wait(timeout=4);return value+40
        registry.register_tool(ToolSpec('fixture.barrier',effect))
        source=('fn make(goal){bot(goal,fn(s){s.input},fn(p,s){tool("fixture.barrier",p)},'+VERIFY+',mailbox="scoped")};'
                'let left=make("left");let right=make("right");let results=await gather([left(1),right(2)]);'
                '[results[0].output,results[1].output]')
        app,value=self.evaluate(source,registry,['fixture.barrier'])
        self.assertEqual(value,[41,42]);self.assertCountEqual(calls,[1,2])
        opened=[row['payload']for row in app.events('p',limit=300)['items']if row['type']=='bot.scope.opened']
        self.assertEqual(len(opened),2);self.assertTrue(all(row['parentScopeId']is None and row['mailboxKind']=='scoped'for row in opened))

    def test_same_bot_still_refuses_overlapping_calls_and_first_finishes(self):
        entered=threading.Event();release=threading.Event();registry=Registry()
        def gate(value,ctx):
            entered.set()
            if not release.wait(4):raise RuntimeError('second invocation did not release first')
            return value
        registry.register_tool(ToolSpec('fixture.gate',gate))
        registry.register_tool(ToolSpec('fixture.entered',lambda value,ctx:entered.is_set(),effect='read'))
        registry.register_tool(ToolSpec('fixture.release',lambda value,ctx:release.set()or True))
        self.addCleanup(release.set)
        source=('let b=bot("one",fn(s){1},fn(p,s){tool("fixture.gate",p)},'+VERIFY+');'
                'async fn other(){while !tool("fixture.entered",null){await sleep(0)};'
                'let code=try{await b()}catch error{error.code};tool("fixture.release",null);code};'
                'let results=await gather([b(),other()]);[results[0].status,results[1]]')
        unused,value=self.evaluate(source,registry,['fixture.gate','fixture.entered','fixture.release'])
        self.assertEqual(value,['achieved','bot_busy'])

    def test_parent_revision_stales_child_before_any_external_handler(self):
        app,run,ctx,parent,child,effects=self.rig();run.guide('new root plan',message_id='root-guide')
        with self.assertRaises(BotStale)as caught:ctx.tool('probe',42,{'probe'},'nested')
        self.assertEqual(caught.exception.scope_id,parent['scopeId']);self.assertEqual(effects,[])
        self.assertFalse(any(row['type']=='bot.action.admitted'for row in app.events('p',run_id=run.id,limit=300)['items']))

    def test_descendant_effect_is_admitted_once_and_visible_in_parent_outcome(self):
        app,run,ctx,parent,child,effects=self.rig();self.assertEqual(ctx.tool('probe',42,{'probe'},'nested'),42)
        one=control(ctx,'bot_outcome',{'scopeId':child['scopeId'],'decisionId':child['decisionId']})
        two=control(ctx,'bot_outcome',{'scopeId':parent['scopeId'],'decisionId':parent['decisionId']})
        self.assertEqual(effects,[42]);self.assertEqual(one['actions'],two['actions'])
        self.assertEqual(len(one['actions']),1);self.assertEqual(one['actions'][0]['status'],'completed')
        self.assertEqual(sum(row['type']=='bot.action.admitted'for row in app.events('p',run_id=run.id,limit=300)['items']),1)

    def test_child_cannot_expand_parent_caps_or_replace_ancestor_ticket(self):
        unused,run,ctx,parent,child,effects=self.rig()
        with self.assertRaises(LanguageError)as denied:ctx.tool('secret',42,{'probe','secret'},'nested')
        self.assertEqual(denied.exception.code,'capability_error')
        forged=copy.deepcopy(child);forged['ancestors'][0]['ticket']='forged';ctx.bot_guard=forged
        with self.assertRaises(LanguageError)as rejected:ctx.tool('probe',42,{'probe'},'nested')
        self.assertEqual(rejected.exception.code,'ownership_error');self.assertEqual(effects,[])

    def test_admitted_child_prefix_finishes_but_parent_guide_refuses_suffix(self):
        unused,run,ctx,parent,child,effects=self.rig();self.assertEqual(ctx.tool('probe',1,{'probe'},'nested'),1)
        run.guide('replace remaining work',message_id='after-prefix')
        with self.assertRaises(BotStale):ctx.tool('probe',2,{'probe'},'nested')
        parent_result=control(ctx,'bot_outcome',{'scopeId':parent['scopeId'],'decisionId':parent['decisionId']})
        self.assertEqual(effects,[1]);self.assertEqual([(r['kind'],r['status'])for r in parent_result['actions']],[('tool:probe','completed')])

    def test_caught_uncertain_child_cannot_wash_parent_into_success(self):
        calls=[];registry=Registry()
        def uncertain(value,ctx):
            calls.append(value)
            raise RuntimeError('effect began, outcome unavailable')
        registry.register_tool(ToolSpec('fixture.uncertain',uncertain))
        source=('let child=bot("child",fn(s){42},fn(p,s){tool("fixture.uncertain",p)},'+VERIFY+');'
                'let parent=bot("parent",fn(s){null},async fn(p,s){await child();42},'+VERIFY+');'
                'let result=await parent();[result.status,result.done]')
        unused,value=self.evaluate(source,registry,['fixture.uncertain'])
        self.assertEqual(calls,[42]);self.assertEqual(value,['needs_reconciliation',False])

    def test_program_run_keeps_trusted_ambient_parent_and_returns_42(self):
        child='let child=bot("program-child",fn(s){s.input},fn(p,s){p+2},'+VERIFY+');(await child(input)).output'
        definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':child},'languageVersions':{'main.chaos':2}}
        source=('let program=program_from('+json.dumps(definition)+');let root=bot("root",fn(s){s.input},'
                'async fn(p,s){await run_program(program,p)},'+VERIFY+');(await root(40)).output')
        app,value=self.evaluate(source);self.assertEqual(value,42)
        opened=[row['payload']for row in app.events('p',limit=300)['items']if row['type']=='bot.scope.opened']
        self.assertEqual(len(opened),2);self.assertEqual(opened[1]['parentScopeId'],opened[0]['scopeId'])
        self.assertEqual(opened[1]['rootScopeId'],opened[0]['scopeId'])

    def test_parent_guidance_crosses_program_and_stops_child_effect_without_spinning(self):
        calls=[];registry=Registry();registry.register_tool(ToolSpec('fixture.probe',lambda value,ctx:calls.append(value)or value))
        child=('async fn plan(s){await sleep(0.3);s.input};let child=bot("program-child",plan,'
               'fn(p,s){tool("fixture.probe",p)},'+VERIFY+');(await child(input)).output')
        definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':child},'languageVersions':{'main.chaos':2}}
        source=('let program=program_from('+json.dumps(definition)+');let root=bot("root",fn(s){s.input},'
                'async fn(p,s){await run_program(program,p)},'+VERIFY+');let result=await root(40);[result.status,result.done]')
        app=self.app(source,registry,['fixture.probe']);run=app.start('p',1,None,mode='source',trace=False)
        deadline=time.monotonic()+3;found=False
        while time.monotonic()<deadline:
            opened=[row['payload']for row in app.events('p',run_id=run.id,limit=300)['items']if row['type']=='bot.scope.opened']
            if len(opened)==2:found=True;break
            time.sleep(.002)
        self.assertTrue(found,'actual nested Program Bot never opened')
        run.guide('replace parent plan',message_id='ambient-parent-stale')
        self.assertEqual(run.result(5),['needs_reconciliation',False]);self.assertEqual(calls,[])
