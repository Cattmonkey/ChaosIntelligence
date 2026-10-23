"""All64 checkpoint residues plus wrap on original PatternCloseCursor paths."""
import unittest
import _pattern_flow218 as fixture

class PatternCapture218(unittest.TestCase):
    def matrix(self,mode='match',awaited=False,group=False):
        charges={}
        for padding in range(65):
            rows=[]
            for target in fixture.TARGETS():
                with self.subTest(padding=padding,target=target,mode=mode,awaited=awaited,group=group):
                    row,states,functions=fixture.pending(self,padding,target,mode,awaited,group=group)
                    self.assertEqual(row[0],('value',42));fixture.compiled(self,target,states,functions);rows.append(row)
            self.assertEqual(len(rows),len(fixture.TARGETS()));self.assertTrue(all(row==rows[0]for row in rows),rows)
            for name,delta,paths in rows[0][-1]:self.assertGreater(delta,0);self.assertTrue(paths);charges.setdefault(name,[]).append(delta)
        for name,values in charges.items():
            self.assertEqual(len(values),130 if group else 65);self.assertGreater(len(set(values)),1,name+' crosses actual charged capture boundaries')

    def test_all65_subject_false_guard_true_guard_and_body_match_original_cursor_paths(self):self.matrix()
    def test_all65_immutable_declaration_rhs_is_cold_until_atomic_bind(self):self.matrix('let_pattern')
    def test_all65_mutable_declaration_rhs_keeps_real_cells_and_capture(self):self.matrix('var_pattern')
    def test_all65_assignment_rhs_keeps_precommit_cells_and_charged_graph(self):self.matrix('assign_pattern')
    def test_all65_borrowed_await_keeps_pattern_body_owner_without_implicit_consumption(self):self.matrix(awaited=True)
    def test_all65_task_group_child_cursor_pattern_and_nonlocal_outcomes_match_original(self):self.matrix(group=True)

    def test_cancel_pending_match_does_not_cancel_borrowed_task_or_replay_body(self):
        rows=[]
        for target in fixture.TARGETS():
            row,states,functions=fixture.pending(self,2,target,awaited=True,cancel=True)
            self.assertEqual(row[0],('value',40));fixture.compiled(self,target,states,functions);rows.append(row)
        self.assertTrue(all(row==rows[0]for row in rows),rows)
