"""Actual rooted Flow payloads, checked packet selection and root retirement."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from chaos import Evaluator, LanguageError, ResourceLimit
from chaos.language import Native, serializable
from chaos.native_values import RootDomain, RootRef
from chaos.flow_abi import ABI_VERSION, EventPacket, EventTable, EVENT_KINDS, kind_code
from chaos.jit import compile_function, compile_info
from chaos.jit_flow import FlowProgram, info as flow_info
from chaos.native_jit import BackendUnavailable
from chaos.native_flow import compile_flow
from chaos.compiled_flow_state import FlowState
from test_native_jit import NATIVE
from test_native_objects236 import run_four, FOUR_EXPECTED
import test_async_flow_capture203 as pending_fixture
import _generator_flow208 as streams


def make(source='fn(){tool("must-not-run",1)}',target='portable',effects=None):
    effects=[] if effects is None else effects
    e=Evaluator(tool_handler=lambda name,value:effects.append((name,value)) or value,step_limit=1000000)
    function=e.run(source)
    if target=='portable':
        with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('forced-portable237')):
            function=compile_function(e,function,'auto-flow')
    else:function=compile_function(e,function,'native-flow')
    return e,function,effects


def retired(test,states):
    test.assertTrue(states)
    for state in states:
        test.assertTrue(state.closed);test.assertTrue(state.domain.closed);test.assertEqual(state.domain.live_count,0)
        test.assertEqual(state.domain.roots(),());test.assertEqual(state._value_refs,{})
        test.assertEqual(state.events.refs,());test.assertEqual(state.events.handles,())
        test.assertFalse(state.frames);test.assertFalse(state._owned_activations)
        for name in state.ROOTED_FIELDS:test.assertIsNone(getattr(state,name))


class FlowRootsPortable237(unittest.TestCase):
    def rejects(self,call):
        with self.assertRaises(LanguageError) as caught:call()
        self.assertEqual(caught.exception.code,'ownership_error',caught.exception.record())

    def test_shared_private_cache_lock_uses_atomic_creation_and_existing_open(self):
        import os, tempfile
        from pathlib import Path
        from chaos.native_objects import NativeObjectCache
        original=os.open;flags=[]
        def opened(path, options, *args, **kwargs):
            if path=='lock':
                flags.append(options)
                if options & os.O_CREAT:self.assertTrue(options & os.O_EXCL)
            return original(path, options, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory,patch('chaos.native_objects.os.open',opened):
            first=NativeObjectCache(Path(directory)/'cache')
            second=NativeObjectCache(Path(directory)/'cache')
            self.assertEqual(first.info()['entries'],0);self.assertEqual(second.info()['entries'],0)
            self.assertTrue(all(value & os.O_NOFOLLOW for value in flags))
            self.assertTrue(any(not value & os.O_CREAT for value in flags))
            first.close();second.close()

    def test_actual_value_identity_generation_reuse_and_bounded_pins(self):
        owner=Evaluator();domain=RootDomain(owner,owner.suspension.epoch,max_slots=1);value=[]
        first=domain.pin(value);self.assertIs(domain.resolve(first,owner,0),value)
        self.assertEqual(domain.live_count,1)
        with self.assertRaises(ResourceLimit):domain.pin(object())
        self.assertEqual(domain.live_count,1);domain.release(first)
        second=domain.pin(value);self.assertNotEqual(first,second);self.assertIs(domain.resolve(second,owner,0),value)
        self.rejects(lambda:domain.resolve(first,owner,0));self.rejects(lambda:domain.release(first))
        domain.release(second);self.rejects(lambda:domain.release(second));self.assertEqual(domain.roots(),())
        domain.close();domain.close();self.rejects(lambda:domain.resolve(second,owner,0));self.rejects(lambda:domain.pin(42))

    def test_foreign_domain_owner_epoch_and_malformed_tokens_are_not_authority(self):
        owner=Evaluator();foreign=Evaluator();domain=RootDomain(owner,0);other=RootDomain(owner,0);ref=domain.pin({'value':42});alien=other.pin(99)
        for bad in (alien,RootRef(domain.id,0),RootRef(domain.id,True),RootRef(True,ref.token),{'domain':domain.id,'token':ref.token}):
            with self.subTest(ref=bad):self.rejects(lambda:domain.resolve(bad,owner,0))
        self.rejects(lambda:domain.resolve(ref,foreign,0));self.rejects(lambda:domain.resolve(ref,owner,1))
        owner.suspension.epoch=1;self.rejects(lambda:domain.resolve(ref,owner,0));domain.close();other.close()
        self.assertEqual(domain.live_count,0);self.assertEqual(domain.roots(),())

    def test_all_host_event_kinds_use_pinned_actual_rows_and_control_rows_have_no_payload(self):
        owner=Evaluator();domain=RootDomain(owner,0)
        rows=tuple((kind,{'actual':kind}) for kind in EVENT_KINDS)+(('done',),)
        table=EventTable(domain,rows)
        for site,row in enumerate(rows[:-1]):
            packet=table.packet(site+1,site+1);self.assertEqual(packet.kind,kind_code(row[0]));self.assertIs(table.resolve(packet,owner,0),row)
            self.assertIs(domain.resolve(table.refs[site],owner,0),row)
        self.assertIsNone(table.refs[-1]);self.assertEqual(table.handles[-1],0)
        self.assertIsNone(table.resolve(table.packet(len(rows)-1,0),owner,0))
        count=domain.live_count;self.assertEqual(count,len(EVENT_KINDS));table.close();self.assertEqual(domain.live_count,0);domain.close()

    def test_malformed_foreign_stale_recycled_and_closed_packets_reject_without_payload_fallback(self):
        owner=Evaluator();domain=RootDomain(owner,0);other=RootDomain(owner,0)
        rows=(('tick',),('null',),('done',));table=EventTable(domain,rows);foreign=EventTable(other,rows)
        packet=table.packet(1,1)
        for changes in ({'abi':ABI_VERSION+1},{'abi':True},{'domain':other.id},{'kind':kind_code('null')},{'site':1},{'token':foreign.handles[0]},{'token':0},{'next_pc':2}):
            with self.subTest(changes=changes):
                # A foreign slot can have an equal numeric token: its domain is
                # the authority separator, not Python object identity.
                bad=replace(packet,**changes)
                if bad==packet:bad=replace(bad,domain=other.id)
                self.rejects(lambda:table.resolve(bad,owner,0))
        ref=table.refs[0];domain.release(ref);replacement=domain.pin(rows[0]);self.assertNotEqual(ref,replacement)
        self.rejects(lambda:table.resolve(packet,owner,0));domain.close();self.rejects(lambda:table.resolve(packet,owner,0));foreign.close();other.close()

    def test_portable_dispatch_really_uses_same_resolver_and_rejects_typed_corruption_before_effect(self):
        e,f,effects=make();seen=[];original=EventTable.resolve
        def observe(table,packet,owner,epoch):
            row=original(table,packet,owner,epoch);seen.append((packet,row));return row
        with streams.observe_states() as states,patch.object(EventTable,'resolve',observe):self.assertEqual(e.invoke(f,[]),1)
        self.assertTrue(seen);self.assertEqual(effects,[('must-not-run',1)]);self.assertTrue(all(type(p) is EventPacket for p,_ in seen));retired(self,states)
        e,f,effects=make();original_packet=EventTable.packet
        def corrupt(table,*args):return replace(original_packet(table,*args),domain=table.domain.id+1)
        with streams.observe_states() as states,patch.object(EventTable,'packet',corrupt),self.assertRaises(LanguageError) as caught:e.invoke(f,[])
        self.assertEqual(caught.exception.code,'ownership_error');self.assertEqual(effects,[]);retired(self,states)

    def test_pending_state_fields_are_actual_domain_slots_and_invalid_slot_rejects_before_resume(self):
        observations=[]
        def inspect(e,task,state):
            self.assertEqual(state.phase,'host');self.assertIs(state.pending_cursor,task.runner.iterator)
            self.assertNotIn('function',vars(state));self.assertNotIn('scope',vars(state));self.assertNotIn('pending_request',vars(state))
            for name in state.ROOTED_FIELDS:
                ref=state._value_refs.get(name)
                if ref is not None:
                    self.assertIs(type(ref),RootRef);self.assertIs(state.domain.resolve(ref,e,state.epoch),getattr(state,name))
            self.assertIs(state.domain.resolve(state._value_refs['pending_cursor'],e,state.epoch),task.runner.iterator)
            self.assertIs(state.domain.resolve(state._value_refs['scope'],e,state.epoch),state.scope)
            state.validate(full=True)
            key='pending_request';original=state._value_refs[key];foreign_domain=RootDomain(e,state.epoch);foreign=foreign_domain.pin(state.pending_request)
            state._value_refs[key]=foreign
            try:self.rejects(lambda:state.validate(full=True))
            finally:state._value_refs[key]=original;foreign_domain.close()
            observations.append(state.domain.live_count)
        fixture=pending_fixture.AsyncFlowCapture203('test_real_pending_state_metadata_and_foreign_roots_fail_atomically')
        result,states=fixture.run_pending(3,inspect=inspect)
        self.assertEqual(result[0],('value',42));self.assertTrue(observations);self.assertGreater(observations[0],0);retired(self,states)

    def test_real_state_value_replacement_retires_old_handle_and_keeps_shared_value_identity(self):
        def inspect(e,task,state):
            previous=state.result;marker=[40,2];state.result=marker;first=state._value_refs['result']
            self.assertIs(state.result,marker);self.assertIs(state.domain.resolve(first,e,state.epoch),marker)
            marker.append(3);self.assertEqual(state.result,[40,2,3]);state.result=previous
            self.rejects(lambda:state.domain.resolve(first,e,state.epoch))
        fixture=pending_fixture.AsyncFlowCapture203('test_real_pending_state_metadata_and_foreign_roots_fail_atomically')
        result,states=fixture.run_pending(2,inspect=inspect);self.assertEqual(result[0],('value',42));retired(self,states)

    def test_corrupted_domain_table_and_pending_ref_preserve_owned_retirement_and_hard_error(self):
        from chaos.suspension import CHECKPOINT
        for mode in ('domain','table','both','pending-hard-error','event-domain','event-domain-refs','event-domain-hard-error'):
            with self.subTest(mode=mode):
                e,f,effects=make();foreign=RootDomain(e,e.suspension.epoch);marker={'foreign':42};foreign_ref=foreign.pin(marker)
                foreign_table=EventTable(foreign,f.compiled_plan.flow.instructions)
                with streams.observe_states() as states:
                    cursor=e._s_invoke(f,[])
                    for _ in range(100):
                        event=next(cursor)
                        if states and states[-1].pending_request is CHECKPOINT:break
                    else:self.fail('Actual compiled pending checkpoint not reached')
                    state=states[-1];owned=state.domain;owned_table=state.events
                    if mode.startswith('event-domain'):
                        state.events.domain=foreign
                        if mode=='event-domain-refs':state.events.refs=foreign_table.refs
                    if mode in ('pending-hard-error','event-domain-hard-error'):
                        if mode=='pending-hard-error':state._value_refs['pending_request']=RootRef(owned.id,0)
                        incoming=ResourceLimit('incoming hard failure237')
                        with self.assertRaises(ResourceLimit) as caught:cursor.throw(incoming)
                        self.assertIs(caught.exception,incoming)
                    else:
                        if mode in ('domain','both'):state.domain=foreign
                        if mode in ('table','both'):state.events=foreign_table
                        with self.assertRaises(LanguageError) as caught:state.validate(full=True)
                        self.assertEqual(caught.exception.code,'ownership_error')
                        with self.assertRaises(LanguageError) as resumed:cursor.throw(caught.exception)
                        self.assertEqual(resumed.exception.code,'ownership_error')
                    self.assertTrue(owned.closed);self.assertEqual(owned.live_count,0)
                    self.assertIs(state.domain,owned);self.assertIs(state.events,owned_table)
                    self.assertIs(foreign.resolve(foreign_ref,e,e.suspension.epoch),marker)
                    self.assertFalse(foreign.closed);self.assertEqual(effects,[]);retired(self,states)
                foreign_table.close();foreign.close()

    def test_four_flavor_portable_completion_and_existing_metadata_shapes_are_unchanged(self):
        old,_,_,_=run_four(None,'raw')
        with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('portable237')):
            value,e,functions,states=run_four(None,'auto-flow')
        self.assertEqual(value,old);self.assertEqual(value,FOUR_EXPECTED);retired(self,states)
        self.assertEqual({s.flavor for s in states},{'fn','async_fn','gen_fn','async_gen_fn'})
        for function in functions:
            self.assertEqual(len(compile_info(e,function)),12);self.assertEqual(len(flow_info(e,function)),11)
            self.assertGreater(flow_info(e,function)['portableResumes'],0);self.assertEqual(flow_info(e,function)['nativeResumes'],0)

    def test_stream_close_error_and_hard_budget_keep_original_effects_and_retire_roots(self):
        cases=[('let g=choose(fn*(){try{yield 1}finally{tool("one",1);tool("two",2)}});let s=g();mark(s);next(s);close(s);42',30000),
               ('let g=choose(async fn*(){tool("prefix",1);try{yield 1;while true{1}}finally{tool("forbidden",2)}});let s=g();mark(s);await anext(s);await anext(s)',180),
               ('let g=choose(fn*(){yield 1;throw 43});let s=g();mark(s);next(s);next(s)',30000)]
        for source,limit in cases:
            with self.subTest(source=source):
                compared=[];states=[]
                for target in ('raw','portable'):
                    details=[];row,active,_,_=streams.run(source,target,limit=limit,budget_details=details)
                    if target=='portable':states=active
                    if limit==180:
                        self.assertEqual(row[0][0],'error');record=row[0][1]
                        self.assertEqual(record['code'],'resource_limit')
                        self.assertEqual(record['message'],'超过计算步数限制 '+str(details[0][1]))
                        self.assertEqual(details[0][1],details[0][0]+limit)
                        self.assertEqual(row[2],[('prefix',1)])
                        # Each absolute limit truthfully includes that target's
                        # compile setup; all execution deltas/other error fields remain exact.
                        row=((row[0][0],{k:v for k,v in record.items() if k!='message'}),)+row[1:]
                    compared.append(row)
                self.assertEqual(compared[0],compared[1]);retired(self,states)


@unittest.skipUnless(NATIVE,'requires actual optional llvmlite0.45 backend; portable class has no native skip')
class FlowRootsActual237(unittest.TestCase):
    def test_machine_reads_actual_token_vector_and_old_resume_hook_still_runs_once(self):
        owner=Evaluator();domain=RootDomain(owner,0)
        rows=(('branch',0,1,2),('eval',('literal',41),False,None),('eval',('literal',42),False,None),('done',))
        program=FlowProgram(rows,1);table=EventTable(domain,rows);kernel=compile_flow(program,code_cache=None)
        seen=[];original=kernel.resume
        def observed(pc,flags):seen.append((pc,list(flags)));return original(pc,flags)
        kernel.resume=observed
        left=kernel.resume_event(0,[1],table);right=kernel.resume_event(0,[0],table)
        self.assertEqual([left.site,right.site],[1,2]);self.assertIs(table.resolve(left,owner,0),rows[1]);self.assertIs(table.resolve(right,owner,0),rows[2]);self.assertEqual(len(seen),2)
        original_token=table.native_handles[1];table.native_handles[1]=table.handles[2]
        try:
            changed=kernel.resume_event(0,[1],table);self.assertEqual(changed.token,table.handles[2]);self.assertNotEqual(changed.token,left.token)
            with self.assertRaises(LanguageError) as caught:table.resolve(changed,owner,0)
            self.assertEqual(caught.exception.code,'ownership_error')
        finally:table.native_handles[1]=original_token
        self.assertEqual(kernel.resume(0,[1]),(2,2))
        kernel.resume=lambda pc,flags:(2,2)
        with self.assertRaises(LanguageError) as absent_packet:kernel.resume_event(0,[1],table)
        self.assertEqual(absent_packet.exception.code,'ownership_error')
        kernel.resume=observed;table.close();domain.close()

    def test_real_native_successor_packet_selects_other_body_and_bad_packet_is_rejected_before_effect(self):
        e,f,effects=make('fn(flag){if flag{tool("left",41)}else{tool("right",42)}}','native');program=f.compiled_plan.flow
        original=program.kernel.resume;changed=[];packets=[];resolve=EventTable.resolve
        def counterfactual(pc,flags):
            if program.instructions[pc][0]=='branch' and not changed:
                flags[program.instructions[pc][1]]=0;changed.append(pc)
            return original(pc,flags)
        def observe(table,packet,owner,epoch):
            row=resolve(table,packet,owner,epoch);packets.append((packet,row));return row
        program.kernel.resume=counterfactual
        with streams.observe_states() as states,patch.object(EventTable,'resolve',observe):self.assertEqual(e.invoke(f,[True]),42)
        self.assertTrue(changed);self.assertEqual(effects,[('right',42)]);self.assertTrue(packets);self.assertGreater(flow_info(e,f)['nativeResumes'],0);retired(self,states)
        e,f,effects=make(target='native');kernel=f.compiled_plan.flow.kernel;actual=kernel.resume_event
        def corrupt(pc,flags,table):return replace(actual(pc,flags,table),token=0)
        kernel.resume_event=corrupt
        with streams.observe_states() as states,self.assertRaises(LanguageError) as caught:e.invoke(f,[])
        self.assertEqual(caught.exception.code,'ownership_error');self.assertEqual(effects,[]);retired(self,states)

    def test_four_flavor_native_roots_release_without_changing_cold_or_multiple_value_contract(self):
        old,_,_,_=run_four(None,'raw');value,e,functions,states=run_four(None,'native-flow')
        self.assertEqual(value,old);self.assertEqual(value,FOUR_EXPECTED);retired(self,states)
        self.assertEqual({s.flavor for s in states},{'fn','async_fn','gen_fn','async_gen_fn'})
        for function in functions:
            self.assertEqual(len(compile_info(e,function)),12);self.assertEqual(len(flow_info(e,function)),11)
            self.assertGreater(flow_info(e,function)['nativeResumes'],0);self.assertEqual(flow_info(e,function)['portableResumes'],0)
