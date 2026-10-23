"""Temporary real Process/Bot fixture for stream transport and UI tests.

No providers, devices, production databases or external servers are involved.
The gate and effect tools are ordinary registered host callbacks. HTTP failures
are injected after the production handler commits a guidance receipt.
"""
from collections import defaultdict
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import urllib.parse

from chaos import Client, Framework, Registry, Settings, ToolSpec
from chaos.server import make_server


ROOT = Path(__file__).resolve().parents[1]
DEFINITION = {
    'kind': 'process', 'name': 'stream bot', 'module': 'bot.chaos',
    'export': 'step', 'intervalSeconds': None, 'maxTurns': 10,
}
INITIAL = {'goal': 'checked stream turns', 'context': {'count': 0, 'latest': None}}
STEP = '''async fn step(ctx) {
  await progress_emit("plan", {summary:"public plan",turn:ctx.turn});
  tool("fixture.gate", {turn:ctx.turn,stage:"progress"});
  await progress_emit("progress", {summary:"public progress",turn:ctx.turn});
  for index in range(6) {
    await progress_emit("progress", {summary:"public progress detail",index:index,turn:ctx.turn})
  };
  tool("fixture.gate", {turn:ctx.turn,stage:"adoption"});
  fn plan(s) {if length(s.guidance)>0 {s.guidance[-1].input} else {s.input}}
  fn execute(proposal,s) {tool("fixture.effect",proposal)}
  fn verify(s) {
    let count=s.context.count+1;
    {done:false,context:{count:count,latest:s.evidence[-1]},output:{count:count,latest:s.evidence[-1]}}
  }
  let worker=bot(ctx.state.goal,plan,execute,verify,context=ctx.state.context,max_iterations=1);
  let actual=await worker(ctx.input);
  await progress_emit("result", {summary:"public result",count:actual.output.count,latest:actual.output.latest});
  process_step(actual.state,actual.output,false)
}
export {step};'''


def eventually(predicate, *, timeout=7, message='fixture condition did not become true'):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.01)
    raise AssertionError(message)


class StreamFixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='chaos-process-stream-test-')
        self.path = Path(self.tmp.name)
        self.entered = defaultdict(threading.Event)
        self.releases = defaultdict(threading.Event)
        self.effects, self.posts, self.stream_requests, self.control_requests = [], [], [], []
        self.condition = threading.Condition()
        self.stream_sockets = set()
        self.stream_allowed = threading.Event()
        self.stream_allowed.set()
        self.drop_next_guidance = False
        self.dropped_guidance = threading.Event()
        self.hold_next_guidance = False
        self.guidance_waiting = threading.Event()
        self.guidance_response_allowed = threading.Event()
        self.guidance_response_allowed.set()
        self.release_everything = threading.Event()
        registry = Registry()

        def gate(value, context):
            identity = (value['turn'], value['stage'])
            self.entered[identity].set()
            deadline = time.monotonic() + 45
            while not self.release_everything.is_set() and not self.releases[identity].wait(.01):
                context.checkpoint()
                if time.monotonic() >= deadline:
                    raise AssertionError('unreleased fixture gate: ' + repr(identity))
            return value

        def effect(value, context):
            context.checkpoint()
            self.effects.append(value)
            return value

        registry.register_tool(ToolSpec('fixture.gate', gate))
        registry.register_tool(ToolSpec('fixture.effect', effect))
        self.app = Framework(self.path/'runtime.sqlite', registry=registry, settings=Settings(':memory:'))
        self.app.create_project('p', source='null', files={'bot.chaos': STEP},
                                capabilities=['process.manage', 'fixture.gate', 'fixture.effect'])
        self.server = make_server('127.0.0.1', 0, framework=self.app,
                                 state_dir=self.path/'control', update_home=self.path/'updates')
        fixture = self
        production_handler = self.server.RequestHandlerClass

        class FaultHandler(production_handler):
            def body(self):
                value = super().body()
                path = urllib.parse.urlsplit(self.path).path
                if path.endswith('/guidance'):
                    with fixture.condition:
                        fixture.posts.append(json.loads(json.dumps(value)))
                        fixture.condition.notify_all()
                if path.endswith('/control') or path.endswith('/cancel'):
                    fixture.control_requests.append(path)
                return value

            def respond(self, status, data, *args, **kwargs):
                path = urllib.parse.urlsplit(self.path).path
                if status < 300 and path.endswith('/guidance') and fixture.hold_next_guidance:
                    fixture.hold_next_guidance = False
                    fixture.guidance_waiting.set()
                    if not fixture.guidance_response_allowed.wait(12):
                        return super().respond(503, {'error': 'fixture receipt gate timed out'})
                if status < 300 and path.endswith('/guidance') and fixture.drop_next_guidance:
                    fixture.drop_next_guidance = False
                    fixture.dropped_guidance.set()
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.connection.close()
                    return
                return super().respond(status, data, *args, **kwargs)

            def stream(self, project_id, after, run_id=None, process_key=None):
                with fixture.condition:
                    fixture.stream_requests.append({
                        'after': after, 'header': self.headers.get('Last-Event-ID'),
                        'project': project_id, 'key': process_key, 'path': self.path,
                    })
                    fixture.condition.notify_all()
                if not fixture.stream_allowed.wait(12):
                    return self.respond(503, {'error': 'fixture stream gate timed out'})
                with fixture.condition:
                    fixture.stream_sockets.add(self.connection)
                try:
                    return super().stream(project_id, after, run_id, process_key)
                finally:
                    with fixture.condition:
                        fixture.stream_sockets.discard(self.connection)

        self.server.RequestHandlerClass = FaultHandler
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.client = Client(self.origin, timeout=2)

    def start(self, key='worker'):
        return self.app.create_process('p', 1, key, DEFINITION, state=INITIAL)

    def at_gate(self, turn, stage):
        if not self.entered[(turn, stage)].wait(7):
            raise AssertionError('real Process never entered gate ' + repr((turn, stage)))
        return self.app.get_process('p', 'worker')

    def release(self, turn, stage):
        self.releases[(turn, stage)].set()

    def release_turn(self, turn):
        self.release(turn, 'progress')
        self.release(turn, 'adoption')

    def row(self, turn, *, status='waiting'):
        def ready():
            value = self.app.get_process('p', 'worker')
            if value['status'] == 'needs_reconciliation':
                raise AssertionError(value)
            return value if value['turn'] >= turn and value['status'] == status else None
        return eventually(ready, message='real Process failed to commit expected turn')

    def events(self):
        rows, cursor = [], 0
        while True:
            page = self.client.events('p', process_key='worker', after=cursor, limit=100)
            rows.extend(page['items'])
            if page['nextCursor'] is None:
                return rows
            cursor = page['nextCursor']

    def interrupt_streams(self):
        self.stream_allowed.clear()
        with self.condition:
            connections = list(self.stream_sockets)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def child_env(self):
        # Never inherit host provider credentials/configuration or PYTHONPATH.
        env = {name: os.environ[name] for name in ('PATH', 'SYSTEMROOT', 'WINDIR') if name in os.environ}
        env.update(HOME=str(self.path), XDG_CONFIG_HOME=str(self.path/'config'),
                   PYTHONPATH=str(ROOT), PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
                   TERM='xterm-256color', LANG='en_US.UTF-8')
        return env

    def close(self):
        self.release_everything.set()
        self.stream_allowed.set()
        self.guidance_response_allowed.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.app.close()
        self.tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
