"""Pure, bounded editor snapshots shared by assistant start and apply.

Source remains in the existing turn row; only small provenance enters the job.
Positions refer to the frozen input, never to later assistant draft mutations.
"""
import bisect
import hashlib
import json

from .extensions import json_copy
from .ide_language import SourceMap, analyze
from .language import LanguageError
from .modules import path_name, normalize_language_versions


def module_path(value, directory=False):
    try: canonical = path_name(value)
    except LanguageError as exc: raise ValueError('Invalid project-relative path') from exc
    if canonical != value or any(part.startswith('.') for part in canonical.split('/')):
        raise ValueError('Use a canonical project-relative path without hidden segments')
    if canonical.startswith('std/') or (not directory and not canonical.endswith('.chaos')):
        raise ValueError('Project modules must use .chaos paths outside std/')
    return canonical


def source_limits(files):
    if not isinstance(files, dict) or not 1 <= len(files) <= 64:
        raise ValueError('A project needs 1–64 modules')
    size = 0
    for name, source in files.items():
        module_path(name)
        if not isinstance(source, str) or len(source.encode('utf-8')) > 100000:
            raise ValueError('Each module must contain at most 100000 UTF-8 bytes')
        size += len(source.encode('utf-8'))
    if size > 1000000: raise ValueError('Project source exceeds 1 MB')


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def editor_snapshot(value, project_id, version):
    """Validate and detach a complete input snapshot without parsing/execution."""
    fields = {'files', 'entry', 'languageVersions', 'activeFile', 'selection'}
    if type(value) is not dict or set(value) != fields:
        raise ValueError('editorContext requires only files, entry, languageVersions, activeFile and selection')
    source_limits(value['files'])
    files = dict(value['files'])
    entry, active = module_path(value['entry']), module_path(value['activeFile'])
    if entry not in files or active not in files:
        raise ValueError('Editor entry and activeFile must exist in files')
    versions = value['languageVersions']
    if type(versions) is not dict or set(versions) != set(files):
        raise ValueError('Editor languageVersions must cover exactly its files')
    versions = normalize_language_versions(files, versions)
    selection = value['selection']
    if type(selection) is not dict or set(selection) != {'anchor', 'head'}:
        raise ValueError('Editor selection requires anchor and head UTF-16 offsets')
    units = SourceMap(files[active]).units
    for offset in selection.values():
        if type(offset) is not int or offset < 0 or offset > units[-1] or offset not in units:
            raise ValueError('Editor selection must use exact UTF-16 character boundaries')
    file_hashes = {name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in sorted(files.items())}
    metadata = {'schemaVersion': 1, 'baseVersion': version,
                'draftHash': _hash({'projectId': project_id, 'baseVersion': version,
                                    'files': files, 'entry': entry, 'languageVersions': versions}),
                'activeFile': active, 'selection': dict(selection), 'sourceHash': file_hashes[active],
                'entry': entry, 'files': sorted(files), 'languageVersions': versions, 'fileHashes': file_hashes}
    # Keep the generic background task limit; full source never enters this tree.
    return files, json_copy(metadata)


def semantic_context(files, metadata):
    """Derive a bounded, explicitly partial summary from the installed analyzer."""
    active = metadata['activeFile']; source = files[active]
    selection = metadata['selection']; units = SourceMap(source).units
    start, end = sorted((selection['anchor'], selection['head']))
    selected = source[bisect.bisect_left(units, start):bisect.bisect_left(units, end)]
    result = analyze(source, file=active, files=files, entry=metadata['entry'],
                     position=selection['head'], language_versions=metadata['languageVersions'])
    truncated = [False]; remaining = [40000]
    def bounded(value, depth=0):
        if isinstance(value, str):
            limit = min(1000, remaining[0])
            if len(value) > limit: truncated[0] = True
            remaining[0] -= min(len(value), limit)
            return value[:limit]
        if isinstance(value, list):
            if len(value) > 40: truncated[0] = True
            return [bounded(item, depth+1) for item in value[:40]]
        if isinstance(value, dict):
            if depth >= 6: truncated[0] = True; return {}
            return {key: bounded(item, depth+1) for key, item in value.items()}
        return value
    summary = bounded({key: result[key] for key in ('complete', 'symbols', 'diagnostics', 'hover', 'signatureHelp', 'definition', 'outline') if key in result})
    summary['truncated'] = truncated[0]
    return json_copy({**metadata, 'selectedText': selected[:4000], 'selectionTruncated': len(selected)>4000,
                      'analysis': summary}, max_chars=200000)
