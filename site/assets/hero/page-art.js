/* Draws the illustrative web pages the robots crawl: silver glass pages in greys with cyan marks, plus the data grid
   the records flow into. Everything is drawn in code, so it stays sharp at any texture size. Logical page: 1600 x 1000. */

export const RECORDS = ['Headphones', 'Laptop', 'Camera', 'Watch', 'Mountain guide', 'Price table'];
const CY = '#19d9ee', INK = '#1a222a', LINE = '#8a949e', SOFT = '#b9c1c9', CARD = '#eef1f4', PAGE = '#dfe4e9';

function rr(c, x, y, w, h, r) {
  c.beginPath();
  c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
}
const fill = (c, col, x, y, w, h, r = 6) => { c.fillStyle = col; rr(c, x, y, w, h, r); c.fill(); };
/* Lines of text, drawn as bars. */
function bars(c, x, y, w, n, gap = 22, col = LINE, h = 9) {
  for (let i = 0; i < n; i++) fill(c, col, x, y + i * gap, w * (i === n - 1 ? 0.62 : 1 - ((i * 37) % 23) / 100), h, h / 2);
}
/* A mountain photograph in greys. */
function photo(c, x, y, w, h, seed, r = 10) {
  c.save(); rr(c, x, y, w, h, r); c.clip();
  const sky = c.createLinearGradient(0, y, 0, y + h); sky.addColorStop(0, '#5d6a76'); sky.addColorStop(1, '#c4ccd3');
  c.fillStyle = sky; c.fillRect(x, y, w, h);
  const ridge = (base, amp, col, s) => {
    c.fillStyle = col; c.beginPath(); c.moveTo(x, y + h);
    for (let i = 0; i <= 28; i++) { const t = i / 28; c.lineTo(x + w * t, y + h * (base - amp * (0.6 * Math.abs(Math.sin(t * 4.6 + s)) + 0.4 * Math.abs(Math.sin(t * 10.7 + s * 2.1))))); }
    c.lineTo(x + w, y + h); c.closePath(); c.fill();
  };
  ridge(0.7, 0.42, '#eef2f5', seed); ridge(0.74, 0.34, '#8f9ba6', seed + 0.5); ridge(0.86, 0.3, '#4b5661', seed + 2); ridge(0.98, 0.2, '#262e36', seed + 4);
  c.restore();
}
function icon(c, kind, cx, cy, s) {
  c.save(); c.translate(cx, cy); c.scale(s, s);
  c.strokeStyle = '#d9dfe5'; c.fillStyle = '#d9dfe5'; c.lineWidth = 7; c.lineCap = 'round'; c.lineJoin = 'round';
  if (kind === 0) { c.beginPath(); c.arc(0, 2, 30, Math.PI, 0); c.stroke(); rr(c, -40, 0, 20, 38, 8); c.fill(); rr(c, 20, 0, 20, 38, 8); c.fill(); }              // headphones
  else if (kind === 1) { rr(c, -34, -28, 68, 44, 5); c.stroke(); rr(c, -46, 22, 92, 9, 4); c.fill(); }                                                                // laptop
  else if (kind === 2) { rr(c, -40, -22, 80, 52, 9); c.fill(); rr(c, -16, -34, 32, 14, 4); c.fill(); c.fillStyle = '#2b333b'; c.beginPath(); c.arc(0, 5, 17, 0, 7); c.fill(); c.strokeStyle = '#d9dfe5'; c.lineWidth = 4; c.beginPath(); c.arc(0, 5, 9, 0, 7); c.stroke(); }   // camera
  else { rr(c, -13, -44, 26, 88, 9); c.fill(); c.fillStyle = '#2b333b'; rr(c, -22, -24, 44, 48, 12); c.fill(); c.strokeStyle = '#d9dfe5'; c.lineWidth = 5; rr(c, -22, -24, 44, 48, 12); c.stroke(); }   // watch
  c.restore();
}
/* Link badge: where a data stream leaves the page. */
function badge(c, x, y) {
  fill(c, '#0c1a22', x - 22, y - 13, 44, 26, 8); c.strokeStyle = CY; c.lineWidth = 2.5; rr(c, x - 22, y - 13, 44, 26, 8); c.stroke();
  c.lineWidth = 3; rr(c, x - 12, y - 5, 13, 10, 5); c.stroke(); rr(c, x - 1, y - 5, 13, 10, 5); c.stroke();
}
function chrome(c, w, url) {
  c.fillStyle = '#141b22'; c.fillRect(0, 0, w, 58);
  [['#a6e22e', 28], ['#a6e22e', 52], [CY, 76]].forEach(([col, x]) => { c.fillStyle = col; c.beginPath(); c.arc(x, 29, 7.5, 0, 7); c.fill(); });
  fill(c, '#0a1116', 126, 14, w - 300, 30, 15); fill(c, '#3b4853', 150, 25, url, 8, 4);
}

/* Main page. Returns content boxes in page fractions: {kind, x, y, w, h, record?}; a record also has the badge position bx, by. */
export function paintSite(canvas) {
  const W = canvas.width, H = canvas.height, c = canvas.getContext('2d');
  const els = [];
  const el = (kind, x, y, w, h, record = -1, bx = 0, by = 0) => els.push({ kind, x: x / 1600, y: y / 1000, w: w / 1600, h: h / 1000, record, bx: bx / 1600, by: by / 1000 });
  c.setTransform(W / 1600, 0, 0, H / 1000, 0, 0);
  c.fillStyle = PAGE; c.fillRect(0, 0, 1600, 1000);
  chrome(c, 1600, 300); el('link', 126, 14, 1300, 30);

  // side navigation
  c.fillStyle = INK; c.fillRect(0, 58, 250, 942);
  for (let i = 0; i < 7; i++) { const y = 110 + i * 46; if (i === 2) fill(c, CY, 28, y - 6, 150, 22, 11); else fill(c, '#56626d', 40, y, 120 - (i * 29) % 50, 9, 4); el('link', 28, y - 10, 170, 30); }
  c.strokeStyle = CY; c.lineWidth = 3; c.beginPath(); c.moveTo(70, 500); c.lineTo(70, 640); c.moveTo(70, 560); c.lineTo(150, 600); c.lineTo(150, 700); c.stroke();
  [[70, 500], [70, 640], [150, 600], [150, 700], [70, 560]].forEach(([x, y]) => { c.fillStyle = CY; c.beginPath(); c.arc(x, y, 9, 0, 7); c.fill(); });
  el('link', 40, 480, 150, 240);

  // lead story: photograph, text, small photograph
  photo(c, 280, 84, 420, 250, 1.2); el('record', 280, 84, 420, 250, 4, 690, 100);
  bars(c, 730, 100, 520, 4); fill(c, CY, 730, 196, 190, 16, 8); bars(c, 730, 232, 520, 4);
  el('title', 730, 92, 520, 34); el('text', 730, 130, 520, 60); el('link', 730, 190, 200, 28); el('text', 730, 226, 520, 90);
  photo(c, 1290, 84, 280, 170, 3.4); el('image', 1290, 84, 280, 170); bars(c, 1290, 272, 280, 3, 20);

  // table
  bars(c, 280, 372, 300, 7, 26); el('text', 280, 364, 300, 190);
  fill(c, CARD, 610, 360, 960, 200, 10);
  for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) {
    const x = 634 + k * 236, y = 384 + r * 44, on = (r * 3 + k * 5) % 4 === 1;
    fill(c, r === 0 ? '#5a6672' : on ? CY : SOFT, x, y, 190 - ((r + k) * 31) % 60, 14, 7);
  }
  for (let r = 1; r < 4; r++) { c.fillStyle = '#cfd6dc'; c.fillRect(626, 372 + r * 44, 928, 2); }
  el('record', 610, 360, 960, 200, 5, 1552, 376);

  // products
  for (let i = 0; i < 4; i++) {
    const x = 280 + i * 326;
    fill(c, '#2b333b', x, 596, 300, 200, 12); icon(c, i, x + 150, 694, 1.35);
    fill(c, LINE, x + 6, 816, 190, 11, 5); fill(c, CY, x + 6, 842, 120, 16, 8);
    el('record', x, 596, 300, 270, i, x + 280, 612);
  }
  bars(c, 280, 900, 1290, 3, 24, SOFT); el('text', 280, 892, 1290, 70);
  els.forEach((e) => { if (e.record >= 0) badge(c, e.bx * 1600, e.by * 1000); });
  return els;
}

/* Four linked pages in one 2 x 2 atlas. */
export function paintAtlas(canvas) {
  const c = canvas.getContext('2d'), w = canvas.width / 2, h = canvas.height / 2;
  for (let i = 0; i < 4; i++) {
    c.setTransform(w / 800, 0, 0, h / 500, (i % 2) * w, Math.floor(i / 2) * h);
    c.fillStyle = i % 2 ? '#d5dbe1' : PAGE; c.fillRect(0, 0, 800, 500);
    chrome(c, 800, 160 + i * 40);
    if (i === 2) { fill(c, CARD, 30, 86, 740, 380, 10); for (let r = 0; r < 7; r++) for (let k = 0; k < 3; k++) fill(c, r === 0 ? '#5a6672' : (r + k) % 3 === 1 ? CY : SOFT, 56 + k * 240, 112 + r * 48, 170 - ((r + k) * 37) % 50, 14, 7); }
    else {
      photo(c, 30, 86, i === 1 ? 300 : 360, 230, i * 1.7 + 0.4);
      bars(c, i === 1 ? 360 : 420, 96, i === 1 ? 400 : 340, 6, 26); fill(c, CY, i === 1 ? 360 : 420, 262, 150, 16, 8);
      if (i === 1) { fill(c, '#2b333b', 30, 340, 200, 130, 10); icon(c, 2, 130, 405, 0.9); bars(c, 260, 350, 500, 4, 26); } else bars(c, 30, 344, 740, 5, 26);
    }
  }
}

/* The structured data grid: a picture column and three value columns, one row per record slot. */
export function paintData(canvas) {
  const c = canvas.getContext('2d'), W = canvas.width, H = canvas.height, rows = 8;
  c.setTransform(W / 600, 0, 0, H / 800, 0, 0);
  c.fillStyle = '#04141b'; c.fillRect(0, 0, 600, 800);
  for (let r = 0; r < rows; r++) {
    const y = r * 100;
    fill(c, '#0b2a36', 14, y + 12, 104, 76, 8);
    if (r % 4 === 0 || r % 4 === 3) photo(c, 20, y + 18, 92, 64, r * 1.3, 6); else icon(c, r % 4, 66, y + 50, 0.72);
    for (let k = 0; k < 3; k++) fill(c, '#7df0ff', 150 + k * 150, y + 40, 110 - ((r * 3 + k * 7) % 5) * 9, 20, 10);
  }
  c.strokeStyle = '#2bd6ee'; c.lineWidth = 3;
  for (let r = 1; r < rows; r++) { c.beginPath(); c.moveTo(6, r * 100); c.lineTo(594, r * 100); c.stroke(); }
  for (const x of [132, 282, 432]) { c.beginPath(); c.moveTo(x, 6); c.lineTo(x, 794); c.stroke(); }
  c.lineWidth = 6; rr(c, 4, 4, 592, 792, 14); c.stroke();
}
