// Browser check for a generated viewer/index.html (developer-only).
//
//   docker run --rm -u "$(id -u)" -e HOME=/tmp \
//     -e PUPPETEER_CACHE_DIR=/home/pptruser/.cache/puppeteer \
//     -v "$PWD":/work -w /work ghcr.io/puppeteer/puppeteer \
//     node tools/check-viewer.cjs OUT/viewer/index.html [SHOTS_DIR]
//
// Opens the file over file:// at desktop 1440x900 and mobile 390x844 and checks:
// no network requests, no console errors, wire/GLB alignment, non-empty frames at
// the story's checkpoints (window.sketchcadViewer.checkpoints) and 0 / 0.7 / 1,
// rewinding 1 -> 0 reproduces the first frame (< 1% pixels), ?autoplay=1 reaches the
// final state within the story's autoplay time + 1 s, reduced motion shows the final
// state, and dragging turns the model once the story is done.
// Story stages (timeline.mjs): at 0 the source sketch card is shown (the drawing
// sheet when the page has no sketch); at checkpoints.sheet the sheet card is fully
// shown over a hidden sketch; at checkpoints.threeD (0.5) both cards are gone (the
// sheet lies on the 3D floor, drawn in the canvas). One part ("rise" story): the strip
// reads Sketch / Drawing / 3D, and at checkpoints.flat the flattened wire lies on the
// sheet's printed top view (within 3 px). Two or more parts ("assembly" story): the
// strip reads Sketch / Parts sheet / 3D assembly, at checkpoints.flat every part's flat wire
// lies on its own printed view of the parts sheet (near and far edges within 3 px,
// inside it sideways within 3 px), at checkpoints.mid some parts are seated and some
// are not (they arrive one by one), and at 1 every part is seated.
// At progress 1 a soft ground shadow sits below the model; with several parts the explode toggle
// (mouse, touch, keyboard) changes the frame and toggling back restores it
// (< 1% pixels); in the exploded state every part's screen box lies inside the
// canvas and no two parts' screen boxes overlap by more than 25% of the smaller.
// Framing: assembled (at 1) and exploded, the parts' projected bounds fill >= 60% of
// the limiting canvas dimension, centered within 8% of the canvas. Exploded, no more
// than 3% of the shadow pixels lie farther than 6% of the canvas from every part's
// screen box (no stray smear under a lifted part).
// Screenshots go to SHOTS_DIR, by default out/viewer-shots/ (or the system temp
// folder when that is not writable).
// Prints PASS and exits 0 when every check holds.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

function loadPuppeteer() {
  for (const id of ['puppeteer', '/home/pptruser/node_modules/puppeteer']) {
    try { return require(id); } catch (e) { if (e.code !== 'MODULE_NOT_FOUND') throw e; }
  }
  throw new Error('puppeteer not found; run inside the ghcr.io/puppeteer/puppeteer image');
}
const puppeteer = loadPuppeteer();

const VIEWPORTS = [
  {name: 'desktop', width: 1440, height: 900, deviceScaleFactor: 1},
  {name: 'mobile', width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true},
];
const BG_TOLERANCE = 12;       // channel difference that counts as "not background"
const MIN_INK = 0.002;         // share of non-background pixels for a non-empty frame
const REWIND_MAX_DIFF = 0.01;  // share of differing pixels allowed after rewinding
const AUTOPLAY_SLACK_MS = 1000;
const TOPVIEW_PX = 3;           // flat wire vs printed top view on screen (CSS px)
const ALIGN_MAX_MM = 0.5;
const CSS_TRANSITION_MS = 500; // hint/button fades finish before a screenshot
const MIN_SHADOW = 0.002;      // share of pixels the ground shadow darkens
const MIN_EXPLODE_DIFF = 0.01; // share of pixels the explode toggle changes
const MAX_EXPLODE_OVERLAP = 0.25; // exploded parts: screen-box overlap / smaller box area
const MIN_FILL = 0.6;             // projected model bounds / limiting canvas dimension
const MAX_CENTER_OFFSET = 0.08;   // projected bounds center vs canvas center, share of the canvas
const SHADOW_MARGIN = 0.06;       // share of the shorter canvas side a shadow may reach past a part (VSM blur halo + a grounded part's own offset)
const MAX_STRAY_SHADOW = 0.03;    // share of shadow pixels allowed beyond that margin

const failures = [];
const notes = [];
const check = (ok, message) => { (ok ? notes : failures).push(`${ok ? 'ok  ' : 'FAIL'} ${message}`); };

function shotsDir(requested) {
  const preferred = path.resolve(requested || path.join('out', 'viewer-shots'));
  try {
    fs.mkdirSync(preferred, {recursive: true});
    fs.accessSync(preferred, fs.constants.W_OK);
    return preferred;
  } catch {
    const fallback = fs.mkdtempSync(path.join(os.tmpdir(), 'viewer-shots-'));
    console.warn(`${preferred} is not writable; screenshots go to ${fallback}`);
    return fallback;
  }
}

// Pixel statistics run inside the page on data: URLs, so no extra requests or packages.
async function inkRatio(page, png) {
  return page.evaluate(async b64 => {
    const bmp = await createImageBitmap(await (await fetch(`data:image/png;base64,${b64}`)).blob());
    const c = new OffscreenCanvas(bmp.width, bmp.height).getContext('2d');
    c.drawImage(bmp, 0, 0);
    const d = c.getImageData(0, 0, bmp.width, bmp.height).data;
    const bg = getComputedStyle(document.body).backgroundColor.match(/\d+/g).map(Number);
    let ink = 0;
    for (let i = 0; i < d.length; i += 4)
      if (Math.abs(d[i] - bg[0]) > 12 || Math.abs(d[i + 1] - bg[1]) > 12 || Math.abs(d[i + 2] - bg[2]) > 12) ink++;
    return ink / (d.length / 4);
  }, png.toString('base64'));
}

// Share of pixels that differ between a and b, plus the mean row of those pixels and
// of the non-background pixels of a (0 = top, 1 = bottom).
async function diffStats(page, a, b) {
  return page.evaluate(async (a64, b64) => {
    const load = async s => {
      const bmp = await createImageBitmap(await (await fetch(`data:image/png;base64,${s}`)).blob());
      const c = new OffscreenCanvas(bmp.width, bmp.height).getContext('2d');
      c.drawImage(bmp, 0, 0);
      return {w: bmp.width, h: bmp.height, d: c.getImageData(0, 0, bmp.width, bmp.height).data};
    };
    const [x, y] = [await load(a64), await load(b64)];
    const bg = getComputedStyle(document.body).backgroundColor.match(/\d+/g).map(Number);
    let diff = 0, diffRows = 0, ink = 0, inkRows = 0;
    for (let i = 0; i < x.d.length; i += 4) {
      const row = Math.floor(i / 4 / x.w) / x.h;
      if (Math.abs(x.d[i] - y.d[i]) > 8 || Math.abs(x.d[i + 1] - y.d[i + 1]) > 8 || Math.abs(x.d[i + 2] - y.d[i + 2]) > 8) {
        diff++; diffRows += row;
      }
      if (Math.abs(x.d[i] - bg[0]) > 12 || Math.abs(x.d[i + 1] - bg[1]) > 12 || Math.abs(x.d[i + 2] - bg[2]) > 12) {
        ink++; inkRows += row;
      }
    }
    const n = x.d.length / 4;
    return {diff: diff / n, diffRow: diff ? diffRows / diff : 0, inkRow: ink ? inkRows / ink : 0};
  }, a.toString('base64'), b.toString('base64'));
}

// Share of the shadow pixels (a with shadow, b without) that lie outside every
// box expanded by margin. Boxes and margin are in CSS pixels; scale maps them to
// screenshot pixels.
async function strayShadow(page, a, b, boxes, margin, scale) {
  return page.evaluate(async (a64, b64, boxes, margin, scale) => {
    const load = async s => {
      const bmp = await createImageBitmap(await (await fetch(`data:image/png;base64,${s}`)).blob());
      const c = new OffscreenCanvas(bmp.width, bmp.height).getContext('2d');
      c.drawImage(bmp, 0, 0);
      return {w: bmp.width, d: c.getImageData(0, 0, bmp.width, bmp.height).data};
    };
    const [x, y] = [await load(a64), await load(b64)];
    const near = boxes.map(q => [(q.x0 - margin) * scale, (q.y0 - margin) * scale, (q.x1 + margin) * scale, (q.y1 + margin) * scale]);
    let shadow = 0, stray = 0;
    for (let i = 0; i < x.d.length; i += 4) {
      if (Math.abs(x.d[i] - y.d[i]) <= 8 && Math.abs(x.d[i + 1] - y.d[i + 1]) <= 8 && Math.abs(x.d[i + 2] - y.d[i + 2]) <= 8) continue;
      shadow++;
      const px = (i / 4) % x.w, py = Math.floor(i / 4 / x.w);
      if (!near.some(([x0, y0, x1, y1]) => px >= x0 && px <= x1 && py >= y0 && py <= y1)) stray++;
    }
    return {shadow, stray: shadow ? stray / shadow : 0};
  }, a.toString('base64'), b.toString('base64'), boxes, margin, scale);
}

// Shadow pixels sit next to the parts that cast them.
async function checkShadowNearParts(page, label, viewport, state) {
  const {width, height, boxes} = await page.evaluate(() => window.sketchcadViewer.partScreenBoxes());
  const style = await hideOverlays(page);
  await settle(page);
  const withShadow = await page.screenshot();
  await page.evaluate(() => window.sketchcadViewer.setShadowVisible(false));
  await settle(page);
  const noShadow = await page.screenshot();
  await page.evaluate(() => window.sketchcadViewer.setShadowVisible(true));
  await settle(page);
  await style.evaluate(el => el.remove());
  const r = await strayShadow(page, withShadow, noShadow, boxes, SHADOW_MARGIN * Math.min(width, height),
    viewport.deviceScaleFactor || 1);
  check(r.stray <= MAX_STRAY_SHADOW,
    `${label}: ${state} shadows stay by their parts (${(r.stray * 100).toFixed(1)}% of ${r.shadow} shadow pixels stray)`);
}

// The model fills the frame and sits in its middle.
async function checkFraming(page, label, state) {
  const {width, height, boxes} = await page.evaluate(() => window.sketchcadViewer.partScreenBoxes());
  const x0 = Math.min(...boxes.map(b => b.x0)), x1 = Math.max(...boxes.map(b => b.x1));
  const y0 = Math.min(...boxes.map(b => b.y0)), y1 = Math.max(...boxes.map(b => b.y1));
  const fill = Math.max((x1 - x0) / width, (y1 - y0) / height);
  const dx = Math.abs((x0 + x1) / 2 - width / 2) / width, dy = Math.abs((y0 + y1) / 2 - height / 2) / height;
  check(fill >= MIN_FILL && dx <= MAX_CENTER_OFFSET && dy <= MAX_CENTER_OFFSET,
    `${label}: ${state} framing fills ${(fill * 100).toFixed(0)}% of the limiting side, ` +
    `center off by ${(dx * 100).toFixed(1)}% / ${(dy * 100).toFixed(1)}%`);
}

async function diffRatio(page, a, b) {
  return page.evaluate(async (a64, b64) => {
    const load = async s => {
      const bmp = await createImageBitmap(await (await fetch(`data:image/png;base64,${s}`)).blob());
      const c = new OffscreenCanvas(bmp.width, bmp.height).getContext('2d');
      c.drawImage(bmp, 0, 0);
      return c.getImageData(0, 0, bmp.width, bmp.height).data;
    };
    const [x, y] = [await load(a64), await load(b64)];
    if (x.length !== y.length) return 1;
    let diff = 0;
    for (let i = 0; i < x.length; i += 4)
      if (Math.abs(x[i] - y[i]) > 8 || Math.abs(x[i + 1] - y[i + 1]) > 8 || Math.abs(x[i + 2] - y[i + 2]) > 8) diff++;
    return diff / (x.length / 4);
  }, a.toString('base64'), b.toString('base64'));
}

async function openPage(browser, url, viewport, {reducedMotion = false} = {}) {
  const page = await browser.newPage();
  const errors = [], requests = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push(String(e)));
  page.on('request', r => requests.push(r.url()));
  await page.setViewport(viewport);
  if (reducedMotion) await page.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: 'reduce'}]);
  await page.goto(url, {waitUntil: 'load'});
  await page.waitForFunction(() => ['ready', 'error'].includes(window.sketchcadViewer?.status), {timeout: 30000});
  const state = await page.evaluate(() => ({...window.sketchcadViewer}));
  return {page, errors, requests, state};
}

const settle = page => page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));

async function scrollTo(page, p) {
  await page.evaluate(p => {
    const max = document.documentElement.scrollHeight - innerHeight;
    window.scrollTo(0, Math.round(p * max));
  }, p);
  await page.waitForFunction(p => Math.abs(window.sketchcadViewer.renderedProgress - p) < 0.002, {timeout: 10000}, p);
  await settle(page);
  await new Promise(r => setTimeout(r, CSS_TRANSITION_MS));
}

const hideOverlays = page => page.addStyleTag({content: '#sketch,#sheet,#stages,#hud,#title,#hint{visibility:hidden!important}'});

// At the flat-wire moment the flattened model lies on the printed top view of the
// floor sheet: their screen boxes share the near and far edges (within TOPVIEW_PX)
// and the wire stays inside the print sideways (perspective widens the print's
// box at its near corners, which a round footprint does not reach).
async function checkTopViewAlignment(page, label) {
  const r = await page.evaluate(() => {
    const v = window.sketchcadViewer;
    const print = v.topViewScreenBox?.();
    if (!print) return null;
    const {boxes} = v.partScreenBoxes();
    return {print, wire: {x0: Math.min(...boxes.map(b => b.x0)), y0: Math.min(...boxes.map(b => b.y0)),
      x1: Math.max(...boxes.map(b => b.x1)), y1: Math.max(...boxes.map(b => b.y1))}};
  });
  if (!r) { notes.push(`ok   ${label}: no top-view layout on this page (alignment not checked)`); return; }
  const {print: a, wire: b} = r;
  const dy = Math.max(Math.abs(a.y0 - b.y0), Math.abs(a.y1 - b.y1));
  const out = Math.max(a.x0 - b.x0, b.x1 - a.x1, 0);
  check(dy <= TOPVIEW_PX && out <= TOPVIEW_PX,
    `${label}: flat wire lies on the printed top view (edges off by ${dy.toFixed(1)} px, ${out.toFixed(1)} px outside sideways)`);
}

// Assembly story: at the flat moment each part's wire lies on its printed view
// (same criteria as the top view: near/far edges within TOPVIEW_PX, inside sideways).
async function checkItemAlignment(page, label) {
  const r = await page.evaluate(() => window.sketchcadViewer.itemScreenBoxes?.());
  if (!r || !r.length) { check(false, `${label}: no parts-sheet layout on an assembly story`); return; }
  let worst = {dy: 0, out: 0, id: 'none'};
  for (const {id, print: a, part: b} of r) {
    const dy = Math.max(Math.abs(a.y0 - b.y0), Math.abs(a.y1 - b.y1));
    const out = Math.max(a.x0 - b.x0, b.x1 - a.x1, 0);
    if (Math.max(dy, out) > Math.max(worst.dy, worst.out)) worst = {dy, out, id};
  }
  check(worst.dy <= TOPVIEW_PX && worst.out <= TOPVIEW_PX,
    `${label}: every flat part lies on its printed view (${r.length} parts; worst ${worst.id}: edges off by ` +
    `${worst.dy.toFixed(1)} px, ${worst.out.toFixed(1)} px outside sideways)`);
}

// Story cards and stage strip at progress p (0 sketch, checkpoints.sheet, checkpoints.threeD).
async function checkStoryStage(page, label, p, cp, story) {
  const names = story === 'assembly' ? {sheet: 'Parts sheet', threeD: '3D assembly'} : {sheet: 'Drawing', threeD: '3D'};
  const s = await page.evaluate(() => {
    const card = sel => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const inner = el.querySelector('.card');
      const cs = getComputedStyle(el);
      return {opacity: cs.visibility === 'hidden' ? 0 : Number(cs.opacity),
        clipped: getComputedStyle(inner).clipPath !== 'none'};
    };
    const on = document.querySelector('#stages li.on');
    return {sketch: card('#sketch'), sheet: card('#sheet'), stage: on ? on.textContent.trim() : null,
      stages: [...document.querySelectorAll('#stages li')].map(li => li.textContent.trim())};
  });
  const shown = c => c && c.opacity > 0.99 && !c.clipped;
  const gone = c => !c || c.opacity < 0.01;
  if (p === 0) {
    if (s.sketch) {
      check(shown(s.sketch) && (!s.sheet || s.sheet.clipped) && s.stage === 'Sketch',
        `${label}: sketch card shown at 0, sheet not yet (stage ${s.stage}; strip ${s.stages.join(' / ')})`);
    } else {
      check(shown(s.sheet) && s.stage === names.sheet, `${label}: no sketch; sheet shown at 0 (stage ${s.stage})`);
    }
  } else if (p === cp.sheet) {
    if (s.sheet) {
      check(shown(s.sheet) && gone(s.sketch) && s.stage === names.sheet,
        `${label}: ${story === 'assembly' ? 'parts' : 'drawing'} sheet shown at ${p} over a hidden sketch (stage ${s.stage})`);
    }
  } else {
    check(gone(s.sketch) && gone(s.sheet) && s.stage === names.threeD,
      `${label}: cards gone at ${p}, stage ${names.threeD} (stage ${s.stage}; strip ${s.stages.join(' / ')})`);
  }
}

function checkPageHygiene(label, fileUrl, {errors, requests}) {
  const foreign = requests.filter(u => u !== fileUrl && !u.startsWith('data:') && !u.startsWith('blob:'));
  check(foreign.length === 0, `${label}: no requests besides the file itself (${foreign.join(', ') || 'none'})`);
  check(errors.length === 0, `${label}: no console errors (${errors.join(' | ') || 'none'})`);
}

async function checkScroll(browser, fileUrl, viewport, dir) {
  const label = `${viewport.name} scroll`;
  const {page, errors, requests, state} = await openPage(browser, fileUrl, viewport);
  check(state.status === 'ready', `${label}: viewer ready (${state.status}${state.error ? `: ${state.error}` : ''})`);
  if (state.status !== 'ready') return page.close();
  check(state.alignmentMm < ALIGN_MAX_MM, `${label}: wire and solid aligned (${state.alignmentMm.toFixed(4)} mm)`);

  const shots = {};
  const {story, checkpoints: cp} = state;
  const points = [...new Set([0, cp.sheet, cp.flat, cp.threeD, ...(cp.mid ? [cp.mid] : []), 0.7, 1])].sort((a, b) => a - b);
  for (const p of points) {
    await scrollTo(page, p);
    const png = await page.screenshot();
    fs.writeFileSync(path.join(dir, `${viewport.name}-p${String(p).replace('.', '')}.png`), png);
    shots[p] = png;
    const ink = await inkRatio(page, png);
    check(ink > MIN_INK, `${label}: frame at ${p} is not empty (${(ink * 100).toFixed(2)}% ink)`);
    if ([0, cp.sheet, cp.threeD].includes(p)) await checkStoryStage(page, label, p, cp, story);
    if (p === cp.flat) await (story === 'assembly' ? checkItemAlignment(page, label) : checkTopViewAlignment(page, label));
    if (story === 'assembly' && (p === cp.mid || p === 1)) {
      const seated = await page.evaluate(() => window.sketchcadViewer.seated);
      const n = state.partCount;
      check(p === 1 ? seated === n : seated > 0 && seated < n,
        `${label}: ${p === 1 ? 'every part seated at 1' : `parts arrive one by one (${p})`} (${seated} of ${n} seated)`);
    }
  }
  await scrollTo(page, 0);
  const rewound = await page.screenshot();
  fs.writeFileSync(path.join(dir, `${viewport.name}-p0-rewound.png`), rewound);
  const diff = await diffRatio(page, shots[0], rewound);
  check(diff < REWIND_MAX_DIFF, `${label}: rewind 1 -> 0 matches the first frame (${(diff * 100).toFixed(3)}% differ)`);

  // canvas alone (overlays hidden) must show the model mid-story and at the end
  const style = await hideOverlays(page);
  for (const p of [0.5, 1]) {
    await scrollTo(page, p);
    const ink = await inkRatio(page, await page.screenshot());
    check(ink > MIN_INK, `${label}: canvas pixels at ${p} are not empty (${(ink * 100).toFixed(2)}% ink)`);
  }
  // ground shadow: the pixels it darkens exist and lie lower on screen than the model
  const hook = await page.evaluate(() => typeof window.sketchcadViewer.setShadowVisible === 'function');
  check(hook, `${label}: viewer exposes setShadowVisible for the shadow check`);
  if (hook) {
    const withShadow = await page.screenshot();
    await page.evaluate(() => window.sketchcadViewer.setShadowVisible(false));
    await settle(page);
    const noShadow = await page.screenshot();
    await page.evaluate(() => window.sketchcadViewer.setShadowVisible(true));
    await settle(page);
    const shadow = await diffStats(page, withShadow, noShadow);
    check(shadow.diff > MIN_SHADOW && shadow.diffRow > shadow.inkRow,
      `${label}: ground shadow below the model at 1 (${(shadow.diff * 100).toFixed(2)}% shadow pixels, ` +
      `rows ${shadow.diffRow.toFixed(3)} vs model ${shadow.inkRow.toFixed(3)})`);
  }
  await style.evaluate(el => el.remove());
  await checkFraming(page, label, 'assembled');

  // at the end the model can be turned by dragging
  const interactive = await page.evaluate(() => window.sketchcadViewer.interactive);
  check(interactive, `${label}: orbit controls enabled at progress 1`);
  if (!viewport.hasTouch) {
    const before = await page.screenshot();
    const cx = viewport.width / 2, cy = viewport.height / 2;
    await page.mouse.move(cx, cy);
    await page.mouse.down();
    await page.mouse.move(cx + 160, cy + 20, {steps: 8});
    await page.mouse.up();
    await settle(page);
    const after = await page.screenshot();
    fs.writeFileSync(path.join(dir, `${viewport.name}-p1-dragged.png`), after);
    const moved = await diffRatio(page, before, after);
    check(moved > 0.002, `${label}: dragging turns the model (${(moved * 100).toFixed(2)}% changed)`);
  }
  // the exploded layout is laid out for the default view: leave and re-enter the end
  // so the camera returns there after the drag
  await scrollTo(page, 0.98);
  await scrollTo(page, 1);
  await checkExplodeToggle(page, label, viewport, dir);
  checkPageHygiene(label, fileUrl, {errors, requests});
  await page.close();
}

const explodeValue = page => page.evaluate(() => window.sketchcadViewer.explode);
const explodeVisible = page => page.evaluate(() => {
  const b = document.querySelector('#explode');
  return !!b && getComputedStyle(b).display !== 'none' && Number(getComputedStyle(b).opacity) > 0.5
    && getComputedStyle(b).pointerEvents !== 'none';
});

// Press the explode toggle: mouse click on desktop, tap on touch, or keyboard Enter.
async function pressExplode(page, how) {
  if (how === 'keyboard') {
    await page.focus('#explode');
    await page.keyboard.press('Enter');
  } else if (how === 'tap') {
    await page.tap('#explode');
  } else {
    await page.click('#explode');
  }
}

async function waitExplode(page, value) {
  await page.waitForFunction(v => window.sketchcadViewer.explode === v, {timeout: 5000}, value);
  await settle(page);
  await new Promise(r => setTimeout(r, CSS_TRANSITION_MS));
}

async function checkExplodeToggle(page, label, viewport, dir) {
  const parts = await page.evaluate(() => window.sketchcadViewer.partCount);
  const visible = await explodeVisible(page);
  if (parts < 2) {
    check(!visible, `${label}: no explode toggle for a single part`);
    return;
  }
  check(visible, `${label}: explode toggle shown at the end`);
  if (!visible) return;
  const names = await page.$eval('#explode', b => [b.textContent.trim(), b.getAttribute('aria-label')]);
  check(names[0] === 'Explode' && /explode/i.test(names[1]), `${label}: toggle labelled (${names.join(' / ')})`);
  await settle(page);
  await new Promise(r => setTimeout(r, CSS_TRANSITION_MS));
  const before = await page.screenshot();
  await pressExplode(page, viewport.hasTouch ? 'tap' : 'mouse');
  await waitExplode(page, 1);
  const exploded = await page.screenshot();
  fs.writeFileSync(path.join(dir, `${viewport.name}-exploded.png`), exploded);
  const changed = await diffRatio(page, before, exploded);
  const after = await page.$eval('#explode', b => [b.textContent.trim(), b.getAttribute('aria-label')]);
  check(changed > MIN_EXPLODE_DIFF && after[0] === 'Assemble' && /assemble/i.test(after[1]),
    `${label}: explode toggle changes the frame (${(changed * 100).toFixed(2)}% changed, now ${after.join(' / ')})`);
  await checkExplodedLayout(page, label);
  await checkFraming(page, label, 'exploded');
  await checkShadowNearParts(page, label, viewport, 'exploded');
  await pressExplode(page, 'keyboard');
  await waitExplode(page, 0);
  await page.$eval('#explode', b => b.blur()); // drop the keyboard focus ring before comparing
  await settle(page);
  await new Promise(r => setTimeout(r, CSS_TRANSITION_MS));
  const back = await diffRatio(page, before, await page.screenshot());
  check(back < REWIND_MAX_DIFF, `${label}: toggling back (keyboard) restores the frame (${(back * 100).toFixed(3)}% differ)`);
}

// Exploded layout reads like an assembly diagram: every part on screen, none hidden
// behind another. Boxes are the parts' projected mesh vertices in canvas pixels.
async function checkExplodedLayout(page, label) {
  const hook = await page.evaluate(() => typeof window.sketchcadViewer.partScreenBoxes === 'function');
  check(hook, `${label}: viewer exposes partScreenBoxes for the exploded layout check`);
  if (!hook) return;
  const {width, height, boxes} = await page.evaluate(() => window.sketchcadViewer.partScreenBoxes());
  const outside = boxes.filter(b => b.x0 < 0 || b.y0 < 0 || b.x1 > width || b.y1 > height).map(b => b.id);
  check(outside.length === 0, `${label}: every exploded part inside the canvas (${outside.join(', ') || 'all inside'})`);
  const area = b => Math.max(0, b.x1 - b.x0) * Math.max(0, b.y1 - b.y0);
  let worst = {share: 0, pair: 'none'};
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const [a, b] = [boxes[i], boxes[j]];
    const common = area({x0: Math.max(a.x0, b.x0), y0: Math.max(a.y0, b.y0), x1: Math.min(a.x1, b.x1), y1: Math.min(a.y1, b.y1)});
    const share = common / Math.max(1e-9, Math.min(area(a), area(b)));
    if (share > worst.share) worst = {share, pair: `${a.id} / ${b.id}`};
  }
  check(worst.share <= MAX_EXPLODE_OVERLAP,
    `${label}: exploded parts do not hide each other (worst overlap ${(worst.share * 100).toFixed(1)}%: ${worst.pair})`);
}

async function checkAutoplay(browser, fileUrl, viewport, dir) {
  const label = `${viewport.name} autoplay`;
  const url = `${fileUrl}?autoplay=1`;
  const {page, errors, requests, state} = await openPage(browser, url, viewport);
  check(state.status === 'ready' && state.mode === 'autoplay', `${label}: ready in autoplay mode`);
  const wait = state.autoplayMs + AUTOPLAY_SLACK_MS;
  await new Promise(r => setTimeout(r, wait));
  await settle(page);
  const end = await page.evaluate(() => ({...window.sketchcadViewer}));
  check(end.progress === 1 && end.renderedProgress === 1 && end.interactive,
    `${label}: final state after ${wait / 1000} s (progress ${end.progress}, rendered ${end.renderedProgress})`);
  fs.writeFileSync(path.join(dir, `${viewport.name}-autoplay-end.png`), await page.screenshot());
  checkPageHygiene(label, url, {errors, requests});
  await page.close();
}

async function checkReducedMotion(browser, fileUrl, viewport, dir) {
  const label = `${viewport.name} reduced motion`;
  const {page, errors, requests, state} = await openPage(browser, fileUrl, viewport, {reducedMotion: true});
  await settle(page);
  const end = await page.evaluate(() => ({...window.sketchcadViewer}));
  check(state.mode === 'still' && end.renderedProgress === 1, `${label}: final state without motion (${end.mode}, ${end.renderedProgress})`);
  fs.writeFileSync(path.join(dir, `${viewport.name}-reduced.png`), await page.screenshot());
  checkPageHygiene(label, fileUrl, {errors, requests});
  await page.close();
}

(async () => {
  const file = process.argv[2];
  if (!file || !fs.existsSync(file)) {
    console.error('usage: node tools/check-viewer.cjs OUT/viewer/index.html');
    process.exit(2);
  }
  const fileUrl = pathToFileURL(path.resolve(file)).href;
  const dir = shotsDir(process.argv[3]);
  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  });
  try {
    for (const viewport of VIEWPORTS) {
      await checkScroll(browser, fileUrl, viewport, dir);
      await checkAutoplay(browser, fileUrl, viewport, dir);
      await checkReducedMotion(browser, fileUrl, viewport, dir);
    }
  } finally {
    await browser.close();
  }
  for (const line of [...notes, ...failures]) console.log(line);
  console.log(`screenshots: ${dir}`);
  console.log(failures.length ? `FAIL (${failures.length})` : 'PASS');
  process.exit(failures.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
