"""Public, process-local extension contracts. Host callbacks are trusted Python."""
import copy
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from .language import LanguageError, TYPES, check_type


BUILTIN_TOOLS = {
    'computer.control': {'label':'注册电脑与移动设备','effect':'write','reserved':True,'description':'仅原生 Computer 绑定效果；按名取得宿主注册设备，不自动授权或重放输入'},
    'robot.control': {'label':'原生机器人','effect':'write','reserved':True,'description':'仅原生 Robot 效果；发送动作不表示运动已验证'},
    'device.control': {'label':'物理输入设备','effect':'write','reserved':True,'description':'仅原生 Device 效果；HID 操作目标机全局焦点'},
    'code.execute': {'label':'代码解释器','effect':'write','description':'在任务目录执行 Python/shell；可信主机进程，受超时与取消约束'},
    'browser.control': {'label':'后台浏览器','effect':'write','description':'隔离无头浏览器与虚拟光标，不触碰桌面鼠标'},
    'image.generate': {'label':'图像生成','effect':'write','description':'使用配置的图像提供方生成并保存图像'},
    'text.normalize': {'label': '文本归一化', 'effect': 'pure', 'description': '去除首尾空白并合并连续空白'},
    'text.stats': {'label': '结构统计', 'effect': 'pure', 'description': '字符、词、行与句子统计'},
    'data.select': {'label': '字段选择', 'effect': 'pure', 'description': '输入 {record, key}，输出记录字段'},
    'math.evaluate': {'label': '纯函数求值', 'effect': 'pure', 'description': '在禁用工具与智能体的环境执行 Chaos 表达式'},
    'memory.get': {'label': '读取记忆', 'effect': 'read', 'description': '读取当前项目内的键'},
    'memory.put': {'label': '写入记忆', 'effect': 'write', 'description': '输入 {key, value}，持久化到当前项目'},
    'memory.read': {'label': '读取记忆版本', 'effect': 'read', 'description': '输入键 String，返回 {key,exists,value,revision}；旧数据与不存在的键均可为版本0，以exists区分'},
    'memory.compare_exchange': {'label': '按版本提交记忆', 'effect': 'write', 'description': '输入 {key,expectedRevision,value}；同一项目和键的版本一致才原子写入并递增版本，返回当前记忆及swapped；冲突不重试'},
    'graph.inspect': {'label': '查看拓扑', 'effect': 'read', 'description': '读取当前执行快照'},
    'graph.patch': {'label': '调整拓扑', 'effect': 'topology', 'description': '仅平台事务可执行'},
    'session.manage': {'label':'管理原生 AgentSession','effect':'write','reserved':True,'description':'仅专用语言效果或 SDK/API 可用，不是模型工具'},
    'process.manage': {'label':'管理持久 Process','effect':'write','reserved':True,'description':'仅专用语言效果或 SDK/API 可用的权限，不可作为工具调用'},
    'program.adopt': {'label': '采纳程序版本', 'effect': 'write', 'reserved': True, 'description': '仅 program_adopt 语言效果或专用 SDK/API 可使用的权限；不可作为工具调用'},
}
DEFAULT_CAPABILITIES = tuple(sorted(set(BUILTIN_TOOLS) - {'computer.control','robot.control','device.control','graph.patch','program.adopt','process.manage','session.manage','code.execute','browser.control','image.generate'}))
TERMINAL_STATES = frozenset(('completed', 'failed', 'cancelled', 'interrupted'))


def capability_scope(descriptor):
    """One host contract: ordinary tool, native language permission, or platform transaction."""
    if descriptor.get('reserved', False):
        return 'language'
    return 'platform' if descriptor.get('effect') == 'topology' else 'tool'


def language_permissions(descriptors=None):
    descriptors = BUILTIN_TOOLS if descriptors is None else descriptors
    return frozenset(name for name, descriptor in descriptors.items()
                     if capability_scope(descriptor) == 'language')


def callback_capabilities_allowed(capabilities, requested, *, allowed=None, descriptors=None):
    """Explicit language callback grants, separate from model host tools.

    This predicate describes delegation; only a live caller can supply the
    actual authority ceiling. Public contracts and JSON never grant authority.
    """
    declared = frozenset(capabilities)
    if not declared.issubset(frozenset(requested) | language_permissions(descriptors)):
        return False
    return allowed is None or declared.issubset(allowed)


def capability_inventory(descriptors):
    """Copied authoring metadata; does not invoke tools or grant capabilities."""
    return {name: {**copy.deepcopy(descriptor), 'scope': capability_scope(descriptor),
                   'callable': capability_scope(descriptor) == 'tool'}
            for name, descriptor in descriptors.items()}



def json_copy(value, max_chars=250000, max_nodes=20000, max_depth=100, checkpoint=None):
    """Bounded JSON tree copy, also rejects cycles, callables and non-finite floats."""
    count, chars = 0, 0
    def visit(v, depth=0):
        nonlocal count, chars
        count += 1
        if checkpoint is not None: checkpoint()
        if count > max_nodes or depth > max_depth:
            raise ValueError('JSON value exceeds node/depth budget')
        if v is None or type(v) is bool:
            return v
        if type(v) is int:
            if v.bit_length() > 14000:
                raise ValueError('Integer exceeds bit budget')
            return v
        if type(v) is float:
            if not math.isfinite(v):
                raise ValueError('JSON numbers must be finite')
            return v
        if type(v) is str:
            v.encode("utf-8")
            chars += len(v)
            if chars > max_chars:
                raise ValueError('JSON text exceeds character budget')
            return v
        if type(v) is list:
            if len(v) > 10000:
                raise ValueError('JSON list exceeds item budget')
            return [visit(x, depth + 1) for x in v]
        if type(v) is dict:
            if not all(type(k) is str for k in v):
                raise ValueError('JSON object keys must be strings')
            return {visit(k, depth + 1): visit(x, depth + 1) for k, x in v.items()}
        raise ValueError('Extension boundaries require JSON values, got ' + type(v).__name__)
    return visit(value)


def valid_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,127}', name):
        raise ValueError('Extension name must be a 1–128 character identifier')
    return name


@dataclass(frozen=True)
class ToolSpec:
    name: str
    handler: object
    description: str = ''
    effect: str = 'pure'
    input_type: str = 'Any'
    output_type: str = 'Any'
    label: str = ''
    version: str = '1'

    def __post_init__(self):
        valid_name(self.name)
        if not callable(self.handler):
            raise ValueError('Tool handler must be callable')
        if self.effect not in ('pure', 'read', 'write'):
            raise ValueError('Tool effect must be pure/read/write; topology is reserved')
        json_types = {'Any', 'Number', 'Int', 'String', 'Bool', 'List', 'Record', 'Null'}
        if self.input_type not in json_types or self.output_type not in json_types:
            raise ValueError('Tool contracts must use JSON-compatible Chaos types')
        for name, limit in (('description', 4000), ('label', 120), ('version', 64)):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError('Invalid tool ' + name)

    def describe(self):
        return {'label': self.label or self.name, 'description': self.description, 'effect': self.effect,
                'inputType': self.input_type, 'outputType': self.output_type, 'version': self.version, 'custom': True}


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    handler: object
    description: str = ''
    version: str = '1'

    def __post_init__(self):
        valid_name(self.name)
        if not callable(self.handler) or not isinstance(self.description, str) or len(self.description) > 4000:
            raise ValueError('Invalid provider handler or description')
        if not isinstance(self.version, str) or not 1 <= len(self.version) <= 64:
            raise ValueError('Invalid provider version')


@dataclass(frozen=True)
class AgentRequest:
    """A copied observation for one bounded agent transition."""
    name: str
    prompt: str
    input: object
    step: int
    max_steps: int
    tools: dict
    history: list


@dataclass(frozen=True)
class Final:
    output: object


@dataclass(frozen=True)
class ToolCall:
    name: str
    input: object


@dataclass(frozen=True)
class ExtensionContext:
    """Cooperative lifecycle information; no store/engine handle is exposed."""
    project_id: str
    run_id: object
    node_id: str
    deadline: float
    _checkpoint: object
    _is_cancelled: object

    def checkpoint(self):
        self._checkpoint()

    @property
    def cancelled(self):
        return self._is_cancelled()


class Registry:
    """Collect extensions before creating a Framework; each engine snapshots them."""
    def __init__(self):
        self._tools, self._providers, self._devices, self._robots, self._computers = {}, {}, {}, {}, {}

    def register_computer(self,spec):
        from .computer_adapters import ComputerSpec
        if type(spec) is not ComputerSpec:raise TypeError('Expected ComputerSpec')
        if spec.name in self._computers:raise ValueError('Computer binding already registered')
        self._computers[spec.name]=spec;return spec

    def computer_snapshot(self):return MappingProxyType(dict(self._computers))

    def register_robot(self,spec):
        from .robots import RobotSpec
        if type(spec) is not RobotSpec:raise TypeError('Expected RobotSpec')
        if spec.name in self._robots:raise ValueError('Robot binding already registered')
        self._robots[spec.name]=spec;return spec

    def robot_snapshot(self):return MappingProxyType(dict(self._robots))

    def register_device(self, spec):
        from .devices import DeviceSpec
        if type(spec) is not DeviceSpec: raise TypeError('Expected DeviceSpec')
        if spec.name in self._devices: raise ValueError('Device binding already registered')
        self._devices[spec.name]=spec
        return spec

    def device_snapshot(self):
        return MappingProxyType(dict(self._devices))

    def register_tool(self, spec):
        if not isinstance(spec, ToolSpec):
            raise TypeError('Expected ToolSpec')
        if spec.name in BUILTIN_TOOLS or spec.name in self._tools:
            raise ValueError('Tool name already registered or reserved: ' + spec.name)
        self._tools[spec.name] = spec
        return spec

    def register_provider(self, spec):
        if not isinstance(spec, ProviderSpec):
            raise TypeError('Expected ProviderSpec')
        if spec.name in ('local', 'model') or spec.name in self._providers:
            raise ValueError('Provider name already registered or reserved: ' + spec.name)
        self._providers[spec.name] = spec
        return spec

    def tool(self, name, **metadata):
        def decorate(handler):
            self.register_tool(ToolSpec(name, handler, **metadata))
            return handler
        return decorate

    def provider(self, name, **metadata):
        def decorate(handler):
            self.register_provider(ProviderSpec(name, handler, **metadata))
            return handler
        return decorate

    def snapshot(self):
        return MappingProxyType(dict(self._tools)), MappingProxyType(dict(self._providers))
