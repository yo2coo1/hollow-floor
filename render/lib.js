// Hollow Floor blocky 3D library. Every story is a JSON scene assembled from these pieces.
// Everything is a pure function of time t, so any frame renders identically in any order (crash-safe, parallelizable).
import * as THREE from 'three';

// ---------- helpers ----------
const lerp = (a, b, k) => a + (b - a) * k;
const ease = k => k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
const clamp01 = k => Math.max(0, Math.min(1, k));
// deterministic noise: same t -> same value
const hash = n => { const s = Math.sin(n * 127.1 + 311.7) * 43758.5453; return s - Math.floor(s); };
const noise = t => { const i = Math.floor(t), f = t - i; return lerp(hash(i), hash(i + 1), f * f * (3 - 2 * f)); };

// keyframes: [[t, v1, v2, ...], ...] -> interpolated [v1, v2, ...]
export function kf(frames, t, smooth = true) {
  if (!frames || !frames.length) return null;
  if (t <= frames[0][0]) return frames[0].slice(1);
  for (let i = 1; i < frames.length; i++) {
    if (t <= frames[i][0]) {
      const [a, b] = [frames[i - 1], frames[i]];
      let k = (t - a[0]) / (b[0] - a[0]);
      if (smooth) k = ease(k);
      return a.slice(1).map((v, j) => lerp(v, b[j + 1], k));
    }
  }
  return frames[frames.length - 1].slice(1);
}

// faint grime/wear on every surface so blocks read as worn materials, not flat plastic (fixed seed: identical every render)
let dotTex = null;
const dot = () => dotTex || (dotTex = (() => {  // soft round sprite (dust, eye halos)
  const c = Object.assign(document.createElement('canvas'), { width: 32, height: 32 }), g = c.getContext('2d'), r = g.createRadialGradient(16, 16, 0, 16, 16, 16);
  r.addColorStop(0, 'rgba(255,255,255,1)'); r.addColorStop(1, 'rgba(255,255,255,0)'); g.fillStyle = r; g.fillRect(0, 0, 32, 32);
  return new THREE.CanvasTexture(c);
})());
let grime = null;
function grimeMap() {
  if (grime || typeof document === 'undefined') return grime;
  const S = 128, c = Object.assign(document.createElement('canvas'), { width: S, height: S }), g = c.getContext('2d'), img = g.createImageData(S, S);
  for (let i = 0; i < S * S; i++) {
    const x = i % S, y = (i / S) | 0, v = 228 + 27 * (0.55 * noise(x / 9 + hash(y * 0.37) * 0.3 + y / 11 * 7.1) + 0.45 * hash(i * 0.618)) - (x < 2 || y < 2 ? 40 : 0);
    img.data.set([v, v, v * 0.98, 255], i * 4);
  }
  g.putImageData(img, 0, 0);
  grime = new THREE.CanvasTexture(c); grime.colorSpace = THREE.SRGBColorSpace;
  return grime;
}
const mats = new Map();
function mat(color, opts = {}) {
  const key = color + JSON.stringify(opts);
  if (!mats.has(key)) mats.set(key, new THREE.MeshStandardMaterial({ color, roughness: 0.9, metalness: 0, flatShading: true, map: grimeMap(), ...opts }));
  return mats.get(key);
}
function box(w, h, d, color, opts) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), typeof color === 'string' ? mat(color, opts) : color);
  m.castShadow = m.receiveShadow = true;
  return m;
}
const at = (obj, x, y, z) => (obj.position.set(x, y, z), obj);

// ---------- characters ----------
// One signature color per character (torso). kind: 'person' | 'shadow' (black silhouette, glowing eyes).
export function character({ color = '#3b82f6', skin = '#d8b48c', pants = '#2b2b33', kind = 'person', eyeColor = '#ff2020', scale = 1 } = {}) {
  const g = new THREE.Group();
  const shadow = kind === 'shadow';
  // shadow figures are unlit pure black so they always read as a silhouette, even under red light
  const black = new THREE.MeshBasicMaterial({ color: '#020203' });
  const body = shadow ? black : color, sk = shadow ? black : skin, pa = shadow ? black : pants;
  const limb = (w, h, col, px, py) => { const p = at(new THREE.Group(), px, py, 0); p.add(at(box(w, h, w, col), 0, -h / 2, 0)); g.add(p); return p; };
  const legs = [limb(0.22, 0.8, pa, -0.13, 0.8), limb(0.22, 0.8, pa, 0.13, 0.8)];
  g.add(at(box(0.6, 0.75, 0.32, body), 0, 1.18, 0));
  const arms = [limb(0.18, 0.7, body, -0.4, 1.52), limb(0.18, 0.7, body, 0.4, 1.52)];
  const head = at(new THREE.Group(), 0, 1.6, 0);
  head.add(at(box(0.5, 0.5, 0.5, sk), 0, 0.25, 0));
  const eyeMat = shadow ? new THREE.MeshBasicMaterial({ color: eyeColor, toneMapped: false }) : mat('#111');
  const eyes = [-0.11, 0.11].map(x => { const e = at(box(shadow ? 0.1 : 0.08, shadow ? 0.06 : 0.08, 0.02, eyeMat), x, 0.3, 0.26); e.castShadow = false; head.add(e); return e; });
  if (shadow) { // faint red glow around the eyes so the silhouette's edges read in the dark
    const glow = new THREE.PointLight(eyeColor, 0.8, 1.6, 2); glow.position.set(0, 0.3, 0.45); head.add(glow);
    if (typeof document !== 'undefined') {  // soft red halo in front of the eyes (bloom alone would wash pure red out to orange)
      const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: dot(), color: eyeColor, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false }));
      halo.scale.set(0.55, 0.28, 1); halo.position.set(0, 0.3, 0.3); head.add(halo);
    }
  }
  const mouth = at(box(0.18, 0.04, 0.02, '#3a1f1f'), 0, 0.14, 0.26);
  if (!shadow) head.add(mouth);
  g.add(head);
  g.scale.setScalar(scale);
  return { group: g, legs, arms, head, eyes, mouth, shadow };
}

// spec: { path: [[t,x,z],...], face: [[t,deg],...], pose: [[t,'idle'|'walk'|'panic'|'still'|'point'|'sit'],...], look: [[t,yawDeg,pitchDeg]], visible: [[t,0|1]] }
function animateCharacter(c, spec, t) {
  const p = kf(spec.path, t, false) || [0, 0];
  const pPrev = kf(spec.path, t - 0.05, false) || p;
  const speed = Math.hypot(p[0] - pPrev[0], p[1] - pPrev[1]) / 0.05;
  c.group.position.set(p[0], spec.y || 0, p[1]);
  const face = spec.face && t >= spec.face[0][0] ? kf(spec.face, t) : null;  // before the first key: face the walking direction
  // facing: explicit keyframe, else direction of the last path segment that moved (pure function of t)
  let yaw = 0;
  for (let i = 1; i < (spec.path || []).length && spec.path[i - 1][0] < t; i++) {
    const [a, b] = [spec.path[i - 1], spec.path[i]];
    if (a[1] !== b[1] || a[2] !== b[2]) yaw = Math.atan2(b[1] - a[1], b[2] - a[2]);
  }
  c.group.rotation.y = face ? THREE.MathUtils.degToRad(face[0]) : yaw;
  const pose = step(spec.pose, t) || (speed > 0.05 ? 'walk' : 'idle');
  const sw = pose === 'walk' ? Math.sin(t * 9) * 0.6 : 0;
  c.legs[0].rotation.x = sw; c.legs[1].rotation.x = -sw;
  c.arms[0].rotation.x = -sw; c.arms[1].rotation.x = sw;
  c.arms[0].rotation.z = c.arms[1].rotation.z = 0;
  if (pose === 'panic') { c.arms[0].rotation.x = c.arms[1].rotation.x = -2.6 + Math.sin(t * 30) * 0.15; c.group.position.x += Math.sin(t * 45) * 0.02; }
  if (pose === 'point') c.arms[1].rotation.x = -1.5;
  if (pose === 'sit') { c.legs[0].rotation.x = c.legs[1].rotation.x = -1.57; c.arms[0].rotation.x = c.arms[1].rotation.x = -0.35; c.group.position.y -= 0.32; }  // hips at couch-seat height
  if (pose === 'idle') c.group.position.y += Math.sin(t * 2) * 0.01;
  const look = kf(spec.look, t);
  c.head.rotation.set(look ? THREE.MathUtils.degToRad(look[1] || 0) : 0, look ? THREE.MathUtils.degToRad(look[0]) : 0, 0);
  const vis = step(spec.visible, t);
  c.group.visible = vis === null ? true : !!vis;
}
// step keyframes: value holds until the next key
function step(frames, t) { let v = null; for (const f of frames || []) if (t >= f[0]) v = f[1]; return v; }

// ---------- rooms ----------
// A closed box room. Walls face inward; the camera can sit inside. Tiled floor gives depth cues.
// holes: [{wall: 'back'|'left'|'right', at: offset along the wall from its center, w: 1.1, h: 2.3}] = doorways
export function room({ w = 6, d = 8, h = 3, wall = '#2b2b36', floor = ['#3a3530', '#332e2a'], ceiling = '#1c1c22', openFront = true, holes = [], walls = true } = {}) {
  const g = new THREE.Group();
  const nx = Math.ceil(w), nz = Math.ceil(d), tx = w / nx, tz = d / nz;  // tiles fit the room exactly (no overlap into neighbours)
  for (let x = 0; x < nx; x++) for (let z = 0; z < nz; z++)
    g.add(at(box(tx, 0.1, tz, floor[(x + z) % 2]), (x + 0.5) * tx - w / 2, -0.05, (z + 0.5) * tz - d / 2));
  if (!walls) return g;  // outdoor ground (street, yard): floor only
  g.add(at(box(w, 0.1, d, ceiling), 0, h + 0.05, 0));
  // a wall of length L along one axis, with optional doorways cut out
  const wallAlong = (L, place, cuts) => {
    let a = -L / 2;
    for (const c of [...cuts].sort((p, q) => p.at - q.at)) {
      const hw = (c.w ?? 1.1) / 2, hh = c.h ?? 2.3;
      if (c.at - hw > a) place(a, c.at - hw, 0, h);
      place(c.at - hw, c.at + hw, hh, h);  // lintel above the doorway
      a = c.at + hw;
    }
    if (a < L / 2) place(a, L / 2, 0, h);
  };
  const cut = side => holes.filter(o => o.wall === side);
  wallAlong(w, (a, b, y0, y1) => g.add(at(box(b - a, y1 - y0, 0.1, wall), (a + b) / 2, (y0 + y1) / 2, -d / 2)), cut('back'));
  wallAlong(d, (a, b, y0, y1) => g.add(at(box(0.1, y1 - y0, b - a, wall), -w / 2, (y0 + y1) / 2, (a + b) / 2)), cut('left'));
  wallAlong(d, (a, b, y0, y1) => g.add(at(box(0.1, y1 - y0, b - a, wall), w / 2, (y0 + y1) / 2, (a + b) / 2)), cut('right'));
  if (!openFront) g.add(at(box(w, h, 0.1, wall), 0, h / 2, d / 2));
  // baseboard trim: cheap detail that makes the blocks read as a real room
  g.add(at(box(w, 0.15, 0.12, '#15151b'), 0, 0.075, -d / 2 + 0.06));
  return g;
}

// ---------- props ----------
// Each returns { group, update?(t, spec) }. Positions/rotations come from the scene spec.
export const props = {
  door({ color = '#5a3d2b', frame = '#2a1d14', void: dark = true } = {}) {  // void=false when a real room is modeled behind it
    const g = new THREE.Group();
    g.add(at(box(0.12, 2.3, 0.2, frame), -0.56, 1.15, 0), at(box(0.12, 2.3, 0.2, frame), 0.56, 1.15, 0), at(box(1.24, 0.12, 0.2, frame), 0, 2.3, 0));
    if (dark) g.add(at(box(1.0, 2.2, 0.02, '#000'), 0, 1.1, -0.06)); // darkness behind the door
    const hinge = at(new THREE.Group(), -0.5, 0, 0);
    hinge.add(at(box(1.0, 2.2, 0.08, color), 0.5, 1.1, 0), at(box(0.08, 0.08, 0.12, '#c9a227', { metalness: 0.6 }), 0.88, 1.05, 0.05));
    g.add(hinge);
    return { group: g, update: (t, s) => { const o = kf(s.open, t); hinge.rotation.y = o ? -THREE.MathUtils.degToRad(o[0]) : 0; } };
  },
  // elevator doors: two panels that slide apart; open: [[t, 0..1], ...]. Panels sit just behind the wall so they vanish into it.
  liftdoor({ color = '#a9b0b8', frame = '#4a4f56' } = {}) {
    const g = new THREE.Group();
    g.add(at(box(0.1, 2.3, 0.16, frame), -0.62, 1.15, 0), at(box(0.1, 2.3, 0.16, frame), 0.62, 1.15, 0), at(box(1.34, 0.1, 0.16, frame), 0, 2.3, 0));
    const metal = { metalness: 0.5 };
    const l = at(box(0.58, 2.22, 0.05, color, metal), -0.29, 1.11, -0.1), r = at(box(0.58, 2.22, 0.05, color, metal), 0.29, 1.11, -0.1);
    g.add(l, r);
    return { group: g, update: (t, s) => { const o = (kf(s.open, t) || [0])[0]; l.position.x = -0.29 - o * 0.6; r.position.x = 0.29 + o * 0.6; } };
  },
  // an invisible light source (a TV screen's spill, a phone in the dark): flicker/off/red events work on it like a bulb
  glow({ color = '#8fb4ff' } = {}) { return { group: new THREE.Group(), light: { y: 0, color }, glass: null }; },
  bulb({ color = '#ffd9a0' } = {}) {
    const g = new THREE.Group();
    g.add(at(box(0.04, 0.4, 0.04, '#111'), 0, -0.2, 0));
    const glass = at(box(0.16, 0.2, 0.16, new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 2 })), 0, -0.5, 0);
    glass.castShadow = false; g.add(glass);
    return { group: g, light: { y: -0.6, color }, glass };
  },
  window({ w = 1.4, h = 1.2, glow = '#4a6fa5' } = {}) {
    const g = new THREE.Group();
    const pane = at(box(w, h, 0.04, mat(glow, { emissive: glow, emissiveIntensity: 0.6 })), 0, 0, 0);
    pane.castShadow = false;
    g.add(pane, at(box(w + 0.16, 0.08, 0.1, '#222'), 0, h / 2, 0), at(box(w + 0.16, 0.08, 0.1, '#222'), 0, -h / 2, 0),
      at(box(0.08, h, 0.1, '#222'), 0, 0, 0), at(box(w, 0.06, 0.1, '#222'), 0, 0, 0));
    return { group: g, pane };
  },
  bed({ sheet = '#6b7a8f' } = {}) {
    const g = new THREE.Group();
    g.add(at(box(1.4, 0.4, 2.1, '#3b2a20'), 0, 0.2, 0), at(box(1.3, 0.2, 2.0, sheet), 0, 0.5, 0), at(box(0.9, 0.15, 0.4, '#ddd'), 0, 0.66, -0.75), at(box(1.4, 0.9, 0.1, '#3b2a20'), 0, 0.45, -1.05));
    return { group: g };
  },
  table({ color = '#4a3526' } = {}) {
    const g = new THREE.Group();
    g.add(at(box(1.4, 0.08, 0.8, color), 0, 0.76, 0));
    for (const [x, z] of [[-0.62, -0.32], [0.62, -0.32], [-0.62, 0.32], [0.62, 0.32]]) g.add(at(box(0.08, 0.76, 0.08, color), x, 0.38, z));
    return { group: g };
  },
  chair({ color = '#4a3526' } = {}) {
    const g = new THREE.Group();
    g.add(at(box(0.5, 0.06, 0.5, color), 0, 0.45, 0), at(box(0.5, 0.55, 0.06, color), 0, 0.75, -0.22));
    for (const [x, z] of [[-0.2, -0.2], [0.2, -0.2], [-0.2, 0.2], [0.2, 0.2]]) g.add(at(box(0.05, 0.45, 0.05, color), x, 0.225, z));
    return { group: g };
  },
  tv({ glow = '#9fc4ff' } = {}) {
    const g = new THREE.Group();
    const screen = at(box(1.0, 0.6, 0.02, mat(glow, { emissive: glow, emissiveIntensity: 1.2 })), 0, 0, 0.06);
    screen.castShadow = false;
    g.add(at(box(1.1, 0.7, 0.1, '#111'), 0, 0, 0), screen);
    return { group: g, screen };
  },
  phone({ glow = '#cfe3ff' } = {}) {
    const g = new THREE.Group();
    const screen = at(box(0.14, 0.26, 0.01, mat(glow, { emissive: glow, emissiveIntensity: 1.5 })), 0, 0, 0.012);
    g.add(at(box(0.16, 0.3, 0.02, '#111'), 0, 0, 0), screen);
    return { group: g, screen };
  },
  crate({ color = '#5b4632', size = 0.6 } = {}) { return { group: at(new THREE.Group().add(at(box(size, size, size, color), 0, 0, 0)), 0, size / 2, 0) }; },
  shelf({ color = '#3b2a20' } = {}) {
    const g = new THREE.Group();
    g.add(at(box(0.08, 2, 0.4, color), -0.6, 1, 0), at(box(0.08, 2, 0.4, color), 0.6, 1, 0));
    for (const y of [0.1, 0.7, 1.3, 1.9]) g.add(at(box(1.2, 0.06, 0.4, color), 0, y, 0));
    return { group: g };
  },
  couch({ color = '#5b3a4a' } = {}) {
    const g = new THREE.Group();
    g.add(at(box(2.0, 0.45, 0.9, color), 0, 0.22, 0), at(box(2.0, 0.6, 0.22, color), 0, 0.75, -0.34),
      at(box(0.22, 0.35, 0.9, color), -0.95, 0.55, 0), at(box(0.22, 0.35, 0.9, color), 0.95, 0.55, 0),
      at(box(0.85, 0.12, 0.65, '#6e4a5c'), -0.45, 0.5, 0.05), at(box(0.85, 0.12, 0.65, '#6e4a5c'), 0.45, 0.5, 0.05));
    return { group: g };
  },
  lamp({ color = '#ffcf8a' } = {}) {
    const g = new THREE.Group();
    g.add(at(box(0.3, 0.05, 0.3, '#222'), 0, 0.025, 0), at(box(0.05, 1.4, 0.05, '#222'), 0, 0.7, 0));
    const glass = at(box(0.4, 0.3, 0.4, new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 1.2 })), 0, 1.5, 0);
    glass.castShadow = false; g.add(glass);
    return { group: g, light: { y: 1.4, color }, glass };
  },
  // a folded note: slides under doors via the generic "path" keyframes
  note() {
    const g = new THREE.Group();
    const paper = at(box(0.22, 0.01, 0.15, new THREE.MeshStandardMaterial({ color: '#f2efe6', emissive: '#f2efe6', emissiveIntensity: 0.25 })), 0, 0.01, 0);
    g.add(paper, at(box(0.14, 0.012, 0.012, '#333'), 0, 0.012, -0.02), at(box(0.1, 0.012, 0.012, '#333'), -0.02, 0.012, 0.02));
    return { group: g };
  },
  fridge({ color = '#c9ccd1' } = {}) {
    const g = new THREE.Group();
    g.add(at(box(0.8, 1.8, 0.7, color), 0, 0.9, 0), at(box(0.04, 0.4, 0.05, '#555'), 0.32, 1.2, 0.37));
    return { group: g };
  },
  plank({ color = '#8a6a44', len = 1.3, flat = false } = {}) {  // a board nailed across a door; flat=true lies on the floor (a crack)
    const g = new THREE.Group();
    if (flat) { g.add(at(box(len, 0.012, 0.09, color), 0, 0.006, 0)); return { group: g }; }
    g.add(at(box(len, 0.16, 0.04, color), 0, 0, 0));
    for (const x of [-len / 2 + 0.08, len / 2 - 0.08]) g.add(at(box(0.03, 0.03, 0.05, '#bbb', { metalness: 0.6 }), x, 0, 0.02));
    return { group: g };
  },
  coin() {
    const g = new THREE.Group();
    g.add(at(box(0.07, 0.015, 0.07, '#d4af37', { metalness: 0.7, roughness: 0.3 }), 0, 0.008, 0));
    return { group: g };
  },
  bottle({ color = '#5aa9e6' } = {}) {
    const g = new THREE.Group();
    g.add(at(box(0.08, 0.22, 0.08, color, { roughness: 0.2 }), 0, 0.11, 0), at(box(0.04, 0.05, 0.04, '#e8e8e8'), 0, 0.245, 0));
    return { group: g };
  },
  // a desk counter for night-shift scenes (gas station, hotel lobby, security desk)
  dryer({ color = '#d8dade', glow = '#2a3440' } = {}) {  // washer/dryer; "spin": [[t, turns/s]] tumbles the drum bar, 0 = still
    const g = new THREE.Group();
    const door = at(box(0.5, 0.5, 0.03, mat(glow, { emissive: glow, emissiveIntensity: 0.8 })), 0, 0.45, 0.385);
    door.castShadow = false;
    const bar = at(box(0.42, 0.07, 0.02, '#111'), 0, 0.45, 0.405);
    g.add(at(box(0.8, 0.85, 0.75, color), 0, 0.425, 0), at(box(0.62, 0.62, 0.02, '#555'), 0, 0.45, 0.376), door, bar,
      at(box(0.5, 0.06, 0.02, '#333'), 0, 0.8, 0.38));
    return { group: g, update: (t, s) => { const k = kf(s.spin, t, false); bar.rotation.z = k ? t * k[0] * 6.28 : 0; } };
  },
  counter({ color = '#2f3a40', top = '#556066' } = {}) {
    const g = new THREE.Group();
    g.add(at(box(2.4, 1.0, 0.7, color), 0, 0.5, 0), at(box(2.5, 0.06, 0.8, top), 0, 1.03, 0));
    return { group: g };
  },
};

// ---------- lights ----------
// Moody defaults: low ambient, cold moonlight, warm bulbs. Events can flicker, black out, or shift everything to red.
function lightRig(scene, spec) {
  const ambient = new THREE.HemisphereLight(spec.ambientColor ?? '#4a5070', spec.groundColor ?? '#0a0a0f', spec.ambient ?? 0.35);
  const moon = new THREE.DirectionalLight(spec.moonColor ?? '#8aa4ff', spec.moon ?? 0.35);
  moon.position.set(3, 8, 5); moon.castShadow = true; moon.shadow.mapSize.set(1024, 1024);
  Object.assign(moon.shadow.camera, { left: -8, right: 8, top: 8, bottom: -8 });
  scene.add(ambient, moon);
  return { ambient, moon, base: { ambient: ambient.intensity, moon: moon.intensity, ambientColor: ambient.color.clone() } };
}

// events: [{t, type:'red', dur}] [{t, until, type:'flicker', target?}] [{t, until, type:'blackout'}] [{t, type:'flash', color}]
function redAmount(events, t) {
  let r = 0;
  for (const e of events || []) if (e.type === 'red' && t >= e.t) r = Math.max(r, clamp01((t - e.t) / (e.ramp ?? 0.15)) * (e.until && t > e.until ? 0 : 1));
  return r;
}
function lightFactor(events, t, name) {
  let f = 1;
  for (const e of events || []) {
    if (t < e.t || (e.until !== undefined && t > e.until)) continue;
    if (e.type === 'blackout') f = 0.03;
    if (e.type === 'dim') f *= e.level ?? 0.35;  // lights low but the scene still readable
    if (e.type === 'off' && e.target === name) f = 0;  // one named light switched off (motion lights, a dying bulb)
    if (e.type === 'flicker' && (!e.target || e.target === name)) f *= noise(t * 18 + 7) > 0.45 ? 1 : 0.08 + noise(t * 40) * 0.2;
  }
  return f;
}

// ---------- scene assembly ----------
export function buildScene(spec) {
  const scene = new THREE.Scene();
  const fogColor = new THREE.Color(spec.fog?.color ?? '#0b0b12');
  scene.background = fogColor.clone();
  scene.fog = new THREE.FogExp2(fogColor.clone(), spec.fog?.density ?? 0.08);
  const rig = lightRig(scene, spec.lights || {});
  for (const r of spec.rooms || [spec.room || {}]) { const g = room(r); g.position.set(r.pos?.[0] || 0, 0, r.pos?.[1] || 0); scene.add(g); }

  const items = [];
  const bulbs = [];
  for (const p of spec.props || []) {
    const made = props[p.type](p);
    at(made.group, ...(p.pos || [0, 0, 0]));
    made.group.rotation.y = THREE.MathUtils.degToRad(p.rot || 0);
    if (p.scale) made.group.scale.setScalar(p.scale);
    scene.add(made.group);
    if (made.light) {
      const L = new THREE.PointLight(made.light.color, p.intensity ?? 6, p.range ?? 9, 1.6);
      L.position.set(p.pos[0], p.pos[1] + made.light.y, p.pos[2]);
      L.castShadow = !!p.shadow; if (L.castShadow) L.shadow.mapSize.set(512, 512);
      scene.add(L);
      // soft glow around the bulb (a bloom look without a costly bloom pass); follows the light's brightness and colour
      const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: dot(), color: made.light.color, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.35 }));
      halo.position.copy(L.position); halo.scale.setScalar(0.9); scene.add(halo);
      bulbs.push({ L, halo, glass: made.glass, name: p.name, base: L.intensity, color: new THREE.Color(made.light.color) });
    }
    items.push({ made, spec: p });
  }
  const chars = (spec.characters || []).map(cs => { const c = character(cs); scene.add(c.group); return { c, spec: cs }; });
  // dust drifting in the air: catches the light and gives depth (spec.dust: 0 = none, default 1)
  const nd = Math.round(260 * (spec.dust ?? 1)), dust = new THREE.BufferGeometry(), seeds = new Float32Array(nd * 3), dpos = new Float32Array(nd * 3);
  const box3 = new THREE.Box3(); for (const r of spec.rooms || [spec.room || {}]) {
    const w = r.w ?? 6, d = r.d ?? 8, x = r.pos?.[0] || 0, z = r.pos?.[1] || 0;
    box3.union(new THREE.Box3(new THREE.Vector3(x - w / 2, 0.2, z - d / 2), new THREE.Vector3(x + w / 2, (r.h ?? 3) - 0.2, z + d / 2)));
  }
  for (let i = 0; i < nd * 3; i++) seeds[i] = hash(i * 1.37 + 0.5);
  dust.setAttribute('position', new THREE.BufferAttribute(dpos, 3));
  if (nd) scene.add(new THREE.Points(dust, new THREE.PointsMaterial({ color: '#cfc7b8', size: 0.014, transparent: true, opacity: 0.4, depthWrite: false, map: dot(), alphaTest: 0.01 })));
  const sz = box3.getSize(new THREE.Vector3());

  const red = new THREE.Color('#ff1a1a'), redFog = new THREE.Color('#2a0000');
  const tmp = new THREE.Color();
  function update(t) {
    const r = redAmount(spec.events, t), lf = lightFactor(spec.events, t, '*');
    rig.ambient.intensity = rig.base.ambient * lf * (1 - r * 0.5);
    rig.ambient.color.copy(rig.base.ambientColor).lerp(red, r);
    rig.moon.intensity = rig.base.moon * lf * (1 - r);
    for (const b of bulbs) {
      const f = lightFactor(spec.events, t, b.name);
      b.L.intensity = b.base * f * (1 + r * 0.5);
      b.L.color.copy(b.color).lerp(red, r);
      b.halo.material.color.copy(b.L.color); b.halo.material.opacity = 0.35 * Math.min(1, f);
      tmp.copy(b.color).lerp(red, r);
      if (b.glass) { b.glass.material.color.copy(tmp); b.glass.material.emissive.copy(tmp); b.glass.material.emissiveIntensity = 2 * f; }
    }
    scene.fog.color.copy(fogColor).lerp(redFog, r); scene.background.copy(scene.fog.color);
    for (const { made, spec: s } of items) {
      made.update?.(t, s);
      const p = kf(s.path, t);  // any prop can move: [[t, x, y, z], ...]
      if (p) made.group.position.set(p[0], p[1], p[2]);
      const rk = kf(s.rock, t);  // rocking tilt: [[t, radians], ...], 0 = still
      if (rk) made.group.rotation.x = Math.sin(t * 3.2) * rk[0];
      const v = step(s.visible, t);
      if (v !== null) made.group.visible = !!v;
    }
    for (const { c, spec: s } of chars) animateCharacter(c, s, t);
    for (let i = 0; i < nd; i++) {  // slow drift, wrapping inside the rooms; pure function of t
      const k = i * 3, f = (v, sp) => ((v + t * sp) % 1 + 1) % 1;
      dpos[k] = box3.min.x + sz.x * f(seeds[k], 0.004 + seeds[k + 1] * 0.006) + Math.sin(t * 0.5 + i) * 0.05;
      dpos[k + 1] = box3.min.y + sz.y * f(seeds[k + 1], -0.003 - seeds[k + 2] * 0.004);
      dpos[k + 2] = box3.min.z + sz.z * f(seeds[k + 2], 0.003);
    }
    dust.attributes.position.needsUpdate = true;
  }
  return { scene, update };
}

// ---------- camera ----------
// shots: [{t, from:[x,y,z], to?:[x,y,z], look:[x,y,z], lookTo?, fov?, shake?}] — a new shot is a hard cut.
export function cameraAt(cam, shots, t, duration) {
  let i = 0;
  for (let k = 0; k < shots.length; k++) if (t >= shots[k].t) i = k;
  const s = shots[i], end = shots[i + 1]?.t ?? duration;
  const k = ease(clamp01((t - s.t) / Math.max(0.001, end - s.t)));
  const P = s.to ? s.from.map((v, j) => lerp(v, s.to[j], k)) : s.from;
  const L = s.lookTo ? s.look.map((v, j) => lerp(v, s.lookTo[j], k)) : s.look;
  const sh = s.shake || 0;
  cam.position.set(P[0] + (noise(t * 13) - 0.5) * sh, P[1] + (noise(t * 17 + 3) - 0.5) * sh, P[2]);
  cam.lookAt(L[0] + (noise(t * 11 + 9) - 0.5) * sh * 0.5, L[1], L[2]);
  cam.fov = s.fov || 50; cam.updateProjectionMatrix();
  return i;
}

// ---------- 2D overlay: title card, word-by-word captions, end card, flashes, vignette ----------
// Safe area for Reels/TikTok/Shorts: keep text between 14% and 72% of height, 9% side margins.
export function drawOverlay(ctx, spec, t, W, H, mode) {
  const u = W / 1080;
  const vg = ctx.createRadialGradient(W / 2, H / 2, H * 0.25, W / 2, H / 2, H * 0.75);
  vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(1, 'rgba(0,0,0,0.75)');
  ctx.fillStyle = vg; ctx.fillRect(0, 0, W, H);

  for (const e of spec.events || []) if (e.type === 'flash' && t >= e.t && t < e.t + 0.2) {
    ctx.fillStyle = e.color || 'rgba(255,40,40,1)'; ctx.globalAlpha = 1 - (t - e.t) / 0.2; ctx.fillRect(0, 0, W, H); ctx.globalAlpha = 1;
  }

  const font = (px) => `${px * u}px ${spec.font || "'HF Brand', 'Arial Black', sans-serif"}`;
  const wrap = (text, px, maxW) => {
    ctx.font = font(px); const lines = [];
    for (const para of text.split('\n')) { // '\n' forces a break
      let line = '';
      for (const w of para.split(' ')) { const tr = line ? line + ' ' + w : w; if (ctx.measureText(tr).width > maxW && line) { lines.push(line); line = w; } else line = tr; }
      if (line) lines.push(line);
    }
    return lines;
  };
  const stroke = (txt, x, y, fill) => { ctx.lineJoin = 'round'; ctx.lineWidth = 14 * u; ctx.strokeStyle = '#000'; ctx.strokeText(txt, x, y); ctx.fillStyle = fill; ctx.fillText(txt, x, y); };
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';

  if (mode === 'thumb') return drawThumb(ctx, spec, W, H, u, font, wrap, stroke);

  if (spec.title && t < (spec.title.until ?? 1.2)) {
    const a = clamp01(((spec.title.until ?? 1.2) - t) / 0.25);
    ctx.globalAlpha = a;
    const lines = wrap(spec.title.text.toUpperCase(), 110, W * 0.82);
    ctx.fillStyle = 'rgba(0,0,0,0.55)'; ctx.fillRect(0, H * 0.3 - lines.length * 70 * u, W, lines.length * 140 * u);
    lines.forEach((l, i) => stroke(l, W / 2, H * 0.3 + (i - (lines.length - 1) / 2) * 125 * u, i === lines.length - 1 ? '#ff2a2a' : '#fff'));
    ctx.globalAlpha = 1;
  }

  // peephole: round fish-eye window, everything else black
  for (const e of spec.events || []) if (e.type === 'peephole' && t >= e.t && t < e.until) {
    ctx.save();
    ctx.beginPath(); ctx.rect(0, 0, W, H); ctx.arc(W / 2, H * 0.42, W * 0.44, 0, Math.PI * 2, true); ctx.fillStyle = '#000'; ctx.fill('evenodd');
    const ring = ctx.createRadialGradient(W / 2, H * 0.42, W * 0.3, W / 2, H * 0.42, W * 0.44);
    ring.addColorStop(0, 'rgba(0,0,0,0)'); ring.addColorStop(1, 'rgba(0,0,0,0.9)');
    ctx.fillStyle = ring; ctx.fillRect(0, 0, W, H); ctx.restore();
  }

  // security-camera look: green tint, scanlines, REC dot and a timestamp label
  for (const e of spec.events || []) if (e.type === 'cctv' && t >= e.t && t < e.until) {
    ctx.save();
    ctx.globalCompositeOperation = 'saturation'; ctx.fillStyle = 'hsl(0,0%,50%)'; ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = 'multiply'; ctx.fillStyle = 'rgb(150,230,170)'; ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = 'source-over'; ctx.fillStyle = 'rgba(0,0,0,0.18)';
    for (let y = 0; y < H; y += 6 * u) ctx.fillRect(0, y, W, 2 * u);
    ctx.font = font(52); ctx.textAlign = 'left'; ctx.fillStyle = '#e8ffe8';
    ctx.fillText(e.label || 'CAM 4', 70 * u, H * 0.16);
    if (Math.floor(t * 2) % 2 === 0) { ctx.fillStyle = '#ff3030'; ctx.beginPath(); ctx.arc(W - 120 * u, H * 0.16, 18 * u, 0, Math.PI * 2); ctx.fill(); }
    ctx.fillStyle = '#e8ffe8'; ctx.textAlign = 'right'; ctx.fillText('REC', W - 150 * u, H * 0.16);
    ctx.restore(); ctx.textAlign = 'center';
  }

  // Part 1 choice card: the viewer vote
  const cc = spec.choiceCard;
  if (cc && t >= cc.from) {
    const k = clamp01((t - cc.from) / 0.3);
    ctx.globalAlpha = k;
    ctx.fillStyle = 'rgba(0,0,0,0.72)'; ctx.fillRect(0, H * 0.14, W, H * 0.4);
    ctx.font = font(76);
    stroke((cc.prompt || 'What would you do?').toUpperCase(), W / 2, H * 0.2, '#fff');
    const bw = W * 0.38, bh = 170 * u, y = H * 0.27;
    [['1', cc.a, W * 0.29, '#ff2a2a'], ['2', cc.b, W * 0.71, '#3aa0ff']].forEach(([n, label, x, col]) => {
      ctx.fillStyle = col; ctx.globalAlpha = k * 0.9; ctx.fillRect(x - bw / 2, y, bw, bh); ctx.globalAlpha = k;
      ctx.font = font(96);
      const lw = ctx.measureText(label.toUpperCase()).width;
      ctx.font = font(96 * Math.min(1, (bw - 30 * u) / lw));  // long words (UPSTAIRS) shrink to fit the box
      stroke(label.toUpperCase(), x, y + bh / 2, '#fff');
    });
    ctx.font = font(64);
    stroke('COMMENT YOUR CHOICE', W / 2, H * 0.42, '#ffd23a');
    stroke(cc.footer || 'PART 2 TOMORROW', W / 2, H * 0.49, '#fff');
    ctx.globalAlpha = 1;
  }

  if (spec.endCard && t >= spec.endCard.from) {
    ctx.globalAlpha = clamp01((t - spec.endCard.from) / 0.3);
    ctx.fillStyle = 'rgba(0,0,0,0.6)'; ctx.fillRect(0, H * 0.36, W, H * 0.2);
    wrap(spec.endCard.text.toUpperCase(), 84, W * 0.82).forEach((l, i, arr) => stroke(l, W / 2, H * 0.46 + (i - (arr.length - 1) / 2) * 100 * u, i === arr.length - 1 ? '#ff2a2a' : '#fff'));
    ctx.globalAlpha = 1;
  }

  // captions are drawn LAST so no layer (peephole, cards, flashes, shadows) can ever cover them
  // captions: [{w, t0, t1, who?}] grouped into chunks of up to 3 words, breaking at sentence ends, pauses and speaker changes;
  // current word highlighted. No narrator: words without "who" are the main character's thoughts (italic), spoken words get a name tag.
  const words = spec.captions || [];
  if (words.length) {
    const cur = words.findIndex(w => t >= w.t0 && t < w.t1 + 0.25);
    if (cur >= 0) {
      const brk = (i) => /[.!?…]$/.test(words[i - 1].w) || words[i].t0 - words[i - 1].t1 > 0.4 || words[i].who !== words[i - 1].who;
      let start = 0;
      for (let i = 1, n = 1; i <= cur; i++, n++) {
        if (n >= 3 || brk(i)) { start = i; n = 0; }
      }
      let end = start + 1;
      while (end < words.length && end - start < 3 && !brk(end)) end++;
      const chunk = words.slice(start, end).filter(w => w.t0 <= t);
      const who = words[start].who;
      const full = chunk.map(w => w.w.toUpperCase()).join(' ');
      const cf = (px) => (who ? '' : 'italic ') + font(px);
      ctx.font = cf(84);  // owner: a touch smaller than 92
      const fit = Math.min(1, (W * 0.86) / ctx.measureText(full).width);  // long lines shrink to stay inside the safe margins
      ctx.font = cf(84 * fit);
      const fw = ctx.measureText(full).width;
      ctx.fillStyle = 'rgba(0,0,0,0.45)';  // soft backing so captions read over any shadow or red flash
      ctx.fillRect(W / 2 - fw / 2 - 28 * u, H * 0.62 - 62 * u, fw + 56 * u, 124 * u);
      if (who) {  // speaker's name tag, left-aligned on top of the caption box
        ctx.font = font(46); const tw = ctx.measureText(who).width + 36 * u, tx = W / 2 - fw / 2 - 28 * u;
        ctx.fillStyle = '#c81e1e'; ctx.fillRect(tx, H * 0.62 - 128 * u, tw, 66 * u);
        ctx.textAlign = 'left'; ctx.fillStyle = '#fff'; ctx.fillText(who, tx + 18 * u, H * 0.62 - 94 * u);
        ctx.font = cf(84 * fit);
      }
      let x = W / 2 - fw / 2;
      ctx.textAlign = 'left';
      for (const w of chunk) {
        const txt = w.w.toUpperCase(), active = t >= w.t0 && t < w.t1;
        stroke(txt, x, H * 0.62, active ? '#ffd23a' : '#ffffff');
        x += ctx.measureText(txt + ' ').width;
      }
      ctx.textAlign = 'center';
    }
  }

}

// Thumbnail: same brand fonts/colors on every video. Dark scene frame, 2-4 big words (last one red), part badge, channel tag.
function drawThumb(ctx, spec, W, H, u, font, wrap, stroke) {
  const th = spec.thumb || {};
  const g = ctx.createLinearGradient(0, 0, 0, H);
  g.addColorStop(0, 'rgba(0,0,0,0.55)'); g.addColorStop(0.45, 'rgba(0,0,0,0.1)'); g.addColorStop(1, 'rgba(0,0,0,0.6)');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  const lines = wrap((th.text || '').toUpperCase(), 150, W * 0.86);
  ctx.save(); ctx.shadowColor = 'rgba(255,30,30,0.85)'; ctx.shadowBlur = 40 * u;  // the one glowing red element
  lines.forEach((l, i) => { ctx.font = font(150); ctx.lineWidth = 20 * u; stroke(l, W / 2, H * 0.24 + i * 165 * u, i === lines.length - 1 ? '#ff2a2a' : '#ffffff'); });
  ctx.restore();
  if (th.badge) {
    ctx.font = font(64); const bw = ctx.measureText(th.badge).width + 60 * u;
    ctx.fillStyle = '#ff2a2a'; ctx.fillRect(W / 2 - bw / 2, H * 0.24 + lines.length * 165 * u, bw, 100 * u);
    ctx.fillStyle = '#fff'; ctx.fillText(th.badge, W / 2, H * 0.24 + lines.length * 165 * u + 52 * u);
  }
  ctx.font = font(44); ctx.fillStyle = 'rgba(255,255,255,0.8)'; ctx.fillText('HOLLOW FLOOR', W / 2, H * 0.9);
}
