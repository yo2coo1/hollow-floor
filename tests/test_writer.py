"""python -m tests.test_writer — exercises the writer's gates with a fake LLM (no API key needed)."""
import copy, json, tempfile
from pathlib import Path
from hollowfloor import db, writer

base = json.load(open("stories/0001_notes.json", encoding="utf-8"))
calls = {"write": 0}

def fake(prompt, temperature=0.9):
    if '"ideas"' in prompt:
        return {"ideas": [{"premise": "A motel ice machine fills every bucket with someone else's room key", "story_type": "night shifts",
                           "cliffhanger": "x", "choice_a": "OPEN", "choice_b": "HIDE", "hook": 9, "animatable": 8, "originality": 8}]}
    if "strict editor" in prompt:
        return {"hook": 8, "clarity": 8, "pacing": 8, "safety": 10, "notes": "ok"}
    calls["write"] += 1
    s = copy.deepcopy(base)
    s["premise"] = "A motel ice machine fills every bucket with someone else's room key, and tonight it gives me my own."
    s["slug"] = "ice_machine"
    if calls["write"] == 1:             # first draft breaks a gate: too short, must be rewritten
        s["scripts"]["part1"] = "Too short. [scare]"
    else:
        assert "previous attempt had these problems" in prompt  # the rewrite sees the gate failures
    return s

con = db.connect(":memory:")
writer.ROOT = Path(tempfile.mkdtemp())  # don't write into the real stories/ folder
(writer.ROOT / "stories").mkdir()
sid, path = writer.write_one(con, llm=fake)
assert calls["write"] == 2 and path.exists()
assert con.execute("SELECT status FROM stories WHERE id=?", (sid,)).fetchone()[0] == "scripted"
assert con.execute("SELECT COUNT(*) FROM scripts WHERE story_id=?", (sid,)).fetchone()[0] == 3
print("writer self-check: OK")
