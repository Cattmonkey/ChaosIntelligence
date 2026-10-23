"""All checkpoint residues plus wrap at genuine resource protocol Futures."""
import unittest
import _resource_flow217 as fixture

class ResourceCapture217(unittest.TestCase):
    def matrix(self,asynchronous=True,awaited=False):
        charges={}
        for padding in range(65):
            rows=[]
            for target in fixture.TARGETS():
                with self.subTest(padding=padding,target=target,asynchronous=asynchronous,awaited=awaited):
                    row,states,functions=fixture.pending(self,padding,target,asynchronous,awaited)
                    self.assertEqual(row[0],('value',42));fixture.compiled(self,target,states,functions);rows.append(row)
            self.assertEqual(len(rows),len(fixture.TARGETS()));self.assertTrue(all(row==rows[0]for row in rows),rows)
            for name,delta,paths in rows[0][-1]:
                self.assertGreater(delta,0);self.assertTrue(paths);charges.setdefault(name,[]).append(delta)
        for name,values in charges.items():
            self.assertEqual(len(values),65);self.assertGreater(len(set(values)),1,name+' must cross a real charged checkpoint')

    def test_all65_sync_manager_acquire_body_and_both_exit_effects_match_original(self):
        self.matrix(asynchronous=False)

    def test_all65_async_manager_acquire_body_and_both_exit_effects_match_original(self):
        self.matrix(asynchronous=True)

    def test_all65_borrowed_await_keeps_resource_owner_and_original_capture_graph(self):
        self.matrix(awaited=True)

    def test_cancelling_consumer_keeps_borrowed_task_and_skips_user_exit_on_hard_cancel(self):
        rows=[]
        for target in fixture.TARGETS():
            with self.subTest(target=target):
                row,states,functions=fixture.pending(self,2,target,awaited=True,cancel=True)
                self.assertEqual(row[0],('value',40));fixture.compiled(self,target,states,functions);rows.append(row)
        self.assertTrue(all(row==rows[0]for row in rows),rows)

    def test_all65_task_group_body_keeps_distinct_original_cursor_and_nonlocal_outcome(self):
        charges=[]
        for padding in range(65):
            rows=[]
            for target in fixture.TARGETS():
                with self.subTest(padding=padding,target=target):
                    row,states,functions=fixture.pending(self,padding,target,group=True)
                    self.assertEqual(row[0],('value',42));fixture.compiled(self,target,states,functions);rows.append(row)
            self.assertEqual(len(rows),len(fixture.TARGETS()));self.assertTrue(all(row==rows[0]for row in rows),rows)
            self.assertEqual([x[0]for x in rows[0][-1]],['group_body_hold','group_body_hold']);charges.append(tuple(x[1]for x in rows[0][-1]))
        self.assertGreater(len(set(charges)),1)
