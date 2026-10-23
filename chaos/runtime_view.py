"""Bounded, read-only semantic projection of the existing program and journal.

This is an observer, never an interpreter. Labels and provenance are generated
from closed kinds and identities; user prose, literals and effect data are not
part of the projection. A missing event is not evidence of successful work.
"""
import hashlib
import json
import math
import time

from .store import NotFound

SCHEMA = 'chaos.runtime-view/1'
MAX_NODES = 400
MAX_EDGES = 1200
MAX_EVENTS = 500
MAX_STATIC = 160
MAX_TIMELINE = 80
_SYNTAX = frozenset(('def', 'fn', 'async_fn', 'gen_fn', 'async_gen_fn',
                     'agent', 'class', 'call', 'await', 'while', 'for',
                     'async_for', 'if', 'match', 'try', 'with', 'async_with'))
_RUN_STATES = frozenset(('queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted'))
_STATES = _RUN_STATES | frozenset(('created', 'started', 'closed', 'waiting',
    'open', 'observed', 'admitted', 'sent', 'stopSent', 'uncertain', 'skipped',
    'received', 'applied', 'committed', 'planning', 'execution', 'paused',
    'needs_reconciliation', 'not-observed'))


def _identity(value):
    return type(value) is str and 0 < len(value) <= 128 and not any(ord(c) < 32 for c in value)


def _state(value, default='not-observed'):
    return value if type(value) is str and value in _STATES else default


def _id(kind, *parts):
    raw = json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()
    return kind + ':' + hashlib.sha256(raw).hexdigest()[:24]


def _snapshot(store, project_id, run_id):
    current = store.project(project_id)
    if run_id is None:
        return current, None
    if not _identity(run_id):
        raise ValueError('Invalid runtime view run id')
    row = store.db.execute('SELECT id,project_id,version,status,mode,snapshot FROM runs WHERE id=?', (run_id,)).fetchone()
    if row is None or row['project_id'] != project_id:
        raise NotFound('Run does not belong to project')
    frozen = json.loads(row['snapshot'])
    if (type(frozen) is not dict or frozen.get('id') != project_id or
            frozen.get('version') != row['version']):
        raise ValueError('Invalid historical run snapshot origin')
    return frozen, dict(row)


def snapshot(store, project_id, run_id=None):
    """Return one SQLite read point, with explicit bounded-observation coverage.

    Existing outer transactions remain owned by their caller. No input/output,
    source text, resource handle, raw error, prompt or model reasoning is exposed.
    """
    with store.lock:
        own_transaction = not store.db.in_transaction
        if own_transaction:
            store.db.execute('BEGIN')
        try:
            return _project(store, project_id, run_id)
        finally:
            if own_transaction:
                store.db.rollback()


def _project(store, project_id, run_id):
    captured_at = time.time()
    project, selected = _snapshot(store, project_id, run_id)
    version = project['version']
    execution = None
    executable_bundle = None
    if selected is not None and selected['mode'] == 'executable':
        from .executable_runs import load_execution, descriptor
        executable_bundle = load_execution(project.get('execution'))
        execution = descriptor(project['execution'])
    cursor = store.db.execute('SELECT COALESCE(MAX(id),0) FROM events WHERE project_id=?', (project_id,)).fetchone()[0]
    sql = 'SELECT id,run_id,type,payload,created FROM events WHERE project_id=? AND id<=?'
    args = [project_id, cursor]
    if run_id is not None:
        sql += ' AND run_id=?'
        args.append(run_id)
    rows = store.db.execute(sql + ' ORDER BY id DESC LIMIT ?', args + [MAX_EVENTS + 1]).fetchall()
    earlier = len(rows) > MAX_EVENTS
    rows = list(reversed(rows[:MAX_EVENTS]))
    nodes, edges, timeline = {}, {}, []
    clipped = [earlier]
    ignored = [0]
    run_nodes, source_nodes = {}, {}
    root_id = (_id('executable', project_id, version, execution['imageHash'])
               if execution is not None else _id('project', project_id, version))

    def node(key, kind, status='not-observed', event=None, structural=False):
        if key not in nodes:
            if len(nodes) >= MAX_NODES:
                clipped[0] = True
                return None
            nodes[key] = {'id': key, 'kind': kind, 'label': kind, 'status': status,
                          'provenance': {'kind': 'structural' if structural else 'observed', 'eventId': event}}
        elif status != 'not-observed':
            nodes[key]['status'] = status
        if event is not None:
            nodes[key]['lastEventId'] = event
        return key

    def edge(source, target, relation, event=None, structural=False):
        if source not in nodes or target not in nodes:
            return
        key = _id('edge', source, target, relation)
        if key not in edges and len(edges) >= MAX_EDGES:
            clipped[0] = True
            return
        edges[key] = {'id': key, 'source': source, 'target': target, 'kind': relation,
                      'status': 'declared' if structural else 'observed',
                      'provenance': {'kind': 'structural' if structural else 'observed', 'eventId': event}}

    node(root_id, 'executable' if execution is not None else 'project', structural=True)
    if execution is not None:
        nodes[root_id]['execution'] = dict(execution)
        nodes[root_id]['authorityVersion'] = version
    graph = {} if execution is not None else project.get('graph', {})
    for item in graph.get('nodes', []):
        if type(item) is not dict or not _identity(item.get('id')):
            continue
        key = _id('workflow', project_id, version, item['id'])
        kind = item.get('kind') if item.get('kind') in ('input', 'output', 'function', 'agent', 'tool') else 'workflow'
        source_nodes[item['id']] = node(key, kind, structural=True)
        edge(root_id, key, 'contains', structural=True)
    for item in graph.get('nodes', []):
        if type(item) is dict and item.get('parentId') in source_nodes:
            edge(source_nodes[item['parentId']], source_nodes.get(item.get('id')), 'contains', structural=True)
    for item in graph.get('edges', []):
        if type(item) is dict:
            edge(source_nodes.get(item.get('source')), source_nodes.get(item.get('target')), 'dataflow', structural=True)

    # Read stored validated IR only. Never parse/evaluate source or inspect values.
    from .program import children, SHAPES
    pending = [] if execution is not None else [(project.get('program', {}).get('root'), root_id)]
    static_count = 0
    visited = 0
    while pending:
        item, parent = pending.pop()
        visited += 1
        if visited > 10000:
            clipped[0] = True
            break
        if type(item) is not dict or item.get('kind') not in SHAPES:
            continue
        if item['kind'] in _SYNTAX and _identity(item.get('id')):
            if static_count >= MAX_STATIC:
                clipped[0] = True
                continue
            key = _id('syntax', project_id, version, item['id'])
            source_nodes[item['id']] = node(key, item['kind'], structural=True)
            if key in nodes:
                name = item.get('name')
                if (item['kind'] in ('def', 'fn', 'async_fn', 'gen_fn', 'async_gen_fn', 'class', 'agent')
                        and type(name) is str and len(name) <= 80 and name.isidentifier()):
                    nodes[key]['label'] = item['kind'] + ' · ' + name
                span = item.get('span')
                if (type(span) is dict and _identity(span.get('file')) and
                        type(span.get('line')) is int and 1 <= span['line'] <= 1000000000 and
                        type(span.get('column')) is int and 1 <= span['column'] <= 1000000000):
                    nodes[key]['location'] = {'file': span['file'], 'line': span['line'],
                                              'column': span['column'], 'sourceVersion': version}
            edge(parent, key, 'contains', structural=True)
            parent = key
            static_count += 1
        pending.extend((child, parent) for _, child in reversed(children(item)))

    if executable_bundle is not None:
        # Walk only the decoded executable syntax. The authority project's graph
        # and source IR are never presented as this run's executed code.
        from .language import Node
        from .macros import _children
        image_hash = execution['imageHash']
        work = []
        for module, tree in reversed(list(executable_bundle.expanded_trees.items())):
            module_id = node(_id('image-module', image_hash, module), 'module', structural=True)
            if module_id:
                nodes[module_id]['label'] = 'module · ' + module
                nodes[module_id]['imageHash'] = image_hash
            edge(root_id, module_id, 'contains', structural=True)
            work.append((tree, module_id or root_id, module, ()))
        while work:
            item, parent, module, path = work.pop()
            visited += 1
            if visited > 10000:
                clipped[0] = True
                break
            if type(item) not in (Node, tuple) or not item:
                continue
            if item[0] in _SYNTAX:
                if static_count >= MAX_STATIC:
                    clipped[0] = True
                    continue
                key = node(_id('image-syntax', image_hash, module, path), item[0], structural=True)
                if key:
                    nodes[key]['imageHash'] = image_hash
                    name = item[4] if item[0] in ('fn', 'gen_fn', 'async_fn', 'async_gen_fn') else item[1] if item[0] in ('def', 'agent', 'class') else None
                    if type(name) is str and len(name) <= 80 and name.isidentifier():
                        nodes[key]['label'] = item[0] + ' · ' + name
                    span = getattr(item, 'span', {})
                    if _identity(span.get('file')) and type(span.get('line')) is int and type(span.get('column')) is int:
                        nodes[key]['location'] = {'file': span['file'], 'line': span['line'], 'column': span['column'], 'imageHash': image_hash}
                    edge(parent, key, 'contains', structural=True)
                    parent = key
                static_count += 1
            semantic_children = []
            _children(item, semantic_children.append)
            work.extend((child, parent, module, path + (index,)) for index, child in reversed(list(enumerate(semantic_children))))

    # Actual stored run state overrides a window's potentially stale lifecycle.
    run_ids = {row['run_id'] for row in rows if _identity(row['run_id'])}
    if selected is not None:
        run_ids.add(run_id)
    if run_ids:
        marks = ','.join('?' for _ in run_ids)
        records = store.db.execute('SELECT id,status,version,mode FROM runs WHERE project_id=? AND id IN (' + marks + ') ORDER BY id', [project_id, *sorted(run_ids)]).fetchall()
        for row in records:
            status = row['status'] if row['status'] in _RUN_STATES else 'not-observed'
            key = node(_id('run', project_id, row['id']), 'run', status)
            run_nodes[row['id']] = key
            if key:
                if row['mode'] == 'executable':
                    nodes[key]['authorityVersion'] = row['version']
                    nodes[key]['executionKind'] = 'executable'
                    if execution is not None: nodes[key]['imageHash'] = execution['imageHash']
                else: nodes[key]['sourceVersion'] = row['version']
                nodes[key]['provenance']['source'] = 'runs'
            edge(root_id, key, 'contains')

    # Process records are authoritative current state only in project scope.
    # Historical run scope exposes its recorded lineage, never today's mailbox.
    if run_id is None:
        for record in store.processes.list(project_id, limit=100)['items']:
            key = node(_id('process', project_id, record['key']), 'process', _state(record['status']))
            if key:
                nodes[key]['provenance']['source'] = 'processes'
                nodes[key]['revision'] = record['revision']
            edge(root_id, key, 'contains')
            edge(key, run_nodes.get(record.get('lastRunId')), 'runs')
    else:
        for record in store.db.execute('SELECT key FROM process_run_lineage WHERE project_id=? AND run_id=?', (project_id, run_id)):
            key = node(_id('process', project_id, record['key']), 'process')
            if key:
                nodes[key]['provenance']['source'] = 'process_run_lineage'
            edge(key, run_nodes.get(run_id), 'runs')

    def entity(kind, identity, event, status='not-observed', call=None):
        if not _identity(identity):
            return None
        key = node(_id(kind, project_id, event['run_id'], call, identity), kind, status, event['id'])
        edge(run_nodes.get(event['run_id'], root_id), key, 'contains', event['id'])
        return key

    for row in rows:
        try:
            payload = json.loads(row['payload'])
        except (ValueError, TypeError):
            ignored[0] += 1
            continue
        if type(payload) is not dict:
            ignored[0] += 1
            continue
        typ, eid = row['type'], row['id']
        call = payload.get('callId') if _identity(payload.get('callId')) else None
        target, state, known = None, None, True
        if typ in ('run.queued', 'run.running', 'run.completed', 'run.failed', 'run.cancelled', 'run.interrupted'):
            target = run_nodes.get(row['run_id'])
        elif typ in ('node.started', 'node.completed', 'node.failed', 'node.skipped'):
            state = typ.split('.')[1]
            if call is not None:
                target = entity('activity', payload.get('nodeId'), row, state, call)
            elif type(payload.get('version')) is int and payload['version'] == version:
                target = source_nodes.get(payload['nodeId']) if _identity(payload.get('nodeId')) else None
                if target:
                    node(target, nodes[target]['kind'], state, eid)
        elif typ in ('program.task.created', 'program.task.started', 'program.task.completed', 'program.task.cancelled', 'program.task.failed'):
            state = typ.rsplit('.', 1)[1]
            target = entity('task', payload.get('taskId'), row, state, call)
            parent = entity('task', payload.get('parentTaskId'), row, call=call)
            group = entity('group', payload.get('groupId'), row, call=call)
            edge(parent, target, 'spawns', eid)
            edge(group, target, 'member', eid)
        elif typ in ('program.group.created', 'program.group.entered', 'program.group.member', 'program.group.closed'):
            state = 'closed' if typ == 'program.group.closed' else 'observed'
            target = entity('group', payload.get('groupId'), row, state, call)
            parent = entity('group', payload.get('parentGroupId'), row, call=call)
            edge(parent, target, 'contains', eid)
            members = payload.get('memberTaskIds')
            for member in members[:100] if type(members) is list else []:
                edge(target, entity('task', member, row, call=call), 'member', eid)
            if payload.get('membersTruncated') is True:
                clipped[0] = True
        elif typ in ('program.execution.started', 'program.execution.completed', 'program.execution.failed'):
            state = typ.rsplit('.', 1)[1]
            target = entity('program-call', call, row, state)
            parent = entity('program-call', payload.get('parentCallId'), row)
            edge(parent, target, 'calls', eid)
        elif typ in ('bot.scope.opened', 'bot.scope.closed', 'bot.decision.created', 'bot.action.admitted', 'bot.action.completed', 'bot.context.applied', 'bot.context.committed', 'bot.result.committed'):
            scope = entity('bot', payload.get('scopeId'), row, 'closed' if typ == 'bot.scope.closed' else 'observed', call)
            decision = entity('decision', payload.get('decisionId'), row, call=call)
            action = entity('action', payload.get('actionId'), row, call=call)
            state = _state(payload.get('status'), 'admitted' if typ == 'bot.action.admitted' else None)
            if action and state:
                node(action, 'action', state, eid)
            edge(scope, decision, 'decides', eid)
            edge(decision, action, 'admits', eid)
            target = action or decision or scope
        elif typ in ('team.role.started', 'team.role.completed', 'team.role.failed'):
            identity = payload.get('invocationId') or payload.get('taskId')
            state = typ.rsplit('.', 1)[1]
            target = entity('team-role', identity, row, state, call)
        elif typ in ('computer.lifecycle', 'computer.action', 'computer.observation', 'computer.completed', 'computer.failed', 'computer.uncertain'):
            computer = entity('computer', payload.get('computerId'), row, call=call)
            state = _state(payload.get('status'), _state(typ.split('.')[1]))
            target = entity('computer-record', payload.get('recordId'), row, state, call)
            edge(computer, target, 'records', eid)
        elif typ in ('robot.opened', 'robot.observed', 'robot.command.admitted', 'robot.command.sent', 'robot.stopped', 'robot.uncertain', 'robot.closed'):
            state = {'robot.opened': 'open', 'robot.uncertain': 'uncertain', 'robot.closed': 'closed'}.get(typ)
            target = entity('robot', payload.get('id') if typ == 'robot.opened' else payload.get('robotId'), row, state or 'not-observed', call)
            if typ in ('robot.command.admitted', 'robot.command.sent', 'robot.stopped'):
                command = entity('robot-command', payload.get('commandId') or payload.get('id'), row, _state(payload.get('status')), call)
                edge(target, command, 'sends', eid)
                target = command
        elif typ in ('agent.observing', 'agent.thinking', 'agent.completed', 'agent.failed', 'tool.started', 'tool.completed', 'tool.failed', 'bot.progress', 'guidance.queued', 'guidance.received'):
            # No invocation identity exists here; an event is a fact, not a
            # guessed long-lived invocation grouped by human-readable name.
            state = typ.rsplit('.', 1)[1]
            target = entity('activity', str(eid), row, state if state in _STATES else 'observed', call)
        elif typ in ('program.lifecycle.truncated', 'program.trace_truncated'):
            clipped[0] = True
        else:
            known = False
            ignored[0] += 1
        if known:
            created = row['created']
            timeline.append({'eventId': eid, 'type': typ, 'nodeId': target,
                             'time': created if type(created) in (int, float) and math.isfinite(created) else None})

    timeline_clipped = len(timeline) > MAX_TIMELINE
    return {'schema': SCHEMA, 'projectId': project_id, 'runId': run_id,
            'sourceVersion': None if execution is not None else version, 'eventCursor': cursor, 'snapshotAt': captured_at,
            **({'execution': execution, 'authorityVersion': version} if execution is not None else {}),
            'windowBound': {'maxEvents': MAX_EVENTS, 'eventCount': len(rows),
                            'firstEventId': rows[0]['id'] if rows else None,
                            'lastEventId': rows[-1]['id'] if rows else None,
                            'hasEarlierEvents': earlier, 'maxNodes': MAX_NODES,
                            'maxEdges': MAX_EDGES,
                            'maxTimeline': MAX_TIMELINE, 'timelineTruncated': timeline_clipped,
                            'coverage': 'bounded-observations'},
            'truncated': clipped[0] or timeline_clipped, 'nodes': list(nodes.values()),
            'edges': list(edges.values()),
            'metrics': {'nodes': len(nodes), 'edges': len(edges),
                        'observedEvents': len(rows), 'ignoredEvents': ignored[0]},
            'timeline': timeline[-MAX_TIMELINE:]}
