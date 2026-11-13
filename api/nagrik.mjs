// Vercel function: Nagrik's own API (/api/v1/*, /api/health, /__manifest, *.data), the same code as the
// local server (nagrik/backend/server.mjs). vercel.json rewrites those paths here with ?__path=<original>.
import { handle } from '../nagrik/backend/server.mjs';

export default function nagrik(req, res) {
  const url = new URL(req.url, 'http://localhost');
  const original = url.searchParams.get('__path');
  if (original) {
    url.searchParams.delete('__path');
    const q = url.searchParams.toString();
    req.url = original + (q ? '?' + q : '');
  }
  return handle(req, res);
}
