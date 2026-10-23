"""Finite witnesses for one ordinary-language interpreter of replaceable data.

These tests exercise an encoding, not a formal theorem about every program.
No provider, network, device or new runtime primitive is needed.
"""
import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from chaos import Evaluator,Framework,LanguageError,Settings
from chaos.language import ResourceLimit
from chaos.program import parse_program,format_program
from chaos.executable_image import compile_executable,load_executable

ROOT=Path(__file__).resolve().parents[1]
LIBRARY=ROOT.joinpath('examples/counter-machine.chaos').read_text()
FILES={'counter-machine.chaos':LIBRARY}
CLIENT='import "counter-machine.chaos" as M;M.run(input.program,input.a,input.b,input.pc,input.fuel)'
TRANSFER=[{'op':'decjz','register':'a','zero':2,'nonzero':1},{'op':'inc','register':'b','next':0},{'op':'halt'}]
LOOP=[{'op':'inc','register':'a','next':0}]


def state(a=0,b=0,pc=0,steps=0,status='halted'):
    return dict(status=status,pc=pc,a=a,b=b,steps=steps)


def payload(program,a=0,b=0,pc=0,fuel=100):
    return dict(program=copy.deepcopy(program),a=a,b=b,pc=pc,fuel=fuel)


CASES=[
    (payload([{'op':'halt'}],fuel=0),state()),
    (payload([{'op':'inc','register':'a','next':1},{'op':'halt'}]),state(a=1,pc=1,steps=1)),
    (payload([{'op':'inc','register':'b','next':1},{'op':'halt'}]),state(b=1,pc=1,steps=1)),
    (payload([{'op':'decjz','register':'a','zero':2,'nonzero':1},{'op':'inc','register':'b','next':2},{'op':'halt'}],b=3),state(b=3,pc=2,steps=1)),
    (payload([{'op':'decjz','register':'a','zero':2,'nonzero':1},{'op':'inc','register':'b','next':2},{'op':'halt'}],a=2,b=3),state(a=1,b=4,pc=2,steps=2)),
    (payload([{'op':'decjz','register':'b','zero':2,'nonzero':1},{'op':'inc','register':'a','next':2},{'op':'halt'}],a=3),state(a=3,pc=2,steps=1)),
    (payload([{'op':'decjz','register':'b','zero':2,'nonzero':1},{'op':'inc','register':'a','next':2},{'op':'halt'}],a=3,b=2),state(a=4,b=1,pc=2,steps=2)),
    (payload(TRANSFER,a=4,b=5),state(b=9,pc=2,steps=9)),
    # The same function honors arbitrary labels rather than falling through.
    (payload([{'op':'inc','register':'a','next':2},{'op':'inc','register':'b','next':3},{'op':'decjz','register':'a','zero':3,'nonzero':3},{'op':'halt'}]),state(pc=3,steps=2)),
    (payload(LOOP,fuel=5),state(a=5,steps=5,status='fuel_exhausted')),
]


class CounterMachine249(unittest.TestCase):
    def function(self):
        evaluator=Evaluator(step_limit=400000,timeout=30)
        function=evaluator.run('import "counter-machine.chaos" as M;M.run',files=FILES)
        self.addCleanup(evaluator.suspension.finish)
        return evaluator,function

    def invoke(self,evaluator,function,values):
        return evaluator.invoke(function,[values[name]for name in('program','a','b','pc','fuel')])

    def test_original_example_default_and_exported_machine_remain_compatible(self):
        self.assertEqual(Framework.evaluate(LIBRARY),{'a':0,'b':42})
        self.assertEqual(Framework.evaluate('import "counter-machine.chaos" as M;M.machine(0,37,5)',files=FILES),{'a':0,'b':42})
        self.assertEqual(Framework.evaluate('import "counter-machine.chaos" as M;M.machine(7,2,3)',files=FILES),{'a':2,'b':3})

    def test_one_actual_language_function_runs_every_replaced_instruction_table(self):
        evaluator,function=self.function();identity=id(function);body=function.body
        for values,expected in CASES:
            with self.subTest(program=values['program'],a=values['a'],b=values['b'],fuel=values['fuel']):
                before=copy.deepcopy(values)
                self.assertEqual(self.invoke(evaluator,function,values),expected)
                self.assertEqual(id(function),identity);self.assertIs(function.body,body)
                self.assertEqual(values,before);self.assertEqual(evaluator.frames,[]);self.assertEqual(evaluator.depth,0)

    def test_every_instruction_is_validated_including_unreachable_rows(self):
        evaluator,function=self.function()
        cases=[
            (payload([]),'empty_program'),
            (payload([{'op':'halt'},{'op':'unknown'}]),'invalid_opcode'),
            (payload([{'op':'halt'},42]),'invalid_instruction'),
            (payload([{'op':'halt','next':0}]),'invalid_instruction'),
            (payload([{'op':'inc','register':'c','next':0}]),'invalid_register'),
            (payload([{'op':'inc','register':None,'next':0}]),'invalid_register'),
            (payload([{'op':'inc','register':'a','next':True}]),'invalid_jump'),
            (payload([{'op':'inc','register':'a','next':1}]),'invalid_jump'),
            (payload([{'op':'decjz','register':'a','zero':0,'nonzero':-1}]),'invalid_jump'),
            (payload([{'op':'halt'}],pc=1),'invalid_jump'),
            (payload([{'op':'halt'}],a=-1),'invalid_state'),
            (payload([{'op':'halt'}],fuel=-1),'invalid_state'),
        ]
        for values,reason in cases:
            with self.subTest(reason=reason,input=values):
                before=copy.deepcopy(values)
                with self.assertRaises(LanguageError)as caught:self.invoke(evaluator,function,values)
                self.assertEqual(caught.exception.value['kind'],'counter_machine')
                self.assertEqual(caught.exception.value['reason'],reason)
                self.assertEqual(values,before)
        # Failed validation cannot corrupt a subsequent use of this same closure.
        self.assertEqual(self.invoke(evaluator,function,payload(TRANSFER,a=2,b=3)),state(b=5,pc=2,steps=5))

    def test_user_fuel_is_resumable_data_and_not_the_hard_runtime_budget(self):
        evaluator,function=self.function();values=payload(TRANSFER,a=4,b=5,fuel=4)
        partial=self.invoke(evaluator,function,values)
        self.assertEqual(partial,state(a=2,b=7,steps=4,status='fuel_exhausted'))
        resumed=self.invoke(evaluator,function,payload(TRANSFER,a=partial['a'],b=partial['b'],pc=partial['pc'],fuel=100))
        self.assertEqual(resumed,state(b=9,pc=2,steps=5))
        # Host exhaustion remains hard even when source catches ordinary errors.
        source='import "counter-machine.chaos" as M;try{M.run(input.program,0,0,0,100000)}catch e{"swallowed"}'
        with self.assertRaises(ResourceLimit):Framework.evaluate(source,{'input':payload(LOOP)},files=FILES,step_limit=5000)

    def test_source_IR_Framework_and_source_free_executable_share_results(self):
        # Parse all modules through public IR, then use their canonical source.
        formatted={name:format_program(parse_program(text,name))for name,text in FILES.items()}
        ir_client=format_program(parse_program(CLIENT))
        image=compile_executable(CLIENT,files=FILES)
        for values,expected in (CASES[7],CASES[8],CASES[9]):
            with self.subTest(input=values):
                self.assertEqual(Framework.evaluate(CLIENT,{'input':values},files=FILES,step_limit=100000),expected)
                self.assertEqual(Framework.evaluate(ir_client,{'input':values},files=formatted,step_limit=100000),expected)
                with ExitStack()as stack:
                    for target in('chaos.modules.ProgramBundle.__init__','chaos.language.Parser.program','chaos.modules.parse_unit','chaos.macros.expand_trees','chaos.program.parse_program'):
                        stack.enter_context(patch(target,side_effect=AssertionError('Executable entered source frontend: '+target)))
                    self.assertEqual(Framework.evaluate(load_executable(copy.deepcopy(image)),{'input':values},step_limit=100000),expected)

    def test_real_CLI_uses_the_same_module_and_replaced_JSON_program(self):
        with tempfile.TemporaryDirectory()as temporary:
            base=Path(temporary);base.joinpath('counter-machine.chaos').write_text(LIBRARY);main=base/'main.chaos';main.write_text(CLIENT)
            environment=dict(os.environ);environment['CHAOS_CONFIG']=str(base/'settings.json')
            for key in list(environment):
                if any(marker in key.upper()for marker in('API_KEY','APIKEY','ACCESS_TOKEN','AUTH_TOKEN')):environment.pop(key)
            for values,expected in(CASES[7],CASES[9]):
                result=subprocess.run([sys.executable,'-B','-m','chaos.cli','run',str(main),'--root',str(base),'--input-json',json.dumps(values)],cwd=ROOT,env=environment,text=True,capture_output=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout),expected)
