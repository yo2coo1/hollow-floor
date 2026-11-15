"""Step 4: check a story file, save it to the database, and render Part 1 plus both Part 2 branches.
Usage: python scripts/make_story.py stories/0001_notes.json [--scale 0.5] [--only part1]"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import subprocess  # noqa: E402
from hollowfloor import build, db, metadata, story  # noqa: E402
from hollowfloor.config import ROOT  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("file"); ap.add_argument("--scale", type=float, default=1.0); ap.add_argument("--only")
a = ap.parse_args()
s = json.loads(Path(a.file).read_text(encoding="utf-8"))
con = db.connect()
existing = con.execute("SELECT id FROM stories WHERE premise=?", (s["premise"],)).fetchone()
problems = story.check(s, None if existing else con) + story.cue_problems(s)
if problems:
    sys.exit("story failed checks:\n  " + "\n  ".join(problems))
sid = existing["id"] if existing else story.save(con, s)
print(f"story {sid} passed checks ({'already saved' if existing else 'saved'}); words:",
      {k: len(story.words(v)) for k, v in s["scripts"].items()})
out = Path(f"out/story_{sid:04d}"); out.mkdir(parents=True, exist_ok=True)
for key in ([a.only] if a.only else ["part1", "part2A", "part2B"]):
    r = build.build(story.scene_for(s, key), s["scripts"][key], str(out / f"{key}.mp4"), scale=a.scale)
    print(key, r)
    # thumbnails A/B from the tensest frames: just after the last scare (red, glowing eyes) and the beat before it
    th = metadata.versions(s.get("thumbs", {}).get(key, s.get("thumbnail_text", "")))
    for v, t in zip("AB", r["thumbs"]):
        subprocess.run(["node", str(ROOT / "render/render.js"), str(out / key / "scene.resolved.json"), "x", "--thumb", str(t),
                        str(out / f"{key}_thumb{v}.jpg"), th[v], "PART 1" if key == "part1" else "PART 2"], check=True)
    if key == "part1":  # Part 1 exists as a file now; Part 2 gets its row once the vote picks a branch
        con.execute("INSERT OR REPLACE INTO videos (story_id, part, branch, file_path, thumbnail_path, seconds, rendered_at, status) VALUES (?,1,'',?,?,?,?,'rendered')",
                    (sid, r["video"], str(out / "part1_thumbA.jpg"), r["seconds"], db.now()))
        con.commit()
metadata.record_experiment(con, sid, s)
posts = {key: {pf: metadata.post_text(s, key, pf, sid) for pf in ("youtube", "tiktok", "instagram")} for key in ("part1", "part2A", "part2B")}
(out / "posts_preview.json").write_text(json.dumps(posts, indent=1, ensure_ascii=False), encoding="utf-8")
st = con.execute("SELECT status FROM stories WHERE id=?", (sid,)).fetchone()["status"]
for nxt in db.STORY_FLOW[db.STORY_FLOW.index(st) + 1: db.STORY_FLOW.index("rendered") + 1]:
    db.set_story_status(con, sid, nxt)  # scripted -> approved (passed gates) -> rendered
print("story status:", con.execute("SELECT status FROM stories WHERE id=?", (sid,)).fetchone()["status"])
