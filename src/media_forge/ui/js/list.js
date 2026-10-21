// File list: one `.glass-lite` card per item (any number of them; no lenses on cards).
//
// A card shows the thumbnail, name, kind chip, the probe line (resolution · duration · codec ·
// size) and one state line: the plan (ready), Queued with Start now (start_now) and Cancel, stage /
// percent / speed / ETA over a liquid fill (running), output size and ratio with Show in folder /
// Open (done), the message with Retry (error). The head carries a trash button (remove_item, which
// also cancels a running file) while the card is collapsed, then the chevron.
// Clicking a card expands it (spring height) into its settings panel (js/panels.js);
// one card is expanded at a time, and the panel is built on expand and destroyed on collapse, so
// its droplet lenses exist only while it is open.

import { call, on, emit } from './api.js';
import { store, notify } from './store.js';
import { SettingsPanel, planSummary } from './panels.js';
import { springAnimate, springEasing, SPRINGS, whenDone, reducedMotion } from '../vendor/liquid_ui/js/motion.js';
import { gel } from '../vendor/liquid_ui/js/glass.js';
import { icon, KIND_ICON } from './icons.js';
import { bytes, duration, codecName, sampleRate, channelsName, eta, percent, escapeHtml, KIND_LABEL, KIND_ALL, nKind } from './format.js';
import { toast } from '../vendor/liquid_ui/js/toast.js';

const EXPAND = { response: 0.46, damping: 0.86 };
const STAGE = {
  analyzing: 'Analyzing',
  'pass 1': 'Pass 1 of 2',
  'pass 2': 'Pass 2 of 2',
  encoding: 'Encoding',
  upscaling: 'Upscaling',
  'downloading model': 'Downloading model',
  writing: 'Writing',
  retrying: 'Retrying to fit the size limit',
};

let listEl;
let scrollEl;
const cards = new Map(); // id -> { el, parts, item, panel, stateKey }
let expandedId = null;

export function initList(state) {
  listEl = document.getElementById('list');
  scrollEl = document.getElementById('list-scroll');
  for (const it of state.items || []) upsert(it, { animate: false });
  changed();

  on('item', (it) => it && upsert(it));
  on('items_added', (p) => {
    const list = (p && p.items) || [];
    list.forEach((it, i) => upsert(it, { delay: i * 40 }));
    if (list.length) revealNew(list[0].id);
  });
  on('item_removed', (p) => p && removeCard(p.id));

  listEl.addEventListener('click', onClick);
  listEl.addEventListener('keydown', onKey);
}

/** Items returned by pick_files / add_files: added like the items_added event. */
export function addItems(list) {
  (list || []).forEach((it, i) => upsert(it, { delay: i * 40 }));
  if (list && list.length) revealNew(list[0].id);
}

export function getItems() {
  return Array.from(store.items.values());
}

function changed() {
  notify('items');
  emit('ui:items');
}

function revealNew(id) {
  const c = cards.get(id);
  if (!c) return;
  setTimeout(() => c.el.scrollIntoView({ block: 'nearest', behavior: reducedMotion.matches ? 'auto' : 'smooth' }), 60);
}

// ---------------------------------------------------------------------------------------------
// Cards

function upsert(it, { animate = true, delay = 0 } = {}) {
  const prev = store.items.get(it.id);
  store.items.set(it.id, it);
  let c = cards.get(it.id);
  if (!c) {
    c = createCard(it);
    cards.set(it.id, c);
    // Keep the list in the order the files were added: `item` events of a batch arrive as each probe finishes.
    let before = null;
    for (const [oid, oc] of cards) {
      if (oid === it.id) continue;
      const other = store.items.get(oid);
      if (!other || (other.added_at || 0) <= (it.added_at || 0)) continue;
      if (!before || other.added_at < before.at) before = { el: oc.el, at: other.added_at };
    }
    listEl.insertBefore(c.el, before ? before.el : null);
    if (animate && !reducedMotion.matches) {
      springAnimate(c.el, [
        { opacity: 0, transform: 'translateY(16px) scale(0.97)' },
        { opacity: 1, transform: 'none' },
      ], SPRINGS.bouncy, { delay, fill: 'backwards' });
    }
  }
  render(c, it, prev);
  changed();
}

function createCard(it) {
  const el = document.createElement('article');
  el.className = 'card glass-lite';
  el.dataset.id = it.id;
  el.tabIndex = 0;
  el.setAttribute('role', 'listitem');
  el.innerHTML = `
    <div class="card-liquid" aria-hidden="true"><i></i></div>
    <div class="card-head">
      <div class="card-thumb"><span class="thumb-ph">${icon(KIND_ICON[it.kind] || 'file')}</span></div>
      <div class="card-body">
        <div class="card-l1"><span class="card-name"></span><span class="kind-chip"></span></div>
        <div class="card-meta tnum"></div>
        <div class="card-state"></div>
      </div>
      <div class="card-tools">
        <button type="button" class="icon-btn card-del" data-act="remove" aria-label="Remove" title="Remove">${icon('trash')}</button>
        <button type="button" class="icon-btn card-exp" data-act="toggle" aria-expanded="false" aria-label="File settings">${icon('chevronDown')}</button>
      </div>
    </div>
    <div class="card-drawer" hidden></div>
    <span class="lg-rim" aria-hidden="true"></span>`;
  const parts = {
    thumb: el.querySelector('.card-thumb'),
    name: el.querySelector('.card-name'),
    chip: el.querySelector('.kind-chip'),
    meta: el.querySelector('.card-meta'),
    state: el.querySelector('.card-state'),
    exp: el.querySelector('.card-exp'),
    del: el.querySelector('.card-del'),
    drawer: el.querySelector('.card-drawer'),
    liquid: el.querySelector('.card-liquid'),
  };
  gel(parts.del);
  return { el, parts, item: null, panel: null, stateKey: '' };
}

function metaLine(it) {
  const p = it.probe;
  const out = [];
  if (p) {
    if (p.width && p.height) out.push(`${p.width}×${p.height}`);
    if (p.duration != null && it.kind !== 'image') out.push(duration(p.duration));
    if (it.kind === 'video' && p.vcodec) out.push(codecName(p.vcodec));
    if (it.kind === 'image' && p.format) out.push(p.format);
    if (it.kind === 'audio') {
      if (p.acodec) out.push(codecName(p.acodec));
      if (p.sample_rate) out.push(sampleRate(p.sample_rate));
      if (p.channels) out.push(channelsName(p.channels));
    }
  }
  if (it.size) out.push(bytes(it.size));
  return out.join(' · ');
}

function render(c, it, prev) {
  const { el, parts } = c;
  c.item = it;
  el.dataset.status = it.status;
  el.dataset.kind = it.kind;
  el.setAttribute('aria-label', `${it.name}, ${statusWord(it)}`);
  if (!prev || prev.name !== it.name) {
    parts.name.textContent = it.name;
    parts.name.title = it.path;
  }
  parts.chip.textContent = KIND_LABEL[it.kind] || it.kind;
  // A file that could not be read has no reliable kind: no chip.
  parts.chip.hidden = it.status === 'error' && !it.probe;
  const thumbKey = it.thumb || `ph:${it.status === 'error' && !it.probe ? 'file' : it.kind}`;
  if (c.thumbKey !== thumbKey) {
    c.thumbKey = thumbKey;
    if (it.thumb) {
      parts.thumb.innerHTML = `<img alt="" src="${it.thumb}" draggable="false">`;
      const img = parts.thumb.firstChild;
      img.decode && img.decode().catch(() => {});
      if (prev && !prev.thumb && !reducedMotion.matches) img.animate([{ opacity: 0, filter: 'blur(6px)' }, { opacity: 1, filter: 'blur(0)' }], { duration: 420, easing: 'ease-out' });
    } else {
      const glyph = it.status === 'error' && !it.probe ? 'file' : KIND_ICON[it.kind] || 'file';
      parts.thumb.innerHTML = `<span class="thumb-ph">${icon(glyph)}</span>`;
    }
  }
  parts.meta.textContent = it.status === 'probing' ? '' : metaLine(it);
  parts.meta.classList.toggle('is-probing', it.status === 'probing');
  renderState(c, it);
  // Liquid fill: progress of a running file; done flashes full in mint, then fades.
  const p = it.status === 'running' ? it.progress || 0 : it.status === 'done' ? 1 : 0;
  if (prev && prev.status !== 'running' && it.status === 'running') {
    parts.liquid.classList.add('is-instant');
    el.style.setProperty('--p', '0');
    void parts.liquid.offsetWidth;
    parts.liquid.classList.remove('is-instant');
  }
  el.style.setProperty('--p', String(p));
  if (prev && prev.status === 'running' && it.status === 'done') {
    el.classList.add('is-finishing');
    setTimeout(() => el.classList.remove('is-finishing'), 1400);
  }
  // Panel: settings shown as returned; locked unless the file is ready or failed.
  if (c.panel) {
    if (!prev || JSON.stringify(prev.settings) !== JSON.stringify(it.settings)) c.panel.external(it.settings);
    lockPanel(c);
    updateDrawerActions(c);
  }
}

function statusWord(it) {
  return { probing: 'reading', ready: 'ready', queued: 'queued', running: 'processing', done: 'done', error: 'failed' }[it.status] || it.status;
}

function renderState(c, it) {
  const s = c.parts.state;
  const key = `${it.status}|${it.status === 'ready' ? planSummary(it.kind, it.settings) : ''}|${it.status === 'error' ? it.error + !!it.probe : ''}|${it.status === 'done' && it.output ? it.output.path : ''}`;
  if (key !== c.stateKey) {
    c.stateKey = key;
    switch (it.status) {
      case 'probing':
        s.innerHTML = `<span class="st-probe"><i class="sk"></i><span>Reading file…</span></span>`;
        break;
      case 'ready':
        s.innerHTML = `<span class="st-plan">${icon('arrow')}<span>${escapeHtml(planSummary(it.kind, it.settings))}</span></span>`;
        break;
      case 'queued':
        s.innerHTML = `<span class="st-wait">${icon('clock')}<span>Queued</span></span><span class="st-acts"><button type="button" class="btn btn-xs st-btn" data-act="start-now" title="Start this file now, outside the queue">${icon('play')}<span>Start now</span></button><button type="button" class="btn btn-xs btn-ghost st-btn" data-act="cancel">Cancel</button></span>`;
        break;
      case 'running':
        s.innerHTML = `<span class="st-run"><span class="st-stage"></span><b class="st-pct tnum"></b></span><span class="st-speed tnum"></span><button type="button" class="icon-btn st-x" data-act="cancel" aria-label="Cancel" title="Cancel">${icon('x')}</button>`;
        break;
      case 'done': {
        s.innerHTML = `<span class="st-done tnum">${icon('check')}<span class="st-out"></span></span><span class="st-acts"><button type="button" class="btn btn-xs st-btn" data-act="reveal" title="Show in folder">${icon('reveal')}<span>Show in folder</span></button><button type="button" class="btn btn-xs st-btn" data-act="open" title="Open">${icon('open')}<span>Open</span></button><button type="button" class="btn btn-xs st-btn" data-act="rerun" title="Process the file again with its current settings; the earlier output stays">${icon('retry')}<span>Run again</span></button></span>`;
        break;
      }
      case 'error':
        s.innerHTML = `<span class="st-err">${icon('warn')}<span class="st-msg"></span></span>${it.probe ? `<button type="button" class="btn btn-xs st-btn" data-act="retry">${icon('retry')}<span>Retry</span></button>` : ''}`;
        s.querySelector('.st-msg').textContent = it.error || 'The file could not be processed.';
        break;
      default:
        s.textContent = '';
    }
    s.querySelectorAll('.st-btn').forEach((b) => gel(b));
  }
  if (it.status === 'running') {
    s.querySelector('.st-stage').textContent = STAGE[it.stage] || (it.stage ? it.stage[0].toUpperCase() + it.stage.slice(1) : 'Starting');
    s.querySelector('.st-pct').textContent = percent(it.progress || 0);
    const right = [it.speed, eta(it.eta)].filter(Boolean).join(' · ');
    s.querySelector('.st-speed').textContent = right;
  }
  if (it.status === 'done' && it.output) {
    const o = it.output;
    const parts = [`<b>${escapeHtml(bytes(o.size))}</b>`, escapeHtml(percent(o.ratio))];
    const p = it.probe;
    if (o.width && o.height && p && (o.width !== p.width || o.height !== p.height)) parts.push(`${o.width}×${o.height}`);
    s.querySelector('.st-out').innerHTML = parts.join(' · ');
    s.querySelector('.st-done').title = o.path;
  }
}

// ---------------------------------------------------------------------------------------------
// Expand / collapse

function toggle(id) {
  if (expandedId === id) collapse(id);
  else expand(id);
}

function expand(id) {
  const c = cards.get(id);
  if (!c || !store.options) return;
  if (expandedId && expandedId !== id) collapse(expandedId);
  expandedId = id;
  const it = c.item;
  const d = c.parts.drawer;
  c.panel = new SettingsPanel({
    kind: it.kind,
    settings: it.settings,
    onPatch: async (patch) => {
      const item = await call('set_item_settings', id, patch);
      if (item && cards.has(id)) {
        store.items.set(id, item);
        render(c, item, c.item);
        changed();
      }
      return item ? item.settings : null;
    },
  });
  const actions = document.createElement('div');
  actions.className = 'drawer-actions';
  actions.innerHTML = `
    <button type="button" class="btn btn-s" data-act="apply-kind">${icon('layers')}<span></span></button>
    <button type="button" class="btn btn-s btn-ghost btn-danger" data-act="remove">${icon('trash')}<span>Remove</span></button>`;
  d.innerHTML = '';
  d.append(c.panel.el, actions);
  actions.querySelectorAll('.btn').forEach((b) => gel(b));
  lockPanel(c);
  updateDrawerActions(c);
  c.el.classList.add('is-open');
  c.parts.exp.setAttribute('aria-expanded', 'true');
  d.hidden = false;
  if (reducedMotion.matches) return;
  const h = d.scrollHeight;
  const { easing, duration } = springEasing(EXPAND);
  d.style.overflow = 'hidden';
  const a = d.animate([{ height: '0px' }, { height: `${h}px` }], { duration, easing });
  c.panel.el.animate([{ opacity: 0, transform: 'translateY(-8px)' }, { opacity: 1, transform: 'none' }], { duration: 360, easing: 'cubic-bezier(.2,.8,.2,1)', delay: 60, fill: 'backwards' });
  whenDone(a, () => {
    d.style.overflow = '';
    // Keep the opened card in view: scroll just enough to show its panel.
    const r = c.el.getBoundingClientRect();
    const box = scrollEl.getBoundingClientRect();
    const bottomLimit = box.bottom - footerSpace();
    if (r.bottom > bottomLimit) scrollEl.scrollBy({ top: Math.min(r.bottom - bottomLimit + 12, r.top - box.top - 70), behavior: 'smooth' });
  }, 0);
}

function footerSpace() {
  const f = document.getElementById('foot');
  return f && !f.hidden ? f.offsetHeight + 24 : 16;
}

function collapse(id) {
  const c = cards.get(id);
  if (expandedId === id) expandedId = null;
  if (!c || !c.panel) return;
  const d = c.parts.drawer;
  const panel = c.panel;
  c.panel = null;
  c.el.classList.remove('is-open');
  c.parts.exp.setAttribute('aria-expanded', 'false');
  const finish = () => {
    panel.destroy();
    d.hidden = true;
    d.innerHTML = '';
    d.style.overflow = '';
  };
  if (reducedMotion.matches || !d.isConnected) return finish();
  const h = d.offsetHeight;
  d.style.overflow = 'hidden';
  const a = springAnimate(d, [{ height: `${h}px` }, { height: '0px' }], SPRINGS.sheetClose, { fill: 'forwards' });
  whenDone(a, () => {
    a.cancel();
    finish();
  }, 0);
}

export function collapseExpanded() {
  if (expandedId) {
    collapse(expandedId);
    return true;
  }
  return false;
}

function lockPanel(c) {
  if (!c.panel) return;
  const st = c.item.status;
  const note = {
    queued: 'Queued. Cancel the file to change its settings.',
    running: 'The file is being processed.',
    probing: 'Reading the file…',
  }[st];
  c.panel.setLocked(!!note, note || '');
}

function updateDrawerActions(c) {
  const btn = c.parts.drawer.querySelector('[data-act="apply-kind"]');
  if (!btn) return;
  const it = c.item;
  const others = getItems().filter((x) => x.id !== it.id && x.kind === it.kind && x.status !== 'running' && x.status !== 'queued').length;
  btn.querySelector('span').textContent = `Apply to all ${KIND_ALL[it.kind]}`;
  btn.disabled = others === 0 || it.status === 'probing';
  btn.title = others ? `Copy these settings to ${nKind(others, it.kind)} that are not running` : `No other ${KIND_ALL[it.kind]} in the list`;
}

// ---------------------------------------------------------------------------------------------
// Remove

function removeCard(id) {
  const c = cards.get(id);
  store.items.delete(id);
  if (!c) return changed();
  cards.delete(id);
  if (expandedId === id) expandedId = null;
  if (c.panel) {
    c.panel.destroy();
    c.panel = null;
  }
  const el = c.el;
  const wasFocused = el.contains(document.activeElement);
  const next = el.nextElementSibling || el.previousElementSibling;
  changed();
  if (wasFocused && next) next.focus({ preventScroll: true });
  if (reducedMotion.matches) return el.remove();
  el.style.pointerEvents = 'none';
  const h = el.offsetHeight;
  const a = el.animate([
    { height: `${h}px`, opacity: 1, transform: 'scale(1)', marginBottom: '0px' },
    { height: `${h}px`, opacity: 0, transform: 'scale(0.96)', offset: 0.4 },
    { height: '0px', opacity: 0, transform: 'scale(0.96)', marginBottom: 'calc(var(--card-gap) * -1)' },
  ], { duration: 420, easing: 'cubic-bezier(.3,.7,.2,1)', fill: 'forwards' });
  whenDone(a, () => el.remove(), 0);
}

export async function removeItem(id) {
  try {
    await call('remove_item', id);
  } catch (err) {
    toast('error', err.message);
  }
}

// ---------------------------------------------------------------------------------------------
// Events

async function onClick(e) {
  const card = e.target.closest('.card');
  if (!card) return;
  const id = card.dataset.id;
  const c = cards.get(id);
  if (!c) return;
  const act = e.target.closest('[data-act]');
  const fail = (err) => toast('error', err.message);
  if (act) {
    e.stopPropagation();
    const it = c.item;
    switch (act.dataset.act) {
      case 'toggle':
        return toggle(id);
      case 'cancel':
        return call('cancel', id).catch(fail);
      case 'start-now':
        return call('start_now', id).then((ok) => ok || toast('error', 'The file could not be started.')).catch(fail);
      case 'reveal':
        return it.output && call('reveal', it.output.path).catch(fail);
      case 'open':
        return it.output && call('open_path', it.output.path).catch(fail);
      case 'rerun':
      case 'retry': {
        try {
          const ids = await call('start', [id]);
          if (!ids || !ids.length) toast('error', 'The file could not be queued again.');
        } catch (err) {
          fail(err);
        }
        return;
      }
      case 'apply-kind': {
        try {
          const list = await call('apply_to_kind', id);
          (list || []).forEach((x) => upsert(x));
          const n = (list || []).length;
          toast('success', n ? `Settings copied to ${nKind(n, it.kind)}` : `No other ${KIND_ALL[it.kind]} to update`);
        } catch (err) {
          fail(err);
        }
        return;
      }
      case 'remove':
        return removeItem(id);
      default:
        return;
    }
  }
  // A click on the card head toggles the panel; clicks inside the open panel do not.
  if (e.target.closest('.card-head')) toggle(id);
}

function onKey(e) {
  const card = e.target.closest('.card');
  if (!card || e.target !== card) return;
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    toggle(card.dataset.id);
  } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const sib = e.key === 'ArrowDown' ? card.nextElementSibling : card.previousElementSibling;
    sib && sib.focus();
  }
}

/** The card that holds keyboard focus (for Delete), or null. */
export function focusedCardId() {
  const a = document.activeElement;
  if (!a || a.closest('input, textarea, [contenteditable="true"]')) return null;
  const card = a.closest && a.closest('.card');
  return card ? card.dataset.id : null;
}
