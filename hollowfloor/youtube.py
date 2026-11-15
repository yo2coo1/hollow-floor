"""YouTube via the official Data API v3 (stdlib only).
- connect(): one-time browser sign-in on this PC; stores the refresh token in .env (never printed).
- upload(): resumable upload, marked as altered/synthetic content (AI label), not made for kids.
- comments(): for the viewer vote. Quota: an upload costs 1600 of the 10,000 daily units (3 a day is fine)."""
import http.server
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from .config import ROOT, get_logger, secret

log = get_logger("youtube")
SCOPES = ("https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly "
          "https://www.googleapis.com/auth/yt-analytics.readonly")  # analytics: watch time for the learning loop
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"


def _client():
    """Client id/secret: GitHub secrets in the cloud, the downloaded JSON on this PC."""
    cid, cs = secret("YOUTUBE_CLIENT_ID"), secret("YOUTUBE_CLIENT_SECRET")
    if cid and cs:
        return cid, cs
    d = json.loads((ROOT / "private/google_client_secret.json").read_text())["installed"]
    return d["client_id"], d["client_secret"]


def _post(url, data, headers=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:  # keep Google's reason (e.g. invalid_grant = access revoked); it holds no secrets
        raise RuntimeError(f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}") from None


def _set_env(name, value, path=ROOT / ".env"):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out = [l for l in lines if not l.startswith(name + "=")] + [f"{name}={value}"]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ[name] = value


def connect():
    """Opens Google sign-in in the browser; the owner picks the Hollow Floor channel and clicks Allow."""
    cid, cs = _client()
    state, got = secrets.token_urlsafe(16), {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                got.update({k: v[0] for k, v in q.items()})
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Hollow Floor is connected to YouTube. You can close this tab.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state})
    print("Opening Google sign-in. Choose the Hollow Floor channel, then click Allow.")
    webbrowser.open(url)
    while "code" not in got and "error" not in got:
        srv.handle_request()
    if "error" in got:
        raise RuntimeError(f"sign-in refused: {got['error']}")
    tok = _post(TOKEN_URL, {"code": got["code"], "client_id": cid, "client_secret": cs, "redirect_uri": redirect, "grant_type": "authorization_code"})
    _set_env("YOUTUBE_REFRESH_TOKEN", tok["refresh_token"])
    print("Saved the YouTube connection to .env (YOUTUBE_REFRESH_TOKEN).")


def access_token():
    rt = secret("YOUTUBE_REFRESH_TOKEN")
    if not rt:
        raise RuntimeError("YouTube not connected: run  python -m hollowfloor.youtube connect")
    cid, cs = _client()
    return _post(TOKEN_URL, {"refresh_token": rt, "client_id": cid, "client_secret": cs, "grant_type": "refresh_token"})["access_token"]


def _api(method, path, token, body=None, params=None, data=None, headers=None):
    url = f"{API}/{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    h = {"Authorization": f"Bearer {token}", **(headers or {})}
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}, r.headers


def video_body(title, description, tags, privacy="private", publish_at=None):
    """Metadata for every Hollow Floor upload. containsSyntheticMedia = YouTube's 'altered or synthetic content' label."""
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}
    if publish_at:  # scheduled publish needs privacy=private plus publishAt (RFC 3339)
        status.update(privacyStatus="private", publishAt=publish_at)
    return {"snippet": {"title": title[:100], "description": description[:5000], "tags": [t.lstrip("#") for t in tags][:15],
                        "categoryId": "1"},  # Film & Animation
            "status": status}


def upload(path, title, description, tags, privacy="private", publish_at=None, thumbnail=None):
    """Resumable upload. Returns the video id. Raises on failure (the poster retries and logs)."""
    token = access_token()
    size = Path(path).stat().st_size
    # resumable session on the upload host: metadata first, then the bytes
    url = UPLOAD + "/videos?" + urllib.parse.urlencode({"uploadType": "resumable", "part": "snippet,status"})
    req = urllib.request.Request(url, data=json.dumps(video_body(title, description, tags, privacy, publish_at)).encode(), method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    with urllib.request.urlopen(req, timeout=60) as r:
        session = r.headers["Location"]
    with open(path, "rb") as f:
        req = urllib.request.Request(session, data=f.read(), method="PUT", headers={"Content-Type": "video/mp4"})
    with urllib.request.urlopen(req, timeout=900) as r:
        vid = json.loads(r.read())["id"]
    log.info("uploaded %s as https://youtube.com/shorts/%s (%s)", Path(path).name, vid, privacy)
    if thumbnail:
        try:
            with open(thumbnail, "rb") as f:
                req = urllib.request.Request(UPLOAD + "/thumbnails/set?videoId=" + vid, data=f.read(), method="POST",
                                             headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"})
            urllib.request.urlopen(req, timeout=60).read()
        except urllib.error.HTTPError as e:  # custom thumbnails need a verified channel; a missing thumbnail must not fail the post
            log.warning("thumbnail not set for %s: HTTP %s", vid, e.code)
    return vid


def comments(video_id):
    """Top-level comments for the vote: [{"author", "text", "at"}]. Costs 1 quota unit per page."""
    token, out, page = access_token(), [], None
    while True:
        params = {"part": "snippet", "videoId": video_id, "maxResults": 100, "textFormat": "plainText"}
        if page:
            params["pageToken"] = page
        d, _ = _api("GET", "commentThreads", token, params=params)
        for it in d.get("items", []):
            s = it["snippet"]["topLevelComment"]["snippet"]
            out.append({"author": s.get("authorChannelId", {}).get("value", s.get("authorDisplayName")), "text": s["textDisplay"], "at": s["publishedAt"]})
        page = d.get("nextPageToken")
        if not page:
            return out


def my_channel():
    d, _ = _api("GET", "channels", access_token(), params={"part": "snippet,statistics", "mine": "true"})
    return d["items"][0] if d.get("items") else None


def retry(fn, *a, tries=3, delays=(30, 120, 300), **kw):
    """Spec section 9: retry up to three times with delays, then raise so the caller logs it and moves on."""
    for i in range(tries):
        try:
            return fn(*a, **kw)
        except Exception as e:  # network blips, 5xx, quota hiccups
            if i == tries - 1:
                raise
            log.warning("%s failed (%s); retry %d/%d in %ds", fn.__name__, re.sub(r"\s+", " ", str(e))[:200], i + 1, tries - 1, delays[i])
            time.sleep(delays[i])


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["connect"]:
        connect()
    elif sys.argv[1:] == ["whoami"]:
        ch = my_channel()
        print(ch["snippet"]["title"] if ch else "no channel on this account")
