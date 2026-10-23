import unittest
from chaos import Framework,Settings
from chaos.language import Evaluator,LanguageError

CALLBACKS='fn(s){s.context},fn(p,s){p},fn(s){{done:true,context:s.context,output:s.evidence}}'


class BotValuesTests(unittest.TestCase):
    def test_constructor_callable_type_and_cold_task(self):
        value=Evaluator().run('let b=bot("goal",'+CALLBACKS+',{x:1});let t=b();[type_of(b),type_of(t),bot_info(b).busy,bot_context(b).context]')
        self.assertEqual(value,['Bot','Task',False,{'x':1}])

    def test_snapshots_are_copied_and_cannot_revive_bot(self):
        from chaos.language import Native
        from chaos.bot_values import owned
        evaluator=Evaluator()
        def mutate_snapshot(snapshot,bot):
            owned(bot,evaluator)
            self.assertIsNot(snapshot['context'],bot.state.context)
            self.assertIsNot(snapshot['context']['x'],bot.state.context['x'])
            snapshot['context']['x'][0]=9
            return None
        evaluator.env.bind('mutate_snapshot',Native('mutate_snapshot',mutate_snapshot,evaluator))
        value=evaluator.run('let b=bot("goal",'+CALLBACKS+',{x:[1]});let c=bot_context(b);mutate_snapshot(c,b);[bot_context(b).context,try{bot_info(parse_json(json_stringify(b)))}catch e{e.code}]')
        self.assertEqual(value,[{'x':[1]},'argument_error'])

    def test_callback_and_iteration_preflight(self):
        for source in ('bot("x",3,fn(p,s){p},fn(s){s})','bot("x",'+CALLBACKS+',null,0)','bot("x",'+CALLBACKS+',null,true)'):
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator().run(source)

    def test_foreign_and_later_epoch_return_are_rejected(self):
        e=Evaluator();value=e.run('bot("goal",'+CALLBACKS+')')
        other=Evaluator();other.env.bind('saved',value)
        with self.assertRaises(LanguageError) as caught:other.run('saved()')
        self.assertEqual(caught.exception.code,'ownership_error')
        e.env.bind('saved',value)
        with self.assertRaises(LanguageError) as caught:e.run('fn get(){saved};get()')
        self.assertEqual(caught.exception.code,'ownership_error')

    def test_reconcile_requires_blocked_inactive_identity(self):
        self.assertEqual(Evaluator().run('let b=bot("goal",'+CALLBACKS+');try{bot_reconcile(b,null,null)}catch e{e.code}'),'bot_state_error')

    def test_direct_raw_and_nested_stale_values_are_rejected(self):
        for source in ('saved','[saved]','{held:saved}'):
            e=Evaluator();value=e.run('bot("old",'+CALLBACKS+')');e.env.bind('saved',value)
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:e.run(source)
            self.assertEqual(caught.exception.code,'ownership_error')
