import json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from chaos.ide_language import analyze
from chaos.language import Parser
from chaos.framework import Framework
from chaos.extensions import DEFAULT_CAPABILITIES
from chaos.openapi import specification
ROOT=Path(__file__).resolve().parent.parent


class RegisteredComputerSurfacesTests(unittest.TestCase):
    def test_editor_uses_shared_native_metadata(self):
        source='let c=await computer_attach("phone");let answer=computer_info(c);answer'
        result=analyze(source)
        self.assertEqual(result['diagnostics'],[])
        labels={v['label']for v in analyze(source+'.')['completions']};self.assertTrue({'backend','binding','profile','actions'}<=labels)
        self.assertEqual(Framework.evaluate('type_of(computer_attach("phone"))'),'Task')
    def test_validation_is_explicit_and_does_not_grant_authority(self):
        with tempfile.TemporaryDirectory()as folder,Framework(Path(folder)/'db')as app:
            spec={'action':'input','ref':'e1','text':'append'}
            with self.assertRaises(Exception):app.validate_computer_action(spec)
            self.assertEqual(app.validate_computer_action(spec,registered=True),spec)
            with self.assertRaises(Exception):app.validate_computer_action(spec,registered=1)
            self.assertNotIn('computer.control',DEFAULT_CAPABILITIES)
    def test_openapi_default_and_extended_branches(self):
        schemas=specification()['components']['schemas'];branches=schemas['ComputerActionValidation']['oneOf']
        self.assertEqual(branches[0]['properties']['registered'],{'const':False})
        self.assertEqual(branches[1]['required'],['spec','registered'])
        self.assertEqual(branches[1]['properties']['registered'],{'const':True})
        self.assertEqual(len(schemas['ComputerActionSpec']['oneOf']),5)
        self.assertEqual(len(schemas['RegisteredComputerActionSpec']['oneOf']),9)
    @unittest.skipUnless(shutil.which('node'),'Node unavailable')
    def test_mobile_snippet_requires_current_explicit_ref_and_checks_image_geometry(self):
        script=r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');global.window={};global.document={querySelector:()=>null};global.stringifyJSON=JSON.stringify;global.parseJSON=JSON.parse;vm.runInThisContext(fs.readFileSync(process.argv[1]+'/web/computers.js','utf8'));const c=window.ChaosComputerContracts;let o={backend:'registered',viewport:{width:390,height:844}};assert(c.overlaySafe(o,{width:780,height:1688}));assert(!c.overlaySafe(o,{width:1688,height:780}));let s=c.snippet({action:'input',ref:'historic-ref',text:'hello'},o,{});assert(s.includes('target_ref: String'));assert(!s.includes('historic-ref'));process.stdout.write(s);'''
        result=subprocess.run([shutil.which('node'),'-e',script,str(ROOT)],check=True,capture_output=True,text=True,timeout=10)
        Parser(result.stdout).program();self.assertEqual(analyze(result.stdout)['diagnostics'],[])
