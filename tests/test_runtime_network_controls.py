"""Execute the proposed native network frontend against isolated fake HTTP/React hooks."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which('node')


@unittest.skipUnless(NODE, 'Node.js unavailable')
class RuntimeNetworkControlsTests(unittest.TestCase):
    def check_case(self, name):
        result = subprocess.run([NODE, str(ROOT / 'tests/runtime_network_controls.cjs'), name],
                                cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'passed')

    def test_executable_sync_events_do_not_borrow_authority_graph_identity(self): self.check_case('executable_sync_events_do_not_borrow_authority_graph_identity')
    def test_executable_identity_displays_and_preserves_image_locations(self): self.check_case('executable_identity_displays_and_preserves_image_locations')
    def test_executable_wrong_authority_descriptor_or_changed_image_preserves_last(self): self.check_case('executable_wrong_authority_descriptor_or_changed_image_preserves_last')

    def test_obsolete_event_sources_cannot_relabel_or_mutate_current_project(self): self.check_case('obsolete_event_sources_cannot_relabel_or_mutate_current_project')
    def test_static_dense_bounds(self): self.check_case('static_dense_bounds')
    def test_static_selection_and_literal_text(self): self.check_case('static_selection_and_literal_text')
    def test_unbound_and_partial_never_fetch(self): self.check_case('unbound_and_partial_never_fetch')
    def test_shared_current_run_and_monotonic_cursor(self): self.check_case('shared_current_run_and_monotonic_cursor')
    def test_project_scope_has_no_run_query(self): self.check_case('project_scope_has_no_run_query')
    def test_origin_switch_aborts_and_drops_late_reply(self): self.check_case('origin_switch_aborts_and_drops_late_reply')
    def test_disconnect_and_wrong_source_preserve_last_snapshot(self): self.check_case('disconnect_and_wrong_source_preserve_last_snapshot')


if __name__ == '__main__': unittest.main()
