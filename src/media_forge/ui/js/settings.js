// Settings sheet (Settings, BRIDGE_API 3.5): appearance (the kit's theme row), output, processing,
// defaults for new files (the same panels as the cards), engine status (Capabilities 3.6) and
// updates. Grows out of the gear.
//
// Saves are optimistic with a guard: while a save of a key is in flight, neither its answer nor a
// `settings` event moves that control; a refusal shows the shell's sentence and the `settings`
// event that follows restores the old value.

import { call, on } from './api.js';
import { store, notify, watch } from './store.js';
import { Overlay } from '../vendor/liquid_ui/js/overlay.js';
import { gel } from '../vendor/liquid_ui/js/glass.js';
import { Segmented, Switch, Stepper } from '../vendor/liquid_ui/js/controls.js';
import { applyTheme, mountThemeControl } from '../vendor/liquid_ui/js/theme.js';
import { mountScaleControl } from '../vendor/liquid_ui/js/scale.js';
import { SettingsPanel } from './panels.js';
import { icon } from './icons.js';
import { escapeHtml, KIND_LABEL } from './format.js';
import { toast } from '../vendor/liquid_ui/js/toast.js';

let sheet, overlay, content;
let ctl = {};
let defaultsPanel = null;
let defaultsKind = 'video';
let update = null;      // the kit's update control (main.js); renders the "Updates" rows
let updateRows = null;
const inflight = new Map(); // key -> count

export function initSettings(state, updateControl) {
  update = updateControl;
  store.settings = state.settings;
  sheet = document.getElementById('settings-sheet');
  content = sheet.querySelector('.ov-content');
  overlay = new Overlay(sheet, { modal: true, preset: 'sheet', onClose: teardown });
  on('settings', (s) => {
    if (!s) return;
    store.settings = s;
    notify('settings', s);
    if (overlay.open) refresh(s);
  });
  watch('caps', () => overlay.open && renderEngine());
}

export function settingsOpen() {
  return overlay && overlay.open;
}

/** The gear of the title bar: open the sheet, or close it when it is open. */
export function toggleSettings() {
  if (overlay.open) overlay.close();
  else openSettings();
}

export async function openSettings() {
  build();
  overlay.show(document.getElementById('btn-settings'));
  // Fresh values from the shell (another window state may have changed them).
  try {
    const [s, caps] = await Promise.all([call('get_settings'), call('capabilities')]);
    if (s) {
      store.settings = s;
      refresh(s);
    }
    if (caps) {
      store.caps = caps;
      notify('caps', caps);
    }
  } catch (err) {
    toast('error', err.message);
  }
}

function teardown() {
  // Lenses inside the sheet (droplets) are released once the close animation has run.
  setTimeout(() => {
    if (overlay.open) return;
    destroyControls();
    content.innerHTML = '';
  }, 600);
}

function destroyControls() {
  for (const c of Object.values(ctl)) c && c.destroy && c.destroy();
  ctl = {};
  if (updateRows) updateRows.destroy();
  updateRows = null;
  if (defaultsPanel) defaultsPanel.destroy();
  defaultsPanel = null;
}

// ---------------------------------------------------------------------------------------------

async function save(key, patch, revert) {
  inflight.set(key, (inflight.get(key) || 0) + 1);
  try {
    const s = await call('set_settings', patch);
    if (s) {
      store.settings = s;
      notify('settings', s);  // main.js applies the theme; the update control takes the confirmed values
    }
    return s;
  } catch (err) {
    toast('error', err.message);
    revert && revert();
    return null;
  } finally {
    inflight.set(key, inflight.get(key) - 1);
    if (store.settings && overlay.open) refresh(store.settings, key);
  }
}

const busy = (k) => (inflight.get(k) || 0) > 0;

function group(title, inner, cls = '') {
  return `<section class="set-group ${cls}"><h3>${escapeHtml(title)}</h3><div class="set-card">${inner}</div></section>`;
}

function rowHtml(id, label, sub = '', extra = '') {
  return `<div class="set-row ${extra}" data-row="${id}"><div class="set-label"><span>${escapeHtml(label)}</span>${sub ? `<small>${sub}</small>` : ''}</div><div class="set-ctl" data-slot="${id}"></div></div>`;
}

function build() {
  destroyControls();
  const s = store.settings;
  content.innerHTML = `
    <div class="sheet-head">
      <h2 id="settings-title">Settings</h2>
      <button type="button" class="icon-btn sheet-x" data-act="close" aria-label="Close settings">${icon('close')}</button>
    </div>
    <div class="sheet-scroll">
      ${group('Appearance', '', 'set-appearance')}
      ${group('Output', `
        ${rowHtml('output_mode', 'Save to', '', 'set-row-col')}
        <div class="set-row set-row-col" data-row="output_dir">
          <div class="set-path"><span class="path-text mono" data-path></span><button type="button" class="btn btn-s" data-act="pick-dir">${icon('folder')}<span>Choose…</span></button><button type="button" class="icon-btn" data-act="open-dir" aria-label="Open the output folder" title="Open the output folder">${icon('open')}</button></div>
        </div>
        <div class="set-row" data-row="suffix">
          <div class="set-label"><span>File name suffix</span><small data-suffix-example></small></div>
          <div class="set-ctl"><input class="field field-s mono" id="suffix-in" maxlength="24" spellcheck="false" autocomplete="off" aria-label="File name suffix"></div>
        </div>`)}
      ${group('Processing', `
        ${rowHtml('parallel_jobs', 'Files at the same time', 'More than one helps with many small files')}
        ${rowHtml('open_when_done', 'Open the folder when done', 'After a batch finishes')}
        ${rowHtml('sound_when_done', 'Sound when done', 'A short sound after a batch')}`)}
      <section class="set-group">
        <h3>Defaults for new files</h3>
        <div class="set-card set-defaults">
          <div class="set-row set-row-col"><div data-slot="defaults-kind"></div><div class="set-hint">Files added from now on start with these settings. Each file can still be changed on its own.</div></div>
          <div class="set-panel" data-slot="defaults-panel"></div>
        </div>
      </section>
      ${group('Engine', '<div class="eng-rows" data-engine></div>')}
      ${group('Updates', '', 'set-updates')}
      <p class="set-about" data-about></p>
    </div>`;

  content.querySelector('[data-act="close"]').addEventListener('click', () => overlay.close());
  content.addEventListener('click', onClick);

  // Theme: applied at once, saved like every other key; a refusal restores the confirmed theme.
  ctl.theme = mountThemeControl(content.querySelector('.set-appearance .set-card'), {
    value: s.theme,
    onChange: (name) => {
      applyTheme(name);
      save('theme', { theme: name }, () => {
        ctl.theme.set(store.settings.theme);
        applyTheme(store.settings.theme);
      });
    },
  });

  // Scale: the shell applies the step to the window at once (set_scale) and saves it as ui_scale.
  ctl.ui_scale = mountScaleControl(content.querySelector('.set-appearance .set-card'), {
    value: s.ui_scale,
    onChange: async (v) => {
      inflight.set('ui_scale', (inflight.get('ui_scale') || 0) + 1);
      try {
        const next = await call('set_scale', v);
        if (next) {
          store.settings = next;
          notify('settings', next);
        }
      } catch (err) {
        toast('error', err.message);
      } finally {
        inflight.set('ui_scale', inflight.get('ui_scale') - 1);
        if (store.settings && overlay.open) refresh(store.settings, 'ui_scale');
      }
    },
  });

  // Output mode.
  ctl.output_mode = new Segmented({
    label: 'Save to',
    cls: 'seg-p seg-wide',
    options: [{ value: 'beside', label: 'Beside the source' }, { value: 'folder', label: 'One folder' }],
    value: s.output_mode,
    onChange: (v) => setOutputMode(v),
  });
  slot('output_mode').append(ctl.output_mode.el);

  // Suffix.
  const suffix = content.querySelector('#suffix-in');
  const commitSuffix = () => {
    const v = suffix.value;
    if (v === store.settings.suffix) return;
    const old = store.settings.suffix;
    save('suffix', { suffix: v }, () => (suffix.value = old));
  };
  suffix.addEventListener('change', commitSuffix);
  suffix.addEventListener('input', () => exampleName(suffix.value));
  suffix.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') suffix.blur();
    if (e.key === 'Escape') {
      suffix.value = store.settings.suffix;
      exampleName(suffix.value);
      e.stopPropagation();
      suffix.blur();
    }
  });

  // Processing.
  ctl.parallel_jobs = new Stepper({ label: 'Files at the same time', value: s.parallel_jobs, min: 1, max: 3, onChange: (v) => save('parallel_jobs', { parallel_jobs: v }) });
  slot('parallel_jobs').append(ctl.parallel_jobs.el);
  for (const k of ['open_when_done', 'sound_when_done']) {
    ctl[k] = new Switch({ label: content.querySelector(`[data-row="${k}"] .set-label span`).textContent, checked: s[k], onChange: (v) => save(k, { [k]: v }) });
    slot(k).append(ctl[k].el);
  }

  // Defaults per kind.
  ctl.defaultsKind = new Segmented({
    label: 'Kind',
    cls: 'seg-p',
    options: ['video', 'image', 'audio'].map((k) => ({ value: k, label: KIND_LABEL[k] })),
    value: defaultsKind,
    onChange: (v) => {
      defaultsKind = v;
      buildDefaultsPanel();
    },
  });
  slot('defaults-kind').append(ctl.defaultsKind.el);
  buildDefaultsPanel();

  content.querySelectorAll('.btn, .icon-btn').forEach((b) => gel(b));

  // Updates: status line with the next action, automatic download (the kit's rows).
  if (update) updateRows = update.mountSettings(content.querySelector('.set-updates .set-card'));

  refresh(s);
  renderEngine();
}

const slot = (id) => content.querySelector(`[data-slot="${id}"]`);

function buildDefaultsPanel() {
  if (defaultsPanel) defaultsPanel.destroy();
  const kind = defaultsKind;
  const box = slot('defaults-panel');
  defaultsPanel = new SettingsPanel({
    kind,
    settings: store.settings.defaults[kind],
    onPatch: async (patch) => {
      inflight.set('defaults', (inflight.get('defaults') || 0) + 1);
      try {
        const s = await call('set_settings', { defaults: { [kind]: patch } });
        if (s) store.settings = s;
        return s ? s.defaults[kind] : null;
      } finally {
        inflight.set('defaults', inflight.get('defaults') - 1);
      }
    },
  });
  box.innerHTML = '';
  box.append(defaultsPanel.el);
}

async function setOutputMode(v) {
  const s = store.settings;
  if (v === 'folder' && !s.output_dir) {
    // No folder yet: pick one first; cancelling keeps "Beside the source".
    const dir = await pickDir();
    if (!dir) {
      ctl.output_mode.set(s.output_mode);
      return;
    }
    return save('output_mode', { output_mode: 'folder', output_dir: dir }, () => ctl.output_mode.set(store.settings.output_mode));
  }
  return save('output_mode', { output_mode: v }, () => ctl.output_mode.set(store.settings.output_mode));
}

async function pickDir() {
  try {
    return await call('pick_output_dir');
  } catch (err) {
    toast('error', err.message);
    return null;
  }
}

function exampleName(suffix) {
  const ex = content.querySelector('[data-suffix-example]');
  if (!ex) return;
  ex.innerHTML = `holiday.mp4 → <b class="mono">holiday${escapeHtml(suffix)}.mp4</b>`;
}

/** Show confirmed settings; controls with a save in flight keep their local value. */
function refresh(s, justSaved = null) {
  if (!content.firstElementChild) return;
  if (!busy('theme') && ctl.theme) ctl.theme.set(s.theme);
  if (!busy('ui_scale') && ctl.ui_scale) ctl.ui_scale.set(s.ui_scale);
  if (!busy('output_mode') && ctl.output_mode) ctl.output_mode.set(s.output_mode);
  const dirRow = content.querySelector('[data-row="output_dir"]');
  dirRow.hidden = s.output_mode !== 'folder';
  const path = content.querySelector('[data-path]');
  path.textContent = s.output_dir || 'No folder chosen';
  path.title = s.output_dir || '';
  const suffix = content.querySelector('#suffix-in');
  if (document.activeElement !== suffix && !busy('suffix')) suffix.value = s.suffix;
  exampleName(document.activeElement === suffix ? suffix.value : s.suffix);
  const sufRow = content.querySelector('[data-row="suffix"] small');
  if (sufRow && s.output_mode === 'folder' && !s.suffix) sufRow.innerHTML += ' <span class="set-note">(no suffix: the name stays the same)</span>';
  if (!busy('parallel_jobs') && ctl.parallel_jobs) ctl.parallel_jobs.set(s.parallel_jobs);
  for (const k of ['open_when_done', 'sound_when_done']) if (!busy(k) && ctl[k]) ctl[k].set(s[k]);
  if (!busy('defaults') && defaultsPanel && justSaved !== 'defaults') defaultsPanel.external(s.defaults[defaultsKind]);
  content.querySelector('[data-about]').textContent = `Media Forge ${store.version || ''}`.trim();
}

function renderEngine() {
  const box = content && content.querySelector('[data-engine]');
  if (!box) return;
  const c = store.caps || {};
  const ffState = { ready: 'Ready', probing: 'Looking for FFmpeg…', downloading: 'Downloading…', missing: 'Missing' }[c.ffmpeg] || '—';
  const nv = c.nvenc || {};
  const nvList = ['h264', 'h265', 'av1'].filter((k) => nv[k]).map((k) => ({ h264: 'H.264', h265: 'HEVC', av1: 'AV1' })[k]);
  const model = (st) => ({ ready: 'Ready', missing: 'Downloaded on first use', downloading: 'Downloading…', error: 'Download failed' })[st] || '—';
  const imgs = [c.avif ? 'AVIF' : null, c.heif ? 'HEIC' : null].filter(Boolean);
  const rows = [
    ['FFmpeg', c.ffmpeg_version ? `${c.ffmpeg_version}` : '', ffState, c.ffmpeg === 'ready' ? 'ok' : c.ffmpeg === 'missing' ? 'bad' : 'busy', c.ffmpeg_path || ''],
    ['GPU', c.gpu_name || 'No NVIDIA GPU found', nvList.length ? `NVENC: ${nvList.join(', ')}` : 'CPU encoding only', nvList.length ? 'ok' : 'off', ''],
    ['FSRCNN', '2× · 4×', model(c.models && c.models.fsrcnn), c.models && c.models.fsrcnn === 'ready' ? 'ok' : 'off', ''],
    ['EDSR', '2× · 4×', model(c.models && c.models.edsr), c.models && c.models.edsr === 'ready' ? 'ok' : c.models && c.models.edsr === 'error' ? 'bad' : 'off', ''],
    ['Image formats', imgs.length ? `${imgs.join(', ')} available` : 'AVIF and HEIC unavailable', '', imgs.length === 2 ? 'ok' : 'off', ''],
  ];
  box.innerHTML = rows.map(([name, val, st, cls, title]) => `
    <div class="eng-row is-${cls}"${title ? ` title="${escapeHtml(title)}"` : ''}>
      <i class="eng-dot" aria-hidden="true"></i>
      <span class="eng-name">${escapeHtml(name)}</span>
      <span class="eng-val">${escapeHtml(val)}</span>
      <span class="eng-st">${escapeHtml(st)}</span>
    </div>`).join('');
}

async function onClick(e) {
  const b = e.target.closest('[data-act]');
  if (!b) return;
  switch (b.dataset.act) {
    case 'pick-dir': {
      const dir = await pickDir();
      if (dir) save('output_dir', { output_dir: dir, output_mode: 'folder' });
      return;
    }
    case 'open-dir':
      if (store.settings.output_dir) call('open_path', store.settings.output_dir).catch((err) => toast('error', err.message));
      return;
    default:
  }
}
