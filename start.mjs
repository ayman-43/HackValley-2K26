// Starts the whole application in ONE window: Nagrik Roads (Python/FastAPI, port 8000) and Nagrik (Node, port 5501),
// waits until both answer, then opens the browser. Ctrl+C stops both.
//   node start.mjs            (or double-click start.cmd)
//   HOST=0.0.0.0              lets phones on the same Wi-Fi connect (live scanning)
//   ROADS_PYTHON=<python.exe> use a specific Python
import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const roadsDir = join(root, 'roads');
const ROADS_PORT = Number(process.env.ROADS_PORT || 8000);
const PORT = Number(process.env.PORT || 5501);
const roadsUrl = `http://127.0.0.1:${ROADS_PORT}`;

const say = (m) => console.log(`[start] ${m}`);
const fail = (m) => { console.error(`\n[start] PROBLEM: ${m}\n`); process.exit(1); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function status(url) {
  try { const r = await fetch(url, { redirect: 'manual', signal: AbortSignal.timeout(2000) }); return r.status; }
  catch { return 0; }
}

function findPython() {
  const candidates = [process.env.ROADS_PYTHON, join(roadsDir, '.venv', 'Scripts', 'python.exe'),
    join(roadsDir, '.venv', 'bin', 'python'), 'python', 'py'].filter(Boolean);
  for (const c of candidates) {
    if (c.includes('\\') || c.includes('/')) { if (existsSync(c)) return c; continue; }
    const r = spawnSync(c, ['-c', 'import fastapi, uvicorn'], { stdio: 'ignore' });
    if (r.status === 0) return c;
  }
  return null;
}

// 1. Nagrik Roads (Python)
let child = null;
const existing = await status(`${roadsUrl}/api/health`);
if (existing) {
  // something already runs on the port: use it only if it is the current version (pages under /roads/)
  if (await status(`${roadsUrl}/roads/`) === 200) say(`Nagrik Roads is already running on port ${ROADS_PORT}; using it.`);
  else fail(`an OLD Nagrik Roads / RoadSense server is running on port ${ROADS_PORT}.\n`
    + `  Close its window (or press Ctrl+C in it), then start again.`);
} else {
  const py = findPython();
  if (!py) fail('Python with the Nagrik Roads packages was not found.\n'
    + '  One-time set-up:  cd roads  &&  python -m venv .venv  &&  .venv\\Scripts\\pip install -r requirements.txt');
  if (!existsSync(join(roadsDir, 'backend', 'models', 'roadsense_y26n.pt')))
    say('note: roads/backend/models/roadsense_y26n.pt is missing; detection will fall back to another model if present.');
  say(`starting Nagrik Roads on ${roadsUrl} with ${py}`);
  child = spawn(py, ['-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', String(ROADS_PORT)],
    { cwd: roadsDir, stdio: ['ignore', 'inherit', 'inherit'] });
  child.on('exit', (code) => {
    if (code !== null && code !== 0) console.error(`\n[start] Nagrik Roads stopped (exit code ${code}). See the error above.`);
  });
  let ok = false;
  for (let i = 0; i < 90 && child.exitCode === null; i++) {
    if (await status(`${roadsUrl}/api/health`)) { ok = true; break; }
    await sleep(1000);
  }
  if (!ok) fail('Nagrik Roads did not start (see its error above).');
  say('Nagrik Roads is ready.');
}

// 2. Nagrik (Node) - the public address; forwards /roads, /api, /media, /eval to Nagrik Roads
process.env.ROADS_URL = roadsUrl;
process.env.PORT = String(PORT);
if (await status(`http://127.0.0.1:${PORT}/`)) fail(`port ${PORT} is already in use. Close the other Nagrik window first.`);
await import('./nagrik/backend/server.mjs');
for (let i = 0; i < 20 && !(await status(`http://127.0.0.1:${PORT}/roads/`)); i++) await sleep(500);

const url = `http://localhost:${PORT}/`;
say(`ready:  ${url}   (Nagrik Roads: ${url}roads/)`);
if (process.env.HOST === '0.0.0.0') say(`phones on the same Wi-Fi: http://<this laptop's IP>:${PORT}/roads/app`);
say('keep this window open; press Ctrl+C to stop both servers.');
if (!process.env.NO_BROWSER && process.platform === 'win32') spawn('cmd', ['/c', 'start', '', url], { stdio: 'ignore', detached: true }).unref();

const stop = () => { if (child && child.exitCode === null) child.kill(); process.exit(0); };
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
