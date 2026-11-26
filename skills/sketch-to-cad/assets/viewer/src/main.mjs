// Viewer entry point. Reads only the inline #sketchcad-data JSON (see viewer.py).
// Story (timeline.mjs), one part: 1 source sketch -> 2 drawing sheet, laid onto the 3D
// floor -> 3 wires drawn flat on it rise into 3D -> solid -> spin. Two or more parts:
// 1 sketch -> 2 parts sheet, laid on the floor, each part's wire drawn flat on its own
// printed view -> 3 the wires rise, then fly one by one to their places in the assembly
// and turn solid as they seat -> spin.
//
// Progress sources:
//   default       the page's own scroll (works inside an iframe; never the parent's)
//   ?autoplay=1   12 s timeline (15 s for the assembly story), no loop; the replay button restarts it
//   <html data-autoplay>  the same, baked into a copy of the file (for hosts that drop the query)
//   reduced motion  final state only
// When progress reaches 1 the model can be turned by mouse or touch, and a
// multi-part model can be exploded and reassembled with the #explode toggle.
import {OrbitControls} from 'three/examples/jsm/controls/OrbitControls.js';
import {createRenderLoop, createViewerScene} from './scene.mjs';
import {ASSEMBLY_TIMING, TIMING, assemblyState, clamp01, timelineState} from './timeline.mjs';

const AUTOPLAY_MS = {rise: 12000, assembly: 15000};
const EXPLODE_MS = 900;
const easeInOut = t => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const root = document.documentElement;
const $ = sel => document.querySelector(sel);
const status = window.sketchcadViewer = {status: 'loading', error: null, mode: null, progress: 0,
  renderedProgress: null, interactive: false, frames: 0, alignmentMm: null, partCount: 0, explode: 0,
  setShadowVisible: null, partScreenBoxes: null};

function setState(value, error = null) {
  status.status = value;
  status.error = error;
  root.dataset.state = value;
}

function fail(error) {
  setState('error', String(error?.message || error));
  const note = $('#status');
  note.textContent = 'This 3D view could not be loaded.';
  note.hidden = false;
  console.error(error);
}

function readData() {
  return JSON.parse(document.getElementById('sketchcad-data').textContent);
}

function pickMode() {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return 'still';
  const autoplay = new URLSearchParams(location.search).get('autoplay') === '1'
    || document.documentElement.hasAttribute('data-autoplay');
  return autoplay ? 'autoplay' : 'scroll';
}

// A paper card (#sketch or #sheet) sized to fit the free space at the given aspect.
const CARD_MAX_WIDTH = 880;
function fitter(holder, card, ratio) {
  return () => {
    const r = holder.getBoundingClientRect();
    const width = Math.min(r.width, r.height * ratio, CARD_MAX_WIDTH);
    card.style.width = `${width}px`;
    card.style.height = `${width / ratio}px`;
  };
}

// Stage 1: the source sketch the model was read from.
function mountSketch(sketch) {
  const holder = $('#sketch');
  if (!sketch?.src || !(sketch.width > 0) || !(sketch.height > 0)) { holder.remove(); return null; }
  const card = holder.querySelector('.card');
  const img = card.querySelector('img');
  img.width = sketch.width;
  img.height = sketch.height;
  img.src = sketch.src;
  return {el: holder, fit: fitter(holder, card, sketch.width / sketch.height)};
}

// Stage 2: the dimensioned drawing sheet.
function mountSheet(svgText) {
  const holder = $('#sheet');
  if (!svgText) { holder.remove(); return null; }
  const card = holder.querySelector('.card');
  card.innerHTML = svgText;
  const svg = card.querySelector('svg');
  const [, , w, h] = (svg.getAttribute('viewBox') || '0 0 420 297').split(/[\s,]+/).map(Number);
  svg.removeAttribute('width');
  svg.removeAttribute('height');
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  const fitCard = fitter(holder, card, w / h);
  const sheet = {el: holder, card, edge: holder.querySelector('.edge'), svgText, paper: [w, h], base: null,
    fit() { // size the card, then note its untransformed box (the corners the lie-down starts from)
      fitCard();
      const keep = card.style.transform;
      card.style.transform = 'none';
      const r = card.getBoundingClientRect();
      sheet.base = {x: r.left, y: r.top, w: r.width, h: r.height};
      card.style.transform = keep;
    }};
  return sheet;
}

// CSS matrix3d that maps the box (0,0)-(w,h) onto the quad q (top-left, top-right,
// bottom-right, bottom-left), a projective map (Heckbert's square-to-quad).
function quadMatrix(w, h, q) {
  const [[x0, y0], [x1, y1], [x2, y2], [x3, y3]] = q;
  const dx1 = x1 - x2, dx2 = x3 - x2, dx3 = x0 - x1 + x2 - x3;
  const dy1 = y1 - y2, dy2 = y3 - y2, dy3 = y0 - y1 + y2 - y3;
  const det = dx1 * dy2 - dx2 * dy1;
  const g = Math.abs(det) > 1e-9 ? (dx3 * dy2 - dx2 * dy3) / det : 0;
  const k = Math.abs(det) > 1e-9 ? (dx1 * dy3 - dx3 * dy1) / det : 0;
  const a = x1 - x0 + g * x1, b = x3 - x0 + k * x3, d = y1 - y0 + g * y1, e = y3 - y0 + k * y3;
  const m = [a / w, d / w, 0, g / w, b / h, e / h, 0, k / h, 0, 0, 1, 0, x0, y0, 0, 1];
  return `matrix3d(${m.map(v => +v.toFixed(9)).join(',')})`;
}

const showCard = (card, opacity, transform) => {
  card.el.style.opacity = String(opacity);
  card.el.style.transform = transform;
  card.el.style.visibility = opacity > 0.001 ? 'visible' : 'hidden';
};

async function start() {
  const data = readData();
  const mode = pickMode();
  status.mode = root.dataset.mode = mode;
  root.style.setProperty('--wire', data.wireColor);
  $('#title').textContent = data.name;

  const canvas = $('#view');
  const multi = data.parts.length > 1;
  const still = mode === 'still';
  const sheet = still ? null : mountSheet(multi ? data.partsSheetSvg : data.parts[0].drawingSvg);
  const view = await createViewerScene(canvas, data, sheet ? {sheetSvg: sheet.svgText, sheetPaper: sheet.paper,
    topView: data.sheetTopView, partsSheet: multi ? data.partsSheet : null} : {});
  const assembly = view.story === 'assembly';
  status.story = root.dataset.story = view.story;
  status.autoplayMs = AUTOPLAY_MS[view.story];
  // progress values tools/check-viewer.cjs inspects: sheet card shown, wires flat on the print, 3D
  status.checkpoints = assembly
    ? {sheet: 0.2, flat: ASSEMBLY_TIMING.flatCheck, mid: ASSEMBLY_TIMING.midFlight, threeD: 0.5}
    : {sheet: 0.22, flat: TIMING.flatCheck, threeD: 0.5};
  status.alignmentMm = view.alignmentMm;
  status.partCount = data.parts.length;
  root.toggleAttribute('data-multi', multi);
  const sketch = still ? null : mountSketch(data.sketch);
  if (still) { $('#sketch')?.remove(); $('#sheet')?.remove(); }
  const cards = {sketch: !!sketch, sheet: !!sheet};
  // stage strip: drop the stages this model has no card for
  if (!sketch) $('#stages [data-stage="1"]').remove();
  if (!sheet) $('#stages [data-stage="2"]').remove();
  if (assembly) { // Sketch -> Parts sheet -> 3D assembly
    const label = (n, text) => { const li = $(`#stages [data-stage="${n}"]`); if (li) li.textContent = text; };
    label(2, 'Parts sheet');
    label(3, '3D assembly');
    $('#stages').setAttribute('aria-label', 'Sketch → parts sheet → 3D assembly');
  }
  const stageItems = [...document.querySelectorAll('#stages [data-stage]')];
  let shownStage = 0;

  const controls = new OrbitControls(view.camera);
  controls.enableDamping = false;
  controls.enablePan = false;
  controls.enableZoom = mode !== 'scroll'; // in scroll mode the wheel keeps scrolling the page
  let connected = false;

  let progress = mode === 'still' ? 1 : 0;
  const fill = $('#fill');
  const replay = $('#replay');
  const explodeButton = $('#explode');

  // End-state explode (0 assembled .. 1 exploded). Not part of the timeline: it is
  // reset to 0 whenever the story leaves the end, so scrubbing stays deterministic.
  // The camera keeps the user's orbit direction and moves between two framings:
  // `fromView`, the camera as it was when leaving the assembled state (so toggling
  // back returns exactly there), and `toView`, the exploded parts fitted and
  // centered from that direction.
  let explode = 0, explodeGoal = 0, explodeAnim = null, fromView = null, toView = null;
  const distanceAt = e => fromView.distance + (toView.distance - fromView.distance) * e;
  function labelExplode() {
    const exploded = explodeGoal === 1;
    explodeButton.textContent = exploded ? 'Assemble' : 'Explode';
    explodeButton.setAttribute('aria-label', exploded ? 'Assemble parts' : 'Explode parts');
  }
  function setExplode(e) {
    if (connected) { // keep the user's orbit angle; move the orbit target and distance between the framings
      const offset = view.camera.position.clone().sub(controls.target);
      if (explode === 0) {
        fromView = {target: controls.target.clone(), distance: offset.length()};
        toView = view.fitAt('exploded', offset);
      }
      const target = fromView.target.clone().lerp(toView.target, e);
      offset.multiplyScalar(distanceAt(e) / distanceAt(explode)); // keeps a zoom the user made meanwhile
      view.camera.position.copy(target).add(offset);
      controls.target.copy(target);
      controls.update();
    }
    explode = status.explode = e;
    loop.request();
  }
  function resetExplode() {
    if (explodeAnim) cancelAnimationFrame(explodeAnim);
    explodeAnim = null;
    explodeGoal = 0;
    explode = status.explode = 0;
    labelExplode();
  }
  function toggleExplode() {
    if (!connected || !multi) return;
    explodeGoal = explodeGoal === 1 ? 0 : 1;
    labelExplode();
    if (explodeAnim) cancelAnimationFrame(explodeAnim);
    explodeAnim = null;
    if (mode === 'still') { setExplode(explodeGoal); return; } // reduced motion: no animation
    const from = explode, to = explodeGoal;
    const duration = EXPLODE_MS * Math.abs(to - from);
    let t0 = null;
    const step = now => {
      if (t0 === null) t0 = now;
      const t = duration > 0 ? Math.min(1, (now - t0) / duration) : 1;
      setExplode(t >= 1 ? to : from + (to - from) * easeInOut(t));
      explodeAnim = t < 1 ? requestAnimationFrame(step) : null;
    };
    explodeAnim = requestAnimationFrame(step);
  }
  explodeButton.addEventListener('click', toggleExplode);
  labelExplode();

  function setInteractive(on) {
    if (on === connected) return;
    if (!on) resetExplode();
    connected = status.interactive = on;
    if (on) {
      controls.connect(canvas);
      // scroll mode: vertical swipes keep scrolling, horizontal drags turn the model
      canvas.style.touchAction = mode === 'scroll' ? 'pan-y' : 'none';
      // start from the assembled framing even when progress jumped straight to 1
      // (reduced motion, a fast scroll) and no frame placed the camera there yet
      view.placeCamera(1, 1);
      controls.target.copy(view.target);
      controls.update();
    } else {
      controls.disconnect();
    }
    root.toggleAttribute('data-done', on);
  }

  const loop = createRenderLoop(() => {
    const state = assembly ? assemblyState(progress, view.assemblyRank, cards) : timelineState(progress, data.parts.length, cards);
    view.apply(state, {camera: !connected, explode});
    if (sketch) { // recedes as the sheet covers it (or as the 3D starts when there is no sheet)
      const away = 1 - state.sketch;
      showCard(sketch, state.sketch, `translateY(${away * -10}px) scale(${1 - 0.03 * away}) rotate(-0.8deg)`);
    }
    if (sheet) {
      // wipes in left to right over the sketch, then lies down: its corners travel to
      // the floor sheet's projected corners, where the 3D plane takes over
      sheet.el.style.opacity = String(state.sheet);
      sheet.el.style.visibility = state.sheet > 0.001 ? 'visible' : 'hidden';
      const lie = state.sheetLie, base = sheet.base;
      if (lie > 0 && base && base.w > 0 && state.sheet > 0.001) {
        const flat = [[base.x, base.y], [base.x + base.w, base.y], [base.x + base.w, base.y + base.h], [base.x, base.y + base.h]];
        const quad = view.sheetQuad().map(([x, y], i) => [
          flat[i][0] + (x - flat[i][0]) * lie - base.x, flat[i][1] + (y - flat[i][1]) * lie - base.y]);
        sheet.card.style.transform = quadMatrix(base.w, base.h, quad);
      } else {
        sheet.card.style.transform = 'none';
      }
      const wiping = state.sheetIn < 1;
      sheet.card.style.clipPath = wiping ? `inset(-64px ${((1 - state.sheetIn) * 100).toFixed(2)}% -64px -64px)` : 'none';
      sheet.edge.style.left = `${(state.sheetIn * 100).toFixed(2)}%`;
      sheet.edge.style.opacity = wiping ? String(Math.sin(Math.PI * state.sheetIn).toFixed(3)) : '0';
    }
    if (state.stage !== shownStage) {
      shownStage = state.stage;
      for (const item of stageItems) {
        const on = Number(item.dataset.stage) === shownStage;
        item.classList.toggle('on', on);
        if (on) item.setAttribute('aria-current', 'step'); else item.removeAttribute('aria-current');
      }
    }
    fill.style.transform = `scaleX(${state.progress})`;
    root.toggleAttribute('data-started', state.progress > 0.015);
    view.render();
    status.renderedProgress = state.progress;
    status.seated = state.parts.filter(s => (s.fly ?? 1) >= 1).length; // assembly story: parts in place
    status.frames++;
  });

  function setProgress(p) {
    progress = clamp01(p);
    status.progress = progress;
    setInteractive(progress >= 1);
    loop.request();
  }
  controls.addEventListener('change', loop.request);
  // hooks for tools/check-viewer.cjs
  status.setShadowVisible = on => { view.setShadowVisible(on); loop.request(); };
  status.partScreenBoxes = () => view.partScreenBoxes();
  status.topViewScreenBox = () => view.topViewScreenBox();
  status.itemScreenBoxes = () => view.itemScreenBoxes();
  status.explodeTree = view.explodeTree;

  function resize() {
    const r = canvas.getBoundingClientRect();
    view.resize(Math.max(1, r.width), Math.max(1, r.height));
    sketch?.fit();
    sheet?.fit();
    if (connected) { // keep the user's orbit angle, refit both framings to the new aspect
      const dir = view.camera.position.clone().sub(controls.target).normalize();
      fromView = view.fitAt('assembled', dir);
      toView = multi ? view.fitAt('exploded', dir) : fromView;
      controls.target.copy(fromView.target).lerp(toView.target, explode);
      view.camera.position.copy(controls.target).addScaledVector(dir, distanceAt(explode));
      controls.update();
    }
    loop.request();
  }
  new ResizeObserver(resize).observe($('#stage'));
  resize();

  if (mode === 'scroll') {
    const read = () => {
      const max = document.documentElement.scrollHeight - innerHeight;
      setProgress(max > 0 ? scrollY / max : 1);
    };
    addEventListener('scroll', read, {passive: true});
    addEventListener('resize', read, {passive: true});
    replay.addEventListener('click', () => scrollTo({top: 0, behavior: 'smooth'}));
    replay.setAttribute('aria-label', 'Back to start');
    read();
  } else if (mode === 'autoplay') {
    let t0 = null, running = false;
    const tick = now => {
      if (t0 === null) t0 = now;
      setProgress((now - t0) / status.autoplayMs);
      if (progress < 1) requestAnimationFrame(tick);
      else running = false;
    };
    const play = () => {
      t0 = null;
      setProgress(0);
      if (!running) { running = true; requestAnimationFrame(tick); }
    };
    replay.addEventListener('click', play);
    replay.setAttribute('aria-label', 'Replay');
    if (document.visibilityState === 'visible') play();
    else document.addEventListener('visibilitychange', play, {once: true});
  } else {
    setProgress(1);
  }
  setState('ready');
}

start().catch(fail);
