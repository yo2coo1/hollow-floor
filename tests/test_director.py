"""python -m tests.test_director"""
from hollowfloor import director

scene = {"duration": 10, "rooms": [{"w": 5, "d": 6, "pos": [0, 0]}, {"w": 6, "d": 3, "pos": [0, -4.5]}],
         "characters": [{"id": "me", "path": [[0, 1.2, -2.0]]}, {"id": "n", "path": [[0, 1.2, -4.3]], "face": [[0, 0]]}],
         "props": [{"type": "door", "name": "door", "pos": [1.2, 0, -2.9]}],
         "shots": [{"t": 0, "tpl": "wide", "target": "me"}, {"t": 9, "tpl": "peephole", "target": "door"}]}
out = director.apply(scene)["shots"]
assert all(-2.5 < s["from"][0] < 2.5 for s in out if s["fov"] != 100), "camera left the room"
assert all(b["t"] - a["t"] <= 4 for a, b in zip(out, out[1:])), "long shot not split"
peep = [s for s in out if s["fov"] == 100][0]
assert peep["from"][2] < -2.9, "peephole must sit beyond the door"

# a wall that forces the camera closer widens the lens instead of filling the frame with a face
tight = {"director_v": 2, "duration": 3, "rooms": [{"w": 2.0, "d": 2.2, "pos": [0, 0]}],
         "characters": [{"id": "me", "path": [[0, 0, 0.3]], "face": [[0, 180]]}], "props": [],
         "shots": [{"t": 0, "tpl": "face", "target": "me"}]}
f = director.apply(tight)["shots"][0]
assert f["fov"] > director.FOV["face"] + 10, f
# someone standing between the camera and the subject: the face shot swings to a clear angle
two = {"director_v": 2, "duration": 3, "rooms": [{"w": 10, "d": 10, "pos": [0, 0]}],
       "characters": [{"id": "a", "path": [[0, 0, 0]], "face": [[0, 0]]}, {"id": "b", "path": [[0, 0, 1.0]]}], "props": [],
       "shots": [{"t": 0, "tpl": "face", "target": "a"}]}
f = director.apply(two)["shots"][0]
assert not director._blocked(two, "a", f["to"], (0, 0), 1.5), f
assert director.apply({**tight, "director_v": 1, "shots": [{"t": 0, "tpl": "face", "target": "me"}]})["shots"][0]["fov"] == director.FOV["face"], "v1 must stay unchanged"
print("director self-check: OK")
