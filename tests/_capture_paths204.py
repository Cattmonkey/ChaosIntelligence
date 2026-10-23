"""Test-only observations of the actual seal algorithm, without changing its walk.

Four pure metadata calls are inserted into a copy of the current function AST.
Removing those calls must recover the exact original AST. No root, queue, seen
map, counter, tick, authority or return statement is replaced.
"""
import ast
import copy
from pathlib import Path


def observed_seal():
    from chaos import capture_graph
    original = next(node for node in ast.parse(Path(capture_graph.__file__).read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'seal')
    function = copy.deepcopy(original)
    inserted = dict(pop=0, visit=0, children=0, queued=0)
    names = {'_capture204_pop', '_capture204_visit', '_capture204_children', '_capture204_queued'}

    def call(name, arguments=()):
        return ast.Expr(ast.Call(ast.Name(name, ast.Load()), [ast.Name(value, ast.Load()) for value in arguments], []))

    class Observe(ast.NodeTransformer):
        def visit_Assign(self, node):
            node = self.generic_visit(node)
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Tuple) and [getattr(x, 'id', None) for x in node.targets[0].elts] == ['value', 'ceiling', 'depth']:
                inserted['pop'] += 1
                return [node, call('_capture204_pop', ('value', 'charging', 'depth'))]
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'identity':
                inserted['children'] += 1
                return [call('_capture204_children', ('children', 'seen', 'value')), node]
            return node

        def visit_AugAssign(self, node):
            node = self.generic_visit(node)
            if isinstance(node.target, ast.Name) and node.target.id == 'count':
                inserted['visit'] += 1
                return [node, call('_capture204_visit', ('value', 'charging', 'depth', 'count', 'charged_count'))]
            return node

        def visit_Expr(self, node):
            node = self.generic_visit(node)
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == 'pending' and node.value.func.attr == 'extend':
                inserted['queued'] += 1
                return [node, call('_capture204_queued')]
            return node

    Observe().visit(function)
    assert inserted == dict(pop=1, visit=1, children=1, queued=1), 'Actual seal observation sites changed'

    class RemoveObservations(ast.NodeTransformer):
        def visit_Expr(self, node):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id in names:
                return None
            return self.generic_visit(node)

    stripped = RemoveObservations().visit(copy.deepcopy(function))
    assert ast.dump(stripped, include_attributes=False) == ast.dump(original, include_attributes=False)
    active, shadow, rows = [None], [], []

    def pop(value, charging, depth):
        if not charging:
            active[0] = None
            return
        assert shadow, 'Charged diagnostic shadow lost a real queue entry'
        identity, path = shadow.pop()
        assert identity == id(value), 'Observed path differs from actual original DFS'
        kind = type(value)
        shape = ('value', value) if kind in (type(None), bool, int, float, str) else ('length', len(value)) if kind in (list, tuple, dict) else None
        row = {'path':path, 'depth':depth, 'kind':kind.__name__, 'shape':shape,
               'counted':False, 'children':None, 'seen':None, 'queued':False}
        active[0] = row
        rows.append(row)

    def visit(value, charging, depth, count, charged_count):
        if charging:
            assert active[0] is not None and active[0]['depth'] == depth
            active[0]['counted'] = True

    def children(values, seen, value):
        row = active[0]
        if row is not None:
            row['children'] = tuple((type(child).__name__, id(child)) for child in values)
            row['seen'] = id(value) in seen

    def queued():
        row = active[0]
        if row is not None:
            row['queued'] = True
            shadow.extend((identity, row['path']+'/'+row['kind']+'['+str(index)+']')
                          for index, (kind, identity) in enumerate(row['children']))

    namespace = dict(capture_graph.__dict__)
    namespace.update(_capture204_pop=pop, _capture204_visit=visit,
                     _capture204_children=children, _capture204_queued=queued)
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(capture_graph.__file__), 'exec'), namespace)
    instrumented = namespace['seal']

    def record(evaluator, roots, ceiling):
        roots = tuple(roots)
        rows.clear(); active[0] = None
        shadow[:] = [(id(value), 'root['+str(index)+']') for index, value in enumerate(roots)]
        before = evaluator.steps
        result = instrumented(evaluator, roots, ceiling)
        assert not shadow
        charged = tuple((row['path'], row['depth'], row['kind'], row['shape'],
                         None if row['children'] is None else tuple(kind for kind, identity in row['children']),
                         row['seen'], row['queued']) for row in rows if row['counted'])
        assert charged and max(row[1] for row in charged) > 0
        return result, evaluator.steps-before, charged

    return record
