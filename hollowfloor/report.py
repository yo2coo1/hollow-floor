"""Nightly report for the owner: one PDF in Discord (#daily-report, or #alerts if that channel isn't set).
Standard library only (runs in the light cloud job); the PDF is printed by headless Chrome, which the runner already has.
YouTube comments are read live for the report and never stored (YouTube API policy); authors are left out."""
import html
import json
import os
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from . import db, discord
from .config import CONFIG, get_logger

log = get_logger("report")
PLATFORMS = ("youtube", "instagram", "tiktok")
NAMES = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}
APIS_DAY = date(2026, 11, 25)  # owner turns 18: Instagram/TikTok developer apps, so the cloud can post and read stats there


def _yt_top_comments(video_id, n=3):
    from . import youtube
    d = youtube.public_get("commentThreads", {"part": "snippet", "videoId": video_id, "maxResults": 20, "order": "relevance", "textFormat": "plainText"})
    out = []
    for it in d.get("items", []):
        s = it["snippet"]["topLevelComment"]["snippet"]
        out.append((int(s.get("likeCount", 0)), " ".join(s["textDisplay"].split())[:140]))
    return [t for _, t in sorted(out, key=lambda x: -x[0])[:n]]


def _yt_stats(ids):
    from . import youtube
    if not ids:
        return {}
    d, _ = youtube._api("GET", "videos", youtube.access_token(), params={"part": "statistics", "id": ",".join(ids[:50])})
    return {it["id"]: it.get("statistics", {}) for it in d.get("items", [])}


# Payout programs and what they need (checked Nov 2026; the platforms change these, so verify before applying).
GOALS = [("YouTube Partner Program, early tier (fan funding)", {"subs": 500, "views90": 3_000_000}),
         ("YouTube Partner Program, full tier (Shorts ad revenue)", {"subs": 1000, "views90": 10_000_000})]


def _monetization(con, now, subs, problems):
    """Progress toward YouTube's payout thresholds, with a rough date from the last week's growth.
    Only YouTube can be read until the Instagram/TikTok developer apps are connected (from APIS_DAY)."""
    try:
        since90 = (now - timedelta(days=90)).isoformat(timespec="seconds")
        ids = [r[0] for r in con.execute("SELECT platform_post_id FROM posts WHERE platform='youtube' AND status='posted' "
                                         "AND platform_post_id IS NOT NULL AND posted_at >= ?", (since90,))]
        views90 = sum(int(st.get("viewCount", 0)) for i in range(0, len(ids), 50) for st in _yt_stats(ids[i:i + 50]).values())
    except Exception as ex:
        problems.append(f"YouTube 90-day views could not be read: {str(ex)[:120]}")
        return None
    subs = int(subs) if subs is not None else None
    # daily snapshots (aggregate channel numbers only, kept 30 days: YouTube API data policy)
    log_ = json.loads(db.kv_get(con, "growth_log", "{}"))
    log_[now.date().isoformat()] = {"subs": subs, "views90": views90}
    log_ = dict(sorted(log_.items())[-30:])
    db.kv_set(con, "growth_log", json.dumps(log_))
    days = sorted(log_)
    week = [d for d in days if d <= (now.date() - timedelta(days=7)).isoformat()]
    base = log_[week[-1]] if week else log_[days[0]]
    span = max(1, (now.date() - date.fromisoformat(week[-1] if week else days[0])).days)
    rate = {k: ((log_[days[-1]][k] or 0) - (base[k] or 0)) / span for k in ("subs", "views90")}
    goals = []
    for name, need in GOALS:
        have = {"subs": subs or 0, "views90": views90}
        eta = [(need[k] - have[k]) / rate[k] for k in need if have[k] < need[k] and rate[k] > 0]
        reached = all(have[k] >= need[k] for k in need)
        goals.append({"name": name, "need": need, "have": have, "reached": reached,
                      "eta": None if reached or len(eta) < sum(have[k] < need[k] for k in need) else now.date() + timedelta(days=int(max(eta)) + 1)})
    return {"subs": subs, "views90": views90, "rate": rate, "goals": goals, "days_logged": len(days)}


def collect(con, now, tz, live=True):
    """Everything the report shows, as plain data (live=False skips the YouTube API, for tests)."""
    since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    window = (now - timedelta(days=3)).isoformat(timespec="seconds")
    rows = con.execute("SELECT p.*, v.story_id, v.part, v.branch, s.premise, s.title AS stitle FROM posts p JOIN videos v ON v.id=p.video_id "
                       "JOIN stories s ON s.id=v.story_id WHERE p.status='posted' AND p.posted_at >= ? ORDER BY p.posted_at", (since,)).fetchall()
    # one entry per video (story + part) posted today on any platform
    today = {}
    for r in rows:
        e = today.setdefault((r["story_id"], r["part"]), {"story": r["story_id"], "part": r["part"], "title": (r["stitle"] or "").replace(" (Part 1)", ""),
                                                         "about": r["premise"], "platforms": [], "yt_id": None})
        e["platforms"].append(r["platform"])
        if r["platform"] == "youtube":
            e["yt_id"] = r["platform_post_id"]
    # cross-platform match for every video posted in the last 3 days
    match = []
    vids = con.execute("SELECT DISTINCT v.id, v.story_id, v.part FROM videos v JOIN posts p ON p.video_id=v.id "
                       "WHERE p.status='posted' AND p.posted_at >= ? ORDER BY v.story_id, v.part", (window,)).fetchall()
    for v in vids:
        st = {r["platform"]: r for r in con.execute("SELECT platform, status, error FROM posts WHERE video_id=?", (v["id"],))}
        cells = {}
        for pf in PLATFORMS:
            r = st.get(pf)
            if r and r["status"] == "posted":
                cells[pf] = ("ok", "posted")
            elif pf != "youtube" and v["part"] == 1 and v["story_id"] > 10:
                cells[pf] = ("na", f"cloud posts here from {APIS_DAY:%b %d}")  # new Part 1s are YouTube-only until the APIs
            else:
                cells[pf] = ("wait", "in the PC queue" if pf != "youtube" else (r["status"] if r else "not queued"))
        match.append({"story": v["story_id"], "part": v["part"], "cells": cells,
                      "ok": all(c[0] in ("ok", "na") for c in cells.values())})
    stats, comments, subs, subs_box, problems = {}, {}, None, [], []
    if live:
        from . import youtube
        for what, fn in (("stats", lambda: stats.update(_yt_stats([e["yt_id"] for e in today.values() if e["yt_id"]]))),
                         ("comments", lambda: [comments.__setitem__(e["yt_id"], _yt_top_comments(e["yt_id"])) for e in today.values() if e["yt_id"]]),
                         ("subscribers", lambda: subs_box.append((youtube.my_channel() or {}).get("statistics", {}).get("subscriberCount")))):
            try:
                fn()
            except Exception as ex:  # one missing number must not drop the others
                log.warning("YouTube %s unavailable: %s", what, ex)
                problems.append(f"YouTube {what} could not be read: {str(ex)[:120]}")
        subs = subs_box[0] if subs_box else None
    money = _monetization(con, now, subs, problems) if live else None
    votes = [dict(story=r["id"], title=(r["title"] or "").replace(" (Part 1)", ""), winner=r["winning_choice"], counts=json.loads(r["vote_counts"] or "{}"))
             for r in con.execute("SELECT s.id, s.title, s.winning_choice, s.vote_counts FROM stories s JOIN videos v ON v.story_id=s.id AND v.part=2 "
                                  "WHERE s.winning_choice IS NOT NULL AND v.rendered_at >= ?", (since,))]
    review = con.execute("SELECT v.story_id, v.part FROM videos v WHERE v.status='review' ORDER BY v.story_id").fetchall()
    late = con.execute("SELECT v.story_id, v.part, p.scheduled_at FROM posts p JOIN videos v ON v.id=p.video_id WHERE p.platform='youtube' "
                       "AND p.status='queued' AND v.status='approved' AND p.scheduled_at <= ?", ((now - timedelta(hours=2)).isoformat(timespec="seconds"),)).fetchall()
    errors = problems + [r[0][:160] for r in con.execute("SELECT step || ': ' || message FROM errors WHERE at >= ? ORDER BY id DESC LIMIT 8",
                                                   ((now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"),))]  # errors.at is SQLite CURRENT_TIMESTAMP (UTC, space)
    failed_runs = con.execute("SELECT COUNT(*) FROM runs WHERE ok=0 AND started_at >= ?", (since,)).fetchone()[0]
    upcoming = con.execute("SELECT v.story_id, v.part, MIN(p.scheduled_at) AS at, v.status FROM posts p JOIN videos v ON v.id=p.video_id "
                           "WHERE p.status IN ('queued','blocked') AND p.platform='youtube' AND p.scheduled_at BETWEEN ? AND ? GROUP BY v.id ORDER BY at",
                           (now.isoformat(timespec="seconds"), (now + timedelta(hours=24)).isoformat(timespec="seconds"))).fetchall()
    return dict(date=now.astimezone(tz).date(), today=list(today.values()), match=match, stats=stats, comments=comments, subs=subs, money=money,
                votes=votes, review=[tuple(r) for r in review], late=[tuple(r) for r in late], errors=errors, failed_runs=failed_runs,
                upcoming=[(r["story_id"], r["part"], r["at"], r["status"]) for r in upcoming],
                audit_pending=CONFIG["posting"].get("youtube_privacy") == "hand", days_to_apis=(APIS_DAY - now.astimezone(tz).date()).days)


def _e(s):
    return html.escape(str(s))


def render_html(d, tz):
    mark = {"ok": "✅", "wait": "⏳", "na": "—"}
    total = sum(len(e["platforms"]) for e in d["today"])
    h = [f"<h1>Hollow Floor · daily report</h1><p class=sub>{d['date']:%A %d %B %Y}</p>",
         f"<div class=kpis><div><b>{len(d['today'])}</b>videos</div><div><b>{total}</b>posts</div>"
         f"<div><b>{_e(d['subs'] if d['subs'] is not None else '–')}</b>YouTube subscribers</div>"
         f"<div><b>{len(d['review'])}</b>waiting for your ✅</div></div>"]
    h.append("<h2>Posted today</h2>")
    if not d["today"]:
        h.append("<p>Nothing was posted in the last 24 hours.</p>")
    for e in d["today"]:
        st = d["stats"].get(e["yt_id"] or "", {})
        nums = f"{st.get('viewCount', '–')} views · {st.get('likeCount', '–')} likes · {st.get('commentCount', '–')} comments" if e["yt_id"] else "not on YouTube"
        h.append(f"<div class=card><h3>Story {e['story']} · Part {e['part']} — {_e(e['title'])}</h3><p>{_e(e['about'])}</p>"
                 f"<p class=meta>Posted on: {', '.join(NAMES[p] for p in e['platforms'])} · YouTube: {nums}</p>")
        cs = d["comments"].get(e["yt_id"] or "", [])
        if cs:
            h.append("<p class=meta>Top YouTube comments:</p><ul>" + "".join(f"<li>“{_e(c)}”</li>" for c in cs) + "</ul>")
        elif e["yt_id"]:
            h.append("<p class=meta>No YouTube comments yet.</p>")
        h.append("</div>")
    h.append("<p class=note>Instagram and TikTok views and comments appear here once their developer apps are connected "
             f"({'in ' + str(d['days_to_apis']) + ' days' if d['days_to_apis'] > 0 else 'now due'}).</p>")
    h.append("<h2>Do all platforms match? (last 3 days)</h2><table><tr><th>Video</th>" + "".join(f"<th>{NAMES[p]}</th>" for p in PLATFORMS) + "<th></th></tr>")
    for m in d["match"]:
        h.append(f"<tr><td>Story {m['story']} · Part {m['part']}</td>" +
                 "".join(f"<td title='{_e(m['cells'][p][1])}'>{mark[m['cells'][p][0]]} <span class=meta>{_e(m['cells'][p][1])}</span></td>" for p in PLATFORMS) +
                 f"<td>{'match' if m['ok'] else 'catching up'}</td></tr>")
    h.append("</table>" + ("" if d["match"] else "<p>No posts in the last 3 days.</p>"))
    if d["votes"]:
        h.append("<h2>Votes closed today</h2><ul>" + "".join(
            f"<li>Story {v['story']} ({_e(v['title'])}): {v['counts'].get('A', 0)}–{v['counts'].get('B', 0)} → Part 2 {v['winner']}</li>" for v in d["votes"]) + "</ul>")
    need = [f"Story {s} Part {p} is waiting for your ✅ in Discord #reviews." for s, p in d["review"]]
    need += [f"Story {s} Part {p} was due on YouTube at {a[11:16]} UTC and still isn't up: is the PC on with Claude open?" for s, p, a in d["late"]]
    h.append("<h2>Needs you</h2>" + ("<ul>" + "".join(f"<li>{_e(n)}</li>" for n in need) + "</ul>" if need else "<p>Nothing. 👍</p>"))
    probs = ([f"{d['failed_runs']} cloud run(s) failed in the last 24 h."] if d["failed_runs"] else []) + d["errors"]
    h.append("<h2>Problems</h2>" + ("<ul>" + "".join(f"<li>{_e(p)}</li>" for p in probs) + "</ul>" if probs else "<p>None. ✅</p>"))
    h.append("<h2>Next 24 hours (YouTube)</h2>" + ("<ul>" + "".join(
        f"<li>{datetime.fromisoformat(a).astimezone(tz):%a %H:%M} — Story {s} Part {p}" + (" (waiting for your ✅)" if st == "review" else "") + "</li>"
        for s, p, a, st in d["upcoming"]) + "</ul>" if d["upcoming"] else "<p>Nothing scheduled yet.</p>"))
    m = d.get("money")
    if m:
        bar = lambda have, need: f"<span class=bar><i style='width:{min(100, 100 * have / need):.1f}%'></i></span> {have:,} / {need:,}"
        h.append("<h2>Road to monetization</h2>")
        for g in m["goals"]:
            when = "✅ reached: apply in YouTube Studio → Earn" if g["reached"] else \
                (f"at this week's pace, around {g['eta']:%b %d, %Y}" if g["eta"] else "not growing fast enough yet to estimate a date")
            h.append(f"<div class=card><h3>{_e(g['name'])}</h3><p class=meta>Subscribers {bar(g['have']['subs'], g['need']['subs'])}</p>"
                     f"<p class=meta>Shorts views, last 90 days {bar(g['have']['views90'], g['need']['views90'])}</p><p>{_e(when)}</p></div>")
        h.append(f"<p class=note>This week: about {m['rate']['subs']:+.1f} subscribers/day and {m['rate']['views90']:+,.0f} views/day "
                 f"({m['days_logged']} day(s) of history so far). Both YouTube tiers also need you to be 18 and 3+ uploads in 90 days. "
                 "TikTok Creator Rewards (18+, 10,000 followers, 100,000 views in 30 days, videos over 1 minute) and Instagram "
                 f"payouts show here once their developer apps are connected on {APIS_DAY:%b %d}.</p>")
    apis = f"starts {APIS_DAY:%b %d} (in {d['days_to_apis']} days)" if d["days_to_apis"] > 0 else "ready to connect"
    h.append("<h2>Status</h2><ul>" +
             f"<li>YouTube API audit: {'waiting for Google (videos go public through the PC meanwhile)' if d['audit_pending'] else 'approved: the cloud posts publicly'}</li>"
             f"<li>Instagram + TikTok cloud posting: {apis}</li></ul>")
    css = ("body{font-family:Arial,Helvetica,sans-serif;color:#1d1d1f;margin:28px;font-size:12px}h1{margin:0;font-size:22px}"
           ".sub{color:#666;margin:2px 0 14px}h2{font-size:15px;margin:18px 0 6px;border-bottom:1px solid #ddd;padding-bottom:3px}"
           "h3{font-size:13px;margin:0 0 4px}.kpis{display:flex;gap:10px}.kpis div{flex:1;background:#f3f3f5;border-radius:8px;padding:8px;color:#555}"
           ".kpis b{display:block;font-size:20px;color:#111}.card{border:1px solid #e3e3e6;border-radius:8px;padding:8px 10px;margin:6px 0;page-break-inside:avoid}"
           ".card p{margin:2px 0}.meta{color:#666;font-size:11px}.note{color:#777;font-size:11px}ul{margin:4px 0 4px 18px;padding:0}"
           ".bar{display:inline-block;width:160px;height:8px;background:#e6e6ea;border-radius:4px;vertical-align:middle;margin-right:6px}"
           ".bar i{display:block;height:8px;background:#7a4fd0;border-radius:4px}"
           "table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #eee;padding:4px;text-align:left;vertical-align:top}")
    return f"<!doctype html><html><head><meta charset=utf-8><style>{css}</style></head><body>{''.join(h)}</body></html>"


def to_pdf(html_text, out):
    chrome = os.environ.get("CHROME_PATH") or shutil.which("google-chrome") or shutil.which("chromium") or \
        r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "report.html"
        src.write_text(html_text, encoding="utf-8")
        subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                        f"--user-data-dir={Path(t) / 'profile'}", f"--print-to-pdf={out}", src.as_uri()],
                       check=True, capture_output=True, timeout=120)
    if not Path(out).exists():
        raise RuntimeError("Chrome did not write the PDF")
    return Path(out)


def summary_line(d):
    bad = [m for m in d["match"] if not m["ok"]]
    return (f"📊 **Daily report** ({d['date']:%a %d %b}): {len(d['today'])} video(s) posted · "
            f"{len(d['review'])} waiting for your ✅ · " + ("all platforms match ✅" if not bad else f"{len(bad)} video(s) still catching up on Instagram/TikTok") +
            ("" if not (d["errors"] or d["failed_runs"] or d["late"]) else " · ⚠️ see Problems"))


def send(con, now, tz):
    d = collect(con, now, tz)
    with tempfile.TemporaryDirectory() as t:
        pdf = to_pdf(render_html(d, tz), Path(t) / f"hollow-floor-report-{d['date']}.pdf")
        ch = "report" if os.environ.get("DISCORD_REPORT_CHANNEL_ID") else "alerts"
        discord.send(ch, summary_line(d), files=[pdf])
    return d
