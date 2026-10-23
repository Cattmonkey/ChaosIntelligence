"""Terminal access to the existing project-bound IDE programming assistant.

The server owns model selection, conversation state, tools and reviewable drafts.
This module only submits explicit requests and observes the same durable jobs.
"""
import argparse
import json
import math
import sys
import time

from .client import APIError, Client, TransportError


TERMINAL = frozenset(('completed', 'failed', 'cancelled', 'interrupted'))


def _integer(minimum):
    def parse(value):
        try:
            result = int(value)
            if result < minimum:
                raise ValueError()
            return result
        except ValueError:
            raise argparse.ArgumentTypeError('must be an integer >= ' + str(minimum)) from None
    return parse


def _seconds(minimum, maximum):
    def parse(value):
        try:
            result = float(value)
            if not math.isfinite(result) or not minimum <= result <= maximum:
                raise ValueError()
            return result
        except ValueError:
            raise argparse.ArgumentTypeError('must be finite seconds in %s..%s' % (minimum, maximum)) from None
    return parse


def _emit(value, *, error=False):
    # JSON escapes terminal control bytes even when provider text contains them.
    print(json.dumps(value, ensure_ascii=False, allow_nan=False),
          file=sys.stderr if error else sys.stdout, flush=True)


def _job(record, project, job_id=None):
    if (not isinstance(record, dict) or not isinstance(record.get('id'), str)
            or not record['id'] or record.get('projectId') != project
            or (job_id is not None and record['id'] != job_id)
            or record.get('status') not in TERMINAL | {'queued', 'running'}):
        raise TransportError('Assistant returned an invalid job identity or status')
    return record


def _exit_code(job):
    if job['status'] in ('cancelled', 'interrupted'):
        return 130
    return 1 if job['status'] == 'failed' else 0


def _call(client, deadline, method, *args, **kwargs):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Assistant observation deadline reached')
    old = client.timeout
    try:
        client.timeout = min(old, remaining)
        return method(*args, **kwargs)
    finally:
        client.timeout = old


def _stream(client, args, state, deadline):
    job = state['job']
    job_id = job['id']
    cursor = 0
    terminal_events = set()
    _emit({'type': 'assistant.accepted', 'projectId': args.project, 'jobId': job_id,
           'sessionId': job.get('sessionId'), 'baseVersion': job.get('baseVersion'),
           'status': job['status']})
    while True:
        page = _call(client, deadline, client.assistant_events,
                     args.project, job_id, after=cursor, limit=200)
        if not isinstance(page, dict) or not isinstance(page.get('items'), list) or len(page['items']) > 200:
            raise TransportError('Assistant returned an invalid event page')
        for event in page['items']:
            if (not isinstance(event, dict) or type(event.get('id')) is not int
                    or event['id'] <= cursor or event.get('taskId') != job_id):
                raise TransportError('Assistant returned an invalid event identity or cursor')
            _emit(event)
            cursor = event['id']
            if event.get('type') in {'task.' + status for status in TERMINAL}:
                terminal_events.add(event['type'][5:])
        state['job'] = job = _job(_call(client, deadline, client.assistant,
                                       args.project, job_id), args.project, job_id)
        # BackgroundManager commits status before its final event. Wait for both
        # and drain paginated history, rather than losing the final event in a race.
        if job['status'] in terminal_events and len(page['items']) < 200:
            _emit({'type': 'assistant.result', 'projectId': args.project,
                   'jobId': job_id, 'after': cursor, 'job': job})
            return _exit_code(job)
        if len(page['items']) < 200:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Assistant observation deadline reached')
            time.sleep(min(args.poll_interval, remaining))


def _cancel_owned(client, args, state):
    job = state.get('job')
    if job is None:
        return None
    if job['status'] in TERMINAL:
        return {'requested': False, 'status': job['status']}
    old = client.timeout
    try:
        client.timeout = min(old, 5)
        receipt = _job(client.cancel_assistant(args.project, job['id']), args.project, job['id'])
        return {'requested': True, 'status': receipt['status']}
    except (APIError, TransportError, ValueError, OSError, KeyboardInterrupt):
        return {'requested': True, 'confirmed': False,
                'message': 'Cancellation could not be confirmed; inspect this job with assist show.'}
    finally:
        client.timeout = old


def argument_parser(prog='chaos assist', *, directory=False):
    parser = argparse.ArgumentParser(prog=prog,
        description='Use the same project assistant as the IDE; edits stay proposals until explicit apply.')
    if directory:
        parser.add_argument('--directory', help='Local project directory')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('ask', 'show', 'events', 'cancel', 'apply', 'history'):
        command = commands.add_parser(name)
        if directory:
            # Preserve a selection made before the assistant subcommand.
            command.add_argument('--directory', default=argparse.SUPPRESS, help='Local project directory')
        command.add_argument('--server', default='http://127.0.0.1:8765', help='Running Chaos server origin')
        command.add_argument('--project', required=True, help='Existing server project ID')
        command.add_argument('--request-timeout', type=_seconds(.1, 120), default=30,
                             help='Maximum seconds per HTTP request (default: 30)')
        if name == 'ask':
            command.add_argument('prompt', help='Natural-language request for this project')
            command.add_argument('--version', type=_integer(1), help='Expected project version; default: read current version once')
            command.add_argument('--session', help='Existing conversation ID for the next turn')
            command.add_argument('--stream', action='store_true', help='Emit public events and final same-job result as NDJSON')
            command.add_argument('--timeout', type=_seconds(.1, 3600), default=300,
                                 help='Submission and observation deadline in seconds (default: 300); expiry leaves the remote job running')
            command.add_argument('--poll-interval', type=_seconds(.05, 5), default=.25,
                                 help='Seconds between non-full event pages (default: 0.25)')
        elif name == 'history':
            command.add_argument('--session', help='Filter by conversation ID')
        else:
            command.add_argument('job', help='Assistant job ID')
            if name == 'apply':
                command.add_argument('--version', type=_integer(1), required=True,
                                     help='Explicit current project version required for compare-and-swap')
            if name == 'events':
                command.add_argument('--after', type=_integer(0), default=0)
                command.add_argument('--limit', type=_integer(1), default=200, help='Snapshot page size, 1..1000')
    return parser


def parse_args(argv=None, *, prog='chaos assist', directory=False):
    parser = argument_parser(prog, directory=directory)
    args = parser.parse_args(argv)
    if not args.project.strip():
        parser.error('--project cannot be blank')
    if args.command == 'ask' and not 1 <= len(args.prompt) <= 20000:
        parser.error('prompt must contain 1..20000 characters')
    if args.command == 'events' and args.limit > 1000:
        parser.error('--limit must be 1..1000')
    return args


def main(argv=None):
    return run(parse_args(argv))


def run(args):
    client = None
    state = {'job': None, 'submitting': False}
    try:
        client = Client(args.server, timeout=args.request_timeout)
        if args.command == 'ask':
            deadline = time.monotonic() + args.timeout
            version = args.version
            if version is None:
                project = _call(client, deadline, client.get_project, args.project)
                version = project.get('version') if isinstance(project, dict) else None
                if type(version) is not int or version < 1:
                    raise TransportError('Project returned an invalid version')
            state['submitting'] = True
            record = _call(client, deadline, client.start_assistant, args.project,
                           version, args.prompt, session_id=args.session)
            state['job'] = _job(record, args.project)
            state['submitting'] = False
            if args.stream:
                return _stream(client, args, state, deadline)
            _emit(state['job'])
            return _exit_code(state['job'])
        if args.command == 'history':
            _emit(client.assistant_history(args.project, session_id=args.session))
        elif args.command == 'events':
            _emit(client.assistant_events(args.project, args.job, after=args.after, limit=args.limit))
        elif args.command == 'apply':
            _emit(client.apply_assistant(args.project, args.job, args.version))
        elif args.command == 'cancel':
            _emit(client.cancel_assistant(args.project, args.job))
        else:
            job = _job(client.assistant(args.project, args.job), args.project, args.job)
            _emit(job)
            return _exit_code(job)
        return 0
    except (KeyboardInterrupt, TimeoutError, APIError, TransportError, ValueError, OSError) as error:
        status = 'interrupted' if isinstance(error, KeyboardInterrupt) else 'timed_out' if isinstance(error, TimeoutError) else 'error'
        receipt = {'type': 'assistant.exit', 'status': status, 'projectId': args.project}
        if state['job'] is not None:
            receipt['jobId'] = state['job']['id']
            if isinstance(error, KeyboardInterrupt):
                receipt['cancellation'] = _cancel_owned(client, args, state)
            else:
                receipt['message'] = 'Observation ended; no cancellation was requested. Use assist show, events or cancel with this job ID.'
        elif state['submitting'] and not isinstance(error, APIError):
            receipt['submissionUnknown'] = True
            receipt['message'] = 'Submission outcome is unknown; inspect assist history before retrying.'
        if isinstance(error, APIError):
            receipt['error'] = {'code': error.code, 'status': error.status, 'message': str(error)}
        elif not isinstance(error, KeyboardInterrupt):
            receipt['error'] = str(error)
        _emit(receipt, error=True)
        return 130 if status == 'interrupted' else 124 if status == 'timed_out' else 1
