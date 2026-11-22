/* Homepage crawl scene, page side. The scene itself runs in a worker (engine.js); this file wires it to the
   page: scrolling drives the camera through four chapters, and the crawl is mirrored into the dataset panel. */
const canvas = document.getElementById('crawl-scene');
const hero = canvas && canvas.closest('.hero');
const STATUS = ['Finding links', 'Reading pages', 'Extracting fields', '6 records · JSON ready'];

if (canvas && hero) boot();

function boot() {
  const stage = hero.querySelector('.stage'), chapters = [...hero.querySelectorAll('[data-chapter]')];
  const rows = [...hero.querySelectorAll('[data-row]')], chips = [...hero.querySelectorAll('[data-stage]')];
  const statusEl = hero.querySelector('[data-status]'), countEl = hero.querySelector('[data-count]'), panel = hero.querySelector('.dataset');
  const stats = window.__crawlScene = { fps: 0, ms: 0, cpu: 0, draws: 0, tris: 0, bots: 0, size: [0, 0], quality: 1, pagePx: 0, thread: 'worker', chapter: 0 };
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (calm) hero.classList.add('flat');           // no journey: one still picture and the chapters as plain text
  let live = canvas, send = null, shown = 0, failed = false, chapter = 0;
  const queue = [];
  const tell = (m) => { if (send) send(m); else queue.push(m); };

  /* Size of the stage, and where the dataset panel sits in the picture (x and y from -1 to 1) so data flies to it. */
  const measure = () => {
    const r = stage.getBoundingClientRect();
    let anchor = [0.62, -0.35];
    if (panel && panel.offsetParent) { const p = panel.getBoundingClientRect(); anchor = [((p.left + 22 - r.left) / r.width) * 2 - 1, 1 - ((p.top + p.height / 2 - r.top) / r.height) * 2]; }
    return { cssW: r.width, cssH: r.height, dpr: window.devicePixelRatio || 1, anchor };
  };
  const first = measure();
  const compact = first.cssW < 900 || first.cssW / first.cssH < 1.05;
  const cfg = { ...first, compact, calm, debug: +(new URLSearchParams(location.search).get('lens') || 0),
    weak: compact || (navigator.deviceMemory || 8) <= 4 || (navigator.hardwareConcurrency || 8) <= 4 };

  /* Scroll position inside the hero, 0 to 1, and the chapter it belongs to. */
  const progress = () => { const span = hero.offsetHeight - stage.offsetHeight; return span > 4 ? Math.min(1, Math.max(0, (scrollY - hero.offsetTop) / span)) : 0; };
  const setChapter = (i) => {
    if (i === chapter) return;
    chapter = stats.chapter = i; stage.dataset.ch = i;
    chapters.forEach((c, k) => c.classList.toggle('on', k === i)); chips.forEach((c, k) => c.classList.toggle('on', k === i));
  };
  chapters.forEach((c, k) => c.addEventListener('focusin', () => {      // keyboard users: bring a chapter's links on stage with it
    const span = hero.offsetHeight - stage.offsetHeight;
    if (span > 4 && k !== chapter) scrollTo({ top: hero.offsetTop + span * (k / 3), behavior: 'auto' });
  }));

  function fail(reason) {
    if (failed) return;
    failed = true; send = () => {};
    hero.classList.add('no-gl', 'flat');
    // the still picture shows a finished crawl, so the panel shows the finished dataset
    rows.forEach((row) => row.classList.add('on')); if (countEl) countEl.textContent = rows.length;
    if (statusEl) statusEl.textContent = STATUS[3];
    console.info('Crawl scene not started:', reason);
  }
  function handle(m) {
    if (m.t === 'row') { const row = rows[m.r]; if (row && !row.classList.contains('on')) { row.classList.add('on'); shown++; if (countEl) countEl.textContent = shown; } }
    else if (m.t === 'reset') { rows.forEach((row) => row.classList.remove('on')); shown = 0; if (countEl) countEl.textContent = 0; }
    else if (m.t === 'stage') { if (statusEl) statusEl.textContent = STATUS[m.i]; }
    else if (m.t === 'on') { live.classList.add('on'); performance.mark('crawl:first-picture'); }
    else if (m.t === 'stats') Object.assign(stats, m);
    else if (m.t === 'mark') performance.mark('crawl:' + m.name);
    else if (m.t === 'fail') onFail(m.m);
  }

  /* Fallback: run the same engine on the page thread, on a fresh canvas (the first one now belongs to the worker). */
  function onPage(reason) {
    if (live !== canvas || failed) return fail(reason);
    live = canvas.cloneNode(false);
    canvas.replaceWith(live);
    stats.thread = 'page';
    send = null;
    import('./engine.js').then(({ run }) => run(live, cfg, handle)).then((api) => { send = api.input; queue.splice(0).forEach(send); }).catch((err) => fail(err && err.message));
  }
  let onFail = fail;
  const offscreen = typeof Worker === 'function' && typeof OffscreenCanvas === 'function' && canvas.transferControlToOffscreen;
  if (offscreen) {
    try {
      const worker = new Worker(new URL('./scene-worker.js', import.meta.url), { type: 'module' });
      const surface = canvas.transferControlToOffscreen();
      // a worker that cannot make a WebGL2 context is not a broken device: try once more on the page thread
      onFail = (reason) => (/WebGL2 is not available/.test(reason) ? onPage(reason) : fail(reason));
      worker.onmessage = (e) => handle(e.data);
      worker.onerror = () => onPage('the scene worker did not load');
      worker.postMessage({ t: 'init', canvas: surface, cfg }, [surface]);
      send = (m) => worker.postMessage(m);
    } catch (err) { onPage(err && err.message); }
  } else onPage('no worker canvas');

  /* Inputs. Pointer and scroll are sent at most once a frame. */
  let pending = null, ticking = false;
  const flush = () => {
    ticking = false;
    if (pending) { tell(pending); pending = null; }
    const v = progress();
    tell({ t: 'scroll', v });
    if (!hero.classList.contains('flat')) setChapter(Math.round(v * 3));
  };
  const soon = () => { if (!ticking) { ticking = true; requestAnimationFrame(flush); } };
  stage.addEventListener('pointermove', (e) => {
    const r = stage.getBoundingClientRect();
    pending = { t: 'pointer', x: ((e.clientX - r.left) / r.width) * 2 - 1, y: ((e.clientY - r.top) / r.height) * 2 - 1 };
    soon();
  }, { passive: true });
  stage.addEventListener('pointerleave', () => { pending = { t: 'pointer', x: null, y: null }; soon(); }, { passive: true });
  addEventListener('scroll', soon, { passive: true });
  let inView = true, timer = 0;
  const show = () => tell({ t: 'show', on: inView && !document.hidden });
  if ('IntersectionObserver' in window) new IntersectionObserver((es) => { inView = es[0].isIntersecting; show(); }, { threshold: 0 }).observe(hero);
  document.addEventListener('visibilitychange', show);
  addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(() => { tell({ t: 'size', ...measure() }); soon(); }, 120); }, { passive: true });
  if (scrollY) soon();
}
