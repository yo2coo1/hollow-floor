"""python -m tests.test_pipeline — the whole daily loop with a fake clock, fake Discord/YouTube and no rendering."""
import copy, json, os, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
os.environ["HF_DB"] = str(tmp / "t.db")
os.environ["YOUTUBE_REFRESH_TOKEN"] = "fake"            # YouTube counts as connected
os.environ["HF_MAX_RENDERS"] = "3"
from hollowfloor import db, discord, pipeline, writer, story  # noqa: E402

pipeline.ROOT = tmp
base = json.load(open("stories/0001_notes.json", encoding="utf-8"))
made = []

def fake_write(con, llm=None):
    s = copy.deepcopy(base); n = len(made) + 1
    s["premise"] = f"Test premise number {n} about a different haunted thing {n * 7919}"
    sid = story.save(con, s); made.append(sid); return sid, None
writer.write_one = fake_write
pipeline.story_file = lambda sid: base

def fake_render(con, sid, s, key):
    part = 1 if key == "part1" else 2
    con.execute("INSERT OR REPLACE INTO videos (story_id, part, branch, file_path, thumbnail_path, seconds, rendered_at, status) VALUES (?,?,?,?,?,?,?,'rendered')",
                (sid, part, "" if part == 1 else key[-1], f"out/{sid}_{key}.mp4", f"out/{sid}_{key}_thumbA.jpg", 50, db.now()))
    con.commit()
    return con.execute("SELECT id FROM videos WHERE story_id=? AND part=?", (sid, part)).fetchone()[0]
pipeline.render_part = fake_render

reviews, alerts, state = {}, [], {"paused": False}
discord.send_review = lambda *a, **k: (reviews.__setitem__(str(len(reviews) + 1), "pending") or str(len(reviews)))
discord.review_result = lambda mid: (reviews[mid], "too slow" if reviews[mid] == "declined" else "")
discord.paused = lambda: state["paused"]
discord.alert = lambda t: alerts.append(t)
import hollowfloor.analytics as an  # noqa: E402
an.step_metrics = lambda con, now: None

T = datetime(2026, 12, 1, 13, 0, tzinfo=timezone.utc)  # 08:00 New York
clock = {"now": T}
pipeline.utcnow = lambda: clock["now"]
pipeline.P["youtube_privacy"] = "public"  # this test covers cloud posting, not the hand-posting stopgap
pipeline.S["hold_new_part1"] = False  # the test plans new Part 1s whatever the live setting is
pipeline.S["post_times_local"] = ["12:00", "17:00", "21:00"]  # the test expects 3 slots whatever the live pace is
con = db.connect()
q = lambda sql, *a: con.execute(sql, a).fetchall()

# 1) morning tick: three Part 1 slots (12:00, 17:00, 21:00 local) get stories, renders and review requests
assert pipeline.tick(dry_run=True)
assert len(made) == 3 and len(reviews) == 3, (made, reviews)
assert len(q("SELECT * FROM posts WHERE status='queued' AND platform='youtube'")) == 3
assert len(q("SELECT * FROM posts WHERE status='blocked' AND platform IN ('tiktok','instagram')")) == 6  # blocked, not lost

# 2) nothing posts without approval, even when due
clock["now"] = T + timedelta(hours=5, minutes=5)   # 13:05 local, first slot passed
assert pipeline.tick(dry_run=True)
assert q("SELECT COUNT(*) FROM posts WHERE status='posted'")[0][0] == 0

# 3) owner approves #1 and #2, declines #3 -> #1 posts now, #3 is skipped and its feedback stored
reviews.update({"1": "approved", "2": "approved", "3": "declined"})
assert pipeline.tick(dry_run=True)
assert q("SELECT COUNT(*) FROM posts WHERE status='posted'")[0][0] == 1
assert q("SELECT COUNT(*) FROM posts WHERE status='skipped'")[0][0] == 3  # all 3 platforms of the declined video
assert "too slow" in db.kv_get(con, "owner_feedback")
before = q("SELECT COUNT(*) FROM posts WHERE status='posted'")[0][0]
assert pipeline.tick(dry_run=True) and q("SELECT COUNT(*) FROM posts WHERE status='posted'")[0][0] == before  # never twice

# 4) pause from Discord stops posting; resume lets it through
state["paused"] = True
clock["now"] = T + timedelta(hours=9, minutes=5)   # 17:05 local, slot 2 due
assert pipeline.tick(dry_run=True) and q("SELECT COUNT(*) FROM posts WHERE status='posted'")[0][0] == 1
state["paused"] = False
assert pipeline.tick(dry_run=True) and q("SELECT COUNT(*) FROM posts WHERE status='posted'")[0][0] == 2

# 5) 16 h after Part 1: votes close (6 votes for OPEN), Part 2A is rendered, scheduled +24 h and auto-approved
first_sid = made[0]
pipeline.step_votes.__defaults__ = ({"youtube": lambda vid: [{"author": f"u{i}", "text": "open", "at": "1"} for i in range(6)]},)
posted_at = datetime.fromisoformat(q("SELECT MIN(p.posted_at) FROM posts p JOIN videos v ON v.id=p.video_id WHERE v.story_id=? AND p.status='posted'", first_sid)[0][0])
clock["now"] = posted_at + timedelta(hours=16, minutes=1)
assert pipeline.tick(dry_run=True)
assert q("SELECT winning_choice FROM stories WHERE id=?", first_sid)[0][0] == "A"
p2 = q("SELECT p.scheduled_at FROM posts p JOIN videos v ON v.id=p.video_id WHERE v.story_id=? AND v.part=2 AND p.platform='youtube'", first_sid)[0][0]
assert p2 == (posted_at + timedelta(hours=24)).isoformat(timespec="seconds"), p2
assert q("SELECT status FROM videos WHERE story_id=? AND part=2", first_sid)[0][0] == "approved"  # owner reviews Part 1s only

# 6) unanswered review for 12 h -> exactly one reminder, still not posted
n_alerts = len([a for a in alerts if "Reminder" in a])
waiting = q("SELECT COUNT(*) FROM videos WHERE status='review'")[0][0]
clock["now"] += timedelta(hours=12, minutes=5)
pipeline.tick(dry_run=True); pipeline.tick(dry_run=True)
assert len([a for a in alerts if "Reminder" in a]) == n_alerts + waiting  # one per waiting video, never repeated
assert q("SELECT COUNT(*) FROM posts p JOIN videos v ON v.id=p.video_id WHERE v.status='review' AND p.status='posted'")[0][0] == 0
# 7) render cap: a fresh database with cap 1 renders only one video per run
os.environ["HF_DB"] = str(tmp / "t2.db"); os.environ["HF_MAX_RENDERS"] = "1"
con2 = db.connect(); made.clear(); clock["now"] = T
pipeline.step_plan_part1(con2, clock["now"])
assert con2.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1
print("pipeline self-check: OK")
