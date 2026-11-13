"""Nagrik Roads backend package.

Settings can be put in backend/.env (KEY=VALUE per line, git-ignored), e.g. ROADSENSE_MONGO_URI and
ROADSENSE_GOOGLE_CLIENT_ID. Real environment variables win over the file.
"""
import os
from pathlib import Path

_env = Path(__file__).resolve().parents[1] / ".env"
if _env.is_file():
    for _line in _env.read_text(encoding="utf-8-sig").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k, _v = _k.strip(), _v.strip()
        if len(_v) >= 2 and _v[0] == _v[-1] and _v[0] in "\"'":
            _v = _v[1:-1]
        os.environ.setdefault(_k, _v)
