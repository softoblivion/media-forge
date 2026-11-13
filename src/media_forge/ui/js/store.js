// Shared page state: what the bridge last confirmed (State, events). Components read from here
// and subscribe to the keys they show; only the module that owns a key writes it:
//   items        js/list.js      (item, item_removed, items_added events, method results)
//   settings     js/settings.js  (settings event, set_settings results; the kit's update rows
//                                 report their saves through main.js onSettings)
//   caps         js/main.js      (capabilities event)
//   options      js/main.js      (State only; the option tables never change at runtime)
//   update       js/main.js      (the kit's update control, onStatus of the update event)
//   scales       js/main.js      (WindowState.scales of State and the window event: the Scale steps
//                                 that fit the screen; null while the shell has not said)
//   scale        js/main.js      (WindowState.scale: the Scale step applied to the window, below
//                                 settings.ui_scale while that one does not fit; null while unknown)

export const store = {
  version: '',
  settings: null,
  caps: null,
  options: null,
  update: null,
  scales: null,
  scale: null,
  items: new Map(), // id -> Item, in list order
};

const subs = new Map();

/** Subscribe to a key; returns an unsubscribe function. */
export function watch(key, fn) {
  if (!subs.has(key)) subs.set(key, new Set());
  subs.get(key).add(fn);
  return () => subs.get(key).delete(fn);
}

export function notify(key, detail) {
  const set = subs.get(key);
  if (!set) return;
  for (const fn of Array.from(set)) {
    try {
      fn(detail);
    } catch (err) {
      console.error(`[ui] store watcher for "${key}" failed`, err);
    }
  }
}

/** The Scale step the Settings sheet shows for the saved step `saved`: the step applied to the window, which is
 *  below the saved one while that one does not fit this screen (the saved step comes back when it fits again). */
export function shownScale(saved) {
  const fits = store.scales;
  if (!Array.isArray(fits) || fits.includes(saved) || !Number.isInteger(store.scale)) return saved;
  return store.scale;
}

/** The option list of a kind's formats. */
export function formatsOf(kind) {
  const o = store.options;
  return (o && o[kind] && o[kind].formats) || [];
}

/** True when the codec can use NVENC on this machine (Options.codecs[].gpu and Capabilities.nvenc). */
export function gpuFor(codec) {
  const o = store.options;
  const c = o && o.video.codecs.find((x) => x.id === codec);
  const nv = store.caps && store.caps.nvenc;
  return !!(c && c.gpu && nv && nv[codec]);
}

export function anyGpu() {
  const nv = store.caps && store.caps.nvenc;
  return !!(nv && Object.values(nv).some(Boolean));
}
