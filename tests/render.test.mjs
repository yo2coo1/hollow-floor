// node tests/render.test.mjs — scene rule checks (no browser needed)
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { validateScene } from '../render/render.js';

assert.deepStrictEqual(validateScene(JSON.parse(readFileSync('scenes/test_hallway.json', 'utf8'))), []);
assert.match(validateScene({ duration: 10, shots: [{ t: 0 }, { t: 5 }] }).join(), /lasts 5.0s/);
assert.match(validateScene({ duration: 10, shots: [{ t: 1 }] }).join(), /t=0/);
console.log('render self-check: OK');
