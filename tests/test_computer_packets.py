import base64
import copy
from io import BytesIO
import unittest
from unittest.mock import patch
from chaos.computer_adapters import ComputerSpec,validate_computer_packet,prepare_computer_image


def packet():
    return {'sessionId':'s','controlEpoch':1,'snapshotId':1,'url':'','title':'Phone',
            'viewport':{'width':390,'height':844},'cursor':{'x':0,'y':0},
            'truncated':{'text':False,'targets':False},'snapshot':'Screen',
            'targets':[{'ref':'e1','tag':'button','text':'Go','bbox':{'x':1,'y':2,'width':30,'height':20}}]}


class ComputerPacketTests(unittest.TestCase):
    def test_registration_is_cold_and_hides_physical_identity(self):
        factory=lambda ctx: self.fail('cold')
        spec=ComputerSpec('phone',factory,resource_id='secret-physical-id',actions=('click','input'))
        shown=spec.describe();self.assertNotIn('resource_id',shown);shown['actions'].clear()
        self.assertEqual(spec.actions,('click','input'))
        for actions in ([],(),('click','click'),('unknown',),('click',True)):
            with self.subTest(actions=actions),self.assertRaises(ValueError):ComputerSpec('phone',factory,actions=actions)
    def test_closed_packet_copies_without_inventing_locators(self):
        raw=packet();accepted=validate_computer_packet(raw);accepted['targets'][0]['text']='edited'
        self.assertEqual(raw['targets'][0]['text'],'Go');self.assertNotIn('locator',accepted['targets'][0])
        for field in ('selector','locator'):
            bad=packet();bad['targets'][0][field]='forged'
            with self.assertRaises(ValueError):validate_computer_packet(bad)
        bad=packet();bad['runId']='forged'
        with self.assertRaises(ValueError):validate_computer_packet(bad)
    def test_identity_is_lease_bound_and_strictly_monotone(self):
        old=packet()
        for fields in ({},{'snapshotId':0},{'sessionId':'other'},{'controlEpoch':0},{'snapshotId':True}):
            bad={**old,**fields}
            with self.subTest(fields=fields),self.assertRaises(ValueError):validate_computer_packet(bad,old)
        self.assertEqual(validate_computer_packet({**old,'controlEpoch':2},old)['snapshotId'],1)
        self.assertEqual(validate_computer_packet({**old,'snapshotId':2},old)['snapshotId'],2)
    def test_geometry_and_bounds_are_not_trusted(self):
        mutations=[lambda p:p['viewport'].update(width=True),lambda p:p['cursor'].update(x=float('nan')),
                   lambda p:p['cursor'].update(x=390),lambda p:p['targets'][0]['bbox'].update(width=0),
                   lambda p:p['targets'][0]['bbox'].update(x=400),lambda p:p.update(snapshot='x'*12001),
                   lambda p:p.update(targets=p['targets']*41),lambda p:p['truncated'].update(text=1)]
        for mutate in mutations:
            bad=packet();mutate(bad)
            with self.assertRaises(ValueError):validate_computer_packet(bad)
        valid=packet();valid['targets'][0]['bbox']=None;validate_computer_packet(valid)
    def test_no_image_path_loads_pillow(self):
        with patch('chaos.computer_adapters._pillow',side_effect=AssertionError('optional')):
            validate_computer_packet(packet());ComputerSpec('phone',lambda c:None)
    def test_real_pixels_and_mime_are_verified(self):
        from PIL import Image
        out=BytesIO();Image.new('RGB',(1170,2532),(22,66,99)).save(out,format='PNG');raw=out.getvalue()
        images=prepare_computer_image(raw,'image/png');accepted=validate_computer_packet({**packet(),'_images':images})
        self.assertLessEqual(len(images['model']['data']),16000);self.assertLessEqual(images['model']['width'],480)
        self.assertEqual(accepted['viewport']['width'],390)
        with self.assertRaises(ValueError):prepare_computer_image(raw,'image/jpeg')
        with self.assertRaises(ValueError):prepare_computer_image(b'not pixels','image/png')
        bad=copy.deepcopy(images);bad['model']['width']+=1
        with self.assertRaises(ValueError):validate_computer_packet({**packet(),'_images':bad})
        bad=copy.deepcopy(images);bad['model']['data']=base64.b64encode(b'not pixels').decode()
        with self.assertRaises(ValueError):validate_computer_packet({**packet(),'_images':bad})
    def test_image_checkpoint_can_cancel_before_decode(self):
        def cancel():raise RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError,'cancelled'):prepare_computer_image(b'x','image/png',checkpoint=cancel)
