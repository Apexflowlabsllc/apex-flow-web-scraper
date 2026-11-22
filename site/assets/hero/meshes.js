/* Procedural meshes for the crawl scene. Every vertex is 10 floats:
   position xyz, normal xyz, material id, leg id (-1 = body), leg weight (0 hip .. 1 foot), gait group. */

export const MAT = { BODY: 0, PLATE: 1, JOINT: 2, EYE: 3, CORE: 4 };

function builder() {
  const v = [];
  const push = (p, n, mat, leg = -1, w = 0, g = 0) => v.push(p[0], p[1], p[2], n[0], n[1], n[2], mat, leg, w, g);
  const norm = (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

  function quad(a, b, c, d, mat, leg, g) {   // each corner: {p, n, w}
    for (const k of [a, b, c, a, c, d]) push(k.p, k.n, mat, leg, k.w || 0, g);
  }

  function ellipsoid(c, r, mat, rings = 6, sides = 10, leg = -1, w = 0, g = 0) {
    const at = (i, j) => {
      const th = (i / rings) * Math.PI, ph = (j / sides) * Math.PI * 2;
      const d = [Math.sin(th) * Math.cos(ph), Math.cos(th), Math.sin(th) * Math.sin(ph)];
      return { p: [c[0] + d[0] * r[0], c[1] + d[1] * r[1], c[2] + d[2] * r[2]], n: norm([d[0] / r[0], d[1] / r[1], d[2] / r[2]]), w };
    };
    for (let i = 0; i < rings; i++) for (let j = 0; j < sides; j++) quad(at(i, j), at(i, j + 1), at(i + 1, j + 1), at(i + 1, j), mat, leg, g);
  }

  /* Tapered tube from a to b. Weights w0/w1 drive the leg animation in the vertex shader. */
  function tube(a, b, r0, r1, mat, sides = 5, leg = -1, w0 = 0, w1 = 0, g = 0, caps = false) {
    const ax = norm([b[0] - a[0], b[1] - a[1], b[2] - a[2]]);
    const ref = Math.abs(ax[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
    const u = norm(cross(ax, ref)), t = cross(ax, u);
    const ring = (o, r, w, k) => {
      const an = (k / sides) * Math.PI * 2, cs = Math.cos(an), sn = Math.sin(an);
      const n = [u[0] * cs + t[0] * sn, u[1] * cs + t[1] * sn, u[2] * cs + t[2] * sn];
      return { p: [o[0] + n[0] * r, o[1] + n[1] * r, o[2] + n[2] * r], n, w };
    };
    for (let k = 0; k < sides; k++) quad(ring(a, r0, w0, k), ring(b, r1, w1, k), ring(b, r1, w1, k + 1), ring(a, r0, w0, k + 1), mat, leg, g);
    if (caps) for (const [o, r, w, dir] of [[a, r0, w0, -1], [b, r1, w1, 1]]) {
      const n = [ax[0] * dir, ax[1] * dir, ax[2] * dir];
      for (let k = 0; k < sides; k++) {
        const p0 = ring(o, r, w, k).p, p1 = ring(o, r, w, k + 1).p;
        const tri = dir > 0 ? [o, p0, p1] : [o, p1, p0];
        for (const p of tri) push(p, n, mat, leg, w, g);
      }
    }
  }

  /* Ring around the X axis. */
  function torus(cx, R, r, mat, seg = 28, sides = 8) {
    const at = (i, j) => {
      const a = (i / seg) * Math.PI * 2, b = (j / sides) * Math.PI * 2;
      const cy = Math.cos(a), cz = Math.sin(a);
      const n = [Math.sin(b), Math.cos(b) * cy, Math.cos(b) * cz];
      return { p: [cx + n[0] * r, (R + Math.cos(b) * r) * cy, (R + Math.cos(b) * r) * cz], n };
    };
    for (let i = 0; i < seg; i++) for (let j = 0; j < sides; j++) quad(at(i, j), at(i + 1, j), at(i + 1, j + 1), at(i, j + 1), mat, -1, 0);
  }

  function box(c, h, mat) {
    const f = [[[1, 0, 0], [0, 1, 0], [0, 0, 1]], [[-1, 0, 0], [0, 0, 1], [0, 1, 0]], [[0, 1, 0], [0, 0, 1], [1, 0, 0]],
      [[0, -1, 0], [1, 0, 0], [0, 0, 1]], [[0, 0, 1], [1, 0, 0], [0, 1, 0]], [[0, 0, -1], [0, 1, 0], [1, 0, 0]]];
    for (const [n, a, b] of f) {
      const k = (sa, sb) => ({ p: [0, 1, 2].map((i) => c[i] + (n[i] + a[i] * sa + b[i] * sb) * h[i]), n });
      quad(k(-1, -1), k(1, -1), k(1, 1), k(-1, 1), mat, -1, 0);
    }
  }

  return { v, ellipsoid, tube, torus, box, data: () => new Float32Array(v) };
}

/* Crawler bot. Faces +X, stands on y = 0, leg span about 1.5 units.
   detail 0: simple (small on screen), 1: detailed, 2: hero (the few large bots, smooth and fully fitted). */
export function botMesh(detail) {
  const b = builder();
  const hi = detail > 0, hero = detail > 1;
  const q = hero ? 2 : 1;                                   // tessellation multiplier
  b.ellipsoid([-0.24, 0.27, 0], [0.31, 0.2, 0.25], MAT.BODY, hi ? 6 * q : 4, hi ? 10 * q : 8);     // abdomen
  b.ellipsoid([0.17, 0.25, 0], [0.2, 0.15, 0.18], MAT.BODY, hi ? 5 * q : 3, hi ? 8 * q : 6);       // head
  if (hi) {
    b.ellipsoid([-0.25, 0.39, 0], [0.22, 0.1, 0.17], MAT.PLATE, 4 * q, 8 * q);                     // armour plate
    b.ellipsoid([-0.28, 0.485, 0], [0.1, 0.035, 0.075], MAT.CORE, 3 * q, 6 * q);                   // data core
    b.ellipsoid([0.2, 0.35, 0], [0.12, 0.05, 0.11], MAT.PLATE, 3 * q, 6 * q);                      // brow plate
    for (const s of [-1, 1]) {
      b.ellipsoid([0.325, 0.27, s * 0.078], [0.055, 0.055, 0.055], MAT.EYE, 3 * q, 6 * q);
      b.ellipsoid([0.3, 0.215, s * 0.135], [0.03, 0.03, 0.03], MAT.EYE, 2 * q, 5 * q);
      b.tube([0.18, 0.36, s * 0.06], [0.33, 0.55, s * 0.13], 0.012, 0.006, MAT.JOINT, 3 * q);      // antenna
      b.ellipsoid([0.335, 0.56, s * 0.132], [0.022, 0.022, 0.022], MAT.CORE, 2 * q, 4 * q);
      if (hero) {
        b.tube([0.33, 0.2, s * 0.05], [0.44, 0.1, s * 0.035], 0.022, 0.008, MAT.JOINT, 6);          // mandible
        b.ellipsoid([-0.24, 0.3, s * 0.2], [0.2, 0.07, 0.07], MAT.PLATE, 5, 10);                   // side armour
        b.ellipsoid([-0.08, 0.33, s * 0.15], [0.03, 0.03, 0.03], MAT.CORE, 4, 8);                  // running light
      }
    }
    if (hero) {
      for (const x of [-0.4, -0.13]) b.ellipsoid([x, 0.3, 0], [0.028, 0.19, 0.24], MAT.JOINT, 8, 16);   // segment bands
      b.ellipsoid([-0.54, 0.24, 0], [0.05, 0.06, 0.06], MAT.EYE, 5, 10);                           // tail emitter
    }
  } else {
    b.box([0.34, 0.27, 0], [0.03, 0.045, 0.12], MAT.EYE);                                           // visor
    b.box([-0.25, 0.42, 0], [0.17, 0.035, 0.13], MAT.PLATE);
    b.box([-0.28, 0.475, 0], [0.09, 0.02, 0.07], MAT.CORE);
  }
  const fwd = [1, 0.36, -0.36, -1];
  for (const s of [-1, 1]) for (let row = 0; row < 4; row++) {
    const leg = (s < 0 ? 0 : 4) + row, g = (row + (s < 0 ? 0 : 1)) % 2;
    const hx = 0.2 - row * 0.14;
    const hip = [hx, 0.22, s * 0.14];
    const knee = [hx + fwd[row] * 0.2, 0.47, s * 0.43];
    const foot = [hx + fwd[row] * 0.4, 0, s * 0.75];
    const sides = hero ? 8 : hi ? 6 : 4;
    b.tube(hip, knee, 0.04, 0.032, MAT.JOINT, sides, leg, 0.08, 0.5, g);
    if (hero) {   // three-part leg: a shin plate and an ankle give the big bots a proper silhouette
      const ankle = [knee[0] + (foot[0] - knee[0]) * 0.62, 0.17, knee[2] + (foot[2] - knee[2]) * 0.7];
      b.tube(knee, ankle, 0.036, 0.024, MAT.PLATE, sides, leg, 0.5, 0.82, g);
      b.tube(ankle, foot, 0.022, 0.009, MAT.BODY, sides, leg, 0.82, 1, g);
      b.ellipsoid(ankle, [0.03, 0.03, 0.03], MAT.JOINT, 5, 8, leg, 0.82, g);
      b.ellipsoid(hip, [0.05, 0.05, 0.05], MAT.JOINT, 5, 8, leg, 0.08, g);
    } else b.tube(knee, foot, 0.032, 0.011, MAT.BODY, sides, leg, 0.5, 1, g);
    if (hi) {
      b.ellipsoid(knee, [0.048, 0.048, 0.048], MAT.PLATE, 3 * q, 5 * q, leg, 0.5, g);
      b.ellipsoid(foot, [0.022, 0.014, 0.022], MAT.EYE, 2 * q, 4 * q, leg, 1, g);
    }
  }
  return b.data();
}

/* Scraper engine: barrel along X, mouth at +X, with an emissive core disc. */
export function engineMesh() {
  const b = builder();
  b.tube([-1.9, 0, 0], [0, 0, 0], 0.86, 0.92, MAT.BODY, 36, -1, 0, 0, 0, true);
  for (const x of [-1.62, -1.08, -0.54]) b.tube([x - 0.1, 0, 0], [x + 0.1, 0, 0], 1.0, 1.0, MAT.PLATE, 36, -1, 0, 0, 0, true);
  b.torus(0.04, 0.9, 0.13, MAT.PLATE, 40, 10);
  b.tube([0.03, 0, 0], [0.07, 0, 0], 0.74, 0.74, MAT.CORE, 40, -1, 0, 0, 0, true);                // glowing core
  b.torus(0.09, 0.42, 0.035, MAT.EYE, 32, 6);
  b.tube([-2.25, 0, 0], [-1.9, 0, 0], 0.5, 0.86, MAT.JOINT, 36, -1, 0, 0, 0, true);               // tail cone
  for (let k = 0; k < 8; k++) {
    const a = (k / 8) * Math.PI * 2, y = Math.cos(a), z = Math.sin(a);
    b.tube([-1.75, y * 0.96, z * 0.96], [-0.25, y * 0.99, z * 0.99], 0.035, 0.035, k % 2 ? MAT.EYE : MAT.PLATE, 5);   // conduits
  }
  b.box([-0.95, -1.25, 0], [0.75, 0.32, 0.6], MAT.JOINT);                                           // mount
  b.box([-0.95, -1.62, 0], [1.15, 0.07, 0.95], MAT.PLATE);
  return b.data();
}

/* Notched ring that spins around the engine mouth. */
export function ringMesh() {
  const b = builder();
  b.torus(0, 1.22, 0.04, MAT.PLATE, 48, 6);
  for (let k = 0; k < 6; k++) {
    const a = (k / 6) * Math.PI * 2, y = Math.cos(a) * 1.22, z = Math.sin(a) * 1.22;
    b.ellipsoid([0, y, z], [0.09, 0.09, 0.09], k % 2 ? MAT.EYE : MAT.CORE, 4, 8);
  }
  return b.data();
}

/* Page slab: unit box, top face at y = 0, body down to y = -1. Vertex: position xyz, normal xyz. */
export function slabMesh() {
  const v = [];
  const face = (n, a, b, c, d) => { for (const p of [a, b, c, a, c, d]) v.push(p[0], p[1], p[2], n[0], n[1], n[2]); };
  const X = 0.5, Z = 0.5;
  face([0, 1, 0], [-X, 0, -Z], [-X, 0, Z], [X, 0, Z], [X, 0, -Z]);
  face([0, -1, 0], [-X, -1, Z], [-X, -1, -Z], [X, -1, -Z], [X, -1, Z]);
  face([0, 0, 1], [-X, -1, Z], [X, -1, Z], [X, 0, Z], [-X, 0, Z]);
  face([0, 0, -1], [X, -1, -Z], [-X, -1, -Z], [-X, 0, -Z], [X, 0, -Z]);
  face([1, 0, 0], [X, -1, Z], [X, -1, -Z], [X, 0, -Z], [X, 0, Z]);
  face([-1, 0, 0], [-X, -1, -Z], [-X, -1, Z], [-X, 0, Z], [-X, 0, -Z]);
  return new Float32Array(v);
}
