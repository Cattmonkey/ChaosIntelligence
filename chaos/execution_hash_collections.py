"""Authoritative persistent-map and protocol-set algorithms on the shared cursor.

Only transport is a generator. All algorithm phases, drafts and loop positions
are data; portable pure-key operations retain their existing synchronous budget.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class HashCollectionFrameRequest:
    operation: str
    values: tuple


def hash_collection_protocol(evaluator,operation,*values):
    from .execution_frames import request_proxy,ensure_cursor
    proxy=request_proxy(HashCollectionFrameRequest(operation,values))
    if getattr(evaluator,'_execution_cursor',None) is not None:return proxy
    return ensure_cursor(evaluator,proxy,observer=True)


def collection_carrier_roots(evaluator,value):
    """Exact private carriers; schema fields only, no arbitrary host iteration."""
    from .hash_maps import _HashBudget,_Builder
    from .set_runtime import Builder
    from .set_values import _Budget,_Key,SetBuilder
    from .capture_frames import Edge
    from .language import LanguageError
    def fail():raise LanguageError('Malformed or foreign collection execution state',code='ownership_error')
    def pure_roots(values):
        result=[];pending=list(values);seen=set()
        while pending:
            item=pending.pop()
            if type(item)in(_Key,tuple):
                if id(item)in seen:continue
                seen.add(id(item))
                if len(seen)>20000:fail()
            if type(item)is _Key:
                if type(item.kind)is not str or type(item.fingerprint)is not bytes or type(item.height)is not int:fail()
                pending.append(item.payload)
            elif type(item)is tuple:pending.extend(item)
            elif type(item)not in(type(None),bool,int,float,str,bytes):result.append(item)
        return tuple(result)
    def memo_roots(memo,pure=False):
        if type(memo)is not dict:fail()
        result=[]
        for identity,row in memo.items():
            if type(identity)is not int or type(row)is not tuple or len(row)!=2:fail()
            result.append(row[0])
            if not pure:
                if type(row[1])is not tuple or len(row[1])!=3 or tuple(type(x)for x in row[1])!=(int,bool,int):fail()
            else:
                if type(row[1])is not _Key:fail()
                result.extend(pure_roots((row[1],)))
        return tuple(result)
    def budget_schema(budget):
        if type(budget)is not _Budget or type(budget.nodes)is not int or type(budget.chars)is not int or type(budget.active)is not set or any(type(x)is not int for x in budget.active)or type(budget.comparisons)is not dict:fail()
        for pair,result in budget.comparisons.items():
            if type(pair)is not tuple or len(pair)!=2 or any(type(x)is not int for x in pair)or type(result)is not bool:fail()
    if type(value)is _HashBudget:
        if value.evaluator is not evaluator or type(value.nodes)is not int or type(value.active)is not set or any(type(x)is not int for x in value.active):fail()
        budget_schema(value.pure)
        return (Edge(evaluator,None,memo_roots(value.memo)+memo_roots(value.pure.memo,True)),)
    if type(value)is _Builder:
        if value.owner is not evaluator or type(value.entries)is not list or type(value.buckets)is not dict:fail()
        children=[]
        for row in value.entries:
            if type(row)is not tuple or len(row)!=3 or type(row[2])is not int:fail()
            children.extend(row[:2])
        for fingerprint,indices in value.buckets.items():
            if type(fingerprint)is not int or type(indices)is not list or any(type(index)is not int or index<0 or index>=len(value.entries)for index in indices):fail()
        return (Edge(evaluator,value.capabilities,tuple(children)),)
    if type(value)is Builder:
        if value.owner is not evaluator or type(value.items)is not list or type(value.pure)is not SetBuilder or type(value.pure.budget)is not _Budget or value.table is not None and type(value.table)is not _Builder:fail()
        pure=value.pure
        if any(type(x)is not int for x in (value.limit,value.attempts,pure.limit,pure.attempts)):fail()
        budget_schema(pure.budget)
        if type(pure.items)is not list or type(pure.keys)is not list or type(pure.buckets)is not dict:fail()
        keys=list(pure.keys)
        for bucket,rows in pure.buckets.items():
            if type(bucket)is not bytes or type(rows)is not list:fail()
            keys.extend(rows)
        if any(type(key)is not _Key for key in keys):fail()
        return (Edge(evaluator,value.capabilities,(value.table,pure.items)+memo_roots(pure.budget.memo,True)+pure_roots(keys)),)
    return None


class _HashCollectionFrame:
    capabilities=None
    ROOTS=()
    def __init__(self):self.phase='start';self.result=None;self.dependencies=None
    def bind(self):return ()
    def roots(self):
        from .capture_frames import Edge
        from .hash_maps import intersection
        from .language import LanguageError
        runner=getattr(self,'authority_runner',None)
        if runner is not None:
            from .suspension import Runner
            from .execution_frames import ExecutionCursor
            if type(runner)is not Runner or type(runner.iterator)is not ExecutionCursor or runner.iterator.owner is not self.owner or self.authority_epoch!=self.owner.suspension.epoch:
                raise LanguageError('Collection restoration runner cannot cross owner or epoch',code='ownership_error')
        result=[]
        for name in self.ROOTS+('result','previous_authority'):
            value=getattr(self,name,None)
            if name in ('rows','parts','entries')and type(value)in(tuple,list):values=tuple(child for row in value for child in (row if type(row)is tuple else (row,)))
            elif name in('children','items','result')and type(value)is tuple:values=tuple(value)
            else:values=(value,)
            caps=self.capabilities if name=='previous_authority'else intersection(self.capabilities,getattr(self,'active_caps',None))
            result.append(Edge(self.owner,caps,values))
        return tuple(result)
    def enter_scope(self,e,*ceilings):
        from .hash_maps import intersection
        self.authority_runner=e.suspension.current;self.authority_epoch=e.suspension.epoch
        self.previous_authority=getattr(self.authority_runner,'capabilities',None)
        self.active_caps=intersection(self.previous_authority,*ceilings)if self.authority_runner is not None else None;self.authority_entered=True
        if self.authority_runner is not None:self.authority_runner.capabilities=self.active_caps
    def retire(self,e):
        if getattr(self,'active_entered',False):self.active_entered=False;self.budget.active.remove(self.identity)
        if getattr(self,'locked',False):self.locked=False;self.cursor.busy=False
        if getattr(self,'authority_entered',False):
            self.authority_entered=False
            if self.authority_runner is not None:self.authority_runner.capabilities=self.previous_authority
    def done(self,e,value=None):
        from .execution_frames import Done
        self.result=value;self.retire(e);self.phase='done';return Done(value)
    def step(self,cursor,value,error):
        try:
            if error is not None:raise error
            if self.dependencies is None:self.dependencies=self.bind()
            return self.advance(cursor,value)
        except BaseException:self.retire(cursor.owner);raise
    def close(self,cursor):self.retire(cursor.owner)
    def leaf(self,iterator):
        from .execution_frames import Push,ProtocolBridge
        bridge=ProtocolBridge(iterator);bridge.supplementary=True;return Push(bridge)


class HashKeyFrame(_HashCollectionFrame):
    ROOTS=('budget','value','parts','children')
    def __init__(self,budget,value,depth=0):
        super().__init__();self.budget,self.value,self.depth=budget,value,depth;self.parts=[];self.children=();self.index=0;self.active_entered=False
    def bind(self):
        from .language import ResourceLimit
        from .tuple_values import TupleValue
        from .objects import ChaosInstance,owned
        from .numeric_values import Fraction,Complex,Decimal
        from .set_values import SetValue,_fingerprint
        from . import hash_maps
        return ResourceLimit,TupleValue,ChaosInstance,owned,Fraction,Complex,Decimal,SetValue,_fingerprint,hash_maps
    def advance(self,cursor,value):
        ResourceLimit,TupleValue,ChaosInstance,object_owned,Fraction,Complex,Decimal,SetValue,fingerprint,h=self.dependencies;e=cursor.owner
        if self.phase=='start':
            self.budget.step()
            if self.depth>100:raise ResourceLimit('Hash key depth exceeds 100')
            self.identity=id(self.value)
            if self.identity in self.budget.active:h.error('Hash keys cannot contain cycles')
            if self.identity in self.budget.memo:
                result=self.budget.memo[self.identity][1]
                if self.depth+result[2]>100:raise ResourceLimit('Hash key depth exceeds 100')
                return self.done(e,result)
            if type(self.value)is ChaosInstance:
                object_owned(self.value,e);object_owned(self.value.klass,e);self.phase='instance';return self.leaf(self.budget.instance(self.value))
            if type(self.value)is TupleValue:
                if len(self.value)>h.MAX_ENTRIES:raise ResourceLimit('Hash key Tuple exceeds 10000 items')
                self.tag='tuple-hash';self.children=self.value.items;self.budget.active.add(self.identity);self.active_entered=True;self.phase='next'
            elif type(self.value)is SetValue:
                from .set_runtime import owned as set_owned
                set_owned(self.value,e);self.tag='set-hash'
                if self.value.owner is not None:self.parts=[(row[2],False,0)for row in self.value._mapping.entries];self.phase='finish'
                else:self.children=self.value.items;self.budget.active.add(self.identity);self.active_entered=True;self.phase='next'
            elif type(self.value)in(type(None),bool,int,float,str,Fraction,Complex,Decimal):
                native=self.budget.pure.key(self.value,self.depth);result=(int.from_bytes(native.fingerprint,'big'),True,native.height)
                if result[1]:self.budget.memo[self.identity]=(self.value,result)
                return self.done(e,result)
            else:h.error('Unhashable value; keys require native immutable values or an Instance hash protocol','unhashable_type')
        if self.phase=='instance':return self.done(e,(value,False,0))
        if self.phase=='child':self.parts.append(value);self.index+=1;self.phase='next'
        if self.phase=='next':
            if self.index<len(self.children):
                self.phase='child';return self.leaf(self.budget.hash(self.children[self.index],self.depth+1))
            self.active_entered=False;self.budget.active.remove(self.identity);self.phase='finish'
        encoded=tuple(h._encoded(child[0])for child in self.parts)
        if self.tag=='set-hash':encoded=tuple(sorted(encoded))
        result=(int.from_bytes(fingerprint(self.tag,encoded),'big'),all(child[1]for child in self.parts)if self.tag=='tuple-hash'else self.value.owner is None,1+max((child[2]for child in self.parts),default=-1))
        if result[1]:self.budget.memo[self.identity]=(self.value,result)
        return self.done(e,result)


class InstanceHashFrame(_HashCollectionFrame):
    ROOTS=('budget','value','hash_member','function')
    def __init__(self,budget,value):super().__init__();self.budget,self.value=budget,value;self.hash_member=self.function=None
    def bind(self):
        from .language import ResourceLimit
        from .objects import member_value,special_method,require_callable
        from .set_values import _fingerprint
        from . import hash_maps
        return ResourceLimit,member_value,special_method,require_callable,_fingerprint,hash_maps
    def advance(self,cursor,value):
        ResourceLimit,member_value,special_method,require_callable,fingerprint,h=self.dependencies;e=cursor.owner
        if self.phase=='start':
            self.hash_position=self.eq_position=None
            for position,base in enumerate(self.value.klass.mro):
                e.tick()
                if self.hash_position is None and '__hash__'in base.members:self.hash_position,self.hash_member=position,base.members['__hash__']
                if self.eq_position is None and '__eq__'in base.members:self.eq_position=position
            if self.hash_position is None:
                if self.eq_position is not None:h.error('An equality override requires an explicit __hash__','unhashable_type')
                return self.done(e,int.from_bytes(fingerprint('identity',(h._encoded(h._identity(e,self.value)),)),'big'))
            if self.eq_position is not None and self.eq_position<self.hash_position:h.error('An equality override must explicitly retain or replace its inherited __hash__','unhashable_type')
            if member_value(self.hash_member)is None:h.error('__hash__ = null makes the Instance unhashable','unhashable_type')
            self.phase='access';return self.leaf(e._s_access_result(special_method(self.value,'__hash__',e)))
        if self.phase=='access':
            self.function=value;require_callable(value,e,setter=True);self.phase='invoke';return self.leaf(e._s_invoke(value,[],synchronous=True))
        e.tick()
        if type(value)is not int:h.error('__hash__ must synchronously return an exact Int',value=value)
        if value.bit_length()>14000:raise ResourceLimit('__hash__ Int exceeds 14000 bits')
        return self.done(e,value)


class HashValueFrame(_HashCollectionFrame):
    ROOTS=('value','budget')
    def __init__(self,value):super().__init__();self.value=value;self.budget=None
    def bind(self):
        from .indexing import validate_owned
        from . import hash_maps
        return validate_owned,hash_maps
    def advance(self,cursor,value):
        validate_owned,h=self.dependencies
        if self.phase=='start':
            validate_owned(cursor.owner,self.value);self.budget=h._HashBudget(cursor.owner);self.phase='hash';return self.leaf(self.budget.hash(self.value))
        return self.done(cursor.owner,value[0])


class HashFindFrame(_HashCollectionFrame):
    ROOTS=('mapping','key','stored','left','right')
    def __init__(self,mapping,key,fingerprint):
        super().__init__();self.mapping,self.key,self.fingerprint=mapping,key,fingerprint;self.offset=0;self.index=None;self.stored=self.left=self.right=None
    def bind(self):
        from .class_creation import project_value
        from .operators import equal
        from . import hash_maps
        return project_value,equal,hash_maps
    def advance(self,cursor,value):
        unused,equal,h=self.dependencies;e=cursor.owner
        if self.phase=='start':self.indices=self.mapping.buckets.get(self.fingerprint,());self.phase='next'
        if self.phase=='compare':
            if value:return self.done(e,self.index)
            self.offset+=1;self.phase='next'
        if self.offset>=len(self.indices):return self.done(e,None)
        self.index=self.indices[self.offset];e.tick();self.stored=self.mapping.entries[self.index][0]
        if self.stored is self.key:return self.done(e,self.index)
        self.left=h.project_key(e,self.stored,self.mapping);self.right=h.project_key(e,self.key,self.mapping)
        self.phase='compare';return self.leaf(equal(e,self.left,self.right,synchronous=True))


class HashStoreFrame(_HashCollectionFrame):
    ROOTS=('mapping','key','value','entries')
    def __init__(self,mapping,key,value):super().__init__();self.mapping,self.key,self.value=mapping,key,value;self.entries=None
    def bind(self):
        from .class_creation import validate_value
        from .language import ResourceLimit
        from . import hash_maps
        return validate_value,ResourceLimit,hash_maps
    def advance(self,cursor,value):
        validate_value,ResourceLimit,h=self.dependencies;e=cursor.owner
        if self.phase=='start':validate_value(e,self.key);validate_value(e,self.value);self.phase='hash';return self.leaf(h.hash_value(e,self.key))
        if self.phase=='hash':self.fingerprint=value;self.phase='find';return self.leaf(h._find(e,self.mapping,self.key,value))
        self.position=value;self.entries=list(self.mapping.entries)
        if value is None:
            if len(self.entries)>=h.MAX_ENTRIES:raise ResourceLimit('HashMap exceeds 10000 entries')
            self.entries.append((self.key,self.value,self.fingerprint))
        else:
            original,previous,old_hash=self.entries[value];self.entries[value]=(original,self.value,old_hash)
        return self.done(e,h.make(e,self.entries,self.mapping.capabilities))


class HashBuilderAddFrame(_HashCollectionFrame):
    ROOTS=('builder','key','value')
    def __init__(self,builder,key,value):super().__init__();self.builder,self.key,self.value=builder,key,value
    def bind(self):
        from .class_creation import validate_value
        from .language import ResourceLimit
        from . import hash_maps
        return validate_value,ResourceLimit,hash_maps
    def advance(self,cursor,value):
        validate_value,ResourceLimit,h=self.dependencies;e=cursor.owner
        if self.phase=='start':validate_value(e,self.key);validate_value(e,self.value);self.phase='hash';return self.leaf(h.hash_value(e,self.key))
        if self.phase=='hash':self.fingerprint=value;self.phase='find';return self.leaf(h._find(e,self.builder,self.key,value))
        if value is None:
            if len(self.builder.entries)>=h.MAX_ENTRIES:raise ResourceLimit('HashMap exceeds 10000 entries')
            self.builder.buckets.setdefault(self.fingerprint,[]).append(len(self.builder.entries));self.builder.entries.append((self.key,self.value,self.fingerprint))
        else:
            original,previous,old_hash=self.builder.entries[value];self.builder.entries[value]=(original,self.value,old_hash)
        return self.done(e)


class HashConstructFrame(_HashCollectionFrame):
    ROOTS=('source','cursor','builder','step_value','pair')
    def __init__(self,source):super().__init__();self.source=source;self.cursor=self.builder=self.step_value=self.pair=None;self.consumed=0;self.locked=False
    def bind(self):
        from .language import ResourceLimit
        from .tuple_values import TupleValue
        from . import hash_maps
        return ResourceLimit,TupleValue,hash_maps
    def advance(self,cursor,value):
        ResourceLimit,TupleValue,h=self.dependencies;e=cursor.owner
        if self.phase=='start':self.phase='acquire';return self.leaf(e.suspension.iteration.acquire(self.source,temporary=True))
        if self.phase=='acquire':
            self.cursor=value
            if value.busy:h.error('HashMap input iterator cannot be re-entered')
            value.busy=True;self.locked=True;self.builder=h._Builder(e);self.phase='next'
        elif self.phase=='step':
            self.step_value=value
            if value['done']:return self.done(e,h.make(e,self.builder.entries,self.builder.capabilities))
            if self.consumed>=h.MAX_ENTRIES:raise ResourceLimit('hash_map input exceeds 10000 pairs')
            self.consumed+=1;self.pair=value['value']
            if type(self.pair)not in(list,TupleValue)or len(self.pair)!=2:h.error('hash_map entries require List or Tuple pairs of length 2')
            self.phase='add';return self.leaf(self.builder.add(e,self.pair[0],self.pair[1]))
        self.phase='step';return self.leaf(e.suspension.iteration.step(self.cursor,locked=True))


class HashEqualFrame(_HashCollectionFrame):
    ROOTS=('left','right','key','value','query','other')
    def __init__(self,left,right):super().__init__();self.left,self.right=left,right;self.index=0;self.key=self.value=self.query=self.other=None
    def bind(self):
        from .agent_tools import scope
        from .class_creation import project_value
        from .operators import equal
        from . import hash_maps
        return scope,project_value,equal,hash_maps
    def advance(self,cursor,value):
        unused,project_value,equal,h=self.dependencies;e=cursor.owner
        if self.phase=='start':
            h.owned(self.left,e);h.owned(self.right,e)
            if self.left is self.right:return self.done(e,True)
            if len(self.left)!=len(self.right):return self.done(e,False)
            self.enter_scope(e,h.intersection(self.left.capabilities,self.right.capabilities));self.phase='next'
        if self.phase=='find':
            if value is None:return self.done(e,False)
            self.other=self.right.entries[value][1];self.phase='compare';return self.leaf(equal(e,project_value(e,self.value,self.left),project_value(e,self.other,self.right),synchronous=True))
        if self.phase=='compare':
            if not value:return self.done(e,False)
            self.index+=1;self.phase='next'
        if self.index>=len(self.left.entries):return self.done(e,True)
        self.key,self.value,self.fingerprint=self.left.entries[self.index];e.tick();self.query=h.project_key(e,self.key,self.left);self.phase='find';return self.leaf(h._find(e,self.right,self.query,self.fingerprint))


class HashResolveFrame(_HashCollectionFrame):
    ROOTS=('request','mapping')
    def __init__(self,request):super().__init__();self.request=request;self.mapping=None
    def bind(self):
        from .agent_tools import scope
        from .class_creation import project_value
        from . import hash_maps
        return scope,project_value,hash_maps
    def advance(self,cursor,value):
        unused,project_value,h=self.dependencies;e=cursor.owner;r=self.request
        if self.phase=='start':
            if r.operation=='hash':self.phase='direct';return self.leaf(h.hash_value(e,r.key))
            if r.operation=='create':self.phase='direct';return self.leaf(h.construct(e,r.value))
            self.mapping=h.owned(r.mapping,e);self.enter_scope(e,self.mapping.capabilities)
            if r.operation=='set':self.phase='direct';return self.leaf(h._store(e,self.mapping,r.key,r.value))
            self.phase='hash';return self.leaf(h.hash_value(e,r.key))
        if self.phase=='direct':return self.done(e,value)
        if self.phase=='hash':self.fingerprint=value;self.phase='find';return self.leaf(h._find(e,self.mapping,r.key,value))
        self.position=value
        if r.operation=='has':return self.done(e,value is not None)
        if r.operation=='remove':return self.done(e,h.make(e,tuple(entry for i,entry in enumerate(self.mapping.entries)if i!=value),self.mapping.capabilities))
        if r.operation in('get','index'):
            if value is None:
                if r.operation=='index':h.error('HashMap key was not found','key_error')
                return self.done(e,project_value(e,r.value,self.mapping))
            return self.done(e,project_value(e,self.mapping.entries[value][1],self.mapping))
        h.error('Unknown HashMap operation')


class SetBuilderAddFrame(_HashCollectionFrame):
    ROOTS=('builder','value','item','promotion_items')
    def __init__(self,builder,value):super().__init__();self.builder,self.value=builder,value;self.index=0;self.item=None;self.promotion_items=None
    def bind(self):
        from .language import ResourceLimit
        from .hash_maps import _Builder
        from .agent_tools import scope
        from . import set_runtime
        return ResourceLimit,_Builder,scope,set_runtime
    def advance(self,cursor,value):
        ResourceLimit,Builder,unused,s=self.dependencies;e=cursor.owner;b=self.builder
        if self.phase=='start':
            if b.attempts>=b.limit:raise ResourceLimit('Set input exceeds '+str(b.limit)+' items')
            b.attempts+=1;b.items.append(self.value)
            if b.table is None and s.portable(self.value,e.tick):b.pure.add(self.value);return self.done(e)
            s.account(self.value,b.pure.budget);self.enter_scope(e,b.capabilities)
            if b.table is None:b.table=Builder(e);self.promotion_items=b.pure.items;self.phase='promote'
            else:self.phase='append'
        if self.phase=='promoted':self.index+=1;self.phase='promote'
        if self.phase=='promote':
            if self.index<len(self.promotion_items):self.item=self.promotion_items[self.index];self.phase='promoted';return self.leaf(b.table.add(e,self.item,None))
            self.phase='append'
        if self.phase=='append':self.phase='added';return self.leaf(b.table.add(e,self.value,None))
        return self.done(e)


class SetMappingFrame(_HashCollectionFrame):
    ROOTS=('value','draft','item','items')
    def __init__(self,value):super().__init__();self.value=value;self.draft=self.item=None;self.items=();self.index=0
    def bind(self):
        from .hash_maps import _Builder,make
        from . import set_runtime
        return _Builder,make,set_runtime
    def advance(self,cursor,value):
        Builder,make,s=self.dependencies;e=cursor.owner
        if self.phase=='start':
            s.owned(self.value,e)
            if self.value.owner is not None:return self.done(e,self.value._mapping)
            self.draft=Builder(e);self.items=self.value.items;self.phase='next'
        if self.phase=='add':self.index+=1;self.phase='next'
        if self.index>=len(self.items):return self.done(e,make(e,self.draft.entries))
        self.item=self.items[self.index];self.phase='add';return self.leaf(self.draft.add(e,self.item,None))


class SetContainsFrame(_HashCollectionFrame):
    ROOTS=('value','item','table')
    def __init__(self,value,item):super().__init__();self.value,self.item=value,item;self.table=None
    def bind(self):
        from .set_values import set_contains
        from .hash_maps import hash_value,_find
        from .agent_tools import scope
        from . import set_runtime
        return set_contains,hash_value,_find,scope,set_runtime
    def advance(self,cursor,value):
        contains,hash_value,find,unused,s=self.dependencies;e=cursor.owner
        if self.phase=='start':
            s.owned(self.value,e)
            if self.value.owner is None and s.portable(self.item,e.tick):return self.done(e,contains(self.value,self.item,e.tick))
            self.enter_scope(e,self.value.capabilities);self.phase='mapping';return self.leaf(s.mapping(e,self.value))
        if self.phase=='mapping':self.table=value;self.phase='hash';return self.leaf(hash_value(e,self.item))
        if self.phase=='hash':self.fingerprint=value;self.phase='find';return self.leaf(find(e,self.table,self.item,value))
        return self.done(e,value is not None)


class SetAlgebraFrame(_HashCollectionFrame):
    ROOTS=('left','right','a','b','rows','key','query')
    def __init__(self,operation,left,right):
        super().__init__();self.operation,self.left,self.right=operation,left,right;self.a=self.b=self.key=self.query=None;self.rows=[];self.index=0
    def bind(self):
        from .set_values import set_binary,set_equal,set_is_disjoint
        from .hash_maps import _find,make,intersection,project_key
        from .agent_tools import scope
        from .language import ResourceLimit
        from . import set_runtime
        return set_binary,set_equal,set_is_disjoint,_find,make,intersection,project_key,scope,ResourceLimit,set_runtime
    def append_or_finish(self,e,present):
        op=self.operation
        if op in('==','<','<=')and not present:return self.done(e,False)
        if op=='disjoint'and present:return self.done(e,False)
        if op=='|'or op=='&'and present or op in('-','^')and not present:self.rows.append((self.key,None,self.fingerprint))
        self.index+=1;self.phase='next';return None
    def advance(self,cursor,value):
        binary,equal,disjoint,find,make,intersection,project_key,unused,ResourceLimit,s=self.dependencies;e=cursor.owner
        if self.phase=='start':
            s.owned(self.left,e);s.owned(self.right,e)
            if self.left.owner is None and self.right.owner is None:return self.done(e,equal(self.left,self.right,e.tick)if self.operation=='=='else disjoint(self.left,self.right,e.tick)if self.operation=='disjoint'else binary(self.operation,self.left,self.right,e.tick))
            self.ceiling=intersection(self.left.capabilities,self.right.capabilities);self.enter_scope(e,self.ceiling);self.phase='a';return self.leaf(s.mapping(e,self.left))
        if self.phase=='a':self.a=value;self.phase='b';return self.leaf(s.mapping(e,self.right))
        if self.phase=='b':
            self.b=value
            if self.operation=='=='and len(self.a)!=len(self.b):return self.done(e,False)
            if self.operation in('>','>='):self.a,self.b=self.b,self.a;self.operation='<'if self.operation=='>'else'<='
            if self.operation in('<','<=')and(len(self.a)>len(self.b)or self.operation=='<'and len(self.a)==len(self.b)):return self.done(e,False)
            self.phase='next'
        if self.phase=='find':
            action=self.append_or_finish(e,value is not None)
            if action is not None:return action
        if self.phase=='find_second':
            if value is None:self.rows.append((self.key,None,self.fingerprint))
            self.index+=1;self.phase='second'
        while self.phase=='next':
            if self.index>=len(self.a.entries):
                if self.operation in('==','<','<=','disjoint'):return self.done(e,True)
                self.index=0;self.phase='second';break
            self.key,unused_value,self.fingerprint=self.a.entries[self.index];e.tick();self.query=project_key(e,self.key,self.a)
            if self.operation=='|':self.append_or_finish(e,False);continue
            self.phase='find';return self.leaf(find(e,self.b,self.query,self.fingerprint))
        if self.phase=='second'and self.operation in('|','^')and self.index<len(self.b.entries):
            self.key,unused_value,self.fingerprint=self.b.entries[self.index];e.tick();self.query=project_key(e,self.key,self.b);self.phase='find_second';return self.leaf(find(e,self.a,self.query,self.fingerprint))
        if len(self.rows)>10000:raise ResourceLimit('Set exceeds 10000 members')
        return self.done(e,s.freeze(e,make(e,self.rows,self.ceiling)))


class SetUpdateFrame(_HashCollectionFrame):
    ROOTS=('value','item','table')
    def __init__(self,operation,value,item):super().__init__();self.operation,self.value,self.item=operation,value,item;self.table=None
    def bind(self):
        from .set_values import set_add,set_discard
        from .hash_maps import _store,_find,hash_value,make
        from .agent_tools import scope
        from . import set_runtime
        return set_add,set_discard,_store,_find,hash_value,make,scope,set_runtime
    def advance(self,cursor,value):
        add,discard,store,find,hash_value,make,unused,s=self.dependencies;e=cursor.owner
        if self.phase=='start':
            s.owned(self.value,e)
            if self.value.owner is None and s.portable(self.item,e.tick):return self.done(e,(add if self.operation=='add'else discard)(self.value,self.item,e.tick))
            self.enter_scope(e,self.value.capabilities);self.phase='mapping';return self.leaf(s.mapping(e,self.value))
        if self.phase=='mapping':
            self.table=value
            if self.operation=='add':self.phase='store';return self.leaf(store(e,self.table,self.item,None))
            self.phase='hash';return self.leaf(hash_value(e,self.item))
        if self.phase=='store':return self.done(e,s.freeze(e,value))
        if self.phase=='hash':self.fingerprint=value;self.phase='find';return self.leaf(find(e,self.table,self.item,value))
        self.found=value;rows=tuple(row for i,row in enumerate(self.table.entries)if i!=value)
        return self.done(e,s.freeze(e,make(e,rows,self.value.capabilities)))


class SetResolveFrame(_HashCollectionFrame):
    ROOTS=('request',)
    def __init__(self,request):super().__init__();self.request=request
    def bind(self):
        from . import set_runtime
        return(set_runtime,)
    def advance(self,cursor,value):
        s=self.dependencies[0];e=cursor.owner;r=self.request
        if self.phase!='start':return self.done(e,value)
        self.phase='result'
        if r.operation in('add','discard'):return self.leaf(s.update(e,r.operation,r.value,r.other))
        if r.operation=='disjoint':return self.leaf(s.algebra(e,'disjoint',r.value,r.other))
        raise RuntimeError('Unknown Set request')


class SetLiteralEvaluationFrame(_HashCollectionFrame):
    KINDS={'set'}
    ROOTS=('node','env','builder','source','item')
    def __init__(self,node,env):super().__init__();self.node,self.env=node,env;self.builder=self.source=self.item=None;self.index=0
    def advance(self,cursor,value):
        from .execution_frames import Push,EvaluationBoundaryFrame,EvaluationRequest
        e=cursor.owner
        if self.phase=='start':
            e.tick()
            from .language import ResourceLimit
            if not self.node[1]or len(self.node[1])>10000:raise ResourceLimit('Set 字面量需要 1–10000 项')
            from .set_runtime import Builder
            from .capture_frames import active,retain
            self.builder=Builder(e);retain(active(e),self.builder);self.phase='next'
        if self.phase=='item':self.item=value;self.phase='added';return self.leaf(self.builder.add(value))
        if self.phase=='source':self.source=value;self.phase='added';return self.leaf(e.suspension.iteration.consume(value,'set',initial=self.builder,limit=10000-self.builder.attempts))
        if self.phase=='added':self.index+=1;self.phase='next'
        if self.index>=len(self.node[1]):return self.done(e,self.builder.finish())
        item=self.node[1][self.index]
        if item[0]=='spread':self.phase='source';return Push(EvaluationBoundaryFrame(EvaluationRequest(item[1],self.env)))
        from .language import ResourceLimit
        if self.builder.attempts>=10000:raise ResourceLimit('Set 输入超过 10000 项')
        self.phase='item';return Push(EvaluationBoundaryFrame(EvaluationRequest(item,self.env)))


HASH_COLLECTION_FRAMES=(HashKeyFrame,InstanceHashFrame,HashValueFrame,HashFindFrame,HashStoreFrame,
    HashBuilderAddFrame,HashConstructFrame,HashEqualFrame,HashResolveFrame,SetBuilderAddFrame,
    SetMappingFrame,SetContainsFrame,SetAlgebraFrame,SetUpdateFrame,SetResolveFrame,SetLiteralEvaluationFrame)

def hash_collection_frame(request):
    if type(request)is not HashCollectionFrameRequest:return None
    mapping=dict(zip(('hash_key','instance_hash','hash_value','find','store','map_add','construct','equal','map_resolve','set_add','mapping','contains','algebra','update','set_resolve','literal'),HASH_COLLECTION_FRAMES))
    return mapping[request.operation](*request.values)
