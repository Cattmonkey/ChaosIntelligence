"""Native attribute hooks compose actual agents, roles and program revisions."""
import unittest
from chaos import Framework,Registry,ProviderSpec,ToolSpec,ToolCall,Final,Settings
from tests.test_team_functions import definition

BOX='''class Box {
var base:Int=0;
fn __getattribute__(self,name){if name=="answer"{default_getattr(self,"base")+2}else{default_getattr(self,name)}}
fn __getattr__(self,name){if name=="missing"{40}else{throw AttributeError(name)}}
fn __setattr__(self,name,item){default_setattr(self,name,item);null}
fn __delattr__(self,name){default_delattr(self,name);null}
fn __dir__(self){["base","answer","answer"]}
};'''
PROXY='''class RoleProxy {
var bound=null;
fn __init__(self,bound){default_setattr(self,"bound",bound);null}
fn __getattr__(self,name){team_role(default_getattr(self,"bound"),name)}
};'''

def program(source):
    return {'kind':'program','entry':'main.chaos','files':{'main.chaos':source},'languageVersions':{'main.chaos':2}}


class AttributeProtocolIntegration(unittest.TestCase):
    def test_agent_tool_executes_five_hooks_and_projects_json_explicitly(self):
        requests=[]
        def provider(request,context):
            context.checkpoint();requests.append(request.input)
            return Final(request.history[-1]['output'])if request.history else ToolCall('work',request.input)
        registry=Registry();registry.register_provider(ProviderSpec('app.choose',provider))
        source=BOX+'''async fn work(value){let box=Box();box.base=value;let before=box.answer;
let names=dir(box);let fallback=box.missing;delattr(box,"base");[before,names,fallback,box.answer]}
agent a{provider:"app.choose"};await agent_bind(a,[agent_tool("work",work)])(40)'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('attribute-agent',source=source,capabilities=[])
            self.assertEqual(app.start('attribute-agent',1,None,mode='source').result(10),[42,['answer','answer','base'],40,2])
        self.assertEqual(requests,[40,40])

    def test_dynamic_team_role_proxy_composes_with_swarm_json_states(self):
        source='import "std/swarm" as S;'+PROXY+'''
let proxy=RoleProxy(team_bind(team_from(input),{worker:fn(value){value+2}}));
async fn worker(value){await proxy.worker(value)}
let before=S.create({a:40,b:41});let after=await S.step(before,{a:worker,b:worker},prepare=fn(context){context.state});
[before.states,after.states,after.tick]'''
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('attribute-team',source=source,capabilities=[])
            run=app.start('attribute-team',1,definition(),mode='source')
            self.assertEqual(run.result(10),[{'a':40,'b':41},{'a':42,'b':43},1])
            events=app.events('attribute-team',run_id=run.id)['items']
            self.assertEqual(sum(e['type']=='team.role.completed'for e in events),2)

    def test_dynamic_role_cannot_recover_tool_authority(self):
        calls=[];registry=Registry();registry.register_tool(ToolSpec('app.secret',lambda value,context:calls.append(value)or 42))
        source=PROXY+'''let proxy=RoleProxy(team_bind(team_from(input),{worker:fn(value){tool("app.secret",value)}}));
try{await proxy.worker(40)}catch e{e.code}'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('attribute-caps',source=source,capabilities=['app.secret'])
            self.assertEqual(app.start('attribute-caps',1,definition(),mode='source').result(10),'capability_error')
        self.assertEqual(calls,[])

    def test_async_missing_getter_returns_cold_task_to_agent_runtime(self):
        calls=[];registry=Registry()
        def provider(request,context):context.checkpoint();calls.append(request.input);return Final(request.input+2)
        registry.register_provider(ProviderSpec('app.compute',provider))
        source='''agent worker{provider:"app.compute"};class Proxy{async fn __getattr__(self,name){worker(40)}};
let value=Proxy().answer;[type_of(value),await value]'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('attribute-cold',source=source,capabilities=[])
            self.assertEqual(app.start('attribute-cold',1,None,mode='source').result(10),['Task',42])
        self.assertEqual(calls,[40])

    def test_code_module_and_metaclass_share_original_attribute_nodes(self):
        library=BOX+'''class Meta(Type){fn __getattr__(cls,name){if name=="answer"{42}else{throw AttributeError(name)}}}
class Service(metaclass=Meta){};
fn run(){let box=Box();box.base=40;[read(box),Service.answer]};export{run};'''
        source='''let read=function_from(code_from("fn(value){value.answer}"));
let component=module_from(program_from(input),{read:read});component.run()'''
        self.assertEqual(Framework.evaluate(source,{'input':program(library)}),[42,42])

    def test_program_probe_adoption_preserves_old_snapshot_and_hook_source(self):
        candidate=BOX+'let box=Box();box.base=input;box.answer'
        source='''let base=current_program();let candidate=program_from(input);
let report=await program_probe(candidate,[{input:40,expect:42}]);
let receipt=await program_adopt(base,candidate,[{input:40,expect:42}],"native attributes");
[report.passed,receipt.version,program_metadata(current_program()).origin.version]'''
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('attribute-adopt',source=source,capabilities=['program.adopt'])
            old=app.start('attribute-adopt',1,program(candidate),mode='source')
            self.assertEqual(old.result(10),[True,2,1]);self.assertEqual(app.start('attribute-adopt',2,40,mode='source').result(10),42)
            self.assertEqual(old.get()['snapshot']['source'],source)


if __name__=='__main__':unittest.main()
