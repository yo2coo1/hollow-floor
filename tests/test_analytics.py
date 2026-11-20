"""python -m tests.test_analytics — A/B winners are judged relative to each platform's own average."""
import json
from hollowfloor import analytics, db

con = db.connect(":memory:")
con.execute("INSERT INTO stories (id, premise, story_type, status) VALUES (1, 'p', 'night shifts', 'posted')")
con.execute("INSERT INTO videos (id, story_id, part, branch, status) VALUES (1, 1, 1, '', 'posted')")
# YouTube (A) is small: 1,000 views where its average is 1,000. TikTok (B) is big: 5,000 where its average is 10,000.
con.execute("INSERT INTO videos (id, story_id, part, branch, status) VALUES (2, 1, 2, 'A', 'posted')")
for pid, pf, var, vid in ((1, "youtube", "A", 1), (2, "tiktok", "B", 1), (3, "youtube", "A", 2), (4, "tiktok", "B", 2)):
    con.execute("INSERT INTO posts (id, video_id, platform, variant, status) VALUES (?,?,?,?, 'posted')", (pid, vid, pf, var))
for pid, views in ((1, 1000), (2, 5000), (3, 1000), (4, 15000)):
    con.execute("INSERT INTO metrics (post_id, views, kind, raw) VALUES (?,?, '72h', ?)", (pid, views, json.dumps({"avg_view_pct": 50})))
con.execute("INSERT INTO experiments (story_id, tested, version_a, version_b) VALUES (1, 'title+thumb part1', 'a', 'b')")
con.commit()
assert analytics.decide_experiments(con) == 1
assert con.execute("SELECT winner FROM experiments").fetchone()[0] == "A"  # B had more views but did worse for its platform

# 30-day rule: YouTube stats for posts older than 30 days are deleted and not fetched again
from datetime import datetime, timezone
now = datetime(2026, 12, 31, tzinfo=timezone.utc)
con.execute("UPDATE posts SET posted_at='2026-11-01T12:00:00+00:00'")
analytics.youtube_stats = lambda *a: (_ for _ in ()).throw(AssertionError("must not fetch old posts"))
analytics.step_metrics(con, now)
left = {r[0] for r in con.execute("SELECT p.platform FROM metrics m JOIN posts p ON p.id=m.post_id")}
assert left == {"tiktok"}, left
# analytics lag: a snapshot saved before they existed (0% from the old bug) is filled in on a later run
con.execute("INSERT INTO stories (id, premise, story_type, status) VALUES (2, 'q', 'night shifts', 'posted')")
con.execute("INSERT INTO videos (id, story_id, part, branch, status) VALUES (3, 2, 1, '', 'posted')")
con.execute("INSERT INTO posts (id, video_id, platform, platform_post_id, posted_at, status, url) VALUES (9, 3, 'youtube', 'vid9', '2026-12-28T00:00:00+00:00', 'posted', 'u')")
con.execute("INSERT INTO metrics (post_id, views, kind, raw) VALUES (9, 500, '24h', ?)", (json.dumps({"views": 500, "avg_view_pct": 0}),))
con.commit()
analytics.youtube_stats = lambda vid, at: {"views": 900, "engaged_views": 120, "watch_min": 40, "avg_view_pct": 61.5, "follows": 2}
analytics.step_metrics(con, now)
raw = json.loads(con.execute("SELECT raw FROM metrics WHERE post_id=9 AND kind='24h'").fetchone()[0])
assert raw["avg_view_pct"] == 61.5 and raw["engaged_views"] == 120 and raw["views"] == 500, raw
analytics.youtube_stats = lambda vid, at: {"views": 900}  # still lagging: nothing is overwritten with zeros
analytics.step_metrics(con, now)
print("analytics self-check: OK")
