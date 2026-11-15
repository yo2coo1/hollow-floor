"""Step 3 check: the test hallway scene, narrated, captioned by Whisper, mixed and rendered.
Usage: python scripts/voiced_test.py [engine voice]   (defaults to config.toml voice)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hollowfloor import build  # noqa: E402
from hollowfloor.config import CONFIG  # noqa: E402

SCRIPT = ("My boss gave me one rule for the night shift. Never open the last door. [pause] "
          "Tonight, I heard knocking. From the other side. [pause] "
          "Then the lights started blinking... [scare] And it opened.")

if len(sys.argv) == 3:
    CONFIG["voice"]["engine"], CONFIG["voice"]["voice_id"] = sys.argv[1], sys.argv[2]
name = f'test_voiced_{CONFIG["voice"]["voice_id"]}'
print(build.build("scenes/test_hallway_voiced.json", SCRIPT, f"out/{name}.mp4"))
