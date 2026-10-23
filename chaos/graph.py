"""Version-independent graph validation, proof certificates and scoped patches."""
import copy
import re


class GraphError(ValueError):
    pass


LEVELS = {'platform': 3, 'coordinator': 2, 'worker': 1}
KINDS = {'input', 'output', 'function', 'agent', 'tool'}


def level(node):
    return LEVELS.get(node.get('config', {}).get('level', 'worker'), 1) if node['kind'] == 'agent' else 0


def executable(node):
    return not (node['kind'] == 'agent' and node.get('config', {}).get('level') == 'platform')


def validate(graph, complete=False):
    if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list) or not isinstance(graph.get('edges'), list):
        raise GraphError('图必须包含 nodes 和 edges 数组')
    nodes, edges = graph['nodes'], graph['edges']
    if len(nodes) > 100 or len(edges) > 300:
        raise GraphError('图最多 100 个节点、300 条边')
    by_id = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get('id'), str) or not re.fullmatch(r'[A-Za-z_][\w-]{0,63}', node['id']):
            raise GraphError('节点 id 必须为 1–64 位标识符')
        if node['id'] in by_id:
            raise GraphError(f"重复节点 {node['id']}")
        if not isinstance(node.get('kind'), str) or node['kind'] not in KINDS:
            raise GraphError('未知节点类型')
        if not isinstance(node.get('config', {}), dict):
            raise GraphError('节点 config 必须为对象')
        if node['kind'] == 'agent' and (not isinstance(node.get('config', {}).get('level', 'worker'), str) or node.get('config', {}).get('level', 'worker') not in LEVELS):
            raise GraphError('未知智能体层级')
        config = node.get('config', {})
        if not isinstance(node.get('label', ''), str) or len(node.get('label', '')) > 120:
            raise GraphError('节点标签必须为不超过 120 字符的文本')
        if not isinstance(config.get('tools', []), list) or any(not isinstance(t, str) for t in config.get('tools', [])):
            raise GraphError('tools 必须为字符串数组')
        if type(config.get('maxSteps', 4)) is not int or not 1 <= config.get('maxSteps', 4) <= 12:
            raise GraphError('maxSteps 必须为 1–12')
        for key in ('source', 'prompt', 'tool', 'provider', 'mode', 'policy'):
            if key in config and (not isinstance(config[key], str) or len(config[key]) > 100000):
                raise GraphError(f'配置 {key} 必须为文本且不超过 100 KB')
        for coordinate in ('x', 'y'):
            if type(node.get(coordinate, 0)) not in (int, float) or not -10000 <= node.get(coordinate, 0) <= 10000:
                raise GraphError('节点坐标超出范围')
        by_id[node['id']] = node
    for node in nodes:
        parent = node.get('parentId')
        if parent is not None and not isinstance(parent, str):
            raise GraphError('parentId 必须为字符串或 null')
        if parent:
            if parent not in by_id or by_id[parent]['kind'] != 'agent' or level(by_id[parent]) <= level(node):
                raise GraphError(f"{node['id']} 的父智能体不存在或层级不足")
    active = {n['id'] for n in nodes if executable(n)}
    incoming, outgoing = {n: [] for n in active}, {n: [] for n in active}
    edge_ids, ports = set(), set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise GraphError('边必须为对象')
        eid, src, dst = edge.get('id'), edge.get('source'), edge.get('target')
        if not isinstance(eid, str) or not eid or eid in edge_ids:
            raise GraphError('边 id 必须唯一且非空')
        edge_ids.add(eid)
        if not isinstance(src, str) or not isinstance(dst, str) or src not in active or dst not in active:
            raise GraphError(f'边 {eid} 引用了无效或平台控制节点')
        if by_id[src]['kind'] == 'output' or by_id[dst]['kind'] == 'input':
            raise GraphError('输入节点不能有入边，输出节点不能有出边')
        if edge.get('sourcePort', 'value') != 'value':
            raise GraphError('源端口必须为 value')
        port = edge.get('targetPort', 'input')
        if not isinstance(port, str) or not re.fullmatch(r'[A-Za-z_]\w{0,63}', port):
            raise GraphError('目标端口名称无效')
        if (dst, port) in ports:
            raise GraphError(f'{dst}.{port} 已有数据源')
        ports.add((dst, port))
        outgoing[src].append(dst)
        incoming[dst].append(src)
    remaining = {n: len(incoming[n]) for n in active}
    queue = sorted(n for n, degree in remaining.items() if degree == 0)
    order = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for target in outgoing[n]:
            remaining[target] -= 1
            if remaining[target] == 0:
                queue.append(target)
    if len(order) != len(active):
        raise GraphError('数据图包含环；迭代请使用函数节点中的受预算递归')
    starts = [n['id'] for n in nodes if n['kind'] == 'input']
    ends = [n['id'] for n in nodes if n['kind'] == 'output']

    def reachable(seeds, adjacency):
        found, todo = set(), list(seeds)
        while todo:
            n = todo.pop()
            if n not in found:
                found.add(n)
                todo.extend(adjacency[n])
        return found

    from_input = reachable(starts, outgoing)
    to_output = reachable(ends, incoming)
    missing = sorted(active - (from_input & to_output))
    closed = len(starts) == 1 and len(ends) == 1 and not missing
    certificate = {'valid': True, 'closed': closed, 'entry': starts, 'exit': ends, 'topologicalOrder': order, 'uncovered': missing,
                   'checks': {'acyclicDataflow': True, 'validReferences': True, 'uniqueInputPorts': True, 'strictHierarchy': True, 'singleEntryExit': len(starts) == len(ends) == 1, 'allNodesOnPath': not missing},
                   'scope': '结构路径覆盖证书：不证明任意函数终止、模型结论正确或任务成功。'}
    if complete and not closed:
        raise GraphError('运行需要唯一输入、唯一输出，且全部执行节点在输入到输出的路径上；未覆盖：' + ', '.join(missing))
    return certificate


def apply_patch(graph, operations, actor=None):
    if actor is not None and not isinstance(actor, str):
        raise GraphError('actor 必须为字符串或 null')
    result = copy.deepcopy(graph)
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        raise GraphError('修改必须包含 1–100 项操作')
    original = {n['id']: n for n in graph['nodes']}
    if actor:
        controller = original.get(actor)
        if not controller or level(controller) != 3 or 'graph.patch' not in controller.get('config', {}).get('tools', []):
            raise GraphError('平台智能体缺少 graph.patch 能力')
        def in_scope(node_id):
            current = original.get(node_id)
            seen = set()
            while current and current['id'] not in seen:
                seen.add(current['id'])
                if current.get('parentId') == actor:
                    return True
                current = original.get(current.get('parentId'))
            return False
    for op in operations:
        if not isinstance(op, dict) or not isinstance(op.get('op'), str):
            raise GraphError('修改操作必须包含 op 字符串')
        kind = op['op']
        if kind == 'add_node' and not isinstance(op.get('node'), dict):
            raise GraphError('add_node 需要 node 对象')
        if kind == 'add_node':
            validate({'nodes': [dict(op['node'], parentId=None)], 'edges': []})
        if kind == 'add_edge' and not isinstance(op.get('edge'), dict):
            raise GraphError('add_edge 需要 edge 对象')
        if kind == 'add_edge' and not all(isinstance(op['edge'].get(k), str) for k in ('id','source','target')):
            raise GraphError('add_edge 需要合法 id/source/target')
        if kind in ('remove_node', 'update_node', 'remove_edge') and not isinstance(op.get('id'), str):
            raise GraphError('修改需要节点或边 id')
        if kind == 'update_node' and not isinstance(op.get('changes'), dict):
            raise GraphError('update_node 需要 changes 对象')
        if actor:
            affected = []
            if kind in ('remove_node', 'update_node'):
                affected = [op.get('id')]
                if kind == 'remove_node':
                    for edge in result['edges']:
                        if op.get('id') in (edge['source'], edge['target']):
                            affected.extend([edge['source'], edge['target']])
            elif kind == 'add_edge':
                affected = [op['edge']['source'], op['edge']['target']]
            elif kind == 'remove_edge':
                old = next((e for e in result['edges'] if e['id'] == op.get('id')), None)
                if old:
                    affected = [old['source'], old['target']]
            elif kind == 'add_node':
                n = op['node']
                if level(n) >= 3 or n.get('parentId') != actor or 'graph.patch' in n.get('config', {}).get('tools', []):
                    raise GraphError('平台只能添加自己管理的低层节点且不能授予 graph.patch')
            for nid in affected:
                if nid in original and not in_scope(nid):
                    raise GraphError(f'平台无权修改管理域外节点 {nid}')
            if kind == 'update_node':
                changes = op.get('changes', {})
                if any(k in changes for k in ('id', 'kind', 'parentId')) or 'config' in changes:
                    raise GraphError('平台补丁不能更改已有节点的身份、层级或能力配置')
        if kind == 'add_node':
            result['nodes'].append(copy.deepcopy(op['node']))
        elif kind == 'remove_node':
            if not any(n['id'] == op.get('id') for n in result['nodes']):
                raise GraphError('待删除节点不存在')
            result['nodes'] = [n for n in result['nodes'] if n['id'] != op['id']]
            result['edges'] = [e for e in result['edges'] if op['id'] not in (e['source'], e['target'])]
        elif kind == 'update_node':
            target = next((n for n in result['nodes'] if n['id'] == op.get('id')), None)
            if not target:
                raise GraphError('待修改节点不存在')
            if 'id' in op.get('changes', {}):
                raise GraphError('不能修改节点 id')
            target.update(copy.deepcopy(op.get('changes', {})))
        elif kind == 'add_edge':
            result['edges'].append(copy.deepcopy(op['edge']))
        elif kind == 'remove_edge':
            if not any(e['id'] == op.get('id') for e in result['edges']):
                raise GraphError('待删除边不存在')
            result['edges'] = [e for e in result['edges'] if e['id'] != op['id']]
        else:
            raise GraphError(f'不支持的操作 {kind}')
    validate(result)
    return result
