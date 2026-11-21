/* Lightweight, self-contained 3D crawler swarm for the homepage hero. */
(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const stage = document.querySelector('.crawl-theatre');
  const canvas = document.getElementById('crawl-swarm');
  if (!stage || !canvas) return;

  const gl = canvas.getContext('webgl', { alpha: true, antialias: true, premultipliedAlpha: false });
  if (!gl) return;

  const vertexShader = `
    attribute vec3 aPosition;
    attribute vec3 aNormal;
    attribute vec4 aColor;
    attribute float aLeg;
    attribute float aStride;
    attribute float aShade;
    uniform vec2 uViewport;
    uniform vec3 uCenter;
    uniform float uScale;
    uniform float uYaw;
    uniform float uPhase;
    uniform float uTime;
    uniform float uShadow;
    varying vec3 vNormal;
    varying vec4 vColor;
    varying float vShade;
    void main() {
      vec3 p = aPosition;
      if (aLeg >= 0.0) {
        float legPhase = uTime * 6.4 + aLeg * 1.63 + uPhase;
        p.x += sin(legPhase) * 0.11 * aStride;
        p.y += cos(legPhase) * 0.075 * aStride;
        p.z += max(0.0, sin(legPhase + 1.2)) * 0.16 * aStride;
      } else {
        p.z += sin(uTime * 4.1 + uPhase) * 0.018;
      }
      float c = cos(uYaw), s = sin(uYaw);
      vec2 page = vec2(c * p.x - s * p.y, s * p.x + c * p.y) * uScale;
      float height = p.z * uScale * 0.72;
      float depth = uCenter.z + height;
      float perspective = 1400.0 / max(850.0, 1400.0 - depth);
      vec2 screen = uCenter.xy + vec2(page.x, -page.y - height) * perspective;
      gl_Position = vec4(screen.x / uViewport.x * 2.0 - 1.0, 1.0 - screen.y / uViewport.y * 2.0, 0.0, 1.0);
      gl_PointSize = 1.0;
      vNormal = vec3(c * aNormal.x - s * aNormal.y, s * aNormal.x + c * aNormal.y, aNormal.z);
      vColor = aColor;
      vShade = max(aShade, uShadow);
    }
  `;
  const fragmentShader = `
    precision mediump float;
    uniform float uTime;
    varying vec3 vNormal;
    varying vec4 vColor;
    varying float vShade;
    void main() {
      if (vShade > 0.5) {
        gl_FragColor = vec4(0.005, 0.025, 0.035, vColor.a);
        return;
      }
      vec3 n = normalize(vNormal);
      vec3 light = normalize(vec3(-0.42, 0.62, 1.0));
      float diffuse = max(dot(n, light), 0.0);
      float rim = pow(1.0 - max(dot(n, vec3(0.0, 0.0, 1.0)), 0.0), 2.0);
      vec3 metal = vColor.rgb * (0.34 + diffuse * 0.72) + vec3(0.11, 0.7, 0.82) * rim * 0.24;
      float pulse = 0.82 + 0.18 * sin(uTime * 5.0);
      gl_FragColor = vec4(metal + vColor.rgb * vColor.a * pulse, 1.0);
    }
  `;

  function compile(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    return shader;
  }
  let program;
  try {
    program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, vertexShader));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fragmentShader));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
  } catch (_) {
    canvas.hidden = true;
    return;
  }
  gl.useProgram(program);

  const loc = (name) => gl.getAttribLocation(program, name);
  const attr = { position: loc('aPosition'), normal: loc('aNormal'), color: loc('aColor'), leg: loc('aLeg'), stride: loc('aStride'), shade: loc('aShade') };
  const uni = {};
  ['uViewport', 'uCenter', 'uScale', 'uYaw', 'uPhase', 'uTime', 'uShadow'].forEach((name) => { uni[name] = gl.getUniformLocation(program, name); });
  const vertices = [];
  const vtx = (p, n, color, leg = -1, stride = 0, shade = 0) => vertices.push(...p, ...n, ...color, leg, stride, shade);
  const colors = {
    dark: [0.035, 0.085, 0.12, 0], steel: [0.34, 0.48, 0.57, 0], light: [0.72, 0.86, 0.9, 0], joint: [0.09, 0.22, 0.29, 0], cyan: [0.07, 0.78, 0.95, 1], lime: [0.55, 0.95, 0.33, 0.34]
  };
  function sphere(center, radius, color, leg = -1, stride = 0, rings = 5, sides = 8) {
    const point = (u, v) => {
      const theta = u * Math.PI, phi = v * Math.PI * 2;
      const n = [Math.sin(theta) * Math.cos(phi), Math.sin(theta) * Math.sin(phi), Math.cos(theta)];
      return { p: [center[0] + n[0] * radius[0], center[1] + n[1] * radius[1], center[2] + n[2] * radius[2]], n };
    };
    for (let r = 0; r < rings; r++) for (let s = 0; s < sides; s++) {
      const a = point(r / rings, s / sides), b = point((r + 1) / rings, s / sides), c = point((r + 1) / rings, (s + 1) / sides), d = point(r / rings, (s + 1) / sides);
      vtx(a.p, a.n, color, leg, stride); vtx(b.p, b.n, color, leg, stride); vtx(c.p, c.n, color, leg, stride);
      vtx(a.p, a.n, color, leg, stride); vtx(c.p, c.n, color, leg, stride); vtx(d.p, d.n, color, leg, stride);
    }
  }
  function segment(a, b, radius, color, leg, startWeight, endWeight) {
    const axis = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    const len = Math.hypot(...axis); axis.forEach((_, i) => axis[i] /= len);
    let ref = Math.abs(axis[2]) < 0.88 ? [0, 0, 1] : [0, 1, 0];
    let u = [axis[1] * ref[2] - axis[2] * ref[1], axis[2] * ref[0] - axis[0] * ref[2], axis[0] * ref[1] - axis[1] * ref[0]];
    let ul = Math.hypot(...u); u = u.map((v) => v / ul);
    const w = [axis[1] * u[2] - axis[2] * u[1], axis[2] * u[0] - axis[0] * u[2], axis[0] * u[1] - axis[1] * u[0]];
    const point = (t, ang) => {
      const cs = Math.cos(ang), sn = Math.sin(ang), n = u.map((v, i) => v * cs + w[i] * sn);
      const r = radius * (1 - 0.23 * t), center = a.map((v, i) => v + axis[i] * len * t);
      return { p: center.map((v, i) => v + n[i] * r), n, weight: startWeight + (endWeight - startWeight) * t };
    };
    const sides = 5;
    for (let i = 0; i < sides; i++) {
      const t0 = i / sides, t1 = (i + 1) / sides;
      const a0 = point(t0, 0), a1 = point(t1, 0), b1 = point(t1, Math.PI * 2 / sides), b0 = point(t0, Math.PI * 2 / sides);
      vtx(a0.p, a0.n, color, leg, a0.weight); vtx(a1.p, a1.n, color, leg, a1.weight); vtx(b1.p, b1.n, color, leg, b1.weight);
      vtx(a0.p, a0.n, color, leg, a0.weight); vtx(b1.p, b1.n, color, leg, b1.weight); vtx(b0.p, b0.n, color, leg, b0.weight);
    }
  }
  function box(center, size, color) {
    const [x, y, z] = center, [sx, sy, sz] = size;
    const faces = [
      [[1,0,0],[[sx, -sy,-sz],[sx,sy,-sz],[sx,sy,sz],[sx,-sy,sz]]], [[-1,0,0],[[-sx,-sy,sz],[-sx,sy,sz],[-sx,sy,-sz],[-sx,-sy,-sz]]],
      [[0,1,0],[[-sx,sy,-sz],[-sx,sy,sz],[sx,sy,sz],[sx,sy,-sz]]], [[0,-1,0],[[-sx,-sy,sz],[-sx,-sy,-sz],[sx,-sy,-sz],[sx,-sy,sz]]],
      [[0,0,1],[[-sx,-sy,sz],[sx,-sy,sz],[sx,sy,sz],[-sx,sy,sz]]], [[0,0,-1],[[sx,-sy,-sz],[-sx,-sy,-sz],[-sx,sy,-sz],[sx,sy,-sz]]]
    ];
    for (const [n, q] of faces) for (const i of [0,1,2,0,2,3]) vtx([x+q[i][0],y+q[i][1],z+q[i][2]], n, color);
  }

  // One finely shaded mechanical spider, instanced at many positions on the page plane.
  sphere([0, 0, 0.23], [0.47, 0.34, 0.24], colors.steel, -1, 0, 6, 10);
  box([-0.02, 0, 0.27], [0.32, 0.27, 0.15], colors.dark);
  sphere([0.3, 0, 0.32], [0.2, 0.19, 0.16], colors.dark, -1, 0, 5, 8);
  sphere([0.43, -0.015, 0.34], [0.07, 0.105, 0.09], colors.cyan, -1, 0, 5, 8);
  sphere([-0.25, 0.03, 0.44], [0.12, 0.105, 0.035], colors.lime, -1, 0, 4, 7);
  for (let side = -1; side <= 1; side += 2) for (let row = 0; row < 4; row++) {
    const id = (side < 0 ? 0 : 4) + row;
    const x = -0.28 + row * 0.19, y = side * 0.2, hip = [x, y, 0.19];
    const knee = [x + (row < 2 ? 0.16 : -0.16), side * 0.48, 0.12];
    const foot = [knee[0] + (row < 2 ? 0.1 : -0.1), side * 0.73, 0.035];
    segment(hip, knee, 0.065, colors.dark, id, 0.16, 0.68);
    sphere(knee, [0.075, 0.075, 0.07], colors.light, id, 0.72, 4, 6);
    segment(knee, foot, 0.042, colors.light, id, 0.7, 1);
    sphere(foot, [0.075, 0.075, 0.04], colors.joint, id, 1, 4, 6);
  }

  const stride = 13;
  const mesh = new Float32Array(vertices);
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, mesh, gl.STATIC_DRAW);
  const offsets = { position: 0, normal: 3, color: 6, leg: 10, stride: 11, shade: 12 };
  const sizes = { position: 3, normal: 3, color: 4, leg: 1, stride: 1, shade: 1 };
  Object.keys(attr).forEach((key) => { gl.enableVertexAttribArray(attr[key]); gl.vertexAttribPointer(attr[key], sizes[key], gl.FLOAT, false, stride * 4, offsets[key] * 4); });
  const vertexCount = mesh.length / stride;

  const shadow = [];
  for (let i = 0; i < 18; i++) {
    const a = i / 18 * Math.PI * 2, b = (i + 1) / 18 * Math.PI * 2;
    const points = [[0,0,0.008,0.22],[Math.cos(a)*0.78,Math.sin(a)*0.52,0.008,0],[Math.cos(b)*0.78,Math.sin(b)*0.52,0.008,0]];
    for (const p of points) shadow.push(p[0],p[1],p[2],0,0,1,0.01,0.025,0.035,p[3],-1,0,1);
  }
  const shadowBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, shadowBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(shadow), gl.STATIC_DRAW);
  const shadowCount = shadow.length / stride;

  const paths = [
    [[0.07,0.13],[0.24,0.1],[0.51,0.25],[0.77,0.32]],
    [[0.08,0.31],[0.3,0.2],[0.48,0.45],[0.78,0.51]],
    [[0.05,0.52],[0.31,0.39],[0.49,0.58],[0.75,0.72]],
    [[0.2,0.69],[0.41,0.6],[0.58,0.83],[0.83,0.9]],
    [[0.62,0.27],[0.75,0.22],[0.9,0.42],[0.95,0.51]],
    [[0.53,0.39],[0.71,0.33],[0.86,0.59],[0.94,0.68]]
  ];
  let width = 0, height = 0, visible = true, raf = 0, previous = 0, pointer = null;
  const bots = [];
  function resize() {
    const rect = stage.getBoundingClientRect(); width = rect.width; height = rect.height;
    const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    canvas.width = Math.max(1, Math.round(width * dpr)); canvas.height = Math.max(1, Math.round(height * dpr));
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.uniform2f(uni.uViewport, canvas.width / dpr, canvas.height / dpr);
    bots.length = 0;
    const count = Math.min(34, Math.max(19, Math.floor(width / 20)));
    for (let i = 0; i < count; i++) bots.push({ path: i % paths.length, t: (i / count + Math.random() * 0.09) % 1, speed: 0.035 + Math.random() * 0.018, scale: 0.62 + Math.random() * 0.58, depth: Math.random() * 105, phase: Math.random() * 6.28 });
  }
  function curve(route, t) {
    const [p0,p1,p2,p3] = route, m = 1-t;
    const x = m*m*m*p0[0]+3*m*m*t*p1[0]+3*m*t*t*p2[0]+t*t*t*p3[0];
    const y = m*m*m*p0[1]+3*m*m*t*p1[1]+3*m*t*t*p2[1]+t*t*t*p3[1];
    const dx = 3*m*m*(p1[0]-p0[0])+6*m*t*(p2[0]-p1[0])+3*t*t*(p3[0]-p2[0]);
    const dy = 3*m*m*(p1[1]-p0[1])+6*m*t*(p2[1]-p1[1])+3*t*t*(p3[1]-p2[1]);
    return [x,y,Math.atan2(-dy,dx)];
  }
  function sample(bot) {
    const point = curve(paths[bot.path], bot.t);
    if (pointer) {
      const dx = pointer[0] - point[0], dy = pointer[1] - point[1], distance = Math.hypot(dx, dy);
      if (distance < 0.24) {
        const pull = (0.24 - distance) * 0.9;
        point[0] += dx * pull; point[1] += dy * pull;
      }
    }
    return point;
  }
  function bind(bufferId) {
    gl.bindBuffer(gl.ARRAY_BUFFER, bufferId);
    Object.keys(attr).forEach((key) => gl.vertexAttribPointer(attr[key], sizes[key], gl.FLOAT, false, stride * 4, offsets[key] * 4));
  }
  function frame(now) {
    if (!visible || document.hidden || !width || !height) return;
    const time = now * 0.001;
    const dt = previous ? Math.min(0.05, (now - previous) / 1000) : 0;
    previous = now;
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.uniform1f(uni.uTime, time);
    // Soft contact shadows sit on the imagined glass; the bodies are true shaded 3D meshes.
    bind(shadowBuffer); gl.uniform1f(uni.uShadow, 1);
    for (const bot of bots) {
      const [x,y,yaw] = sample(bot);
      gl.uniform3f(uni.uCenter, x * width, y * height + 8, bot.depth);
      gl.uniform1f(uni.uScale, 10 + bot.scale * 7);
      gl.uniform1f(uni.uYaw, yaw); gl.uniform1f(uni.uPhase, bot.phase);
      gl.drawArrays(gl.TRIANGLES, 0, shadowCount);
    }
    bind(buffer); gl.uniform1f(uni.uShadow, 0);
    for (const bot of bots) {
      bot.t = (bot.t + bot.speed * dt) % 1;
      const [x,y,yaw] = sample(bot);
      gl.uniform3f(uni.uCenter, x * width, y * height, bot.depth);
      gl.uniform1f(uni.uScale, 10 + bot.scale * 7);
      gl.uniform1f(uni.uYaw, yaw); gl.uniform1f(uni.uPhase, bot.phase);
      gl.drawArrays(gl.TRIANGLES, 0, vertexCount);
    }
    raf = requestAnimationFrame(frame);
  }
  resize();
  addEventListener('resize', resize, { passive: true });
  stage.addEventListener('pointermove', (event) => {
    const rect = stage.getBoundingClientRect();
    pointer = [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height];
  }, { passive: true });
  stage.addEventListener('pointerleave', () => { pointer = null; }, { passive: true });
  document.addEventListener('visibilitychange', () => { if (document.hidden) cancelAnimationFrame(raf); else if (visible) { previous = 0; raf = requestAnimationFrame(frame); } });
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver((entries) => {
      visible = entries[0].isIntersecting;
      if (visible && !document.hidden) raf = requestAnimationFrame(frame); else cancelAnimationFrame(raf);
    }, { threshold: 0 });
    observer.observe(stage);
  } else raf = requestAnimationFrame(frame);
})();
