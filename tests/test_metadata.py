"""python -m tests.test_metadata"""
import json
from hollowfloor import metadata

s = json.load(open("stories/0001_notes.json", encoding="utf-8"))
for sid in (1, 2):
    vs = {pf: metadata.variant_for(pf, sid) for pf in ("youtube", "tiktok", "instagram")}
    assert vs["youtube"] != vs["tiktok"], vs  # A and B always run on different platforms
assert metadata.variant_for("youtube", 1) != metadata.variant_for("youtube", 2)  # flips story to story
yt, tt = metadata.post_text(s, "part1", "youtube", 1), metadata.post_text(s, "part1", "tiktok", 1)
assert yt["title"] != tt["title"] and yt["thumb_text"] != tt["thumb_text"]
assert "open" in yt["caption"].lower() and "hide" in yt["caption"].lower()      # vote ask present
assert 3 <= yt["caption"].count("#") <= 5
assert yt["caption"] != tt["caption"]                                          # wording varies across platforms
p2 = metadata.post_text(s, "part2B", "instagram", 1)
assert p2["title"].endswith("(Part 2)") and "Part 1" in p2["caption"] or "Follow" in p2["caption"]
assert yt["caption"].lower().count("open or hide") == 1   # vote asked once, not twice
print("metadata self-check: OK")
