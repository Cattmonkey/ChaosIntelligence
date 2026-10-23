"""Explicit roots for live suspended expression state; no host-frame inspection."""
import unittest
from chaos.language import Evaluator,Native,TailCall,ResourceLimit,ExecutionControl

PREFIX='let base=generic_method(generic_function("g"),["Any"],fn(x){tool("fixture",x)});\nlet g=generic_method(base,["Int"],fn(next,x){next},with_next=true);\nvar saved=null;let sink=limited(fn(){fn(item){saved=item;0}});\n'
TASK_CASES=[('awaited-anonymous-child', 'await (async fn(resume){await sleep(0.05);resume})(g(42))', 'result()'), ('list-prior-element', '[g(42),await sleep(0.05)]', 'result[0]()'), ('tuple-prior-element', '(g(42),await sleep(0.05))', 'result[0]()'), ('record-prior-field', '{resume:g(42),delay:await sleep(0.05)}', 'result.resume()'), ('call-prior-argument', '(fn(resume,delay){resume})(g(42),await sleep(0.05))', 'result()'), ('call-prior-keyword', '(fn(*,resume,delay){resume})(resume=g(42),delay=await sleep(0.05))', 'result()'), ('binary-left-value', '[g(42)] + do{await sleep(0.05);[]}', 'result[0]()'), ('index-target', '[g(42)][do{await sleep(0.05);0}]', 'result()'), ('completed-child-result', '[await (async fn(){g(42)})(),await sleep(0.05)]', 'result[0]()'), ('native-hash-pair-before-wait', 'hash_map([("resume",g(42)),("delay",await sleep(0.05))])', 'hash_get(result,"resume")()')]
GENERATOR_CASES=[('suspended-generator-list', 'fn* outer(){[g(42),yield null]}\n let stream=outer();next(stream);sink(stream);let result=next(saved).value;\n try{result[0]()}catch error{error.code}'), ('suspended-generator-call', 'fn* outer(){(fn(resume,item){resume})(g(42),yield null)}\n let stream=outer();next(stream);sink(stream);let result=next(saved).value;\n try{result()}catch error{error.code}'), ('suspended-async-generator-list', 'async fn* outer(){[g(42),yield null]}\n let stream=outer();await anext(stream);sink(stream);let result=(await anext(saved)).value;\n try{result[0]()}catch error{error.code}'), ('awaiting-async-generator-operation', 'async fn* outer(){yield ([g(42),await sleep(0.05)])}\n let pending=anext(outer());spawn(pending);await sleep(0.005);sink(pending);\n let result=(await saved).value;try{result[0]()}catch error{error.code}'), ('materialized-comprehension-retained-first-result', 'async fn outer(){[if x==0 {g(42)} else {await sleep(0.05);null} for x in [0,1]]}\n let pending=outer();spawn(pending);await sleep(0.005);sink(pending);\n let result=await saved;try{result[0]()}catch error{error.code}'), ('lazy-comprehension-suspended-element', 'let stream=async ([g(42),await sleep(0.05)] for x in [0]);\n let pending=anext(stream);spawn(pending);await sleep(0.005);sink(pending);\n let result=(await saved).value;try{result[0]()}catch error{error.code}')]
SUFFIX='\nlet pending=outer();spawn(pending);await sleep(0.005);\nlet state=task_info(pending).state;sink(pending);let result=await saved;\n[state,try{INVOKE}catch error{error.code}]\n'

class CaptureFramesTests(unittest.TestCase):
    def execute(self,source):
        calls=[];e=Evaluator(step_limit=1000000,tool_handler=lambda *args:calls.append(args) or 42)
        e.env.bind('limited',Native('limited',lambda f:TailCall(f,[]),e,capabilities=frozenset()))
        value=e.run(PREFIX+source)
        self.assertEqual(calls,[])
        self.assertEqual(e.depth,0)
        self.assertEqual(e.frames,[])
        return value

    def test_suspended_expression_operands(self):
        for name,expression,invoke in TASK_CASES:
            with self.subTest(name=name):
                source='async fn outer(){'+expression+'}'+SUFFIX.replace('INVOKE',invoke)
                self.assertEqual(self.execute(source),['running','capability_error'])

    def test_generator_and_comprehension_operands(self):
        for name,source in GENERATOR_CASES:
            with self.subTest(name=name):self.assertEqual(self.execute(source),'capability_error')

    def test_loops_and_tail_dispatch_drop_consumed_history(self):
        from chaos.capture_frames import holder
        e=Evaluator(step_limit=1000000);counts=[]
        def sample():
            frames=holder(e).capture_frames
            counts.append((len(frames),sum(len(frame.roots) for frame in frames)))
        e.env.bind('sample',Native('sample',sample,e))
        self.assertEqual(e.run('var i=0;while i<1500{[i];sample();i=i+1};fn f(n){if n==0{42}else{sample();f(n-1)}};f(1500)'),42)
        self.assertLess(max(count for count,roots in counts),30)
        self.assertLess(max(roots for count,roots in counts),100)

    def test_frame_cleanup_survives_parent_root_resource_limit(self):
        from chaos.capture_frames import Frame,leave
        from chaos.suspension import Runner
        e=Evaluator();runner=Runner(iter(()));parent=Frame(e,runner,'parent');child=Frame(e,runner,'child')
        parent.roots=[object() for _ in range(20000)];parent.identities={id(v) for v in parent.roots}
        runner.capture_frames=[parent,child]
        with self.assertRaises(ResourceLimit):leave(child,object())
        self.assertEqual(runner.capture_frames,[parent]);self.assertEqual(child.roots,[])

    def test_frame_cleanup_does_not_replace_hard_control(self):
        from chaos.capture_frames import Frame,leave
        from chaos.suspension import Runner
        e=Evaluator();runner=Runner(iter(()));parent=Frame(e,runner,'parent');child=Frame(e,runner,'child')
        parent.roots=[object() for _ in range(20000)];parent.identities={id(v) for v in parent.roots}
        runner.capture_frames=[parent,child]
        leave(child,ExecutionControl('stop'))
        self.assertEqual(runner.capture_frames,[parent]);self.assertEqual(child.roots,[])

    def test_runtime_control_restores_execution_frames(self):
        e=Evaluator()
        def stop():raise ExecutionControl('stop')
        e.env.bind('stop',Native('stop',stop,e))
        with self.assertRaises(ExecutionControl):e.run('fn f(){[fn(){0},stop()]};f()')
        self.assertEqual(e.depth,0);self.assertEqual(e.frames,[])
        self.assertTrue(all(not task.runner.capture_frames for task in e.suspension.tasks))

    def test_pending_capture_edge_keeps_its_own_authority(self):
        from chaos.capture_frames import Edge
        from chaos.capture_graph import seal
        from chaos.generic_functions import NextDescriptor
        e=Evaluator();capsule=NextDescriptor(e,None)
        token=Native('next',lambda:0,e,generic=capsule,captures=())
        seal(e,[Edge(e,frozenset(),(token,))],None)
        self.assertEqual(capsule.capabilities,frozenset())

if __name__=='__main__':unittest.main()
