"""Complete metadata projects existing language behavior without executing it."""
import dataclasses
import inspect
import unittest
from unittest.mock import patch

from chaos.ide_language import analyze, builtin_symbols, native_info
from chaos.language import Evaluator, Native, LanguageError
from chaos.native_specs import SHAPES, SPECS, all_specs, member_specs_for, spec_for, call_spec_for
from chaos.program import decode_program, format_program, parse_program, plain


class NativeCatalogTests(unittest.TestCase):
    def symbols(self, source):
        result = analyze(source)
        self.assertEqual(result['diagnostics'], [], result['diagnostics'])
        return {item['name']: item for item in result['symbols']}

    def test_global_coverage_is_bidirectional_and_member_exports_stay_separate(self):
        env = Evaluator().env.values
        natives = {name: value for name, value in env.items() if type(value) is Native}
        globals_ = {name for name in all_specs() if '.' not in name and name != 'Type'}
        self.assertEqual(set(natives), globals_)
        self.assertEqual(len(natives), 336)
        self.assertEqual(len(all_specs()), 382)
        for name, native in natives.items():
            self.assertEqual(native.spec_id, name)
            self.assertIs(spec_for(native.spec_id), SPECS[name])
        receivers = {'Device','Computer','AgentSession','Process','Program','Team','Plan','Goal','Skill','EscapeContinuation','Continuation','Robot','Bot'}
        declarations = [spec for spec in all_specs().values() if spec.inference_rule == 'receiver_call']
        self.assertEqual({spec.receiver_type for spec in declarations}, receivers)
        for spec in declarations:
            self.assertNotIn(spec.id, SPECS)
            self.assertIs(call_spec_for(spec.receiver_type), spec)
            self.assertNotIn('__call__', member_specs_for(spec.receiver_type))
        self.assertIn('Type.__call__', SPECS)
        self.assertIn('TaskGroup.spawn', SPECS)

    def test_real_argument_names_modes_and_optional_defaults_match_every_global(self):
        modes = {inspect.Parameter.POSITIONAL_ONLY:'positional_only',
                 inspect.Parameter.POSITIONAL_OR_KEYWORD:'positional',
                 inspect.Parameter.KEYWORD_ONLY:'keyword_only',
                 inspect.Parameter.VAR_POSITIONAL:'varargs',
                 inspect.Parameter.VAR_KEYWORD:'varkw'}
        for name, value in Evaluator().env.values.items():
            if type(value) is not Native:
                continue
            with self.subTest(name=name):
                actual = list(inspect.signature(value.call).parameters.values())
                declared = spec_for(name).parameters
                self.assertEqual([(p.name,modes[p.kind],p.default is not p.empty) for p in actual],
                                 [(p.name,p.mode,p.optional) for p in declared])
        self.assertEqual(spec_for('partial').parameters[0].mode, 'positional_only')
        self.assertEqual(spec_for('goal').parameters[-1].default_display, '8')
        self.assertEqual(spec_for('session_open').parameters[-1].default_display, '100000')
        self.assertEqual(spec_for('replay_skill').parameters[-1].default_display, '{}')

    def test_catalog_and_nested_shape_declarations_are_immutable(self):
        with self.assertRaises(TypeError):
            all_specs()['invented'] = spec_for('goal')
        with self.assertRaises(dataclasses.FrozenInstanceError):
            spec_for('goal').result_type = 'String'
        for spec in all_specs().values():
            for shape in (spec.result_shape, spec.awaited_shape):
                if shape is not None:
                    self.assertIn(shape, SHAPES)
        for fields in SHAPES.values():
            self.assertEqual(len(fields), len({item.name for item in fields}))
            for field in fields:
                if field.shape is not None:
                    self.assertIn(field.shape, SHAPES)

    def test_analysis_uses_descriptors_without_inspecting_or_invoking_implementations(self):
        class InertImplementation:
            @property
            def __signature__(self):
                raise AssertionError('Tooling inspected an executable implementation')

            def __call__(self, *args, **kwargs):
                raise AssertionError('Tooling invoked a native implementation')

        original = Evaluator.builtins
        def inert(evaluator):
            env = original(evaluator)
            for value in env.values.values():
                if type(value) is Native:
                    value.call = InertImplementation()
            return env

        with patch.object(Evaluator, 'builtins', inert), \
             patch.object(Evaluator, 'run', side_effect=AssertionError('Ran source')):
            symbols = self.symbols('let s=await session_load("chat");let p=current_program();'
                                   'let report=await program_probe(p,[]);let info=agent_info(input);'
                                   'let bound=agent_bind(input,[]);let session=session_bind(s,[]);'
                                   'let generic=generic_function("name");let restored=agent_from(input);')
            self.assertEqual(symbols['s']['type'], 'AgentSession')
            self.assertEqual(symbols['report']['type'], 'Record')
            self.assertEqual(builtin_symbols()['goal'].returns, 'Goal')

    def test_returned_callable_contracts_are_declared_and_keep_no_global_identity(self):
        for name in ('agent_from','agent_bind','session_bind','generic_function'):
            with self.subTest(name=name):
                descriptor = spec_for(name).result_callable
                self.assertIsNotNone(descriptor)
                projected = native_info(spec_for(name)).result_info
                self.assertEqual(projected.builtin, '')
                self.assertEqual(projected.parameters,
                                 [(p.name,p.type,p.mode,p.optional) for p in descriptor.parameters])
                self.assertEqual(projected.returns, descriptor.result_type)
                self.assertEqual(projected.awaited, descriptor.awaited_type)
                if descriptor.awaited_shape:
                    self.assertEqual(set(projected.awaited_info.members),
                                     {field.name for field in SHAPES[descriptor.awaited_shape]})
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    descriptor.result_type = 'String'
        source = 'let bind=session_bind;let callable=bind(input,[]);let alias=callable;let receipt=await alias(1);receipt.'
        self.assertEqual({item['label'] for item in analyze(source)['completions']},
                         {field.name for field in SHAPES['SessionReceipt']})

    def test_missing_runtime_declaration_cannot_silently_fall_back(self):
        original = Evaluator.builtins
        def extra(evaluator):
            env = original(evaluator)
            env.bind('unreviewed_native', Native('unreviewed_native', lambda: None, evaluator))
            return env
        with patch.object(Evaluator, 'builtins', extra), self.assertRaisesRegex(ValueError, 'unreviewed_native'):
            builtin_symbols()

    def test_device_callable_signature_and_await_result_match_device_act(self):
        result = analyze('async fn use(d:Device, a:DeviceAction){await d(')
        signature = result['signatureHelp']
        self.assertIsNotNone(signature)
        self.assertEqual([(p['name'],p['type']) for p in signature['parameters']], [('action','DeviceAction')])
        values = self.symbols('async fn use(d:Device,a:DeviceAction){'
                              'let cold=d(a);let answer=await cold;answer}')
        self.assertEqual(values['cold']['type'], 'Task')
        self.assertEqual(values['answer']['type'], 'DeviceReceipt')
        self.assertEqual(spec_for('Device.__call__').awaited_type, spec_for('device_act').awaited_type)

    def test_all_fixed_receiver_signatures_and_cold_completion_types(self):
        calls = {'Device':('action','DeviceAction','DeviceReceipt'),
                 'Computer':('action','ComputerAction','Observation'),
                 'AgentSession':('input','Any','Record'), 'Process':('key','String','Record'),
                 'Program':('input','Any','Any'), 'Team':('input','Any','Any'),
                 'Plan':('input','Any','Record'), 'Goal':('input','Any','Record'),
                 'Skill':('values','Record','List')}
        for typ, (name, arg_type, completed) in calls.items():
            with self.subTest(receiver=typ):
                result = analyze('async fn use(value:'+typ+',arg:'+arg_type+'){value(')
                self.assertEqual(result['signatureHelp']['parameters'][0]['name'], name)
                symbols = self.symbols('async fn use(value:'+typ+',arg:'+arg_type+')'
                                       '{let cold=value(arg);let answer=await cold;answer}')
                self.assertEqual(symbols['cold']['type'], 'Task')
                self.assertEqual(symbols['answer']['type'], completed)

    def test_unrelated_native_values_do_not_gain_callable_metadata(self):
        for typ in ('DeviceAction','DeviceReceipt','ComputerAction','Observation','ObservedInput','AgentTool','Task'):
            with self.subTest(type=typ):
                self.assertNotIn('__call__', member_specs_for(typ))
                result = analyze('fn use(value:'+typ+'){value(')
                self.assertIsNone(result['signatureHelp'])

    def test_agent_definition_shape_and_callable_alias_shadowing_stay_precise(self):
        symbols = self.symbols('let factory=agent_from;let role=factory(input);let answer=role(42);')
        self.assertEqual(symbols['role']['type'], 'Function')
        self.assertEqual(symbols['answer']['type'], 'Any')
        self.assertEqual(self.symbols('fn agent_from(value)->String{"own"};let answer=agent_from(null)')['answer']['type'], 'String')
        self.assertEqual(self.symbols('var factory=agent_from;let answer=factory(input)')['answer']['type'], 'Any')
        items = analyze('let info=agent_info(input);info.config.')['completions']
        self.assertIn('maxSteps', {item['label'] for item in items})
        self.assertNotIn('apiKey', {item['label'] for item in items})

    def test_program_and_iteration_record_shapes_survive_aliases_and_await(self):
        cases = [('let f=program_info;let r=f(input);r.', {'kind','entry','files','languageVersions'}),
                 ('let f=program_metadata;let r=f(input);r.', {'definitionHash','origin'}),
                 ('let f=program_ir;let r=f(input);r.', {'schema','languageVersion','file','root','semanticHash'}),
                 ('let f=program_history;let r=await f();r.', {'items','nextCursor','currentVersion','firstAvailableVersion'}),
                 ('let f=next;let r=f(input);r.', {'done','value'}),
                 ('let f=anext;let r=await f(input);r.', {'done','value'}),
                 ('let s=await session_load("chat");let r=await s(1);r.', {'key','messageId','status','output','revision','replayed'})]
        for source, fields in cases:
            with self.subTest(source=source):
                self.assertEqual({item['label'] for item in analyze(source)['completions']}, fields)

    def test_unknown_mode_and_user_callback_results_remain_conservative(self):
        symbols = self.symbols('let a=mode_from(input);let b=await load_mode("key");'
                               'let c=await run_program(input);let d=partial(input,1);')
        self.assertEqual([symbols[key]['type'] for key in ('a','b','c','d')], ['Any','Any','Any','Function'])
        for source in ('fn use(value){let answer=iter(value);answer}',
                       'fn use(value){let answer=aiter(value);answer}'):
            self.assertEqual(self.symbols(source)['answer']['type'], 'Any')

    def test_existing_dynamic_team_binding_preserves_entry_parameters(self):
        source = ('let t=team_from({kind:"team",name:"T",entry:"work",roles:[{id:"work",agent:{kind:"agent",name:"w",config:{}}}]});'
                  'fn job(value:Int,*,label:String="x")->String{label};'
                  'let bound=team_bind(t,{work:job});bound(')
        signature = analyze(source)['signatureHelp']
        self.assertEqual([p['name'] for p in signature['parameters']], ['value','label'])
        self.assertEqual(signature['parameters'][1]['mode'], 'keyword_only')

    def test_structural_formatting_and_semantic_token_contract_remain_unchanged(self):
        for source in ('let open=session_load;async fn f(){let s=await open("chat");await s(42)}',
                       'let p=current_program();let info=program_info(p);info.entry',
                       'let x=goal("goal","planner","verify");x'):
            ir = parse_program(source)
            formatted = format_program(ir)
            self.assertEqual(plain(decode_program(ir)), plain(decode_program(parse_program(formatted))))
            result = analyze(formatted)
            self.assertFalse(result['diagnostics'])
            self.assertTrue(result['tokens'])
            self.assertTrue(all(0 <= item['from'] < item['to'] <= len(formatted) for item in result['tokens']))

    def test_runtime_validation_and_cold_mode_behavior_are_not_replaced_by_metadata(self):
        evaluator = Evaluator(mode_handler=lambda *args: (_ for _ in ()).throw(AssertionError('cold task executed')))
        self.assertEqual(evaluator.run('type_of(plan([{id:"step",call:"worker"}])())'), 'Task')
        with self.assertRaises(LanguageError):
            Evaluator().run('session_history("chat",limit=0)')
        with self.assertRaises(LanguageError):
            Evaluator().run('partial(fn=fn(x){x})')


if __name__ == '__main__':
    unittest.main()
