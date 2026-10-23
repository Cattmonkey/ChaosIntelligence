"""Pure composition of closed Program libraries using existing logical paths.

Mounts own disjoint directory namespaces. Sources are never rewritten; relative
imports and std imports are resolved by the original ProgramBundle compiler.
"""
from .language import LanguageError, ResourceLimit
from .modules import MAX_FILES, MAX_BYTES, bundle_files, normalize_language_versions, path_name


def _error(message):
    raise LanguageError(message, code='program_error')


def _shape(definition, checkpoint):
    """Bounded structural snapshot; the root may still refer to mounted files."""
    checkpoint()
    if (type(definition) is not dict or len(definition)!=4 or
            any(type(key) is not str for key in definition) or
            set(definition)!={'kind','entry','files','languageVersions'} or
            type(definition['kind']) is not str or definition['kind']!='program'):
        _error('Program definition requires kind/entry/files/languageVersions')
    entry,files,versions=definition['entry'],definition['files'],definition['languageVersions']
    if type(files) is not dict or type(versions) is not dict or type(entry) is not str:
        _error('Program entry/files/languageVersions have invalid types')
    if len(files)>MAX_FILES or len(versions)>MAX_FILES:raise ResourceLimit('Program exceeds 64 files')
    if (not files or any(type(name) is not str or type(source) is not str for name,source in files.items()) or
            any(type(name) is not str or type(version) is not int for name,version in versions.items())):
        _error('Program files require String sources and exact Int language versions')
    if entry not in files or set(versions)!=set(files):
        _error('Program entry and language versions must exactly correspond to files')
    # Reuse the established closed-bundle structural rules, without yet parsing
    # imports; malformed strings never reach normalization or host protocols.
    try:
        entry.encode('utf-8')
        for name in files:
            checkpoint();name.encode('utf-8')
    except UnicodeError:
        _error('Program paths require valid Unicode')
    copied,entry=bundle_files(files[entry],dict(files),entry)
    versions=normalize_language_versions(copied,dict(versions))
    checkpoint()
    return {'kind':'program','entry':entry,'files':copied,'languageVersions':versions}


def _mounts(libraries, checkpoint):
    if type(libraries) is not dict:_error('Program libraries require a Record of directory mounts')
    if len(libraries)>MAX_FILES:raise ResourceLimit('Program libraries exceed 64 mounts')
    mounts=[]
    for mount in libraries:
        checkpoint()
        if type(mount) is not str:_error('Program mount requires a String')
        if path_name(mount)!=mount or mount=='std' or mount.startswith('std/'):
            _error('Program mount requires a canonical non-std directory prefix')
        try:mount.encode('utf-8')
        except UnicodeError:_error('Program mount requires valid Unicode')
        mounts.append(mount)
    ordered=sorted(mounts)
    names=set(ordered)
    for mount in ordered:
        checkpoint()
        if any(mount[:index] in names for index,char in enumerate(mount) if char=='/'):
            _error('Program library mount namespaces overlap')
    return tuple(ordered)


def link_program(definition, library_definitions, *, checkpoint=None):
    """Return a closed four-field definition; no module initialization or I/O.

The first definition may import library paths that do not exist until linking.
Every library definition is independently closed and then relocated under its
Record key. Logical path identity remains exact and case sensitive. The input
objects, including source text and language versions, are never modified.
"""
    return _assemble(definition,library_definitions,checkpoint=checkpoint)


def _assemble(definition,libraries,*,checkpoint=None,decode=None):
    """Shared bounded assembler; native owners are checked before lazy decode."""
    from .program_values import validate_definition
    tick=checkpoint if checkpoint is not None else lambda:None
    tick()
    mounts=_mounts(libraries,tick)
    root=_shape(definition,tick)
    for mount in mounts:
        for name in root['files']:
            tick()
            if name==mount or name.startswith(mount+'/') or mount.startswith(name+'/'):
                _error('Program root file conflicts with a library mount namespace')
    snapshots=[]
    merged_files=dict(root['files']);merged_versions=dict(root['languageVersions'])
    total_bytes=sum(len(source.encode('utf-8')) for source in merged_files.values())
    # All structural/aggregate checks precede parsing any library. A cancelled,
    # invalid or oversized link cannot expose a partially composed Program.
    for mount in mounts:
        value=libraries[mount]
        library=_shape(value if decode is None else decode(value),tick)
        for name,source in library['files'].items():
            tick();target=mount+'/'+name
            if len(target)>240:raise LanguageError('Mounted module path exceeds 240 characters',code='module_error')
            if target in merged_files:_error('Program module path collision')
            if len(merged_files)>=MAX_FILES:raise ResourceLimit('Linked Program exceeds 64 files')
            total_bytes+=len(source.encode('utf-8'))
            if total_bytes>MAX_BYTES:raise ResourceLimit('Linked Program source exceeds 1 MB')
            merged_files[target]=source;merged_versions[target]=library['languageVersions'][name]
        snapshots.append(library)
    for name in merged_files:
        tick()
        if any(name[:index] in merged_files for index,char in enumerate(name) if char=='/'):
            _error('Program path is used as both a file and a directory')
    for library in snapshots:
        tick();validate_definition(library,checkpoint=tick)
    linked={'kind':'program','entry':root['entry'],'files':merged_files,'languageVersions':merged_versions}
    tick()
    return validate_definition(linked,checkpoint=tick)
