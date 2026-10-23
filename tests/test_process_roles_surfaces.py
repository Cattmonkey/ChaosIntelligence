"""Actual durable role registry and guidance through HTTP, SDK and CLI."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import inspect
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Framework, Settings
from chaos.client import ConflictError, ValidationError
from chaos.cli import process_main
from chaos.server import make_server

SOURCE = 'fn step(c){process_step(c.state,null,false)};export {step}'
DEFINITION = {'kind':'process','name':'roles','module':'roles.chaos','export':'step',
              'intervalSeconds':None,'maxTurns':None,'roles':['left','right']}


@contextmanager
def service():
    with tempfile.TemporaryDirectory(prefix='chaos-process-role-surfaces-') as tmp:
        with Framework(Path(tmp)/'state.sqlite', settings=Settings(':memory:')) as app:
            app._engine.pause_dispatch(True)
            app.create_project('p',source='null',files={'roles.chaos':SOURCE},capabilities=['process.manage'])
            app.create_process('p',1,'worker',DEFINITION,state={'kept':True})
            app.create_process('p',1,'other',{**DEFINITION,'name':'other','roles':['observer']})
            app.create_project('q',source='null',files={'roles.chaos':SOURCE},capabilities=['process.manage'])
            app.create_process('q',1,'worker',DEFINITION)
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(tmp)/'control',update_home=Path(tmp)/'updates')
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            origin='http://127.0.0.1:'+str(server.server_port)
            try:yield app,Client(origin),origin
            finally:server.shutdown();worker.join(3);server.server_close()


def roles(client,key='worker',project='p'):
    return {row['roleKey']:row for row in client.process_guidance(project,key)['roles']}


class ProcessRoleSurfaces(unittest.TestCase):
    def test_legacy_default_wire_shape_and_keyword_only_target_are_preserved(self):
        client=Client('http://127.0.0.1:1')
        with patch.object(client,'_request',side_effect=lambda *args,**kwargs:(args,kwargs)):
            self.assertEqual(client.guide_process('p','worker',None)[0],
                             ('POST','/projects/p/processes/worker/guidance',{'input':None,'messageId':None}))
            self.assertEqual(client.process_guidance('p','worker')[0],('GET','/projects/p/processes/worker/guidance'))
            target={'kind':'processRole','roleId':'bot-role-'+'a'*32}
            self.assertEqual(client.guide_process('p','worker',42,message_id='same',target=target)[0][2],
                             {'input':42,'messageId':'same','target':target})
        for method in (Client.guide_process,Framework.guide_process):
            self.assertEqual(inspect.signature(method).parameters['target'].kind,inspect.Parameter.KEYWORD_ONLY)

    def test_actual_offline_registry_namespaces_literal_input_and_original_null_receipts(self):
        with service() as (app,client,origin):
            actual=client.process_guidance('p','worker')
            self.assertEqual(actual,app.process_guidance('p','worker'))
            self.assertEqual(set(actual),{'projectId','key','status','roles'})
            by_key=roles(client);a,b=by_key['left']['target'],by_key['right']['target']
            self.assertNotEqual(a,b)
            self.assertTrue(all(row['activeScopeId']is None and row['addressable']for row in actual['roles']))
            first={'text':'@right PASS <script>literal()</script> 👋','target':'just data'}
            receipt=client.guide_process('p','worker',first,message_id='same',target=a)
            self.assertEqual(set(receipt),{'key','runId','messageId','revision','status','target'})
            self.assertIsNone(receipt['runId']);self.assertEqual(receipt['target'],a)
            self.assertEqual(app.guide_process('p','worker',first,message_id='same',target=a),receipt)
            other=client.guide_process('p','worker',{'text':'B'},message_id='same',target=b)
            self.assertEqual(other['revision'],1);self.assertEqual(other['target'],b)
            root=client.guide_process('p','worker',{'text':'root'},message_id='same')
            self.assertEqual(set(root),{'key','runId','messageId','revision','status'})
            self.assertEqual([roles(client)[key]['pendingCount']for key in ('left','right')],[1,1])
            events=client.events('p',process_key='worker',limit=100)['items']
            durable=[e['payload']['receipt']for e in events if e['type']=='process.guidance.queued'and e['payload'].get('receipt',{}).get('target')==a]
            self.assertEqual(durable,[receipt]);self.assertEqual(app.get_process('p','worker')['state'],{'kept':True})
            with self.assertRaises(ConflictError):client.guide_process('p','worker',{'different':1},message_id='same',target=a)

    def test_closed_http_targets_foreign_retired_and_readded_roles_never_fall_back(self):
        with service() as (app,client,origin):
            target=roles(client)['left']['target'];foreign=roles(client,project='q')['left']['target']
            base='/projects/p/processes/worker/guidance'
            before=client.get_process('p','worker')
            for malformed in (None,{},dict(target,roleKey='left'),{'kind':'processRole','roleId':'left'}):
                with self.subTest(target=malformed),self.assertRaises(ValidationError):
                    client._request('POST',base,{'input':42,'target':malformed})
            with self.assertRaises(ConflictError):client.guide_process('p','worker',42,target=foreign)
            with self.assertRaises(ValidationError):client._request('GET',base,query={'roleKey':'left'})
            self.assertEqual(client.get_process('p','worker'),before)
            row=client.configure_process('p','worker',before['revision'],{**DEFINITION,'roles':['right']})
            client.configure_process('p','worker',row['revision'],DEFINITION)
            fresh=roles(client)['left']['target'];self.assertNotEqual(target,fresh)
            with self.assertRaises(ConflictError):client.guide_process('p','worker',42,target=target)
            self.assertEqual(roles(client)['left']['pendingCount'],0)
            self.assertEqual(client.get_process('p','worker').get('guidanceRevision',0),0)

    def test_real_cli_role_key_resolves_current_registry_without_interpreting_text(self):
        with service() as (app,client,origin):
            text='@right PASS is literal; do not choose a role from these words'
            output,error=io.StringIO(),io.StringIO()
            with redirect_stdout(output),redirect_stderr(error):
                code=process_main(['guide','p','worker','--server',origin,'--role-key','left',
                                   '--text',text,'--message-id','cli-same'])
            self.assertEqual(code,0,error.getvalue())
            receipt=json.loads(output.getvalue());self.assertEqual(receipt['target'],roles(client)['left']['target'])
            self.assertEqual(receipt,client.guide_process('p','worker',{'text':text},message_id='cli-same',target=receipt['target']))
            self.assertIsNone(receipt['runId']);self.assertEqual(roles(client)['right']['pendingCount'],0)
            with redirect_stdout(io.StringIO()),redirect_stderr(error):
                code=process_main(['guide','p','worker','--server',origin,'--role-key','missing','--text',text])
            self.assertEqual(code,1);self.assertEqual(roles(client)['left']['pendingCount'],1)

    def test_actual_openapi_keeps_legacy_schema_and_describes_closed_role_union(self):
        with service() as (app,client,origin):
            spec=client._request('GET','/openapi.json');schemas=spec['components']['schemas']
            path=spec['paths']['/projects/{projectId}/processes/{key}/guidance']
            self.assertEqual(path['get']['operationId'],'listProcessGuidanceRoles')
            self.assertFalse(schemas['ProcessRoleTarget']['additionalProperties'])
            self.assertEqual(set(schemas['ProcessRoleTarget']['required']),{'kind','roleId'})
            self.assertEqual(set(schemas['GuidanceReceipt']['required']),{'runId','messageId','revision','status'})
            self.assertEqual(set(schemas['ProcessRoleGuidanceReceipt']['required']),{'key','runId','messageId','revision','status','target'})
            self.assertEqual(len(schemas['AnyGuidanceTarget']['oneOf']),2)
            self.assertEqual(schemas['ProcessGuidanceRegistry']['properties']['roles']['maxItems'],16)


if __name__=='__main__':unittest.main()
