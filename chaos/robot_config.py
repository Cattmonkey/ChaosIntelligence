"""Cold trusted Robot registry configuration; loading never opens a port."""
import json
from pathlib import Path


def load_robot_registry(path, registry=None):
    from .extensions import Registry
    from .robot_openbot import openbot_robot
    with Path(path).open('rb') as stream:
        data = stream.read(64001)
    if len(data) > 64000:
        raise ValueError('Robot configuration exceeds 64KB')
    document = json.loads(data)
    if type(document) is not dict or set(document) != {'robots'} or type(document['robots']) is not list or len(document['robots']) > 32:
        raise ValueError('Expected {robots: [...]} with at most 32 bindings')
    registry = registry if registry is not None else Registry()
    specs = []
    for row in document['robots']:
        if type(row) is not dict:
            raise ValueError('Robot binding must be a record')
        fields = dict(row)
        transport = fields.pop('transport', 'openbot-serial')
        if transport != 'openbot-serial' or not {'name', 'port'} <= set(fields) or set(fields) - {'name', 'port', 'baudrate', 'timeout', 'telemetry_ms', 'max_observation_age_ms'}:
            raise ValueError('Invalid OpenBot serial binding configuration')
        specs.append(openbot_robot(**fields))
    names = [spec.name for spec in specs]
    if len(set(names)) != len(names) or set(names) & set(registry.robot_snapshot()):
        raise ValueError('Duplicate or already registered Robot name')
    for spec in specs:
        registry.register_robot(spec)
    return registry
