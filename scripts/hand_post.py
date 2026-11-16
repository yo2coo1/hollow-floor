"""Hand-posting helper for the owner's PC (config posting.youtube_privacy = "hand").
YouTube keeps API uploads private until the API audit passes, so the cloud leaves YouTube posts queued and a scheduled
Claude task on this PC uploads them publicly in YouTube Studio, then reports each one back with `done`.

  python scripts/hand_post.py due  <data repo clone>             -> JSON list of approved, due YouTube posts with a ready upload file
  python scripts/hand_post.py done <data repo clone> <post id> <shorts url>   -> commits data/hand_posted/<id>.json and pushes

The cloud run imports data/hand_posted/*.json into the database (pipeline.step_hand_posted).
Needs GITHUB_TOKEN (for encrypted render artifacts), ARTIFACT_KEY (.env) and FFMPEG_PATH."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hollowfloor import storage  # noqa: E402
from hollowfloor.config import ROOT  # noqa: E402

LIMIT = 9_600_000  # the browser upload tool takes files up to 10 MB


def fit(src, dst):
    """Copy, or re-encode two-pass x264 to ~9.3 MB when the render is too big for the browser upload."""
    if src.stat().st_size <= LIMIT:
        shutil.copy2(src, dst); return
    ff = os.environ.get("FFMPEG_PATH", "ffmpeg")
    probe = str(Path(ff).with_name("ffprobe" + Path(ff).suffix)) if os.sep in ff or "/" in ff else "ffprobe"
    dur = float(subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                               capture_output=True, text=True, check=True).stdout)
    kbps = int(9_300_000 * 8 / dur / 1000) - 128
    log = str(dst.with_suffix(""))
    for extra in (["-pass", "1", "-an", "-f", "null", os.devnull],
                  ["-pass", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)]):
        subprocess.run([ff, "-y", "-v", "error", "-i", str(src), "-c:v", "libx264", "-preset", "slow", "-b:v", f"{kbps}k",
                        "-passlogfile", log] + extra, check=True)
    for f in dst.parent.glob(dst.stem + "*.log*"):
        f.unlink()
    assert dst.stat().st_size <= 10_000_000, f"{dst} still too big"


def due(data):
    con = sqlite3.connect(Path(data) / "data" / "hollowfloor.db"); con.row_factory = sqlite3.Row
    soon = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(timespec="seconds")
    marked = {f.stem for f in (Path(data) / "data" / "hand_posted").glob("*.json")}
    rows = con.execute("SELECT p.id, p.title, p.caption, p.scheduled_at, v.story_id, v.part, v.file_path, v.artifact FROM posts p "
                       "JOIN videos v ON v.id=p.video_id WHERE p.platform='youtube' AND p.status='queued' AND v.status='approved' "
                       "AND p.scheduled_at <= ? ORDER BY p.scheduled_at", (soon,)).fetchall()
    out = []
    for r in rows:
        if str(r["id"]) in marked:
            continue  # done, waiting for the next cloud run to import it
        desc = r["caption"]
        if r["part"] == 2:  # same as pipeline._part1_link
            p1 = con.execute("SELECT p.url FROM posts p JOIN videos v ON v.id=p.video_id WHERE v.story_id=? AND v.part=1 "
                             "AND p.platform='youtube' AND p.status='posted'", (r["story_id"],)).fetchone()
            desc += f"\n\nPart 1: {p1['url']}" if p1 else ""
        item = {"post_id": r["id"], "story_id": r["story_id"], "part": r["part"], "scheduled_at": r["scheduled_at"],
                "title": r["title"], "description": desc}
        try:
            src = storage.ensure(r["file_path"], r["artifact"])
            dst = ROOT / "out" / "hand" / f"post_{r['id']}.mp4"
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                fit(src, dst)
            item["file"] = str(dst)
        except Exception as e:
            item["error"] = f"{type(e).__name__}: {e}"[:300]
        out.append(item)
    print(json.dumps(out, indent=1, ensure_ascii=False))


QUEUES = {"instagram": ("ig_queue.json", "ig_posted.txt"), "tiktok": ("tt_queue.json", "tt_posted.txt")}


def cross_queue(data, post_id, qdir=None):
    """A Part 2 just went up on YouTube: queue the same video for the Instagram/TikTok hand-posting tasks, but only where
    that story's Part 1 is already posted or queued, placed right after its Part 1 so the order is always right.
    shortcut: new Part 1s are not cross-posted (too many for hand posting); the cloud does all platforms once the APIs exist."""
    qdir = Path(qdir or ROOT / "out" / "public5")
    con = sqlite3.connect(Path(data) / "data" / "hollowfloor.db"); con.row_factory = sqlite3.Row
    v = con.execute("SELECT v.id, v.story_id, v.part FROM posts p JOIN videos v ON v.id=p.video_id WHERE p.id=?", (int(post_id),)).fetchone()
    caps = {r["platform"]: r["caption"] for r in con.execute("SELECT platform, caption FROM posts WHERE video_id=?", (v["id"] if v else -1,))}
    con.close()
    src = ROOT / "out" / "hand" / f"post_{int(post_id)}.mp4"
    if not v or v["part"] != 2 or not src.exists():
        return
    for pf, (qname, posted) in QUEUES.items():
        row = {"caption": caps[pf]} if pf in caps else None
        qf, pf_posted = qdir / qname, qdir / posted
        q = json.loads(qf.read_text(encoding="utf-8")) if qf.exists() else []
        done = {l.split()[0] for l in pf_posted.read_text().splitlines() if l.strip()} if pf_posted.exists() else set()
        item = {"story": v["story_id"], "part": 2, "file": str(src), "caption": row["caption"] if row else ""}
        if not row or any(i["story"] == v["story_id"] and i.get("part", 1) == 2 for i in q) or f"{v['story_id']}p2" in done:
            continue
        p1 = [n for n, i in enumerate(q) if i["story"] == v["story_id"] and i.get("part", 1) == 1]
        if p1:
            q.insert(p1[0] + 1, item)
        elif str(v["story_id"]) in done:
            q.insert(0, item)  # Part 1 is live there: this Part 2 goes next
        else:
            continue  # that platform never got Part 1
        qf.write_text(json.dumps(q, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"queued story {v['story_id']} Part 2 for {pf}")


def git(data, *a):
    return subprocess.run(["git", "-C", str(data)] + list(a), capture_output=True, text=True)


def done(data, post_id, url):
    assert url.startswith("https://youtube.com/shorts/") and len(url.rsplit("/", 1)[-1]) == 11, f"bad url {url}"
    f = Path(data) / "data" / "hand_posted" / f"{int(post_id)}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"post_id": int(post_id), "url": url,
                             "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}), encoding="utf-8")
    git(data, "add", str(f.relative_to(data)))
    git(data, "commit", "-qm", f"hand-posted YouTube post {post_id}")
    for _ in range(3):  # a cloud run may have pushed meanwhile; a new small file always rebases cleanly
        if git(data, "push", "-q").returncode == 0:
            print("recorded", post_id, url)
            try:
                cross_queue(data, post_id)
            except Exception as e:  # the YouTube post is already recorded; a queue problem must not undo that
                print("cross-queue failed:", e)
            return
        git(data, "pull", "--rebase", "-q")
    sys.exit("push failed: the marker is committed locally; run `git push` in the data clone")


if __name__ == "__main__":
    cmd, data = sys.argv[1], Path(sys.argv[2])
    due(data) if cmd == "due" else done(data, sys.argv[3], sys.argv[4])
