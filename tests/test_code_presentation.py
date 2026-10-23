import json
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from chaos.code_presentation import code_language, presentation_tokens, render_code
from chaos.documentation import Documentation, markdown_parser
from chaos.ide_language import analyze


class CodeHTML(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.source = ''
        self.in_code = False
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == 'code': self.in_code = True

    def handle_endtag(self, tag):
        if tag == 'code': self.in_code = False

    def handle_data(self, text):
        if self.in_code: self.source += text


class CodePresentation(unittest.TestCase):
    def test_chaos_uses_exact_shared_semantic_ranges(self):
        source = 'fn square(value: Int) -> Int { value * value };\nlet answer = square(3);\nanswer'
        self.assertEqual([{k: t[k] for k in ('from', 'to', 'type')} for t in presentation_tokens(source)], analyze(source)['tokens'])
        html = render_code(source, 'chaos example=square')
        for kind in ('keyword', 'function', 'parameter', 'type', 'operator', 'variable', 'number'):
            self.assertIn('chaos-token-' + kind, html)
        self.assertIn('square(value: Int) -&gt; Int', html)
        self.assertEqual(CodeHTML(html).source, source)

    def test_shadowed_builtin_is_analyzed_by_binding(self):
        source = 'let program_history = 7;\nprogram_history'
        values = presentation_tokens(source)
        names = [t for t in values if source[t['from']:t['to']] == 'program_history']
        self.assertEqual([t['type'] for t in names], ['variable', 'variable'])
        self.assertIn('program_history: Int', names[0]['detail'])

    def test_unicode_utf16_ranges_preserve_text_after_astral_character(self):
        source = 'let greeting = "😃 世界";\nfn answer() { 42 };\nanswer()\n'
        html = render_code(source, 'chaos')
        self.assertEqual(CodeHTML(html).source, source)
        self.assertIn('chaos-token-number">42</span>', html)
        self.assertIn('data-line="4"', html)

    def test_multiline_tokens_and_empty_lines_round_trip(self):
        source = 'let story = "first\n\nlast";\n# comment\n\nstory\n'
        html = render_code(source, 'chaos')
        self.assertEqual(CodeHTML(html).source, source)
        self.assertEqual(html.count('class="chaos-code-line"'), len(source.split('\n')))
        self.assertIn('chaos-token-string">last&quot;</span>', html)
        self.assertEqual(CodeHTML(render_code('', 'chaos')).source, '')

    def test_unfinished_source_remains_readable(self):
        source = 'fn work(value: Int) {\n  let result = "unfinished 😃\n  @'
        self.assertEqual(CodeHTML(render_code(source, 'chaos')).source, source)
        self.assertTrue(presentation_tokens(source))

    def test_source_and_language_metadata_never_become_html(self):
        source = '</code><img src=x onerror="alert(1)"><script>oops</script>'
        html = render_code(source, 'x"><svg/onload=alert(1)>')
        parsed = CodeHTML(html)
        self.assertEqual(parsed.source, source)
        self.assertEqual(set(tag for tag, _ in parsed.tags), {'pre', 'code', 'span'})
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        self.assertTrue(all(not key.startswith('on') for _, attrs in parsed.tags for key in attrs))

    def test_explicit_language_detection_does_not_guess_from_keywords(self):
        source = 'fn square(x) { x * x }'
        self.assertEqual(presentation_tokens(source, 'shell'), [])
        self.assertEqual(presentation_tokens(source, 'text'), [])
        self.assertEqual(code_language('CHAOS example=demo'), 'chaos')
        self.assertEqual(code_language('py'), 'python')
        self.assertNotIn('chaos-token-', render_code(source, 'mystery'))

    def test_python_uses_standard_tokenizer_without_execution(self):
        source = '# hi\ndef meaning(x):\n    return "😃" if x == 42 else None\n'
        tokens = presentation_tokens(source, 'python')
        self.assertEqual(tokens[0]['from'], 0)
        self.assertEqual(tokens[0]['to'], 4)
        self.assertEqual(tokens[0]['type'], 'comment')
        self.assertEqual(CodeHTML(render_code(source, 'py')).source, source)
        self.assertIn('chaos-token-keyword">return</span>', render_code(source, 'python'))
        self.assertIn('chaos-token-number">42</span>', render_code(source, 'python'))
        self.assertEqual(CodeHTML(render_code('def bad(\n', 'python')).source, 'def bad(\n')

    def test_cached_tokens_cannot_be_mutated_by_a_caller(self):
        first = presentation_tokens('let immutable = 42;')
        first[0]['type'] = 'evil'
        self.assertEqual(presentation_tokens('let immutable = 42;')[0]['type'], 'keyword')

    def test_large_and_failed_analysis_fall_back_to_original(self):
        source = 'a' * 100001
        self.assertEqual(presentation_tokens(source), [])
        self.assertEqual(CodeHTML(render_code(source, 'chaos')).source, source)
        with mock.patch('chaos.code_presentation.analyze', side_effect=ValueError('unavailable')):
            self.assertEqual(CodeHTML(render_code('let unique_fallback = 43;', 'chaos')).source, 'let unique_fallback = 43;')

    def test_markdown_code_block_uses_shared_safe_renderer(self):
        source = 'let value = "<b>😃</b>";\nvalue\n'
        html = markdown_parser().render('```chaos example=value\n' + source + '```\n')
        self.assertEqual(CodeHTML(html).source, source)
        self.assertIn('data-language="chaos"', html)
        self.assertIn('chaos-token-variable', html)
        self.assertNotIn('<b>', html)
        self.assertEqual(CodeHTML(markdown_parser().render('    42\n')).source, '42\n')

    def test_live_and_exported_documentation_have_identical_code_and_assets(self):
        docs = Documentation()
        with tempfile.TemporaryDirectory() as temp:
            result = docs.build(Path(temp) / 'site')
            self.assertEqual(result['status'], 'passed')
            shell = (Path(temp) / 'site/index.html').read_text()
            payload = shell.split('<script id="docs-bootstrap" type="application/json">', 1)[1].split('</script>', 1)[0]
            static = json.loads(payload)
            live = docs.snapshot()
            self.assertEqual(live['languageVersion'], '2')
            self.assertTrue(all(page['languageVersion'] == '2' for page in live['pages']))
            by_id = {page['id']: page for page in static['pages']}
            for page in live['pages']:
                live_code = CodeHTML(page['html'])
                offline_code = CodeHTML(by_id[page['id']]['html'])
                self.assertEqual(live_code.source, offline_code.source)
                self.assertEqual([attrs for tag, attrs in live_code.tags if tag == 'span' and 'chaos-token-' in attrs.get('class', '')],
                                 [attrs for tag, attrs in offline_code.tags if tag == 'span' and 'chaos-token-' in attrs.get('class', '')])
            for name in ('code.js', 'code.css'):
                self.assertEqual((Path(temp) / 'site' / name).read_bytes(), (docs.web / name).read_bytes())
            self.assertIn('<script src="code.js" defer>', shell)


if __name__ == '__main__': unittest.main()


class ReferenceHints(unittest.TestCase):
    def test_builtin_alias_user_function_and_record_member_references(self):
        source='fn twice(x:Int)->Int{x*2};let send=channel_send;let ch=channel();send(ch,twice(21));let record={work:twice};record.work(4);'
        hints=analyze(source)['referenceHints']
        details=[row['detail']for row in hints]
        self.assertTrue(any('channel_send(channel: Channel, value)' in detail for detail in details))
        self.assertTrue(any('send: Function' in detail for detail in details))
        self.assertTrue(any('twice(x: Int) -> Int' in detail for detail in details))
        html=render_code(source,'chaos')
        self.assertIn('title="channel_send(channel: Channel, value)',html)
        self.assertIn('title="twice(x: Int) -&gt; Int',html)
        self.assertEqual(CodeHTML(html).source,source)

    def test_reference_hints_follow_shadowed_and_mutable_bindings(self):
        source='let channel_send=42;channel_send;var receive=channel_receive;receive(input);'
        hints=analyze(source)['referenceHints']
        references={source[row['from']:row['to']]:row['detail']for row in hints}
        self.assertEqual(references['channel_send'],'channel_send: Int')
        self.assertEqual(references['receive'],'receive: Any')
        self.assertNotIn('channel_send(channel:',render_code(source,'chaos'))

    def test_receive_record_member_and_unicode_ranges_match_hover(self):
        source='let label="😃 中文";let ch=channel();let step=await channel_receive(ch);step.done;'
        data=analyze(source)
        for name,expected in [('channel_receive','channel_receive(channel: Channel)'),('done','done: Bool')]:
            start=len(source[:source.rindex(name)].encode('utf-16-le'))//2
            hint=next(row for row in data['referenceHints']if row['from']==start)
            self.assertIn(expected,hint['detail'])
            self.assertEqual(analyze(source,position=start)['hover']['contents'],hint['detail'])
        self.assertEqual(CodeHTML(render_code(source,'chaos')).source,source)

    def test_smallest_ast_reference_wins_and_does_not_split_utf16(self):
        from chaos.ide_language import Analysis,Info
        analysis=Analysis('outer.foo','main.chaos',{},'main.chaos',None)
        analysis.references=[(0,9,Info(detail='outer reference')),(6,9,Info(detail='member reference'))]
        hints=analysis.reference_hints()
        self.assertEqual(next(row['detail']for row in hints if row['from']==6),'member reference')
        self.assertEqual(next(row['detail']for row in hints if row['from']==0),'outer reference')

    def test_hints_are_nonexecuting_and_attribute_escaped(self):
        from chaos.language import Evaluator
        with mock.patch.object(Evaluator,'run',side_effect=AssertionError('must not execute')):
            data=analyze('channel_send(channel(),42)')
        self.assertTrue(data['referenceHints'])
        source='unique_hint_escape'
        forged={'tokens':[{'from':0,'to':len(source),'type':'function'}], 'symbols':[],
                'referenceHints':[{'from':0,'to':len(source),'detail':'bad " ><img src=x onerror=alert(1)>'}]}
        with mock.patch('chaos.code_presentation.analyze',return_value=forged):html=render_code(source,'chaos')
        self.assertNotIn('<img',html);self.assertEqual(CodeHTML(html).source,source)
        self.assertTrue(any(attrs.get('title')=='bad " ><img src=x onerror=alert(1)>'for _,attrs in CodeHTML(html).tags))

    def test_source_analysis_schema_keeps_reference_hints_optional(self):
        from chaos.openapi import specification
        schema=specification()['components']['schemas']['SourceAnalysis']
        self.assertIn('referenceHints',schema['properties']);self.assertNotIn('referenceHints',schema['required'])


class FormattedDocumentation(unittest.TestCase):
    def test_embedded_alternate_preserves_original_and_semantics(self):
        from chaos.source_format import format_source
        from chaos.program import parse_program
        source = '// 🙂 保留\nfn add(x:Int,y:Int)->Int{x+y};let note="<script>  text";add(20,22)'
        html = render_code(source, 'chaos', readable=True)
        self.assertEqual(CodeHTML(html).source, source)
        self.assertIn('data-format-mode="embedded"', html)
        template = html.split('<template data-code-formatted>', 1)[1].split('</template>', 1)[0]
        formatted = CodeHTML('<code>' + template + '</code>').source
        self.assertEqual(formatted, format_source(source))
        self.assertEqual(parse_program(source)['semanticHash'], parse_program(formatted)['semanticHash'])
        self.assertIn('chaos-token-parameter', template)
        self.assertIn('title="add(x: Int, y: Int) -&gt; Int"', template)
        self.assertNotIn('<script>', html)

    def test_incomplete_unknown_large_and_already_formatted_do_not_add_template(self):
        from chaos.source_format import format_source
        for source, language in [('fn incomplete(', 'chaos'), ('x' * 100001, 'chaos'), ('fn x(){42}', 'text'), (format_source('42'), 'chaos')]:
            html = render_code(source, language, readable=True)
            self.assertNotIn('<template', html)
            self.assertEqual(CodeHTML(html).source, source)

    def test_layout_does_not_evaluate_macros_functions_or_effects(self):
        from chaos.language import Evaluator
        source='macro identity(x){x};agent worker{prompt:"test"};identity!(worker(input))'
        with mock.patch.object(Evaluator, 'run', side_effect=AssertionError('must not execute')):
            html=render_code(source, 'chaos', readable=True)
        self.assertEqual(CodeHTML(html).source,source)
        self.assertIn('data-code-formatted',html)

    def test_only_documentation_opt_in_embeds_layout(self):
        source='fn answer(){42};answer()'
        self.assertNotIn('<template',render_code(source,'chaos'))
        html=markdown_parser().render('```chaos\n'+source+'\n```\n')
        self.assertIn('<template data-code-formatted>',html)
        self.assertEqual(CodeHTML(html).source,source+'\n')
