// Media Forge formatting: the kit's en-US helpers (vendor/liquid_ui/js/format.js: plurals, sizes,
// percentages, durations, ETA, dates, escaping) plus the app's nouns: file counts, codec names,
// channels, sample rates and the kind labels.

import { num, plural } from '../vendor/liquid_ui/js/format.js';

export * from '../vendor/liquid_ui/js/format.js';

export const nFiles = (n) => `${n} ${plural(n, ['file', 'files'])}`;

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
