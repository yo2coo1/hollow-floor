"""Loads config.toml and secrets (.env locally, real env vars in GitHub Actions).
Every loaded secret is masked out of all log output."""
import logging
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))


def _load_dotenv(path=ROOT / ".env"):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()
_SECRET_NAMES = [l.split("=", 1)[0] for l in (ROOT / ".env.example").read_text().splitlines()
                 if "=" in l and not l.startswith("#")]


def secret(name):
    """Returns the secret or None. Never log the return value directly (the filter masks it anyway)."""
    return os.environ.get(name) or None


class _Redact(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        for n in _SECRET_NAMES:
            v = os.environ.get(n)
            if v and len(v) >= 6:
                msg = msg.replace(v, "***")
        record.msg, record.args = msg, ()
        return True


def get_logger(name="hollowfloor"):
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        h.addFilter(_Redact())
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log
