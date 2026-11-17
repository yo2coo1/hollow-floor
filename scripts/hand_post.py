"""Hand-posting helper for the owner's PC (config posting.youtube_privacy = "hand").
YouTube keeps API uploads private until the API audit passes, so the cloud leaves YouTube posts queued and a scheduled
Claude task on this PC uploads them publicly in YouTube Studio, then reports each one back with `done`.

  python scripts/hand_post.py due  <data repo clone>             -> JSON list of approved, due YouTube posts with a ready upload file
  python scripts/hand_post.py done <data repo clone> <post id> <shorts url>   -> commits data/hand_posted/<id>.json and pushes
  python scripts/hand_post.py mark <data repo clone> instagram|tiktok <story> <part> [url]   -> same, for a Reel/TikTok posted by hand

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
            # never reuse a copy of an older render (a download keeps its original date, so its stamp's date counts too)
            made = max(f.stat().st_mtime for f in (src, src.with_name(src.name + ".from")) if f.exists())
            if not dst.exists() or dst.stat().st_mtime < made:
                fit(src, dst)
            item["file"] = str(dst)
        except Exception as e:
            item["error"] = f"{type(e).__name__}: {e}"[:300]
        out.append(item)
    print(json.dumps(out, indent=1, ensure_ascii=False))


QUEUES = {"instagram": ("ig_queue.json", "ig_posted.txt"), "tiktok": ("tt_queue.json", "tt_posted.txt")}


def cross_queue(data, post_id, qdir=None):
    """A video just went up on YouTube: queue the same file for the Instagram/TikTok hand-posting tasks so all three
    accounts carry the same stories (owner, 2026-11-16). A Part 1 goes to the back of the queue. A Part 2 goes right
    after its Part 1 if that is still queued, first if its Part 1 is already live there, and never where Part 1 is missing."""
    qdir = Path(qdir or ROOT / "out" / "public5")
    con = sqlite3.connect(Path(data) / "data" / "hollowfloor.db"); con.row_factory = sqlite3.Row
    v = con.execute("SELECT v.id, v.story_id, v.part FROM posts p JOIN videos v ON v.id=p.video_id WHERE p.id=?", (int(post_id),)).fetchone()
    caps = {r["platform"]: r["caption"] for r in con.execute("SELECT platform, caption FROM posts WHERE video_id=?", (v["id"] if v else -1,))}
    con.close()
    src = ROOT / "out" / "hand" / f"post_{int(post_id)}.mp4"
    if not v or not src.exists():
        return
    for pf, (qname, posted) in QUEUES.items():
        row = {"caption": caps[pf]} if pf in caps else None
        qf, pf_posted = qdir / qname, qdir / posted
        q = json.loads(qf.read_text(encoding="utf-8")) if qf.exists() else []
        done = {l.split()[0] for l in pf_posted.read_text().splitlines() if l.strip()} if pf_posted.exists() else set()
        part, sid = v["part"], v["story_id"]
        item = {"story": sid, "part": part, "file": str(src), "caption": row["caption"] if row else ""}
        if not row or any(i["story"] == sid and i.get("part", 1) == part for i in q) or (f"{sid}p2" if part == 2 else str(sid)) in done:
            continue
        p1 = [n for n, i in enumerate(q) if i["story"] == sid and i.get("part", 1) == 1]
        if part == 1:
            q.append(item)
        elif p1:
            q.insert(p1[0] + 1, item)
        elif str(v["story_id"]) in done:
            q.insert(0, item)  # Part 1 is live there: this Part 2 goes next
        else:
            continue  # that platform never got Part 1
        qf.write_text(json.dumps(q, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"queued story {sid} Part {part} for {pf}")


def git(data, *a):
    return subprocess.run(["git", "-C", str(data)] + list(a), capture_output=True, text=True)


def _push(data, f, msg):
    """Commit one new marker file in the data clone and push it (new small files always rebase cleanly)."""
    git(data, "add", str(f.relative_to(data)))
    git(data, "commit", "-qm", msg)
    for _ in range(3):  # a cloud run may have pushed meanwhile
        if git(data, "push", "-q").returncode == 0:
            return True
        git(data, "pull", "--rebase", "-q")
    return False


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def done(data, post_id, url):
    assert url.startswith("https://youtube.com/shorts/") and len(url.rsplit("/", 1)[-1]) == 11, f"bad url {url}"
    f = Path(data) / "data" / "hand_posted" / f"{int(post_id)}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"post_id": int(post_id), "url": url, "posted_at": _now()}), encoding="utf-8")
    if not _push(data, f, f"hand-posted YouTube post {post_id}"):
        sys.exit("push failed: the marker is committed locally; run `git push` in the data clone")
    print("recorded", post_id, url)
    try:
        cross_queue(data, post_id)
    except Exception as e:  # the YouTube post is already recorded; a queue problem must not undo that
        print("cross-queue failed:", e)


def mark(data, platform, story, part, url=""):
    """Instagram/TikTok post made by hand: tell the cloud (for the daily report's cross-platform check)."""
    assert platform in QUEUES and int(part) in (1, 2), "usage: mark <data> instagram|tiktok <story> <1|2> [url]"
    f = Path(data) / "data" / "hand_posted" / f"{platform}-{int(story)}-{int(part)}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"platform": platform, "story": int(story), "part": int(part), "url": url or None, "posted_at": _now()}), encoding="utf-8")
    if not _push(data, f, f"hand-posted {platform} story {story} part {part}"):
        sys.exit("push failed: the marker is committed locally; run `git push` in the data clone")
    print("recorded", platform, story, part)


def vote(data, story, winner, counts=""):
    """Part 2 ending counted on the PC (e.g. Instagram comments read in Chrome): the cloud re-renders Part 2 if it changes."""
    assert winner in ("A", "B"), "usage: vote <data> <story> <A|B> [counts note]"
    f = Path(data) / "data" / "hand_posted" / f"vote-{int(story)}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"vote": winner, "story": int(story), "counts": counts or None}), encoding="utf-8")
    if not _push(data, f, f"vote story {story}: {winner}"):
        sys.exit("push failed: the marker is committed locally; run `git push` in the data clone")
    print("recorded vote", story, winner)


if __name__ == "__main__":
    cmd, data = sys.argv[1], Path(sys.argv[2])
    {"due": lambda: due(data), "done": lambda: done(data, *sys.argv[3:5]), "mark": lambda: mark(data, *sys.argv[3:7]), "vote": lambda: vote(data, *sys.argv[3:6])}[cmd]()
