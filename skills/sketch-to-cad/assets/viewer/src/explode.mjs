// Exploded-assembly layout: where each part sits when the assembly is pulled apart,
// read like a technical exploded illustration. Pure and computed once from the
// placed parts, so the timeline stays deterministic.
//
//   1. Shape: every part's surface is sampled evenly (by area). From the samples and
//      the triangle normals it gets an oriented center and, when it has one, a
//      symmetry axis: the odd eigenvector of the point spread or of the normal
//      spread (a revolved part, a rod, a disk, a square bar, a plate).
//   2. Hierarchy: parts whose surfaces come within a small tolerance touch. The
//      base (largest footprint on the floor) is the root; every other part hangs
//      under the largest touching part one contact step closer to the base. So a
//      sub-assembly (the mount on the pier, the tube on the mount) moves as a unit
//      and its own parts spread out relative to it.
//   3. Direction of a part relative to its parent, first rule that applies:
//        coaxial with the parent (on its axis line)      -> slide along that axis
//        a rod whose axis runs through the parent        -> slide along its own axis
//                                                           (a shaft, a screw, a pin)
//        sitting on the parent's end/face along its axis -> lift along that axis
//        a disk or plate                                 -> lift along its normal
//        otherwise                                       -> away from the parent center
//      always away from the parent (then from the assembly center, then up).
//   4. Travel: depth-first from the base, nearer children first, each part moves
//      (on top of its parent's offset) the shortest distance, at least the previous
//      sibling's on the same line, that clears every part already laid out: their
//      boxes in 3D by a gap scaled to the part, and their outlines on screen in the
//      default view.
// Everything is in the glTF frame (Y up). Lengths are shares of the assembly diagonal.
import {Vector3} from 'three';

const AXES = ['x', 'y', 'z'];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const norm = a => Math.hypot(a[0], a[1], a[2]);
const unit = a => { const l = norm(a); return l > 1e-12 ? scale(a, 1 / l) : [0, 1, 0]; };
const frac = v => v - Math.floor(v);

// ---- shape ----------------------------------------------------------------------

// Eigen decomposition of a symmetric 3x3 (Jacobi). Returns [{value, vector}] sorted
// by value, largest first.
export function eigen3(m) {
  const a = m.map(r => r.slice()), v = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  for (let sweep = 0; sweep < 32; sweep++) {
    let off = 0;
    for (let p = 0; p < 3; p++) for (let q = p + 1; q < 3; q++) off += a[p][q] ** 2;
    if (off < 1e-30) break;
    for (let p = 0; p < 3; p++) for (let q = p + 1; q < 3; q++) {
      if (Math.abs(a[p][q]) < 1e-30) continue;
      const theta = (a[q][q] - a[p][p]) / (2 * a[p][q]);
      const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
      const c = 1 / Math.sqrt(t * t + 1), s = t * c;
      for (let k = 0; k < 3; k++) {
        const akp = a[k][p], akq = a[k][q];
        a[k][p] = c * akp - s * akq; a[k][q] = s * akp + c * akq;
      }
      for (let k = 0; k < 3; k++) {
        const apk = a[p][k], aqk = a[q][k];
        a[p][k] = c * apk - s * aqk; a[q][k] = s * apk + c * aqk;
      }
      for (let k = 0; k < 3; k++) {
        const vkp = v[k][p], vkq = v[k][q];
        v[k][p] = c * vkp - s * vkq; v[k][q] = s * vkp + c * vkq;
      }
    }
  }
  return [0, 1, 2].map(i => ({value: a[i][i], vector: unit([v[0][i], v[1][i], v[2][i]])}))
    .sort((x, y) => y.value - x.value);
}

// The odd one out of three eigenvalues (largest first): {index, strength}, where
// strength in 0..1 says how clearly one value stands apart while the other two match.
function oddAxis(e) {
  const [a, b, c] = e.map(x => Math.max(0, x.value));
  const g1 = a > 0 ? (a - b) / a : 0, g2 = b > 0 ? (b - c) / b : 0;
  return g1 - g2 >= 0 ? {index: 0, strength: g1 - g2} : {index: 2, strength: g2 - g1};
}

// Even surface samples of a triangle soup (flat [x,y,z, x,y,z, x,y,z, ...]) with
// about `spacing` between neighbours (at most maxSamples), deterministic, plus the
// area-weighted normal spread and the total area.
export function surfaceStats(tris, spacing, maxSamples) {
  const n = Math.floor(tris.length / 9), cum = new Float64Array(n);
  const normalCov = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  let area = 0;
  for (let t = 0; t < n; t++) {
    const o = t * 9;
    const e1 = [tris[o + 3] - tris[o], tris[o + 4] - tris[o + 1], tris[o + 5] - tris[o + 2]];
    const e2 = [tris[o + 6] - tris[o], tris[o + 7] - tris[o + 1], tris[o + 8] - tris[o + 2]];
    const c = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]];
    const a2 = norm(c);
    area += a2 / 2;
    cum[t] = area;
    if (a2 > 0) for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) normalCov[i][j] += c[i] * c[j] / a2 / 2;
  }
  const count = Math.max(64, Math.min(maxSamples, Math.ceil(area / (spacing * spacing))));
  const samples = [];
  if (n === 0 || area <= 0) return {samples, normalCov, area, spacing};
  let t = 0;
  for (let k = 0; k < count; k++) {
    const target = (k + 0.5) / count * area;
    while (t < n - 1 && cum[t] < target) t++;
    let r1 = frac(0.5 + k * 0.7548776662466927), r2 = frac(0.5 + k * 0.5698402909980532);
    if (r1 + r2 > 1) { r1 = 1 - r1; r2 = 1 - r2; }
    const o = t * 9;
    samples.push([0, 1, 2].map(i => tris[o + i] + r1 * (tris[o + 3 + i] - tris[o + i]) + r2 * (tris[o + 6 + i] - tris[o + i])));
  }
  return {samples, normalCov, area, spacing: Math.max(spacing, Math.sqrt(area / count))};
}

// Oriented frame of a part from its samples: center, principal directions with
// half-lengths, and a kind with its directions:
//   rod  (long, round or square section)  axis = symmetry axis
//   disk (flat, round or square outline)  axis = normal = symmetry axis
//   bar  (long, no symmetry)              normal = thinnest direction
//   plate (flat, no symmetry)             normal = thinnest direction
//   blob                                  neither
function partShape(stats, axisStrength) {
  const pts = stats.samples, n = pts.length;
  const mean = [0, 1, 2].map(k => pts.reduce((s, p) => s + p[k], 0) / n);
  const cov = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (const p of pts) {
    const d = sub(p, mean);
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) cov[i][j] += d[i] * d[j] / n;
  }
  const pe = eigen3(cov);
  const half = [], center = mean.slice();
  for (const {vector} of pe) {
    let lo = Infinity, hi = -Infinity;
    for (const p of pts) { const s = dot(sub(p, mean), vector); lo = Math.min(lo, s); hi = Math.max(hi, s); }
    half.push((hi - lo) / 2);
    for (let k = 0; k < 3; k++) center[k] += vector[k] * (hi + lo) / 2;
  }
  // a symmetry axis: the odd eigenvector of the point spread or of the normal spread
  const ne = eigen3(stats.normalCov);
  const byPoints = {...oddAxis(pe), e: pe}, byNormals = {...oddAxis(ne), e: ne};
  const best = byPoints.strength >= byNormals.strength ? byPoints : byNormals;
  let axis = null, normal = null, kind = 'blob';
  if (best.strength >= axisStrength) {
    const a = best.e[best.index].vector;
    const along = halfAlong(pts, center, a), radial = radialHalf(pts, center, a);
    // the two other extents match (a round or square section / outline)
    const others = pe.map(x => x.vector).filter(v => Math.abs(dot(v, a)) < 0.8).map(v => halfAlong(pts, center, v));
    const round = others.length === 2 && Math.min(...others) >= 0.6 * Math.max(...others);
    if (round) { axis = a; kind = along >= radial ? 'rod' : 'disk'; if (kind === 'disk') normal = a; }
  }
  if (!axis) {
    if (half[1] < 0.5 * half[0]) kind = 'bar';
    else if (half[2] < 0.5 * half[1]) kind = 'plate';
    if (kind !== 'blob') normal = pe[2].vector;
  }
  return {center, axis, normal, kind, half, samples: pts, spacing: stats.spacing, area: stats.area};
}

// Whether the samples form a bore around the line through origin along d (within
// `along` of origin): none of them closer than `inner`, and those within `outer`
// surround the line (at least 6 of 8 angular sectors).
function surrounds(pts, origin, d, along, inner, outer) {
  const u = unit(Math.abs(d[0]) < 0.9 ? [0, -d[2], d[1]] : [-d[2], 0, d[0]]);
  const w = [d[1] * u[2] - d[2] * u[1], d[2] * u[0] - d[0] * u[2], d[0] * u[1] - d[1] * u[0]];
  const sectors = new Set();
  for (const p of pts) {
    const v = sub(p, origin), s = dot(v, d);
    if (Math.abs(s) > along) continue;
    const r = Math.sqrt(Math.max(0, dot(v, v) - s * s));
    if (r < inner) return false;
    if (r <= outer) sectors.add(Math.floor((Math.atan2(dot(v, w), dot(v, u)) + Math.PI) / (Math.PI / 4)) % 8);
  }
  return sectors.size >= 6;
}

// Median distance of the samples from the line through origin along d.
function medianRadius(pts, origin, d) {
  const r = pts.map(p => { const v = sub(p, origin), s = dot(v, d); return Math.sqrt(Math.max(0, dot(v, v) - s * s)); });
  r.sort((a, b) => a - b);
  return r[Math.floor(r.length / 2)] || 0;
}

function halfAlong(pts, center, d) {
  let m = 0;
  for (const p of pts) m = Math.max(m, Math.abs(dot(sub(p, center), d)));
  return m;
}

function radialHalf(pts, center, d) {
  let m = 0;
  for (const p of pts) { const v = sub(p, center), s = dot(v, d); m = Math.max(m, Math.sqrt(Math.max(0, dot(v, v) - s * s))); }
  return m;
}

// Extent [lo, hi] of the samples along d, relative to `origin`.
function extentAlong(pts, origin, d) {
  let lo = Infinity, hi = -Infinity;
  for (const p of pts) { const s = dot(sub(p, origin), d); lo = Math.min(lo, s); hi = Math.max(hi, s); }
  return [lo, hi];
}

// ---- contact ----------------------------------------------------------------------

// Pairs of parts whose surface samples come within `tol` (boxes checked first).
function contacts(shapes, boxes, tol) {
  const n = shapes.length, touching = shapes.map(() => new Set());
  const grids = shapes.map(s => {
    const cell = Math.max(tol, s.spacing), grid = new Map();
    for (const p of s.samples) {
      const key = p.map(v => Math.floor(v / cell)).join(',');
      if (!grid.has(key)) grid.set(key, []);
      grid.get(key).push(p);
    }
    return {cell, grid};
  });
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    const reach = Math.max(tol, shapes[i].spacing, shapes[j].spacing);
    if (AXES.some(k => boxes[i].min[k] - reach > boxes[j].max[k] || boxes[j].min[k] - reach > boxes[i].max[k])) continue;
    // probe the part with fewer samples against the other's grid
    const [a, b] = shapes[i].samples.length <= shapes[j].samples.length ? [i, j] : [j, i];
    const {cell, grid} = grids[b], r = Math.ceil(reach / cell), r2 = reach * reach;
    let hit = false;
    for (const p of shapes[a].samples) {
      if (AXES.some((k, m) => p[m] < boxes[b].min[k] - reach || p[m] > boxes[b].max[k] + reach)) continue;
      const c = p.map(v => Math.floor(v / cell));
      for (let dx = -r; dx <= r && !hit; dx++) for (let dy = -r; dy <= r && !hit; dy++) for (let dz = -r; dz <= r && !hit; dz++) {
        const bucket = grid.get(`${c[0] + dx},${c[1] + dy},${c[2] + dz}`);
        if (bucket) for (const q of bucket) if ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2 <= r2) { hit = true; break; }
      }
      if (hit) break;
    }
    if (hit) { touching[i].add(j); touching[j].add(i); }
  }
  return touching;
}

// Parent of every part: the largest touching part one contact step closer to the
// root. Parts not connected to the root hang under the nearest connected part.
function hierarchy(shapes, touching, root) {
  const n = shapes.length, hop = new Array(n).fill(Infinity), parent = new Array(n).fill(-1);
  hop[root] = 0;
  const reached = [root];
  const spread = () => {
    for (let q = 0; q < reached.length; q++) {
      const i = reached[q];
      for (const j of [...touching[i]].sort((a, b) => a - b)) if (hop[j] === Infinity) { hop[j] = hop[i] + 1; reached.push(j); }
    }
  };
  spread();
  while (reached.length < n) { // bridge the nearest unreached part to the reached ones
    let best = null;
    for (let j = 0; j < n; j++) if (hop[j] === Infinity) for (const i of reached) {
      const d = norm(sub(shapes[j].center, shapes[i].center));
      if (!best || d < best.d) best = {i, j, d};
    }
    hop[best.j] = hop[best.i] + 1;
    touching[best.j].add(best.i); touching[best.i].add(best.j);
    reached.push(best.j);
    spread();
  }
  for (let j = 0; j < n; j++) {
    if (j === root) continue;
    let best = -1;
    for (const i of touching[j]) if (hop[i] === hop[j] - 1 && (best < 0 || shapes[i].area > shapes[best].area || (shapes[i].area === shapes[best].area && i < best))) best = i;
    parent[j] = best;
  }
  return parent;
}

// ---- travel -------------------------------------------------------------------------

// Open interval of t where [aMin, aMax] + t*d intersects (bMin, bMax), or null.
function slab(aMin, aMax, d, bMin, bMax) {
  if (Math.abs(d) < 1e-12) return aMin < bMax && aMax > bMin ? [-Infinity, Infinity] : null;
  const t0 = (bMin - aMax) / d, t1 = (bMax - aMin) / d;
  return [Math.min(t0, t1), Math.max(t0, t1)];
}

// Interval of t where box a (moving along d) overlaps box b in every dimension.
function blocked(a, b, d) {
  let lo = -Infinity, hi = Infinity;
  for (let k = 0; k < a.min.length; k++) {
    const s = slab(a.min[k], a.max[k], d[k], b.min[k], b.max[k]);
    if (!s) return null;
    lo = Math.max(lo, s[0]);
    hi = Math.min(hi, s[1]);
    if (lo >= hi) return null;
  }
  return [lo, hi];
}

// Smallest t >= start outside every open interval.
function firstFree(start, intervals) {
  let t = start, moved = true;
  while (moved) {
    moved = false;
    for (const [lo, hi] of intervals) if (lo < t && t < hi) { t = hi; moved = true; }
  }
  return t;
}

const analyses = new WeakMap();

/**
 * items: [{box: Box3 (placed), rect: {r0, r1, u0, u1}, tris: flat placed triangles}]
 * where rect is the part's outline projected on the default view's right/up axes
 * (`view.right`, `view.up`). Returns one Vector3 offset per item.
 */
export function explodeLayout(items, opts) {
  const {view, gap, minGap, partGap, minTravel, screenShrink, floorShare, contactTol, maxSamples, axisStrength,
    coaxialCos, coaxialRadial, clampSpan, clampRadius, insideShare, travelStep, screenMargin} = opts;
  const n = items.length;
  if (n < 2) return items.map(() => new Vector3());
  const all = items.reduce((b, it) => b.union(it.box), items[0].box.clone());
  const diagonal = all.getSize(new Vector3()).length();
  const middle = all.getCenter(new Vector3()).toArray();
  const tol = contactTol * diagonal;
  // shapes and hierarchy depend only on the parts: computed once per items array
  if (!analyses.has(items)) {
    const boxes = items.map(it => ({min: it.box.min, max: it.box.max}));
    const shapes = items.map(it => partShape(surfaceStats(it.tris, tol * 0.7, maxSamples), axisStrength));
    // base: largest footprint among the parts on the floor
    let root = 0, rootArea = -1;
    items.forEach((it, i) => {
      const dx = it.box.max.x - it.box.min.x, dz = it.box.max.z - it.box.min.z;
      if (it.box.min.y <= all.min.y + floorShare * diagonal && dx * dz > rootArea) [root, rootArea] = [i, dx * dz];
    });
    analyses.set(items, {shapes, root, parent: hierarchy(shapes, contacts(shapes, boxes, tol), root)});
  }
  const {shapes, root, parent} = analyses.get(items);
  const children = items.map(() => []);
  parent.forEach((p, j) => { if (p >= 0) children[p].push(j); });

  // A long rod held by a short ring or saddle that hugs it (a tube in its cradle,
  // a finder in its brackets) lifts off sideways, away from the holder; null otherwise.
  const clampLift = (c, p, D) => {
    const cr = medianRadius(c.samples, c.center, c.axis), along = halfAlong(c.samples, c.center, c.axis);
    const [lo, hi] = extentAlong(p.samples, p.center, c.axis), clampLen = hi - lo, clampReach = medianRadius(p.samples, c.center, c.axis);
    if (2 * along < clampSpan * clampLen || clampReach > clampRadius * cr) return null;
    const perp = sub(D, scale(c.axis, dot(D, c.axis)));
    return norm(perp) > 0.05 * cr ? unit(perp) : unit(sub([0, 1, 0], scale(c.axis, c.axis[1])));
  };

  // direction of a part relative to its parent (see header)
  const rule = items.map(() => 'base');
  const direction = j => {
    const c = shapes[j], p = shapes[parent[j]], D = sub(c.center, p.center);
    const radialOff = a => { const s = dot(D, a); return Math.sqrt(Math.max(0, dot(D, D) - s * s)); };
    let d = null;
    // coaxial with the parent's symmetry axis
    if (p.axis && (!c.axis || Math.abs(dot(c.axis, p.axis)) >= coaxialCos)) {
      const pr = radialHalf(p.samples, p.center, p.axis), cr = radialHalf(c.samples, c.center, p.axis);
      if (radialOff(p.axis) <= coaxialRadial * Math.max(pr, cr)) {
        const lift = c.kind === 'rod' ? clampLift(c, p, D) : null;
        [d, rule[j]] = lift ? [lift, 'clamped'] : [p.axis, 'coaxial'];
      }
    }
    // a rod running through a bore in the parent: slides out along its axis, unless the
    // parent only clamps it (a ring or saddle around a long tube): then it lifts off
    if (!d && c.kind === 'rod') {
      const cr = medianRadius(c.samples, c.center, c.axis), along = halfAlong(c.samples, c.center, c.axis);
      if (surrounds(p.samples, c.center, c.axis, along, 0.5 * cr, 1.5 * cr + tol)) {
        const lift = clampLift(c, p, D);
        [d, rule[j]] = lift ? [lift, 'clamped'] : [c.axis, 'bore'];
      }
    }
    // sitting on the parent's face (along its normal) or end (along its axis)
    const pn = p.normal || p.axis;
    if (!d && pn && Math.abs(dot(D, pn)) > 0.6 * halfAlong(p.samples, p.center, pn)) [d, rule[j]] = [pn, 'on-face'];
    // a disk, plate or bar lifts off along its normal
    if (!d && c.normal) [d, rule[j]] = [c.normal, 'normal'];
    if (!d) [d, rule[j]] = [norm(D) > 1e-6 * diagonal ? unit(D) : [0, 1, 0], 'radial'];
    // away from the parent. A part inside its parent's span (a shaft through a
    // housing) may leave by either end: it takes the upper one. Centered on the
    // parent: away from the assembly center, else up, else to the right of the view.
    const size = extentAlong(c.samples, c.center, d);
    const eps = 0.05 * Math.max(size[1] - size[0], 1e-6 * diagonal);
    let s = dot(D, d);
    const inside = Math.abs(s) < insideShare * halfAlong(p.samples, p.center, d);
    if (inside && Math.abs(d[1]) > 0.25) s = d[1];
    if (Math.abs(s) <= eps) s = dot(sub(c.center, middle), d);
    if (Math.abs(s) <= eps) s = Math.abs(d[1]) > 1e-3 ? d[1] : dot(d, view.right.toArray());
    return s < 0 ? scale(d, -1) : d;
  };

  // Screen outlines in the default view: orthographic (the exact mesh outline) and,
  // when `eye` is given, in perspective from there (the outline of the part's
  // extreme points in 38 directions). Both must clear: the real camera sits
  // somewhere between them, depending on the canvas shape.
  const right = view.right.toArray(), up = view.up.toArray(), back = view.back.toArray();
  const eye = opts.eye ? opts.eye.toArray() : null;
  const probe = [];
  for (const x of [-1, 0, 1]) for (const y of [-1, 0, 1]) for (const z of [-1, 0, 1]) if (x || y || z) probe.push(unit([x, y, z]));
  for (const v of [right, up]) for (const sv of [-1, 1]) for (const sb of [-1, 0, 1]) probe.push(unit([0, 1, 2].map(k => sv * v[k] + sb * back[k])));
  const extremes = shapes.map(sh => probe.map(d => {
    let best = sh.samples[0], m = -Infinity;
    for (const q of sh.samples) { const v = dot(q, d); if (v > m) [m, best] = [v, q]; }
    return best;
  }));
  const screen = (j, off) => {
    const r = items[j].rect, x = dot(off, right), y = dot(off, up);
    const out = {o: [r.r0 + x, r.u0 + y, r.r1 + x, r.u1 + y], p: null};
    if (eye) {
      const q = [Infinity, Infinity, -Infinity, -Infinity];
      for (const e of extremes[j]) {
        const v = [e[0] + off[0] - eye[0], e[1] + off[1] - eye[1], e[2] + off[2] - eye[2]];
        const depth = Math.max(1e-6 * diagonal, -dot(v, back)), px = dot(v, right) / depth, py = dot(v, up) / depth;
        q[0] = Math.min(q[0], px); q[1] = Math.min(q[1], py); q[2] = Math.max(q[2], px); q[3] = Math.max(q[3], py);
      }
      out.p = q;
    }
    return out;
  };
  // outlines may overlap by screenShrink of the smaller one per side and axis, less
  // a small fixed margin (screenMargin of the diagonal) for projection error
  const eyeDistance = eye ? Math.max(1e-9, dot(sub(eye, middle), back)) : 1;
  const apart2 = (a, b, margin) => {
    const sx = screenShrink * Math.min(a[2] - a[0], b[2] - b[0]) - margin;
    const sy = screenShrink * Math.min(a[3] - a[1], b[3] - b[1]) - margin;
    return a[2] - sx <= b[0] + sx || b[2] - sx <= a[0] + sx || a[3] - sy <= b[1] + sy || b[3] - sy <= a[1] + sy;
  };
  const apart = (a, b) => apart2(a.o, b.o, screenMargin * diagonal) && (!a.p || apart2(a.p, b.p, screenMargin * diagonal / eyeDistance));
  const box3 = (box, off, pad) => ({min: AXES.map((k, m) => box.min[k] + off[m] - pad), max: AXES.map((k, m) => box.max[k] + off[m] + pad)});
  const longest = i => 2 * shapes[i].half[0];
  const offsets = items.map(() => [0, 0, 0]);
  const screens = items.map(() => null);
  screens[root] = screen(root, offsets[root]);
  const placed = [root];
  const step = travelStep * diagonal, limit = 4 * diagonal;

  // shortest travel >= start along d (on top of the parent's offset) clear of every
  // placed part: 3D boxes (analytic) and screen outlines (marched)
  const travel = (j, d, start) => {
    const base = offsets[parent[j]], a3 = box3(items[j].box, base, 0);
    const pad = Math.max(minGap * diagonal, Math.min(gap * diagonal, partGap * longest(j)));
    const intervals = [];
    for (const i of placed) { const iv = blocked(a3, box3(items[i].box, offsets[i], pad), d); if (iv) intervals.push(iv); }
    let t = start;
    for (;;) {
      t = firstFree(t, intervals);
      const at = [0, 1, 2].map(k => base[k] + d[k] * t), sj = screen(j, at);
      if (t >= limit || placed.every(i => apart(sj, screens[i]))) return {t, at, sj};
      t += step;
    }
  };

  const subtree = children.map(() => 0);
  const count = i => (subtree[i] = 1 + children[i].reduce((s, j) => s + count(j), 0));
  count(root);
  const visit = p => {
    // small branches first (fasteners stay close), nearer children first; a child on
    // the same line as an earlier sibling travels at least as far, so stacks keep
    // their order
    const plans = children[p].map(j => {
      const d = direction(j);
      return {j, d, along: Math.abs(dot(sub(shapes[j].center, shapes[p].center), d))};
    }).sort((a, b) => subtree[a.j] - subtree[b.j] || a.along - b.along || a.j - b.j);
    const lines = [];
    for (const {j, d} of plans) {
      let start = Math.min(minTravel * diagonal, 0.5 * longest(j));
      for (const l of lines) if (dot(l.d, d) > 0.985) start = Math.max(start, l.t);
      const {t, at, sj} = travel(j, d, start);
      offsets[j] = at;
      screens[j] = sj;
      lines.push({d, t});
      placed.push(j);
      visit(j);
    }
  };
  visit(root);
  const result = offsets.map(o => new Vector3(...o));
  result.parents = parent; // for inspection (window.sketchcadViewer.explodeTree)
  result.kinds = shapes.map((s, i) => `${s.kind} ${rule[i]}`);
  return result;
}
