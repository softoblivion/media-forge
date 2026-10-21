// Media Forge icons: the line icons come from the Liquid UI kit (vendor/liquid_ui/js/icons.js);
// this module adds the kind map and the app glyph.

import { icon } from '../vendor/liquid_ui/js/icons.js';

export { icon };

export const KIND_ICON = { video: 'video', image: 'image', audio: 'audio' };

/**
 * The app glyph: an isometric block (the "forged" file) in cobalt-to-violet glass with a lit top
 * face and a small ember spark. Same drawing as assets/icon.svg.
 */
export function appGlyph(size = 20) {
  return `<svg class="app-glyph" width="${size}" height="${size}" viewBox="0 0 64 64" aria-hidden="true">
    <defs>
      <linearGradient id="mf-g-side" x1="10" y1="20" x2="54" y2="60" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="#3cc4ff"/><stop offset=".5" stop-color="#4a7dff"/><stop offset="1" stop-color="#7457ff"/>
      </linearGradient>
      <linearGradient id="mf-g-top" x1="14" y1="10" x2="50" y2="30" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="#d9f3ff"/><stop offset="1" stop-color="#8fb6ff"/>
      </linearGradient>
    </defs>
    <path d="M32 7 54 19.5v25L32 57 10 44.5v-25Z" fill="url(#mf-g-side)"/>
    <path d="M32 7 54 19.5 32 32 10 19.5Z" fill="url(#mf-g-top)" opacity=".92"/>
    <path d="M32 32v25L10 44.5v-25Z" fill="#0b1a4a" opacity=".28"/>
    <path d="M32 7 54 19.5v25L32 57 10 44.5v-25Z" fill="none" stroke="rgba(255,255,255,.55)" stroke-width="1.6" stroke-linejoin="round"/>
    <path d="M10 19.5 32 32l22-12.5M32 32v25" fill="none" stroke="rgba(255,255,255,.42)" stroke-width="1.4" stroke-linejoin="round"/>
    <path d="M55 2.5l1.6 4.4 4.4 1.6-4.4 1.6-1.6 4.4-1.6-4.4-4.4-1.6 4.4-1.6Z" fill="#ffb37a"/>
  </svg>`;
}
