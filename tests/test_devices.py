"""Native Device lifecycle and authority with deterministic registered adapters only."""
import unittest,threading,time
from chaos import Framework,Registry
from chaos.language import Evaluator,LanguageError,ResourceLimit,Native
from chaos.devices import DeviceSpec
from chaos.device_values import Device,DeviceAction,DeviceReceipt,Lease,builtins,project,owned
from chaos.agent_tools import scope

class Adapter:
    def __init__(self,log,fail=None):self.log=log;self.fail=fail
    def handle(self,id,spec,ctx):
        self.log.append(('act',spec))
        if self.fail:raise self.fail
        return {'protocol':'chaos-hid/1','id':id,'status':'acknowledged'}
    def close(self):self.log.append(('close',))

def host(log,fail=None):
    r=Registry()
    def factory(ctx):log.append(('open',));return Adapter(log,fail)
    r.register_device(DeviceSpec('desk',factory,resource_id='fixture-endpoint'))
    r.register_device(DeviceSpec('alias',factory,resource_id='fixture-endpoint'))
    return Framework(registry=r)

def run(f,source,caps=None):
    p=f.create_project('p'+__import__('uuid').uuid4().hex,source=source,capabilities=['device.control'] if caps is None else caps)
    return f.start(p['id'],p['version'],None,mode='source').result()

class Devices(unittest.TestCase):
    def test_registry_snapshot_abi_and_duplicate_names(self):
        r=Registry();self.assertEqual(len(r.snapshot()),2);spec=DeviceSpec('desk',lambda c:None)
        self.assertIs(r.register_device(spec),spec);self.assertEqual(list(r.device_snapshot()),['desk'])
        with self.assertRaises(ValueError):r.register_device(spec)
    def test_cold_open_does_not_acquire(self):
        log=[]
        with host(log) as f:self.assertEqual(run(f,'let pending=device_open("desk");type_of(pending)'),'Task')
        self.assertEqual(log,[])
    def test_callable_async_scope_receipts_and_normalized_action(self):
        log=[]
        with host(log) as f:
            result=run(f,'async with await device_open("desk") as d{let a=device_action(d,{action:"key_tap",keys:[4]});let r=await d(a);[type_of(d),type_of(a),type_of(r),device_receipt_info(r).status,device_action_info(a).used]}')
        self.assertEqual(result,['Device','DeviceAction','DeviceReceipt','acknowledged',True]);self.assertEqual([x[0] for x in log],['open','act','close'])
        self.assertEqual(log[1][1],{'action':'key_tap','keys':[4],'modifiers':0})
    def test_action_and_act_task_are_cold_until_await(self):
        log=[]
        with host(log) as f:
            result=run(f,'let d=await device_open("desk");let a=device_action(d,{action:"release"});let t=device_act(d,a);[device_action_info(a).used,type_of(t)]')
        self.assertEqual(result,[False,'Task']);self.assertEqual([x[0] for x in log],['open','close'])
    def test_action_one_use_and_alias_exclusivity(self):
        log=[]
        with host(log) as f:
            result=run(f,'let d=await device_open("desk");let a=device_action(d,{action:"release"});await d(a);[try{await d(a)}catch e{e.code},try{await device_open("alias")}catch e{e.code}]')
        self.assertEqual(result,['device_error','device_busy']);self.assertEqual(sum(x[0]=='act' for x in log),1)
    def test_close_idempotent_and_explicit_new_session(self):
        log=[]
        with host(log) as f:
            result=run(f,'let a=await device_open("desk");await device_close(a);await device_close(a);let b=await device_open("alias");device_info(a).id!=device_info(b).id')
        self.assertTrue(result);self.assertEqual(sum(x[0]=='close' for x in log),2)
    def test_uncertain_receipt_consumes_action_and_invalidates(self):
        log=[]
        with host(log,LanguageError('lost ack')) as f:
            result=run(f,'let d=await device_open("desk");let a=device_action(d,{action:"release"});let error=try{await d(a)}catch e{e.value};[error.status,error.targetVerified,device_action_info(a).used,device_info(d).status,try{await d(a)}catch e{e.code}]')
        self.assertEqual(result,['uncertain',False,True,'uncertain','device_error']);self.assertEqual(sum(x[0]=='act' for x in log),1)
    def test_no_permission_and_reserved_tool_deny(self):
        log=[]
        with host(log) as f:
            result=run(f,'try{await device_open("desk")}catch e{e.code}',[]);self.assertEqual(result,'capability_error')
            self.assertEqual(run(f,'try{tool("device.control",{})}catch e{e.code}'),'program_error')
        self.assertEqual(log,[])
    def test_native_values_and_action_projection_share_lease_and_consumption(self):
        e=Evaluator();lease=Lease(e,e.suspension.epoch,{'id':'x','name':'desk','generation':1,'profile':'chaos-hid/1','status':'open'})
        d=Device(e,lease);a=builtins(e)['device_action'](d,{'action':'release'})
        ceiling=type('Ceiling',(),{'capabilities':frozenset()})()
        narrow=project(e,a,ceiling);narrow_d=project(e,d,ceiling)
        self.assertIs(narrow.lease,lease);self.assertIs(narrow.admission,a.admission);self.assertEqual(narrow.capabilities,frozenset())
        with self.assertRaises(LanguageError):owned(d,Evaluator())
        e.suspension.epoch+=1
        for value in (d,a):
            with self.assertRaises(LanguageError):owned(value,e)
    def test_nested_return_cannot_widen_device_or_action(self):
        e=Evaluator();calls=[]
        def handler(op,payload):
            calls.append(op)
            return {'id':'x','name':'desk','generation':1,'profile':'chaos-hid/1','status':'open'}
        e.device_handler=handler
        e.env.bind('limited',Native('limited',lambda f:e.invoke(f,[]),e,capabilities=frozenset()))
        result=e.run('let d=await device_open("desk");let box=limited(fn(){[d,device_action(d,{action:"release"})]});try{await device_act(box[0],box[1])}catch e{e.code}')
        self.assertEqual(result,'capability_error');self.assertEqual(calls,['open'])
    def test_foreign_and_stale_hidden_values_reject(self):
        from chaos.class_creation import validate_value
        from chaos.indexing import validate_owned
        e=Evaluator();other=Evaluator();d=Device(other,Lease(other,other.suspension.epoch,{'id':'x'}))
        for validate in (validate_value,validate_owned):
            with self.assertRaises(LanguageError):validate(e,[{'hidden':d}])
    def test_invalid_action_preflight_never_dispatches(self):
        log=[]
        with host(log) as f:
            result=run(f,'let d=await device_open("desk");[try{device_action(d,{action:"mouse_move",x:128,y:0})}catch e{e.code},try{device_action(d,{action:"key_tap",keys:[4,4]})}catch e{e.code}]')
        self.assertEqual(result,['argument_error','argument_error']);self.assertEqual([x[0] for x in log],['open','close'])
    def test_original_compile_and_closure_paths(self):
        log=[]
        with host(log) as f:
            result=run(f,'let d=await device_open("desk");let a=device_action(d,{action:"release"});let call=compile_function(fn(d,a){d(a)},target="auto-flow");device_receipt_info(await call(d,a)).status')
        self.assertEqual(result,'acknowledged')
    def test_sync_with_rejected_and_host_absence_no_effect(self):
        with self.assertRaises(LanguageError):Evaluator().run('await device_open("desk")')
        log=[]
        with host(log) as f:self.assertEqual(run(f,'let d=await device_open("desk");try{with d as x{42}}catch e{e.code}'),'argument_error')
        self.assertEqual([x[0] for x in log],['open','close'])

if __name__=='__main__':unittest.main()

class DeviceBoundaryRegression(unittest.TestCase):
    def test_raw_receipt_status_identity_and_generation_validation(self):
        metadata={'id':'lease','name':'desk','generation':1,'status':'open','profile':'chaos-hid/1'}
        good={'schema':'chaos.device-receipt/1','id':'receipt','deviceId':'lease','name':'desk','generation':1,'action':'release','status':'acknowledged','targetVerified':False}
        for operation,patch in [('act',{'status':'closed'}),('act',{'generation':2}),('act',{'deviceId':'other'}),('act',{'schema':'bad'}),('close',{'status':'acknowledged'}),('close',{'name':'other'})]:
            with self.subTest(operation=operation,patch=patch):
                receipt=dict(good,action='close',status='closed') if operation=='close' else dict(good)
                receipt.update(patch)
                e=Evaluator(device_handler=lambda op,p:metadata if op=='open' else receipt)
                expression='device_close(d)' if operation=='close' else 'd(a)'
                result=e.run('let d=await device_open("desk");let a=device_action(d,{action:"release"});let code=try{await '+expression+'}catch e{e.code};[code,device_info(d).status,device_action_info(a).used]')
                self.assertEqual(result,['device_error','uncertain',False] if operation=='close' else ['device_error','uncertain',True])
    def test_failed_open_cleanup_quarantines_endpoint(self):
        from types import SimpleNamespace
        from chaos.device_host import DeviceHost
        from chaos.suspension import TaskCancelled
        log=[]
        class Broken(Adapter):
            def close(self):log.append(('close-failed',));raise RuntimeError('close failed')
        engine=SimpleNamespace(device_specs={'desk':DeviceSpec('desk',lambda c:Broken(log),resource_id='physical'),'alias':DeviceSpec('alias',lambda c:Broken(log),resource_id='physical')},device_lock=threading.RLock(),device_leases={})
        def cancelled():raise TaskCancelled('after factory')
        ctx=SimpleNamespace(engine=engine,budget=SimpleNamespace(tick=cancelled),extension_context=lambda k:None)
        host=DeviceHost(ctx)
        with self.assertRaises(TaskCancelled):host.open(ctx,'desk')
        self.assertEqual(len(engine.device_leases),1)
        with self.assertRaisesRegex(LanguageError,'already leased'):host.open(ctx,'alias')
        self.assertEqual(log,[('close-failed',)])
    def test_cancel_parent_await_drains_fresh_device_operation(self):
        from chaos.suspension import TaskCancelled
        started=threading.Event();drained=threading.Event()
        metadata={'id':'lease','name':'desk','generation':1,'status':'open','profile':'chaos-hid/1'}
        def effect(kind,p,cancel):
            if kind=='tool':self.assertTrue(started.wait(1));return None
            if p['operation']=='open':return metadata
            started.set()
            try:
                self.assertTrue(cancel.wait(1));raise TaskCancelled('cancelled device')
            finally:drained.set()
        e=Evaluator(device_handler=lambda *args:None,tool_handler=lambda *args:None,effect_handler=effect)
        with self.assertRaises(TaskCancelled):e.run('let d=await device_open("desk");let a=device_action(d,{action:"release"});async fn parent(){await d(a)}let p=spawn(parent());tool("wait",null);cancel(p);await p')
        self.assertTrue(drained.is_set());self.assertIsNone(e.suspension.pool);self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)

    def test_failed_close_blocks_actions_until_explicit_cleanup_retry(self):
        log=[]
        class RetryAdapter(Adapter):
            def __init__(self):super().__init__(log);self.closes=0
            def close(self):
                self.closes+=1;log.append(('close',self.closes))
                if self.closes==1:raise LanguageError('cleanup uncertain',code='device_error')
        registry=Registry()
        registry.register_device(DeviceSpec('desk',lambda ctx:RetryAdapter(),resource_id='endpoint'))
        registry.register_device(DeviceSpec('alias',lambda ctx:Adapter(log),resource_id='endpoint'))
        with Framework(registry=registry) as f:
            value=run(f,'let d=await device_open("desk");let a=device_action(d,{action:"release"});let failed=try{await device_close(d)}catch e{e.code};let state=device_info(d).status;let denied=try{await d(a)}catch e{e.code};let alias=try{await device_open("alias")}catch e{e.code};await device_close(d);let replacement=await device_open("alias");[failed,state,denied,alias,device_info(d).status,device_info(replacement).status,device_action_info(a).used]')
        self.assertEqual(value,['device_error','uncertain','device_error','device_busy','closed','open',False])
        self.assertEqual(sum(row[0]=='act' for row in log),0)
