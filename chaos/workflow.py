"""Fluent construction of the same JSON graph edited by the visual environment."""
import copy
from .graph import validate
from .extensions import json_copy


class Workflow:
    def __init__(self, graph=None):
        self._graph = json_copy(graph) if graph is not None else {'nodes': [], 'edges': []}
        validate(self._graph)

    def node(self, id, kind, *, label=None, parent=None, config=None, x=None, y=250):
        result = copy.deepcopy(self._graph)
        result['nodes'].append({'id': id, 'kind': kind, 'label': label or id, 'parentId': parent,
                                'x': x if x is not None else len(result['nodes']) * 260,
                                'y': y, 'config': json_copy(config or {})})
        validate(result)
        self._graph = result
        return self

    def input(self, id='input', **layout):
        return self.node(id, 'input', **layout)

    def output(self, id='output', **layout):
        return self.node(id, 'output', **layout)

    def function(self, id, source, *, tools=(), **layout):
        return self.node(id, 'function', config={'source': source, 'tools': list(tools)}, **layout)

    def tool(self, id, name, **layout):
        return self.node(id, 'tool', config={'tool': name}, **layout)

    def agent(self, id, prompt, *, provider='local', tools=(), level='worker', max_steps=4, mode='analyze', policy=None, **layout):
        config = {'prompt': prompt, 'provider': provider, 'tools': list(tools), 'level': level,
                  'maxSteps': max_steps, 'mode': mode}
        if policy is not None:
            config['policy'] = policy
        return self.node(id, 'agent', config=config, **layout)

    def connect(self, source, target, *, port='input', id=None):
        result = copy.deepcopy(self._graph)
        result['edges'].append({'id': id or f'{source}_{target}_{port}', 'source': source,
                                'target': target, 'sourcePort': 'value', 'targetPort': port})
        validate(result)
        self._graph = result
        return self

    def build(self, *, complete=True):
        validate(self._graph, complete=complete)
        return copy.deepcopy(self._graph)

    @classmethod
    def identity(cls):
        return cls().input().output().connect('input', 'output')
