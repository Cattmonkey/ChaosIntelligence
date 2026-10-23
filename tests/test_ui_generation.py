"""Real native effect plumbing with fake transports; never calls paid models."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from chaos import Framework, Settings
from chaos.language import LanguageError
from chaos.ui_generation import stream_ui

SOURCE='root = Text("Hello")'
def chunk(text=None, finish=None, **delta):
    return {'choices':[{'index':0,'delta':{'content':text,**delta},'finish_reason':finish}]}
class Stream:
    def __init__(self, chunks): self.chunks=chunks;self.closes=0
    def __iter__(self):
        for c in self.chunks:
            if isinstance(c,Exception): raise c
            yield c
    def close(self):self.closes+=1
class Client:
    def __init__(self, responses):
        self.responses=iter(responses);self.requests=[];self.closes=0
        self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self,**kw):
        self.requests.append(kw);r=next(self.responses)
        if isinstance(r,Exception):raise r
        return r
    def close(self):self.closes+=1
class UIGenerationTests(unittest.TestCase):
    def run_stream(self,client,**kw):
        events=[]
        result=stream_ui({'provider':'text','model':'test','timeout':3,**kw},[{'role':'user','content':'hello'}],lambda c:client,lambda:None,lambda:False,lambda k,p:events.append((k,p)))
        return result,events
    def test_sdk_stream_true_complete_cumulative_and_close_once(self):
        s=Stream([chunk('root = '),chunk('Text("Hello")'),chunk(finish='stop')]);c=Client([s]);r,e=self.run_stream(c)
        self.assertEqual(r,{'source':SOURCE});self.assertTrue(c.requests[0]['stream']);self.assertEqual(c.requests[0]['model'],'test');self.assertEqual((c.closes,s.closes),(1,1));self.assertEqual(e[0],('ui.delta',{'source':'root = '}));self.assertEqual(e[-1][0],'ui.generation.finished')
    def test_fallback_only_before_first_visible_text(self):
        err=RuntimeError('private transport text');err.status_code=503;s=Stream([chunk(SOURCE),chunk(finish='stop')]);c=Client([err,s]);r,e=self.run_stream(c,candidates=['other'])
        self.assertEqual(r['source'],SOURCE);self.assertEqual([x['model']for x in c.requests],['test','other']);self.assertEqual(e[0][0],'ui.model_fallback')
        c=Client([Stream([chunk('prefix'),err]),s])
        with self.assertRaises(LanguageError):self.run_stream(c,candidates=['other'])
        self.assertEqual(len(c.requests),1)
    def test_invalid_completion_never_reports_finished(self):
        for chunks in ([chunk(SOURCE)], [chunk('',finish='stop')], [chunk(SOURCE,finish='length')], [chunk(SOURCE,tool_calls=[{'id':'x'}])], [chunk(SOURCE,finish='stop'),chunk('later')], [{'choices':[{},{}]}]):
            with self.subTest(chunks=chunks):
                s=Stream(chunks);c=Client([s]);events=[]
                with self.assertRaises(LanguageError):stream_ui({'model':'test','timeout':3},[],lambda cfg:c,lambda:None,lambda:False,lambda k,p:events.append(k))
                self.assertNotIn('ui.generation.finished',events);self.assertEqual((s.closes,c.closes),(1,1))
    def test_preview_coalescing_and_budget_preserve_full_text(self):
        s=Stream([chunk('x'*1000)for _ in range(99)]+[chunk(finish='stop')]);r,e=self.run_stream(Client([s]))
        self.assertEqual(len(r['source']),99000);d=[p for k,p in e if k=='ui.delta'];self.assertLessEqual(sum(len(p['source'])for p in d),2000000);self.assertEqual(sum(k=='ui.preview_limited'for k,p in e),1)
    def test_cancel_closes_blocked_reader_and_joins_watcher(self):
        entered=threading.Event();released=threading.Event();cancel=threading.Event();errors=[]
        class Blocked(Stream):
            def __iter__(self):entered.set();released.wait(3);return iter([])
            def close(self):super().close();released.set()
        s=Blocked([]);c=Client([s])
        def checkpoint():
            if cancel.is_set():raise LanguageError('cancelled',code='cancelled')
        def work():
            try:stream_ui({'model':'test','timeout':3},[],lambda cfg:c,checkpoint,cancel.is_set,lambda *a:None)
            except BaseException as e:errors.append(e)
        worker=threading.Thread(target=work);worker.start();self.assertTrue(entered.wait(2));cancel.set();worker.join(3);self.assertFalse(worker.is_alive());self.assertEqual(errors[0].code,'cancelled');self.assertEqual((s.closes,c.closes),(1,1));self.assertFalse(any(t.name=='chaos-ui-cancel'for t in threading.enumerate()))
    def test_real_framework_native_generate_and_strict_final_validation(self):
        for source,success in ((SOURCE,True),('root = Unknown("x")',False)):
            with self.subTest(source=source):
                settings=Settings(':memory:');settings.update({'providers':{'text':{'apiKey':'fictional-ui-key'}},'roles':{'executor':{'model':'test-model'}}},1)
                c=Client([Stream([chunk(source),chunk(finish='stop')])])
                with patch.object(settings,'client',return_value=c),Framework(settings=settings)as app:
                    app.create_project('ui',source='agent designer{provider:"model",tools:[]};ui_definition(await ui_generate(designer,"hello",title="Demo"))')
                    handle=app.start('ui',1,None,mode='source');run=handle.wait(5)
                    self.assertEqual(run['status'],'completed'if success else'failed');events=app.events('ui',run_id=handle.id)['items'];self.assertTrue(any(x['type']=='ui.delta'for x in events))
                    if success:self.assertEqual(handle.result()['root']['props']['text'],'Hello')
                    self.assertTrue(c.requests[0]['stream']);self.assertEqual(c.requests[0]['model'],'test-model');self.assertEqual(c.closes,1)
    def test_cold_generate_makes_no_provider_call(self):
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('cold',source='agent designer{provider:"model"};type_of(ui_generate(designer,"hello"))');self.assertEqual(app.start('cold',1,None,mode='source').result(5),'Task')
