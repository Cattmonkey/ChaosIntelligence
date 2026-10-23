import unittest
from chaos.computer_schema import configuration_schema,action_schema,validate_action,validate_url
from chaos.session_protocol import validate_message,validate_messages,encode_messages,input_message

IMAGE={'role':'user','content':[{'type':'text','text':'Actual observation'},{'type':'image_url','image_url':{'url':'data:image/png;base64,YQ=='}}]}
FINAL={'role':'assistant','content':'done','tool_calls':[]}
class ComputerProtocolTests(unittest.TestCase):
    def test_media_starts_turn_and_roundtrips_native_history(self):
        history=[IMAGE,FINAL,{'role':'user','content':'next'},FINAL]
        self.assertEqual(validate_messages(history),history);self.assertEqual(encode_messages(history)[0],IMAGE)
        self.assertEqual(validate_messages([IMAGE],complete=False),[IMAGE])
        with self.assertRaises(ValueError):validate_messages([IMAGE,IMAGE,FINAL])
    def test_plain_image_shaped_data_remains_text(self):
        self.assertIsInstance(input_message(IMAGE)['content'],str)
    def test_action_schema_exact_fields_and_fresh_copies(self):
        valid=[{'action':'navigate','url':'http://127.0.0.1:1/path'},{'action':'click','ref':'e1'},{'action':'type','ref':'e1','text':'input'},{'action':'press','ref':'e1','key':'Control+a'},{'action':'scroll','deltaX':0,'deltaY':-10000}]
        for spec in valid:
            result=validate_action(spec);self.assertEqual(result,spec);self.assertIsNot(result,spec)
        schema=configuration_schema();schema['action']['oneOf'].clear();self.assertEqual(len(action_schema()['oneOf']),5)
    def test_invalid_specs_before_effect(self):
        cases=[{}, {'action':'click','ref':'e1','sessionId':'forged'},{'action':'scroll','deltaX':0.,'deltaY':1},{'action':'scroll','deltaX':True,'deltaY':1},{'action':'scroll','deltaX':10001,'deltaY':1},{'action':'press','ref':'e1','key':'\n'}, {'action':'type','ref':'e1','text':'x'*100001}]
        for spec in cases:
            with self.subTest(spec=str(spec)[:80]),self.assertRaises(Exception):validate_action(spec)
        for url in ['file:///tmp/x','https://user:password@host/path','http://host:bad/','http://host/\n','javascript:alert(1)']:
            with self.assertRaises(Exception):validate_url(url)
    def test_visual_message_and_context_bounds(self):
        with self.assertRaises(ValueError):validate_message({'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+'a'*40000}}]})
        with self.assertRaises(ValueError):validate_messages([{'role':'user','content':[{'type':'text','text':'x'*1000},IMAGE['content'][1]]},FINAL],context_limit=1000)
