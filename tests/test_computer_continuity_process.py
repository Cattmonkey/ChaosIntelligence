"""A real Process adopts its next policy and continues the same unsubmitted form."""
import importlib.util
import json
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from chaos import Framework, Settings

OLD_POLICY='fn choose(o){let t=filter(fn(t){t.text=="Work item"},o.targets).[0];{action:"type",ref:t.ref,text:"kept across turns"}};export {choose}'
NEW_POLICY='fn choose(o){let t=filter(fn(t){t.text=="Submit"},o.targets).[0];{action:"click",ref:t.ref}};export {choose}'
DOT='''import "policy.chaos" as Policy;
async fn step(ctx) {
  async with await computer_connect("desk") as computer {
    var seen=await computer_observe(computer,false);
    if ctx.state.round==0 {
      seen=await computer(computer_action(seen,{action:"navigate",url:ctx.state.url}))
    } else {
      if !contains(observation_info(seen).text,"kept across turns") {throw "Pending form state was lost"} else {null}
    };
    let before=observation_info(seen);
    let policy=Policy.choose(before);
    let after=observation_info(await computer(computer_action(seen,policy)));
    var adoption=null;
    if ctx.state.round==0 {
      let base=current_program();let definition=program_info(base);
      let candidate=program_from(merge(definition,{files:merge(definition.files,{"policy.chaos":ctx.state.nextSource})}));
      let cases=[{entry:"checks.chaos",input:{targets:[{text:"Work item",ref:"field"},{text:"Submit",ref:"submit"}]},expect:{action:"click",ref:"submit"}}];
      let proof=await program_probe(candidate,cases);
      if !proof.passed {throw proof} else {null};
      adoption=await program_adopt(base,candidate,cases,reason="Next policy submits the existing form after its pure checks pass")
    } else {null};
    process_step(merge(ctx.state,{round:ctx.state.round+1}),{computer:computer_info(computer),before:before,after:after,policy:policy,afterAdoptPolicy:Policy.choose(before),adoption:adoption,programVersion:ctx.programVersion},ctx.state.round==1)
  }
};export {step};'''


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class ComputerContinuityProcessTests(unittest.TestCase):
    def test_adopted_policy_submits_existing_dom_exactly_once(self):
        fixture={'loads':0,'submissions':[]}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed=urllib.parse.urlsplit(self.path)
                if parsed.path=='/submit':
                    fixture['submissions'].append(urllib.parse.parse_qs(parsed.query));body=b'OK'
                elif parsed.path=='/':
                    fixture['loads']+=1
                    nonce='document-'+str(fixture['loads'])
                    body=('''<title>Continuous form</title><label>Work item<input data-testid="work" aria-label="Work item"></label><button data-testid="submit" onclick="const v=document.querySelector('input').value;document.querySelector('output').textContent='SUBMITTED:'+v+':'+window.nonce;fetch('/submit?value='+encodeURIComponent(v)+'&nonce='+window.nonce)">Submit</button><output>'''+nonce+'''</output><script>window.nonce='''+json.dumps(nonce)+'''</script>''').encode()
                else:body=b''
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*a):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(thread.join,3);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url='http://127.0.0.1:'+str(server.server_port)
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(':memory:');settings.update({'runtime':{'workspace':tmp,'timeout':25}},1)
            with Framework(Path(tmp)/'state.sqlite3',settings=settings)as app:
                files={'dot.chaos':DOT,'policy.chaos':OLD_POLICY,'checks.chaos':'import "policy.chaos" as Policy;Policy.choose(input)'}
                app.create_project('p',source='42',files=files,capabilities=['browser.control','program.adopt','process.manage'])
                definition={'kind':'process','name':'continuous programmer','module':'dot.chaos','export':'step','intervalSeconds':1,'maxTurns':2}
                app.create_process('p',1,'dot',definition,state={'round':0,'url':url,'nextSource':NEW_POLICY})
                deadline=time.monotonic()+35
                while time.monotonic()<deadline:
                    process=app.get_process('p','dot')
                    if process['status']in ('completed','needs_reconciliation'):break
                    time.sleep(.03)
                self.assertEqual(process['status'],'completed',process)
                runs=list(reversed(app._store.runs('p')))
                self.assertEqual(len(runs),2)
                first,second=[app.get_run(row['id'])['output']['output']for row in runs]
                self.assertEqual(first['programVersion'],1);self.assertEqual(second['programVersion'],2)
                self.assertEqual(first['policy'],first['afterAdoptPolicy'])
                self.assertEqual(first['policy']['action'],'type');self.assertEqual(second['policy']['action'],'click')
                self.assertEqual(first['computer']['stableSessionId'],second['computer']['stableSessionId'])
                self.assertNotEqual(first['computer']['id'],second['computer']['id'])
                self.assertGreater(second['computer']['generation'],first['computer']['generation'])
                self.assertEqual(first['after']['sessionId'],second['before']['sessionId'])
                self.assertIn('kept across turns',second['before']['text'])
                self.assertIn('SUBMITTED:kept across turns:document-1',second['after']['text'])
                self.assertEqual(fixture['loads'],1)
                deadline=time.monotonic()+2
                while not fixture['submissions']and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(fixture['submissions'],[{'value':['kept across turns'],'nonce':['document-1']}])
                actions=[r for r in app.computer_history('p',limit=100)['items']if r['kind']=='action']
                self.assertEqual([r['data']['spec']['action']for r in actions],['navigate','type','click'])
                self.assertEqual([r['sourceVersion']for r in actions],[1,1,2])
                self.assertTrue(all(r['status']=='completed'for r in actions))
                self.assertEqual(app.computer_session('p','desk')['heldByRun'],None)
                self.assertNotIn('storage',json.dumps(app.computer_sessions('p')))
