"""Original real held Futures and all65 capture paths through loaded artifacts."""
import unittest
from unittest.mock import patch
from chaos.compilation_artifact import compile_artifact, load_artifact
from chaos.language import Evaluator
import _pattern_flow218 as original


class CompilationArtifactCapture219(unittest.TestCase):
    def pending(self, padding, **kwargs):
        expected, _, _ = original.pending(self, padding, 'raw', **kwargs)
        run = Evaluator.run; loaded = []
        def from_artifact(evaluator, source, *args, **options):
            if type(source) is str:
                bundle = load_artifact(compile_artifact(source)); loaded.append(bundle)
                return run(evaluator, bundle, *args, **options)
            return run(evaluator, source, *args, **options)
        with patch.object(Evaluator, 'run', from_artifact):
            actual, states, functions = original.pending(self, padding, 'portable', **kwargs)
        self.assertTrue(loaded); self.assertEqual(actual, expected)
        self.assertTrue(states); self.assertTrue(functions)
        self.assertTrue(all(f.compiled_plan.flow._artifact_decoded for f in functions))
        self.assertTrue(all(s.program._artifact_decoded for s in states))

    def test_all65_assignment_precommit_capture_and_original_cleanup_match(self):
        for padding in range(65):
            with self.subTest(padding=padding): self.pending(padding, mode='assign_pattern')

    def test_all65_borrowed_await_capture_keeps_real_task_and_cursor_ownership(self):
        for padding in range(65):
            with self.subTest(padding=padding): self.pending(padding, awaited=True)

    def test_cancel_loaded_pattern_never_cancels_borrowed_task_or_replays_effect(self):
        self.pending(64, awaited=True, cancel=True)


if __name__ == '__main__': unittest.main()
