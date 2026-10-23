import threading,time,unittest
from chaos import Framework,Settings,Registry,ToolSpec


class BotLifecycleTests(unittest.TestCase):
    def setup_run(self,source,registry=None,capabilities=None):
        app=Framework(registry=registry,settings=Settings(':memory:'));self.addCleanup(app.close)
        app.create_project('bot-life',source=source,capabilities=capabilities or [])
        return app,app.start('bot-life',1,None,mode='source')

    def test_pure_suspended_planner_replans_without_stale_executor(self):
        effects=[];registry=Registry();registry.register_tool(ToolSpec('act',lambda value,ctx:effects.append(value) or value))
        source='async fn plan(s){await sleep(0.1);length(s.guidance)};let b=bot("guide",plan,fn(p,s){tool("act",p)},fn(s){{done:true,context:null,output:s.evidence}});await b()'
        app,run=self.setup_run(source,registry,['act']);time.sleep(.03);run.guide('new',message_id='new');result=run.result(10)
        self.assertEqual(effects,[1]);self.assertTrue(result['done']);self.assertEqual(result['state']['guidance'][0]['input'],'new')

    def test_cancel_sustained_bot_releases_mailbox_and_tasks(self):
        source='async fn plan(s){await sleep(1);null};let b=bot("sustain",plan,fn(p,s){null},fn(s){{done:false,context:null,output:null}},null,null);await b()'
        app,run=self.setup_run(source);time.sleep(.03);self.assertTrue(run.cancel());self.assertEqual(run.wait(10)['status'],'cancelled')
        self.assertNotIn(run.id,app._engine.guidance)

    def test_normal_receiver_cannot_steal_exclusive_bot_mailbox(self):
        source='async fn plan(s){await sleep(0.1);null};let b=bot("lease",plan,fn(p,s){null},fn(s){{done:true,context:null,output:null}});let work=b();spawn(work);await sleep(0.02);let denied=try{await guidance_receive(0)}catch e{e.code};await work;denied'
        app,run=self.setup_run(source);self.assertEqual(run.result(10),'ownership_error')

    def test_fresh_bot_after_another_adopted_guide_uses_actual_revision(self):
        source='async fn p(s){await sleep(0.05);length(s.guidance)};fn e(p,s){p};fn v(s){{done:true,context:null,output:s.evidence}};let first=bot("first",p,e,v);let a=await first();let second=bot("second",fn(s){42},e,v);let b=await second();[a.state.revision,b.done,b.output,b.state.revision]'
        app,run=self.setup_run(source);run.guide('first input',message_id='adopted')
        self.assertEqual(run.result(10),[1,True,[42],1])

    def test_phase_owned_suspended_stream_closes_before_result(self):
        effects=[];registry=Registry();registry.register_tool(ToolSpec('release',lambda value,ctx:effects.append(value) or value))
        source='var saved=null;async fn* stream(){try{yield 1}finally{tool("release",42)}};async fn execute(p,s){saved=stream();await anext(saved);0};let b=bot("cleanup",fn(s){null},execute,fn(s){{done:true,context:null,output:s.evidence}});await b()'
        app,run=self.setup_run(source,registry,['release']);self.assertTrue(run.result(10)['done']);self.assertEqual(effects,[42])

    def test_superseded_finite_planning_exhaustion_commits_current_context_source_graph(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode):
                effects=[];registry=Registry();registry.register_tool(ToolSpec('act',lambda value,ctx:effects.append(value) or value))
                app=Framework(registry=registry,settings=Settings(':memory:'));self.addCleanup(app.close)
                source='async fn p(s){await progress_emit("plan",{marker:"planner-started"});await sleep(0.1);42};let b=bot("finite",p,fn(p,s){tool("act",p)},fn(s){{done:false,context:null,output:null}},null,1);await b()'
                project=app.create_project('finite-'+mode,source=source,capabilities=['act']);run=app.start(project['id'],1,None,mode=mode)
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    events=app.events(project['id'],run_id=run.id,limit=200)['items']
                    if any(row['type']=='bot.progress' and row['payload'].get('data',{}).get('marker')=='planner-started' for row in events):break
                    time.sleep(.005)
                else:self.fail('actual planner entry marker was absent')
                run.guide('new finite context',message_id='finite-guide');value=run.result(10)
                self.assertEqual(value['status'],'exhausted');self.assertFalse(value['done']);self.assertEqual(effects,[])
                self.assertEqual(value['state']['guidance'][0]['input'],'new finite context');self.assertEqual(value['state']['revision'],1)
                events=app.events(project['id'],run_id=run.id,limit=200)['items']
                self.assertTrue(any(row['type']=='bot.result.committed' and row['payload']['revision']==1 for row in events))

    def test_local_cancel_retires_mailbox_before_parent_continues_source_graph(self):
        source='async fn p(s){await sleep(10);null};fn e(p,s){p};fn v(s){{done:true,context:null,output:42}};let first=bot("cancelled",p,e,v);let work=spawn(first());while task_info(work).phase!="joining"{await sleep(0.001)};cancel(work);await task_wait([work],until="all");let second=bot("next",fn(s){42},e,v);let next=(await second()).status;let empty=(await guidance_receive(0)).received;[task_info(work).state,next,empty,bot_info(first).busy]'
        for mode in ('source','graph'):
            with self.subTest(mode=mode):
                app=Framework(settings=Settings(':memory:'));self.addCleanup(app.close)
                project=app.create_project('cancel-parent-'+mode,source=source,capabilities=[])
                self.assertEqual(app.start(project['id'],1,None,mode=mode).result(10),['cancelled','achieved',False,False])

    def test_cancel_after_host_open_before_native_receipt_retires_owned_scope_source_graph(self):
        from unittest.mock import patch
        import chaos.bot_host as bot_host
        source='fn e(p,s){p};fn v(s){{done:true,context:null,output:42}};let first=bot("opening",fn(s){1},e,v);let work=spawn(first());tool("wait.open",null);cancel(work);tool("release.open",null);await task_wait([work],until="all");let second=bot("parent",fn(s){42},e,v);let next=(await second()).status;let empty=(await guidance_receive(0)).received;[task_info(work).state,next,empty,bot_info(first).busy]'
        for mode in ('source','graph'):
            with self.subTest(mode=mode):
                opened,release=threading.Event(),threading.Event();held=[False];original=bot_host.control
                def controlled(context,operation,payload):
                    result=original(context,operation,payload)
                    if operation=='bot_open' and not held[0]:
                        held[0]=True;opened.set();self.assertTrue(release.wait(3))
                    return result
                def wait_open(value,context):
                    self.assertTrue(opened.wait(3));return None
                def release_open(value,context):release.set();return None
                registry=Registry();registry.register_tool(ToolSpec('wait.open',wait_open));registry.register_tool(ToolSpec('release.open',release_open))
                with Framework(registry=registry,settings=Settings(':memory:')) as app:
                    project=app.create_project('open-cancel-'+mode,source=source,capabilities=['wait.open','release.open'])
                    with patch.object(bot_host,'control',controlled):
                        try:
                            run=app.start(project['id'],1,None,mode=mode)
                            self.assertEqual(run.result(10),['cancelled','achieved',False,False])
                        finally:release.set()
                    self.assertTrue(opened.is_set())
                    events=app.events(project['id'],run_id=run.id,limit=200)['items']
                    self.assertEqual(sum(row['type']=='bot.scope.opened' for row in events),2)
                    self.assertEqual(sum(row['type']=='bot.scope.closed' for row in events),2)

    def test_close_journal_failure_blocks_reinvocation_after_completed_prefix(self):
        from unittest.mock import patch
        from chaos.runtime import Context
        from chaos.language import LanguageError
        effects=[];registry=Registry();registry.register_tool(ToolSpec('act',lambda value,context:effects.append(value) or value))
        original=Context.event;failed=[False]
        def event(context,kind,payload):
            if kind=='bot.scope.closed' and not failed[0]:
                failed[0]=True;raise LanguageError('controlled close journal failure',code='bot_test_close')
            return original(context,kind,payload)
        source='let b=bot("prefix",fn(s){42},fn(p,s){tool("act",p)},fn(s){{done:true,context:null,output:s.evidence}});let first=try{await b()}catch error{error.code};let again=try{await b()}catch error{error.code};[first,again,bot_info(b).needsReconciliation,bot_info(b).busy]'
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            project=app.create_project('close-journal',source=source,capabilities=['act'])
            with patch.object(Context,'event',event):
                run=app.start(project['id'],1,None,mode='source')
                self.assertEqual(run.result(10),['bot_test_close','bot_state_error',True,False])
            self.assertEqual(effects,[42]);self.assertTrue(failed[0])
