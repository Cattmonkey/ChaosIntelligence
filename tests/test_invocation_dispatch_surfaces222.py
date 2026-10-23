"""Actual invocation dispatch phases against the exact original generator algorithms."""
import copy,json,unittest
from contextlib import nullcontext
from unittest.mock import patch
from chaos import Framework,compile_artifact,load_artifact
from chaos.language import Evaluator,Native,LanguageError
from chaos.execution_frames import ensure_cursor
from chaos.execution_invocation import InvocationDispatchFrame
from chaos.suspension import Effect,CHECKPOINT
import chaos.control_prompts as control
import chaos.multiple_values as values
import _invocation_fixture222 as fixture
import _multiple_value_surface220 as mv
import test_multiple_value_surfaces220 as previous
import test_module_frame_surfaces as modules
from test_operator_execution_frames import active
from _invocation_reference222 import reference

class InvocationDispatchSurfaces222(unittest.TestCase):
    run_source=modules.ModuleFrameSurfaces.run_source
    def differential(self,source,expected,**options):
        old,oldseen,_=fixture.run(self,source,legacy=True,**options)
        new,seen,pending=fixture.run(self,source,**options)
        self.assertEqual(new,old);self.assertEqual(new[0],('value',expected))
        self.assertFalse(oldseen);self.assertTrue(seen)
        return seen,pending
    def test_callable_descriptor_lookup_is_the_actual_pending_owner_and_never_replayed(self):
        seen,pending=self.differential(fixture.LOOKUP,42,phase='lookup')
        self.assertEqual(pending,[('lookup',0)])
        self.assertTrue(any(name=='ChaosInstance'for frame,cursor,name in seen))
        for legacy in (True,False):
            fault=LanguageError('descriptor fixture failure',code='lookup_fixture')
            row,_,_=fixture.run(self,fixture.LOOKUP,legacy=legacy,phase='lookup',fault=fault)
            self.assertEqual(row[0][0],'error');self.assertEqual(row[0][1][1]['code'],'lookup_fixture')
            self.assertTrue(row[0][1][2]);self.assertEqual(len(row[2]),1)
            if legacy:golden=copy.deepcopy(row)
            else:self.assertEqual(row,golden)
    def test_ordered_native_request_cascade_keeps_the_next_branch_index_across_real_wait(self):
        # A trusted host resolver hands a later real request to the unchanged
        # multiple-value resolver. Only that embedding leaf is controlled;
        # receive/resolve, invocation, scheduler and Effect all remain real.
        for legacy in (True,False):
            calls=[]
            def setup(e):
                producer=Native('producer222',lambda:Effect('tool',{'tool':'native_hold','value':40}),e)
                def seed():calls.append('seed');return control.Request('fixture-handoff',())
                e.env.bind('seed222',Native('seed222',seed,e))
                e._fixture_producer222=producer
            def handoff(e,request):
                self.assertEqual(request.operation,'fixture-handoff');calls.append('control')
                if False:yield None
                return values.request(e,'list',producers=(e._fixture_producer222,))
            with patch.object(control,'resolve',handoff):row,seen,pending=fixture.run(self,fixture.NATIVE,legacy=legacy,phase='native',setup=setup)
            self.assertEqual(calls,['seed','control']);self.assertEqual(row[0],('value',[40]));self.assertEqual(len(row[2]),1)
            if legacy:golden=row
            else:self.assertEqual(row,golden);self.assertEqual(pending,[('native',2)])
    def test_agent_real_future_success_and_original_language_error_identity(self):
        self.differential(fixture.AGENT,42,phase='agent',agent=True)
        for legacy in (True,False):
            fault=LanguageError('finite agent failure',code='agent_fixture')
            row,_,_=fixture.run(self,fixture.AGENT,legacy=legacy,phase='agent',agent=True,fault=fault)
            self.assertEqual(row[0][0],'error');self.assertTrue(row[0][1][2]);self.assertEqual(row[0][1][1]['code'],'agent_fixture')
            self.assertEqual(len(row[2]),1)
            if legacy:golden=copy.deepcopy(row)
            else:self.assertEqual(row,golden)
    def test_direct_agent_and_native_effect_send_throw_and_repeated_close_match_original_frontier(self):
        # The production _s_invoke gateway runs under an actual scheduler
        # holder; manual transport exposes the exact throw/close position.
        for kind in ('agent','native'):
            for action in ('send','language','exception','stop','generator_exit','close'):
                with self.subTest(kind=kind,action=action):
                    rows=[]
                    for legacy in (True,False):
                        e=Evaluator(timeout=20,agent_handler=lambda n,c,v:v)
                        target=e.run('agent worker{provider:"fixture",tools:[]};worker')if kind=='agent'else Native('direct222',lambda:Effect('tool',{'tool':'direct','value':40}),e)
                        faults={'language':LanguageError('direct fault',code='direct_fixture'),'exception':ValueError('direct fault'),'stop':StopIteration('sentinel'),'generator_exit':GeneratorExit('direct exit')}
                        fault=faults.get(action);outcomes=[]
                        def outcome(call):
                            try:return ('value',call())
                            except StopIteration as error:return ('returned',error.value)
                            except BaseException as error:return ('error',fixture.error_row(error,fault))
                        with reference()if legacy else nullcontext(),fixture.observe_entries()as seen,active(e):
                            cursor=ensure_cursor(e,e._s_invoke(target,[40]if kind=='agent'else []))
                            request=cursor.send(None)
                            while request is CHECKPOINT:request=cursor.send(None)
                            self.assertIs(type(request),Effect);self.assertEqual(request.kind,kind if kind=='agent'else 'tool')
                            if not legacy:
                                frames=[frame for frame,owned in fixture.owned_frames(self,cursor)if frame.state.fn is target]
                                self.assertEqual(len(frames),1);self.assertEqual(frames[0].phase,'agent'if kind=='agent'else 'native')
                            if action=='send':outcomes.append(outcome(lambda:cursor.send(42)))
                            elif action!='close':outcomes.append(outcome(lambda:cursor.throw(fault)))
                            outcomes.append(outcome(cursor.close));outcomes.append(outcome(cursor.close))
                            outcomes.append(outcome(lambda:cursor.throw(LanguageError('after close',code='after_close'))))
                            self.assertTrue(cursor.closed);self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])
                            rows.append((outcomes,e.steps,e.depth,len(e.frames),e.handled_error,e.error_context))
                            self.assertEqual(bool(seen),not legacy)
                        self.assertFalse(e.suspension.active)
                    self.assertEqual(rows[1],rows[0])
    def test_cold_tasks_generators_and_full_completion_packets_keep_original_charging(self):
        self.differential(mv.SOURCE,mv.EXPECTED)
    def test_source_IR_Code_explicit_compile_and_saved_artifact_use_same_invocation_family(self):
        source='class Callable{fn __call__(s,x){return_values(x+2,"call")}};fn raw(x){multiple_value_list(fn(){Callable()(x)})};let f=compile_function(raw,target="auto-flow");f(40)'
        expected=[42,'call']
        self.differential(source,expected)
        for legacy in (True,False):
            with reference()if legacy else nullcontext(),fixture.observe_entries()as seen:
                for mode in ('source','graph'):self.assertEqual(self.run_source(source,mode=mode,capabilities=[])[0],expected)
                artifact=compile_artifact(source);bundle=load_artifact(json.loads(json.dumps(artifact,sort_keys=True)))
                self.assertEqual(Framework.evaluate(bundle),expected)
                self.assertEqual(Framework.evaluate('let f=function_from(code_from("fn(x){multiple_value_list(fn(){return_values(x,2)})}"));f(40)'),[40,2])
            self.assertEqual(bool(seen),not legacy)
            if not legacy:self.assertTrue({'Native','Function','ChaosClass','ChaosInstance','BoundMethod'}<=set(name for frame,cursor,name in seen))
    def test_AgentTool_Team_and_Bot_production_admission_uses_the_same_actual_dispatch_frame(self):
        for legacy in (True,False):
            with reference()if legacy else nullcontext(),fixture.observe_entries()as seen:
                previous.MultipleValueSurfaces220.test_AgentTool_Team_and_Bot_use_explicit_full_values_and_once_effects(self)
            self.assertEqual(bool(seen),not legacy)
            if not legacy:self.assertTrue({'Native','Function','Bot','BoundAgent','BoundTeam'}<=set(name for frame,cursor,name in seen))
    def test_software_Robot_single_use_command_and_denied_admission_use_actual_dispatch(self):
        for legacy in (True,False):
            with reference()if legacy else nullcontext(),fixture.observe_entries()as seen:
                previous.MultipleValueSurfaces220.test_software_Robot_admission_and_multiple_value_receipt_do_not_replay(self)
            self.assertEqual(bool(seen),not legacy)
            if not legacy:self.assertIn('Robot',set(name for frame,cursor,name in seen))
