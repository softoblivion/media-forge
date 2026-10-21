// Window-wide drop target (BRIDGE_API section 5). The page only shows the veil and prevents the
// browser from navigating to a dropped file; it never reads paths from the DOM event. The shell's
// Python drop listener adds the files and emits `items_added`. In the mock, the dropped File
// objects become simulated items by name (mock hook `_simulate_drop`).
//
// Veil: a sheet lens over the whole window materialises (lensing ramps in), a soft ripple ring
// expands from the pointer on dragenter, and the drop target pulses once on drop.

import { mockHooks } from './api.js';
import { Lens } from '../vendor/liquid_ui/js/glass.js';
import { SPRINGS, reducedMotion } from '../vendor/liquid_ui/js/motion.js';
import { icon } from './icons.js';

let veil;
let lens;
let depth = 0;
let visible = false;

const hasFiles = (e) => {
  const t = e.dataTransfer && e.dataTransfer.types;
  return !!t && Array.from(t).includes('Files');
};

export function initDropzone() {
  veil = document.getElementById('veil');
  veil.innerHTML = `
    <div class="veil-dim" aria-hidden="true"></div>
    <div class="veil-glass">
      <div class="veil-ring" aria-hidden="true"></div>
      <div class="veil-inner">
        <span class="veil-ic">${icon('tray')}</span>
        <b>Drop to add files</b>
        <span>Video, photo and audio. A folder adds the media files inside it.</span>
      </div>
    </div>`;
  lens = new Lens(veil.querySelector('.veil-glass'), 'sheet', { strength: 0 });

  addEventListener('dragenter', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth++;
    if (depth === 1) show(e.clientX, e.clientY);
  });
  addEventListener('dragover', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });
  addEventListener('dragleave', (e) => {
    if (!hasFiles(e)) return;
    depth = Math.max(0, depth - 1);
    if (depth === 0 || (e.clientX <= 0 && e.clientY <= 0)) hide(false);
  });
  addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
    hide(true);
    const hooks = mockHooks();
    if (hooks && hooks._simulate_drop && files.length) hooks._simulate_drop(files.map((f) => ({ name: f.name, size: f.size })));
  });
}

function show(x, y) {
  if (visible) return;
  visible = true;
  veil.hidden = false;
  veil.classList.remove('is-dropped');
  lens.update();
  lens.animateStrength(1, SPRINGS.materialize);
  // Opacity only on the dim layer and on the lens itself, never on their parent (backdrop root).
  for (const part of parts()) part.getAnimations().forEach((a) => a.cancel());
  for (const part of parts()) part.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 180, easing: 'ease-out' });
  if (!reducedMotion.matches) {
    veil.querySelector('.veil-glass').animate([{ transform: 'scale(0.97)' }, { transform: 'none' }], { duration: 420, easing: 'cubic-bezier(.2,1.2,.3,1)', composite: 'add' });
    ripple(x, y);
  }
}

function hide(dropped) {
  if (!visible) return;
  visible = false;
  if (dropped) veil.classList.add('is-dropped');
  lens.animateStrength(0, SPRINGS.sheetClose);
  const anims = parts().map((p) => p.animate([{ opacity: 1 }, { opacity: 0 }], { duration: dropped ? 380 : 200, easing: 'ease-in', delay: dropped ? 120 : 0, fill: 'forwards' }));
  let finished = false;
  const done = () => {
    if (finished) return;
    finished = true;
    if (!visible) {
      veil.hidden = true;
      anims.forEach((a) => a.cancel());
    }
  };
  anims[0].onfinish = done;
  setTimeout(done, 900);
}

const parts = () => [veil.querySelector('.veil-dim'), veil.querySelector('.veil-glass')];

function ripple(x, y) {
  const r = document.createElement('span');
  r.className = 'veil-ripple';
  r.style.left = `${x || innerWidth / 2}px`;
  r.style.top = `${y || innerHeight / 2}px`;
  veil.appendChild(r);
  const a = r.animate([
    { transform: 'translate(-50%, -50%) scale(0.05)', opacity: 0.9 },
    { transform: 'translate(-50%, -50%) scale(1)', opacity: 0 },
  ], { duration: 1100, easing: 'cubic-bezier(.15,.7,.25,1)' });
  a.onfinish = () => r.remove();
  setTimeout(() => r.remove(), 1500);
}
