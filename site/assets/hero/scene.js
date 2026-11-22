/* Homepage crawl scene: a website being crawled by thousands of 3D bots, drawn live in WebGL2.
   The scene is illustrative; the labels on the page say so. No outside libraries or image assets. */
import * as G from './gl.js';
import * as S from './shaders.js';
import { botMesh, engineMesh, ringMesh, slabMesh } from './meshes.js';
import { paintSite, paintAtlas } from './page-art.js';
import { makeWorld, ribbonMesh, PAGE_W, PAGE_D } from './world.js';
import { Swarm, Packets, STRIDE } from './swarm.js';

const canvas = document.getElementById('crawl-scene');
const hero = canvas && canvas.closest('.hero');
const pause = () => new Promise((r) => setTimeout(r, 0));
const DEG = Math.PI / 180, CYCLE = 18, FOGC = [0.014, 0.04, 0.058];
const STATUS = ['Finding links', 'Reading pages', 'Extracting fields', '6 records · JSON ready'];

if (canvas && hero) start().catch((err) => { hero.classList.add('no-gl'); console.warn('Crawl scene unavailable:', err && err.message); });

async function start() {
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const gl = canvas.getContext('webgl2', { alpha: false, antialias: false, depth: false, stencil: false, powerPreference: 'high-performance' });
  if (!gl) throw new Error('WebGL2 is not available');
  let cssW = hero.clientWidth, cssH = hero.clientHeight;
  const compact = cssW < 900 || cssW / cssH < 1.05;
  const weak = compact || (navigator.deviceMemory || 8) <= 4 || (navigator.hardwareConcurrency || 8) <= 4;
  const dprCap = compact ? 2 : 3, pixelCap = compact ? 2.2e6 : 8.4e6;
  let quality = 1, W = 2, H = 2, scene = null, b1 = null, b1b = null, b2 = null, b2b = null;

  const prog = {};
  for (const [k, vs, fs] of [['bot', S.BOT_VS, S.BOT_FS], ['shadow', S.SHADOW_VS, S.SHADOW_FS], ['page', S.PAGE_VS, S.PAGE_FS],
    ['trail', S.TRAIL_VS, S.TRAIL_FS], ['flat', S.QUAD_VS, S.FLAT_FS], ['sky', S.QUAD_VS, S.SKY_FS], ['floor', S.FLOOR_VS, S.FLOOR_FS],
    ['link', S.LINK_VS, S.LINK_FS], ['glow', S.GLOW_VS, S.GLOW_FS], ['bright', S.QUAD_VS, S.BRIGHT_FS], ['blur', S.QUAD_VS, S.BLUR_FS], ['final', S.QUAD_VS, S.FINAL_FS]]) {
    prog[k] = G.program(gl, vs, fs);
  }
  await pause();

  /* Textures: the page art is painted in code, 2048 px first, 4096 px once the screen can show it. */
  const aniso = gl.getExtension('EXT_texture_filter_anisotropic');
  const texFrom = (cv, tex = gl.createTexture()) => {
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, cv);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    if (aniso) gl.texParameterf(gl.TEXTURE_2D, aniso.TEXTURE_MAX_ANISOTROPY_EXT, Math.min(16, gl.getParameter(aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT)));
    return tex;
  };
  const sheet = (w, h) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };
  const first = sheet(compact ? 1536 : 2048, compact ? 960 : 1280);
  const elements = paintSite(first);
  const pageTex = texFrom(first);
  let pagePx = first.width;
  const atlasSheet = sheet(compact ? 1024 : 2048, compact ? 640 : 1280); paintAtlas(atlasSheet);
  const atlasTex = texFrom(atlasSheet);
  const trail = G.target(gl, 320, 200);
  gl.bindFramebuffer(gl.FRAMEBUFFER, trail.fbo); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
  await pause();

  /* World, swarm and geometry. */
  const world = makeWorld(elements, compact);
  const count = compact ? 720 : weak ? 1500 : 2600;
  const fov = (compact ? 36 : 30) * DEG, camDist = compact ? 23 : 31;
  const pxPerUnit = (h) => h / (2 * camDist * Math.tan(fov / 2));
  const hiSize = 36 / (pxPerUnit(cssH * Math.min(devicePixelRatio || 1, dprCap)) * 1.5);   // detailed mesh once a bot is ~36 px wide
  const swarm = new Swarm(world, count, Math.max(hiSize, weak ? 0.4 : 0.17));               // on big screens the small bots get the detailed mesh too
  const packets = new Packets();
  const recordEl = []; elements.forEach((e, i) => { if (e.record >= 0) recordEl[e.record] = i; });
  const dyn = (floats) => { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, floats * 4, gl.DYNAMIC_DRAW); return b; };
  const INST = [[3, 3, 1], [4, 4, 1], [5, 4, 1], [6, 1, 1]];
  const vao = (parts) => { const v = gl.createVertexArray(); gl.bindVertexArray(v); for (const [buf, layout] of parts) G.bindLayout(gl, buf, layout); return v; };
  const MESH = [[0, 3], [1, 3], [2, 4]];
  const corners = G.staticBuffer(gl, new Float32Array([-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1]));
  const lod = [2, 1, 0].map((d) => {
    const data = botMesh(d), inst = dyn(count * STRIDE);
    return { inst, verts: data.length / 10, vao: vao([[G.staticBuffer(gl, data), MESH], [inst, INST]]), shadow: vao([[corners, [[0, 2]]], [inst, INST]]) };
  });
  const engData = engineMesh(), ringData = ringMesh();
  const engInst = dyn(STRIDE), ringInst = dyn(STRIDE * 2), ringBuf = new Float32Array(STRIDE * 2);
  const engVao = vao([[G.staticBuffer(gl, engData), MESH], [engInst, INST]]), ringVao = vao([[G.staticBuffer(gl, ringData), MESH], [ringInst, INST]]);
  const E = world.engine;
  gl.bindBuffer(gl.ARRAY_BUFFER, engInst);
  gl.bufferSubData(gl.ARRAY_BUFFER, 0, new Float32Array([...E.pos, ...E.q, E.scale, 0, 0, 0.35, 1.7]));
  const slabVao = vao([[G.staticBuffer(gl, slabMesh()), [[0, 3], [1, 3]]]]);
  const floorVao = vao([[G.staticBuffer(gl, new Float32Array([-90, -70, 90, -70, 90, 60, -90, -70, 90, 60, -90, 60])), [[0, 2]]]]);
  const ribbon = ribbonMesh(world), ribbonVao = vao([[G.staticBuffer(gl, ribbon), [[0, 3], [1, 3]]]]);
  const MOTES = compact ? 40 : 90, motes = Array.from({ length: MOTES }, (_, i) => { const h = (k) => { const v = Math.sin(i * 127.1 + k * 311.7) * 43758.5453; return v - Math.floor(v); }; return [h(1) * 46 - 23, h(2) * 11, h(3) * 30 - 20, 0.07 + h(4) * 0.13, 0.15 + h(5) * 0.45]; });
  const glowBuf = new Float32Array((packets.max * 6 + 40 + MOTES) * 8), glowInst = dyn(glowBuf.length);
  const glowVao = vao([[corners, [[0, 2]]], [glowInst, [[3, 4, 1], [4, 4, 1]]]]);
  const trailBuf = dyn(count * 2), trailVao = vao([[trailBuf, [[0, 2]]]]);
  gl.bindVertexArray(null);
  await pause();

  /* Hero DOM that mirrors the scene. */
  const rows = [...hero.querySelectorAll('[data-row]')], stages = [...hero.querySelectorAll('[data-stage]')];
  const statusEl = hero.querySelector('[data-status]'), countEl = hero.querySelector('[data-count]'), panel = hero.querySelector('.dataset');
  let shown = 0, stage = -1;
  const showRow = (r) => { if (rows[r] && !rows[r].classList.contains('on')) { rows[r].classList.add('on'); shown++; if (countEl) countEl.textContent = shown; flare = 1; } };
  const setStage = (i) => { if (i === stage) return; stage = i; stages.forEach((s, k) => s.classList.toggle('on', k === i)); if (statusEl) statusEl.textContent = STATUS[i]; };
  let flare = 0, ct = calm ? 15.6 : 2.2, fired = { f: [], p: [] };
  const hiRec = new Float32Array(6), hiRect = new Float32Array(48), hiA = new Float32Array(12), hiC = new Float32Array(12);
  const collector = [9.5, 1.6, 6.5];
  swarm.onPacket = (x, z) => packets.send([x, 0.25, z], collector.slice(), -1);

  function direct(dt) {
    ct += dt;
    if (ct >= CYCLE) { ct -= CYCLE; fired = { f: [], p: [] }; rows.forEach((r) => r.classList.remove('on')); shown = 0; if (countEl) countEl.textContent = 0; }
    setStage(ct < 3 ? 0 : ct < 8 ? 1 : ct < 14.4 ? 2 : 3);
    swarm.linkBoost += ((ct < 3 ? 1 : 0.15) - swarm.linkBoost) * Math.min(1, dt * 3);
    for (let r = 0; r < 6; r++) {
      const t0 = 8.1 + r;
      if (!fired.f[r] && ct >= t0) { fired.f[r] = 1; swarm.setFocus(recordEl[r], 1.5); }
      if (!fired.p[r] && ct >= t0 + 0.7) {
        fired.p[r] = 1;
        const e = elements[recordEl[r]], from = [(e.x + e.w / 2 - 0.5) * PAGE_W, 0.25, (e.y + e.h / 2 - 0.5) * PAGE_D];
        for (let k = 0; k < 5; k++) packets.send([from[0] + (Math.random() - 0.5) * e.w * PAGE_W * 0.7, 0.25, from[2] + (Math.random() - 0.5) * e.h * PAGE_D * 0.7], collector.slice(), r, k * 0.09, k ? null : showRow);
      }
      const active = ct >= t0 && ct < t0 + 1.7 ? 1 : 0;
      hiRec[r] += (active - hiRec[r]) * Math.min(1, dt * (active ? 9 : 2.5));
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

  /* Camera. */
  const proj = G.mat4(), view = G.mat4(), vp = G.mat4(), inv = G.mat4(), eye = [0, 0, 0];
  const base = compact ? { tx: 1.6, tz: 0.4, az: -10, el: 33, sx: 0, sy: -0.64 } : { tx: 1.0, tz: 0.3, az: -16, el: 37, sx: 0.36, sy: -0.2 };
  let px = 0, py = 0, pxT = 0, pyT = 0, intro = calm ? 1 : 0, clock = 0;
  function camera(dt) {
    px += (pxT - px) * Math.min(1, dt * 3); py += (pyT - py) * Math.min(1, dt * 3);
    intro = Math.min(1, intro + dt / 2.6);
    const k = 1 - Math.pow(1 - intro, 3), sc = Math.min(1, Math.max(0, scrollY / (cssH * 0.9)));
    const az = (base.az - (1 - k) * 16 + px * 3.2 + Math.sin(clock * 0.11) * 1.3 - sc * 6) * DEG;
    const el = (base.el + (1 - k) * 15 - py * 2 + Math.sin(clock * 0.07) * 0.7 - sc * 13) * DEG;
    const d = camDist + (1 - k) * 11 - sc * 8;
    const tx = base.tx + sc * 1.2, tz = base.tz - sc * 1.4;
    eye[0] = tx + d * Math.sin(az) * Math.cos(el); eye[1] = d * Math.sin(el); eye[2] = tz + d * Math.cos(az) * Math.cos(el);
    G.perspective(proj, fov, W / H, 1, 160, base.sx, base.sy);
    G.lookAt(view, eye, [tx, 0, tz], [0, 1, 0]);
    G.mul(vp, proj, view); G.invert(inv, vp);
  }
  /* Where a point of the hero (client coordinates) meets the plane y = planeY. */
  function pick(cx, cy, planeY) {
    const r = canvas.getBoundingClientRect(), nx = ((cx - r.left) / r.width) * 2 - 1, ny = 1 - ((cy - r.top) / r.height) * 2;
    const far = G.project(inv, [nx, ny, 1]), dx = far[0] - eye[0], dy = far[1] - eye[1], dz = far[2] - eye[2];
    const t = (planeY - eye[1]) / dy;
    return t > 0 && t < 1 ? [eye[0] + dx * t, planeY, eye[2] + dz * t] : null;
  }
  hero.addEventListener('pointermove', (e) => {
    const r = hero.getBoundingClientRect();
    pxT = ((e.clientX - r.left) / r.width) * 2 - 1; pyT = ((e.clientY - r.top) / r.height) * 2 - 1;
    const hit = pick(e.clientX, e.clientY, 0);
    swarm.pointer = hit && Math.abs(hit[0]) < PAGE_W / 2 && Math.abs(hit[2]) < PAGE_D / 2 ? [hit[0], hit[2]] : null;
  }, { passive: true });
  hero.addEventListener('pointerleave', () => { pxT = pyT = 0; swarm.pointer = null; }, { passive: true });

  function resize() {
    const r = hero.getBoundingClientRect(); cssW = r.width; cssH = r.height;
    let k = Math.min(devicePixelRatio || 1, dprCap) * quality;
    if (cssW * cssH * k * k > pixelCap) k = Math.sqrt(pixelCap / (cssW * cssH));
    const w = Math.max(2, Math.round(cssW * k)), h = Math.max(2, Math.round(cssH * k));
    if (w === W && h === H) return;
    W = canvas.width = w; H = canvas.height = h;
    [scene, b1, b1b, b2, b2b].forEach((t) => t && t.dispose());
    const samples = Math.min(gl.getParameter(gl.MAX_SAMPLES), W * H > 5e6 || compact ? 2 : 4);
    scene = G.target(gl, W, H, { depth: true, samples });
    b1 = G.target(gl, W >> 1 || 1, H >> 1 || 1); b1b = G.target(gl, b1.w, b1.h);
    b2 = G.target(gl, W >> 2 || 1, H >> 2 || 1); b2b = G.target(gl, b2.w, b2.h);
  }

  const stats = { project: (p) => { const n = G.project(vp, p); return [Math.round((n[0] * 0.5 + 0.5) * cssW), Math.round((0.5 - n[1] * 0.5) * cssH)]; }, fps: 0, ms: 0, draws: 0, bots: count, get size() { return [W, H]; }, get quality() { return quality; }, get pagePx() { return pagePx; } };
  window.__crawlScene = stats;
  let draws = 0, trailClock = 0;
  const use = (p) => { gl.useProgram(p.p); return p.u; };
  const shared = (u) => { gl.uniformMatrix4fv(u.uVP, false, vp); if (u.uEye) gl.uniform3fv(u.uEye, eye); if (u.uFog) gl.uniform3fv(u.uFog, FOGC); if (u.uFogR) gl.uniform2f(u.uFogR, camDist + 9, camDist + 44); if (u.uTime) gl.uniform1f(u.uTime, clock); };
  const fullscreen = () => { gl.drawArrays(gl.TRIANGLES, 0, 3); draws++; };

  function render(dt) {
    let u;
    swarm.fill();
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

    gl.bindFramebuffer(gl.FRAMEBUFFER, scene.draw); gl.viewport(0, 0, W, H);
    gl.disable(gl.BLEND); gl.disable(gl.DEPTH_TEST); gl.depthMask(true);
    gl.clearColor(FOGC[0], FOGC[1], FOGC[2], 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.bindVertexArray(null);
    u = use(prog.sky); gl.uniform1f(u.uTime, clock); gl.uniform1f(u.uAspect, W / H); fullscreen();
    gl.enable(gl.DEPTH_TEST);
    u = use(prog.floor); shared(u); gl.uniform1f(u.uY, -1.05); gl.bindVertexArray(floorVao); gl.drawArrays(gl.TRIANGLES, 0, 6); draws++;

    u = use(prog.page); shared(u);
    gl.uniform1i(u.uTex, 0); gl.uniform1i(u.uTrail, 1);
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, trail.tex); gl.activeTexture(gl.TEXTURE0);
    gl.uniform4fv(u.uHi, hiRect); gl.uniform1fv(u.uHiA, hiA); gl.uniform1fv(u.uHiC, hiC);
    gl.uniform1f(u.uScan, ct >= 3 && ct < 8 ? -0.03 + ((ct - 3) / 5) * 1.06 : -1);
    gl.bindVertexArray(slabVao);
    world.surfaces.forEach((s, i) => {
      gl.bindTexture(gl.TEXTURE_2D, i ? atlasTex : pageTex);
      gl.uniform1f(u.uMain, i ? 0 : 1);
      if (i) gl.uniform4f(u.uAtlas, 0.5, 0.5, (s.atlas % 2) * 0.5, Math.floor(s.atlas / 2) * 0.5); else gl.uniform4f(u.uAtlas, 1, 1, 0, 0);
      gl.uniform3fv(u.uPos, s.pos); gl.uniform4fv(u.uQuat, s.q); gl.uniform3f(u.uSize, s.w, i ? 0.14 : 0.3, s.d); gl.uniform2f(u.uDim, s.w, s.d);
      gl.drawArrays(gl.TRIANGLES, 0, 36); draws++;
    });
    u = use(prog.link); shared(u); gl.uniform1f(u.uAct, swarm.linkBoost);
    gl.bindVertexArray(ribbonVao); gl.drawArrays(gl.TRIANGLES, 0, ribbon.length / 6); draws++;

    u = use(prog.bot); shared(u);
    gl.bindVertexArray(engVao); gl.drawArraysInstanced(gl.TRIANGLES, 0, engData.length / 10, 1); draws++;
    for (let k = 0; k < 2; k++) {
      const q = G.qMul(E.q, G.qAxis(1, 0, 0, clock * (k ? -0.9 : 0.6))), p = G.qRot(E.q, [(k ? -0.55 : 0.28) * E.scale, 0, 0]);
      ringBuf.set([E.pos[0] + p[0], E.pos[1] + p[1], E.pos[2] + p[2], ...q, E.scale * (k ? 0.86 : 1), 0, 0, 0.6, 1.7], k * STRIDE);
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, ringInst); gl.bufferSubData(gl.ARRAY_BUFFER, 0, ringBuf);
    gl.bindVertexArray(ringVao); gl.drawArraysInstanced(gl.TRIANGLES, 0, ringData.length / 10, 2); draws++;

    gl.enable(gl.BLEND); gl.depthMask(false);
    gl.blendFuncSeparate(gl.ZERO, gl.ONE_MINUS_SRC_ALPHA, gl.ZERO, gl.ONE);
    u = use(prog.shadow); gl.uniformMatrix4fv(u.uVP, false, vp);
    lod.forEach((l, k) => { gl.bindVertexArray(l.shadow); gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, swarm.counts[k]); draws++; });
    gl.disable(gl.BLEND); gl.depthMask(true);
    u = use(prog.bot); shared(u);
    lod.forEach((l, k) => { gl.bindVertexArray(l.vao); gl.drawArraysInstanced(gl.TRIANGLES, 0, l.verts, swarm.counts[k]); draws++; });

    /* additive sprites: packets, the engine core, the dataset dock, link ports */
    let g = packets.count * 8;
    glowBuf.set(packets.buf.subarray(0, g));
    const sprite = (p, size, r, gr, b, a) => { glowBuf.set([p[0], p[1], p[2], size, r, gr, b, a], g); g += 8; };
    for (const m of motes) sprite([m[0] + Math.sin(clock * 0.2 + m[1]) * 0.6, 0.4 + ((m[1] + clock * m[4] * 0.5) % 11), m[2]], m[3], 0.2, 0.85, 1, 0.35 + 0.3 * Math.sin(clock * m[4] * 3 + m[0]));
    const mouth = world.bridges[world.ramp].p[0];
    sprite(mouth, 2.6 * E.scale, 0.1, 0.8, 1, 0.5 + 0.12 * Math.sin(clock * 2.3));
    sprite(collector, 1.1 + flare * 0.9, 0.6, 1, 0.32, 0.3 + flare * 0.45);
    for (const b of world.bridges) if (!b.spawn) { sprite(b.p[0], 0.55, 0.1, 0.8, 1, 0.35 + 0.45 * swarm.linkBoost); sprite(b.p[3], 0.55, 0.1, 0.8, 1, 0.35 + 0.45 * swarm.linkBoost); }
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE); gl.depthMask(false);
    u = use(prog.glow); gl.uniformMatrix4fv(u.uVP, false, vp);
    gl.uniform3f(u.uRight, view[0], view[4], view[8]); gl.uniform3f(u.uUp, view[1], view[5], view[9]);
    gl.bindVertexArray(glowVao); gl.bindBuffer(gl.ARRAY_BUFFER, glowInst); gl.bufferSubData(gl.ARRAY_BUFFER, 0, glowBuf, 0, g);
    gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, g / 8); draws++;
    gl.disable(gl.BLEND); gl.depthMask(true); gl.disable(gl.DEPTH_TEST);
    scene.resolve();

    /* bloom from the emissive mask, then the final grade */
    gl.bindVertexArray(null);
    const pass = (p, to, tex, set) => { gl.bindFramebuffer(gl.FRAMEBUFFER, to ? to.fbo : null); gl.viewport(0, 0, to ? to.w : W, to ? to.h : H); const uu = use(p); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, tex); gl.uniform1i(uu.uTex, 0); if (set) set(uu); fullscreen(); };
    pass(prog.bright, b1, scene.tex);
    pass(prog.blur, b1b, b1.tex, (uu) => gl.uniform2f(uu.uDir, 1 / b1.w, 0));
    pass(prog.blur, b1, b1b.tex, (uu) => gl.uniform2f(uu.uDir, 0, 1 / b1.h));
    pass(prog.blur, b2b, b1.tex, (uu) => gl.uniform2f(uu.uDir, 1.6 / b2.w, 0));
    pass(prog.blur, b2, b2b.tex, (uu) => gl.uniform2f(uu.uDir, 0, 1.6 / b2.h));
    pass(prog.final, null, scene.tex, (uu) => {
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, b1.tex); gl.uniform1i(uu.uB1, 1);
      gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, b2.tex); gl.uniform1i(uu.uB2, 2);
      gl.uniform1f(uu.uTime, clock); gl.uniform2f(uu.uRes, W, H); gl.activeTexture(gl.TEXTURE0);
    });
    stats.draws = draws; draws = 0;
  }

  function aimCollector() {
    if (!panel || !panel.offsetParent) return;
    const r = panel.getBoundingClientRect(), hit = pick(r.left + 22, r.top + r.height * 0.5, 1.5);
    if (hit) { collector[0] = hit[0]; collector[1] = hit[1]; collector[2] = hit[2]; }
  }

  /* Start: settle the swarm off-screen so the first picture is already alive. */
  resize();
  for (let i = 0; i < 5; i++) { for (let k = 0; k < 24; k++) swarm.step(1 / 24); await pause(); }
  camera(0); aimCollector(); direct(0);
  let last = 0, raf = 0, visible = true, ema = 16, frames = 0, steady = 0, raises = 0;
  function frame(now) {
    raf = 0;
    const raw = last ? now - last : 16; last = now;
    const dt = Math.min(0.05, raw / 1000);
    clock += dt; flare *= Math.max(0, 1 - dt * 2.4);
    camera(dt); swarm.step(dt); packets.step(dt); direct(dt); render(dt);
    if (++frames % 20 === 0) aimCollector();
    ema += (raw - ema) * 0.06; stats.ms = ema; stats.fps = 1000 / ema;
    if (frames > 90 && frames % 45 === 0) {     // ease resolution, then bot count, before frames drop
      if (ema > 21.5) { steady = 0; if (quality > 0.56) { quality *= 0.86; resize(); } else if (swarm.n > 420) { swarm.n = (swarm.n * 0.75) | 0; stats.bots = swarm.n; } }
      else if (ema < 17.6 && quality < 1 && raises < 2 && ++steady > 10) { quality = Math.min(1, quality * 1.1); steady = 0; raises++; resize(); }
    }
    if (frames === 2) canvas.classList.add('on');
    if (visible && !document.hidden && !calm) raf = requestAnimationFrame(frame);
  }
  const wake = () => { if (!raf && visible && !document.hidden) { last = 0; raf = requestAnimationFrame(frame); } };
  if (calm) { flare = 0; packets.step(0); render(0.04); render(0.04); canvas.classList.add('on'); }
  else {
    new IntersectionObserver((es) => { visible = es[0].isIntersecting; wake(); }, { threshold: 0 }).observe(hero);
    document.addEventListener('visibilitychange', wake);
    wake();
  }
  let rz = 0;
  addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { resize(); if (calm) { camera(0); render(0.04); } }, 120); }, { passive: true });

  /* Sharper page art once the first picture is up: 4096 px, only where the screen can use it. */
  if (!weak && W >= 2300 && gl.getParameter(gl.MAX_TEXTURE_SIZE) >= 4096) setTimeout(() => {
    const big = sheet(4096, 2560); paintSite(big); texFrom(big, pageTex); pagePx = 4096;
    if (calm) render(0.04);
  }, 900);
}
