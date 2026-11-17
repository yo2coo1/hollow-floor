"""Where rendered videos live between cloud runs.
On this PC: just out/. In GitHub Actions (GITHUB_RUN_ID set): each run that renders copies its new files to out/_new/,
the workflow uploads that folder as the artifact "renders-<run id>" (kept 5 days), and later runs download it again
when they need to review or post. Artifacts expire on their own, which also covers "delete files after posting"."""
import io
import json
import os
import shutil
import subprocess
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .config import ROOT, get_logger

log = get_logger("storage")


def in_cloud():
    return bool(os.environ.get("GITHUB_RUN_ID"))


def artifact_name():
    return f"renders-{os.environ['GITHUB_RUN_ID']}" if in_cloud() else None


def stage(paths):
    """Copy freshly rendered files into out/_new (same relative layout) for the workflow to upload."""
    if not in_cloud():
        return None
    for p in paths:
        p = Path(p)
        if p.exists():
            dst = ROOT / "out" / "_new" / p.relative_to(ROOT / "out")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
            for f in (p, dst):  # remember which run made it, so an older local file with the same name is never mistaken for it
                _stamp(f).write_text(artifact_name())
    return artifact_name()


def _stamp(p):
    return Path(p).with_name(Path(p).name + ".from")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _download(url, headers):
    """GitHub answers an artifact download with a redirect to a signed storage URL. urllib would resend our GitHub
    token there and the storage server rejects it (HTTP 401), so follow the redirect ourselves, without the token."""
    try:
        r = urllib.request.build_opener(_NoRedirect).open(urllib.request.Request(url, headers=headers), timeout=60)
        return r.read()  # no redirect (served directly)
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        return urllib.request.urlopen(e.headers["Location"], timeout=300).read()


def ensure(rel_path, artifact):
    """Make sure out/<file> exists locally, downloading its run's artifact if needed. Returns the absolute path.
    The code repo is public, so artifacts are encrypted (renders.enc, key = ARTIFACT_KEY secret) and anyone can
    list them but not watch them. Renders made before the repo went public are kept in the private data repo."""
    p = ROOT / rel_path
    kept = ROOT / "data" / "renders" / Path(rel_path).relative_to("out")
    fresh = lambda: p.exists() and _stamp(p).exists() and _stamp(p).read_text().strip() == (artifact or "")
    if kept.exists() and not fresh():  # bring the whole story folder: posting also needs the thumbnails next to the video
        shutil.copytree(kept.parent, p.parent, dirs_exist_ok=True)
        _stamp(p).write_text(artifact or "")
    # a local file only counts if it came from this artifact: old test renders share these names (2026-11-16 bug:
    # Part 2s went out with Nov 15 test renders in the old voice instead of the approved cloud renders)
    if not artifact or fresh():
        return p
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    lst = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/artifacts?name={artifact}&per_page=1", headers=h), timeout=60).read())
    if not lst.get("artifacts"):
        raise FileNotFoundError(f"artifact {artifact} not found (expired?) for {rel_path}")
    data = _download(lst["artifacts"][0]["archive_download_url"], h)
    z = zipfile.ZipFile(io.BytesIO(data))
    if "renders.enc" in z.namelist():
        plain = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-pass", "env:ARTIFACT_KEY"],
                               input=z.read("renders.enc"), capture_output=True, check=True).stdout
        tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz").extractall(ROOT / "out", filter="data")
    else:
        z.extractall(ROOT / "out")
    log.info("restored %s from %s", rel_path, artifact)
    if not p.exists():
        raise FileNotFoundError(f"{rel_path} not inside {artifact}")
    _stamp(p).write_text(artifact)
    return p
