"""Channel presentation is driven by real binding identities, never execution."""
import unittest
from unittest.mock import patch
from chaos.ide_language import analyze,builtin_symbols
from chaos.code_presentation import render_code
from chaos.language import Evaluator

class ChannelIDE(unittest.TestCase):
    def symbols(self,source):
        return {item['name']:item for item in analyze(source)['symbols']}

    def test_signatures_and_bounded_metadata_fields(self):
        table=builtin_symbols()
        self.assertEqual(table['channel'].parameters,[('capacity','Int','positional',True)])
        self.assertEqual(table['channel_send'].parameters,[('channel','Channel','positional',False),('value','Any','positional',False)])
        for name in ('channel_receive','channel_close','channel_info'):
            self.assertEqual(table[name].parameters,[('channel','Channel','positional',False)])
        self.assertEqual(table['channel'].returns,'Channel');self.assertEqual(table['channel_close'].returns,'Bool')
        fields={'id':'String','state':'String','capacity':'Int','buffered':'Int','waitingSenders':'Int','waitingReceivers':'Int',
                'sendTaskIds':'List','receiveTaskIds':'List','sendersTruncated':'Bool','receiversTruncated':'Bool','location':'Record'}
        self.assertEqual({key:value.type for key,value in table['channel_info'].result_info.members.items()},fields)
        self.assertEqual({row['label'] for row in analyze('let info=channel_info(channel());info.')['completions']},set(fields))

    def test_cold_send_receive_and_alias_awaited_metadata(self):
        prefix='let ch=channel();let receive=channel_receive;let send=channel_send;'
        for call in ('channel_receive(ch)','channel_receive(channel=ch)','receive(ch)','spawn(receive(ch))'):
            with self.subTest(call=call):
                source=prefix+'let task='+call+';let step=await task;let done=step.done;let value=step.value;'
                symbols=self.symbols(source)
                self.assertEqual([symbols[n]['type']for n in ('task','step','done','value')],['Task','Record','Bool','Any'])
                self.assertEqual({r['label']for r in analyze(source+'step.')['completions']},{'done','value'})
        symbols=self.symbols(prefix+'let sent=await send(ch,42);let closed=channel_close(ch)')
        self.assertEqual(symbols['sent']['type'],'Null');self.assertEqual(symbols['closed']['type'],'Bool')
        symbols=self.symbols(prefix+'async with task_group() as group{let step=await group.spawn(receive(ch));let done=step.done}')
        self.assertEqual(symbols['done']['type'],'Bool')

    def test_annotation_iteration_and_factory_alias(self):
        source='let make=channel;let ch:Channel=make();let cursor=aiter(ch);async for value in ch{value};let values=await acollect(ch);'
        symbols=self.symbols(source)
        self.assertEqual([symbols[n]['type']for n in ('ch','cursor','value','values')],['Channel','AsyncIterator','Any','List'])
        result=analyze('fn consume(ch:Channel){channel_info(ch)}')
        self.assertFalse(result['diagnostics']);self.assertTrue(any(x['type']=='type'for x in result['tokens']))

    def test_wrong_arguments_and_signature_help_use_native_contract(self):
        for source in ('channel("wrong")','channel_send(42,"value")','channel_receive(42)','channel_info(42)','channel_close(42)'):
            with self.subTest(source=source):self.assertTrue(any(d['code']=='type_mismatch' for d in analyze(source)['diagnostics']))
        info=analyze('let ch=channel();channel_send(ch,')['signatureHelp']
        self.assertEqual(info['activeParameter'],1);self.assertEqual(info['parameters'][1]['name'],'value')
        self.assertFalse(analyze('fn consume(value){channel_receive(value)}')['diagnostics'])

    def test_shadowing_and_mutable_tasks_do_not_invent_receive_record(self):
        cases=['fn channel_receive(value)->String{"local"};let step=await channel_receive(42);',
               'let ch=channel();var task=channel_receive(ch);let step=await task;']
        for source in cases:
            with self.subTest(source=source):self.assertEqual(self.symbols(source)['step']['type'],'Any')
        result=analyze('let channel=42;channel')
        self.assertEqual([r['type']for r in result['symbols']if r['name']=='channel'],['Int'])

    def test_documentation_tokens_hints_preserve_source_and_never_execute(self):
        from chaos import channels
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis must not execute')), \
             patch.object(channels,'info',side_effect=AssertionError('analysis must not read runtime')):
            source='let ch:Channel=channel();let step=await channel_receive(ch);let done=step.done;'
            result=analyze(source);html=render_code(source,'chaos')
        self.assertTrue(result['complete']);self.assertFalse(result['diagnostics'])
        self.assertIn('chaos-token-type',html);self.assertIn('channel_receive(channel: Channel)',html)
        self.assertIn('done: Bool',html);self.assertNotIn('Task&lt;',html)
