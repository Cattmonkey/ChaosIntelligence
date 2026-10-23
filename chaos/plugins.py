"""Dependency-scoped plugin host adapted from DeepSeek Harness / Cordis (MIT).

Copyright (c) 2026 DeepSeek; Copyright (c) 2021-present Shigma.
Upstream source, license and exact revision: vendor/deepseek/UPSTREAM.md.
This Python host implements a synchronous subset, not the JavaScript plugin ABI.
"""
from dataclasses import dataclass
import threading
import uuid


@dataclass(frozen=True)
class PluginSpec:
    id: str
    apply: object
    requires: tuple = ()
    label: str = ''
    version: str = '1'
    has_client: bool = False

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id or len(self.id) > 128 or not callable(self.apply):
            raise ValueError('Plugin requires an id and apply callback')
        if not isinstance(self.requires, (tuple, list)) or not all(isinstance(n, str) and n for n in self.requires):
            raise ValueError('Plugin dependencies must be service names')


class PluginContext:
    """An activation owns services and single-shot cleanup effects."""
    def __init__(self, host, plugin_id):
        self.host, self.plugin_id = host, plugin_id
        self._disposers, self._services = [], []
        self.active = True

    def get(self, name, default=None):
        return self.host.get(name, default)

    def effect(self, disposer):
        if not self.active:
            raise RuntimeError('Cannot register an effect on an inactive plugin')
        if not callable(disposer):
            raise TypeError('Effect disposer must be callable')
        called = [False]
        guard = threading.Lock()
        def once():
            with guard:
                if called[0]: return
                called[0] = True
            return disposer()
        self._disposers.append(once)
        return once

    def provide(self, name, value):
        if not self.active:
            raise RuntimeError('Cannot provide from an inactive plugin')
        if not isinstance(name, str) or not name:
            raise ValueError('Service needs a name')
        if name in self.host._services:
            raise ValueError('Service already registered: ' + name)
        self.host._services[name] = value
        self._services.append(name)
        return value

    def dispose(self):
        if not self.active:
            return []
        self.active = False
        errors = []
        # Remove owned services first, so no new consumer can start while cleanup runs.
        for name in self._services:
            self.host._services.pop(name, None)
        for dispose in reversed(self._disposers):
            try:
                dispose()
            except Exception as error:
                errors.append(type(error).__name__)
        self._services.clear(); self._disposers.clear()
        return errors


class PluginHost:
    """Cordis-style pending/loading/active/failed/unloading/disposed lifecycle.

    Trusted callbacks register services through PluginContext. HTTP settings only
    select already registered ids. Reconciliation never imports user text.
    """
    def __init__(self, services=None, emit=None):
        self._services = dict(services or {})
        self._plugins = {}
        self._lock = threading.RLock()
        self._emit = emit
        self._closed = False
        self._sequence = 0

    def _state(self, record, state):
        record['state'] = state
        if self._emit:
            try:
                self._emit('plugin.status', {'pluginId': record['spec'].id,
                    'state': state, 'pluginRunId': record['runId']})
            except Exception:
                pass  # An observer cannot interrupt ownership cleanup.

    def add(self, spec):
        if not isinstance(spec, PluginSpec):
            raise TypeError('Expected PluginSpec')
        with self._lock:
            if self._closed: raise RuntimeError('Plugin host is closed')
            if spec.id in self._plugins: raise ValueError('Plugin id already registered')
            self._plugins[spec.id] = {'spec': spec, 'state': 'pending', 'enabled': True,
                'context': None, 'runId': None, 'dependencies': {}, 'error': None}
            self._reconcile()
        return spec

    def get(self, name, default=None):
        with self._lock:
            return self._services.get(name, default)

    def require(self, name):
        with self._lock:
            if name not in self._services: raise RuntimeError('Plugin service unavailable: ' + name)
            return self._services[name]

    def _missing(self, record):
        # Direct port of DSH lifecycle.missingServices: required names absent from context.
        return [name for name in record['spec'].requires if name not in self._services]

    def _unload(self, record, state='pending'):
        if record['context']:
            self._state(record, 'unloading')
            errors = record['context'].dispose()
            if errors: record['error'] = 'Cleanup failed: ' + ', '.join(errors)
        record['context'] = None; record['dependencies'] = {}; record['runId'] = None
        self._state(record, state)

    def _reconcile(self):
        # Remove dependent activations before loading replacements. Identity matters:
        # a new implementation under the same service name reactivates its consumers.
        affected = set()
        active = {key: r for key, r in self._plugins.items() if r['state'] == 'active'}
        for key, record in active.items():
            stale = any(self._services.get(n) is not impl
                for n, impl in record['dependencies'].items())
            if not record['enabled'] or self._missing(record) or stale: affected.add(key)
        while True:
            removed = {name for key in affected for name in active[key]['context']._services}
            more = {key for key, record in active.items()
                if any(n in removed for n in record['dependencies'])} - affected
            if not more: break
            affected.update(more)
        for record in sorted((active[k] for k in affected), key=lambda r: r['order'], reverse=True):
            self._unload(record)
        while True:
            changed = False
            for record in self._plugins.values():
                if not record['enabled'] or record['state'] != 'pending' or self._missing(record): continue
                ctx = PluginContext(self, record['spec'].id)
                record['context'] = ctx; record['runId'] = uuid.uuid4().hex
                self._sequence += 1; record['order'] = self._sequence
                record['dependencies'] = {n: self._services[n] for n in record['spec'].requires}
                self._state(record, 'loading')
                try:
                    disposer = record['spec'].apply(ctx)
                    if disposer is not None: ctx.effect(disposer)
                    record['error'] = None
                    self._state(record, 'active')
                except Exception as error:
                    # DSH startHostHalf always disposes partially mounted startup failures.
                    self._unload(record, 'failed')
                    record['error'] = type(error).__name__
                changed = True
            if not changed: break

    def refresh(self, selections=()):
        with self._lock:
            if self._closed: raise RuntimeError('Plugin host is closed')
            wanted = {}
            for selection in selections:
                if (not isinstance(selection, dict) or set(selection) != {'id', 'enabled'}
                    or selection.get('id') not in self._plugins or type(selection.get('enabled')) is not bool):
                    raise ValueError('Unknown plugin or invalid enabled setting')
                if selection['id'] in wanted: raise ValueError('Duplicate plugin setting')
                wanted[selection['id']] = selection['enabled']
            for key, record in self._plugins.items():
                enabled = wanted.get(key, True)
                if record['enabled'] != enabled and record['state'] == 'failed':
                    record['state'] = 'pending'
                record['enabled'] = enabled
            self._reconcile()
            return self.inventory()

    def inventory(self):
        with self._lock:
            result = []
            for record in self._plugins.values():
                spec = record['spec']; package_id = spec.id + '@' + spec.version
                result.append({'id': spec.id, 'pluginId': spec.id, 'label': spec.label or spec.id,
                    'version': spec.version, 'enabled': record['enabled'], 'state': record['state'],
                    'missingServices': self._missing(record), 'error': record['error'],
                    'packages': [{'packageId': package_id, 'hasClientHalf': spec.has_client}],
                    'activeRun': {'packageId': package_id, 'pluginRunId': record['runId']}
                        if record['state'] == 'active' else None})
            return result

    def close(self):
        with self._lock:
            if self._closed: return
            self._closed = True
            for record in sorted(self._plugins.values(), key=lambda r: r.get('order', 0), reverse=True):
                self._unload(record, 'disposed')
