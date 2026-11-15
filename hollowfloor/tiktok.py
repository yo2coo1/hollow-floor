"""TikTok via the official Content Posting API (Direct Post). NOT YET TESTED: needs the TikTok account + app approval
(planned after 25 Nov 2026). Written against TikTok's v2 API docs; verify field names when the app is approved.

Flow: creator_info -> video/init (FILE_UPLOAD, AI-generated label on) -> PUT the bytes -> poll status.
Note: until TikTok audits the app, Direct Post only allows private (SELF_ONLY) posts.
Tokens: access token lasts ~24 h, refresh token ~1 year; refreshed on every run."""
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import get_logger, secret
from .youtube import _set_env

log = get_logger("tiktok")
API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,video.publish,video.upload,video.list"
REDIRECT = "https://yo2coo1.github.io/hollow-floor-site/callback.html"  # shows the code to paste back (TikTok needs an https redirect)


def _call(method, path, token=None, body=None, form=None, data=None, headers=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json; charset=UTF-8"
    if form is not None:
        data, h["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    url = path if path.startswith("http") else API + path
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method=method), timeout=300) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def connect_url():
    return "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": secret("TIKTOK_CLIENT_KEY"), "scope": SCOPES, "response_type": "code", "redirect_uri": REDIRECT, "state": "hollowfloor"})


def connect(code):
    """One-time: exchange the code shown on the callback page for tokens (stored in .env, never printed)."""
    tok = _call("POST", "/oauth/token/", form={"client_key": secret("TIKTOK_CLIENT_KEY"), "client_secret": secret("TIKTOK_CLIENT_SECRET"),
                                               "code": code, "grant_type": "authorization_code", "redirect_uri": REDIRECT})
    _set_env("TIKTOK_REFRESH_TOKEN", tok["refresh_token"])
    print("Saved the TikTok connection to .env (TIKTOK_REFRESH_TOKEN).")


def access_token():
    tok = _call("POST", "/oauth/token/", form={"client_key": secret("TIKTOK_CLIENT_KEY"), "client_secret": secret("TIKTOK_CLIENT_SECRET"),
                                               "grant_type": "refresh_token", "refresh_token": secret("TIKTOK_REFRESH_TOKEN")})
    if tok.get("refresh_token") and tok["refresh_token"] != secret("TIKTOK_REFRESH_TOKEN"):
        log.warning("TikTok issued a new refresh token; update the TIKTOK_REFRESH_TOKEN secret")  # the pipeline alerts on this
    return tok["access_token"]


def upload(path, caption, cover_ms=1000, private=True):
    """Direct Post with the AI-generated content label. Returns the publish id."""
    token = access_token()
    info = _call("POST", "/post/publish/creator_info/query/", token, body={})["data"]
    privacy = "SELF_ONLY" if private or "PUBLIC_TO_EVERYONE" not in info.get("privacy_level_options", []) else "PUBLIC_TO_EVERYONE"
    size = Path(path).stat().st_size
    chunk = min(size, 10_000_000)
    init = _call("POST", "/post/publish/video/init/", token, body={
        "post_info": {"title": caption[:2200], "privacy_level": privacy, "disable_comment": False, "disable_duet": False,
                      "disable_stitch": False, "video_cover_timestamp_ms": cover_ms, "is_aigc": True},  # is_aigc = TikTok's AI-generated label
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": math.ceil(size / chunk)}})["data"]
    with open(path, "rb") as f:
        for i in range(math.ceil(size / chunk)):
            part = f.read(chunk)
            start = i * chunk
            _call("PUT", init["upload_url"], data=part, headers={"Content-Type": "video/mp4",
                                                                  "Content-Range": f"bytes {start}-{start + len(part) - 1}/{size}"})
    pid = init["publish_id"]
    for _ in range(40):  # TikTok processes asynchronously
        st = _call("POST", "/post/publish/status/fetch/", token, body={"publish_id": pid})["data"]
        if st.get("status") in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            log.info("TikTok post done: %s", pid)
            return pid
        if st.get("status") == "FAILED":
            raise RuntimeError(f"TikTok publish failed: {st.get('fail_reason')}")
        time.sleep(15)
    raise TimeoutError("TikTok still processing after 10 minutes")


def stats(video_ids):
    token = access_token()
    d = _call("POST", "/video/query/?fields=id,view_count,like_count,comment_count,share_count", token, body={"filters": {"video_ids": video_ids}})
    return {v["id"]: v for v in d.get("data", {}).get("videos", [])}
