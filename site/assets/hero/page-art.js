/* Draws the illustrative website the crawlers walk on, and reports where its content is.
   Everything is drawn in code, so it stays sharp at any texture size. Logical page: 1600 x 1000. */

export const RECORDS = [
  { title: 'Headphones', price: '$129.00', stock: 'Yes', url: '/products/1' },
  { title: 'Chair', price: '$249.00', stock: 'Yes', url: '/products/2' },
  { title: 'Backpack', price: '$99.00', stock: 'Yes', url: '/products/3' },
  { title: 'Tokyo', price: '$799.00', stock: 'Yes', url: '/travel/tokyo' },
  { title: 'Paris', price: '$699.00', stock: 'Yes', url: '/travel/paris' },
  { title: 'Rome', price: '$649.00', stock: 'Low', url: '/travel/rome' }
];

const SANS = '"Segoe UI",system-ui,-apple-system,Roboto,Helvetica,Arial,sans-serif';
const SERIF = 'Georgia,"Times New Roman",serif';
const MONO = 'ui-monospace,Consolas,Menlo,monospace';

function rr(c, x, y, w, h, r) {
  c.beginPath();
  c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
}
function text(c, s, x, y, font, color, align = 'left') {
  c.font = font; c.fillStyle = color; c.textAlign = align; c.textBaseline = 'alphabetic'; c.fillText(s, x, y);
}

/* A small landscape: sky, sun, three ridges and water. hue picks the mood. */
function landscape(c, x, y, w, h, hue, r = 10) {
  c.save(); rr(c, x, y, w, h, r); c.clip();
  const sky = c.createLinearGradient(0, y, 0, y + h);
  sky.addColorStop(0, `hsl(${hue},62%,42%)`); sky.addColorStop(0.6, `hsl(${hue + 22},70%,70%)`); sky.addColorStop(1, `hsl(${hue + 40},80%,84%)`);
  c.fillStyle = sky; c.fillRect(x, y, w, h);
  c.fillStyle = 'rgba(255,246,214,.92)'; c.beginPath(); c.arc(x + w * 0.74, y + h * 0.34, h * 0.11, 0, 7); c.fill();
  const ridge = (base, amp, col, seed) => {
    c.fillStyle = col; c.beginPath(); c.moveTo(x, y + h);
    for (let i = 0; i <= 24; i++) {
      const t = i / 24;
      const yy = base - amp * (0.55 * Math.abs(Math.sin(t * 5.1 + seed)) + 0.45 * Math.abs(Math.sin(t * 11.3 + seed * 2.3)));
      c.lineTo(x + w * t, y + h * yy);
    }
    c.lineTo(x + w, y + h); c.closePath(); c.fill();
  };
  ridge(0.66, 0.34, `hsl(${hue + 8},34%,52%)`, hue * 0.07);
  ridge(0.78, 0.3, `hsl(${hue + 4},40%,36%)`, hue * 0.13 + 2);
  ridge(0.9, 0.2, `hsl(${hue},46%,22%)`, hue * 0.19 + 4);
  c.fillStyle = `hsla(${hue + 14},60%,70%,.55)`; c.fillRect(x, y + h * 0.9, w, h * 0.1);
  c.restore();
}

function icon(c, kind, cx, cy, s) {
  c.save(); c.translate(cx, cy); c.scale(s, s);
  c.strokeStyle = '#1b2430'; c.fillStyle = '#1b2430'; c.lineWidth = 7; c.lineCap = 'round'; c.lineJoin = 'round';
  if (kind === 0) {            // headphones
    c.beginPath(); c.arc(0, 2, 30, Math.PI, 0); c.stroke();
    rr(c, -40, 0, 20, 38, 8); c.fill(); rr(c, 20, 0, 20, 38, 8); c.fill();
  } else if (kind === 1) {     // chair
    rr(c, -24, -40, 48, 44, 10); c.fill(); rr(c, -30, 6, 60, 14, 6); c.fill();
    c.beginPath(); c.moveTo(-22, 20); c.lineTo(-28, 44); c.moveTo(22, 20); c.lineTo(28, 44); c.stroke();
  } else {                     // backpack
    rr(c, -28, -26, 56, 68, 16); c.fill();
    c.beginPath(); c.arc(0, -26, 15, Math.PI, 0); c.stroke();
    c.fillStyle = '#eef2f6'; rr(c, -17, 8, 34, 22, 6); c.fill();
  }
  c.restore();
}

/* Main page. Returns content boxes in page fractions: {kind, x, y, w, h, record?}. */
export function paintSite(canvas) {
  const W = canvas.width, H = canvas.height, c = canvas.getContext('2d'), k = W / 1600;
  const els = [];
  const el = (kind, x, y, w, h, record = -1) => els.push({ kind, x: x / 1600, y: y / 1000, w: w / 1600, h: h / 1000, record });
  c.setTransform(k, 0, 0, H / 1000, 0, 0);
  c.fillStyle = '#f7f9fb'; c.fillRect(0, 0, 1600, 1000);

  // browser bar
  c.fillStyle = '#16202a'; c.fillRect(0, 0, 1600, 58);
  [['#ff5f57', 28], ['#febc2e', 52], ['#28c840', 76]].forEach(([col, x]) => { c.fillStyle = col; c.beginPath(); c.arc(x, 29, 7.5, 0, 7); c.fill(); });
  c.fillStyle = '#0c141b'; rr(c, 126, 13, 1150, 32, 16); c.fill();
  c.strokeStyle = '#7fe9ff'; c.lineWidth = 2.2; rr(c, 146, 26, 11, 9, 2); c.stroke(); c.beginPath(); c.arc(151.5, 26, 4, Math.PI, 0); c.stroke();
  text(c, 'https://example.com', 170, 35, `500 17px ${MONO}`, '#cfe3ee');
  el('link', 126, 13, 1150, 32);
  c.fillStyle = '#2a3947'; for (let i = 0; i < 3; i++) { rr(c, 1330 + i * 78, 17, 58, 24, 8); c.fill(); }

  // site header
  c.fillStyle = '#ffffff'; c.fillRect(0, 58, 1600, 66);
  c.fillStyle = '#e3e8ee'; c.fillRect(0, 123, 1600, 1.5);
  text(c, 'Wayfarer', 40, 103, `700 34px ${SERIF}`, '#111a24');
  el('title', 36, 72, 170, 42);
  ['Home', 'World', 'Business', 'Tech', 'Travel', 'Shop'].forEach((n, i) => {
    const x = 330 + i * 124;
    text(c, n, x, 99, `600 18px ${SANS}`, i === 4 ? '#0a84c2' : '#33404d');
    if (i === 4) { c.fillStyle = '#0a84c2'; c.fillRect(x, 108, 52, 3); }
    el('link', x - 6, 78, 86, 32);
  });
  c.strokeStyle = '#33404d'; c.lineWidth = 3; c.beginPath(); c.arc(1518, 90, 10, 0, 7); c.moveTo(1526, 98); c.lineTo(1536, 108); c.stroke();
  el('link', 1500, 74, 44, 40);

  // lead story
  landscape(c, 40, 148, 920, 300, 205, 14);
  const shade = c.createLinearGradient(40, 0, 700, 0); shade.addColorStop(0, 'rgba(6,18,32,.72)'); shade.addColorStop(1, 'rgba(6,18,32,0)');
  c.save(); rr(c, 40, 148, 920, 300, 14); c.clip(); c.fillStyle = shade; c.fillRect(40, 148, 920, 300); c.restore();
  text(c, 'TRAVEL', 76, 222, `700 15px ${SANS}`, '#8fe8ff');
  text(c, 'Global Travel Trends', 74, 286, `700 54px ${SERIF}`, '#ffffff');
  text(c, 'for the Year Ahead', 74, 344, `700 44px ${SERIF}`, '#ffffff');
  text(c, 'Where people are going, what it costs, and when to book.', 76, 388, `400 19px ${SANS}`, '#dbe9f2');
  c.fillStyle = '#ffffff'; rr(c, 76, 404, 138, 30, 15); c.fill(); text(c, 'Read more  →', 145, 425, `600 15px ${SANS}`, '#0a5f8f', 'center');
  el('image', 560, 160, 390, 276); el('title', 70, 240, 560, 110); el('text', 74, 368, 520, 28); el('link', 76, 404, 138, 30);

  // latest articles
  text(c, 'Latest Articles', 40, 500, `700 30px ${SERIF}`, '#111a24');
  el('title', 38, 472, 250, 38);
  const arts = [['Best Cities to Visit This Year', 28, 'A short list of places that are easy to reach,', 'kind to a budget and worth more than a weekend.'],
    ['Travel Tips for Remote Workers', 150, 'How to keep a steady routine on the road, from', 'reliable internet to quiet places to take a call.']];
  arts.forEach((a, i) => {
    const y = 522 + i * 138;
    c.fillStyle = '#ffffff'; rr(c, 40, y, 920, 124, 12); c.fill(); c.strokeStyle = '#e3e8ee'; c.lineWidth = 1.5; rr(c, 40, y, 920, 124, 12); c.stroke();
    landscape(c, 52, y + 12, 190, 100, a[1], 8);
    text(c, a[0], 264, y + 42, `700 25px ${SERIF}`, '#111a24');
    text(c, a[2], 264, y + 70, `400 16px ${SANS}`, '#5a6877'); text(c, a[3], 264, y + 92, `400 16px ${SANS}`, '#5a6877');
    text(c, 'Read more →', 264, y + 114, `600 14px ${SANS}`, '#0a84c2');
    el('image', 52, y + 12, 190, 100); el('title', 262, y + 18, 480, 32); el('text', 262, y + 54, 520, 44); el('link', 262, y + 100, 100, 20);
  });

  // travel guides strip
  text(c, 'Travel Guides', 40, 832, `700 30px ${SERIF}`, '#111a24');
  el('title', 38, 804, 230, 38);
  [['Mountain Routes', 95], ['Coast by Rail', 190], ['City Breaks', 330]].forEach((g, i) => {
    const x = 40 + i * 312;
    landscape(c, x, 850, 296, 88, g[1], 10);
    text(c, g[0], x + 14, 962, `600 17px ${SANS}`, '#1c2733');
    el('image', x, 850, 296, 88); el('link', x + 10, 944, 180, 24);
  });

  // products
  text(c, 'Products', 1000, 180, `700 30px ${SERIF}`, '#111a24');
  el('title', 998, 152, 160, 38);
  RECORDS.slice(0, 3).forEach((p, i) => {
    const x = 1000 + i * 190;
    c.fillStyle = '#ffffff'; rr(c, x, 200, 176, 246, 12); c.fill(); c.strokeStyle = '#e3e8ee'; c.lineWidth = 1.5; rr(c, x, 200, 176, 246, 12); c.stroke();
    c.fillStyle = '#eef2f6'; rr(c, x + 10, 210, 156, 118, 8); c.fill();
    icon(c, i, x + 88, 268, 0.95);
    text(c, p.title, x + 14, 358, `600 18px ${SANS}`, '#1c2733');
    text(c, p.price, x + 14, 390, `700 24px ${SANS}`, '#111a24');
    c.fillStyle = '#d9f7df'; rr(c, x + 14, 404, 86, 26, 6); c.fill(); text(c, 'In Stock', x + 57, 422, `600 14px ${SANS}`, '#12813a', 'center');
    el('record', x, 200, 176, 246, i);
  });

  // destinations table
  text(c, 'Destinations', 1000, 500, `700 30px ${SERIF}`, '#111a24');
  el('title', 998, 472, 220, 38);
  c.fillStyle = '#ffffff'; rr(c, 1000, 518, 556, 216, 12); c.fill(); c.strokeStyle = '#e3e8ee'; rr(c, 1000, 518, 556, 216, 12); c.stroke();
  c.fillStyle = '#eef2f6'; rr(c, 1000, 518, 556, 48, 12); c.fill(); c.fillRect(1000, 540, 556, 26);
  ['Destination', 'Price', 'Availability', 'Rating'].forEach((h, i) => text(c, h, 1020 + [0, 168, 282, 440][i], 549, `700 16px ${SANS}`, '#33404d'));
  [['Tokyo', '$799', 'In Stock', '4.8'], ['Paris', '$699', 'In Stock', '4.7'], ['Rome', '$649', 'Low Stock', '4.6']].forEach((r, i) => {
    const y = 566 + i * 56;
    if (i) { c.fillStyle = '#e9edf2'; c.fillRect(1014, y, 528, 1.5); }
    text(c, r[0], 1020, y + 35, `600 18px ${SANS}`, '#1c2733'); text(c, r[1], 1188, y + 35, `700 18px ${SANS}`, '#111a24');
    const low = r[2] !== 'In Stock';
    c.fillStyle = low ? '#fdeccd' : '#d9f7df'; rr(c, 1296, y + 14, 100, 28, 6); c.fill();
    text(c, r[2], 1346, y + 33, `600 14px ${SANS}`, low ? '#8a5a00' : '#12813a', 'center');
    text(c, r[3] + ' ★', 1460, y + 35, `600 18px ${SANS}`, '#33404d');
    el('record', 1004, y + 2, 548, 52, 3 + i);
  });

  // planner card
  c.fillStyle = '#0d2233'; rr(c, 1000, 760, 556, 206, 12); c.fill();
  text(c, 'Trip Planner', 1024, 804, `700 26px ${SERIF}`, '#ffffff');
  ['Flights and rail passes', 'Hotels near the centre', 'Weekend itineraries', 'Visa and entry rules'].forEach((l, i) => {
    const y = 842 + i * 30;
    c.fillStyle = '#39d5f0'; c.beginPath(); c.arc(1032, y - 6, 4, 0, 7); c.fill();
    text(c, l, 1048, y, `500 17px ${SANS}`, '#cfe3ee');
    el('link', 1022, y - 22, 300, 28);
  });
  c.fillStyle = '#19d9ee'; rr(c, 1392, 912, 140, 36, 18); c.fill(); text(c, 'Plan a trip', 1462, 936, `700 15px ${SANS}`, '#04222c', 'center');
  el('link', 1392, 912, 140, 36);
  return els;
}

/* Four linked pages in one 2 x 2 atlas. */
export function paintAtlas(canvas) {
  const c = canvas.getContext('2d'), w = canvas.width / 2, h = canvas.height / 2;
  const pages = [['example.com/guide/', 'Travel Guide', 205, 0], ['example.com/products/1', 'Product Details', 28, 1],
    ['example.com/products/1/specs', 'Specifications', 150, 2], ['example.com/travel/tips/', 'Travel Tips', 330, 3]];
  pages.forEach(([url, title, hue, kind], i) => {
    c.setTransform(w / 800, 0, 0, h / 500, (i % 2) * w, Math.floor(i / 2) * h);
    c.fillStyle = '#f7f9fb'; c.fillRect(0, 0, 800, 500);
    c.fillStyle = '#16202a'; c.fillRect(0, 0, 800, 50);
    [['#ff5f57', 24], ['#febc2e', 46], ['#28c840', 68]].forEach(([col, x]) => { c.fillStyle = col; c.beginPath(); c.arc(x, 25, 7, 0, 7); c.fill(); });
    c.fillStyle = '#0c141b'; rr(c, 100, 10, 560, 30, 15); c.fill();
    text(c, 'https://' + url, 120, 31, `500 16px ${MONO}`, '#cfe3ee');
    text(c, title, 30, 104, `700 38px ${SERIF}`, '#111a24');
    if (kind === 2) {
      for (let r = 0; r < 7; r++) {
        const y = 132 + r * 48;
        c.fillStyle = r % 2 ? '#ffffff' : '#eef2f6'; c.fillRect(30, y, 740, 48);
        c.fillStyle = '#33404d'; rr(c, 46, y + 17, 150 + (r * 37) % 80, 13, 6); c.fill();
        c.fillStyle = '#8b98a6'; rr(c, 330, y + 17, 220 + (r * 53) % 150, 13, 6); c.fill();
      }
    } else {
      if (kind === 1) { c.fillStyle = '#eef2f6'; rr(c, 30, 128, 330, 250, 12); c.fill(); icon(c, 0, 195, 250, 2.1); }
      else landscape(c, 30, 128, 330, 250, hue, 12);
      for (let r = 0; r < 9; r++) { c.fillStyle = r === 0 ? '#33404d' : '#9aa7b4'; rr(c, 390, 138 + r * 27, r === 0 ? 300 : 250 + (r * 61) % 120, r === 0 ? 16 : 11, 5); c.fill(); }
      if (kind === 1) { text(c, '$129.00', 390, 420, `700 36px ${SANS}`, '#111a24'); c.fillStyle = '#19d9ee'; rr(c, 560, 388, 170, 44, 22); c.fill(); text(c, 'Add to cart', 645, 417, `700 17px ${SANS}`, '#04222c', 'center'); }
      for (let r = 0; r < 3; r++) { c.fillStyle = '#b4bfca'; rr(c, 30, 404 + r * 26, 320 - r * 50, 11, 5); c.fill(); }
    }
  });
}
