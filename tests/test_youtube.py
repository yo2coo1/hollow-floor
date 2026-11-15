"""python -m tests.test_youtube (offline: checks the metadata every upload sends)"""
from hollowfloor import youtube

b = youtube.video_body("Someone slides notes under my door (Part 1)", "desc", ["#scarystories", "#creepy"])
assert b["status"]["containsSyntheticMedia"] is True          # AI label, always
assert b["status"]["selfDeclaredMadeForKids"] is False
assert b["status"]["privacyStatus"] == "private"               # default until the API audit allows public
assert b["snippet"]["tags"] == ["scarystories", "creepy"]
s = youtube.video_body("t", "d", [], publish_at="2026-11-16T17:00:00Z")["status"]
assert s["privacyStatus"] == "private" and s["publishAt"] == "2026-11-16T17:00:00Z"
calls = []
def flaky():
    calls.append(1)
    if len(calls) < 3:
        raise OSError("blip")
    return "ok"
assert youtube.retry(flaky, delays=(0, 0, 0)) == "ok" and len(calls) == 3
print("youtube self-check: OK")
