"""Glue: narration -> word timings -> scene times resolved -> audio mix -> video.
Scene JSON may use time references instead of numbers anywhere a time goes:
  "scare1", "scare1-0.8"   marker from the script's [scare] tags
  "w:knocking+0.3"         when that word is first spoken (Whisper timing)
  "end", "end+1.2"         when narration ends"""
import json
import re
import subprocess
from pathlib import Path

from . import audio, director, voice
from .config import CONFIG, ROOT

REF = re.compile(r"^(?:(scare\d+|end)|w:([\w']+))([+-]\d+(?:\.\d+)?)?$")


def resolve(obj, markers, words):
    def t_of(s):
        m = REF.match(s)
        if not m:
            return s
        if m.group(1):
            base = markers[m.group(1)]
        else:
            hits = [w["t0"] for w in words if re.sub(r"[^\w']", "", w["w"]).lower() == m.group(2).lower()]
            if not hits:
                raise KeyError(f"word '{m.group(2)}' not found in narration")
            base = hits[0]
        return round(base + float(m.group(3) or 0), 3)
    if isinstance(obj, dict):
        return {k: resolve(v, markers, words) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve(v, markers, words) for v in obj]
    return t_of(obj) if isinstance(obj, str) else obj


def build(scene_path, script, out_mp4, scale=1.0):
    """scene_path: a scene JSON file or an already-loaded scene dict."""
    v = CONFIG["voice"]
    out = Path(out_mp4); work = out.with_suffix("")
    work.mkdir(parents=True, exist_ok=True)
    narr = work / "narration.wav"
    r = voice.speak(script, narr, engine=v["engine"], voice=v["voice_id"], rate=v["rate"], pitch=v["pitch"], roles=v.get("roles"))
    words = voice.word_times(narr, script)
    markers = {**r["markers"], "end": r["seconds"]}
    raw = scene_path if isinstance(scene_path, dict) else json.loads(Path(scene_path).read_text())
    scene = director.apply(resolve(raw, markers, words))
    scene["captions"] = words
    r_cfg = CONFIG.get("render", {})
    scene.setdefault("quality", {"aa": True, "shadows": r_cfg.get("shadows", "basic"), "scale": r_cfg.get("scale", 0.75)})
    scene.setdefault("fps", r_cfg.get("fps", 24))
    (work / "scene.resolved.json").write_text(json.dumps(scene, indent=1))
    mix = work / "mix.wav"
    audio.mix(narr, mix, duration=scene["duration"], events=scene.get("audio", []), voice_meta=f'{v["engine"]}:{v["voice_id"]}')
    subprocess.run(["node", str(ROOT / "render/render.js"), str(work / "scene.resolved.json"), str(out), "--audio", str(mix), "--scale", str(scale)], check=True)
    last = max((t for k, t in markers.items() if k.startswith("scare")), default=scene["duration"] / 2)
    # thumbnails A/B: a scene may pick its own moments ("thumbAt": ["w:steps+0.2", "scare1-0.5"]), else the tensest frames
    thumbs = scene.get("thumbAt") or [last + 0.35, last - 1.0]
    return {"video": str(out), "seconds": scene["duration"], "markers": markers, "words": len(words), "thumbs": thumbs}
