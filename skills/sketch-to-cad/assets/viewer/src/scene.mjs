// three.js scene for the viewer: parts (wire guide + solid), lights, camera framing.
// Everything is in the glTF frame: meters, Y-up. Wire points arrive already
// converted from CAD (see viewer.py), so wire and GLB share each part's local frame.
import {
  BufferGeometry, Box3, Color, DirectionalLight, DoubleSide, Float32BufferAttribute, Group,
  CanvasTexture, HemisphereLight, LineSegments, Matrix4, Mesh, Quaternion, MeshBasicMaterial, MeshDepthMaterial, MeshPhysicalMaterial, NeutralToneMapping,
  OrthographicCamera, PerspectiveCamera, PlaneGeometry, PMREMGenerator, Scene, ShaderMaterial, ShadowMaterial,
  SRGBColorSpace, Vector2, Vector3, VSMShadowMap, WebGLRenderTarget, WebGLRenderer,
} from 'three';
import {GLTFLoader} from 'three/examples/jsm/loaders/GLTFLoader.js';
import {RoomEnvironment} from 'three/examples/jsm/environments/RoomEnvironment.js';
import {explodeLayout} from './explode.mjs';
import {assemblyOrder} from './timeline.mjs';

// Every look-and-feel value of the scene lives here.
export const SCENE = Object.freeze({
  pixelRatioCap: 2,
  fov: 32,
  // image-based light: three's procedural RoomEnvironment (no network, no image
  // files) prefiltered with PMREM gives metal and glossy parts something to reflect
  exposure: 0.9,
  environmentBlur: 0.04,
  // lights on top of the environment (neutral tone mapping keeps lit faces from
  // clipping to white)
  skyColor: '#ffffff',
  groundColor: '#9c9a97',
  hemisphereIntensity: 0.55,
  keyColor: '#fffaf2',
  keyIntensity: 1.75,
  keyPosition: [0.15, 0.3, 0.2],   // direction from the model
  topColor: '#ffffff',
  topIntensity: 0.25,
  topPosition: [0, 1, 0],           // straight overhead: casts the ground shadow right under each part
  rimColor: '#f2f6ff',
  rimIntensity: 1.5,
  rimPosition: [-0.55, 0.45, -0.7], // behind the model as first seen: separates silhouettes
  // part material: color comes from the data (viewer.py PALETTE / partColors), the
  // surface response from data part.material (viewer.partMaterials, default satin)
  solidColor: '#b9b1a8',
  defaultMaterial: 'satin',
  materials: {
    matte: {roughness: 0.85, metalness: 0, clearcoat: 0, clearcoatRoughness: 0, envMapIntensity: 0.25,
      specularIntensity: 0.6},
    satin: {roughness: 0.55, metalness: 0, clearcoat: 0.25, clearcoatRoughness: 0.3, envMapIntensity: 0.35,
      specularIntensity: 1},
    gloss: {roughness: 0.22, metalness: 0, clearcoat: 1, clearcoatRoughness: 0.03, envMapIntensity: 0.75,
      specularIntensity: 1},
    metal: {roughness: 0.3, metalness: 1, clearcoat: 0, clearcoatRoughness: 0, envMapIntensity: 1.0,
      specularIntensity: 1},
  },
  // contact shadow: the parts' undersides seen from the floor, darker the closer
  // they are to it, blurred; grounds the assembly where it touches the floor
  contactOpacity: 0.9,
  contactFalloff: 0.1,    // height above the floor (share of the assembly diagonal) at which it is gone
  contactBlur: 0.05,      // blur radius as a share of the assembly diagonal
  contactMapSize: 512,
  contactColor: '#1d1c1b',
  // soft drop shadow (overhead light) on an invisible ground
  shadowOpacity: 0.24,
  shadowExploded: 0.6,   // share of the shadow opacity left while parts float apart
  shadowLiftNone: 0.06,  // a part displaced (exploded) this share of the diagonal from its assembled
                         // place casts no shadow; assembled parts cast their full shadow
  shadowColor: '#2e2c2a',
  shadowMapSize: 1024,
  shadowSoftness: 0.04,  // shadow blur radius as a share of the assembly diagonal
  shadowBlurSamples: 24,
  // camera framing: the model's projected bounds fill this share of the limiting
  // canvas dimension, centered in the band between the title and the controls
  fill: 0.76,
  safeTop: 0.07,
  safeBottom: 0.11,
  spinSamples: 24,   // view angles checked so the spinning model stays in frame
  // the drawing sheet laid on the floor (no top-view layout): the flattened footprint
  // fits this region of it (sheet-relative, x from the left, y from the top; the
  // notes column is on the right); its texture is rasterized this wide
  sheetDrawArea: {x0: 0.06, x1: 0.66, y0: 0.1, y1: 0.9},
  sheetTexturePx: 2048,
  sheetWash: 0.72,       // white veil over the laid sheet's print (all but the top view) at full wash
  topViewMargin: 4,      // paper mm of the top view kept clear of the veil
  sheetAzimuth: 0,       // the sheet phase looks down square onto the sheet ...
  sheetElevation: 0.9,   // ... from this high (radians), so the top view reads
  azimuth: 0.62,     // radians, camera around the vertical axis
  elevation: 0.42,   // radians above the horizon
  // multi-part exploded layout (see explode.mjs); lengths are shares of the
  // assembly diagonal
  explodeGap: 0.035,        // 3D clearance between exploded parts at most ...
  explodeMinGap: 0.015,     // ... and at least this
  explodePartGap: 0.25,     // ... otherwise this share of the moving part's length
  explodeMinTravel: 0.06,   // a part moves at least this far (or half its length, if less)
  explodeScreenShrink: 0.1, // outlines may overlap (> 0) or must stay apart (< 0) by this share of the smaller per side on screen
  explodeScreenMargin: 0.004, // ... less this share of the diagonal (projection error)
  explodeFloorShare: 0.02,  // parts this close to the floor can be the base
  explodeContactTol: 0.012, // surfaces this close touch (decides what sits on what)
  explodeMaxSamples: 4000,  // surface samples per part for contact and shape
  explodeAxisStrength: 0.25, // how clearly a symmetry axis must stand out (0..1)
  explodeCoaxialCos: 0.94,  // axes within ~20 degrees are parallel
  explodeCoaxialRadial: 0.5, // a part centered within this share of the radius of its parent's axis is coaxial
  explodeClampSpan: 2,      // a rod this many times longer than the part holding it ...
  explodeClampRadius: 2.5,  // ... and hugs it (median surface distance at most this many rod radii from its axis), lifts off sideways
  explodeInsideShare: 0.35, // a part centered within this share of its parent's half-length leaves by the upper end
  explodeAspect: 1.6,       // canvas shape the perspective of the layout is planned for
  explodePasses: 2,         // perspective re-planning passes
  explodeTravelStep: 0.004, // search step along a part's line for a clear screen outline
  sectionAlpha: 0.5, // section curves are lighter than BRep edges
  // assembly story: a flight arcs up by this share of its length; the final approach
  // runs along the part's explode direction for its explode travel, clamped to these
  // shares of the assembly diagonal
  flightArc: 0.1,
  drawingsZoomAspect: 1, // below this canvas aspect (portrait) the sheet phase closes in on the drawings
  drawingsFill: 0.96,    // ... which then fill this share of the canvas width
  drawingsMargin: 6,     // paper mm kept around the drawings in that close-up
  sheetFill: 0.9,   // the laid parts sheet fills this share of the limiting canvas side
  approachMin: 0.08,
  approachMax: 0.14,
});

const easeInOut = t => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const easeOut = t => 1 - (1 - t) ** 3;

// ---- framing ------------------------------------------------------------------

export function boxPoints(box) {
  const points = [];
  for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y])
    for (const z of [box.min.z, box.max.z]) points.push([x, y, z]);
  return points;
}

// Camera distance from `center` (along the azimuth/elevation direction) that keeps
// every point inside widthFraction x heightFraction of the view.
export function fitOrientedPoints(points, {center, aspect, fovDegrees, elevation, azimuth, widthFraction, heightFraction}) {
  const ca = Math.cos(azimuth), sa = Math.sin(azimuth), ce = Math.cos(elevation), se = Math.sin(elevation);
  const tan = Math.tan(fovDegrees * Math.PI / 360);
  let distance = 0;
  for (const [px, py, pz] of points) {
    const x = px - center.x, y = py - center.y, z = pz - center.z;
    const right = ca * x - sa * z, up = -sa * se * x + ce * y - ca * se * z, depth = sa * ce * x + se * y + ca * ce * z;
    distance = Math.max(distance, depth + Math.abs(right) / (tan * aspect * widthFraction), depth + Math.abs(up) / (tan * heightFraction));
  }
  return distance * 1.005;
}

export function cameraDirection(azimuth, elevation) {
  return new Vector3(Math.sin(azimuth) * Math.cos(elevation), Math.sin(elevation), Math.cos(azimuth) * Math.cos(elevation));
}

const smoothstep01 = v => { v = Math.min(1, Math.max(0, v)); return v * v * (3 - 2 * v); };

// Camera {target, distance} along `dir` (unit, from the target toward the camera)
// at which the points' perspective-projected bounds fill `fill` of the limiting
// canvas dimension, centered horizontally and in the band between safeTop and
// safeBottom (shares of the height). A few fixed-point steps: re-center the target
// on the projected bounds, then scale the distance by the size error.
export function fitView(points, dir, {aspect, fovDegrees, fill, safeTop, safeBottom}) {
  const d = dir.clone().normalize();
  const right = new Vector3(0, 1, 0).cross(d);
  if (right.lengthSq() < 1e-12) right.set(1, 0, 0);
  right.normalize();
  const up = d.clone().cross(right);
  const tan = Math.tan(fovDegrees * Math.PI / 360);
  const band = 1 - safeTop - safeBottom, fillY = Math.min(fill, band);
  const centerY = 1 - 2 * (safeTop + band / 2); // NDC, y up
  const box = new Box3().setFromPoints(points.map(p => new Vector3(...p)));
  const target = box.getCenter(new Vector3());
  const r = Math.max(1e-9, box.getSize(new Vector3()).length() / 2);
  let distance = r / (tan * Math.min(1, aspect) * fill) + r;
  for (let step = 0; step < 8; step++) {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const [px, py, pz] of points) {
      const x = px - target.x, y = py - target.y, z = pz - target.z;
      const depth = Math.max(1e-9, distance - (x * d.x + y * d.y + z * d.z));
      const nx = (x * right.x + y * right.y + z * right.z) / (depth * tan * aspect);
      const ny = (x * up.x + y * up.y + z * up.z) / (depth * tan);
      x0 = Math.min(x0, nx); x1 = Math.max(x1, nx); y0 = Math.min(y0, ny); y1 = Math.max(y1, ny);
    }
    target.addScaledVector(right, (x0 + x1) / 2 * distance * tan * aspect)
      .addScaledVector(up, ((y0 + y1) / 2 - centerY) * distance * tan);
    distance *= Math.max((x1 - x0) / (2 * fill), (y1 - y0) / (2 * fillY));
    distance = Math.max(distance, 2 * r * 1.05);
  }
  return {target, distance};
}

// ---- render loop ----------------------------------------------------------------

// Coalesce input bursts without running an idle animation loop.
export function createRenderLoop(render) {
  let pending = false;
  function request() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; render(); });
  }
  return {request};
}

// ---- parts ------------------------------------------------------------------------

const WIRE_VERTEX = `
attribute float at;
attribute float section;
varying float vAt;
varying float vSection;
void main() {
  vAt = at;
  vSection = section;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;
const WIRE_FRAGMENT = `
uniform vec3 color;
uniform float edgeOpacity;
uniform float sectionOpacity;
uniform float reveal;
varying float vAt;
varying float vSection;
void main() {
  if (vAt > reveal) discard;
  gl_FragColor = vec4(color, mix(edgeOpacity, sectionOpacity, step(0.5, vSection)));
  #include <colorspace_fragment>
}`;

// Each vertex gets `at` in 0..1: the reveal value at which the pen reaches it.
// BRep edges trace first, all at once; section curves follow, overlapping.
function wireGeometry(groups) {
  const pos = [], at = [], section = [];
  for (const g of groups) {
    const edge = g.kind === 'brep-edge';
    const [start, length] = edge ? [0, 0.72] : [0.3, 0.7];
    for (const line of g.lines) {
      let total = 0;
      const cum = [0];
      for (let i = 1; i < line.length; i++) {
        const [x0, y0, z0] = line[i - 1], [x1, y1, z1] = line[i];
        total += Math.hypot(x1 - x0, y1 - y0, z1 - z0);
        cum.push(total);
      }
      for (let i = 1; i < line.length; i++) {
        for (const k of [i - 1, i]) {
          pos.push(...line[k]);
          at.push(start + length * (total > 0 ? cum[k] / total : 1));
          section.push(edge ? 0 : 1);
        }
      }
    }
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new Float32BufferAttribute(pos, 3));
  geometry.setAttribute('at', new Float32BufferAttribute(at, 1));
  geometry.setAttribute('section', new Float32BufferAttribute(section, 1));
  geometry.computeBoundingBox();
  return geometry;
}

// Contact shadow pass: each fragment's darkness falls off with its height above the
// floor; seen from below, the depth test keeps the lowest surface.
const CONTACT_GROUND = {value: 0};   // floor height (world), shared by every part
const CONTACT_FALLOFF = {value: 1};  // height at which a surface no longer darkens the floor
const CONTACT_DEPTH_VERTEX = `
uniform float groundY;
varying float vHeight;
void main() {
  vec4 world = modelMatrix * vec4(position, 1.0);
  vHeight = world.y - groundY;
  gl_Position = projectionMatrix * viewMatrix * world;
}`;
const CONTACT_DEPTH_FRAGMENT = `
uniform float strength;
uniform float falloff;
varying float vHeight;
void main() {
  float h = clamp(max(vHeight, 0.0) / falloff, 0.0, 1.0);
  float dark = (1.0 - h) * (1.0 - h) * strength;
  if (dark < 0.002) discard;
  gl_FragColor = vec4(dark, 0.0, 0.0, 1.0);
}`;
const FULLSCREEN_VERTEX = `
varying vec2 vUv;
void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }`;
// 9-tap separable gaussian on the red channel
const BLUR_FRAGMENT = `
uniform sampler2D map;
uniform vec2 dir;
varying vec2 vUv;
void main() {
  float s = texture2D(map, vUv).r * 0.2270270270;
  s += (texture2D(map, vUv + dir).r + texture2D(map, vUv - dir).r) * 0.1945945946;
  s += (texture2D(map, vUv + 2.0 * dir).r + texture2D(map, vUv - 2.0 * dir).r) * 0.1216216216;
  s += (texture2D(map, vUv + 3.0 * dir).r + texture2D(map, vUv - 3.0 * dir).r) * 0.0540540541;
  s += (texture2D(map, vUv + 4.0 * dir).r + texture2D(map, vUv - 4.0 * dir).r) * 0.0162162162;
  gl_FragColor = vec4(s, 0.0, 0.0, 1.0);
}`;
const CONTACT_GROUND_VERTEX = `
uniform mat4 shadowMatrix;
varying vec4 vShadow;
void main() {
  vec4 world = modelMatrix * vec4(position, 1.0);
  vShadow = shadowMatrix * world;
  gl_Position = projectionMatrix * viewMatrix * world;
}`;
const CONTACT_GROUND_FRAGMENT = `
uniform sampler2D map;
uniform vec3 color;
uniform float opacity;
varying vec4 vShadow;
void main() {
  vec2 uv = vShadow.xy / vShadow.w * 0.5 + 0.5;
  vec2 edge = smoothstep(0.0, 0.04, uv) * smoothstep(0.0, 0.04, 1.0 - uv);
  float dark = texture2D(map, uv).r * edge.x * edge.y * opacity;
  if (dark < 0.002) discard;
  gl_FragColor = vec4(color, dark);
  #include <colorspace_fragment>
}`;

// Contact shadow on the floor under the assembly: the parts rendered from below
// into a small texture (createPart's contactMaterial), blurred, and shown on a
// ground plane. update() runs before every frame, so it follows spin and explode.
function createContactShadow(renderer, scene, parts, {center, floorY, half, diagonal}) {
  const size = SCENE.contactMapSize;
  const targets = [0, 1].map(() => new WebGLRenderTarget(size, size, {depthBuffer: true}));
  const camera = new OrthographicCamera(-half, half, half, -half, 0, diagonal * SCENE.contactFalloff * 1.5);
  camera.position.set(center.x, floorY - diagonal * 1e-3, center.z);
  camera.up.set(0, 0, -1);
  camera.lookAt(center.x, floorY + 1, center.z);
  camera.updateMatrixWorld(true);
  CONTACT_GROUND.value = floorY;
  CONTACT_FALLOFF.value = diagonal * SCENE.contactFalloff;

  const blur = new ShaderMaterial({uniforms: {map: {value: null}, dir: {value: new Vector2()}},
    vertexShader: FULLSCREEN_VERTEX, fragmentShader: BLUR_FRAGMENT, depthTest: false, depthWrite: false});
  const quad = new Mesh(new PlaneGeometry(2, 2), blur);
  quad.frustumCulled = false;
  const blurScene = new Scene().add(quad);
  const step = SCENE.contactBlur * diagonal / (2 * half) / 4; // gaussian reaches 4 taps

  const shadowMatrix = new Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  const material = new ShaderMaterial({
    uniforms: {map: {value: targets[0].texture}, shadowMatrix: {value: shadowMatrix},
      color: {value: new Color(SCENE.contactColor)}, opacity: {value: SCENE.contactOpacity}},
    vertexShader: CONTACT_GROUND_VERTEX, fragmentShader: CONTACT_GROUND_FRAGMENT,
    transparent: true, depthWrite: false,
  });
  const plane = new Mesh(new PlaneGeometry(2 * half, 2 * half), material);
  plane.rotation.x = -Math.PI / 2;
  plane.position.set(center.x, floorY - diagonal * 5e-4, center.z);
  plane.renderOrder = -1;
  scene.add(plane);

  const hidden = [];
  function update() {
    const previous = renderer.getRenderTarget(), autoShadow = renderer.shadowMap.autoUpdate;
    renderer.shadowMap.autoUpdate = false;
    hidden.length = 0;
    scene.traverse(o => {
      if (!o.visible) return;
      if (o.isLineSegments || (o.isMesh && !o.userData.part)) { hidden.push(o); o.visible = false; }
    });
    parts.forEach(p => p.solid.traverse(m => { if (m.isMesh) m.material = p.contactMaterial; }));
    renderer.setRenderTarget(targets[0]);
    renderer.clear();
    renderer.render(scene, camera);
    parts.forEach(p => p.solid.traverse(m => { if (m.isMesh) m.material = p.material; }));
    hidden.forEach(o => { o.visible = true; });
    for (let pass = 0; pass < 2; pass++) {
      for (const [from, to, dx, dy] of [[0, 1, step, 0], [1, 0, 0, step]]) {
        blur.uniforms.map.value = targets[from].texture;
        blur.uniforms.dir.value.set(dx, dy);
        renderer.setRenderTarget(targets[to]);
        renderer.render(blurScene, camera);
      }
    }
    renderer.setRenderTarget(previous);
    renderer.shadowMap.autoUpdate = autoShadow;
  }
  return {plane, material, update};
}

function decodeBase64(text) {
  const bin = atob(text), bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

function parseGlb(base64) {
  return new Promise((resolve, reject) => new GLTFLoader().parse(decodeBase64(base64), '', resolve, reject));
}

async function createPart(part, wireColor, envMap) {
  const wire = new LineSegments(wireGeometry(part.wire), new ShaderMaterial({
    uniforms: {color: {value: new Color(wireColor)}, edgeOpacity: {value: 1}, sectionOpacity: {value: 1},
      reveal: {value: 0}},
    vertexShader: WIRE_VERTEX, fragmentShader: WIRE_FRAGMENT, transparent: true, depthWrite: false,
  }));
  wire.renderOrder = 2;
  const material = new MeshPhysicalMaterial({
    color: part.color || SCENE.solidColor, envMap, ...partMaterial(part.material),
    polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
  });
  const gltf = await parseGlb(part.glbBase64);
  const solid = gltf.scene;
  const shadowFade = {value: 1};
  const depthMaterial = fadingDepthMaterial(shadowFade);
  const contactStrength = {value: 1};
  const contactMaterial = new ShaderMaterial({
    uniforms: {strength: contactStrength, groundY: CONTACT_GROUND, falloff: CONTACT_FALLOFF},
    vertexShader: CONTACT_DEPTH_VERTEX, fragmentShader: CONTACT_DEPTH_FRAGMENT, side: DoubleSide,
  });
  solid.traverse(o => {
    if (!o.isMesh) return;
    if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals(); // viewer meshes carry positions only
    o.material = material;
    o.userData.part = true;
    o.castShadow = true;
    o.customDepthMaterial = depthMaterial;
  });
  solid.updateMatrixWorld(true);
  const local = wire.geometry.boundingBox.clone();
  const meshBox = new Box3().setFromObject(solid);
  const alignmentMm = 1000 * Math.max(...['x', 'y', 'z'].flatMap(k =>
    [Math.abs(local.min[k] - meshBox.min[k]), Math.abs(local.max[k] - meshBox.max[k])]));

  const node = new Group();
  node.matrixAutoUpdate = false;
  node.add(solid, wire);
  const placement = new Matrix4().fromArray(part.placement);
  const placedBox = local.clone().applyMatrix4(placement);
  return {id: part.id, node, wire, solid, material, contactMaterial, contactStrength, placement, placedBox,
    points: placedVertices(solid, placement), tris: placedTriangles(solid, placement), shadowFade, alignmentMm};
}

// MeshPhysicalMaterial settings for a data part.material name (see SCENE.materials).
export function partMaterial(name) {
  return {...(SCENE.materials[name] || SCENE.materials[SCENE.defaultMaterial])};
}

// Shadow-pass material whose occluder coverage is `fade.value` (0..1): fragments are
// dropped by a fixed hash of their shadow-map texel, and the VSM blur turns the
// sparse occluders into a proportionally lighter shadow.
function fadingDepthMaterial(fade) {
  const m = new MeshDepthMaterial();
  m.onBeforeCompile = shader => {
    shader.uniforms.shadowFade = fade;
    shader.fragmentShader = shader.fragmentShader
      .replace('void main() {', `uniform float shadowFade;
void main() {
  if (fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453) >= shadowFade) discard;`);
  };
  return m;
}

// Mesh vertices as placed in the assembly, [[x, y, z], ...]: the exact outline for
// framing and for the exploded layout.
function placedVertices(solid, placement) {
  const points = [], p = new Vector3(), m = new Matrix4();
  solid.traverse(o => {
    if (!o.isMesh) return;
    m.multiplyMatrices(placement, o.matrixWorld);
    const pos = o.geometry.attributes.position;
    for (let i = 0; i < pos.count; i++) points.push(p.fromBufferAttribute(pos, i).applyMatrix4(m).toArray());
  });
  return points;
}

// Mesh triangles as placed in the assembly, flat [x,y,z, x,y,z, x,y,z, ...]: the
// surface the exploded layout samples for contact and shape.
function placedTriangles(solid, placement) {
  const out = [], p = new Vector3(), m = new Matrix4();
  solid.traverse(o => {
    if (!o.isMesh) return;
    m.multiplyMatrices(placement, o.matrixWorld);
    const pos = o.geometry.attributes.position, index = o.geometry.index;
    const count = index ? index.count : pos.count;
    for (let k = 0; k < count; k++) out.push(...p.fromBufferAttribute(pos, index ? index.getX(k) : k).applyMatrix4(m).toArray());
  });
  return new Float32Array(out);
}

// The part's placed mesh outline on the default view's right/up axes.
function viewRect(part, right, up) {
  const rect = {r0: Infinity, r1: -Infinity, u0: Infinity, u1: -Infinity};
  for (const [x, y, z] of part.points) {
    const r = x * right.x + y * right.y + z * right.z, u = x * up.x + y * up.y + z * up.z;
    rect.r0 = Math.min(rect.r0, r); rect.r1 = Math.max(rect.r1, r);
    rect.u0 = Math.min(rect.u0, u); rect.u1 = Math.max(rect.u1, u);
  }
  return rect;
}

// Exploded offsets for the default view. The layout clears the parts' outlines on
// screen: a first pass projects orthographically, later passes in perspective from
// the camera that frames the previous pass on a desktop-shaped canvas.
function explodeOffsets(parts) {
  const dir = cameraDirection(SCENE.azimuth, SCENE.elevation);
  const ca = Math.cos(SCENE.azimuth), sa = Math.sin(SCENE.azimuth);
  const ce = Math.cos(SCENE.elevation), se = Math.sin(SCENE.elevation);
  const view = {right: new Vector3(ca, 0, -sa), up: new Vector3(-sa * se, ce, -ca * se), back: dir};
  const items = parts.map(p => ({box: p.placedBox, rect: viewRect(p, view.right, view.up), tris: p.tris}));
  const options = {
    view, gap: SCENE.explodeGap, minGap: SCENE.explodeMinGap, partGap: SCENE.explodePartGap,
    minTravel: SCENE.explodeMinTravel, screenShrink: SCENE.explodeScreenShrink, floorShare: SCENE.explodeFloorShare,
    contactTol: SCENE.explodeContactTol, maxSamples: SCENE.explodeMaxSamples, axisStrength: SCENE.explodeAxisStrength,
    coaxialCos: SCENE.explodeCoaxialCos, coaxialRadial: SCENE.explodeCoaxialRadial,
    clampSpan: SCENE.explodeClampSpan, clampRadius: SCENE.explodeClampRadius,
    insideShare: SCENE.explodeInsideShare, travelStep: SCENE.explodeTravelStep, screenMargin: SCENE.explodeScreenMargin,
  };
  // passes: orthographic, then perspective from the camera that frames the previous
  // pass (the framing changes with the layout, so twice)
  let offsets = explodeLayout(items, options);
  if (parts.length < 2) return offsets;
  for (let pass = 0; pass < SCENE.explodePasses; pass++) {
    const prev = offsets;
    const points = parts.flatMap((p, i) => p.points.map(([x, y, z]) => [x + prev[i].x, y + prev[i].y, z + prev[i].z]));
    const {target, distance} = fitView(points, dir, {aspect: SCENE.explodeAspect, fovDegrees: SCENE.fov, fill: SCENE.fill,
      safeTop: SCENE.safeTop, safeBottom: SCENE.safeBottom});
    offsets = explodeLayout(items, {...options, eye: target.clone().addScaledVector(dir, distance)});
  }
  return offsets;
}

// ---- viewer scene ---------------------------------------------------------------

// Rasterize an SVG string to a canvas texture (data: URL, so no request leaves the page).
async function svgTexture(svgText, widthPx, maxAnisotropy) {
  const img = new Image();
  img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svgText)}`;
  await img.decode();
  const ratio = img.naturalWidth > 0 && img.naturalHeight > 0 ? img.naturalWidth / img.naturalHeight : 297 / 210;
  const c = document.createElement('canvas');
  c.width = widthPx;
  c.height = Math.round(widthPx / ratio);
  const g = c.getContext('2d');
  g.fillStyle = '#fff';
  g.fillRect(0, 0, c.width, c.height);
  g.drawImage(img, 0, 0, c.width, c.height);
  const texture = new CanvasTexture(c);
  texture.colorSpace = SRGBColorSpace;
  texture.anisotropy = maxAnisotropy;
  return texture;
}

// `sheetSvg`: the drawing the story lays down on the floor before the wires rise from it.
// `partsSheet` (multi-part): the parts sheet layout (viewer.py data.partsSheet); each part
// then lies on its own printed view, rises and flies to its place (timeline assemblyState).
export async function createViewerScene(canvas, data, {sheetSvg = null, sheetPaper = [297, 210], topView = null, partsSheet = null} = {}) {
  const sheetRatio = sheetPaper[0] / sheetPaper[1];
  const renderer = new WebGLRenderer({canvas, antialias: true, alpha: true});
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = NeutralToneMapping; // keeps lit faces from clipping to the background white
  renderer.toneMappingExposure = SCENE.exposure;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = VSMShadowMap; // blurred, no stair-stepping on small screens
  const scene = new Scene();
  const pmrem = new PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  // set on each material, not as scene.environment: only a material's own envMap
  // honours its envMapIntensity (the per-finish strength in SCENE.materials)
  const envMap = pmrem.fromScene(room, SCENE.environmentBlur).texture;
  room.traverse(o => { o.geometry?.dispose(); o.material?.dispose(); });
  pmrem.dispose();
  const camera = new PerspectiveCamera(SCENE.fov, 1, 0.01, 10);

  const parts = await Promise.all(data.parts.map(p => createPart(p, data.wireColor, envMap)));
  const assembled = new Box3();
  parts.forEach(p => assembled.union(p.placedBox));
  const center = assembled.getCenter(new Vector3());
  const offsets = explodeOffsets(parts);
  const spread = new Box3();
  parts.forEach((p, i) => spread.union(p.placedBox.clone().translate(offsets[i])));
  // the exploded framing fits the parts' mesh vertices, not a box around them all,
  // so an exploded layout is not framed by its empty corners
  const spreadPoints = parts.flatMap((p, i) => p.points.map(([x, y, z]) => [x + offsets[i].x, y + offsets[i].y, z + offsets[i].z]));
  const assembledPoints = parts.flatMap(p => p.points);

  // spin rotates about the vertical axis through the assembly center
  const spin = new Group(), pivot = new Group();
  spin.position.copy(center);
  pivot.position.copy(center).negate();
  spin.add(pivot);
  parts.forEach(p => pivot.add(p.node));
  // `flat` squashes the story onto the assembled floor (lift 0) and lets
  // it rise to full height (lift 1): y' = floor + lift * (y - floor)
  const flat = new Group();
  flat.add(spin);
  scene.add(flat);

  const flatFloor = assembled.min.y; // the assembled floor, where the sheet lies down
  const radius = assembled.getSize(new Vector3()).length() / 2;
  // the invisible ground covers the exploded spread at every spin angle
  let reach = 0;
  for (const [x, y, z] of boxPoints(spread)) reach = Math.max(reach, Math.hypot(x - center.x, y - center.y, z - center.z));
  scene.add(new HemisphereLight(SCENE.skyColor, SCENE.groundColor, SCENE.hemisphereIntensity));
  const key = new DirectionalLight(SCENE.keyColor, SCENE.keyIntensity);
  key.position.copy(center).add(new Vector3(...SCENE.keyPosition).normalize().multiplyScalar(radius * 4));
  key.target.position.copy(center);
  const top = new DirectionalLight(SCENE.topColor, SCENE.topIntensity);
  // the shadow camera only needs the assembly at every spin angle plus the explode
  // displacement within which a part still casts (see shadowLiftNone): a tight
  // frustum keeps the shadow map fine and free of banding
  let shadowReach = 0;
  for (const [x, y, z] of boxPoints(assembled)) shadowReach = Math.max(shadowReach, Math.hypot(x - center.x, y - center.y, z - center.z));
  shadowReach += SCENE.shadowLiftNone * 2 * radius;
  top.position.copy(center).add(new Vector3(...SCENE.topPosition).normalize().multiplyScalar(shadowReach * 3));
  top.target.position.copy(center);
  top.castShadow = true;
  const sc = top.shadow.camera;
  sc.left = sc.bottom = -shadowReach * 1.05;
  sc.right = sc.top = shadowReach * 1.05;
  sc.near = shadowReach * 0.5;
  sc.far = shadowReach * 6;
  top.shadow.mapSize.set(SCENE.shadowMapSize, SCENE.shadowMapSize);
  // blur radius in shadow-map texels for the same world-space softness on every model
  top.shadow.radius = SCENE.shadowSoftness * 2 * radius * SCENE.shadowMapSize / (2 * shadowReach * 1.05);
  top.shadow.blurSamples = SCENE.shadowBlurSamples;
  top.shadow.bias = -0.0005;
  const rim = new DirectionalLight(SCENE.rimColor, SCENE.rimIntensity);
  rim.position.copy(center).add(new Vector3(...SCENE.rimPosition).normalize().multiplyScalar(radius * 4));
  rim.target.position.copy(center);
  scene.add(key, key.target, top, top.target, rim, rim.target);

  // invisible ground at the assembled floor that only shows the shadow
  const groundMaterial = new ShadowMaterial({color: SCENE.shadowColor, opacity: SCENE.shadowOpacity,
    transparent: true, depthWrite: false});
  const ground = new Mesh(new PlaneGeometry(reach * 8, reach * 8), groundMaterial);
  ground.rotation.x = -Math.PI / 2;
  ground.position.set(center.x, assembled.min.y - radius * 1e-3, center.z);
  ground.receiveShadow = true;
  ground.renderOrder = -1;
  scene.add(ground);
  const contact = createContactShadow(renderer, scene, parts, {center, floorY: assembled.min.y, half: reach * 1.02,
    diagonal: 2 * radius});

  const direction = cameraDirection(SCENE.azimuth, SCENE.elevation);
  const diagonal = 2 * radius;

  // Drawing sheet on the floor. With the sheet's top-view layout (topView, see
  // viewer.py) it is placed in world units so its printed top view lies exactly under
  // the assembled model (paper mm = 1/scale model mm; paper right = CAD +X, paper up =
  // CAD +Y, i.e. glTF -Z); the flattened wire is then the top view itself. Without it
  // the sheet is square to the sheet camera and the footprint is fitted into its
  // drawing area. Corners are in card order: top-left, top-right, bottom-right, bottom-left.
  const sheetDir = cameraDirection(SCENE.sheetAzimuth, SCENE.sheetElevation);
  const sheetY = flatFloor - radius * 2e-3;
  const [paperW, paperH] = sheetPaper;
  let sheetCorners = null, topRect = null; // topRect: the top view's paper box [x0, y0, x1, y1]
  const story = sheetSvg && partsSheet?.items?.length && parts.length > 1 ? 'assembly' : 'rise';
  let paperToFloor = null; // assembly story: paper (x, y) mm -> world point on the floor sheet
  if (story === 'assembly') {
    // true paper scale, the table area (where the assembly will stand) centered under it
    const s = partsSheet.items[0].scale, [sx0, sy0, sx1, sy1] = partsSheet.stand;
    const ax = (sx0 + sx1) / 2, ay = (sy0 + sy1) / 2;
    paperToFloor = (x, y) => new Vector3(center.x + (x - ax) / s / 1000, sheetY, center.z + (y - ay) / s / 1000);
    sheetCorners = [[0, 0], [paperW, 0], [paperW, paperH], [0, paperH]].map(([x, y]) => paperToFloor(x, y));
  } else if (topView) {
    const {origin: [px, py], scale: s, extent: [tu0, tv0, tu1, tv1]} = topView;
    // part frame of the sheet: world for an assembly sheet, the part's own for a part sheet
    const frame = parts.length === 1 ? parts[0].placement : new Matrix4();
    const paperToWorld = (x, y) => new Vector3((tu0 + (x - px) / s) / 1000, 0, -(tv1 - (y - py) / s) / 1000).applyMatrix4(frame);
    const corners = [[0, 0], [paperW, 0], [paperW, paperH], [0, paperH]].map(([x, y]) => paperToWorld(x, y));
    if (corners.every(c => Math.abs(c.y - corners[0].y) < diagonal * 1e-6)) { // the part frame keeps it level
      sheetCorners = corners.map(c => c.setY(sheetY));
      topRect = [px, py, px + (tu1 - tu0) * s, py + (tv1 - tv0) * s];
    }
  }
  if (!sheetCorners) {
    const forward = new Vector3(-sheetDir.x, 0, -sheetDir.z).normalize();
    const right = new Vector3().crossVectors(forward, new Vector3(0, 1, 0)).normalize();
    let u0 = Infinity, u1 = -Infinity, v0 = Infinity, v1 = -Infinity;
    for (const [x, , z] of assembledPoints) {
      const u = x * right.x + z * right.z, v = x * forward.x + z * forward.z;
      u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v);
    }
    const area = SCENE.sheetDrawArea;
    const sheetW = Math.max((u1 - u0) / (area.x1 - area.x0), (v1 - v0) / (area.y1 - area.y0) * sheetRatio, diagonal * 1e-3);
    const sheetH = sheetW / sheetRatio;
    const cu = (u0 + u1) / 2 + (0.5 - (area.x0 + area.x1) / 2) * sheetW;
    const cv = (v0 + v1) / 2 - (0.5 - (area.y0 + area.y1) / 2) * sheetH; // sheet y runs from the far edge
    const c = new Vector3().addScaledVector(right, cu).addScaledVector(forward, cv).setY(sheetY);
    sheetCorners = [[-1, 1], [1, 1], [1, -1], [-1, -1]].map(([a, b]) =>
      c.clone().addScaledVector(right, a * sheetW / 2).addScaledVector(forward, b * sheetH / 2));
  }
  // paper (x, y) mm -> world point on the sheet (bilinear on the corners; the sheet is a parallelogram)
  const [tl, tr, , bl] = sheetCorners;
  const onSheet = (x, y) => tl.clone().addScaledVector(tr.clone().sub(tl), x / paperW).addScaledVector(bl.clone().sub(tl), y / paperH);
  const quadGeometry = (rects) => { // paper rects [x0, y0, x1, y1] -> one geometry with sheet UVs
    const pos = [], uv = [], index = [];
    for (const [x0, y0, x1, y1] of rects) {
      if (x1 <= x0 || y1 <= y0) continue;
      const k = pos.length / 3;
      for (const [x, y] of [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]) {
        pos.push(...onSheet(x, y).toArray());
        uv.push(x / paperW, 1 - y / paperH);
      }
      index.push(k, k + 3, k + 1, k + 1, k + 3, k + 2);
    }
    const g = new BufferGeometry();
    g.setAttribute('position', new Float32BufferAttribute(pos, 3));
    g.setAttribute('uv', new Float32BufferAttribute(uv, 2));
    g.setIndex(index);
    return g;
  };
  let sheetMesh = null, veil = null;
  if (sheetSvg) {
    const texture = await svgTexture(sheetSvg, SCENE.sheetTexturePx, renderer.capabilities.getMaxAnisotropy());
    const sheetMaterial = opts => new MeshBasicMaterial({transparent: true, opacity: 0, depthWrite: false,
      toneMapped: false, side: DoubleSide, ...opts});
    sheetMesh = new Mesh(quadGeometry([[0, 0, paperW, paperH]]), sheetMaterial({map: texture}));
    sheetMesh.renderOrder = -2;
    sheetMesh.visible = false;
    scene.add(sheetMesh);
    // white veil that pales the print, except over the top view the model grows out of
    const m = SCENE.topViewMargin;
    const [x0, y0, x1, y1] = topRect ? [topRect[0] - m, topRect[1] - m, topRect[2] + m, topRect[3] + m] : [0, 0, 0, 0];
    // (assembly story: the veil pales the table the assembly is built over)
    veil = new Mesh(quadGeometry(story === 'assembly' ? [[partsSheet.stand[0], 0, paperW, paperH]] : topRect
      ? [[0, 0, paperW, y0], [0, y1, paperW, paperH], [0, y0, x0, y1], [x1, y0, paperW, y1]]
      : [[0, 0, paperW, paperH]]), sheetMaterial({color: '#ffffff'}));
    veil.renderOrder = -1.5;
    sheetMesh.add(veil);
  }
  const sheetPoints = sheetCorners.map(c => c.toArray());

  // Assembly story poses (glTF local -> world, meters). On the sheet, a part lies in its
  // own frame with the side its printed view shows facing up: u = P.right runs along
  // paper x, v = P.up against paper y, depth (right x up) rises off the sheet, squashed
  // by `rise`; so the flat wire is the print. Copies of a row (qty > 1) are centered on
  // the same print. A flight goes from the sheet pose (risen) to a point short of the
  // assembled place, arcing up and turning on the way, then slides in along the part's
  // explode direction (relative to its parent).
  const rank = story === 'assembly' ? assemblyOrder(offsets.parents || parts.map((_, i) => (i ? 0 : -1))) : null;
  const flights = story === 'assembly' ? parts.map((part, i) => {
    let item = null, first = false;
    for (const it of partsSheet.items) { const k = it.parts.indexOf(part.id); if (k >= 0) { item = it; first = k === 0; break; } }
    if (!item) return null;
    const cad = a => [a[0], a[2], -a[1]]; // CAD axis -> its coefficients on glTF-local meters
    const r = item.right, w = item.up, e = [r[1] * w[2] - r[2] * w[1], r[2] * w[0] - r[0] * w[2], r[0] * w[1] - r[1] * w[0]];
    const [cr, cw, ce] = [cad(r), cad(w), cad(e)];
    const dot = (c, p) => c[0] * p[0] + c[1] * p[1] + c[2] * p[2];
    const box = part.wire.geometry.boundingBox, corners = boxPoints(box);
    const range = c => [Math.min(...corners.map(p => dot(c, p))), Math.max(...corners.map(p => dot(c, p)))];
    const [[pu0, pu1], [pv0, pv1], [pd0]] = [range(cr), range(cw), range(ce)];
    const [u0, v0, u1, v1] = item.extent.map(x => x / 1000);
    const du = first ? 0 : (u0 + u1) / 2 - (pu0 + pu1) / 2, dv = first ? 0 : (v0 + v1) / 2 - (pv0 + pv1) / 2;
    const o = paperToFloor(item.origin[0], item.origin[1]);
    const lift = diagonal * 1e-3; // just above the sheet
    const sheetPose = (rise, m = new Matrix4()) => m.set(
      cr[0], cr[1], cr[2], o.x + du - u0,
      rise * ce[0], rise * ce[1], rise * ce[2], sheetY + lift - rise * pd0,
      -cw[0], -cw[1], -cw[2], o.z + v1 - dv,
      0, 0, 0, 1);
    const p0 = new Vector3(), q0 = new Quaternion(), pA = new Vector3(), qA = new Quaternion(), unitScale = new Vector3();
    sheetPose(1).decompose(p0, q0, unitScale);
    part.placement.decompose(pA, qA, unitScale);
    const parent = offsets.parents?.[i] ?? -1;
    const rel = offsets[i].clone().sub(parent >= 0 ? offsets[parent] : new Vector3());
    const dirn = rel.lengthSq() > 1e-18 ? rel.clone().normalize() : new Vector3(0, 1, 0);
    const reach = Math.min(Math.max(rel.length(), SCENE.approachMin * diagonal), SCENE.approachMax * diagonal);
    const pre = pA.clone().addScaledVector(dirn, reach);
    const paper = [item.origin[0], item.origin[1], item.origin[0] + (u1 - u0) * 1000 * item.scale, item.origin[1] + (v1 - v0) * 1000 * item.scale];
    return {item: item.item, first, paper, sheetPose, p0, q0, pA, qA, pre, arc: SCENE.flightArc * pre.distanceTo(p0)};
  }) : null;
  const flightPos = new Vector3(), flightQuat = new Quaternion(), ONE = new Vector3(1, 1, 1);
  // part matrix (world) at a timeline part state; null once it is seated
  function flightMatrix(f, s, m) {
    if (s.fly >= 1) return null;
    if (s.fly <= 0) return f.sheetPose(s.rise, m);
    if (s.seat > 0) {
      flightPos.lerpVectors(f.pre, f.pA, easeOut(s.seat));
      flightQuat.copy(f.qA);
    } else {
      const g = easeInOut(s.travel);
      flightPos.lerpVectors(f.p0, f.pre, g);
      flightPos.y += f.arc * Math.sin(Math.PI * g);
      flightQuat.slerpQuaternions(f.q0, f.qA, g);
    }
    return m.compose(flightPos, flightQuat, ONE);
  }
  const topPoints = topRect
    ? [[topRect[0], topRect[1]], [topRect[2], topRect[1]], [topRect[2], topRect[3]], [topRect[0], topRect[3]]].map(([x, y]) => onSheet(x, y).toArray())
    : [];
  // assembly story: the drawings area of the parts sheet (every item's print, a little
  // margin) with headroom for the wires risen in place
  const drawingsPoints = [];
  if (flights) {
    const ps = flights.filter(Boolean).map(f => f.paper);
    if (ps.length) {
      const m = SCENE.drawingsMargin;
      const x0 = Math.min(...ps.map(p => p[0])) - m, y0 = Math.min(...ps.map(p => p[1])) - m;
      const x1 = Math.max(...ps.map(p => p[2])) + m, y1 = Math.max(...ps.map(p => p[3])) + m;
      for (const [x, y] of [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]) drawingsPoints.push(paperToFloor(x, y).toArray());
    }
  }
  const flatAssembled = assembledPoints.map(([x, , z]) => [x, flatFloor, z]);
  let aspect = 1;
  // Framings ({target, distance}): S the spinning assembly (centered on the spin axis,
  // in frame at every angle), F the finished assembly; placeCamera blends S -> F with
  // settle. The sheet phase looks down along sheetDir: P0 frames the whole sheet with
  // the assembly (the card lands on it), P1 the top view with the flat and the risen
  // model (the wires rise in it); sheetView blends from them back to S/F.
  let viewS = null, viewF = null, viewP0 = null, viewP1 = null;
  const framing = () => ({aspect, fovDegrees: SCENE.fov, fill: SCENE.fill, safeTop: SCENE.safeTop, safeBottom: SCENE.safeBottom});
  const fitAt = (which, dir) => fitView(which === 'exploded' ? spreadPoints : assembledPoints, dir, framing());

  function resize(width, height) {
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, SCENE.pixelRatioCap));
    renderer.setSize(width, height, false);
    aspect = width / Math.max(1, height);
    camera.aspect = aspect;
    viewF = fitAt('assembled', direction);
    viewP0 = fitView([...assembledPoints, ...(sheetMesh ? sheetPoints : flatAssembled)], sheetDir,
      story === 'assembly' ? {...framing(), fill: SCENE.sheetFill} : framing());
    if (story === 'assembly') {
      // portrait: close in on the drawings (the BOM is unreadable there anyway); landscape keeps P0
      viewP1 = aspect < SCENE.drawingsZoomAspect && drawingsPoints.length
        ? fitView(drawingsPoints, sheetDir, {...framing(), fill: SCENE.drawingsFill}) : viewP0;
    } else {
      viewP1 = fitView([...assembledPoints, ...flatAssembled, ...topPoints], sheetDir, framing());
    }
    const stride = Math.max(1, Math.floor(assembledPoints.length / 4000));
    const sample = assembledPoints.filter((_, i) => i % stride === 0);
    const band = 1 - SCENE.safeTop - SCENE.safeBottom;
    let spinDistance = 0;
    for (let k = 0; k < SCENE.spinSamples; k++) {
      spinDistance = Math.max(spinDistance, fitOrientedPoints(sample, {center, aspect, fovDegrees: SCENE.fov,
        elevation: SCENE.elevation, azimuth: SCENE.azimuth - k * 2 * Math.PI / SCENE.spinSamples,
        widthFraction: SCENE.fill, heightFraction: Math.min(SCENE.fill, band)}));
    }
    viewS = {target: center.clone(), distance: spinDistance};
    const fitted = [viewS, viewF, viewP0, viewP1, fitAt('exploded', direction)].map(v => v.distance);
    const nearest = Math.min(...fitted);
    camera.near = Math.max(nearest - diagonal * 2, nearest / 100);
    camera.far = Math.max(...fitted) + diagonal * 4;
    camera.updateProjectionMatrix();
  }

  const target = new Vector3(), settleTarget = new Vector3(), sheetTarget = new Vector3(), dir = new Vector3();
  // S -> F with settle, then toward the sheet framing (P0 -> P1 with sheetZoom) with sheetView;
  // the view direction turns from the sheet's to the story's with it
  function placeCamera(_gather, settle = 1, sheetView = 0, sheetZoom = 0) {
    target.copy(viewS.target).lerp(settleTarget.copy(viewF.target), settle);
    let distance = viewS.distance + (viewF.distance - viewS.distance) * settle;
    sheetTarget.lerpVectors(viewP0.target, viewP1.target, sheetZoom);
    const sheetDistance = viewP0.distance + (viewP1.distance - viewP0.distance) * sheetZoom;
    target.lerp(sheetTarget, sheetView);
    distance += (sheetDistance - distance) * sheetView;
    dir.copy(cameraDirection(SCENE.azimuth + (SCENE.sheetAzimuth - SCENE.azimuth) * sheetView,
      SCENE.elevation + (SCENE.sheetElevation - SCENE.elevation) * sheetView));
    camera.position.copy(target).addScaledVector(dir, distance);
    camera.lookAt(target);
  }

  const offset = new Matrix4();
  // `explode` (0..1) re-spreads the finished assembly on top of the timeline state.
  function apply(state, {camera: moveCamera = true, explode = 0} = {}) {
    const gather = state.gather * (1 - explode);
    let solidSum = 0;
    parts.forEach((part, i) => {
      const s = state.parts[i];
      part.node.visible = s.visible;
      const u = part.wire.material.uniforms;
      part.wire.visible = s.visible;
      u.reveal.value = s.reveal;
      u.edgeOpacity.value = s.edgeOpacity;
      u.sectionOpacity.value = s.sectionOpacity * SCENE.sectionAlpha;
      part.solid.visible = s.visible && s.solidOpacity > 0.001;
      const opaque = s.solidOpacity >= 0.999;
      if (part.material.transparent === opaque) { part.material.transparent = !opaque; part.material.needsUpdate = true; }
      part.material.depthWrite = opaque;
      part.material.opacity = s.solidOpacity;
      solidSum += s.visible ? s.solidOpacity : 0;
      const o = offsets[i].clone().multiplyScalar(1 - gather);
      const flying = flights?.[i] ? flightMatrix(flights[i], s, part.node.matrix) : null;
      let away = o.length();
      if (flying) away = flightPos.setFromMatrixPosition(flying).distanceTo(flights[i].pA);
      else part.node.matrix.copy(offset.makeTranslation(o.x, o.y, o.z).multiply(part.placement));
      // a part's shadow fades out with its explode displacement: no smear under a lifted part
      const fade = 1 - smoothstep01(away / diagonal / SCENE.shadowLiftNone);
      part.shadowFade.value = fade;
      part.contactStrength.value = fade * s.solidOpacity;
      part.solid.traverse(m => { if (m.isMesh) m.castShadow = fade > 0.001; });
      part.node.matrixWorldNeedsUpdate = true;
    });
    // the shadow fades in with the solids and is lighter while the parts float apart
    const apart = parts.length > 1 ? 1 - gather : 0;
    groundMaterial.opacity = SCENE.shadowOpacity * solidSum / parts.length * (1 - (1 - SCENE.shadowExploded) * apart);
    spin.rotation.y = state.spin;
    const lift = state.lift ?? 1;
    flat.scale.y = lift;
    flat.position.y = flatFloor * (1 - lift);
    if (sheetMesh) {
      const o = state.plane ?? 0;
      sheetMesh.visible = o > 0.001;
      sheetMesh.material.opacity = o;
      veil.material.opacity = o * (state.planeWash ?? 0) * SCENE.sheetWash;
    }
    if (moveCamera) placeCamera(state.gather, state.settle, state.sheetView ?? 0, state.sheetZoom ?? 0);
  }

  // Screen-space bounding box (canvas CSS pixels) of each part's mesh as last applied.
  const corner = new Vector3();
  function partScreenBoxes() {
    scene.updateMatrixWorld(true);
    camera.updateMatrixWorld(true);
    const {width, height} = canvas.getBoundingClientRect();
    const boxes = parts.map(part => {
      const box = {id: part.id, x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity};
      part.solid.traverse(o => {
        if (!o.isMesh) return;
        const pos = o.geometry.attributes.position;
        for (let i = 0; i < pos.count; i++) {
          corner.fromBufferAttribute(pos, i).applyMatrix4(o.matrixWorld).project(camera);
          const x = (corner.x + 1) / 2 * width, y = (1 - corner.y) / 2 * height;
          box.x0 = Math.min(box.x0, x); box.x1 = Math.max(box.x1, x);
          box.y0 = Math.min(box.y0, y); box.y1 = Math.max(box.y1, y);
        }
      });
      return box;
    });
    return {width, height, boxes};
  }

  // The floor sheet's corners in canvas CSS pixels for the current camera (card order).
  const projected = new Vector3();
  function sheetQuad() {
    camera.updateMatrixWorld(true);
    const {width, height} = canvas.getBoundingClientRect();
    return sheetCorners.map(c => {
      projected.copy(c).project(camera);
      return [(projected.x + 1) / 2 * width, (1 - projected.y) / 2 * height];
    });
  }

  // Assembly story: per part, the screen box of its printed view on the floor sheet and
  // of its mesh as last applied (canvas CSS pixels); null for the rise story.
  function itemScreenBoxes() {
    if (!flights) return null;
    scene.updateMatrixWorld(true);
    camera.updateMatrixWorld(true);
    const {width, height} = canvas.getBoundingClientRect();
    const screen = p => { projected.copy(p).project(camera); return [(projected.x + 1) / 2 * width, (1 - projected.y) / 2 * height]; };
    const mesh = partScreenBoxes().boxes;
    return parts.map((part, i) => {
      const f = flights[i];
      if (!f) return null;
      const [x0, y0, x1, y1] = f.paper;
      const pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]].map(([x, y]) => screen(onSheet(x, y)));
      const print = {x0: Math.min(...pts.map(q => q[0])), x1: Math.max(...pts.map(q => q[0])),
        y0: Math.min(...pts.map(q => q[1])), y1: Math.max(...pts.map(q => q[1]))};
      return {id: part.id, item: f.item, first: f.first, print, part: mesh[i]};
    }).filter(Boolean);
  }

  // Screen box (canvas CSS pixels) of the printed top view on the floor sheet, or null.
  function topViewScreenBox() {
    if (!topRect) return null;
    camera.updateMatrixWorld(true);
    const {width, height} = canvas.getBoundingClientRect();
    const box = {x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity};
    for (const p of topPoints) {
      projected.set(...p).project(camera);
      const x = (projected.x + 1) / 2 * width, y = (1 - projected.y) / 2 * height;
      box.x0 = Math.min(box.x0, x); box.x1 = Math.max(box.x1, x); box.y0 = Math.min(box.y0, y); box.y1 = Math.max(box.y1, y);
    }
    return box;
  }

  return {
    renderer, scene, camera, center, target, apply, resize, placeCamera, fitAt, partScreenBoxes, sheetQuad, topViewScreenBox,
    itemScreenBoxes, story, assemblyRank: rank,
    explodeTree: parts.map((p, i) => ({id: p.id, kind: offsets.kinds?.[i], parent: parts[offsets.parents?.[i]]?.id ?? null,
      offset: offsets[i].toArray()})),
    setShadowVisible: on => { ground.visible = on; contact.plane.visible = on; },
    render: () => {
      if (contact.plane.visible) contact.update();
      renderer.render(scene, camera);
    },
    alignmentMm: Math.max(...parts.map(p => p.alignmentMm)),
  };
}
