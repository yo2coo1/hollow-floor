"""python -m tests.test_roles: character dialogue tags are invisible to word counts and checks, and every speaker has a voice."""
import json
from pathlib import Path
from hollowfloor import story
from hollowfloor.config import CONFIG

assert story.words("Mom said {mom: come down} now") == ["Mom", "said", "come", "down", "now"]
assert story.plain("{dad: Stay. Here.} ok") == "Stay. Here. ok"
s = json.loads(Path("stories/0011_guestbook.json").read_text(encoding="utf-8"))
s["scripts"] = {k: story.plain(v) for k, v in s["scripts"].items()}  # start untagged
base = story.check(s)
tagged = json.loads(json.dumps(s))
tagged["scripts"]["part1"] = tagged["scripts"]["part1"].replace("Let me in. It's freezing out here.", "{sister: Let me in. It's freezing out here.}")
assert "{sister:" in tagged["scripts"]["part1"]
assert story.check(tagged) == base and story.cue_problems(tagged) == story.cue_problems(s)  # tag changes nothing else
bad = json.loads(json.dumps(tagged)); bad["scripts"]["part1"] = bad["scripts"]["part1"].replace("{sister:", "{auntie:")
assert any("no voice for speaker" in p for p in story.check(bad))
broken = json.loads(json.dumps(s)); broken["scripts"]["part1"] += " {mom: oops"
assert any("malformed" in p for p in story.check(broken))
assert all({"voice", "speed", "pitch"} <= set(r) for r in CONFIG["voice"]["roles"].values())
# captions: thoughts carry no speaker, spoken words carry the role's label, token for token with word_times
from hollowfloor.voice import speakers
sc = "Okay. [pause] She said: {grandma: He wants you, dear.} [scare] Then {thing: Sit.}, it said. {me: Hello?}"
assert speakers(sc) == [None, None, None, "GRANDMA", "GRANDMA", "GRANDMA", "GRANDMA", None, "???", None, None, "ME"]
assert len(speakers(sc)) == len(story.words(sc))
# mood tags steer delivery only: never spoken, never counted, never shown in captions
mt = "[tense] Who's there? [scare] [panic] Run! {grandma: [whisper] Stay, dear.}"
assert story.plain(mt) == "Who's there? [scare] Run! Stay, dear."
assert story.words(mt) == ["Who's", "there?", "Run!", "Stay,", "dear."]
assert speakers(mt) == [None, None, None, "GRANDMA", "GRANDMA"]
print("roles self-check: OK")
