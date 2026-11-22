/* GLSL ES 3.00 programs for the crawl scene. Lighting is physically based and linear; colour targets are
   half-float, with view depth in the alpha channel so the post pass can focus the picture like a lens. */
const HEAD = '#version 300 es\nprecision highp float;\nprecision highp sampler2DShadow;\n';
const VIEW = 'uniform vec3 uEye; uniform vec3 uFwd; uniform vec3 uFog; uniform vec2 uFogR;\n' +
  'float depthOf(vec3 w){ return dot(w - uEye, uFwd); }\n' +
  'vec3 fogged(vec3 c, vec3 w){ float d = length(w - uEye); float f = smoothstep(uFogR.x, uFogR.y, d)*0.92; f = max(f, (1.0 - exp(-max(0.0, d - uFogR.x*0.35)*0.012))*smoothstep(3.0, -1.0, w.y)*0.5); return mix(c, uFog, f); }\n';

/* Studio the robots stand in: a dark room, a large soft light overhead, a cyan strip behind, a cool fill, a bright floor. */
const ENV = `vec3 studio(vec3 d){
  vec3 c = mix(vec3(0.01, 0.018, 0.026), vec3(0.002, 0.004, 0.007), smoothstep(-0.1, 0.9, d.y));
  if (d.y > 0.04) { vec2 p = d.xz/d.y; c += vec3(0.94, 0.98, 1.0)*11.0*smoothstep(0.62, 0.44, abs(p.x + 0.3))*smoothstep(0.4, 0.26, abs(p.y - 0.12)); }   // overhead soft light
  float el = asin(clamp(d.y, -1.0, 1.0));
  c += vec3(0.1, 0.72, 1.0)*7.0*smoothstep(1.0, 0.5, abs(atan(d.x, -d.z)))*smoothstep(0.055, 0.0, abs(el - 0.18));        // cyan strip behind
  c += vec3(0.7, 0.84, 1.0)*1.6*smoothstep(0.45, 0.2, abs(atan(d.z, -d.x)))*smoothstep(0.22, 0.06, abs(el - 0.45));        // cool fill, left
  c += vec3(1.0, 0.74, 0.5)*0.9*smoothstep(0.3, 0.1, abs(atan(-d.z, d.x) + 0.5))*smoothstep(0.14, 0.04, abs(el - 0.3));    // small warm kicker
  c += vec3(0.4, 0.52, 0.6)*0.22*smoothstep(0.0, -0.55, d.y);                                                             // floor
  return c;
}
`;
export const QUAD_VS = HEAD + `out vec2 vUv;
void main(){ vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2)); vUv = p; gl_Position = vec4(p*2.0 - 1.0, 0.0, 1.0); }`;
export const FLAT_FS = HEAD + `uniform vec4 uColor; out vec4 o; void main(){ o = uColor; }`;

/* One cube face of the studio. */
export const ENV_FS = HEAD + ENV + `in vec2 vUv; uniform int uFace; out vec4 o;
void main(){
  vec2 p = vUv*2.0 - 1.0; vec3 d;
  if (uFace == 0) d = vec3(1.0, -p.y, -p.x); else if (uFace == 1) d = vec3(-1.0, -p.y, p.x);
  else if (uFace == 2) d = vec3(p.x, 1.0, p.y); else if (uFace == 3) d = vec3(p.x, -1.0, -p.y);
  else if (uFace == 4) d = vec3(p.x, -p.y, 1.0); else d = vec3(-p.x, -p.y, -1.0);
  o = vec4(studio(normalize(d)), 1.0);
}`;

/* Robot vertex stage, shared by the lit pass and the shadow pass. Each leg has three joints. */
const RIG = `layout(location=0) in vec3 aPos; layout(location=1) in vec3 aNor; layout(location=2) in vec3 aMisc;
layout(location=3) in vec3 iPos; layout(location=4) in vec4 iQuat; layout(location=5) in vec4 iA; layout(location=6) in float iTint;
uniform vec4 uHip[6]; uniform vec3 uJ1[6]; uniform vec3 uJ2[6]; uniform vec3 uAx[6];
vec3 rot(vec3 v, vec4 q){ return v + 2.0*cross(q.xyz, cross(q.xyz, v) + q.w*v); }
vec3 spin(vec3 v, vec3 a, float c, float s){ return v*c + cross(a, v)*s + a*dot(a, v)*(1.0 - c); }
void pose(inout vec3 p, inout vec3 n){
  float amp = iA.z;
  if (aMisc.y > 0.5) {
    float k = aMisc.y - 1.0, leg = floor(k/3.0 + 0.01), seg = k - leg*3.0; int L = int(leg + 0.5);
    float ph = iA.y + mod(leg, 2.0)*3.14159265;
    float swing = sin(ph)*0.3*amp*uHip[L].w, lift = (max(0.0, cos(ph))*0.46 + 0.03*sin(iA.y*0.5 + leg))*amp;
    vec3 ax = uAx[L];
    if (seg > 1.5) { float c = cos(-lift*0.8), s = sin(-lift*0.8); p = uJ2[L] + spin(p - uJ2[L], ax, c, s); n = spin(n, ax, c, s); }
    if (seg > 0.5) { float c = cos(lift), s = sin(lift); p = uJ1[L] + spin(p - uJ1[L], ax, c, s); n = spin(n, ax, c, s); }
    float c = cos(swing), s = sin(swing); vec3 up = vec3(0.0, 1.0, 0.0);
    p = uHip[L].xyz + spin(p - uHip[L].xyz, up, c, s); n = spin(n, up, c, s);
  }
  p.y += sin(iA.y*2.0)*0.01*amp;
}
`;
export const BOT_VS = HEAD + RIG + `uniform mat4 uVP;
out vec3 vNor; out vec3 vWorld; out vec3 vLocal; out vec2 vMat; out float vGlow; out float vTint;
void main(){
  vec3 p = aPos, n = aNor; pose(p, n);
  vec3 w = iPos + rot(p*iA.x, iQuat);
  vWorld = w; vNor = rot(n, iQuat); vLocal = aPos; vMat = aMisc.xz; vGlow = iA.w; vTint = iTint;
  gl_Position = uVP*vec4(w, 1.0);
}`;
export const DEPTH_VS = HEAD + RIG + `uniform mat4 uVP;
void main(){ vec3 p = aPos, n = aNor; pose(p, n); gl_Position = uVP*vec4(iPos + rot(p*iA.x, iQuat), 1.0); }`;
export const DEPTH_FS = HEAD + `void main(){}`;

const SHADOW = `uniform sampler2DShadow uShadow; uniform mat4 uLightVP;
float shadowAt(vec3 w, float ndl){
  vec4 lp = uLightVP*vec4(w, 1.0); vec3 s = lp.xyz/lp.w*0.5 + 0.5;
  if (s.x < 0.0 || s.x > 1.0 || s.y < 0.0 || s.y > 1.0 || s.z > 1.0) return 1.0;
  float bias = 0.0012 + 0.0035*(1.0 - ndl), sum = 0.0, tx = 1.6/float(textureSize(uShadow, 0).x);
  for (int x = -1; x <= 1; x++) for (int y = -1; y <= 1; y++) sum += texture(uShadow, vec3(s.xy + vec2(x, y)*tx, s.z - bias));
  float edge = smoothstep(0.0, 0.04, min(min(s.x, 1.0 - s.x), min(s.y, 1.0 - s.y)));
  return mix(1.0, sum/9.0, edge);
}
`;
const PBR = `uniform samplerCube uEnv; uniform float uEnvMips; uniform vec3 uSun; uniform vec3 uSunCol;
vec2 envBRDF(float rough, float ndv){
  vec4 r = rough*vec4(-1.0, -0.0275, -0.572, 0.022) + vec4(1.0, 0.0425, 1.04, -0.04);
  float a = min(r.x*r.x, exp2(-9.28*ndv))*r.x + r.y; return vec2(-1.04, 1.04)*a + r.zw;
}
vec3 lit(vec3 N, vec3 V, vec3 alb, float metal, float rough, float ao, float sh, float coat){
  float ndv = max(dot(N, V), 1e-3); vec3 F0 = mix(vec3(0.04), alb, metal);
  vec3 H = normalize(uSun + V); float ndl = max(dot(N, uSun), 0.0), ndh = max(dot(N, H), 0.0), vdh = max(dot(V, H), 0.0);
  float a = rough*rough, a2 = a*a, d = ndh*ndh*(a2 - 1.0) + 1.0, D = a2/(3.14159*d*d);
  float k = (rough + 1.0)*(rough + 1.0)/8.0, G = (ndl/(ndl*(1.0 - k) + k))*(ndv/(ndv*(1.0 - k) + k));
  vec3 F = F0 + (1.0 - F0)*pow(1.0 - vdh, 5.0);
  vec3 col = ((1.0 - F)*(1.0 - metal)*alb/3.14159 + min(D*G*F/(4.0*ndl*ndv + 1e-4), vec3(5.0)))*uSunCol*ndl*sh;
  vec3 R = reflect(-V, N);
  vec2 ab = envBRDF(rough, ndv);
  float so = clamp(pow(ndv + ao, exp2(-16.0*rough - 1.0)) - 1.0 + ao, 0.0, 1.0);
  col += textureLod(uEnv, N, uEnvMips - 0.6).rgb*alb*(1.0 - metal)*ao;
  col += textureLod(uEnv, R, rough*(uEnvMips - 1.0)).rgb*(F0*ab.x + ab.y)*so;
  if (coat > 0.0) {   // lacquer over the shell: a second, sharper reflection
    float fc = (0.04 + 0.96*pow(1.0 - ndv, 5.0))*coat;
    float dc = ndh*ndh*(0.0016 - 1.0) + 1.0;
    col = col*(1.0 - fc*0.5) + textureLod(uEnv, R, 0.9).rgb*fc*so + uSunCol*ndl*sh*fc*min(0.0016/(3.14159*dc*dc), 4.0)*0.25;
  }
  return col;
}
`;
export const BOT_FS = HEAD + VIEW + SHADOW + PBR + `
in vec3 vNor; in vec3 vWorld; in vec3 vLocal; in vec2 vMat; in float vGlow; in float vTint;
uniform float uTime; uniform float uMirror; uniform sampler2D uPage; uniform vec2 uPageSize;
out vec4 o;
float h3(vec3 p){ return fract(sin(dot(p, vec3(127.1, 311.7, 74.7)))*43758.5453); }
float n3(vec3 p){ vec3 i = floor(p), f = fract(p); f = f*f*(3.0 - 2.0*f);
  return mix(mix(mix(h3(i), h3(i + vec3(1, 0, 0)), f.x), mix(h3(i + vec3(0, 1, 0)), h3(i + vec3(1, 1, 0)), f.x), f.y),
             mix(mix(h3(i + vec3(0, 0, 1)), h3(i + vec3(1, 0, 1)), f.x), mix(h3(i + vec3(0, 1, 1)), h3(i + vec3(1, 1, 1)), f.x), f.y), f.z); }
void main(){
  vec3 N = normalize(vNor), V = normalize(uEye - vWorld);
  float id = vMat.x, ao = vMat.y, grain = n3(vLocal*70.0), wear = n3(vLocal*9.0);
  vec3 alb = vec3(0.045, 0.05, 0.06), emit = vec3(0.0); float metal = 1.0, rough = 0.4, coat = 0.0;
  if (id < 0.5) {            // shell: pearl ceramic or deep graphite, lacquered
    alb = vTint > 1.0 ? vec3(0.74, 0.77, 0.8) : vec3(0.028, 0.04, 0.058); metal = 0.0; rough = 0.34 + wear*0.1; coat = 1.0;
  } else if (id < 1.5) {     // frame: dark anodised metal with a fine brushed grain
    rough = 0.36 + n3(vLocal*vec3(160.0, 14.0, 160.0))*0.16;
  } else if (id < 2.5) {     // polished steel
    alb = vec3(0.9, 0.92, 0.94); rough = 0.12 + grain*0.07;
  } else if (id < 3.5) {     // rubber
    alb = vec3(0.012); metal = 0.0; rough = 0.88;
  } else if (id < 4.5) {     // lights
    alb = vec3(0.01); metal = 0.0; rough = 0.3; emit = vec3(0.12, 0.82, 1.0)*(3.2 + 2.6*vGlow);
  } else if (id < 5.5) {     // lens glass
    alb = vec3(0.004, 0.008, 0.012); metal = 0.0; rough = 0.05; coat = 1.6;
  } else if (id < 6.5) {     // cyan anodised trim
    alb = vec3(0.01, 0.4, 0.58); rough = 0.26 + grain*0.08;
  } else if (id > 7.5) {     // green side lights
    alb = vec3(0.01); metal = 0.0; rough = 0.3; emit = vec3(0.45, 1.0, 0.12)*(2.4 + 0.5*sin(uTime*2.0 + vWorld.x*3.0));
  } else {                   // status core
    alb = vec3(0.01); metal = 0.0; rough = 0.2; coat = 1.0;
    emit = mix(vec3(0.1, 0.8, 1.0), vec3(0.62, 1.0, 0.26), clamp(vGlow, 0.0, 1.0))*(2.0 + 0.8*sin(uTime*3.0 + vWorld.x*2.0 + vWorld.z) + 1.8*vGlow);
  }
  float ndl = max(dot(N, uSun), 0.0);
  float sh = uMirror > 0.5 ? 1.0 : shadowAt(vWorld + N*0.01, ndl);
  vec3 col = lit(N, V, alb, metal, rough, ao, sh, coat);
  vec2 puv = vWorld.xz/uPageSize + 0.5;      // light coming up off the page below
  float on = step(abs(puv.x - 0.5), 0.5)*step(abs(puv.y - 0.5), 0.5)*exp(-max(vWorld.y, 0.0)*1.6);
  vec3 pg = textureLod(uPage, puv, 5.5).rgb; pg *= pg;
  col += pg*mix(alb, vec3(0.5), metal*0.7)*(0.3 - N.y*0.5)*on*ao*0.9;
  col += emit;
  o = vec4(min(fogged(col, vWorld), vec3(12.0)), uMirror > 0.5 ? 1.0 : depthOf(vWorld));
}`;

/* Soft contact shadow under each robot, on top of the real shadows. */
export const CONTACT_VS = HEAD + `layout(location=0) in vec2 aCorner;
layout(location=3) in vec3 iPos; layout(location=4) in vec4 iQuat; layout(location=5) in vec4 iA;
uniform mat4 uVP; out vec2 vUv;
vec3 rot(vec3 v, vec4 q){ return v + 2.0*cross(q.xyz, cross(q.xyz, v) + q.w*v); }
void main(){ vUv = aCorner; gl_Position = uVP*vec4(iPos + rot(vec3(aCorner.x*0.62, 0.006, aCorner.y*0.5)*iA.x, iQuat), 1.0); }`;
export const CONTACT_FS = HEAD + `in vec2 vUv; out vec4 o;
void main(){ float a = (1.0 - smoothstep(0.15, 1.0, length(vUv)))*0.42; o = vec4(0.0, 0.0, 0.0, a); }`;

/* Page slabs: a lit glass panel showing the site, with reflections of the robots standing on it. */
export const PAGE_VS = HEAD + `layout(location=0) in vec3 aPos; layout(location=1) in vec3 aNor;
uniform mat4 uVP; uniform vec3 uPos; uniform vec4 uQuat; uniform vec3 uSize;
out vec2 vUv; out vec3 vWorld; out vec3 vNor; out float vTop; out float vY;
vec3 rot(vec3 v, vec4 q){ return v + 2.0*cross(q.xyz, cross(q.xyz, v) + q.w*v); }
void main(){
  vec3 w = uPos + rot(aPos*uSize, uQuat);
  vUv = aPos.xz + 0.5; vWorld = w; vNor = rot(aNor, uQuat); vTop = aNor.y; vY = aPos.y;
  gl_Position = uVP*vec4(w, 1.0);
}`;
export const PAGE_FS = HEAD + VIEW + SHADOW + PBR + `
in vec2 vUv; in vec3 vWorld; in vec3 vNor; in float vTop; in float vY;
uniform sampler2D uTex; uniform sampler2D uTrail; uniform sampler2D uRefl;
uniform vec4 uAtlas; uniform float uMain; uniform float uTime; uniform float uScan; uniform vec2 uDim; uniform vec2 uRes; uniform float uReflOn;
uniform vec4 uHi[12]; uniform float uHiA[12]; uniform float uHiC[12]; uniform float uHolo; uniform float uLit[8];
out vec4 o;
void main(){
  vec3 N = normalize(vNor), V = normalize(uEye - vWorld); vec3 col;
  if (uHolo > 0.5) {                                                        // the data grid: a sheet of light, each row lit as its record arrives
    vec3 t = pow(texture(uTex, vUv).rgb, vec3(2.2)); float lit = uLit[int(clamp(floor(vUv.y*8.0), 0.0, 7.0))];
    col = t*vec3(0.5, 0.95, 1.0)*(0.3 + 3.4*lit) + vec3(0.01, 0.07, 0.09);
    o = vec4(fogged(col, vWorld), depthOf(vWorld)); return;
  }
  if (vTop > 0.5) {
    vec3 alb = texture(uTex, vUv*uAtlas.xy + uAtlas.zw, -0.5).rgb; alb = pow(alb, vec3(2.2));
    float ndl = max(dot(N, uSun), 0.0), sh = uMain > 0.5 ? shadowAt(vWorld, ndl) : 1.0;
    vec3 amb = textureLod(uEnv, N, uEnvMips - 0.6).rgb;
    col = alb*(amb*0.3 + uSunCol*ndl*sh*0.19 + 0.1);                        // lit panel with its own backlight
    float ndv = max(dot(N, V), 1e-3), fr = 0.035 + 0.965*pow(1.0 - ndv, 4.0);
    vec3 R = reflect(-V, N);
    vec3 sky = textureLod(uEnv, R, 1.4).rgb;
    if (uMain > 0.5) {
      float heat = clamp(texture(uTrail, vUv).r, 0.0, 1.0);
      col = mix(col, col*vec3(0.62, 0.96, 1.06) + vec3(0.0, 0.05, 0.075), heat*0.4);
      float s = vUv.y - uScan, line = smoothstep(0.006, 0.0, abs(s)), wake = smoothstep(-0.2, 0.0, s)*step(s, 0.0);
      col += vec3(0.1, 0.78, 1.0)*(wake*0.12 + line*3.0);
      for (int i = 0; i < 12; i++) {
        float a = uHiA[i];
        if (a <= 0.002) continue;
        vec4 r = uHi[i]; vec2 c = (vUv - r.xy)/r.zw, q = abs(c - 0.5)*2.0;
        float inside = step(max(q.x, q.y), 1.0); vec2 d = (1.0 - q)*r.zw*uDim*0.5;
        float outline = inside*smoothstep(0.1, 0.0, min(d.x, d.y)), fill = inside*(0.1 + 0.07*sin(uTime*8.0 - c.y*26.0));
        vec3 hc = mix(vec3(0.12, 0.82, 1.0), vec3(0.6, 1.0, 0.3), uHiC[i]);
        col = mix(col, hc*2.2, clamp(outline*a, 0.0, 1.0)*0.9) + hc*fill*a*0.5;
      }
      if (uReflOn > 0.5) { vec4 rf = textureLod(uRefl, gl_FragCoord.xy/uRes, 1.3); float w = clamp(fr*2.4 + 0.1, 0.0, 0.9)*rf.a; col = mix(col, rf.rgb, w); }
    }
    col += sky*fr*0.5;                                                        // the studio lights on the glass
    vec2 e = min(vUv, 1.0 - vUv)*uDim; float edge = smoothstep(0.06, 0.0, min(e.x, e.y));
    col = mix(col, vec3(0.12, 0.82, 1.0)*2.4, edge*0.85);
  } else {                                                                    // the panel's metal body
    col = lit(N, V, vec3(0.04, 0.045, 0.055), 1.0, 0.36, 0.9, 1.0, 0.0);
    col += vec3(0.1, 0.8, 1.0)*pow(smoothstep(-0.5, 0.0, vY), 6.0)*2.2;
  }
  o = vec4(fogged(col, vWorld), depthOf(vWorld));
}`;

/* Heat map of where robots have walked on the main page. */
export const TRAIL_VS = HEAD + `layout(location=0) in vec2 aXZ; uniform vec2 uHalf; uniform float uSize;
void main(){ gl_Position = vec4(aXZ/uHalf, 0.0, 1.0); gl_PointSize = uSize; }`;
export const TRAIL_FS = HEAD + `out vec4 o; void main(){ vec2 d = gl_PointCoord - 0.5; o = vec4(smoothstep(0.5, 0.1, length(d))*0.09); }`;

/* Backdrop: the studio itself, softly. */
export const SKY_FS = HEAD + `in vec2 vUv; uniform mat4 uInv; uniform samplerCube uEnv; uniform vec3 uEye; uniform vec3 uFog; out vec4 o;
void main(){
  vec4 f = uInv*vec4(vUv*2.0 - 1.0, 1.0, 1.0); vec3 d = normalize(f.xyz/f.w - uEye);
  vec3 c = textureLod(uEnv, d, 4.2).rgb*0.22;
  o = vec4(mix(c, uFog, 0.7), 400.0);
}`;

/* Floor: dark polished stone with a faint grid, lit by the page above it. */
export const FLOOR_VS = HEAD + `layout(location=0) in vec2 aXZ; uniform mat4 uVP; uniform float uY; out vec3 vWorld;
void main(){ vWorld = vec3(aXZ.x, uY, aXZ.y); gl_Position = uVP*vec4(vWorld, 1.0); }`;
export const FLOOR_FS = HEAD + VIEW + PBR + `in vec3 vWorld; uniform float uTime; out vec4 o;
void main(){
  vec3 N = vec3(0.0, 1.0, 0.0), V = normalize(uEye - vWorld);
  vec2 g = vWorld.xz/1.6, fw = fwidth(g), a = abs(fract(g - 0.5) - 0.5)/max(fw, vec2(1e-4));
  float line = (1.0 - min(min(a.x, a.y), 1.0))*clamp(0.5/max(fw.x, fw.y), 0.0, 1.0);
  float under = exp(-dot(vWorld.xz*vec2(0.085, 0.13), vWorld.xz*vec2(0.085, 0.13)));
  vec3 col = lit(N, V, vec3(0.012, 0.016, 0.02), 0.0, 0.22, 1.0, 1.0, 0.6)*0.55;
  col += vec3(0.03, 0.3, 0.42)*under*0.45 + vec3(0.06, 0.55, 0.75)*line*(0.1 + 0.4*under);
  o = vec4(fogged(col, vWorld), depthOf(vWorld));
}`;

/* Link ribbons between pages. Vertex: position xyz, then t along the curve, side, length. */
export const LINK_VS = HEAD + `layout(location=0) in vec3 aPos; layout(location=1) in vec3 aInfo; uniform mat4 uVP;
out vec3 vWorld; out vec3 vInfo;
void main(){ vWorld = aPos; vInfo = aInfo; gl_Position = uVP*vec4(aPos, 1.0); }`;
export const LINK_FS = HEAD + VIEW + `in vec3 vWorld; in vec3 vInfo; uniform float uTime; uniform float uAct; out vec4 o;
void main(){
  float edge = smoothstep(0.62, 0.98, abs(vInfo.y));
  float dash = smoothstep(0.8, 1.0, sin(vInfo.x*vInfo.z*5.0 - uTime*6.0))*(1.0 - edge);
  float glow = edge*(0.8 + 1.6*uAct) + dash*(0.4 + 1.8*uAct);
  vec3 col = vec3(0.004, 0.02, 0.03) + vec3(0.1, 0.8, 1.0)*glow*1.6;
  o = vec4(fogged(col, vWorld), depthOf(vWorld));
}`;

/* Additive camera-facing sprites: data packets, link ports, drifting dust. Instance: position xyz, size, colour rgb, strength. */
export const GLOW_VS = HEAD + `layout(location=0) in vec2 aCorner; layout(location=3) in vec4 iPS; layout(location=4) in vec4 iCol;
uniform mat4 uVP; uniform vec3 uRight; uniform vec3 uUp; uniform vec3 uEye; out vec2 vUv; out vec4 vCol;
void main(){
  float near = clamp(length(iPS.xyz - uEye)/9.0, 0.1, 1.0);     // nothing blooms into the lens
  vUv = aCorner; vCol = vec4(iCol.rgb, iCol.a*near);
  gl_Position = uVP*vec4(iPS.xyz + (uRight*aCorner.x + uUp*aCorner.y)*iPS.w*near, 1.0);
}`;
export const GLOW_FS = HEAD + `in vec2 vUv; in vec4 vCol; out vec4 o;
void main(){ float d = length(vUv); float a = (exp(-d*d*9.0) + 0.22*exp(-d*d*2.2))*smoothstep(1.0, 0.7, d)*vCol.a; o = vec4(vCol.rgb*a*3.0, 0.0); }`;

/* Lens: half-size copy with blur size, a disc blur for what is out of focus, bloom, then the final grade. */
const COC = 'uniform float uFocus; uniform float uAperture;\nfloat cocOf(float z){ return clamp(abs(z - uFocus)/max(z, 0.5)*uAperture, 0.0, 1.0); }\n';
export const HALF_FS = HEAD + COC + `in vec2 vUv; uniform sampler2D uTex; out vec4 o;
void main(){ vec4 c = texture(uTex, vUv); o = vec4(min(c.rgb, vec3(24.0)), cocOf(c.a)); }`;
export const DOF_FS = HEAD + `in vec2 vUv; uniform sampler2D uTex; uniform vec2 uPx; uniform float uRadius; out vec4 o;
void main(){
  vec4 c = texture(uTex, vUv); vec3 sum = c.rgb; float n = 1.0, r = max(c.a, 0.02)*uRadius;
  for (int i = 1; i <= 22; i++) {
    float t = float(i), rad = sqrt(t/22.0), an = t*2.39996;
    vec4 s = texture(uTex, vUv + vec2(cos(an), sin(an))*rad*r*uPx);
    float w = smoothstep(0.0, 0.25, s.a - rad*c.a*0.6 + 0.12);       // sharp things do not smear into soft ones
    sum += s.rgb*w; n += w;
  }
  o = vec4(sum/n, c.a);
}`;
export const BRIGHT_FS = HEAD + `in vec2 vUv; uniform sampler2D uTex; out vec4 o;
void main(){ vec3 c = texture(uTex, vUv).rgb; float l = max(c.r, max(c.g, c.b)); o = vec4(c*smoothstep(1.3, 3.2, l), 1.0); }`;
export const BLUR_FS = HEAD + `in vec2 vUv; uniform sampler2D uTex; uniform vec2 uDir; out vec4 o;
void main(){
  vec3 c = texture(uTex, vUv).rgb*0.2270270;
  c += (texture(uTex, vUv + uDir*1.3846154).rgb + texture(uTex, vUv - uDir*1.3846154).rgb)*0.3162162;
  c += (texture(uTex, vUv + uDir*3.2307692).rgb + texture(uTex, vUv - uDir*3.2307692).rgb)*0.0702703;
  o = vec4(c, 1.0);
}`;
export const FINAL_FS = HEAD + COC + `in vec2 vUv; uniform sampler2D uTex; uniform sampler2D uSoft; uniform sampler2D uB1; uniform sampler2D uB2;
uniform float uTime; uniform vec2 uRes; uniform float uDebug; out vec4 o;
float h(vec2 p){ return fract(sin(dot(p, vec2(12.9898, 78.233)))*43758.5453); }
void main(){
  vec4 sharp = texture(uTex, vUv); vec4 soft = texture(uSoft, vUv);
  if (uDebug > 1.5) { o = vec4(vec3(cocOf(sharp.a)), 1.0); return; }
  if (uDebug > 0.5) { vec3 c = sharp.rgb; c = clamp((c*(2.51*c + 0.03))/(c*(2.43*c + 0.59) + 0.14), 0.0, 1.0); o = vec4(pow(c, vec3(1.0/2.2)), 1.0); return; }
  vec3 col = mix(sharp.rgb, soft.rgb, smoothstep(0.04, 0.42, max(cocOf(sharp.a), soft.a*0.85)));
  vec2 q = vUv - 0.5;
  vec3 wide = vec3(texture(uB2, vUv - q*0.014).r, texture(uB2, vUv).g, texture(uB2, vUv + q*0.014).b);
  col += texture(uB1, vUv).rgb*0.16 + wide*0.26;
  col *= 1.0 - dot(q, q)*0.62;
  col = clamp((col*(2.51*col + 0.03))/(col*(2.43*col + 0.59) + 0.14), 0.0, 1.0);      // filmic curve
  col = pow(col, vec3(1.0/2.2));
  col += (h(vUv*uRes + fract(uTime)*91.0) - 0.5)*0.018;
  o = vec4(col, 1.0);
}`;

/* Data streams: light running along a fibre from a page element to its row in the grid. */
export const STREAM_FS = HEAD + VIEW + `in vec3 vWorld; in vec3 vInfo; uniform float uTime; uniform float uPulse[6]; out vec4 o;
void main(){
  float p = uPulse[int(vInfo.z + 0.5)], core = smoothstep(1.0, 0.15, abs(vInfo.y));
  float dots = smoothstep(0.5, 1.0, sin(vInfo.x*46.0 - uTime*(5.0 + 7.0*p)));
  o = vec4(fogged(vec3(0.1, 0.82, 1.0)*core*(1.1 + dots*2.4 + 4.0*p), vWorld), 0.0);
}`;
/* Scan light: a fan of cyan on the page in front of a robot that is reading. */
export const BEAM_VS = HEAD + `layout(location=0) in vec2 aCorner;
layout(location=3) in vec3 iPos; layout(location=4) in vec4 iQuat; layout(location=5) in vec4 iA;
uniform mat4 uVP; out vec2 vUv; out float vGlow;
vec3 rot(vec3 v, vec4 q){ return v + 2.0*cross(q.xyz, cross(q.xyz, v) + q.w*v); }
void main(){
  float t = aCorner.x*0.5 + 0.5; vUv = vec2(t, aCorner.y); vGlow = iA.w;
  vec3 p = vec3(0.42 + t*1.5, 0.014, aCorner.y*(0.1 + 0.62*t))*iA.x*step(0.06, iA.w);
  gl_Position = uVP*vec4(iPos + rot(p, iQuat), 1.0);
}`;
export const BEAM_FS = HEAD + `in vec2 vUv; in float vGlow; uniform float uTime; out vec4 o;
void main(){
  float a = (1.0 - vUv.x)*(1.0 - smoothstep(0.45, 1.0, abs(vUv.y)))*vGlow;
  a += smoothstep(0.07, 0.0, abs(vUv.x - fract(uTime*1.4)))*(1.0 - abs(vUv.y))*vGlow*0.9;
  o = vec4(vec3(0.1, 0.85, 1.0)*a*1.5, 0.0);
}`;
