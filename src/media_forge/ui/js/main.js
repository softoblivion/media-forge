// Entry point: scene, material, bridge, components, keyboard, entrance.

import { connect, call, on, goLive, backendMode, reportError } from './api.js';
import { store, notify, watch } from './store.js';
import { initBackground, setFocused } from './background.js';
import { initLight, gel, Lens } from './glass.js';
import { SPRINGS, springAnimate, reducedMotion } from './motion.js';
import { topOverlay } from './overlay.js';
import { icon, appGlyph } from './icons.js';
import { initToasts, toast } from './toast.js';
import { initList, addItems, removeItem, focusedCardId, collapseExpanded, getItems } from './list.js';
import { initFooter, pickFiles, start, footerMenuOpen } from './footer.js';
import { initSettings } from './settings.js';
import { initUpdate } from './update.js';
import { initDropzone } from './dropzone.js';
import { initSound } from './sound.js';

function initTitlebar() {
  document.getElementById('tb-glyph').innerHTML = appGlyph(22);
  const cluster = document.getElementById('tb-cluster');
  new Lens(cluster, 'capsule');
  document.getElementById('btn-settings').innerHTML = icon('gear');
  document.getElementById('win-min').innerHTML = icon('min');
  document.getElementById('win-close').innerHTML = icon('close');
  for (const b of cluster.querySelectorAll('button')) gel(b, { amount: 0.7 });
  const win = { 'win-min': 'window_minimize', 'win-close': 'window_close' };
  for (const [id, method] of Object.entries(win)) {
    document.getElementById(id).addEventListener('click', () => call(method).catch((err) => toast('error', err.message)));
  }
}

function initEmpty() {
  const empty = document.getElementById('empty');
  const card = empty.querySelector('.empty-card');
  card.__emptyLens = new Lens(card, 'panel');
  const btn = document.getElementById('btn-choose');
  btn.innerHTML = `${icon('plus')}<span>Choose files</span>`;
  gel(btn);
  btn.addEventListener('click', pickFiles);
  const sync = () => {
    const on = store.items.size === 0;
    if (on === !empty.hidden) return;
    empty.hidden = !on;
    card.__emptyLens.setStrength(0);
    if (on) {
      card.__emptyLens.update();
      if (reducedMotion.matches) card.__emptyLens.setStrength(1);
      else {
        card.__emptyLens.animateStrength(1);
        springAnimate(card, [{ opacity: 0, transform: 'translateY(18px) scale(0.96)' }, { opacity: 1, transform: 'none' }], SPRINGS.bouncy);
      }
    }
  };
  watch('items', sync);
  sync();
}

/** FFmpeg status banner above the list (Capabilities.ffmpeg). */
function initCapsBanner() {
  const el = document.getElementById('caps-banner');
  const render = () => {
    const c = store.caps || {};
    const map = {
      probing: ['busy', 'Looking for FFmpeg…'],
      downloading: ['busy', 'Downloading FFmpeg. Files can be added meanwhile.'],
      missing: ['bad', 'FFmpeg is missing and could not be downloaded. Video and audio files cannot be processed.'],
    };
    const m = map[c.ffmpeg];
    el.hidden = !m;
    if (!m) return;
    el.dataset.kind = m[0];
    el.innerHTML = `${m[0] === 'busy' ? '<span class="spinner" aria-hidden="true"></span>' : icon('warn')}<span>${m[1]}</span>`;
  };
  watch('caps', render);
  render();
}

/** Ctrl+V of copied file paths ("Copy as path" in Explorer) adds them through add_files. */
function initPastePaths() {
  addEventListener('paste', async (e) => {
    if (e.target.closest && e.target.closest('input, textarea')) return;
    const text = e.clipboardData && e.clipboardData.getData('text');
    if (!text) return;
    const paths = text.split(/\r?\n/).map((l) => l.trim().replace(/^"(.*)"$/, '$1')).filter((l) => /^(?:[A-Za-z]:\\|\\\\)[^<>"|?*]+$/.test(l));
    if (!paths.length) return;
    e.preventDefault();
    try {
      addItems(await call('add_files', paths));
    } catch (err) {
      toast('error', err.message);
    }
  });
}

function initKeys() {
  addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && !e.shiftKey && !e.altKey && (e.key === 'o' || e.key === 'O')) {
      e.preventDefault();
      if (!topOverlay()) pickFiles();
      return;
    }
    if (mod && e.key === 'Enter') {
      e.preventDefault();
      if (!topOverlay()) start();
      return;
    }
    if (e.key === 'Delete' && !mod && !topOverlay()) {
      const id = focusedCardId();
      if (id) {
        e.preventDefault();
        removeItem(id);
      }
      return;
    }
    if (e.key === 'Escape' && !topOverlay() && !footerMenuOpen() && !e.defaultPrevented) {
      if (collapseExpanded()) e.preventDefault();
    }
  });
}

function entrance() {
  if (reducedMotion.matches) return;
  const parts = [
    [document.querySelector('.tb-brand'), 0],
    [document.getElementById('ver-pill'), 50],
    [document.getElementById('tb-cluster'), 90],
  ];
  for (const [el, delay] of parts) {
    if (!el) continue;
    const own = !!el.__lens;
    springAnimate(el, [
      { opacity: own ? 0 : 1, transform: 'translateY(-10px) scale(0.96)' },
      { opacity: 1, transform: 'none' },
    ], SPRINGS.bouncy, { delay, fill: 'backwards' });
    if (own) {
      el.__lens.setStrength(0);
      setTimeout(() => el.__lens.animateStrength(1), delay);
    }
  }
}

async function boot() {
  initBackground();
  initLight();
  initToasts();
  initTitlebar();

  let state;
  try {
    state = await connect();
  } catch (err) {
    console.error(err);
    toast('error', `Could not connect to the app: ${err.message}`);
    return;
  }
  document.documentElement.dataset.backend = backendMode();
  store.version = state.version || '';
  store.options = state.options;
  store.caps = state.capabilities || null;
  store.settings = state.settings;

  initSettings(state);
  initList(state);
  initFooter();
  initEmpty();
  initCapsBanner();
  initUpdate(state, { activeJobs: () => getItems().some((i) => i.status === 'running' || i.status === 'queued') });
  initDropzone();
  initSound();
  initPastePaths();
  initKeys();

  on('capabilities', (c) => {
    if (!c) return;
    store.caps = c;
    notify('caps', c);
  });
  on('toast', (t) => t && toast(t.kind || 'info', t.message));
  on('window', (w) => w && setFocused(!!w.focused));
  if (state.window) setFocused(state.window.focused !== false);

  goLive();
  document.body.classList.add('is-ready');
  entrance();
  if (backendMode() === 'mock') console.info('[ui] running against js/mock.js');
}

addEventListener('error', (e) => reportError(`${e.message} (${e.filename || ''}:${e.lineno || 0})`));
addEventListener('unhandledrejection', (e) => {
  const msg = e.reason && e.reason.message ? e.reason.message : String(e.reason);
  reportError(`unhandled rejection: ${msg}`);
  toast('error', msg);
});

boot();
