"""Real authority intersections and capture bounds across charged/supplementary roots."""
import unittest
from unittest.mock import patch
from chaos.capture_graph import seal
from chaos.capture_frames import Edge
from chaos.execution_class_protocols import NamespaceWriteFrame
from chaos.generic_functions import NextDescriptor
from chaos.language import Evaluator,Env,Native,LanguageError,ResourceLimit
from chaos.channels import Channel
from _capture_reference187 import no_seed_seal


def token(e):
    capsule=NextDescriptor(e,None)
    return capsule,[Native('next'+str(i),lambda:None,e,generic=capsule,captures=())for i in range(2)]


def frame(e,value,caps=None):
    result=NamespaceWriteFrame({},'field',value);result.owner=e;result.capabilities=caps
    return result


def attempt(call,e,roots):
    before=e.steps
    try:call(e,roots,None);value=('ok',)
    except LanguageError as error:value=('error',error.code)
    return value,e.steps-before


def nested(depth):
    value=None
    for _ in range(depth):value=[value]
    return value


class CaptureAuthorityCache(unittest.TestCase):
    def test_cross_and_narrow_authorities_revisit_shared_descendants_in_original_order(self):
        for reverse in (False,True):
            observations=[]
            for call in (seal,no_seed_seal):
                e=Evaluator();capsule,aliases=token(e);shared={'aliases':aliases};trace=[]
                first=frame(e,shared,frozenset({'read','write'}));second=frame(e,shared,frozenset({'read','network'}))
                roots=(shared,second,first)if reverse else(shared,first,second)
                from chaos import execution_frames
                original=execution_frames.frame_roots
                def observe(value):trace.append('first'if value is first else'second');return original(value)
                with patch.object(execution_frames,'frame_roots',observe):result=attempt(call,e,roots)
                self.assertEqual(result[0],('ok',));self.assertEqual(capsule.capabilities,frozenset({'read'}))
                self.assertEqual([a.capabilities for a in aliases],[frozenset({'read'})]*2)
                observations.append((result,trace,capsule.capabilities,[a.capabilities for a in aliases]))
            self.assertEqual(observations[0],observations[1])

    def test_failed_later_supplementary_root_never_commits_any_alias(self):
        for call in (seal,no_seed_seal):
            e=Evaluator();capsule,aliases=token(e);foreign=Native('foreign',lambda:None,Evaluator())
            narrow=frame(e,aliases[0],frozenset());bad=frame(e,foreign);trace=[]
            from chaos import execution_frames
            original=execution_frames.frame_roots
            def observe(value):
                trace.append('narrow'if value is narrow else'foreign')
                if value is bad:
                    # The previous supplementary frame and its full descendant
                    # DFS have completed; the staged restriction is not committed.
                    self.assertEqual(trace,['narrow','foreign']);self.assertIsNone(capsule.capabilities)
                    self.assertTrue(all(a.capabilities is None for a in aliases))
                return original(value)
            with patch.object(execution_frames,'frame_roots',observe):result=attempt(call,e,(aliases,narrow,bad))
            self.assertEqual(trace,['narrow','foreign']);self.assertEqual(result[0],('error','ownership_error'))
            self.assertIsNone(capsule.capabilities);self.assertTrue(all(a.capabilities is None for a in aliases))

    def test_new_frame_only_foreign_stale_epoch_and_frame_metadata_are_checked(self):
        for call in (seal,no_seed_seal):
            for defect in ('foreign','epoch','frame_owner','phase'):
                e=Evaluator();value=Native('owned',lambda:None,e);pending=frame(e,value)
                if defect=='foreign':pending.value=Native('foreign',lambda:None,Evaluator())
                elif defect=='epoch':pending.value=Channel(e.suspension,1);pending.value.epoch-=1
                elif defect=='frame_owner':pending.owner=Evaluator()
                else:pending.phase=[]
                with self.subTest(oracle=call is no_seed_seal,defect=defect):
                    self.assertEqual(attempt(call,e,(pending,))[0],('error','ownership_error'))

    def test_prepared_env_extra_edges_and_later_batches_preserve_authority_and_atomicity(self):
        for foreign in (False,True):
            observations=[]
            for call in (seal,no_seed_seal):
                e=Evaluator();capsule,aliases=token(e);shared={'aliases':aliases}
                a=frame(e,shared,frozenset({'read','write'}));b=frame(e,shared,frozenset({'read','network'}))
                env=Env();inner=Env();env._class_name='Outer';inner._class_name='Inner'
                env._prepared_namespace={'inner':inner};inner._prepared_namespace={'later':b}
                if foreign:inner._prepared_namespace['foreign']=Native('foreign',lambda:None,Evaluator())
                result=attempt(call,e,(shared,env,a))
                if foreign:
                    self.assertEqual(result[0],('error','ownership_error'));self.assertIsNone(capsule.capabilities)
                    self.assertTrue(all(x.capabilities is None for x in aliases))
                else:
                    self.assertEqual(result[0],('ok',));self.assertEqual(capsule.capabilities,frozenset({'read'}))
                    self.assertEqual([x.capabilities for x in aliases],[frozenset({'read'})]*2)
                observations.append((result,capsule.capabilities,[x.capabilities for x in aliases]))
            self.assertEqual(observations[0],observations[1])

        # A genuinely new prepared-namespace DAG is first discovered in one
        # supplementary batch and aliased by the next. Resetting the cache for
        # every batch would count its 10001 nodes twice and spuriously fail.
        for call in (seal,no_seed_seal):
            e=Evaluator();large=[None]*10000;outer=Env();inner=Env()
            outer._prepared_namespace={'large':large,'inner':inner};inner._prepared_namespace={'again':large}
            self.assertEqual(attempt(call,e,(outer,))[0],('ok',))

    def test_a_new_seal_does_not_reuse_prior_mutable_graph_or_epoch_observations(self):
        for call in (seal,no_seed_seal):
            e=Evaluator();shared={'value':42};pending=frame(e,shared)
            self.assertEqual(attempt(call,e,(shared,pending))[0],('ok',))
            shared['late']=Native('foreign',lambda:None,Evaluator())
            self.assertEqual(attempt(call,e,(pending,))[0],('error','ownership_error'))
            del shared['late'];channel=Channel(e.suspension,1);shared['channel']=channel
            self.assertEqual(attempt(call,e,(shared,pending))[0],('ok',))
            channel.epoch-=1
            self.assertEqual(attempt(call,e,(pending,))[0],('error','ownership_error'))

    def test_original_charged_twenty_thousand_node_boundary_is_unchanged(self):
        for last,expected in ((9998,('ok',)),(9999,('error','resource_limit'))):
            observations=[]
            for call in (seal,no_seed_seal):
                e=Evaluator();result=attempt(call,e,([None]*10000,[None]*last))
                self.assertEqual(result[0],expected);observations.append(result)
            self.assertEqual(observations[0],observations[1])

    def test_genuinely_new_supplementary_graph_still_enforces_twenty_thousand_nodes(self):
        for call in (seal,no_seed_seal):
            e=Evaluator();pending=frame(e,[[None]*10000,[None]*10000])
            self.assertEqual(attempt(call,e,(pending,))[0],('error','resource_limit'))

    def test_depth_one_hundred_boundary_holds_in_both_graph_modes(self):
        for supplementary,depth,expected in ((False,100,('ok',)),(False,101,('error','resource_limit')),(True,98,('ok',)),(True,99,('error','resource_limit'))):
            observations=[]
            for call in (seal,no_seed_seal):
                e=Evaluator();value=nested(depth);root=frame(e,value)if supplementary else value
                result=attempt(call,e,(root,));self.assertEqual(result[0],expected,(supplementary,depth));observations.append(result)
            self.assertEqual(observations[0],observations[1])

    def test_all_sixty_four_original_charged_residues_keep_exact_tick_schedule(self):
        for padding in range(64):
            observations=[]
            for call in (seal,no_seed_seal):
                e=Evaluator();shared=[None]*padding;pending=frame(e,shared)
                result=attempt(call,e,(shared,pending));self.assertEqual(result, (('ok',),2+(padding+1)//64))
                observations.append(result)
            self.assertEqual(observations[0],observations[1])

    def test_completed_charged_dag_is_reused_but_owner_and_schema_are_revalidated(self):
        # Same completed authority proves descendants once; the old unseeded
        # representation counted this 10001-node data graph twice and failed.
        e=Evaluator();shared=[None]*10000;pending=frame(e,shared)
        self.assertEqual(attempt(seal,e,(shared,pending))[0],('ok',))
        e=Evaluator();shared=[None]*10000;pending=frame(e,shared)
        self.assertEqual(attempt(no_seed_seal,e,(shared,pending))[0],('error','resource_limit'))
        for defect in ('owner','native_schema','record_schema','container_bound'):
            for call in (seal,no_seed_seal):
                e=Evaluator();capsule,aliases=token(e);shared={'next':aliases[0]};pending=frame(e,shared)
                # Native itself is reached as a fresh explicit supplementary
                # root. We do not promise an in-seal snapshot of descendants
                # behind an already-validated same-authority container.
                if defect in('owner','native_schema'):pending.value=aliases[0]
                elif defect=='container_bound':shared=[None]*10000;pending.value=shared
                from chaos import execution_frames
                original=execution_frames.frame_roots;mutated=[]
                def mutate(value):
                    if not mutated:
                        mutated.append(True)
                        if defect=='owner':aliases[0].owner=Evaluator()
                        elif defect=='native_schema':aliases[0].captures=[]
                        elif defect=='record_schema':shared[0]=42
                        else:shared.append(None)
                    return original(value)
                with patch.object(execution_frames,'frame_roots',mutate):result=attempt(call,e,(shared,pending))
                expected='ownership_error'if defect=='owner'else'argument_error'if defect in('native_schema','record_schema')else'resource_limit'
                self.assertEqual(result[0],('error',expected),defect);self.assertEqual(mutated,[True])
                self.assertIsNone(capsule.capabilities);self.assertTrue(all(x.capabilities is None for x in aliases))
