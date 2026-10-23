"""One data-only source shared by artifact SDK/CLI/HTTP acceptance."""
SOURCE = "var effects=0;fn raw(x){effects+=1;let [a,b]=[x,2];match a{n if n>0=>n+b;_=>0}};let f=compile_function(raw,target=\"auto-flow\");[f(input),effects]"
EXPECTED = [42, 1]
INPUT = 40

ISOLATED = r'''import json,os,runpy,socket,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve();sys.path.insert(0,str(root))
assert sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
assert not any('site-packages'in p for p in sys.path)
def forbidden(*args,**kwargs):raise RuntimeError('Artifact surface network access forbidden')
socket.socket.connect=forbidden;socket.socket.connect_ex=forbidden;socket.create_connection=forbidden
import chaos
assert Path(chaos.__file__).resolve()==root/'chaos/__init__.py'
if sys.argv[2]=='cli':
    sys.argv=['chaos']+sys.argv[3:]
    runpy.run_module('chaos',run_name='__main__')
else:
    from unittest.mock import patch
    from chaos.compiled_flow_state import FlowState
    artifact=json.loads(Path(sys.argv[3]).read_text())
    states=[];original=FlowState.__init__
    def entered(state,*args,**kwargs):original(state,*args,**kwargs);states.append(state)
    bundle=chaos.load_artifact(artifact)
    with patch.object(FlowState,'__init__',entered):result=chaos.Framework.evaluate(bundle,bindings={'input':40})
    assert result==[42,1] and states
    assert all(state.program._artifact_decoded and state.program._artifact_hash==artifact['artifactHash'] and state.program.resume_calls>0 and state.closed and not state.frames and not state._owned_activations and not state._transfers and state.pending_cursor is None and state.pending_request is None for state in states)
    print(json.dumps({'result':result,'version':chaos.__version__,'sourceRoot':str(root),'actualFlowStates':len(states),'resumes':[s.program.resume_calls for s in states],'artifactHash':artifact['artifactHash'],'allDecoded':True,'allClosed':True,'networkAllowed':False}))
'''
