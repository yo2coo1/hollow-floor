"""Chatterbox TTS worker (MIT licence, runs locally, free). Runs inside its own venv (.venv-cbx: torch pins clash with
the main one) and is driven by voice.py over stdin/stdout, one JSON job per line:
  {"text": ..., "ref": "voice.wav", "ex": 0.5, "cfg": 0.5, "out": "seg.wav"}  ->  prints "ok" (or "err <message>")
ex = emotion exaggeration (0.25 flat .. 1.5 frantic), cfg = pacing guidance (lower = slower, more dramatic)."""
import json
import sys

import soundfile as sf
import torch

torch.set_num_threads(max(1, torch.get_num_threads()))
from chatterbox.tts import ChatterboxTTS  # noqa: E402

dev = "cuda" if torch.cuda.is_available() else "cpu"
model = ChatterboxTTS.from_pretrained(device=dev)
print("ready", flush=True)
for line in sys.stdin:
    try:
        j = json.loads(line)
        torch.manual_seed(j.get("seed", 7))  # same script -> same delivery on every re-render
        w = model.generate(j["text"], audio_prompt_path=j["ref"], exaggeration=j["ex"], cfg_weight=j["cfg"])
        sf.write(j["out"], w.squeeze(0).cpu().numpy(), model.sr)
        print("ok", flush=True)
    except Exception as e:  # report and keep serving; voice.py raises on "err"
        print("err", str(e).replace("\n", " ")[:300], flush=True)
