/* The crawler robot: a six-legged camera pod with one big lens eye, green side lights and three-jointed legs.
   Faces +X, stands on y = 0, about 1.3 long and 1.7 across the feet. Built at three detail levels.
   Parts: 0 body, then 1 + leg * 3 + segment (0 hip link, 1 upper leg, 2 lower leg) so the shader can move each joint. */
import { Mesh, MAT, alongY, basis, rotX, rotZ } from './kit.js';

export const CORE = 7;   // status core on the back: its own material so it can pulse and change colour
export const LED = 8;    // the row of green lights
const DEG = Math.PI / 180;

/* Leg order alternates the two tripod groups: even = front-left, mid-right, rear-left; odd = the other three. */
const LEG_DEF = [[0.27, -1, 38], [0.27, 1, 38], [0, 1, 0], [0, -1, 0], [-0.27, -1, -38], [-0.27, 1, -38]];
const HIP_Y = 0.3, J1 = 0.13, KNEE = [0.4, 0.24], FOOT = [0.66, -HIP_Y];

export const RIG = (() => {
  const hip = new Float32Array(24), j1 = new Float32Array(18), j2 = new Float32Array(18), axis = new Float32Array(18), feet = [];
  LEG_DEF.forEach(([x, s, yaw], i) => {
    const d = [Math.sin(yaw * DEG), 0, s * Math.cos(yaw * DEG)], h = [x, HIP_Y, s * 0.235];
    hip.set([h[0], h[1], h[2], s], i * 4);                                              // w: swing direction
    j1.set([h[0] + d[0] * J1, h[1], h[2] + d[2] * J1], i * 3);
    j2.set([h[0] + d[0] * KNEE[0], h[1] + KNEE[1], h[2] + d[2] * KNEE[0]], i * 3);
    axis.set([-d[2], 0, d[0]], i * 3);                                                   // lift axis: outward x up
    feet.push([h[0] + d[0] * FOOT[0], 0, h[2] + d[2] * FOOT[0]]);
  });
  return { hip, j1, j2, axis, feet };
})();

/* level 0: hero (close to the camera), 1: mid, 2: far. */
export function robotMesh(level) {
  const m = new Mesh();
  const hero = level === 0, far = level === 2;
  const B = far ? 0 : hero ? 4 : 1, S = far ? 0 : hero ? 3 : 1, L = far ? 6 : hero ? 36 : 10, E = far ? [4, 8] : hero ? [18, 40] : [6, 12];
  const set = (mat, part = m.part) => { m.mat = mat; m.part = part; return m; };
  const box = (c, h, r, seg, rot) => m.at(c, rot, () => m.roundBox(h, r, seg));
  const ring = (c, rot, R, r) => m.at(c, rot, () => m.torus(R, r, L, hero ? 10 : 6));
  const ball = (c, rad, rings = E[0], sides = E[1]) => m.at(c, null, () => m.ellipsoid(rad, rings, sides));
  const faceX = rotZ(-90 * DEG);   // local Y -> +X, for parts that look forward

  /* body: a camera pod. A barrel along the body with one big lens at the front and a row of green lights on its side */
  set(MAT.FRAME, 0); box([0, 0.262, 0], [0.31, 0.05, 0.2], 0.04, B);                                   // chassis the legs hang from
  set(MAT.SHELL); m.at([-0.02, 0.37, 0], faceX, () => m.cylinder(0.2, 0.6, L, 0.1));                   // barrel
  set(MAT.FRAME); m.at([0.31, 0.37, 0], faceX, () => m.cylinder(0.228, 0.11, L, 0.2));                 // lens hood
  set(MAT.GLASS); m.at([0.36, 0.37, 0], null, () => m.ellipsoid([0.05, 0.178, 0.178], E[0], E[1]));    // the lens
  set(MAT.EMIT);
  if (far) box([0.405, 0.37, 0], [0.008, 0.1, 0.1], 0, 0);
  else { ring([0.395, 0.37, 0], faceX, 0.115, 0.02); m.at([0.408, 0.37, 0], null, () => m.ellipsoid([0.012, 0.06, 0.06], 6, L)); }   // glowing iris and pupil
  set(MAT.FRAME); m.at([-0.33, 0.37, 0], null, () => m.ellipsoid([0.1, 0.19, 0.19], far ? 3 : E[0] >> 1, E[1]));   // rear cap
  set(LED); for (const s of [-1, 1]) for (let k = 0; k < (far ? 1 : 4); k++) box([far ? 0 : -0.14 + k * 0.075, 0.5, s * 0.142], far ? [0.13, 0.016, 0.012] : [0.024, 0.016, 0.012], far ? 0 : 0.006, far ? 0 : 1);   // green lights
  set(CORE); if (far) box([-0.2, 0.585, 0], [0.05, 0.012, 0.05], 0, 0); else m.at([-0.2, 0.582, 0], null, () => m.ellipsoid([0.05, 0.018, 0.05], 6, L, 0, 0.5));
  if (!far) {
    set(MAT.FRAME); m.at([-0.03, 0.37, 0], faceX, () => m.cylinder(0.207, 0.16, L, 0.15));              // dark band round the barrel
    set(MAT.CHROME); ring([0.372, 0.37, 0], faceX, 0.192, 0.02);                                         // lens bezel
    ring([-0.27, 0.37, 0], faceX, 0.196, 0.012);
    set(MAT.SHELL); box([-0.03, 0.562, 0], [0.2, 0.018, 0.11], 0.016, S);                                // top plate
    set(MAT.CHROME); m.at([-0.2, 0.572, 0], null, () => m.cylinder(0.066, 0.016, L));                    // core seat
    set(MAT.FRAME); m.tube([-0.3, 0.55, -0.07], [-0.4, 0.74, -0.09], 0.007, 0.004, 6);                   // antenna
    set(MAT.EMIT); ball([-0.4, 0.743, -0.09], [0.013, 0.013, 0.013], 5, 10);
    for (const s of [-1, 1]) {
      set(MAT.SHELL); box([0.06, 0.37, s * 0.203], [0.16, 0.085, 0.012], 0.01, S);                        // side armour
      set(MAT.CHROME); m.tube([0.3, 0.2, s * 0.07], [0.43, 0.13, s * 0.05], 0.013, 0.006, hero ? 10 : 6); // probe arm
      set(MAT.EMIT); ball([0.432, 0.129, s * 0.05], [0.011, 0.011, 0.011], 4, 8);
      set(MAT.EMIT); box([-0.425, 0.37, s * 0.09], [0.004, 0.03, 0.014], 0.003, 1);                       // tail light
    }
    set(MAT.RUBBER); box([0, 0.205, 0], [0.25, 0.012, 0.14], 0.01, 1);                                   // belly plate
  }
  if (hero) {
    set(MAT.CHROME);
    for (const s of [-1, 1]) for (const x of [-0.07, 0.19]) for (const y of [0.31, 0.43]) m.at([x, y, s * 0.216], rotX(90 * DEG), () => m.cylinder(0.009, 0.012, 8));   // bolts
    for (let k = 0; k < 8; k++) { const a = (k / 8) * Math.PI * 2; m.at([0.368, 0.37 + Math.cos(a) * 0.212, Math.sin(a) * 0.212], faceX, () => m.cylinder(0.008, 0.014, 8)); }   // hood screws
    set(MAT.ACCENT); m.at([0.24, 0.37, 0], faceX, () => m.cylinder(0.203, 0.016, L, 0.3));                // cyan ring behind the hood
  }

  /* legs: built in a frame at the hip with x outward, y up, z the lift axis */
  LEG_DEF.forEach(([x, s, yaw], i) => {
    const d = [Math.sin(yaw * DEG), 0, s * Math.cos(yaw * DEG)], frame = basis(d, [0, 1, 0], [-d[2], 0, d[0]]);
    const p0 = 1 + i * 3, drum = rotX(90 * DEG);
    if (!far) { set(MAT.FRAME, 0); m.at([x, HIP_Y, s * 0.235], null, () => m.cylinder(0.058, 0.11, L)); set(MAT.CHROME, 0); ring([x, HIP_Y + 0.055, s * 0.235], null, 0.044, 0.008); }
    m.at([x, HIP_Y, s * 0.235], frame, () => {
      /* hip link */
      set(MAT.FRAME, p0);
      if (far) m.tube([0, 0, 0], [J1, 0, 0], 0.035, 0.035, 5, false);
      else { box([J1 / 2, 0, 0], [J1 / 2 + 0.01, 0.034, 0.042], 0.02, S); set(MAT.CHROME, p0); m.at([J1, 0, 0], drum, () => m.cylinder(0.052, 0.1, L)); set(MAT.ACCENT, p0); m.at([J1, 0, 0], drum, () => m.cylinder(0.026, 0.108, L)); }
      /* upper leg, hip joint to knee */
      const up = Math.atan2(KNEE[1], KNEE[0] - J1), len = Math.hypot(KNEE[0] - J1, KNEE[1]);
      m.at([J1, 0, 0], rotZ(up), () => {
        set(MAT.FRAME, p0 + 1); box([len / 2, 0, 0], [len / 2, 0.026, 0.034], far ? 0 : 0.016, S);
        if (!far) {
          set(MAT.SHELL, p0 + 1); for (const z of [-0.037, 0.037]) box([len / 2, 0, z], [len / 2 - 0.035, 0.037, 0.006], 0.005, 1);    // cover plates
          set(MAT.CHROME, p0 + 1); m.tube([0.05, -0.052, 0], [len - 0.06, -0.047, 0], 0.009, 0.009, 8);                               // piston rod
          set(MAT.FRAME, p0 + 1); m.tube([0.05, -0.052, 0], [len * 0.5, -0.05, 0], 0.015, 0.015, 8);                                   // piston sleeve
        }
      });
      if (!far) { set(MAT.CHROME, p0 + 1); m.at([KNEE[0], KNEE[1], 0], drum, () => m.cylinder(0.046, 0.094, L)); set(MAT.ACCENT, p0 + 1); m.at([KNEE[0], KNEE[1], 0], drum, () => m.cylinder(0.022, 0.102, L)); }
      /* lower leg, knee to foot */
      const t = [FOOT[0] - KNEE[0], FOOT[1] - KNEE[1], 0], tl = Math.hypot(t[0], t[1]);
      m.at([KNEE[0], KNEE[1], 0], alongY(t), () => {
        set(MAT.FRAME, p0 + 2);
        if (far) m.lathe([[0.03, 0], [0.012, tl]], 5);
        else {
          m.lathe([[0, 0.02], [0.03, 0.02], [0.036, 0.06, 1], [0.03, tl * 0.45, 1], [0.019, tl - 0.07, 1], [0.019, tl - 0.05], [0, tl - 0.05]], hero ? 20 : 10);
          set(MAT.CHROME, p0 + 2); m.lathe([[0.014, tl - 0.05], [0.009, tl - 0.022]], hero ? 12 : 6);                                   // ankle pin
          set(MAT.RUBBER, p0 + 2); m.at([0, tl - 0.02, 0], null, () => m.ellipsoid([0.03, 0.022, 0.03], 6, hero ? 14 : 8));           // foot pad
          set(MAT.SHELL, p0 + 2); box([0.026, tl * 0.33, 0], [0.012, tl * 0.2, 0.026], 0.01, 1);                                         // shin guard
        }
      });
    });
  });
  if (!far) m.bakeAO(hero ? 110 : 60, 0.3, 1.15);
  else for (let i = 0; i < m.count; i++) m.v[i * 9 + 8] = 0.55 + 0.45 * Math.min(1, Math.max(0, (m.v[i * 9 + 1] - 0.1) / 0.3));   // far level: darker underneath
  return m.data();
}

/* Page slab: unit box, glass top face at y = 0, body down to y = -1. Vertex: position xyz, normal xyz. */
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
