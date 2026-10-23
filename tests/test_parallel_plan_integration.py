"""Dependency-driven plans compose with real agent and durable host effects."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from chaos import Client, Final, Framework, ProviderSpec, Registry, ToolSpec
from chaos.ide_language import analyze, format_source
from chaos.program import parse_program
from chaos.server import make_server
from chaos.settings import Settings
from test_task_join_integration import CooperatingPair, process_definition, program_definition


class ParallelPlanIntegrationTests(unittest.TestCase):
    def app(self, registry=None):
        app = Framework(':memory:', settings=Settings(':memory:'), registry=registry)
        self.addCleanup(app.close)
        return app

    def events(self, app, project, run):
        rows, after = [], 0
        while True:
            page = app.events(project, run_id=run, after=after, limit=300)
            rows.extend(page['items'])
            if page['nextCursor'] is None:
                return rows
            after = page['nextCursor']

    def test_real_agents_release_successor_before_unrelated_branch_completes(self):
        registry = Registry()
        slow_started, successor_started = threading.Event(), threading.Event()
        calls, lock, active = [], threading.Lock(), [0, 0]

        def provider(request, context):
            value = request.input
            with lock:
                active[0] += 1
                active[1] = max(active)
                calls.append(value)
            try:
                if value['id'] == 'slow':
                    slow_started.set()
                    self.assertTrue(successor_started.wait(3), 'successor was blocked by unrelated branch')
                    return Final(20)
                if value['id'] == 'fast':
                    self.assertTrue(slow_started.wait(3))
                    return Final(21)
                self.assertEqual(value['ctx'], {'input': 40, 'results': {'fast': 21}})
                successor_started.set()
                return Final(22)
            finally:
                with lock:
                    active[0] -= 1

        registry.register_provider(ProviderSpec('fixture.plan', provider))
        source = '''agent worker{provider:"fixture.plan",tools:[]};
async fn slow(c){worker({id:"slow",ctx:c})}
async fn fast(c){worker({id:"fast",ctx:c})}
async fn next(c){worker({id:"next",ctx:c})}
fn join(c){c.results.slow+c.results.next}
let p=plan([{id:"slow",call:"slow"},{id:"fast",call:"fast"},
{id:"next",call:"next",needs:["fast"]},{id:"answer",call:"join",needs:["slow","next"]}],max_concurrency=2);
await p(40,{slow:slow,fast:fast,next:next,join:join})'''
        app = self.app(registry)
        app.create_project('agents', source=source, capabilities=[])
        run = app.start('agents', 1, None, mode='source')
        self.assertEqual(run.result(8), {'slow': 20, 'fast': 21, 'next': 22, 'answer': 42})
        self.assertEqual(active, [0, 2])
        self.assertCountEqual([v['id'] for v in calls], ['slow', 'fast', 'next'])
        events = self.events(app, 'agents', run.id)
        steps = [r['payload'] for r in events if r['type'] == 'mode.plan.step.started']
        self.assertEqual(len(steps), 4)
        self.assertEqual(len({r['executionId'] for r in steps}), 1)
        self.assertEqual(len({r['stepTaskId'] for r in steps}), 4)
        next_event = next(i for i, e in enumerate(events) if e['type'] == 'mode.plan.step.started' and e['payload']['stepId'] == 'next')
        slow_end = next(i for i, e in enumerate(events) if e['type'] == 'mode.plan.step.completed' and e['payload']['stepId'] == 'slow')
        self.assertLess(next_event, slow_end)

    def test_agent_failure_drains_peer_before_catch_and_never_admits_dependent(self):
        pair, registry = CooperatingPair(), Registry()
        registry.register_provider(ProviderSpec('fixture.plan', lambda request, context: Final(pair.work(request.input, context))))
        registry.register_tool(ToolSpec('fixture.finished', pair.snapshot))
        source = '''agent worker{provider:"fixture.plan",tools:[]};
fn peer(c){worker("peer")}fn fail(c){worker("fail")}fn forbidden(c){throw "must not run"}
let p=plan([{id:"peer",call:"peer"},{id:"bad",call:"fail"},{id:"later",call:"forbidden",needs:["peer"]}],max_concurrency=2);
let error=try{await p(null,{peer:peer,fail:fail,forbidden:forbidden})}catch e{e.code};
[error,tool("fixture.finished",null)]'''
        app = self.app(registry)
        app.create_project('failed', source=source, capabilities=['fixture.finished'])
        run = app.start('failed', 1, None, mode='source')
        result = run.result(8)
        self.assertIsInstance(result[0], str)
        self.assertNotEqual(result[0], 'resource_limit')
        self.assertEqual(result[1], True)
        self.assertTrue(pair.cancelled)
        starts = [e['payload']['stepId'] for e in self.events(app, 'failed', run.id) if e['type'] == 'mode.plan.step.started']
        self.assertCountEqual(starts, ['peer', 'bad'])

    def test_plan_preserves_agent_capability_checks(self):
        registry, providers, effects = Registry(), [], []
        registry.register_provider(ProviderSpec('fixture.plan', lambda request, context: providers.append(request) or Final(42)))
        registry.register_tool(ToolSpec('fixture.denied', lambda value, context: effects.append(value)))
        source = '''agent worker{provider:"fixture.plan",tools:["fixture.denied"]};
let p=plan([{id:"a",call:"worker"},{id:"b",call:"worker"}],max_concurrency=2);
try{await p(null,{worker:worker})}catch e{e.code}'''
        app = self.app(registry)
        app.create_project('denied', source=source, capabilities=[])
        self.assertIsInstance(app.start('denied', 1, None, mode='source').result(5), str)
        self.assertEqual(providers, [])
        self.assertEqual(effects, [])

    def test_nested_programs_keep_source_versions_and_cancel_as_one_plan(self):
        pair, registry = CooperatingPair(), Registry()
        registry.register_tool(ToolSpec('fixture.work', pair.work))
        registry.register_tool(ToolSpec('fixture.finished', pair.snapshot))
        app = self.app(registry)
        source = '''let program=program_from(input);
fn peer(c){program("peer")}fn fail(c){program("fail")}
let p=plan([{id:"peer",call:"peer"},{id:"bad",call:"fail"}],max_concurrency=2);
let error=try{await p(null,{peer:peer,fail:fail})}catch e{e.code};
[error,tool("fixture.finished",null)]'''
        app.create_project('program', source=source, capabilities=['fixture.work', 'fixture.finished'])
        run = app.start('program', 1, program_definition('tool("fixture.work",input)'), mode='source')
        result = run.result(8)
        self.assertIsInstance(result[0], str)
        self.assertTrue(result[1])
        starts = [e['payload'] for e in self.events(app, 'program', run.id) if e['type'] == 'program.execution.started']
        self.assertEqual(len(starts), 2)
        self.assertEqual(len({v['callId'] for v in starts}), 2)
        self.assertTrue(all(v['version'] == 1 for v in starts))
        self.assertEqual(app.get_project('program')['source'], source)

    def test_persistent_goal_accepts_parallel_plan_without_replaying_completed_effects(self):
        registry, seen = Registry(), []
        registry.register_tool(ToolSpec('fixture.echo', lambda value, context: seen.append(value) or value))
        source = '''fn planner(c){plan([{id:"left",call:"left"},{id:"right",call:"right"}],max_concurrency=2)}
fn left(c){tool("fixture.echo",20)}fn right(c){tool("fixture.echo",22)}
fn verify(c){{done:c.results.left+c.results.right==42,state:42,evidence:c.results}}
await pursue(goal("answer","planner","verify",2),{planner:planner,left:left,right:right,verify:verify},null,key="answer")'''
        app = self.app(registry)
        app.create_project('goal', source=source, capabilities=['fixture.echo'])
        first = app.start('goal', 1, None, mode='source').result(5)
        second = app.start('goal', 1, None, mode='source')
        self.assertEqual(second.result(5), first)
        self.assertEqual(first, {'done': True, 'state': 42, 'evidence': {'left': 20, 'right': 22}, 'iterations': 1, 'status': 'achieved'})
        self.assertCountEqual(seen, [20, 22])
        self.assertFalse(any(e['type'] == 'mode.plan.step.started' for e in self.events(app, 'goal', second.id)))

    def test_process_checkpoint_waits_for_plan_failure_cleanup(self):
        pair, registry = CooperatingPair(), Registry()
        registry.register_tool(ToolSpec('fixture.work', pair.work))
        registry.register_tool(ToolSpec('fixture.finished', pair.snapshot))
        app = self.app(registry)
        worker = '''fn peer(c){tool("fixture.work","peer")}fn fail(c){tool("fixture.work","fail")}
async fn step(c){let p=plan([{id:"peer",call:"peer"},{id:"bad",call:"fail"}],max_concurrency=2);
let error=try{await p(null,{peer:peer,fail:fail})}catch e{e.code};
process_step(c.state+1,[error,tool("fixture.finished",null)])};export {step}'''
        app.create_project('process', source='null', files={'worker.chaos': worker}, capabilities=['process.manage', 'fixture.work', 'fixture.finished'])
        app.create_process('process', 1, 'worker', process_definition(), state=0)
        with app._engine.idle:
            done = app._engine.idle.wait_for(lambda: app.get_process('process', 'worker')['turn'] == 1, timeout=8)
        self.assertTrue(done)
        row = app.get_process('process', 'worker')
        self.assertEqual((row['state'], row['control']), (1, 'running'))
        self.assertIsInstance(row['output'][0], str)
        self.assertTrue(row['output'][1])
        self.assertTrue(pair.cancelled)
        self.assertEqual(app.get_run(row['lastRunId'])['status'], 'completed')

    def test_parallel_recorded_tools_remain_incomplete(self):
        registry, seen, finished = Registry(), [], threading.Event()

        def slow(value, context):
            seen.append(value)
            try:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    context.checkpoint()
                    time.sleep(.005)
                return value
            finally:
                finished.set()

        registry.register_tool(ToolSpec('fixture.slow', slow))
        source = '''fn work(c){tool("fixture.slow",42)}
let p=plan([{id:"a",call:"work"},{id:"b",call:"work"}],max_concurrency=2);
async fn record(c){await p(null,{work:work})};await record_skill(record)'''
        app = self.app(registry)
        app.create_project('record', source=source, capabilities=['fixture.slow'])
        self.assertEqual(app.start('record', 1, None, mode='source').wait(5)['status'], 'failed')
        self.assertLessEqual(len(seen), 1)
        if seen:
            self.assertTrue(finished.is_set())
        self.assertTrue(all(v['status'] != 'ready' for v in app.modes('record')['items']))

    def test_http_definition_source_format_and_execution_share_language(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as temporary:
            server = make_server(port=0, framework=app, state_dir=Path(temporary)/'control', update_home=Path(temporary)/'updates')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                api = Client('http://127.0.0.1:' + str(server.server_port))
                p = api.create_project('api_plan', source='let p=await load_mode("parallel");fn work(c){21};let r=await p(null,{work:work});r.a+r.b', capabilities=[])
                definition = {'kind': 'plan', 'name': 'parallel', 'max_concurrency': 2, 'steps': [{'id': 'a', 'call': 'work'}, {'id': 'b', 'call': 'work'}]}
                row = api.save_mode(p['id'], 'parallel', definition)
                exported = api.mode_source(p['id'], 'parallel', revision=row['revision'], format='value')
                source = exported['source']
                self.assertEqual(parse_program(source)['semanticHash'], parse_program(format_source(source))['semanticHash'])
                self.assertEqual(analyze(source)['diagnostics'], [])
                self.assertEqual(api.start(p['id'], p['version'], None, mode='source').result(5), 42)
                self.assertEqual(api.mode(p['id'], 'parallel')['definition']['max_concurrency'], 2)
                self.assertIn('max_concurrency', analyze('plan(')['signatureHelp']['label'])
            finally:
                server.shutdown()
                thread.join(3)
                server.server_close()


if __name__ == '__main__':
    unittest.main()
