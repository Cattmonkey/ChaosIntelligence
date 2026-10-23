"""Trusted bounded Float32 IR compiler and isolated Metal worker.

Importing this module uses only the standard library.  Its fixed executable
entry accepts numeric JSON, never Python source, shaders, paths or handles.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time

try:
    from . import gpu_ir
except ImportError:
    # The fixed script and runpy test worker both load the trusted sibling.
    import importlib.util
    _ir_spec=importlib.util.spec_from_file_location('_chaos_gpu_ir',Path(__file__).with_name('gpu_ir.py'))
    gpu_ir=importlib.util.module_from_spec(_ir_spec);_ir_spec.loader.exec_module(gpu_ir)

MAX_BYTES=1048576
MAX_NODES=128
MAX_ELEMENTS=10000
WORKER_TIMEOUT=30.0


class MetalFailure(Exception):
    def __init__(self,message,*,phase='unknown',dispatches=None,source_hash=None):
        super().__init__(message)
        self.phase=phase;self.dispatches=dispatches;self.source_hash=source_hash


class MetalCancelled(Exception):pass


def _float32(value):
    if type(value) not in (int,float):raise ValueError('Float32 requires finite numeric values')
    try:value=struct.unpack('!f',struct.pack('!f',value))[0]
    except (OverflowError,struct.error):raise ValueError('Float32 range exceeded') from None
    if not math.isfinite(value):raise ValueError('Float32 must be finite')
    return value


def _bits(value):return struct.unpack('!I',struct.pack('!f',_float32(value)))[0]


def canonical(ir):
    if gpu_ir.is_program(ir):return gpu_ir.canonical(ir)
    count=[0]
    def visit(node,depth):
        count[0]+=1
        if count[0]>MAX_NODES or depth>64:raise ValueError('IR exceeds bounded syntax')
        if type(node) not in (tuple,list) or not node:raise ValueError('Invalid IR node')
        kind=node[0]
        if kind=='input' and len(node)==1:return ('input',)
        if kind=='literal' and len(node)==2:return ('literal',_bits(node[1]))
        if kind=='unary' and len(node)==3 and node[1] in ('+','-'):
            return ('unary',node[1],visit(node[2],depth+1))
        if kind=='binary' and len(node)==4 and node[1] in ('+','-','*'):
            return ('binary',node[1],visit(node[2],depth+1),visit(node[3],depth+1))
        raise ValueError('Unsupported numeric IR')
    return visit(ir,0)


def identity(ir):
    encoded=json.dumps(canonical(ir),separators=(',',':'),ensure_ascii=True).encode('ascii')
    prefix=b'chaos.metal.float32-program/2\0' if gpu_ir.is_program(ir) else b'chaos.metal.float32-ir/1\0'
    return hashlib.sha256(prefix+encoded).hexdigest()


def source(ir):
    if gpu_ir.is_program(ir):return gpu_ir.source(ir)
    tree=canonical(ir);lines=[]
    def visit(node):
        kind=node[0]
        if kind=='input':expression='input[index]'
        elif kind=='literal':expression='as_type<float>(0x%08xu)'%node[1]
        elif kind=='unary':expression=node[1]+visit(node[2])
        else:
            left=visit(node[2]);right=visit(node[3]);expression=left+' '+node[1]+' '+right
        name='t'+str(len(lines));lines.append((name,expression));return name
    last=visit(tree)
    body=[]
    for name,expression in lines:
        body.append('    volatile float '+name+' = '+expression+';')
        # Bit checks remain meaningful even with an external fast-math policy.
        body.append('    if ((as_type<uint>(float('+name+')) & 0x7f800000u) == 0x7f800000u) { output[index] = as_type<float>(0x7fc00000u); return; }')
    return ('#include <metal_stdlib>\nusing namespace metal;\n'
            '#pragma clang fp contract(off)\n#pragma clang fp reassociate(off)\n'
            'kernel void chaos_map(device const float* input [[buffer(0)]], device float* output [[buffer(1)]], constant int& count [[buffer(2)]], uint index [[thread_position_in_grid]]) {\n'
            '    if (index >= uint(count)) return;\n'+'\n'.join(body)+'\n    output[index] = '+last+';\n}\n')


def source_identity(ir):return hashlib.sha256(source(ir).encode('ascii')).hexdigest()


def _check(event):
    if event.is_set():raise MetalCancelled('Metal computation cancelled')


def _read_pipe(pipe,result,limit):
    try:result.append(pipe.read(limit+1))
    except (OSError,ValueError):result.append(b'')


def execute(ir,values,cancel_event):
    """Compile/execute in one disposable process; cancellation drains and reaps."""
    _check(cancel_event)
    payload={'schema':1,'ir':ir,'values':values,'irHash':identity(ir),'sourceHash':source_identity(ir)}
    encoded=json.dumps(payload,allow_nan=False,separators=(',',':')).encode('ascii')+b'\n'
    if len(encoded)>MAX_BYTES:raise MetalFailure('Metal input exceeds bounded JSON')
    child=None;threads=[];output=[];errors=[];cancel_sent=False
    deadline=time.monotonic()+WORKER_TIMEOUT
    try:
        child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker'],stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               env={'PATH':'/usr/bin:/bin','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'})
        for pipe,result,limit in ((child.stdout,output,MAX_BYTES),(child.stderr,errors,16384)):
            thread=threading.Thread(target=_read_pipe,args=(pipe,result,limit),daemon=True)
            threads.append(thread);thread.start()
        descriptor=child.stdin.fileno();os.set_blocking(descriptor,False)
        pending=encoded;written=0
        while child.poll() is None:
            if cancel_event.is_set() and not cancel_sent:
                pending+=b'{"cancel":true}\n';cancel_sent=True
            if output and len(output[0])>MAX_BYTES or errors and len(errors[0])>16384:
                raise MetalFailure('Metal worker exceeded bounded pipes; device drain is unverified')
            if time.monotonic()>deadline:
                raise MetalFailure('Metal worker timeout; device drain is unverified')
            if written<len(pending):
                try:written+=os.write(descriptor,pending[written:written+65536])
                except BlockingIOError:pass
                except BrokenPipeError:pass
            try:child.wait(timeout=.01)
            except subprocess.TimeoutExpired:pass
        for thread in threads:thread.join(1)
        _check(cancel_event)
        raw=output[0] if output else b''
        if child.returncode!=0 or not raw or len(raw)>MAX_BYTES:
            raise MetalFailure('Metal worker exited or violated bounded response; device drain is unverified')
        try:packet=json.loads(raw)
        except (ValueError,UnicodeError):raise MetalFailure('Invalid Metal worker response; device drain is unverified') from None
        if type(packet) is not dict or set(packet)!= {'ok','phase','dispatches','sourceHash','device','values','error'}:
            raise MetalFailure('Invalid Metal worker response schema')
        if packet['sourceHash']!=payload['sourceHash'] or type(packet['ok']) is not bool or packet['phase'] not in ('compile','execute','done','cancelled'):
            raise MetalFailure('Invalid Metal worker identity or phase')
        dispatches=packet['dispatches']
        if type(dispatches) is not int or dispatches not in (0,1):raise MetalFailure('Invalid Metal dispatch evidence')
        if not packet['ok']:
            message=packet['error']
            if type(message) is not str or len(message)>2000:raise MetalFailure('Invalid Metal error evidence')
            raise MetalFailure(message,phase=packet['phase'],dispatches=dispatches,source_hash=packet['sourceHash'])
        result=packet['values']
        if packet['error'] is not None or packet['phase']!='done' or packet['device']!='mps:0' or type(result) is not list or len(result)!=len(values) or dispatches!=int(bool(values)):
            raise MetalFailure('Invalid Metal completion evidence')
        for value in result:
            if type(value) is not float or not math.isfinite(value) or _float32(value)!=value:raise MetalFailure('Invalid Metal Float32 output')
        return result,packet['device'],dispatches,payload['sourceHash']
    except (BrokenPipeError,OSError) as error:
        raise MetalFailure('Metal worker IPC failed; device drain is unverified') from error
    finally:
        if child is not None:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=1)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=1)
            else:child.wait()
            for pipe in (child.stdin,child.stdout,child.stderr):
                if pipe is not None:
                    try:pipe.close()
                    except OSError:pass
            for thread in threads:thread.join(1)


def _request(stream):
    # Raw descriptor reads avoid a daemon holding BufferedReader at shutdown.
    raw=b'';advisory=b''
    if hasattr(stream,'fileno'):
        try:descriptor=stream.fileno()
        except (OSError,ValueError):descriptor=None
    else:descriptor=None
    if descriptor is None:raw=stream.readline(MAX_BYTES+1)
    else:
        while b'\n' not in raw and len(raw)<=MAX_BYTES:
            chunk=os.read(descriptor,65536)
            if not chunk:break
            raw+=chunk
        if b'\n' in raw:raw,advisory=raw.split(b'\n',1)
    if len(raw)>MAX_BYTES:raise ValueError('Input exceeds bounded JSON')
    packet=json.loads(raw)
    if type(packet) is not dict or set(packet)!= {'schema','ir','values','irHash','sourceHash'} or type(packet['schema']) is not int or packet['schema']!=1:
        raise ValueError('Invalid request schema')
    ir=packet['ir'];values=packet['values']
    if type(values) is not list or len(values)>MAX_ELEMENTS:raise ValueError('Input element limit exceeded')
    copied=[_float32(value) for value in values]
    if packet['irHash']!=identity(ir) or packet['sourceHash']!=source_identity(ir):raise ValueError('IR/source identity mismatch')
    return ir,copied,packet['sourceHash'],advisory


def _advisories(event,initial=b''):
    data=initial
    try:
        while True:
            if b'\n' in data:
                line,data=data.split(b'\n',1)
                if line==b'{"cancel":true}':event.set();return
            if len(data)>100:return
            chunk=os.read(sys.stdin.fileno(),100)
            if not chunk:return
            data+=chunk
    except (OSError,ValueError):return


def worker():
    phase='compile';dispatches=0;source_hash=None;event=threading.Event();torch=None;submitted=False
    packet={'ok':False,'phase':phase,'dispatches':0,'sourceHash':None,'device':None,'values':None,'error':None}
    try:
        ir,values,source_hash,advisory=_request(sys.stdin.buffer)
        reader=threading.Thread(target=_advisories,args=(event,advisory),daemon=True);reader.start()
        _check(event)
        import torch
        _check(event)
        if not torch.backends.mps.is_available() or not callable(getattr(torch.mps,'compile_shader',None)):
            raise RuntimeError('MPS Metal compiler unavailable')
        library=torch.mps.compile_shader(source(ir));kernel=library.chaos_map
        _check(event);phase='execute'
        with torch.no_grad():
            inputs=torch.tensor(values,dtype=torch.float32,device='mps')
            output=torch.empty_like(inputs)
            fault_buffer=torch.zeros(len(values),dtype=torch.int32,device='mps') if gpu_ir.is_program(ir) else None
            _check(event)
            if values:
                # Mark before submission: a throwing dispatch is never replayed.
                submitted=True;dispatches=1
                if fault_buffer is not None:kernel(inputs,output,len(values),fault_buffer,threads=len(values),arg_casts={2:'int32'})
                else:kernel(inputs,output,len(values),threads=len(values),arg_casts={2:'int32'})
            _check(event)
            if fault_buffer is not None:
                codes=fault_buffer.detach().cpu().tolist()
                for lane,code in enumerate(codes):
                    if code:raise gpu_ir.ProgramFault(lane,code)
            actual=str(output.device);result=output.detach().cpu().tolist()
            _check(event)
            if any(not math.isfinite(value) for value in result):raise ValueError('GPU intermediate/output must be finite Float32')
            packet.update(ok=True,phase='done',device=actual,values=result)
    except MetalCancelled:
        packet.update(phase='cancelled',error='Metal computation cancelled')
    except Exception as error:
        packet.update(phase=phase,error=(type(error).__name__+': '+str(error))[:2000])
    finally:
        if torch is not None and phase!='compile':
            try:torch.mps.synchronize()
            except Exception as error:packet.update(ok=False,phase='execute',values=None,error=('Metal synchronization failed: '+str(error))[:2000])
        packet.update(dispatches=dispatches,sourceHash=source_hash)
        print(json.dumps(packet,allow_nan=False,separators=(',',':')),flush=True)


if __name__=='__main__':
    if sys.argv[1:]==['--worker']:worker()
    else:raise SystemExit('This is an internal fixed numeric worker')
