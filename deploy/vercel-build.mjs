// Builds the static site Vercel serves (public/):
//   nagrik/          -> public/        (Nagrik city site; the "Roads" link script is added to every page,
//                                       as nagrik/backend/server.mjs does when it serves them locally)
//   roads/frontend/  -> public/roads/  (Nagrik Roads website + installable phone app)
// The Roads API, media and eval files are served by the backend on Hugging Face (see vercel.json).
import { cp, readdir, readFile, rename, rm, writeFile, mkdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const out = join(root, 'public');
const TAG = '<script src="/nagrik-roads-link.js" defer></script>';

await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
await cp(join(root, 'nagrik'), out, { recursive: true, filter: (src) => !src.startsWith(join(root, 'nagrik', 'backend')) });
await cp(join(root, 'roads', 'frontend'), join(out, 'roads'), { recursive: true });
// same addresses as the FastAPI server: /roads/ = home.html, /roads/dashboard = index.html
// (Vercel serves a folder's index.html before any rewrite, so the files are renamed instead)
await rename(join(out, 'roads', 'index.html'), join(out, 'roads', 'dashboard.html'));
await cp(join(out, 'roads', 'home.html'), join(out, 'roads', 'index.html'));

let pages = 0;
async function inject(dir) {
  for (const e of await readdir(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) { if (p !== join(out, 'roads')) await inject(p); continue; }
    if (!e.name.endsWith('.html')) continue;
    let html = await readFile(p, 'utf8');
    if (html.includes(TAG)) continue;
    html = html.includes('</body>') ? html.replace('</body>', `${TAG}</body>`) : html + TAG;
    await writeFile(p, html);
    pages++;
  }
}
await inject(out);
console.log(`public/ built: Nagrik site (${pages} pages linked to Roads) + /roads/`);
