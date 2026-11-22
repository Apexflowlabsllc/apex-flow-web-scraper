/* GLSL ES 3.00 programs for the crawl scene. The alpha channel of the scene target is the bloom mask. */
const HEAD = '#version 300 es\nprecision highp float;\n';
const ROT = 'vec3 rot(vec3 v, vec4 q){ return v + 2.0*cross(q.xyz, cross(q.xyz, v) + q.w*v); }\n';
const FOG = 'uniform vec3 uEye; uniform vec3 uFog; uniform vec2 uFogR;\nfloat fogAt(vec3 w){ return smoothstep(uFogR.x, uFogR.y, length(uEye - w)); }\n';

/* Bots, engine and rings share one lit, instanced program. */
export const BOT_VS = HEAD + ROT + `
layout(location=0) in vec3 aPos; layout(location=1) in vec3 aNor; layout(location=2) in vec4 aMat;
layout(location=3) in vec3 iPos; layout(location=4) in vec4 iQuat; layout(location=5) in vec4 iA; layout(location=6) in float iTint;
uniform mat4 uVP;
out vec3 vNor; out vec3 vWorld; out float vMat; out float vGlow; out float vTint;
void main(){
  vec3 p = aPos; float amp = iA.z;
  if (aMat.y >= 0.0) {
    float ph = iA.y + aMat.w*3.14159 + aMat.y*0.55;
    p.x += sin(ph)*0.17*aMat.z*amp;
    p.y += max(0.0, cos(ph))*0.16*aMat.z*amp;
  } else {
    p.y += sin(iA.y*2.0)*0.014*amp;
    p.y -= iA.w*0.1*smoothstep(-0.3, 0.4, p.x)*(p.x + 0.3);
  }
  vec3 w = iPos + rot(p*iA.x, iQuat);
  vWorld = w; vNor = rot(aNor, iQuat); vMat = aMat.x; vGlow = iA.w; vTint = iTint;
  gl_Position = uVP*vec4(w, 1.0);
}`;

export const BOT_FS = HEAD + FOG + `
in vec3 vNor; in vec3 vWorld; in float vMat; in float vGlow; in float vTint;
uniform float uTime;
out vec4 o;
void main(){
  vec3 N = normalize(vNor); vec3 V = normalize(uEye - vWorld);
  if (dot(N, V) < 0.0) N = -N;
  vec3 L = normalize(vec3(-0.42, 0.86, 0.36));
  float nl = max(dot(N, L), 0.0);
  float spec = pow(max(dot(N, normalize(L + V)), 0.0), 46.0);
  float rim = pow(1.0 - max(dot(N, V), 0.0), 2.4);
  float hemi = 0.5 + 0.5*N.y;
  vec3 base = vec3(0.07, 0.09, 0.12); float metal = 0.9; float em = 0.0; vec3 ec = vec3(0.0);
  if (vMat > 3.5) { base = vec3(0.02); ec = mix(vec3(0.1, 0.78, 1.0), vec3(0.72, 1.0, 0.3), clamp(vGlow, 0.0, 1.0)); em = 0.62 + 0.2*sin(uTime*3.0 + vWorld.x*2.0 + vWorld.z) + 0.55*vGlow; metal = 0.2; }
  else if (vMat > 2.5) { base = vec3(0.02); ec = vec3(0.32, 0.93, 1.0); em = 0.95 + 0.6*vGlow; metal = 0.2; }
  else if (vMat > 1.5) { base = vec3(0.028, 0.036, 0.05); metal = 0.45; }
  else if (vMat > 0.5) { base = vec3(0.3, 0.36, 0.43); metal = 1.0; }
  base *= vTint;
  vec3 col = base*(0.2 + 0.4*hemi) + base*nl*vec3(1.0, 0.97, 0.92);
  col += spec*metal*vec3(0.8, 0.94, 1.0)*0.85;
  col += rim*vec3(0.07, 0.5, 0.72)*0.8;
  col += ec*em;
  float f = fogAt(vWorld);
  o = vec4(mix(col, uFog, f*0.9), clamp(em*0.85 + spec*metal*0.3, 0.0, 1.0)*(1.0 - f));
}`;

/* Soft contact shadow under each bot. Blended as a multiply. */
export const SHADOW_VS = HEAD + ROT + `
layout(location=0) in vec2 aCorner;
layout(location=3) in vec3 iPos; layout(location=4) in vec4 iQuat; layout(location=5) in vec4 iA;
uniform mat4 uVP; out vec2 vUv;
void main(){ vUv = aCorner; gl_Position = uVP*vec4(iPos + rot(vec3(aCorner.x*0.98, 0.012, aCorner.y*0.9)*iA.x, iQuat), 1.0); }`;
export const SHADOW_FS = HEAD + `in vec2 vUv; out vec4 o;
void main(){ float a = (1.0 - smoothstep(0.2, 1.0, length(vUv)))*0.5; o = vec4(0.0, 0.0, 0.0, a); }`;

/* Page slabs: the main site and the linked pages. */
export const PAGE_VS = HEAD + ROT + `
layout(location=0) in vec3 aPos; layout(location=1) in vec3 aNor;
uniform mat4 uVP; uniform vec3 uPos; uniform vec4 uQuat; uniform vec3 uSize;
out vec2 vUv; out vec3 vWorld; out vec3 vNor; out float vTop; out float vY;
void main(){
  vec3 w = uPos + rot(aPos*uSize, uQuat);
  vUv = aPos.xz + 0.5; vWorld = w; vNor = rot(aNor, uQuat); vTop = aNor.y; vY = aPos.y;
  gl_Position = uVP*vec4(w, 1.0);
}`;

export const PAGE_FS = HEAD + FOG + `
in vec2 vUv; in vec3 vWorld; in vec3 vNor; in float vTop; in float vY;
uniform sampler2D uTex; uniform sampler2D uTrail;
uniform vec4 uAtlas; uniform float uMain; uniform float uTime; uniform float uScan; uniform vec2 uDim;
uniform vec4 uHi[12]; uniform float uHiA[12]; uniform float uHiC[12];
out vec4 o;
void main(){
  vec3 N = normalize(vNor); vec3 V = normalize(uEye - vWorld);
  float f = fogAt(vWorld); vec3 col; float em = 0.0;
  if (vTop > 0.5) {
    vec3 alb = texture(uTex, vUv*uAtlas.xy + uAtlas.zw, -0.55).rgb;
    float fres = pow(1.0 - max(dot(N, V), 0.0), 3.0);
    col = alb*0.8 + vec3(0.08, 0.4, 0.52)*fres*0.3;
    vec2 e = min(vUv, 1.0 - vUv)*uDim;
    float edge = smoothstep(0.07, 0.0, min(e.x, e.y));
    col = mix(col, vec3(0.2, 0.9, 1.0), edge*0.85); em += edge*0.55;
    if (uMain > 0.5) {
      float heat = clamp(texture(uTrail, vUv).r, 0.0, 1.0);
      col = mix(col, col*vec3(0.66, 0.97, 1.08) + vec3(0.0, 0.07, 0.1), heat*0.42);
      em += heat*0.08;
      float s = vUv.y - uScan;
      float line = smoothstep(0.007, 0.0, abs(s));
      float wake = smoothstep(-0.2, 0.0, s)*step(s, 0.0);
      col += vec3(0.1, 0.78, 1.0)*(wake*0.3 + line*1.2);
      em += line*0.9 + wake*0.06;
      for (int i = 0; i < 12; i++) {
        float a = uHiA[i];
        if (a <= 0.002) continue;
        vec4 r = uHi[i];
        vec2 c = (vUv - r.xy)/r.zw;
        vec2 q = abs(c - 0.5)*2.0;
        float inside = step(max(q.x, q.y), 1.0);
        vec2 d = (1.0 - q)*r.zw*uDim*0.5;
        float outline = inside*smoothstep(0.11, 0.0, min(d.x, d.y));
        float fill = inside*(0.12 + 0.08*sin(uTime*8.0 - c.y*26.0));
        vec3 hc = mix(vec3(0.2, 0.9, 1.0), vec3(0.6, 1.0, 0.36), uHiC[i]);
        col = mix(col, hc, clamp(outline*a, 0.0, 1.0)*0.92) + hc*fill*a*0.55;
        em += (outline*0.85 + fill*0.35)*a;
      }
    }
  } else {
    float lip = pow(smoothstep(-0.55, 0.0, vY), 5.0);
    col = vec3(0.016, 0.045, 0.062) + vec3(0.1, 0.82, 1.0)*lip*0.9;
    em = lip*0.7;
  }
  o = vec4(mix(col, uFog, f*0.92), clamp(em, 0.0, 1.0)*(1.0 - f));
}`;

/* Heat map of where bots have walked on the main page. */
export const TRAIL_VS = HEAD + `layout(location=0) in vec2 aXZ; uniform vec2 uHalf; uniform float uSize;
void main(){ gl_Position = vec4(aXZ/uHalf, 0.0, 1.0); gl_PointSize = uSize; }`;
export const TRAIL_FS = HEAD + `out vec4 o; void main(){ vec2 d = gl_PointCoord - 0.5; o = vec4(smoothstep(0.5, 0.1, length(d))*0.09); }`;

export const QUAD_VS = HEAD + `out vec2 vUv;
void main(){ vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2)); vUv = p; gl_Position = vec4(p*2.0 - 1.0, 0.0, 1.0); }`;
export const FLAT_FS = HEAD + `uniform vec4 uColor; out vec4 o; void main(){ o = uColor; }`;

/* Sky: graphite gradient with slow cyan haze. */
export const SKY_FS = HEAD + `in vec2 vUv; uniform float uTime; uniform float uAspect; out vec4 o;
float h(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7)))*43758.5453); }
float n(vec2 p){ vec2 i = floor(p), f = fract(p); f = f*f*(3.0 - 2.0*f);
  return mix(mix(h(i), h(i + vec2(1, 0)), f.x), mix(h(i + vec2(0, 1)), h(i + vec2(1, 1)), f.x), f.y); }
void main(){
  vec2 p = vec2(vUv.x*uAspect, vUv.y);
  vec3 col = mix(vec3(0.012, 0.03, 0.045), vec3(0.016, 0.065, 0.095), smoothstep(0.0, 1.0, vUv.y));
  float haze = n(p*2.2 + vec2(uTime*0.02, 0.0))*0.6 + n(p*5.0 - vec2(0.0, uTime*0.03))*0.4;
  col += vec3(0.02, 0.2, 0.28)*pow(haze, 2.2)*smoothstep(0.25, 1.0, vUv.y)*0.55;
  float star = step(0.9975, h(floor(p*420.0)))*(0.4 + 0.6*sin(uTime*1.7 + h(floor(p*420.0) + 3.0)*40.0));
  col += vec3(0.55, 0.9, 1.0)*star*0.5*smoothstep(0.35, 0.9, vUv.y);
  o = vec4(col, 0.0);
}`;

/* Floor grid running to the horizon, lit under the page. */
export const FLOOR_VS = HEAD + `layout(location=0) in vec2 aXZ; uniform mat4 uVP; uniform float uY; out vec3 vWorld;
void main(){ vWorld = vec3(aXZ.x, uY, aXZ.y); gl_Position = uVP*vec4(vWorld, 1.0); }`;
export const FLOOR_FS = HEAD + FOG + `in vec3 vWorld; uniform float uTime; out vec4 o;
void main(){
  vec2 g = vWorld.xz/1.6; vec2 fw = fwidth(g);
  vec2 a = abs(fract(g - 0.5) - 0.5)/max(fw, vec2(1e-4));
  float line = (1.0 - min(min(a.x, a.y), 1.0))*clamp(0.5/max(fw.x, fw.y), 0.0, 1.0);
  float under = exp(-dot(vWorld.xz*vec2(0.085, 0.13), vWorld.xz*vec2(0.085, 0.13)));
  float pulse = 0.5 + 0.5*sin(length(vWorld.xz)*0.9 - uTime*1.4);
  vec3 col = vec3(0.012, 0.035, 0.05) + vec3(0.03, 0.34, 0.46)*under*0.6;
  col += vec3(0.08, 0.6, 0.8)*line*(0.16 + 0.34*under + 0.1*pulse);
  float f = fogAt(vWorld);
  o = vec4(mix(col, uFog, f), line*0.1*(1.0 - f));
}`;

/* Link ribbons between pages. Vertex: position xyz, t along the curve, side -1..1, length. */
export const LINK_VS = HEAD + `layout(location=0) in vec3 aPos; layout(location=1) in vec3 aInfo; uniform mat4 uVP;
out vec3 vWorld; out vec3 vInfo;
void main(){ vWorld = aPos; vInfo = aInfo; gl_Position = uVP*vec4(aPos, 1.0); }`;
export const LINK_FS = HEAD + FOG + `in vec3 vWorld; in vec3 vInfo; uniform float uTime; uniform float uAct; out vec4 o;
void main(){
  float edge = smoothstep(0.62, 0.98, abs(vInfo.y));
  float dash = smoothstep(0.8, 1.0, sin(vInfo.x*vInfo.z*5.0 - uTime*6.0))*(1.0 - edge);
  float glow = edge*(0.55 + 0.45*uAct) + dash*(0.25 + 0.6*uAct);
  vec3 col = vec3(0.012, 0.05, 0.07) + vec3(0.1, 0.8, 1.0)*glow;
  float f = fogAt(vWorld);
  o = vec4(mix(col, uFog, f*0.9), clamp(glow*0.7, 0.0, 1.0)*(1.0 - f));
}`;

/* Additive camera-facing sprites: data packets, ports, halos. Instance: position xyz, size, colour rgb, strength. */
export const GLOW_VS = HEAD + `layout(location=0) in vec2 aCorner; layout(location=3) in vec4 iPS; layout(location=4) in vec4 iCol;
uniform mat4 uVP; uniform vec3 uRight; uniform vec3 uUp; out vec2 vUv; out vec4 vCol;
void main(){ vUv = aCorner; vCol = iCol; gl_Position = uVP*vec4(iPS.xyz + (uRight*aCorner.x + uUp*aCorner.y)*iPS.w, 1.0); }`;
export const GLOW_FS = HEAD + `in vec2 vUv; in vec4 vCol; out vec4 o;
void main(){ float d = length(vUv); float a = (exp(-d*d*9.0) + 0.22*exp(-d*d*2.2))*smoothstep(1.0, 0.7, d)*vCol.a; o = vec4(vCol.rgb*a, a*0.8); }`;

/* Post: bright pass, separable blur, final grade. */
export const BRIGHT_FS = HEAD + `in vec2 vUv; uniform sampler2D uTex; out vec4 o;
void main(){ vec4 c = texture(uTex, vUv); o = vec4(c.rgb*c.a*1.35, 1.0); }`;
export const BLUR_FS = HEAD + `in vec2 vUv; uniform sampler2D uTex; uniform vec2 uDir; out vec4 o;
void main(){
  vec3 c = texture(uTex, vUv).rgb*0.2270270;
  c += (texture(uTex, vUv + uDir*1.3846154).rgb + texture(uTex, vUv - uDir*1.3846154).rgb)*0.3162162;
  c += (texture(uTex, vUv + uDir*3.2307692).rgb + texture(uTex, vUv - uDir*3.2307692).rgb)*0.0702703;
  o = vec4(c, 1.0);
}`;
export const FINAL_FS = HEAD + `in vec2 vUv; uniform sampler2D uTex; uniform sampler2D uB1; uniform sampler2D uB2; uniform float uTime; uniform vec2 uRes; out vec4 o;
float h(vec2 p){ return fract(sin(dot(p, vec2(12.9898, 78.233)))*43758.5453); }
void main(){
  vec3 col = texture(uTex, vUv).rgb;
  vec2 q = vUv - 0.5;
  vec3 wide = vec3(texture(uB2, vUv - q*0.012).r, texture(uB2, vUv).g, texture(uB2, vUv + q*0.012).b);
  col += texture(uB1, vUv).rgb*0.55 + wide*0.95;
  col *= 1.0 - dot(q, q)*0.5;
  col = col/(1.0 + max(col - 0.85, 0.0)*0.6);
  col += (h(vUv*uRes + fract(uTime)*91.0) - 0.5)*0.014;
  o = vec4(col, 1.0);
}`;
