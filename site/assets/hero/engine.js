/* Crawl scene engine: robots crawling over web pages, drawn live in WebGL2 with physically based lighting,
   real shadows, reflections in the glass, depth of field and a camera that travels as the page scrolls.
   It touches no DOM, so it runs inside a worker (the normal path) or on the page thread (the fallback).
   The scene is illustrative; the labels on the page say so. No outside libraries or image assets. */
import * as G from './gl.js';
import * as S from './shaders.js';
import { robotMesh, slabMesh, RIG } from './robot.js';
import { paintSite, paintAtlas, paintData } from './page-art.js';
import { makeWorld, ribbonMesh, streamMesh, PAGE_W, PAGE_D } from './world.js';
import { Swarm, Packets, STRIDE } from './swarm.js';

const pause = () => new Promise((r) => setTimeout(r, 0));
const nextFrame = () => new Promise((r) => requestAnimationFrame(r));
const DEG = Math.PI / 180, CYCLE = 18, FOGC = [0.006, 0.014, 0.02], SUN = [-0.39, 0.86, 0.33], SUNC = [2.5, 2.42, 2.3];
const sheet = (w, h) => { if (typeof OffscreenCanvas === 'function') return new OffscreenCanvas(w, h); const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };

/* The camera's four stops along the scroll: target, bearing, height angle, distance, picture shift, lens opening. */
const SHOTS = {
  wide: [
    { t: [5.6, 0.2, 3.0], az: 44, el: 17, d: 9.5, sx: 0.2, sy: -0.2, ap: 0.3 },
    { t: [3.2, 0.6, -0.6], az: -15, el: 31, d: 35, sx: 0.3, sy: -0.14, ap: 0.09 },
    { t: [2.2, 0.1, 2.3], az: 14, el: 34, d: 11.5, sx: 0.5, sy: -0.04, ap: 0.36 },
    { t: [8.6, 1.9, -0.4], az: -26, el: 13, d: 15.5, sx: 0.4, sy: -0.06, ap: 0.3 }
  ],
  tall: [
    { t: [6.0, 0.2, 3.4], az: 45, el: 26, d: 17, sx: -0.02, sy: -0.58, ap: 0.3 },
    { t: [1.4, 0, 0.4], az: -10, el: 33, d: 24, sx: 0, sy: -0.6, ap: 0.1 },
    { t: [4.2, 0.1, -1.2], az: 10, el: 44, d: 12.5, sx: 0, sy: -0.5, ap: 0.33 },
    { t: [1.2, 0.2, 0.9], az: -30, el: 22, d: 19, sx: 0, sy: -0.55, ap: 0.28 }
  ],
  /* the still picture for visitors who ask for reduced motion: no screen frame, the page seen across its front */
  still: { wide: { t: [0.6, 0.4, 1.6], az: -30, el: 19, d: 10.2, sx: 0.3, sy: -0.02, ap: 0.42 }, tall: { t: [0.2, 0.3, 1.3], az: -14, el: 15, d: 10.5, sx: 0, sy: -0.62, ap: 0.45 } }
};

/* cfg: { calm, compact, weak, cssW, cssH, dpr, anchor }. post(message) reports to the page. Returns { input(message) }. */
export async function run(canvas, cfg, post) {
  const mark = (name) => post({ t: 'mark', name });
  const { calm, compact, weak } = cfg;
  let cssW = cfg.cssW, cssH = cfg.cssH, dpr = cfg.dpr, sillPx = cfg.sill || 0, topPx = cfg.top || 86;
  const gl = canvas.getContext('webgl2', { alpha: false, antialias: false, depth: false, stencil: false, powerPreference: 'high-performance' });
  if (!gl) throw new Error('WebGL2 is not available');
  mark('context');
  const info = gl.getExtension('WEBGL_debug_renderer_info');
  if (info && /swiftshader|llvmpipe|software|basic render/i.test(gl.getParameter(info.UNMASKED_RENDERER_WEBGL))) throw new Error('no graphics hardware, showing the still picture');
  if (!gl.getExtension('EXT_color_buffer_float') && !gl.getExtension('EXT_color_buffer_half_float')) throw new Error('no high-range colour buffers, showing the still picture');
  const dprCap = compact ? 2 : 3, pixelCap = compact ? 2.0e6 : 8.4e6, mirror = !compact;
  let quality = 1, W = 2, H = 2, scene = null, half = null, soft = null, refl = null, b1 = null, b1b = null, b2 = null, b2b = null, pop = null, popB = null;

  /* Shaders build in the browser's compiler threads while the art and meshes are made here. */
  const building = [['bot', S.BOT_VS, S.BOT_FS], ['depth', S.DEPTH_VS, S.DEPTH_FS], ['contact', S.CONTACT_VS, S.CONTACT_FS], ['page', S.PAGE_VS, S.PAGE_FS],
    ['trail', S.TRAIL_VS, S.TRAIL_FS], ['flat', S.QUAD_VS, S.FLAT_FS], ['env', S.QUAD_VS, S.ENV_FS], ['sky', S.QUAD_VS, S.SKY_FS], ['floor', S.FLOOR_VS, S.FLOOR_FS],
    ['link', S.LINK_VS, S.LINK_FS], ['stream', S.LINK_VS, S.STREAM_FS], ['beam', S.BEAM_VS, S.BEAM_FS], ['glow', S.GLOW_VS, S.GLOW_FS], ['half', S.QUAD_VS, S.HALF_FS], ['dof', S.QUAD_VS, S.DOF_FS],
    ['bright', S.QUAD_VS, S.BRIGHT_FS], ['blur', S.QUAD_VS, S.BLUR_FS], ['final', S.QUAD_VS, S.FINAL_FS],
    ['maskBot', S.MASK_BOT_VS, S.MASK_FS], ['maskSlab', S.MASK_SLAB_VS, S.MASK_FS], ['wall', S.WALL_VS, S.WALL_FS]]
    .map(([k, vs, fs]) => [k, G.beginProgram(gl, vs, fs)]);
  mark('shaders-sent');
  const firstW = compact ? 1024 : 1280;
  const paint = (w) => { const c = sheet(w, w * 0.625); return { els: paintSite(c), bmp: c }; };
  const siteArt = paint(firstW);
  await pause();
  const atlasSheet = sheet(compact ? 1024 : 2048, compact ? 640 : 1280); paintAtlas(atlasSheet);
  await pause();
  const models = [null, robotMesh(1), robotMesh(2)];      // the hero model is the largest; it follows after the first picture
  mark('art-ready');
  const parallel = gl.getExtension('KHR_parallel_shader_compile');
  if (parallel) while (building.some(([, p]) => !gl.getProgramParameter(p, parallel.COMPLETION_STATUS_KHR))) await nextFrame();
  const prog = {};
  for (const [k, p] of building) prog[k] = G.finishProgram(gl, p);
  mark('shaders-ready');

  /* Fixed texture units: 0 the picture being drawn, 1 trail, 2 studio, 3 shadow, 4 reflection, 5 page (for light off the page). */
  for (const k in prog) {
    const u = prog[k].u; gl.useProgram(prog[k].p);
    for (const [name, unit] of [['uTex', 0], ['uTrail', 1], ['uEnv', 2], ['uShadow', 3], ['uRefl', 4], ['uPage', 5], ['uSoft', 6], ['uB1', 7], ['uB2', 8]]) if (u[name]) gl.uniform1i(u[name], unit);
    if (u.uHip) { gl.uniform4fv(u.uHip, RIG.hip); gl.uniform3fv(u.uJ1, RIG.j1); gl.uniform3fv(u.uJ2, RIG.j2); gl.uniform3fv(u.uAx, RIG.axis); }
    if (u.uSun) { gl.uniform3fv(u.uSun, SUN); gl.uniform3fv(u.uSunCol, SUNC); }
  }
  const aniso = gl.getExtension('EXT_texture_filter_anisotropic');
  const texFrom = (src, tex = gl.createTexture()) => {
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, src);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    if (aniso) gl.texParameterf(gl.TEXTURE_2D, aniso.TEXTURE_MAX_ANISOTROPY_EXT, Math.min(16, gl.getParameter(aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT)));
    return tex;
  };
  const elements = siteArt.els, pageTex = texFrom(siteArt.bmp), atlasTex = texFrom(atlasSheet);
  const dataSheet = sheet(600, 800); paintData(dataSheet); const dataTex = texFrom(dataSheet);
  let pagePx = firstW;
  const trail = G.target(gl, 320, 200);
  gl.bindFramebuffer(gl.FRAMEBUFFER, trail.fbo); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
  const studio = G.envCube(gl, prog.env, weak ? 64 : 128);
  const shadow = G.shadowTarget(gl, weak ? 1024 : 2048);
  const lightVP = G.mat4();
  { const lv = G.mat4(), lp = G.mat4(); G.lookAt(lv, [SUN[0] * 40, SUN[1] * 40, SUN[2] * 40], [0, 0, 0], [0, 0, -1]); G.ortho(lp, -13.5, 13.5, -10.5, 10.5, 8, 72); G.mul(lightVP, lp, lv); }
  await pause();
  mark('textures');

  /* World, swarm and geometry. */
  const world = makeWorld(elements, compact);
  const count = compact ? 520 : weak ? 1000 : 1700, heroes = compact ? 4 : 7;
  const swarm = new Swarm(world, count, heroes);
  const packets = new Packets();
  const recordEl = []; elements.forEach((e, i) => { if (e.record >= 0) recordEl[e.record] = i; });
  const dyn = (floats) => { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, floats * 4, gl.DYNAMIC_DRAW); return b; };
  const INST = [[3, 3, 1], [4, 4, 1], [5, 4, 1], [6, 1, 1]], MESH = [[0, 3], [1, 3], [2, 3]];
  const vao = (parts, index) => {
    const v = gl.createVertexArray(); gl.bindVertexArray(v);
    for (const [buf, layout] of parts) G.bindLayout(gl, buf, layout);
    if (index) { gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer()); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, index, gl.STATIC_DRAW); }
    return v;
  };
  const corners = G.staticBuffer(gl, new Float32Array([-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1]));
  const lod = swarm.bufs.map((buf) => { const inst = dyn(buf.length); return { inst, contact: vao([[corners, [[0, 2]]], [inst, INST]]) }; });
  /* A robot model can be paired with any group of robots, so shadows and reflections can use a lighter model than the picture. */
  const model = [null, null, null], pairs = {};
  const fit = (level, data) => {
    const ibo = gl.createBuffer();
    gl.bindVertexArray(null); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, data.indices, gl.STATIC_DRAW);
    model[level] = { vbo: G.staticBuffer(gl, data.vertices), ibo, n: data.indices.length, type: data.indices instanceof Uint32Array ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT };
  };
  const pair = (m, group) => {
    const key = m * 3 + group;
    if (!pairs[key]) { const v = gl.createVertexArray(); gl.bindVertexArray(v); G.bindLayout(gl, model[m].vbo, MESH); G.bindLayout(gl, lod[group].inst, INST); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, model[m].ibo); gl.bindVertexArray(null); pairs[key] = v; }
    return pairs[key];
  };
  fit(1, models[1]); fit(2, models[2]);
  const slabVao = vao([[G.staticBuffer(gl, slabMesh()), [[0, 3], [1, 3]]]]);
  const floorVao = vao([[G.staticBuffer(gl, new Float32Array([-110, -90, 110, -90, 110, 70, -110, -90, 110, 70, -110, 70])), [[0, 2]]]]);
  const ribbon = ribbonMesh(world), ribbonVao = vao([[G.staticBuffer(gl, ribbon), [[0, 3], [1, 3]]]]);
  const fibres = streamMesh(world), fibreVao = vao([[G.staticBuffer(gl, fibres), [[0, 3], [1, 3]]]]);
  const litRows = new Float32Array(8), pulse = new Float32Array(6); litRows[0] = litRows[7] = 1;
  const MOTES = compact ? 40 : 110, motes = Array.from({ length: MOTES }, (_, i) => { const h = (k) => { const v = Math.sin(i * 127.1 + k * 311.7) * 43758.5453; return v - Math.floor(v); }; return [h(1) * 46 - 23, h(2) * 9, h(3) * 34 - 20, 0.05 + h(4) * 0.1, 0.15 + h(5) * 0.45]; });
  const glowBuf = new Float32Array((packets.max * 6 + 40 + MOTES) * 8), glowInst = dyn(glowBuf.length);
  const glowVao = vao([[corners, [[0, 2]]], [glowInst, [[3, 4, 1], [4, 4, 1]]]]);
  const trailBuf = dyn(count * 2), trailVao = vao([[trailBuf, [[0, 2]]]]);
  gl.bindVertexArray(null);
  await pause();
  mark('geometry');

  /* The page mirrors the scene: rows and status arrive there as messages. */
  let stage = -1;
  const lit = new Uint8Array(6);
  const showRow = (r) => { if (!lit[r]) { lit[r] = 1; post({ t: 'row', r }); flare = 1; } };
  const setStage = (i) => { if (i !== stage) { stage = i; post({ t: 'stage', i }); } };
  let flare = 0, ct = calm ? 15.6 : 2.2, fired = { f: [], p: [] };
  const hiRec = new Float32Array(6), hiRect = new Float32Array(48), hiA = new Float32Array(12), hiC = new Float32Array(12);
  swarm.onPacket = (x, z) => packets.send([x, 0.25, z], world.rowPos[(Math.random() * 6) | 0].slice(), -1);

  function direct(dt) {
    ct += dt;
    if (ct >= CYCLE) { ct -= CYCLE; fired = { f: [], p: [] }; lit.fill(0); post({ t: 'reset' }); }
    setStage(ct < 3 ? 0 : ct < 8 ? 1 : ct < 14.4 ? 2 : 3);
    swarm.linkBoost += ((ct < 3 ? 1 : 0.15) - swarm.linkBoost) * Math.min(1, dt * 3);
    for (let r = 0; r < 6; r++) {
      const t0 = 8.1 + r;
      if (!fired.f[r] && ct >= t0) { fired.f[r] = 1; swarm.setFocus(recordEl[r], 1.5); }
      if (!fired.p[r] && ct >= t0 + 0.7) {
        fired.p[r] = 1;
        for (let k = 0; k < 5; k++) packets.send(world.streams[r].from.slice(), world.rowPos[r].slice(), r, k * 0.09, k ? null : showRow);
      }
      const active = ct >= t0 && ct < t0 + 1.7 ? 1 : 0;
      hiRec[r] += (active - hiRec[r]) * Math.min(1, dt * (active ? 9 : 2.5));
      pulse[r] = hiRec[r]; litRows[r + 1] += ((lit[r] ? 1 : 0) - litRows[r + 1]) * Math.min(1, dt * 5);
    }
    if (ct >= 14.4) for (let r = 0; r < 6; r++) showRow(r);
    // highlight slots: 0-5 records being extracted (lime), 6-11 the busiest content boxes (cyan)
    const top = [];
    for (let i = 0; i < swarm.heat.length; i++) if (elements[i].record < 0 && swarm.heat[i] > 0.05) top.push(i);
    top.sort((a, b) => swarm.heat[b] - swarm.heat[a]);
    for (let k = 0; k < 12; k++) {
      const idx = k < 6 ? recordEl[k] : top[k - 6], e = idx === undefined ? null : elements[idx];
      hiA[k] = !e ? 0 : k < 6 ? hiRec[k] : swarm.heat[idx] * 0.75; hiC[k] = k < 6 ? 1 : 0;
      if (e) hiRect.set([e.x, e.y, e.w, e.h], k * 4);
    }
  }

  /* Camera: glides between the four stops as the page scrolls, with a little life of its own. */
  const proj = G.mat4(), view = G.mat4(), vp = G.mat4(), inv = G.mat4(), mview = G.mat4(), mvp = G.mat4(), eye = [0, 0, 0], meye = [0, 0, 0], fwd = [0, 0, -1];
  const shots = (compact ? SHOTS.tall : SHOTS.wide).slice(), fov = (compact ? 34 : 30) * DEG, cam = { focus: 8, ap: 1 };
  if (calm) shots[0] = SHOTS.still[compact ? 'tall' : 'wide'];
  let px = 0, py = 0, pxT = 0, pyT = 0, intro = calm ? 1 : 0, clock = 0, scroll = 0, eased = 0, anchor = null;
  function camera(dt) {
    px += (pxT - px) * Math.min(1, dt * 3); py += (pyT - py) * Math.min(1, dt * 3);
    eased += (scroll - eased) * Math.min(1, dt * 4.5);
    intro = Math.min(1, intro + dt / 3);
    const k = 1 - Math.pow(1 - intro, 3), u = Math.min(2.9999, Math.max(0, eased) * 3), i = Math.floor(u), f0 = u - i, f = f0 * f0 * (3 - 2 * f0), A = shots[i], B = shots[i + 1];
    const mix = (a, b) => a + (b - a) * f;
    const az = (mix(A.az, B.az) - (1 - k) * 14 + px * 2.6 + Math.sin(clock * 0.11) * 1.1) * DEG;
    const el = (mix(A.el, B.el) + (1 - k) * 9 - py * 1.4 + Math.sin(clock * 0.07) * 0.5) * DEG;
    const d = mix(A.d, B.d) + (1 - k) * 5, tx = mix(A.t[0], B.t[0]), ty = mix(A.t[1], B.t[1]), tz = mix(A.t[2], B.t[2]);
    eye[0] = tx + d * Math.sin(az) * Math.cos(el); eye[1] = ty + d * Math.sin(el); eye[2] = tz + d * Math.cos(az) * Math.cos(el);
    fwd[0] = (tx - eye[0]) / d; fwd[1] = (ty - eye[1]) / d; fwd[2] = (tz - eye[2]) / d;
    cam.focus = d; cam.ap = mix(A.ap, B.ap);
    G.perspective(proj, fov, W / H, 0.5, 220, mix(A.sx, B.sx), mix(A.sy, B.sy));
    G.lookAt(view, eye, [tx, ty, tz], [0, 1, 0]);
    G.mul(vp, proj, view); G.invert(inv, vp);
    mview.set(view); mview[4] = -view[4]; mview[5] = -view[5]; mview[6] = -view[6]; mview[7] = -view[7];   // the same view, seen in the glass
    G.mul(mvp, proj, mview); meye[0] = eye[0]; meye[1] = -eye[1]; meye[2] = eye[2];
    window_(Math.max(0, eased));
  }
  /* The screen the scene comes out of: a window in a wall. Its sill lies on the page, so the page's near corner and the
     robots on it pass through. As the visitor scrolls on, the window opens until the scene fills the whole view. */
  const win = { on: false, rect: new Float32Array(4), plane: 0, z: 0, s: 1 };
  function window_(v) {
    const open = calm ? 1 : Math.min(1, Math.max(0, (v - 0.012) / 0.15)), e = open * open * (3 - 2 * open), k = W / cssW, far = 90 * k;
    const side = compact ? 8 : Math.min(44, Math.max(18, cssW * 0.02)), top = topPx, sill = sillPx || cssH * 0.22;
    const at = (a, b) => a + (b - a) * e;
    win.rect.set([at(side * k, -far), at(sill * k, -far), at(W - side * k, W + far), at(H - top * k, H + far)]);
    win.s = k * (compact ? 0.62 : 1); win.on = false; win.plane = 0;
    if (e > 0.999) return;
    const hit = pick(0, (win.rect[1] / H) * 2 - 1, 0);
    if (!hit) return;
    const dP = (hit[0] - eye[0]) * fwd[0] + (hit[1] - eye[1]) * fwd[1] + (hit[2] - eye[2]) * fwd[2];
    if (dP < 0.8) return;
    win.on = true; win.plane = dP; win.z = (-proj[10] * dP + proj[14]) / dP;
  }
  /* Where a point of the picture (x and y from -1 to 1, y up) meets the plane y = planeY. */
  function pick(nx, ny, planeY) {
    const far = G.project(inv, [nx, ny, 1]), dx = far[0] - eye[0], dy = far[1] - eye[1], dz = far[2] - eye[2];
    const t = (planeY - eye[1]) / dy;
    return t > 0 && t < 1 ? [eye[0] + dx * t, planeY, eye[2] + dz * t] : null;
  }

  function resize() {
    let k = Math.min(dpr, dprCap) * quality;
    if (cssW * cssH * k * k > pixelCap) k = Math.sqrt(pixelCap / (cssW * cssH));
    const w = Math.max(8, Math.round(cssW * k)), h = Math.max(8, Math.round(cssH * k));
    if (w === W && h === H) return;
    W = canvas.width = w; H = canvas.height = h;
    [scene, half, soft, refl, b1, b1b, b2, b2b, pop, popB].forEach((t) => t && t.dispose());
    const samples = Math.min(gl.getParameter(gl.MAX_SAMPLES), W * H > 5e6 || compact ? 2 : 4);
    const hw = W >> 1, hh = H >> 1;
    scene = G.target(gl, W, H, { depth: true, samples, hdr: true });
    half = G.target(gl, hw, hh, { hdr: true }); soft = G.target(gl, hw, hh, { hdr: true });
    refl = mirror ? G.target(gl, hw, hh, { depth: true, hdr: true, mips: true }) : null;
    b1 = G.target(gl, W >> 2, H >> 2, { hdr: true }); b1b = G.target(gl, b1.w, b1.h, { hdr: true });
    b2 = G.target(gl, W >> 3, H >> 3, { hdr: true }); b2b = G.target(gl, b2.w, b2.h, { hdr: true });
    pop = G.target(gl, W >> 2, H >> 2); popB = G.target(gl, pop.w, pop.h);
  }

  const stats = { fps: 0, ms: 0, cpu: 0, draws: 0, bots: count, tris: 0 };
  let draws = 0, tris = 0, trailClock = 0, detail = weak ? 1.6 : 1;
  const use = (p) => { gl.useProgram(p.p); return p.u; };
  const eyeTo = (u, m, e) => { gl.uniformMatrix4fv(u.uVP, false, m); if (u.uEye) gl.uniform3fv(u.uEye, e); if (u.uFwd) gl.uniform3fv(u.uFwd, fwd); if (u.uFog) gl.uniform3fv(u.uFog, FOGC); if (u.uFogR) gl.uniform2f(u.uFogR, cam.focus + 26, cam.focus + 80); if (u.uTime) gl.uniform1f(u.uTime, clock); if (u.uEnvMips) gl.uniform1f(u.uEnvMips, studio.mips); };
  const fullscreen = () => { gl.drawArrays(gl.TRIANGLES, 0, 3); draws++; };
  const robots = (use3) => { for (let k = 0; k < 3; k++) { const m = model[use3[k]] ? use3[k] : 1, n = swarm.counts[k]; if (!n) continue; gl.bindVertexArray(pair(m, k)); gl.drawElementsInstanced(gl.TRIANGLES, model[m].n, model[m].type, 0, n); draws++; tris += (model[m].n / 3) * n; } };
  const bind = (unit, kind, tex) => { gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(kind, tex); };

  function render(dt) {
    let u;
    const scale = 1080 / Math.max(H, 400) * (weak ? 1.7 : 1);
    swarm.fill(eye, model[0] ? 0.08 * scale * detail : 1e9, 0.017 * scale * detail);
    lod.forEach((l, k) => { gl.bindBuffer(gl.ARRAY_BUFFER, l.inst); gl.bufferSubData(gl.ARRAY_BUFFER, 0, swarm.bufs[k], 0, swarm.counts[k] * STRIDE); });
    gl.disable(gl.CULL_FACE);

    /* trail heat, stepped at 30 Hz so it fades the same on every display */
    trailClock += dt;
    if (trailClock > 1 / 30) {
      trailClock = 0;
      gl.bindFramebuffer(gl.FRAMEBUFFER, trail.fbo); gl.viewport(0, 0, trail.w, trail.h);
      gl.disable(gl.DEPTH_TEST); gl.enable(gl.BLEND);
      gl.bindVertexArray(null);
      u = use(prog.flat);
      gl.blendFunc(gl.ZERO, gl.SRC_COLOR); gl.uniform4f(u.uColor, 0.955, 0.955, 0.955, 0.955); fullscreen();
      gl.blendEquation(gl.FUNC_REVERSE_SUBTRACT); gl.blendFunc(gl.ONE, gl.ONE); gl.uniform4f(u.uColor, 0.004, 0.004, 0.004, 0.004); fullscreen();
      gl.blendEquation(gl.FUNC_ADD);
      u = use(prog.trail); gl.uniform2f(u.uHalf, PAGE_W / 2, PAGE_D / 2); gl.uniform1f(u.uSize, 5);
      gl.bindVertexArray(trailVao); gl.bindBuffer(gl.ARRAY_BUFFER, trailBuf); gl.bufferSubData(gl.ARRAY_BUFFER, 0, swarm.trail, 0, swarm.trailCount * 2);
      gl.drawArrays(gl.POINTS, 0, swarm.trailCount); draws++;
    }
    gl.disable(gl.BLEND); gl.enable(gl.DEPTH_TEST); gl.depthMask(true);

    /* shadows from the key light */
    bind(3, gl.TEXTURE_2D, null);
    gl.bindFramebuffer(gl.FRAMEBUFFER, shadow.fbo); gl.viewport(0, 0, shadow.size, shadow.size);
    gl.clear(gl.DEPTH_BUFFER_BIT); gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(2, 3);
    u = use(prog.depth); gl.uniformMatrix4fv(u.uVP, false, lightVP);
    robots([1, 2, 2]);                                   // shadows are cast by lighter models
    gl.disable(gl.POLYGON_OFFSET_FILL);
    bind(1, gl.TEXTURE_2D, trail.tex); bind(2, gl.TEXTURE_CUBE_MAP, studio.tex); bind(3, gl.TEXTURE_2D, shadow.tex); bind(5, gl.TEXTURE_2D, pageTex);

    /* the robots as the glass sees them */
    if (refl) {
      bind(4, gl.TEXTURE_2D, null);
      gl.bindFramebuffer(gl.FRAMEBUFFER, refl.draw); gl.viewport(0, 0, refl.w, refl.h);
      gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      u = use(prog.bot); eyeTo(u, mvp, meye); gl.uniform1f(u.uMirror, 1); gl.uniformMatrix4fv(u.uLightVP, false, lightVP); gl.uniform2f(u.uPageSize, PAGE_W, PAGE_D);
      robots([1, 1, 2]);
      refl.resolve();
      bind(4, gl.TEXTURE_2D, refl.tex);
    }

    const pass = (p, to, tex, set) => { gl.bindFramebuffer(gl.FRAMEBUFFER, to ? to.fbo : null); gl.viewport(0, 0, to ? to.w : W, to ? to.h : H); const uu = use(p); bind(0, gl.TEXTURE_2D, tex); if (uu.uFocus) { gl.uniform1f(uu.uFocus, cam.focus); gl.uniform1f(uu.uAperture, cam.ap); } if (set) set(uu); fullscreen(); };
    if (win.on) {     /* what has come out through the window, as a soft shape: it shades the wall behind it */
      gl.bindFramebuffer(gl.FRAMEBUFFER, pop.fbo); gl.viewport(0, 0, pop.w, pop.h);
      gl.disable(gl.DEPTH_TEST); gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT);
      u = use(prog.maskSlab); eyeTo(u, vp, eye); gl.uniform1f(u.uPlane, win.plane); gl.uniform3f(u.uPos, 0, 0, 0); gl.uniform3f(u.uSize, PAGE_W, 0.34, PAGE_D);
      gl.bindVertexArray(slabVao); gl.drawArrays(gl.TRIANGLES, 0, 36); draws++;
      u = use(prog.maskBot); eyeTo(u, vp, eye); gl.uniform1f(u.uPlane, win.plane);
      robots([2, 2, 2]);
      gl.bindVertexArray(null);
      pass(prog.blur, popB, pop.tex, (uu) => gl.uniform2f(uu.uDir, 1.5 / pop.w, 0));
      pass(prog.blur, pop, popB.tex, (uu) => gl.uniform2f(uu.uDir, 0, 1.5 / pop.h));
      gl.enable(gl.DEPTH_TEST);
    }

    gl.bindFramebuffer(gl.FRAMEBUFFER, scene.draw); gl.viewport(0, 0, W, H);
    gl.clearColor(FOGC[0], FOGC[1], FOGC[2], 400); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.disable(gl.DEPTH_TEST); gl.bindVertexArray(null);
    u = use(prog.sky); gl.uniformMatrix4fv(u.uInv, false, inv); gl.uniform3fv(u.uEye, eye); gl.uniform3fv(u.uFog, FOGC); fullscreen();
    gl.enable(gl.DEPTH_TEST);
    u = use(prog.floor); eyeTo(u, vp, eye); gl.uniform1f(u.uY, -1.05); gl.uniform1f(u.uPlane, win.plane); gl.bindVertexArray(floorVao); gl.drawArrays(gl.TRIANGLES, 0, 6); draws++;

    u = use(prog.page); eyeTo(u, vp, eye); gl.uniformMatrix4fv(u.uLightVP, false, lightVP);
    gl.uniform4fv(u.uHi, hiRect); gl.uniform1fv(u.uHiA, hiA); gl.uniform1fv(u.uHiC, hiC);
    gl.uniform1f(u.uScan, ct >= 3 && ct < 8 ? -0.03 + ((ct - 3) / 5) * 1.06 : -1);
    gl.uniform2f(u.uRes, W, H); gl.uniform1f(u.uReflOn, refl ? 1 : 0);
    gl.bindVertexArray(slabVao); gl.uniform1f(u.uHolo, 0);
    for (const s of world.tiles) {       // the field of other pages
      bind(0, gl.TEXTURE_2D, atlasTex); gl.uniform1f(u.uMain, 0); gl.uniform4f(u.uAtlas, 0.5, 0.5, (s.atlas % 2) * 0.5, Math.floor(s.atlas / 2) * 0.5);
      gl.uniform3fv(u.uPos, s.pos); gl.uniform4fv(u.uQuat, s.q); gl.uniform3f(u.uSize, s.w, 0.1, s.d); gl.uniform2f(u.uDim, s.w, s.d);
      gl.drawArrays(gl.TRIANGLES, 0, 36); draws++;
    }
    { const s = world.data; bind(0, gl.TEXTURE_2D, dataTex); gl.uniform1f(u.uHolo, 1); gl.uniform1fv(u.uLit, litRows); gl.uniform1f(u.uMain, 0); gl.uniform4f(u.uAtlas, 1, 1, 0, 0);
      gl.uniform3fv(u.uPos, s.pos); gl.uniform4fv(u.uQuat, s.q); gl.uniform3f(u.uSize, s.w, 0.05, s.d); gl.uniform2f(u.uDim, s.w, s.d); gl.drawArrays(gl.TRIANGLES, 0, 36); draws++; gl.uniform1f(u.uHolo, 0); }
    world.surfaces.forEach((s, i) => {
      bind(0, gl.TEXTURE_2D, i ? atlasTex : pageTex);
      gl.uniform1f(u.uMain, i ? 0 : 1);
      if (i) gl.uniform4f(u.uAtlas, 0.5, 0.5, (s.atlas % 2) * 0.5, Math.floor(s.atlas / 2) * 0.5); else gl.uniform4f(u.uAtlas, 1, 1, 0, 0);
      gl.uniform3fv(u.uPos, s.pos); gl.uniform4fv(u.uQuat, s.q); gl.uniform3f(u.uSize, s.w, i ? 0.14 : 0.34, s.d); gl.uniform2f(u.uDim, s.w, s.d);
      gl.drawArrays(gl.TRIANGLES, 0, 36); draws++;
    });
    u = use(prog.link); eyeTo(u, vp, eye); gl.uniform1f(u.uAct, swarm.linkBoost);
    gl.bindVertexArray(ribbonVao); gl.drawArrays(gl.TRIANGLES, 0, ribbon.length / 6); draws++;

    gl.enable(gl.BLEND); gl.depthMask(false);
    gl.blendFuncSeparate(gl.ZERO, gl.ONE_MINUS_SRC_ALPHA, gl.ZERO, gl.ONE);
    u = use(prog.contact); gl.uniformMatrix4fv(u.uVP, false, vp);
    lod.forEach((l, k) => { if (swarm.counts[k]) { gl.bindVertexArray(l.contact); gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, swarm.counts[k]); draws++; } });
    gl.blendFunc(gl.ONE, gl.ONE);         // light: scan fans on the page and the data streams
    u = use(prog.beam); gl.uniformMatrix4fv(u.uVP, false, vp); gl.uniform1f(u.uTime, clock); gl.uniform2f(u.uHalf, PAGE_W / 2, PAGE_D / 2);
    for (let k = 0; k < 2; k++) if (swarm.counts[k]) { gl.bindVertexArray(lod[k].contact); gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, swarm.counts[k]); draws++; }
    u = use(prog.stream); eyeTo(u, vp, eye); gl.uniform1fv(u.uPulse, pulse);
    gl.bindVertexArray(fibreVao); gl.drawArrays(gl.TRIANGLES, 0, fibres.length / 6); draws++;
    gl.disable(gl.BLEND); gl.depthMask(true);
    u = use(prog.bot); eyeTo(u, vp, eye); gl.uniform1f(u.uMirror, 0); gl.uniformMatrix4fv(u.uLightVP, false, lightVP); gl.uniform2f(u.uPageSize, PAGE_W, PAGE_D);
    robots([0, 1, 2]);
    if (win.on) {     /* the wall and its window, standing in the scene at the sill's depth */
      gl.bindVertexArray(null);
      u = use(prog.wall); bind(0, gl.TEXTURE_2D, pop.tex);
      gl.uniform4fv(u.uRect, win.rect); gl.uniform2f(u.uRes, W, H); gl.uniform1f(u.uRad, 20 * win.s); gl.uniform1f(u.uPx, win.s); gl.uniform1f(u.uZ, win.z); gl.uniform1f(u.uFocus, cam.focus);
      gl.uniform1f(u.uMode, 0); fullscreen();
      gl.enable(gl.BLEND); gl.depthMask(false); gl.depthFunc(gl.LEQUAL); gl.blendFuncSeparate(gl.ONE, gl.ONE_MINUS_SRC_ALPHA, gl.ZERO, gl.ONE);
      gl.uniform1f(u.uMode, 1); fullscreen();
      gl.disable(gl.BLEND); gl.depthMask(true); gl.depthFunc(gl.LESS);
    }

    /* additive sprites: packets, the dataset dock, link ports, dust in the light */
    let g = packets.count * 8;
    glowBuf.set(packets.buf.subarray(0, g));
    const sprite = (p, size, r, gr, b, a) => { glowBuf.set([p[0], p[1], p[2], size, r, gr, b, a], g); g += 8; };
    for (const m of motes) sprite([m[0] + Math.sin(clock * 0.2 + m[1]) * 0.6, 0.3 + ((m[1] + clock * m[4] * 0.4) % 9), m[2]], m[3], 0.3, 0.85, 1, 0.22 + 0.18 * Math.sin(clock * m[4] * 3 + m[0]));
    for (let r = 0; r < 6; r++) sprite(world.rowPos[r], 0.3 + pulse[r] * 0.5, 0.1, 0.85, 1, 0.2 + pulse[r] * 0.7);
    for (const b of world.bridges) { sprite(b.p[0], 0.55, 0.1, 0.8, 1, 0.35 + 0.45 * swarm.linkBoost); sprite(b.p[3], 0.55, 0.1, 0.8, 1, 0.35 + 0.45 * swarm.linkBoost); }
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE); gl.depthMask(false);
    u = use(prog.glow); gl.uniformMatrix4fv(u.uVP, false, vp); gl.uniform3fv(u.uEye, eye);
    gl.uniform3f(u.uRight, view[0], view[4], view[8]); gl.uniform3f(u.uUp, view[1], view[5], view[9]);
    gl.bindVertexArray(glowVao); gl.bindBuffer(gl.ARRAY_BUFFER, glowInst); gl.bufferSubData(gl.ARRAY_BUFFER, 0, glowBuf, 0, g);
    gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, g / 8); draws++;
    gl.disable(gl.BLEND); gl.depthMask(true); gl.disable(gl.DEPTH_TEST);
    scene.resolve();

    /* lens: focus, bloom, grade */
    gl.bindVertexArray(null);
    pass(prog.half, half, scene.tex);
    pass(prog.dof, soft, half.tex, (uu) => { gl.uniform2f(uu.uPx, 1 / half.w, 1 / half.h); gl.uniform1f(uu.uRadius, half.h * 0.026); });
    pass(prog.bright, b1, half.tex);
    pass(prog.blur, b1b, b1.tex, (uu) => gl.uniform2f(uu.uDir, 1 / b1.w, 0));
    pass(prog.blur, b1, b1b.tex, (uu) => gl.uniform2f(uu.uDir, 0, 1 / b1.h));
    pass(prog.blur, b2b, b1.tex, (uu) => gl.uniform2f(uu.uDir, 1.6 / b2.w, 0));
    pass(prog.blur, b2, b2b.tex, (uu) => gl.uniform2f(uu.uDir, 0, 1.6 / b2.h));
    bind(6, gl.TEXTURE_2D, soft.tex); bind(7, gl.TEXTURE_2D, b1.tex); bind(8, gl.TEXTURE_2D, b2.tex);
    pass(prog.final, null, scene.tex, (uu) => { gl.uniform1f(uu.uTime, clock); gl.uniform2f(uu.uRes, W, H); gl.uniform1f(uu.uDebug, cfg.debug || 0); });
    stats.draws = draws; stats.tris = Math.round(tris); draws = 0; tris = 0;
  }

  function aimCollector() {}

  /* Start: settle the swarm so the first picture is already alive. */
  mark('wired');
  resize();
  mark('targets');
  for (let i = 0; i < 6; i++) { swarm.step(1 / 12); swarm.step(1 / 12); await pause(); }
  mark('settled');
  anchor = cfg.anchor || null;
  camera(0); aimCollector(); direct(0);
  let last = 0, raf = 0, visible = true, ema = 16, frames = 0, steady = 0, raises = 0;
  const report = () => post({ t: 'stats', fps: stats.fps, ms: stats.ms, cpu: stats.cpu, draws: stats.draws, tris: stats.tris, bots: stats.bots, size: [W, H], quality, pagePx, hero: !!model[0] });
  function frame(now) {
    raf = 0;
    const raw = last ? now - last : 16; last = now;
    const dt = Math.min(0.05, raw / 1000), t0 = performance.now();
    clock += dt; flare *= Math.max(0, 1 - dt * 2.4);
    camera(dt); swarm.step(dt); packets.step(dt); direct(dt); render(dt);
    if (++frames % 20 === 0) aimCollector();
    ema += (raw - ema) * 0.06; stats.ms = ema; stats.fps = 1000 / ema; stats.cpu += (performance.now() - t0 - stats.cpu) * 0.06;
    if (frames > 90 && frames % 45 === 0) {     // ease resolution, then robot count, before frames drop
      if (ema > 21.5) { steady = 0; if (detail < 2.4) detail *= 1.35; else if (quality > 0.56) { quality *= 0.86; resize(); } else if (swarm.n > 420) { swarm.n = (swarm.n * 0.75) | 0; stats.bots = swarm.n; } }
      else if (ema < 17.6 && quality < 1 && raises < 2 && ++steady > 10) { quality = Math.min(1, quality * 1.1); steady = 0; raises++; resize(); }
    }
    if (frames === 2) post({ t: 'on' });
    if (frames % 30 === 2) report();
    if (visible && !calm) raf = requestAnimationFrame(frame);
  }
  const wake = () => { if (!raf && visible && !calm) { last = 0; raf = requestAnimationFrame(frame); } };
  const still = () => { eased = scroll; camera(0); aimCollector(); render(0.04); report(); };
  if (calm) { flare = 0; packets.step(0); render(0.04); still(); post({ t: 'on' }); } else wake();

  /* Messages from the page: pointer, scroll, size, visibility and where the dataset panel sits. */
  function input(m) {
    if (m.t === 'pointer') {
      if (m.x === null) { pxT = pyT = 0; swarm.pointer = null; return; }
      pxT = m.x; pyT = m.y;
      const hit = pick(m.x, -m.y, 0);
      swarm.pointer = hit && Math.abs(hit[0]) < PAGE_W / 2 && Math.abs(hit[2]) < PAGE_D / 2 ? [hit[0], hit[2]] : null;
    } else if (m.t === 'scroll') { scroll = m.v; if (calm) still(); }
    else if (m.t === 'show') { visible = m.on; wake(); }
    else if (m.t === 'size') { cssW = m.cssW; cssH = m.cssH; dpr = m.dpr; anchor = m.anchor; sillPx = m.sill || sillPx; topPx = m.top || topPx; resize(); if (calm) still(); }
  }

  /* After the first picture: the full-detail robot for close shots, then sharper page art (2048 px, 4096 px on big screens). */
  setTimeout(() => {
    if (!weak) { fit(0, robotMesh(0)); if (calm) still(); }
    setTimeout(() => {
      const sharpen = (w) => { texFrom(paint(w).bmp, pageTex); pagePx = w; if (calm) still(); };
      sharpen(compact ? 1536 : 2048);
      if (!weak && W >= 2300 && gl.getParameter(gl.MAX_TEXTURE_SIZE) >= 4096) setTimeout(() => sharpen(4096), 1500);
    }, 500);
  }, 350);
  return { input };
}
