/* The crawler robot: a six-legged machine with an armoured shell, a lens eye and three-jointed legs.
   Faces +X, stands on y = 0, about 1.3 long and 1.7 across the feet. Built at three detail levels.
   Parts: 0 body, then 1 + leg * 3 + segment (0 hip link, 1 upper leg, 2 lower leg) so the shader can move each joint. */
import { Mesh, MAT, alongY, basis, rotX, rotZ } from './kit.js';

export const CORE = 7;   // status core on the back: its own material so it can pulse and change colour
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

  /* body */
  set(MAT.FRAME, 0); box([0, 0.3, 0], [0.37, 0.07, 0.215], 0.05, B);                                   // chassis
  set(MAT.SHELL); m.at([-0.02, 0.345, 0], null, () => m.ellipsoid([0.4, 0.15, 0.255], E[0], E[1], 0, 0.5));   // carapace
  set(MAT.FRAME); box([-0.03, 0.468, 0], [0.27, 0.022, 0.045], 0.02, S);                                // spine channel
  set(MAT.EMIT); box([0.02, 0.491, 0], [0.16, 0.004, 0.011], 0.003, far ? 0 : 1);                        // light bar
  set(CORE); if (far) box([-0.2, 0.5, 0], [0.06, 0.012, 0.06], 0, 0); else m.at([-0.2, 0.497, 0], null, () => m.ellipsoid([0.058, 0.02, 0.058], 6, L, 0, 0.5));
  set(MAT.FRAME); box([-0.4, 0.31, 0], [0.07, 0.075, 0.16], 0.03, B);                                   // rear pack
  set(MAT.SHELL); box([0.455, 0.315, 0], [0.085, 0.075, 0.135], 0.045, B);                              // head
  set(MAT.GLASS); box([0.508, 0.325, 0], [0.04, 0.04, 0.118], 0.03, S);                                  // visor
  set(MAT.EMIT);
  if (far) box([0.552, 0.325, 0], [0.006, 0.03, 0.05], 0, 0);
  else ring([0.552, 0.325, 0], faceX, 0.03, 0.007);                                                      // iris ring
  if (!far) {
    set(MAT.RUBBER); box([-0.02, 0.362, 0], [0.392, 0.009, 0.248], 0.009, 1);                            // gasket between shell and chassis
    set(MAT.FRAME); m.at([0.36, 0.31, 0], faceX, () => m.cylinder(0.07, 0.09, L));                       // neck
    set(MAT.CHROME); ring([0.548, 0.325, 0], faceX, 0.05, 0.012);                                         // lens ring
    set(MAT.GLASS); ball([0.547, 0.325, 0], [0.018, 0.044, 0.044], 8, L);                                 // lens
    set(MAT.CHROME); m.at([-0.2, 0.488, 0], null, () => m.cylinder(0.075, 0.018, L));                    // core seat
    for (const s of [-1, 1]) {
      set(MAT.EMIT); ball([0.535, 0.325, s * 0.086], [0.012, 0.014, 0.014], 5, 10);                       // side sensor
      set(MAT.CHROME); m.tube([0.49, 0.262, s * 0.05], [0.6, 0.205, s * 0.03], 0.012, 0.006, hero ? 10 : 6);   // probe arm
      set(MAT.EMIT); ball([0.602, 0.204, s * 0.03], [0.011, 0.011, 0.011], 4, 8);
      set(MAT.EMIT); box([-0.472, 0.3, s * 0.11], [0.004, 0.012, 0.03], 0.003, 1);                        // tail light
      for (const x of [0.27, 0, -0.27]) { set(MAT.SHELL); box([x, 0.392, s * 0.2], [0.078, 0.02, 0.058], 0.018, S, rotX(s * 22 * DEG)); }   // shoulder armour
    }
    set(MAT.FRAME); m.tube([0.42, 0.39, -0.08], [0.33, 0.6, -0.1], 0.007, 0.004, 6);                     // antenna
    set(MAT.EMIT); ball([0.33, 0.603, -0.1], [0.013, 0.013, 0.013], 5, 10);
    set(MAT.CHROME); for (let k = 0; k < (hero ? 5 : 3); k++) box([-0.432, 0.405, (k - (hero ? 2 : 1)) * (hero ? 0.05 : 0.09)], [0.05, 0.03, 0.006], 0.004, 1);   // cooling fins
    set(MAT.RUBBER); box([0, 0.224, 0], [0.28, 0.014, 0.15], 0.012, 1);                                   // belly plate
  }
  if (hero) {
    set(MAT.CHROME);
    for (const s of [-1, 1]) for (const x of [0.33, 0.21, 0.06, -0.06, -0.21, -0.33]) m.at([x, 0.413, s * 0.2 * Math.cos(22 * DEG) + s * 0.012], rotX(s * 22 * DEG), () => m.cylinder(0.009, 0.012, 8));   // bolts
    set(MAT.ACCENT); box([0.455, 0.392, 0], [0.05, 0.004, 0.09], 0.003, 1);                               // head stripe
    set(MAT.FRAME); for (const s of [-1, 1]) for (let k = 0; k < 3; k++) box([-0.1 + k * 0.09, 0.3, s * 0.217], [0.03, 0.03, 0.004], 0.003, 1);   // side vents
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
