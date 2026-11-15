"""Voices: Kokoro (local, Apache 2.0) or Edge voices (edge-tts, free online service).
There is no narrator: untagged lines are the main character thinking out loud (voice_id, captions in italics).
Script markup: "[pause]" = 0.6 s silence, "[scare]" = 0.5 s silence and a scare marker the video syncs to,
"{role: words}" = words said out loud in a character voice from [voice.roles] ("me" = the main character speaking);
their captions carry the speaker's name.
Returns a 24 kHz mono WAV and the marker times."""
import asyncio
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import CONFIG, ROOT
from .story import MOOD, ROLE, plain

SR = 24000
FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")
_kokoro = None


def _ff(*args):
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args], check=True)


def _kokoro_say(text, voice, speed):
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(ROOT / "models/kokoro-v1.0.int8.onnx"), str(ROOT / "models/voices-v1.0.bin"))
    samples, sr = _kokoro.create(text, voice=voice, speed=speed, lang="en-us" if voice[0] == "a" else "en-gb")
    assert sr == SR, sr
    return samples.astype(np.float32)


def _edge_say(text, voice, rate, pitch):
    import edge_tts
    with tempfile.TemporaryDirectory() as d:
        mp3, wav = Path(d, "a.mp3"), Path(d, "a.wav")
        asyncio.run(edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(mp3)))
        _ff("-i", str(mp3), "-ac", "1", "-ar", str(SR), str(wav))
        return sf.read(wav, dtype="float32")[0]


def _pitch(samples, factor):
    """Lower pitch without changing speed (FFmpeg rubberband). factor<1 = deeper."""
    if abs(factor - 1) < 1e-3:
        return samples
    with tempfile.TemporaryDirectory() as d:
        a, b = Path(d, "a.wav"), Path(d, "b.wav")
        sf.write(a, samples, SR)
        _ff("-i", str(a), "-af", f"rubberband=pitch={factor}", str(b))
        return sf.read(b, dtype="float32")[0]


def _trim(x, thresh=0.01):
    """Cut leading/trailing silence so our own pauses control the pacing."""
    idx = np.where(np.abs(x) > thresh)[0]
    return x[max(0, idx[0] - 600): idx[-1] + 1200] if len(idx) else x


# delivery per mood: Chatterbox emotion dial (ex) and pacing (cfg, lower = more dramatic); Kokoro can only vary speed
MOOD_STYLE = {"calm": dict(ex=0.4, cfg=0.5, speed=0.95, gain=1.0), "tense": dict(ex=0.6, cfg=0.45, speed=1.0, gain=1.0),
              "scared": dict(ex=0.85, cfg=0.35, speed=1.05, gain=1.0), "panic": dict(ex=1.25, cfg=0.3, speed=1.15, gain=1.0),
              "whisper": dict(ex=0.3, cfg=0.5, speed=0.9, gain=0.55)}
_cbx = None


def _ref(voice):
    """Reference clip Chatterbox copies a voice from: made once from the free Kokoro voice, so no outside recordings."""
    f = ROOT / "models/refs" / f"{voice}.wav"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        sf.write(f, _kokoro_say("Okay, okay. Somebody is definitely in the kitchen right now. I can hear them moving around. "
                                "Don't panic. Just breathe, and listen.", voice, 1.0), SR)
    return str(f)


def _cbx_say(text, voice, mood):
    global _cbx
    if _cbx is None:
        py = ROOT / ".venv-cbx" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")  # its own venv (torch pins)
        _cbx = subprocess.Popen([str(py), "-m", "hollowfloor.cbx_worker"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        while (line := _cbx.stdout.readline()) and line.strip() != "ready":
            pass
    st = MOOD_STYLE[mood]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d, "s.wav")
        _cbx.stdin.write(json.dumps({"text": text, "ref": _ref(voice), "ex": st["ex"], "cfg": st["cfg"], "out": str(out)}) + "\n")
        _cbx.stdin.flush()
        r = _cbx.stdout.readline().strip()
        if r != "ok":
            raise RuntimeError(f"chatterbox: {r or 'worker died'}")
        return sf.read(out, dtype="float32")[0]


def speak(script, out_wav, engine="kokoro", voice="am_michael", speed=0.92, rate="-8%", pitch="-4Hz", kokoro_pitch=0.95, roles=None, mood="tense"):
    """Synthesizes a marked-up script. Returns {"seconds": float, "markers": {"scare1": t, ...}}.
    engine "chatterbox" = expressive (emotion follows [calm]/[tense]/[scared]/[panic]/[whisper]), "kokoro" = flat but fast."""
    parts = re.split(r"(\[pause\]|\[scare\])", script)
    out, markers, scares = [np.zeros(int(SR * 0.25), np.float32)], {}, 0
    t = lambda: sum(len(a) for a in out) / SR
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p == "[pause]":
            out.append(np.zeros(int(SR * 0.6), np.float32))
        elif p == "[scare]":
            scares += 1
            out.append(np.zeros(int(SR * 0.5), np.float32))
            markers[f"scare{scares}"] = round(t(), 3)  # the hit lands as the silence ends, right before the next line
            if not MOOD.search(script):
                mood = "scared"  # older scripts without mood marks: at least react to the scare
        else:
            # "{mom: Come down.}" switches to that character's voice for the tagged words only
            bits = ROLE.split(p)  # [thought, role, line, thought, role, line, ...]
            for i in range(0, len(bits), 3):
                for who, chunk in ((None, bits[i]), *(((bits[i + 1], bits[i + 2]),) if i + 1 < len(bits) else ())):
                    m_here, pieces = mood, MOOD.split(chunk)  # [text, mood, text, ...]
                    r = (roles or {}).get(who) if who else None
                    for j in range(0, len(pieces), 2):
                        if j:
                            m_here = pieces[j - 1]
                            if not who:
                                mood = m_here  # a mood tag in the thoughts lasts until the next one
                        text = pieces[j].strip()
                        if not text:
                            continue
                        st = MOOD_STYLE[m_here]
                        if engine == "chatterbox":
                            seg = _pitch(_cbx_say(text, r["voice"] if r else voice, m_here), r.get("pitch", 1.0) if r else 1.0)
                        elif r and engine == "kokoro":
                            seg = _pitch(_kokoro_say(text, r["voice"], r.get("speed", 0.92) * st["speed"]), r.get("pitch", 1.0))
                        elif engine == "kokoro":
                            seg = _pitch(_kokoro_say(text, voice, speed * st["speed"]), kokoro_pitch)
                        else:
                            seg = _edge_say(text, voice, rate, pitch)
                        seg = _trim(seg)
                        out.append(seg / max(1e-6, np.abs(seg).max()) * 0.9 * st["gain"])  # even loudness, then the mood's level
                        out.append(np.zeros(int(SR * (0.18 if who else 0.12)), np.float32))
    out.append(np.zeros(int(SR * 0.4), np.float32))
    audio = np.concatenate(out)
    audio = audio / max(1e-6, np.abs(audio).max()) * 0.9
    sf.write(out_wav, audio, SR)
    return {"seconds": round(len(audio) / SR, 3), "markers": markers}


_whisper = None


def word_times(wav, script_text=""):
    """Whisper (local) word timings for captions: [{"w", "t0", "t1"}]. The script is passed as a prompt so spelling matches."""
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel("base.en", device="cpu", compute_type="int8")
    clean = re.sub(r"\[(pause|scare)\]", " ", plain(script_text))
    # load audio ourselves: faster-whisper's own decoder breaks on newer PyAV versions
    x, sr = sf.read(str(wav), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    x16 = np.interp(np.arange(0, len(x), sr / 16000), np.arange(len(x)), x).astype(np.float32)
    segs, _ = _whisper.transcribe(x16, word_timestamps=True, language="en")
    heard = [{"w": w.word.strip(), "t0": round(w.start, 3), "t1": round(w.end, 3)} for s in segs for w in s.words if w.word.strip()]
    if not clean.strip():
        return heard
    out = align(clean.split(), heard)
    for w, who in zip(out, speakers(script_text)):
        if who:
            w["who"] = who
    return out


def speakers(script, roles=None):
    """Caption label per spoken word (same tokens as word_times): None for thoughts, else the speaker's name."""
    roles = roles if roles is not None else CONFIG["voice"].get("roles", {})
    script = MOOD.sub("", script)
    text, who, pos = "", [], 0
    for m in ROLE.finditer(script):  # rebuild plain() text while remembering who owns each character
        text += script[pos:m.start()]; who += [None] * (m.start() - pos)
        r = m.group(1); label = roles.get(r, {}).get("label") or r.replace("_", " ").upper()
        text += m.group(2); who += [label] * len(m.group(2))
        pos = m.end()
    text += script[pos:]; who += [None] * (len(script) - pos)
    text = re.sub(r"\[(pause|scare)\]", lambda m: " " * len(m.group(0)), text)
    return [who[m.start()] for m in re.finditer(r"\S+", text)]


def _norm(w):
    return re.sub(r"[^\w']", "", w).lower()


def align(script_words, heard):
    """Captions show the script's exact words; Whisper only supplies timing.
    Words Whisper misheard or missed get times interpolated from their neighbours."""
    import difflib
    sm = difflib.SequenceMatcher(None, [_norm(w) for w in script_words], [_norm(h["w"]) for h in heard], autojunk=False)
    times = [None] * len(script_words)
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            times[a + k] = (heard[b + k]["t0"], heard[b + k]["t1"])
    for op, a0, a1, b0, b1 in sm.get_opcodes():  # 1:1 substitutions ("lights" heard as "light") keep the heard timing
        if op == "replace" and a1 - a0 == b1 - b0:
            for k in range(a1 - a0):
                times[a0 + k] = (heard[b0 + k]["t0"], heard[b0 + k]["t1"])
    i = 0
    while i < len(times):  # fill gaps by spreading them evenly between known neighbours
        if times[i] is None:
            j = i
            while j < len(times) and times[j] is None:
                j += 1
            lo = times[i - 1][1] if i else 0.0
            hi = times[j][0] if j < len(times) else lo + 0.35 * (j - i)
            step = (hi - lo) / (j - i)
            for k in range(i, j):
                times[k] = (round(lo + step * (k - i), 3), round(lo + step * (k - i + 1), 3))
            i = j
        i += 1
    return [{"w": w, "t0": t[0], "t1": t[1]} for w, t in zip(script_words, times)]
