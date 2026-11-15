"""Titles, captions, hashtags and the title/thumbnail A/B test (section 8).
Every story carries two title versions and two thumbnail texts per part. One platform gets A, another gets B;
which platform gets which flips on alternate stories so platform differences don't bias the result."""
import hashlib
import json

# Follow lines rotate so posts never look copy-pasted. {A}/{B} are the vote labels.
PART1_FOLLOW = [
    "Part 2 drops tomorrow. Follow so you don't miss it.",
    "Follow for Part 2 tomorrow. You decide how this ends.",
    "Part 2 is up tomorrow, and the comments pick the ending.",
    "Follow, because Part 2 lands tomorrow.",
]
PART1_VOTE = ["Comment {A} or {B}.", "{A} or {B}? Tell me below.", "What would you do: {A} or {B}?"]
PART2_FOLLOW = [
    "Part 1 is on my page. Follow for tomorrow's new story.",
    "Missed Part 1? It's on my page. New story tomorrow.",
    "New scary story every day. Follow for the next one.",
]


def _pick(pool, *keys):
    h = int(hashlib.sha1("|".join(map(str, keys)).encode()).hexdigest(), 16)
    return pool[h % len(pool)]


def versions(value):
    """Title/thumbnail fields may be a plain string (same for both) or {"A": .., "B": ..}."""
    return value if isinstance(value, dict) else {"A": value, "B": value}


def variant_for(platform, story_id):
    base = {"youtube": "A", "tiktok": "B", "instagram": "A"}[platform]
    return base if story_id % 2 else ("B" if base == "A" else "A")


def post_text(story, key, platform, story_id):
    """-> {"variant", "title", "caption", "thumb_text"} for one part on one platform."""
    v = variant_for(platform, story_id)
    title = versions(story["titles"][key])[v]
    thumb = versions(story.get("thumbs", {}).get(key, story.get("thumbnail_text", "")))[v]
    a, b = story["choices"]["A"]["label"], story["choices"]["B"]["label"]
    if key == "part1":
        lines = [story["caption"]]
        if not (a.lower() in story["caption"].lower() and b.lower() in story["caption"].lower()):  # don't ask the vote twice
            lines.append(_pick(PART1_VOTE, story["slug"], platform).format(A=a.lower(), B=b.lower()))
        lines.append(_pick(PART1_FOLLOW, story["slug"], platform, "f"))
    else:
        hook = story.get("captions_part2", {}).get(key) or title.replace(" (Part 2)", ".")
        lines = [hook, _pick(PART2_FOLLOW, story["slug"], platform)]
    tags = story["hashtags"]
    caption = "\n".join(lines) + "\n" + " ".join(tags)
    return {"variant": v, "title": title, "caption": caption, "thumb_text": thumb}


def record_experiment(con, story_id, story):
    """One experiments row per part: what A and B were. The winner is filled in by the analytics step."""
    for key in ("part1", "part2A", "part2B"):
        t, th = versions(story["titles"][key]), versions(story.get("thumbs", {}).get(key, story.get("thumbnail_text", "")))
        if not con.execute("SELECT 1 FROM experiments WHERE story_id=? AND tested=?", (story_id, f"title+thumb {key}")).fetchone():
            con.execute("INSERT INTO experiments (story_id, tested, version_a, version_b) VALUES (?,?,?,?)",
                        (story_id, f"title+thumb {key}", json.dumps({"title": t["A"], "thumb": th["A"]}),
                         json.dumps({"title": t["B"], "thumb": th["B"]})))
    con.commit()
