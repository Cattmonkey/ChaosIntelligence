"""Functional policies share original call/stream/Code/Program pathways."""
import unittest
from chaos import Framework,Registry,Settings,ToolSpec
from chaos.language import LanguageError
from chaos.robots import RobotSpec
from test_robot_values import host,run,Adapter,ACTUATORS


class RobotIntegrationTests(unittest.TestCase):
    def test_original_async_stream_policy_and_task_group(self):
        log=[]
        with host(log) as app:
            value=run(app,'async with await robot_open("rig") as r{async fn* packets(){for n in [1,2,3]{yield await robot_observe(r)}};async fn policy(){async for o in packets(){await r(robot_command(o,{pan:0,tilt:0}))};42};async with task_group() as g{await g.spawn(policy())}}')
        self.assertEqual(value,42);self.assertEqual(sum(x[0]=='observe' for x in log),3);self.assertEqual(sum(x[0]=='act' for x in log),3)

    def test_apply_partial_code_and_compiled_flow_receiver(self):
        log=[]
        with host(log) as app:
            value=run(app,'let r=await robot_open("rig");let o=await robot_observe(r);let c=robot_command(o,{pan:0,tilt:0});let generated=function_from(code_from("fn(r,c){r(c)}"));let call=compile_function(generated,target="auto-flow");let pending=apply(partial(call,r),[c]);robot_receipt_info(await pending).status')
        self.assertEqual(value,'sent');self.assertEqual(sum(x[0]=='act' for x in log),1)

    def test_multiple_values_preserve_native_handles_without_tuple_wrapper(self):
        log=[]
        with host(log) as app:
            value=run(app,'let r=await robot_open("rig");let o=await robot_observe(r);let values=multiple_value_list(fn(){return_values(r,o)});[type_of(values[0]),type_of(values[1]),robot_observation_info(values[1]).sequence]')
        self.assertEqual(value,['Robot','RobotObservation',1])

    def test_top_level_native_results_export_descriptors(self):
        log=[]
        with host(log) as app:
            value=run(app,'let r=await robot_open("rig");let o=await robot_observe(r);let c=robot_command(o,{pan:0,tilt:0});let receipt=await r(c);[r,o,c,receipt]')
        self.assertEqual([item['$robotValue'] for item in value],['Robot','RobotObservation','RobotCommand','RobotReceipt']);self.assertFalse(value[3]['info']['targetVerified'])

    def test_mixed_descriptor_and_native_host_export_is_inert(self):
        log=[];seen=[];registry=Registry()
        registry.register_robot(RobotSpec('rig',lambda ctx:Adapter(log),ACTUATORS))
        registry.register_tool(ToolSpec('fixture.transport',lambda value,ctx:seen.append(value) or None))
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            value=run(app,'var hits=0;class C{let x=property(fn(self){hits=hits+1;42});\n@classmethod\nfn m(cls){42}}let r=await robot_open("rig");let o=await robot_observe(r);let command=robot_command(o,{pan:0,tilt:0});let receipt=await r(command);tool("fixture.transport",[r,o,command,receipt,C.x,C(),C.m]);let packed=json_stringify([r,o,command,receipt]);[hits,type_of(parse_json(packed)),parse_json(packed)[0]["$robotValue"]]',['robot.control','fixture.transport'])
        self.assertEqual(value,[0,'List','Robot']);self.assertEqual(len(seen[0]),7)
        self.assertEqual([x['$robotValue'] for x in seen[0][:4]],['Robot','RobotObservation','RobotCommand','RobotReceipt'])

    def test_typed_function_and_robot_eql_alias(self):
        log=[]
        with host(log) as app:
            value=run(app,'let r:Robot=await robot_open("rig");fn choose(r:Function,c:RobotCommand){r(c)};let g=generic_method(generic_function("same"),[generic_eql(r)],fn(x){x==r});let o:RobotObservation=await robot_observe(r);let c:RobotCommand=robot_command(o,{pan:0,tilt:0});let receipt:RobotReceipt=await choose(r,c);[g(r),receipt.status]')
        self.assertEqual(value,[True,'sent'])
