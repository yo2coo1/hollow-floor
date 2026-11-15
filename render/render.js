// Renders a scene JSON to MP4: headless Chrome draws each frame with Three.js, FFmpeg encodes.
// Usage: node render/render.js <scene.json> <out.mp4> [--scale 0.5] [--audio file.wav] [--still t out.jpg]
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json' };

// Rules from the spec checked before any render: 9:16 length limits and a new shot at least every 4 s.
export function validateScene(s) {
  const errs = [];
  if (!(s.duration > 0)) errs.push('duration missing');
  if (!s.shots?.length || s.shots[0].t !== 0) errs.push('first shot must start at t=0');
  const cuts = [...(s.shots || []).map(x => x.t), s.duration];
  for (let i = 1; i < cuts.length; i++) if (cuts[i] - cuts[i - 1] > 4.01) errs.push(`shot at ${cuts[i - 1]}s lasts ${(cuts[i] - cuts[i - 1]).toFixed(1)}s (>4s)`);
  return errs;
}

function serve() {
  const srv = createServer(async (req, res) => {
    const p = path.normalize(path.join(ROOT, decodeURIComponent(req.url.split('?')[0])));
    if (!p.startsWith(ROOT)) return res.writeHead(403).end();
    try { const body = await readFile(p); res.writeHead(200, { 'Content-Type': TYPES[path.extname(p)] || 'application/octet-stream' }).end(body); }
    catch { res.writeHead(404).end(); }
  });
  return new Promise(r => srv.listen(0, '127.0.0.1', () => r(srv)));
}

export async function render(scenePath, outPath, { scale = 1, audio = null, still = null } = {}) {
  const spec = JSON.parse(await readFile(scenePath, 'utf8'));
  const errs = validateScene(spec);
  if (errs.length) throw new Error('scene invalid:\n  ' + errs.join('\n  '));
  const fps = spec.fps || 24,  /* 24 fps: blocky animation reads the same and renders 20% faster */ W = Math.round(1080 * scale / 2) * 2, H = Math.round(1920 * scale / 2) * 2;
  const frames = Math.round(spec.duration * fps);

  const srv = await serve();
  const launch = () => puppeteer.launch({
    headless: true,
    ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: 'chrome' }),
    // RENDER_GL=swiftshader forces CPU rendering (what a GPU-less cloud runner does) for timing tests
    args: ['--ignore-gpu-blocklist', '--enable-unsafe-swiftshader', `--use-angle=${process.env.RENDER_GL || 'default'}`, '--no-sandbox'],
  });
  const browser = await launch();
  // Video default: basic shadows + 3D at 75% (2.3x faster on CPU, near-identical look). Stills/thumbnails: full quality.
  if (still) spec.quality = {};  // stills and thumbnails: always full quality (one frame is cheap)
  else if (!spec.quality) spec.quality = { aa: true, shadows: 'basic', scale: 0.75 };
  const t0 = Date.now();
  const extra = [];  // one browser per extra worker: tabs in one browser share a single GPU process and don't run in parallel
  const openPage = async (w = 0) => {
    let br = browser;
    if (w > 0) { br = await launch(); extra.push(br); }
    const page = await br.newPage();
    page.on('pageerror', e => console.error('page error:', e.message));
    await page.goto(`http://127.0.0.1:${srv.address().port}/render/page.html`);
    await page.waitForFunction('window.HF_READY === true');
    await page.evaluate((s, w, h) => window.HF.load(s, w, h), spec, W, H);
    return page;
  };
  const FF = process.env.FFMPEG_PATH || 'ffmpeg';
  const run = (args, stdin) => {
    const p = spawn(FF, ['-y', '-loglevel', 'error', ...args], { stdio: [stdin ? 'pipe' : 'ignore', 'inherit', 'inherit'] });
    return { p, done: new Promise((ok, bad) => p.on('close', c => c === 0 ? ok() : bad(new Error('ffmpeg exit ' + c)))) };
  };
  try {
    if (still) {  // a single frame; with still.thumb = {text, badge} it is a thumbnail
      const page = await openPage();
      const b64 = await page.evaluate((t, th) => { if (th) window.HF.setThumb(th); return window.HF.frame(t, th ? 'thumb' : undefined); }, still.t, still.thumb || null);
      writeFileSync(still.out, Buffer.from(b64, 'base64'));
      return { still: still.out };
    }
    // Parallel: each worker tab renders a contiguous range of frames into its own segment; segments are joined losslessly.
    // On a CPU-only cloud runner the per-frame work is single-threaded, so N cores ~ N times faster.
    const auto = (process.env.CI || process.env.RENDER_GL === 'swiftshader') && os.cpus().length >= 8 ? 2 : 1;  // measured: >2 workers adds nothing
    const workers = Math.max(1, Math.min(Number(process.env.RENDER_WORKERS || auto), Math.ceil(frames / fps)));
    const per = Math.ceil(frames / workers);
    const tmp = mkdtempSync(path.join(os.tmpdir(), 'hfseg-'));
    const segs = [];
    let doneFrames = 0;
    await Promise.all(Array.from({ length: workers }, async (_, w) => {
      const from = w * per, to = Math.min(frames, from + per);
      if (from >= to) return;
      const seg = path.join(tmp, `seg${w}.mp4`); segs[w] = seg;
      const page = await openPage(w);
      const ff = run(['-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', '-r', String(fps), seg], true);
      for (let i = from; i < to; i++) {
        const b64 = await page.evaluate(t => window.HF.frame(t), i / fps);
        if (!ff.p.stdin.write(Buffer.from(b64, 'base64'))) await new Promise(r => ff.p.stdin.once('drain', r));
        if (++doneFrames % fps === 0) process.stdout.write(`
  frame ${doneFrames}/${frames}`);
      }
      ff.p.stdin.end();
      await ff.done;
      await page.close();
    }));
    const list = path.join(tmp, 'list.txt');
    writeFileSync(list, segs.filter(Boolean).map(f => `file '${f.replace(/\\/g, '/')}'`).join('\n'));
    await run(['-f', 'concat', '-safe', '0', '-i', list, ...(audio ? ['-i', audio, '-c:a', 'aac', '-b:a', '160k', '-shortest'] : []),
      '-c:v', 'copy', '-movflags', '+faststart', outPath]).done;
    rmSync(tmp, { recursive: true, force: true });
    const secs = (Date.now() - t0) / 1000;
    console.log(`
  rendered ${frames} frames (${spec.duration}s @ ${W}x${H}, ${workers} worker${workers > 1 ? 's' : ''}) in ${secs.toFixed(1)}s -> ${outPath}`);
    return { secs, frames, W, H, workers };
  } finally {
    await Promise.all([browser, ...extra].map(b => b.close()));
    srv.close();
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [scene, out, ...rest] = process.argv.slice(2);
  const opt = (n) => { const i = rest.indexOf(n); return i >= 0 ? rest[i + 1] : null; };
  const st = rest.indexOf('--still'), th = rest.indexOf('--thumb');  // --thumb <t> <out.jpg> <text> [badge]
  render(scene, out, {
    scale: Number(opt('--scale') || 1),
    audio: opt('--audio'),
    still: st >= 0 ? { t: Number(rest[st + 1]), out: rest[st + 2] }
      : th >= 0 ? { t: Number(rest[th + 1]), out: rest[th + 2], thumb: { text: rest[th + 3], badge: rest[th + 4] } } : null,
  }).catch(e => { console.error(e.message); process.exit(1); });
}
