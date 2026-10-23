"""Pure bounded projection of already-authorized host tool contracts.

Selection, ordering, capabilities and dispatch belong to the live caller. This
module only builds provider metadata; schemas do not execute or grant tools.
"""
from .extensions import json_copy, valid_name


_INPUT_SCHEMAS = {
    'Any': {}, 'Number': {'type': 'number'}, 'Int': {'type': 'integer'},
    'String': {'type': 'string'}, 'Bool': {'type': 'boolean'},
    'List': {'type': 'array'}, 'Record': {'type': 'object'}, 'Null': {'type': 'null'},
}


def native_tool(name, descriptor, *, mode, parameters=None, image_output=None, category=None):
    """Copy one descriptor into the existing NativeHarness spec shape.

    ``wrapped`` carries the actual coarse language input contract and exactly
    one ``input`` argument. ``flat`` requires an explicit object schema and adds
    neither inputType nor wrapped. Optional presentation metadata is supplied
    by the caller, never inferred from a name, description, schema or payload.
    """
    valid_name(name)
    if type(descriptor) is not dict:
        raise ValueError('Tool descriptor must be an object')
    if len(descriptor) > 128 or any(type(key) is not str for key in descriptor):
        raise ValueError('Tool descriptor requires bounded string keys')
    if type(mode) is not str or mode not in ('wrapped', 'flat'):
        raise ValueError('Tool declaration mode must be wrapped or flat')
    result = {'name': name, 'description': descriptor.get('description', '')}
    if type(result['description']) is not str or len(result['description']) > 4000:
        raise ValueError('Invalid tool description')
    if mode == 'wrapped':
        input_type = descriptor.get('inputType', 'Any')
        if type(input_type) is not str or input_type not in _INPUT_SCHEMAS:
            raise ValueError('Tool contract must use a JSON-compatible Chaos type')
        if parameters is not None and type(parameters) is not dict:
            raise ValueError('Wrapped input schema must be an object')
        schema = _INPUT_SCHEMAS[input_type] if parameters is None else parameters
        result.update({'wrapped': True, 'inputType': input_type,
                       'parameters': {'type': 'object', 'properties': {'input': schema},
                                      'required': ['input'], 'additionalProperties': False}})
    else:
        if type(parameters) is not dict:
            raise ValueError('Flat tool declaration requires an object schema')
        schema = json_copy(parameters)
        if schema.get('type') != 'object':
            raise ValueError('Flat tool declaration requires an object schema')
        result['parameters'] = schema
    if image_output is not None:
        if type(image_output) is not bool:
            raise ValueError('image_output must be Bool')
        result['imageOutput'] = image_output
    if category is not None:
        if type(category) is not str or len(category) > 120:
            raise ValueError('Invalid tool category')
        result['category'] = category
    return json_copy(result)
