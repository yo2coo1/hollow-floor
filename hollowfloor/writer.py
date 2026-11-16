"""Automatic story writer (section 6). Free-tier LLM via API key (LLM_API_KEY). Every story it writes must pass the
same gates as a hand-written one: idea -> outline -> scripts + scene plan -> automatic checks -> quality score.
A story that fails twice is rejected and replaced by the next-best idea.

Story types are weighted by what performs (the weekly learning loop writes the weights to data/learning.json)."""
import difflib
import json
import random
import re
import urllib.request
from pathlib import Path

from . import db, story
from .config import CONFIG, ROOT, get_logger, secret

log = get_logger("writer")
EXAMPLE = ROOT / "stories/0001_notes.json"
LEARN = ROOT / "data/learning.json"

RULES_TEXT = """You write for "Hollow Floor", a channel of original two-part scary stories animated in simple blocky 3D.
Hard rules:
- Scary means tension and implication. No gore, blood, weapons, killing, real people, real events, real brands, hate,
  self-harm, drugs, sexual content, or children (no kids, babies, schools). No copyrighted characters.
  Must not resemble known creepypasta (Slender Man, smile dog, "humans can lick too", backrooms, etc.).
- One location (a few connected rooms max), one protagonist told in first person ("I"), one central question.
- Part 1 ends at a real decision with two choices, each a single word (e.g. OPEN / HIDE). Both Part 2 branches are written
  now: each opens with a one-line recap thought of where things stand and the choice taken ("Okay. The chair asked me to
  sit. So I'm sitting."), and pays off with a twist. Part 1 says both choice words
  and asks viewers to comment one. Part 2 ends with "Follow for the next story."
- Each script is WORDS_RANGE spoken words (each part runs a little over one minute). Short sentences. Commas and "..." for pauses. Avoid long or hard-to-say words
  (nothing over 12 letters) and names. Mark beats with [pause] (0.6 s silence) and [scare] (silence + scare hit); a beat
  at least every 25 words, at least one [scare] per part. The first sentence must open a question or threat.
- There is NO narrator. Untagged lines are the main character thinking out loud, live, in the present tense, as it
  happens ("Okay. Someone's at the door. Don't open it."), never retelling it afterwards ("Last night, I heard...").
  Anything said out loud is wrapped in a voice tag so it gets its own voice and the speaker's name on screen:
  {mom: Come downstairs.}  The main character speaking out loud is {me: Hello?}. Speakers available: ROLES_LIST.
- Mark how lines are delivered so the acting follows the story: [calm], [tense], [scared], [panic] or [whisper] before a
  line sets the mood until the next mark (inside a voice tag it applies to that line only: {grandma: [whisper] Stay.}).
  Build it up: calm or tense openings, scared as it gets close, panic right after a [scare], whisper when hiding.
- Write the cliffhanger first, then the hook, then the middle."""
RULES = RULES_TEXT.replace("ROLES_LIST", ", ".join(sorted(CONFIG["voice"].get("roles", {})))).replace(
    "WORDS_RANGE", f'{CONFIG["video"]["words_min"]}-{CONFIG["video"]["words_max"]}')

# Owner 2026-11-16: "I want creativity... not something that we're spamming online." Every story must feel made, not stamped out.
CRAFT = f"""Craft (this channel is built for a community that finds the stories satisfying, not for volume):
- Give the story its own world: a specific place with texture (the hum of a heater, a sticker on the fridge, the smell of
  bleach), a protagonist whose job, age or habit shapes how they react, and one memorable image nobody else would use.
- Use the length for build-up and character, not padding: plant a small human detail early that pays off in the twist.
- Add a "style" object that decides how the episode looks and moves. Pick what serves THIS story and differs from the
  recent ones: {{"setting": a short place description, "time": "night", "dusk", "dawn", "day", "storm"...,
  "look": one of {sorted(CONFIG.get('looks', {}))}, "camera": one of {sorted(CONFIG['styles']['camera'])},
  "pacing": one of {sorted(CONFIG['styles']['pacing'])}, "protagonist": who they are in a few words}}.
  Camera: handheld = nervous and close, uneasy = slight drift, drifting = slow and dreamlike, locked = still, watched, CCTV-like.
  Pacing: slow-burn = long held shots and dread, steady = default, frantic = quick cuts for panic stories."""


def _llm(prompt, temperature=0.9):
    """Gemini API (Google AI Studio free tier). Returns parsed JSON."""
    key = secret("LLM_API_KEY")
    if not key:
        raise RuntimeError("LLM_API_KEY is empty: the story writer needs a free Gemini API key")
    w = CONFIG.get("writer", {})
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{w.get('model', 'gemini-2.5-flash')}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.loads(r.read())
    text = out["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip()))


def _examples(exclude_type=None):
    """A hand-checked example story, preferably of a different type, so new stories don't all copy one template."""
    files = sorted((ROOT / "stories").glob("*.json")) or [EXAMPLE]
    pool = [f for f in files if json.loads(f.read_text(encoding="utf-8")).get("story_type") != exclude_type] or files
    return random.choice(pool).read_text(encoding="utf-8")


def _owner_notes(con):
    """Reasons the owner gave when declining videos in Discord (section 14b: 'use it to improve the next video')."""
    fb = json.loads(db.kv_get(con, "owner_feedback", "[]"))
    return [f["reason"] for f in fb if f.get("reason")][-8:]


def _weights():
    types = CONFIG["channel"]["story_types"]
    learned = json.loads(LEARN.read_text()).get("type_weights", {}) if LEARN.exists() else {}
    return {t: learned.get(t, 1.0) for t in types}


def ideas(con, llm=_llm, n=10):
    """Section 6.1: 10 ideas, scored for hook strength, ease to animate in blocky 3D, and originality."""
    used = [r[0] for r in con.execute("SELECT premise FROM stories")]
    recent = [r[0] for r in con.execute("SELECT story_type FROM stories ORDER BY id DESC LIMIT 3")]
    w = _weights()
    for t in recent:  # rotate: recently used types count for less this time
        if t in w:
            w[t] = round(w[t] * 0.5, 2)
    prompt = f"""{RULES}

Give {n} new story ideas as JSON: {{"ideas": [{{"premise": "...", "story_type": one of {list(w)}, "cliffhanger": "...",
"choice_a": "ONEWORD", "choice_b": "ONEWORD", "hook": 0-10, "animatable": 0-10, "originality": 0-10}}]}}.
Favor these story types (higher weight = more): {json.dumps(w)}.
Animatable means: one or two rooms, few characters, props like doors, notes, phones, lamps, beds, TVs, windows.
Do NOT reuse any of these premises: {json.dumps(used[-60:])}
The channel owner rejected earlier videos for these reasons; avoid repeating them: {json.dumps(_owner_notes(con))}"""
    out = llm(prompt)["ideas"]
    fresh = [i for i in out if not any(difflib.SequenceMatcher(None, i["premise"].lower(), u.lower()).ratio() > 0.6 for u in used)]
    return sorted(fresh, key=lambda i: i["hook"] * 1.4 + i["animatable"] + i["originality"] * 1.2 + random.random(), reverse=True)


def _write_prompt(idea, problems=None):
    ex = _examples(idea.get("story_type"))
    recent = [{"slug": r.get("slug"), "type": r.get("story_type"), **(r.get("style") or {})} for r in story.recent_stories({})]
    fix = f"\nYour previous attempt had these problems; fix all of them:\n- " + "\n- ".join(problems) if problems else ""
    return f"""{RULES}

{CRAFT}

Write the full story for this idea as ONE JSON object with exactly the same structure as the example below
(slug, story_type, premise, idea, outline, choices, titles A/B, thumbs A/B, caption, captions_part2, hashtags,
thumbnail_text, scripts, set, parts) PLUS the "style" object. Idea: {json.dumps(idea)}
The most recent stories (don't reuse a look from the last 2, the same camera + pacing as the last one, or a similar
setting or protagonist): {json.dumps(recent)}

Scene rules (the renderer only knows these):
- prop types: {sorted(story.PROP_TYPES)}; shot templates: {sorted(story.SHOT_TPLS)} (each shot: {{"t", "tpl", "target", optional "at", "side", "dist", "fov", "shake"}});
  events: {sorted(story.EVENT_TYPES)}; sounds: {sorted(story.SFX_TYPES)}.
- Times may be numbers or cues: "scare1", "scare2+0.5", "w:WORD-0.2" (WORD must be spoken EXACTLY ONCE in that part's script), "end".
- Characters: one protagonist "me" with one signature color; shadow figures use "kind": "shadow". Rooms open toward +z;
  doors on walls need a matching "holes" entry in the room if anything is visible through them.
- A part can animate a set prop by listing {{"name": <prop name>, ...}} in its "props": "path" [[t,x,y,z]], "visible" [[t,0|1]],
  door "open" [[t,degrees]], and "rock" [[t,radians]] (a chair or prop tilting back and forth; 0.05-0.2, 0 = still);
  a "dryer" (washer/dryer, door faces +z) takes "spin" [[t,turns per second]] (about 1 = running, 0 = stopped).
- A new shot at least every 3-4 seconds (about every 10 words). Use "red" + "flash" events and a "hit" sound on the big scare.
- Titles: under 60 characters, end with "(Part 1)" / "(Part 2)". Thumbnails: 2-4 words. 3-5 hashtags.
{fix}

Example story (format reference only; write something completely different):
{ex}"""


def score(s, llm=_llm):
    """Section 6.5: second-pass review for hook, clarity, pacing and safety (1-10)."""
    prompt = f"""You are a strict editor for a scary-story shorts channel. Score this story 1-10 on hook, clarity, pacing,
safety (advertiser-friendly, no gore/real people/children/self-harm). Return JSON {{"hook":n,"clarity":n,"pacing":n,"safety":n,"notes":"..."}}.
Scripts: {json.dumps(s['scripts'])}"""
    return llm(prompt, temperature=0.2)


def write_one(con, llm=_llm, min_score=7):
    """Returns a saved story id, trying ideas in order until one passes every gate (each idea gets one rewrite)."""
    for idea in ideas(con, llm)[:5]:
        problems = None
        for attempt in range(2):
            try:
                s = llm(_write_prompt(idea, problems), temperature=0.8)
            except Exception as e:
                problems = [f"invalid JSON or API error: {e}"]
                continue
            problems = story.all_problems(s, con)
            if not problems:
                q = score(s, llm)
                low = [k for k in ("hook", "clarity", "pacing", "safety") if q.get(k, 0) < min_score]
                if not low:
                    s.setdefault("quality", q)
                    s["set"]["director_v"] = 2  # unreviewed story: let the director widen clamped shots and dodge blocking characters
                    n = (con.execute("SELECT COALESCE(MAX(id),0) FROM stories").fetchone()[0] or 0) + 1
                    path = ROOT / f"stories/{n:04d}_{re.sub(r'[^a-z0-9]+', '_', s['slug'].lower())[:30]}.json"
                    path.write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
                    sid = story.save(con, s)
                    con.execute("UPDATE stories SET quality_scores=? WHERE id=?", (json.dumps(q), sid)); con.commit()
                    log.info("wrote story %s: %s", sid, s["premise"][:80])
                    return sid, path
                problems = [f"editor scored {k} {q.get(k)} (<{min_score}): {q.get('notes', '')}" for k in low]
            log.warning("idea '%s' attempt %d failed: %s", idea["premise"][:60], attempt + 1, problems[:3])
        db.log_error(con, "writer", f"rejected idea after 2 tries: {idea['premise'][:120]} :: {problems[:3]}")
    raise RuntimeError("no idea passed the gates")
