"""python -m tests.test_cross_queue: after a YouTube post, Part 1s join the back of the Instagram/TikTok queues and a
Part 2 never posts on a platform before its Part 1."""
import json, shutil, sys, tempfile
from pathlib import Path
from hollowfloor import db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hand_post  # noqa: E402

root = Path(tempfile.mkdtemp())
data, qdir = root / "data_clone", root / "q"
(data / "data").mkdir(parents=True); qdir.mkdir(); (root / "out" / "hand").mkdir(parents=True)
hand_post.ROOT = root
con = db.connect(str(data / "data" / "hollowfloor.db"))
pid = 0
for sid in (1, 2):
    con.execute("INSERT INTO stories (id, premise, status) VALUES (?, ?, 'posted')", (sid, f"p{sid}"))
    for part in (1, 2):
        vid = con.execute("INSERT INTO videos (story_id, part, file_path, status) VALUES (?,?,?,'approved')", (sid, part, "x")).lastrowid
        for pf in ("youtube", "instagram", "tiktok"):
            pid += 1
            con.execute("INSERT INTO posts (id, video_id, platform, caption, status) VALUES (?,?,?,?, 'queued')", (pid, vid, pf, f"{pf} s{sid}p{part}"))
            (root / "out" / "hand" / f"post_{pid}.mp4").write_bytes(b"x")
con.commit(); con.close()
yt = {(1, 1): 1, (1, 2): 4, (2, 1): 7, (2, 2): 10}  # YouTube post ids
order = lambda f: [(i["story"], i["part"]) for i in json.loads((qdir / f).read_text(encoding="utf-8"))]

(qdir / "tt_posted.txt").write_text("1 2026-11-16 posted\n")  # TikTok already has story 1 Part 1
hand_post.cross_queue(data, yt[(1, 1)], qdir)
assert order("ig_queue.json") == [(1, 1)] and not (qdir / "tt_queue.json").exists()  # already live on TikTok: not queued twice
hand_post.cross_queue(data, yt[(2, 1)], qdir)
hand_post.cross_queue(data, yt[(1, 2)], qdir)
assert order("ig_queue.json") == [(1, 1), (1, 2), (2, 1)], order("ig_queue.json")  # Part 2 right after its queued Part 1
assert order("tt_queue.json") == [(1, 2), (2, 1)]  # Part 1 live there: Part 2 goes first
hand_post.cross_queue(data, yt[(1, 2)], qdir)  # repeat: no duplicates
assert order("ig_queue.json") == [(1, 1), (1, 2), (2, 1)]
assert json.loads((qdir / "ig_queue.json").read_text(encoding="utf-8"))[0]["caption"] == "instagram s1p1"
shutil.rmtree(root)
print("cross-queue self-check: OK")
