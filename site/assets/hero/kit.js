/* Hard-surface modelling kit: indexed meshes built from lathes, rounded boxes, tubes and rings, with baked
   ambient occlusion. Vertex: position xyz, normal xyz, material, part, occlusion (9 floats). */

export const MAT = { SHELL: 0, FRAME: 1, CHROME: 2, RUBBER: 3, EMIT: 4, GLASS: 5, ACCENT: 6 };

const norm = (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const mulM = (a, b) => { const o = new Array(9); for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) o[r * 3 + c] = a[r * 3] * b[c] + a[r * 3 + 1] * b[3 + c] + a[r * 3 + 2] * b[6 + c]; return o; };
const apply = (m, v) => [m[0] * v[0] + m[1] * v[1] + m[2] * v[2], m[3] * v[0] + m[4] * v[1] + m[5] * v[2], m[6] * v[0] + m[7] * v[1] + m[8] * v[2]];

/* Rotation that carries local +Y onto dir. */
export function alongY(dir) {
  const y = norm(dir), ref = Math.abs(y[1]) < 0.95 ? [0, 1, 0] : [1, 0, 0];
  const x = norm(cross(ref, y)), z = cross(x, y);
  return [x[0], y[0], z[0], x[1], y[1], z[1], x[2], y[2], z[2]];
}
/* Rotation whose columns are the given axes. */
export const basis = (x, y, z) => [x[0], y[0], z[0], x[1], y[1], z[1], x[2], y[2], z[2]];
export const rotY = (a) => [Math.cos(a), 0, Math.sin(a), 0, 1, 0, -Math.sin(a), 0, Math.cos(a)];
export const rotX = (a) => [1, 0, 0, 0, Math.cos(a), -Math.sin(a), 0, Math.sin(a), Math.cos(a)];
export const rotZ = (a) => [Math.cos(a), -Math.sin(a), 0, Math.sin(a), Math.cos(a), 0, 0, 0, 1];

export class Mesh {
  constructor() { this.v = []; this.idx = []; this.mat = 0; this.part = 0; this.frames = [{ r: [1, 0, 0, 0, 1, 0, 0, 0, 1], t: [0, 0, 0] }]; }
  get count() { return this.v.length / 9; }

  /* Run fn with a local frame at position t, rotated by matrix r. */
  at(t, r, fn) {
    const f = this.frames[this.frames.length - 1], wt = apply(f.r, t);
    this.frames.push({ r: r ? mulM(f.r, r) : f.r, t: [f.t[0] + wt[0], f.t[1] + wt[1], f.t[2] + wt[2]] });
    fn(); this.frames.pop();
    return this;
  }
  vert(p, n) {
    const f = this.frames[this.frames.length - 1], a = apply(f.r, p), b = apply(f.r, n);
    this.v.push(a[0] + f.t[0], a[1] + f.t[1], a[2] + f.t[2], b[0], b[1], b[2], this.mat, this.part, 1);
    return this.v.length / 9 - 1;
  }
  /* (nu+1) x (nv+1) patch from fn(i, j) -> [position, normal]. */
  grid(nu, nv, fn) {
    const base = this.count;
    for (let j = 0; j <= nv; j++) for (let i = 0; i <= nu; i++) { const s = fn(i, j); this.vert(s[0], s[1]); }
    for (let j = 0; j < nv; j++) for (let i = 0; i < nu; i++) {
      const a = base + j * (nu + 1) + i, b = a + 1, c = a + nu + 2, d = a + nu + 1;
      this.idx.push(a, b, c, a, c, d);
    }
    return this;
  }

  /* Surface of revolution about local Y. profile: [radius, height, smooth?] from the bottom centre up to the top. */
  lathe(profile, segs = 24) {
    const seg = [];
    for (let k = 0; k < profile.length - 1; k++) {
      const dr = profile[k + 1][0] - profile[k][0], dy = profile[k + 1][1] - profile[k][1], l = Math.hypot(dr, dy) || 1;
      seg.push([dy / l, -dr / l]);
    }
    const rings = [];
    profile.forEach((p, k) => {
      const prev = seg[k - 1], next = seg[k];
      if (prev && next && p[2]) { const n = norm([prev[0] + next[0], prev[1] + next[1], 0]); rings.push([p[0], p[1], n[0], n[1], 1]); }
      else { if (prev) rings.push([p[0], p[1], prev[0], prev[1], 1]); if (next) rings.push([p[0], p[1], next[0], next[1], prev ? 0 : 1]); }
    });
    const base = this.count;
    for (const r of rings) for (let i = 0; i <= segs; i++) {
      const a = (i / segs) * Math.PI * 2, c = Math.cos(a), s = Math.sin(a);
      this.vert([r[0] * c, r[1], r[0] * s], [r[2] * c, r[3], r[2] * s]);
    }
    for (let k = 0; k < rings.length - 1; k++) {
      if (!rings[k + 1][4]) continue;              // second ring of a hard corner: no band between the pair
      for (let i = 0; i < segs; i++) {
        const a = base + k * (segs + 1) + i, b = a + 1, c = a + segs + 2, d = a + segs + 1;
        this.idx.push(a, d, c, a, c, b);
      }
    }
    return this;
  }

  /* Cylinder along local Y, centred, with bevelled rims. */
  cylinder(r, h, segs = 24, bevel = 0.12) {
    const b = Math.min(r, h / 2) * bevel, y = h / 2;
    return this.lathe([[0, -y], [r - b, -y], [r, -y + b], [r, y - b], [r - b, y], [0, y]], segs);
  }

  /* Tapered tube from a to b (local coordinates). */
  tube(a, b, r0, r1, segs = 12, caps = true) {
    const d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], len = Math.hypot(d[0], d[1], d[2]);
    const prof = caps ? [[0, 0], [r0, 0], [r1, len], [0, len]] : [[r0, 0], [r1, len]];
    return this.at(a, alongY(d), () => this.lathe(prof, segs));
  }

  torus(R, r, segs = 32, sides = 10) {
    return this.grid(segs, sides, (i, j) => {
      const a = (i / segs) * Math.PI * 2, b = (j / sides) * Math.PI * 2, ca = Math.cos(a), sa = Math.sin(a);
      const n = [Math.cos(b) * ca, Math.sin(b), Math.cos(b) * sa];
      return [[(R + r * Math.cos(b)) * ca, r * Math.sin(b), (R + r * Math.cos(b)) * sa], n];
    });
  }

  /* Ellipsoid, or a band of one between two latitudes (0 = top pole, 1 = bottom pole). */
  ellipsoid(rad, rings = 12, sides = 20, from = 0, to = 1) {
    return this.grid(sides, rings, (i, j) => {
      const th = (from + (to - from) * (j / rings)) * Math.PI, ph = (i / sides) * Math.PI * 2;
      const d = [Math.sin(th) * Math.cos(ph), Math.cos(th), Math.sin(th) * Math.sin(ph)];
      return [[d[0] * rad[0], d[1] * rad[1], d[2] * rad[2]], norm([d[0] / rad[0], d[1] / rad[1], d[2] / rad[2]])];
    });
  }

  /* Box with rounded edges and corners. h: half sizes, r: edge radius, seg: steps across each rounded edge. */
  roundBox(h, r, seg = 3) {
    r = Math.min(r, h[0], h[1], h[2]);
    if (seg < 1 || r < 1e-5) {   // plain box for the far detail level
      for (const [ax, ua, va, sg] of [[0, 1, 2, 1], [0, 1, 2, -1], [1, 2, 0, 1], [1, 2, 0, -1], [2, 0, 1, 1], [2, 0, 1, -1]]) {
        const n = [0, 0, 0]; n[ax] = sg;
        this.grid(1, 1, (i, j) => { const c = [0, 0, 0]; c[ax] = sg * h[ax]; c[ua] = (sg > 0 ? i : 1 - i) ? h[ua] : -h[ua]; c[va] = j ? h[va] : -h[va]; return [c, n]; });
      }
      return this;
    }
    const line = (half) => {
      const out = [];
      for (let k = seg; k >= 1; k--) out.push(-(half - r) - r * Math.tan((k / seg) * Math.PI / 4));
      out.push(-(half - r)); if (half - r > 1e-6) out.push(half - r);
      for (let k = 1; k <= seg; k++) out.push(half - r + r * Math.tan((k / seg) * Math.PI / 4));
      return out;
    };
    const faces = [[0, 1, 2, 1], [0, 1, 2, -1], [1, 2, 0, 1], [1, 2, 0, -1], [2, 0, 1, 1], [2, 0, 1, -1]];   // axis, u axis, v axis, sign
    for (const [ax, ua, va, sg] of faces) {
      const U = line(h[ua]), V = line(h[va]);
      this.grid(U.length - 1, V.length - 1, (i, j) => {
        const c = [0, 0, 0]; c[ax] = sg * h[ax]; c[ua] = sg > 0 ? U[i] : U[U.length - 1 - i]; c[va] = V[j];
        const inner = c.map((x, k) => Math.max(-(h[k] - r), Math.min(h[k] - r, x)));
        const n = norm([c[0] - inner[0], c[1] - inner[1], c[2] - inner[2]]);
        return [[inner[0] + n[0] * r, inner[1] + n[1] * r, inner[2] + n[2] * r], n];
      });
    }
    return this;
  }

  /* Ambient occlusion per vertex: voxelise the mesh, then march short rays over the hemisphere. */
  bakeAO(res = 72, reach = 0.4, strength = 1) {
    const v = this.v, n = this.count, lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
    for (let i = 0; i < n; i++) for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], v[i * 9 + k]); hi[k] = Math.max(hi[k], v[i * 9 + k]); }
    const cell = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / res;
    const dim = [0, 1, 2].map((k) => Math.ceil((hi[k] - lo[k]) / cell) + 3), grid = new Uint8Array(dim[0] * dim[1] * dim[2]);
    const at = (x, y, z) => { const a = Math.floor((x - lo[0]) / cell) + 1, b = Math.floor((y - lo[1]) / cell) + 1, c = Math.floor((z - lo[2]) / cell) + 1; return a < 0 || b < 0 || c < 0 || a >= dim[0] || b >= dim[1] || c >= dim[2] ? -1 : a + dim[0] * (b + dim[1] * c); };
    for (let t = 0; t < this.idx.length; t += 3) {
      const A = this.idx[t] * 9, B = this.idx[t + 1] * 9, C = this.idx[t + 2] * 9;
      const e1 = Math.hypot(v[B] - v[A], v[B + 1] - v[A + 1], v[B + 2] - v[A + 2]), e2 = Math.hypot(v[C] - v[A], v[C + 1] - v[A + 1], v[C + 2] - v[A + 2]);
      const steps = Math.max(1, Math.ceil(Math.max(e1, e2) / (cell * 0.7)));
      for (let i = 0; i <= steps; i++) for (let j = 0; j <= steps - i; j++) {
        const a = i / steps, b = j / steps, c = 1 - a - b, k = at(v[A] * c + v[B] * a + v[C] * b, v[A + 1] * c + v[B + 1] * a + v[C + 1] * b, v[A + 2] * c + v[B + 2] * a + v[C + 2] * b);
        if (k >= 0) grid[k] = 1;
      }
    }
    const dirs = []; let total = 0;
    for (let i = 0; i < 16; i++) { const y = 1 - (i + 0.5) / 20, r = Math.sqrt(1 - y * y), a = i * 2.39996; dirs.push([r * Math.cos(a), y, r * Math.sin(a)]); total += y; }   // cosine-weighted, nothing near the horizon
    const marches = Math.max(3, Math.round(reach / cell));
    for (let i = 0; i < n; i++) {
      const o = i * 9, N = [v[o + 3], v[o + 4], v[o + 5]], T = norm(cross(Math.abs(N[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0], N)), Bt = cross(N, T);
      let occ = 0;
      for (const d of dirs) {
        const w = [T[0] * d[0] + N[0] * d[1] + Bt[0] * d[2], T[1] * d[0] + N[1] * d[1] + Bt[1] * d[2], T[2] * d[0] + N[2] * d[1] + Bt[2] * d[2]];
        for (let s = 3; s <= marches; s++) {
          const k = at(v[o] + w[0] * cell * s, v[o + 1] + w[1] * cell * s, v[o + 2] + w[2] * cell * s);
          if (k >= 0 && grid[k]) { occ += d[1] * (1 - (s - 3) / marches); break; }
        }
      }
      v[o + 8] = Math.max(0.1, 1 - (occ / total) * strength);
    }
    return this;
  }

  data() {
    return { vertices: new Float32Array(this.v), indices: this.count > 65535 ? new Uint32Array(this.idx) : new Uint16Array(this.idx) };
  }
}
