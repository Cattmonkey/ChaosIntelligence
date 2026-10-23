"""Real Store journals and frozen source, without devices, models or an executor."""
import json
import tempfile
import sqlite3
from pathlib import Path
import unittest
from unittest.mock import patch

from chaos.runtime_view import snapshot, MAX_NODES, MAX_EDGES, MAX_EVENTS
from chaos.store import Store, NotFound


class RuntimeViewTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.project = self.store.create_project('view', 'View', {'nodes': [], 'edges': []},
            'fn compute(x){x+1};compute(41)', capabilities=[])

    def run_record(self, key='run-a', project=None):
        self.store.create_run(key, project or self.project, 'source', {'hiddenInput': 'INPUT_SECRET'})
        return key

    def event(self, kind, payload, run='run-a'):
        return self.store.event('view', kind, payload, run)

    def kinds(self, value, kind):
        return [node for node in value['nodes'] if node['kind'] == kind]

    def test_static_program_has_real_contains_but_no_guessed_call_or_success(self):
        view = snapshot(self.store, 'view')
        self.assertEqual(view['schema'], 'chaos.runtime-view/1')
        self.assertEqual(view['sourceVersion'], 1)
        self.assertIsNone(view['runId'])
        self.assertIsInstance(view['snapshotAt'], (int, float))
        self.assertTrue(self.kinds(view, 'def'))
        self.assertTrue(self.kinds(view, 'call'))
        self.assertTrue(all(n['status'] == 'not-observed' for n in view['nodes']))
        self.assertTrue(all(e['kind'] == 'contains' for e in view['edges']))
        self.assertTrue(all(e['provenance']['kind'] == 'structural' for e in view['edges']))
        self.assertTrue(any(n['label'] == 'def · compute' for n in self.kinds(view, 'def')))
        locations = [n['location'] for n in view['nodes'] if 'location' in n]
        self.assertTrue(locations)
        self.assertTrue(all(set(p) == {'file', 'line', 'column', 'sourceVersion'} and
                            p['file'] == 'main.chaos' and p['line'] >= 1 and
                            p['column'] >= 1 and p['sourceVersion'] == 1 for p in locations))
        self.assertNotIn('literal', [n['kind'] for n in view['nodes']])

    def test_historical_snapshot_is_frozen_after_current_source_changes(self):
        run = self.run_record()
        before = snapshot(self.store, 'view', run)
        self.store.edit_program('view', 1, source='class New {} ; 0')
        after = snapshot(self.store, 'view', run)
        self.assertEqual(before['nodes'], after['nodes'])
        self.assertEqual(before['edges'], after['edges'])
        self.assertEqual(after['sourceVersion'], 1)
        self.assertTrue(self.kinds(snapshot(self.store, 'view'), 'class'))
        self.assertFalse(self.kinds(after, 'class'))

    def test_run_and_frozen_snapshot_origin_cannot_cross_projects(self):
        other = self.store.create_project('other', 'Other', {'nodes': [], 'edges': []}, '0', capabilities=[])
        self.run_record('foreign', other)
        with self.assertRaises(NotFound):
            snapshot(self.store, 'view', 'foreign')
        self.run_record()
        frozen = dict(self.project, id='other')
        self.store.db.execute('UPDATE runs SET snapshot=? WHERE id=?', (json.dumps(frozen), 'run-a'))
        with self.assertRaisesRegex(ValueError, 'origin'):
            snapshot(self.store, 'view', 'run-a')

    def test_duplicate_task_ids_are_scoped_by_run_and_program_call(self):
        self.run_record()
        self.run_record('run-b')
        for run, call in [('run-a', 'one'), ('run-a', 'two'), ('run-b', 'one')]:
            self.event('program.task.created', {'taskId': 't1', 'callId': call}, run)
        view = snapshot(self.store, 'view')
        self.assertEqual(len(self.kinds(view, 'task')), 3)
        self.assertEqual(len({n['id'] for n in self.kinds(view, 'task')}), 3)
        outer = self.project['program']['root']['statements'][0]['id']
        self.event('node.completed', {'nodeId': outer, 'version': 1, 'callId': 'child'})
        nested = snapshot(self.store, 'view', 'run-a')
        self.assertTrue(all(n['status'] == 'not-observed' for n in self.kinds(nested, 'def')))
        self.assertEqual(self.kinds(nested, 'activity')[0]['status'], 'completed')

    def test_tasks_groups_and_program_calls_use_explicit_relations(self):
        self.run_record()
        self.event('program.execution.started', {'callId': 'child', 'parentCallId': 'parent'})
        self.event('program.group.created', {'groupId': 'g1', 'parentGroupId': 'g0', 'memberTaskIds': ['t1']})
        self.event('program.task.started', {'taskId': 't1', 'parentTaskId': 't0', 'groupId': 'g1'})
        self.event('program.task.completed', {'taskId': 't1', 'groupId': 'g1'})
        view = snapshot(self.store, 'view', 'run-a')
        self.assertTrue({'calls', 'member', 'spawns'} <= {e['kind'] for e in view['edges']})
        self.assertEqual(sorted(n['status'] for n in self.kinds(view, 'task')), ['completed', 'not-observed'])
        self.assertTrue(all(n['provenance']['eventId'] for n in self.kinds(view, 'task')))
        self.assertFalse(any('duration' in n for n in view['nodes']))

    def test_bot_decisions_actions_and_guidance_keep_actual_fact_statuses(self):
        self.run_record()
        self.event('bot.scope.opened', {'scopeId': 'scope'})
        self.event('bot.decision.created', {'scopeId': 'scope', 'decisionId': 'decision', 'phase': 'planning'})
        self.event('bot.action.admitted', {'scopeId': 'scope', 'decisionId': 'decision', 'actionId': 'action'})
        done = self.event('bot.action.completed', {'scopeId': 'scope', 'decisionId': 'decision', 'actionId': 'action', 'status': 'uncertain', 'error': 'ERROR_SECRET'})
        self.event('guidance.received', {'messageId': 'guide', 'input': 'GUIDANCE_SECRET'})
        view = snapshot(self.store, 'view', 'run-a')
        action = self.kinds(view, 'action')[0]
        self.assertEqual(action['status'], 'uncertain')
        self.assertEqual(action['lastEventId'], done)
        self.assertTrue({'decides', 'admits'} <= {e['kind'] for e in view['edges']})
        self.assertNotIn('SECRET', json.dumps(view))

    def test_computer_records_and_robot_sent_never_claim_target_success(self):
        self.run_record()
        self.event('computer.action', {'computerId': 'computer', 'recordId': 'record', 'status': 'started'})
        self.event('computer.completed', {'computerId': 'computer', 'recordId': 'record'})
        self.event('robot.opened', {'id': 'robot', 'status': 'open'})
        self.event('robot.command.sent', {'robotId': 'robot', 'commandId': 'cmd', 'status': 'sent', 'targetVerified': False, 'channels': {'secret': 1}})
        view = snapshot(self.store, 'view', 'run-a')
        self.assertEqual(self.kinds(view, 'computer-record')[0]['status'], 'completed')
        self.assertEqual(self.kinds(view, 'computer')[0]['status'], 'not-observed')
        self.assertEqual(self.kinds(view, 'robot-command')[0]['status'], 'sent')
        self.assertNotIn('channels', json.dumps(view))

    def test_team_invocations_are_not_merged_by_role_name(self):
        self.run_record()
        for task in ('a', 'b'):
            self.event('team.role.started', {'taskId': task, 'roleId': 'same', 'name': 'TEAM_SECRET'})
        self.event('team.role.completed', {'taskId': 'a', 'roleId': 'same'})
        roles = self.kinds(snapshot(self.store, 'view', 'run-a'), 'team-role')
        self.assertEqual(len(roles), 2)
        self.assertEqual(sorted(n['status'] for n in roles), ['completed', 'started'])
        self.assertNotIn('TEAM_SECRET', json.dumps(roles))

    def test_sensitive_source_prose_and_all_payloads_are_closed_projected(self):
        self.store.edit_program('view', 1, source='agent a {prompt:"PROMPT_SECRET",provider:"local"};"LITERAL_SECRET"')
        self.project = self.store.project('view')
        self.run_record()
        self.event('tool.started', {'tool': 'TOOL_SECRET', 'arguments': {'authorization': 'KEY_SECRET'}, 'output': 'OUTPUT_SECRET'})
        self.event('not.a.registered.kind', {'taskId': 'invented', 'prompt': 'EXTRA_SECRET'})
        text = json.dumps(snapshot(self.store, 'view', 'run-a'))
        for secret in ('PROMPT_SECRET', 'LITERAL_SECRET', 'TOOL_SECRET', 'KEY_SECRET', 'OUTPUT_SECRET', 'INPUT_SECRET', 'EXTRA_SECRET'):
            self.assertNotIn(secret, text)
        self.assertNotIn('invented', text)

    def test_latest_window_and_lifecycle_truncation_are_explicit(self):
        self.run_record()
        for index in range(MAX_EVENTS + 10):
            self.event('guidance.received', {'messageId': 'g' + str(index)})
        view = snapshot(self.store, 'view', 'run-a')
        self.assertEqual(view['windowBound']['eventCount'], MAX_EVENTS)
        self.assertTrue(view['windowBound']['hasEarlierEvents'])
        self.assertTrue(view['truncated'])
        self.assertLessEqual(len(view['nodes']), MAX_NODES)
        self.assertLessEqual(len(view['edges']), MAX_EDGES)
        self.assertLessEqual(len(view['timeline']), 80)
        self.event('program.lifecycle.truncated', {'limit': 1000})
        self.assertTrue(snapshot(self.store, 'view', 'run-a')['truncated'])

    def test_authoritative_run_status_survives_event_window_and_stable_refresh(self):
        self.run_record()
        self.store.transition_run('run-a', 'completed', {'output': 'SECRET'}, output={'secret': 'SECRET'})
        first = snapshot(self.store, 'view', 'run-a')
        self.event('tool.started', {'tool': 'x'})
        second = snapshot(self.store, 'view', 'run-a')
        self.assertEqual(self.kinds(second, 'run')[0]['status'], 'completed')
        self.assertEqual(self.kinds(first, 'run')[0]['id'], self.kinds(second, 'run')[0]['id'])
        self.assertGreater(second['eventCursor'], first['eventCursor'])

    def test_event_cursor_is_project_scoped_and_unrelated_run_is_filtered(self):
        self.run_record()
        self.run_record('run-b')
        self.event('program.task.created', {'taskId': 'other'}, 'run-b')
        last = self.event('program.task.created', {'taskId': 'selected'})
        view = snapshot(self.store, 'view', 'run-a')
        self.assertEqual(view['eventCursor'], last)
        self.assertEqual(len(self.kinds(view, 'task')), 1)

    def test_existing_transaction_and_database_are_not_modified(self):
        self.run_record()
        before = self.store.db.total_changes
        self.store.db.execute('BEGIN')
        try:
            snapshot(self.store, 'view', 'run-a')
            self.assertTrue(self.store.db.in_transaction)
            self.assertEqual(self.store.db.total_changes, before)
        finally:
            self.store.db.rollback()
        snapshot(self.store, 'view')
        self.assertFalse(self.store.db.in_transaction)

    def test_timeline_limit_is_explicit_even_before_node_and_event_limits(self):
        self.run_record()
        for index in range(90):
            self.event('guidance.received', {'messageId': 'guide-' + str(index)})
        view = snapshot(self.store, 'view', 'run-a')
        self.assertEqual(len(view['timeline']), 80)
        self.assertEqual(view['windowBound']['maxTimeline'], 80)
        self.assertTrue(view['windowBound']['timelineTruncated'])
        self.assertFalse(view['windowBound']['hasEarlierEvents'])
        self.assertLess(len(view['nodes']), MAX_NODES)
        self.assertTrue(view['truncated'])

    def test_process_state_and_lineage_have_no_mailbox_or_definition_payload(self):
        self.run_record()
        record = {'key': 'worker', 'projectId': 'view', 'status': 'waiting',
                  'revision': 3, 'lastRunId': 'run-a', 'pending': None,
                  'state': {'prompt': 'PROCESS_SECRET'}, 'definition': {'secret': 'DEF_SECRET'}}
        self.store.db.execute('INSERT INTO processes VALUES(?,?,?,?,?)',
                              ('view', 'worker', json.dumps(record), 'waiting', None))
        self.store.db.execute('INSERT INTO process_run_lineage VALUES(?,?,?)', ('run-a', 'view', 'worker'))
        current = snapshot(self.store, 'view')
        historical = snapshot(self.store, 'view', 'run-a')
        self.assertEqual(self.kinds(current, 'process')[0]['status'], 'waiting')
        self.assertEqual(self.kinds(historical, 'process')[0]['status'], 'not-observed')
        self.assertIn('runs', [e['kind'] for e in historical['edges']])
        self.assertNotIn('SECRET', json.dumps([current, historical]))

    def test_snapshot_cursor_and_source_share_one_sqlite_read_point(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.sqlite3'
            store = Store(path)
            writer = sqlite3.connect(path, isolation_level=None)
            try:
                store.create_project('coherent', 'Coherent', {'nodes': [], 'edges': []}, '0', capabilities=[])
                old_cursor = store.db.execute('SELECT MAX(id) FROM events WHERE project_id=?', ('coherent',)).fetchone()[0]
                original = store.project
                inserted = [False]
                def concurrent_write(project_id):
                    value = original(project_id)
                    if not inserted[0]:
                        inserted[0] = True
                        writer.execute('INSERT INTO events(project_id,run_id,type,payload,created) VALUES(?,?,?,?,?)',
                                       ('coherent', None, 'tool.started', '{}', 1.0))
                    return value
                with patch.object(store, 'project', side_effect=concurrent_write):
                    view = snapshot(store, 'coherent')
                self.assertEqual(view['eventCursor'], old_cursor)
                self.assertGreater(snapshot(store, 'coherent')['eventCursor'], old_cursor)
            finally:
                writer.close()
                store.close()

    def test_malformed_optional_event_fields_cannot_inject_nodes_or_raise(self):
        self.run_record()
        self.event('computer.action', {'computerId': [], 'recordId': {}, 'status': {}})
        self.event('program.group.member', {'groupId': 'g', 'memberTaskIds': {'bad': 'shape'}})
        self.event('node.completed', {'version': 1, 'nodeId': []})
        self.event('bot.action.completed', {'scopeId': 's', 'actionId': 'a', 'status': []})
        self.event('team.role.started', {'taskId': 'constructor', 'name': '<script>bad</script>'})
        view = snapshot(self.store, 'view', 'run-a')
        self.assertNotIn('<script>', json.dumps(view))
        self.assertTrue(all(e['source'] in {n['id'] for n in view['nodes']} and e['target'] in {n['id'] for n in view['nodes']} for e in view['edges']))


if __name__ == '__main__':
    unittest.main()
