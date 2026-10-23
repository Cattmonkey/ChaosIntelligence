"""CancelShield keeps one AST execution model across source, IR, SDK, HTTP and CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from chaos import Client, Framework, Settings
from chaos.language import Evaluator, LanguageError, serializable
from chaos.program import parse_program, format_program
from chaos.server import make_server

ROOT = Path(__file__).resolve().parent.parent
SOURCE = '''macro call(action,value){quote{unquote(action)(unquote(value))}}
async fn work(value:Int)->List {
 let outer:CancelShield=cancel_shield();
 let answer=with outer as protection {
  async with cancel_shield() as inner {
   await sleep(0);
   [call!(fn(x){x*2},value),cancel_shield_info(protection).depth,
    cancel_shield_info(inner).depth,cancel_shield_info(inner).state]
  }
 };
 [answer,cancel_shield_info(outer).state,cancel_shield_info(outer).depth]
}
await work(input)'''


class CancelShieldSurfaces(unittest.TestCase):
    def test_source_ir_macro_format_and_module_roundtrip(self):
        expected = [[42, 1, 2, 'active'], 'closed', 0]
        self.assertEqual(Framework.evaluate(SOURCE, {'input': 21}), expected)
        self.assertEqual(Framework.evaluate(format_program(parse_program(SOURCE)), {'input': 21}), expected)
        formatted = Framework.format_source(SOURCE, line_width=40, indent_size=4)
        self.assertEqual(Framework.format_source(formatted, line_width=40, indent_size=4), formatted)
        self.assertEqual(Framework.evaluate(formatted, {'input': 21}), expected)
        files = {'protected.chaos': 'async fn work(x){with cancel_shield() as shield{await sleep(0);x+1}};export{work}'}
        self.assertEqual(Framework.evaluate('import "protected.chaos" as P;await P.work(41)', files=files), 42)

    def test_http_structural_edit_and_fixed_run_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, Framework(settings=Settings(':memory:')) as app:
            server = make_server('127.0.0.1', 0, framework=app, state_dir=Path(directory)/'control', update_home=Path(directory)/'updates')
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                client = Client('http://127.0.0.1:' + str(server.server_port))
                project = client.create_project('cancel-shield-surfaces', source=SOURCE, capabilities=[])
                old = client.start(project['id'], 1, 21)
                self.assertEqual(old.result(5), [[42, 1, 2, 'active'], 'closed', 0])
                ir = client.program(project['id'])['program']
                def targets(value):
                    if isinstance(value, dict):
                        if value.get('kind') == 'literal' and value.get('value') == 2: yield value['id']
                        for child in value.values(): yield from targets(child)
                    elif isinstance(value, list):
                        for child in value: yield from targets(child)
                ids = list(targets(ir['root'])); self.assertEqual(len(ids), 1)
                saved = client.edit_program(project['id'], 1, operations=[{'op': 'set', 'id': ids[0], 'field': 'value', 'value': 3}])
                self.assertEqual(client.start(project['id'], 2, 21).result(5), [[63, 1, 2, 'active'], 'closed', 0])
                self.assertEqual(old.get()['snapshot']['source'], SOURCE)
                self.assertEqual(client.get_project(project['id'])['source'], saved['source'])
            finally:
                server.shutdown(); server.server_close(); thread.join(3)

    def test_cli_check_format_ir_run_and_repl(self):
        source = 'async fn work(){with cancel_shield() as shield{await sleep(0);42}};await work()'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'main.chaos'; path.write_text(source)
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            for command in ('check', 'fmt', 'ir', 'run'):
                result = subprocess.run([sys.executable, '-m', 'chaos', command, str(path)], cwd=directory, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                if command == 'run': self.assertEqual(json.loads(result.stdout), 42)
                if command == 'fmt': self.assertEqual(result.stdout, Framework.format_source(source))
            result = subprocess.run([sys.executable, '-m', 'chaos', 'repl'], cwd=directory, env=env, input=source+'\n.quit\n', capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.strip()), 42)

    def test_readonly_handle_description_at_host_boundary(self):
        seen = []
        evaluator = Evaluator(tool_handler=lambda name, value: seen.append(value))
        self.assertEqual(evaluator.run('let shield=cancel_shield();tool("inspect",{shield:shield});type_of(shield)'), 'CancelShield')
        self.assertEqual(seen[0]['shield']['$handle'], 'CancelShield')
        self.assertIsInstance(seen[0]['shield'], dict)
        for expression in ('shield.name=42', 'setattr(shield,"name",42)', 'delattr(shield,"name")'):
            with self.subTest(expression=expression):
                with self.assertRaises(LanguageError) as caught:
                    Framework.evaluate('let shield=cancel_shield();'+expression)
                self.assertEqual(caught.exception.code, 'attribute_error')
        from chaos.cancellation import create
        owner = Evaluator(); other = Evaluator(); owner.suspension.begin(); other.suspension.begin()
        try:
            shield = create(owner)
            self.assertEqual(serializable(shield)['$handle'], 'CancelShield')
            with self.assertRaises(LanguageError) as caught: other.host_value({'shield': shield})
            self.assertEqual(caught.exception.code, 'ownership_error')
        finally: owner.suspension.finish(); other.suspension.finish()


if __name__ == '__main__': unittest.main()
