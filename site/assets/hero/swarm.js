/* Crawler swarm: a few large robots, a company of mid-sized ones and well over a thousand small ones,
   walking page content, following links, scanning and handing off data. */
import { bezier, bezierTangent, PAGE_W, PAGE_D } from './world.js';
import { qFromBasis } from './gl.js';

const WALK = 0, SCAN = 1, BRIDGE = 2, DYING = 3;
const TAU = Math.PI * 2;
export const STRIDE = 12;   // instance floats: position 3, quaternion 4, scale, gait phase, gait amp, glow, tint
const GAIT = 6.8;           // radians of leg cycle per body length travelled, so feet do not slide

export class Swarm {
  constructor(world, count, heroes, seed = 7) {
    this.world = world; this.n = count; this.heroes = heroes;
    let s = seed >>> 0;
    this.rnd = () => { s = (s + 0x6d2b79f5) | 0; let t = Math.imul(s ^ (s >>> 15), 1 | s); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
    const F = (k = 1) => new Float32Array(count * k), I = () => new Int16Array(count);
    Object.assign(this, { x: F(), z: F(), yaw: F(), size: F(), grow: F(), phase: F(), amp: F(), glow: F(), timer: F(), tx: F(), tz: F(), bt: F(), tint: F(), speed: F(),
      surf: I(), br: I(), goal: I(), hops: I(), tk: new Int32Array(count), state: new Uint8Array(count), bdir: new Int8Array(count), want: new Uint8Array(count) });
    this.heat = new Float32Array(world.elements.length);
    this.bufs = [new Float32Array(10 * STRIDE), new Float32Array(110 * STRIDE), F(STRIDE)]; this.counts = [0, 0, 0];   // detail levels: hero, mid, far
    this.trail = F(2); this.trailCount = 0;
    this.pointer = null; this.focus = null; this.linkBoost = 0; this.onPacket = null;
    this.cols = Math.ceil(PAGE_W / 0.5) + 2; this.rows = Math.ceil(PAGE_D / 0.5) + 2;
    this.cell = new Int32Array(this.cols * this.rows); this.next = new Int32Array(count);
    this.P = [0, 0, 0]; this.T = [0, 0, 0]; this.Q = [0, 0, 0, 1];
    const sats = world.surfaces.length - 1;
    for (let i = 0; i < count; i++) {
      const hero = i < heroes, lead = i < world.front, r = this.rnd();
      this.size[i] = lead ? 0.64 + this.rnd() * 0.16 : hero ? 0.95 + this.rnd() * 0.4 : r < 0.014 ? 0.34 + this.rnd() * 0.2 : 0.1 + this.rnd() * 0.075;
      this.speed[i] = hero ? 0.5 + this.rnd() * 0.25 : 0.5 + this.size[i] * 1.2 + this.rnd() * 0.3;
      this.tint[i] = hero ? (i % 3 === 1 ? 0.9 : 1.15) : 0.8 + this.rnd() * 0.48;     // above 1: pearl shell, below: graphite
      this.phase[i] = this.rnd() * TAU; this.grow[i] = 1; this.amp[i] = 1; this.goal[i] = -1;
      const onSat = !hero && sats > 0 && this.size[i] < 0.3 && this.rnd() < 0.26;
      const sIdx = onSat ? 1 + ((this.rnd() * sats) | 0) : 0, S = world.surfaces[sIdx];
      this.surf[i] = sIdx;
      this.x[i] = (this.rnd() - 0.5) * S.w * 0.94; this.z[i] = (this.rnd() - 0.5) * S.d * 0.92;
      if (hero) { const spot = world.stage[i % world.stage.length]; this.x[i] = spot[0]; this.z[i] = spot[1]; }
      this.yaw[i] = this.rnd() * TAU; this.hops[i] = 5 + ((this.rnd() * 14) | 0);
      this.tk[i] = (this.rnd() * (S.targets.pts.length / 3)) | 0;
      this.pick(i);
      if (lead) { this.tx[i] = world.tip[0] - [0.22, 1.3, 0.1][i % 3]; this.tz[i] = world.tip[1] - [0.2, 0.1, 1.25][i % 3]; }   // first walk: out onto the corner
    }
  }

  pick(i) {
    const w = this.world, s = this.surf[i], S = w.surfaces[s], T = S.targets.pts, n = T.length / 3, r = this.rnd();
    this.goal[i] = -1; this.want[i] = 0;
    if (i < this.heroes) {   // the large robots keep to the front of the page, where the camera opens
      if (i < w.front) { const u = this.rnd() * w.tip[2], v = this.rnd() * (w.tip[2] - u); this.tx[i] = w.tip[0] - u; this.tz[i] = w.tip[1] - v; return; }   // the front crew: out on the corner
      const a = w.stageBox;
      this.tx[i] = a[0] + this.rnd() * (a[1] - a[0]); this.tz[i] = a[2] + this.rnd() * (a[3] - a[2]);
      return;
    }
    if (s > 0 && --this.hops[i] <= 0) return this.toLink(i, S.bridge, -1);
    if (r < 0.012 + this.linkBoost * (s === 0 ? 0.05 : 0.02)) {
      const out = []; for (let b = 0; b < w.bridges.length; b++) if (w.bridges[b].a === s) out.push(b);
      if (out.length) return this.toLink(i, out[(this.rnd() * out.length) | 0], 1);
    }
    this.tk[i] = this.rnd() < 0.84 ? (this.tk[i] + 1 + ((this.rnd() * 3) | 0)) % n : (this.rnd() * n) | 0;
    const k = this.tk[i] * 3;
    this.tx[i] = T[k] + (this.rnd() - 0.5) * 0.14; this.tz[i] = T[k + 1] + (this.rnd() - 0.5) * 0.14;
  }

  toLink(i, b, dir) {
    const br = this.world.bridges[b], p = dir > 0 ? br.pa : br.pb;
    this.goal[i] = b; this.bdir[i] = dir; this.tx[i] = p[0]; this.tz[i] = p[1];
  }

  spawn(i) {   // a fresh robot arrives over a link and grows in at its port
    const B = this.world.bridges, b = (this.rnd() * B.length) | 0;
    this.state[i] = BRIDGE; this.surf[i] = -1; this.br[i] = b; this.bt[i] = 1; this.bdir[i] = -1;
    this.grow[i] = 0.01; this.glow[i] = 0; this.amp[i] = 1; this.goal[i] = -1;
    if (!B.length) { this.state[i] = WALK; this.surf[i] = 0; this.x[i] = (this.rnd() - 0.5) * PAGE_W * 0.9; this.z[i] = -PAGE_D / 2 + 0.2; this.pick(i); }
  }

  /* Pull nearby robots onto one content box (used when a record is being extracted). */
  setFocus(el, seconds) {
    const e = this.world.elements[el];
    this.focus = { el, t: seconds, cx: (e.x + e.w / 2 - 0.5) * PAGE_W, cz: (e.y + e.h / 2 - 0.5) * PAGE_D, hw: e.w * PAGE_W / 2, hd: e.h * PAGE_D / 2 };
  }

  step(dt) {
    const { x, z, yaw, size, state, surf, tx, tz, cell, next, cols, world } = this;
    const rnd = this.rnd, f = this.focus, ptr = this.pointer;
    if (f && (f.t -= dt) <= 0) this.focus = null;
    for (let e = 0; e < this.heat.length; e++) this.heat[e] = Math.max(0, this.heat[e] - dt * 0.55);
    cell.fill(-1);
    for (let i = 0; i < this.n; i++) if (surf[i] === 0) {
      const c = ((x[i] + PAGE_W / 2) / 0.5 + 1 | 0) + ((z[i] + PAGE_D / 2) / 0.5 + 1 | 0) * cols;
      if (c >= 0 && c < cell.length) { next[i] = cell[c]; cell[c] = i; }
    }
    for (let i = 0; i < this.n; i++) {
      const st = state[i], hero = i < this.heroes;
      if (this.grow[i] < 1 && st !== DYING) this.grow[i] = Math.min(1, this.grow[i] + dt * 1.6);
      if (st === BRIDGE) { this.walkBridge(i, dt); continue; }
      if (st === DYING) {
        this.grow[i] -= dt * 2.2; this.glow[i] = 1;
        if (this.grow[i] <= 0.02) this.spawn(i);
        continue;
      }
      const S = world.surfaces[surf[i]];
      if (st === SCAN) {
        this.glow[i] += (1 - this.glow[i]) * Math.min(1, dt * 9); this.amp[i] += (0.1 - this.amp[i]) * Math.min(1, dt * 8);
        this.phase[i] += dt * 2.2;
        if (i < world.front) {   // the front crew turn to look out of the screen while they stand
          let t = -Math.PI / 4 + (i - 1) * 0.5 - yaw[i]; t -= Math.round(t / TAU) * TAU;
          const m = 1.5 * dt; yaw[i] += t > m ? m : t < -m ? -m : t;
          if (Math.abs(t) > 0.06) { this.phase[i] += dt * 7; this.amp[i] += (0.75 - this.amp[i]) * Math.min(1, dt * 10); }
        }
        if ((this.timer[i] -= dt) <= 0) {
          if (!hero && surf[i] === 0 && rnd() < 0.02 && size[i] < 0.3) { state[i] = DYING; if (this.onPacket) this.onPacket(x[i], z[i], -1); }
          else { state[i] = WALK; this.pick(i); }
        }
        continue;
      }
      this.glow[i] *= Math.max(0, 1 - dt * 5); this.amp[i] += (1 - this.amp[i]) * Math.min(1, dt * 6);
      if (!hero && surf[i] === 0 && this.goal[i] < 0) {
        if (f && !this.want[i] && rnd() < dt * 2.2) {
          const dx = x[i] - f.cx, dz = z[i] - f.cz;
          if (dx * dx + dz * dz < 7) { tx[i] = f.cx + (rnd() * 2 - 1) * f.hw; tz[i] = f.cz + (rnd() * 2 - 1) * f.hd; this.want[i] = 2; }
        } else if (ptr && !this.want[i] && rnd() < dt * 2.6) {
          const dx = x[i] - ptr[0], dz = z[i] - ptr[1];
          if (dx * dx + dz * dz < 6.5) { const a = rnd() * TAU, r = 0.2 + rnd() * 0.8; tx[i] = ptr[0] + Math.cos(a) * r; tz[i] = ptr[1] + Math.sin(a) * r; this.want[i] = 1; }
        }
      }
      const dx = tx[i] - x[i], dz = tz[i] - z[i], dist = Math.hypot(dx, dz);
      if (dist < 0.1 + size[i] * 0.22) {
        if (this.goal[i] >= 0) { state[i] = BRIDGE; this.br[i] = this.goal[i]; this.bt[i] = this.bdir[i] > 0 ? 0 : 1; surf[i] = -1; this.goal[i] = -1; continue; }
        const el = S.targets.pts[this.tk[i] * 3 + 2], hot = this.want[i] === 2;
        if (hot || i < world.front || rnd() < (hero ? 0.6 : this.want[i] ? 0.7 : 0.26)) {
          state[i] = SCAN; this.timer[i] = i < world.front ? 3.5 + rnd() * 4 : hero ? 1.2 + rnd() * 1.8 : hot ? 0.5 + rnd() * 0.7 : 0.3 + rnd() * 0.9;
          if (!this.want[i] && el >= 0 && !hero) this.heat[el] = Math.min(1, this.heat[el] + 0.3);
        } else this.pick(i);
        this.want[i] = 0;
        continue;
      }
      if (surf[i] === 0) {   // keep robots from standing inside each other; small ones give way to big ones
        const cx = (x[i] + PAGE_W / 2) / 0.5 + 1 | 0, cz = (z[i] + PAGE_D / 2) / 0.5 + 1 | 0, reach = hero ? 3 : size[i] > 0.3 ? 2 : 1;
        for (let oz = -reach; oz <= reach; oz++) for (let ox = -reach; ox <= reach; ox++) {
          const c = cx + ox + (cz + oz) * cols;
          if (c < 0 || c >= cell.length) continue;
          for (let j = cell[c]; j >= 0; j = next[j]) {
            if (j === i) continue;
            const ax = x[i] - x[j], az = z[i] - z[j], d2 = ax * ax + az * az, r = (size[i] + size[j]) * (hero && j < this.heroes ? 0.98 : 0.62);
            if (d2 < r * r && d2 > 1e-6) { const d = Math.sqrt(d2), push = (r - d) / d * (size[j] > size[i] * 1.3 ? 0.6 : size[i] > size[j] * 1.3 ? 0.02 : 0.2); x[i] += ax * push; z[i] += az * push; }
          }
        }
      }
      let turn = Math.atan2(-dz, dx) - yaw[i];
      turn -= Math.round(turn / TAU) * TAU;
      const max = (hero ? 1.6 : 5.5) * dt;
      yaw[i] += turn > max ? max : turn < -max ? -max : turn;
      const sp = this.speed[i] * (0.3 + 0.7 * Math.max(0, Math.cos(turn))) * (this.want[i] ? 1.5 : 1);
      x[i] += Math.cos(yaw[i]) * sp * dt; z[i] -= Math.sin(yaw[i]) * sp * dt;
      this.phase[i] += (sp / size[i]) * GAIT * dt;
    }
  }

  walkBridge(i, dt) {
    const b = this.world.bridges[this.br[i]];
    this.bt[i] += this.bdir[i] * (this.speed[i] * 1.25 * dt) / b.len;
    this.phase[i] += (this.speed[i] * 1.25 / this.size[i]) * GAIT * dt;
    this.amp[i] = 1;
    if (this.bt[i] >= 0 && this.bt[i] <= 1) return;
    const to = this.bdir[i] > 0 ? b.b : b.a, p = this.bdir[i] > 0 ? b.pb : b.pa, S = this.world.surfaces[to];
    this.surf[i] = to; this.state[i] = WALK; this.x[i] = p[0]; this.z[i] = p[1];
    this.yaw[i] = Math.atan2(p[1], -p[0]);   // face the middle of the page
    this.hops[i] = 5 + ((this.rnd() * 14) | 0);
    this.tk[i] = (this.rnd() * (S.targets.pts.length / 3)) | 0;
    this.pick(i);
  }

  /* Write instance data. Detail follows the eye: each robot gets the model that suits its size on screen. */
  fill(eye, near, mid) {
    const W = this.world, P = this.P, T = this.T, Q = this.Q, bufs = this.bufs, cnt = this.counts;
    const cap0 = bufs[0].length / STRIDE, cap1 = bufs[1].length / STRIDE;
    cnt[0] = cnt[1] = cnt[2] = 0;
    let tr = 0;
    for (let i = 0; i < this.n; i++) {
      const s = this.surf[i];
      let px, py, pz, q0, q1, q2, q3;
      if (s === 0) {
        const a = this.yaw[i] / 2;
        px = this.x[i]; py = 0; pz = this.z[i]; q0 = 0; q1 = Math.sin(a); q2 = 0; q3 = Math.cos(a);
        this.trail[tr++] = px; this.trail[tr++] = pz;
      } else {
        let fx, fy, fz, nx, ny, nz;
        if (s > 0) {
          const S = W.surfaces[s], c = Math.cos(this.yaw[i]), sn = Math.sin(this.yaw[i]), x = this.x[i], z = this.z[i];
          px = S.pos[0] + S.ex[0] * x + S.ez[0] * z; py = S.pos[1] + S.ex[1] * x + S.ez[1] * z; pz = S.pos[2] + S.ex[2] * x + S.ez[2] * z;
          fx = S.ex[0] * c - S.ez[0] * sn; fy = S.ex[1] * c - S.ez[1] * sn; fz = S.ex[2] * c - S.ez[2] * sn;
          nx = S.ey[0]; ny = S.ey[1]; nz = S.ey[2];
        } else {
          const b = W.bridges[this.br[i]], t = Math.min(1, Math.max(0, this.bt[i])), d = this.bdir[i];
          bezier(b, t, P); bezierTangent(b, t, T);
          fx = T[0] * d; fy = T[1] * d; fz = T[2] * d;
          nx = b.na[0] + (b.nb[0] - b.na[0]) * t; ny = b.na[1] + (b.nb[1] - b.na[1]) * t; nz = b.na[2] + (b.nb[2] - b.na[2]) * t;
          const k = nx * fx + ny * fy + nz * fz; nx -= fx * k; ny -= fy * k; nz -= fz * k;
          const nl = Math.hypot(nx, ny, nz) || 1; nx /= nl; ny /= nl; nz /= nl;
          px = P[0] + nx * 0.03; py = P[1] + ny * 0.03; pz = P[2] + nz * 0.03;
        }
        qFromBasis(Q, 0, fx, fy, fz, nx, ny, nz, fy * nz - fz * ny, fz * nx - fx * nz, fx * ny - fy * nx);
        q0 = Q[0]; q1 = Q[1]; q2 = Q[2]; q3 = Q[3];
      }
      const dx = px - eye[0], dy = py - eye[1], dz = pz - eye[2], seen = this.size[i] / Math.sqrt(dx * dx + dy * dy + dz * dz);
      const c = seen > near && cnt[0] < cap0 ? 0 : seen > mid && cnt[1] < cap1 ? 1 : 2;
      const out = bufs[c], o = cnt[c]++ * STRIDE;
      out[o] = px; out[o + 1] = py; out[o + 2] = pz; out[o + 3] = q0; out[o + 4] = q1; out[o + 5] = q2; out[o + 6] = q3;
      out[o + 7] = this.size[i] * this.grow[i]; out[o + 8] = this.phase[i]; out[o + 9] = this.amp[i];
      out[o + 10] = this.glow[i]; out[o + 11] = this.tint[i];
    }
    this.trailCount = tr / 2;
  }
}

/* Glowing data packets that fly from the page to the dataset panel. */
export class Packets {
  constructor(max = 72) { this.list = []; this.max = max; this.buf = new Float32Array(max * 6 * 8); this.count = 0; }
  send(from, to, row, delay = 0, onArrive = null) {
    if (this.list.length >= this.max) return;
    const mid = [(from[0] + to[0]) / 2 + (Math.random() - 0.5) * 2, Math.max(from[1], to[1]) + 2.2 + Math.random() * 1.6, (from[2] + to[2]) / 2 + (Math.random() - 0.5) * 2];
    this.list.push({ from, mid, to, t: -delay, dur: 1.05 + Math.random() * 0.35, row, onArrive });
  }
  step(dt) {
    let n = 0;
    for (let i = this.list.length - 1; i >= 0; i--) {
      const p = this.list[i];
      p.t += dt;
      if (p.t >= p.dur) { if (p.onArrive) p.onArrive(p.row); this.list.splice(i, 1); continue; }
      if (p.t < 0) continue;
      const lime = p.row >= 0;
      for (let k = 0; k < 6; k++) {
        const t = Math.max(0, p.t / p.dur - k * 0.028), e = t * t * (3 - 2 * t), m = 1 - e, o = n++ * 8;
        for (let a = 0; a < 3; a++) this.buf[o + a] = m * m * p.from[a] + 2 * m * e * p.mid[a] + e * e * p.to[a];
        this.buf[o + 3] = (k ? 0.13 : 0.26) * (1 - k * 0.1);
        this.buf[o + 4] = lime ? 0.62 : 0.15; this.buf[o + 5] = lime ? 1 : 0.85; this.buf[o + 6] = lime ? 0.3 : 1;
        this.buf[o + 7] = (k ? 0.42 : 0.95) * (1 - k * 0.14);
      }
    }
    this.count = n;
  }
}
