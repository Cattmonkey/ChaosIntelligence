"""Genuine held worker Futures, all64 residues plus wrap and original charged paths."""
import unittest
import _protected_flow214 as fixture

class ProtectedCapture214(unittest.TestCase):
    def matrix(self,awaited=False,raising=False):
        charges={}
        for padding in range(65):
            rows=[]
            for target in fixture.TARGETS():
                with self.subTest(padding=padding,target=target,awaited=awaited,raising=raising):
                    row,states,functions=fixture.pending(self,padding,target,raising,awaited)
                    self.assertEqual(row[0],('value',42));fixture.compiled(self,target,states,functions);rows.append(row)
            self.assertEqual(len(rows),len(fixture.TARGETS()));self.assertTrue(all(row==rows[0]for row in rows),rows)
            for name,delta,paths in rows[0][-1]:
                self.assertGreater(delta,0);self.assertTrue(paths);charges.setdefault(name,[]).append(delta)
        self.assertTrue(charges)
        for name,values in charges.items():
            self.assertEqual(len(values),65);self.assertGreater(len(set(values)),1,'actual '+name+' charged graph must cross a checkpoint')

    def test_all65_body_guards_handler_else_and_both_cleanup_effects_match_bare(self):
        self.matrix(raising=False);self.matrix(raising=True)

    def test_all65_actual_borrowed_child_await_keeps_protected_owner_and_charged_graph(self):
        self.matrix(awaited=True)

    def test_cancelling_consumer_keeps_borrowed_child_and_original_hard_cleanup_rule(self):
        rows=[]
        for target in fixture.TARGETS():
            with self.subTest(target=target):
                row,states,functions=fixture.pending(self,2,target,awaited=True,cancel=True)
                self.assertEqual(row[0],('value',40));fixture.compiled(self,target,states,functions);rows.append(row)
        self.assertTrue(all(row==rows[0]for row in rows),rows)
