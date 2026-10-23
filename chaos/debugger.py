"""Cooperative source debugger. Snapshots are bounded data, never host evaluation."""
import copy
import json
import math
import threading
import time
from types import GetSetDescriptorType
from .language import ResourceLimit
from .store import Conflict
from .operators import NOT_IMPLEMENTED
from .indexing import Slice
from .tuple_values import TupleValue
from .set_values import SetValue
from .modes import Plan, Goal, Skill
from .iteration import Iterator, AsyncIterator
from .task_groups import TaskGroup
from .cancellation import CancelShield
from .contexts import MANAGERS
from .resource_stacks import STACKS
from .channels import Channel, snapshot_info as channel_snapshot_info
from .channel_select import ChannelCase, describe as describe_case
from .objects import ChaosClass, ChaosInstance, ClassMember, BoundMethod, SuperProxy, StaticMethod, ClassMethod, Property, descriptor_protocols


RESUME_COMMANDS = {'continue', 'step_into', 'step_over', 'step_out'}
MAX_BREAKPOINTS = 100


def options(value, files):
    if value is None or value is False:
        return None
    if value is True:
        value = {}
    if not isinstance(value, dict) or set(value) - {'breakpoints', 'pauseOnEntry', 'pauseOnCondition', 'pauseTimeout'}:
        raise ValueError('debug must contain only breakpoints, pauseOnEntry, pauseOnCondition and pauseTimeout')
    pause = value.get('pauseOnEntry', True)
    condition_pause = value.get('pauseOnCondition', False)
    if type(condition_pause) is not bool:raise ValueError('pauseOnCondition must be a boolean')
    timeout = value.get('pauseTimeout', 300)
    if type(pause) is not bool:
        raise ValueError('pauseOnEntry must be a boolean')
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not .1 <= timeout <= 3600:
        raise ValueError('pauseTimeout must be finite and between 0.1 and 3600 seconds')
    return {'breakpoints': breakpoints(value.get('breakpoints', []), files),
            'pauseOnEntry': pause, 'pauseOnCondition':condition_pause, 'pauseTimeout': timeout}


def breakpoints(value, files):
    if not isinstance(value, list) or len(value) > MAX_BREAKPOINTS:
        raise ValueError('breakpoints must be a list of at most 100 source locations')
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {'file', 'line'}:
            raise ValueError('Each breakpoint requires file and line only')
        file, line = item['file'], item['line']
        if not isinstance(file, str) or file not in files:
            raise ValueError('Breakpoint file is not in the fixed run snapshot')
        if type(line) is not int or not 1 <= line <= len(files[file].split('\n')):
            raise ValueError('Breakpoint line is outside its source file')
        if (file, line) not in seen:
            result.append({'file': file, 'line': line}); seen.add((file, line))
    return result


def preview(value, depth=0, seen=None, budget=None, redact=lambda value: value):
    """Avoid repr, properties, callbacks, owner references and arbitrary serialization."""
    budget = [1200] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0: return {'truncated': True}
    seen = set() if seen is None else seen
    if value is NOT_IMPLEMENTED:return {'$notImplemented': True}
    if value is None or type(value) in (bool, int):
        return value if type(value) is not int or value.bit_length() < 4096 else {'type': 'Int', 'truncated': True}
    if type(value) is float:
        return value if math.isfinite(value) else {'type': 'Number', 'value': 'non-finite'}
    from .dynamic_contexts import VALUES as CONTEXT_VALUES,describe as binding_describe
    if type(value) in CONTEXT_VALUES:return binding_describe(value)
    from .eql_specializers import EqlSpecializer, metadata as eql_metadata
    if type(value) is EqlSpecializer:return {'type':'EqlSpecializer',**eql_metadata(value)}
    from .error_values import Error
    if type(value) is Error:
        if depth >= 4:return {'type':'Error','truncated':True}
        from .error_values import describe as describe_error
        return preview(describe_error(value),depth+1,seen,budget,redact)
    from .condition_values import Condition
    from .conditions import VALUES as RECOVERY_VALUES,describe as recovery_describe
    if type(value) is Condition:
        return preview({'type':'Condition','kind':value.kind,'message':value.message,'data':value.data},depth+1,seen,budget,redact)
    if type(value) in RECOVERY_VALUES:return preview({'type':type(value).__name__,**recovery_describe(value)},depth+1,seen,budget,redact)
    from .numeric_values import Fraction,Complex,Decimal
    if type(value) in (Fraction,Complex,Decimal):
        fields=('sign','coefficient','exponent') if type(value) is Decimal else ('numerator','denominator') if type(value) is Fraction else ('real','imag')
        return {'type':type(value).__name__,**{name:preview(object.__getattribute__(value,name),depth+1,seen,budget,redact) for name in fields}}
    from .decimal_values import DecimalContext,info as decimal_info
    if type(value) is DecimalContext:return {'type':'DecimalContext',**decimal_info(value)}
    if type(value) is str:
        value = redact(value)
        return value[:500] + ('…' if len(value) > 500 else '')
    if depth >= 4:
        return {'type': type(value).__name__, 'truncated': True}
    if id(value) in seen:
        return {'type': type(value).__name__, 'circular': True}
    seen.add(id(value))
    try:
        if type(value) in (TupleValue, SetValue):
            items = [preview(v, depth + 1, seen, budget, redact) for v in value.items[:30]]
            return {'type': 'Set' if type(value) is SetValue else 'Tuple', 'items': items,
                    **({'truncated': len(value) - 30} if len(value) > 30 else {})}
        if type(value) is list:
            out = [preview(v, depth + 1, seen, budget, redact) for v in value[:30]]
            if len(value) > 30: out.append({'truncated': len(value) - 30})
            return out
        if type(value) is dict:
            out = {redact(k)[:160]: preview(v, depth + 1, seen, budget, redact) for k, v in list(value.items())[:30] if type(k) is str}
            if len(value) > 30: out['…'] = {'truncated': len(value) - 30}
            return out
        kind = type(value).__name__
        descriptor = next((vars(cls)['__dict__'] for cls in type(value).__mro__ if '__dict__' in vars(cls)), None)
        attrs = object.__getattribute__(value, '__dict__') if isinstance(descriptor, GetSetDescriptorType) else {}
        if type(value) is ClassMember:
            return {'type':'ClassMember',**{key:preview(attrs.get(key),depth+1,seen,budget,redact) for key in ('value','mutable','method','contract')}}
        if type(value) in (Plan,Goal,Skill):
            raw=attrs.get('definition_json')
            return {'type':kind,'definition':preview(json.loads(raw),depth+1,seen,budget,redact) if type(raw) is str else None}
        if type(value) is Slice:
            return {'type':'Slice',**{key:preview(attrs.get(key),depth+1,seen,budget,redact) for key in ('start','stop','step')}}
        if type(value) in STACKS:
            return {'type':kind, 'id':attrs.get('id'), 'state':attrs.get('state'),
                    'entryCount':len(attrs.get('entries', [])), 'maxEntries':attrs.get('max_entries')}
        if type(value) in MANAGERS:
            return {'type':kind, 'id':attrs.get('id'), 'state':attrs.get('state')}
        if type(value) is CancelShield:
            from .cancellation import snapshot_info as shield_info
            return preview({'type':'CancelShield', **shield_info(value)}, depth + 1, seen, budget, redact)
        if type(value) is TaskGroup:
            members = attrs.get('members', [])
            body = attrs.get('body')
            snapshot = {'type':'TaskGroup', 'id':attrs.get('id'), 'state':attrs.get('state'),
                        'ownerTaskId':attrs.get('owner_task_id'),
                        'bodyTaskId':vars(body).get('id') if body is not None else None,
                        'parentGroupId':attrs.get('parent_group_id'),
                        'memberTaskIds':[vars(task).get('id') for task in members[:30]],
                        'memberCount':len(members), 'membersTruncated':len(members)>30}
            return preview(snapshot, depth + 1, seen, budget, redact)
        if type(value) is ChannelCase:
            # Native descriptor metadata only; captured message stays private.
            return preview({'type':'ChannelCase', **describe_case(value)}, depth + 1, seen, budget, redact)
        if type(value) is Channel:
            # The interpreter thread owns this checkpoint. The native snapshot
            # reads bounded metadata only, never message payloads or callbacks.
            metadata = channel_snapshot_info(value)
            safe = {'type':'Channel', **{key:metadata[key] for key in
                    ('id','state','capacity','buffered','waitingSenders','waitingReceivers')}}
            return preview(safe, depth + 1, seen, budget, redact)
        if type(value) in (Iterator,AsyncIterator):
            result={'type':kind}
            for key in ('id','state'):
                if type(attrs.get(key)) is str:result[key]=redact(attrs[key])[:160]
            for key in ('busy','close_attempted'):
                if type(attrs.get(key)) is bool:result[key]=attrs[key]
            return result
        if kind == 'Cell':
            return preview(attrs.get('value'), depth + 1, seen, budget, redact)
        if type(value) is ChaosClass:
            return {'type':'Class','name':redact(attrs['name'])[:160],
                    'bases':[redact(vars(base)['name'])[:160] for base in attrs['bases'][:30]],
                    'members':[redact(name)[:160] for name in list(attrs['members'])[:30]],
                    **({'metaclass':redact(vars(attrs['metaclass'])['name'])[:160]} if type(attrs.get('metaclass')) is ChaosClass else {})}
        if type(value) is ChaosInstance:
            klass=attrs['klass'];fields={}
            for base in reversed(vars(klass)['mro']):
                for name,member in vars(base)['members'].items():
                    if not vars(member)['method']:fields[name]=vars(member)['value']
            fields.update(attrs['fields'])
            safe={}
            for name,item in list(fields.items())[:30]:
                key=redact(name)[:160]
                sensitive=''.join(c for c in name.lower() if c.isalnum()) in {'apikey','authorization','password','secret','token','accesstoken','refreshtoken'}
                safe[key]='[redacted]' if sensitive else preview(item,depth+1,seen,budget,redact)
            return {'type':'Instance','class':redact(vars(klass)['name'])[:160],'fields':safe,
                    **({'truncated':len(fields)-30} if len(fields)>30 else {}),
                    **({'protocols':sorted(descriptor_protocols(value))} if descriptor_protocols(value) else {})}
        if type(value) in (StaticMethod,ClassMethod,Property):
            result={'type':'Descriptor','kind':type(value).__name__}
            if type(value) is Property:result.update(getter=attrs['fget'] is not None,setter=attrs['fset'] is not None,deleter=attrs.get('fdel') is not None)
            else:result['value']=preview(attrs['value'],depth+1,seen,budget,redact)
            return result
        if type(value) is BoundMethod:
            target=preview(attrs['function'],depth+1,seen,budget,redact)
            name=target.get('name',target.get('class',target.get('type','callable'))) if type(target) is dict else 'callable'
            return {'type':'BoundMethod','name':name,
                    'class':redact(vars(attrs['defining_class'])['name'])[:160],
                    'receiverClass':redact(vars(attrs['receiver'] if type(attrs['receiver']) is ChaosClass else vars(attrs['receiver'])['klass'])['name'])[:160]}
        if type(value) is SuperProxy:
            return {'type':'Super','after':redact(vars(attrs['start_class'])['name'])[:160],
                    'receiverClass':redact(vars(attrs['receiver'] if type(attrs['receiver']) is ChaosClass else vars(attrs['receiver'])['klass'])['name'])[:160]}
        result = {'type': kind}
        if kind in {'Function', 'Native', 'Agent', 'DataType', 'Generator', 'AsyncGenerator', 'Task', 'ChaosGenerator', 'ChaosTask'}:
            for key in ('name', 'state', 'status'):
                if type(attrs.get(key)) is str: result[key] = redact(attrs[key])[:160]
        return result
    finally:
        seen.discard(id(value))


def restart_arguments(args,kwargs):
    """Strict copied JSON data; validate the combined boundary before waking."""
    from .extensions import json_copy
    if type(args) is not list or type(kwargs) is not dict:
        raise ValueError("Restart args must be a List and kwargs must be a Record")
    values=json_copy([args,kwargs],max_chars=100000,max_nodes=10000,max_depth=64)
    return values[0],values[1]


def recovery_metadata(condition,restarts,total,redact=lambda value:value):
    """Called only by the interpreter; never discover callbacks from HTTP."""
    from .condition_values import info
    from .conditions import describe
    value=info(condition)
    budget=[1200]
    def sensitive(item):
        if type(item) is list:return [sensitive(value) for value in item]
        if type(item) is dict:
            return {key:("[redacted]" if "".join(c for c in key.lower() if c.isalnum()) in
                         {"apikey","authorization","password","secret","token","accesstoken","refreshtoken"}
                         else sensitive(value)) for key,value in item.items()}
        return item
    data={"kind":value["kind"],"message":value["message"],"data":sensitive(preview(value["data"],budget=budget,redact=redact))}
    return {"condition":data,"restarts":[{key:item for key,item in describe(restart).items() if key!="$handle"} for restart in restarts],
            "total":total,"truncated":total>len(restarts)}


def locals_snapshot(env, budget=None, redact=lambda value: value):
    budget = [1200] if budget is None else budget
    values, visited, names, depth = [], set(), set(), 0
    while env is not None and id(env) not in visited and depth < 16:
        visited.add(id(env))
        raw = vars(env).get('values', {})
        if type(raw) is not dict: break
        for name, value in raw.items():
            if type(name) is not str or name in names: continue
            names.add(name)
            # Builtin callables carry host implementation pointers and add noise.
            if type(value).__name__ == 'Native': continue
            sensitive = ''.join(c for c in name.lower() if c.isalnum()) in {'apikey', 'authorization', 'password', 'secret', 'token', 'accesstoken', 'refreshtoken'}
            kind = 'Set' if type(value) is SetValue else 'Tuple' if type(value) is TupleValue else 'NotImplemented' if value is NOT_IMPLEMENTED else type(value).__name__
            values.append({'name': redact(name)[:160], 'type': kind, 'value': '[redacted]' if sensitive else preview(value, budget=budget, redact=redact), 'scope': depth})
            if len(values) >= 80: return values + [{'name': '…', 'type': 'truncated', 'value': None, 'scope': depth}]
        env = vars(env).get('parent'); depth += 1
    return values


def location(span, kind=None):
    out = {key: value for key, value in (span or {}).items()
           if key in ('file', 'line', 'column', 'endLine', 'endColumn') and type(value) in (str, int)}
    if kind: out['kind'] = kind
    return out


class DebugSession:
    def __init__(self, run_id, project, config, cancel, emit, redact=lambda value: value):
        self.run_id, self.project_id, self.version = run_id, project['id'], project['version']
        self.files, self.entry = dict(project['files']), project['entry']
        from .generated_sources import Registry
        self.generated_sources=Registry()
        self.config, self.cancel_event, self.emit, self.redact = config, cancel, emit, redact
        self.condition = threading.Condition(threading.RLock())
        self.status, self.pause_id, self.reason = 'running', 0, None
        self.pause_requested = config['pauseOnEntry']
        self.breakpoint_set = {(p['file'], p['line']) for p in config['breakpoints']}
        self.current_location, self.frames = None, []
        self.task_snapshot = {'tasks': [], 'total': 0, 'truncated': False}
        self.mode, self.origin, self.origin_frames = 'continue', None, ()
        self.suppressed_line, self.module_envs = None, {}
        self.suppressed_nodes = set()
        self.pause_deadline, self.error = None, None
        self.total_paused = 0.0
        self.pause_started = None
        self.condition_recovery, self.restart_selection = None, None

    def register_source(self, source):
        with self.condition:
            if self.status not in ('running','paused'):return
            self.generated_sources.register(source,self.files)

    def preflight_sources(self,sources):
        with self.condition:self.generated_sources.preflight(sources,self.files)

    def register_sources(self,sources):
        with self.condition:
            if self.status in ('running','paused'):self.generated_sources.register_many(sources,self.files)

    def paused_seconds(self):
        with self.condition:
            return self.total_paused + (time.monotonic() - self.pause_started if self.pause_started is not None else 0)

    def _commands(self):
        if self.status == 'paused':
            commands = ['continue', 'step_into', 'step_over', 'breakpoints', 'cancel']
            if len(self.frames) > 1: commands.insert(3, 'step_out')
            if self.condition_recovery and self.condition_recovery['restarts']:commands.insert(0,'restart')
            return commands
        return ['pause', 'breakpoints', 'cancel'] if self.status == 'running' else []

    def snapshot(self):
        with self.condition:
            result = {'runId': self.run_id, 'projectId': self.project_id, 'version': self.version,
                      'status': self.status, 'pauseId': self.pause_id, 'reason': self.reason,
                      'location': self.current_location, 'frames': self.frames, 'taskSnapshot': self.task_snapshot,
                      'conditionRecovery':self.condition_recovery,
                      'breakpoints': [{'file': f, 'line': line} for f, line in sorted(self.breakpoint_set)],
                      'generatedSources':self.redact({file:source.record() for file,source in self.generated_sources.values.items()}),
                      'generatedSourceContexts':self.redact(self.generated_sources.contexts()),
                      'commands': self._commands(), 'pauseTimeout': self.config['pauseTimeout'],
                      'pauseRemaining': max(0, self.pause_deadline - time.monotonic()) if self.pause_deadline else None,
                      'totalPausedSeconds': round(self.total_paused, 3), 'error': self.error}
            return copy.deepcopy(result)

    def command(self, command, *, pause_id=None, points=None, restart_id=None, args=None, kwargs=None):
        if not isinstance(command, str): raise ValueError('Debugger command must be a string')
        with self.condition:
            if command == 'breakpoints':
                if self.status not in ('running', 'paused'): raise Conflict('Debug session has ended')
                validated = breakpoints(points, {**self.files,**{file:value.source for file,value in self.generated_sources.values.items()}})
                self.breakpoint_set = {(p['file'], p['line']) for p in validated}
            elif command == 'pause':
                if self.status != 'running': raise Conflict('Debug session is not running')
                self.pause_requested = True
            elif command in RESUME_COMMANDS or command=='restart':
                if self.status != 'paused': raise Conflict('Debug session is not paused')
                if self.cancel_event.is_set():raise Conflict('Debug session has been cancelled')
                if self.pause_deadline is None or time.monotonic()>=self.pause_deadline:
                    raise Conflict('Debug pause has expired')
                if type(pause_id) is not int or pause_id != self.pause_id:
                    raise Conflict('Pause changed; read current debugger state before resuming')
                if command == 'step_out' and len(self.frames) <= 1:
                    raise ValueError('Cannot step out of the module frame')
                if command=='restart':
                    if not self.condition_recovery:raise Conflict('Pause has no Condition recovery')
                    if type(restart_id) is not str or not any(item['id']==restart_id for item in self.condition_recovery['restarts']):
                        raise Conflict('Restart is not available at this pause')
                    copied_args,copied_kwargs=restart_arguments(args,kwargs)
                    if self.cancel_event.is_set() or time.monotonic()>=self.pause_deadline:
                        raise Conflict('Debug pause expired or was cancelled')
                    self.restart_selection={'restartId':restart_id,'args':copied_args,'kwargs':copied_kwargs}
                self.mode, self.status = command, 'running'
                self.condition_recovery=None
                self.frames = []; self.pause_deadline = None
                self.task_snapshot = {'tasks': [], 'total': 0, 'truncated': False}
                self.condition.notify_all()
            else:
                raise ValueError('Unknown debugger command')
            return self.snapshot()

    def wake(self):
        with self.condition:
            self.condition.notify_all()

    def finish(self, status, error=None):
        with self.condition:
            self.status, self.error = status, error
            self.frames = []; self.module_envs.clear(); self.pause_deadline = None
            self.condition_recovery=self.restart_selection=None
            self.task_snapshot = {'tasks': [], 'total': 0, 'truncated': False}
            self.condition.notify_all()

    def pause_condition(self, metadata, node, env, stack, *, task_snapshot=None):
        """Wait for copied data while the interpreter retains its live extent."""
        span=getattr(node,"span",{})
        raw_frames=list(stack)
        file=span.get("file",self.entry)
        module_file=(raw_frames[0].get("span") or {}).get("file",file) if raw_frames else file
        logical=[{"id":"module:"+module_file,"name":"<module>","env":self.module_envs.get(module_file,env),"span":{}}]+raw_frames
        with self.condition:
            if self.cancel_event.is_set():return 0.0,None
            if self.status!="running":raise Conflict("Debugger is not running")
            self.condition_recovery=self.redact(copy.deepcopy(metadata))
            self.restart_selection=None
            self.reason="condition";self.status="paused";self.pause_id+=1;self.pause_requested=False
            self.current_location=location(span,"condition")
            self.origin=(file,span.get("line"),str(logical[-1]["id"]))
            self.origin_frames=tuple(str(frame["id"]) for frame in logical)
            self.suppressed_line=self.origin;self.suppressed_nodes={id(node)}
            budget=[1200];frames=[]
            for index,frame in enumerate(logical[-40:]):
                active=index==len(logical[-40:])-1
                next_span=span if active else logical[-40:][index+1].get("span",{})
                frames.append({"id":str(frame["id"]),"name":self.redact(str(frame.get("name","<anonymous>")))[:160],
                               "location":location(next_span),"locals":locals_snapshot(env if active else frame.get("env"),budget,self.redact),
                               "taskId":frame.get("taskId")})
            self.frames=self.redact(list(reversed(frames)))
            self.task_snapshot=self.redact(copy.deepcopy(task_snapshot)) if task_snapshot is not None else {"tasks":[],"total":0,"truncated":False}
            started=time.monotonic();self.pause_started=started;self.pause_deadline=started+self.config["pauseTimeout"]
            self.emit("debug.paused",{"pauseId":self.pause_id,"reason":self.reason,"location":self.current_location})
            try:
                while self.status=="paused" and not self.cancel_event.is_set():
                    remaining=self.pause_deadline-time.monotonic()
                    if remaining<=0:
                        self.status="failed";self.error="调试暂停超时；执行已终止"
                        raise ResourceLimit(self.error)
                    self.condition.wait(min(.1,remaining))
                selection=self.restart_selection
            finally:
                elapsed=time.monotonic()-started;self.total_paused+=elapsed;self.pause_started=None;self.pause_deadline=None
                self.restart_selection=self.condition_recovery=None
            self.emit("debug.resumed",{"pauseId":self.pause_id,"command":self.mode,"pausedSeconds":round(elapsed,3)})
            return elapsed,selection

    def checkpoint(self, node, env, stack, *, task_snapshot=None):
        span = getattr(node, 'span', {})
        file, line = span.get('file'), span.get('line')
        if file not in self.files and file not in self.generated_sources.values or type(line) is not int or node[0] == 'block': return 0.0
        raw_frames = list(stack)
        if not raw_frames: self.module_envs[file] = env
        module_file = (raw_frames[0].get('span') or {}).get('file', file) if raw_frames else file
        logical = [{'id': 'module:' + module_file, 'name': '<module>', 'env': self.module_envs.get(module_file, env), 'span': {}}] + raw_frames
        ids = tuple(str(frame['id']) for frame in logical)
        line_key = (file, line, ids[-1])
        with self.condition:
            if self.cancel_event.is_set(): return 0.0
            if self.suppressed_line is not None and line_key != self.suppressed_line:
                self.suppressed_line = None
            if self.suppressed_line == line_key:
                # A repeated AST node is a new loop iteration, rather than a child
                # expression of the line where Continue was pressed.
                if id(node) in self.suppressed_nodes: self.suppressed_line = None
                else: self.suppressed_nodes.add(id(node))
            hit = (file, line) in self.breakpoint_set and line_key != self.suppressed_line
            step = self.mode == 'step_into' and line_key != self.origin
            if self.mode == 'step_over':
                same = ids[-1] == self.origin_frames[-1] if self.origin_frames else False
                caller = len(ids) < len(self.origin_frames) and ids == self.origin_frames[:len(ids)]
                step = (same or caller) and line_key != self.origin
            if self.mode == 'step_out':
                step = len(ids) < len(self.origin_frames) and ids == self.origin_frames[:len(ids)]
            if not (self.pause_requested or hit or step): return 0.0
            self.reason = 'entry' if self.pause_requested and self.pause_id == 0 and self.config['pauseOnEntry'] else 'pause' if self.pause_requested else 'breakpoint' if hit else self.mode
            self.pause_requested = False; self.status = 'paused'; self.pause_id += 1
            self.current_location = location(span, node[0]); self.origin = line_key; self.origin_frames = ids
            self.suppressed_line = line_key; self.suppressed_nodes = {id(node)}
            frames = []; value_budget = [1200]
            for index, frame in enumerate(logical[-40:]):
                active = index == len(logical[-40:]) - 1
                next_span = span if active else logical[-40:][index + 1].get('span', {})
                frames.append({'id': str(frame['id']), 'name': self.redact(str(frame.get('name', '<anonymous>')))[:160],
                               'location': location(next_span), 'locals': locals_snapshot(env if active else frame.get('env'), value_budget, self.redact),
                               'taskId': frame.get('taskId')})
            self.frames = self.redact(list(reversed(frames)))
            # Captured by the interpreter at this exact pause. HTTP readers only
            # receive this copied data and never inspect the live scheduler.
            self.task_snapshot = self.redact(copy.deepcopy(task_snapshot)) if task_snapshot is not None else {'tasks': [], 'total': 0, 'truncated': False}
            started = time.monotonic(); self.pause_started = started; self.pause_deadline = started + self.config['pauseTimeout']
            self.emit('debug.paused', {'pauseId': self.pause_id, 'reason': self.reason, 'location': self.current_location})
            try:
                while self.status == 'paused' and not self.cancel_event.is_set():
                    remaining = self.pause_deadline - time.monotonic()
                    if remaining <= 0:
                        self.status = 'failed'; self.error = '调试暂停超时；执行已终止'
                        raise ResourceLimit(self.error)
                    self.condition.wait(min(.1, remaining))
            finally:
                elapsed = time.monotonic() - started; self.total_paused += elapsed; self.pause_started = None
                self.pause_deadline = None
            self.emit('debug.resumed', {'pauseId': self.pause_id, 'command': self.mode, 'pausedSeconds': round(elapsed, 3)})
            return elapsed
