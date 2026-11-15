"""Discord as the control panel (REST only, so it works from scheduled cloud runs with no always-on server).

Review: the bot posts the video preview, thumbnail, titles, caption and schedule to #reviews and adds ✅ / ❌.
The owner reacts ✅ to post or ❌ to skip, and can reply to the message with a reason. Only the server owner counts.
Control: typing "pause" or "resume" in #alerts stops or restarts all posting (latest owner message wins).

shortcut: reactions instead of buttons — buttons need an always-on endpoint answering within 3 s;
upgrade by adding a free Cloudflare Worker interactions endpoint if the owner wants real buttons."""
import json
import mimetypes
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .config import get_logger, secret

log = get_logger("discord")
API = "https://discord.com/api/v10"
CHANNELS = {"reviews": "DISCORD_REVIEWS_CHANNEL_ID", "alerts": "DISCORD_ALERTS_CHANNEL_ID"}
YES, NO = "✅", "❌"
UPLOAD_LIMIT = 9_500_000  # bots on a non-boosted server may upload ~10 MB per message
FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")
_owner = None


def _cid(channel):
    cid = secret(CHANNELS[channel])
    if not cid:
        raise RuntimeError(f"{CHANNELS[channel]} is empty in .env")
    return cid


def _req(method, path, body=None, data=None, ctype=None):
    token = secret("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is empty in .env")
    h = {"Authorization": f"Bot {token}", "User-Agent": "DiscordBot (hollowfloor, 0.2)"}
    if body is not None:
        data, ctype = json.dumps(body).encode(), "application/json"
    if ctype:
        h["Content-Type"] = ctype
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(API + path, data=data, headers=h, method=method), timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:  # rate limited: wait as told
                time.sleep(float(json.loads(e.read() or b"{}").get("retry_after", 2)) + 0.5)
                continue
            raise


def send(channel, content, files=()):
    """Posts a message (with optional file attachments) and returns its id."""
    payload = {"content": content[:2000], "allowed_mentions": {"parse": []}}
    if not files:
        return _req("POST", f"/channels/{_cid(channel)}/messages", body=payload)["id"]
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n{json.dumps(payload)}\r\n'.encode()]
    for i, f in enumerate(files):
        f = Path(f)
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="files[{i}]"; filename="{f.name}"\r\n'
                     f'Content-Type: {mimetypes.guess_type(f.name)[0] or "application/octet-stream"}\r\n\r\n'.encode() + f.read_bytes() + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return _req("POST", f"/channels/{_cid(channel)}/messages", data=b"".join(parts), ctype=f"multipart/form-data; boundary={b}")["id"]


def react(channel, msg_id, emoji):
    _req("PUT", f"/channels/{_cid(channel)}/messages/{msg_id}/reactions/{urllib.parse.quote(emoji)}/@me")


def owner_id():
    global _owner
    if _owner is None:
        gid = _req("GET", f"/channels/{_cid('reviews')}")["guild_id"]
        _owner = _req("GET", f"/guilds/{gid}")["owner_id"]
    return _owner


def _reactors(channel, msg_id, emoji):
    return {u["id"] for u in _req("GET", f"/channels/{_cid(channel)}/messages/{msg_id}/reactions/{urllib.parse.quote(emoji)}?limit=100") or []}


def preview(video, out):
    """Small copy for the Discord preview; the full-quality file is the one that gets posted."""
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(video), "-vf", "scale=540:-2", "-c:v", "libx264", "-crf", "30",
                    "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)], check=True)
    return out


def send_review(video, thumbs, title_lines, caption, when, note=""):
    """Posts a review request to #reviews. Returns the message id to poll later."""
    small = preview(video, Path(video).with_suffix(".preview.mp4"))
    files = [p for p in [small, *thumbs] if p and Path(p).exists()]
    if Path(small).stat().st_size > UPLOAD_LIMIT:  # too big even compressed: send thumbnails only and say so
        files = [p for p in thumbs if p]
        note += "\n(Preview too large for Discord; the full video is stored with the run.)"
    text = (f"🎬 **Review needed**\n{title_lines}\n🕒 Scheduled: **{when}**\n📝 Caption:\n> " + caption.replace("\n", "\n> ") +
            f"\n\nReact {YES} to post or {NO} to skip. Reply to this message to say why.{note}")
    mid = send("reviews", text, files)
    react("reviews", mid, YES)
    react("reviews", mid, NO)
    return mid


def review_result(msg_id):
    """-> ("approved"|"declined"|"pending", reason). Only the server owner's reaction counts."""
    me = owner_id()
    yes, no = me in _reactors("reviews", msg_id, YES), me in _reactors("reviews", msg_id, NO)
    reason = ""
    for m in _req("GET", f"/channels/{_cid('reviews')}/messages?after={msg_id}&limit=100") or []:
        if m["author"]["id"] == me and (m.get("message_reference") or {}).get("message_id") == msg_id:
            reason = m["content"][:500]
    if yes and not no:
        return "approved", reason
    if no:
        return "declined", reason
    return "pending", reason


def paused():
    """True if the owner's latest 'pause'/'resume' message in #alerts is 'pause'."""
    me = owner_id()
    for m in _req("GET", f"/channels/{_cid('alerts')}/messages?limit=50") or []:  # newest first
        word = m["content"].strip().lower().lstrip("!/")
        if m["author"]["id"] == me and word in ("pause", "resume"):
            return word == "pause"
    return False


def alert(text):
    try:
        return send("alerts", text)
    except Exception as e:  # an alert failure must never crash the pipeline
        log.error("could not send alert: %s", e)


if __name__ == "__main__":  # python -m hollowfloor.discord  -> test message to both channels
    for ch in CHANNELS:
        print(ch, "ok, message", send(ch, f"Hollow Floor Bot test: #{ch} connected."))
