"""Proposal: observer continuity, committed tail races and cooperative detach.

All transport fixtures are deterministic in-process adapters; no remote service is used.
"""
import copy
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Settings
from chaos.client import TransportError
from chaos.framework import RunHandle, watch_events
from tests.test_process_integration import definition, wait_for


def event(number, kind='bot.progress', **changes):
    return {'id':number, 'type':kind, 'run_id':'turn-'+str(number),
            'project_id':'p', 'payload':{}, 'processOrigin':'run', **changes}


def page(items, after=0, *, more=False):
    last = items[-1]['id'] if items else after
    return {'items':copy.deepcopy(items), 'nextCursor':last if more else None,
            'lastEventId':last, 'historyStartEventId':1, 'legacyHistoryIncluded':False}


class ProcessStreamWatch(unittest.TestCase):
    def test_run_terminal_and_resumable_or_uncertain_states_never_end_process_watch(self):
        states = [('waiting',None), ('paused',None), ('waiting',None),
                  ('running',{'runId':'active'}), ('needs_reconciliation',{'runId':'uncertain'}),
                  ('paused',None), ('waiting',None), ('stopped',None),
                  ('running',{'runId':'cancel-settling'}), ('stopped',None),
                  ('waiting',None), ('completed',None)]
        kinds = ['run.completed','process.pause','process.resume','run.running',
                 'run.failed','process.reconciled','process.resume','process.stop',
                 'process.stop','process.reconciled','process.guidance.queued','process.turn.completed']
        calls = []
        class Backend:
            index = 0
            def events(self, project_id, after=0, run_id=None, process_key=None):
                calls.append(('events',project_id,after,run_id,process_key))
                if self.index < len(states):
                    number = self.index+1
                    if after < number:
                        return page([event(number,kinds[self.index])], after)
                return page([],after)
            def get_process(self, project_id, key):
                calls.append(('status',project_id,key))
                status,pending = states[self.index]
                self.index += 1
                return {'status':status,'pending':pending,'control':'stopped'}
            def get_run(self, *args):
                raise AssertionError('Process watcher consulted individual run state')
        backend = Backend()
        found = list(watch_events(backend,'p',process_key='alpha',timeout=1,poll_interval=.001))
        self.assertEqual([row['id'] for row in found],list(range(1,len(states)+1)))
        self.assertEqual(backend.index,len(states))
        self.assertTrue(all(call[3:] == (None,'alpha') for call in calls if call[0]=='events'))

    def test_completed_observation_drains_a_racing_tail_larger_than_default_page(self):
        calls = []
        class Backend:
            rows = []
            observed = False
            def events(self, project_id, after=0, run_id=None, process_key=None):
                calls.append(after)
                selected = [row for row in self.rows if row['id']>after]
                return page(selected[:100],after,more=len(selected)>=100)
            def get_process(self, project_id, key):
                if not self.observed:
                    self.observed = True
                    # Commit races between the previous empty read and status observation.
                    self.rows = [event(n) for n in range(1,203)]
                    self.rows[0]['type'] = 'run.completed'  # Earlier turn terminal is harmless.
                    self.rows[-2] = event(201,'process.turn.completed',processOrigin='lifecycle')
                    self.rows[-1] = event(202,'run.completed')
                return {'status':'completed','pending':None}
        backend = Backend()
        found = list(watch_events(backend,'p',process_key='alpha',timeout=1,poll_interval=.001))
        self.assertEqual([row['id'] for row in found],list(range(1,203)))
        self.assertEqual(found[-1]['type'],'run.completed')
        self.assertEqual(calls[-1],202)
        self.assertTrue({100,200}.issubset(calls))
        self.assertEqual(len(found),len({row['id'] for row in found}))

    def test_completed_with_pending_remains_open_and_hot_pages_honor_deadline(self):
        class Inconsistent:
            def events(self,*args,**kwargs): return page([],kwargs['after'])
            def get_process(self,*args): return {'status':'completed','pending':{'runId':'unsettled'}}
        with self.assertRaises(TimeoutError):
            list(watch_events(Inconsistent(),'p',process_key='alpha',timeout=0))
        class Hot:
            calls = 0
            def events(self,*args,**kwargs):
                self.calls += 1
                n = kwargs['after']+1
                return page([event(n)],kwargs['after'],more=True)
            def get_process(self,*args): raise AssertionError('Hot pages must check deadline without status polling')
        backend = Hot()
        ticks = iter((0.0, .2, .6, 1.1))
        with patch('chaos.framework.time.monotonic',side_effect=lambda:next(ticks)):
            stream = watch_events(backend,'p',process_key='alpha',timeout=1)
            self.assertEqual([next(stream)['id'] for _ in range(3)],[1,2,3])
            with self.assertRaises(TimeoutError):
                next(stream)
        self.assertEqual(backend.calls,3)

    def test_stop_before_request_between_rows_and_after_inflight_request(self):
        stop = threading.Event()
        class Backend:
            calls = 0
            def events(self,*args,**kwargs):
                self.calls += 1
                return page([event(1),event(2)])
        backend = Backend()
        stop.set()
        self.assertEqual(list(watch_events(backend,'p',process_key='alpha',stop_event=stop)),[])
        self.assertEqual(backend.calls,0)
        stop.clear()
        stream = watch_events(backend,'p',process_key='alpha',stop_event=stop)
        self.assertEqual(next(stream)['id'],1)
        stop.set()
        self.assertEqual(list(stream),[])
        self.assertEqual(backend.calls,1)
        for stage in ('events','status'):
            with self.subTest(stage=stage):
                stop.clear()
                class ReturningAfterStop:
                    def events(self,*args,**kwargs):
                        if stage=='events':
                            stop.set()
                            return None  # Indexing this after detach would fail.
                        return page([])
                    def get_process(self,*args):
                        stop.set()
                        return None
                self.assertEqual(list(watch_events(ReturningAfterStop(),'p',process_key='alpha',stop_event=stop)),[])

    def test_continuously_arriving_short_terminal_tails_also_honor_deadline(self):
        class ShortTail:
            rows = [event(1)]
            def events(self,*args,**kwargs):
                # Every read has a short page and no nextCursor; the hot-page branch
                # therefore cannot accidentally be the only deadline enforcement.
                return page([row for row in self.rows if row['id']>kwargs['after']],kwargs['after'])
            def get_process(self,*args):
                self.rows.append(event(self.rows[-1]['id']+1))
                return {'status':'completed','pending':None}
        backend = ShortTail()
        ticks = iter((0.0,.2,.6,1.1))
        with patch('chaos.framework.time.monotonic',side_effect=lambda:next(ticks)):
            stream = watch_events(backend,'p',process_key='alpha',timeout=1)
            self.assertEqual([next(stream)['id'] for _ in range(3)],[1,2,3])
            with self.assertRaises(TimeoutError):
                next(stream)
        self.assertEqual([row['id'] for row in backend.rows],[1,2,3,4])

    def test_idle_stop_uses_local_wait_and_no_remote_mutation(self):
        calls = []
        class ImmediateStop:
            stopped = False
            def is_set(self): return self.stopped
            def wait(self,seconds):
                calls.append(('wait',seconds))
                self.stopped = True
                return True
        class Backend:
            def events(self,*args,**kwargs): calls.append(('read',)); return page([])
            def get_process(self,*args): calls.append(('status',)); return {'status':'stopped','pending':None}
            def __getattr__(self,name): raise AssertionError('Unexpected observer action: '+name)
        with patch('chaos.framework.time.sleep',side_effect=AssertionError('Idle detach used uninterruptible sleep')):
            self.assertEqual(list(watch_events(Backend(),'p',process_key='alpha',timeout=None,stop_event=ImmediateStop())),[])
        self.assertEqual([call[0] for call in calls],['read','status','wait'])

    def test_original_run_handle_and_third_party_adapter_keep_their_old_signature(self):
        class OriginalBackend:
            calls = []
            def events(self, project_id, after=0, run_id=None):
                self.calls.append((project_id,after,run_id))
                return {'items':[event(8,'run.completed')], 'nextCursor':None, 'lastEventId':8}
            def watch(self,project_id,*,run_id=None,after=0,timeout=60):
                return watch_events(self,project_id,run_id,after,timeout)
            def get_run(self,*args): raise AssertionError('Original terminal event should end run watcher')
        backend = OriginalBackend()
        handle = RunHandle(backend,{'id':'r','project_id':'p'})
        self.assertEqual([row['id'] for row in handle.watch(after=7)],[8])
        self.assertEqual(backend.calls,[('p',7,'r')])

    def test_client_query_compatibility_bounded_stop_requests_and_transport_errors(self):
        client = Client('http://localhost:1',timeout=30)
        with patch.object(client,'_request',return_value={}) as request:
            client.events('p',after=12,limit=3,run_id='r')
            request.assert_called_once_with('GET','/events',query={'projectId':'p','after':12,'limit':3,'runId':'r'})
            request.reset_mock()
            client.events('p',after=12,limit=3,process_key='alpha')
            request.assert_called_once_with('GET','/events',query={'projectId':'p','after':12,'limit':3,'runId':None,'processKey':'alpha'})
        requests = []
        def transport(method,path,body=None,query=None,timeout=None):
            requests.append((method,path,query,timeout))
            return {'status':'completed','pending':None} if '/processes/' in path else page([],query['after'])
        stop = threading.Event()
        with patch.object(client,'_request',side_effect=transport):
            self.assertEqual(list(client.watch_process('p','alpha',timeout=10,stop_event=stop)),[])
        self.assertEqual([r[1] for r in requests],['/events','/projects/p/processes/alpha','/events'])
        self.assertTrue(all(0<r[3]<=1 for r in requests))
        self.assertTrue(all(r[2]['processKey']=='alpha' for r in requests if r[1]=='/events'))
        requests.clear()
        with patch.object(client,'_request',side_effect=transport):
            self.assertEqual(list(client.watch_process('p','alpha',timeout=None)),[])
        self.assertTrue(all(r[3]==30 for r in requests))
        def detached(*args,**kwargs):
            stop.set()
            raise TransportError('inflight observer disconnected')
        with patch.object(client,'_request',side_effect=detached):
            self.assertEqual(list(client.watch_process('p','alpha',stop_event=stop)),[])
        self.assertTrue(stop.is_set(), 'in-flight transport fixture never ran')
        stop.clear()
        with patch.object(client,'_request',side_effect=TransportError('not detached')):
            with self.assertRaises(TransportError):
                list(client.watch_process('p','alpha',timeout=None,stop_event=stop))

    def test_real_waiting_paused_stopped_watch_and_generator_close_have_no_effects(self):
        app = Framework(settings=Settings(':memory:'))
        self.addCleanup(app.close)
        app.create_project('p',source='42',files={'dot.chaos':'fn step(c){process_step(c.state+1)};export {step}'},capabilities=['process.manage'])
        app.create_process('p',1,'alpha',definition(),state=0)
        wait_for(app,'p','alpha')
        for control in (None,'pause','stop'):
            with self.subTest(control=control):
                if control:
                    row = app.get_process('p','alpha')
                    app.control_process('p','alpha',row['revision'],control)
                before = {table:[tuple(r) for r in app._store.db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                    for table in ('processes','process_messages','runs','events','process_run_lineage','process_event_lineage')}
                after = app.events('p',process_key='alpha')['lastEventId']
                with self.assertRaises(TimeoutError):
                    list(app.watch_process('p','alpha',after=after,timeout=.02))
                stream = app.watch_process('p','alpha',timeout=None)
                next(stream)
                stream.close()
                observed = {table:[tuple(r) for r in app._store.db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                    for table in before}
                self.assertEqual(observed,before)


if __name__ == '__main__':
    unittest.main()
