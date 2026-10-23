"""Closed source bundles: deterministic identities, dependency checks, no implicit I/O."""
import posixpath
from pathlib import Path
from importlib import resources
from .language import Parser, LanguageError, ResourceLimit
from .compilation_cache import DEFAULT_COMPILER_CACHE, parse_unit

MAX_FILES, MAX_BYTES, MAX_DEPTH = 64, 1000000, 32
STDLIB = {
    'std/intelligence': resources.files('chaos').joinpath('stdlib/intelligence.chaos').read_text(encoding='utf-8'),
    'std/evolve': resources.files('chaos').joinpath('stdlib/evolve.chaos').read_text(encoding='utf-8'),
    'std/process': resources.files('chaos').joinpath('stdlib/process.chaos').read_text(encoding='utf-8'),
    'std/memory': resources.files('chaos').joinpath('stdlib/memory.chaos').read_text(encoding='utf-8'),
    'std/bot': 'let bot=bot;let bot_info=bot_info;let bot_context=bot_context;let bot_reconcile=bot_reconcile;let process_guide=process_guide;let process_events=process_events;let process_message=process_message;export{bot,bot_info,bot_context,bot_reconcile,process_guide,process_events,process_message};',
    'std/control': 'let prompt_tag=prompt_tag;let call_prompt=call_prompt;let prompt_scope=prompt_scope;let abort_prompt=abort_prompt;let prompt_available=prompt_available;let call_escape=call_escape;let escape_info=escape_info;let capture_continuation=capture_continuation;let continuation_info=continuation_info;let control_barrier=control_barrier;let dynamic_wind=dynamic_wind;export{prompt_tag,call_prompt,prompt_scope,abort_prompt,prompt_available,call_escape,escape_info,capture_continuation,continuation_info,control_barrier,dynamic_wind};',
    'std/values': 'let values=return_values;let return_values=return_values;let values_list=values_list;let multiple_value_list=multiple_value_list;let nth_value=nth_value;let multiple_value_call=multiple_value_call;let multiple_value_bind=multiple_value_bind;let multiple_value_prog1=multiple_value_prog1;export{values,return_values,values_list,multiple_value_list,nth_value,multiple_value_call,multiple_value_bind,multiple_value_prog1};',
    'std/robot': 'let robot_open=robot_open; let robot_info=robot_info; let robot_observe=robot_observe; let robot_observation_info=robot_observation_info; let robot_command=robot_command; let robot_command_info=robot_command_info; let robot_act=robot_act; let robot_stop=robot_stop; let robot_close=robot_close; let robot_receipt_info=robot_receipt_info; export{robot_open,robot_info,robot_observe,robot_observation_info,robot_command,robot_command_info,robot_act,robot_stop,robot_close,robot_receipt_info};',
    'std/device': 'let device_open=device_open;let device_info=device_info;let device_action=device_action;let device_action_info=device_action_info;let device_act=device_act;let device_receipt_info=device_receipt_info;let device_close=device_close;export{device_open,device_info,device_action,device_action_info,device_act,device_receipt_info,device_close};',
    'std/iter': resources.files('chaos').joinpath('stdlib/iter.chaos').read_text(encoding='utf-8'),
    'std/contracts': 'let contract_ref=contract_ref; let contract_recursive=contract_recursive; let contract_list=contract_list; let contract_tuple=contract_tuple; let contract_record=contract_record; let contract_union=contract_union; let contract_optional=contract_optional; let contract_map=contract_map; let contract_accepts=contract_accepts; let contract_info=contract_info; export {contract_ref,contract_recursive,contract_list,contract_tuple,contract_record,contract_union,contract_optional,contract_map,contract_accepts,contract_info};',
    'std/hash': 'let hash=hash; let hash_map=hash_map; let hash_get=hash_get; let hash_set=hash_set; let hash_remove=hash_remove; let hash_has=hash_has; let hash_items=hash_items; let hash_keys=hash_keys; let hash_values=hash_values; let hash_info=hash_info; export {hash,hash_map,hash_get,hash_set,hash_remove,hash_has,hash_items,hash_keys,hash_values,hash_info};',
    'std/generic': 'let generic_eql=generic_eql; let generic_eql_info=generic_eql_info; let generic_function=generic_function; let generic_method=generic_method; let generic_remove=generic_remove; let generic_methods=generic_methods; let generic_info=generic_info; let generic_next_available=generic_next_available; export {generic_eql,generic_eql_info,generic_function,generic_method,generic_remove,generic_methods,generic_info,generic_next_available};',
    'std/conditions': 'let condition=condition; let condition_info=condition_info; let handler_bind=handler_bind; let restart_bind=restart_bind; let restart_case=restart_case; let signal_condition=signal_condition; let error_condition=error_condition; let invoke_debugger=invoke_debugger; let compute_restarts=compute_restarts; let find_restart=find_restart; let invoke_restart=invoke_restart; let restart_info=restart_info; export {condition,condition_info,handler_bind,restart_bind,restart_case,signal_condition,error_condition,invoke_debugger,compute_restarts,find_restart,invoke_restart,restart_info};',
    'std/swarm': resources.files('chaos').joinpath('stdlib/swarm.chaos').read_text(encoding='utf-8'),
    'std/context': """
fn completed_close(value) {
  let kind = type_of(value);
  if kind == "Task" || kind == "Generator" || kind == "AsyncGenerator" {
    throw "关闭回调返回了未完成的 Task/Generator/AsyncGenerator";
  } else { null }
}
class Closing {
  var value = null;
  var closer = null;
  fn __init__(self, value, closer) { self.value = value; self.closer = closer; null }
  fn __enter__(self) { self.value }
  fn __exit__(self, error) { completed_close(self.closer(self.value)); null }
}
class AsyncClosing {
  var value = null;
  var closer = null;
  fn __init__(self, value, closer) { self.value = value; self.closer = closer; null }
  async fn __aenter__(self) { self.value }
  async fn __aexit__(self, error) { completed_close(await self.closer(self.value)); null }
}
fn closing(value, closer = close) { Closing(value, closer) }
fn aclosing(value, closer = aclose) { AsyncClosing(value, closer) }
fn context_factory(factory, asynchronous) {
  class ContextFactory {
    fn __call__(self, *args, **kwargs) {
      generator_context(factory(*args, **kwargs), asynchronous)
    }
    fn __get__(self, instance, owner) {
      if instance == null { self }
      else { fn(*args, **kwargs) { self(instance, *args, **kwargs) } }
    }
  }
  ContextFactory()
}
fn contextmanager(factory) { context_factory(factory, false) }
fn asynccontextmanager(factory) { context_factory(factory, true) }
fn ExitStack(max_entries=1024) -> ExitStack { exit_stack(false,max_entries) }
fn AsyncExitStack(max_entries=1024) -> AsyncExitStack { exit_stack(true,max_entries) }
let context_var=context_var;let context_get=context_get;let context_set=context_set;let context_reset=context_reset;let context_bind=context_bind;let context=context;let copy_context=copy_context;let context_copy=context_copy;let context_get_in=context_get_in;let context_has=context_has;let context_items=context_items;let context_info=context_info;let context_run=context_run;
export {closing, aclosing, contextmanager, asynccontextmanager, ExitStack, AsyncExitStack,context_var,context_get,context_set,context_reset,context_bind,context,copy_context,context_copy,context_get_in,context_has,context_items,context_info,context_run};
""",
    'std/list': 'let map=map; let filter=filter; let fold=fold; let range=range; let length=length; let head=head; let tail=tail; let append=append; let reverse=reverse; let sort=sort; let zip=zip; let flatten=flatten; let any=any; let all=all; let take=take; let drop=drop; export {map,filter,fold,range,length,head,tail,append,reverse,sort,zip,flatten,any,all,take,drop};',
    'std/string': 'let upper=upper; let lower=lower; let trim=trim; let split=split; let join=join; let length=length; let contains=contains; let replace=replace; let slice=slice; export {upper,lower,trim,split,join,length,contains,replace,slice};',
    'std/math': 'let pi=3.141592653589793; let e=2.718281828459045; let abs=abs; let min=min; let max=max; let sum=sum; let floor=floor; let ceil=ceil; let sqrt=sqrt; let pow=pow; let round=round; let fraction=fraction; let complex=complex; let conjugate=conjugate; let numeric_encode=numeric_encode; let numeric_decode=numeric_decode; let decimal=decimal;let decimal_text=decimal_text;let decimal_context=decimal_context;let get_decimal_context=get_decimal_context;let decimal_context_info=decimal_context_info;let decimal_clear_flags=decimal_clear_flags;let decimal_quantize=decimal_quantize; export {pi,e,abs,min,max,sum,floor,ceil,sqrt,pow,round,fraction,complex,conjugate,numeric_encode,numeric_decode,decimal,decimal_text,decimal_context,get_decimal_context,decimal_context_info,decimal_clear_flags,decimal_quantize};',
    'std/record': 'let get=get; let set=set; let keys=keys; let values=values; let has=has; let merge=merge; let remove=remove; export {get,set,keys,values,has,merge,remove};',
    'std/json': 'let parse=parse_json; let stringify=json_stringify; let copy=json_copy; export {parse,stringify,copy};',
    'std/result': 'data Result { Ok(value), Err(error) }; fn map(f, r) { match r { Ok(x) => Ok(f(x)), Err(e) => Err(e) } }; export {Result,Ok,Err,map};',
    'std/option': 'data Option { None, Some(value) }; fn map(f, o) { match o { Some(x) => Some(f(x)), None() => None() } }; export {Option,None,Some,map};',
}


def path_name(path):
    if (not isinstance(path, str) or not path or len(path) > 240 or '\\' in path or '\x00' in path
            or path.startswith('/') or ':' in path):
        raise LanguageError('模块路径必须是包内 POSIX 相对路径', code='module_error')
    normalized = posixpath.normpath(path)
    if normalized in ('.', '..') or normalized.startswith('../'):
        raise LanguageError('模块路径越出程序包', code='module_error')
    return normalized


def resolve_path(specifier, importer):
    if specifier in STDLIB: return specifier
    if not isinstance(specifier, str) or specifier.startswith('std/'):
        raise LanguageError(f'未知标准模块 {specifier}', code='module_error')
    # Check before joining: an absolute or foreign-platform path cannot reset the root.
    if not specifier or specifier.startswith('/') or '\\' in specifier or ':' in specifier or '\x00' in specifier:
        raise LanguageError('非法模块路径', code='module_error')
    target = path_name(posixpath.join(posixpath.dirname(importer), specifier))
    if not target.endswith('.chaos'):
        raise LanguageError('本地模块必须使用 .chaos 扩展名', code='module_error')
    return target


def bundle_files(source, files=None, entry='main.chaos'):
    entry = path_name(entry)
    if entry.startswith('std/') or not entry.endswith('.chaos'):
        raise LanguageError('入口必须是非 std 目录的 .chaos 文件', code='module_error')
    if not isinstance(source, str): raise LanguageError('source 必须是字符串')
    if files is None: files = {}
    if not isinstance(files, dict) or len(files) > MAX_FILES:
        raise ResourceLimit(f'程序包最多 {MAX_FILES} 个文件')
    result = {}
    for name, text in files.items():
        canonical = path_name(name)
        if canonical != name or name.startswith('std/') or not name.endswith('.chaos'):
            raise LanguageError(f'非规范模块文件名 {name}', code='module_error')
        if not isinstance(text, str): raise LanguageError('模块源码必须是字符串')
        result[name] = text
    # The legacy source field is the authoritative entry mirror on every save.
    result[entry] = source
    if len(result) > MAX_FILES: raise ResourceLimit(f'程序包最多 {MAX_FILES} 个文件')
    if any(len(s) > 100000 for s in result.values()): raise ResourceLimit('单文件超过 100 KB 限制')
    try: size = sum(len(s.encode('utf-8')) for s in result.values())
    except UnicodeError: raise LanguageError('源码包含无效 Unicode') from None
    if size > MAX_BYTES: raise ResourceLimit('程序包源码超过 1 MB')
    return result, entry


def declarations(tree, *, include_macros=False):
    names, exports, mutable, export_node = set(), [], set(), None
    for node in tree[1]:
        if node[0] == 'decorated': node = node[2]
        kind = node[0]
        bound = []
        if kind in ('let', 'var', 'def', 'agent', 'macro', 'class'): bound = [node[1]]
        if kind in ('let_pattern', 'var_pattern'):
            from .patterns import pattern_names
            bound = pattern_names(node[1])
            if kind == 'var_pattern': mutable.update(bound)
        if kind == 'var': mutable.add(node[1])
        if kind == 'import': bound = [node[2]]
        elif kind == 'rec': bound = [d[1] for d in node[1]]
        elif kind == 'data': bound = [node[1]] + [v[0] for v in node[2]]
        elif kind == 'export': exports += node[1]; export_node = node
        for name in bound:
            if name in names: raise LanguageError(f'重复顶层名称 {name}', code='module_error').attach(getattr(node,'span',{}))
            names.add(name)
    if len(exports) != len(set(exports)): raise LanguageError('重复 export 名称', code='module_error').attach(getattr(export_node,'span',{}))
    if set(exports) & mutable: raise LanguageError('可变 var 不可直接导出；请导出读取/更新函数', code='module_error').attach(getattr(export_node,'span',{}))
    macro_names = {node[1] for node in tree[1] if node[0] == 'macro'}
    missing = set(exports) - names
    if missing: raise LanguageError('导出未声明名称 '+', '.join(sorted(missing)), code='module_error').attach(getattr(export_node,'span',{}))
    return exports if include_macros else [name for name in exports if name not in macro_names]


def normalize_language_versions(files, versions=None, default=2):
    """Validate per-file syntax versions without executing or rewriting source."""
    if type(default) is not int or default not in (1,2): raise LanguageError('非法语言版本')
    if versions is None: versions={}
    if not isinstance(versions,dict) or set(versions)-set(files): raise LanguageError('语言版本必须对应包内模块')
    if any(type(value) is not int or value not in (1,2) for value in versions.values()): raise LanguageError('非法语言版本')
    return {name:versions.get(name,default) for name in files}


def project_language_versions(project):
    program=project.get('program') or {}
    return normalize_language_versions(project.get('files',{}),program.get('sourceVersions'),program.get('languageVersion',1 if program else 2))


def updated_language_versions(project, files, overrides=None):
    previous=project_language_versions(project)
    versions={name:previous.get(name,2) if text==project.get('files',{}).get(name) else 2 for name,text in files.items()}
    if overrides is not None:
        validated=normalize_language_versions(files,overrides)
        versions.update({name:validated[name] for name in overrides})
    return versions


class ProgramBundle:
    """Parse and link a closed bundle before evaluating any module effects."""
    def __init__(self, source, files=None, entry='main.chaos', *, language_versions=None, checkpoint=None,
                 compiler_cache=DEFAULT_COMPILER_CACHE):
        if checkpoint: checkpoint()
        self.files, self.entry = bundle_files(source, files, entry)
        self.language_versions = normalize_language_versions(self.files, language_versions)
        self.trees, self.exports, self.dependencies = {}, {}, {}
        self.macro_exports, self.imports = {}, {}
        self.compilation_units = {}
        visiting, visited = [], set()
        def visit(name):
            if checkpoint: checkpoint()
            if name in visiting: raise LanguageError('循环导入: '+' -> '.join(visiting+[name]), code='module_error')
            if name in visited: return
            if len(visiting) >= MAX_DEPTH: raise ResourceLimit('模块依赖深度超过 32')
            text = STDLIB.get(name) if name in STDLIB else self.files.get(name)
            if text is None: raise LanguageError(f'模块不存在: {name}', code='module_error')
            visiting.append(name)
            tree, unit = parse_unit(text, name, self.language_versions.get(name, 2),
                                    compiler_cache=compiler_cache, checkpoint=checkpoint)
            self.compilation_units[name] = unit
            self.trees[name] = tree
            exported = declarations(tree, include_macros=True)
            macro_names = {node[1] for node in tree[1] if node[0] == 'macro'}
            self.exports[name] = [key for key in exported if key not in macro_names]
            self.macro_exports[name] = [key for key in exported if key in macro_names]
            dependencies, aliases = {}, {}
            for node in tree[1]:
                if node[0] == 'import':
                    try: target = resolve_path(node[1], name); visit(target)
                    except LanguageError as error: raise error.attach(getattr(node,'span',{}))
                    dependencies[node[1]] = target
                    aliases[node[2]] = target
            self.dependencies[name] = dependencies
            self.imports[name] = aliases
            visiting.pop(); visited.add(name)
        visit(self.entry)
        # Invalid orphan modules are rejected as part of the same source snapshot.
        for name in self.files: visit(name)
        from .macros import expand_trees
        self.expanded_trees = expand_trees(self.trees, imports=self.imports, macro_exports=self.macro_exports, checkpoint=checkpoint)
        from .portable_optimizer import build_plan
        self._literal_plan = build_plan(self.expanded_trees)

    def summary(self):
        return {'valid': True, 'entry': self.entry, 'files': sorted(self.files),
                'dependencies': self.dependencies, 'exports': self.exports, 'macroExports': self.macro_exports}


def load_file_bundle(filename, root=None):
    """CLI-only explicit filesystem adapter; never called by HTTP or source imports."""
    file = Path(filename).expanduser().resolve()
    base = Path(root).expanduser().resolve() if root is not None else file.parent
    try: entry = file.relative_to(base).as_posix()
    except ValueError: raise LanguageError('入口文件不在 --root 程序包目录内', code='module_error') from None
    files, queue = {}, [entry]
    while queue:
        name = queue.pop()
        if name in files or name in STDLIB: continue
        target = (base/name).resolve()
        try: target.relative_to(base)
        except ValueError: raise LanguageError('模块符号链接越出程序包', code='module_error') from None
        if len(files) >= MAX_FILES: raise ResourceLimit(f'程序包最多 {MAX_FILES} 个文件')
        with target.open(encoding='utf-8') as stream: text = stream.read(100001)
        if len(text) > 100000: raise ResourceLimit('单文件超过 100 KB 限制')
        files[name] = text
        bundle_files(files[entry], files, entry)
        try: tree = Parser(text, name).program()
        except RecursionError: raise ResourceLimit('源码语法嵌套过深') from None
        for node in tree[1]:
            if node[0] == 'import': queue.append(resolve_path(node[1], name))
    return ProgramBundle(files[entry], files, entry)
