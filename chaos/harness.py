"""Standalone structured agent harnesses. No Codex App dependency.

Native models communicate through declared SDK tool calls. Ordinary answer text
is returned as text: no keyword, markdown or fixed answer-format recognition.
"""
import copy
import json
import os
from pathlib import Path
import queue
import shutil
import tempfile
import threading
import time

from .execution import ExecutionError, run_process
from .extensions import json_copy, valid_name


class HarnessError(RuntimeError):
    pass


# Cancelled HTTP requests can finish in a daemon thread. Keep their concurrency
# bounded until the actual transport returns, including repeated cancel/resume.
_REQUEST_SLOTS = threading.BoundedSemaphore(4)


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _request(call, checkpoint):
    while not _REQUEST_SLOTS.acquire(timeout=.05):
        checkpoint()
    result = queue.Queue(maxsize=1)
    def work():
        try:
            result.put((True, call()))
        except BaseException as exc:
            result.put((False, exc))
        finally:
            _REQUEST_SLOTS.release()
    threading.Thread(target=work, daemon=True).start()
    while True:
        checkpoint()
        try:
            ok, value = result.get(timeout=.05)
            if ok:
                return value
            raise value
        except queue.Empty:
            continue


def _public_error(exc):
    # Provider exception text can contain HTTP bodies/headers/keys. Only expose
    # class and status. Full transport details stay out of durable events.
    status = getattr(exc, 'status_code', None)
    return type(exc).__name__ + (' (HTTP %s)' % status if status is not None else '')


class _ProtocolError(HarnessError):
    """A model response failed validation before any tool effects occurred."""


def _validated_response(response, aliases):
    choices = _get(response, 'choices', [])
    if not isinstance(choices, (list, tuple)) or not choices:
        raise _ProtocolError('Model returned no completion')
    message = _get(choices[0], 'message')
    calls = _get(message, 'tool_calls', [])
    calls = [] if calls is None else calls
    content = _get(message, 'content')
    if not isinstance(calls, (list, tuple)):
        raise _ProtocolError('Invalid native tool call batch')
    if content is not None and (not isinstance(content, str) or len(content) > 250000):
        raise _ProtocolError('Model answer exceeds the output budget')
    if not calls:
        if content is None:
            raise _ProtocolError('Model returned neither an answer nor a tool call')
        return content, [], []
    if len(calls) > 32:
        raise _ProtocolError('Too many tool calls in one transition')
    history_calls, pending, seen_ids = [], [], set()
    # Validate the complete batch without mutating messages or executing tools.
    for call in calls:
        call_id, function = _get(call, 'id'), _get(call, 'function')
        alias, arguments = _get(function, 'name'), _get(function, 'arguments')
        if not isinstance(call_id, str) or not call_id or call_id in seen_ids:
            raise _ProtocolError('Invalid or duplicate native tool call id')
        seen_ids.add(call_id)
        if not isinstance(alias, str) or alias not in aliases:
            raise _ProtocolError('Model requested an undeclared tool call')
        if not isinstance(arguments, str) or len(arguments) > 250000:
            raise _ProtocolError('Native tool arguments are missing or oversized')
        try:
            data = json.loads(arguments, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
            data = json_copy(data)
        except (ValueError, RecursionError):
            raise _ProtocolError('Native tool arguments are invalid JSON') from None
        if not isinstance(data, dict):
            raise _ProtocolError('Native tool arguments must be an object')
        spec = aliases[alias]
        if spec.get('wrapped'):
            if set(data) != {'input'}:
                raise _ProtocolError('Wrapped tool arguments require exactly input')
            data = data['input']
        if 'inputType' in spec:
            from .language import check_type, LanguageError
            try: check_type(data,spec['inputType'])
            except LanguageError: raise _ProtocolError('Native tool arguments violate the input contract') from None
        pending.append((call_id, spec, data))
        history_calls.append({'id': call_id, 'type': 'function',
                              'function': {'name': alias, 'arguments': arguments}})
    return content, pending, history_calls


class NativeHarness:
    def __init__(self, config, client_factory=None, report_failure=None, report_success=None):
        self.config = dict(config)
        self.client_factory = client_factory
        self.report_failure = report_failure or (lambda provider, model: None)
        self.report_success = report_success or (lambda provider, model: None)

    def _client(self):
        if self.client_factory:
            return self.client_factory(self.config)
        try:
            from openai import OpenAI
            import httpx
        except ImportError as exc:
            raise HarnessError('Install the model extra: pip install "chaos-intelligence[model]"') from exc
        return OpenAI(base_url=self.config['baseURL'], api_key=self.config['apiKey'],
                      max_retries=0, timeout=min(120, self.config.get('timeout', 60)),
                      http_client=httpx.Client(follow_redirects=False, trust_env=False))

    def run(self, prompt, value, tools=None, call_tool=None, checkpoint=lambda: None,
            event=lambda kind, payload: None, *, user_message=None):
        return self._run(prompt,value,tools,call_tool,checkpoint,event,user_message=user_message)

    def run_session(self, prompt, value, history, tools=None, call_tool=None,
                    checkpoint=lambda: None, event=lambda kind,payload: None,
                    journal=lambda messages: None, context_limit=100000, *, user_message=None):
        if type(history) is not list:raise ValueError('Session history must be a list')
        return self._run(prompt,value,tools,call_tool,checkpoint,event,
                         session_history=history,journal=journal,context_limit=context_limit,user_message=user_message)

    def _run(self, prompt, value, tools, call_tool, checkpoint, event,
             session_history=None, journal=None, context_limit=100000, user_message=None):
        if tools and call_tool is None: raise ValueError('A tool dispatcher is required')
        driver = self.actions(prompt, value, tools, checkpoint, event,
                              session_history, journal, context_limit, user_message)
        try:
            action = next(driver)
            while True:
                try:
                    if call_tool is None: raise ValueError('A tool dispatcher is required')
                    result = call_tool(action['name'], action['input'])
                except BaseException as error:
                    action = driver.throw(error)
                else:
                    action = driver.send(result)
        except StopIteration as result:
            return result.value
        finally:
            driver.close()

    def actions(self, prompt, value, tools=None, checkpoint=lambda: None,
                event=lambda kind, payload: None,
             session_history=None, journal=None, context_limit=100000, user_message=None):
        from .session_protocol import (validate_messages,validate_message,encode_messages,
                                       input_message,tool_alias)
        if user_message is not None:
            user_message=validate_message(user_message)
            if user_message['role']!='user':raise ValueError('Native user_message must be a canonical user message')
        session_mode = session_history is not None
        history = validate_messages(session_history,context_limit=context_limit) if session_mode else []
        delta = []
        config = self.config
        if not config.get('baseURL') or not config.get('apiKey') or not config.get('model'):
            raise HarnessError('Configure a provider baseURL, API key and model')
        maximum = config.get('maxSteps', 12)
        timeout = config.get('timeout', 60)
        if type(maximum) is not int or not 1 <= maximum <= 100:
            raise ValueError('Harness maxSteps must be 1–100')
        if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
            raise ValueError('Harness timeout must be in (0, 3600]')
        output_limit = config.get('maxOutputTokens')
        if 'maxOutputTokens' in config and (type(output_limit) is not int or not 1 <= output_limit <= 65536):
            raise ValueError('Harness maxOutputTokens must be 1–65536')
        deadline = time.monotonic() + timeout
        def check():
            checkpoint()
            if time.monotonic() >= deadline:
                raise HarnessError('Agent time budget exhausted')
        descriptors = tools or []
        aliases, declarations = {}, []
        for index, spec in enumerate(descriptors):
            name = spec['name']
            valid_name(name)
            alias = tool_alias(name) if session_mode else 'tool_%d' % index
            if alias in aliases: raise ValueError('Duplicate native tool alias')
            if any(item['name'] == name for item in aliases.values()):
                raise ValueError('Duplicate tool name: ' + name)
            schema = json_copy(spec.get('parameters', {'type': 'object'}))
            if schema.get('type') != 'object':
                raise ValueError('Tool parameters require an object JSON schema')
            aliases[alias] = spec
            declarations.append({'type': 'function', 'function': {'name': alias,
                'description': (name + ': ' + spec.get('description', ''))[:4000], 'parameters': schema}})
        messages = [{'role': 'system', 'content': str(prompt)}]
        def record(batch):
            # Validate the entire proposed append and budget before journaling or
            # executing any tool. Host persistence failure aborts the transition.
            copied=[validate_message(message) for message in batch]
            validate_messages(history+delta+copied,complete=False,context_limit=context_limit)
            check();journal(json_copy(copied));check()
            delta.extend(copied);messages.extend(encode_messages(copied))
        if session_mode:
            messages.extend(encode_messages(history));record([input_message(value) if user_message is None else user_message])
        else:
            messages.append(user_message if user_message is not None else {'role':'user','content':value if isinstance(value,str) else json.dumps(json_copy(value),ensure_ascii=False)})
        candidates = list(dict.fromkeys([config['model']] + list(config.get('candidates', []))))[:8]
        if any(not isinstance(model, str) or not model for model in candidates):
            raise ValueError('Model candidates must be nonempty names')
        model_index = 0
        client = self._client()
        primary_failed=False
        primary_error=None
        try:
            for step in range(1, maximum + 1):
                check()
                event('agent.thinking', {'step': step, 'maxSteps': maximum,
                                        'summary': '正在规划下一步操作', 'model': candidates[model_index]})
                while True:
                    check()
                    model = candidates[model_index]
                    args = {'model': model, 'messages': messages,
                            'timeout': max(.1, min(120, deadline - time.monotonic()))}
                    if output_limit is not None:
                        args['max_tokens'] = output_limit
                    if declarations:
                        args.update(tools=declarations, tool_choice='auto')
                    try:
                        response = _request(lambda: client.chat.completions.create(**args), check)
                    except Exception as exc:
                        check()
                        # Authentication applies to all same-provider models.
                        status = getattr(exc, 'status_code', None)
                        retryable = status in (404, 408, 409, 422, 429) or (isinstance(status, int) and status >= 500)
                        retryable = retryable or type(exc).__name__ in ('APIConnectionError', 'APITimeoutError', 'TimeoutError', 'ConnectionError')
                        if not retryable or model_index + 1 >= len(candidates):
                            raise HarnessError('Model request failed: ' + _public_error(exc)) from None
                        self.report_failure(config.get('provider'), model)
                        model_index += 1
                        event('agent.model_fallback', {'model': candidates[model_index], 'summary': '当前模型不可用，改用已配置的候选模型'})
                        continue
                    try:
                        content, pending, history_calls = _validated_response(response, aliases)
                    except _ProtocolError as exc:
                        check()
                        self.report_failure(config.get('provider'), model)
                        if model_index + 1 >= len(candidates):
                            raise HarnessError('Model response protocol failed: ' + str(exc)) from None
                        model_index += 1
                        event('agent.model_fallback', {'model': candidates[model_index],
                            'summary': '工具响应格式无效，切换候选模型'})
                        continue
                    check()
                    self.report_success(config.get('provider'), model)
                    break
                if not pending:
                    if session_mode:
                        record([{'role':'assistant','content':content,'tool_calls':[]}])
                        validate_messages(history+delta,context_limit=context_limit)
                        event('agent.completed', {'step': step, 'model': model, 'summary': '已完成本轮任务'})
                        return {'output':content,'messages':delta}
                    event('agent.completed', {'step': step, 'model': model, 'summary': '已完成本轮任务'})
                    return content
                if session_mode:
                    record([{'role':'assistant','content':content,'tool_calls':[
                        {'id':call['id'],'name':aliases[call['function']['name']]['name'],
                         'arguments':json.loads(call['function']['arguments'])} for call in history_calls]}])
                else:
                    messages.append({'role': 'assistant', 'content': content, 'tool_calls': history_calls})
                images = []
                for call_id, spec, data in pending:
                    name = spec['name']
                    activity = {'step': step, 'tool': name, 'callId': call_id, 'category': spec.get('category', 'tools')}
                    check()
                    event('agent.tool_started', {**activity, 'summary': '正在执行工具 ' + name})
                    failed = False
                    try:
                        result = json_copy((yield {'name': name, 'input': data}))
                    except (ValueError, ExecutionError) as exc:
                        # Recoverable tool failures are observations for the next
                        # model transition. Cancellation/budget exceptions are not
                        # caught here and always unwind the run.
                        check()
                        detail = str(exc)[:1000]
                        key = config.get('apiKey')
                        if key:
                            detail = detail.replace(key, '[redacted]')
                        result = {'error': {'type': type(exc).__name__, 'message': detail}}
                        failed = True
                    check()
                    if session_mode and spec.get('imageOutput') and isinstance(result,dict) and 'image' in result and (not isinstance(result['image'],str) or not result['image'].startswith('data:image/')):
                        raise ValueError('Session image observation requires a bounded data:image URL')
                    if spec.get('imageOutput') and isinstance(result, dict) and isinstance(result.get('image'), str) and result['image'].startswith('data:image/'):
                        images.append({'type': 'image_url', 'image_url': {'url': result['image']}})
                        if not session_mode: result = {**result, 'image': '[image attached]'}
                    if session_mode:
                        record([{'role':'tool','tool_call_id':call_id,'name':name,'content':result}])
                    else:
                        messages.append({'role': 'tool', 'tool_call_id': call_id,
                                         'content': json.dumps(result, ensure_ascii=False)})
                    event('agent.tool_failed' if failed else 'agent.tool_completed', {**activity,
                        'summary': ('工具执行失败，等待智能体调整后重试 ' if failed else '工具已完成 ') + name})
                if images:
                    observation={'role':'user','content':[{'type':'text','text':'Images returned by the completed tools:'}]+images}
                    if session_mode: record([observation])
                    else: messages.append(observation)
            raise HarnessError('Agent action budget exhausted without a final answer')
        except BaseException as error:
            primary_error=error
            primary_failed=True
            raise
        finally:
            close = getattr(client, 'close', None)
            if close:
                try: close()
                except BaseException as cleanup:
                    from .suspension import hard_error, error_priority
                    if isinstance(primary_error,GeneratorExit) or error_priority(cleanup)>error_priority(primary_error): raise
                    if not hard_error(primary_error) and not(session_mode and primary_failed): raise


class CodexHarness:
    """Adapter to the separately installed `codex exec` CLI (JSONL events).

    Codex owns its code tools. Host callback tools cannot be silently advertised
    to this backend; select native for the host's capability dispatcher.
    """
    def __init__(self, config):
        self.config = dict(config)

    def run(self, prompt, value, tools=None, call_tool=None, checkpoint=lambda: None,
            event=lambda kind, payload: None):
        config = self.config
        if tools:
            raise HarnessError('Codex owns its CLI tools; host callback tools require the native backend')
        if not config.get('apiKey') or not config.get('baseURL') or not config.get('model'):
            raise HarnessError('Configure the Codex provider baseURL, API key and model')
        executable = shutil.which(config.get('codexPath') or 'codex')
        if not executable:
            raise HarnessError('Install the standalone open-source Codex CLI and set codexPath')
        workspace = Path(config.get('workspace') or '.').expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        final = [None]
        failure = [False]
        def consume(line):
            try:
                item = json.loads(line)
            except ValueError:
                return
            kind = item.get('type')
            if kind == 'item.completed':
                body = item.get('item', {})
                if body.get('type') == 'agent_message':
                    final[0] = body.get('text')
                elif body.get('type') in ('command_execution', 'file_change', 'mcp_tool_call', 'web_search'):
                    event('agent.tool_completed', {'tool': body.get('type'), 'callId': body.get('id'), 'category': 'code' if body.get('type') in ('command_execution', 'file_change') else 'tools', 'summary': 'Codex 已完成一项工具操作'})
            elif kind in ('turn.started', 'item.started'):
                # Reasoning item text is intentionally neither retained nor emitted.
                event('agent.thinking', {'summary': 'Codex 正在推进任务'})
            elif kind in ('error', 'turn.failed'):
                failure[0] = True
        with tempfile.TemporaryDirectory(prefix='chaos-codex-') as home:
            env = os.environ.copy()
            env.pop('OPENAI_API_KEY', None)
            env.update(CODEX_HOME=home, CHAOS_PROVIDER_KEY=config['apiKey'])
            provider = {'name': 'Chaos configured provider', 'base_url': config['baseURL'],
                        'env_key': 'CHAOS_PROVIDER_KEY', 'wire_api': 'responses', 'requires_openai_auth': False, 'supports_websockets': False}
            argv = [executable, 'exec', '--json', '--ephemeral', '--skip-git-repo-check',
                    '--ignore-user-config', '--color', 'never', '-C', str(workspace),
                    '--sandbox', 'workspace-write' if config.get('codeEnabled') else 'read-only',
                    '--model', config['model'], '-c', 'model_provider="chaos"']
            for key, setting in provider.items():
                argv += ['-c', 'model_providers.chaos.' + key + '=' + json.dumps(setting)]
            argv.append('-')
            text = str(prompt) + '\n\nTask input:\n' + (value if isinstance(value, str) else json.dumps(json_copy(value), ensure_ascii=False))
            result = run_process(argv, workspace, timeout=config.get('timeout', 60), max_output=1_000_000,
                                 checkpoint=checkpoint, env=env, stdin=text, on_line=consume)
        # Do not expose raw JSONL/stderr: they can include internal reasoning.
        if result['exitCode'] != 0 or failure[0] or final[0] is None:
            raise HarnessError('Codex execution failed or produced no final answer (exit %s)' % result['exitCode'])
        if not isinstance(final[0], str) or len(final[0]) > 250000:
            raise HarnessError('Codex answer exceeds the output budget')
        event('agent.completed', {'summary': 'Codex 已完成本轮任务'})
        return final[0]


def run_harness(config, prompt, value, tools=None, call_tool=None, checkpoint=lambda: None,
                event=lambda kind, payload: None, client_factory=None,
                report_failure=None, report_success=None):
    backend = config.get('backend', 'native')
    if backend == 'native':
        harness = NativeHarness(config, client_factory, report_failure, report_success)
    elif backend == 'codex':
        harness = CodexHarness(config)
    else:
        raise HarnessError('Unknown harness backend: ' + str(backend))
    return harness.run(prompt, value, tools, call_tool, checkpoint, event)
