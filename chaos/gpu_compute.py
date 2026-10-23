"""Explicit Float32 bulk operations on optional GPU backends and portable CPU.

Only bounded, immutable arithmetic IR and copied numeric tuples reach workers.
This module imports no Torch package while creating or analyzing language code.
"""
import math
import struct
import sys
import threading

from .language import Function, LanguageError, ResourceLimit, Node
from . import gpu_ir

MAX_NODES, MAX_ELEMENTS, MAX_DIMENSION, MAX_OPERATIONS = 128, 10000, 256, 64
_DEVICE_LOCKS = {'mps':threading.Lock(), 'cuda':threading.Lock()}


def _error(message):
    return LanguageError(message,code='gpu_error')


def _check(cancel_event):
    if cancel_event.is_set():
        from .suspension import TaskCancelled
        raise TaskCancelled('GPU 计算任务已取消',_cancel_event=cancel_event)


def _float32(value):
    if type(value) not in (int,float): raise _error('Float32 输入需要有限 Number，不能为 Bool 或对象')
    try:
        result=struct.unpack('!f',struct.pack('!f',value))[0]
    except (OverflowError,struct.error):
        raise _error('数值超出有限 Float32 范围') from None
    if not math.isfinite(result): raise _error('Float32 输入和输出必须有限')
    return result


def _device(device):
    if type(device) is not str or device not in ('auto','gpu','cpu','mps','cuda'):
        raise _error('device 需要 auto、gpu、cpu、mps 或 cuda')
    return device


def _lower_expression(evaluator,function):
    if type(function) is not Function or function.owner is not evaluator:
        raise _error('gpu_map 需要当前执行上下文的普通 Function')
    if function.flavor!='fn' or type(function.params) is not list or len(function.params)!=1:
        raise _error('gpu_map 函数需要一个同步简单参数')
    parameter=function.params[0]
    if type(parameter) is not tuple or len(parameter)!=4:
        raise _error('gpu_map 不支持参数模式')
    name,contract,mode,default=parameter
    if type(name) is not str or len(name)>240 or type(contract) is not str or contract not in ('Any','Number') or mode not in ('positional','positional_only'):
        raise _error('gpu_map 参数需要 Number/Any 位置参数；Int 契约不能转换为 Float32')
    if type(function.returns) is not str or function.returns not in ('Any','Number'):
        raise _error('gpu_map 返回值需要 Number/Any 契约')
    aliases=function.macro_bindings or {}
    if type(aliases) is not dict or len(aliases)>1 or any(type(k) is not str or type(v) is not str or len(k)>240 or len(v)>240 for k,v in aliases.items()):
        raise _error('gpu_map 参数别名无效')
    name=aliases.get(name,name)
    count=[0]
    def enter(node,depth):
        evaluator.tick();count[0]+=1
        if count[0]>MAX_NODES or depth>64:raise _error('gpu_map 语法超过 128 节点或 64 层限制')
        if type(node) not in (Node,tuple) or not node:raise _error('gpu_map 语法无效')
    body=function.body;enter(body,0)
    if body[0]!='block' or len(body)!=2 or type(body[1]) is not list or len(body[1])!=1:
        raise _error('gpu_map 需要单个纯算术表达式或 return')
    expression=body[1][0]
    if type(expression) in (Node,tuple) and expression and expression[0]=='return':
        enter(expression,1)
        if len(expression)!=2:raise _error('gpu_map return 语法无效')
        expression=expression[1]
    def lower(node,depth):
        enter(node,depth);kind=node[0]
        if kind=='literal' and len(node)==2:return ('literal',_float32(node[1]))
        if kind=='name' and len(node)==2 and node[1]==name:return ('input',)
        if kind=='unary' and len(node)==3 and node[1] in ('+','-'):
            return ('unary',node[1],lower(node[2],depth+1))
        if kind=='binary' and len(node)==4 and node[1] in ('+','-','*'):
            return ('binary',node[1],lower(node[2],depth+1),lower(node[3],depth+1))
        raise _error('gpu_map 仅支持参数、有限数值和 + - *，不能调用工具或读取闭包')
    return lower(expression,2)



def _lower(evaluator,function):
    try:return _lower_expression(evaluator,function)
    except LanguageError as original:
        if original.code != 'gpu_error':raise
        if type(function) is not Function or function.owner is not evaluator or function.flavor!='fn':raise
        if type(function.params) is not list or len(function.params)!=1 or type(function.params[0]) is not tuple or len(function.params[0])!=4:raise
        name,contract,mode,default=function.params[0]
        if type(name) is not str or len(name)>240 or type(contract) is not str or contract not in ('Any','Number') or mode not in ('positional','positional_only'):raise
        if type(function.returns) is not str or function.returns not in ('Any','Number'):raise
        aliases=function.macro_bindings or {}
        if type(aliases) is not dict or len(aliases)>1 or any(type(k) is not str or type(v) is not str or len(k)>240 or len(v)>240 for k,v in aliases.items()):raise
        try:return gpu_ir.lower(evaluator,function.body,aliases.get(name,name))
        except ValueError as error:raise _error(str(error)) from None


def _vector(evaluator,values):
    if type(values) is not list or len(values)>MAX_ELEMENTS:raise _error('gpu_map values 需要至多 10000 项 List')
    result=[]
    for value in values:
        evaluator.tick();result.append(_float32(value))
    return tuple(result)


def _matrix(evaluator,values):
    if type(values) is not list or not values or len(values)>MAX_DIMENSION:
        raise _error('矩阵需要 1–256 行的非空二维 List')
    width=None;result=[];count=0
    for row in values:
        evaluator.tick()
        if type(row) is not list or not row or len(row)>MAX_DIMENSION:
            raise _error('矩阵每行需要 1–256 个数值')
        if width is None:width=len(row)
        if width!=len(row):raise _error('矩阵必须为矩形')
        count+=len(row)
        if count>MAX_ELEMENTS:raise _error('矩阵元素超过 10000')
        copied=[]
        for value in row:evaluator.tick();copied.append(_float32(value))
        result.append(tuple(copied))
    return tuple(result)


def _operation(payload,kernel_info=None):
    from .suspension import CHECKPOINT,Effect
    yield CHECKPOINT
    try:result=yield Effect('gpu',payload)
    except LanguageError as error:
        evidence=getattr(error,'_gpu_kernel_evidence',None)
        if kernel_info is not None and type(evidence) is dict:kernel_info.update(evidence)
        raise
    if kernel_info is not None:
        kernel_info.update(result['kernel'])
        return result['result']
    return result


def _task(evaluator,payload,kernel_info=None):
    scheduler=evaluator.suspension
    if not scheduler.active:raise _error('GPU Task 必须在活动执行周期内创建')
    if scheduler.compute_operations>=MAX_OPERATIONS:raise ResourceLimit('GPU 操作 Task 每周期最多 64 个')
    evaluator.tick();scheduler.compute_operations+=1
    task=scheduler.task(_operation(payload,kernel_info),kind='compute',_compute_operation=True)
    if kernel_info is not None:task._gpu_kernel_evidence=kernel_info
    return task


def _compiler(compiler):
    if type(compiler) is not str or compiler not in ('tensor','auto','metal'):
        raise _error('compiler 需要 tensor、auto 或 metal')
    return compiler


def _pending(compiler,ir):
    from .gpu_metal import identity
    return {'requestedCompiler':compiler,'compiler':None,'irHash':identity(ir),
            'sourceHash':None,'sourceCacheHit':None,'dispatches':None,'isolated':None,'fallbackReason':None}


def gpu_map(evaluator,function,values,*,device='auto',compiler='tensor'):
    selected=_device(device);compiler=_compiler(compiler);ir=_lower(evaluator,function);copied=_vector(evaluator,values)
    return _task(evaluator,{'operation':'map','ir':ir,'values':copied,'device':selected,'compiler':compiler},_pending(compiler,ir))


def gpu_kernel_info(evaluator,task):
    from .suspension import Task
    scheduler=evaluator.suspension;scheduler.validate(task,Task);scheduler._recording_access(task)
    evidence=getattr(task,'_gpu_kernel_evidence',None)
    if type(evidence) is not dict:raise _error('gpu_kernel_info 仅接受原始 gpu_map Task')
    return evaluator.copy_value({**evidence,'status':task.state})


def gpu_matmul(evaluator,left,right,*,device='auto'):
    selected=_device(device);a=_matrix(evaluator,left);b=_matrix(evaluator,right)
    if len(a[0])!=len(b):raise _error('矩阵乘法维度不匹配')
    if len(a)*len(b[0])>MAX_ELEMENTS:raise _error('矩阵输出超过 10000 元素')
    return _task(evaluator,{'operation':'matmul','left':a,'right':b,'device':selected})


def gpu_devices(evaluator):
    return _task(evaluator,{'operation':'devices','device':'auto'})


def builtins(evaluator):
    return {'gpu_map':lambda fn,values,*,device='auto',compiler='tensor':gpu_map(evaluator,fn,values,device=device,compiler=compiler),
            'gpu_kernel_info':lambda task:gpu_kernel_info(evaluator,task),
            'gpu_matmul':lambda left,right,*,device='auto':gpu_matmul(evaluator,left,right,device=device),
            'gpu_devices':lambda:gpu_devices(evaluator)}


def _load_torch(cancel_event):
    _check(cancel_event)
    if sys.version_info<(3,10):return None
    try:import torch
    except (ImportError,OSError,RuntimeError):return None
    _check(cancel_event)
    return torch


def _availability(torch):
    if torch is None:return False,False
    return bool(torch.cuda.is_available()),bool(torch.backends.mps.is_available())


def _select(torch,requested):
    cuda,mps=_availability(torch)
    if requested=='cpu':return 'cpu',None
    if requested in ('auto','gpu'):
        if cuda:return 'cuda',None
        if mps:return 'mps',None
        if requested=='gpu':raise _error('没有可用 GPU；gpu 请求不能降级为 CPU')
        return 'cpu','backend_unavailable' if torch is None else 'no_gpu'
    if torch is None or requested=='cuda' and not cuda or requested=='mps' and not mps:
        raise _error('请求的 '+requested+' GPU 后端不可用')
    return requested,None


def _cpu_map(ir,values,cancel_event):
    if gpu_ir.is_program(ir):return gpu_ir.cpu(ir,values,lambda:_check(cancel_event))
    def evaluate(node,value):
        _check(cancel_event);kind=node[0]
        if kind=='literal':return node[1]
        if kind=='input':return value
        left=evaluate(node[2],value)
        if kind=='unary':return _float32(left if node[1]=='+' else -left)
        right=evaluate(node[3],value)
        return _float32(left+right if node[1]=='+' else left-right if node[1]=='-' else left*right)
    result=[]
    for value in values:_check(cancel_event);result.append(evaluate(ir,value))
    return result


def _cpu_matmul(left,right,cancel_event):
    result=[];work=0
    for row in left:
        output=[]
        for column in range(len(right[0])):
            value=0.0
            for k,item in enumerate(row):
                work+=1
                if work%128==0:_check(cancel_event)
                value=_float32(value+_float32(item*right[k][column]))
            output.append(value)
        _check(cancel_event);result.append(output)
    return result


def _acquire(lock,cancel_event):
    while not lock.acquire(timeout=.05):_check(cancel_event)
    try:_check(cancel_event)
    except BaseException:lock.release();raise


def _synchronize(torch,device):
    if device=='mps':torch.mps.synchronize()
    elif device=='cuda':torch.cuda.synchronize(torch.device('cuda:0'))


def _torch_result(torch,payload,device,cancel_event):
    lock=_DEVICE_LOCKS[device];_acquire(lock,cancel_event)
    target='cuda:0' if device=='cuda' else device
    try:
        with torch.no_grad():
            _check(cancel_event)
            if payload['operation']=='map':
                values=torch.tensor(payload['values'],dtype=torch.float32,device=target)
                def evaluate(ir):
                    _check(cancel_event);kind=ir[0]
                    if kind=='literal':return torch.full_like(values,ir[1])
                    if kind=='input':return values
                    left=evaluate(ir[2]);_check(cancel_event)
                    if kind=='unary':return left if ir[1]=='+' else -left
                    right=evaluate(ir[3]);_check(cancel_event)
                    return left+right if ir[1]=='+' else left-right if ir[1]=='-' else left*right
                result=(gpu_ir.tensor(torch,payload['ir'],values,lambda:_check(cancel_event))
                        if gpu_ir.is_program(payload['ir']) else evaluate(payload['ir']))
            else:
                left=torch.tensor(payload['left'],dtype=torch.float32,device=target)
                _check(cancel_event)
                right=torch.tensor(payload['right'],dtype=torch.float32,device=target)
                _check(cancel_event);result=torch.matmul(left,right)
            _check(cancel_event)
            actual=str(result.device)
            if not bool(torch.isfinite(result).all().item()):raise _error('GPU 输出必须为有限 Float32')
            _check(cancel_event);output=result.detach().cpu().tolist();_check(cancel_event)
            return {'values':output,'device':actual,'backend':'torch','dtype':'float32','fallbackReason':None}
    finally:
        # Submitted GPU work must drain even if its logical Task was cancelled.
        try:_synchronize(torch,device)
        finally:lock.release()


def _devices(torch,cancel_event):
    cuda,mps=_availability(torch);devices=['cpu'];selected='cuda' if cuda else 'mps' if mps else 'cpu'
    # Availability is a probe; only the selected default gets an actual tiny
    # Tensor allocation to report the backend's canonical device identity.
    if cuda:devices.append('cuda:0')
    if mps:devices.append('mps:0')
    actual='cpu'
    if selected!='cpu':
        lock=_DEVICE_LOCKS[selected];_acquire(lock,cancel_event)
        try:
            _check(cancel_event)
            actual=str(torch.empty(0,dtype=torch.float32,device='cuda:0' if selected=='cuda' else selected).device)
            _check(cancel_event)
        finally:
            try:_synchronize(torch,selected)
            finally:lock.release()
    return {'backend':'torch' if torch is not None else 'python',
            'version':str(torch.__version__) if torch is not None else None,
            'cpu':True,'mps':mps,'cuda':cuda,'devices':devices,'defaultDevice':actual}


def _metal_result(payload,cancel_event,info):
    from .gpu_metal import execute,MetalFailure,MetalCancelled,source_identity
    lock=_DEVICE_LOCKS['mps'];_acquire(lock,cancel_event)
    info.update(compiler='metal',sourceHash=source_identity(payload['ir']),sourceCacheHit=False,isolated=True)
    try:
        try:
            values,device,dispatches,source_hash=execute(payload['ir'],payload['values'],cancel_event)
        except MetalCancelled:
            _check(cancel_event)
            raise _error('Metal 子进程取消失败')
        except MetalFailure as error:
            info['dispatches']=error.dispatches
            if payload['compiler']=='auto' and error.phase=='compile' and error.dispatches==0:
                _check(cancel_event)
                info.update(compiler='tensor',fallbackReason='metal_compile_failed')
                return None
            raise _error('Metal 后端计算失败: '+str(error)) from None
        info['dispatches']=dispatches
        return {'values':values,'device':device,'backend':'torch','dtype':'float32','fallbackReason':None}
    finally:lock.release()


def _run(payload,cancel_event,info):
    requested=payload['device']
    torch=None if requested=='cpu' else _load_torch(cancel_event)
    _check(cancel_event)
    if payload['operation']=='devices':return _devices(torch,cancel_event)
    device,fallback=_select(torch,requested);_check(cancel_event)
    if info is not None:
        compiler=payload.get('compiler','tensor')
        available=device=='mps' and callable(getattr(getattr(torch,'mps',None),'compile_shader',None))
        if compiler=='metal' and not available:raise _error('compiler=metal 需要真实 MPS 和 compile_shader，不能降级')
        if compiler in ('metal','auto') and available:
            result=_metal_result(payload,cancel_event,info)
            if result is not None:return result
            # _metal_result released the MPS lock before this tensor fallback.
        elif compiler=='auto':info['fallbackReason']='non_mps_device' if device!='mps' else 'metal_unavailable'
        info.update(compiler='tensor',isolated=False,dispatches=0 if device=='cpu' else None)
    if device!='cpu':return _torch_result(torch,payload,device,cancel_event)
    output=(_cpu_map(payload['ir'],payload['values'],cancel_event) if payload['operation']=='map'
            else _cpu_matmul(payload['left'],payload['right'],cancel_event))
    _check(cancel_event)
    return {'values':output,'device':'cpu','backend':'python','dtype':'float32','fallbackReason':fallback}


def run(payload,cancel_event):
    """Internal worker: returns detached evidence; only AST commits Task state."""
    _check(cancel_event)
    info=_pending(payload.get('compiler','tensor'),payload['ir']) if payload['operation']=='map' and 'compiler' in payload else None
    try:
        result=_run(payload,cancel_event,info)
        return {'result':result,'kernel':info} if info is not None else result
    except (LanguageError,ResourceLimit) as error:
        if info is not None:error._gpu_kernel_evidence=dict(info)
        raise
    except (RuntimeError,ValueError,TypeError,OverflowError) as error:
        failure=_error('GPU 后端计算失败: '+str(error))
        if info is not None:failure._gpu_kernel_evidence=dict(info)
        raise failure from None
