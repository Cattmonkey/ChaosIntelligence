import unittest
from chaos import Framework,Settings,Registry,ToolSpec


class BotRuntimeTests(unittest.TestCase):
    def run_source(self,source,registry=None,capabilities=None,mode='source'):
        app=Framework(registry=registry,settings=Settings(':memory:'));self.addCleanup(app.close)
        app.create_project('bot-core',source=source,capabilities=capabilities or [])
        return app.start('bot-core',1,None,mode=mode).result(10)

    def test_real_native_bot_runs_sync_callbacks(self):
        value=self.run_source('let b=bot("count",fn(s){s.context.n},fn(p,s){p+1},fn(s){{done:true,context:{n:s.evidence[0]},output:s.evidence}},{n:1});let result=await b();[result.done,result.status,result.output,bot_context(b).context,bot_info(b).busy]')
        self.assertEqual(value,[True,'achieved',[2],{'n':2},False])

    def test_null_iterations_and_repeated_verification(self):
        value=self.run_source('let b=bot("count",fn(s){length(s.evidence)},async fn(p,s){await sleep(0);p+1},fn(s){{done:length(s.evidence)==3,context:null,output:s.evidence}},null,null);let result=await b();[result.done,result.output,result.state.iteration]')
        self.assertEqual(value,[True,[1,2,3],3])

    def test_error_after_entry_latches_reconciliation_and_no_replay(self):
        seen=[];registry=Registry();registry.register_tool(ToolSpec('act',lambda value,ctx:seen.append(value) or value))
        value=self.run_source('let b=bot("prefix",fn(s){1},fn(p,s){tool("act",p);throw "failed"},fn(s){{done:true,context:null,output:null}});let first=await b();let again=try{await b()}catch e{e.code};bot_reconcile(b,{recovered:true},"checked externally");[first.status,again,bot_context(b).evidence,bot_context(b).history]',registry,['act'])
        self.assertEqual(seen,[1]);self.assertEqual(value[0],'needs_reconciliation');self.assertEqual(value[1],'bot_state_error')
        self.assertEqual(value[2],[{'reconciliation':'checked externally'}])
        self.assertTrue(any(row.get('actions') for row in value[3]))

    def test_borrowed_unguarded_task_never_starts(self):
        seen=[];registry=Registry();registry.register_tool(ToolSpec('act',lambda value,ctx:seen.append(value) or value))
        value=self.run_source('async fn old(){tool("act",1)};let borrowed=old();let b=bot("borrow",fn(s){null},fn(p,s){borrowed},fn(s){{done:true,context:null,output:null}});let r=await b();[r.status,task_info(borrowed).state]',registry,['act'])
        self.assertEqual(value,['needs_reconciliation','created']);self.assertEqual(seen,[])

    def test_baseline_source_and_graph_share_native_bot(self):
        source='let b=bot("graph",fn(s){s.input},fn(p,s){p},fn(s){{done:true,context:null,output:s.evidence}},null,20);await b(42)'
        a=self.run_source(source);b=self.run_source(source,mode='graph')
        self.assertEqual((a['done'],a['output']),(b['done'],b['output']));self.assertEqual(a['output'],[42])

    def test_finite_false_verification_commits_exhaustion_once_source_graph(self):
        source='let b=bot("bounded",fn(s){42},fn(p,s){p},fn(s){{done:false,context:null,output:s.evidence}},null,2);let r=await b();[r.status,r.done,r.output,r.state.iteration]'
        for mode in ('source','graph'):
            with self.subTest(mode=mode):self.assertEqual(self.run_source(source,mode=mode),['exhausted',False,[42,42],2])
