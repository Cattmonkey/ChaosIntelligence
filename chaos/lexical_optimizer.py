"""Bounded compiler facts, never runtime Envs or executable continuations."""
from dataclasses import dataclass

from .language import Node

_UNKNOWN = object()
_MAX_DEPTH = 64


@dataclass(frozen=True, eq=False)
class _Facts:
    parent: object
    names: frozenset
    value: object = _UNKNOWN


def extend_plan(roots, sites, literal_values, shared, remaining, checkpoint, integer, site_type):
    """Consume only the original planner's remaining structural budget.

    Shared subtrees and unsupported binding edges retain literal-only sites.
    Fact chains are persistent: a closure keeps its definition-time pointer.
    Every inspected sequence position, name lookup and metadata entry is charged
    before work. Recursion beyond 64 is an honest conservative truncation.
    """
    truncated = False
    def charge():
        nonlocal remaining, truncated
        if remaining <= 0:
            truncated = True
            return False
        remaining -= 1
        if checkpoint is not None:checkpoint()
        return True
    def bind(parent, names, value=_UNKNOWN):
        # Callers have already paid for every exact-string name.
        return _Facts(parent, frozenset(names), value)
    def lookup(facts, name):
        while facts is not None:
            if not charge():return _UNKNOWN
            if name in facts.names:return facts.value
            facts = facts.parent
        return _UNKNOWN
    def positions(sequence, start=0):
        for index in range(start,len(sequence)):
            if not charge():break
            yield sequence[index]
    def metadata(node):
        if not charge():return None
        attributes=object.__getattribute__(node,'__dict__')
        if type(attributes)is not dict:return None
        aliases=None
        for key,value in attributes.items():
            if not charge():return None
            if type(key)is not str:return None
            if key=='macro_bindings':aliases=value
        if aliases is None:return {}
        if type(aliases)is not dict:return None
        result={}
        for key,value in aliases.items():
            if not charge():return None
            if type(key)is not str or type(value)is not str:return None
            result[key]=value
        return result
    def block(node,facts,depth):
        if len(node)!=2 or type(node[1])is not list:return _UNKNOWN
        names=[];counts={};opaque=False
        for statement in positions(node[1]):
            if type(statement)is not Node or not statement or type(statement[0])is not str:
                opaque=True;continue
            tag=statement[0]
            # Unsupported declarations may introduce multiple pattern names.
            if tag in ('let_pattern','var_pattern','data','rec','decorated'):
                opaque=True
            if tag in ('let','var','def','agent','class'):
                if len(statement)<2 or type(statement[1])is not str:
                    opaque=True;continue
                name=statement[1]
            elif tag=='import':
                if len(statement)!=3 or type(statement[2])is not str:
                    opaque=True;continue
                name=statement[2]
            else:continue
            if not charge():return _UNKNOWN
            names.append(name);counts[name]=counts.get(name,0)+1
        current=bind(None if opaque else facts,names)
        for statement in positions(node[1]):
            value=visit(statement,current,depth+1)
            if (type(statement)is Node and len(statement)==4 and
                    type(statement[0])is str and statement[0]=='let' and
                    type(statement[1])is str and counts.get(statement[1])==1 and not opaque):
                if not charge():return _UNKNOWN
                current=bind(current,(statement[1],),value if integer(value)else _UNKNOWN)
        return _UNKNOWN
    def function(node,facts,depth):
        if len(node)!=5 or type(node[1])is not list:return _UNKNOWN
        aliases=metadata(node);names=[];valid=aliases is not None
        for parameter in positions(node[1]):
            if type(parameter)not in(tuple,Node)or len(parameter)not in(2,4,5):
                valid=False;continue
            fields=[]
            for field in positions(parameter):fields.append(field)
            if len(fields)!=len(parameter):return _UNKNOWN
            if type(fields[0])is not str:
                valid=False;continue
            name=fields[0];names.append(name)
            if aliases is not None and name in aliases:names.append(aliases[name])
            if len(fields)>=4 and fields[3]is not None:visit(fields[3],facts,depth+1)
            if len(fields)==5 and fields[4]is not None:valid=False
        if type(node[4])is str:names.append(node[4])
        else:valid=False
        if not charge():return _UNKNOWN
        visit(node[3],bind(facts if valid else None,names),depth+1)
        return _UNKNOWN
    # These expressions do not bind a different lexical environment. Their
    # results are deliberately unknown unless handled explicitly below.
    preserving=frozenset(('call','keyword','spread','kwspread','list','tuple','record',
                          'member','index','subscript','return','await','yield','yield_from',
                          'unary','binary','let','var','def','throw'))
    def visit(item,facts,depth):
        if not charge():return _UNKNOWN
        if depth>_MAX_DEPTH:
            nonlocal truncated
            truncated=True;return _UNKNOWN
        kind=type(item)
        if kind not in(Node,list,tuple):return _UNKNOWN
        if id(item)in shared:return literal_values.get(id(item),_UNKNOWN)
        if kind is not Node:
            for child in positions(item):visit(child,facts,depth+1)
            return _UNKNOWN
        if not item or type(item[0])is not str:return _UNKNOWN
        tag=item[0]
        if tag=='literal':return item[1]if len(item)==2 and integer(item[1])else _UNKNOWN
        if tag=='name':return lookup(facts,item[1])if len(item)==2 and type(item[1])is str else _UNKNOWN
        if tag=='block':return block(item,facts,depth)
        # Class fields are not the method's captured lexical namespace.
        # Keep the original literal pass only for this unsupported edge.
        if tag=='class':return _UNKNOWN
        if tag in('fn','gen_fn','async_fn','async_gen_fn'):return function(item,facts,depth)
        operation=None;operands=();value=_UNKNOWN
        if len(item)==3 and tag=='unary' and type(item[1])is str and item[1]in('+','-'):
            if not charge():return _UNKNOWN
            child=visit(item[2],facts,depth+1)
            if integer(child):
                operation='pos'if item[1]=='+'else'neg';operands=(child,);value=child if operation=='pos'else-child
        elif len(item)==4 and tag=='binary' and type(item[1])is str and item[1]in('+','-','*'):
            if not charge():return _UNKNOWN
            left=visit(item[2],facts,depth+1);right=visit(item[3],facts,depth+1)
            if integer(left)and integer(right):
                operation=item[1];operands=(left,right);value=left+right if operation=='+'else left-right if operation=='-'else left*right
        elif len(item)==4 and tag in('let','var'):
            for unused in positions(item,1):pass
            initializer=visit(item[3],facts,depth+1)
            return initializer if tag=='let'else _UNKNOWN
        else:
            inherited=facts if tag in preserving else None
            for child in positions(item,1):visit(child,inherited,depth+1)
        if integer(value):
            if operation is not None:
                if id(item)in sites or len(sites)<4096:
                    sites[id(item)]=site_type(item,operation,operands,value)
                else:truncated=True
            return value
        return _UNKNOWN
    for root in positions(roots):visit(root,None,0)
    return truncated
