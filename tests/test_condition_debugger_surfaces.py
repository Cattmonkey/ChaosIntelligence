"""One native recovery protocol through IDE metadata, SDK, HTTP and CLI."""
import json
import math
from unittest.mock import patch
from chaos.debugger import breakpoints,options
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from chaos import Framework, Client, Settings
from chaos.ide_language import analyze
from chaos.native_specs import spec_for
from chaos.server import make_server

# Exact 0.102.0 options function frozen as a previous-server compatibility fixture.
def _legacy_debug_options(value, files):
    if value is None or value is False:
        return None
    if value is True:
        value = {}
    if not isinstance(value, dict) or set(value) - {'breakpoints', 'pauseOnEntry', 'pauseTimeout'}:
        raise ValueError('debug must contain only breakpoints, pauseOnEntry and pauseTimeout')
    pause = value.get('pauseOnEntry', True)
    timeout = value.get('pauseTimeout', 300)
    if type(pause) is not bool:
        raise ValueError('pauseOnEntry must be a boolean')
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not .1 <= timeout <= 3600:
        raise ValueError('pauseTimeout must be finite and between 0.1 and 3600 seconds')
    return {'breakpoints': breakpoints(value.get('breakpoints', []), files),
            'pauseOnEntry': pause, 'pauseTimeout': timeout}

ROOT=Path(__file__).resolve().parent.parent

class ConditionDebuggerSurfaces(unittest.TestCase):
    def setUp(self):
        self.app=Framework(':memory:',settings=Settings(':memory:'));self.addCleanup(self.app.close)
    def paused(self,run):
        end=time.monotonic()+3
        while time.monotonic()<end:
            state=run.debugger()
            if state['status']=='paused':return state
            time.sleep(.005)
        self.fail('Recovery did not pause')
    def test_native_spec_ide_signature_and_standard_module_use_same_primitive(self):
        self.assertEqual([p.name for p in spec_for('invoke_debugger').parameters],['value'])
        self.assertEqual([p['name'] for p in analyze('invoke_debugger(')['signatureHelp']['parameters']],['value'])
        self.assertEqual(Framework.evaluate('import "std/conditions" as C;try{C.invoke_debugger(C.condition("missing"))}catch e{e.code}'),'condition_error')
    def test_http_client_cli_restart_uses_exact_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            server=make_server('127.0.0.1',0,framework=self.app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_address[1]));p=client.create_project('recovery-http',source='restart_case(fn(){error_condition(condition("missing","need",{fallback:42}))},[{name:"same",function:fn(x){0}},{name:"same",function:fn(x){x}}])')
                run=client.start_debug(p['id'],p['version'],pause_on_entry=False,pause_on_condition=True);state=self.paused(run)
                with self.assertRaises(Exception):client.debug_command(run.id,'restart',pause_id=state['pauseId'],restart_id='same',args=[42],kwargs={})
                self.assertEqual(run.debugger()['status'],'paused')
                result=subprocess.run([sys.executable,'-m','chaos','debug','restart',run.id,'--server',client.base_url.rsplit('/api',1)[0],'--pause-id',str(state['pauseId']),'--restart-id',state['conditionRecovery']['restarts'][1]['id'],'--args-json','[42]','--kwargs-json','{}'],cwd=ROOT,capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(run.result(3),42)
            finally:server.shutdown();server.server_close();worker.join(2)
    def test_sdk_keyword_contract_and_schema_describe_real_recovery(self):
        import inspect
        from chaos.framework import RunHandle
        from chaos.openapi import specification
        self.assertIn('pause_on_condition',inspect.signature(Client.start_debug).parameters)
        for method in (Framework.debug_command,Client.debug_command,RunHandle.debug_command):
            self.assertTrue({'restart_id','args','kwargs'}.issubset(inspect.signature(method).parameters))
        schemas=specification()['components']['schemas']
        self.assertFalse(schemas['DebugConfig']['properties']['pauseOnCondition']['default'])
        self.assertIn('restart',schemas['DebugCommand']['properties']['command']['enum'])
        self.assertIn('conditionRecovery',schemas['Debugger']['properties'])

    def test_macro_phase_cannot_enter_runtime_debugger(self):
        with self.assertRaises(Exception):Framework.evaluate('macro x(){syntax_literal(invoke_debugger(condition("x")))};x!()')
        self.assertTrue(self.app.capabilities()['debugger']['conditionRecovery'])
        self.assertFalse(self.app.capabilities()['debugger']['restartResumable'])

    def test_default_debug_calls_preserve_legacy_wire_and_invalid_values_reach_validator(self):
        legacy={'breakpoints':[],'pauseOnEntry':True,'pauseTimeout':300}
        client=Client('http://127.0.0.1:1')
        with patch.object(client,'_request',return_value={'id':'run','project_id':'p'}) as wire:
            for supplied in ({},{'pause_on_condition':False}):
                client.start_debug('p',1,**supplied)
                self.assertEqual(wire.call_args.args,('POST','/runs',{'projectId':'p','version':1,'input':None,'mode':'source','autoOptimize':False,'trace':False,'debug':legacy}))
                self.assertEqual(set(wire.call_args.args[2]['debug']),{'breakpoints','pauseOnEntry','pauseTimeout'})
                self.assertEqual(_legacy_debug_options(wire.call_args.args[2]['debug'],{'main.chaos':'1'})['pauseOnEntry'],True)
            client.start_debug('p',1,pause_on_condition=True)
            self.assertIs(wire.call_args.args[2]['debug']['pauseOnCondition'],True)
            for invalid in (None,1,0):
                client.start_debug('p',1,pause_on_condition=invalid)
                actual=wire.call_args.args[2]['debug']
                self.assertIs(actual['pauseOnCondition'],invalid)
                with self.assertRaises(ValueError):options(actual,{'main.chaos':'1'})
        with patch.object(self.app,'start',return_value=object()) as start:
            self.app.start_debug('p',1)
            self.assertEqual(start.call_args.kwargs['debug'],legacy)
            self.app.start_debug('p',1,pause_on_condition=True)
            self.assertIs(start.call_args.kwargs['debug']['pauseOnCondition'],True)
            for invalid in (None,1):
                self.app.start_debug('p',1,pause_on_condition=invalid)
                self.assertIs(start.call_args.kwargs['debug']['pauseOnCondition'],invalid)
        project=self.app.create_project('invalid-recovery-options',source='1')
        for invalid in (None,1):
            with self.assertRaises(ValueError):self.app.start_debug(project['id'],1,pause_on_condition=invalid)

    def test_run_handle_retains_legacy_backend_signature_and_additive_restart_data(self):
        from chaos.framework import RunHandle
        class LegacyBackend:
            def debug_command(self,run_id,command,*,pause_id=None,breakpoints=None):
                self.received=(run_id,command,pause_id,breakpoints)
                return {'legacy':True}
        legacy=LegacyBackend();handle=RunHandle(legacy,{'id':'run','project_id':'project'})
        self.assertEqual(handle.debug_command('continue',pause_id=7),{'legacy':True})
        self.assertEqual(legacy.received,('run','continue',7,None))
        class FeatureBackend:
            def debug_command(self,run_id,command,**options):
                self.received=(run_id,command,options)
                return {'feature':True}
        backend=FeatureBackend();handle=RunHandle(backend,{'id':'run','project_id':'project'})
        self.assertEqual(handle.debug_command('restart',pause_id=7,restart_id='restarts-1/0',args=[41],kwargs={}),{'feature':True})
        self.assertEqual(backend.received,('run','restart',{'pause_id':7,'breakpoints':None,'restart_id':'restarts-1/0','args':[41],'kwargs':{}}))
