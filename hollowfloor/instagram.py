"""Instagram Reels via the official Instagram API with Instagram Login (graph.instagram.com) — no Facebook Page needed.
NOT YET TESTED: needs the Instagram professional account + Meta app review (planned after 25 Nov 2026).

Flow: create a REELS container with resumable upload -> send the bytes -> wait for FINISHED -> publish.
AI label: the API has no 'AI info' switch at the time of writing, so every caption carries a clear AI-generated
disclosure; turn on Meta's label in the app as well if it becomes available through the API.
Token: long-lived (60 days), refreshed by the pipeline well before it expires."""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import get_logger, secret
from .youtube import _set_env

log = get_logger("instagram")
API = "https://graph.instagram.com/v21.0"
AI_NOTE = "\n\nThis story is AI-generated (animation and voice)."


def _call(method, url, params=None, data=None, headers=None):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read() or b"{}")


def refresh_token():
    """Long-lived tokens can be refreshed once they are 24 h old; doing it daily keeps them alive indefinitely."""
    d = _call("GET", "https://graph.instagram.com/refresh_access_token", {"grant_type": "ig_refresh_token", "access_token": secret("INSTAGRAM_ACCESS_TOKEN")})
    _set_env("INSTAGRAM_ACCESS_TOKEN", d["access_token"])
    return d.get("expires_in")


def upload(path, caption, cover_ms=1000):
    token, uid = secret("INSTAGRAM_ACCESS_TOKEN"), secret("INSTAGRAM_USER_ID")
    c = _call("POST", f"{API}/{uid}/media", {"media_type": "REELS", "upload_type": "resumable", "caption": (caption + AI_NOTE)[:2200],
                                             "thumb_offset": cover_ms, "share_to_feed": "true", "access_token": token})
    data = Path(path).read_bytes()
    _call("POST", c["uri"], data=data, headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))})
    for _ in range(40):
        st = _call("GET", f"{API}/{c['id']}", {"fields": "status_code,status", "access_token": token})
        if st.get("status_code") == "FINISHED":
            pub = _call("POST", f"{API}/{uid}/media_publish", {"creation_id": c["id"], "access_token": token})
            log.info("Instagram reel published: %s", pub["id"])
            return pub["id"]
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram processing failed: {st.get('status')}")
        time.sleep(15)
    raise TimeoutError("Instagram still processing after 10 minutes")


def comments(media_id):
    """For the viewer vote: [{"author", "text", "at"}]."""
    token, out, url = secret("INSTAGRAM_ACCESS_TOKEN"), [], f"{API}/{media_id}/comments"
    params = {"fields": "text,username,timestamp", "limit": 50, "access_token": token}
    while url:
        d = _call("GET", url, params)
        out += [{"author": c.get("username"), "text": c.get("text", ""), "at": c.get("timestamp", "")} for c in d.get("data", [])]
        url, params = d.get("paging", {}).get("next"), None
    return out


def stats(media_id):
    d = _call("GET", f"{API}/{media_id}/insights", {"metric": "views,reach,likes,comments,saved,shares,ig_reels_avg_watch_time",
                                                    "access_token": secret("INSTAGRAM_ACCESS_TOKEN")})
    return {m["name"]: (m.get("values") or [{}])[0].get("value") for m in d.get("data", [])}
