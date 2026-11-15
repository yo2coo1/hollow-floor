"""Add finished story files to the CLOUD queue (the private hollow-floor-data repo).
Usage: python scripts/queue_story.py <data-repo clone dir> stories/0009_x.json [more.json ...]
Runs every gate against the cloud database (read-only), then copies the file into stories/inbox/ of the clone.
Commit + push the clone; the next pipeline run imports the inbox (pipeline.step_inbox). The database itself is never
touched here, so pushing while a run is active is safe: the run rebases onto the new file and pushes its own changes."""
import json
import os
import shutil
import sys
from pathlib import Path

clone = Path(sys.argv[1]).resolve()
os.environ["HF_DB"] = str(clone / "data" / "hollowfloor.db")  # must be set before hollowfloor.db is imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hollowfloor import db, story  # noqa: E402

con = db.connect()
inbox = clone / "stories" / "inbox"
inbox.mkdir(parents=True, exist_ok=True)
for f in sys.argv[2:]:
    s = json.loads(Path(f).read_text(encoding="utf-8"))
    if con.execute("SELECT id FROM stories WHERE premise=?", (s["premise"],)).fetchone():
        print(f"{f}: already queued, skipped"); continue
    problems = story.all_problems(s, con)
    if problems:
        sys.exit(f"{f} failed checks:\n  " + "\n  ".join(problems))
    shutil.copyfile(f, inbox / Path(f).name)
    print(f"{f}: in the inbox; the next run gives it a story number")
con.rollback()  # read-only: nothing is written to the cloud database from here
