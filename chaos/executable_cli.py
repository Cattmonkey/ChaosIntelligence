"""Explicit, inert object compilation and source-free executable IR loading."""
import argparse
import json
from pathlib import Path
import sys

from .executable_image import compile_objects, link_objects, load_executable, mount_objects, MAX_EXECUTABLE_BYTES
from .framework import Framework
from .language import LanguageError, Parser, ResourceLimit
from .modules import ProgramBundle, load_file_bundle


def _library_file_bundle(filename, root, library_names):
    """Collect explicit client files; compiled modules never become source stubs."""
    from .modules import STDLIB, MAX_FILES, bundle_files, resolve_path
    file = Path(filename).expanduser().resolve()
    base = Path(root).expanduser().resolve() if root is not None else file.parent
    try:
        entry = file.relative_to(base).as_posix()
    except ValueError:
        raise LanguageError('入口文件不在 --root 程序包目录内', code='module_error') from None
    if entry in library_names:
        raise LanguageError('客户端入口与已编译库模块重名', code='executable_error')
    files, queue = {}, [entry]
    while queue:
        name = queue.pop()
        if name in files or name in STDLIB or name in library_names:
            continue
        target = (base / name).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            raise LanguageError('模块符号链接越出程序包', code='module_error') from None
        if len(files) >= MAX_FILES:
            raise ResourceLimit('程序包最多64个本地文件')
        with target.open(encoding='utf-8') as stream:
            text = stream.read(100001)
        if len(text) > 100000:
            raise ResourceLimit('单文件超过100 KB限制')
        files[name] = text
        bundle_files(files[entry], files, entry)
        try:
            tree = Parser(text, name).program()
        except RecursionError:
            raise ResourceLimit('源码语法嵌套过深') from None
        for node in tree[1]:
            if node[0] == 'import':
                queue.append(resolve_path(node[1], name))
    return files, entry


def _json_file(filename):
    limit = MAX_EXECUTABLE_BYTES
    if filename == '-':
        raw = getattr(sys.stdin, 'buffer', sys.stdin).read(limit + 1)
    else:
        with Path(filename).open('rb') as stream:
            raw = stream.read(limit + 1)
    if type(raw) is str:
        raw = raw.encode('utf-8')
    if len(raw) > limit:
        raise ValueError('Executable JSON exceeds byte limit')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON object key')
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError('Nonfinite JSON number')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def main(argv):
    parser = argparse.ArgumentParser(prog='chaos executable',
        description='Compile and link portable executable IR for the Chaos runtime')
    sub = parser.add_subparsers(dest='action', required=True)
    build = sub.add_parser('build', help='Compile a closed source bundle to an image on stdout')
    build.add_argument('file')
    build.add_argument('--root')
    build.add_argument('--objects', action='store_true', help='Emit module objects instead of a linked image')
    build.add_argument('--macro-interfaces', action='store_true',
                       help='Publish validated pure macro interfaces with source-free v3 module objects')
    build.add_argument('--library', action='append', metavar='OBJECTS_JSON',
                       help='Validated compiled module-object list; repeat for multiple libraries, without library source')
    link = sub.add_parser('link', help='Link JSON module-object lists; stdout only')
    link.add_argument('files', nargs='+')
    link.add_argument('--entry', default='main.chaos')
    mount = sub.add_parser('mount', help='Mount a source-free module-object list in a logical namespace')
    mount.add_argument('file')
    mount.add_argument('--prefix', required=True, help='Canonical relative non-std directory prefix')
    validate = sub.add_parser('validate', help='Validate all objects without executing')
    validate.add_argument('file')
    run = sub.add_parser('run', help='Load and execute with the bounded pure evaluator')
    run.add_argument('file')
    run.add_argument('--input-json', default='null')
    start = sub.add_parser('start', help='Run an image in the persistent host runtime with existing project grants')
    start.add_argument('file')
    start.add_argument('--db', default=':memory:')
    start.add_argument('--config')
    start.add_argument('--project', default='workspace')
    start.add_argument('--input-json', default='null')
    start.add_argument('--trace', action='store_true')
    start.add_argument('--stream', action='store_true')
    start.add_argument('--interactive', action='store_true')
    start.add_argument('--devices')
    start.add_argument('--robots')
    start.add_argument('--computers')
    args = parser.parse_args(argv)
    try:
        if args.action == 'start':
            if args.interactive:
                from .process_console import validate_interactive
                validate_interactive(args.file)
            image = _json_file(args.file)
            load_executable(image)
            value = json.loads(args.input_json, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite input')))
            from .settings import Settings
            from .device_serial import load_device_registry
            registry = load_device_registry(args.devices) if args.devices else None
            if args.robots:
                from .robot_config import load_robot_registry
                registry = load_robot_registry(args.robots, registry)
            if args.computers:
                from .computer_config import load_computer_registry
                registry = load_computer_registry(args.computers, registry)
            with Framework(args.db, settings=Settings(args.config) if args.config else Settings(), registry=registry) as app:
                project = app.get_project(args.project)
                run = app.start_executable(args.project, project['version'], image, value, trace=args.trace)
                if args.interactive:
                    from .process_console import attach_run
                    return attach_run(run)
                if args.stream:
                    from .process_console import stream_run
                    return stream_run(run)
                result = run.result()
        elif args.action == 'build':
            if args.library is None:
                bundle = ProgramBundle(sys.stdin.read(100001)) if args.file == '-' else load_file_bundle(args.file, args.root)
                objects = compile_objects(bundle.files[bundle.entry], bundle.files, bundle.entry,
                                          language_versions=bundle.language_versions, macro_interfaces=args.macro_interfaces)
                entry = bundle.entry
            else:
                if args.library.count('-') + int(args.file == '-') > 1:
                    raise ValueError('Standard input can only be read once')
                libraries = []
                for filename in args.library:
                    data = _json_file(filename)
                    if type(data) is not list:
                        raise ValueError('--library requires a JSON module-object list')
                    libraries.extend(data)
                from .separate_compilation import library_modules
                names = library_modules(libraries)
                if args.file == '-':
                    source = sys.stdin.read(100001)
                    entry = 'main.chaos'
                    objects = compile_objects(source, libraries=libraries, macro_interfaces=args.macro_interfaces)
                else:
                    files, entry = _library_file_bundle(args.file, args.root, names)
                    objects = compile_objects(files[entry], files, entry, libraries=libraries, macro_interfaces=args.macro_interfaces)
            result = objects if args.objects else link_objects(objects, entry=entry)
        elif args.action == 'mount':
            result = mount_objects(_json_file(args.file), args.prefix)
        elif args.action == 'link':
            if args.files.count('-') > 1:
                raise ValueError('Standard input can only be read once')
            objects = []
            for filename in args.files:
                data = _json_file(filename)
                if type(data) is not list:
                    raise ValueError('Link input must be a list of module objects')
                objects.extend(data)
            result = link_objects(objects, entry=args.entry)
        else:
            bundle = load_executable(_json_file(args.file))
            if args.action == 'validate':
                result = bundle.summary()
            else:
                result = Framework.evaluate(bundle, bindings={'input': json.loads(args.input_json)})
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')))
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, LanguageError, RuntimeError, RecursionError) as error:
        print(json.dumps({'error': error.record() if isinstance(error, LanguageError) else str(error)},
                         ensure_ascii=False), file=sys.stderr)
        return 1
