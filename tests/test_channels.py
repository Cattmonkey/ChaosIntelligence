import unittest

from chaos.language import Evaluator, LanguageError, ResourceLimit
from chaos.channels import create, send, receive, close, info, owned
from chaos.suspension import Await, Runner


class ChannelTests(unittest.TestCase):
    def test_cold_factories_and_eager_copy(self):
        self.assertEqual(Evaluator().run('''
          let c=channel(1);var v={x:[1]};let s=channel_send(c,v);
          v={x:[2]};let before=[task_info(s).state,channel_info(c).buffered];
          await s;[before,(await channel_receive(c)).value,task_info(s).kind,task_info(s).channelId==channel_info(c).id]
        '''),[['created',0],{'x':[1]},'channel_send',True])

    def test_rendezvous_and_argument_order(self):
        self.assertEqual(Evaluator().run('''
          let c=channel();let a=channel_send(c,1);let b=channel_receive(c);
          [await gather([a,b]),cancel(a),cancel(b)]
        '''),[[None,{'done':False,'value':1}],False,False])

    def test_fifo_at_admission_not_factory_time(self):
        self.assertEqual(Evaluator().run('''
          let c=channel(2);let a=channel_send(c,1);let b=channel_send(c,2);
          await b;await a;channel_close(c);await acollect(c)
        '''),[2,1])

    def test_pending_sender_backpressure_and_fifo(self):
        self.assertEqual(Evaluator().run('''
          let c=channel(1);await channel_send(c,1);
          let a=spawn(channel_send(c,2));let b=spawn(channel_send(c,3));
          async fn checkpoint(){0};await checkpoint();
          let pending=channel_info(c);let first=await channel_receive(c);
          let second=await channel_receive(c);await gather([a,b]);
          [pending.buffered,pending.waitingSenders,first.value,second.value,(await channel_receive(c)).value]
        '''),[1,2,1,2,3])

    def test_close_refuses_pending_sender_and_drains_buffer(self):
        self.assertEqual(Evaluator().run('''
          let c=channel(1);await channel_send(c,1);let s=spawn(channel_send(c,2));
          async fn checkpoint(){0};await checkpoint();
          let closes=[channel_close(c),channel_close(c)];
          let err=try{await s}catch e{e.code};
          [closes,err,await channel_receive(c),await channel_receive(c),channel_info(c).waitingSenders]
        '''),[[True,False],'channel_closed',{'done':False,'value':1},{'done':True,'value':None},0])

    def test_close_releases_all_receivers(self):
        self.assertEqual(Evaluator().run('''
          let c=channel();let a=spawn(channel_receive(c));let b=spawn(channel_receive(c));
          async fn checkpoint(){0};await checkpoint();channel_close(c);await gather([a,b])
        '''),[{'done':True,'value':None}]*2)

    def test_cancel_pending_is_immediate_and_does_not_steal(self):
        self.assertEqual(Evaluator().run('''
          let c=channel(1);let r=spawn(channel_receive(c));
          async fn checkpoint(){0};await checkpoint();
          let cancelled=cancel(r);let before=[task_info(r).state,channel_info(c).waitingReceivers];
          await channel_send(c,9);[cancelled,before,(await channel_receive(c)).value]
        '''),[True,['cancelled',0],9])

    def test_deadlock_removes_only_target_waiter_before_catch(self):
        self.assertEqual(Evaluator().run('''
          let c=channel();try{await channel_receive(c)}catch e{[e.code,channel_info(c).waitingReceivers]}
        '''),['task_deadlock',0])

    def test_distinct_cursors_and_local_close(self):
        self.assertEqual(Evaluator().run('''
          let c=channel(2);let a=aiter(c);let b=aiter(c);
          await channel_send(c,1);await channel_send(c,2);
          let first=await anext(a);await aclose(a);let second=await anext(b);
          [first.value,second.value,(await anext(a)).done,channel_info(c).state]
        '''),[1,2,True,'open'])

    def test_async_for_break_keeps_shared_channel_open(self):
        self.assertEqual(Evaluator().run('''
          let c=channel(2);await channel_send(c,1);await channel_send(c,2);
          async for v in c {break};[channel_info(c).state,(await channel_receive(c)).value]
        '''),['open',2])

    def test_first_class_function_and_instance_identity(self):
        self.assertEqual(Evaluator().run('''
          class C {fn __init__(self,x){self.x=x;null};fn read(self){self.x}}
          fn plus(x){x+1};let object=C(7);let c=channel(2);
          await channel_send(c,[plus,object]);let value=(await channel_receive(c)).value;
          [value[0](4),value[1].read(),value[1]==object]
        '''),[5,7,True])

    def test_invalid_capacity_and_lifetime(self):
        for value in (-1, True, '1', 1.5):
            e=Evaluator();e.suspension.begin()
            try:
                with self.assertRaises(LanguageError):create(e,value)
            finally:e.suspension.finish()
        e=Evaluator();e.suspension.begin();c=create(e);e.suspension.finish();e.suspension.begin()
        try:
            for operation in (lambda:info(e,c),lambda:close(e,c),lambda:send(e,c,1),lambda:receive(e,c)):
                with self.assertRaises(LanguageError):operation()
        finally:e.suspension.finish()

    def test_shared_message_budget_counts_cold_capture_and_releases(self):
        e=Evaluator();s=e.suspension;s.begin()
        try:
            c=create(e,1)
            tasks=[send(e,c,'x'*200000) for _ in range(5)]
            with self.assertRaises(ResourceLimit):send(e,c,'x')
            self.assertEqual((s.channel_slots,s.channel_chars),(5,1000000))
            self.assertTrue(s.cancel(tasks[0]))
            task=send(e,c,'x');self.assertEqual(s.channel_slots,5)
            self.assertEqual(task.state,'created')
        finally:s.finish()
        self.assertEqual((s.channel_slots,s.channel_nodes,s.channel_chars),(0,0,0))

    def test_cross_recording_checks_factory_control_and_metadata(self):
        e=Evaluator();s=e.suspension;s.begin()
        def stub():yield
        parent=s.task(stub());s.current=parent.runner
        try:
            c=create(e);op=receive(e,c)
            parent.recording_scope='recorded'
            for fn in (lambda:info(e,c),lambda:close(e,c),lambda:send(e,c,1),lambda:receive(e,c),lambda:s.info(op),lambda:s.spawn(op),lambda:s.cancel(op)):
                with self.assertRaises(LanguageError) as caught:fn()
                self.assertEqual(caught.exception.code,'mode_error')
            rc=create(e);rop=receive(e,rc);parent.recording_scope=None
            for fn in (lambda:info(e,rc),lambda:s.spawn(rop),lambda:s.info(rop)):
                with self.assertRaises(LanguageError):fn()
        finally:s.current=None;s.finish()

    def test_nested_foreign_callable_rejected_before_task_or_reservation(self):
        foreign=Evaluator().run('fn f(){1};f')
        e=Evaluator();s=e.suspension;s.begin()
        try:
            c=create(e)
            with self.assertRaises(LanguageError) as caught:send(e,c,{'nested':[foreign]})
            self.assertEqual(caught.exception.code,'ownership_error')
            self.assertEqual((len(s.tasks),s.channel_slots),(0,0))
        finally:s.finish()

    def test_start_revalidates_live_payload_without_second_reservation(self):
        e=Evaluator();s=e.suspension;s.begin()
        def stub():yield
        try:
            c=create(e,1);live=s.task(stub());sender=send(e,c,{'nested':[live]})
            self.assertEqual(s.channel_slots,1)
            live.recording_scope='recording-now'
            def waiting():return (yield Await(sender))
            with self.assertRaises(LanguageError) as caught:s.drive(waiting())
            self.assertEqual(caught.exception.code,'mode_error')
            self.assertEqual((s.channel_slots,len(c.buffer),len(c.senders)),(0,0,0))
            self.assertEqual(sender.state,'failed')
        finally:s.finish()

    def test_buffered_payload_scope_change_fails_receiver_without_rollback(self):
        e=Evaluator();s=e.suspension;s.begin()
        def stub():yield
        try:
            c=create(e,1);live=s.task(stub());sender=send(e,c,[live])
            def sending():return (yield Await(sender))
            s.drive(sending());live.recording_scope='recording-now'
            receiver=receive(e,c)
            def receiving():return (yield Await(receiver))
            with self.assertRaises(LanguageError) as caught:s.drive(receiving())
            self.assertEqual(caught.exception.code,'mode_error')
            self.assertEqual((sender.state,receiver.state),('completed','failed'))
            self.assertEqual((s.channel_slots,len(c.buffer)),(0,0))
        finally:s.finish()

    def test_host_list_capture_is_copied_before_start(self):
        e=Evaluator();s=e.suspension;s.begin()
        try:
            c=create(e,1);value={'nested':[1]};sender=send(e,c,value)
            value['nested'][0]=2
            def transfer():
                yield Await(sender)
                return (yield Await(receive(e,c)))
            self.assertEqual(s.drive(transfer()),{'done':False,'value':{'nested':[1]}})
        finally:s.finish()

    def test_overlapping_joins_cancel_iteratively_and_drain_all_native_tasks(self):
        from chaos.suspension import TERMINAL
        evaluator=Evaluator(step_limit=1000000,timeout=8)
        source='''let c=channel();
let ops=map(fn(i){channel_send(c,i)},range(600));
let joins=map(fn(i){spawn(gather([ops[i],ops[i+1]]))},range(599));
async fn ready(){while channel_info(c).waitingSenders!=600{1}};
await ready();channel_close(c);for job in joins{await job}'''
        with self.assertRaises(LanguageError) as caught:evaluator.run(source)
        self.assertEqual(caught.exception.code,'channel_closed')
        self.assertFalse(evaluator.suspension.active)
        self.assertTrue(all(task.state in TERMINAL for task in evaluator.suspension.tasks))
        self.assertEqual(len(evaluator.suspension.terminal_queue),0)
        self.assertEqual(evaluator.suspension.channel_slots,0)

    def test_terminal_trace_hard_error_does_not_repeat_commit_or_strand_notices(self):
        from chaos.language import ExecutionControl
        from chaos.suspension import TERMINAL
        for cls in (ResourceLimit,ExecutionControl,KeyboardInterrupt,SystemExit):
            with self.subTest(error=cls.__name__):
                error=cls('terminal trace identity');events=[];fired=[]
                def trace(event):
                    if event['event'] in ('task.completed','task.cancelled','task.failed'):
                        events.append(event['taskId'])
                        if not fired:
                            fired.append(True)
                            raise error
                evaluator=Evaluator(trace_handler=trace,step_limit=100000)
                with self.assertRaises(cls) as caught:
                    evaluator.run('''let c=channel();let a=channel_send(c,1);let b=channel_receive(c);
await gather([a,b])''')
                self.assertIs(caught.exception,error)
                self.assertEqual([task.state for task in evaluator.suspension.tasks if task.channel is not None],['completed','completed'])
                self.assertEqual(len(events),len(set(events)))
                self.assertTrue(all(task.state in TERMINAL for task in evaluator.suspension.tasks))
                self.assertEqual(len(evaluator.suspension.terminal_queue),0)
                self.assertEqual(evaluator.suspension.completion_sequence,len(evaluator.suspension.tasks))

    def test_close_commits_all_senders_and_eof_receivers_before_trace_failure(self):
        for direction in ('send','receive'):
            with self.subTest(direction=direction):
                fired=[]
                def trace(event):
                    if event['event']in('task.failed','task.completed') and event.get('kind')=='channel_'+direction and not fired:
                        fired.append(True)
                        raise LanguageError('close trace once',code='trace_fixture')
                evaluator=Evaluator(trace_handler=trace)
                op='channel_send(c,i)' if direction=='send' else 'channel_receive(c)'
                field='waitingSenders' if direction=='send' else 'waitingReceivers'
                source='''let c=channel();let ops=map(fn(i){spawn(OP)},[1,2]);
async fn ready(){while channel_info(c).FIELD!=2{1}};await ready();
let code=try{channel_close(c)}catch e{e.code};let pending=channel_info(c).FIELD;
for op in ops{try{await op}catch e{null}};[code,pending,await channel_receive(c)]'''.replace('OP',op).replace('FIELD',field)
                self.assertEqual(evaluator.run(source),['trace_fixture',0,{'done':True,'value':None}])
                self.assertTrue(fired)
                self.assertEqual(evaluator.suspension.channel_slots,0)

    def test_open_pump_refills_available_buffer_before_trace_failure(self):
        error=LanguageError('receive trace once',code='trace_fixture');fired=[]
        def trace(event):
            if event['event']=='task.completed' and event.get('kind')=='channel_receive' and not fired:
                fired.append(True)
                raise error
        evaluator=Evaluator(trace_handler=trace)
        with self.assertRaises(LanguageError) as caught:
            evaluator.run('''let c=channel(1);await channel_send(c,0);
let sender=spawn(channel_send(c,1));async fn ready(){while channel_info(c).waitingSenders!=1{1}};
await ready();await channel_receive(c)''')
        self.assertIs(caught.exception,error)
        operations=[task for task in evaluator.suspension.tasks if task.channel is not None]
        self.assertEqual([task.state for task in operations],['completed','completed','completed'])
        self.assertEqual(len(evaluator.suspension.terminal_queue),0)

    def test_snapshot_is_bounded_safe_copy(self):
        e=Evaluator();s=e.suspension;s.begin()
        try:
            c=create(e);op=receive(e,c)
            snap=s.snapshot();snap['channels'][0]['location']['file']='changed'
            self.assertNotEqual(info(e,c)['location'].get('file'),'changed')
            self.assertEqual(snap['tasks'][0]['channelId'],c.id)
            self.assertNotIn('value',info(e,c));self.assertEqual(op.state,'created')
        finally:s.finish()


if __name__=='__main__':unittest.main()
