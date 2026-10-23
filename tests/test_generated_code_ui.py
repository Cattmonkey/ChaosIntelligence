"""Generated source views preserve original copy/import data with or without highlighting."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which('node')


@unittest.skipUnless(NODE, 'Node.js unavailable')
class GeneratedCodeUI(unittest.TestCase):
    def run_js(self, body):
        setup = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const root=process.argv[1],mode=fs.readFileSync(root+'/web/modes.js','utf8'),computer=fs.readFileSync(root+'/web/computers.js','utf8');
const extract=(source,start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
const raw='// 🙂 <tag>\nlet value = "keep   spacing";\nvalue\n';
class Element {
 constructor(tag,text=''){this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];this.attributes={};}
 append(...children){this.children.push(...children)}
 replaceChildren(...children){this.children=children}
 setAttribute(key,value){this.attributes[key]=value}
 focus(){this.focused=true}
 querySelector(selector){return selector==='code'?this.children.find(x=>x.tagName==='CODE')||null:null}
}
const el=(tag,text='')=>new Element(tag,text);
(async()=>{
'''
        result = subprocess.run([NODE, '-e', setup + body + '\n})().catch(error=>{console.error(error);process.exitCode=1});', str(ROOT)],
                                capture_output=True, text=True, cwd=ROOT, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mode_validation_highlight_and_plain_fallback_are_lossless(self):
        self.run_js(r'''
for(const enhanced of [true,false]){
 const preview=el('pre'),calls=[],draft={key:'plan',serial:0,definition:{kind:'plan'}};
 const context=vm.createContext({document:{activeElement:null},state:{draft},form:{reportValidity:()=>true},copy:x=>x,draftMessage:()=>{},request:async()=>({source:raw,definitionHash:'abcdef0123456789'}),currentDraft:()=>true,preview,window:{ChaosCode:enhanced?{update:(node,text,options)=>calls.push({node,text,options})}:undefined}});
 vm.runInContext(extract(mode,'  async function validateDraft()','  async function saveDraft'),context);
 await context.validateDraft();assert.equal(preview.hidden,false);
 if(enhanced){assert.equal(calls.length,1);assert.equal(calls[0].node,preview);assert.equal(calls[0].text,raw);assert.equal(calls[0].options.language,'chaos');}
 else assert.equal(preview.textContent,raw);
 assert.equal(draft.validated.source,raw);
}
''')

    def test_export_uses_one_view_and_original_copy_download_import(self):
        self.run_js(r'''
for(const enhanced of [true,false]){
 const output=el('div'),buttons=[],copied=[],imports=[],downloads=[],selection={removeAllRanges(){},addRange(range){this.text=range.target.textContent}},calls=[];
 const data={source:raw,revision:1},ideState={file:'main.chaos',revision:1,source:'1'};
 const window={ChaosIDE:{state:ideState,importModeSource:async(text,name)=>imports.push({text,name})},getSelection:()=>selection};
 if(enhanced)window.ChaosCode={render:(text,options)=>{calls.push({text,options});const pre=el('pre'),code=el('code',text);pre.append(code);return pre;}};
 const context=vm.createContext({window,document:{createRange:()=>({selectNodeContents(target){this.target=target}})},navigator:{clipboard:{writeText:async text=>copied.push(text)}},contracts:{policy:()=>({blocked:false})},state:{project:'p',generation:1},exportRequest:0,copy:x=>JSON.parse(JSON.stringify(x)),currentProject:()=> 'p',exportBody:output,exportDialog:{open:false,showModal(){this.open=true},close(){this.open=false}},request:async()=>data,base:()=>'/modes',el:(tag,text)=>{const item=el(tag,text);if(tag==='textarea')item.select=()=>{selection.text=item.value};if(tag==='a')item.click=()=>{};return item;},button:(text,fn)=>{const item={text,fn};buttons.push(item);return item;},note:()=>{},URL:{createObjectURL:blob=>{downloads.push(blob.parts[0]);return 'blob:test'},revokeObjectURL(){}},Blob:class{constructor(parts){this.parts=parts}},setTimeout:fn=>fn()});
 vm.runInContext(extract(mode,'  async function exportArtifact(','  async function refresh('),context);
 await context.exportArtifact({kind:'plan',key:'example',revision:1});
 const views=output.children.filter(item=>['PRE','TEXTAREA'].includes(item.tagName));assert.equal(views.length,1);assert.equal(views[0].tagName,enhanced?'PRE':'TEXTAREA');
 await buttons.find(item=>item.text==='复制源码').fn();assert.deepEqual(copied,[raw]);
 if(enhanced){views[0].querySelector('code').textContent='formatted display';window.ChaosCode.update=(pre,text,options)=>{assert.equal(options.analyze,false);pre.querySelector('code').textContent=text;};}
 context.navigator.clipboard.writeText=async()=>{throw Error('denied')};await buttons.find(item=>item.text==='复制源码').fn();assert.equal(selection.text,raw);
 await buttons.find(item=>item.text==='下载模块').fn();assert.deepEqual(downloads,[raw]);
 await buttons.find(item=>item.text==='加入 IDE 新模块草稿').fn();assert.deepEqual(imports,[{text:raw,name:'example'}]);
 if(enhanced)assert.equal(calls[0].text,raw);else assert(views[0].readOnly);
}
''')

    def test_computer_preview_refresh_and_invalidation_share_original_source(self):
        self.run_js(r'''
for(const enhanced of [true,false]){
 const calls=[],code=el(enhanced?'pre':'textarea'),state={draft:{source:raw}},importButton={};
 const context=vm.createContext({state,code,importButton,window:{ChaosCode:enhanced?{update:(node,text,options)=>calls.push({node,text,options})}:undefined}});
 vm.runInContext(extract(computer,' function updateEditor()',' function renderEditor()'),context);context.updateEditor();assert.equal(importButton.disabled,false);
 if(enhanced){assert.equal(calls[0].text,raw);assert.equal(calls[0].options.analyze,true);}else assert.equal(code.value,raw);
 state.draft.source=null;context.updateEditor();assert.equal(importButton.disabled,true);
 if(enhanced){assert.equal(calls[1].text,'');assert.equal(calls[1].options.analyze,false);}else assert.equal(code.value,'');
}
''')


if __name__ == '__main__':
    unittest.main()
