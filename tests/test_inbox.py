"""python -m tests.test_inbox: story files dropped into stories/inbox/ are checked, saved and filed by id."""
import json, shutil, tempfile
from pathlib import Path
from unittest import mock
from hollowfloor import db, pipeline

root = Path(tempfile.mkdtemp())
(root / "stories" / "inbox").mkdir(parents=True)
good = json.loads(Path("stories/0011_guestbook.json").read_text(encoding="utf-8"))
shutil.copy("stories/0011_guestbook.json", root / "stories" / "inbox" / "a.json")
bad = dict(good, premise="A different premise entirely so the originality gate passes.", scripts=dict(good["scripts"], part1="Too short."))
(root / "stories" / "inbox" / "b.json").write_text(json.dumps(bad), encoding="utf-8")
con = db.connect(":memory:")
with mock.patch.object(pipeline.discord, "alert") as alert:
    pipeline.step_inbox(con, root)
    pipeline.step_inbox(con, root)  # second run: nothing left to do, no duplicate
files = sorted(p.name for p in (root / "stories").glob("*.json"))
assert files == ["0001_guestbook.json"], files
assert con.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 1
assert (root / "stories" / "rejected" / "b.json").exists() and alert.call_count == 1
assert not list((root / "stories" / "inbox").glob("*.json"))
shutil.rmtree(root)
print("inbox self-check: OK")
