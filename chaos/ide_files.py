"""Atomic module-tree edits over the same versioned source bundle as all clients."""
import copy
import json
import posixpath
from .language import LanguageError, Parser, tokenize
from .modules import path_name, resolve_path, ProgramBundle, STDLIB, project_language_versions, updated_language_versions
from .store import Conflict


def canonical_path(value, directory=False):
    name = path_name(value)
    if name != value or name.startswith('std/') or name == 'std':
        raise ValueError('Path must be canonical and outside the standard library')
    if not directory and not name.endswith('.chaos'):
        raise ValueError('Project files require the .chaos extension')
    return name


def _rewrite_imports(source, old_file, new_file, moved):
    tree = Parser(source, old_file).program()
    tokens = tokenize(source)
    replacements = []
    by_position = {(t.line, t.column): i for i,t in enumerate(tokens)}
    for node in tree[1]:
        if node[0] != 'import': continue
        old_target = resolve_path(node[1], old_file)
        if old_target in STDLIB: continue
        target = moved.get(old_target, old_target)
        if target == old_target and old_file == new_file: continue
        specifier = posixpath.relpath(target, posixpath.dirname(new_file) or '.')
        i = by_position[(node.span['line'], node.span['column'])]
        token = tokens[i+1]
        assert token.kind == 'string'
        replacements.append((token.start, token.end, json.dumps(specifier, ensure_ascii=False)))
    for start,end,text in reversed(replacements): source = source[:start]+text+source[end:]
    return source


def transform_files(project, operation, path, *, new_path=None, source=None, directory=False):
    if type(directory) is not bool: raise ValueError('directory must be boolean')
    path = canonical_path(path, directory)
    result = copy.deepcopy(project)
    files = result['files']; entry = result['entry']; versions=project_language_versions(project)
    if operation == 'create':
        target = path+'/__init__.chaos' if directory else path
        if target in files or path in files or any(n.startswith(path+'/') for n in files):
            raise ValueError('Target already exists')
        if source is not None and not isinstance(source,str): raise ValueError('source must be text')
        files[target] = source if source is not None else 'export {};\n'; versions[target]=2
    elif operation in ('rename','delete'):
        selected = [n for n in files if n.startswith(path+'/')] if directory else ([path] if path in files else [])
        if not selected: raise ValueError('File or directory does not exist')
        if operation == 'delete':
            if entry in selected: raise ValueError('Cannot delete the entry file; select another entry first')
            for name in selected: del files[name]; versions.pop(name,None)
        else:
            target = canonical_path(new_path,directory)
            if target == path: return result
            if directory and target.startswith(path+'/'): raise ValueError('Cannot move a directory into itself')
            if target in files or any(n.startswith(target+'/') for n in files):
                raise ValueError('Target already exists')
            moved = {name:target+name[len(path):] for name in selected}
            destinations = [moved.get(n,n) for n in files]
            if len(destinations) != len(set(destinations)): raise ValueError('Target would overwrite an existing file')
            files = {moved.get(name,name):_rewrite_imports(text,name,moved.get(name,name),moved) for name,text in files.items()}
            result['files'] = files; entry = moved.get(entry,entry); result['entry'] = entry
            versions={moved.get(name,name):version for name,version in versions.items()}
    else:
        raise ValueError('Unknown file operation')
    # A module cannot also be a parent directory in the project tree.
    for name in files:
        parent = posixpath.dirname(name)
        while parent:
            if parent in files: raise ValueError('A file cannot be used as a directory')
            parent = posixpath.dirname(parent)
    result['source'] = files[entry]
    result['editFrom'] = 'source'
    result['sourceVersions']=versions
    ProgramBundle(result['source'],files,entry,language_versions=versions)
    return result


def edit_project_files(app, project_id, body):
    if not isinstance(body,dict): raise ValueError('Expected file operation object')
    if set(body)-{'version','operation','path','newPath','source','directory'}:
        raise ValueError('Unknown file operation fields')
    if type(body.get('version')) is not int: raise ValueError('version must be an integer')
    project = app.get_project(project_id)
    if project['version'] != body['version']: raise Conflict('Project changed; reload before editing files')
    candidate = transform_files(project,body.get('operation'),body.get('path'),new_path=body.get('newPath'),source=body.get('source'),directory=body.get('directory',False))
    if candidate['files'] == project['files'] and candidate['entry'] == project['entry']: return project
    return app.save_project(candidate,from_view='source')
