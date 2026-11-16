"""The daily loop (sections 9, 12, 13, 14b). Every step picks up work by status, so any run can crash and the next
one resumes without doing anything twice. Run `python -m hollowfloor.pipeline tick` on a schedule.

Rhythm: Part 1 day -> 3 new Part 1s at the configured times; Part 2 of each posts 24 h after its Part 1 (so the next
day is Part 2 day automatically). Votes close 16 h after Part 1; the winning branch is rendered and sent for review.
Review period: the first 14 days after the first post, nothing posts without the owner's ✅ in Discord."""
import json
import os
import subprocess
import sys
import traceback
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db, discord, metadata, storage, story, votes  # build (numpy, voice models) is imported only when rendering
from .config import CONFIG, ROOT, get_logger, secret

log = get_logger("pipeline")
S, V, P = CONFIG["schedule"], CONFIG["votes"], CONFIG["posting"]
TZ = ZoneInfo(S["timezone"])
PLATFORMS = P["platforms"]


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(s):
    return datetime.fromisoformat(s) if s else None


def story_file(sid):
    hits = sorted((ROOT / "stories").glob(f"{sid:04d}_*.json"))
    if not hits:
        raise FileNotFoundError(f"no story file for story {sid}")
    return json.loads(hits[0].read_text(encoding="utf-8"))


# ---------------------------------------------------------------- platform availability
def platform_ready(con, pf):
    """(ready, reason). A platform that warned/limited us stays stopped until the owner clears it (section 13 stop switch)."""
    stop = db.kv_get(con, f"stop:{pf}")
    if stop:
        return False, f"stopped: {stop}"
    need = {"youtube": "YOUTUBE_REFRESH_TOKEN", "tiktok": "TIKTOK_REFRESH_TOKEN", "instagram": "INSTAGRAM_ACCESS_TOKEN"}[pf]
    if secret(need):
        return True, ""
    return False, {"youtube": "YouTube not connected yet",
                   "tiktok": "TikTok account and Content Posting API approval pending (after 25 Nov)",
                   "instagram": "Instagram account and API review pending (after 25 Nov)"}[pf]


# ---------------------------------------------------------------- review period
def in_review_period(con, now):
    first = parse(db.kv_get(con, "first_post_at"))
    return first is None or now < first + timedelta(days=S["review_period_days"])


def autopilot_notice(con, now):
    first = parse(db.kv_get(con, "first_post_at"))
    if first and not in_review_period(con, now) and not db.kv_get(con, "autopilot_announced"):
        discord.alert(f"🤖 The {S['review_period_days']}-day review period is over: Hollow Floor is on full autopilot from today. "
                      "Videos that pass the quality gates now post without asking. Daily summaries continue here.")
        db.kv_set(con, "autopilot_announced", iso(now))


# ---------------------------------------------------------------- planning: which slots get which stories
def next_part1_slots(con, now, horizon_h=36):
    """Upcoming Part 1 post times (UTC) within the horizon, on Part 1 days only (every other day from the cycle start)."""
    start = date.fromisoformat(db.kv_get(con, "cycle_start") or now.astimezone(TZ).date().isoformat())
    db.kv_set(con, "cycle_start", start.isoformat())
    out, d = [], now.astimezone(TZ).date()
    while True:
        if (d - start).days % 2 == 0:
            for hhmm in S["post_times_local"]:
                h, m = map(int, hhmm.split(":"))
                t = datetime.combine(d, time(h, m), TZ).astimezone(timezone.utc)
                if now <= t <= now + timedelta(hours=horizon_h):
                    out.append(t)
        if datetime.combine(d, time(0, 0), TZ).astimezone(timezone.utc) > now + timedelta(hours=horizon_h):
            return out
        d += timedelta(days=1)


def taken_slots(con):
    return {r[0] for r in con.execute("SELECT DISTINCT p.scheduled_at FROM posts p JOIN videos v ON v.id=p.video_id WHERE v.part=1")}


# ---------------------------------------------------------------- rendering
def render_part(con, sid, s, key):
    """Renders one part + its two thumbnails; returns the videos row id. Idempotent per (story, part)."""
    part, branch = (1, "") if key == "part1" else (2, key[-1])
    row = con.execute("SELECT id, status FROM videos WHERE story_id=? AND part=?", (sid, part)).fetchone()
    if row and row["status"] not in ("pending", "failed"):
        return row["id"]
    from . import build
    out = ROOT / "out" / f"story_{sid:04d}"
    out.mkdir(parents=True, exist_ok=True)
    r = build.build(story.scene_for(s, key), s["scripts"][key], str(out / f"{key}.mp4"))
    # technical gate (section 13): plays, 9:16, length, audible narration
    tech = technical_check(out / f"{key}.mp4")
    if tech:
        raise RuntimeError(f"technical check failed for story {sid} {key}: {tech}")
    th = metadata.versions(s.get("thumbs", {}).get(key, s.get("thumbnail_text", "")))
    for v, t in zip("AB", r["thumbs"]):
        subprocess.run(["node", str(ROOT / "render/render.js"), str(out / key / "scene.resolved.json"), "x", "--thumb", str(t),
                        str(out / f"{key}_thumb{v}.jpg"), th[v], "PART 1" if part == 1 else "PART 2"], check=True)
    art = storage.stage([out / f"{key}.mp4", out / f"{key}_thumbA.jpg", out / f"{key}_thumbB.jpg"])
    con.execute("INSERT INTO videos (story_id, part, branch, file_path, thumbnail_path, seconds, rendered_at, status, artifact) VALUES (?,?,?,?,?,?,?, 'rendered', ?) "
                "ON CONFLICT(story_id, part) DO UPDATE SET branch=excluded.branch, file_path=excluded.file_path, thumbnail_path=excluded.thumbnail_path, "
                "seconds=excluded.seconds, rendered_at=excluded.rendered_at, status='rendered', artifact=excluded.artifact",
                (sid, part, branch, (out / f"{key}.mp4").relative_to(ROOT).as_posix(), (out / f"{key}_thumbA.jpg").relative_to(ROOT).as_posix(), r["seconds"], db.now(), art))
    con.commit()
    return con.execute("SELECT id FROM videos WHERE story_id=? AND part=?", (sid, part)).fetchone()["id"]


def technical_check(path, min_len=20):
    """-> list of problems (empty = fine)."""
    ffprobe = str(Path(os.environ.get("FFMPEG_PATH", "ffmpeg")).with_name("ffprobe" + (".exe" if sys.platform == "win32" else "")))
    if not Path(ffprobe).exists():
        ffprobe = "ffprobe"
    p = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if p.returncode:
        return ["file does not play"]
    info = json.loads(p.stdout)
    vids = [s for s in info["streams"] if s["codec_type"] == "video"]
    probs = []
    if not vids or (vids[0]["width"], vids[0]["height"]) != (CONFIG["video"]["width"], CONFIG["video"]["height"]):
        probs.append("not 1080x1920")
    if not any(s["codec_type"] == "audio" for s in info["streams"]):
        probs.append("no audio")
    dur = float(info["format"]["duration"])
    if not min_len <= dur <= CONFIG["video"]["max_seconds"] + 15:
        probs.append(f"length {dur:.0f}s out of range")
    ff = os.environ.get("FFMPEG_PATH", "ffmpeg")
    vol = subprocess.run([ff, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    mean = [float(l.split(":")[1].split()[0]) for l in vol.splitlines() if "mean_volume" in l]
    if not mean or mean[0] < -40:
        probs.append("narration not audible")
    return probs


# ---------------------------------------------------------------- steps
def step_plan_part1(con, now, write=True):
    """Fill upcoming Part 1 slots: pick ready stories (or write new ones), render Part 1, create posts, send review."""
    from . import writer
    if S.get("hold_new_part1"):
        log.info("new Part 1s on hold (config schedule.hold_new_part1)"); return
    free = [t for t in next_part1_slots(con, now) if iso(t) not in taken_slots(con)]
    # at most N renders per run (each takes ~15 min on a free cloud machine); the rest happen on the next run
    for slot in free[:int(os.environ.get("HF_MAX_RENDERS", "2"))]:
        row = con.execute("SELECT id FROM stories WHERE status IN ('scripted','approved') AND id NOT IN (SELECT story_id FROM videos WHERE part=1) "
                          "ORDER BY id LIMIT 1").fetchone()
        if row is None:
            if not write:
                log.info("no ready story for slot %s", iso(slot)); return
            sid, _ = writer.write_one(con)
        else:
            sid = row["id"]
        s = story_file(sid)
        if con.execute("SELECT status FROM stories WHERE id=?", (sid,)).fetchone()[0] == "scripted":
            db.set_story_status(con, sid, "approved")  # passed the automatic gates when it was saved
        vid = render_part(con, sid, s, "part1")
        db.set_story_status(con, sid, "rendered")
        queue_posts(con, sid, s, "part1", vid, slot)
        db.set_story_status(con, sid, "scheduled")
        send_for_review(con, vid, sid, s, "part1", slot, now)


def queue_posts(con, sid, s, key, vid, when):
    for pf in PLATFORMS:
        t = metadata.post_text(s, key, pf, sid)
        ready, why = platform_ready(con, pf)
        con.execute("INSERT OR IGNORE INTO posts (video_id, platform, variant, title, caption, scheduled_at, status, error) VALUES (?,?,?,?,?,?,?,?)",
                    (vid, pf, t["variant"], t["title"], t["caption"], iso(when), "queued" if ready else "blocked", None if ready else why))
    con.commit()


def send_for_review(con, vid, sid, s, key, when, now):
    v = con.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
    if v["status"] != "rendered":
        return
    if not in_review_period(con, now):
        con.execute("UPDATE videos SET status='approved' WHERE id=?", (vid,)); con.commit(); return
    yt, tt = metadata.post_text(s, key, "youtube", sid), metadata.post_text(s, key, "tiktok", sid)
    lines = f"**YouTube / Instagram (A/B {yt['variant']}):** {yt['title']}\n**TikTok ({tt['variant']}):** {tt['title']}"
    out = storage.ensure(v["file_path"], v["artifact"])
    thumbs = [out.with_name(f"{key}_thumbA.jpg"), out.with_name(f"{key}_thumbB.jpg")]
    when_local = when.astimezone(TZ).strftime("%a %d %b, %H:%M") + f" ({S['timezone']})"
    note = f"\n**{os.environ['HF_TEST_LABEL']}**" if os.environ.get("HF_TEST_LABEL") else ""
    mid = discord.send_review(out, thumbs, lines, yt["caption"], when_local, note=note)
    con.execute("UPDATE videos SET status='review', review_msg_id=?, review_sent_at=? WHERE id=?", (mid, iso(now), vid)); con.commit()


def step_reviews(con, now):
    """Read the owner's ✅/❌. No answer after 12 h -> one reminder, and the video waits (never posts unanswered)."""
    for v in con.execute("SELECT * FROM videos WHERE status='review'").fetchall():
        result, reason = discord.review_result(v["review_msg_id"])
        if result == "approved":
            con.execute("UPDATE videos SET status='approved' WHERE id=?", (v["id"],))
        elif result == "declined":
            con.execute("UPDATE videos SET status='declined', review_note=? WHERE id=?", (reason, v["id"]))
            con.execute("UPDATE posts SET status='skipped', error='owner said no' WHERE video_id=? AND status IN ('queued','blocked')", (v["id"],))
            st = con.execute("SELECT status FROM stories WHERE id=?", (v["story_id"],)).fetchone()[0]
            if v["part"] == 1 and st not in ("posted", "analyzed", "rejected"):
                db.set_story_status(con, v["story_id"], "rejected")
            fb = json.loads(db.kv_get(con, "owner_feedback", "[]"))
            fb.append({"story": v["story_id"], "part": v["part"], "reason": reason, "at": iso(now)})
            db.kv_set(con, "owner_feedback", json.dumps(fb[-50:]))  # the writer reads these to improve the next stories
        elif not v["reminded"] and parse(v["review_sent_at"]) and now - parse(v["review_sent_at"]) >= timedelta(hours=S["review_reminder_hours"]):
            discord.alert(f"⏰ Reminder: a video is still waiting for your ✅ or ❌ in #reviews (story {v['story_id']}, Part {v['part']}). It won't post until you answer.")
            con.execute("UPDATE videos SET reminded=1 WHERE id=?", (v["id"],))
        con.commit()


def step_post(con, now, dry_run=False):
    """Post every approved, due item. Retry x3, then log and move on; one failure never stops the rest."""
    from . import youtube
    if discord.paused():
        log.info("paused from Discord: not posting"); return
    rows = con.execute("SELECT p.*, v.file_path, v.thumbnail_path, v.story_id, v.part, v.artifact FROM posts p JOIN videos v ON v.id=p.video_id "
                       "WHERE p.status='queued' AND v.status='approved' AND p.scheduled_at <= ? ORDER BY p.scheduled_at", (iso(now),)).fetchall()
    for p in rows:
        if p["platform"] == "youtube" and P.get("youtube_privacy") == "hand":
            continue  # posted publicly by hand in YouTube Studio from the owner's PC (scripts/hand_post.py); see step_hand_posted
        ready, why = platform_ready(con, p["platform"])
        if not ready:
            con.execute("UPDATE posts SET status='blocked', error=? WHERE id=?", (why, p["id"])); con.commit(); continue
        try:
            if dry_run:
                url = f"dry-run://{p['platform']}/{p['id']}"
            elif p["platform"] == "youtube":
                s = story_file(p["story_id"])
                desc = p["caption"] + _part1_link(con, p)
                thumb = ROOT / p["thumbnail_path"]
                if p["variant"] == "B":
                    thumb = thumb.with_name(thumb.name.replace("thumbA", "thumbB"))
                vid = youtube.retry(youtube.upload, storage.ensure(p["file_path"], p["artifact"]), p["title"], desc, s["hashtags"],
                                    privacy=P.get("youtube_privacy", "private"), thumbnail=thumb)
                url = f"https://youtube.com/shorts/{vid}"
            elif p["platform"] == "tiktok":
                from . import tiktok
                pid = youtube.retry(tiktok.upload, storage.ensure(p["file_path"], p["artifact"]), p["caption"],
                                    private=P.get("tiktok_private", True))
                url = f"tiktok://publish/{pid}"
            elif p["platform"] == "instagram":
                from . import instagram
                mid = youtube.retry(instagram.upload, storage.ensure(p["file_path"], p["artifact"]), p["caption"])
                url = f"instagram://media/{mid}"
            con.execute("UPDATE posts SET status='posted', posted_at=?, url=?, platform_post_id=?, attempts=attempts+1, error=NULL WHERE id=?",
                        (iso(now), url, url.rsplit("/", 1)[-1], p["id"]))
            public = not (p["platform"] == "youtube" and P.get("youtube_privacy", "private") != "public")
            if public and not dry_run and not db.kv_get(con, "first_post_at"):
                db.kv_set(con, "first_post_at", iso(now))  # the 14-day review clock starts with the first PUBLIC post
            con.commit()
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            con.execute("UPDATE posts SET status='failed', attempts=attempts+3, error=? WHERE id=?", (msg[:500], p["id"])); con.commit()
            db.log_error(con, f"post:{p['platform']}", msg)
            if "403" in msg or "forbidden" in msg.lower() or "suspend" in msg.lower():
                db.kv_set(con, f"stop:{p['platform']}", msg[:200])  # stop switch: never keep pushing at a platform that refused us
                discord.alert(f"🛑 {p['platform']} refused a post and posting there is STOPPED until you check it:\n`{msg[:300]}`")
            else:
                discord.alert(f"⚠️ Post failed after 3 tries ({p['platform']}, story {p['story_id']} Part {p['part']}): `{msg[:300]}`")
    # a part is 'posted' once it is live on at least one platform
    for (sid,) in con.execute("SELECT DISTINCT v.story_id FROM posts p JOIN videos v ON v.id=p.video_id WHERE p.status='posted' AND v.part=1").fetchall():
        if con.execute("SELECT status FROM stories WHERE id=?", (sid,)).fetchone()[0] == "scheduled":
            db.set_story_status(con, sid, "posted")


def step_hand_posted(con, root=None):
    """Import posts made by hand in YouTube Studio: the owner's PC drops data/hand_posted/<post id>.json into the data repo
    (new small files never conflict with this run's database push), this run copies them into the database and removes them."""
    for f in sorted(((root or ROOT) / "data" / "hand_posted").glob("*.json")):
        h = json.loads(f.read_text(encoding="utf-8"))
        if "platform" in h:  # Instagram/TikTok Reel posted by hand (found by story + part)
            r = con.execute("SELECT p.id FROM posts p JOIN videos v ON v.id=p.video_id WHERE v.story_id=? AND v.part=? AND p.platform=?",
                            (h["story"], h["part"], h["platform"])).fetchone()
            if r:
                con.execute("UPDATE posts SET status='posted', posted_at=?, url=?, error=NULL WHERE id=? AND status!='posted'",
                            (h["posted_at"], h.get("url") or f"{h['platform']}://hand/{h['story']}-{h['part']}", r[0]))
            else:
                log.warning("hand post %s: no %s post row for story %s part %s", f.name, h["platform"], h["story"], h["part"])
            con.commit(); f.unlink(); continue
        con.execute("UPDATE posts SET status='posted', posted_at=?, url=?, platform_post_id=?, attempts=attempts+1, error=NULL WHERE id=? AND status!='posted'",
                    (h["posted_at"], h["url"], h["url"].rsplit("/", 1)[-1], int(h["post_id"])))
        if not db.kv_get(con, "first_post_at"):
            db.kv_set(con, "first_post_at", h["posted_at"])
        sid = con.execute("SELECT v.story_id FROM posts p JOIN videos v ON v.id=p.video_id WHERE p.id=? AND v.part=1", (int(h["post_id"]),)).fetchone()
        if sid and con.execute("SELECT status FROM stories WHERE id=?", (sid[0],)).fetchone()[0] == "scheduled":
            db.set_story_status(con, sid[0], "posted")
        con.commit()
        f.unlink()
        log.info("hand post %s imported: %s", h["post_id"], h["url"])


def _part1_link(con, p):
    """Part 2 descriptions link back to Part 1 on the same platform (YouTube Shorts has no API for 'related video')."""
    if p["part"] != 2:
        return ""
    r = con.execute("SELECT p.url FROM posts p JOIN videos v ON v.id=p.video_id WHERE v.story_id=? AND v.part=1 AND p.platform=? AND p.status='posted'",
                    (p["story_id"], p["platform"])).fetchone()
    return f"\n\nPart 1: {r['url']}" if r else ""


def step_votes(con, now, comment_fetch=None):
    """16 h after Part 1: count votes, pick the branch, render that Part 2, schedule it 24 h after Part 1."""
    from . import youtube
    from . import instagram
    fetch = comment_fetch or {"youtube": youtube.comments, "instagram": instagram.comments}
    rows = con.execute("SELECT s.id, MIN(p.posted_at) AS first FROM stories s JOIN videos v ON v.story_id=s.id AND v.part=1 "
                       "JOIN posts p ON p.video_id=v.id AND p.status='posted' WHERE s.winning_choice IS NULL GROUP BY s.id").fetchall()
    for r in rows:
        first = parse(r["first"])
        if now < first + timedelta(hours=V["close_after_hours"]):
            continue
        sid, s = r["id"], story_file(r["id"])
        comments, yt_failed = {}, False
        for pf in V["count_platforms"]:
            post = con.execute("SELECT p.platform_post_id FROM posts p JOIN videos v ON v.id=p.video_id WHERE v.story_id=? AND v.part=1 AND p.platform=? AND p.status='posted'",
                               (sid, pf)).fetchone()
            if post and post[0] and pf in fetch:  # hand-made Instagram/TikTok posts have no id to read comments from
                try:
                    comments[pf] = fetch[pf](post[0])
                except Exception as e:
                    db.log_error(con, f"votes:{pf}", str(e))
                    yt_failed |= pf == "youtube"
        if yt_failed and now < first + timedelta(hours=V["close_after_hours"] + 8):
            continue  # couldn't read the YouTube votes: try again next run instead of silently picking the default ending
        counts = votes.tally(comments, s["choices"]["A"]["label"], s["choices"]["B"]["label"])
        winner, why = votes.decide(counts, s["choices"]["default"], V["min_votes"])
        con.execute("UPDATE stories SET winning_choice=?, vote_counts=? WHERE id=?", (winner, json.dumps(counts), sid)); con.commit()
        log.info("story %s vote: %s (%s)", sid, winner, why)
        key = f"part2{winner}"
        vid = render_part(con, sid, s, key)
        when = first + timedelta(hours=24)
        queue_posts(con, sid, s, key, vid, when)
        send_for_review(con, vid, sid, s, key, when, now)
        discord.alert(f"🗳️ Story {sid} vote closed: {why}. Part 2 ({s['choices'][winner]['label']}) is rendered and scheduled.")


def step_health(con, run_id, ok, summary):
    db.finish_run(con, run_id, ok, summary)
    status = ROOT / "data" / "status.md"
    status.parent.mkdir(exist_ok=True)
    last = con.execute("SELECT started_at, ok, summary FROM runs ORDER BY id DESC LIMIT 10").fetchall()
    blocked = con.execute("SELECT platform, error, COUNT(*) FROM posts WHERE status='blocked' GROUP BY platform, error").fetchall()
    lines = ["# Hollow Floor status", "", f"Updated {db.now()}", "", "## Last runs"] + \
            [f"- {r['started_at']} {'OK' if r['ok'] else 'FAILED'}: {(r['summary'] or '')[:160]}" for r in last] + \
            ["", "## Blocked"] + [f"- {b[0]}: {b[2]} post(s) waiting ({b[1]})" for b in blocked]
    if db.two_failed_runs_in_a_row(con):
        lines += ["", "## ALERT", "Two runs in a row failed. See errors table and the latest run log."]
        if not db.kv_get(con, f"alerted_run:{run_id}"):
            discord.alert("🚨 Two pipeline runs in a row failed. Check the GitHub Actions log and data/status.md.")
            db.kv_set(con, f"alerted_run:{run_id}", "1")
            open(ROOT / "data" / "ALERT_TWO_FAILURES", "w").write(summary)  # the workflow opens a GitHub issue when it sees this
    status.write_text("\n".join(lines) + "\n", encoding="utf-8")


def step_daily_summary(con, now):
    today = now.astimezone(TZ).date().isoformat()
    if db.kv_get(con, "summary_date") == today or now.astimezone(TZ).hour < 22:
        return
    since = iso(now - timedelta(hours=24))
    posted = con.execute("SELECT p.platform, p.title, p.url FROM posts p WHERE p.status='posted' AND p.posted_at >= ?", (since,)).fetchall()
    fails = con.execute("SELECT COUNT(*) FROM runs WHERE ok=0 AND started_at >= ?", (since,)).fetchone()[0]
    blocked = con.execute("SELECT DISTINCT platform FROM posts WHERE status='blocked'").fetchall()
    waiting = con.execute("SELECT COUNT(*) FROM videos WHERE status='review'").fetchone()[0]
    try:  # the full PDF report (posted videos, top comments, cross-platform check, numbers); the short text below is the fallback
        from . import report
        report.send(con, now, TZ)
        db.kv_set(con, "summary_date", today); return
    except Exception as e:
        db.log_error(con, "report", f"{type(e).__name__}: {e}")
    msg = [f"📊 **Daily summary** ({today})", f"Posted: {len(posted)}"] + [f"• {p['platform']}: {p['title']} {p['url'] or ''}" for p in posted] + \
          [f"Waiting for your review: {waiting}", f"Blocked platforms: {', '.join(b[0] for b in blocked) or 'none'}",
           f"Health: {'✅ all runs OK' if not fails else f'⚠️ {fails} failed run(s) in 24 h'}",
           "Pause everything: type `pause` here. Resume: `resume`."]
    discord.alert("\n".join(msg))
    db.kv_set(con, "summary_date", today)


def step_tokens(con, now):
    """Daily: prove each connected platform's token still refreshes; if not, tell the owner exactly how to fix it."""
    today = now.date().isoformat()
    if db.kv_get(con, "tokens_checked") == today:
        return
    if secret("YOUTUBE_REFRESH_TOKEN"):
        from . import youtube
        try:
            youtube.access_token()
        except Exception as e:
            discord.alert("🔑 The YouTube connection stopped working, so posting to YouTube is waiting (videos keep being made).\n"
                          "Fix: on your PC open the project folder and run `python -m hollowfloor.youtube connect`, sign in, choose "
                          "Hollow Floor, click Allow, then tell Claude to update the YOUTUBE_REFRESH_TOKEN GitHub secret.\n"
                          f"Error: `{str(e)[:200]}`")
            db.log_error(con, "tokens:youtube", str(e))
            if "invalid_grant" in str(e):  # access revoked: privacy policy promises YouTube API data is deleted within 7 days
                con.execute("DELETE FROM metrics WHERE post_id IN (SELECT id FROM posts WHERE platform='youtube')")
                con.commit()
    db.kv_set(con, "tokens_checked", today)


def weekly(con, now):
    """Weekly learning report + a self-test of every step except real posting (section 16)."""
    from . import analytics, build
    report = analytics.weekly_report(con)
    probs = []
    try:
        r = build.build(ROOT / "scenes/test_hallway_voiced.json",
                        "My boss gave me one rule for the night shift. Never open the last door. [pause] Tonight, I heard knocking. "
                        "From the other side. [pause] Then the lights started blinking... [scare] And it opened.", str(ROOT / "out/selftest.mp4"))
        probs += technical_check(ROOT / "out/selftest.mp4", min_len=8)  # the self-test clip is deliberately short
    except Exception as e:
        probs.append(f"render failed: {e}")
    if secret("YOUTUBE_REFRESH_TOKEN"):
        try:
            from . import youtube
            youtube.my_channel()
        except Exception as e:
            probs.append(f"YouTube API: {e}")
    discord.alert(f"🧪 Weekly self-test: {'✅ all steps OK' if not probs else '⚠️ ' + '; '.join(probs)}\n\n{report}")
    return not probs


def needs_render(con, now):
    """Cheap check the workflow runs first, so the heavy render tools are only installed when needed."""
    if not S.get("hold_new_part1") and [t for t in next_part1_slots(con, now) if iso(t) not in taken_slots(con)]:
        return True
    for r in con.execute("SELECT MIN(p.posted_at) FROM stories s JOIN videos v ON v.story_id=s.id AND v.part=1 JOIN posts p ON p.video_id=v.id "
                         "AND p.status='posted' WHERE s.winning_choice IS NULL GROUP BY s.id"):
        if now >= parse(r[0]) + timedelta(hours=V["close_after_hours"]):
            return True
    return False


def step_inbox(con, root=None):
    """Hand-written stories arrive as files in stories/inbox/. Adding a file never conflicts with a running job's
    database commit (the database is one binary file and can't be merged). Each file is gate-checked, saved, and
    filed as stories/<id>_<slug>.json; a failing file is set aside in stories/rejected/ and reported."""
    base = (root or ROOT) / "stories"
    for f in sorted((base / "inbox").glob("*.json")):
        s = json.loads(f.read_text(encoding="utf-8"))
        if con.execute("SELECT id FROM stories WHERE premise=?", (s["premise"],)).fetchone():
            f.unlink(); continue
        problems = story.all_problems(s, con)
        if problems:
            (base / "rejected").mkdir(exist_ok=True)
            f.replace(base / "rejected" / f.name)
            discord.alert(f"📥 Story file `{f.name}` failed the checks and was set aside: " + "; ".join(problems[:3]))
            continue
        sid = story.save(con, s)
        slug = "".join(c if c.isalnum() else "_" for c in s["slug"].lower())[:30]
        (base / f"{sid:04d}_{slug}.json").write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
        f.unlink()
        log.info("inbox: %s queued as story %s", f.name, sid)


def tick(dry_run=False, write=True):
    con = db.connect()
    run_id = db.start_run(con)
    now = utcnow()
    done, ok = [], True
    for name, fn in [("tokens", lambda: step_tokens(con, now)), ("reviews", lambda: step_reviews(con, now)), ("hand", lambda: step_hand_posted(con)), ("inbox", lambda: step_inbox(con)), ("plan", lambda: step_plan_part1(con, now, write)),
                     ("votes", lambda: step_votes(con, now)), ("post", lambda: step_post(con, now, dry_run)),
                     ("metrics", lambda: __import__("hollowfloor.analytics", fromlist=["x"]).step_metrics(con, now)),
                     ("autopilot", lambda: autopilot_notice(con, now)), ("summary", lambda: step_daily_summary(con, now))]:
        try:
            fn(); done.append(f"{name} ok")
        except Exception as e:  # one failing step never stops the others
            ok = False
            done.append(f"{name} FAILED: {e}")
            db.log_error(con, name, traceback.format_exc()[-1500:])
            log.error("step %s failed: %s", name, e)
    step_health(con, run_id, ok, "; ".join(done))
    print("; ".join(done))
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tick"
    if cmd == "needs-render":
        print("true" if needs_render(db.connect(), utcnow()) else "false"); sys.exit(0)
    if cmd == "selftest":  # render the short test clip only (cloud check of voice + renderer; posts and saves nothing)
        from . import build
        build.build(ROOT / "scenes/test_hallway_voiced.json", "[tense] My boss gave me one rule for the night shift. Never open the last door. "
                    "[pause] [scared] Tonight, I heard knocking. From the other side. [pause] Then the lights started blinking... "
                    "[scare] [panic] And it opened!", str(ROOT / "out/selftest.mp4"))
        probs = technical_check(ROOT / "out/selftest.mp4", min_len=8)
        storage.stage([ROOT / "out/selftest.mp4"])  # goes through the encrypted artifact upload, so that path is tested too
        print("selftest:", probs or "OK"); sys.exit(1 if probs else 0)
    if cmd == "weekly":
        sys.exit(0 if weekly(db.connect(), utcnow()) else 1)
    if cmd == "tick":
        sys.exit(0 if tick(dry_run="--dry-run" in sys.argv, write="--no-write" not in sys.argv) else 1)
