"""Same Device program and inert tooling through SDK, HTTP and headless source."""
import inspect
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from chaos import Client, DeviceSpec, Framework, Registry, Settings
from chaos.code_presentation import render_code
from chaos.ide_language import analyze, format_source
from chaos.language import Evaluator
from chaos.native_specs import spec_for
from chaos.server import make_server

SOURCE='''async with await device_open("desk") as device {
 let action=device_action(device,{action:"mouse_move",x:2,y:-3});
 device_receipt_info(await device(action))
}'''


class Adapter:
    def __init__(self):self.actions=[];self.closed=False
    def handle(self, request_id, spec, context):
        context.checkpoint();self.actions.append(spec)
        return {'protocol':'chaos-hid/1','id':request_id,'status':'acknowledged'}
    def close(self):self.closed=True


class DeviceSurfaceTests(unittest.TestCase):
    def test_native_metadata_matches_actual_parameters(self):
        env=Evaluator().env.values
        for name in ('device_open','device_info','device_action','device_action_info','device_act','device_receipt_info','device_close'):
            spec=spec_for(name);self.assertIsNotNone(spec)
            params=list(inspect.signature(env[name].call).parameters.values())
            self.assertEqual([(p.name,p.default is not p.empty) for p in params],[(p.name,p.optional) for p in spec.parameters])

    def test_types_completion_and_formatting_do_not_execute_hardware(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('inert analysis')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('inert analysis')):
            result=analyze(SOURCE);self.assertFalse(result['diagnostics'])
            formatted=format_source(SOURCE);self.assertEqual(format_source(formatted),formatted)
            self.assertIn('chaos-token-function',render_code(formatted,'chaos'))
        source='let d=await device_open("desk");let a=device_action(d,{action:"release"});let receipt=await device_act(d,a);device_receipt_info(receipt).'
        result=analyze(source,position=len(source))
        symbols={item['name']:item['type'] for item in result['symbols']}
        self.assertEqual((symbols['d'],symbols['a'],symbols['receipt']),('Device','DeviceAction','DeviceReceipt'))
        self.assertIn('status',{item['label'] for item in result['completions']})

    def test_http_and_sdk_execute_same_source_and_persist_receipts(self):
        with tempfile.TemporaryDirectory() as folder:
            adapters=[]
            def factory(context):
                value=Adapter();adapters.append(value);return value
            registry=Registry();registry.register_device(DeviceSpec('desk',factory))
            server=make_server(port=0,db=Path(folder)/'state.sqlite3',settings=Settings(':memory:'),state_dir=Path(folder)/'control',registry=registry)
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                project=client.create_project('robot',source=SOURCE,capabilities=['device.control'])
                result=client.start('robot',project['version'],None,mode='source').result(10)
                self.assertEqual(result['status'],'acknowledged');self.assertFalse(result['targetVerified'])
                self.assertEqual(adapters[0].actions,[{'action':'mouse_move','x':2,'y':-3,'wheel':0}])
                self.assertTrue(adapters[0].closed)
                direct=server.framework.start('robot',project['version'],None,mode='source').result(10)
                self.assertEqual(direct['status'],result['status']);self.assertTrue(adapters[1].closed)
                events=client.events('robot',limit=100)
                rows=events['items']
                self.assertTrue(any('device' in str(row.get('type',row.get('kind',''))) for row in rows))
            finally:
                server.shutdown();server.server_close();worker.join(3)
                self.assertFalse(worker.is_alive())

    def test_cli_registers_without_grant_and_grants_only_explicit_flag(self):
        import contextlib,io,sys
        from chaos.cli import main
        with tempfile.TemporaryDirectory() as folder:
            entry=Path(folder)/'main.chaos';entry.write_text(SOURCE)
            registry=Registry();adapter=Adapter();registry.register_device(DeviceSpec('desk',lambda context:adapter))
            for flags,expected in (([],1),(['--capability','device.control'],0)):
                output=io.StringIO();errors=io.StringIO()
                with patch.object(sys,'argv',['chaos','run',str(entry),'--devices','fixture.json']+flags),patch('chaos.device_serial.load_device_registry',return_value=registry),patch('chaos.settings.Settings',return_value=Settings(':memory:')),contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
                    result=main()
                self.assertEqual(result,expected,errors.getvalue())
                if expected==0:self.assertEqual(json.loads(output.getvalue())['status'],'acknowledged')
                else:self.assertFalse(adapter.actions)

    def test_server_borrowed_framework_registry_ambiguity_rejected(self):
        with Framework(settings=Settings(':memory:')) as app:
            with self.assertRaisesRegex(ValueError,'framework or registry'):
                make_server(port=0,framework=app,registry=Registry())
