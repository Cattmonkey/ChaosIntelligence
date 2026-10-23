import unittest
from chaos import Evaluator, Framework, LanguageError, ResourceLimit
from chaos.program_values import builtins as program_builtins
from chaos.program import parse_program, format_program


class CompilerLanguageTests(unittest.TestCase):
    def test_owned_program_compile_is_native_and_non_executing(self):
        source = 'program_compile(program_from({kind:"program",entry:"main.chaos",files:{"main.chaos":"unknown()"},languageVersions:{"main.chaos":2}}))'
        result = Evaluator().run(source)
        self.assertEqual(result['schema'],'chaos.compilation/1')
        self.assertEqual(result['summary']['cacheBypasses'],1)
        self.assertIn('unknown',result['summary']['effects'])

    def test_self_inspection_and_ir_roundtrip(self):
        source = 'let report=program_compile(current_program());[report.target,report.entry,report.summary.moduleCount]'
        expected = ['portable-ast','main.chaos',1]
        self.assertEqual(Evaluator().run(source),expected)
        self.assertEqual(Evaluator().run(format_program(parse_program(source))),expected)

    def test_owned_value_cannot_cross_evaluators(self):
        owner = Evaluator(); other = Evaluator()
        value = program_builtins(owner)['program_from']({'kind':'program','entry':'main.chaos','files':{'main.chaos':'42'},'languageVersions':{'main.chaos':2}})
        with self.assertRaises(LanguageError) as error: program_builtins(other)['program_compile'](value)
        self.assertEqual(error.exception.code,'ownership_error')

    def test_compilation_charged_to_existing_budget(self):
        ev = Evaluator(step_limit=30000)
        values = program_builtins(ev)
        value = values['program_from']({'kind':'program','entry':'main.chaos','files':{'main.chaos':'42'},'languageVersions':{'main.chaos':2}})
        ev.step_limit = ev.steps
        with self.assertRaises(ResourceLimit): values['program_compile'](value)

    def test_native_signature_and_alias_record_hint(self):
        signature = Framework.analyze('program_compile(')['signatureHelp']
        self.assertIn('value: Program',signature['label'])
        source = 'let inspect=program_compile;let r=inspect(current_program());r.'
        report = Framework.analyze(source,position=len(source))
        names = {item.get('label',item.get('name')) for item in report['completions']}
        self.assertIn('artifactHash',names)


if __name__ == '__main__': unittest.main()
