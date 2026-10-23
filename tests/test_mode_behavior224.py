"""Mode behavior against the exact original algorithms, not implementation copies."""
import copy
import unittest
from contextlib import nullcontext
from chaos.language import LanguageError, ResourceLimit
from chaos.suspension import TaskCancelled
import _mode_fixture224 as fixture
from _mode_reference224 import original_modes, fixed_execution_ids
import test_agent_modes as original_agent
import test_parallel_plans as original_parallel
import test_modes_integration as original_integration

class ModeBehavior224(unittest.TestCase):
    evaluate=original_agent.AgentModeTests.evaluate
    start=original_agent.AgentModeTests.start
    evaluator=original_parallel.ParallelPlanTests.evaluator

    def differential(self,source,expected=None,**options):
        old,oldframes,_,_,_=fixture.snapshot(self,source,legacy=True,**options)
        new,frames,e,error,timing=fixture.snapshot(self,source,**options)
        self.assertEqual(new,old)
        if expected is not None:self.assertEqual(new[0],('value',expected))
        self.assertEqual(oldframes,([],[]))
        return new,frames,e,error,timing

    def original_assertions(self,cls,names,required):
        for legacy in (True,False):
            with self.subTest(original=legacy),original_modes()if legacy else nullcontext(),fixture.observe_frames(self)as observed:
                for name in names:
                    with self.subTest(case=name):getattr(cls,name)(self)
            if legacy:self.assertEqual(observed,([],[]))
            else:
                actual={type(frame).__name__ for frame,cursor in observed[0]}
                self.assertTrue(set(required)<=actual,(required,actual))

    def test_cold_operations_do_not_construct_or_run_algorithm_state(self):
        source='''var calls=0;let p=plan([{id:"x",call:"f"}]);let g=goal("cold","p","v");
let a=run_plan(p,{f:fn(c){calls=calls+1;42}});let b=pursue(g,{});let c=record_skill(fn(x){calls=calls+1});let d=save_mode(p,"saved");let f=load_mode("saved");[calls,type_of(a),type_of(b),type_of(c),type_of(d),type_of(f)]'''
        row,frames,_,_,_=self.differential(source,[0,'Task','Task','Task','Task','Task'])
        self.assertEqual(frames,([],[]));self.assertEqual(row[2],[])

    def test_serial_scan_does_not_restart_after_each_completed_dependency(self):
        row,frames,_,_,_=self.differential(fixture.SERIAL,[{'b':0,'a':40,'end':[['a'],42]},['b','a','end']])
        names={type(f).__name__ for f,c in frames[0]}
        self.assertTrue({'ModeRunPlanFrame','ModeSerialPlanFrame','ModePlanStepFrame','ModeInvokeFrame'}<=names)
        events=[r for r in row[3]if r['event']=='mode.plan.step.started']
        self.assertEqual([r['stepId']for r in events],['b','a','end'])
        self.assertTrue(all(r['executionId']=='plan-'+format(1,'032x')for r in events))

    def test_async_actions_are_once_and_receive_only_declared_dependency_results(self):
        row,frames,_,_,_=self.differential(fixture.SOURCE,fixture.EXPECTED)
        invokes=[f for f,c in frames[0]if type(f).__name__=='ModeInvokeFrame']
        self.assertEqual(len(invokes),3);self.assertTrue(any(f.phase=='done'and f.result==42 for f in invokes))

    def test_preflight_and_unconsumed_stream_rejection_match_original_full_error_trace(self):
        for source in (
            'var calls=0;let p=plan([{id:"a",call:"ok"},{id:"b",call:"missing"}],max_concurrency=2);let error=try{await run_plan(p,{ok:fn(c){calls=calls+1}})}catch e{e.code};[calls,error]',
            'fn* stream(){yield 1};try{await run_plan(plan([{id:"x",call:"f"}]),{f:fn(c){stream()}})}catch e{e.code}'):
            with self.subTest(source=source):
                row,_,_,_,_=self.differential(source)
                self.assertEqual(row[0][0],'value');self.assertEqual(row[2],[])
        self.assertEqual(row[0][1],'mode_error')

    def test_parallel_real_group_overlap_and_successor_admission_order(self):
        self.original_assertions(original_parallel.ParallelPlanTests,[
            'test_independent_effects_overlap_with_bounded_deterministic_admission',
            'test_successor_starts_after_own_dependency_without_waiting_for_wave'],
            ['ModeParallelPlanFrame','ModeParallelBodyFrame','ModePlanStepFrame'])

    def test_parallel_failure_drains_owned_children_but_preserves_borrowed_tasks(self):
        self.original_assertions(original_parallel.ParallelPlanTests,[
            'test_async_action_failure_stops_admission_and_drains_fresh_child',
            'test_borrowed_cold_and_running_action_tasks_survive_peer_failure',
            'test_fresh_gather_with_borrowed_peer_is_refused_without_claiming_it'],
            ['ModeParallelPlanFrame','ModeParallelBodyFrame','ModeInvokeFrame'])

    def test_parallel_late_hard_error_identity_and_explicit_cancel_cleanup(self):
        self.original_assertions(original_parallel.ParallelPlanTests,[
            'test_late_hard_failure_during_drain_preserves_exact_identity',
            'test_explicit_cancellation_drains_parallel_steps'],
            ['ModeParallelBodyFrame','ModeInvokeFrame'])

    def test_goal_claim_commits_and_ordinary_failure_release_match_original(self):
        for failure in (None,LanguageError('ordinary fixture',code='mode_fixture')):
            rows=[]
            for legacy in (True,False):
                stored={'checkpoint':{'iteration':0,'state':40,'results':{},'history':[],'phase':'ready','evidence':None},'lease':'lease224','revision':1};operations=[]
                def host(op,payload):
                    operations.append((op,copy.deepcopy(payload)))
                    if op=='goal.commit':stored.update(checkpoint=copy.deepcopy(payload['checkpoint']),revision=stored['revision']+1)
                    return copy.deepcopy(stored)
                source=fixture.GOAL.replace('tool("step",c.input+1)','tool("fault",c.input+1)')if failure else fixture.GOAL
                row,frames,_,caught,_=fixture.snapshot(self,source,legacy=legacy,host=host,fault=failure,schedule='first_wait')
                rows.append(row)
                self.assertEqual(operations[0][0],'goal.claim')
                if failure:
                    self.assertIs(caught,failure);self.assertEqual(operations[-1][0],'goal.release');self.assertEqual(stored['checkpoint']['phase'],'pending')
                else:
                    self.assertEqual(row[0][1],{'done':True,'state':42,'evidence':{'x':42},'iterations':2,'status':'achieved'})
                    self.assertEqual([p['checkpoint']['phase']for op,p in operations if op=='goal.commit'],['pending','ready','pending','done'])
                    self.assertNotIn('goal.release',[op for op,p in operations])
                if not legacy:self.assertTrue(any(type(f).__name__=='ModePursueFrame'for f,c in frames[0]))
            self.assertEqual(rows[1],rows[0])

    def test_goal_hard_failure_preserves_pending_lease_for_reconciliation(self):
        for failure in (ResourceLimit('hard fixture'),TaskCancelled('cancel fixture'),GeneratorExit('close fixture')):
            rows=[]
            for legacy in (True,False):
                state={'checkpoint':{'iteration':0,'state':40,'results':{},'history':[],'phase':'ready','evidence':None},'lease':'lease224','revision':1};operations=[]
                def host(op,payload):
                    operations.append(op)
                    if op=='goal.commit':state.update(checkpoint=copy.deepcopy(payload['checkpoint']),revision=state['revision']+1)
                    return copy.deepcopy(state)
                row,frames,_,caught,_=fixture.snapshot(self,fixture.GOAL.replace('tool("step",c.input+1)','tool("fault",c.input+1)'),legacy=legacy,host=host,fault=failure,schedule='first_wait')
                self.assertIs(caught,failure);self.assertEqual(state['checkpoint']['phase'],'pending');self.assertNotIn('goal.release',operations)
                rows.append(row)
            self.assertEqual(rows[1],rows[0])

    def test_goal_store_terminal_reuse_and_reconciliation_are_original_host_semantics(self):
        self.original_assertions(original_agent.AgentModeTests,[
            'test_artifact_save_load_and_terminal_goal_do_not_repeat',
            'test_pending_failure_requires_reconciliation_and_counter_survives'],
            ['ModePursueFrame','ModeArtifactFrame','ModeHostFrame'])

    def test_recording_descendant_fixedpoint_discards_cold_and_rejects_failed_child(self):
        self.original_assertions(original_agent.AgentModeTests,[
            'test_recording_inherits_spawned_tasks_and_seals_after_join',
            'test_recording_discards_cold_children_without_running_them',
            'test_started_failed_child_prevents_recording_seal'],
            ['ModeRecordFrame','ModeCloseResourcesFrame','ModeHostFrame'])

    def test_recording_closes_all_four_stream_flavors_before_real_host_seal(self):
        self.original_assertions(original_agent.AgentModeTests,[
            'test_recording_closes_all_stream_flavors_before_sealing',
            'test_recording_stream_cleanup_failure_never_seals'],
            ['ModeRecordFrame','ModeCloseResourcesFrame','ModeHostFrame'])

    def test_recording_abort_drain_and_hard_finalization_never_publish_skill(self):
        self.original_assertions(original_agent.AgentModeTests,[
            'test_cancelled_recording_drains_worker_and_never_publishes_skill',
            'test_recording_cannot_adopt_foreign_scope_task_or_nested_scope',
            'test_recording_internal_await_spawn_gather_and_cancel_reject_foreign_scope'],
            ['ModeRecordFrame','ModeHostFrame'])

    def test_real_agent_internal_tool_record_and_replay_effects_are_once(self):
        self.original_assertions(original_agent.AgentModeTests,[
            'test_agent_internal_tool_is_recorded_once',
            'test_record_and_parameterized_replay_are_real_tools',
            'test_replay_rechecks_permissions'],
            ['ModeRecordFrame','ModeReplayFrame','ModeInvokeFrame'])

    def test_artifact_CAS_export_replay_and_current_permissions_share_original_path(self):
        self.original_assertions(original_integration.ModesIntegration,[
            'test_record_agent_tools_parameters_export_and_replay_current_permission'],
            ['ModeArtifactFrame','ModeRecordFrame','ModeReplayFrame'])
