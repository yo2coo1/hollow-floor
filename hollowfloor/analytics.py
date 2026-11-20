"""Section 12: metrics 24 h and 72 h after each post, and the weekly learning report.
YouTube: views/likes from the Data API; watch time, average view %, subscribers gained from the Analytics API.
TikTok/Instagram pulls are added when those APIs are approved."""
import json
from datetime import timedelta

from . import db
from .config import ROOT, get_logger

log = get_logger("analytics")
LEARN = ROOT / "data/learning.json"


def youtube_stats(video_id, posted_at):
    from . import youtube
    tok = youtube.access_token()
    d, _ = youtube._api("GET", "videos", tok, params={"part": "statistics", "id": video_id})
    st = (d.get("items") or [{}])[0].get("statistics", {})
    out = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)), "comments": int(st.get("commentCount", 0))}
    try:  # Analytics API: engaged views, watch time, average view %, subscribers gained. It lags 2-3 days: no rows = not ready
        import urllib.parse, urllib.request
        q = urllib.parse.urlencode({"ids": "channel==MINE", "startDate": posted_at[:10], "endDate": "2099-12-31",
                                    "metrics": "views,estimatedMinutesWatched,averageViewPercentage,subscribersGained",
                                    "filters": f"video=={video_id}"})
        req = urllib.request.Request("https://youtubeanalytics.googleapis.com/v2/reports?" + q, headers={"Authorization": f"Bearer {tok}"})
        rows = json.loads(urllib.request.urlopen(req, timeout=60).read()).get("rows")
        if rows:  # never record 0% for "not counted yet" (2026-11-19 bug: every retention figure was 0)
            # Analytics "views" counts engaged views only (what YouTube's Partner Program counts); the public counter counts every start
            out.update(engaged_views=rows[0][0], watch_min=rows[0][1], avg_view_pct=rows[0][2], follows=rows[0][3])
    except Exception as e:
        log.warning("analytics not available yet for %s: %s", video_id, e)
    return out


YT_KEEP_DAYS = 30  # YouTube Developer Policies: API data is refreshed or deleted within 30 days


def step_metrics(con, now):
    keep = (now - timedelta(days=YT_KEEP_DAYS)).isoformat(timespec="seconds")
    con.execute("DELETE FROM metrics WHERE post_id IN (SELECT id FROM posts WHERE platform='youtube' AND posted_at < ?)", (keep,))
    con.commit()
    for kind, hours in (("24h", 24), ("72h", 72)):
        cutoff = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
        rows = con.execute("SELECT p.* FROM posts p WHERE p.status='posted' AND p.posted_at <= ? AND NOT EXISTS "
                           "(SELECT 1 FROM metrics m WHERE m.post_id=p.id AND m.kind=?) AND p.posted_at >= ?", (cutoff, kind, keep)).fetchall()
        for p in rows:
            if p["platform"] != "youtube" or not p["platform_post_id"] or p["url"].startswith("dry-run"):
                continue
            m = youtube_stats(p["platform_post_id"], p["posted_at"])
            con.execute("INSERT INTO metrics (post_id, views, watch_time_s, likes, follows, saves, kind, raw) VALUES (?,?,?,?,?,?,?,?)",
                        (p["id"], m.get("views"), (m.get("watch_min") or 0) * 60, m.get("likes"), m.get("follows"), None, kind, json.dumps(m)))
            con.commit()
    # analytics arrive days later: fill in snapshots taken before they existed (or saved as 0 by the old bug), up to 7 days
    fill = (now - timedelta(days=7)).isoformat(timespec="seconds")
    for m in con.execute("SELECT m.id, m.raw, p.platform_post_id, p.posted_at FROM metrics m JOIN posts p ON p.id=m.post_id WHERE p.platform='youtube' "
                         "AND p.posted_at >= ? AND p.platform_post_id IS NOT NULL", (fill,)).fetchall():
        raw = json.loads(m["raw"] or "{}")
        if raw.get("avg_view_pct") and raw.get("engaged_views") is not None:
            continue
        a = youtube_stats(m["platform_post_id"], m["posted_at"])
        if a.get("avg_view_pct") is not None:  # keep the snapshot's own view count; add the analytics figures
            raw.update({k: a[k] for k in ("engaged_views", "watch_min", "avg_view_pct", "follows")})
            con.execute("UPDATE metrics SET raw=?, watch_time_s=?, follows=? WHERE id=?", (json.dumps(raw), raw["watch_min"] * 60, raw["follows"], m["id"]))
            con.commit()


def decide_experiments(con):
    """Fill experiments.winner. A and B run on different platforms, so each post is scored against its own
    platform's average (views x average-view-%), which removes the 'TikTok is just bigger' bias."""
    rows = con.execute("""SELECT p.platform, p.variant, v.story_id, v.part, v.branch, m.views, m.raw FROM metrics m JOIN posts p ON p.id=m.post_id
                          JOIN videos v ON v.id=p.video_id WHERE m.kind='72h'""").fetchall()
    def val(r):
        raw = json.loads(r["raw"] or "{}")
        return (r["views"] or 0) * (raw.get("avg_view_pct") or 50) / 100
    by_pf = {}
    for r in rows:
        by_pf.setdefault(r["platform"], []).append(val(r))
    avg = {k: (sum(v) / len(v)) or 1 for k, v in by_pf.items()}
    decided = 0
    for e in con.execute("SELECT * FROM experiments WHERE winner IS NULL").fetchall():
        key = e["tested"].split()[-1]                      # part1 / part2A / part2B
        part, branch = (1, "") if key == "part1" else (2, key[-1])
        score = {"A": [], "B": []}
        for r in rows:
            if r["story_id"] == e["story_id"] and r["part"] == part and (r["branch"] or "") == branch and r["variant"] in score:
                score[r["variant"]].append(val(r) / avg[r["platform"]])
        if score["A"] and score["B"]:
            a, b = sum(score["A"]) / len(score["A"]), sum(score["B"]) / len(score["B"])
            con.execute("UPDATE experiments SET winner=? WHERE id=?", ("A" if a >= b else "B", e["id"]))
            decided += 1
    con.commit()
    return decided


def weekly_report(con):
    """Rank story types and variants by views, watch time and follows; write learning.json the writer reads."""
    decided = decide_experiments(con)
    rows = con.execute("""SELECT s.story_type, p.variant, m.views, m.watch_time_s, m.follows, m.raw FROM metrics m
                          JOIN posts p ON p.id=m.post_id JOIN videos v ON v.id=p.video_id JOIN stories s ON s.id=v.story_id
                          WHERE m.kind='72h'""").fetchall()
    if not rows:
        return "No 72-hour metrics yet, so nothing to learn from."
    by_type, by_var = {}, {}
    for r in rows:
        score = (r["views"] or 0) * 0.01 + (r["watch_time_s"] or 0) / 60 + (r["follows"] or 0) * 5
        by_type.setdefault(r["story_type"], []).append(score)
        by_var.setdefault(r["variant"], []).append(score)
    avg = {k: sum(v) / len(v) for k, v in by_type.items()}
    top = max(avg.values()) or 1
    weights = {k: round(0.5 + v / top, 2) for k, v in avg.items()}  # winners up to 1.5x, never below 0.5x (keep variety)
    LEARN.parent.mkdir(exist_ok=True)
    LEARN.write_text(json.dumps({"type_weights": weights, "variants": {k: sum(v) / len(v) for k, v in by_var.items()}}, indent=1))
    lines = ["Weekly report", "Story types (score = views/100 + watch minutes + 5 x follows):"] + \
            [f"- {k}: {v:.1f} -> weight {weights[k]}" for k, v in sorted(avg.items(), key=lambda x: -x[1])] + \
            ["Title/thumbnail versions:"] + [f"- {k}: {sum(v) / len(v):.1f}" for k, v in by_var.items()] +             [f"A/B tests decided this week: {decided}"]
    return "\n".join(lines)
