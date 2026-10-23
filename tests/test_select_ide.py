"""Selection hints describe actual native identities without starting a branch."""
import inspect
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.code_presentation import render_code
from chaos.ide_language import analyze,builtin_symbols
from chaos.language import Evaluator,Native
from chaos.native_specs import SHAPES,spec_for
from chaos.suspension import Scheduler

class SelectIDE(unittest.TestCase):
    def symbols(self,source):return {x['name']:x['type']for x in analyze(source)['symbols']}

    def test_shared_signatures_match_real_registered_native_callables(self):
        env=Evaluator().env.values
        for name in ('send_case','receive_case','case_info','channel_select'):
            native=env[name];spec=spec_for(name);self.assertIs(type(native),Native);self.assertEqual(native.spec_id,name)
            actual=list(inspect.signature(native.call).parameters.values())
            self.assertEqual([p.name for p in actual],[p.name for p in spec.parameters])
            self.assertEqual([p.default is not p.empty for p in actual],[p.optional for p in spec.parameters])
            self.assertTrue(all(p.kind==p.POSITIONAL_OR_KEYWORD for p in actual))
        help=analyze('channel_select([],timeout=')['signatureHelp']
        self.assertEqual(help['activeParameter'],1);self.assertEqual(help['parameters'][1]['type'],'Number | Null')
        self.assertIn('原子提交一个候选',help['label'])
        help=analyze('send_case(channel(),')['signatureHelp'];self.assertEqual(help['activeParameter'],1);self.assertEqual(help['parameters'][1]['name'],'value')

    def test_case_type_alias_and_annotated_user_constructor(self):
        source='let make=receive_case;let input:Channel=channel();let candidate:ChannelCase=make(input);fn incoming(ch:Channel)->ChannelCase{receive_case(ch)};let custom=incoming(input);let metadata=case_info(custom);'
        self.assertFalse(analyze(source)['diagnostics']);values=self.symbols(source)
        self.assertEqual([values[n]for n in ('candidate','custom','metadata')],['ChannelCase','ChannelCase','Record'])
        self.assertTrue(any(t['type']=='type'for t in analyze(source)['tokens']))
        labels={i['label']for i in analyze(source+'metadata.')['completions']}
        self.assertEqual(labels,{'id','op','channelId','location'})

    def test_direct_await_alias_and_spawn_follow_shared_result_shape(self):
        case='receive_case(channel())'
        for expr in ['channel_select(['+case+'])','spawn(channel_select(['+case+']))','task_group().spawn(channel_select(['+case+']))']:
            with self.subTest(expr=expr):
                items={i['label']:i for i in analyze('(await '+expr+').')['completions']}
                self.assertEqual(set(items),{'index','op','done','value','timedOut'})
                self.assertIn('Int | Null',items['index']['detail']);self.assertIn('String | Null',items['op']['detail'])
        result=self.symbols('let choose=channel_select;let selected=await choose([receive_case(channel())]);let timed=selected.timedOut;let done=selected.done;let value=selected.value;')
        self.assertEqual([result[n]for n in ('timed','done','value')],['Bool','Bool','Any'])
        self.assertEqual({i['label']for i in analyze('({inspect:case_info})["inspect"](receive_case(channel())).')['completions']},{'id','op','channelId','location'})

    def test_metadata_fields_match_actual_cold_runtime_without_payload(self):
        value=Framework.evaluate('let c=channel();let option=send_case(c,{secret:42});[case_info(option),task_info(channel_select([option],timeout=1)),task_info(sleep(0))]')
        case,task,ordinary=value
        self.assertEqual(set(case),{f.name for f in SHAPES['ChannelCaseInfo']})
        self.assertNotIn('secret',str(case));self.assertEqual(set(task),{f.name for f in SHAPES['TaskInfo']})
        self.assertEqual(set(task['selection']),{f.name for f in SHAPES['TaskSelection']})
        self.assertEqual(task['selection']['caseCount'],1);self.assertIsNone(task['selection']['index']);self.assertEqual(task['selection']['cases'][0]['op'],'send')
        self.assertIsNone(ordinary['selection']);self.assertEqual(task['memberTaskIds'],[]);self.assertIsNone(task['channelId'])
        self.assertEqual({i['label']for i in analyze('task_info(channel_select([receive_case(channel())])).selection.')['completions']},{'caseCount','cases','casesTruncated','index'})

    def test_selection_metadata_is_bounded_without_deduplicating_indices(self):
        value=Framework.evaluate('let c=channel();let option=receive_case(c);task_info(channel_select(map(fn(i){option},range(105)))).selection')
        self.assertEqual(value['caseCount'],105);self.assertTrue(value['casesTruncated']);self.assertEqual(len(value['cases']),100)
        self.assertEqual([row['index']for row in value['cases']],list(range(100)))
        self.assertEqual(len({row['channelId']for row in value['cases']}),1)

    def test_wrong_known_arguments_are_diagnosed_and_dynamic_cases_stay_conservative(self):
        for source in ['send_case(1,42)','receive_case(42)','let fake={op:"receive"};case_info(fake)','channel_select(42)','channel_select([],timeout="wrong")']:
            with self.subTest(source=source):self.assertTrue(any(d['code']=='type_mismatch'for d in analyze(source)['diagnostics']))
        self.assertFalse(analyze('fn choose(options){channel_select(options)}')['diagnostics'])
        # The editor does not infer native identity from an operation-shaped record.
        self.assertEqual(self.symbols('let receive_case=fn(x){x};let candidate=receive_case({op:"receive",channel:42});')['candidate'],'Any')
        self.assertEqual(analyze('var choose=channel_select;(await choose(input)).')['completions'],[])
        self.assertEqual(analyze('fn channel_select(x)->String{"local"};(await channel_select(input)).')['completions'],[])

    def test_page_hints_utf16_and_type_colors_without_execution(self):
        from chaos import channel_select
        source='let title="🙂 中文";let option:ChannelCase=receive_case(channel());let chosen=await channel_select([option]);chosen.timedOut;case_info(option);'
        with patch.object(Evaluator,'run',side_effect=AssertionError('no evaluation')),patch.object(Scheduler,'spawn',side_effect=AssertionError('no task start')),patch.object(channel_select,'case_info',side_effect=AssertionError('no runtime inspection')):
            data=analyze(source);html=render_code(source,'chaos')
        self.assertFalse(data['diagnostics']);self.assertIn('chaos-token-type',html);self.assertIn('receive_case(channel: Channel)',html);self.assertIn('channel_select(cases: List',html);self.assertIn('case_info(case: ChannelCase)',html);self.assertIn('timedOut: Bool',html)
        raw=source.encode('utf-16-le');hints={raw[i['from']*2:i['to']*2].decode('utf-16-le'):i['detail']for i in data['referenceHints']}
        self.assertIn('receive_case(channel: Channel)',hints['receive_case'])

if __name__=='__main__':unittest.main()
