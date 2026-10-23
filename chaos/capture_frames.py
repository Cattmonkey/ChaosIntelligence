"""Explicit active evaluation roots; never Python generator/frame reflection.

A frame owns operands until its semantic operation completes. Control nodes
reset consumed statement/iteration roots; tail dispatch replaces its operands.
The holder is selected once on entry and remains fixed through suspension.
"""
from dataclasses import dataclass,field

@dataclass(eq=False)
class Frame:
    owner: object
    holder: object
    kind: str
    roots: list = field(default_factory=list)
    identities: set = field(default_factory=set)
    capabilities: object = None


@dataclass(frozen=True,eq=False)
class Edge:
    owner: object
    capabilities: object
    values: tuple


def holder(evaluator):
    scheduler=evaluator.suspension
    return scheduler.stream.runner if scheduler.stream is not None else scheduler.current


def active(evaluator):
    current=holder(evaluator)
    return current.capture_frames[-1] if current is not None and current.capture_frames else None


def retain(frame,*values):
    if frame is None:return
    from .language import ResourceLimit
    for value in values:
        if value is None or type(value) in (bool,int,float,str):continue
        identity=id(value)
        if identity in frame.identities:continue
        if len(frame.roots)>=20000:raise ResourceLimit('Active evaluation capture roots exceed 20000 values')
        frame.identities.add(identity);frame.roots.append(value)


def reset(frame,*values):
    if frame is None:return
    frame.roots.clear();frame.identities.clear();retain(frame,*values)


def enter(evaluator,kind,*values):
    current=holder(evaluator)
    if current is None:return None
    from .agent_tools import current_caps
    frame=Frame(evaluator,current,kind,capabilities=current_caps(evaluator))
    retain(frame,*values)
    current.capture_frames.append(frame)
    return frame


def leave(frame,result):
    if frame is None:return
    from .language import MISSING
    frames=frame.holder.capture_frames
    if not frames or frames[-1] is not frame:
        raise RuntimeError('Evaluation capture frame lifetime mismatch')
    frames.pop()
    frame.roots.clear();frame.identities.clear()
    if isinstance(result,BaseException):
        from .suspension import hard_error
        if hard_error(result):return
    if result is not MISSING and frames:retain(frames[-1],result)
