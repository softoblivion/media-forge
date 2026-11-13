// Moving and top-edge resizing of the resizable window by the system (window_drag_start, BRIDGE_API 2).
// The left, right and bottom edges are the window's own sizing border, outside the page. The top edge
// belongs to the page (the title bar reaches it), so a thin strip along it hands a press to the shell,
// which starts the system's resize from that edge; the strip carries the system resize cursors. A press
// on the title bar's drag region starts the system's move instead of pywebview's script move, so a move
// is exact at every Scale and DPI and carries the window onto another monitor. Both stay off until the
// shell reports that it can take a drag over (WindowState `drag`).

import { call, backendMode } from './api.js';

const EDGES = ['top-left', 'top', 'top-right'];
const DRAG_REGION = '.pywebview-drag-region';

let enabled = false;
let grip = null;

function press(e, part) {
  if (e.button !== 0) return;
  // preventDefault on pointerdown also suppresses the compatibility mousedown, on which pywebview's
  // own script drag starts.
  e.preventDefault();
  call('window_drag_start', part).catch(() => {});
}

export function initWindowDrag() {
  if (backendMode() !== 'pywebview') return; // a browser window has its own edges
  grip = document.createElement('div');
  grip.className = 'top-grip';
  grip.hidden = true;
  grip.setAttribute('aria-hidden', 'true');
  grip.innerHTML = EDGES.map((edge) => `<span data-edge="${edge}"></span>`).join('');
  grip.addEventListener('pointerdown', (e) => {
    const part = e.target.closest('[data-edge]');
    if (part) press(e, part.dataset.edge);
  });
  grip.addEventListener('dblclick', (e) => e.preventDefault());
  document.body.append(grip);
  addEventListener('pointerdown', (e) => {
    if (enabled && e.target.closest && e.target.closest(DRAG_REGION)) press(e, 'move');
  }, true);
}

/** WindowState `drag`: the shell starts the system's move and top-edge resize. */
export function setWindowDrag(on) {
  enabled = !!on;
  if (grip) grip.hidden = !enabled;
}
