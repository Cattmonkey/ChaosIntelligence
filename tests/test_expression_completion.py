"""Editor postfix recovery resolves real AST values without evaluation."""
import unittest
from unittest.mock import patch
from chaos import Framework
from chaos.language import Parser, LanguageError, Evaluator
from chaos.ide_language import analyze


class ExpressionCompletion(unittest.TestCase):
    def labels(self, source, position=None):
        return {item['label'] for item in analyze(source,position=position)['completions']}

    def test_call_await_parentheses_and_subscript_receivers(self):
        fields={'done','value'}
        for source in ['(await channel_receive(channel())).',
                       '((await channel_receive(channel()))).',
                       '({result:await channel_receive(channel())})["result"].']:
            with self.subTest(source=source):self.assertEqual(self.labels(source),fields)
        info=self.labels('let c=channel();channel_info(c).')
        self.assertIn('waitingReceivers',info);self.assertIn('capacity',info)
        self.assertEqual(self.labels('let c=channel();channel_info(c).wa'),{'waitingReceivers','waitingSenders'})
        self.assertIn('timing',self.labels('task_info(gather([])).'))

    def test_actual_method_and_record_callable_signatures(self):
        cases=[('task_group().spawn(',['task']),
               ('({send:channel_send})["send"](',['channel','value']),
               ('class C{fn work(self,x:Int,*,factor:Int=2)->Int{x*factor}};(C()).work(',['x','factor'])]
        for source,names in cases:
            with self.subTest(source=source):
                help=analyze(source)['signatureHelp'];self.assertIsNotNone(help)
                self.assertEqual([p['name']for p in help['parameters']],names)
                self.assertEqual(help['activeParameter'],0)
        help=analyze('({send:channel_send})["send"](channel(), value=')['signatureHelp']
        self.assertIsNotNone(help);self.assertEqual(help['activeParameter'],1)

    def test_recovery_preserves_function_scope_and_mid_document_utf16_ranges(self):
        self.assertIn('capacity',self.labels('fn inspect(c:Channel){channel_info(c).'))
        source='let note="🙂中文";let c=channel();channel_info(c).wa;42'
        point=source.index('.wa')+3;units=len(source[:point].encode('utf-16-le'))//2
        result=analyze(source,position=units)
        items=result['completions'];self.assertEqual({i['label']for i in items},{'waitingSenders','waitingReceivers'})
        self.assertTrue(all(i['from']==units-2 and i['to']==units for i in items))
        blank='let c=channel();channel_info(c).;42';point=blank.index('.;')+1
        self.assertIn('capacity',self.labels(blank,point))

    def test_shadow_mutability_and_dynamic_members_remain_unknown(self):
        for source in ['fn channel_info(c)->String{"local"};channel_info(1).',
                       'var inspect=channel_info;inspect(channel()).',
                       'let values={info:channel_info};values[input](channel()).',
                       'var values={info:channel_info};values.info(channel()).']:
            with self.subTest(source=source):self.assertEqual(self.labels(source),set())
        result=analyze('let send=partial(channel_send,channel());send(')
        self.assertEqual(result['signatureHelp']['parameters'],[])
        self.assertNotIn('channel_send',result['signatureHelp']['label'])

    def test_analysis_does_not_run_calls_getters_or_context_protocols(self):
        source='class C{fn __getattr__(self,name){tool("never",name)}};(C()).whatever.'
        with patch.object(Evaluator,'run',side_effect=AssertionError('no evaluation')),patch.object(Evaluator,'invoke',side_effect=AssertionError('no invocation')):
            self.assertEqual(self.labels(source),set())
            self.assertIn('done',self.labels('(await channel_receive(channel())).'))
        for source in ['channel_info(channel()).','task_group().spawn(']:
            with self.assertRaises(LanguageError):Parser(source).program()

    def test_member_and_call_sites_do_not_cross_neighboring_expressions(self):
        self.assertEqual(self.labels('channel_info(channel());input.'),set())
        self.assertIsNone(analyze('task_group().spawn;input(')['signatureHelp'])
        self.assertIsNone(analyze('macro identity(x){quote{unquote(x)}};identity(')['signatureHelp'])


if __name__=='__main__':unittest.main()
