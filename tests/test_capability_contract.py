"""One capability contract for model tools, candidate probes and project saves."""
from pathlib import Path
import tempfile
import threading
import unittest
from chaos import Framework, Registry, DeviceSpec, Settings, Client, Workflow
from chaos.framework import RunFailed
from chaos.client import ValidationError, ConflictError
from chaos.extensions import BUILTIN_TOOLS, capability_inventory, language_permissions
from chaos.evaluation import evaluate, validate_fixtures
from chaos.language import LanguageError
from chaos.program import parse_program
from chaos.server import make_server
from chaos.store import Conflict


def definition(source):
    return {'kind':'program','entry':'main.chaos','files':{'main.chaos':source},'languageVersions':{'main.chaos':2}}


class CapabilityContract(unittest.TestCase):
    def app(self):
        registry=Registry()
        def factory(context):raise AssertionError('Static capability discovery opened hardware')
        registry.register_device(DeviceSpec('desk',factory,description='Registered keyboard and mouse',resource_id='/private/hid/path'))
        app=Framework(registry=registry,settings=Settings(':memory:'));self.addCleanup(app.close)
        return app

    def test_descriptor_is_single_language_permission_source(self):
        self.assertEqual(language_permissions(),frozenset(name for name,descriptor in BUILTIN_TOOLS.items() if descriptor.get('reserved')))
        self.assertIn('device.control',language_permissions())
        inventory=capability_inventory(BUILTIN_TOOLS)
        self.assertEqual(inventory['device.control']['scope'],'language');self.assertFalse(inventory['device.control']['callable'])
        self.assertEqual(inventory['graph.patch']['scope'],'platform');self.assertFalse(inventory['graph.patch']['callable'])
        self.assertEqual(inventory['graph.inspect']['scope'],'tool');self.assertTrue(inventory['graph.inspect']['callable'])
        inventory['device.control']['description']='changed'
        self.assertNotEqual(BUILTIN_TOOLS['device.control']['description'],'changed')

    def test_device_inventory_is_static_safe_and_detached(self):
        app=self.app();first=app.capabilities()
        self.assertEqual(first['devices'],{'desk':{'name':'desk','description':'Registered keyboard and mouse','version':'1','profile':'chaos-hid/1'}})
        self.assertNotIn('/private/hid/path',str(first))
        first['devices']['desk']['description']='changed'
        self.assertEqual(app.capabilities()['devices']['desk']['description'],'Registered keyboard and mouse')
        self.assertEqual(first['apiVersion'],'v1')

    def test_all_language_only_permissions_are_rejected_as_fixture_tools(self):
        for name in language_permissions():
            with self.subTest(name=name),self.assertRaisesRegex(LanguageError,'Reserved effects'):
                validate_fixtures({'tools':[{'name':name,'input':{},'output':42}]})
        with self.assertRaises(LanguageError):validate_fixtures({'tools':[{'name':'graph.patch','input':{},'output':42}]})

    def test_device_fixture_cannot_turn_forbidden_tool_into_a_passing_program(self):
        with self.assertRaises(LanguageError):evaluate('tool("device.control", {})',fixtures={'tools':[{'name':'device.control','input':{},'output':42}]},capabilities=['device.control'])

    def test_ordinary_fixture_tool_still_executes(self):
        result=evaluate('tool("text.normalize", " A ")',fixtures={'tools':[{'name':'text.normalize','input':' A ','output':'A'}]},capabilities=['text.normalize'])
        self.assertEqual(result['value'],'A');self.assertTrue(result['fixtures']['passed'])

    def test_program_probe_cannot_certify_reserved_device_tool(self):
        app=self.app();app.create_project('probe',source='let p=program_from(input.definition);await program_probe(p,input.cases)',capabilities=['device.control'])
        payload={'definition':definition('tool("device.control", {})'),'cases':[{'input':None,'expect':42,'fixtures':{'tools':[{'name':'device.control','input':{},'output':42}]}}]}
        with self.assertRaises(RunFailed) as caught:app.start('probe',1,payload,mode='source').result(5)
        self.assertIn('Reserved effects',str(caught.exception))
        self.assertEqual(app.get_project('probe')['version'],1)

    def test_graph_preflight_rejects_native_permission_as_tool(self):
        app=self.app();graph=Workflow().input().tool('hid','device.control').output().connect('input','hid').connect('hid','output')
        app.create_project('graph',graph=graph,capabilities=['device.control'])
        with self.assertRaisesRegex(LanguageError,'语言专属权限'):app.validate('graph')

    def test_graph_agent_preflight_rejects_native_permission(self):
        app=self.app();graph=Workflow().input().agent('worker','',tools=['device.control']).output().connect('input','worker').connect('worker','output')
        app.create_project('graph',graph=graph,capabilities=['device.control'])
        with self.assertRaisesRegex(LanguageError,'语言专属权限'):app.validate('graph')

    def test_graph_function_scope_keeps_native_permission(self):
        app=self.app();graph=Workflow().input().function('worker','let t=device_open("desk");input+1',tools=['device.control']).output().connect('input','worker').connect('worker','output')
        project=app.create_project('graph',graph=graph,capabilities=['device.control'])
        self.assertTrue(app.validate('graph')['valid'])
        self.assertEqual(app.start('graph',project['version'],41,mode='graph').result(5),42)

    def test_historical_agent_definition_decode_is_unchanged(self):
        app=self.app();value={'kind':'agent','name':'legacy','config':{'provider':'local','tools':['device.control']}}
        self.assertEqual(app.validate_agent(value)['definition'],value)

    def test_sdk_ir_and_permissions_commit_in_one_revision(self):
        app=self.app();app.create_project('edit',source='40+1',capabilities=[])
        edited=app.edit_program('edit',1,program=parse_program('40+2'),capabilities=['device.control'])
        self.assertEqual(edited['version'],2);self.assertEqual(edited['capabilities'],['device.control'])
        self.assertEqual(Framework.evaluate(edited['source']),42);self.assertEqual(app.get_project('edit'),edited)

    def test_legacy_ir_save_keeps_existing_permissions(self):
        app=self.app();app.create_project('edit',source='40+1',capabilities=['device.control'])
        self.assertEqual(app.edit_program('edit',1,program=parse_program('40+2'))['capabilities'],['device.control'])

    def test_stale_or_invalid_ir_permission_edit_changes_neither(self):
        app=self.app();before=app.create_project('edit',source='40+1',capabilities=[])
        with self.assertRaises(ValueError):app.edit_program('edit',1,program=parse_program('40+2'),capabilities=[True])
        self.assertEqual(app.get_project('edit'),before)
        app.edit_program('edit',1,source='39+2');current=app.get_project('edit')
        with self.assertRaises(Conflict):app.edit_program('edit',1,program=parse_program('40+2'),capabilities=['device.control'])
        self.assertEqual(app.get_project('edit'),current)

    def test_http_discovery_and_atomic_ir_permission_edit(self):
        app=self.app()
        with tempfile.TemporaryDirectory() as directory:
            server=make_server(port=0,framework=app,state_dir=Path(directory)/'state',update_home=Path(directory)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));self.assertEqual(client.capabilities(),app.capabilities())
                client.create_project('http',source='40+1',capabilities=[])
                edited=client.edit_program('http',1,program=parse_program('40+2'),capabilities=['device.control'])
                self.assertEqual(edited['version'],2);self.assertEqual(edited['capabilities'],['device.control'])
                self.assertEqual(client.start('http',2,None,mode='source').result(),42)
                with self.assertRaises(ConflictError):client.edit_program('http',1,program=parse_program('null'),capabilities=[])
                self.assertEqual(client.get_project('http'),edited)
                with self.assertRaises(ValidationError):client.edit_program('http',2,program=parse_program('null'),capabilities=[True])
                self.assertEqual(client.get_project('http'),edited)
                self.assertEqual(client.edit_program('http',2,source='41+1')['capabilities'],['device.control'])
            finally:server.shutdown();server.server_close();thread.join(2)
