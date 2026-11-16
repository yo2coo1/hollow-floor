"""python -m tests.test_hand_posted: a YouTube post made by hand in Studio is imported once, and the cloud never uploads it itself."""
import json, shutil, tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from hollowfloor import db, pipeline

root = Path(tempfile.mkdtemp())
(root / "data" / "hand_posted").mkdir(parents=True)
con = db.connect(":memory:")
con.execute("INSERT INTO stories (id, premise, status) VALUES (1, 'x', 'scheduled')")
con.execute("INSERT INTO videos (id, story_id, part, file_path, status) VALUES (1, 1, 1, 'out/x.mp4', 'approved')")
con.execute("INSERT INTO posts (id, video_id, platform, scheduled_at, status) VALUES (7, 1, 'youtube', '2026-01-01T00:00:00+00:00', 'queued')")
con.commit()
with mock.patch.dict(pipeline.P, youtube_privacy="hand"), mock.patch.object(pipeline.discord, "paused", return_value=False), \
        mock.patch("hollowfloor.youtube.upload") as up:
    pipeline.step_post(con, datetime.now(timezone.utc))
assert not up.called and con.execute("SELECT status FROM posts WHERE id=7").fetchone()[0] == "queued"
(root / "data" / "hand_posted" / "7.json").write_text(json.dumps({"post_id": 7, "url": "https://youtube.com/shorts/ABCDEFGHIJK", "posted_at": "2026-01-01T00:05:00+00:00"}))
pipeline.step_hand_posted(con, root)
pipeline.step_hand_posted(con, root)  # nothing left: no error, no change
assert tuple(con.execute("SELECT status, platform_post_id FROM posts WHERE id=7").fetchone()) == ("posted", "ABCDEFGHIJK")
assert con.execute("SELECT status FROM stories WHERE id=1").fetchone()[0] == "posted"
assert db.kv_get(con, "first_post_at") == "2026-01-01T00:05:00+00:00"
assert not list((root / "data" / "hand_posted").glob("*.json"))
# an Instagram Reel of a story that has no Instagram row yet (made before Instagram existed) gets one
(root / "data" / "hand_posted" / "instagram-1-1.json").write_text(json.dumps({"platform": "instagram", "story": 1, "part": 1, "url": None, "posted_at": "2026-01-02T00:00:00+00:00"}))
pipeline.step_hand_posted(con, root)
assert con.execute("SELECT status FROM posts WHERE platform='instagram' AND video_id=1").fetchone()[0] == "posted"
shutil.rmtree(root)
print("hand-posted self-check: OK")
