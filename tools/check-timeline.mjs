// Unit checks for the pure story timelines (developer-only):
//   docker run --rm -u "$(id -u)" -v "$PWD":/work -w /work node:24-bookworm node tools/check-timeline.mjs
import assert from 'node:assert/strict';
import {ASSEMBLY_TIMING as T, assemblyOrder, assemblyState, timelineState} from '../skills/sketch-to-cad/assets/viewer/src/timeline.mjs';

// assembly order: root first, depth-first, siblings in part order
assert.deepEqual(assemblyOrder([-1, 0, 0, 1]), [0, 1, 3, 2]);
assert.deepEqual(assemblyOrder([2, 2, -1]), [1, 2, 0]);
assert.deepEqual(assemblyOrder([-1, 5]), [0, 1]); // unreachable parent: follows in part order

const order = assemblyOrder([-1, 0, 0, 1, 3]);
const cards = {sketch: true, sheet: true};
const at = p => assemblyState(p, order, cards);
// stages: sketch, parts sheet, 3D assembly
assert.equal(at(0).stage, 1);
assert.equal(at(0.2).stage, 2);
assert.equal(at(T.flatCheck).stage, 2);
assert.equal(at(0.5).stage, 3);
// flat moment: every wire drawn, nothing risen or flown
for (const s of at(T.flatCheck).parts) {
  assert.equal(s.reveal, 1);
  assert.ok(s.rise <= T.liftFloor + 1e-9);
  assert.equal(s.fly, 0);
  assert.equal(s.solidOpacity, 0);
}
// the base flies first, the rest in rank order
const mid = at(0.6).parts;
for (let i = 0; i < order.length; i++) for (let j = 0; j < order.length; j++)
  if (order[i] < order[j]) assert.ok(mid[i].fly >= mid[j].fly);
// solid only while seating (the approach), full when seated
for (const s of at(0.6).parts) if (s.fly < 1 - T.approach) assert.equal(s.solidOpacity, 0);
for (const s of at(1).parts) { assert.equal(s.fly, 1); assert.equal(s.solidOpacity, 1); }
// sheet: card shown, then laid, then the plane fades
assert.equal(at(0.2).sheet, 1);
assert.ok(at(0.5).plane > 0.99 && at(0.5).sheet === 0);
assert.equal(at(0.95).plane, 0);
// pure: same progress, same state
assert.deepEqual(at(0.637), at(0.637));
assert.ok(at(1).done && at(1).spin > 0 && at(0.8).spin === 0);
// two parts: the second flight follows close behind the first
const two = assemblyState(0.62, [0, 1], cards).parts;
assert.ok(two[0].fly === 1 && two[1].fly > 0 && two[1].fly < 1);
// the single-part story is unchanged
assert.equal(timelineState(0.465, 1, cards).lift < 0.01, true);
console.log('timeline PASS');
