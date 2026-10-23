"""Trusted registration of robot adapters; language programs select names only."""
from dataclasses import dataclass
import math
from types import MappingProxyType


def finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def actuator_descriptors(value):
    from .extensions import valid_name
    if type(value) is not dict or not 1 <= len(value) <= 16:
        raise ValueError('Robot actuators must declare 1..16 channels')
    result = {}
    for name, fields in value.items():
        valid_name(name)
        if type(fields) is not dict or set(fields) != {'minimum', 'maximum', 'unit'}:
            raise ValueError('Actuator requires minimum, maximum and unit')
        low, high, unit = fields['minimum'], fields['maximum'], fields['unit']
        if not finite(low) or not finite(high) or low > high:
            raise ValueError('Actuator bounds must be finite and ordered')
        if type(unit) is not str or not 1 <= len(unit) <= 64:
            raise ValueError('Actuator unit must be a nonempty string')
        result[name] = {'minimum': low, 'maximum': high, 'unit': unit}
    return result


def validate_channels(channels, descriptors):
    if type(channels) is not dict or set(channels) != set(descriptors):
        raise ValueError('Command channels must match registered actuators')
    result = {}
    for name, value in channels.items():
        fields = descriptors[name]
        if not finite(value) or not fields['minimum'] <= value <= fields['maximum']:
            raise ValueError('Actuator value must be finite and within registered bounds')
        result[name] = value
    return result


@dataclass(frozen=True)
class RobotSpec:
    name: str
    factory: object
    actuators: object
    profile: str = 'robot/1'
    description: str = ''
    version: str = '1'
    resource_id: object = None
    max_observation_age_ms: int = 1000

    def __post_init__(self):
        from .extensions import valid_name
        valid_name(self.name)
        if not callable(self.factory):
            raise ValueError('Robot factory must be callable')
        for name, maximum in (('profile', 128), ('version', 64)):
            value = getattr(self, name)
            if type(value) is not str or not 1 <= len(value) <= maximum:
                raise ValueError('Invalid Robot ' + name)
        if type(self.description) is not str or len(self.description) > 4000:
            raise ValueError('Invalid Robot description')
        if self.resource_id is not None and (type(self.resource_id) is not str or not 1 <= len(self.resource_id) <= 1024):
            raise ValueError('Invalid Robot resource identity')
        if type(self.max_observation_age_ms) is not int or not 1 <= self.max_observation_age_ms <= 10000:
            raise ValueError('Observation age must be 1..10000 ms')
        rows = actuator_descriptors(self.actuators)
        object.__setattr__(self, 'actuators', MappingProxyType({name: MappingProxyType(fields) for name, fields in rows.items()}))

    def describe(self):
        return {'name': self.name, 'profile': self.profile, 'description': self.description,
                'version': self.version, 'actuators': {name: dict(fields) for name, fields in self.actuators.items()},
                'maxObservationAgeMs': self.max_observation_age_ms}
