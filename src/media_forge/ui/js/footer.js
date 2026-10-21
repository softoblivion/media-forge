// Footer: the counters capsule (add files, counts, Clear split button or Stop) and the primary
// Start pill, both lenses floating over the list.
//
// Start morph: "Start" / "Start 3 files" (tinted) -> progress pill "3 of 7 · 1.8x" with a liquid
// level (whole-batch progress) -> mint "Done" for 2.6 s -> back. The width follows the label by a
// spring; the lens map is rebuilt by its ResizeObserver.

import { call, on } from './api.js';
import { store, watch } from './store.js';
import { Lens, gel } from '../vendor/liquid_ui/js/glass.js';
import { Spring, SPRINGS, reducedMotion } from '../vendor/liquid_ui/js/motion.js';
import { icon } from './icons.js';
import { bytes, nFiles, plural } from './format.js';
import { toast } from '../vendor/liquid_ui/js/toast.js';
import { addItems } from './list.js';

let foot, stats, startBtn, startLens, statsLens;
let batch = null;       // { ids:Set, startedAt }
let doneUntil = 0;
let doneTimer = 0;
let shown = false;
let widthSpring = null;

export function initFooter() {
  foot = document.getElementById('foot');
  stats = document.getElementById('foot-stats');
  startBtn = document.getElementById('btn-start');
  statsLens = new Lens(stats, 'capsule');
  startLens = new Lens(startBtn, 'pill');
  gel(startBtn, { amount: 0.8 });

  startBtn.innerHTML = `
    <span class="pm-fill" aria-hidden="true"><span class="pm-liquid"></span></span>
    <span class="pm-layer pm-label">${icon('play')}<span class="pm-text">Start</span></span>
    <span class="pm-layer pm-progress"><span class="pm-ptext tnum"></span></span>
    <span class="pm-layer pm-done">${icon('check')}<span>Done</span></span>`;
  startBtn.addEventListener('click', () => start());

  stats.innerHTML = `
    <button type="button" class="icon-btn foot-add" data-act="add" aria-label="Add files" title="Add files (Ctrl+O)">${icon('plus')}</button>
    <div class="foot-text">
      <b class="foot-count tnum"></b>
      <span class="foot-sub tnum"></span>
    </div>
    <div class="foot-acts">
      <div class="split" id="clear-split">
        <button type="button" class="btn btn-s btn-ghost split-main" data-act="clear-done">${icon('check')}<span>Clear done</span></button>
        <button type="button" class="btn btn-s btn-ghost split-more" data-act="clear-menu" aria-label="More ways to clear the list" aria-haspopup="menu" aria-expanded="false">${icon('chevronDown')}</button>
      </div>
      <button type="button" class="btn btn-s btn-ghost btn-danger foot-stop" data-act="stop" hidden>${icon('stop')}<span>Stop</span></button>
    </div>
    <div class="split-menu glass-lite" id="clear-menu" role="menu" hidden>
      <button type="button" class="menu-item" role="menuitem" data-act="clear" data-what="done">${icon('check')}<span>Clear done</span></button>
      <button type="button" class="menu-item" role="menuitem" data-act="clear" data-what="errors">${icon('warn')}<span>Clear failed</span></button>
      <button type="button" class="menu-item" role="menuitem" data-act="clear" data-what="idle">${icon('layers')}<span>Clear all except running</span></button>
      <button type="button" class="menu-item is-danger" role="menuitem" data-act="clear" data-what="all">${icon('trash')}<span>Clear all</span></button>
    </div>`;
  stats.querySelectorAll('.btn, .icon-btn').forEach((b) => gel(b));
  // The menu lives outside the capsule: a lens is a backdrop root, and a menu inside it would
  // lose the scene behind it.
  foot.appendChild(stats.querySelector('#clear-menu'));
  foot.addEventListener('click', onStatsClick);
  foot.addEventListener('keydown', onMenuKey);

  watch('items', render);
  watch('caps', render);
  on('batch_done', onBatchDone);
  render();
}

export async function pickFiles() {
  try {
    const list = await call('pick_files');
    addItems(list);
  } catch (err) {
    toast('error', err.message);
  }
}

export async function start() {
  const st = startBtn.dataset.state;
  if (st !== 'ready') return;
  try {
    const ids = await call('start', null);
    if (!ids || !ids.length) return;
    if (!batch) batch = { ids: new Set() };
    ids.forEach((i) => batch.ids.add(i));
    doneUntil = 0;
    render();
  } catch (err) {
    toast('error', err.message);
  }
}

function counts() {
  const items = Array.from(store.items.values());
  const c = { total: items.length, ready: 0, queued: 0, running: 0, done: 0, error: 0, probing: 0, delta: 0 };
  for (const it of items) {
    c[it.status] = (c[it.status] || 0) + 1;
    if (it.status === 'done' && it.output && it.size) c.delta += it.output.size - it.size;
  }
  return { c, items };
}

function render() {
  const { c, items } = counts();
  // Show / hide the footer with the list.
  const want = c.total > 0;
  if (want !== shown) showFooter(want);

  // Counters.
  stats.querySelector('.foot-count').textContent = c.total ? nFiles(c.total) : 'No files';
  const sub = [];
  if (c.done) sub.push(`<span class="is-done">${c.done} done</span>`);
  if (c.error) sub.push(`<span class="is-err">${c.error} failed</span>`);
  if (c.queued + c.running) sub.push(`<span>${c.queued + c.running} in queue</span>`);
  if (!sub.length && c.probing) sub.push(`<span>Reading ${c.probing}…</span>`);
  if (!sub.length && c.ready) sub.push(`<span>${c.ready} ready</span>`);
  if (c.done && c.delta) sub.push(`<span class="${c.delta < 0 ? 'is-done' : ''}" title="Size change of the finished files">${c.delta < 0 ? '−' : '+'}${bytes(Math.abs(c.delta))}</span>`);
  stats.querySelector('.foot-sub').innerHTML = sub.join('<i>·</i>');

  const busy = c.queued + c.running > 0;
  stats.querySelector('#clear-split').hidden = busy;
  stats.querySelector('.foot-stop').hidden = !busy;
  stats.querySelector('[data-act="clear-done"]').disabled = !c.done;

  // Start pill.
  const ffReady = !store.caps || store.caps.ffmpeg === 'ready';
  let state;
  if (busy) state = 'progress';
  else if (doneUntil > Date.now()) state = 'done';
  else state = c.ready && ffReady ? 'ready' : 'empty';
  setStartState(state, c, items, ffReady);
}

function setStartState(state, c, items, ffReady) {
  const prev = startBtn.dataset.state;
  if (prev !== state) {
    if (state === 'progress') {
      startBtn.classList.add('pm-instant');
      startBtn.style.setProperty('--p', '0');
      void startBtn.offsetWidth;
      startBtn.classList.remove('pm-instant');
    }
    startBtn.dataset.state = state;
    startBtn.classList.toggle('tint-accent', state === 'ready');
    startBtn.classList.toggle('tint-mint', state === 'done');
  }
  startBtn.disabled = state === 'empty';
  startBtn.setAttribute('aria-disabled', String(state !== 'ready'));
  const label = c.ready > 1 ? `Start ${c.ready} files` : 'Start';
  startBtn.querySelector('.pm-text').textContent = label;
  if (state === 'empty') {
    startBtn.title = !ffReady ? 'Waiting for FFmpeg' : c.total ? 'No files are ready to start' : 'Add files first';
  } else if (state === 'ready') startBtn.title = 'Start (Ctrl+Enter)';
  else startBtn.removeAttribute('title');

  if (state === 'progress') {
    const ids = batch ? Array.from(batch.ids) : items.filter((i) => i.status === 'queued' || i.status === 'running').map((i) => i.id);
    const list = ids.map((id) => store.items.get(id)).filter(Boolean);
    const n = list.length || 1;
    const finished = list.filter((i) => i.status === 'done' || i.status === 'error' || i.status === 'ready').length;
    const runningItems = list.filter((i) => i.status === 'running');
    const sum = finished + runningItems.reduce((a, i) => a + (i.progress || 0), 0);
    const pos = Math.min(n, finished + (runningItems.length ? 1 : 0)) || 1;
    const speed = runningItems.find((i) => i.speed);
    const text = `${pos} of ${n}${speed ? ` · ${speed.speed}` : ''}`;
    startBtn.querySelector('.pm-ptext').textContent = text;
    startBtn.style.setProperty('--p', (sum / n).toFixed(4));
    startBtn.setAttribute('aria-label', `Processing ${pos} of ${n}`);
  } else {
    startBtn.removeAttribute('aria-label');
  }
  fitWidth(state, label);
}

/** Spring the pill width to its content (min 148 px, max 42 % of the footer). */
function fitWidth(state, label) {
  const key = `${state}|${state === 'ready' || state === 'empty' ? label : ''}`;
  if (startBtn.__wkey === key) return;
  startBtn.__wkey = key;
  const layer = startBtn.querySelector(state === 'progress' ? '.pm-progress' : state === 'done' ? '.pm-done' : '.pm-label');
  const meas = layer.cloneNode(true);
  meas.style.cssText = 'position:absolute;visibility:hidden;inset:auto;width:auto;padding:0 26px;opacity:1;transform:none;filter:none';
  startBtn.appendChild(meas);
  const content = meas.scrollWidth;
  meas.remove();
  const target = Math.round(Math.max(state === 'progress' ? 176 : 148, Math.min(content + 8, foot.clientWidth * 0.46)));
  if (!widthSpring) {
    widthSpring = new Spring(target, SPRINGS.bouncy, (v) => (startBtn.style.width = `${v.toFixed(1)}px`));
    widthSpring.set(target);
    return;
  }
  if (reducedMotion.matches) widthSpring.set(target);
  else widthSpring.to(target, SPRINGS.bouncy);
}

function showFooter(on) {
  shown = on;
  if (on) {
    foot.hidden = false;
    if (!reducedMotion.matches) {
      statsLens.setStrength(0);
      startLens.setStrength(0);
      foot.animate([{ transform: 'translateY(30px) scale(0.96)' }, { transform: 'none' }], { duration: 520, easing: 'cubic-bezier(.2,1.1,.3,1)' });
      startLens.animateStrength(1);
      statsLens.animateStrength(1);
    }
  } else {
    foot.hidden = true;
    closeMenu();
  }
  document.documentElement.classList.toggle('has-items', on);
}

function onBatchDone(p) {
  const done = (p && p.done) || 0;
  const failed = (p && p.failed) || 0;
  batch = null;
  if (done) {
    doneUntil = Date.now() + 2600;
    clearTimeout(doneTimer);
    doneTimer = setTimeout(render, 2700);
  }
  if (failed) toast('error', done ? `${done} done, ${failed} failed` : `${failed} ${plural(failed, ['file', 'files'])} failed`);
  render();
}

// ---- counters capsule actions -----------------------------------------------------------------

async function onStatsClick(e) {
  const b = e.target.closest('[data-act]');
  if (!b) return;
  const fail = (err) => toast('error', err.message);
  switch (b.dataset.act) {
    case 'add':
      return pickFiles();
    case 'clear-done':
      return call('clear', 'done').catch(fail);
    case 'clear-menu':
      return menuOpen() ? closeMenu() : openMenu();
    case 'clear':
      closeMenu();
      return call('clear', b.dataset.what).catch(fail);
    case 'stop':
      try {
        const n = await call('cancel_all');
        if (n) toast('info', `Stopped ${nFiles(n)}`);
      } catch (err) {
        fail(err);
      }
      return;
    default:
  }
}

const menu = () => document.getElementById('clear-menu');
const menuOpen = () => !menu().hidden;

function openMenu() {
  const m = menu();
  const { c } = counts();
  m.querySelector('[data-what="done"]').disabled = !c.done;
  m.querySelector('[data-what="errors"]').disabled = !c.error;
  m.hidden = false;
  const more = stats.querySelector('.split-more');
  const r = more.getBoundingClientRect();
  const f = foot.getBoundingClientRect();
  m.style.left = `${Math.max(0, Math.round(r.right - f.left - m.offsetWidth + 6))}px`;
  more.setAttribute('aria-expanded', 'true');
  m.animate([{ opacity: 0, transform: 'translateY(8px) scale(.96)' }, { opacity: 1, transform: 'none' }], { duration: 220, easing: 'cubic-bezier(.2,.8,.2,1)' });
  const first = m.querySelector('.menu-item:not(:disabled)');
  first && first.focus();
  addEventListener('pointerdown', outside, true);
}

function closeMenu() {
  const m = menu();
  if (!m || m.hidden) return;
  m.hidden = true;
  stats.querySelector('.split-more').setAttribute('aria-expanded', 'false');
  removeEventListener('pointerdown', outside, true);
}

function outside(e) {
  if (!menu().contains(e.target) && !e.target.closest('.split-more')) closeMenu();
}

function onMenuKey(e) {
  if (!menuOpen()) return;
  const items = Array.from(menu().querySelectorAll('.menu-item:not(:disabled)'));
  const i = items.indexOf(document.activeElement);
  if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
  if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
  if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation();
    closeMenu();
    stats.querySelector('.split-more').focus();
  }
  if (e.key === 'Tab') closeMenu();
}

export function footerMenuOpen() {
  return menuOpen();
}
