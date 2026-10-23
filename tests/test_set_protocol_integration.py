"""Protocol-backed Set values compose actual agents, modules and revisions."""
import json
import unittest
from chaos import Framework, Registry, ProviderSpec, ToolSpec, ToolCall, Final, Settings
from tests.test_team_functions import definition

KEY='''class Key {
var number=0;
fn __init__(self,number){self.number=number;null}
fn __hash__(self){hash(self.number)}
fn __eq__(self,other){if isinstance(other,Key){self.number==other.number}else{false}}
};'''


def program(source):
    return {'kind':'program','entry':'main.chaos','files':{'main.chaos':source},'languageVersions':{'main.chaos':2}}


class SetProtocolIntegration(unittest.TestCase):
    def test_agent_tool_uses_dedup_algebra_and_explicit_json_projection(self):
        requests=[]
        def provider(request,context):
            context.checkpoint();requests.append(request.input)
            return Final(request.history[-1]['output']) if request.history else ToolCall('work',request.input)
        registry=Registry();registry.register_provider(ProviderSpec('app.choose',provider))
        source=KEY+'''async fn work(value){await sleep(0);let before={Key(value),Key(value)};
let added=before|{Key(42)};let result=set_discard(added,Key(value));
[map(fn(key){key.number},before),map(fn(key){key.number},result),contains(added,Key(value))]}
agent a{provider:"app.choose"};let bound=agent_bind(a,[agent_tool("work",work)]);await bound(40)'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('set-agent',source=source,capabilities=[])
            self.assertEqual(app.start('set-agent',1,None,mode='source').result(10),[[40],[42],True])
        self.assertEqual(requests,[40,40])

    def test_team_and_swarm_exchange_actual_set_values_and_preserve_old_states(self):
        source='import "std/swarm" as S;fn make(value){'+KEY+'''
{Key(value),Key(value)}}
fn work(values:Set){fold(fn(total,key){total+key.number},2,values)}
let model=team_from(input);
let maker=team_role(team_bind(model,{worker:make}),"worker");
let role=team_role(team_bind(model,{worker:work}),"worker");
async fn worker(value){let values=await maker(value);await role(values)}
let old=S.create({a:40,b:41});
let after=await S.step(old,{a:worker,b:worker},prepare=fn(context){context.state});
[old.states,after.states,after.tick]'''
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('set-team',source=source,capabilities=[])
            run=app.start('set-team',1,definition(),mode='source')
            self.assertEqual(run.result(10),[{'a':40,'b':41},{'a':42,'b':43},1])
            events=app.events('set-team',run_id=run.id)['items']
            self.assertEqual(sum(e['type']=='team.role.started'for e in events),4)
            self.assertEqual(sum(e['type']=='team.role.completed'for e in events),4)

    def test_code_and_module_factories_preserve_native_members_and_nested_keys(self):
        library='''fn run(values:Set){let combined={values,(values,)};
let mapping=hash_map([(combined,42)]);[count(values),mapping[{(values,),values}],hash(combined)==hash({(values,),values})]};export{run};'''
        source=KEY+'''let count=function_from(code_from("fn(values:Set)->Int{length(values)}"));
let module=module_from(program_from(input),{count:count});let values={Key(20),Key(22),Key(20)};module.run(values)'''
        self.assertEqual(Framework.evaluate(source,{'input':program(library)}),[2,42,True])

    def test_channel_iterator_class_fields_retain_real_identity(self):
        source=KEY+'''let first=Key(42);let values={first,Key(42)};
class Box{let keys=values};let Box::{keys:keys}=Box();let ch=channel(1);
await channel_send(ch,keys);let received=(await channel_receive(ch)).value;channel_close(ch);
let cursor=iter(received);let item=next(cursor).value;
let is_first=generic_method(generic_method(generic_function("is_first"),["Any"],fn(value){false}),[generic_eql(first)],fn(value){true});
[item.number,is_first(item),generic_eql_info(generic_eql(item)).comparison,
 next(cursor).done,contains(received,Key(42)),length(keys)]'''
        self.assertEqual(Framework.evaluate(source),[42,True,'identity',True,True,1])

    def test_hash_effect_cannot_gain_team_authority(self):
        calls=[];registry=Registry();registry.register_tool(ToolSpec('app.hash',lambda value,context:calls.append(value) or value))
        source='''class Key{fn __hash__(self){tool("app.hash",42)}};let key=Key();let values={key};
let crew=team_bind(team_from(input),{worker:fn(value){contains(value,key)}});
try{await team_role(crew,"worker")(values)}catch e{e.code}'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('set-caps',source=source,capabilities=['app.hash'])
            self.assertEqual(app.start('set-caps',1,definition(),mode='source').result(10),'capability_error')
        self.assertEqual(calls,[42])

    def test_program_probe_and_adoption_rebuild_protocol_set_from_source(self):
        candidate=KEY+'''let choices={Key(input),Key(input),Key(22)};
fold(fn(total,key){total+key.number},0,choices)'''
        source='''let base=current_program();let candidate=program_from(input);
let report=await program_probe(candidate,[{input:20,expect:42}]);
let receipt=await program_adopt(base,candidate,[{input:20,expect:42}],"native object set");
[report.passed,receipt.version,program_metadata(current_program()).origin.version]'''
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('set-adopt',source=source,capabilities=['program.adopt'])
            old=app.start('set-adopt',1,program(candidate),mode='source')
            self.assertEqual(old.result(10),[True,2,1])
            self.assertEqual(app.start('set-adopt',2,20,mode='source').result(10),42)
            self.assertEqual(old.get()['snapshot']['source'],source)


if __name__=='__main__':unittest.main()
