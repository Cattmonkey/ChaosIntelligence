"""Augmented syntax composes with actual Agent tools, recording and adoption."""
import unittest
from chaos import Final, Framework, ProviderSpec, Registry, Settings, ToolCall, ToolSpec


class AugmentedIntegration(unittest.TestCase):
    def registry(self):
        registry=Registry()
        def choose(request,context):
            context.checkpoint()
            return ToolCall('calculate',request.input) if request.step==1 else Final(request.history[-1]['output'])
        registry.register_provider(ProviderSpec('choose',choose))
        return registry

    def test_actual_language_tool_inplace_descriptor_and_recorded_receipts(self):
        seen=[];registry=self.registry()
        registry.register_tool(ToolSpec('audit',lambda value,context:seen.append(value) or value))
        source='''var calls:Int=0;
class Operator{fn __get__(self,obj,owner){fn(other){
 calls+=1;obj.value+=other;tool("audit",obj.value);obj
}}}
class Counter{var value:Int=40;let __iadd__=Operator()}
fn work(x:Int)->Int{var counter=Counter();let alias=counter;counter+=x;alias.value}
agent worker{provider:"choose",tools:["audit"]};
let bound=agent_bind(worker,[agent_tool("calculate",work,input_type="Int",output_type="Int",capabilities=["audit"])]);
let recorded=await record_skill(bound,2);
[recorded.output,calls,map(fn(a){a.input},mode_info(recorded.skill).actions),await replay_skill(recorded.skill)]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('aug-record',source=source,capabilities=['audit'])
            run=app.start('aug-record',1,None,mode='source')
            self.assertEqual(run.result(8),[42,1,[42],[42]])
            events=app.events('aug-record',run_id=run.id,limit=300)['items']
            self.assertTrue(any(e['type']=='agent.completed' for e in events))
        self.assertEqual(seen,[42,42])

    def test_inplace_does_not_expand_tool_capabilities(self):
        seen=[];registry=self.registry()
        registry.register_tool(ToolSpec('secret',lambda value,context:seen.append(value) or 42))
        source='''class Counter{fn __iadd__(self,x){tool("secret",x)}}
fn work(x){var counter=Counter();counter+=x;counter}
agent worker{provider:"choose",tools:[]};
let bound=agent_bind(worker,[agent_tool("calculate",work)]);
try{await bound(2)}catch e{"denied"}'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('aug-denied',source=source,capabilities=['secret'])
            self.assertEqual(app.start('aug-denied',1,None,mode='source').result(8),'denied')
        self.assertEqual(seen,[])

    def test_async_inplace_remains_cold_until_explicit_await(self):
        seen=[];registry=Registry()
        def calculate(request,context):context.checkpoint();seen.append(request.input);return Final(request.input+40)
        registry.register_provider(ProviderSpec('calculate',calculate))
        source='''agent worker{provider:"calculate",tools:[]};
class Counter{async fn __iadd__(self,x){worker(x)}}
var counter=Counter();counter+=2;
[type_of(counter),await counter]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('aug-cold',source=source,capabilities=[])
            self.assertEqual(app.start('aug-cold',1,None,mode='source').result(8),['Task',42])
            app.create_project('aug-unused',source=source.replace('[type_of(counter),await counter]','type_of(counter)'),capabilities=[])
            self.assertEqual(app.start('aug-unused',1,None,mode='source').result(8),'Task')
        self.assertEqual(seen,[2])

    def test_language_program_edit_probe_and_adopt_augmented_tool(self):
        main='''import "behavior.chaos" as B;
if input.kind=="adopt"{
 let base=current_program();let ir=program_ir(base,"behavior.chaos");
 let operation=ir.root.statements[0].function.body.statements[1];
 let candidate=program_edit(base,"behavior.chaos",[{op:"set",id:operation.id,field:"operator",value:"**"}]);
 await program_adopt(base,candidate,[{entry:"tests.chaos",input:3,expect:8}],reason="power implementation")
}else{
 agent worker{provider:"choose",tools:[]};
 let bound=agent_bind(worker,[agent_tool("calculate",B.work,input_type="Int",output_type="Int")]);
 await bound(input.value)
}'''
        library='fn work(x:Int)->Int{var value:Int=2;value+=x;value};export{work}'
        files={'behavior.chaos':library,'tests.chaos':'import "behavior.chaos" as B;B.work(input)'}
        with Framework(registry=self.registry(),settings=Settings(':memory:')) as app:
            app.create_project('aug-evolve',source=main,files=files,capabilities=['program.adopt'])
            old=app.start('aug-evolve',1,{'kind':'run','value':3},mode='source')
            self.assertEqual(old.result(8),5)
            app.start('aug-evolve',1,{'kind':'adopt'},mode='source').result(8)
            self.assertEqual(app.get_project('aug-evolve')['version'],2)
            self.assertEqual(app.start('aug-evolve',2,{'kind':'run','value':3},mode='source').result(8),8)
            self.assertEqual(old.get()['snapshot']['files']['behavior.chaos'],library)


if __name__=='__main__':unittest.main()
