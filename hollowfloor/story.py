"""Story format, automatic quality gates (section 6.5 + 13) and database storage.

A story file (stories/NNNN_slug.json) holds everything: premise, choices, three scripts (Part 1, Part 2 A, Part 2 B),
titles/caption/hashtags, and one scene plan per part. The writer can be a person, this session, or the cloud LLM:
every story passes the same gates before anything is rendered."""
import difflib
import json
import re

from . import db
from .config import CONFIG

TAG = re.compile(r"\[(pause|scare)\]")
# Content gate (section 13): words that should never appear in a Hollow Floor script.
BANNED = ["blood", "gore", "kill", "killed", "murder", "suicide", "corpse", "dead body", "stab", "knife", "gun", "shoot",
          "rape", "naked", "sex", "child", "kid", "baby", "toddler", "school", "drug", "cocaine", "terrorist", "nazi",
          "slur", "self-harm", "cut myself", "hang", "noose"]
# Famous creepypasta premises we must not echo (originality gate). Matched by similarity to our premise.
KNOWN = ["a smiling dog picture that drives people insane", "a tall faceless man in a suit stalks children in the woods",
         "a killer with a carved smile says go to sleep", "soviet scientists keep prisoners awake with a gas",
         "a cave explorer finds a passage that should not exist", "a children's tv show only some people remember",
         "a dog licks your hand under the bed but the dog is in the other room humans can lick too",
         "an endless yellow office maze of backrooms", "a creature that only moves when you are not looking",
         "a man in the window at night who smiles", "a voicemail from yourself in the future",
         "a doll that moves every night closer to your bed", "a babysitter gets calls from inside the house"]


# Character dialogue: "{mom: Come downstairs.}" is read in that character's voice ([voice.roles] in config.toml).
ROLE = re.compile(r"\{(\w+):\s*([^}]*)\}")
# Delivery from that point on: "[scared] Who's there?" (voice.MOOD_STYLE). Not spoken, not counted as words.
MOODS = ("calm", "tense", "scared", "panic", "whisper")
MOOD = re.compile(r"\[(" + "|".join(MOODS) + r")\]\s*")


def plain(text):
    """The script as spoken: dialogue and mood tags removed, the words kept."""
    return MOOD.sub("", ROLE.sub(r"\2", text))


def words(text):
    return TAG.sub(" ", plain(text)).split()


def check(story, con=None):
    """Returns a list of problems; empty means it passes. Rejected stories get one rewrite, then are replaced (section 6.5)."""
    p = []
    v = CONFIG["video"]
    parts = {"part1": story["scripts"]["part1"], "part2A": story["scripts"]["part2A"], "part2B": story["scripts"]["part2B"]}
    roles = CONFIG["voice"].get("roles", {})
    for name, raw in parts.items():
        unknown = sorted({r for r, _ in ROLE.findall(raw) if r not in roles})
        if unknown:
            p.append(f"{name}: no voice for speaker(s) {unknown}; use one of {sorted(roles)}")
        if raw.count("{") != len(ROLE.findall(raw)) or raw.count("}") != raw.count("{"):
            p.append(f"{name}: malformed dialogue tag (use {{role: words}})")
        s = plain(raw)
        n = len(words(s))
        if not v["words_min"] <= n <= v["words_max"]:
            p.append(f"{name}: {n} words (need {v['words_min']}-{v['words_max']})")
        # a beat ([pause] or [scare]) at least every ~25 spoken words = every ~8-10 s at our pace
        gaps = [len(seg.split()) for seg in TAG.split(s)[::2]]
        if max(gaps) > 30:
            p.append(f"{name}: {max(gaps)} words without a beat (max 30)")
        if "[scare]" not in s:
            p.append(f"{name}: no [scare] beat")
        long = [w for w in words(s) if len(re.sub(r"\W", "", w)) > 12]
        if long:
            p.append(f"{name}: long words the voice may stumble on: {long}")
        low = " " + re.sub(r"[^a-z\- ]", " ", s.lower()) + " "
        hit = [b for b in BANNED if f" {b} " in low]
        if hit:
            p.append(f"{name}: content gate words {hit}")
    first = TAG.split(plain(parts["part1"]))[0].split(".")[0]
    if not re.search(r"\?|every (night|morning|day|time)|someone|something|never|don't|shouldn't|wasn't|nobody|no one|wrong|again|one more|keeps|appear", first.lower()):
        p.append("part1: first sentence doesn't open a question or threat")
    for b in ("part2A", "part2B"):
        if not plain(parts[b]).lower().startswith(("last time", "previously", "yesterday")):
            p.append(f"{b}: must open with a one-line recap")
    a, b2 = story["choices"]["A"]["label"], story["choices"]["B"]["label"]
    for lab in (a, b2):
        if not re.fullmatch(r"[A-Za-z]{2,10}", lab):
            p.append(f"choice label '{lab}' must be one short word")
        if lab.lower() not in parts["part1"].lower():
            p.append(f"part1 never says the choice '{lab}'")
    if a.lower() == b2.lower():
        p.append("both choices have the same label")
    if story["choices"].get("default") not in ("A", "B"):
        p.append("choices.default must be A or B")
    from .metadata import versions
    for k, t in story["titles"].items():
        for v, title in versions(t).items():
            if len(title) > 60:
                p.append(f"title {k}{v} over 60 chars")
            if not title.endswith("(Part 1)" if k == "part1" else "(Part 2)"):
                p.append(f"title {k}{v} must end with {'(Part 1)' if k == 'part1' else '(Part 2)'}")
    for k, t in story.get("thumbs", {}).items():
        for v, text in versions(t).items():
            if not 2 <= len(text.split()) <= 4:
                p.append(f"thumbnail {k}{v} needs 2-4 words: '{text}'")
    tags = story["hashtags"]
    if not 3 <= len(tags) <= 5:
        p.append("need 3-5 hashtags")
    prem = story["premise"].lower()
    for k in KNOWN:
        if difflib.SequenceMatcher(None, prem, k).ratio() > 0.6:
            p.append(f"premise too close to known story: {k}")
    if con is not None:
        for (old,) in con.execute("SELECT premise FROM stories"):
            if difflib.SequenceMatcher(None, prem, old.lower()).ratio() > 0.7:
                p.append(f"premise too close to an earlier story: {old}")
    return p


def save(con, story):
    """Stores a passing story and its three scripts; status becomes 'scripted'. Returns story id."""
    from .metadata import versions
    c = story["choices"]
    sid = con.execute(
        "INSERT INTO stories (premise, story_type, genre, title, caption, hashtags, thumbnail_text, idea, outline,"
        " choice_a, choice_b, default_choice, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'idea')",
        (story["premise"], story["story_type"], CONFIG["channel"]["genre"], versions(story["titles"]["part1"])["A"], story["caption"],
         " ".join(story["hashtags"]), story["thumbnail_text"], story["idea"], json.dumps(story["outline"]),
         c["A"]["label"], c["B"]["label"], c["default"])).lastrowid
    for part, branch, key in ((1, "", "part1"), (2, "A", "part2A"), (2, "B", "part2B")):
        con.execute("INSERT INTO scripts (story_id, part, branch, text, word_count, voice_settings) VALUES (?,?,?,?,?,?)",
                    (sid, part, branch, story["scripts"][key], len(words(story["scripts"][key])), json.dumps(CONFIG["voice"])))
    con.commit()
    db.set_story_status(con, sid, "scripted")
    return sid


def cue_problems(story):
    """Scene cues like "w:knocked" bind to the FIRST time a word is spoken. Flag cues whose word repeats (ambiguous)
    or never appears, so a writer can't silently time a shot to the wrong line."""
    p = []
    for key, scene in story["parts"].items():
        spoken = [re.sub(r"[^\w']", "", w).lower() for w in words(story["scripts"][key])]
        for word in sorted(set(re.findall(r'"w:([\w\']+)', json.dumps(scene)))):
            n = spoken.count(word.lower())
            if n != 1:
                p.append(f"{key}: cue word '{word}' is spoken {n} times (must be exactly once)")
    return p


def scene_for(story, key):
    """Merges the shared set with one part's scene. Part props with a set prop's name override that prop."""
    s = json.loads(json.dumps(story["set"]))
    part = json.loads(json.dumps(story["parts"][key]))
    by_name = {p.get("name"): p for p in s["props"] if p.get("name")}
    for p in part.pop("props", []):
        if p.get("name") in by_name:
            by_name[p["name"]].update(p)
        else:
            s["props"].append(p)
    s.update(part)
    s["name"] = f'{story["slug"]}_{key}'
    return s


PROP_TYPES = {"door", "bulb", "window", "bed", "table", "chair", "tv", "phone", "crate", "shelf", "couch", "lamp", "note", "fridge", "counter", "coin", "bottle", "plank", "liftdoor", "glow", "dryer"}
SHOT_TPLS = {"wide", "push", "face", "ots", "low", "insert", "peephole", "pov"}
EVENT_TYPES = {"flicker", "blackout", "dim", "off", "red", "flash", "peephole", "cctv"}
SFX_TYPES = {"footsteps", "creak", "hit", "buzz", "knock", "ring"}


def scene_problems(story):
    """Catches scene plans the renderer can't build (unknown props, shot targets that don't exist, bad templates)."""
    p = []
    set_props = {x.get("name") for x in story["set"].get("props", []) if x.get("name")}
    for x in story["set"].get("props", []):
        if x.get("type") not in PROP_TYPES:
            p.append(f"set: unknown prop type '{x.get('type')}'")
    if not story["set"].get("rooms"):
        p.append("set: needs at least one room")
    for key in ("part1", "part2A", "part2B"):
        sc = story["parts"].get(key)
        if not sc:
            p.append(f"{key}: missing scene"); continue
        names = set(set_props) | {x.get("name") for x in sc.get("props", []) if x.get("name")} | {c["id"] for c in sc.get("characters", [])}
        for x in sc.get("props", []):
            if x.get("name") not in set_props and x.get("type") not in PROP_TYPES:
                p.append(f"{key}: unknown prop '{x.get('name')}' / type '{x.get('type')}'")
        for c in sc.get("characters", []):
            if not c.get("path"):
                p.append(f"{key}: character '{c.get('id')}' has no path")
        for e in sc.get("events", []):
            if e.get("type") not in EVENT_TYPES:
                p.append(f"{key}: unknown event '{e.get('type')}'")
        for a in sc.get("audio", []):
            if a.get("type") not in SFX_TYPES:
                p.append(f"{key}: unknown sound '{a.get('type')}'")
        shots = sc.get("shots", [])
        if not shots or shots[0].get("t") != 0:
            p.append(f"{key}: first shot must start at t=0")
        for s in shots:
            if "tpl" in s:
                if s["tpl"] not in SHOT_TPLS:
                    p.append(f"{key}: unknown shot template '{s['tpl']}'")
                for k in ("target", "at"):
                    if k in s and s[k] not in names:
                        p.append(f"{key}: shot {k} '{s[k]}' is not a prop name or character id")
            elif "from" not in s or "look" not in s:
                p.append(f"{key}: a shot needs either tpl+target or from+look")
        if key == "part1" and not sc.get("choiceCard"):
            p.append("part1: needs a choiceCard (the viewer vote)")
        if key != "part1" and not sc.get("endCard"):
            p.append(f"{key}: needs an endCard")
    return p


def all_problems(story, con=None):
    return check(story, con) + cue_problems(story) + scene_problems(story)
