import unittest
from chaos.language import Evaluator
from chaos.jit import analyze, prove, Expression, Unsupported
from chaos.scalar_optimizer import optimize, OptimizationError, MIN_I64

class ScalarOptimizer(unittest.TestCase):
    def plan(self,source):
        analysis=analyze(Evaluator().run(source));prove(analysis)
        return analysis,optimize(analysis.expression,analysis.prefix)
    def test_cse_preserves_duplicate_checkpoints(self):
        a,p=self.plan('fn(x){(x+1)*(x+1)}')
        self.assertEqual(p.info()['originalOperations'],3);self.assertEqual(p.info()['emittedOperations'],2)
        self.assertEqual(p.info()['commonSubexpressions'],1)
        self.assertEqual(len(p.checkpoints),len(a.spans));self.assertEqual(len(set(p.checkpoints)),len(a.spans))
    def test_constants_fold_without_erasing_logical_events(self):
        a,p=self.plan('fn(x){x+(2*3)+(2*3)}')
        self.assertEqual(p.info()['emittedOperations'],2);self.assertEqual(p.info()['constantFolds'],1)
        self.assertEqual(len(p.checkpoints),len(a.spans))
    def test_nontrivial_dependence_scheduling(self):
        a,p=self.plan('fn(a,b,c,d,e,f){(a+b)+(((c*d)*e)*f)}')
        self.assertTrue(p.info()['scheduled']);self.assertEqual(p.info()['emittedOperations'],5)
        order=[p.values[i].value for i in p.schedule if p.values[i].kind=='binary']
        self.assertEqual(order[0],'*');self.assertNotEqual(p.info()['originalOrderHash'],p.info()['scheduledOrderHash'])
    def test_topology_respects_all_dependencies(self):
        _,p=self.plan('fn(x,y){(x+y)*(x+y)+(x*y)}');seen=set()
        for i in p.schedule:
            self.assertTrue(set(p.values[i].children)<=seen);seen.add(i)
        self.assertIn(p.result,seen)
    def test_identity_removal_is_exact_integer_only(self):
        for s in ['fn(x){+x+0}','fn(x){x*1}','fn(x){x*0}','fn(x){x-0}']:
            a,p=self.plan(s);self.assertEqual(p.info()['emittedOperations'],0)
            self.assertEqual(len(p.checkpoints),len(a.spans))
    def test_safe_fold_comparison_and_unary(self):
        e=Expression('unary','-',(Expression('literal',6,(),1),),0,2)
        p=optimize(e);self.assertEqual(p.values[p.result].value,-6)
        p=optimize(Expression('binary','<',(Expression('literal',2,(),1),Expression('literal',3,(),2)),0,3))
        self.assertEqual(p.values[p.result].value,1)
    def test_original_range_proof_not_optimized_away(self):
        a=analyze(Evaluator().run('fn(x){(9223372036854775807+1)*0}'))
        with self.assertRaises(Unsupported):prove(a)
    def test_invalid_and_effect_ir_rejected(self):
        bad=[Expression('call','context_get',(),0),Expression('literal',True,(),0),Expression('name',True,(),0),Expression('literal',1.0,(),0)]
        for e in bad:
            with self.assertRaises(OptimizationError):optimize(e)
    def test_metadata_is_defensive_and_frozen(self):
        _,p=self.plan('fn(x){(x+1)*(x+1)}');a=p.info();a['emittedOperations']=999
        self.assertEqual(p.info()['emittedOperations'],2)
        with self.assertRaises(AttributeError):p.result=99
    def test_position_identity_and_determinism(self):
        _,a=self.plan('fn(x,y){x-y}');_,b=self.plan('fn(left,right){left-right}')
        self.assertEqual(a.info()['valueGraphHash'],b.info()['valueGraphHash'])
        self.assertEqual(a.info(),optimize(analyze(Evaluator().run('fn(x,y){x-y}')).expression,(0,)).info())
    def test_no_integer_reassociation(self):
        _,a=self.plan('fn(x,y,z){(x+y)+z}');_,b=self.plan('fn(x,y,z){x+(y+z)}')
        self.assertNotEqual(a.info()['valueGraphHash'],b.info()['valueGraphHash'])
    def test_unreachable_computation_removed_but_tape_retained(self):
        a,p=self.plan('fn(x){(x*x)*0}');self.assertEqual(p.info()['emittedOperations'],0)
        self.assertEqual(len(p.checkpoints),len(a.spans));self.assertEqual(p.values[p.result].value,0)
