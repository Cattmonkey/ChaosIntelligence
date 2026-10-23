"""Sealed CFG children on original assignment, access and item-key frames.

Original frames own all values, protocol calls and commits. This module only
routes a requested semantic child and consumes its actual sealed return site.
"""
KINDS = frozenset(('assign','index','subscript','set_member','set_item','aug_assign'))


def key_segments(node,path=()):
    if node[0]=='tuple':
        for index,part in enumerate(node[1]):
            if part[0]=='slice':
                for offset,child in enumerate(part[1:]):
                    if child is not None:yield (('key',path+(index,offset)),child,False)
            else:yield (('key',path+(index,)),part,False)
    elif node[0]=='slice':
        for index,part in enumerate(node[1:]):
            if part is not None:yield (('key',path+(index,)),part,False)
    else:yield (('key',path),node,False)


def segments(node):
    kind=node[0]
    if kind=='assign':return ((('assign',()),node[2],True),)
    if kind=='aug_assign':
        target=node[1];rows=[]
        if target[0]!='name':
            rows.append((('target',()),target[1],False))
            rows.extend(key_segments(target[2]))
        rows.append((('right',()),node[3],True))
        return tuple(rows)
    rows=[(('target',()),node[1],False)]
    if kind in ('subscript','set_item'):rows.extend(key_segments(node[2]))
    else:rows.append((('key',()),node[2],False))
    if kind in ('set_member','set_item'):rows.append((('item',()),node[3],False))
    return tuple(rows)


def attach(frame,boundary):
    frame._flow_boundary=boundary
    boundary.flow_operation=frame
    frame._flow_access_segment=None
    frame._flow_access_child=None
    return frame


def assignment_frame(boundary):
    from .execution_frames import EvaluationBoundaryFrame
    from .execution_class_creation import ClassSyntaxEvaluationFrame
    from .language import LanguageError
    if type(boundary)is not EvaluationBoundaryFrame or boundary.node[0]!='assign' or not getattr(boundary,'flow_structural',False):
        raise LanguageError('Malformed compiled assignment boundary',code='ownership_error')
    return attach(ClassSyntaxEvaluationFrame(boundary.node,boundary.env,boundary.options.get('_compiled_regions')),boundary)


def state_of(frame):
    from .execution_frames import EvaluationBoundaryFrame
    from .compiled_flow_state import FlowState
    from .language import LanguageError
    boundary=getattr(frame,'_flow_boundary',None)
    state=getattr(boundary,'flow_state',None)
    if (type(boundary)is not EvaluationBoundaryFrame or type(state)is not FlowState
            or state.owner is not frame.owner or boundary.node is not frame.node
            or getattr(boundary,'flow_operation',None)is not frame):
        raise LanguageError('Foreign compiled access activation',code='ownership_error')
    return state


def child_key(frame):
    from .execution_access import ItemKeyFrame
    if type(frame)is ItemKeyFrame:
        path=frame._flow_access_path
        return ('key',path if frame.phase=='scalar' else path+(frame.index,))
    return (frame.phase,())


def validate_keys(state,parent):
    from .execution_access import ItemKeyFrame
    cursor=state._cursor
    if cursor is None:return
    root=getattr(parent,'_flow_access_key_root',None)
    if root is not None and root in cursor.frames:
        if type(root)is not ItemKeyFrame or root._flow_access_parent is not parent:state.malformed()
    keys=[frame for frame in cursor.frames if type(frame)is ItemKeyFrame and getattr(frame,'_flow_access_parent',None)is parent]
    if not keys:return
    source=parent.node[1][2] if parent.node[0]=='aug_assign' else parent.node[2]
    if parent.phase!='key' or type(root)is not ItemKeyFrame or root not in keys or root.node is not source or root._flow_access_path!=():state.malformed()
    for frame in keys:
        path=frame._flow_access_path
        if type(path)is not tuple or any(type(i)is not int or i<0 for i in path):state.malformed()
        expected=source
        if path:
            if len(path)!=1 or source[0]!='tuple' or path[0]>=len(source[1]):state.malformed()
            expected=source[1][path[0]]
            if expected[0]!='slice' or root.phase!='part' or root.index!=path[0] or cursor.frames.index(frame)<=cursor.frames.index(root):state.malformed()
        elif frame is not root:state.malformed()
        if frame.node is not expected or frame.owner is not parent.owner or frame.env is not parent.env:state.malformed()
        if type(frame.index)is not int or type(frame.parts)is not list or type(frame.phase)is not str:state.malformed()
        if expected[0]not in ('tuple','slice'):
            if frame.phase not in ('start','scalar','done') or frame.index!=0 or frame.parts:state.malformed()
        else:
            items=expected[1] if expected[0]=='tuple' else expected[1:]
            if not 0<=frame.index<=len(items) or len(frame.parts)!=frame.index or frame.phase not in ('start','next','part','resolve','done'):state.malformed()
            if any(items[i] is None and frame.parts[i] is not None for i in range(frame.index)):state.malformed()
            if frame.phase=='part' and (frame.index==len(items) or items[frame.index] is None):state.malformed()
            if frame.phase=='resolve' and frame.index!=len(items):state.malformed()


def validate_operation(state,boundary):
    if boundary.node[0] not in KINDS:return
    from .execution_access import ObjectAccessEvaluationFrame,ItemKeyFrame
    from .execution_class_creation import ClassSyntaxEvaluationFrame
    from .language import Env
    frame=getattr(boundary,'flow_operation',None)
    if frame is None:return
    expected=ClassSyntaxEvaluationFrame if boundary.node[0]=='assign' else ObjectAccessEvaluationFrame
    if (type(frame)is not expected or frame.owner is not state.owner or frame.node is not boundary.node
            or frame.env is not boundary.env or getattr(frame,'_flow_boundary',None)is not boundary):state.malformed()
    phases={'start','assign','finish','done'} if expected is ClassSyntaxEvaluationFrame else {'start','target','key','item','previous','right','operation','assigned','finish','done'}
    if type(frame.phase)is not str or frame.phase not in phases:state.malformed()
    validate_keys(state,frame)
    region=getattr(frame,'_flow_access_region',None)
    if region is None:
        if state.phase in ('host','checkpoint'):state.malformed()
        return
    if region is not state.program.instructions[boundary.flow_index+1] or region[0]!='access':state.malformed()
    segment=frame._flow_access_segment;child=frame._flow_access_child
    if segment is None:
        if child is not None:state.malformed()
        if state._unwind_error is None and state.phase in ('host','checkpoint'):
            required=frame.phase in ('target','item','right','assign') or frame.phase=='key' and frame.node[0] in ('index','set_member')
            if required:state.malformed()
        return
    row=next((row for row in region[2] if row[0] is segment),None)
    if row is None:state.malformed()
    if child is frame:
        if child_key(frame)!=segment:state.malformed()
    elif type(child)is ItemKeyFrame:
        if (child.owner is not state.owner or child.env is not frame.env
                or child._flow_access_parent is not frame or frame.phase!='key'
                or state._cursor is None or child not in state._cursor.frames
                or type(child._flow_access_path)is not tuple
                or any(type(i)is not int or i<0 for i in child._flow_access_path)
                or child.phase not in ('scalar','part') or type(child.index)is not int or child.index<0
                or child_key(child)!=segment):state.malformed()
    else:state.malformed()
    if state.phase in ('host','checkpoint') and not row[2]<=state._event_index<=row[3]:state.malformed()


def evaluate(frame,node,*,regions=False):
    from .execution_access import ItemKeyFrame
    from .execution_class_creation import ClassSyntaxEvaluationFrame
    from .execution_frames import Push,request_proxy
    from .compiled_flow_frames import take,make_boundary,FlowBodyRequest
    parent=frame._flow_access_parent if type(frame)is ItemKeyFrame else frame
    state=state_of(parent);region=parent._flow_access_region
    if parent._flow_access_segment is not None:state.malformed()
    key=child_key(frame);row=next((row for row in region[2] if row[0]==key),None)
    if row is None:state.malformed()
    expected=next((item for item in segments(parent.node) if item[0]==key),None)
    if expected is None or expected[1]is not node or expected[2] is not regions:state.malformed()
    parent._flow_access_segment=row[0];parent._flow_access_child=frame
    for actual,flag,entry,end in region[2]:state.flags[flag]=int(actual==key)
    state.pc=region[3];event=take(state)
    if event is None or state._event_index!=row[2] or event[1]is not node:state.malformed()
    options={'all_results':False}
    if regions and parent._flow_boundary.options.get('_compiled_regions') is not None:
        options['_compiled_regions']=parent._flow_boundary.options['_compiled_regions']
    boundary=make_boundary(state,event,frame.env,False,options)
    boundary.tail=False;boundary.options['all_results']=False
    if not regions or parent._flow_boundary.options.get('_compiled_regions') is None:
        boundary.options.pop('_compiled_regions',None)
    state.result=None
    # Name assignment has an existing native leaf bridge; retain it. Access
    # and key frames originally push their child boundary directly.
    if type(frame)is ClassSyntaxEvaluationFrame:
        return frame.leaf(request_proxy(FlowBodyRequest(frame.owner,boundary)))
    return Push(boundary)


def returned(parent,frame,error):
    from .compiled_flow_frames import take
    state=state_of(parent)
    if parent._flow_access_segment is not None and parent._flow_access_child is frame:
        key=parent._flow_access_segment
        if error is None and state._unwind_error is None:
            event=take(state)
            if event!=('access_return',parent._flow_boundary.flow_index,key):state.malformed()
        parent._flow_access_segment=parent._flow_access_child=None
    return state


def step(frame,cursor,value,error,advance):
    from .compiled_flow_frames import take
    from .execution_frames import Done
    state=state_of(frame)
    if not hasattr(frame,'_flow_access_region'):
        event=take(state)
        if event is None or event[0]!='access' or event[1]is not frame.node:state.malformed()
        frame._flow_access_region=event
    else:returned(frame,frame,error)
    action=advance(cursor,value,error)
    if type(action)is Done:
        if frame._flow_access_segment is not None:state.malformed()
        if state._unwind_error is not None:raise state._unwind_error
        state.pc=frame._flow_access_region[4];event=take(state)
        if event is None or event[0]!='exit' or event[1]is not frame.node:state.malformed()
        state.result=action.value
    return action


def key_step(frame,cursor,value,error,advance):
    parent=frame._flow_access_parent
    state=state_of(parent)
    if frame.owner is not parent.owner or frame.env is not parent.env or parent.phase!='key':state.malformed()
    validate_keys(state,parent)
    returned(parent,frame,error)
    return advance(cursor,value,error)
