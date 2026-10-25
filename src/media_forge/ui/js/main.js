// Entry point: scene, material, bridge, components, keyboard, entrance.

import { connect, call, on, goLive, backendMode, reportError } from './api.js';
import { store, notify, watch } from './store.js';
import { initBackground, setFocused } from '../vendor/liquid_ui/js/background.js';
import { applyTheme } from '../vendor/liquid_ui/js/theme.js';
import { initLight, gel, Lens } from '../vendor/liquid_ui/js/glass.js';
import { SPRINGS, springAnimate, reducedMotion } from '../vendor/liquid_ui/js/motion.js';
import { topOverlay } from '../vendor/liquid_ui/js/overlay.js';
import { icon, appGlyph } from './icons.js';
import { initToasts, toast } from '../vendor/liquid_ui/js/toast.js';
import { initList, addItems, removeItem, focusedCardId, collapseExpanded, getItems } from './list.js';
import { initFooter, pickFiles, start, footerMenuOpen } from './footer.js';
import { initSettings, toggleSettings } from './settings.js';
import { mountUpdateControl } from '../vendor/liquid_ui/js/update.js';
import { mountTitlebar } from '../vendor/liquid_ui/js/titlebar.js';
import { initDropzone } from './dropzone.js';
import { initSound } from './sound.js';

// The theme saved with the settings (BRIDGE_API 3.5 `theme`): applied through the kit on every
// confirmed settings object and mirrored in localStorage so index.html sets it before the first
// paint. The scene reads the theme tokens itself (the kit's `deep` treatment); docs/UI_DESIGN.md 5.
const THEME_KEY = 'media-forge.theme';

function syncTheme(settings) {
  if (!settings) return;
  const name = applyTheme(settings.theme);
  try {
    localStorage.setItem(THEME_KEY, name);
  } catch {
    /* storage unavailable: the next launch starts on the default and switches after connect */
  }
}

/** Version pill, update popover and toasts from the kit; the store keeps the last status. */
function initUpdate(state) {
  const busy = () => getItems().some((i) => i.status === 'running' || i.status === 'queued');
  const ctl = mountUpdateControl({
    pill: document.getElementById('ver-pill'),
    popoverHost: document.getElementById('update-pop'),
    call,
    on,
    options: {
      product: 'Media Forge',
      version: state.version || '',
      status: state.update || null,
      settings: state.settings,
      hasActiveWork: busy,
      restartWarning: 'Files in progress stop when the app restarts',
      onStatus: (u) => {
        store.update = u;
        notify('update', u);
      },
      onSettings: (s) => {
        store.settings = s;
        notify('settings', s);
      },
    },
  });
  // Settings confirmed elsewhere (the `settings` event, the sheet's own saves) reach the control.
  watch('settings', (s) => ctl.setSettings(s));
  return ctl;
}

let titlebar = null;

/** The kit's title bar: glyph, name and version pill on the left, settings | minimize, close on the right. */
function initTitlebar() {
  const win = (method) => () => call(method).catch((err) => toast('error', err.message));
  titlebar = mountTitlebar(document.getElementById('titlebar'), {
    name: 'Media Forge',
    glyph: appGlyph(24),
    onSettings: () => toggleSettings(),
    onMinimize: win('window_minimize'),
    onClose: win('window_close'),
  });
  titlebar.gear.id = 'btn-settings';
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
    [titlebar.left, 0],
    [titlebar.pill, 50],
    [titlebar.cluster, 90],
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
  initBackground(document.getElementById('scene'));
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
  syncTheme(state.settings);
  watch('settings', syncTheme);

  const update = initUpdate(state);
  initSettings(state, update);
  initList(state);
  initFooter();
  initEmpty();
  initCapsBanner();
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
