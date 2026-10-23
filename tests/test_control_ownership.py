"""Owned control identities, full payload sealing and authority ceilings."""
import unittest
from chaos.language import Evaluator,LanguageError,Native,TailCall
from chaos.capture_graph import seal
from chaos.control_prompts import PromptTag


class ControlOwnershipTests(unittest.TestCase):
    def test_foreign_tags_scopes_and_escape_rejected(self):
        other=Evaluator();foreign=other.run('prompt_tag("same")')
        e=Evaluator();e.env.bind('foreign',foreign)
        for source in ('prompt_available(foreign)','call_prompt(foreign,fn(){1})','prompt_scope(foreign)'):
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:e.run(source)
            self.assertEqual(caught.exception.code,'ownership_error')
        for source in ('call_escape(fn(exit){exit})','prompt_scope(prompt_tag())'):
            e=Evaluator()
            e.env.bind('foreign',other.run(source))
            with self.subTest(source=source),self.assertRaises(LanguageError):e.run('type_of(foreign)')

    def test_tag_reuses_across_epochs_escape_and_scope_do_not(self):
        e=Evaluator();tag=e.run('prompt_tag()');self.assertEqual(e.run('call_prompt(input,fn(){42})',{'input':tag}),42)
        for source in ('call_escape(fn(exit){exit})','prompt_scope(prompt_tag())'):
            old=e.run(source)
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:e.run('type_of(input)',{'input':old})
            self.assertEqual(caught.exception.code,'ownership_error')

    def test_native_metadata_readonly_and_payload_not_exposed(self):
        self.assertEqual(Evaluator().run('''let p=prompt_tag("name");let s=prompt_scope(p);
          [type_of(p),p.name,dir(p),dir(s),call_escape(fn(exit){dir(exit)})]'''),
          ['PromptTag','name',['id','name'],['active','id','state','tag'],['active','available','id','scopeId','state']])
        for source in ('let p=prompt_tag();p.name="other"','let s=prompt_scope(prompt_tag());s.handler',
                       'call_escape(fn(exit){exit.scope})','abort_prompt({id:"fake"})'):
            with self.subTest(source=source),self.assertRaises(LanguageError):Evaluator().run(source)

    def test_secondary_foreign_payload_rejects_before_transfer(self):
        other=Evaluator();e=Evaluator();e.env.bind('foreign',Native('foreign',lambda:42,other))
        with self.assertRaises(LanguageError) as caught:e.run('let p=prompt_tag();call_prompt(p,fn(){abort_prompt(p,1,foreign)})')
        self.assertEqual(caught.exception.code,'ownership_error')

    def test_payload_callable_is_projected_under_narrow_authority(self):
        effects=[];e=Evaluator(tool_handler=lambda name,value:effects.append(name) or 42)
        function=e.run('fn(){tool("secret",null)}');e.env.bind('wide',function)
        e.env.bind('restricted',Native('restricted',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        with self.assertRaises(LanguageError) as caught:
            e.run('''let p=prompt_tag();let captured=call_prompt(p,fn(){restricted(fn(){abort_prompt(p,wide)})});captured()''')
        self.assertEqual(caught.exception.code,'capability_error');self.assertEqual(effects,[])

    def test_foreign_tag_capture_is_checked(self):
        e=Evaluator();foreign=PromptTag(Evaluator(),'foreign','')
        with self.assertRaises(LanguageError) as caught:seal(e,(foreign,),frozenset())
        self.assertEqual(caught.exception.code,'ownership_error')

    def test_child_task_cannot_invoke_parent_escape(self):
        self.assertEqual(Evaluator().run('''call_escape(async fn(exit){async fn child(){try{exit(1)}catch e{e.code}};
          [await child(),exit.available]})'''),['escape_error',True])

    def test_projected_escape_keeps_identity_and_eql_dispatch(self):
        e=Evaluator();e.env.bind('restricted',Native('restricted',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        self.assertEqual(e.run('''call_escape(fn(exit){let limited=restricted(fn(){exit});
          let same=generic_method(generic_function("same"),[generic_eql(exit)],fn(x){x==exit});
          [limited==exit,same(limited),limited.available]})'''),[True,True,True])

