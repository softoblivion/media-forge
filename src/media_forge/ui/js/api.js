// Bridge layer (docs/BRIDGE_API.md, section 1).
//
// Python -> JS: the shell evaluates `window.mediaForge.dispatch(name, payload)`. This module
// registers that hook at import time, before ready() is called, and holds every event in a queue
// until the page has wired its components (goLive()); then events are delivered in order.
// JS -> Python: call(method, ...args) on window.pywebview.api, or on the mock (js/mock.js) with
// ?mock=1 or when pywebview does not appear within 1.5 s. Components never touch
// window.pywebview directly.

const listeners = new Map(); // event name -> Set<fn>
let backend = null;
let mode = 'pending'; // 'pywebview' | 'mock'

/** Subscribe to a bridge event (or a UI-internal one, prefixed "ui:"). Returns an unsubscribe. */
export function on(name, fn) {
  if (!listeners.has(name)) listeners.set(name, new Set());
  listeners.get(name).add(fn);
  return () => listeners.get(name).delete(fn);
}

export function emit(name, payload) {
  const set = listeners.get(name);
  if (!set) return;
  for (const fn of Array.from(set)) {
    try {
      fn(payload);
    } catch (err) {
      console.error(`[ui] handler for "${name}" failed`, err);
      reportError(`handler for ${name}: ${err && err.message}`);
    }
  }
}

let early = [];

function dispatch(name, payload) {
  if (early) early.push([name, payload]);
  else emit(name, payload);
}

// The one hook the shell calls. Registered at module evaluation, i.e. before ready().
window.mediaForge = Object.assign(window.mediaForge || {}, { dispatch });

function waitForPywebview(timeoutMs) {
  return new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.ready) return resolve(true);
    const t = setTimeout(() => {
      window.removeEventListener('pywebviewready', ok);
      resolve(!!(window.pywebview && window.pywebview.api));
    }, timeoutMs);
    function ok() {
      clearTimeout(t);
      resolve(true);
    }
    window.addEventListener('pywebviewready', ok, { once: true });
  });
}

/** Connect to the backend and return the initial State (BRIDGE_API 3.1). */
export async function connect() {
  const params = new URLSearchParams(location.search);
  const forceMock = params.get('mock') === '1';
  let real = false;
  if (!forceMock) real = await waitForPywebview(1500);
  if (real) {
    backend = window.pywebview.api;
    mode = 'pywebview';
  } else {
    const mock = await import('./mock.js');
    backend = mock.createMock({ params, dispatch: (n, p) => window.mediaForge.dispatch(n, p) });
    mode = 'mock';
  }
  return call('ready');
}

/** Start delivering events (queued ones first). Call once every component is subscribed. */
export function goLive() {
  const queued = early || [];
  early = null;
  for (const [n, p] of queued) emit(n, p);
}

export const backendMode = () => mode;

/** The mock's development hooks (underscored, not part of the contract); null with the real shell. */
export function mockHooks() {
  return mode === 'mock' ? backend : null;
}

/** Call a bridge method. A rejection carries one English sentence for the user. */
export async function call(method, ...args) {
  if (!backend) throw new Error('The app is not connected yet.');
  const fn = backend[method];
  if (typeof fn !== 'function') throw new Error(`${method} is not available.`);
  try {
    return await fn.apply(backend, args);
  } catch (err) {
    throw normalizeError(err);
  }
}

function normalizeError(err) {
  if (err instanceof Error) return err;
  if (err && typeof err === 'object') return new Error(err.message || err.error || JSON.stringify(err));
  return new Error(String(err));
}

let reporting = false;
/** UI diagnostics into the app log (`log(level, message)`); never throws, never recurses. */
export function reportError(message, level = 'error') {
  if (!backend || reporting) return;
  reporting = true;
  Promise.resolve()
    .then(() => backend.log && backend.log(level, String(message).slice(0, 2000)))
    .catch(() => {})
    .finally(() => (reporting = false));
}
