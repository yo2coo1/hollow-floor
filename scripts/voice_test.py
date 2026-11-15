"""Section 7 voice test: same ~30 s scary sample with each candidate voice. Output: out/voice_test/*.wav + times."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hollowfloor.voice import speak  # noqa: E402

SAMPLE = ("I work the night shift, at an old storage building. Nobody comes in after ten. [pause] "
          "Last night, at three in the morning... the phone at the front desk rang. "
          "I picked it up. Nobody spoke. I could only hear breathing. [pause] And then, a whisper. It said my name. [scare] "
          "The thing is... that phone isn't connected. It hasn't been, for years.")

CANDIDATES = [
    ("kokoro", "am_michael"), ("kokoro", "am_fenrir"), ("kokoro", "bm_george"), ("kokoro", "af_heart"),
    ("edge", "en-US-AndrewNeural"), ("edge", "en-US-BrianNeural"), ("edge", "en-US-ChristopherNeural"), ("edge", "en-GB-RyanNeural"),
]

if __name__ == "__main__":
    out = Path("out/voice_test"); out.mkdir(parents=True, exist_ok=True)
    for engine, voice in CANDIDATES:
        t = time.time()
        try:
            r = speak(SAMPLE, out / f"{engine}_{voice}.wav", engine=engine, voice=voice)
            print(f"{engine:7} {voice:26} {r['seconds']:5.1f}s audio  made in {time.time() - t:4.1f}s  markers={r['markers']}")
        except Exception as e:
            print(f"{engine:7} {voice:26} FAILED: {e}")
