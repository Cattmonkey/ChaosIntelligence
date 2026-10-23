"""Color real lexical roles without evaluating the edited program."""
import bisect
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from chaos.ide_language import analyze,SourceMap
from chaos.language import Evaluator


class SemanticParameterRoles(unittest.TestCase):
    def analysis(self,source,**kwargs):
        with ExitStack() as stack:
            for name in ('run','invoke','_s_invoke','_s_evaluate'):
                stack.enter_context(patch.object(Evaluator,name,side_effect=AssertionError('Editor analysis executed language code')))
            result=analyze(source,**kwargs)
        self.assertTrue(result['complete'],result['diagnostics'])
        self.assertEqual(result['diagnostics'],[])
        return result

    def roles(self,source,result,name):
        mapping=SourceMap(source)
        return [token['type'] for token in result['tokens']
                if source[bisect.bisect_left(mapping.units,token['from']):bisect.bisect_left(mapping.units,token['to'])]==name]

    def test_function_parameter_declaration_and_all_references_keep_parameter_role(self):
        source='// 🙂 UTF-16 offsets\nfn apply(callback:Function){callback(41);callback(42)}'
        result=self.analysis(source)
        self.assertEqual(self.roles(source,result,'callback'),['parameter','parameter','parameter'])
        self.assertEqual(self.roles(source,result,'apply'),['function'])
        self.assertEqual(self.roles(source,result,'Function'),['type'])

    def test_shadowed_function_and_native_names_follow_actual_parameter_scope(self):
        source='fn callback(value:Int){value};fn apply(callback:Function){callback(1)};callback(2);fn use(length:Function){length(1)};length([1,2])'
        result=self.analysis(source)
        self.assertEqual(self.roles(source,result,'callback'),['function','parameter','parameter','function'])
        self.assertEqual(self.roles(source,result,'length'),['parameter','parameter','function'])

    def test_function_aliases_and_destructured_functions_keep_function_role(self):
        source='fn increment(value:Int){value+1};let transform=increment;let [operation]=[transform];operation(41);transform(41)'
        result=self.analysis(source)
        self.assertEqual(set(self.roles(source,result,'increment')),{'function'})
        self.assertEqual(set(self.roles(source,result,'transform')),{'function'})
        self.assertEqual(self.roles(source,result,'operation'),['function','function'])
        self.assertEqual(self.roles(source,result,'value'),['parameter','parameter'])

    def test_fresh_alias_of_parameter_keeps_value_metadata_not_parameter_identity(self):
        source='fn apply(callback:Function,value:Int){let invoke=callback;let amount=value;invoke(amount)}'
        result=self.analysis(source)
        self.assertEqual(self.roles(source,result,'callback'),['parameter','parameter'])
        self.assertEqual(self.roles(source,result,'invoke'),['function','function'])
        self.assertEqual(self.roles(source,result,'amount'),['variable','variable'])
        self.assertEqual(self.roles(source,result,'value'),['parameter','parameter'])

    def test_function_parameter_inside_method_keeps_member_and_type_roles(self):
        source='class Worker{fn run(self,callback:Function){callback(41)}};let worker=Worker();worker.run(fn(value){value+1})'
        result=self.analysis(source)
        self.assertEqual(self.roles(source,result,'callback'),['parameter','parameter'])
        self.assertEqual(self.roles(source,result,'run'),['function','function'])
        self.assertEqual(self.roles(source,result,'Worker'),['type','type'])
        self.assertEqual(self.roles(source,result,'worker'),['variable','variable'])

    def test_record_member_does_not_borrow_stored_parameter_lexical_role(self):
        source='fn apply(callback:Function){let record={handler:callback,answer:42};record.handler(41);record.answer}'
        result=self.analysis(source)
        self.assertEqual(self.roles(source,result,'callback'),['parameter','parameter'])
        self.assertEqual(self.roles(source,result,'handler'),['property','function'])
        self.assertEqual(self.roles(source,result,'answer'),['property','property'])

    def test_imported_functions_and_their_own_callback_parameters_are_inert(self):
        library='fn apply(callback:Function,value:Int){callback(value)};export{apply}'
        source='import "callbacks.chaos" as F;F.apply(fn(value:Int){value+1},41)'
        files={'main.chaos':source,'callbacks.chaos':library}
        result=self.analysis(source,files=files)
        self.assertEqual(self.roles(source,result,'apply'),['function'])
        imported=self.analysis(library,file='callbacks.chaos',entry='main.chaos',files=files)
        self.assertEqual(self.roles(library,imported,'callback'),['parameter','parameter'])
        self.assertEqual(self.roles(library,imported,'value'),['parameter','parameter'])

    def test_destructuring_parameter_bindings_keep_parameter_role(self):
        source='fn apply(input as [callback]:List){callback(41)};fn read(input as {handler:work}:Record){work(42)}'
        result=self.analysis(source)
        self.assertEqual(self.roles(source,result,'callback'),['parameter','parameter'])
        self.assertEqual(self.roles(source,result,'work'),['parameter','parameter'])
        self.assertEqual(self.roles(source,result,'handler'),['property'])

    def test_effectful_descriptors_and_tools_are_only_analyzed(self):
        source='class Trap{fn __get__(self,obj,owner){throw "must not execute"}};class Worker{let operation=Trap();fn use(self,callback:Function){callback(1)}};let worker=Worker();worker.operation;tool("must.not.run",42)'
        result=self.analysis(source)
        self.assertEqual(self.roles(source,result,'callback'),['parameter','parameter'])
        self.assertEqual(self.roles(source,result,'operation'),['property','property'])


if __name__=='__main__':unittest.main()
