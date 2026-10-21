// Mock backend: every method and event of docs/BRIDGE_API.md in plain JS, so the UI runs in a
// browser (index.html?mock=1, or no pywebview within 1.5 s).
//
// Simulates: probing (300-900 ms, generated thumbnails: a frame for video, a photo-like gradient
// for images, a waveform for audio), jobs at a plausible pace with stages / ETA / speed, one job
// in ten failing, size limits, normalisation exactly as BRIDGE_API 3.4, capabilities (NVENC on),
// options exactly as 3.7, settings persisted in localStorage, the self-update cycle on demand.
//
// URL flags: items=N (N ready files at start), demo=1 (one file in every state), fast=1 (jobs 4x
// faster), update=available|uptodate|error (result of the launch check), nvenc=0 (no NVIDIA GPU),
// ffmpeg=downloading (FFmpeg arrives after a simulated download).
// Development hooks (underscored, not part of the contract): _simulate_drop(files).

const VERSION = '1.0.0';
const STORE_KEY = 'media-forge.mock.settings';

export function createMock({ params, dispatch }) {
  const fast = params.get('fast') === '1';
  const pace = fast ? 4 : 1;
  const noGpu = params.get('nvenc') === '0';
  const updateFlag = params.get('update') || '';

  // ---- event delivery: buffered until ready(), item events coalesced per id ------------------
  let live = false;
  const buffer = [];
  const clone = (v) => (v == null ? v : JSON.parse(JSON.stringify(v)));
  function emit(name, payload) {
    const p = clone(payload);
    if (!live) buffer.push([name, p]);
    else setTimeout(() => dispatch(name, p), 0);
  }
  const pendingItems = new Map();
  let itemTimer = 0;
  function emitItem(it) {
    pendingItems.set(it.id, it);
    if (!itemTimer) itemTimer = setTimeout(flushItems, 40);
  }
  function flushItems() {
    itemTimer = 0;
    const list = Array.from(pendingItems.values());
    pendingItems.clear();
    for (const it of list) if (items.has(it.id)) emit('item', it);
  }

  // ---- capabilities and options ----------------------------------------------------------------
  const caps = {
    ffmpeg: params.get('ffmpeg') === 'downloading' ? 'downloading' : 'probing',
    ffmpeg_version: null,
    ffmpeg_path: null,
    nvenc: { h264: !noGpu, h265: !noGpu, av1: !noGpu },
    gpu_name: noGpu ? null : 'NVIDIA GeForce RTX 4070 Ti SUPER',
    avif: true,
    heif: true,
    models: { fsrcnn: 'ready', edsr: 'missing' },
  };
  const OPTIONS = buildOptions(caps);

  const DEFAULTS = {
    video: { format: 'mp4', codec: 'h264', encoder: 'auto', quality: 70, size_limit_mb: null, scale: 'none', upscale_method: 'lanczos', sharpen: true, fps: 'keep', audio: 'keep', audio_bitrate_k: 192, strip_metadata: true },
    image: { format: 'jpg', quality: 85, size_limit_mb: null, scale: 'none', upscale_method: 'lanczos', sharpen: true, strip_metadata: true },
    audio: { format: 'mp3', bitrate_k: 192, size_limit_mb: null, sample_rate: 'keep', channels: 'keep', normalize: false, strip_metadata: true },
  };

  const SCHEMA = {
    video: {
      format: { t: 'enum', v: OPTIONS.video.formats.map((f) => f.id) },
      codec: { t: 'enum', v: OPTIONS.video.codecs.map((c) => c.id) },
      encoder: { t: 'enum', v: OPTIONS.video.encoders.map((c) => c.id) },
      quality: { t: 'int', min: 0, max: 100 },
      size_limit_mb: { t: 'limit' },
      scale: { t: 'enum', v: OPTIONS.video.scales.map((c) => c.id) },
      upscale_method: { t: 'enum', v: OPTIONS.upscale_methods.filter((c) => c.id !== 'edsr').map((c) => c.id) },  // EDSR is photos only
      sharpen: { t: 'bool' },
      fps: { t: 'enum', v: OPTIONS.video.fps.map((c) => c.id) },
      audio: { t: 'enum', v: OPTIONS.video.audio.map((c) => c.id) },
      audio_bitrate_k: { t: 'num', v: OPTIONS.video.audio_bitrates },
      strip_metadata: { t: 'bool' },
    },
    image: {
      format: { t: 'enum', v: OPTIONS.image.formats.map((f) => f.id) },
      quality: { t: 'int', min: 0, max: 100 },
      size_limit_mb: { t: 'limit' },
      scale: { t: 'enum', v: OPTIONS.image.scales.map((c) => c.id) },
      upscale_method: { t: 'enum', v: OPTIONS.upscale_methods.map((c) => c.id) },
      sharpen: { t: 'bool' },
      strip_metadata: { t: 'bool' },
    },
    audio: {
      format: { t: 'enum', v: OPTIONS.audio.formats.map((f) => f.id) },
      bitrate_k: { t: 'num', v: OPTIONS.audio.bitrates },
      size_limit_mb: { t: 'limit' },
      sample_rate: { t: 'enum', v: OPTIONS.audio.sample_rates.map((c) => c.id) },
      channels: { t: 'enum', v: OPTIONS.audio.channels.map((c) => c.id) },
      normalize: { t: 'bool' },
      strip_metadata: { t: 'bool' },
    },
  };

  function coerce(spec, value, dflt) {
    switch (spec.t) {
      case 'enum': {
        const s = value == null || typeof value === 'boolean' ? '' : String(value);
        return spec.v.includes(s) ? s : dflt;
      }
      case 'int': {
        const n = typeof value === 'string' && value.trim() === '' ? NaN : Number(value);
        if (value == null || typeof value === 'boolean' || !isFinite(n)) return dflt;
        return Math.min(spec.max, Math.max(spec.min, Math.round(n)));
      }
      case 'num': {
        const n = typeof value === 'string' && value.trim() === '' ? NaN : Number(value);
        if (value == null || typeof value === 'boolean' || !isFinite(n)) return dflt;
        return spec.v.reduce((a, b) => (Math.abs(b - n) < Math.abs(a - n) ? b : a));
      }
      case 'bool':
        return typeof value === 'boolean' ? value : dflt;
      case 'limit': {
        if (value == null || value === '' || typeof value === 'boolean') return null;
        const n = Number(value);
        return isFinite(n) && n > 0 ? Math.round(n * 1000) / 1000 : null;
      }
      default:
        return dflt;
    }
  }

  const gpuFor = (codec) => {
    const c = OPTIONS.video.codecs.find((x) => x.id === codec);
    return !!(c && c.gpu && caps.nvenc[codec]);
  };

  /** BRIDGE_API 3.4: unknown keys dropped, values to the nearest allowed, combinations resolved. */
  function normalize(kind, base, patch) {
    const sc = SCHEMA[kind];
    const d = DEFAULTS[kind];
    const out = {};
    for (const k of Object.keys(sc)) {
      const has = patch && Object.prototype.hasOwnProperty.call(patch, k);
      const src = has ? patch[k] : base && Object.prototype.hasOwnProperty.call(base, k) ? base[k] : d[k];
      out[k] = coerce(sc[k], src, d[k]);
    }
    if (kind === 'video') {
      if (patch && patch.upscale_method === 'edsr') out.upscale_method = 'fsrcnn';  // EDSR is photos only (3.4)
      const fmt = OPTIONS.video.formats.find((f) => f.id === out.format);
      if (fmt && fmt.codecs.length && !fmt.codecs.includes(out.codec)) out.codec = fmt.codecs[0];
      if (out.codec === 'copy' && (out.scale !== 'none' || out.size_limit_mb != null || out.fps !== 'keep')) out.codec = 'h264';
      if (out.encoder === 'gpu' && !gpuFor(out.codec)) out.encoder = 'auto';
    }
    return out;
  }

  // ---- settings ---------------------------------------------------------------------------------
  const BASE_SETTINGS = {
    output_mode: 'beside',
    output_dir: '',
    suffix: '_forge',
    parallel_jobs: 1,
    open_when_done: false,
    sound_when_done: true,
    auto_download_updates: false,
    update_check_interval: '30m',
    defaults: clone(DEFAULTS),
  };
  let settings = loadSettings();

  function loadSettings() {
    let saved = null;
    try {
      saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    } catch {
      saved = null;
    }
    const s = { ...clone(BASE_SETTINGS), ...(saved && typeof saved === 'object' ? saved : {}) };
    s.defaults = {
      video: normalize('video', DEFAULTS.video, s.defaults && s.defaults.video),
      image: normalize('image', DEFAULTS.image, s.defaults && s.defaults.image),
      audio: normalize('audio', DEFAULTS.audio, s.defaults && s.defaults.audio),
    };
    return s;
  }

  function saveSettings() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(settings));
    } catch {
      /* storage unavailable: settings live for this page only */
    }
  }

  const INTERVALS = OPTIONS.update_intervals.map((x) => x.id);
  const BAD_NAME = /[<>:"/\\|?*\u0000-\u001f]/;

  function applySettingsPatch(patch) {
    if (!patch || typeof patch !== 'object') return settings;
    const next = clone(settings);
    for (const [k, v] of Object.entries(patch)) {
      switch (k) {
        case 'output_mode':
          if (v === 'beside' || v === 'folder') next.output_mode = v;
          break;
        case 'output_dir':
          next.output_dir = typeof v === 'string' ? v : next.output_dir;
          break;
        case 'suffix':
          if (typeof v !== 'string' || v.length > 24 || BAD_NAME.test(v) || /[. ]$/.test(v)) {
            throw new Error('The suffix can have up to 24 characters that are valid in a file name.');
          }
          next.suffix = v;
          break;
        case 'parallel_jobs': {
          const n = Math.round(Number(v));
          if (isFinite(n)) next.parallel_jobs = Math.min(3, Math.max(1, n));
          break;
        }
        case 'open_when_done':
        case 'sound_when_done':
        case 'auto_download_updates':
          if (typeof v === 'boolean') next[k] = v;
          break;
        case 'update_check_interval':
          if (INTERVALS.includes(v)) next.update_check_interval = v;
          break;
        case 'defaults':
          if (v && typeof v === 'object') {
            for (const kind of ['video', 'image', 'audio']) {
              if (v[kind] && typeof v[kind] === 'object') next.defaults[kind] = normalize(kind, next.defaults[kind], v[kind]);
            }
          }
          break;
        default:
          break; // unknown keys are ignored
      }
    }
    if (next.output_mode === 'folder' && (!next.output_dir || /^[A-Z]:\\missing/i.test(next.output_dir))) {
      throw new Error('The output folder does not exist.');
    }
    if (next.output_mode === 'beside' && next.suffix === '') {
      throw new Error('A suffix is needed when files are written next to the source.');
    }
    settings = next;
    saveSettings();
    return settings;
  }

  // ---- items ---------------------------------------------------------------------------------------
  const items = new Map(); // id -> Item (insertion order = list order)
  let seq = 0;
  const EXT = {
    video: ['mp4', 'mov', 'mkv', 'webm', 'avi', 'm4v', 'wmv', 'mts', 'm2ts', 'ts', 'mpg', 'mpeg', '3gp', 'flv'],
    image: ['jpg', 'jpeg', 'png', 'webp', 'avif', 'heic', 'heif', 'tif', 'tiff', 'bmp', 'gif'],
    audio: ['mp3', 'm4a', 'aac', 'wav', 'flac', 'ogg', 'opus', 'wma', 'aiff', 'aif'],
  };
  const kindOf = (name) => {
    const ext = (name.split('.').pop() || '').toLowerCase();
    for (const k of Object.keys(EXT)) if (EXT[k].includes(ext)) return k;
    return null;
  };
  const baseName = (p) => String(p).split(/[\\/]/).pop();
  const dirName = (p) => {
    const s = String(p);
    const i = Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/'));
    return i > 0 ? s.slice(0, i) : s;
  };

  function newItem(path, size) {
    const name = baseName(path);
    const kind = kindOf(name) || 'video';
    const it = {
      id: `i${++seq}`,
      path,
      name,
      kind,
      size: size || null,
      status: 'probing',
      probe: null,
      thumb: null,
      settings: clone(settings.defaults[kind]),
      progress: 0,
      stage: null,
      eta: null,
      speed: null,
      output: null,
      error: null,
      added_at: Date.now() / 1000,
    };
    it.__media = kindOf(name) != null;
    return it;
  }

  function addPaths(paths, sizes = []) {
    const known = new Set(Array.from(items.values()).map((i) => i.path.toLowerCase()));
    const added = [];
    let dup = 0;
    paths.forEach((p, idx) => {
      if (typeof p !== 'string' || !p) return;
      if (known.has(p.toLowerCase())) {
        dup++;
        return;
      }
      known.add(p.toLowerCase());
      const it = newItem(p, sizes[idx]);
      items.set(it.id, it);
      added.push(it);
      setTimeout(() => finishProbe(it), (300 + Math.random() * 600) / Math.sqrt(pace));
    });
    if (dup) emit('toast', { kind: 'info', message: dup === 1 ? 'Already in the list' : `${dup} files are already in the list` });
    return added.map(pub);
  }

  /** Public copy of an item (no private fields). */
  function pub(it) {
    const o = {};
    for (const [k, v] of Object.entries(it)) if (!k.startsWith('__')) o[k] = v;
    return clone(o);
  }

  function finishProbe(it) {
    if (!it || !items.has(it.id) || it.status !== 'probing') return;
    if (!it.__media) {
      it.status = 'error';
      it.error = 'This file is not a video, photo or audio file the app can read.';
      it.size = it.size || 18432;
      emitItem(pub(it));
      return;
    }
    const r = rng(it.name);
    if (it.kind === 'video') {
      const res = pick(r, [[3840, 2160], [1920, 1080], [1920, 1080], [1280, 720], [1080, 1920], [2560, 1440]]);
      const duration = Math.round((6 + r() * r() * 420) * 10) / 10;
      const vcodec = pick(r, ['h264', 'h264', 'hevc', 'av1', 'vp9']);
      const bitrate = Math.round((res[0] * res[1] * 30 * (vcodec === 'h264' ? 0.09 : 0.06)) * (0.6 + r() * 0.8));
      it.probe = {
        duration, width: res[0], height: res[1], fps: pick(r, [23.976, 25, 29.97, 30, 59.94, 60]),
        vcodec, acodec: 'aac', bitrate, sample_rate: 48000, channels: 2, has_audio: r() > 0.08,
        format: it.name.toLowerCase().endsWith('.mkv') ? 'matroska,webm' : it.name.toLowerCase().endsWith('.webm') ? 'matroska,webm' : 'mov,mp4,m4a,3gp,3g2,mj2',
      };
      if (!it.size) it.size = Math.round(bitrate * duration / 8 + 128000 * duration / 8);
      it.thumb = thumbVideo(r, res[0] >= res[1]);
    } else if (it.kind === 'image') {
      const res = pick(r, [[4032, 3024], [3024, 4032], [6000, 4000], [1920, 1280], [1080, 1350], [800, 600]]);
      const ext = it.name.split('.').pop().toLowerCase();
      const fmt = { jpg: 'JPEG', jpeg: 'JPEG', png: 'PNG', webp: 'WEBP', avif: 'AVIF', heic: 'HEIF', heif: 'HEIF', tif: 'TIFF', tiff: 'TIFF', bmp: 'BMP', gif: 'GIF' }[ext] || 'JPEG';
      it.probe = { duration: null, width: res[0], height: res[1], fps: null, vcodec: null, acodec: null, bitrate: null, sample_rate: null, channels: null, has_audio: false, format: fmt };
      const bpp = { JPEG: 0.32, PNG: 1.6, WEBP: 0.2, AVIF: 0.12, HEIF: 0.16, TIFF: 3, BMP: 3, GIF: 0.5 }[fmt];
      if (!it.size) it.size = Math.round(res[0] * res[1] * bpp * (0.7 + r() * 0.6));
      it.thumb = thumbPhoto(r, res[0] >= res[1]);
    } else {
      const duration = Math.round((40 + r() * 320) * 10) / 10;
      const ext = it.name.split('.').pop().toLowerCase();
      const acodec = { mp3: 'mp3', m4a: 'aac', aac: 'aac', wav: 'pcm_s16le', flac: 'flac', ogg: 'vorbis', opus: 'opus', wma: 'wmav2', aiff: 'pcm_s16be', aif: 'pcm_s16be' }[ext] || 'mp3';
      const bitrate = acodec.startsWith('pcm') ? 1411200 : acodec === 'flac' ? 880000 : pick(r, [128000, 192000, 256000, 320000]);
      it.probe = {
        duration, width: null, height: null, fps: null, vcodec: null, acodec, bitrate,
        sample_rate: pick(r, [44100, 48000]), channels: r() > 0.15 ? 2 : 1, has_audio: true,
        format: { mp3: 'mp3', m4a: 'mov,mp4,m4a,3gp,3g2,mj2', wav: 'wav', flac: 'flac', ogg: 'ogg', opus: 'ogg' }[ext] || ext,
      };
      if (!it.size) it.size = Math.round(bitrate * duration / 8);
      it.thumb = thumbWave(r);
    }
    it.status = 'ready';
    emitItem(pub(it));
  }

  // ---- jobs ------------------------------------------------------------------------------------------
  const queue = [];
  const running = new Map(); // id -> job
  let batch = null; // { done, failed, cancelled, total }
  let tickTimer = 0;
  const outNames = new Set();

  function extFor(it) {
    const f = it.settings.format;
    return f;
  }

  function outputPath(it) {
    const dir = settings.output_mode === 'folder' && settings.output_dir ? settings.output_dir : dirName(it.path);
    const stem = it.name.replace(/\.[^.]+$/, '');
    let name = `${stem}${settings.suffix}.${extFor(it)}`;
    let n = 2;
    while (outNames.has(`${dir}\\${name}`.toLowerCase())) name = `${stem}${settings.suffix} (${n++}).${extFor(it)}`;
    outNames.add(`${dir}\\${name}`.toLowerCase());
    return `${dir}\\${name}`;
  }

  function scaled(it) {
    const p = it.probe;
    if (!p || !p.width) return { w: null, h: null };
    let w = p.width;
    let h = p.height;
    const s = it.settings.scale;
    const k = { '2x': 2, '4x': 4, '0.5x': 0.5, '0.25x': 0.25 }[s];
    if (k) {
      w = Math.round(w * k);
      h = Math.round(h * k);
    } else if (/^\d+p$/.test(s)) {
      const target = parseInt(s, 10);
      const short = Math.min(w, h);
      if (short > target) {
        const f = target / short;
        w = Math.round((w * f) / 2) * 2;
        h = Math.round((h * f) / 2) * 2;
      }
    }
    return { w, h };
  }

  /** Plan of one job: stages with weights, wall time (s), output size, speed labels, failure. */
  function plan(it) {
    const s = it.settings;
    const p = it.probe;
    const r = Math.random;
    const limit = s.size_limit_mb != null ? s.size_limit_mb * 1024 * 1024 : null;
    const up = s.scale === '2x' || s.scale === '4x';
    const stages = [];
    let wall = 4;
    let out = it.size;
    let speedLabel = () => null;
    let error = null;

    if (it.kind === 'video') {
      const audioOnly = ['mp3', 'm4a', 'opus', 'flac', 'wav'].includes(s.format);
      const dur = p.duration || 30;
      const { w, h } = scaled(it);
      if (audioOnly) {
        const kbps = s.format === 'flac' ? 900 : s.format === 'wav' ? 1536 : s.audio_bitrate_k;
        out = (kbps * 1000 * dur) / 8;
        if (limit && (s.format === 'flac' || s.format === 'wav')) error = 'FLAC and WAV cannot be held to a size limit. Choose a lossy format or remove the limit.';
        else if (limit) out = Math.min(out, limit * (0.9 + r() * 0.08));
        const x = 40 + r() * 30;
        wall = dur / x;
        stages.push(['analyzing', 0.06], ['encoding', 0.88], ['writing', 0.06]);
        speedLabel = () => `${x.toFixed(0)}x`;
      } else {
        const gpu = s.encoder !== 'cpu' && gpuFor(s.codec);
        const base = { h264: 1.7, h265: 0.85, av1: 0.45, vp9: 0.55, prores: 2.4, copy: 30 }[s.codec] || 1;
        let x = (gpu ? base * 4.2 : base) * (1920 * 1080) / Math.max(1, w * h) * (0.8 + r() * 0.4);
        if (s.format === 'gif') x = 3;
        let model = false;
        if (up && s.upscale_method !== 'lanczos') {
          model = true;
          const fpsRate = s.upscale_method === 'edsr' ? 1.6 : 9;
          x = Math.min(x, fpsRate / (p.fps || 30));
        }
        if (up && s.upscale_method === 'lanczos') x *= 0.55;
        wall = dur / x;
        const fps = (p.fps || 30) * x;
        speedLabel = () => (model ? `${(fps * (0.9 + Math.random() * 0.2)).toFixed(fps < 10 ? 1 : 0)} fps` : `${(x * (0.92 + Math.random() * 0.16)).toFixed(1)}x`);
        stages.push(['analyzing', 0.04]);
        if (model && s.upscale_method === 'edsr' && caps.models.edsr !== 'ready') stages.push(['downloading model', 0.12]);
        if (s.codec === 'copy') {
          stages.push(['encoding', 0.9]);
          out = it.size * (0.97 + r() * 0.02);
        } else if (limit && s.format !== 'gif') {
          stages.push(['pass 1', 0.32], [model ? 'upscaling' : 'pass 2', 0.56]);
          out = limit * (0.9 + r() * 0.08);
          if (r() < 0.25) {
            stages.push(['retrying', 0.2]);
            wall *= 1.2;
          }
          wall *= 1.7;
        } else {
          stages.push([model ? 'upscaling' : 'encoding', 0.9]);
          const eff = { h264: 1, h265: 0.62, av1: 0.5, vp9: 0.66, prores: 9 }[s.codec] || 1;
          const bpp = 0.055 * Math.pow(2, (s.quality - 70) / 11) * eff;
          const fpsOut = s.fps === 'keep' ? p.fps || 30 : Number(s.fps);
          out = (w * h * Math.min(fpsOut, s.format === 'gif' ? 15 : 999) * bpp * dur) / 8;
          if (s.format === 'gif') out = w * h * Math.min(fpsOut, 15) * dur * 0.09;
          if (s.audio !== 'none' && p.has_audio && s.format !== 'gif') out += (s.audio_bitrate_k * 1000 * dur) / 8;
        }
        stages.push(['writing', 0.04]);
      }
    } else if (it.kind === 'image') {
      const { w, h } = scaled(it);
      const fmt = OPTIONS.image.formats.find((f) => f.id === s.format);
      const q = s.quality;
      const jpg = 0.04 + 0.32 * Math.pow(q / 100, 2.4);
      const bpp = { jpg, webp: jpg * 0.68, avif: jpg * 0.48, heic: jpg * 0.52, png: 1.7, tiff: 3, bmp: 3 }[s.format] || jpg;
      out = w * h * bpp * (fmt && !fmt.lossy ? 1 : 1);
      if (limit) {
        if (out > limit) out = limit * (0.86 + r() * 0.12);
      }
      const model = up && s.upscale_method !== 'lanczos';
      wall = 0.8 + (w * h) / 9e6 + (model ? (s.upscale_method === 'edsr' ? 9 : 3) : 0) + (limit ? 1.2 : 0);
      if (up) stages.push([model && s.upscale_method === 'edsr' && caps.models.edsr !== 'ready' ? 'downloading model' : 'upscaling', 0.45]);
      stages.push(['encoding', up ? 0.47 : 0.88], ['writing', 0.08]);
      const mbps = (it.size / 1024 / 1024) / Math.max(0.5, wall);
      speedLabel = () => `${Math.max(0.1, mbps * (0.9 + Math.random() * 0.2)).toFixed(1)} MB/s`;
    } else {
      const dur = p.duration || 120;
      const lossless = s.format === 'flac' || s.format === 'wav';
      if (limit && lossless) error = 'FLAC and WAV cannot be held to a size limit. Choose a lossy format or remove the limit.';
      let kbps = lossless ? (s.format === 'flac' ? 880 : 1411) : s.bitrate_k;
      if (s.format === 'opus') kbps = Math.min(kbps, 256);
      if (limit && !lossless) {
        const fit = (limit * 8) / dur / 1000;
        const allowed = OPTIONS.audio.bitrates.filter((b) => b <= fit);
        if (!allowed.length) error = 'The size limit is too small for the length of this file.';
        else kbps = Math.min(kbps, allowed[allowed.length - 1]);
      }
      out = (kbps * 1000 * dur) / 8;
      const x = 30 + Math.random() * 30;
      wall = dur / x + (s.normalize ? 2 : 0);
      if (s.normalize) stages.push(['analyzing', 0.3]);
      stages.push(['encoding', s.normalize ? 0.64 : 0.92], ['writing', s.normalize ? 0.06 : 0.08]);
      speedLabel = () => `${(x * (0.92 + Math.random() * 0.16)).toFixed(0)}x`;
    }
    wall = Math.min(26, Math.max(2.2, wall)) / pace;
    const fail = !error && Math.random() < 0.1 ? 0.25 + Math.random() * 0.5 : null;
    return { stages, wall, out: Math.max(1024, Math.round(out)), speedLabel, error, fail };
  }

  const FAILS = [
    'FFmpeg stopped with an error while encoding. The details are in the job log.',
    'The source has a damaged frame that the decoder could not read.',
    'The output could not be written. Check that the disk has free space.',
  ];

  function startTicking() {
    if (!tickTimer) tickTimer = setInterval(tick, 160);
  }

  function pump() {
    while (running.size < settings.parallel_jobs && queue.length) {
      const id = queue.shift();
      const it = items.get(id);
      if (!it || it.status !== 'queued') continue;
      const job = { id, plan: plan(it), t: 0, outPath: null };
      if (job.plan.error) {
        it.status = 'error';
        it.error = job.plan.error;
        it.progress = 0;
        batch && batch.failed++;
        emitItem(pub(it));
        continue;
      }
      job.outPath = outputPath(it);
      running.set(id, job);
      it.status = 'running';
      it.progress = 0;
      it.stage = job.plan.stages[0][0];
      emitItem(pub(it));
    }
    if (!running.size && !queue.length) {
      clearInterval(tickTimer);
      tickTimer = 0;
      if (batch && batch.done + batch.failed + batch.cancelled > 0) {
        emit('batch_done', { done: batch.done, failed: batch.failed, cancelled: batch.cancelled });
      }
      batch = null;
    }
  }

  function tick() {
    const dt = 0.16;
    for (const job of Array.from(running.values())) {
      const it = items.get(job.id);
      if (!it) {
        running.delete(job.id);
        continue;
      }
      job.t += dt * (0.85 + Math.random() * 0.3);
      const p = Math.min(1, job.t / job.plan.wall);
      // Stage from the cumulative weights.
      const total = job.plan.stages.reduce((a, s) => a + s[1], 0);
      let acc = 0;
      let stage = job.plan.stages[job.plan.stages.length - 1][0];
      for (const [name, w] of job.plan.stages) {
        acc += w / total;
        if (p <= acc) {
          stage = name;
          break;
        }
      }
      if (stage === 'downloading model' && caps.models.edsr !== 'downloading' && caps.models.edsr !== 'ready') {
        caps.models.edsr = 'downloading';
        emit('capabilities', caps);
      }
      if (stage !== 'downloading model' && caps.models.edsr === 'downloading') {
        caps.models.edsr = 'ready';
        emit('capabilities', caps);
      }
      if (job.plan.fail != null && p >= job.plan.fail) {
        running.delete(job.id);
        it.status = 'error';
        it.error = FAILS[Math.floor(Math.random() * FAILS.length)];
        it.stage = null;
        it.eta = null;
        it.speed = null;
        batch && batch.failed++;
        emitItem(pub(it));
        continue;
      }
      if (p >= 1) {
        running.delete(job.id);
        const { w, h } = scaled(it);
        const audioOut = it.kind === 'audio' || ['mp3', 'm4a', 'opus', 'flac', 'wav'].includes(it.settings.format);
        it.status = 'done';
        it.progress = 1;
        it.stage = null;
        it.eta = null;
        it.speed = null;
        it.output = {
          path: job.outPath,
          size: job.plan.out,
          width: audioOut ? null : w,
          height: audioOut ? null : h,
          duration: it.probe && it.probe.duration != null && it.kind !== 'image' ? it.probe.duration : null,
          ratio: Math.round((job.plan.out / it.size) * 1000) / 1000,
        };
        batch && batch.done++;
        emitItem(pub(it));
        continue;
      }
      it.progress = Math.round(p * 1000) / 1000;
      it.stage = stage;
      it.eta = stage === 'analyzing' && p < 0.03 ? null : Math.max(0, Math.round(job.plan.wall * (1 - p) * pace) / pace);
      it.speed = stage === 'downloading model' ? `${(3 + Math.random() * 6).toFixed(1)} MB/s` : stage === 'analyzing' ? null : job.plan.speedLabel();
      emitItem(pub(it));
    }
    pump();
  }

  function cancelItem(it) {
    const i = queue.indexOf(it.id);
    if (i >= 0) queue.splice(i, 1);
    const wasRunning = running.delete(it.id);
    if (it.status === 'queued' || it.status === 'running' || wasRunning) {
      it.status = 'ready';
      it.progress = 0;
      it.stage = null;
      it.eta = null;
      it.speed = null;
      batch && batch.cancelled++;
      emitItem(pub(it));
      return true;
    }
    return false;
  }

  // ---- update -------------------------------------------------------------------------------------
  let update = { stage: 'idle', current: VERSION, latest: null, notes: null, size: null, published_at: null, progress: null, message: 'Updates are not checked yet', checked_at: null, can_apply: false };
  let checks = 0;
  let busy = false;
  const NOTES = '## Media Forge 0.2.0\n\n- Size limits for GIF output\n- FSRCNN upscale uses all CPU cores\n- **Open when done** opens the output folder once per batch\n- Details in the [project notes](https://github.com/softoblivion/media-forge/releases)\n\n### Fixes\n\n- HEIC photos with a rotation flag are written upright\n- The size field accepts values like `0.5`';
  const setUpdate = (patch) => {
    update = { ...update, ...patch };
    emit('update', update);
    return clone(update);
  };

  function runCheck(launch) {
    if (busy) return clone(update);
    busy = true;
    checks++;
    setUpdate({ stage: 'checking', message: 'Checking for updates…', progress: null });
    setTimeout(() => {
      busy = false;
      const now = Date.now() / 1000;
      let result = launch ? (updateFlag === 'available' ? 'available' : updateFlag === 'error' ? 'error' : 'up_to_date') : updateFlag === 'uptodate' ? 'up_to_date' : 'available';
      if (!launch && updateFlag === 'error' && checks > 1) result = 'available';
      if (update.current !== VERSION) result = 'up_to_date';
      if (result === 'available') {
        setUpdate({ stage: 'available', latest: '0.2.0', notes: NOTES, size: 61_342_720, published_at: now - 3600 * 26, message: 'Version 0.2.0 is available', checked_at: now, can_apply: false });
      } else if (result === 'error') {
        setUpdate({ stage: 'error', latest: null, notes: null, size: null, published_at: null, message: 'No internet connection', checked_at: now });
      } else {
        setUpdate({ stage: 'up_to_date', latest: update.current, notes: null, size: null, published_at: null, message: 'Media Forge is up to date', checked_at: now });
      }
    }, 1300 / Math.sqrt(pace));
    return clone(update);
  }

  function runDownload() {
    if (busy || !(update.stage === 'available' || (update.stage === 'error' && update.latest))) return clone(update);
    busy = true;
    let p = 0;
    setUpdate({ stage: 'downloading', progress: 0, message: `Downloading Media Forge ${update.latest}… 0%` });
    const t = setInterval(() => {
      p = Math.min(1, p + 0.06 + Math.random() * 0.08);
      if (p >= 1) {
        clearInterval(t);
        busy = false;
        setUpdate({ stage: 'ready', progress: 1, can_apply: true, message: `Version ${update.latest} is ready to install` });
      } else {
        setUpdate({ progress: Math.round(p * 100) / 100, message: `Downloading Media Forge ${update.latest}… ${Math.round(p * 100)}%` });
      }
    }, 260);
    return clone(update);
  }

  // ---- generated thumbnails ---------------------------------------------------------------------
  function canvas(w, h) {
    const c = document.createElement('canvas');
    c.width = w;
    c.height = h;
    return [c, c.getContext('2d')];
  }

  function thumbVideo(r, landscape) {
    const [W, H] = landscape ? [240, 135] : [135, 240];
    const [c, g] = canvas(W, H);
    const hue = Math.floor(r() * 360);
    const sky = g.createLinearGradient(0, 0, 0, H);
    sky.addColorStop(0, `hsl(${hue} 60% ${18 + r() * 14}%)`);
    sky.addColorStop(0.6, `hsl(${(hue + 30) % 360} 70% ${40 + r() * 16}%)`);
    sky.addColorStop(1, `hsl(${(hue + 50) % 360} 70% ${55 + r() * 10}%)`);
    g.fillStyle = sky;
    g.fillRect(0, 0, W, H);
    g.fillStyle = `hsla(${(hue + 60) % 360} 90% 80% / .9)`;
    g.beginPath();
    g.arc(W * (0.2 + r() * 0.6), H * (0.3 + r() * 0.2), Math.min(W, H) * 0.09, 0, Math.PI * 2);
    g.fill();
    for (let l = 0; l < 3; l++) {
      g.fillStyle = `hsl(${(hue + 200) % 360} ${30 + l * 10}% ${22 - l * 6}%)`;
      g.beginPath();
      g.moveTo(0, H);
      const base = H * (0.6 + l * 0.12);
      for (let x = 0; x <= W; x += 6) g.lineTo(x, base - Math.sin(x / (18 + l * 9) + l * 2 + r()) * (8 + l * 3) - Math.sin(x / 7) * 1.5);
      g.lineTo(W, H);
      g.fill();
    }
    vignette(g, W, H);
    return c.toDataURL('image/jpeg', 0.82);
  }

  function thumbPhoto(r, landscape) {
    const [W, H] = landscape ? [240, 180] : [180, 240];
    const [c, g] = canvas(W, H);
    const hue = Math.floor(r() * 360);
    const grd = g.createLinearGradient(0, 0, W, H);
    grd.addColorStop(0, `hsl(${hue} 70% ${50 + r() * 15}%)`);
    grd.addColorStop(0.5, `hsl(${(hue + 40) % 360} 65% ${40 + r() * 15}%)`);
    grd.addColorStop(1, `hsl(${(hue + 90) % 360} 60% ${20 + r() * 10}%)`);
    g.fillStyle = grd;
    g.fillRect(0, 0, W, H);
    for (let i = 0; i < 9; i++) {
      const x = r() * W;
      const y = r() * H * 0.7;
      const rad = 6 + r() * 22;
      const b = g.createRadialGradient(x, y, 0, x, y, rad);
      b.addColorStop(0, `hsla(${(hue + 20) % 360} 100% 90% / ${0.25 + r() * 0.35})`);
      b.addColorStop(1, 'hsla(0 0% 100% / 0)');
      g.fillStyle = b;
      g.fillRect(x - rad, y - rad, rad * 2, rad * 2);
    }
    g.fillStyle = 'rgba(8,10,20,.78)';
    g.beginPath();
    const cx = W * (0.3 + r() * 0.4);
    g.ellipse(cx, H * 0.86, W * 0.5, H * 0.18, 0, 0, Math.PI * 2);
    g.fill();
    g.beginPath();
    g.arc(cx, H * 0.6, Math.min(W, H) * 0.08, 0, Math.PI * 2);
    g.fill();
    vignette(g, W, H);
    return c.toDataURL('image/jpeg', 0.84);
  }

  function thumbWave(r) {
    const [W, H] = [240, 96];
    const [c, g] = canvas(W, H);
    g.fillStyle = '#0a1230';
    g.fillRect(0, 0, W, H);
    const grd = g.createLinearGradient(0, 0, W, 0);
    grd.addColorStop(0, '#46d2ff');
    grd.addColorStop(0.55, '#7f8cff');
    grd.addColorStop(1, '#bb9cff');
    g.fillStyle = grd;
    let env = 0.4;
    for (let x = 0; x < W; x += 2) {
      env += (r() - 0.5) * 0.18;
      env = Math.min(0.95, Math.max(0.12, env));
      const a = env * (0.55 + r() * 0.45) * (H / 2 - 6);
      g.fillRect(x, H / 2 - a, 1.4, a * 2);
    }
    return c.toDataURL('image/jpeg', 0.86);
  }

  function vignette(g, W, H) {
    const v = g.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.3, W / 2, H / 2, Math.max(W, H) * 0.75);
    v.addColorStop(0, 'rgba(0,0,0,0)');
    v.addColorStop(1, 'rgba(0,0,0,.45)');
    g.fillStyle = v;
    g.fillRect(0, 0, W, H);
  }

  // ---- sample names ---------------------------------------------------------------------------------
  const SAMPLES = {
    video: ['holiday_lisbon.mp4', 'drone_coast_4k.mov', 'interview_take3.mkv', 'screen_recording_0912.mp4', 'birthday_party.mp4', 'timelapse_clouds.webm', 'GX010482.MP4', 'reel_draft_v2.mov', 'concert_front_row.mp4', 'tutorial_part1.mkv'],
    image: ['IMG_4821.HEIC', 'sunset_pier.jpg', 'portrait_studio.png', 'DSC09912.JPG', 'product_shot_white.png', 'scan_letter_1964.tiff', 'mountain_lake.webp', 'IMG_20261003_181244.jpg'],
    audio: ['voice_memo_0815.m4a', 'podcast_ep12_raw.wav', 'demo_track_master.flac', 'field_recording_rain.wav', 'lecture_week4.mp3', 'guitar_idea.m4a'],
  };
  const DIRS = ['C:\\Users\\user\\Videos', 'C:\\Users\\user\\Pictures', 'D:\\Media\\Inbox', 'C:\\Users\\user\\Desktop'];
  // Mixed kinds (about half video), each name used once; repeats get " (2)", " (3)" ...
  const KIND_CYCLE = ['video', 'image', 'video', 'audio', 'image', 'video', 'audio'];
  const nextIndex = { video: 0, image: 0, audio: 0 };
  let sampleN = 0;
  function samplePaths(n) {
    const out = [];
    for (let i = 0; i < n; i++) {
      const k = KIND_CYCLE[sampleN % KIND_CYCLE.length];
      const list = SAMPLES[k];
      const idx = nextIndex[k]++;
      const round = Math.floor(idx / list.length);
      let name = list[idx % list.length];
      if (round) name = name.replace(/(\.[^.]+)$/, ` (${round + 1})$1`);
      out.push(`${DIRS[sampleN % DIRS.length]}\\${name}`);
      sampleN++;
    }
    return out;
  }

  // ---- initial items from URL flags -----------------------------------------------------------------
  function seed() {
    const n = Math.min(200, Number(params.get('items') || 0));
    if (n > 0) {
      const added = addPaths(samplePaths(n));
      for (const a of added) finishProbe(items.get(a.id));
    }
    if (params.get('demo') === '1') {
      const paths = ['C:\\Users\\user\\Videos\\holiday_lisbon.mp4', 'C:\\Users\\user\\Pictures\\IMG_4821.HEIC', 'D:\\Media\\Inbox\\podcast_ep12_raw.wav', 'C:\\Users\\user\\Videos\\drone_coast_4k.mov', 'C:\\Users\\user\\Desktop\\notes.txt', 'C:\\Users\\user\\Pictures\\sunset_pier.jpg'];
      const added = addPaths(paths);
      for (const a of added) finishProbe(items.get(a.id));
      const [v, img, aud, v2] = added.map((a) => items.get(a.id));
      Object.assign(v, { status: 'done', progress: 1, output: { path: 'C:\\Users\\user\\Videos\\holiday_lisbon_forge.mp4', size: Math.round(v.size * 0.19), width: v.probe.width, height: v.probe.height, duration: v.probe.duration, ratio: 0.19 } });
      outNames.add(v.output.path.toLowerCase());
      Object.assign(img, { status: 'error', error: 'The output could not be written. Check that the disk has free space.' });
      Object.assign(v2, { status: 'running', progress: 0.42, stage: 'encoding', eta: 74, speed: '1.8x' });
      aud.settings = normalize('audio', aud.settings, { format: 'flac' });
      running.set(v2.id, { id: v2.id, plan: { ...plan(v2), fail: null, wall: 40 / pace }, t: (40 / pace) * 0.42, outPath: outputPath(v2) });
      // The probe events queued above carry the old status; queue the final state per item.
      for (const x of [v, img, aud, v2]) emitItem(pub(x));
      batch = { done: 0, failed: 0, cancelled: 0 };
      startTicking();
    }
  }

  // ---- methods (BRIDGE_API section 2) ---------------------------------------------------------------
  const delay = (ms) => new Promise((res) => setTimeout(res, ms));
  const find = (id) => {
    const it = items.get(id);
    if (!it) throw new Error('The file is no longer in the list.');
    return it;
  };

  const api = {
    async ready() {
      seed();
      const state = {
        version: VERSION,
        settings: clone(settings),
        items: Array.from(items.values()).map(pub),
        capabilities: clone(caps),
        options: clone(OPTIONS),
        update: clone(update),
        window: { focused: document.hasFocus() },
      };
      setTimeout(() => {
        live = true;
        for (const [n, p] of buffer.splice(0)) dispatch(n, p);
      }, 0);
      // Tool discovery in the background.
      const ffDelay = caps.ffmpeg === 'downloading' ? 4200 : 900;
      if (caps.ffmpeg === 'downloading') emit('toast', { kind: 'info', message: 'FFmpeg was not found. Downloading it once.' });
      setTimeout(() => {
        Object.assign(caps, { ffmpeg: 'ready', ffmpeg_version: '9.0.2', ffmpeg_path: 'C:\\Tools\\ffmpeg\\bin\\ffmpeg.exe' });
        emit('capabilities', caps);
      }, ffDelay / Math.sqrt(pace));
      setTimeout(() => runCheck(true), 2600);
      addEventListener('focus', () => emit('window', { focused: true }));
      addEventListener('blur', () => emit('window', { focused: false }));
      return state;
    },

    async add_files(paths) {
      return addPaths(Array.isArray(paths) ? paths : []);
    },

    async pick_files() {
      await delay(220);
      return addPaths(samplePaths(3 + Math.floor(Math.random() * 4)));
    },

    async remove_item(id) {
      const it = items.get(id);
      if (!it) return false;
      if (it.status === 'running' || it.status === 'queued') cancelItem(it);
      items.delete(id);
      pendingItems.delete(id);
      emit('item_removed', { id });
      pump();
      return true;
    },

    async clear(what) {
      const pickers = {
        done: (i) => i.status === 'done',
        errors: (i) => i.status === 'error',
        idle: (i) => i.status !== 'running',
        all: () => true,
      };
      const f = pickers[what];
      if (!f) throw new Error('Unknown clear mode.');
      const ids = [];
      for (const it of Array.from(items.values())) {
        if (!f(it)) continue;
        if (it.status === 'running' || it.status === 'queued') cancelItem(it);
        items.delete(it.id);
        pendingItems.delete(it.id);
        ids.push(it.id);
        emit('item_removed', { id: it.id });
      }
      pump();
      return ids;
    },

    async set_item_settings(id, patch) {
      const it = find(id);
      if (it.status === 'running') throw new Error('The file is being processed.');
      it.settings = normalize(it.kind, it.settings, patch || {});
      return pub(it);
    },

    async apply_to_kind(id) {
      const src = find(id);
      const changed = [];
      for (const it of items.values()) {
        if (it.id === src.id || it.kind !== src.kind || it.status === 'running' || it.status === 'queued') continue;
        it.settings = clone(src.settings);
        changed.push(pub(it));
        emitItem(pub(it));
      }
      return changed;
    },

    async start(ids) {
      if (caps.ffmpeg !== 'ready') throw new Error('FFmpeg is not ready yet.');
      const list = ids == null ? Array.from(items.values()) : ids.map((i) => items.get(i)).filter(Boolean);
      const queued = [];
      for (const it of list) {
        // ready; error = retry (a file that was read); done = run again (a new numbered output).
        const ok = it.status === 'ready' || (ids != null && ((it.status === 'error' && it.probe) || it.status === 'done'));
        if (!ok) continue;
        it.status = 'queued';
        it.progress = 0;
        it.stage = null;
        it.eta = null;
        it.speed = null;
        it.error = null;
        it.output = null;
        queue.push(it.id);
        queued.push(it.id);
        emitItem(pub(it));
      }
      if (queued.length) {
        if (!batch) batch = { done: 0, failed: 0, cancelled: 0 };
        startTicking();
        setTimeout(pump, 120);
      }
      return queued;
    },

    async cancel(id) {
      return cancelItem(find(id));
    },

    async cancel_all() {
      let n = 0;
      for (const it of Array.from(items.values())) if ((it.status === 'queued' || it.status === 'running') && cancelItem(it)) n++;
      pump();
      return n;
    },

    async get_settings() {
      return clone(settings);
    },

    async set_settings(patch) {
      try {
        const s = applySettingsPatch(patch);
        emit('settings', s);
        return clone(s);
      } catch (err) {
        emit('settings', settings);
        throw err;
      }
    },

    async pick_output_dir() {
      await delay(200);
      const dirs = ['D:\\Exports\\Media Forge', 'C:\\Users\\user\\Videos\\Forge'];
      return settings.output_dir === dirs[0] ? dirs[1] : dirs[0];
    },

    async reveal(path) {
      console.info('[mock] reveal', path);
      return true;
    },

    async open_path(path) {
      console.info('[mock] open_path', path);
      return true;
    },

    async capabilities() {
      return clone(caps);
    },

    async window_minimize() {
      console.info('[mock] window_minimize');
      return null;
    },

    async window_close() {
      console.info('[mock] window_close');
      return null;
    },

    async update_status() {
      return clone(update);
    },

    async check_updates() {
      return runCheck(false);
    },

    async download_update() {
      return runDownload();
    },

    async apply_update() {
      if (update.stage !== 'ready' || !update.can_apply) throw new Error('The update is not ready to install');
      // The real app closes here; the mock pretends the new version started.
      setTimeout(() => {
        const v = update.latest;
        setUpdate({ stage: 'up_to_date', current: v, latest: v, notes: null, size: null, progress: null, can_apply: false, message: 'Media Forge is up to date', checked_at: Date.now() / 1000 });
      }, 900);
      return true;
    },

    async open_url(url) {
      if (typeof url !== 'string' || !/^https:\/\//i.test(url)) return false;
      window.open(url, '_blank', 'noopener');
      return true;
    },

    async log(level, message) {
      console.debug(`[mock log:${level}]`, message);
      return null;
    },

    // Development hook: a drop in the browser. The real shell reads the paths itself and emits
    // `items_added`; the mock does the same with the dropped File names.
    _simulate_drop(files) {
      const paths = files.map((f) => `C:\\Users\\user\\Desktop\\${f.name}`);
      const added = addPaths(paths, files.map((f) => f.size || null));
      if (added.length) emit('items_added', { items: added });
    },
  };
  return api;
}

function buildOptions(caps) {
  const o = {
    video: {
      formats: [
        { id: 'mp4', label: 'MP4', codecs: ['h264', 'h265', 'av1'], audio: true },
        { id: 'mkv', label: 'MKV', codecs: ['h264', 'h265', 'av1', 'vp9', 'copy'], audio: true },
        { id: 'webm', label: 'WebM', codecs: ['vp9', 'av1'], audio: true },
        { id: 'mov', label: 'MOV', codecs: ['h264', 'h265', 'prores'], audio: true },
        { id: 'gif', label: 'GIF', codecs: [], audio: false },
        { id: 'mp3', label: 'MP3 · audio only', codecs: [], audio: true },
        { id: 'm4a', label: 'M4A · audio only', codecs: [], audio: true },
        { id: 'opus', label: 'Opus · audio only', codecs: [], audio: true },
        { id: 'flac', label: 'FLAC · audio only', codecs: [], audio: true },
        { id: 'wav', label: 'WAV · audio only', codecs: [], audio: true },
      ],
      codecs: [
        { id: 'h264', label: 'H.264', gpu: true },
        { id: 'h265', label: 'H.265 / HEVC', gpu: true },
        { id: 'av1', label: 'AV1', gpu: true },
        { id: 'vp9', label: 'VP9', gpu: false },
        { id: 'prores', label: 'ProRes', gpu: false },
        { id: 'copy', label: 'Copy stream', gpu: false },
      ],
      encoders: [{ id: 'auto', label: 'Auto' }, { id: 'cpu', label: 'CPU' }, { id: 'gpu', label: 'GPU' }],
      scales: [
        { id: 'none', label: 'Original' }, { id: '2x', label: '2×' }, { id: '4x', label: '4×' },
        { id: '1080p', label: 'Fit 1080p' }, { id: '720p', label: 'Fit 720p' }, { id: '480p', label: 'Fit 480p' },
      ],
      fps: [{ id: 'keep', label: 'Keep' }, { id: '24', label: '24' }, { id: '30', label: '30' }, { id: '60', label: '60' }],
      audio: [{ id: 'keep', label: 'Keep' }, { id: 'aac', label: 'AAC' }, { id: 'none', label: 'None' }],
      audio_bitrates: [96, 128, 192, 256, 320],
    },
    image: {
      formats: [
        { id: 'jpg', label: 'JPEG', lossy: true }, { id: 'png', label: 'PNG', lossy: false },
        { id: 'webp', label: 'WebP', lossy: true }, { id: 'avif', label: 'AVIF', lossy: true },
        { id: 'tiff', label: 'TIFF', lossy: false }, { id: 'bmp', label: 'BMP', lossy: false },
        { id: 'heic', label: 'HEIC', lossy: true },
      ],
      scales: [
        { id: 'none', label: 'Original' }, { id: '2x', label: '2×' }, { id: '4x', label: '4×' },
        { id: '0.5x', label: '½' }, { id: '0.25x', label: '¼' },
      ],
    },
    audio: {
      formats: [
        { id: 'mp3', label: 'MP3', lossy: true }, { id: 'm4a', label: 'M4A · AAC', lossy: true },
        { id: 'opus', label: 'Opus', lossy: true }, { id: 'ogg', label: 'OGG · Vorbis', lossy: true },
        { id: 'flac', label: 'FLAC', lossy: false }, { id: 'wav', label: 'WAV', lossy: false },
      ],
      bitrates: [64, 96, 128, 160, 192, 256, 320],
      sample_rates: [{ id: 'keep', label: 'Keep' }, { id: '44100', label: '44.1 kHz' }, { id: '48000', label: '48 kHz' }],
      channels: [{ id: 'keep', label: 'Keep' }, { id: '1', label: 'Mono' }, { id: '2', label: 'Stereo' }],
    },
    upscale_methods: [
      { id: 'lanczos', label: 'Lanczos', hint: 'Resampling with a light sharpen. Fast.' },
      { id: 'fsrcnn', label: 'FSRCNN', hint: 'Small deterministic super-resolution network, bundled. Moderate.' },
      { id: 'edsr', label: 'EDSR', hint: 'Photos only. Larger deterministic network, downloaded once (38 MB per scale). Slow, best detail.' },
    ],
    update_intervals: [
      { id: '30m', label: 'Every 30 minutes' }, { id: '1h', label: 'Every hour' }, { id: '6h', label: 'Every 6 hours' },
      { id: '1d', label: 'Every day' }, { id: '1w', label: 'Every week' },
    ],
  };
  if (!caps.heif) o.image.formats = o.image.formats.filter((f) => f.id !== 'heic');
  return o;
}

// Deterministic randomness per file name, so a file keeps its look and metadata.
function rng(seedText) {
  let h = 2166136261;
  for (let i = 0; i < seedText.length; i++) {
    h ^= seedText.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return () => {
    h += 0x6d2b79f5;
    let t = h;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pick(r, list) {
  return list[Math.floor(r() * list.length)];
}
