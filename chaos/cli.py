"""Standalone Chaos CLI, formatter, IR export, execution trace and interactive REPL."""
import argparse
import json
from pathlib import Path
import sys
from .framework import Framework, RunFailed
from .language import LanguageError, serializable
from .modules import ProgramBundle, load_file_bundle
from .program import parse_program, format_program
from .session import Session
from .server import main as serve_main
from .version import __version__


def artifact_main(argv):
    """Explicit build/validate/run over the one portable artifact implementation."""
    from .compilation_artifact import compile_artifact, load_artifact, MAX_ARTIFACT_BYTES
    parser=argparse.ArgumentParser(prog='chaos artifact',description='Build or revalidate portable flow templates; run is explicit pure evaluation')
    sub=parser.add_subparsers(dest='action',required=True)
    build=sub.add_parser('build',help='Build artifact JSON from a closed source bundle; stdout only')
    build.add_argument('file',help='UTF-8 .chaos entry; - reads standard input')
    build.add_argument('--root',help='Directory containing the entry and relative imports')
    validate=sub.add_parser('validate',help='Revalidate artifact JSON and print the original bundle summary')
    validate.add_argument('file',help='UTF-8 artifact JSON; - reads standard input')
    run=sub.add_parser('run',help='Revalidate then run with the original pure Evaluator; no model/tool privileges')
    run.add_argument('file',help='UTF-8 artifact JSON; - reads standard input')
    run.add_argument('--input-json',default='null',help='JSON value bound to input')
    args=parser.parse_args(argv)
    try:
        if args.action=='build':
            bundle=ProgramBundle(sys.stdin.read(100001)) if args.file=='-' else load_file_bundle(args.file,args.root)
            result=compile_artifact(bundle.files[bundle.entry],bundle.files,bundle.entry,language_versions=bundle.language_versions)
        else:
            if args.file=='-':raw=getattr(sys.stdin,'buffer',sys.stdin).read(MAX_ARTIFACT_BYTES+1)
            else:
                with Path(args.file).open('rb')as stream:raw=stream.read(MAX_ARTIFACT_BYTES+1)
            if type(raw)is str:raw=raw.encode('utf-8')
            if len(raw)>MAX_ARTIFACT_BYTES:raise ValueError('Artifact JSON exceeds byte limit')
            def pairs(items):
                result={}
                for key,value in items:
                    if key in result:raise ValueError('Duplicate JSON object key')
                    result[key]=value
                return result
            def nonfinite(value):raise ValueError('Nonfinite JSON number')
            data=json.loads(raw,object_pairs_hook=pairs,parse_constant=nonfinite)
            bundle=load_artifact(data)
            if args.action=='validate':result=bundle.summary()
            else:
                value=json.loads(args.input_json,parse_constant=nonfinite)
                result=Framework.evaluate(bundle,bindings={'input':value})
        print(json.dumps(result,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')))
        return 0
    except (OSError,ValueError,LanguageError,RunFailed,RuntimeError,RecursionError)as error:
        value=error.record()if isinstance(error,LanguageError)else str(error)
        print(json.dumps({'error':value},ensure_ascii=False),file=sys.stderr);return 1


def compile_main(argv):
    """Compile one closed source snapshot; report only, no filesystem writes."""
    from .compiler import compile_bundle
    parser=argparse.ArgumentParser(prog='chaos compile',description='Compile and inspect module identities, cache work and conservative effects without running the program')
    parser.add_argument('file',help='UTF-8 .chaos entry; - reads standard input')
    parser.add_argument('--root',help='Directory containing the entry and relative imports')
    args=parser.parse_args(argv)
    try:
        bundle=ProgramBundle(sys.stdin.read(100001)) if args.file=='-' else load_file_bundle(args.file,args.root)
        print(json.dumps(compile_bundle(bundle),ensure_ascii=False,allow_nan=False,indent=2))
        return 0
    except (OSError,ValueError,LanguageError,RecursionError) as error:
        print(json.dumps({'error':error.record() if isinstance(error,LanguageError) else str(error)},ensure_ascii=False),file=sys.stderr)
        return 1


def symbols_main(argv):
    """Pure refactor of an explicitly supplied portable Program JSON; stdout only."""
    from .symbols import references, rename
    parser=argparse.ArgumentParser(prog='chaos symbols',description='Query or rename source symbols in a closed Program JSON without writing files')
    parser.add_argument('action',choices=('references','rename'))
    parser.add_argument('bundle',help='Program JSON file; - reads standard input')
    parser.add_argument('--file',help='Selected module; defaults to program entry')
    parser.add_argument('--position',type=int,required=True,help='Absolute UTF-16 source position')
    parser.add_argument('--new-name',help='New identifier for rename')
    parser.add_argument('--exclude-declaration',action='store_true')
    args=parser.parse_args(argv)
    if args.action=='rename' and args.new_name is None:parser.error('--new-name is required for rename')
    if args.action=='references' and args.new_name is not None:parser.error('--new-name only applies to rename')
    try:
        if args.bundle=='-':text=sys.stdin.read(1500001)
        else:
            with Path(args.bundle).open(encoding='utf-8') as stream:text=stream.read(1500001)
        if len(text)>1500000:raise ValueError('Program JSON exceeds 1500000 characters')
        def pairs(items):
            result={}
            for key,value in items:
                if key in result:raise ValueError('Duplicate JSON object key')
                result[key]=value
            return result
        value=json.loads(text,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite input')))
        if type(value) is not dict or set(value)!={'kind','entry','files','languageVersions'} or value['kind']!='program':
            raise ValueError('Expected a portable Program definition')
        files=value['files'];entry=value['entry'];file=args.file or entry
        if type(files) is not dict or type(file) is not str or file not in files or type(value['languageVersions']) is not dict or set(value['languageVersions'])!=set(files):
            raise ValueError('Program files and languageVersions must match; selected file must exist')
        options={'file':file,'files':files,'entry':entry,'position':args.position,'language_versions':value['languageVersions']}
        result=(references(files[file],include_declaration=not args.exclude_declaration,**options) if args.action=='references'
                else rename(files[file],new_name=args.new_name,**options))
        print(json.dumps(result,ensure_ascii=False,allow_nan=False));return 0
    except (OSError,ValueError,LanguageError,RecursionError) as error:
        value=error.record() if isinstance(error,LanguageError) else str(error)
        print(json.dumps({'error':value},ensure_ascii=False),file=sys.stderr);return 1


def repl(file=None, root=None, config=None):
    bundle=load_file_bundle(file,root) if file else None
    session=Session(files=bundle.files if bundle else {},entry=bundle.entry if bundle else 'repl.chaos')
    if bundle:session.evaluate(bundle.files[bundle.entry])
    def emit_error(error):
        # Values stay native inside the language; only terminal output is JSON.
        try: record = serializable(error.record())
        except LanguageError as projection_error:
            # Do not retry the oversized/unsupported original thrown value.
            record = serializable(projection_error.record())
        print(json.dumps({'error':record},ensure_ascii=False),file=sys.stderr)
    interactive=sys.stdin.isatty();pending=[]
    if interactive:print('Chaos '+__version__+' — .help / .ask QUESTION / .quit / .reset / .env; blank line submits a multiline block')
    while True:
        try:line=input('... ' if pending else 'chaos> ') if interactive else sys.stdin.readline()
        except EOFError:break
        if not interactive and line=='':break
        line=line.rstrip('\n')
        if not pending and line=='.quit':break
        if not pending and line=='.help':
            print('Pure language REPL: .env / .reset / .quit. .ask QUESTION explicitly uses the configured model with installed getting-started documentation; bindings are not sent.')
            continue
        if not pending and line.startswith('.ask '):
            from .command_intelligence import ask_main
            question = line[5:].strip()
            if question == '-':
                print(json.dumps({'error':'.ask requires question text, not stdin'},ensure_ascii=False),file=sys.stderr)
                continue
            ask_main(['--role','assistant','--topic','getting-started'] + (['--config',config] if config else []) + ['--', question])
            continue
        if not pending and line=='.reset':session=Session(files=bundle.files if bundle else {},entry=bundle.entry if bundle else 'repl.chaos');continue
        if not pending and line=='.env':
            try: print(json.dumps(session.bindings(),ensure_ascii=False))
            except LanguageError as error: emit_error(error)
            continue
        if not line and not pending:continue
        pending.append(line);source='\n'.join(pending)
        try:
            # Braces, parentheses and lists are resolved by the real parser, not delimiter counting.
            ProgramBundle(source,session.files,session.entry)
        except LanguageError as error:
            if line and error.code=='syntax_error' and ("<eof>" in error.message or '缺少' in error.message or error.line==len(pending) and error.column>=len(line)):
                continue
            emit_error(error);pending=[];continue
        try:print(json.dumps(serializable(session.evaluate(source)),ensure_ascii=False))
        except LanguageError as error:emit_error(error)
        pending=[]
    if pending:
        print(json.dumps({'error':'未完成的 REPL 输入'},ensure_ascii=False),file=sys.stderr)
        return 1
    return 0


def workspace_main(argv):
    from .workspace import Workspace
    from .client import Client, TransportError
    import time
    parser=argparse.ArgumentParser(prog='chaos workspace',description='Live source workspace connected to the same service as the visual IDE')
    parser.add_argument('action',choices=['checkout','sync','run','status'])
    parser.add_argument('directory')
    parser.add_argument('--server',default=None)
    parser.add_argument('--project',default='workspace')
    parser.add_argument('--watch',action='store_true')
    parser.add_argument('--interval',type=float,default=.5)
    parser.add_argument('--input-json',default='null')
    args=parser.parse_args(argv)
    try:
        if not .1<=args.interval<=60:raise ValueError('interval must be 0.1 to 60 seconds')
        client=Client(args.server)if args.server else None
        if args.action=='checkout':
            workspace=Workspace.checkout(client or Client(),args.project,args.directory)
            print(json.dumps(workspace.status()));return 0
        workspace=Workspace.open(args.directory,client)
        if args.action=='status':print(json.dumps(workspace.status()));return 0
        if args.action=='run':
            value=json.loads(args.input_json,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite input')))
            print(json.dumps(workspace.run(value),ensure_ascii=False));return 0
        last_error=None
        while True:
            try:
                result=workspace.sync()
                if result['status']!='unchanged' or not args.watch:print(json.dumps(result),flush=True)
                last_error=None
            except (LanguageError,ValueError,TransportError)as error:
                if not args.watch:raise
                if str(error)!=last_error:print(json.dumps({'waiting':str(error)}),file=sys.stderr,flush=True)
                last_error=str(error)
            if not args.watch:return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:return 0
    except (OSError,ValueError,LanguageError,RuntimeError,KeyError)as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False),file=sys.stderr);return 1


def run_guide_main(argv):
    """Append copied JSON to one active language run; never restart its effects."""
    from .client import Client
    parser=argparse.ArgumentParser(prog='chaos run-guide',description='Queue guidance for an active language run at its explicit receive boundary')
    parser.add_argument('run_id');parser.add_argument('--input',required=True,help='JSON input; use {"text":"..."} for natural-language guidance')
    parser.add_argument('--message-id');parser.add_argument('--server',default='http://127.0.0.1:8765')
    targets=parser.add_mutually_exclusive_group()
    targets.add_argument('--bot-scope',help='Actual active private Bot invocation scope ID; omitted preserves the run guidance mailbox')
    targets.add_argument('--process-role',help='Actual persistent Process role ID from its host registry')
    args=parser.parse_args(argv)
    try:
        value=json.loads(args.input,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite input')))
        extra = {} if args.bot_scope is None else {'target': {'kind': 'botScope', 'scopeId': args.bot_scope}}
        if args.process_role is not None:extra={'target':{'kind':'processRole','roleId':args.process_role}}
        result=Client(args.server).guide(args.run_id,value,message_id=args.message_id,**extra)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False));return 0
    except Exception as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False),file=sys.stderr);return 1


def task_main(argv):
    from .client import Client
    p=argparse.ArgumentParser(prog='chaos task',description='Headless background agent tasks sharing the web control plane')
    p.add_argument('action',choices=['start','list','get','cancel','resume','events','models','settings'])
    p.add_argument('value',nargs='?');p.add_argument('--server',default='http://127.0.0.1:8765')
    p.add_argument('--role',default='executor');p.add_argument('--agent',default='background');p.add_argument('--iterations',type=int,default=1);p.add_argument('--interval',type=float,default=15);p.add_argument('--after',type=int,default=0)
    a=p.parse_args(argv);client=Client(a.server)
    try:
        if a.action=='start':result=client.start_task(a.value or sys.stdin.read(20001),agent_id=a.agent,role=a.role,iterations=a.iterations,interval=a.interval)
        elif a.action=='list':result=client.tasks()
        elif a.action=='settings':result=client.settings()
        elif a.action=='models':result=client.discover_models(a.value or 'text')
        elif a.action=='events':result=client.task_events(after=a.after,task_id=a.value)
        else:
            if not a.value:p.error('task id is required')
            result={'get':client.task,'cancel':client.cancel_task,'resume':client.resume_task}[a.action](a.value)
        print(json.dumps(result,ensure_ascii=False));return 0
    except Exception as e:
        print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 1


def debug_main(argv):
    """Control a real source interpreter through the same persistent run service."""
    from .client import Client
    import time
    parser = argparse.ArgumentParser(prog='chaos debug', description='Source breakpoints and paused stack inspection; no expression evaluation')
    parser.add_argument('action', choices=['start', 'inspect', 'continue', 'pause', 'step_into', 'step_over', 'step_out', 'breakpoints', 'cancel', 'shell', 'restart'])
    parser.add_argument('id', help='Project id for start; run id for other actions')
    parser.add_argument('--server', default='http://127.0.0.1:8765')
    parser.add_argument('--version', type=int, help='Required saved project version for start')
    parser.add_argument('--input-json', default='null')
    parser.add_argument('--breakpoint', action='append', default=[], metavar='FILE:LINE')
    parser.add_argument('--no-entry', action='store_true', help='Run until a breakpoint instead of pausing on entry')
    parser.add_argument('--pause-timeout', type=float, default=300)
    parser.add_argument('--pause-id', type=int, help='Required current pauseId for resume/step/restart commands')
    parser.add_argument('--pause-on-condition',action='store_true',help='Pause at unrecovered error_condition')
    parser.add_argument('--restart-id',help='Exact available restart id from inspect')
    parser.add_argument('--args-json',default='[]',help='Strict JSON List of restart positional arguments')
    parser.add_argument('--kwargs-json',default='{}',help='Strict JSON Record of restart keyword arguments')
    args = parser.parse_args(argv)
    client = Client(args.server)
    def points():
        result = []
        for item in args.breakpoint:
            file, separator, line = item.rpartition(':')
            if not separator or not file: raise ValueError('Breakpoint must be FILE:LINE')
            result.append({'file': file, 'line': int(line)})
        return result
    def output(value): print(json.dumps(value, ensure_ascii=False), flush=True)
    try:
        if args.action == 'start':
            if args.version is None: parser.error('--version is required for start')
            value = json.loads(args.input_json, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite input')))
            handle = client.start_debug(args.id, args.version, value, breakpoints=points(), pause_on_entry=not args.no_entry, pause_timeout=args.pause_timeout,pause_on_condition=args.pause_on_condition)
            output(handle.get()); return 0
        if args.action == 'inspect': output(client.debugger(args.id)); return 0
        if args.action != 'shell':
            extra={}
            if args.action=='restart':
                reject=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite restart input'))
                extra={'restart_id':args.restart_id,'args':json.loads(args.args_json,parse_constant=reject),'kwargs':json.loads(args.kwargs_json,parse_constant=reject)}
            output(client.debug_command(args.id, args.action, pause_id=args.pause_id, breakpoints=points() if args.action == 'breakpoints' else None,**extra)); return 0
        print('Commands: continue/c, into/i, over/n, out/o, pause/p, stack/s, break FILE:LINE, clear, restart ID ARGS_JSON KWARGS_JSON, cancel/q. EOF cancels this run.', file=sys.stderr)
        aliases = {'c':'continue', 'i':'step_into', 'n':'step_over', 'o':'step_out', 'p':'pause', 's':'stack', 'q':'cancel'}
        while True:
            state = client.debugger(args.id); output(state)
            if state['status'] not in ('running', 'paused'): return 0
            try: line = input('debug> ').strip()
            except (EOFError, KeyboardInterrupt): client.debug_command(args.id, 'cancel'); return 0
            command, _, value = line.partition(' '); command = aliases.get(command, command)
            if command in ('', 'stack'): continue
            if command in ('break', 'clear'):
                existing = state['breakpoints'] if command == 'break' else []
                if command == 'break':
                    file, _, number = value.rpartition(':'); existing = existing + [{'file':file,'line':int(number)}]
                output(client.debug_command(args.id, 'breakpoints', breakpoints=existing)); continue
            try:
                extra={}
                if command=='restart':
                    restart_id,separator,payload=value.partition(' ')
                    if not separator:raise ValueError('restart requires ID ARGS_JSON KWARGS_JSON')
                    reject=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite restart input'))
                    decoder=json.JSONDecoder(parse_constant=reject)
                    parsed_args,end=decoder.raw_decode(payload.lstrip())
                    tail=payload.lstrip()[end:].lstrip()
                    parsed_kwargs,end=decoder.raw_decode(tail)
                    if tail[end:].strip():raise ValueError('Unexpected text after restart JSON data')
                    extra={'restart_id':restart_id,'args':parsed_args,'kwargs':parsed_kwargs}
                output(client.debug_command(args.id, command, pause_id=state['pauseId'],**extra))
                if command == 'cancel': return 0
                if command in ('continue', 'step_into', 'step_over', 'step_out','restart'):
                    # Poll briefly for the next pause; the prompt remains available during long effects.
                    deadline = time.monotonic() + 1
                    while time.monotonic() < deadline and client.debugger(args.id)['status'] == 'running': time.sleep(.05)
            except Exception as error: output({'error':str(error)})
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(json.dumps({'error':str(error)}, ensure_ascii=False), file=sys.stderr); return 1


def _process_role_target(client, project_id, key, role_key):
    registry=client.process_guidance(project_id,key)
    for role in registry['roles']:
        if role['roleKey']==role_key:
            return dict(role['target'])
    raise ValueError('Declared Process role not found; no guidance was sent')


def process_main(argv):
    """Manage native language Processes on the same host as the IDE."""
    from .client import Client
    parser=argparse.ArgumentParser(prog='chaos process',description='Persistent Chaos function turns; the server owns execution and timing')
    parser.add_argument('action',choices=['list','get','message','create','send','guide','events','watch','attach','pause','resume','stop','configure','reconcile'])
    parser.add_argument('project');parser.add_argument('key',nargs='?')
    parser.add_argument('--server',default='http://127.0.0.1:8765')
    parser.add_argument('--version',type=int);parser.add_argument('--revision',type=int)
    parser.add_argument('--definition-file',help='UTF-8 JSON Process definition')
    parser.add_argument('--state-json',default='null')
    guide_input=parser.add_mutually_exclusive_group()
    guide_input.add_argument('--input-json',default='null')
    guide_input.add_argument('--text',help='Natural-language Process guidance text')
    parser.add_argument('--message-id');parser.add_argument('--evidence',default='')
    parser.add_argument('--role-key',help='Exact declared Process role key; resolved once to its current persistent identity')
    parser.add_argument('--resume-after-reconcile',action='store_true')
    parser.add_argument('--after',default='');parser.add_argument('--limit',type=int,default=100)
    parser.add_argument('--timeout',type=float,default=None,help='Local observation limit in seconds; does not stop the Process')
    args=parser.parse_args(argv);client=Client(args.server)
    def parse(text):
        if len(text)>100000:raise ValueError('JSON input exceeds100000 characters')
        return json.loads(text,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    try:
        if args.action!='list' and not args.key:parser.error('key is required')
        if args.text is not None and args.action!='guide':parser.error('--text is only valid for guide')
        if args.role_key is not None and args.action not in ('guide','attach'):parser.error('--role-key is only valid for guide or attach')
        target=None if args.role_key is None else _process_role_target(client,args.project,args.key,args.role_key)
        if args.action in ('events','watch','attach'):
            after=int(args.after or '0')
            if after<0:raise ValueError('Event cursor must be nonnegative')
            if args.action=='events':
                result=client.events(args.project,process_key=args.key,after=after,limit=args.limit)
            elif args.action=='watch':
                for event in client.watch_process(args.project,args.key,after=after,timeout=args.timeout):
                    print(json.dumps(event,ensure_ascii=False,allow_nan=False),flush=True)
                return 0
            else:
                from .process_console import attach
                extra={} if target is None else {'target':target}
                return attach(client,args.project,args.key,after=after,timeout=args.timeout,**extra)
            print(json.dumps(result,ensure_ascii=False,allow_nan=False));return 0
        if args.action=='message' and not args.message_id:parser.error('--message-id is required for message')
        if args.action=='create' and args.version is None:parser.error('--version is required')
        if args.action in ('pause','resume','stop','configure','reconcile') and args.revision is None:parser.error('--revision is required')
        if args.action in ('create','configure') and not args.definition_file:parser.error('--definition-file is required')
        if args.action=='list':result=client.processes(args.project,after=args.after,limit=args.limit)
        elif args.action=='get':result=client.get_process(args.project,args.key)
        elif args.action=='message':result=client.get_process_message(args.project,args.key,args.message_id)
        elif args.action=='create':result=client.create_process(args.project,args.version,args.key,parse(Path(args.definition_file).read_text(encoding='utf-8')),state=parse(args.state_json))
        elif args.action=='send':result=client.send_process(args.project,args.key,parse(args.input_json),message_id=args.message_id)
        elif args.action=='guide':
            extra={} if target is None else {'target':target}
            result=client.guide_process(args.project,args.key,{'text':args.text} if args.text is not None else parse(args.input_json),message_id=args.message_id,**extra)
        elif args.action=='configure':result=client.configure_process(args.project,args.key,args.revision,parse(Path(args.definition_file).read_text(encoding='utf-8')))
        elif args.action=='reconcile':result=client.reconcile_process(args.project,args.key,args.revision,parse(args.state_json),evidence=args.evidence,resume=args.resume_after_reconcile)
        else:result=client.control_process(args.project,args.key,args.revision,args.action)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False));return 0
    except KeyboardInterrupt:
        return 130
    except (OSError,ValueError,RuntimeError,KeyError) as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False),file=sys.stderr);return 1


def computer_main(argv):
    from .client import Client
    parser=argparse.ArgumentParser(prog='chaos computer')
    parser.add_argument('action',choices=('schema','history','record','image','validate','sessions','session'))
    parser.add_argument('project',nargs='?');parser.add_argument('record',nargs='?')
    parser.add_argument('--server',default='http://127.0.0.1:8765');parser.add_argument('--run-id')
    parser.add_argument('--after',type=int,default=0);parser.add_argument('--limit',type=int,default=50)
    parser.add_argument('--variant',choices=('preview','model'),default='preview');parser.add_argument('--spec-json')
    parser.add_argument('--registered',action='store_true',help='Validate registered Computer actions; does not grant authority')
    args=parser.parse_args(argv);client=Client(args.server)
    if args.action not in ('schema','validate') and not args.project:parser.error('project is required')
    if args.action in ('record','image','session') and not args.record:parser.error('record or session key is required')
    if args.action=='validate' and args.spec_json is None:parser.error('--spec-json is required')
    try:
        if args.action=='schema':result=client.computer_schema()
        elif args.action=='validate':result=client.validate_computer_action(json.loads(args.spec_json),registered=args.registered)
        elif args.action=='sessions':result=client.computer_sessions(args.project,after=args.after,limit=args.limit)
        elif args.action=='session':result=client.computer_session(args.project,args.record)
        elif args.action=='history':result=client.computer_history(args.project,run_id=args.run_id,after=args.after,limit=args.limit)
        elif args.action=='record':result=client.computer_record(args.project,args.record)
        else:result=client.computer_image(args.project,args.record,variant=args.variant)
        print(json.dumps(result,ensure_ascii=False));return 0
    except Exception as exc:print(str(exc),file=sys.stderr);return 1


def program_main(argv):
    """Read immutable program history from the same runtime as the IDE."""
    from .client import Client
    parser=argparse.ArgumentParser(prog='chaos program',description='Read exact saved Program versions; adoption uses the ordinary tested Program API')
    parser.add_argument('action',choices=['history','revision','snapshot'])
    parser.add_argument('project');parser.add_argument('version',type=int,nargs='?')
    parser.add_argument('--server',default='http://127.0.0.1:8765')
    parser.add_argument('--after',type=int,default=0);parser.add_argument('--limit',type=int,default=50)
    args=parser.parse_args(argv);client=Client(args.server)
    if args.action=='revision' and args.version is None:parser.error('revision requires a saved version')
    if args.action!='revision' and args.version is not None:parser.error('version is only accepted for revision')
    try:
        if args.action=='history':result=client.program_history(args.project,after=args.after,limit=args.limit)
        elif args.action=='revision':result=client.program_revision(args.project,args.version)
        else:result=client.program_snapshot(args.project)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False));return 0
    except (OSError,ValueError,RuntimeError,KeyError)as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False),file=sys.stderr);return 1


def session_main(argv):
    """Native language AgentSession client; execution belongs to the shared host."""
    from .client import Client
    parser=argparse.ArgumentParser(prog='chaos session')
    parser.add_argument('action',choices=['list','get','history','open','send','fork','compact','close','reconcile'])
    parser.add_argument('project');parser.add_argument('key',nargs='?')
    parser.add_argument('--server',default='http://127.0.0.1:8765')
    parser.add_argument('--version',type=int);parser.add_argument('--revision',type=int)
    parser.add_argument('--agent-file');parser.add_argument('--context-limit',type=int,default=100000)
    parser.add_argument('--input-json',default='null');parser.add_argument('--message-id')
    parser.add_argument('--new-key');parser.add_argument('--keep-turns',type=int);parser.add_argument('--evidence',default='')
    parser.add_argument('--after',default='');parser.add_argument('--limit',type=int,default=100)
    args=parser.parse_args(argv);client=Client(args.server)
    def parse(text):
        if len(text)>200000:raise ValueError('JSON input exceeds200000 characters')
        return json.loads(text,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    def agent():return parse(Path(args.agent_file).read_text(encoding='utf-8')) if args.agent_file else None
    try:
        if args.action!='list' and not args.key:parser.error('key is required')
        if args.action in ('open','send') and args.version is None:parser.error('--version is required')
        if args.action in ('send','fork','compact','close','reconcile') and args.revision is None:parser.error('--revision is required')
        if args.action=='open' and not args.agent_file:parser.error('--agent-file is required')
        if args.action=='fork' and not args.new_key:parser.error('--new-key is required')
        if args.action=='compact' and args.keep_turns is None:parser.error('--keep-turns is required')
        if args.action=='list':result=client.agent_sessions(args.project,after=args.after,limit=args.limit)
        elif args.action=='get':result=client.agent_session(args.project,args.key)
        elif args.action=='history':result=client.agent_session_history(args.project,args.key,after=int(args.after or 0),limit=args.limit)
        elif args.action=='open':result=client.open_agent_session(args.project,args.version,args.key,agent(),context_limit=args.context_limit)
        elif args.action=='send':result=client.start_session_turn(args.project,args.version,args.key,args.revision,parse(args.input_json),message_id=args.message_id).get()
        elif args.action=='fork':result=client.fork_agent_session(args.project,args.key,args.revision,args.new_key,agent=agent())
        elif args.action=='compact':result=client.compact_agent_session(args.project,args.key,args.revision,args.keep_turns)
        elif args.action=='close':result=client.close_agent_session(args.project,args.key,args.revision)
        else:result=client.reconcile_agent_session(args.project,args.key,args.revision,args.evidence)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False));return 0
    except (OSError,ValueError,RuntimeError,KeyError)as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False),file=sys.stderr);return 1


def lsp_main(argv):
    """Run the inert language service over standard LSP binary stdio."""
    parser = argparse.ArgumentParser(prog='chaos lsp', description='Chaos language server (stdio)')
    parser.add_argument('--stdio', action='store_true', help='Use standard input/output (the default transport)')
    parser.add_argument('--root', help='Constrain analysis to this local package directory')
    parser.add_argument('--entry', help='Package entry path relative to its root')
    parser.add_argument('--language-version', type=int, choices=(1, 2), default=2,
                        help='Source dialect for this language-server session')
    args = parser.parse_args(argv)
    from .lsp import run_stdio
    return run_stdio(root=args.root, entry=args.entry, language_version=args.language_version)


def ui_main(argv):
    """Compile, validate and export the same component data as the native APIs."""
    parser = argparse.ArgumentParser(prog='chaos ui', description='Local OpenUI artifact tools')
    parser.add_argument('action', choices=('schema', 'compile', 'validate', 'export', 'runtime'))
    parser.add_argument('file', nargs='?', help='UTF-8 OpenUI source; - reads stdin')
    parser.add_argument('--kind', choices=('page', 'report', 'slides'), default='page')
    parser.add_argument('--title', default='')
    parser.add_argument('--input-format', choices=('source', 'definition'), default='source')
    parser.add_argument('--format', choices=('html', 'json', 'openui', 'chaos'), default='html')
    parser.add_argument('--server',default='http://127.0.0.1:8765')
    parser.add_argument('--run-id')
    parser.add_argument('--requirements', help='JSON array of structural requirements')
    parser.add_argument('--output', help='Write the result to this UTF-8 file')
    args = parser.parse_args(argv)
    try:
        if args.action == 'runtime':
            if not args.file:parser.error('runtime requires a project id')
            from .client import Client
            result=Client(args.server).runtime_view(args.file,run_id=args.run_id)
        elif args.action == 'schema':
            result = Framework.ui_schema()
        else:
            if not args.file: parser.error('file is required')
            if args.file == '-':
                text = sys.stdin.read(250001)
            else:
                with Path(args.file).open(encoding='utf-8') as stream: text = stream.read(250001)
            if len(text) > 250000: raise ValueError('UI input exceeds the file budget')
            parse_json = lambda s: json.loads(s, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON number')))
            definition = parse_json(text) if args.input_format == 'definition' else Framework.ui_compile(text, kind=args.kind, title=args.title)
            if args.action == 'compile':
                Framework.ui_validate(definition)
                result = definition
            elif args.action == 'export':
                result = Framework.ui_export(definition, format=args.format)['content']
            else:
                requirements = []
                if args.requirements:
                    with Path(args.requirements).open(encoding='utf-8') as stream: raw = stream.read(100001)
                    if len(raw) > 100000: raise ValueError('UI requirements exceed the file budget')
                    requirements = parse_json(raw)
                result = Framework.ui_validate(definition, requirements=requirements)
        content = result if args.action == 'export' else json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
        if args.output:
            target = Path(args.output)
            if args.file and args.file != '-' and target.resolve() == Path(args.file).resolve():
                raise ValueError('UI output must not replace its input file')
            target.write_text(content, encoding='utf-8')
        else:
            print(content, end='' if content.endswith('\n') else '\n')
        return 1 if args.action == 'validate' and not result['passed'] else 0
    except (OSError, ValueError, LanguageError, RuntimeError, KeyError, RecursionError) as error:
        print(json.dumps({'error': error.record() if isinstance(error, LanguageError) else str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


def main(argv=None):
    # Entry adapters all re-enter this same command implementation.
    if argv is not None:
        previous = sys.argv
        try:
            sys.argv = [previous[0], *argv]
            return main()
        finally:
            sys.argv = previous
    incoming = sys.argv[1:]
    if incoming and incoming[0] in ('-h', '--help', 'help'):
        from .command_intelligence import print_help
        return print_help()
    if incoming == ['--version']:
        print(__version__); return 0
    if incoming and incoming[0] == 'ask':
        from .command_intelligence import ask_main
        return ask_main(incoming[1:])
    if incoming and incoming[0] == 'assist':
        from .assistant_cli import main as assistant_main
        return assistant_main(incoming[1:])
    if incoming and (incoming[0] == '-c' or incoming[0] == '-' or incoming[0].endswith('.chaos')):
        return main(['run', *incoming])
    if len(sys.argv)>1 and sys.argv[1]=='project':
        from .project import main as project_main
        return project_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='service':
        from .service import main as service_main
        return service_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='package':
        from .portable import main as package_main
        return package_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='ui':return ui_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='executable':
        from .executable_cli import main as executable_main
        return executable_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='artifact':return artifact_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='compile':return compile_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='lsp':return lsp_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='docs':
        from .documentation import main as docs_main
        return docs_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='update':
        from .updater import main as update_main
        return update_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='release':
        from .release import main as release_main
        return release_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='debug':return debug_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='computer':return computer_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='program':return program_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='symbols':return symbols_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='session':return session_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='process':return process_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='run-guide':return run_guide_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='task':return task_main(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1]=='workspace':return workspace_main(sys.argv[2:])
    commands=('run','check','fmt','ir','repl','serve')
    if len(sys.argv)<2 or sys.argv[1] not in commands:return serve_main()
    if sys.argv[1]=='serve':del sys.argv[1];return serve_main()
    parser=argparse.ArgumentParser(description='Chaos functional agent programming language')
    parser.add_argument('command',choices=commands[:-1])
    parser.add_argument('file',nargs='?',help='UTF-8 .chaos source file; - reads standard input')
    parser.add_argument('--root',help='Package directory containing the entry and its relative imports')
    if sys.argv[1] == 'run':
        parser.add_argument('-c', dest='source', help='Evaluate inline Chaos source; imports may use std/* only')
    if sys.argv[1] in ('run','repl'):
        parser.add_argument('--config', help='Private model settings file; otherwise use CHAOS_CONFIG or user settings')
    parser.add_argument('--input-json',default='null',help='JSON value bound to input')
    parser.add_argument('--db',default=':memory:',help='Optional persistent runtime database')
    parser.add_argument('--trace',action='store_true',help='Write bounded source-step trace as JSON lines to stderr')
    if sys.argv[1]=='run':
        parser.add_argument('--stream',action='store_true',help='Flush persisted run events as NDJSON, then a same-run result envelope')
        parser.add_argument('--interactive',action='store_true',help='POSIX terminal guidance for this locally owned run; implies stream')
        parser.add_argument('--devices',help='Trusted serial/Linux HID device configuration JSON file')
        parser.add_argument('--robots',help='Trusted registered Robot configuration JSON file; does not grant robot.control')
        parser.add_argument('--computers',help='Trusted named Computer/Mobile MCP configuration JSON file; does not grant computer.control')
        parser.add_argument('--capability',action='append',default=[],help='Explicitly grant a named capability to this workspace project')
    if sys.argv[1]=='fmt':
        parser.add_argument('--line-width',type=int,default=88,help='Target line width, 40..240')
        parser.add_argument('--indent-size',type=int,default=2,help='Indentation spaces, 1..8')
        parser.add_argument('--language-version',type=int,choices=(1,2),default=2,help='Input source dialect')
    args=parser.parse_args()
    try:
        if args.command=='repl':return repl(args.file,args.root,args.config)
        inline = args.command == 'run' and args.source is not None
        if inline and args.file:parser.error('choose a file or -c, not both')
        if inline and args.root:parser.error('--root applies to files; inline source supports std/* imports only')
        if not args.file and not inline:parser.error('file is required (or use run -c SOURCE)')
        if args.command=='run' and args.interactive:
            from .process_console import validate_interactive
            validate_interactive(args.file or '<command>')
        if args.command=='fmt':
            # Formatting reads just the requested source. It must not link an
            # import graph or expand macros merely to improve whitespace.
            from .ide_language import format_source
            if args.file=='-':
                source,filename=sys.stdin.read(100001),'main.chaos'
            else:
                target=Path(args.file).expanduser().resolve()
                base=Path(args.root).expanduser().resolve() if args.root else target.parent
                try:filename=target.relative_to(base).as_posix()
                except ValueError:raise LanguageError('入口文件不在 --root 程序包目录内',code='module_error') from None
                with target.open(encoding='utf-8') as stream:source=stream.read(100001)
            print(format_source(source,filename,language_version=args.language_version,line_width=args.line_width,indent_size=args.indent_size),end='')
            return 0
        if inline:
            bundle=ProgramBundle(args.source)
        else:
            bundle=ProgramBundle(sys.stdin.read(100001)) if args.file=='-' else load_file_bundle(args.file,args.root)
        source=bundle.files[bundle.entry]
        if args.command=='check':
            print(json.dumps({**bundle.summary(),'version':__version__},ensure_ascii=False));return 0
        if args.command=='ir':
            print(json.dumps(parse_program(source,bundle.entry),ensure_ascii=False,indent=2))
            return 0
        value=json.loads(args.input_json,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite input')))
        trace_count=[0]
        def trace(event):
            if trace_count[0]<2000:print(json.dumps(event,ensure_ascii=False),file=sys.stderr)
            elif trace_count[0]==2000:print(json.dumps({'trace':'truncated','limit':2000}),file=sys.stderr)
            trace_count[0]+=1
        from .settings import Settings
        from .device_serial import load_device_registry
        registry=load_device_registry(args.devices) if args.devices else None
        if args.robots:
            from .robot_config import load_robot_registry
            registry=load_robot_registry(args.robots,registry)
        if args.computers:
            from .computer_config import load_computer_registry
            registry=load_computer_registry(args.computers,registry)
        with Framework(args.db,trace_handler=trace if args.trace else None,settings=Settings(args.config),registry=registry) as app:
            project=app.get_project('workspace');project.update(source=source,files=bundle.files,entry=bundle.entry)
            if args.capability:project['capabilities']=sorted(set(project['capabilities']) | set(args.capability))
            project=app.save_project(project,from_view='source')
            run=app.start('workspace',project['version'],value,mode='source')
            if args.interactive:
                from .process_console import attach_run
                return attach_run(run)
            if args.stream:
                from .process_console import stream_run
                return stream_run(run)
            result=run.result()
            print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 0
    except KeyboardInterrupt:
        if args.command=='run' and (args.stream or args.interactive):return 130
        raise
    except (OSError,ValueError,LanguageError,RunFailed,RuntimeError,RecursionError) as error:
        value=error.record() if isinstance(error,LanguageError) else str(error)
        print(json.dumps({'error':value},ensure_ascii=False),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
