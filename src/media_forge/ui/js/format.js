// en-US formatting: plurals, sizes, durations, ETA, relative times, codec names, kind nouns.

export function plural(n, forms) {
  return Math.abs(n) === 1 ? forms[0] : forms[1];
}

export const nFiles = (n) => `${n} ${plural(n, ['file', 'files'])}`;

const num = (v, digits = 1) =>
  v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function bytes(b) {
  if (b == null || !isFinite(b)) return '';
  const a = Math.abs(b);
  if (a < 1024) return `${Math.round(b)} B`;
  if (a < 1024 * 1024) return `${num(b / 1024, 0)} KB`;
  if (a < 1024 ** 3) return `${num(b / 1024 ** 2, a < 10 * 1024 ** 2 ? 1 : 1)} MB`;
  return `${num(b / 1024 ** 3, 2)} GB`;
}

/** Ratio as a percentage with a no-break space: 0.19 -> "19 %". */
export function percent(r) {
  if (r == null || !isFinite(r)) return '';
  const p = r * 100;
  return `${p < 10 && p > 0 ? num(p, 1) : Math.round(p)}\u00a0%`;
}

export function eta(s) {
  if (s == null || !isFinite(s) || s < 0) return '';
  s = Math.round(s);
  if (s < 60) return `${Math.max(1, s)} s left`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return r ? `${m} min ${r} s left` : `${m} min left`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} min left`;
}

export function duration(s) {
  if (s == null || !isFinite(s)) return '';
  s = Math.round(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${r}` : `${m}:${r}`;
}

export function timeAgo(epoch) {
  if (!epoch) return '';
  const d = Date.now() / 1000 - epoch;
  if (d < 45) return 'just now';
  if (d < 3600) return `${Math.max(1, Math.round(d / 60))} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  if (d < 172800) return 'yesterday';
  return new Date(epoch * 1000).toLocaleDateString('en-US', { day: 'numeric', month: 'short' });
}

export function shortDate(epoch) {
  if (!epoch) return '';
  return new Date(epoch * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

const CODECS = {
  h264: 'H.264', avc1: 'H.264', hevc: 'HEVC', h265: 'HEVC', vp9: 'VP9', vp8: 'VP8', av1: 'AV1', prores: 'ProRes',
  mpeg4: 'MPEG-4', mpeg2video: 'MPEG-2', gif: 'GIF', aac: 'AAC', mp3: 'MP3', opus: 'Opus', vorbis: 'Vorbis',
  flac: 'FLAC', pcm_s16le: 'PCM', pcm_s24le: 'PCM', alac: 'ALAC', ac3: 'AC-3', eac3: 'E-AC-3',
};

export function codecName(c) {
  if (!c) return '';
  const k = String(c).toLowerCase();
  return CODECS[k] || String(c).toUpperCase();
}

export function channelsName(n) {
  if (!n) return '';
  if (n === 1) return 'Mono';
  if (n === 2) return 'Stereo';
  return `${n} ch`;
}

export function sampleRate(hz) {
  if (!hz) return '';
  const k = hz / 1000;
  return `${Number.isInteger(k) ? k : num(k, 1)} kHz`;
}

/** Kind labels used in the UI (the bridge's kind `image` reads as "Photo"). */
export const KIND_LABEL = { video: 'Video', image: 'Photo', audio: 'Audio' };
/** "Apply to all video" / "Apply to all photos" / "Apply to all audio". */
export const KIND_ALL = { video: 'video', image: 'photos', audio: 'audio' };
/** "3 video files" / "3 photos" / "3 audio files". */
export function nKind(n, kind) {
  if (kind === 'image') return `${n} ${plural(n, ['photo', 'photos'])}`;
  return `${n} ${kind} ${plural(n, ['file', 'files'])}`;
}

export function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
}

/** Last path segment of a Windows path ("D:\\clips\\a.mp4" -> "a.mp4"; "E:\\" stays). */
export function baseName(p) {
  if (!p) return '';
  const s = String(p).replace(/[\\/]+$/, '');
  const i = Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/'));
  return i >= 0 ? s.slice(i + 1) || s : s;
}
