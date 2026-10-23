"""All robot authority views retain owned leases, packets and admission."""
import time,unittest
from chaos.language import Evaluator,LanguageError,Native,TailCall,serializable
from chaos.robot_values import Robot,RobotObservation,RobotCommand,RobotReceipt,Lease,builtins,project,encoded
from chaos.capture_graph import seal
from test_robot_values import ACTUATORS


def fixture(e):
    lease=Lease(e,e.suspension.epoch,{'id':'lease','name':'rig','generation':1,'profile':'fixture/1','status':'open','actuators':ACTUATORS,'maxObservationAgeMs':1000,'latestObservationId':'packet'})
    observation=RobotObservation(e,lease,encoded({'id':'packet','robotId':'lease','generation':1,'sequence':1,'receivedAtNs':time.monotonic_ns(),'readings':{'angle':{'value':1,'unit':'degree'}}}))
    return Robot(e,lease),observation


class RobotOwnershipTests(unittest.TestCase):
    def test_foreign_and_stale_handles_reject_even_secondary_packet(self):
        other=Evaluator();other.suspension.begin();robot,observation=fixture(other)
        for value in (robot,observation,builtins(other)['robot_command'](observation,{'pan':0,'tilt':0}),RobotReceipt(other,other.suspension.epoch,encoded({'id':'receipt'}))):
            e=Evaluator();e.env.bind('foreign',value)
            for source in ('type_of(foreign)','return_values(1,foreign)'):
                with self.subTest(type=type(value).__name__,source=source),self.assertRaises(LanguageError) as caught:e.run(source)
                self.assertEqual(caught.exception.code,'ownership_error')
        other.suspension.finish()

    def test_capture_graph_checks_foreign_command_observation(self):
        e=Evaluator();e.suspension.begin();other=Evaluator();other.suspension.begin();robot,observation=fixture(other)
        command=RobotCommand(e,observation,'id',encoded({'pan':0,'tilt':0}),250)
        with self.assertRaises(LanguageError) as caught:seal(e,(command,),frozenset())
        self.assertEqual(caught.exception.code,'ownership_error');e.suspension.finish();other.suspension.finish()

    def test_projection_keeps_admission_identity_and_ceiling(self):
        e=Evaluator();e.suspension.begin();robot,observation=fixture(e);command=builtins(e)['robot_command'](observation,{'pan':0,'tilt':0});ceiling=type('Ceiling',(),{'capabilities':frozenset()})()
        narrowed=project(e,command,ceiling);self.assertIs(narrowed.admission,command.admission);self.assertIs(narrowed.observation,command.observation);self.assertEqual(narrowed.capabilities,frozenset());narrowed.admission.used=True;self.assertTrue(command.admission.used);e.suspension.finish()

    def test_serialization_is_descriptive_without_json_revival(self):
        e=Evaluator();e.suspension.begin();robot,observation=fixture(e)
        self.assertEqual(serializable(robot)['$robotValue'],'Robot');self.assertEqual(serializable(observation)['info']['sequence'],1)
        e.suspension.finish()
        with self.assertRaises(LanguageError):Evaluator().run('robot_info(input)',{'input':serializable(robot)})

    def test_descriptor_projection_cannot_widen_robot(self):
        e=Evaluator();e.env.bind('restricted',Native('restricted',lambda f:TailCall(f,[]),e,capabilities=frozenset()));calls=[]
        def handler(op,payload):
            calls.append(op)
            return {'id':'lease','name':'rig','generation':1,'profile':'fixture/1','status':'open','actuators':ACTUATORS,'maxObservationAgeMs':1000,'latestObservationId':None}
        e.robot_handler=handler
        self.assertEqual(e.run('let r=await robot_open("rig");let narrowed=restricted(fn(){r});[r==narrowed,try{await robot_stop(narrowed)}catch e{e.code}]'),[True,'capability_error']);self.assertEqual(calls,['open'])

    def test_epoch_cannot_be_reused_through_saved_callable(self):
        e=Evaluator();e.suspension.begin();robot,observation=fixture(e);e.suspension.finish();e.env.bind('old',robot)
        with self.assertRaises(LanguageError) as caught:e.run('fn f(){old};f()')
        self.assertEqual(caught.exception.code,'ownership_error')

    def test_stale_robot_results_reject_nested_and_receipt_leaves(self):
        for shape in ('old','[old]','{hidden:old}','return_values(1,old)'):
            for kind in ('robot','receipt'):
                e=Evaluator();e.suspension.begin();robot,_=fixture(e)
                value=robot if kind=='robot' else RobotReceipt(e,e.suspension.epoch,encoded({'id':'receipt'}))
                e.suspension.finish();e.env.bind('old',value)
                with self.subTest(shape=shape,kind=kind),self.assertRaises(LanguageError) as caught:e.run('fn f(){'+shape+'};f()')
                self.assertEqual(caught.exception.code,'ownership_error')
