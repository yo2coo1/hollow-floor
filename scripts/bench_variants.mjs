// Times a full frame (render + overlay + encode + transfer) on CPU for several quality settings.
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import path from 'node:path';
import puppeteer from 'puppeteer';
const ROOT = path.resolve('.');
const base = JSON.parse(await readFile(process.argv[2], 'utf8'));
const srv = createServer(async (q, r) => { try { const p = path.join(ROOT, decodeURIComponent(q.url.split('?')[0])); const b = await readFile(p);
  r.writeHead(200, { 'Content-Type': p.endsWith('.js') ? 'text/javascript' : p.endsWith('.html') ? 'text/html' : 'application/octet-stream' }).end(b); } catch { r.writeHead(404).end(); } });
await new Promise(r => srv.listen(0, r));
const b = await puppeteer.launch({ headless: true, channel: 'chrome', args: ['--ignore-gpu-blocklist', '--enable-unsafe-swiftshader', '--use-angle=swiftshader'] });
for (const q of [{ aa: true, shadows: "basic", scale: 0.75 }, { aa: true, shadows: "basic", scale: 0.6 }, { aa: false, shadows: "basic", scale: 0.6 }]) {
  const page = await b.newPage();
  await page.goto(`http://127.0.0.1:${srv.address().port}/render/page.html`);
  await page.waitForFunction('window.HF_READY === true');
  await page.evaluate((s) => window.HF.load(s, 1080, 1920), { ...base, quality: q });
  await page.evaluate(t => window.HF.frame(t).length, 1);  // warm up
  const t0 = Date.now(); for (let i = 0; i < 15; i++) await page.evaluate(t => window.HF.frame(t).length, 2 + i * 0.6);
  console.log(JSON.stringify(q).padEnd(46), ((Date.now() - t0) / 15).toFixed(0), 'ms/frame');
  await page.close();
}
await b.close(); srv.close();
