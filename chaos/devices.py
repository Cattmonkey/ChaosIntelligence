"""Trusted host device registration; adapters never become language values."""
from dataclasses import dataclass

@dataclass(frozen=True)
class DeviceSpec:
    name: str
    factory: object
    description: str=''
    version: str='1'
    resource_id: object=None
    def __post_init__(self):
        from .extensions import valid_name
        valid_name(self.name)
        if not callable(self.factory):raise ValueError('Device factory must be callable')
        if type(self.description)is not str or len(self.description)>4000:raise ValueError('Invalid Device description')
        if type(self.version)is not str or not 1<=len(self.version)<=64:raise ValueError('Invalid Device version')
        if self.resource_id is not None and (type(self.resource_id)is not str or not 1<=len(self.resource_id)<=1024):raise ValueError('Invalid Device resource identity')
    def describe(self):return {'name':self.name,'description':self.description,'version':self.version,'profile':'chaos-hid/1'}
