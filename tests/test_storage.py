"""python -m tests.test_storage: encrypted render artifacts round-trip, and pre-public renders are found in the data repo."""
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from hollowfloor import storage

root = Path(tempfile.mkdtemp())
storage.ROOT = root
os.environ.update(ARTIFACT_KEY="test-key-123", GITHUB_REPOSITORY="o/r", GITHUB_TOKEN="t")

# what the workflow does before uploading: tar out/_new | openssl enc -> renders.enc
new = root / "src" / "story_0042"; new.mkdir(parents=True); (new / "part1.mp4").write_bytes(b"video-bytes")
tgz = io.BytesIO()
with tarfile.open(fileobj=tgz, mode="w:gz") as t:
    t.add(new, arcname="story_0042")
enc = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-pass", "env:ARTIFACT_KEY"],
                     input=tgz.getvalue(), capture_output=True, check=True).stdout
assert b"video-bytes" not in enc
zb = io.BytesIO(); zipfile.ZipFile(zb, "w").writestr("renders.enc", enc)


def fake_open(req, timeout=0):
    body = json.dumps({"artifacts": [{"archive_download_url": "https://x/zip"}]}).encode() if "artifacts?" in req.full_url else zb.getvalue()
    return mock.Mock(read=lambda: body)


with mock.patch("urllib.request.urlopen", fake_open), mock.patch.object(storage, "_download", lambda url, h: zb.getvalue()):
    p = storage.ensure("out/story_0042/part1.mp4", "renders-1")
assert p.read_bytes() == b"video-bytes"
# an older local file with the same name (e.g. a test render) is replaced by the artifact's version
p.write_bytes(b"old-test-render"); (p.parent / "part1.mp4.from").unlink()
with mock.patch("urllib.request.urlopen", fake_open), mock.patch.object(storage, "_download", lambda url, h: zb.getvalue()):
    assert storage.ensure("out/story_0042/part1.mp4", "renders-1").read_bytes() == b"video-bytes"

# renders made before the repo went public: kept in the private data repo, no download needed
(root / "data" / "renders" / "story_0001").mkdir(parents=True)
(root / "data" / "renders" / "story_0001" / "part1.mp4").write_bytes(b"old")
with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no download expected")):
    assert storage.ensure("out/story_0001/part1.mp4", "renders-old").read_bytes() == b"old"
shutil.rmtree(root)
print("storage self-check: OK")
