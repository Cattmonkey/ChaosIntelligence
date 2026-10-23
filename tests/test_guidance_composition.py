import unittest

from chaos.language import Evaluator
from chaos import Framework, Registry, ProviderSpec, ToolCall, Final, Settings
from tests.test_guidance_native import Mailbox
from tests.test_team_functions import definition


class GuidanceComposition(unittest.TestCase):
    def test_appended_source_builds_real_async_function_with_original_bindings(self):
        box = Mailbox([{'source': 'async fn(x){await sleep(0);base+x}'}])
        result = Evaluator(guidance_handler=box.handle).run('''
        let guide=await guidance_receive();
        let work=function_from(code_from(guide.message.input.source),{base:40});
        await work(2)''')
        self.assertEqual(result, 42)

    def test_async_stream_preserves_terminal_multiple_values(self):
        box = Mailbox([21])
        result = Evaluator(guidance_handler=box.handle).run('''
        async fn* controller(){let guide=await guidance_receive();
            await progress_emit("plan",{summary:"received"});yield guide.message.input;
            return return_values(null,42)};
        let stream=controller();let first=await anext(stream);await anext(stream);
        [first,multiple_value_list(stream)]''')
        self.assertEqual(result, [{'done': False, 'value': 21}, [None, 42]])
        self.assertEqual(box.events, [{'kind': 'plan', 'data': {'summary': 'received'}}])

    def test_bound_team_preserves_receiving_authority_and_provenance(self):
        box = Mailbox([42])
        evaluator = Evaluator(guidance_handler=box.handle)
        result = evaluator.run('''let team=team_from(input);
        async fn controller(x){(await guidance_receive()).message.input+x};
        await team_bind(team,{worker:controller})(0)''', {'input': definition(['text.normalize'])})
        self.assertEqual(result, 42)
        receive = next(item for item in box.calls if item[0] == 'receive')
        self.assertEqual(receive[1], frozenset({'text.normalize'}))
        self.assertEqual(receive[2]['roleId'], 'worker')

    def test_agent_tool_uses_same_live_evaluator_and_native_guidance(self):
        registry = Registry()
        registry.register_provider(ProviderSpec('choose', lambda request, context:
            ToolCall('work', request.input) if not request.history else Final(request.history[-1]['output'])))
        with Framework(registry=registry, settings=Settings(':memory:')) as app:
            app.create_project('guided', source='''agent model{provider:"choose"};
            async fn work(x){let guide=await guidance_receive(null);
                await progress_emit("result",{summary:"received",value:guide.message.input});
                guide.message.input+x};
            await agent_bind(model,[agent_tool("work",work)])(2)''', capabilities=[])
            run = app.start('guided', 1, None, mode='source')
            receipt = app._engine.guide(run.id, 40, 'message-1')
            self.assertEqual(receipt['status'], 'queued')
            self.assertEqual(run.result(5), 42)
            events = app.events('guided', run_id=run.id)['items']
            self.assertTrue(any(item['type'] == 'bot.progress' and item['payload']['data']['value'] == 40 for item in events))
