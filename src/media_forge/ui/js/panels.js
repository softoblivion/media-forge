// Settings panels per kind (BRIDGE_API 3.3), shared by the file cards (set_item_settings) and the
// per-kind defaults in the settings sheet (set_settings({defaults: {<kind>: patch}})).
//
// The panel sends one-key patches through `onPatch(patch) -> Promise<settings>` and then shows the
// settings the bridge returned (normalised, 3.4), never its own guess: a change is applied locally
// only until the answer arrives; a refusal shows a toast and restores the last confirmed values.
// Visibility follows the settings: codec / encoder only for formats with codecs, upscale method and
// sharpen only for 2x / 4x, audio bitrate only where it applies, and so on.

import { Segmented, Switch, Slider } from '../vendor/liquid_ui/js/controls.js';
import { store, watch, gpuFor, formatsOf } from './store.js';
import { icon } from './icons.js';
import { escapeHtml } from './format.js';
import { toast } from '../vendor/liquid_ui/js/toast.js';

const AUDIO_ONLY = new Set(['mp3', 'm4a', 'opus', 'flac', 'wav']);
const UPSCALE = new Set(['2x', '4x']);
const LOSSLESS_AUDIO = new Set(['flac', 'wav']);

const opt = (list) => list.map((x) => ({ value: String(x.id), label: x.label }));

/** Short label of a format for summaries ("MP3 · audio only" -> "MP3", "M4A · AAC" -> "M4A"). */
export function shortFormat(kind, id) {
  const f = formatsOf(kind).find((x) => x.id === id);
  return f ? f.label.split(' · ')[0] : String(id || '').toUpperCase();
}

/** One-line summary of what a file becomes: "MP4 · H.264 · Quality 70". */
export function planSummary(kind, s) {
  if (!s || !store.options) return '';
  const o = store.options;
  const parts = [shortFormat(kind, s.format)];
  const limit = s.size_limit_mb != null ? `≤ ${fmtLimit(s.size_limit_mb)} MB` : null;
  if (kind === 'video') {
    if (AUDIO_ONLY.has(s.format)) {
      if (!LOSSLESS_AUDIO.has(s.format)) parts.push(`${s.audio_bitrate_k} kbps`);
      if (limit) parts.push(limit);
    } else {
      const fmt = o.video.formats.find((f) => f.id === s.format);
      if (fmt && fmt.codecs.length) {
        const c = o.video.codecs.find((x) => x.id === s.codec);
        if (c) parts.push(c.id === 'h265' ? 'HEVC' : c.label);
      }
      if (s.codec !== 'copy' || !(fmt && fmt.codecs.length)) parts.push(limit || `Quality ${s.quality}`);
      if (s.scale !== 'none') {
        const sc = o.video.scales.find((x) => x.id === s.scale);
        parts.push(sc ? sc.label : s.scale);
      }
      if (s.encoder === 'gpu' || (s.encoder === 'auto' && fmt && fmt.codecs.length && gpuFor(s.codec))) parts.push('GPU');
    }
  } else if (kind === 'image') {
    const fmt = o.image.formats.find((f) => f.id === s.format);
    if (limit) parts.push(limit);
    else if (fmt && fmt.lossy) parts.push(`Quality ${s.quality}`);
    if (s.scale !== 'none') {
      const sc = o.image.scales.find((x) => x.id === s.scale);
      parts.push(sc ? sc.label : s.scale);
    }
  } else {
    if (!LOSSLESS_AUDIO.has(s.format)) parts.push(`${s.format === 'opus' ? Math.min(256, s.bitrate_k) : s.bitrate_k} kbps`);
    if (limit) parts.push(limit);
    if (s.normalize) parts.push('Loudness −16 LUFS');
  }
  return parts.join(' · ');
}

export function fmtLimit(v) {
  if (v == null) return '';
  return String(Math.round(v * 1000) / 1000);
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

export class SettingsPanel {
  /**
   * @param {{kind:'video'|'image'|'audio', settings:object, onPatch:(patch)=>Promise<object>}} o
   */
  constructor({ kind, settings, onPatch }) {
    this.kind = kind;
    this.onPatch = onPatch;
    this.s = { ...settings };
    this.confirmed = { ...settings };
    this.seq = 0;
    this.inflight = 0;
    this.locked = false;
    this.parts = [];          // controls with destroy()
    this.rows = {};
    this.c = {};
    this.el = el('div', `panel panel-${kind}`);
    this.el.dataset.kind = kind;
    this.note = el('div', 'p-note', '');
    this.note.hidden = true;
    this.body = el('div', 'p-body');
    this.el.append(this.note, this.body);
    this.build();
    this.sync(settings);
    this.unwatch = watch('caps', () => this._apply());
  }

  // ---- building blocks ----------------------------------------------------------------------

  row(key, caption, control, { aside = '' } = {}) {
    const r = el('div', 'p-row');
    r.dataset.key = key;
    const cap = el('div', 'p-cap', `<span>${escapeHtml(caption)}</span><span class="p-aside tnum">${aside}</span>`);
    r.append(cap, control.el || control);
    const hint = el('div', 'p-hint');
    hint.hidden = true;
    r.append(hint);
    r.hint = hint;
    r.aside = cap.querySelector('.p-aside');
    this.rows[key] = r;
    return r;
  }

  switchRow(key, label, sub) {
    const sw = new Switch({ label, checked: !!this.s[key], onChange: (v) => this.patch({ [key]: v }) });
    this.parts.push(sw);
    this.c[key] = sw;
    const r = el('div', 'p-row p-switch');
    r.dataset.key = key;
    r.append(el('div', 'p-sw-label', `<span>${escapeHtml(label)}</span>${sub ? `<small>${escapeHtml(sub)}</small>` : ''}`), sw.el);
    this.rows[key] = r;
    return r;
  }

  seg(key, label, options, { scroll = false, cls = 'seg-p', map = String } = {}) {
    const s = new Segmented({
      label,
      cls,
      scroll,
      options,
      value: String(this.s[key]),
      onChange: (v) => this.patch({ [key]: map(v) }),
    });
    this.parts.push(s);
    this.c[key] = s;
    return s;
  }

  limitField() {
    const box = el('div', 'lim', `
      <input class="lim-in tnum" type="text" inputmode="decimal" placeholder="No limit" maxlength="9" aria-label="Size limit in megabytes" spellcheck="false" autocomplete="off">
      <span class="lim-unit">MB</span>
      <button type="button" class="lim-clear" aria-label="Remove the size limit" title="Remove the size limit">${icon('x')}</button>`);
    const input = box.querySelector('input');
    const clear = box.querySelector('.lim-clear');
    const commit = () => {
      const raw = input.value.trim().replace(',', '.');
      let v = raw === '' ? null : Number(raw);
      if (v != null && (!isFinite(v) || v <= 0)) v = null;
      const cur = this.s.size_limit_mb ?? null;
      if (v === cur) {
        input.value = fmtLimit(cur);
        return;
      }
      this.patch({ size_limit_mb: v });
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        commit();
        input.blur();
      } else if (e.key === 'Escape') {
        input.value = fmtLimit(this.s.size_limit_mb);
        input.blur();
        e.stopPropagation();
      }
    });
    input.addEventListener('beforeinput', (e) => {
      if (e.data && /[^0-9.,]/.test(e.data)) e.preventDefault();
    });
    input.addEventListener('input', () => box.classList.toggle('has-value', input.value.trim() !== ''));
    input.addEventListener('blur', commit);
    clear.addEventListener('click', () => {
      input.value = '';
      box.classList.remove('has-value');
      if (this.s.size_limit_mb != null) this.patch({ size_limit_mb: null });
    });
    this.c.size_limit_mb = {
      set: (v) => {
        if (document.activeElement !== input) input.value = fmtLimit(v);
        box.classList.toggle('has-value', v != null || input.value.trim() !== '');
      },
      input,
    };
    return box;
  }

  quality() {
    const sl = new Slider({ label: 'Quality', value: this.s.quality, onChange: (v) => this.patch({ quality: v }) });
    this.c.quality = sl;
    return sl;
  }

  // ---- layout per kind -------------------------------------------------------------------------

  build() {
    const o = store.options;
    const b = this.body;
    if (this.kind === 'video') {
      b.append(this.row('format', 'Format', this.seg('format', 'Format', opt(o.video.formats), { scroll: true })));
      this.codecSlot = el('div', 'p-slot');
      b.append(this.row('codec', 'Codec', this.codecSlot));
      b.append(this.row('encoder', 'Encoder', this.seg('encoder', 'Encoder', opt(o.video.encoders))));
      b.append(this.row('scale', 'Scale', this.seg('scale', 'Scale', opt(o.video.scales), { scroll: true })));
      // EDSR is photos only (BRIDGE_API.md 3.3): far below one frame per second on a CPU.
      b.append(this.row('upscale_method', 'Upscale method', this.seg('upscale_method', 'Upscale method', opt(o.upscale_methods.filter((m) => m.id !== 'edsr')))));
      b.append(this.row('quality', 'Quality', this.quality()));
      b.append(this.row('size_limit_mb', 'Size limit', this.limitField()));
      const grid = el('div', 'p-grid');
      grid.append(
        this.row('fps', 'Frame rate', this.seg('fps', 'Frame rate', opt(o.video.fps))),
        this.row('audio', 'Audio', this.seg('audio', 'Audio', opt(o.video.audio))),
      );
      b.append(grid);
      b.append(this.row('audio_bitrate_k', 'Audio bitrate', this.seg('audio_bitrate_k', 'Audio bitrate', o.video.audio_bitrates.map((n) => ({ value: String(n), label: String(n) })), { map: Number }), { aside: 'kbps' }));
      const sw = el('div', 'p-switches');
      sw.append(
        this.switchRow('sharpen', 'Sharpen after upscale', 'A light unsharp mask'),
        this.switchRow('strip_metadata', 'Remove metadata', 'No tags, chapters or encoder names'),
      );
      b.append(sw);
    } else if (this.kind === 'image') {
      b.append(this.row('format', 'Format', this.seg('format', 'Format', opt(o.image.formats), { scroll: true })));
      b.append(this.row('quality', 'Quality', this.quality()));
      b.append(this.row('size_limit_mb', 'Size limit', this.limitField()));
      b.append(this.row('scale', 'Scale', this.seg('scale', 'Scale', opt(o.image.scales))));
      b.append(this.row('upscale_method', 'Upscale method', this.seg('upscale_method', 'Upscale method', opt(o.upscale_methods))));
      const sw = el('div', 'p-switches');
      sw.append(
        this.switchRow('sharpen', 'Sharpen after upscale', 'A light unsharp mask'),
        this.switchRow('strip_metadata', 'Remove metadata', 'EXIF, XMP and IPTC; the colour profile stays'),
      );
      b.append(sw);
    } else {
      b.append(this.row('format', 'Format', this.seg('format', 'Format', opt(o.audio.formats), { scroll: true })));
      b.append(this.row('bitrate_k', 'Bitrate', this.seg('bitrate_k', 'Bitrate', o.audio.bitrates.map((n) => ({ value: String(n), label: String(n) })), { scroll: true, map: Number }), { aside: 'kbps' }));
      b.append(this.row('size_limit_mb', 'Size limit', this.limitField()));
      const grid = el('div', 'p-grid');
      grid.append(
        this.row('sample_rate', 'Sample rate', this.seg('sample_rate', 'Sample rate', opt(o.audio.sample_rates))),
        this.row('channels', 'Channels', this.seg('channels', 'Channels', opt(o.audio.channels))),
      );
      b.append(grid);
      const sw = el('div', 'p-switches');
      sw.append(
        this.switchRow('normalize', 'Normalise loudness', 'EBU R128, −16 LUFS'),
        this.switchRow('strip_metadata', 'Remove metadata', 'No tags or cover art'),
      );
      b.append(sw);
    }
  }

  /** Video codec options depend on the format: rebuilt when the format's codec list changes. */
  _codecs() {
    if (this.kind !== 'video') return;
    const o = store.options;
    const fmt = o.video.formats.find((f) => f.id === this.s.format);
    const ids = fmt ? fmt.codecs : [];
    const key = ids.join(',');
    if (key === this.codecKey) return;
    this.codecKey = key;
    if (this.c.codec) {
      this.c.codec.destroy();
      this.parts = this.parts.filter((p) => p !== this.c.codec);
      this.c.codec = null;
    }
    this.codecSlot.innerHTML = '';
    if (!ids.length) return;
    const options = ids.map((id) => {
      const c = o.video.codecs.find((x) => x.id === id);
      return { value: id, label: c ? c.label : id };
    });
    const s = this.seg('codec', 'Codec', options, { scroll: true });
    this.codecSlot.append(s.el);
  }

  // ---- state ---------------------------------------------------------------------------------------

  /** Show settings confirmed by the bridge. */
  sync(settings) {
    if (!settings) return;
    this.s = { ...settings };
    this.confirmed = { ...settings };
    this._apply();
  }

  /** Item events may carry settings; they win unless one of our own patches is still in flight. */
  external(settings) {
    if (this.inflight) return;
    this.sync(settings);
  }

  _apply() {
    const s = this.s;
    this._codecs();
    for (const [k, ctl] of Object.entries(this.c)) {
      if (!ctl || !(k in s)) continue;
      const v = s[k];
      if (ctl instanceof Segmented) ctl.set(String(v));
      else if (ctl instanceof Switch) ctl.set(!!v);
      else if (ctl.set) ctl.set(v);
    }
    this._visibility();
  }

  _show(key, on) {
    const r = this.rows[key];
    if (r) r.hidden = !on;
  }

  _hint(key, text, warn = false) {
    const r = this.rows[key];
    if (!r || !r.hint) return;
    r.hint.hidden = !text;
    r.hint.textContent = text || '';
    r.hint.classList.toggle('is-warn', !!warn);
  }

  _visibility() {
    const s = this.s;
    const o = store.options;
    const caps = store.caps || {};
    const up = UPSCALE.has(s.scale);
    const limit = s.size_limit_mb != null;

    if (this.kind === 'video') {
      const fmt = o.video.formats.find((f) => f.id === s.format) || { codecs: [], audio: true };
      const audioOnly = AUDIO_ONLY.has(s.format);
      const hasCodecs = fmt.codecs.length > 0;
      this._show('codec', hasCodecs);
      this._show('encoder', hasCodecs && s.codec !== 'copy');
      if (this.c.encoder) {
        const ok = gpuFor(s.codec);
        this.c.encoder.setOptionDisabled('gpu', !ok, ok ? '' : 'No NVIDIA encoder for this codec on this computer');
      }
      this._hint('encoder', s.encoder === 'auto' && hasCodecs ? (gpuFor(s.codec) ? 'Uses the GPU for this codec' : 'Uses the CPU for this codec') : '');
      this._hint('codec', s.codec === 'copy' ? 'Keeps the video stream as it is. Scale, quality and size limit do not apply.' : '');
      this._show('scale', !audioOnly);
      this._show('upscale_method', !audioOnly && up);
      this._upscaleHint();
      this._show('quality', !audioOnly && s.format !== 'gif' ? true : !audioOnly);
      const qOff = limit || (hasCodecs && s.codec === 'copy');
      this.c.quality.setDisabled(qOff);
      this._hint('quality', limit ? 'Set by the size limit' : hasCodecs && s.codec === 'copy' ? 'Not used when the stream is copied' : '');
      if (audioOnly && LOSSLESS_AUDIO.has(s.format)) this._hint('size_limit_mb', 'FLAC and WAV cannot be held to a size limit', limit);
      else this._hint('size_limit_mb', limit ? (audioOnly ? 'The bitrate is chosen to fit' : 'Two passes; the file stays under this size') : '');
      this._show('fps', !audioOnly);
      this._hint('fps', s.format === 'gif' ? 'GIF is capped at 15 fps' : '');
      this._show('audio', !audioOnly && fmt.audio);
      const brOn = audioOnly ? !LOSSLESS_AUDIO.has(s.format) : fmt.audio && s.audio === 'aac';
      this._show('audio_bitrate_k', brOn);
      this._show('sharpen', !audioOnly && up);
      this.el.querySelector('.p-grid').hidden = audioOnly;
    } else if (this.kind === 'image') {
      const fmt = o.image.formats.find((f) => f.id === s.format) || { lossy: true };
      if (this.c.format) this.c.format.setOptionDisabled('avif', caps.avif === false, caps.avif === false ? 'AVIF is not available in this build' : '');
      this.c.quality.setDisabled(limit || !fmt.lossy);
      this._hint('quality', limit ? 'Set by the size limit' : !fmt.lossy ? (s.format === 'png' ? 'PNG is lossless; the file is optimised instead' : `${fmt.label} is lossless`) : '');
      this._hint('size_limit_mb', limit ? 'Quality first, then a smaller size if needed' : '');
      this._show('upscale_method', up);
      this._upscaleHint();
      this._show('sharpen', up);
    } else {
      const lossless = LOSSLESS_AUDIO.has(s.format);
      this._show('bitrate_k', !lossless);
      this._hint('bitrate_k', s.format === 'opus' && s.bitrate_k > 256 ? 'Opus is encoded at 256 kbps at most' : limit && !lossless ? 'Lowered if needed to fit the size limit' : '');
      if (lossless) this._hint('size_limit_mb', 'FLAC and WAV cannot be held to a size limit', limit);
      else this._hint('size_limit_mb', limit ? 'The bitrate is chosen from the length' : '');
    }
  }

  _upscaleHint() {
    if (!this.rows.upscale_method) return;
    const o = store.options;
    const m = o.upscale_methods.find((x) => x.id === this.s.upscale_method);
    let text = m ? m.hint : '';
    const models = (store.caps && store.caps.models) || {};
    const st = models[this.s.upscale_method];
    if (st === 'downloading') text = 'Downloading the model…';
    else if (st === 'error') text = 'The model could not be downloaded. It is tried again on the next run.';
    else if (st === 'missing' && this.s.upscale_method === 'fsrcnn') text = 'The bundled model is missing from this build.';
    this._hint('upscale_method', text);
  }

  async patch(patch) {
    if (this.locked) return;
    const my = ++this.seq;
    this.inflight++;
    Object.assign(this.s, patch);
    this._visibility();
    try {
      const out = await this.onPatch(patch);
      if (my === this.seq && out) this.sync(out);
    } catch (err) {
      toast('error', err.message);
      if (my === this.seq) this.sync(this.confirmed);
    } finally {
      this.inflight--;
    }
  }

  /** Locked while the file is queued, running or finished: the controls stay visible, inert. */
  setLocked(on, note = '') {
    this.locked = !!on;
    this.el.classList.toggle('is-locked', this.locked);
    this.body.inert = this.locked;
    this.note.hidden = !(on && note);
    this.note.textContent = note;
  }

  destroy() {
    this.unwatch && this.unwatch();
    for (const p of this.parts) p.destroy && p.destroy();
    this.parts = [];
  }
}
