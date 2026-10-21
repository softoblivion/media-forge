// Self-update: the version pill in the title bar and its popover (UpdateStatus, BRIDGE_API 3.8).
// idle / up_to_date -> "Check for updates"; checking -> indeterminate bar; available -> latest
// version, size, date, release notes, "Download"; downloading -> determinate bar; ready ->
// "Restart to update" (apply_update); error -> the message and "Try again".
//
// The shell downloads by itself when Settings.auto_download_updates is on (ARCHITECTURE 5.7); the
// page never starts a download on its own. The first `available` of a session raises one toast,
// `ready` one sticky toast with "Restart". The restart is never automatic.

import { call, on } from './api.js';
import { store, notify } from './store.js';
import { gel, Lens } from './glass.js';
import { Overlay } from './overlay.js';
import { icon } from './icons.js';
import { escapeHtml, bytes, shortDate, timeAgo } from './format.js';
import { toast } from './toast.js';

const IDLE = { stage: 'idle', current: '', latest: null, notes: null, size: null, published_at: null, progress: null, message: '', checked_at: null, can_apply: false };
const CAN_CHECK = new Set(['idle', 'up_to_date', 'error']);

let pill, pop, overlay;
let u = IDLE;
let lastBusy = null;
let availToasted = null;
let readyToasted = null;
let hasActiveJobs = () => false;

export function initUpdate(state, opts = {}) {
  if (opts.activeJobs) hasActiveJobs = opts.activeJobs;
  pill = document.getElementById('ver-pill');
  pop = document.getElementById('update-pop');
  new Lens(pill, 'capsule');
  overlay = new Overlay(pop, {
    preset: 'sheet',
    place(surface, origin) {
      const r = (origin || pill).getBoundingClientRect();
      surface.style.top = `${Math.round(r.bottom + 10)}px`;
      const w = surface.offsetWidth || 360;
      surface.style.left = `${Math.round(Math.max(12, Math.min(r.left - 8, innerWidth - w - 12)))}px`;
    },
  });
  gel(pill);
  pill.addEventListener('click', async () => {
    if (overlay.open) return overlay.close();
    renderPop();
    overlay.show(pill);
    try {
      render(await call('update_status'));
    } catch {
      /* the last known status stays */
    }
  });
  pop.addEventListener('click', onClick);
  on('update', render);
  render({ ...IDLE, current: state.version || '', ...(state.update || {}) });
}

/** Open the panel from elsewhere (settings "Check for updates"); optionally start a check. */
export function openUpdatePanel({ check = false } = {}) {
  renderPop();
  overlay.show(pill);
  if (check && CAN_CHECK.has(u.stage)) runCheck();
}

export const updateState = () => u;

export function runCheck() {
  return call('check_updates').then((s) => s && render(s)).catch((err) => toast('error', err.message));
}

export function runDownload() {
  return call('download_update').then((s) => s && render(s)).catch((err) => toast('error', err.message));
}

function render(next) {
  if (!next) return;
  const prev = u;
  u = { ...IDLE, ...next };
  store.update = u;
  if (u.stage === 'checking' || u.stage === 'downloading') lastBusy = u.stage;
  pill.dataset.stage = u.stage;
  pill.querySelector('.ver-text').textContent = u.current ? `v${u.current}` : 'Version';
  const tip = {
    available: `Media Forge ${u.latest} is available`,
    downloading: `Downloading Media Forge ${u.latest}`,
    ready: `Media Forge ${u.latest} is ready to install`,
  }[u.stage] || 'Version and updates';
  pill.title = tip;
  pill.setAttribute('aria-label', tip);
  if (u.stage === 'available' && u.latest && availToasted !== u.latest && !(store.settings && store.settings.auto_download_updates)) {
    availToasted = u.latest;
    toast('info', `Media Forge ${u.latest} is available`, { ms: 8000, action: { label: 'View', run: () => openUpdatePanel() } });
  }
  if (u.stage === 'ready' && u.latest && readyToasted !== u.latest) {
    readyToasted = u.latest;
    toast('success', `Media Forge ${u.latest} is ready`, {
      sticky: true,
      action: u.can_apply ? { label: 'Restart', run: restart } : { label: 'View', run: () => openUpdatePanel() },
    });
  }
  if (overlay.open || prev.stage !== u.stage) renderPop();
  notify('update', u);
}

export function restart() {
  const go = () => call('apply_update').catch((err) => toast('error', err.message));
  if (hasActiveJobs()) {
    return toast('warning', 'Files in progress stop when the app restarts', { ms: 8000, action: { label: 'Restart anyway', run: go } });
  }
  return go();
}

/** One line of status, shared with the settings sheet. */
export function statusLine(s = u) {
  if (s.stage === 'error') return s.message || (lastBusy === 'downloading' ? 'The download did not finish' : 'Could not check for updates');
  const d = {
    idle: 'Updates come from GitHub Releases',
    checking: 'Checking for updates…',
    up_to_date: 'You have the latest version',
    available: `Version ${s.latest} is available`,
    downloading: `Downloading version ${s.latest}…`,
    ready: `Version ${s.latest} is ready to install`,
  }[s.stage];
  return d || s.message || '';
}

let popKey = '';

function renderPop() {
  const key = [u.stage, u.current, u.latest, u.size, u.published_at, !!u.notes, u.can_apply, u.message, u.checked_at, u.progress == null].join('|');
  if (key === popKey && u.stage === 'downloading') {
    const bar = pop.querySelector('.pop-progress');
    if (bar) bar.style.setProperty('--p', String(u.progress ?? 0));
    const pct = pop.querySelector('.upd-pct');
    if (pct && u.progress != null) {
      pct.querySelector('b').textContent = `${Math.round(u.progress * 100)}%`;
      pct.querySelector('span').textContent = u.size ? `${bytes(u.size * u.progress)} of ${bytes(u.size)}` : '';
    }
    return;
  }
  popKey = key;
  const newer = !!u.latest && ['available', 'downloading', 'ready'].includes(u.stage);
  const rows = [`<li class="comp">
      <span class="comp-ic">${icon('app')}</span>
      <span class="comp-name">Installed</span>
      <span class="comp-ver">${escapeHtml(u.current ? `v${u.current}` : '—')}</span>
    </li>`];
  if (newer && u.latest !== u.current) {
    const tags = [];
    if (u.size) tags.push(`<em>${escapeHtml(bytes(u.size))}</em>`);
    if (u.published_at) tags.push(`<em>${escapeHtml(shortDate(u.published_at))}</em>`);
    if (u.stage === 'ready') tags.push('<em class="tag-new">downloaded</em>');
    rows.push(`<li class="comp ${u.stage === 'ready' ? 'is-ready' : 'is-new'}">
      <span class="comp-ic">${icon(u.stage === 'ready' ? 'check' : 'down')}</span>
      <span class="comp-name">Latest</span>
      <span class="comp-ver">${escapeHtml(`v${u.latest}`)}</span>
      <span class="comp-tags">${tags.join('')}</span>
    </li>`);
  }
  let progress = '';
  if (u.stage === 'checking') progress = '<div class="pop-progress is-indet"><span></span></div>';
  if (u.stage === 'downloading') {
    const p = u.progress;
    progress = `<div class="pop-progress ${p == null ? 'is-indet' : ''}" style="--p:${p ?? 0}"><span></span></div>`;
    if (p != null) progress += `<div class="upd-pct"><b>${Math.round(p * 100)}%</b><span>${escapeHtml(u.size ? `${bytes(u.size * p)} of ${bytes(u.size)}` : '')}</span></div>`;
  }
  const notes = newer && u.notes ? `<div class="upd-notes" tabindex="0" aria-label="Release notes">${renderMarkdown(u.notes)}</div>` : '';
  const box = u.stage === 'error' && u.message ? `<p class="upd-msg" role="alert">${escapeHtml(u.message)}</p>` : '';
  let action;
  switch (u.stage) {
    case 'checking':
      action = `<button type="button" class="btn ov-focus" disabled>${icon('refresh')}<span>Checking…</span></button>`;
      break;
    case 'available':
      action = `<button type="button" class="btn btn-tinted ov-focus" data-act="download">${icon('down')}<span>Download</span></button>`;
      break;
    case 'downloading':
      action = `<button type="button" class="btn ov-focus" disabled>${icon('down')}<span>Downloading…</span></button>`;
      break;
    case 'ready':
      action = `<button type="button" class="btn btn-tinted ov-focus" data-act="apply" ${u.can_apply ? '' : `disabled title="${escapeHtml(u.message || 'Installs from the packaged app only')}"`}>${icon('refresh')}<span>Restart to update</span></button>`;
      break;
    case 'error':
      action = `<button type="button" class="btn ov-focus" data-act="retry">${icon('retry')}<span>Try again</span></button>`;
      break;
    default:
      action = `<button type="button" class="btn ov-focus" data-act="check">${icon('refresh')}<span>Check for updates</span></button>`;
  }
  const auto = store.settings && store.settings.auto_download_updates;
  pop.querySelector('.ov-content').innerHTML = `
    <div class="pop-head">
      <div class="pop-title">Media Forge</div>
      <div class="pop-sub">${escapeHtml(statusLine())}</div>
      ${auto ? `<div class="pop-auto">${icon('down')}<span>New versions download in the background</span></div>` : ''}
    </div>
    ${progress}
    ${box}
    <ul class="comp-list upd-rows">${rows.join('')}</ul>
    ${notes}
    <div class="pop-foot">
      <span class="pop-meta">${u.checked_at ? `Checked ${escapeHtml(timeAgo(u.checked_at))}` : 'Not checked yet'}</span>
      ${action}
    </div>`;
  pop.setAttribute('aria-busy', String(u.stage === 'checking' || u.stage === 'downloading'));
  pop.querySelectorAll('.btn').forEach((b) => gel(b));
}

function onClick(e) {
  const a = e.target.closest('a[data-url]');
  if (a) {
    e.preventDefault();
    call('open_url', a.dataset.url).then((ok) => ok || toast('error', 'The link could not be opened.')).catch((err) => toast('error', err.message));
    return;
  }
  const b = e.target.closest('[data-act]');
  if (!b) return;
  switch (b.dataset.act) {
    case 'check':
      return runCheck();
    case 'download':
      return runDownload();
    case 'retry':
      if (lastBusy === 'downloading' && u.latest) return runDownload();
      return runCheck();
    case 'apply':
      return restart();
    default:
  }
}

// Minimal, safe Markdown for release notes: headings, bullets, paragraphs, **bold**, `code` and
// https links (opened through open_url). Everything is HTML-escaped first; nothing else is parsed.

export function renderMarkdown(md) {
  const lines = String(md || '').replace(/\r\n?/g, '\n').split('\n');
  let html = '';
  let para = [];
  let inList = false;
  const flush = () => {
    if (para.length) html += `<p>${inline(para.join(' '))}</p>`;
    para = [];
  };
  const closeList = () => {
    if (inList) html += '</ul>';
    inList = false;
  };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    let m;
    if (!line.trim()) {
      flush();
      closeList();
      continue;
    }
    if ((m = line.match(/^ {0,3}(#{1,6})\s+(.*?)\s*#*$/))) {
      flush();
      closeList();
      html += `<h4${m[1].length <= 2 ? ' class="h-top"' : ''}>${inline(m[2])}</h4>`;
      continue;
    }
    if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) {
      flush();
      if (!inList) html += '<ul>';
      inList = true;
      html += `<li>${inline(m[1])}</li>`;
      continue;
    }
    if (inList && /^\s{2,}\S/.test(line)) {
      html = html.replace(/<\/li>$/, ` ${inline(line.trim())}</li>`);
      continue;
    }
    closeList();
    para.push(line.trim());
  }
  flush();
  closeList();
  return html;
}

function inline(s) {
  const codes = [];
  let t = escapeHtml(s).replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return `\u0001${codes.length - 1}\u0001`;
  });
  t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/__(.+?)__/g, '<strong>$1</strong>');
  t = t.replace(/\[([^\]]+)\]\((https:\/\/[^\s)"]+)\)/g, (_, text, url) => `<a href="#" data-url="${url}" title="${url}">${text}</a>`);
  return t.replace(/\u0001(\d+)\u0001/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
}
