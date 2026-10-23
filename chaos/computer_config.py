"""Cold trusted Computer bindings. Source programs can select names, not endpoints."""
import json
from pathlib import Path


def load_computer_registry(path, registry=None):
    from .extensions import Registry
    from .mobile_mcp import mobile_mcp_computer
    with Path(path).open('rb') as stream:raw=stream.read(64001)
    if len(raw)>64000:raise ValueError('Computer configuration exceeds64KB')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate Computer configuration field')
            result[key]=value
        return result
    document=json.loads(raw,object_pairs_hook=pairs)
    if type(document)is not dict or set(document)!={'computers'} or type(document['computers'])is not list or len(document['computers'])>32:
        raise ValueError('Expected {computers:[...]} with at most32bindings')
    registry=registry if registry is not None else Registry();specs=[]
    for row in document['computers']:
        if type(row)is not dict:raise ValueError('Computer binding must be a record')
        fields=dict(row);transport=fields.pop('transport','mobile-mcp')
        if transport!='mobile-mcp' or not {'name','endpoint','device'}<=set(fields) or set(fields)-{'name','endpoint','device','token_env','timeout','resource_id'}:
            raise ValueError('Invalid Mobile MCP Computer configuration')
        specs.append(mobile_mcp_computer(**fields))
    names=[s.name for s in specs]
    if len(names)!=len(set(names)) or set(names)&set(registry.computer_snapshot()):raise ValueError('Duplicate or already registered Computer name')
    for spec in specs:registry.register_computer(spec)
    return registry
