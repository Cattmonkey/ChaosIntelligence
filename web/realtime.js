/* Durable public events: acknowledge only after the consumer has applied them. */
(() => {
  'use strict';
  if (window.ChaosRealtime) return;
  const identity = value => { if (typeof ExactNumber !== 'undefined' && value instanceof ExactNumber) value = value.raw; if (typeof value === 'bigint' && value >= 0n) return value; if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) return BigInt(value); if (typeof value === 'string' && /^(0|[1-9][0-9]{0,18})$/.test(value)) return BigInt(value); throw Error('Invalid event cursor'); };
  const compareIds = (left, right) => { const a = identity(left), b = identity(right); return a < b ? -1 : a > b ? 1 : 0; };
  function subscribe({url, after = 0, onEvent, onState = () => {}}) {

    if (typeof onEvent !== 'function') throw Error('Invalid event subscription');
    const endpoint = new URL(url, window.location.href);
    if (endpoint.origin !== window.location.origin) throw Error('Events must use this service');
    let cursor = identity(after), source = null, timer = null, closed = false, draining = false, suspended = false;
    let pending = [], retry = false, attempt = 0, state = '';
    function status(value) { if (!closed && value !== state) { state = value; onState(value); } }
    function disconnect() { const previous = source; source = null; previous?.close(); }
    function schedule() {
      if (closed || suspended || draining || pending.length || timer !== null) return;
      timer = setTimeout(() => { timer = null; connect(); }, Math.min(5000, 250 * 2 ** Math.min(attempt++, 5)));
    }
    function recover() { disconnect(); retry = true; status('reconnecting'); schedule(); }
    async function drain() {
      if (draining || closed) return;
      draining = true;
      try {
        while (pending.length && !closed) {
          const event = pending.shift();
          if (identity(event.id) <= cursor) continue;
          await onEvent(event);
          if (!closed) cursor = identity(event.id);
        }
      } catch (_) {
        // The failed event is unacknowledged. Replay it rather than skip its effects.
        pending = []; recover();
      } finally { draining = false; if (retry) schedule(); }
    }
    function connect() {
      if (closed || suspended) return;
      retry = false; endpoint.searchParams.set('after', String(cursor));
      status(cursor || attempt ? 'reconnecting' : 'connecting');
      const current = new EventSource(endpoint.pathname + endpoint.search);
      source = current;
      current.onopen = () => { if (source === current && !closed) { attempt = 0; status('connected'); } };
      current.onerror = () => { if (source === current && !closed) recover(); };
      current.onmessage = message => {
        if (closed || source !== current) return;
        try {
          const event = (window.parseJSON || JSON.parse)(message.data);
          if (!event || identity(event.id) < 1n || typeof event.type !== 'string') throw Error('Invalid event');
          if (identity(event.id) <= cursor) return;
          if (pending.length >= 1000) { recover(); return; }
          pending.push(event); drain();
        } catch (_) { recover(); }
      };
    }
    function reconnect() {
      if (closed) return;
      disconnect(); retry = true;
      clearTimeout(timer); timer = null; status('reconnecting'); schedule();
    }
    function visible() { if (!document.hidden && state !== 'connected') reconnect(); }
    function pagehide(event) {
      if (!event.persisted) { close(); return; }
      suspended = true; disconnect(); clearTimeout(timer); timer = null;
    }
    function pageshow(event) { if (event.persisted && !closed) { suspended = false; reconnect(); } }
    function close() {
      if (closed) return;
      closed = true; pending = []; disconnect(); clearTimeout(timer); timer = null;
      window.removeEventListener('online', reconnect); window.removeEventListener('pagehide', pagehide); window.removeEventListener('pageshow', pageshow);
      document.removeEventListener('visibilitychange', visible);
    }
    window.addEventListener('online', reconnect); window.addEventListener('pagehide', pagehide); window.addEventListener('pageshow', pageshow);
    document.addEventListener('visibilitychange', visible);
    connect();
    return Object.freeze({close, reconnect, cursor: () => String(cursor)});
  }
  window.ChaosRealtime = Object.freeze({subscribe, compareIds, eventId: value => String(identity(value))});
})();
