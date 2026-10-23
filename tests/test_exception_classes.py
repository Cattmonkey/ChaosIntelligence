"""Exception classes share nominal objects, occurrence graphs and effect ceilings."""
import unittest
from chaos.language import Evaluator, LanguageError, Native, TailCall, ResourceLimit, ExecutionControl
from chaos.suspension import TaskCancelled

class ExceptionClasses(unittest.TestCase):
    def code(self, source, **options): return Evaluator(**options).run(source)

    def test_class_identity_c3_aliases_and_shadowing(self):
        self.assertEqual(self.code('class A(Exception){};class B(A){};class C(A){};class D(B,C){};let Alias=A;let d=D("x");try{throw d}catch Alias::{message:m}{[m,isinstance(d,C),class_of(d)==D]}'),['x',True,True])
        self.assertEqual(self.code('let Root=Exception;class Exception{};try{throw Root(42)}catch Exception::{}{0}catch Root::{value:v}{v}'),42)
        self.assertEqual(self.code('let C=new_class("Dynamic",[ValueError],{});try{throw C(42)}catch ValueError::{value:v}{v}'),42)

    def test_default_arguments_and_codes(self):
        self.assertEqual(self.code('let a=Exception();let b=ValueError(20,22);let c=ValueError("bad");[type_of(Exception),a.message,length(a.args),b.message,b.value==b.args,type_of(b.args),c.code,c.message,c.value]'),['Class','Exception',0,'ValueError',True,'Tuple','value_error','bad','bad'])
        self.assertEqual(self.code('class Custom(Exception){let code="custom"};try{throw Custom("failure")}catch e{[e.code,e.message]}'),['custom','failure'])
        for source in ['Exception(reason=1)','Exception.__new__(Type)']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.code(source)

    def test_normal_initializer_and_super(self):
        self.assertEqual(self.code('class Custom(Exception){var seen=0;fn __init__(self,*,reason){self.seen=42;super(Custom,self).__init__(reason);null}};let x=Custom(reason="failed");[x.seen,x.message,x.args[0],x.value]'),[42,'failed','failed','failed'])
        self.assertEqual(self.code('class Custom(Exception){fn __new__(cls,x){alloc_instance(cls)}fn __init__(self,x){super(Custom,self).__init__(x);null}};Custom("ok").message'),'ok')
        self.assertEqual(self.code('class Custom(Exception){fn __new__(cls){42}fn __init__(self){throw 0}};Custom()'),42)
        for source in ['class C(Exception){async fn __init__(self){null}};C()', 'class C(Exception){fn __init__(self){42}};C()', 'class C(Exception){let value:Int=1};C("bad")']:
            with self.subTest(source=source),self.assertRaises(LanguageError):self.code(source)

    def test_runtime_classification_uses_codes(self):
        for expression,kind in [('1/0','ZeroDivisionError'),('[1][4]','IndexError'),('{}["missing"]','KeyError'),('missing','RuntimeError')]:
            with self.subTest(expression=expression):self.assertTrue(self.code('try{'+expression+'}catch '+kind+'::{}{true}'))
        ev=Evaluator();ev.env.bind('fail',Native('fail',lambda:(_ for _ in ()).throw(LanguageError('ZeroDivisionError',code='opaque')),ev))
        self.assertTrue(ev.run('try{fail()}catch ZeroDivisionError::{}{false}catch RuntimeError::{}{true}'))

    def test_occurrence_and_instance_identities(self):
        self.assertEqual(self.code('let x=ValueError("x");let a=error(x);let b=error(x);try{throw a}catch e{[a!=b,a.instance==x,b.instance==x,e.value==x,current_error()==a]}'),[True]*5)
        for statement in ['throw','throw current_error()']:
            self.assertTrue(self.code('let e=error(ValueError("x"));try{try{throw e}catch x{'+statement+'}}catch x{current_error()==e}'))

    def test_legacy_record_nested_payload_and_cause(self):
        self.assertEqual(self.code('class C(ValueError){};let a=error(C("cause"));try{throw error(C("outer"),cause=a)}catch {value:C::{message:m},..rest}{[m,keys(rest),current_error().cause.instance.message]}'),['outer',['code','message','location'],'cause'])
        self.assertEqual(self.code('try{throw ValueError(42)}catch e{keys(e)}'),['code','message','value','location'])
        self.assertEqual(self.code('try{try{throw ValueError("inner")}catch e{throw TypeError("outer")}}catch TypeError::{}{current_error().context.instance.message}'),'inner')

    def test_runtime_views_are_fresh_and_do_not_mutate_error(self):
        self.assertEqual(self.code('let e=error(42);let a=e.instance;a.extra=7;let b=e.instance;[a!=b,hasattr(b,"extra"),e.value,isinstance(a,UserError)]'),[True,False,42,True])
        self.assertTrue(self.code('isinstance(error_group("g",[error(1)]).instance,Exception)'))

    def test_group_partition_guards_and_reraise(self):
        self.assertEqual(self.code('var out=[];try{throw error_group("g",[error(ValueError(20)),error(TypeError(22))])}catch* ValueError::{value:v} if v==20 as g{out=append(out,g.exceptions[0].instance.value)}catch* TypeError::{} as g{out=append(out,g.exceptions[0].instance.value)};out'),[20,22])
        self.assertEqual(self.code('let e=error(ValueError(42));try{try{throw error_group("g",[e])}catch* Exception::{} as g{throw}}catch x{current_error().exceptions[0]==e}'),True)

    def test_generator_and_async_generator_injection(self):
        self.assertEqual(self.code('fn* f(){try{yield 1}catch ValueError::{value:v}{yield v}};let g=f();next(g);throw_into(g,ValueError(42)).value'),42)
        self.assertEqual(self.code('async fn* f(){try{yield 1}catch ValueError::{value:v}{yield v}};let g=f();await anext(g);(await athrow(g,ValueError(42))).value'),42)
        self.assertEqual(self.code('class I{fn __iter__(self){self}fn __next__(self){{done:false,value:1}}};try{throw_into(I(),ValueError(42))}catch ValueError::{value:v}{v}'),42)

    def test_injection_and_delegation_reach_user_iterator_protocols(self):
        iterator='class I{fn __iter__(self){self}fn __next__(self){{done:false,value:1}}fn __throw__(self,x){{done:true,value:x.value}}};'
        self.assertEqual(self.code(iterator+'throw_into(I(),ValueError(42)).value'),42)
        self.assertEqual(self.code(iterator+'fn* f(){return yield from I()};let g=f();next(g);throw_into(g,ValueError(42)).value'),42)
        self.assertEqual(self.code('class I{async fn __aiter__(self){self}async fn __anext__(self){{done:false,value:1}}async fn __athrow__(self,x){{done:true,value:x.value}}};(await athrow(I(),ValueError(42))).value'),42)
        self.assertTrue(self.code('let e=error(42);fn* f(){try{yield 1}catch x{yield x.value==e}};let g=f();next(g);throw_into(g,e).value'))

    def test_async_isolation_and_group(self):
        self.assertEqual(self.code('async fn f(n){try{throw ValueError(n)}catch ValueError::{value:v}{await sleep(0);[v,current_error().instance.value,current_error().context]}};await gather([f(20),f(22)])'),[[20,20,None],[22,22,None]])
        self.assertEqual(self.code('async fn f(){throw ValueError(42)};try{async with task_group(errors="group") as g{g.spawn(f())}}catch* ValueError::{} as errors{errors.exceptions[0].instance.value}'),42)

    def test_resource_exit_uses_same_types_and_chain(self):
        self.assertEqual(self.code('class C{fn __enter__(self){null}fn __exit__(self,e){throw TypeError("exit")}};try{with C() as x{throw ValueError("body")}}catch TypeError::{}{current_error().context.instance.message}'),'body')

    def test_matching_does_not_execute_protocols(self):
        self.assertEqual(self.code('var calls=0;class M(Type){fn __instancecheck__(cls,x){calls+=1;true}};class C(Exception,metaclass=M){let extra=property(fn(self){calls+=1;42})};try{throw C("ok")}catch C::{extra:x}{0}catch Exception::{}{calls}'),0)
        self.assertEqual(self.code('var calls=0;class C(Exception){let message=property(fn(self){calls+=1;"bad"})};try{throw C()}catch ArgumentError::{}{calls}'),0)

    def test_builtin_sealing_and_reflection(self):
        for operation in ['Exception.extra=42','Exception.code="x"','delattr(Exception,"code")','ValueError.extra=1']:
            with self.subTest(operation=operation),self.assertRaises(LanguageError):self.code(operation)
        self.assertEqual(self.code('class C(Exception){var x=1};C.x=42;C().x'),42)
        from chaos.native_specs import SHAPES
        self.assertEqual(set(self.code('dir(error(1))')),{field.name for field in SHAPES['ErrorFields']})

    def test_foreign_instances_and_classes_rejected(self):
        other=Evaluator();value=other.run('ValueError(42)')
        for source,binding in [('throw foreign',value),('error(foreign)',value),('try{throw 1}catch foreign::{}{42}',other.exception_classes['Exception'])]:
            with self.subTest(source=source),self.assertRaises(LanguageError) as caught:Evaluator().run(source,{'foreign':binding})
            self.assertEqual(caught.exception.code,'ownership_error')

    def restricted(self):
        seen=[];ev=Evaluator(tool_handler=lambda *args:seen.append(args))
        ev.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),ev,capabilities=frozenset()))
        return ev,seen

    def test_runtime_instance_views_cannot_expand_authority(self):
        ev,seen=self.restricted()
        self.assertEqual(ev.run('fn secret(){tool("secret",0)};let e=error(secret);let wide=e.instance;let narrow=limited(fn(){e.instance});try{narrow.value()}catch e{e.code}'),'capability_error');self.assertEqual(seen,[])
        ev,seen=self.restricted()
        self.assertEqual(ev.run('fn secret(){tool("secret",0)};let e=limited(fn(){error(secret)});try{e.instance.value()}catch e{e.code}'),'capability_error');self.assertEqual(seen,[])

    def test_sealed_builtin_classes_are_authority_neutral_values(self):
        ev,seen=self.restricted()
        self.assertTrue(ev.run('limited(fn(){let e=Exception(Exception);try{throw e}catch Exception::{value:v}{v==Exception}})'))
        self.assertEqual(seen,[])

    def test_original_opaque_user_instance_cannot_cross_narrowing(self):
        for operation in ['x.instance','try{throw x}catch ValueError::{}{42}']:
            ev,seen=self.restricted()
            self.assertEqual(ev.run('let x=error(ValueError(42));try{limited(fn(){'+operation+'})}catch e{e.code}'),'capability_error');self.assertEqual(seen,[])
        ev,seen=self.restricted();self.assertEqual(ev.run('let x=limited(fn(){error(ValueError(42))});x.instance.value'),42)

    def test_hard_controls_are_not_selected_by_classes(self):
        for failure in [ResourceLimit('stop'),ExecutionControl('stop'),TaskCancelled('stop'),KeyboardInterrupt(),SystemExit(2)]:
            ev=Evaluator();ev.env.bind('fail',Native('fail',lambda:(_ for _ in ()).throw(failure),ev))
            with self.subTest(kind=type(failure).__name__),self.assertRaises(type(failure)):ev.run('try{fail()}catch Exception::{}{42}')
            self.assertEqual(ev.object_instance_count,0)
        self.assertEqual(self.code('class C(Exception){let code="resource_limit"};try{throw C(42)}catch Exception::{value:v}{v}'),42)

    def test_rejected_class_no_allocation_and_view_budget(self):
        ev=Evaluator();self.assertEqual(ev.run('try{1/0}catch TypeError::{}{0}catch e{42}'),42);self.assertEqual(ev.object_instance_count,0)
        ev.object_instance_count=9999;ev.run('error(1).instance');self.assertEqual(ev.object_instance_count,10000)
        with self.assertRaises(ResourceLimit):ev.run('error(1).instance')

    def test_legacy_underscore_binding_and_group_leaf_authority(self):
        self.assertEqual(self.code('try{throw 42}catch _{_.value}'),42)
        from chaos.errors import GroupedError
        ev,seen=self.restricted()
        callback=ev.run('fn secret(){tool("secret",0)};secret')
        leaf=LanguageError('leaf',value=callback);leaf._error_capabilities=frozenset()
        group=GroupedError('group',[leaf])
        ev.env.bind('fail',Native('fail',lambda:(_ for _ in ()).throw(group),ev))
        self.assertEqual(ev.run('try{try{fail()}catch Exception::{value:v}{v.exceptions[0].value()}}catch e{e.code}'),'capability_error')
        self.assertEqual(seen,[])

    def test_actual_agent_provider_failure_is_nominal(self):
        from chaos import Framework, Registry, Settings, ProviderSpec
        registry=Registry()
        def provider(request,context):raise ValueError('upstream unavailable')
        registry.register_provider(ProviderSpec('broken',provider))
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('nominal-agent',source='agent worker{provider:"broken",tools:[]};try{worker(1)}catch AgentError::{code:c}{[c,isinstance(current_error().instance,ProviderError)]}')
            self.assertEqual(app.start('nominal-agent',1,None,mode='source').result(8),['provider_error',True])

if __name__=='__main__':unittest.main()
