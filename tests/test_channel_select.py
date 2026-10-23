import unittest
from unittest.mock import patch

from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.suspension import Await
from chaos.channels import create, receive, send, close
from chaos.channel_select import send_case, receive_case, select, case_info


class ChannelSelectTests(unittest.TestCase):
    def evaluator(self):
        e = Evaluator()
        now = [100.0]
        e.suspension.task_waits.clock = lambda:now[0]
        e.suspension.task_waits.idle_wait = lambda seconds:now.__setitem__(0,now[0]+seconds)
        return e, now

    def clean(self, e):
        s=e.suspension
        self.assertEqual((s.select_registrations,s.channel_slots,s.channel_nodes,s.channel_chars),(0,0,0,0))
        self.assertEqual((s.channel_dirty,s.channel_dirty_set),(type(s.channel_dirty)(),set()))
        self.assertEqual((s.task_waits.timers,s.task_waits.deadlines),([],{}))
        self.assertTrue(all(not c.senders and not c.receivers and not c.buffer for c in s.channels))

    def test_first_class_case_info_and_cold_task(self):
        e,_=self.evaluator()
        result=e.run('''let c=channel(1);let branch=send_case(c,42);
          let op=channel_select([branch]);let before=task_info(op);
          [type_of(branch),case_info(branch),before.kind,before.state,before.memberTaskIds,
           before.selection,await op,(await channel_receive(c)).value]''')
        self.assertEqual(result[0],'ChannelCase')
        self.assertEqual(set(result[1]),{'id','op','channelId','location'})
        self.assertEqual(result[2:5],['channel_select','created',[]])
        self.assertEqual(result[5]['index'],None)
        self.assertEqual(result[5]['caseCount'],1)
        self.assertEqual(result[6],dict(index=0,op='send',done=False,value=None,timedOut=False))
        self.assertEqual(result[7],42)
        self.clean(e)

    def test_send_template_is_bound_once_but_each_use_copies_collections(self):
        # Language containers are value-style and reject in-place writes. The
        # trusted host seam still verifies that no mutable payload is aliased.
        e,_=self.evaluator();s=e.suspension;s.begin()
        def take(task):
            def operation():return (yield Await(task))
            return s.drive(operation())
        try:
            c=create(e,1);original=[1];branch=send_case(e,c,original)
            original[0]=7;take(select(e,[branch]));first=take(receive(e,c))['value']
            first[0]=9;take(select(e,[branch]));second=take(receive(e,c))['value']
            self.assertEqual((first,second),([9],[1]))
        finally:s.finish()
        self.clean(e)

    def test_cases_and_functions_can_be_sent_as_values(self):
        e,_=self.evaluator()
        self.assertEqual(e.run('''let carrier=channel(1);let target=channel(1);
          let branch=send_case(target,fn(x){x+1});await channel_send(carrier,branch);
          let same=(await channel_receive(carrier)).value;await channel_select([same]);
          [(await channel_receive(target)).value(41),same==branch]'''),[42,True])
        self.clean(e)

    def test_only_one_ready_receive_is_consumed_in_input_order(self):
        e,_=self.evaluator()
        self.assertEqual(e.run('''let a=channel(1);let b=channel(1);await channel_send(a,1);await channel_send(b,2);
          let r=await channel_select([receive_case(b),receive_case(a)]);
          [r.index,r.value,channel_info(a).buffered,channel_info(b).buffered]'''),[0,2,1,0])
        self.clean(e)

    def test_initial_fifo_compatibility_does_not_hide_preferred_direction(self):
        e,_=self.evaluator()
        self.assertEqual(e.run('''let c=channel();let a=spawn(channel_select([send_case(c,1),receive_case(c)]));
          while task_info(a).phase!="channel_select"{null};
          let b=channel_select([send_case(c,2),receive_case(c)]);let result=await gather([a,b]);
          [result[0].index,result[0].value,result[1].index,result[1].op]'''),[1,2,0,'send'])
        self.clean(e)

    def test_duplicate_send_cases_do_not_emit_losing_payloads(self):
        e,_=self.evaluator()
        self.assertEqual(e.run('''let c=channel(3);let r=await channel_select([send_case(c,1),send_case(c,2),send_case(c,3)]);
          channel_close(c);[r.index,await acollect(c)]'''),[0,[1]])
        self.clean(e)

    def test_null_message_is_distinct_from_closed_eof_and_timeout(self):
        e,_=self.evaluator()
        result=e.run('''let c=channel(1);await channel_send(c,null);let branch=receive_case(c);
          let first=await channel_select([branch]);let timeout=await channel_select([branch],0);
          channel_close(c);[first,timeout,await channel_select([branch])]''')
        self.assertEqual(result,[dict(index=0,op='receive',done=False,value=None,timedOut=False),
                                 dict(index=None,op=None,done=False,value=None,timedOut=True),
                                 dict(index=0,op='receive',done=True,value=None,timedOut=False)])
        self.clean(e)

    def test_initial_closed_send_is_error_only_if_it_wins(self):
        e,_=self.evaluator()
        self.assertEqual(e.run('''let closed=channel();channel_close(closed);let ready=channel(1);
          await channel_send(ready,42);let a=await channel_select([receive_case(ready),send_case(closed,1)]);
          let op=channel_select([send_case(closed,2),receive_case(closed)]);
          let err=try{await op}catch e{e.code};[a.value,err,task_info(op).selection.index]'''),[42,'channel_closed',0])
        self.clean(e)

    def test_self_offers_may_buffer_but_cannot_self_rendezvous(self):
        e,_=self.evaluator()
        self.assertEqual(e.run('''let c=channel(1);let r=await channel_select([receive_case(c),send_case(c,42)]);
          [r.index,r.op,channel_info(c).buffered,(await channel_receive(c)).value]'''),[1,'send',1,42])
        self.clean(e)

    def test_timeout_begins_at_dispatch_and_cleans_one_shared_deadline(self):
        e,now=self.evaluator()
        self.assertEqual(e.run('''let c=channel();let op=channel_select([receive_case(c)],2);
          await sleep(3);let r=await op;[r.timedOut,task_info(op).timing,task_info(op).selection.index]'''),
          [True,dict(seconds=None,until=None,timeoutSeconds=2.0,remainingSeconds=None),None])
        self.assertAlmostEqual(now[0],105.0)
        self.clean(e)

    def test_pending_info_is_bounded_and_does_not_expose_payload(self):
        e,_=self.evaluator();s=e.suspension;s.begin()
        try:
            c=create(e);branch=send_case(e,c,'sensitive message')
            op=select(e,[branch]*101,2)
            self.assertEqual(s.info(op)['selection']['caseCount'],101)
            self.assertEqual(len(s.info(op)['selection']['cases']),100)
            self.assertTrue(s.info(op)['selection']['casesTruncated'])
            self.assertNotIn('sensitive message',str(s.info(op)))
            s.spawn(op);s._step(op.runner)
            self.assertEqual(s.info(op)['phase'],'channel_select')
            self.assertEqual(s.info(op)['timing']['remainingSeconds'],2)
            self.assertIsNone(s.info(op)['channelId'])
            copied=s.info(op);copied['selection']['cases'][0]['op']='changed'
            self.assertEqual(s.info(op)['selection']['cases'][0]['op'],'send')
        finally:s.finish()
        self.clean(e)

    def test_invalid_case_or_timeout_does_not_create_partial_task(self):
        e,_=self.evaluator();s=e.suspension;s.begin()
        try:
            c=create(e);branch=send_case(e,c,1);before=(len(s.tasks),s.channel_slots,s.select_registrations)
            for cases,seconds in (([],None),([branch,{}],None),([branch],True),([branch],-1),([branch],float('inf'))):
                with self.assertRaises(LanguageError):select(e,cases,seconds)
                self.assertEqual((len(s.tasks),s.channel_slots,s.select_registrations),before)
        finally:s.finish()
        self.clean(e)

    def test_partial_capture_resource_error_rolls_back_new_reservations(self):
        e,_=self.evaluator();s=e.suspension;s.begin()
        try:
            c=create(e);branch=send_case(e,c,'x'*200000)
            with self.assertRaises(ResourceLimit):select(e,[branch]*5)
            self.assertEqual((s.channel_slots,s.channel_chars,s.select_registrations,len(s.tasks)),(1,200000,0,0))
        finally:s.finish()
        self.clean(e)

    def test_initial_duplicate_readiness_scan_is_linear(self):
        import chaos.channels as channels
        e,_=self.evaluator();s=e.suspension;s.begin();count=[0]
        try:
            c=create(e);a=send_case(e,c,1);b=receive_case(e,c);cases=[a]*1000+[b]*1000
            op=select(e,cases,0);owner=channels._owner
            def counted(item):count[0]+=1;return owner(item)
            def consume():return (yield Await(op))
            with patch.object(channels,'_owner',counted):result=s.drive(consume())
            self.assertTrue(result['timedOut'])
            self.assertLessEqual(count[0],10*len(cases))
        finally:s.finish()
        self.clean(e)

    def test_expiring_selectors_all_commit_before_trace_failure(self):
        for error in (LanguageError('notice'),ResourceLimit('notice'),KeyboardInterrupt('notice')):
            e,now=self.evaluator();s=e.suspension;s.begin();fired=[]
            try:
                c=create(e);branch=receive_case(e,c)
                tasks=[select(e,[branch],1),select(e,[branch],1)]
                for task in tasks:s.spawn(task);s._step(task.runner)
                def trace(event):
                    if event['event']=='task.completed' and not fired:fired.append(1);raise error
                e.trace_handler=trace;now[0]=102.0
                with self.assertRaises(type(error)) as caught:s.task_waits.poll()
                self.assertIs(caught.exception,error)
                self.assertEqual([task.state for task in tasks],['completed','completed'])
                self.assertTrue(all(task.runner.result['timedOut'] for task in tasks))
                self.assertFalse(c.receivers)
                self.assertFalse(s.terminal_queue)
            finally:e.trace_handler=None;s.finish(original=error)
            self.clean(e)


if __name__=='__main__':unittest.main()
