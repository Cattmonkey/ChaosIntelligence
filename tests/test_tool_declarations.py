"""One bounded declaration projection, actual coarse contracts and batch safety."""
import copy
import json
import unittest

from chaos import Registry, ToolSpec
from chaos.harness import NativeHarness, HarnessError, _validated_response
from chaos.tool_declarations import native_tool
from tests.test_harness_runtime import CONFIG, FakeClient, call, message


class ToolDeclarationTests(unittest.TestCase):
    def test_all_eight_actual_contracts_have_one_exact_wrapper(self):
        expected={'Any':{},'Number':{'type':'number'},'Int':{'type':'integer'},
                  'String':{'type':'string'},'Bool':{'type':'boolean'},
                  'List':{'type':'array'},'Record':{'type':'object'},'Null':{'type':'null'}}
        for kind,schema in expected.items():
            with self.subTest(kind=kind):
                actual=native_tool('work',{'description':'Work','inputType':kind},mode='wrapped')
                self.assertEqual(actual,{'name':'work','description':'Work','wrapped':True,'inputType':kind,
                    'parameters':{'type':'object','properties':{'input':schema},'required':['input'],'additionalProperties':False}})

    def test_missing_contract_is_any_but_explicit_unknown_is_rejected(self):
        self.assertEqual(native_tool('work',{},mode='wrapped')['inputType'],'Any')
        for kind in ('Function','Map','integer','',None,True,[],{}):
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                native_tool('work',{'inputType':kind},mode='wrapped')

    def test_flat_is_explicit_and_does_not_acquire_language_fields(self):
        schema={'type':'object','properties':{'code':{'type':'string'}},'required':['code']}
        result=native_tool('work',{'description':'Flat','inputType':'Int'},mode='flat',parameters=schema)
        self.assertEqual(result,{'name':'work','description':'Flat','parameters':schema})
        for params in (None,{},[],{'type':'array'}):
            with self.subTest(params=params),self.assertRaises(ValueError):
                native_tool('work',{},mode='flat',parameters=params)
        for mode in ('auto','',True,None):
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                native_tool('work',{},mode=mode)
        with self.assertRaises(TypeError):native_tool('work',{})

    def test_copies_are_independent_of_host_other_calls_and_schema_factories(self):
        descriptor={'description':'Work','inputType':'Record'}
        parameters={'type':'object','properties':{'value':{'type':'string'}}}
        original=copy.deepcopy(parameters)
        first=native_tool('work',descriptor,mode='wrapped',parameters=parameters)
        second=native_tool('work',descriptor,mode='wrapped',parameters=parameters)
        first['parameters']['properties']['input']['properties']['value']['type']='number'
        first['description']='Changed'
        self.assertEqual(parameters,original)
        self.assertEqual(second['parameters']['properties']['input'],original)
        self.assertEqual(descriptor,{'description':'Work','inputType':'Record'})
        coarse=native_tool('work',descriptor,mode='wrapped')
        coarse['parameters']['properties']['input']['type']='array'
        self.assertEqual(native_tool('work',descriptor,mode='wrapped')['parameters']['properties']['input'],{'type':'object'})

    def test_metadata_has_no_name_or_natural_text_inference_or_authority(self):
        effects=[]
        descriptor={'description':'browser code execute image delegate Int Record input',
                    'inputType':'String','handler':lambda:effects.append('called'),
                    'effect':'write','reserved':True,'capabilities':['secret']}
        result=native_tool('code.execute',descriptor,mode='wrapped')
        self.assertEqual(result['inputType'],'String')
        self.assertEqual(set(result),{'name','description','wrapped','inputType','parameters'})
        self.assertEqual(effects,[])
        self.assertNotIn('secret',json.dumps(result))

    def test_non_string_collision_keys_are_rejected_before_hooks(self):
        effects=[]
        class HostileKey:
            def __init__(self,target):self.target=target
            def __hash__(self):return hash(self.target)
            def __eq__(self,other):effects.append(other);raise AssertionError('hook invoked')
        descriptor={HostileKey('description'):'hidden'}
        parameters={HostileKey('type'):'object'}
        # Construction hashes are outside adaptation; no equality was needed.
        self.assertEqual(effects,[])
        with self.assertRaises(ValueError):native_tool('work',descriptor,mode='wrapped')
        with self.assertRaises(ValueError):native_tool('work',{},mode='flat',parameters=parameters)
        self.assertEqual(effects,[])

    def test_presentation_is_only_explicit_caller_metadata(self):
        result=native_tool('work',{},mode='wrapped',image_output=False,category='tools')
        self.assertIs(result['imageOutput'],False);self.assertEqual(result['category'],'tools')
        for value in (0,1,'true',[],{}):
            with self.subTest(value=value),self.assertRaises(ValueError):
                native_tool('work',{},mode='wrapped',image_output=value)
        with self.assertRaises(ValueError):native_tool('work',{},mode='wrapped',category=1)

    def test_schema_copy_is_json_bounded_and_never_runs_hooks(self):
        cyclic={'type':'object'};cyclic['properties']=cyclic
        deep={'type':'object'};cursor=deep
        for _ in range(110):cursor['child']={};cursor=cursor['child']
        for schema in (cyclic,deep,{'type':'object','value':float('nan')},
                       {'type':'object','value':'x'*250001},
                       {'type':'object','value':lambda:None}):
            with self.subTest(schema_type=type(schema).__name__),self.assertRaises(ValueError):
                native_tool('work',{},mode='flat',parameters=schema)
        with self.assertRaises(ValueError):native_tool('work',{},mode='wrapped',parameters=[])

    def test_real_runtime_check_rejects_bool_and_float_for_int(self):
        for value in (True,1.0,'1',None,[],{}):
            with self.subTest(value=value),self.assertRaises(HarnessError):
                _validated_response(message(calls=[call(args=json.dumps({'input':value}))]),
                                    {'tool_0':native_tool('work',{'inputType':'Int'},mode='wrapped')})
        for value in (True,'1'):
            with self.subTest(number=value),self.assertRaises(HarnessError):
                _validated_response(message(calls=[call(args=json.dumps({'input':value}))]),
                                    {'tool_0':native_tool('work',{'inputType':'Number'},mode='wrapped')})

    def test_any_list_and_record_do_not_gain_invented_constraints(self):
        rows=[('Any',None),('Any',True),('Any',7),('Any','text'),('Any',[{'a':1}]),
              ('Any',{'unknown':[None,True]}),('List',[1,'two',None,{}]),
              ('Record',{'arbitrary':[None,1,'two']})]
        for kind,value in rows:
            with self.subTest(kind=kind,value=value):
                _,pending,_=_validated_response(message(calls=[call(args=json.dumps({'input':value}))]),
                    {'tool_0':native_tool('work',{'inputType':kind},mode='wrapped')})
                self.assertEqual(pending[0][2],value)

    def test_schema_is_provider_metadata_without_new_local_schema_validator(self):
        spec=native_tool('work',{'inputType':'String'},mode='wrapped',
                         parameters={'type':'string','minLength':20})
        _,pending,_=_validated_response(message(calls=[call(args='{"input":"x"}')]),{'tool_0':spec})
        self.assertEqual(pending[0][2],'x')
        with self.assertRaises(HarnessError):
            _validated_response(message(calls=[call(args='{"input":NaN}')]),
                                {'tool_0':native_tool('work',{},mode='wrapped')})

    def test_real_harness_rejects_whole_later_bad_typed_batch_before_effect(self):
        client=FakeClient([message(calls=[call(args='{"input":1}'),call(args='{"input":"wrong"}',ident='b')])])
        effects=[]
        with self.assertRaises(HarnessError):
            NativeHarness(CONFIG,client_factory=lambda _:client).run('',None,
                tools=[native_tool('write',{'inputType':'Int'},mode='wrapped')],
                call_tool=lambda name,value:effects.append((name,value)))
        self.assertEqual(effects,[]);self.assertTrue(client.closed)
        self.assertEqual(len(client.requests),1)
        self.assertEqual(client.requests[0]['tools'][0]['function']['parameters']['properties']['input'],{'type':'integer'})

    def test_public_toolspec_and_registry_contract_shape_are_preserved(self):
        spec=ToolSpec('work',lambda value,context:value,'Work','write','Int','String','Label','2')
        expected={'label':'Label','description':'Work','effect':'write','inputType':'Int',
                  'outputType':'String','version':'2','custom':True}
        self.assertEqual(spec.describe(),expected)
        registry=Registry();registry.register_tool(spec)
        result=native_tool('work',spec.describe(),mode='wrapped')
        result['inputType']='Record'
        self.assertEqual(spec.describe(),expected)

    def test_control_flat_shapes_and_original_browser_schema_export_are_preserved(self):
        from chaos.control import ToolEnvironment, CODE_SCHEMA, BROWSER_SCHEMA, IMAGE_SCHEMA
        from chaos.execution import browser_schema
        from chaos.tool_schemas import DELEGATE_SCHEMA
        env=ToolEnvironment.__new__(ToolEnvironment)
        env.config={'codeEnabled':True,'browserEnabled':True,'depth':0}
        env.plugins={'code':True,'browser':True,'image':True}
        declarations=env.declarations()
        self.assertEqual([row['name']for row in declarations],
                         ['code.execute','browser.control','image.generate','agent.delegate'])
        self.assertEqual([row['parameters']for row in declarations],
                         [CODE_SCHEMA,BROWSER_SCHEMA,IMAGE_SCHEMA,DELEGATE_SCHEMA])
        self.assertTrue(all('wrapped'not in row and 'inputType'not in row for row in declarations))
        self.assertEqual([row.get('category')for row in declarations],['code','browser',None,None])
        self.assertTrue(declarations[1]['imageOutput'])
        self.assertEqual(browser_schema(),BROWSER_SCHEMA)
        declarations[1]['parameters']['oneOf'].clear()
        self.assertTrue(BROWSER_SCHEMA['oneOf'])
        self.assertTrue(env.declarations()[1]['parameters']['oneOf'])
