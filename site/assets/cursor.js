/* Robot cursor: a small crawler that walks wherever the pointer goes. Its glowing lens is the exact click point.
   Mouse and pen only (touch screens keep their normal behaviour), off for reduced motion, and the normal text
   cursor comes back inside fields so typing is never harder. */
(function () {
  if (!matchMedia('(hover: hover) and (pointer: fine)').matches || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var NS = 'http://www.w3.org/2000/svg', legs = [];
  var el = document.createElement('div');
  el.setAttribute('aria-hidden', 'true');
  el.style.cssText = 'position:fixed;left:0;top:0;width:44px;height:44px;margin:-22px 0 0 -22px;pointer-events:none;z-index:2147483647;opacity:0;transition:opacity .25s;will-change:transform';
  var svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '-22 -22 44 44'); svg.setAttribute('width', '44'); svg.setAttribute('height', '44');
  var mk = function (tag, a) { var n = document.createElementNS(NS, tag); for (var k in a) n.setAttribute(k, a[k]); svg.appendChild(n); return n; };
  /* six jointed legs, drawn under the body; each is a polyline hip-knee-foot */
  for (var i = 0; i < 6; i++) legs.push(mk('polyline', { fill: 'none', stroke: '#cfd8de', 'stroke-width': '1.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  var g = document.createElementNS(NS, 'g'); svg.appendChild(g);
  var part = function (tag, a) { var n = document.createElementNS(NS, tag); for (var k in a) n.setAttribute(k, a[k]); g.appendChild(n); return n; };
  part('ellipse', { cx: '-3', cy: '0', rx: '10', ry: '7', fill: '#e6edf1', stroke: '#2a3d47', 'stroke-width': '1' });   // shell
  part('rect', { x: '-10', y: '-1.2', width: '9', height: '2.4', rx: '1', fill: '#0c1a22' });                                // dark band
  part('circle', { cx: '-6', cy: '-4.2', r: '.9', fill: '#a6f05a' }); part('circle', { cx: '-3', cy: '-4.6', r: '.9', fill: '#a6f05a' });   // green lights
  part('circle', { cx: '0', cy: '0', r: '6', fill: '#0b1a22', stroke: '#9fb3bd', 'stroke-width': '1' });                        // lens hood
  var iris = part('circle', { cx: '0', cy: '0', r: '3.6', fill: 'none', stroke: '#19d9ee', 'stroke-width': '1.6' });
  var pupil = part('circle', { cx: '0', cy: '0', r: '1.4', fill: '#bff8ff' });
  var beam = mk('path', { d: 'M0 0 L18 -7 L18 7 Z', fill: 'url(#apexBeam)', opacity: '0' });
  var defs = document.createElementNS(NS, 'defs');
  defs.innerHTML = '<linearGradient id="apexBeam"><stop offset="0" stop-color="#19d9ee" stop-opacity=".55"/><stop offset="1" stop-color="#19d9ee" stop-opacity="0"/></linearGradient>';
  svg.insertBefore(defs, svg.firstChild);
  el.appendChild(svg);
  svg.style.filter = 'drop-shadow(0 3px 4px rgba(0,0,0,.55)) drop-shadow(0 0 6px rgba(25,217,238,.35))';

  var style = document.createElement('style');
  style.textContent = 'html.robot-cursor,html.robot-cursor *{cursor:none!important}html.robot-cursor input,html.robot-cursor textarea,html.robot-cursor select,html.robot-cursor [contenteditable]{cursor:text!important}';
  document.head.appendChild(style);

  var x = -100, y = -100, px = x, py = y, ang = 0, phase = 0, over = 0, overT = 0, shown = false, raf = 0, still = 0;
  var HIP = [[-6, -4], [-1, -5], [3, -4], [-6, 4], [-1, 5], [3, 4]], OUT = [[-13, -11], [-2, -13], [8, -11], [-13, 11], [-2, 13], [8, 11]];
  function frame() {
    raf = 0;
    var dx = x - px, dy = y - py, d = Math.hypot(dx, dy);
    px = x; py = y;
    if (d > 0.6) { var a = Math.atan2(dy, dx), t = a - ang; t -= Math.round(t / (2 * Math.PI)) * 2 * Math.PI; ang += t * Math.min(1, 0.12 + d / 120); phase += Math.min(d, 40) * 0.32; still = 0; }
    else still++;
    over += (overT - over) * 0.2;
    var amp = Math.min(1, d / 6);
    for (var i = 0; i < 6; i++) {
      var s = Math.sin(phase + (i % 2 ? Math.PI : 0) + (i > 2 ? Math.PI : 0)) * amp, h = HIP[i], o = OUT[i];
      var fx = o[0] + s * 3.2, fy = o[1], kx = (h[0] + fx) / 2 + (i > 2 ? 0 : 0), ky = (h[1] + fy) / 2 + (i > 2 ? 3 : -3) * (1 - 0.4 * Math.abs(s));
      legs[i].setAttribute('points', h[0] + ',' + h[1] + ' ' + kx.toFixed(1) + ',' + ky.toFixed(1) + ' ' + fx.toFixed(1) + ',' + fy.toFixed(1));
    }
    var bob = Math.sin(phase * 2) * 0.6 * amp;
    g.setAttribute('transform', 'translate(0 ' + bob.toFixed(2) + ')');
    iris.setAttribute('r', (3.6 + over * 1.6).toFixed(2));
    pupil.setAttribute('r', (1.4 + over * 0.9 + 0.25 * Math.sin(performance.now() / 260)).toFixed(2));
    beam.setAttribute('opacity', (over * 0.9).toFixed(2));
    el.style.transform = 'translate(' + x + 'px,' + y + 'px) rotate(' + ang + 'rad) scale(' + (1 + over * 0.12).toFixed(3) + ')';
    if (still < 30 || Math.abs(overT - over) > 0.01) raf = requestAnimationFrame(frame);
  }
  var wake = function () { if (!raf) raf = requestAnimationFrame(frame); };
  addEventListener('pointermove', function (e) {
    if (e.pointerType === 'touch') return;
    x = e.clientX; y = e.clientY;
    if (!shown) { shown = true; px = x; py = y; document.documentElement.classList.add('robot-cursor'); el.style.opacity = '1'; }
    var t = e.target.closest ? e.target.closest('a,button,summary,label,[role=tab],[role=radio],input[type=range]') : null;
    overT = t ? 1 : 0;
    var field = e.target.closest && e.target.closest('input:not([type=range]):not([type=checkbox]):not([type=radio]),textarea,select,[contenteditable]');
    el.style.opacity = field ? '0' : '1';
    wake();
  }, { passive: true });
  document.addEventListener('pointerleave', function () { el.style.opacity = '0'; });
  document.addEventListener('pointerenter', function () { if (shown) el.style.opacity = '1'; });
  addEventListener('pointerdown', function () { overT = 1.6; wake(); setTimeout(function () { overT = 0; wake(); }, 160); }, { passive: true });
  (document.body ? Promise.resolve() : new Promise(function (r) { addEventListener('DOMContentLoaded', r); })).then(function () { document.body.appendChild(el); });
})();
