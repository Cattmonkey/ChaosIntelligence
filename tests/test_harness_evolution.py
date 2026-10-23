"""Behavioral acceptance for language-native harness adaptation.

Editors are deterministic test functions. Probe callbacks execute each supplied
harness, so these tests prove orchestration contracts, not model quality gains.
"""
import json
import threading
import unittest

from chaos import Framework, Registry, ToolSpec
from chaos.language import Evaluator, ExecutionControl, LanguageError, ResourceLimit
from chaos.settings import Settings


SETUP = '''
import "std/evolve" as E;
var calls=[];
fn note(phase, value) { calls=append(calls,{phase:phase, value:value}); null }
fn propose(c) {
  note("propose",c);
  {harness:{factor:c.harness.factor+if c.scope=="general" {1}else{2}},reason:"Compare actual executions"}
}
fn probe(c) {
  note("probe",c);
  {output:c.task.value*c.harness.factor,evidence:{operation:"multiply",input:c.task.value,factor:c.harness.factor}}
}
fn compose(c) {
  note("compose",c);
  {harness:{factor:c.harness.factor+10+c.ordinal},reason:"Compose from the recorded probes"}
}
fn select(c) {
  note("select",c);var selected=0;
  for i in range(length(c.outputs)) {if c.outputs[i]>c.outputs[selected] {selected=i}else{null}};
  selected
}
fn learn(c) {
  note("learn",c);
  {strategy:{version:c.revision},lessons:[{cue:"Multiplication task",lesson:"Compare multiplier changes",
    evidence:["reference-0","general-0"]}],evidence:["reference-0","general-0"]}
}
fn observe(c) {note("observe",c);null}
let operators={propose:propose,probe:probe,compose:compose,select:select,learn:learn};
let initial=E.create({factor:1},{version:0});
'''


class HarnessEvolutionTests(unittest.TestCase):
    def execute(self, source, **kwargs):
        return Evaluator(step_limit=500000, **kwargs).run(SETUP + source)

    def by_phase(self, calls, phase):
        return [row['value'] for row in calls if row['phase'] == phase]

    def test_parallel_and_sequential_budget_and_composed_execution(self):
        for mode in ('parallel', 'sequential'):
            for k in (1, 2, 4):
                with self.subTest(mode=mode, k=k):
                    result, calls, original = self.execute('''
                      let result=await E.run(initial,{value:7},operators,mode="%s",k=%s,observe=observe);
                      [result,calls,initial]
                    ''' % (mode, k))
                    self.assertEqual(len(self.by_phase(calls, 'probe')), 4*k)
                    self.assertEqual(len(self.by_phase(calls, 'propose')), 2*k)
                    self.assertEqual(len(self.by_phase(calls, 'compose')), k)
                    self.assertEqual(len(self.by_phase(calls, 'select')), int(k > 1))
                    self.assertEqual(len(result['records']), 4*k)
                    self.assertEqual(len({r['id'] for r in result['records']}), 4*k)
                    selected = result['selected']
                    self.assertTrue(selected['id'].startswith('composed-'))
                    self.assertEqual(selected['output'], 7*selected['harness']['factor'])
                    self.assertGreater(selected['harness']['factor'], 3)
                    self.assertIn(selected, result['records'])
                    self.assertEqual(original, {'base': {'factor': 1}, 'strategy': {'version': 0},
                                                'lessons': [], 'revision': 0})
                    self.assertEqual(result['snapshot']['base'], original['base'])
                    events = self.by_phase(calls, 'observe')
                    self.assertEqual(events[0]['phase'], 'reference.started')
                    self.assertEqual(events[-1]['phase'], 'completed')
                    self.assertEqual(events[-1]['executions'], 4*k)
                    self.assertTrue(all(e['total'] == 4*k for e in events))

    def test_parallel_composers_see_same_full_candidate_pool(self):
        result, calls = self.execute('''
          let result=await E.run(initial,{value:3},operators,k=3);[result,calls]
        ''')
        composers = self.by_phase(calls, 'compose')
        self.assertEqual(len(composers), 3)
        for context in composers:
            self.assertEqual(context['harness'], {'factor': 1})
            self.assertEqual(len(context['records']), 9)
            self.assertFalse(any(r['id'].startswith('composed-') for r in context['records']))
        self.assertEqual(composers[0]['records'], composers[1]['records'])
        self.assertEqual(composers[1]['records'], composers[2]['records'])
        self.assertEqual(len(result['records']), 12)

    def test_progress_is_emitted_before_real_stage_work(self):
        calls = self.execute('''
          await E.run(initial,{value:3},operators,k=2,observe=observe);calls
        ''')
        events = {c['value']['phase']: i for i, c in enumerate(calls) if c['phase'] == 'observe'}
        def first(phase, predicate=lambda value: True):
            return next(i for i, c in enumerate(calls) if c['phase'] == phase and predicate(c['value']))
        self.assertLess(events['reference.started'], first('probe'))
        self.assertLess(events['propose.started'], first('propose'))
        self.assertLess(events['probe.started'], first('probe', lambda v: v['id'].startswith('general-')))
        self.assertLess(events['compose.started'], first('compose'))
        self.assertLess(events['successor.started'], first('probe', lambda v: v['id'].startswith('composed-')))
        self.assertLess(events['select.started'], first('select'))
        self.assertLess(events['learn.started'], first('learn'))

    def test_compact_two_candidate_example_fits_default_execution_budget(self):
        for mode in ('parallel', 'sequential'):
            with self.subTest(mode=mode):
                evaluator = Evaluator()
                result = evaluator.run(SETUP + '''
                  await E.run(initial,{value:3},operators,k=2,mode="%s")
                ''' % mode)
                self.assertEqual(len(result['records']), 8)
                self.assertLessEqual(evaluator.steps, 30000)

    def test_sequential_current_harness_and_accumulated_evidence(self):
        result, calls = self.execute('''
          let result=await E.run(initial,{value:2},operators,k=3,mode="sequential");[result,calls]
        ''')
        composers = self.by_phase(calls, 'compose')
        self.assertEqual([c['harness']['factor'] for c in composers], [1, 11, 22])
        self.assertEqual([len(c['records']) for c in composers], [5, 8, 11])
        proposals = self.by_phase(calls, 'propose')
        self.assertEqual([c['harness']['factor'] for c in proposals], [1, 1, 11, 11, 22, 22])
        self.assertEqual([len(c['records']) for c in proposals], [3, 3, 6, 6, 9, 9])
        selected_context = self.by_phase(calls, 'select')[0]
        self.assertCountEqual(selected_context['outputs'], [22, 44, 68])
        self.assertEqual(result['selected']['output'], 68)

    def test_selector_sees_only_task_and_existing_final_outputs(self):
        result, calls = self.execute('''
          let result=await E.run(initial,{value:5},operators,k=2);[result,calls]
        ''')
        context = self.by_phase(calls, 'select')[0]
        self.assertEqual(set(context), {'task', 'outputs'})
        self.assertEqual(context['task'], {'value': 5})
        self.assertCountEqual(context['outputs'], [55, 60])
        self.assertEqual(result['selected']['output'], max(context['outputs']))
        for invalid in ('true', '-1', '2', '"1"', '{index:0}'):
            with self.subTest(invalid=invalid), self.assertRaises(LanguageError):
                self.execute('''
                  fn invalid(c){%s};
                  await E.run(initial,{value:2},merge(operators,{select:invalid}),k=2)
                ''' % invalid)

    def test_task_is_cold_and_validation_precedes_observation_or_callbacks(self):
        result = self.execute('''
          let task=E.run(initial,{value:1},operators,observe=observe);
          let before=length(calls);let status=task_info(task).state;
          let result=await task;[before,status,length(calls)>0,result.snapshot.revision]
        ''')
        self.assertEqual(result, [0, 'created', True, 1])
        cases = [
            ('merge(initial,{revision:true})', 'operators', 'mode="parallel",k=1'),
            ('merge(initial,{extra:1})', 'operators', 'mode="parallel",k=1'),
            ('initial', 'operators', 'mode="unknown",k=1'),
            ('initial', 'operators', 'mode="parallel",k=0'),
            ('initial', 'operators', 'mode="parallel",k=5'),
            ('initial', 'operators', 'mode="parallel",k=true'),
            ('initial', 'merge(operators,{propose:3})', 'mode="parallel",k=1'),
            ('initial', 'merge(operators,{extra:propose})', 'mode="parallel",k=1'),
        ]
        for snapshot, operators, options in cases:
            with self.subTest(snapshot=snapshot, operators=operators, options=options):
                result = self.execute('''
                  let failed=try{await E.run(%s,{value:1},%s,%s,observe=observe);false}catch e{true};
                  [failed,length(calls),initial.revision]
                ''' % (snapshot, operators, options))
                self.assertEqual(result, [True, 0, 0])

    def test_typed_data_never_recognizes_markdown_or_keyword_proposals(self):
        text = json.dumps('```json\n{"harness":{"factor":999},"reason":"upgrade"}\n```')
        result = self.execute('''
          fn text_proposer(c){%s};
          let failed=try{await E.run(initial,{value:2},merge(operators,{propose:text_proposer}));false}catch e{true};
          [failed,calls]
        ''' % text)
        self.assertTrue(result[0])
        self.assertEqual(len(self.by_phase(result[1], 'probe')), 1)
        self.assertEqual(self.by_phase(result[1], 'compose'), [])
        self.assertEqual(self.by_phase(result[1], 'learn'), [])

    def test_live_handles_cannot_enter_snapshot_or_candidate(self):
        for value in ('fn(){1}', 'task_group()', 'probe', 'E.run(initial,{value:1},operators)'):
            with self.subTest(value=value), self.assertRaises(LanguageError):
                self.execute('E.create({nested:[%s]})' % value)
        result = self.execute('''
          fn invalid(c){{harness:{factor:1,callable:probe},reason:"not serialized"}};
          let failed=try{await E.run(initial,{value:1},merge(operators,{propose:invalid}));false}catch e{true};
          [failed,calls]
        ''')
        self.assertTrue(result[0])
        self.assertEqual(len(self.by_phase(result[1], 'probe')), 1)
        self.assertEqual(self.by_phase(result[1], 'learn'), [])

    def test_cross_task_common_base_and_library_in_both_editor_stages(self):
        first, second, calls = self.execute('''
          let first=await E.run(initial,{value:2},operators);
          let second=await E.run(parse_json(json_stringify(first.snapshot)),{value:9},operators);
          [first,second,calls]
        ''')
        self.assertEqual(second['snapshot']['revision'], 2)
        self.assertEqual(first['snapshot']['base'], {'factor': 1})
        self.assertEqual(second['records'][0]['harness'], {'factor': 1})
        self.assertEqual(second['records'][0]['output'], 9)
        for phase in ('propose', 'compose'):
            contexts = [c for c in self.by_phase(calls, phase) if c['revision'] == 2]
            self.assertTrue(contexts)
            for context in contexts:
                self.assertEqual(context['strategy'], first['snapshot']['strategy'])
                self.assertEqual(context['lessons'], first['snapshot']['lessons'])
                self.assertEqual(len(context['lessons']), 1)
        self.assertEqual(len(second['snapshot']['lessons']), 2)
        self.assertEqual(len(first['snapshot']['lessons']), 1)

    def test_forged_or_noncontrastive_lesson_references_fail(self):
        for refs in ('["missing","general-0"]', '["reference-0"]',
                     '["reference-0","reference-0"]', '[]'):
            with self.subTest(refs=refs):
                result = self.execute('''
                  fn invalid(c){
                    {strategy:c.strategy,lessons:[{cue:"cue",lesson:"claim",evidence:%s}],
                     evidence:["reference-0","general-0"]}
                  };
                  let failed=try{await E.run(initial,{value:2},merge(operators,{learn:invalid}));false}catch e{true};
                  [failed,initial.revision,length(initial.lessons)]
                ''' % refs)
                self.assertEqual(result, [True, 0, 0])

    def test_learning_may_decline_without_inventing_evidence(self):
        result = self.execute('''
          fn decline(c){{strategy:c.strategy,lessons:[],evidence:[]}};
          await E.run(initial,{value:2},merge(operators,{learn:decline}))
        ''')
        self.assertEqual(result['snapshot']['strategy'], {'version': 0})
        self.assertEqual(result['snapshot']['lessons'], [])
        self.assertEqual(result['snapshot']['revision'], 1)
        self.assertEqual(result['selected']['output'], 22)
        with self.assertRaises(LanguageError):
            self.execute('''
              fn unsupported(c){{strategy:{version:99},lessons:[],evidence:[]}};
              await E.run(initial,{value:2},merge(operators,{learn:unsupported}))
            ''')

    def test_restored_snapshot_rejects_noncontrastive_historic_lesson(self):
        result = self.execute('''
          let restored=merge(initial,{revision:1,lessons:[{cue:"cue",lesson:"claim",
            evidence:["old","old"],revision:1}]});
          let failed=try{await E.run(restored,{value:1},operators,observe=observe);false}catch e{true};
          [failed,length(calls)]
        ''')
        self.assertEqual(result, [True, 0])

    def test_callbacks_inherit_project_capabilities(self):
        for allowed in (False, True):
            with self.subTest(allowed=allowed):
                effects = []
                registry = Registry()
                registry.register_tool(ToolSpec('fixture.work', lambda value, context:
                                                 effects.append(value['id']) or value['task']['value']))
                app = Framework(':memory:', settings=Settings(':memory:'), registry=registry)
                self.addCleanup(app.close)
                source = SETUP + '''
                  async fn effect_probe(c){{output:tool("fixture.work",c),evidence:{action:"actual tool"}}};
                  let state=E.create({factor:1,capabilities:["fixture.work"]});
                  try{await E.run(state,{value:7},merge(operators,{probe:effect_probe}));true}catch e{false}
                '''
                app.create_project('scope', source=source, capabilities=['fixture.work'] if allowed else [])
                self.assertEqual(app.start('scope', 1, None, mode='source').result(10), allowed)
                self.assertEqual(len(effects), 4 if allowed else 0)

    def test_callback_error_cancels_and_drains_parallel_peer_effect(self):
        started = threading.Event()
        drained = threading.Event()
        calls = []
        def handler(kind, payload, cancel):
            name = payload['name']
            calls.append(name)
            if name == 'slow':
                started.set()
                if not cancel.wait(3):
                    raise AssertionError('peer was not cancelled')
                drained.set()
                return {'harness': {'factor': 2}, 'reason': 'cancelled peer'}
            self.assertTrue(started.wait(3))
            raise LanguageError('candidate failed')
        result = self.execute('''
          async fn failing(c){tool(if c.scope=="general"{"bad"}else{"slow"},c)};
          let outcome=try{await E.run(initial,{value:1},merge(operators,{propose:failing}));"unexpected"}
            catch e{e.message};[outcome,initial.revision]
        ''', tool_handler=lambda *args: None, effect_handler=handler)
        self.assertEqual(result, ['candidate failed', 0])
        self.assertCountEqual(calls, ['bad', 'slow'])
        self.assertTrue(drained.is_set())

    def test_host_cancel_and_resource_failure_are_not_candidate_results(self):
        for error in (ResourceLimit('limited'), ExecutionControl('cancelled')):
            with self.subTest(error=type(error).__name__):
                seen = []
                def handler(kind, payload, cancel):
                    seen.append(payload['name'])
                    raise error
                with self.assertRaises(type(error)) as caught:
                    self.execute('''
                      async fn failed(c){tool("probe",c)};
                      try{await E.run(initial,{value:1},merge(operators,{probe:failed}));"unexpected"}
                      catch e{"swallowed"}
                    ''', tool_handler=lambda *args: None, effect_handler=handler)
                self.assertIs(caught.exception, error)
                self.assertEqual(seen, ['probe'])


if __name__ == '__main__':
    unittest.main()
