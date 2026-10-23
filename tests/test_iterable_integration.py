"""Iterable composition crosses real Registry effects without hidden awaiting."""
import threading
import unittest

from chaos import Final, Framework, ProviderSpec, Registry, ToolCall, ToolSpec
from chaos.settings import Settings


def team_definition(tools=()):
    tools = list(tools)
    return {'kind': 'team', 'name': 'iterable-team', 'entry': 'worker', 'roles': [
        {'id': 'worker', 'parent': None, 'capabilities': tools,
         'agent': {'kind': 'agent', 'name': 'worker',
                   'config': {'provider': 'fixture.worker', 'tools': tools}}}]}


class IterableIntegrationTests(unittest.TestCase):
    def run_source(self, registry, source, value=None, capabilities=()):
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            project = app.create_project('iterable', source=source,
                                         capabilities=list(capabilities))
            run = app.start(project['id'], project['version'], value, mode='source')
            return run.result(timeout=8)

    def test_agent_callbacks_map_filter_fold_any_all_use_real_registry(self):
        registry, seen = Registry(), []
        def provider(request, context):
            context.checkpoint()
            seen.append(request.input)
            op, value = request.input['op'], request.input['value']
            return Final(value * 2 if op == 'double' else value % 2 == 0 if op == 'even'
                         else value['total'] + value['item'])
        registry.register_provider(ProviderSpec('fixture.worker', provider))
        source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:1};
fn* values(){yield 1;yield 2;yield 3}
[map(fn(x){worker({op:"double",value:x})},values()),
 filter(fn(x){worker({op:"even",value:x})},values()),
 fold(fn(total,x){worker({op:"sum",value:{total:total,item:x}})},0,values()),
 any(fn(x){worker({op:"even",value:x})},values()),
 all(fn(x){worker({op:"even",value:x})},values())]'''
        self.assertEqual(self.run_source(registry, source), [[2, 4, 6], [2], 6, True, False])
        self.assertEqual([row['op'] for row in seen],
                         ['double'] * 3 + ['even'] * 3 + ['sum'] * 3 + ['even'] * 3)
        self.assertEqual([row['value'] for row in seen[-3:]], [1, 2, 1])

    def test_direct_agent_callback_consumes_generator(self):
        registry, seen = Registry(), []
        def provider(request, context):
            context.checkpoint(); seen.append(request.input)
            return Final(request.input * 2)
        registry.register_provider(ProviderSpec('fixture.worker', provider))
        source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:1};
fn* values(){yield 20;yield 21}map(worker,values())'''
        self.assertEqual(self.run_source(registry, source), [40, 42])
        self.assertEqual(seen, [20, 21])

    def test_direct_agent_apply_and_spread_with_effectful_iterators(self):
        registry, seen = Registry(), []
        def provider(request, context):
            context.checkpoint(); seen.append(request.input)
            return Final(request.input.get('value'))
        registry.register_provider(ProviderSpec('fixture.worker', provider))
        source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:1};
class Source{var n=0;fn __iter__(self){worker({op:"iter"});self}
fn __next__(self){self.n=self.n+1;worker({op:"next",value:self.n});
{done:self.n>1,value:{op:"target",value:42}}}}
[apply(worker,Source()),worker(*Source())]'''
        self.assertEqual(self.run_source(registry, source), [42, 42])
        self.assertEqual([row['op'] for row in seen], ['iter', 'next', 'next', 'target'] * 2)

    def test_map_async_callbacks_are_cold_and_compose_with_gather_and_group(self):
        registry, seen = Registry(), []
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context:
            context.checkpoint() or seen.append(request.input) or Final(request.input * 2)))
        registry.register_tool(ToolSpec('fixture.count', lambda value, context: len(seen)))
        source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:1};
fn* values(){yield 20;yield 21}async fn work(x){worker(x)}
let jobs=map(work,values());let cold=[tool("fixture.count",null),map(fn(t){task_info(t).state},jobs)];
let gathered=await gather(jobs);let more=map(work,iter([22,23]));
async with task_group() as group{for task in more{group.spawn(task)}};
[cold,gathered,await gather(more)]'''
        self.assertEqual(self.run_source(registry, source, capabilities=['fixture.count']),
                         [[0, ['created', 'created']], [40, 42], [44, 46]])
        self.assertCountEqual(seen, [20, 21, 22, 23])

    def test_direct_team_map_returns_cold_tasks_and_preserves_role_provenance(self):
        registry, seen = Registry(), []
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context:
            context.checkpoint() or seen.append(request.input) or Final(request.input + 1)))
        registry.register_tool(ToolSpec('fixture.count', lambda value, context: len(seen)))
        source = '''let team=team_from(input);fn* values(){yield 40;yield 41}
let jobs=map(team,values());let before=tool("fixture.count",null);[before,await gather(jobs)]'''
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            project = app.create_project('team', source=source, capabilities=['fixture.count'])
            run = app.start(project['id'], project['version'], team_definition(), mode='source')
            self.assertEqual(run.result(timeout=8), [0, [41, 42]])
            events = app.events(project['id'], run_id=run.id, limit=300)['items']
            starts = [event['payload'] for event in events if event['type'] == 'team.role.started']
            self.assertEqual(len(starts), 2)
            self.assertTrue(all(row['roleId'] == 'worker' and row['rolePath'] == ['worker']
                                for row in starts))
        self.assertCountEqual(seen, [40, 41])

    def test_parallel_consumers_cooperate_across_real_factory_step_and_callback_effects(self):
        registry, barrier, lock, seen = Registry(), threading.Barrier(2), threading.Lock(), []
        def provider(request, context):
            context.checkpoint()
            with lock:
                seen.append((request.input['phase'], threading.get_ident()))
            barrier.wait(timeout=3)
            context.checkpoint()
            value = request.input['value']
            return Final(value * 2 if request.input['phase'] == 'callback' else value)
        registry.register_provider(ProviderSpec('fixture.worker', provider))
        source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:1};
class Source{var n=0;var done=false;fn __init__(self,n){self.n=n;null}
fn __iter__(self){worker({phase:"factory",value:self.n});self}
fn __next__(self){if self.done{{done:true,value:null}}else{self.done=true;
{done:false,value:worker({phase:"step",value:self.n})}}}}
async fn consume(n){map(fn(x){worker({phase:"callback",value:x})},Source(n))}
await gather([consume(20),consume(21)])'''
        self.assertEqual(self.run_source(registry, source), [[40], [42]])
        self.assertEqual([phase for phase, _ in seen], ['factory'] * 2 + ['step'] * 2 + ['callback'] * 2)
        self.assertEqual(len({thread for _, thread in seen}), 2)

    def test_argument_iteration_failure_never_invokes_agent_target(self):
        for call in ('apply(worker,Source())', 'worker(*Source())'):
            with self.subTest(call=call):
                registry, seen = Registry(), []
                registry.register_provider(ProviderSpec('fixture.worker', lambda request, context:
                    seen.append(request.input) or Final(None)))
                source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:1};
class Source{var n=0;fn __iter__(self){self}fn __next__(self){self.n=self.n+1;
worker({op:"read",value:self.n});if self.n==2{throw "source failed"}else{{done:false,value:1}}}}
try{''' + call + '''}catch e{e.value}'''
                self.assertEqual(self.run_source(registry, source), 'source failed')
                self.assertEqual(seen, [{'op': 'read', 'value': 1}, {'op': 'read', 'value': 2}])

    def test_composition_keeps_agent_and_team_capability_boundaries(self):
        registry, effects = Registry(), []
        registry.register_tool(ToolSpec('fixture.denied', lambda value, context: effects.append(value)))
        registry.register_provider(ProviderSpec('fixture.worker', lambda request, context:
            ToolCall('fixture.denied', request.input)))
        source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:2};
try{map(worker,iter([1]))}catch e{"denied"}'''
        self.assertEqual(self.run_source(registry, source, capabilities=['fixture.denied']), 'denied')
        source = '''let team=team_from(input);try{await gather(map(team,iter([1])))}catch e{"denied"}'''
        self.assertEqual(self.run_source(registry, source, team_definition(['fixture.denied'])), 'denied')
        self.assertEqual(effects, [])

    def test_recording_rejects_external_cursors_before_callback_effects(self):
        for consumer in ('map(worker,foreign)', 'filter(worker,foreign)',
                         'fold(fn(a,x){worker(x)},0,foreign)', 'any(worker,foreign)',
                         'all(worker,foreign)', 'apply(worker,foreign)', 'worker(*foreign)'):
            with self.subTest(consumer=consumer):
                registry, effects = Registry(), []
                registry.register_provider(ProviderSpec('fixture.worker', lambda request, context:
                    effects.append(request.input) or Final(True)))
                source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:1};
let foreign=iter([1]);async fn action(x){''' + consumer + '''};
try{await record_skill(action)}catch error{error.code}'''
                self.assertEqual(self.run_source(registry, source), 'mode_error')
                self.assertEqual(effects, [])

    def test_recording_cursors_cannot_escape_to_outer_composition(self):
        for consumer in ('map(worker,cursor)', 'filter(worker,cursor)',
                         'fold(fn(a,x){worker(x)},0,cursor)', 'any(worker,cursor)',
                         'all(worker,cursor)', 'apply(worker,cursor)', 'worker(*cursor)'):
            with self.subTest(consumer=consumer):
                registry, effects = Registry(), []
                registry.register_tool(ToolSpec('fixture.identity', lambda value, context: value))
                registry.register_provider(ProviderSpec('fixture.worker', lambda request, context:
                    effects.append(request.input) or Final(True)))
                source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:1};
var cursor=null;async fn action(x){cursor=iter([1]);tool("fixture.identity",42)};await record_skill(action);
try{''' + consumer + '''}catch error{error.code}'''
                self.assertEqual(self.run_source(registry, source, capabilities=['fixture.identity']), 'mode_error')
                self.assertEqual(effects, [])

    def test_cancellation_drains_iterator_and_callback_effects(self):
        cases = {
            'factory': ('worker("blocked");self', '{done:false,value:1}', 'map(fn(x){x},Source())'),
            'step': ('self', 'worker("blocked");{done:false,value:1}', 'apply(worker,Source())'),
            'callback': ('self', '{done:false,value:1}', 'map(worker,Source())'),
        }
        for label, (factory, step, action) in cases.items():
            with self.subTest(phase=label):
                registry, entered, exited, cleanup = Registry(), threading.Event(), threading.Event(), []
                def provider(request, context):
                    if request.input == 'cleanup':
                        cleanup.append(True); return Final(None)
                    entered.set()
                    try:
                        for _ in range(500):
                            context.checkpoint(); threading.Event().wait(.01)
                        raise AssertionError('iterable effect ignored cancellation')
                    finally:
                        exited.set()
                registry.register_provider(ProviderSpec('fixture.worker', provider))
                source = '''agent worker{provider:"fixture.worker",tools:[],maxSteps:1};
class Source{fn __iter__(self){''' + factory + '''}fn __next__(self){''' + step + '''}
fn __close__(self){worker("cleanup");null}}async fn consume(){''' + action + '''};await consume()'''
                with Framework(registry=registry, settings=Settings(':memory:')) as app:
                    project = app.create_project('cancel', source=source, capabilities=[])
                    run = app.start(project['id'], project['version'], None, mode='source')
                    try:
                        self.assertTrue(entered.wait(3)); run.cancel()
                        self.assertEqual(run.wait(timeout=5)['status'], 'cancelled')
                        self.assertTrue(exited.is_set()); self.assertEqual(cleanup, [])
                    finally:
                        run.cancel()


if __name__ == '__main__':
    unittest.main()
