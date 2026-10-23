import unittest
from chaos.language import Evaluator,LanguageError,Native,ResourceLimit
from chaos.debugger import preview

class DynamicValues(unittest.TestCase):
    def value(self,source):return Evaluator(step_limit=250000,timeout=4).run(source)
    def test_context_ids_do_not_consume_existing_task_provenance_sequence(self):
        self.assertEqual(self.value('let v=context_var("x",0);let c=copy_context();async fn f(){v.get()}let t=f();[task_info(t).id,await t]'),['task-1',0])
    def test_defaults_missing_null_and_distinct_names(self):
        self.assertEqual(self.value('let a=context_var("x");let b=context_var("x",null);[try{a.get()}catch e{e.code},a.get(3),b.get(),b.get(5),a==b]'),['context_lookup_error',3,None,5,False])
    def test_reset_original_identity_and_singleuse(self):
        self.assertEqual(self.value('let v=context_var("x",0);let a=v.set(1);let b=v.set(2);v.reset(a);let x=v.get();v.reset(b);[x,v.get(),try{v.reset(b)}catch e{e.code}]'),[0,1,'context_token_error'])
    def test_wrong_variable_does_not_consume(self):
        self.assertEqual(self.value('let a=context_var("x",0);let b=context_var("x",1);let t=a.set(3);let e=try{b.reset(t)}catch e{e.code};a.reset(t);[e,a.get()]'),['context_token_error',0])
    def test_context_maps_defaults_not_entries(self):
        self.assertEqual(self.value('let a=context_var("x",42);let c=copy_context();[c.has(a),c.keys(),try{c.get(a)}catch e{e.code},c.get(a,null)]'),[False,[],'context_lookup_error',None])
    def test_shallow_copy_function_identity(self):
        self.assertEqual(self.value('let v=context_var("f");let f=fn(x){x+1};v.set(f);let c=copy_context();v.set(fn(x){0});[c.get(v)==f,c.get(v)(41),v.get()(41)]'),[True,42,0])
    def test_original_native_values_in_class(self):
        self.assertEqual(self.value('let v=context_var("n",decimal("0.10"));class C{let field=v;}let c=C();[type_of(c.field),decimal_text(c.field.get())]')[:2],['ContextVar','0.10'])
    def test_persistent_run_kwargs_and_restore(self):
        self.assertEqual(self.value('let v=context_var("x",0);let c=context();fn f(x,*,n){v.set(x+n);v.get()}let a=c.run(f,[40],{n:2});[a,c.get(v),v.get()]'),[42,42,0])
    def test_active_run_rejected_and_reusable(self):
        self.assertEqual(self.value('let c=context();let e=c.run(fn(){try{c.run(fn(){1})}catch e{e.code}});[e,c.run(fn(){2})]'),['context_busy',2])
    def test_failed_run_restores(self):
        self.assertEqual(self.value('let v=context_var("x",0);let c=context();try{c.run(fn(){v.set(3);throw "x"})}catch e{null};[v.get(),c.get(v),c.run(fn(){v.get()})]'),[0,3,3])
    def test_token_with_and_old_value(self):
        self.assertEqual(self.value('let v=context_var("x",0);v.set(1);let t=v.set(2);let old=t.old_value;with t as token {v.set(3)};[old,t.["var"]==v,t.used,v.get(),try{with t as x{null}}catch e{e.code}]'),[1,True,True,1,'context_token_error'])
    def test_duplicate_token_entry_rejected(self):
        self.assertEqual(self.value('let v=context_var("x",0);let t=v.set(2);let e=with t as x{try{with t as y{null}}catch e{e.code}};[e,v.get()]'),['context_token_error',0])
    def test_token_old_value_after_reset_and_lexical_reset_guard(self):
        self.assertEqual(self.value('let v=context_var("x",0);v.set(1);let t=v.set(2);v.reset(t);let old=t.old_value;let b=v.set(3);let e=with b as x{try{v.reset(b)}catch e{e.code}};[old,b.old_value,e,v.get()]'),[1,1,'context_token_error',1])
    def test_mapping_bracket_length_contains_iteration(self):
        self.assertEqual(self.value('let v=context_var("x",0);v.set(42);let c=copy_context();[c[v],getitem(c,v),length(c),contains(c,v),collect(iter(c))[0]==v,c.items()[0][1]]'),[42,42,1,True,True,42])
    def test_callback_extraction_keeps_restricted_authority(self):
        e=Evaluator(tool_handler=lambda n,v:42)
        limited=Native('limited',lambda fn:e.invoke(fn,[]),e,capabilities=frozenset())
        self.assertEqual(e.run('let v=context_var("f",fn(){tool("secret",null)});let f=limited(fn(){v.get()});try{f()}catch e{e.code}',{'limited':limited}),'capability_error')
    def test_foreign_epoch_and_host_metadata(self):
        e=Evaluator();v=e.run('context_var("secret",fn(){42})');self.assertNotIn('default',preview(v));self.assertNotIn('owner',preview(v))
        with self.assertRaises(LanguageError):Evaluator().run('context_get(input)',{'input':v})
    def test_aggregate_copy_budget_and_cleanup(self):
        e=Evaluator(step_limit=1000000,timeout=4)
        with self.assertRaises(ResourceLimit):e.run('let v=context_var("x");v.set(1);while true{copy_context()}')
        self.assertEqual(e.suspension.context_values,[])
    def test_capability_not_granted_by_stored_callback(self):
        e=Evaluator(tool_handler=lambda n,v:42)
        limited=Native('limited',lambda fn:e.invoke(fn,[]),e,capabilities=frozenset())
        self.assertEqual(e.run('let v=context_var("f",fn(){tool("secret",null)});try{limited(fn(){v.get()()})}catch e{e.code}',{'limited':limited}),'capability_error')

if __name__=='__main__':unittest.main()
