"""Real AgentTool/Program/debugger routes share structural state semantics."""
import time
import unittest
from chaos import Final, Framework, ProviderSpec, Registry, Settings, ToolCall, ToolSpec


class PatternStateIntegration(unittest.TestCase):
    def registry(self,reply=None):
        registry=Registry()
        def choose(request,context):
            context.checkpoint()
            return ToolCall('update',request.input) if request.step==1 else Final(request.history[-1]['output'])
        registry.register_provider(ProviderSpec('choose',choose))
        def planner(request,context):context.checkpoint();return Final(reply if reply is not None else {'pair':[20,22],'trace':'plan'})
        registry.register_provider(ProviderSpec('planner',planner))
        return registry

    def test_agent_result_updates_closure_inside_recorded_resource_scope(self):
        seen=[];registry=self.registry()
        registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or value))
        source='''var left:Int=1;var right:Int=2;
agent planner{provider:"planner",tools:[]};
class Resource{var reply=null;fn __init__(self,value){self.reply=value;null}
 fn __enter__(self){tool("audit","enter");self.reply}
 fn __exit__(self,error){tool("audit","exit");null}}
fn update(value){with Resource(planner(value)) as {pair:[a,b],.._}{[left,right]=[a,b];[left,right]}}
agent worker{provider:"choose",tools:["audit"]};
let bound=agent_bind(worker,[agent_tool("update",update,capabilities=["audit"])]);
let recorded=await record_skill(bound,0);
[recorded.output,left,right,map(fn(a){a.input},mode_info(recorded.skill).actions),await replay_skill(recorded.skill)]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('state-record',source=source,capabilities=['audit'])
            run=app.start('state-record',1,None,mode='source')
            self.assertEqual(run.result(8),[[20,22],20,22,['enter','exit'],['enter','exit']])
            events=app.events('state-record',run_id=run.id,limit=300)['items']
            self.assertEqual(sum(e['type']=='agent.completed' for e in events),2)
        self.assertEqual(seen,['enter','exit','enter','exit'])

    def test_bad_agent_second_leaf_preserves_both_typed_cells_and_cleans_up(self):
        seen=[];registry=self.registry({'count':99,'label':0})
        registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or None))
        source='''var count:Int=7;var label:String="ready";
agent planner{provider:"planner",tools:[]};
class Resource{fn __enter__(self){planner(null)}fn __exit__(self,error){tool("audit",error.code);null}}
fn update(value){try{with Resource() as {count:newCount,label:newLabel}{[count,label]=[newCount,newLabel]}}catch e{null};[count,label]}
agent worker{provider:"choose",tools:["audit"]};
let bound=agent_bind(worker,[agent_tool("update",update,capabilities=["audit"])]);await bound(0)'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('state-bad',source=source,capabilities=['audit'])
            self.assertEqual(app.start('state-bad',1,None,mode='source').result(8),[7,'ready'])
        self.assertEqual(seen,['runtime_error'])

    def test_async_agent_entry_mismatch_uses_actual_exit_effect(self):
        seen=[];registry=self.registry()
        registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or None))
        source='''agent planner{provider:"planner",tools:[]};
class Resource{async fn __aenter__(self){planner(null)}
 async fn __aexit__(self,error){tool("audit",error.code);null}}
async fn update(value){try{async with Resource() as {missing:answer}{tool("audit","body")}}catch e{e.code}}
agent worker{provider:"choose",tools:["audit"]};
let bound=agent_bind(worker,[agent_tool("update",update,capabilities=["audit"])]);await bound(0)'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('state-async',source=source,capabilities=['audit'])
            self.assertEqual(app.start('state-async',1,None,mode='source').result(8),'match_error')
        self.assertEqual(seen,['match_error'])

    def test_pattern_scope_does_not_add_tool_authority(self):
        seen=[];registry=self.registry()
        registry.register_tool(ToolSpec('secret',lambda value,context:seen.append(value) or 42))
        source='''class Resource{fn __enter__(self){[42]}fn __exit__(self,error){null}}
fn update(value){with Resource() as [answer]{tool("secret",answer)}}
agent worker{provider:"choose",tools:[]};let bound=agent_bind(worker,[agent_tool("update",update)]);
try{await bound(0)}catch e{"denied"}'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('state-denied',source=source,capabilities=['secret'])
            self.assertEqual(app.start('state-denied',1,None,mode='source').result(8),'denied')
        self.assertEqual(seen,[])

    def test_program_self_edit_probe_adopt_changes_next_agent_tool(self):
        main='''import "behavior.chaos" as B;
if input.kind=="adopt"{
 let base=current_program();let ir=program_ir(base,"behavior.chaos");
 let literal=ir.root.statements[2].function.body.statements[0].value.items[1].right;
 let candidate=program_edit(base,"behavior.chaos",[{op:"set",id:literal.id,field:"value",value:2}]);
 await program_adopt(base,candidate,[{entry:"tests.chaos",input:21,expect:[21,23]}],reason="structured state revision")
}else{
 agent worker{provider:"choose",tools:[]};let bound=agent_bind(worker,[agent_tool("update",B.work)]);await bound(input.value)
}'''
        library='var count:Int=0;var total:Int=0;fn work(x:Int){[count,total]=[x,x+1];[count,total]};export{work}'
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('state-evolve',source=main,files={'behavior.chaos':library,'tests.chaos':'import "behavior.chaos" as B;B.work(input)'},capabilities=['program.adopt'])
            old=app.start('state-evolve',1,{'kind':'run','value':21},mode='source')
            self.assertEqual(old.result(8),[21,22])
            app.start('state-evolve',1,{'kind':'adopt'},mode='source').result(8)
            self.assertEqual(app.get_project('state-evolve')['version'],2)
            self.assertEqual(app.start('state-evolve',2,{'kind':'run','value':21},mode='source').result(8),[21,23])
            self.assertEqual(old.get()['snapshot']['files']['behavior.chaos'],library)

    def test_debugger_observes_committed_group_inside_real_tool(self):
        source='\n'.join(['var left:Int=1;var right:Int=2;',
            'class Resource{fn __enter__(self){[20,22]}fn __exit__(self,error){null}}',
            'fn update(value){','with Resource() as [a,b]{','[left,right]=[a,b];','[left,right]','}','}',
            'agent worker{provider:"choose",tools:[]};',
            'let bound=agent_bind(worker,[agent_tool("update",update)]);','await bound(0)'])
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('state-debug',source=source,capabilities=[])
            run=app.start_debug('state-debug',1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':6}])
            try:
                deadline=time.monotonic()+6
                while True:
                    state=run.debugger()
                    if state['status']=='paused':break
                    self.assertEqual(state['status'],'running',str(run.get()))
                    self.assertLess(time.monotonic(),deadline);time.sleep(.005)
                values={v['name']:v['value'] for frame in state['frames'] for v in frame['locals']}
                self.assertEqual([values['a'],values['b'],values['left'],values['right']],[20,22,20,22])
                run.debug_command('breakpoints',breakpoints=[]);run.debug_command('continue',pause_id=state['pauseId'])
                self.assertEqual(run.result(5),[20,22])
            finally:run.cancel()


if __name__=='__main__':unittest.main()
