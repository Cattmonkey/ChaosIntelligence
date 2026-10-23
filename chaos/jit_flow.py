"""A shared continuation IR for opt-in stable-definition function control flow.

LLVM chooses successors. Python owns every semantic event and live language root.
The portable dispatcher resumes exactly the same PC after instrumentation/deopt.
"""
from dataclasses import dataclass
from .jit import Unsupported,MAX_KERNELS
from .language import Env,LanguageError,ReturnSignal,BreakSignal,ContinueSignal,MISSING,check_type

@dataclass
class FlowProgram:
    instructions: tuple
    flag_count: int
    kernel: object = None
    node_ids: tuple = ()
    resume_calls: int = 0
    native_resumes: int = 0
    portable_resumes: int = 0
    last_pc: int = 0
    effects: tuple = ()
    layout_seal: object = None
    await_suspensions: int = 0
    def portable(self,pc,flags):
        while True:
            item=self.instructions[pc];kind=item[0]
            if kind=='jump':pc=item[1]
            elif kind=='branch':pc=item[2] if flags[item[1]] else item[3]
            elif kind=='flag':flags[item[1]]=item[2];pc+=1
            elif kind=='done':return pc,0
            else:return pc+1,pc+1
    def info(self):
        return {'schema':'chaos.compiled-flow/1','abi':'pc-host-events/1',
            'status':'native' if self.kernel else 'portable',
            'instructionCount':len(self.instructions),
            'branchCount':sum(i[0]=='branch' for i in self.instructions),
            'checkpointCount':sum(i[0]=='enter' and i[1][0] in ('block','while','for','for_pattern','async_for','async_for_pattern') for i in self.instructions),
            'resumeCalls':self.resume_calls,'nativeResumes':self.native_resumes,
            'portableResumes':self.portable_resumes,'lastPC':self.last_pc,
            'definitionPolicy':'stable-during-invocation'}

def node_ids(node):
    result=[]
    def visit(value):
        if isinstance(value,tuple):
            result.append(id(value))
            for child in value:visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(node)
    return tuple(result)

def effect_tags(node):
    """Conservative syntax facts; never classify purity by callable names."""
    from .compiler import _EFFECTS
    tags=set();todo=[node];visits=0
    while todo:
        value=todo.pop();visits+=1
        if visits>20000:raise Unsupported('flow_effect_limit')
        if type(value) is list:todo.extend(value);continue
        if not isinstance(value,tuple) or not value:continue
        kind=value[0]
        if type(kind) is not str:tags.update(('unknown','raise'));continue
        tags.update(_EFFECTS.get(kind,('unknown','raise')))
        if kind in ('fn','gen_fn','async_fn','async_gen_fn'):
            # Only evaluated defaults are immediate; bodies remain latent.
            if len(value)>1 and type(value[1]) is list:
                todo.extend(row[3] for row in value[1] if type(row) in (tuple,list) and len(row)>3 and isinstance(row[3],tuple))
            continue
        todo.extend(value[1:])
    if tags & {'call','protocol','unknown'}:tags.add('suspend')
    return frozenset(tags)


def instruction_effects(row):
    # Static grammar facts cover semantic descriptors as well as leaves. In
    # particular resources/protection/patterns may run unknown child calls or
    # protocols; an empty descriptor must never be mistaken for purity.
    kind=row[0]
    if kind in('enter','eval','await','yield','delegate','acquire','resource','protect','pattern','operand','access'):return effect_tags(row[1])
    if kind in ('operand_return','access_return'):return frozenset(('control','call','unknown','protocol','allocation','suspend','raise','binding','state'))
    if kind=='step':return frozenset(('protocol','suspend','raise'))
    if kind in('jump','branch','flag','done'):return frozenset()
    return frozenset(('control','raise'))


def validate_program(program):
    """Validate metadata without running source, callbacks or a backend."""
    from .language import Node
    bad=lambda:(_ for _ in ()).throw(LanguageError('Malformed compiled flow program',code='ownership_error'))
    if type(program) is not FlowProgram or type(program.instructions) is not tuple or not 0<len(program.instructions)<=1024 or type(program.flag_count) is not int or not 0<=program.flag_count<=1024:bad()
    code=program.instructions;n=len(code)
    integer=lambda value,limit:type(value)is int and 0<=value<limit
    node=lambda value:type(value)in(Node,tuple) and bool(value) and type(value[0])is str
    def route(value):return value is None or type(value)is tuple and len(value)==3 and type(value[0])is int and value[0]>0 and all(integer(x,n)for x in value[1:])
    for row in code:
        if type(row)is not tuple or not row or type(row[0])is not str:bad()
        k=row[0]
        if k in ('done','reset','tick','null','return'):
            if len(row)!=1:bad()
        elif k=='jump':
            if len(row)!=2 or not integer(row[1],n):bad()
        elif k=='branch':
            if len(row)!=4 or not integer(row[1],program.flag_count)or not all(integer(x,n)for x in row[2:]):bad()
        elif k=='flag':
            if len(row)!=3 or not integer(row[1],program.flag_count)or type(row[2])is not int or row[2]not in(0,1):bad()
        elif k=='enter':
            if len(row)!=3 or not node(row[1])or not route(row[2]):bad()
        elif k=='protect':
            if len(row)!=6 or not node(row[1])or type(row[2])is not tuple or not row[2]or type(row[3])is not tuple or not integer(row[4],n)or not integer(row[5],n):bad()
            keys=set();slots=set()
            for item in row[2]:
                if type(item)is not tuple or len(item)!=3:bad()
                key,flag,entry=item
                if type(key)is not tuple or len(key)!=2 or type(key[0])is not str or key[0]not in ('body','guard','handler','cleanup','protected','else')or type(key[1])is not int or key[1]<0 or key in keys or not integer(flag,program.flag_count)or flag in slots or not integer(entry,n):bad()
                if code[entry][0]not in ('enter','eval','await','yield','delegate'):bad()
                keys.add(key);slots.add(flag)
            for item in row[3]:
                if type(item)is not tuple or len(item)!=2:bad()
                key,flag=item
                if type(key)is not tuple or len(key)!=2 or type(key[0])is not str or type(key[1])is not int or key not in keys or not integer(flag,program.flag_count)or flag in slots:bad()
                slots.add(flag)
        elif k=='operand':
            if len(row)!=5 or not node(row[1]) or row[1][0] not in ('call','let','var','list','tuple','record','binary','unary','do') or type(row[2]) is not tuple or not integer(row[3],n) or not integer(row[4],n):bad()
            keys=set();slots=set()
            for item in row[2]:
                if type(item) is not tuple or len(item)!=4:bad()
                key,flag,entry,end=item
                if type(key)is not tuple or len(key)!=2 or key[0] not in ('callee','argument_value','collection_value','first','right','boolean') or type(key[1])is not int or key[1]<0 or key in keys or not integer(flag,program.flag_count) or flag in slots or not integer(entry,n) or not integer(end,n) or entry>end:bad()
                if code[entry][0] not in ('enter','eval','await','yield','delegate') or code[end][0]!='operand_return':bad()
                keys.add(key);slots.add(flag)
            if code[row[4]][0]!='exit' or code[row[4]][1] is not row[1]:bad()
        elif k=='access':
            from .compiled_flow_access import KINDS,segments
            if len(row)!=5 or not node(row[1]) or row[1][0] not in KINDS or type(row[2])is not tuple or not integer(row[3],n) or not integer(row[4],n):bad()
            expected=segments(row[1]);keys=set();slots=set()
            if len(row[2])!=len(expected):bad()
            for item,source in zip(row[2],expected):
                if type(item)is not tuple or len(item)!=4:bad()
                key,flag,entry,end=item
                if type(key)is not tuple or len(key)!=2 or key!=source[0] or type(key[1])is not tuple or any(type(i)is not int or i<0 for i in key[1]) or key in keys or not integer(flag,program.flag_count) or flag in slots or not integer(entry,n) or not integer(end,n) or entry>end:bad()
                if code[entry][0]not in ('enter','eval','await','yield','delegate') or code[entry][1]is not source[1] or code[end][0]!='access_return' or code[end][2]!=key:bad()
                keys.add(key);slots.add(flag)
            if code[row[4]][0]!='exit' or code[row[4]][1]is not row[1]:bad()
        elif k=='access_return':
            if len(row)!=3 or not integer(row[1],n) or code[row[1]][0]!='enter' or type(row[2])is not tuple or len(row[2])!=2 or type(row[2][1])is not tuple:bad()
        elif k=='access_missing':
            if len(row)!=2 or not integer(row[1],n) or code[row[1]][0]!='enter':bad()
        elif k=='operand_return':
            if len(row)!=3 or not integer(row[1],n) or code[row[1]][0]!='enter' or type(row[2]) is not tuple or len(row[2])!=2:bad()
        elif k=='operand_missing':
            if len(row)!=2 or not integer(row[1],n) or code[row[1]][0]!='enter':bad()
        elif k=='pattern':
            if len(row)!=6 or not node(row[1])or row[1][0]not in('match','let_pattern','var_pattern','assign_pattern')or type(row[2])is not tuple or not row[2]or type(row[3])is not tuple or not integer(row[4],n)or not integer(row[5],n):bad()
            keys=set();slots=set()
            for item in row[2]:
                if type(item)is not tuple or len(item)!=4:bad()
                key,flag,entry,end=item
                if type(key)is not tuple or len(key)!=2 or key[0]not in('subject','rhs','guard','body')or type(key[1])is not int or key[1]<0 or key in keys or not integer(flag,program.flag_count)or flag in slots or not integer(entry,n)or not integer(end,n):bad()
                if code[entry][0]not in('enter','eval','await','yield','delegate')or code[end][0]!='pattern_return':bad()
                keys.add(key);slots.add(flag)
            for item in row[3]:
                if type(item)is not tuple or len(item)!=2:bad()
                key,flag=item
                if type(key)is not tuple or len(key)!=2 or type(key[0])is not str or type(key[1])is not int or key not in keys or key[0]!='guard'or not integer(flag,program.flag_count)or flag in slots:bad()
                slots.add(flag)
        elif k=='pattern_return':
            if len(row)!=3 or not integer(row[1],n)or code[row[1]][0]!='enter'or type(row[2])is not tuple or len(row[2])!=2:bad()
        elif k in('pattern_missing','pattern_next'):
            if len(row)!=2 or not integer(row[1],n)or code[row[1]][0]!='enter':bad()
        elif k=='resource':
            if len(row)!=5 or not node(row[1])or row[1][0]not in('with','async_with','with_pattern','async_with_pattern')or type(row[2])is not tuple or len(row[2])!=2 or not integer(row[3],n)or not integer(row[4],n):bad()
            keys=set();slots=set()
            for item in row[2]:
                if type(item)is not tuple or len(item)!=4:bad()
                key,flag,entry,end=item
                if type(key)is not str or key not in('manager','body')or key in keys or not integer(flag,program.flag_count)or flag in slots or not integer(entry,n)or not integer(end,n):bad()
                if code[entry][0]not in('enter','eval','await','yield','delegate')or code[end][0]!='resource_return':bad()
                keys.add(key);slots.add(flag)
        elif k=='resource_return':
            if len(row)!=3 or not integer(row[1],n)or code[row[1]][0]!='enter'or row[2]not in('manager','body'):bad()
        elif k=='resource_missing':
            if len(row)!=2 or not integer(row[1],n)or code[row[1]][0]!='enter':bad()
        elif k in ('protect_return',):
            if len(row)!=3 or not integer(row[1],n)or code[row[1]][0]!='enter'or type(row[2])is not tuple or len(row[2])!=2:bad()
        elif k in ('protect_select','protect_finish','protect_missing'):
            if len(row)!=2 or not integer(row[1],n)or code[row[1]][0]!='enter':bad()
        elif k=='protect_guard':
            if len(row)!=3 or not integer(row[1],n)or code[row[1]][0]!='enter'or type(row[2])is not bool:bad()
        elif k=='exit':
            if len(row)!=2 or not node(row[1]):bad()
        elif k in ('eval','await','yield','delegate'):
            if len(row)!=4 or not node(row[1])or type(row[2])is not bool or not route(row[3]):bad()
        elif k=='acquire':
            if len(row)!=3 or not node(row[1])or not route(row[2]):bad()
        elif k=='step':
            if len(row)!=4 or type(row[1])is not int or row[1]<=0 or not integer(row[2],program.flag_count)or not route(row[3]):bad()
        elif k=='bind':
            if len(row)!=3 or not node(row[1])or type(row[2])is not int or row[2]<=0:bad()
        elif k in ('loop_reset','unwind'):
            if len(row)!=2 or type(row[1])is not int or row[1]<0:bad()
        elif k=='condition':
            if len(row)!=2 or not integer(row[1],program.flag_count):bad()
        else:bad()
    allowed={'read','raise','binding','state','call','unknown','protocol','allocation','control','suspend','resource','module'}
    if type(program.effects)is not tuple or len(program.effects)!=n or any(type(tags)is not frozenset or any(type(tag)is not str or tag not in allowed for tag in tags)for tags in program.effects):bad()
    if type(program.node_ids)is not tuple or any(type(x)is not int for x in program.node_ids):bad()
    return (id(code),program.flag_count,tuple((id(row),row[0],tuple(id(value)if isinstance(value,tuple)else value for value in row[1:]))for row in code),program.effects)


def lower(function):
    code=[];flags=[0]
    # Async acquisition/next are real host events; the same CFG chooses body
    # and exhaustion after their original owned operation has completed.
    loop_kinds=('while','for','for_pattern') + (('async_for','async_for_pattern') if function.flavor in ('async_fn','async_gen_fn') else ())
    def emit(*item):code.append(list(item));return len(code)-1
    def slot():value=flags[0];flags[0]+=1;return value
    def route(loops):return loops[-1] if loops else None
    operand_kinds=('call','let','var','list','tuple','record','binary','unary','do')
    def operand(node,parent,depth,loops,tail):
        kind=node[0];segments=[]
        if kind=='call':
            segments.append((('callee',0),node[1],False))
            for index,arg in enumerate(node[2]):
                child=arg[2] if arg[0]=='keyword' else arg[1] if arg[0] in ('spread','kwspread') else arg
                segments.append((('argument_value',index),child,False))
        elif kind in ('list','tuple','record'):
            segments=[(('collection_value',index),item[1] if kind=='record' else item,False) for index,item in enumerate(node[1])]
        else:
            child=node[1] if kind=='do' else node[2] if kind in ('binary','unary') else node[3]
            segments.append((('first',0),child,tail if kind=='do' else False))
            if kind=='binary':segments.append((('boolean' if node[1] in ('&&','||') else 'right',0),node[3],tail if node[1] in ('&&','||') else False))
        meta=emit('operand',node,None,None,None)
        router=len(code);routes=[];branches=[]
        for key,child,child_tail in segments:
            flag=slot();branches.append(emit('branch',flag,None,len(code)+1));routes.append([key,flag,None,None])
        missing=emit('operand_missing',parent)
        for index,(key,child,child_tail) in enumerate(segments):
            routes[index][2]=len(code);code[branches[index]][2]=len(code)
            build(child,depth,loops,child_tail)
            routes[index][3]=emit('operand_return',parent,key);emit('jump',missing)
        end=len(code);emit('exit',node)
        code[meta][2:]=[tuple(tuple(row) for row in routes),router,end]
    from .compiled_flow_access import KINDS as access_kinds,segments as access_segments
    def access(node,parent,depth,loops):
        selected=access_segments(node)
        meta=emit('access',node,None,None,None)
        router=len(code);routes=[];branches=[]
        for key,child,regions in selected:
            flag=slot();branches.append(emit('branch',flag,None,len(code)+1));routes.append([key,flag,None,None])
        missing=emit('access_missing',parent)
        for index,(key,child,regions) in enumerate(selected):
            routes[index][2]=len(code);code[branches[index]][2]=len(code)
            build(child,depth,loops,False)
            routes[index][3]=emit('access_return',parent,key);emit('jump',missing)
        end=len(code);emit('exit',node)
        code[meta][2:]=[tuple(tuple(row)for row in routes),router,end]
    protected_kinds=('try','try_cases','try_group','finally','try_else')
    def protection(node,parent,depth,loops,tail):
        kind=node[0];segments=[];decisions={}
        if kind in ('try','try_cases','try_group'):
            segments.append((('body',0),node[1],True))
            cases=[(None,None,node[3])]if kind=='try'else node[2]
            for index,case in enumerate(cases):
                guard=case[2]if kind=='try_group'else case[1]
                handler=case[4]if kind=='try_group'else case[2]
                if guard is not None:
                    segments.append((('guard',index),guard,kind=='try_group'))
                    decisions[('guard',index)]=slot()
                segments.append((('handler',index),handler,True if kind=='try_group'else tail))
        elif kind=='finally':
            segments=[(('body',0),node[1],True),(('cleanup',0),node[2],True)]
        else:
            segments=[(('protected',0),node[1],tail),(('else',0),node[2],tail)]
            decisions[('protected',0)]=slot()
        meta=emit('protect',node,None,None,None,None)
        router=len(code);routes=[];branches=[]
        for key,child,child_tail in segments:
            flag=slot();branches.append(emit('branch',flag,None,len(code)+1));routes.append([key,flag,None])
        missing=emit('protect_missing',parent)
        labels={};decision_branches=[]
        for index,(key,child,child_tail)in enumerate(segments):
            labels[key]=len(code);routes[index][2]=len(code);code[branches[index]][2]=len(code)
            build(child,depth,loops,child_tail)
            emit('protect_return',parent,key)
            if key in decisions:
                branch=emit('branch',decisions[key],None,None)
                if kind=='try_group':
                    yes=emit('protect_guard',parent,True);emit('jump',missing)
                    no=emit('protect_guard',parent,False);emit('jump',missing)
                    code[branch][2:]=[yes,no]
                else:
                    no=emit('protect_select'if key[0]=='guard'else'protect_finish',parent)
                    emit('jump',missing)
                    decision_branches.append((branch,('handler',key[1])if key[0]=='guard'else('else',0),no))
            else:emit('jump',missing)
        exit_pc=len(code)
        for branch,key,no in decision_branches:code[branch][2:]=[labels[key],no]
        code[meta][2:]=[tuple(tuple(row)for row in routes),tuple(decisions.items()),router,exit_pc]
        emit('exit',node)

    resource_kinds=('with','async_with','with_pattern','async_with_pattern')
    def resource(node,parent,depth,loops):
        meta=emit('resource',node,None,None,None)
        router=len(code);routes=[];branches=[]
        for key in ('manager','body'):
            flag=slot();branches.append(emit('branch',flag,None,len(code)+1));routes.append([key,flag,None,None])
        missing=emit('resource_missing',parent)
        for index,(key,child,child_tail)in enumerate((('manager',node[2],False),('body',node[3],True))):
            routes[index][2]=len(code);code[branches[index]][2]=len(code)
            build(child,depth,loops,child_tail)
            routes[index][3]=emit('resource_return',parent,key)
            emit('jump',missing)
        code[meta][2:]=[tuple(tuple(row)for row in routes),router,len(code)]
        emit('exit',node)

    pattern_kinds=('match','let_pattern','var_pattern','assign_pattern')
    def pattern_control(node,parent,depth,loops,tail):
        decisions={};segments=[]
        if node[0]=='match':
            segments.append((('subject',0),node[1],False))
            for index,case in enumerate(node[2]):
                if case[1]is not None:
                    segments.append((('guard',index),case[1],False));decisions[('guard',index)]=slot()
                segments.append((('body',index),case[2],tail))
        else:segments.append((('rhs',0),node[2]if node[0]=='assign_pattern'else node[3],False))
        meta=emit('pattern',node,None,None,None,None)
        router=len(code);routes=[];branches=[]
        for key,child,child_tail in segments:
            flag=slot();branches.append(emit('branch',flag,None,len(code)+1));routes.append([key,flag,None,None])
        missing=emit('pattern_missing',parent);labels={};guards=[]
        for index,(key,child,child_tail)in enumerate(segments):
            labels[key]=len(code);routes[index][2]=len(code);code[branches[index]][2]=len(code)
            build(child,depth,loops,child_tail);routes[index][3]=emit('pattern_return',parent,key)
            if key[0]=='guard':
                branch=emit('branch',decisions[key],None,None);next_case=emit('pattern_next',parent);emit('jump',missing)
                guards.append((branch,('body',key[1]),next_case))
            else:emit('jump',missing)
        for branch,key,next_case in guards:code[branch][2:]=[labels[key],next_case]
        code[meta][2:]=[tuple(tuple(row)for row in routes),tuple(decisions.items()),router,len(code)]
        emit('exit',node)

    def build(node,depth=0,loops=(),tail=False):
        kind=node[0]
        structural=kind in access_kinds or kind in operand_kinds or kind in ('block','if','return','break','continue') or kind in loop_kinds or kind in protected_kinds or kind in resource_kinds or kind in pattern_kinds
        structural=structural or kind=='loop_else' and node[1][0] in loop_kinds
        if not structural:
            # One original semantic operation, including its protected cleanup,
            # dynamic calls and all internal suspension. No callable whitelist.
            # The backend returns at each stream semantic leaf. The existing
            # suspension/iteration frames own feedback and any number of
            # delegated requests until this one event actually completes.
            event = ('await' if kind=='await' and function.flavor in ('async_fn','async_gen_fn') else
                     'yield' if kind=='yield' and function.flavor in ('gen_fn','async_gen_fn') else
                     'delegate' if kind=='yield_from' and function.flavor in ('gen_fn','async_gen_fn') else 'eval')
            emit(event,node,tail,route(loops));return None
        parent=emit('enter',node,route(loops));depth+=1
        if kind in access_kinds:
            access(node,parent,depth,loops)
            return None
        if kind in operand_kinds:
            operand(node,parent,depth,loops,tail)
            return None
        if kind in pattern_kinds:
            pattern_control(node,parent,depth,loops,tail)
            return None
        if kind in resource_kinds:
            resource(node,parent,depth,loops)
            return None
        if kind in protected_kinds:
            protection(node,parent,depth,loops,tail)
            return None
        completion=None
        if kind=='block':
            for index,statement in enumerate(node[1]):
                build(statement,depth,loops,tail and index==len(node[1])-1);emit('reset')
        elif kind=='if':
            build(node[1],depth,loops);condition=slot();emit('condition',condition)
            branch=emit('branch',condition,None,None);code[branch][2]=len(code)
            build(node[2],depth,loops,tail);jump=emit('jump',None);code[branch][3]=len(code)
            build(node[3],depth,loops,tail);code[jump][1]=len(code)
        elif kind in loop_kinds:
            completion=slot();emit('flag',completion,0)
            if kind!='while':emit('acquire',node,route(loops))
            reset_pc=len(code);emit('loop_reset',depth)
            condition=slot()
            if kind=='while':
                build(node[1],depth,loops);emit('condition',condition)
            else:emit('step',depth,condition,route(loops))
            branch=emit('branch',condition,None,None);code[branch][2]=len(code)
            emit('tick')
            if kind!='while':emit('bind',node,depth)
            context={'depth':depth,'continue':reset_pc,'break':None,'breaks':[]}
            build(node[2] if kind=='while' else node[3],depth,loops+(context,))
            emit('jump',reset_pc)
            code[branch][3]=len(code);emit('flag',completion,1)
            context['break']=len(code)
            for jump in context['breaks']:code[jump][1]=len(code)
            emit('loop_reset',depth);emit('null')
        elif kind=='loop_else':
            if node[1][0] not in loop_kinds:raise Unsupported('flow_loop')
            completion=build(node[1],depth,loops)
            branch=emit('branch',completion,None,None);code[branch][2]=len(code)
            build(node[2],depth,loops,tail);code[branch][3]=len(code)
        elif kind=='return':
            build(node[1],depth,loops,True);emit('return')
        else:
            if not loops:raise Unsupported('flow_loop_signal')
            context=loops[-1];emit('unwind',context['depth'])
            jump=emit('jump',context['continue'] if kind=='continue' else None)
            if kind=='break':context['breaks'].append(jump)
        emit('exit',node)
        return completion
    build(function.body,tail=True);emit('done')
    if len(code)>1024:raise Unsupported('flow_instruction_limit')
    # The handler is the lexical surrounding loop body, not the loop whose
    # source/factory/step is being evaluated. Freeze labels after backpatching.
    for item in code:
        if item[0] in ('enter','eval','await','yield','delegate','acquire','step'):
            target=item[-1]
            item[-1]=(target['depth'],target['continue'],target['break']) if target else None
    instructions=tuple(tuple(i)for i in code)
    effects=tuple(instruction_effects(row)for row in instructions)
    program=FlowProgram(instructions,flags[0],node_ids=node_ids(function.body),effects=effects)
    program.layout_seal=validate_program(program)
    return program

def compile_plan(evaluator,function,result,plan):
    from .jit_regions import analyze_structured,compile_regions
    from .native_flow import compile_flow
    from .native_jit import BackendUnavailable
    plan.analysis=analyze_structured(function,evaluator.tick,control_flow=True,resumable=True)
    from .compilation_artifact import link_flow_template
    program=link_flow_template(evaluator,function)
    if program is None:program=lower(function)
    if validate_program(program)!=program.layout_seal:raise LanguageError('Compiled flow layout changed',code='ownership_error')
    # Reserve the control kernel before any scalar allocation, avoiding a
    # partial quota bypass. Every actually allocated engine is charged.
    if evaluator.native_kernel_count>=MAX_KERNELS:raise Unsupported('kernel_limit')
    try:
        evaluator.tick();program.kernel=compile_flow(program,code_cache=evaluator.native_code_cache);evaluator.native_kernel_count+=1;evaluator.tick()
    except (BackendUnavailable,OSError,RuntimeError) as error:
        plan.reason=str(error) if isinstance(error,BackendUnavailable) else 'backend_failure'
        if plan.requested=='native-flow':raise LanguageError('本机原生编译后端不可用: '+plan.reason,code='compiler_error') from None
        plan.flow=program;return result
    requested=plan.requested
    try:
        plan.requested='native-control' if requested=='native-flow' else 'auto-control'
        compile_regions(evaluator,function,result,plan,optional=True)
    finally:plan.requested=requested
    plan.kernel=program.kernel;plan.status='native';plan.flow=program
    return result

def evaluate(plan,evaluator,function,scope):
    # A single opaque request transport, not a second flow interpreter. The
    # ordinary parent cursor owns every actual lexical and host activation.
    from .compiled_flow_frames import FlowBodyRequest, body_frame
    return (yield FlowBodyRequest(evaluator,body_frame(plan,evaluator,function,scope)))


def info(evaluator,function):
    from .jit import _owned
    _owned(evaluator,function)
    flow=getattr(function.compiled_plan,'flow',None)
    if flow:return flow.info()
    return {'schema':'chaos.compiled-flow/1','abi':'pc-host-events/1','status':'not_compiled','instructionCount':0,'branchCount':0,'checkpointCount':0,'resumeCalls':0,'nativeResumes':0,'portableResumes':0,'lastPC':0,'definitionPolicy':'stable-during-invocation'}
