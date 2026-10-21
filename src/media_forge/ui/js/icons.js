// Original line icons (24 x 24 grid, 1.8 px stroke, round caps) and the app glyph.
// icon('gear') returns an inline SVG string.

const P = {
  gear: '<path d="M12 8.6a3.4 3.4 0 1 0 0 6.8 3.4 3.4 0 0 0 0-6.8Z"/><path d="M19.4 13.5a7.6 7.6 0 0 0 0-3l1.8-1.4-1.8-3.1-2.2.8a7.5 7.5 0 0 0-2.6-1.5L14.2 3h-3.6l-.4 2.3a7.5 7.5 0 0 0-2.6 1.5l-2.2-.8-1.8 3.1 1.8 1.4a7.6 7.6 0 0 0 0 3l-1.8 1.4 1.8 3.1 2.2-.8a7.5 7.5 0 0 0 2.6 1.5l.4 2.3h3.6l.4-2.3a7.5 7.5 0 0 0 2.6-1.5l2.2.8 1.8-3.1Z"/>',
  min: '<path d="M6 12h12"/>',
  close: '<path d="M7 7l10 10M17 7 7 17"/>',
  x: '<path d="M7.5 7.5l9 9M16.5 7.5l-9 9"/>',
  plus: '<path d="M12 5.5v13M5.5 12h13"/>',
  minus: '<path d="M5.5 12h13"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  warn: '<path d="M12 4.5 2.8 19.5h18.4Z"/><path d="M12 10v4.2"/><path d="M12 17.2v.1"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><path d="M12 7.8v.1"/>',
  retry: '<path d="M4.5 12a7.5 7.5 0 0 1 13-5.1L19.5 9"/><path d="M19.5 4.5V9H15"/><path d="M19.5 12a7.5 7.5 0 0 1-13 5.1L4.5 15"/><path d="M4.5 19.5V15H9"/>',
  refresh: '<path d="M19 12a7 7 0 1 1-2.1-5"/><path d="M19 4.5V9h-4.5"/>',
  folder: '<path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h3.6l2 2.2h7.4a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2Z"/>',
  reveal: '<path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h3.6l2 2.2h7.4a2 2 0 0 1 2 2v2.3"/><path d="M3.5 7.5V17a2 2 0 0 0 2 2h5"/><circle cx="16.5" cy="16" r="3"/><path d="m18.7 18.2 2.3 2.3"/>',
  open: '<path d="M13.5 4.5h6v6"/><path d="M19.5 4.5 11 13"/><path d="M18 14v3.5a2 2 0 0 1-2 2H6.5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2H10"/>',
  play: '<path d="M8 5.8v12.4a1 1 0 0 0 1.5.9l9.7-6.2a1 1 0 0 0 0-1.7L9.5 4.9A1 1 0 0 0 8 5.8Z"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2.4"/>',
  trash: '<path d="M4.5 7h15"/><path d="M9.5 7V5.4A1.4 1.4 0 0 1 10.9 4h2.2a1.4 1.4 0 0 1 1.4 1.4V7"/><path d="m6.5 7 .8 11.4A1.8 1.8 0 0 0 9.1 20h5.8a1.8 1.8 0 0 0 1.8-1.6L17.5 7"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  arrow: '<path d="M4.5 12h14"/><path d="m13.5 7 5 5-5 5"/>',
  down: '<path d="M12 4v11.5"/><path d="m6.5 10.5 5.5 5.5 5.5-5.5"/><path d="M5 20h14"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  copyAll: '<rect x="8.5" y="8.5" width="11" height="11" rx="2.6"/><path d="M15.5 8.5V6.6a2.1 2.1 0 0 0-2.1-2.1H6.6a2.1 2.1 0 0 0-2.1 2.1v6.8a2.1 2.1 0 0 0 2.1 2.1h1.9"/><path d="m11.5 14 1.8 1.8 3.2-3.6"/>',
  video: '<rect x="2.5" y="5.5" width="19" height="13" rx="3.5"/><path d="m10.3 9.6 4.2 2.4-4.2 2.4Z"/>',
  image: '<rect x="3.5" y="4.5" width="17" height="15" rx="3.5"/><path d="M3.5 16 9 11l4 3.5 2.5-2 5 4"/><circle cx="15.5" cy="9" r="1.5"/>',
  audio: '<path d="M4 12h1.5M8 8.5v7M11.5 5v14M15 8v8M18.5 10.5v3M20 12h.5"/>',
  file: '<path d="M7 3.5h6.5L18 8v11a1.5 1.5 0 0 1-1.5 1.5h-9.5A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5Z"/><path d="M13 3.5V8.5h5"/>',
  sparkle: '<path d="M10 3.5 11.6 8a2 2 0 0 0 1.3 1.3l4.6 1.6-4.6 1.6a2 2 0 0 0-1.3 1.3L10 18.4l-1.6-4.6a2 2 0 0 0-1.3-1.3L2.5 10.9l4.6-1.6A2 2 0 0 0 8.4 8Z"/><path d="M18 14.5v5M15.5 17h5"/>',
  chip: '<rect x="6.5" y="6.5" width="11" height="11" rx="2.2"/><path d="M9.5 3.5v3M14.5 3.5v3M9.5 17.5v3M14.5 17.5v3M3.5 9.5h3M3.5 14.5h3M17.5 9.5h3M17.5 14.5h3"/>',
  layers: '<path d="m12 4 8.5 4.5L12 13 3.5 8.5Z"/><path d="m3.5 12.5 8.5 4.5 8.5-4.5"/><path d="m3.5 16.5 8.5 4.5 8.5-4.5"/>',
  tray: '<path d="M12 3.5v10"/><path d="m8 10 4 4 4-4"/><path d="M4.5 13.5V17a2.5 2.5 0 0 0 2.5 2.5h10a2.5 2.5 0 0 0 2.5-2.5v-3.5"/>',
  app: '<path d="M12 3.5 19.5 7.7v8.6L12 20.5l-7.5-4.2V7.7Z"/><path d="M4.5 7.7 12 12l7.5-4.3M12 12v8.5"/>',
};

export function icon(name, cls = '') {
  const body = P[name] || P.info;
  const fill = name === 'play' ? ' fill="currentColor"' : ' fill="none"';
  return `<svg class="ic ${cls}" viewBox="0 0 24 24"${fill} stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

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
