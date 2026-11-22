/* Static layout of the crawl scene: the main page, linked pages, link ribbons, the engine and walk targets. */
import { qAxis, qMul, qRot } from './gl.js';

export const PAGE_W = 16, PAGE_D = 10;
const DEG = Math.PI / 180;

function surface(w, d, pos, yaw, pitch, atlas) {
  const q = qMul(qAxis(0, 1, 0, yaw * DEG), qAxis(1, 0, 0, pitch * DEG));
  return { w, d, pos, q, atlas, ex: qRot(q, [1, 0, 0]), ey: qRot(q, [0, 1, 0]), ez: qRot(q, [0, 0, 1]), targets: null, bridge: -1 };
}
const at = (s, x, z, lift = 0) => [s.pos[0] + s.ex[0] * x + s.ez[0] * z + s.ey[0] * lift, s.pos[1] + s.ex[1] * x + s.ez[1] * z + s.ey[1] * lift, s.pos[2] + s.ex[2] * x + s.ez[2] * z + s.ey[2] * lift];
const add = (a, b, k = 1) => [a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k];
const unit = (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

export function bezier(b, t, out) {
  const m = 1 - t, a = m * m * m, c = 3 * m * m * t, d = 3 * m * t * t, e = t * t * t;
  for (let i = 0; i < 3; i++) out[i] = a * b.p[0][i] + c * b.p[1][i] + d * b.p[2][i] + e * b.p[3][i];
  return out;
}
export function bezierTangent(b, t, out) {
  const m = 1 - t;
  for (let i = 0; i < 3; i++) out[i] = 3 * m * m * (b.p[1][i] - b.p[0][i]) + 6 * m * t * (b.p[2][i] - b.p[1][i]) + 3 * t * t * (b.p[3][i] - b.p[2][i]);
  const l = Math.hypot(out[0], out[1], out[2]) || 1; out[0] /= l; out[1] /= l; out[2] /= l;
  return out;
}

/* Walk targets from the painted content boxes: [x, z, element] triples in page units, in reading order. */
function mainTargets(elements) {
  const pts = [], spans = [];
  elements.forEach((e, idx) => {
    const x0 = (e.x - 0.5) * PAGE_W, z0 = (e.y - 0.5) * PAGE_D, w = e.w * PAGE_W, d = e.h * PAGE_D;
    const start = pts.length / 3;
    const rows = Math.max(1, Math.round(d / 0.34)), cols = Math.max(1, Math.round(w / 0.36));
    for (let r = 0; r < rows; r++) for (let c = 0; c <= cols; c++) {
      const cc = r % 2 ? cols - c : c;   // serpentine, like reading a block line by line
      pts.push(x0 + (w * cc) / cols, z0 + (d * (r + 0.5)) / rows, idx);
    }
    spans.push([start, pts.length / 3 - start]);
  });
  return { pts: new Float32Array(pts), spans };
}

function gridTargets(s) {
  const pts = [];
  for (let r = 0; r < 4; r++) for (let c = 0; c < 7; c++) {
    const cc = r % 2 ? 6 - c : c;
    pts.push((cc / 6 - 0.5) * s.w * 0.86, (r / 3 - 0.5) * s.d * 0.62 + s.d * 0.08, -1);
  }
  return { pts: new Float32Array(pts), spans: [] };
}

export function makeWorld(elements, compact) {
  const main = surface(PAGE_W, PAGE_D, [0, 0, 0], 0, 0, -1);
  main.targets = mainTargets(elements);
  // On a phone the copy sits above the page, so the linked pages are left out rather than drawn behind the text.
  const sats = compact ? [] : [
    surface(5.2, 3.25, [9.0, 2.9, -8.6], -16, 64, 0), surface(5.4, 3.4, [1.6, 3.5, -10.4], -4, 68, 3),
    surface(3.7, 2.3, [12.9, 1.25, -3.3], -38, 54, 1)
  ];
  sats.forEach((s) => { s.targets = gridTargets(s); });
  const surfaces = [main, ...sats];

  /* One ribbon from the main page (or from another linked page) to each linked page. */
  const bridges = [];
  const link = (a, pa, outA, b, pb, outB, reach = 2.4) => {
    const p0 = at(surfaces[a], pa[0], pa[1], 0.02), p3 = at(surfaces[b], pb[0], pb[1], 0.02);
    const br = { a, b, pa, pb, p: [p0, add(p0, outA, reach), add(p3, outB, reach), p3], na: surfaces[a].ey, nb: surfaces[b].ey, len: 0, spawn: false };
    bridges.push(br); surfaces[b].bridge = bridges.length - 1;
    return br;
  };
  const leftOf = (s) => [[-s.w / 2, 0.1 * s.d], unit(add([0, 0, 0], s.ex, -1))];
  const lowOf = (s) => [[-0.15 * s.w, s.d / 2], unit(s.ez)];
  if (!compact) {
    link(0, [6.2, -5], [0.2, 0.3, -1], 1, ...lowOf(sats[0]), 1.9);
    link(0, [0.4, -5], [0, 0.3, -1], 2, ...lowOf(sats[1]), 2.6);
    link(0, [8, -2.6], [1, 0.3, 0], 3, ...leftOf(sats[2]), 1.5);
  }

  const tmp = [0, 0, 0], prev = [0, 0, 0];
  for (const b of bridges) {
    bezier(b, 0, prev);
    for (let i = 1; i <= 40; i++) { bezier(b, i / 40, tmp); b.len += Math.hypot(tmp[0] - prev[0], tmp[1] - prev[1], tmp[2] - prev[2]); prev[0] = tmp[0]; prev[1] = tmp[1]; prev[2] = tmp[2]; }
  }
  /* The data grid the records flow into: upright, beside the page, turned to the camera. */
  const data = surface(4.2, 5.6, compact ? [6.2, 3.6, -5.2] : [12.4, 3.4, -1.4], compact ? -10 : -30, 78, -1);
  const recs = elements.filter((e) => e.record >= 0).sort((a, b) => a.record - b.record);
  const rowPos = recs.map((e, r) => at(data, -data.w / 2, ((r + 1.5) / 8 - 0.5) * data.d, 0.05));
  const streams = recs.map((e, r) => {
    const p0 = [(e.bx - 0.5) * PAGE_W, 0.05, (e.by - 0.5) * PAGE_D], p3 = rowPos[r];
    return { p: [p0, add(p0, [0.9, 1.5 + r * 0.12, -0.5]), add(p3, data.ex, -3.2), p3], from: p0 };
  });
  /* A field of other pages running off behind the main one. */
  const tiles = [];
  if (!compact) for (let j = 0; j < 3; j++) for (let i = 0; i < 8; i++) tiles.push(surface(7.4, 4.6, [-30 + i * 8.4 + (j % 2) * 3, -0.5 - j * 0.25, -9.6 - j * 5.3], 0, 0, (i + j * 3) % 4));
  /* Where the large robots stand when the page opens, and the area they keep to (x min, x max, z min, z max). */
  const stage = compact ? [[-0.6, 2.6], [2.4, 1.2], [-3.0, 0.6], [1.0, -1.6]] : [[0.5, 2.7], [-2.9, 1.3], [3.6, 0.9], [-0.3, -1.1], [5.6, 2.6], [-4.4, -2.4], [3.0, -2.6]];
  const stageBox = compact ? [-4.5, 4.5, -2.5, 3.6] : [-6.6, 6.2, -3.2, 3.9];
  return { surfaces, bridges, elements, stage, stageBox, data, rowPos, streams, tiles };
}

/* Ribbon geometry for every bridge: position xyz, then t, side, length. */
export function ribbonMesh(world, half = 0.2, seg = 44) {
  const v = [], P = [0, 0, 0], T = [0, 0, 0];
  const edge = (b, t) => {
    bezier(b, t, P); bezierTangent(b, t, T);
    const n = unit([b.na[0] + (b.nb[0] - b.na[0]) * t, b.na[1] + (b.nb[1] - b.na[1]) * t, b.na[2] + (b.nb[2] - b.na[2]) * t]);
    const s = unit([T[1] * n[2] - T[2] * n[1], T[2] * n[0] - T[0] * n[2], T[0] * n[1] - T[1] * n[0]]);
    return [[P[0] - s[0] * half, P[1] - s[1] * half, P[2] - s[2] * half], [P[0] + s[0] * half, P[1] + s[1] * half, P[2] + s[2] * half]];
  };
  for (const b of world.bridges) {
    let a = edge(b, 0);
    for (let i = 1; i <= seg; i++) {
      const t0 = (i - 1) / seg, t1 = i / seg, c = edge(b, t1);
      const quad = [[a[0], t0, -1], [a[1], t0, 1], [c[1], t1, 1], [a[0], t0, -1], [c[1], t1, 1], [c[0], t1, -1]];
      for (const [p, t, side] of quad) v.push(p[0], p[1], p[2], t, side, b.len);
      a = c;
    }
  }
  return new Float32Array(v);
}

/* Thin ribbons for the data streams: position xyz, then t along the stream, side, stream number. */
export function streamMesh(world, half = 0.06, seg = 48) {
  const v = [], P = [0, 0, 0], T = [0, 0, 0];
  world.streams.forEach((b, n) => {
    const edge = (t) => { bezier(b, t, P); bezierTangent(b, t, T); const s = unit([T[2], 0, -T[0]]); return [[P[0] - s[0] * half, P[1], P[2] - s[2] * half], [P[0] + s[0] * half, P[1], P[2] + s[2] * half]]; };
    let a = edge(0);
    for (let i = 1; i <= seg; i++) {
      const t0 = (i - 1) / seg, t1 = i / seg, c = edge(t1);
      for (const [p, t, side] of [[a[0], t0, -1], [a[1], t0, 1], [c[1], t1, 1], [a[0], t0, -1], [c[1], t1, 1], [c[0], t1, -1]]) v.push(p[0], p[1], p[2], t, side, n);
      a = c;
    }
  });
  return new Float32Array(v);
}
