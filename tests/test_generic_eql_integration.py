"""Value specializers compose existing agents, source factories and revisions."""
import unittest
from chaos import Framework, Registry, ProviderSpec, ToolCall, Final, Settings
from tests.test_team_functions import definition


def program(source):
    return {'kind':'program','entry':'main.chaos','files':{'main.chaos':source},'languageVersions':{'main.chaos':2}}


class GenericEqlIntegration(unittest.TestCase):
    def test_actual_agent_tool_uses_value_primary_next_and_cold_task(self):
        calls = []
        def choose(request, context):
            context.checkpoint()
            calls.append(request.input)
            return Final(request.history[-1]['output']) if request.history else ToolCall('work',request.input)
        registry = Registry();registry.register_provider(ProviderSpec('app.choose',choose))
        source = '''var log=[];
let base=generic_method(generic_function("work"),["Int"],async fn(x){log=append(log,"base");await sleep(0);x+1});
let work=generic_method(base,[generic_eql(40)],async fn(next,x){log=append(log,"value");let n=await next();n+1},with_next=true);
agent a{provider:"app.choose"};let bound=agent_bind(a,[agent_tool("work",work)]);
[await bound(40),await bound(41),log]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('eql-agent',source=source,capabilities=[])
            run=app.start('eql-agent',1,None,mode='source')
            self.assertEqual(run.result(10),[42,42,['value','base','base']])
        self.assertEqual(calls,[40,40,41,41])

    def test_team_role_calls_exported_value_dispatch_with_keywords(self):
        source='''import "worker.chaos" as W;
let crew=team_bind(team_from(input),{worker:W.work});await team_role(crew,"worker")("review",offset=2)'''
        worker='''import "std/generic" as G;
let base=G.generic_method(G.generic_function("work"),["String"],fn(x,*,offset=0){offset});
let work=G.generic_method(base,[G.generic_eql("review")],fn(next,x,*,offset=0){40+next()},with_next=true);export{work};'''
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('eql-team',source=source,files={'main.chaos':source,'worker.chaos':worker},capabilities=[])
            run=app.start('eql-team',1,definition(),mode='source')
            self.assertEqual(run.result(10),42)
            self.assertEqual([e['type'] for e in app.events('eql-team',run_id=run.id)['items'] if e['type'].startswith('team.role.')],['team.role.started','team.role.completed'])

    def test_std_swarm_awaits_real_value_dispatch_workers_once(self):
        source='''import "std/swarm" as S;
var log=[];
let base=generic_method(generic_function("work"),["Int"],async fn(x){log=append(log,x);x+1});
let work=generic_method(base,[generic_eql(40)],async fn(next,x){let n=await next();n+1},with_next=true);
let old=S.create({a:40,b:41});let changed=await S.step(old,{a:work,b:work},prepare=fn(context){context.state});
[old.states,changed.states,changed.tick,sort(log)]'''
        self.assertEqual(Framework.evaluate(source),[{'a':40,'b':41},{'a':42,'b':42},1,[40,41]])

    def test_code_and_module_factories_retain_identity_specializer(self):
        source='''let token={kind:"private"};let specializer=generic_eql(token);
let implementation=function_from(code_from("fn(value){42}"));
let library=program_from(input);
let module=module_from(library,{selector:specializer,implementation:implementation});
[module.work(token),module.work({kind:"private"}),generic_eql_info(specializer)]'''
        library='''let base=generic_method(generic_function("work"),["Any"],fn(value){0});
let work=generic_method(base,[selector],implementation);export{work};'''
        self.assertEqual(Framework.evaluate(source,{'input':program(library)}),[42,0,{'kind':'eql','comparison':'identity','valueType':'Record'}])

    def test_program_adoption_rebuilds_value_method_without_mutating_old_snapshot(self):
        source='''let old=generic_method(generic_function("old"),[generic_eql(40)],fn(value){41});
let base=current_program();let candidate=program_from(input);
let report=await program_probe(candidate,[{input:40,expect:42},{input:41,expect:42}]);
let receipt=await program_adopt(base,candidate,[{input:40,expect:42}],"value method");
[report.passed,receipt.version,old(40),program_metadata(current_program()).origin.version]'''
        candidate='''let base=generic_method(generic_function("work"),["Int"],fn(value){value+1});
let work=generic_method(base,[generic_eql(40)],fn(next,value){next()+1},with_next=true);work(input)'''
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('eql-adopt',source=source,capabilities=['program.adopt'])
            run=app.start('eql-adopt',1,program(candidate),mode='source')
            self.assertEqual(run.result(10),[True,2,41,1])
            self.assertEqual(app.get_run(run.id)['snapshot']['source'],source)
            self.assertEqual(app.start('eql-adopt',2,40,mode='source').result(10),42)
            self.assertEqual(app.start('eql-adopt',2,41,mode='source').result(10),42)


if __name__=='__main__':unittest.main()
