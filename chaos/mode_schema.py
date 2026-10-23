"""Pure public Plan definition schema and shared execution bounds."""

PLAN_MAX_CONCURRENCY = 128


def plan_definition_schema():
    identifier = {'type': 'string', 'pattern': r'^[A-Za-z_][A-Za-z0-9_.-]{0,127}$'}
    return {
        'type': 'object', 'additionalProperties': False,
        'required': ['kind', 'name', 'steps'],
        'properties': {
            'kind': {'const': 'plan'},
            'name': {'type': 'string', 'minLength': 1, 'maxLength': 200},
            'max_concurrency': {
                'type': 'integer', 'minimum': 1, 'maximum': PLAN_MAX_CONCURRENCY, 'default': 1,
                'description': 'Maximum active logical steps. Omitted or 1 preserves serial order; explicit 1 is omitted from the canonical definition and hash. Dependencies must complete before a step starts. Host effect worker limits still apply.'},
            'steps': {'type': 'array', 'minItems': 1, 'maxItems': 128, 'items': {
                'type': 'object', 'additionalProperties': False, 'required': ['id', 'call'],
                'properties': {'id': dict(identifier), 'call': dict(identifier),
                               'needs': {'type': 'array', 'maxItems': 128, 'uniqueItems': True,
                                         'items': {'type': 'string'}, 'default': []}}}},
        },
        'description': 'Pure versioned Plan definition. The shared language validator also checks unique step ids, existing dependencies and acyclicity before effects.',
    }
