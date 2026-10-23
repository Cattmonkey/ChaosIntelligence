"""Configured, cancellable OpenUI text streaming; no second language executor."""
import threading
import time

from .extensions import json_copy
from .harness import _get, _public_error
from .language import LanguageError

_STREAM_SLOTS = threading.BoundedSemaphore(4)


def stream_ui(config, messages, client_factory, checkpoint, cancelled,
              event, report_failure=lambda provider, model: None,
              report_success=lambda provider, model: None):
    """Read one full replacement. Never retry once visible content was emitted.

    A cancellation watcher closes the exact transport. The effect worker keeps
    its slot until the SDK reader returns; its read timeout bounds that drain.
    No evaluator or language callback runs on the watcher thread.
    """
    from .ui_artifacts import MAX_SOURCE
    timeout = config.get('timeout', 60)
    if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
        raise LanguageError('UI generation timeout is invalid', code='ui_error')
    deadline = time.monotonic() + timeout
    candidates = list(dict.fromkeys([config['model']] + list(config.get('candidates', []))))[:8]
    if not candidates or any(type(m) is not str or not m for m in candidates):
        raise LanguageError('UI model candidates must be nonempty names', code='ui_error')

    def check():
        checkpoint()
        if time.monotonic() >= deadline:
            raise LanguageError('UI generation time budget exhausted', code='ui_error')

    while not _STREAM_SLOTS.acquire(timeout=.05):
        check()
    client = stream = None
    stop = threading.Event()
    transport_lock = threading.Lock()
    closed_transports = {}

    def close_one(transport):
        if transport is None:
            return
        with transport_lock:
            if id(transport) in closed_transports:
                return
            closed_transports[id(transport)] = transport
        try:
            transport.close()
        except Exception:
            pass

    def close_transport():
        with transport_lock:
            current_stream, current_client = stream, client
        for transport in (current_stream, current_client):
            close_one(transport)

    def watch():
        while not stop.wait(.025):
            if cancelled() or time.monotonic() >= deadline:
                close_transport()
                return

    watcher = threading.Thread(target=watch, name='chaos-ui-cancel', daemon=True)
    try:
        check()
        client = client_factory(config)
        watcher.start()
        for number, model in enumerate(candidates):
            source = ''
            emitted = False
            finished = False
            chunks = preview_events = preview_bytes = 0
            preview_limited = False
            last_preview = last_size = 0
            try:
                check()
                response = client.chat.completions.create(
                    model=model, messages=messages, stream=True,
                    timeout=max(.1, min(30, deadline - time.monotonic())))
                with transport_lock:
                    stream = response
                check()
                for chunk in response:
                    check()
                    chunks += 1
                    if chunks > 20000:
                        raise LanguageError('UI stream chunk budget exceeded', code='resource_limit')
                    choices = _get(chunk, 'choices', [])
                    if type(choices) not in (list, tuple):
                        raise LanguageError('UI stream choices are invalid', code='ui_error')
                    if not choices:
                        continue  # optional SDK usage-only terminal chunk
                    if len(choices) != 1 or _get(choices[0], 'index', 0) != 0:
                        raise LanguageError('UI stream requires one completion', code='ui_error')
                    choice = choices[0]
                    delta = _get(choice, 'delta')
                    if _get(delta, 'tool_calls') or _get(delta, 'function_call'):
                        raise LanguageError('UI text generation cannot invoke model tools', code='ui_error')
                    text = _get(delta, 'content')
                    reason = _get(choice, 'finish_reason')
                    if text is not None:
                        if type(text) is not str or finished:
                            raise LanguageError('UI stream content protocol is invalid', code='ui_error')
                        if len(source) + len(text) > MAX_SOURCE:
                            raise LanguageError('UI source budget exceeded', code='resource_limit')
                        if text:
                            source += text
                            emitted = True
                            now = time.monotonic()
                            if not last_preview or now - last_preview >= .25 or len(source) - last_size >= 1000:
                                cost = len(source.encode('utf-8'))
                                if preview_events < 128 and preview_bytes + cost <= 2000000:
                                    event('ui.delta', {'source': source})
                                    preview_events += 1
                                    preview_bytes += cost
                                    last_preview, last_size = now, len(source)
                                elif not preview_limited:
                                    preview_limited = True
                                    event('ui.preview_limited', {'summary': 'Preview budget reached; final response is still being validated.'})
                    if reason is not None:
                        if reason != 'stop' or finished:
                            raise LanguageError('UI completion did not finish normally', code='ui_error')
                        finished = True
                check()
                if not finished or not source:
                    raise LanguageError('UI stream ended without a complete response', code='ui_error')
                report_success(config.get('provider'), model)
                event('ui.generation.finished', {'characters': len(source), 'model': model})
                return {'source': source}
            except BaseException as error:
                check()  # cancellation retains the original engine control error
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                report_failure(config.get('provider'), model)
                status = getattr(error, 'status_code', None)
                retryable = status in (404, 408, 409, 422, 429) or (type(status) is int and status >= 500)
                retryable = retryable or type(error).__name__ in (
                    'APIConnectionError', 'APITimeoutError', 'TimeoutError', 'ConnectionError')
                if emitted or not retryable or number + 1 >= len(candidates):
                    if isinstance(error, LanguageError):
                        raise
                    raise LanguageError('UI model request failed: ' + _public_error(error), code='provider_error') from None
                event('ui.model_fallback', {'model': candidates[number + 1]})
            finally:
                with transport_lock:
                    previous_stream, stream = stream, None
                close_one(previous_stream)
        raise LanguageError('UI generation produced no result', code='ui_error')
    finally:
        stop.set()
        close_transport()
        if watcher.ident is not None:
            # Preserve the real worker barrier even if a transport close takes
            # time. Other runs remain responsive; this slot is not reused yet.
            watcher.join()
        _STREAM_SLOTS.release()


def generate(context, payload, allowed, node_id):
    """Effect-worker boundary. Only copied data enters; native AST resumes later."""
    from .ui_artifacts import LIBRARY_HASH, prompt as ui_prompt
    from .control import plugin_host
    import json
    if context.recording_scope is not None:
        raise LanguageError('UI model generation cannot run inside skill recording', code='mode_error')
    data = json_copy({k: v for k, v in payload.items() if not k.startswith('_')})
    expected = {'operation', 'agent', 'request', 'kind', 'title', 'previous', 'libraryHash'}
    if set(data) != expected or data['operation'] != 'generate':
        raise LanguageError('Invalid UI generation effect', code='ui_error')
    if data['libraryHash'] != LIBRARY_HASH:
        raise LanguageError('UI component library changed', code='ui_error')
    agent = data['agent']
    if type(agent) is not dict or set(agent) != {'name', 'config'}:
        raise LanguageError('Invalid UI generation Agent', code='ui_error')
    config = agent['config']
    if type(config) is not dict or config.get('provider') != 'model':
        raise LanguageError('ui_generate requires a configured model Agent', code='ui_error')
    tools = config.get('tools', [])
    if type(tools) is not list or any(type(t) is not str or t not in context.engine.tools for t in tools):
        raise LanguageError('UI Agent contains unknown tools', code='capability_error')
    if not set(tools).issubset(context.scoped_allowed(allowed)):
        raise LanguageError('UI Agent exceeds enclosing authority', code='capability_error')
    settings = context.engine.settings
    resolved = settings.resolve(config.get('route', agent['name']), config.get('role', 'executor'))
    if resolved['backend'] != 'native':
        raise LanguageError('UI generation requires the native model backend', code='ui_error')
    resolved['timeout'] = min(resolved['timeout'], max(.1, context.budget.deadline - time.monotonic()))
    instructions = ui_prompt(data['kind'])
    previous = data['previous']
    if previous is not None:
        if type(previous) is not str:
            raise LanguageError('UI previous source must be a String', code='ui_error')
        instructions += '\nEdit the supplied previous UI. Return a complete replacement, never a patch.\nPrevious UI:\n' + previous
    prompt = config.get('prompt', '')
    messages = [{'role': 'system', 'content': prompt + '\n' + instructions},
                {'role': 'user', 'content': data['request'] if type(data['request']) is str else json.dumps(data['request'], ensure_ascii=False, allow_nan=False)}]
    host = plugin_host(settings, lambda kind, info: context.event(kind, info))
    try:
        host.require('native')
        context.event('route.selected', {k: resolved[k] for k in ('provider', 'model', 'backend', 'configVersion')})
        def event(kind, info):
            context.budget.tick()
            context.event(kind, {'nodeId': node_id or agent['name'], 'kind': data['kind'],
                                 'title': data['title'], 'libraryHash': data['libraryHash'], **settings.redact(info)})
        return stream_ui(resolved, messages, lambda c: settings.client(resolved=c),
                         context.budget.tick, context.cancel.is_set, event,
                         settings.report_failure, settings.report_success)
    finally:
        host.close()
