// Finish sound (Settings.sound_when_done): a short two-note chime synthesised with Web Audio when
// a batch ends with at least one finished file (`batch_done`). No audio file is shipped.

import { on } from './api.js';
import { store } from './store.js';

let ctx = null;
let last = 0;

export function initSound() {
  on('batch_done', (p) => {
    if (!p || !p.done || !(store.settings && store.settings.sound_when_done)) return;
    chime();
  });
}

function chime() {
  const now = Date.now();
  if (now - last < 1000) return;
  last = now;
  try {
    ctx = ctx || new AudioContext();
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    const t0 = ctx.currentTime + 0.02;
    const out = ctx.createGain();
    out.gain.value = 0.16;
    out.connect(ctx.destination);
    [[880, 0], [1318.5, 0.11]].forEach(([f, dt]) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = 'sine';
      o.frequency.value = f;
      g.gain.setValueAtTime(0, t0 + dt);
      g.gain.linearRampToValueAtTime(1, t0 + dt + 0.012);
      g.gain.exponentialRampToValueAtTime(0.0008, t0 + dt + 0.55);
      o.connect(g).connect(out);
      o.start(t0 + dt);
      o.stop(t0 + dt + 0.6);
    });
  } catch {
    /* audio unavailable: silent */
  }
}
