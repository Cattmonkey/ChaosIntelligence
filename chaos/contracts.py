"""Pure immutable composite contracts, shared by existing language boundaries.

A contract retains actual nominal descriptors. Checking never converts values,
executes protocols, or treats an ownership/control failure as a shape mismatch.
"""
from dataclasses import dataclass

MAX_NODES, MAX_DEPTH, MAX_FIELDS, MAX_ALTERNATIVES = 20000, 100, 10000, 128
MAX_DEFINITIONS, MAX_SYMBOL = 128, 128


def error(message, code='argument_error'):
    from .language import LanguageError
    raise LanguageError(message, code=code)


@dataclass(frozen=True, eq=False)
class Contract:
    owner: object
    kind: str
    arguments: tuple

    @property
    def name(self): return 'Contract('+self.kind+')'


def owned(value, evaluator):
    if type(value) is not Contract: error('Expected a native Contract')
    if value.owner is not evaluator: error('Contract cannot cross evaluators', 'ownership_error')
    return value


def symbol(value):
    if type(value) is not str or not 1 <= len(value) <= MAX_SYMBOL:
        error('Recursive contract names require exact nonempty Strings of up to 128 characters')
    try:value.encode('utf-8')
    except UnicodeError:error('Recursive contract names require valid Unicode')
    return value


def children(value):
    if value.kind == 'ref':return ()
    if value.kind == 'recursive':return tuple(spec for name,spec in value.arguments[0])
    if value.kind == 'record':
        return tuple(spec for fields in value.arguments[:2] for name, spec in fields)
    return value.arguments


class Budget:
    def __init__(self, evaluator, *, ticking=True):
        self.evaluator, self.ticking = evaluator, ticking
        self.nodes, self.chars, self.output_chars = 0, 0, 0
        self.active, self.validated = set(), {}
        self.matches, self.match_active = {}, set()
        self.groups, self.closed = {}, set()

    def step(self, depth=0):
        from .language import ResourceLimit
        if self.ticking: self.evaluator.tick()
        self.nodes += 1
        if self.nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ResourceLimit('Contract checking exceeds 20000 nodes or depth 100')

    def text(self, value):
        from .language import ResourceLimit
        self.chars += len(value)
        if self.chars > 250000: raise ResourceLimit('Contract names exceed 250000 characters')

    def output(self, record):
        from .language import ResourceLimit
        for name, value in record.items():
            self.output_chars += len(name)+(len(value) if type(value) is str else 0)
            if self.output_chars > 250000:
                raise ResourceLimit('Contract diagnostic output exceeds 250000 characters')
        return record

    @staticmethod
    def path(parent, suffix):
        result = parent+suffix if len(parent) < 1000 else parent
        return result if len(result) <= 1000 else result[:999]+'…'

    @staticmethod
    def field(name):
        return '['+repr(name[:120])+('…' if len(name)>120 else '')+']'

    def validate(self, spec, depth=0):
        from .language import TYPES, DataType
        from .objects import ChaosClass, owned as object_owned
        self.step(depth)
        if type(spec) is str:
            if spec not in TYPES: error('Contract requires a builtin type name or actual Class/DataType/Contract')
            return 0
        if type(spec) is ChaosClass:
            object_owned(spec, self.evaluator)
            if spec.building: error('Contract requires a completed Class')
            if type(spec.name) is not str: error('Nominal contract names require native Strings')
            self.text(spec.name)
            return 0
        if type(spec) is DataType:
            if type(spec.name) is not str or type(spec.variants) is not tuple or len(spec.variants)>MAX_FIELDS:
                error('DataType contract requires a String name and bounded native variant tuples')
            self.text(spec.name)
            for pair in spec.variants:
                self.step(depth+1)
                if type(pair) is not tuple or len(pair)!=2 or type(pair[0]) is not str or type(pair[1]) is not int or not 0<=pair[1]<=MAX_FIELDS:
                    error('DataType variants require String tags and nonnegative bounded Int arities')
                self.text(pair[0])
            return 0
        owned(spec, self.evaluator)
        identity = id(spec)
        if identity in self.active: error('Contract algebra cannot contain a cycle')
        if identity in self.validated:
            height = self.validated[identity]
            if depth + height > MAX_DEPTH:
                from .language import ResourceLimit
                raise ResourceLimit('Contract checking exceeds depth 100')
            return height
        if type(spec.kind) is not str or type(spec.arguments) is not tuple: error('Contract algebra requires a native kind and immutable arguments')
        args, kind = spec.arguments, spec.kind
        if kind == 'ref':
            if len(args)!=1:error('Invalid reference contract')
            self.text(symbol(args[0]))
        elif kind == 'recursive':
            if len(args)!=2 or type(args[0]) is not tuple or not 1<=len(args[0])<=MAX_DEFINITIONS:
                error('Recursive contract requires 1..128 immutable definitions and a root')
            self.text(symbol(args[1]));names=set()
            for pair in args[0]:
                self.step(depth+1)
                if type(pair) is not tuple or len(pair)!=2:error('Invalid recursive definition')
                name=symbol(pair[0]);self.text(name)
                if name in names:error('Recursive definition names must be unique')
                names.add(name)
            if args[1] not in names:error('Recursive contract root is not defined')
        elif kind == 'list':
            if len(args) != 1: error('Invalid list contract')
        elif kind == 'map':
            if len(args) != 2: error('Invalid map contract')
        elif kind == 'tuple':
            if len(args) > MAX_FIELDS: error('Tuple contract exceeds 10000 elements')
        elif kind == 'union':
            if not 1 <= len(args) <= MAX_ALTERNATIVES: error('Union contract requires 1..128 alternatives')
        elif kind == 'record':
            if len(args) != 3 or type(args[0]) is not tuple or type(args[1]) is not tuple or type(args[2]) is not bool:
                error('Invalid immutable Record contract')
            if len(args[0])+len(args[1]) > MAX_FIELDS: error('Record contract exceeds 10000 fields')
            names = set()
            for pair in args[0]+args[1]:
                self.step(depth+1)
                if type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not str:
                    error('Record contract fields require String names')
                name = pair[0]; self.text(name)
                if name in names: error('Record required and optional fields must be unique and disjoint')
                names.add(name)
        else: error('Unknown Contract algebra kind')
        self.active.add(identity)
        try:
            height = 1+max((self.validate(child, depth+1) for child in children(spec)), default=-1)
        finally: self.active.remove(identity)
        if kind == 'recursive':self.bind_group(spec,depth)
        self.validated[identity] = height
        return height

    def bind_group(self, group, depth=0):
        """Resolve local symbols and reject every non-consuming reference cycle."""
        identity=id(group)
        if identity in self.groups:return self.groups[identity]
        definitions=dict(group.arguments[0]);edges={name:set() for name in definitions}
        for name,body in definitions.items():
            pending=[(body,False,depth+1)];seen=set()
            while pending:
                spec,guarded,level=pending.pop();self.step(level)
                key=(id(spec),guarded)
                if key in seen:continue
                seen.add(key)
                if type(spec) is not Contract:continue
                if spec.kind=='recursive':continue  # Independent, already validated binding.
                if spec.kind=='ref':
                    target=spec.arguments[0]
                    if target not in definitions:error('Unbound recursive contract reference '+target)
                    if not guarded:edges[name].add(target)
                else:
                    child_guard=guarded or spec.kind in ('list','tuple','record','map')
                    pending.extend((child,child_guard,level+1) for child in children(spec))
        # An explicit DFS avoids Python recursion even for a maximal alias group.
        finished=set()
        for name in definitions:
            if name in finished:continue
            active=set();pending=[(name,False)]
            while pending:
                name,leaving=pending.pop();self.step(depth)
                if leaving:active.remove(name);finished.add(name);continue
                if name in finished:continue
                if name in active:error('Recursive contract has an unguarded reference cycle')
                active.add(name);pending.append((name,True))
                pending.extend((child,False) for child in edges[name])
        self.groups[identity]=definitions
        return definitions

    def require_closed(self, spec, depth=0):
        """A template value is legal, but every annotation branch must be closed."""
        self.step(depth)
        if type(spec) is not Contract:return
        if id(spec) in self.closed:return
        if spec.kind=='ref':error('Open contract reference requires contract_recursive binding')
        if spec.kind=='recursive':self.bind_group(spec,depth)
        else:
            for child in children(spec):self.require_closed(child,depth+1)
        self.closed.add(id(spec))

    def match(self, spec, value, depth=0, path='$', group=None):
        """Return None on success, an inert path on mismatch, or raise a control."""
        from .language import check_type
        from .tuple_values import TupleValue
        from .hash_maps import HashMap
        self.step(depth)
        if type(spec) is not Contract:
            return None if check_type(value, spec, _probe=True) else path
        identity = (id(spec), id(value), id(group))
        if identity in self.matches: return None if self.matches[identity] else path
        if identity in self.match_active:
            error('Recursive contracts require finite acyclic data; cyclic matching state')
        self.match_active.add(identity)
        args, kind, failure = spec.arguments, spec.kind, None
        try:
            if kind == 'recursive':
                definitions=self.bind_group(spec,depth)
                failure=self.match(definitions[args[1]],value,depth+1,path,spec)
            elif kind == 'ref':
                if group is None:error('Open contract reference requires contract_recursive binding')
                failure=self.match(self.groups[id(group)][args[0]],value,depth+1,path,group)
            elif kind == 'list':
                if type(value) is not list: failure = path
                else:
                    for index, item in enumerate(value):
                        failure = self.match(args[0], item, depth+1, self.path(path,'['+str(index)+']'),group)
                        if failure is not None: break
            elif kind == 'tuple':
                if type(value) is not TupleValue or len(value) != len(args): failure = path
                else:
                    for index, (child, item) in enumerate(zip(args, value)):
                        failure = self.match(child, item, depth+1, self.path(path,'['+str(index)+']'),group)
                        if failure is not None: break
            elif kind == 'record':
                if type(value) is not dict: failure = path
                else:
                    required, optional, extra = args
                    expected = {name for name, child in required+optional}
                    if not extra and any(name not in expected for name in value): failure = path
                    if failure is None:
                        for name, child in required:
                            location = self.path(path,self.field(name))
                            if name not in value: failure = location; break
                            failure = self.match(child, value[name], depth+1, location,group)
                            if failure is not None: break
                    if failure is None:
                        for name, child in optional:
                            if name in value:
                                failure = self.match(child, value[name], depth+1, self.path(path,self.field(name)),group)
                                if failure is not None: break
            elif kind == 'union':
                failure = path
                for child in args:
                    if self.match(child, value, depth+1, path,group) is None:
                        failure = None; break
            elif kind == 'map':
                if type(value) is not HashMap: failure = path
                else:
                    for index, (key, item, fingerprint) in enumerate(value.entries):
                        failure = self.match(args[0], key, depth+1, self.path(path,'.key['+str(index)+']'),group)
                        if failure is not None: break
                        failure = self.match(args[1], item, depth+1, self.path(path,'.value['+str(index)+']'),group)
                        if failure is not None: break
            self.matches[identity] = failure is None
            return failure
        finally: self.match_active.remove(identity)

    def describe(self, spec, depth=0):
        from .language import DataType
        from .objects import ChaosClass
        self.step(depth)
        if type(spec) is str: return self.output({'kind':'builtin', 'name':spec})
        if type(spec) is ChaosClass: return self.output({'kind':'class', 'name':spec.name})
        if type(spec) is DataType: return self.output({'kind':'data', 'name':spec.name})
        args, kind = spec.arguments, spec.kind
        if kind == 'ref':return self.output({'kind':kind,'name':args[0]})
        if kind == 'recursive':return self.output({'kind':kind,'definitions':self.output({name:self.describe(child,depth+1) for name,child in args[0]}),'root':args[1]})
        if kind == 'list': return self.output({'kind':kind, 'element':self.describe(args[0],depth+1)})
        if kind == 'tuple': return self.output({'kind':kind, 'elements':[self.describe(child,depth+1) for child in args]})
        if kind == 'union': return self.output({'kind':kind, 'alternatives':[self.describe(child,depth+1) for child in args]})
        if kind == 'map': return self.output({'kind':kind, 'key':self.describe(args[0],depth+1), 'value':self.describe(args[1],depth+1)})
        return self.output({'kind':kind, 'required':self.output({name:self.describe(child,depth+1) for name,child in args[0]}),
                'optional':self.output({name:self.describe(child,depth+1) for name,child in args[1]}), 'allowExtra':args[2]})


def validate(evaluator, spec):
    Budget(evaluator).validate(spec)
    return spec


def validate_closed(evaluator, spec):
    budget=Budget(evaluator);budget.validate(spec);budget.require_closed(spec)
    return spec


def _make(evaluator, kind, arguments):
    result = Contract(evaluator, kind, tuple(arguments))
    return validate(evaluator, result)


def ref_contract(evaluator, name):return _make(evaluator,'ref',(symbol(name),))


def recursive_contract(evaluator, definitions, root):
    if type(definitions) is not dict or not 1<=len(definitions)<=MAX_DEFINITIONS:
        error('contract_recursive requires a Record with 1..128 definitions')
    return _make(evaluator,'recursive',(tuple(definitions.items()),symbol(root)))


def list_contract(evaluator, element): return _make(evaluator, 'list', (element,))


def tuple_contract(evaluator, elements):
    if type(elements) is not list or len(elements) > MAX_FIELDS:
        error('contract_tuple requires a List of up to 10000 element contracts')
    return _make(evaluator, 'tuple', elements)


def record_contract(evaluator, required, optional=None, allow_extra=False):
    optional = {} if optional is None else optional
    if type(required) is not dict or type(optional) is not dict or type(allow_extra) is not bool:
        error('contract_record requires required/optional Records and Bool allow_extra')
    if len(required)+len(optional) > MAX_FIELDS: error('Record contract exceeds 10000 fields')
    return _make(evaluator, 'record', (tuple(required.items()), tuple(optional.items()), allow_extra))


def union_contract(evaluator, alternatives):
    if type(alternatives) is not list or not 1 <= len(alternatives) <= MAX_ALTERNATIVES:
        error('contract_union requires 1..128 alternatives')
    return _make(evaluator, 'union', alternatives)


def map_contract(evaluator, key, value): return _make(evaluator, 'map', (key, value))


def inspect_value(evaluator, spec, value):
    from .class_creation import validate_value
    budget = Budget(evaluator)
    budget.validate(spec)
    budget.require_closed(spec)
    # The existing validator checks actual nested ownership, including HashMap
    # contents and epoch handles. Its checkpoint shares this union/check budget.
    validate_value(evaluator, value, checkpoint=budget.step, contract_validator=budget.validate,reject_cycles=bool(budget.groups))
    return budget.match(spec, value)


def accepts(evaluator, contract, value):
    return inspect_value(evaluator, contract, value) is None


def require(evaluator, contract, value):
    failure = inspect_value(evaluator, contract, value)
    if failure is not None:
        label = contract.kind if type(contract) is Contract else contract if type(contract) is str else contract.name
        error('Composite contract '+label+' does not match at '+failure[:1000], 'type_mismatch')
    return value


def info(evaluator, contract):
    budget = Budget(evaluator)
    budget.validate(contract)
    return budget.describe(contract)


def describe(value):
    budget = Budget(value.owner, ticking=False)
    budget.validate(value)
    return {'$contract':budget.describe(value)}


def builtins(evaluator):
    return {
        'contract_ref':lambda name:ref_contract(evaluator,name),
        'contract_recursive':lambda definitions,root:recursive_contract(evaluator,definitions,root),
        'contract_list':lambda element:list_contract(evaluator,element),
        'contract_tuple':lambda elements:tuple_contract(evaluator,elements),
        'contract_record':lambda required,optional=None,allow_extra=False:record_contract(evaluator,required,optional,allow_extra),
        'contract_union':lambda alternatives:union_contract(evaluator,alternatives),
        'contract_optional':lambda contract:union_contract(evaluator,[contract,'Null']),
        'contract_map':lambda key,value:map_contract(evaluator,key,value),
        'contract_accepts':lambda contract,value:accepts(evaluator,contract,value),
        'contract_info':lambda contract:info(evaluator,contract),
    }
