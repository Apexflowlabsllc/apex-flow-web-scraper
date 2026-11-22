/* Runs the crawl scene off the page thread, so drawing thousands of bots never slows scrolling or typing. */
import { run } from './engine.js';

let api = null;
const queued = [];
self.onmessage = (e) => {
  const m = e.data;
  if (m.t !== 'init') { if (api) api.input(m); else queued.push(m); return; }
  run(m.canvas, m.cfg, (msg) => self.postMessage(msg))
    .then((made) => { api = made; queued.splice(0).forEach((q) => api.input(q)); })
    .catch((err) => self.postMessage({ t: 'fail', m: String((err && err.message) || err) }));
};
