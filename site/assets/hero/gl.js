/* Small WebGL2 toolkit for the homepage crawl scene: matrices, quaternions, programs, render targets. */

export function mat4() {
  const m = new Float32Array(16);
  m[0] = m[5] = m[10] = m[15] = 1;
  return m;
}

export function perspective(out, fovy, aspect, near, far, shiftX = 0, shiftY = 0) {
  const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
  out.fill(0);
  out[0] = f / aspect; out[5] = f;
  out[8] = -shiftX; out[9] = -shiftY;          // lens shift: moves the picture without changing perspective
  out[10] = (far + near) * nf; out[11] = -1;
  out[14] = 2 * far * near * nf;
  return out;
}

export function lookAt(out, e, c, up) {
  let zx = e[0] - c[0], zy = e[1] - c[1], zz = e[2] - c[2];
  let l = 1 / Math.hypot(zx, zy, zz); zx *= l; zy *= l; zz *= l;
  let xx = up[1] * zz - up[2] * zy, xy = up[2] * zx - up[0] * zz, xz = up[0] * zy - up[1] * zx;
  l = 1 / Math.hypot(xx, xy, xz); xx *= l; xy *= l; xz *= l;
  const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
  out[0] = xx; out[1] = yx; out[2] = zx; out[3] = 0;
  out[4] = xy; out[5] = yy; out[6] = zy; out[7] = 0;
  out[8] = xz; out[9] = yz; out[10] = zz; out[11] = 0;
  out[12] = -(xx * e[0] + xy * e[1] + xz * e[2]);
  out[13] = -(yx * e[0] + yy * e[1] + yz * e[2]);
  out[14] = -(zx * e[0] + zy * e[1] + zz * e[2]);
  out[15] = 1;
  return out;
}

export function mul(out, a, b) {
  for (let c = 0; c < 4; c++) {
    const b0 = b[c * 4], b1 = b[c * 4 + 1], b2 = b[c * 4 + 2], b3 = b[c * 4 + 3];
    out[c * 4] = a[0] * b0 + a[4] * b1 + a[8] * b2 + a[12] * b3;
    out[c * 4 + 1] = a[1] * b0 + a[5] * b1 + a[9] * b2 + a[13] * b3;
    out[c * 4 + 2] = a[2] * b0 + a[6] * b1 + a[10] * b2 + a[14] * b3;
    out[c * 4 + 3] = a[3] * b0 + a[7] * b1 + a[11] * b2 + a[15] * b3;
  }
  return out;
}

export function invert(out, a) {
  const a00 = a[0], a01 = a[1], a02 = a[2], a03 = a[3], a10 = a[4], a11 = a[5], a12 = a[6], a13 = a[7];
  const a20 = a[8], a21 = a[9], a22 = a[10], a23 = a[11], a30 = a[12], a31 = a[13], a32 = a[14], a33 = a[15];
  const b00 = a00 * a11 - a01 * a10, b01 = a00 * a12 - a02 * a10, b02 = a00 * a13 - a03 * a10;
  const b03 = a01 * a12 - a02 * a11, b04 = a01 * a13 - a03 * a11, b05 = a02 * a13 - a03 * a12;
  const b06 = a20 * a31 - a21 * a30, b07 = a20 * a32 - a22 * a30, b08 = a20 * a33 - a23 * a30;
  const b09 = a21 * a32 - a22 * a31, b10 = a21 * a33 - a23 * a31, b11 = a22 * a33 - a23 * a32;
  let det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
  if (!det) return null;
  det = 1 / det;
  out[0] = (a11 * b11 - a12 * b10 + a13 * b09) * det; out[1] = (a02 * b10 - a01 * b11 - a03 * b09) * det;
  out[2] = (a31 * b05 - a32 * b04 + a33 * b03) * det; out[3] = (a22 * b04 - a21 * b05 - a23 * b03) * det;
  out[4] = (a12 * b08 - a10 * b11 - a13 * b07) * det; out[5] = (a00 * b11 - a02 * b08 + a03 * b07) * det;
  out[6] = (a32 * b02 - a30 * b05 - a33 * b01) * det; out[7] = (a20 * b05 - a22 * b02 + a23 * b01) * det;
  out[8] = (a10 * b10 - a11 * b08 + a13 * b06) * det; out[9] = (a01 * b08 - a00 * b10 - a03 * b06) * det;
  out[10] = (a30 * b04 - a31 * b02 + a33 * b00) * det; out[11] = (a21 * b02 - a20 * b04 - a23 * b00) * det;
  out[12] = (a11 * b07 - a10 * b09 - a12 * b06) * det; out[13] = (a00 * b09 - a01 * b07 + a02 * b06) * det;
  out[14] = (a31 * b01 - a30 * b03 - a32 * b00) * det; out[15] = (a20 * b03 - a21 * b01 + a22 * b00) * det;
  return out;
}

/* Transform a point by a matrix with perspective divide. */
export function project(m, p) {
  const w = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15] || 1;
  return [(m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12]) / w,
    (m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13]) / w,
    (m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14]) / w];
}

export const qAxis = (x, y, z, a) => { const s = Math.sin(a / 2); return [x * s, y * s, z * s, Math.cos(a / 2)]; };

export function qMul(a, b) {
  return [a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
    a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
    a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
    a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2]];
}

export function qRot(q, v) {
  const x = q[0], y = q[1], z = q[2], w = q[3];
  const cx = y * v[2] - z * v[1] + w * v[0], cy = z * v[0] - x * v[2] + w * v[1], cz = x * v[1] - y * v[0] + w * v[2];
  return [v[0] + 2 * (y * cz - z * cy), v[1] + 2 * (z * cx - x * cz), v[2] + 2 * (x * cy - y * cx)];
}

/* Quaternion from three orthonormal axes (the columns of a rotation matrix). Writes into out at offset o. */
export function qFromBasis(out, o, xx, xy, xz, yx, yy, yz, zx, zy, zz) {
  const tr = xx + yy + zz;
  let s;
  if (tr > 0) { s = Math.sqrt(tr + 1) * 2; out[o + 3] = s / 4; out[o] = (yz - zy) / s; out[o + 1] = (zx - xz) / s; out[o + 2] = (xy - yx) / s; }
  else if (xx > yy && xx > zz) { s = Math.sqrt(1 + xx - yy - zz) * 2; out[o + 3] = (yz - zy) / s; out[o] = s / 4; out[o + 1] = (yx + xy) / s; out[o + 2] = (zx + xz) / s; }
  else if (yy > zz) { s = Math.sqrt(1 + yy - xx - zz) * 2; out[o + 3] = (zx - xz) / s; out[o] = (yx + xy) / s; out[o + 1] = s / 4; out[o + 2] = (zy + yz) / s; }
  else { s = Math.sqrt(1 + zz - xx - yy) * 2; out[o + 3] = (xy - yx) / s; out[o] = (zx + xz) / s; out[o + 1] = (zy + yz) / s; out[o + 2] = s / 4; }
}

/* Programs compile in two steps so the browser can build them in parallel without blocking the page. */
export function beginProgram(gl, vs, fs) {
  const p = gl.createProgram();
  for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s); gl.attachShader(p, s);
  }
  gl.linkProgram(p);
  return p;
}

export function finishProgram(gl, p) {
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
    const logs = (gl.getAttachedShaders(p) || []).map((s) => gl.getShaderInfoLog(s)).filter(Boolean).join(' | ');
    throw new Error(gl.getProgramInfoLog(p) || logs || 'program link failed');
  }
  const u = {};
  for (let i = 0, n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS); i < n; i++) {
    const info = gl.getActiveUniform(p, i);
    u[info.name.replace(/\[0\]$/, '')] = gl.getUniformLocation(p, info.name);
  }
  return { p, u };
}

/* layout: [[location, size, divisor?], ...] read in order from one interleaved float buffer. */
export function bindLayout(gl, buffer, layout) {
  const stride = layout.reduce((n, a) => n + a[1], 0) * 4;
  let offset = 0;
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  for (const [loc, size, divisor = 0] of layout) {
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, size, gl.FLOAT, false, stride, offset);
    gl.vertexAttribDivisor(loc, divisor);
    offset += size * 4;
  }
}

export function staticBuffer(gl, data) {
  const b = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, b);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
  return b;
}

/* Colour target, optionally multisampled with depth. resolve() makes .tex current. */
export function target(gl, w, h, { depth = false, samples = 0 } = {}) {
  const t = { w, h, tex: gl.createTexture(), fbo: gl.createFramebuffer(), draw: null, rbs: [] };
  gl.bindTexture(gl.TEXTURE_2D, t.tex);
  gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, w, h);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.bindFramebuffer(gl.FRAMEBUFFER, t.fbo);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t.tex, 0);
  t.draw = t.fbo;
  if (depth || samples) {
    const store = (format, attach, fbo) => {
      const rb = gl.createRenderbuffer();
      gl.bindRenderbuffer(gl.RENDERBUFFER, rb);
      if (samples) gl.renderbufferStorageMultisample(gl.RENDERBUFFER, samples, format, w, h);
      else gl.renderbufferStorage(gl.RENDERBUFFER, format, w, h);
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
      gl.framebufferRenderbuffer(gl.FRAMEBUFFER, attach, gl.RENDERBUFFER, rb);
      t.rbs.push(rb);
    };
    if (samples) { t.draw = gl.createFramebuffer(); store(gl.RGBA8, gl.COLOR_ATTACHMENT0, t.draw); }
    if (depth) store(gl.DEPTH_COMPONENT24, gl.DEPTH_ATTACHMENT, t.draw);
  }
  t.resolve = () => {
    if (t.draw === t.fbo) return;
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, t.draw);
    gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, t.fbo);
    gl.blitFramebuffer(0, 0, w, h, 0, 0, w, h, gl.COLOR_BUFFER_BIT, gl.NEAREST);
  };
  t.dispose = () => {
    gl.deleteTexture(t.tex); gl.deleteFramebuffer(t.fbo);
    if (t.draw !== t.fbo) gl.deleteFramebuffer(t.draw);
    t.rbs.forEach((rb) => gl.deleteRenderbuffer(rb));
  };
  return t;
}
