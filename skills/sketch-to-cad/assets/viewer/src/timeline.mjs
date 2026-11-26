// Progress (0..1) -> scene state. Pure and deterministic, so scrubbing back to a
// progress value always reproduces the same frame (rewind support).
//
//   1 sketch  -> 2 drawing sheet (wipes in over the sketch)
//   -> lies down onto a sheet plane on the scene's floor, its printed top view right
//   under the assembled model -> 3 the wire guides draw in flat on it (the top view
//   itself) and rise in place into 3D -> parts turn solid (the sheet fades) -> axis spin
export const clamp01 = v => Math.min(1, Math.max(0, v));
export const smooth = v => { v = clamp01(v); return v * v * (3 - 2 * v); };
const span = (p, start, end) => clamp01((p - start) / (end - start));

export const TIMING = Object.freeze({
  sketchHold: 0.09,    // source sketch alone until here
  sheetInEnd: 0.17,    // drawing sheet has wiped in over the sketch
  sheetHold: 0.27,     // sheet card fully shown until here, then it lies down ...
  sheetLaid: 0.35,     // ... onto the sheet plane on the floor of the 3D scene
  cardSwap: 0.025,
  washEnd: 0.4,        // the laid sheet's print pales (from sheetLaid) so the wires on it stand out     // the plane fades in under the card, then the card fades out, over this span
  wireStart: 0.35,     // wire guides draw in, flat on the sheet (the parts' plan view) ...
  wireEnd: 0.46,       // ... staggered part by part, overlapping
  wireDraw: 0.45,      // share of the wire window one part spends drawing (multi-part)
  flatCheck: 0.465,    // every wire drawn and still flat (tools/check-viewer.cjs aligns it with the print)
  liftStart: 0.47,     // the flat wires rise off the sheet into their 3D height
  liftEnd: 0.59,
  planeFadeStart: 0.58, // the floor sheet fades as the parts turn solid
  planeFadeEnd: 0.66,
  sheetViewEnd: 0.7,   // camera eases from the sheet framing to the story framing (from liftEnd)
  solidStart: 0.58,    // parts turn solid one after another
  solidEnd: 0.76,
  solidFade: 0.05,     // shortest crossfade of one part from wire to solid
  spinStart: 0.75,     // axis spin, once the parts are solid
  spinTurns: 1,
  settleStart: 0.95,   // camera eases from the spin framing to the final framing
  edgeRest: 0.3,       // BRep edges stay faintly on the finished solid
  bareLead: 0.04,      // no sketch and no sheet: the 3D story starts this far before wireStart
  liftFloor: 0.002,    // flattened height share (not 0: keeps the transform invertible)
});

// Window [start, end) of item i of n inside [from, to], each `duration` long, evenly staggered.
function stagger(i, n, from, to, duration) {
  duration = Math.min(duration, to - from);
  const start = n > 1 ? from + (to - from - duration) * i / (n - 1) : from;
  return [start, start + duration];
}

// Stage shown at progress p: 1 sketch, 2 drawing, 3 3D (index into the stage strip).
function stageAt(p, t, sketch, sheet) {
  if (p >= t.sheetLaid || (!sketch && !sheet)) return 3;
  if (sketch && (!sheet || p < (t.sketchHold + t.sheetInEnd) / 2)) return 1;
  return 2;
}

// cards: which intro cards exist ({sketch, sheet}); a missing card skips its stage.
// Without either card the 3D story is stretched over the whole progress range.
export function timelineState(progress, partCount, cards = {sketch: false, sheet: true}, t = TIMING) {
  const p = clamp01(progress);
  const hasSketch = !!cards.sketch, hasSheet = !!cards.sheet;
  // q: progress on the 3D story's clock (equals p whenever an intro card exists)
  const lead = t.wireStart - t.bareLead;
  const q = hasSketch || hasSheet ? p : lead + p * (1 - lead);
  const wireLen = partCount > 1 ? (t.wireEnd - t.wireStart) * t.wireDraw : t.wireEnd - t.wireStart;
  const solidLen = Math.max(t.solidFade, (t.solidEnd - t.solidStart) / partCount);
  const parts = [];
  for (let i = 0; i < partCount; i++) {
    const [start, drawEnd] = stagger(i, partCount, t.wireStart, t.wireEnd, wireLen);
    const [solidFrom, solidTo] = stagger(i, partCount, t.solidStart, t.solidEnd, solidLen);
    const solid = smooth(span(q, solidFrom, solidTo));
    parts.push({visible: q > start, reveal: smooth(span(q, start, drawEnd)), solidOpacity: solid,
      edgeOpacity: 1 - solid * (1 - t.edgeRest), sectionOpacity: 1 - solid});
  }
  // lift: model height share, the wires rise off the floor (where the sheet lay down)
  const lift = t.liftFloor + (1 - t.liftFloor) * smooth(span(q, t.liftStart, t.liftEnd));
  // sheet card: wipes in over the sketch (sheetIn), lies down onto the floor plane
  // (sheetLie, main.mjs maps its corners onto the plane's), then hands over to the plane
  const sheetIn = hasSketch ? smooth(span(p, t.sketchHold, t.sheetInEnd)) : 1;
  const sheetLie = hasSheet ? smooth(span(p, t.sheetHold, t.sheetLaid)) : 0;
  const sheet = hasSheet ? 1 - span(p, t.sheetLaid, t.sheetLaid + t.cardSwap) : 0;
  const plane = hasSheet ? span(p, t.sheetLaid - t.cardSwap, t.sheetLaid) * (1 - smooth(span(q, t.planeFadeStart, t.planeFadeEnd))) : 0;
  // the sketch stays under the sheet while it wipes in, then recedes; without a
  // sheet it fades as the 3D story starts
  const sketchAfterSheet = 1 - smooth(span(p, (t.sketchHold + t.sheetInEnd) / 2, t.sheetInEnd + 0.03));
  const sketchAlone = 1 - smooth(span(p, t.sheetHold, t.wireStart + 0.02));
  const sketch = hasSketch ? (hasSheet ? sketchAfterSheet : sketchAlone) : 0;
  const planeWash = hasSheet ? smooth(span(p, t.sheetLaid, t.washEnd)) : 0;
  // camera: sheet framing (sheet, flat and risen layout in view) until the wires are up
  const sheetView = 1 - smooth(span(q, t.liftEnd, t.sheetViewEnd));
  // camera zooms from the whole sheet to its top view while the wires draw in
  const sheetZoom = smooth(span(q, t.wireStart, t.wireEnd));
  // the model rises in its assembled place (the end-state explode toggle is separate)
  const gather = 1;
  const spin = smooth(span(q, t.spinStart, 1)) * t.spinTurns * Math.PI * 2;
  const settle = smooth(span(q, t.settleStart, 1));
  return {progress: p, sketch, sheet, sheetIn, sheetLie, plane, planeWash, sheetView, sheetZoom, stage: stageAt(p, t, hasSketch, hasSheet),
    parts, lift, gather, spin, settle, done: p >= 1};
}

// ---- assembly story (two or more parts with a parts sheet) ---------------------
//
//   1 sketch -> 2 parts sheet (wipes in, lies down on the floor; the assembly will
//   stand over its table) -> each part's wire draws in flat on its own printed view
//   -> 3 the wires rise in place into 3D -> one by one, in assembly order, each part
//   flies to its place, the last stretch along its explode direction, and turns
//   solid as it seats -> the sheet fades -> axis spin
export const ASSEMBLY_TIMING = Object.freeze({
  sketchHold: 0.06,
  sheetInEnd: 0.13,
  sheetHold: 0.24,
  sheetLaid: 0.31,
  cardSwap: 0.025,
  wireStart: 0.31,     // every part's wire draws in flat on its printed view ...
  wireEnd: 0.39,       // ... staggered in assembly order, overlapping
  wireDraw: 0.6,       // share of the wire window one part spends drawing
  flatCheck: 0.395,    // all drawn and still flat (tools/check-viewer.cjs aligns each with its print)
  zoomInStart: 0.31,   // portrait canvases: the camera closes in on the drawings ...
  zoomInEnd: 0.37,
  zoomOutStart: 0.45,  // ... and backs out again as the flights begin (landscape: no-op)
  zoomOutEnd: 0.56,
  riseStart: 0.4,      // the flat wires rise in place to their full height
  riseEnd: 0.47,
  washStart: 0.4,      // the table under where the assembly stands pales
  washEnd: 0.47,
  flyStart: 0.45,      // flights, staggered in assembly order ...
  flyEnd: 0.86,
  flyLen: 0.13,        // ... each this long (travel, then the approach)
  flyStep: 0.08,       // at most this between two flights' starts (few parts do not idle)
  approach: 0.35,      // share of a flight spent on the final approach along the explode direction
  planeFadeStart: 0.84,
  planeFadeEnd: 0.9,
  sheetViewStart: 0.62, // camera eases from the sheet framing to the story framing
  sheetViewEnd: 0.88,
  spinStart: 0.87,
  spinTurns: 1,
  settleStart: 0.96,
  edgeRest: 0.3,
  liftFloor: 0.002,
  midFlight: 0.62,     // a moment with parts in the air (README preview, check screenshots)
});

// order[i]: rank of part i in the assembly order (0 = the base). Pure in progress.
export function assemblyState(progress, order, cards = {sketch: false, sheet: true}, t = ASSEMBLY_TIMING) {
  const p = clamp01(progress);
  const n = order.length;
  const hasSketch = !!cards.sketch;
  const wireLen = n > 1 ? (t.wireEnd - t.wireStart) * t.wireDraw : t.wireEnd - t.wireStart;
  const rise = t.liftFloor + (1 - t.liftFloor) * smooth(span(p, t.riseStart, t.riseEnd));
  const flyEnd = Math.min(t.flyEnd, t.flyStart + t.flyLen + Math.max(0, n - 1) * t.flyStep);
  const parts = order.map(rank => {
    const [start, drawEnd] = stagger(rank, n, t.wireStart, t.wireEnd, wireLen);
    const [f0, f1] = stagger(rank, n, t.flyStart, flyEnd, t.flyLen);
    const fly = span(p, f0, f1);
    const solid = smooth(span(fly, 1 - t.approach, 1));
    // travel: to a point short of the seat; seat: the approach along the explode direction
    return {visible: p > start, reveal: smooth(span(p, start, drawEnd)), rise, fly,
      travel: span(fly, 0, 1 - t.approach), seat: span(fly, 1 - t.approach, 1), solidOpacity: solid,
      edgeOpacity: 1 - solid * (1 - t.edgeRest), sectionOpacity: 1 - solid};
  });
  const sheetIn = hasSketch ? smooth(span(p, t.sketchHold, t.sheetInEnd)) : 1;
  const sheetLie = smooth(span(p, t.sheetHold, t.sheetLaid));
  const sheet = 1 - span(p, t.sheetLaid, t.sheetLaid + t.cardSwap);
  const plane = span(p, t.sheetLaid - t.cardSwap, t.sheetLaid) * (1 - smooth(span(p, t.planeFadeStart, t.planeFadeEnd)));
  const sketch = hasSketch ? 1 - smooth(span(p, (t.sketchHold + t.sheetInEnd) / 2, t.sheetInEnd + 0.03)) : 0;
  let stage = 3;
  if (p < t.riseStart) stage = hasSketch && p < (t.sketchHold + t.sheetInEnd) / 2 ? 1 : 2;
  return {progress: p, story: 'assembly', sketch, sheet, sheetIn, sheetLie, plane,
    planeWash: smooth(span(p, t.washStart, t.washEnd)),
    sheetView: 1 - smooth(span(p, t.sheetViewStart, t.sheetViewEnd)),
    sheetZoom: smooth(span(p, t.zoomInStart, t.zoomInEnd)) * (1 - smooth(span(p, t.zoomOutStart, t.zoomOutEnd))), stage, parts,
    lift: 1, gather: 1, spin: smooth(span(p, t.spinStart, 1)) * t.spinTurns * Math.PI * 2,
    settle: smooth(span(p, t.settleStart, 1)), done: p >= 1};
}

// Assembly order from the explode contact tree (parents[i]: index of part i's parent,
// -1 for the root): root first, depth-first, siblings in part order. Parts the tree
// does not reach follow in part order. Returns rank per part.
export function assemblyOrder(parents) {
  const n = parents.length, children = parents.map(() => []), seq = [], seen = new Set();
  parents.forEach((q, i) => { if (q >= 0 && q < n) children[q].push(i); });
  const visit = i => {
    if (seen.has(i)) return;
    seen.add(i); seq.push(i);
    children[i].forEach(visit);
  };
  parents.forEach((q, i) => { if (!(q >= 0 && q < n)) visit(i); });
  for (let i = 0; i < n; i++) visit(i);
  const rank = new Array(n);
  seq.forEach((i, r) => { rank[i] = r; });
  return rank;
}
