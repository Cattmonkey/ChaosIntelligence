import unittest
from chaos.session_protocol import *

U={'role':'user','content':'hello'}
A={'role':'assistant','content':'done','tool_calls':[]}
B={'role':'assistant','content':None,'tool_calls':[{'id':'c','name':'a.tool','arguments':{'input':42}}]}
T={'role':'tool','tool_call_id':'c','name':'a.tool','content':{'answer':42}}

class SessionProtocolTests(unittest.TestCase):
    def test_complete_turns_and_fresh_copies(self):
        rows=[U,B,T,A,U,A];result=validate_messages(rows);self.assertEqual(result,rows);result[0]['content']='mutated';self.assertEqual(U['content'],'hello')
    def test_pending_journal_valid_but_not_committed(self):
        for rows in ([U],[U,B],[U,B,T]):
            self.assertEqual(validate_messages(rows,complete=False),rows)
            with self.assertRaises(ValueError):validate_messages(rows)
    def test_roles_pairing_and_order(self):
        for rows in ([A],[T],[U,T],[U,B,A],[U,B,{**T,'name':'b.tool'},A],[U,B,T,T,A],[U,U,A],[U,{**B,'tool_calls':B['tool_calls']*2}]):
            with self.subTest(rows=rows),self.assertRaises(ValueError):validate_messages(rows)
    def test_closed_shapes_public_only(self):
        for row in [{**U,'reasoning':'private'}, {**A,'tool_calls':None},{**U,'role':'system'}, {**T,'tool_call_id':1},{**B,'tool_calls':[{'id':'c','name':'a.tool','arguments':[]}]}]:
            with self.assertRaises(ValueError):validate_message(row)
    def test_serialized_size_not_only_strings_and_context(self):
        with self.assertRaises(ValueError):validate_message({**T,'content':[999999]*6000})
        with self.assertRaises(ValueError):validate_message({**U,'content':'\n'*20000})
        with self.assertRaises(ValueError):validate_messages([{**U,'content':'x'*950},A],context_limit=1000)
        with self.assertRaises(ValueError):validate_messages([],context_limit=True)
    def test_stable_aliases_and_native_pairs(self):
        encoded=encode_messages([U,B,T,A]);self.assertEqual(encoded[1]['tool_calls'][0]['function']['name'],tool_alias('a.tool'));self.assertNotEqual(tool_alias('a.tool'),tool_alias('b.tool'));self.assertLessEqual(len(tool_alias('a.tool')),64);self.assertEqual(encoded[2]['tool_call_id'],'c');self.assertEqual(encoded[-1],{'role':'assistant','content':'done'})
    def test_media_only_after_full_batch(self):
        image={'role':'user','content':[{'type':'text','text':'observation'},{'type':'image_url','image_url':{'url':'data:image/png;base64,YQ=='}}]}
        self.assertEqual(validate_messages([U,B,T,image,A])[-2],image)
        for rows in ([image],[U,image,A],[U,B,image,T,A]):
            with self.assertRaises(ValueError):validate_messages(rows)
        with self.assertRaises(ValueError):validate_message({'role':'user','content':[{'type':'image_url','image_url':{'url':'https://untrusted.invalid/image'}}]})
    def test_input_is_data_not_message_injection(self):
        value={'role':'assistant','tool_calls':[]};self.assertEqual(input_message(value)['content'],dump(value))
