"""Camera shot templates: a scene says {"tpl": "push", "target": "door"} and this module computes the camera.
Targets are prop names or character ids. Characters face +z at yaw 0; rooms are open toward +z (camera side)."""
import math

FOV = {"wide": 55, "push": 45, "face": 42, "ots": 45, "low": 55, "insert": 50, "peephole": 100, "pov": 60}


def _interp(path, t):
    """Linear [[t, *vals]] interpolation (matches the JS renderer for paths)."""
    if t <= path[0][0]:
        return path[0][1:]
    for a, b in zip(path, path[1:]):
        if t <= b[0]:
            k = (t - a[0]) / max(1e-9, b[0] - a[0])
            return [x + (y - x) * k for x, y in zip(a[1:], b[1:])]
    return path[-1][1:]


def _yaw(ch, t):
    for f in reversed(ch.get("face") or []):
        if t >= f[0]:
            return math.radians(f[1])
    yaw, path = 0.0, ch.get("path") or []
    for a, b in zip(path, path[1:]):
        if a[0] < t and (a[1], a[2]) != (b[1], b[2]):
            yaw = math.atan2(b[1] - a[1], b[2] - a[2])
    return yaw


def locate(scene, name, t):
    """-> (x, y_top, z, yaw). y_top is a good look-at height (head for people)."""
    for c in scene.get("characters", []):
        if c["id"] == name:
            x, z = _interp(c["path"], t)
            return x, 1.75 * c.get("scale", 1), z, _yaw(c, t)
    for p in scene.get("props", []):
        if p.get("name") == name:
            pos = _interp(p["path"], t)[0:3] if p.get("path") else p["pos"]
            return pos[0], pos[1] + p.get("look_h", 1.0), pos[2], math.radians(p.get("rot", 0))
    raise KeyError(f"shot target '{name}' is not a prop name or character id")


def camera(shot, scene, t_end):
    tpl = shot.get("tpl")
    if not tpl:
        return shot
    t0 = shot["t"]
    tm = (t0 + t_end) / 2  # aim at where a moving subject is mid-shot (a sliding note, a walking person)
    x, y, z, yaw = locate(scene, shot["target"], tm)
    fx, fz = math.sin(yaw), math.cos(yaw)       # target's facing direction
    rx, rz = fz, -fx                            # its right-hand side
    side = shot.get("side", 1)                  # 1 = camera right, -1 = left
    d = shot.get("dist", 1)                     # distance multiplier
    out = {"t": t0, "fov": shot.get("fov", FOV[tpl])}
    if tpl == "wide":
        out.update(**{"from": [x + 1.6 * side * d, 2.3, z + 3.6 * d], "to": [x + 1.3 * side * d, 2.2, z + 3.0 * d], "look": [x, 1.0, z]})
    elif tpl == "push":
        out.update(**{"from": [x + fx * 3.0 * d + rx * 0.35 * side, 1.6, z + fz * 3.0 * d + rz * 0.35 * side],
                      "to": [x + fx * 1.8 * d + rx * 0.15 * side, 1.55, z + fz * 1.8 * d + rz * 0.15 * side], "look": [x, y, z]})
    elif tpl == "face":
        # swing the camera around the subject: an explicit angle, else the first angle with nobody standing in the way
        smart = scene.get("director_v", 1) >= 2  # v1 = the camera every hand-reviewed story was checked with
        for deg in ([shot["angle"]] if "angle" in shot else [0, 35, -35, 60, -60] if smart else [0]):
            a = math.radians(deg)
            fx, fz = math.sin(yaw + a), math.cos(yaw + a)
            rx, rz = fz, -fx
            cam = {"from": [x + fx * 2.3 * d + rx * 0.3 * side, y + 0.05, z + fz * 2.3 * d + rz * 0.3 * side],
                   "to": [x + fx * 2.0 * d, y + 0.03, z + fz * 2.0 * d], "look": [x, y - 0.1, z]}
            if not _blocked(scene, shot["target"], cam["to"], (x, z), tm):
                break
        out.update(**cam)
    elif tpl == "ots":  # over the shoulder of target, looking at shot["at"]
        lx, ly, lz, _ = locate(scene, shot["at"], tm)
        out.update(**{"from": [x - fx * 1.4 + rx * 0.6 * side, y + 0.2, z - fz * 1.4 + rz * 0.6 * side],
                      "to": [x - fx * 1.15 + rx * 0.55 * side, y + 0.18, z - fz * 1.15 + rz * 0.55 * side], "look": [lx, ly, lz]})
    elif tpl == "low":
        out.update(**{"from": [x + fx * 1.3 * d + rx * 0.5 * side, 0.6, z + fz * 1.3 * d + rz * 0.5 * side], "look": [x, y + 0.2, z]})
    elif tpl == "insert":  # close, looking down at a small prop
        out.update(**{"from": [x + 0.1, y + 0.7, z + 0.55], "to": [x + 0.05, y + 0.5, z + 0.4], "look": [x, y, z]})
    elif tpl == "peephole":  # from just beyond a door, looking out through it
        out.update(**{"from": [x - fx * 0.25, 1.6, z - fz * 0.25], "look": [x - fx * 3, 1.55, z - fz * 3]})
    elif tpl == "pov":  # first-person: from target's eyes toward shot["at"]
        lx, ly, lz, _ = locate(scene, shot["at"], tm)
        out.update(**{"from": [x + fx * 0.3, y, z + fz * 0.3], "look": [lx, ly, lz]})
    else:
        raise KeyError(f"unknown shot template '{tpl}'")
    for k in ("shake", "lookTo"):
        if k in shot:
            out[k] = shot[k]
    return out


def _visible(ch, t):
    v = None
    for f in ch.get("visible") or []:
        if t >= f[0]:
            v = f[1]
    return v is None or bool(v)


def _blocked(scene, target, cam, subj, t, radius=0.4):
    """True if another visible character stands between the camera and the subject (it would fill the frame)."""
    cx, cz = cam[0], cam[2]
    sx, sz = subj
    L2 = (sx - cx) ** 2 + (sz - cz) ** 2 or 1e-9
    for c in scene.get("characters", []):
        if c["id"] == target or not _visible(c, t):
            continue
        px, pz = _interp(c["path"], t)
        k = ((px - cx) * (sx - cx) + (pz - cz) * (sz - cz)) / L2
        if 0.05 < k < 0.95 and math.hypot(px - (cx + k * (sx - cx)), pz - (cz + k * (sz - cz))) < radius:
            return True
    return False


def _split_long(shots, duration, max_len=3.8):
    """Spec: a new shot at least every 3-4 s. A template shot that runs long gets a reverse-angle cut of the same subject."""
    out = []
    for i, s in enumerate(shots):
        end = shots[i + 1]["t"] if i + 1 < len(shots) else duration
        out.append(s)
        if not s.get("tpl") and end - s["t"] > max_len and "from" in s:  # hand-placed camera: cut to a tighter zoom
            n = int((end - s["t"]) // max_len)
            for k in range(1, n + 1):
                out.append({**s, "t": round(s["t"] + (end - s["t"]) * k / (n + 1), 3), "fov": round(s.get("fov", 50) * (0.78 if k % 2 else 1), 1)})
            continue
        if s.get("tpl") and end - s["t"] > max_len:
            n = int((end - s["t"]) // max_len)
            for k in range(1, n + 1):
                alt = {**s, "t": round(s["t"] + (end - s["t"]) * k / (n + 1), 3), "side": -s.get("side", 1) if k % 2 else s.get("side", 1),
                       "dist": s.get("dist", 1) * (0.8 if k % 2 else 1)}
                out.append(alt)
    return out


def _clamp(scene, shot, cam):
    """Keep the camera inside the room its subject stands in, so it never films the back of a wall.
    The first room is open toward +z (the audience side), so the camera may pull back beyond it there."""
    if not shot.get("tpl") or shot["tpl"] in ("peephole", "pov") or "from" not in cam:  # hand-placed shots are trusted as written
        return cam
    rooms = scene.get("rooms") or [scene.get("room", {})]
    tx, _, tz, _ = locate(scene, shot["target"], shot["t"])
    key = "to" if "to" in cam else "from"
    dist = lambda: math.dist(cam[key], cam["look"])
    before = dist()
    for i, r in enumerate(rooms):
        cx, cz = (r.get("pos") or [0, 0]); w, d, h = r.get("w", 6), r.get("d", 8), r.get("h", 3)
        if abs(tx - cx) <= w / 2 + 0.15 and abs(tz - cz) <= d / 2 + 0.15:
            zmax = cz + d / 2 + (6 if i == 0 else -0.25)
            for k in ("from", "to"):
                if k in cam:
                    x, y, z = cam[k]
                    cam[k] = [min(max(x, cx - w / 2 + 0.35), cx + w / 2 - 0.35), min(max(y, 0.3), h - 0.2),
                              min(max(z, cz - d / 2 + 0.35), zmax)]
            break
    after = dist()
    if scene.get("director_v", 1) >= 2 and after < before * 0.95:  # a wall pushed the camera closer: widen the lens so the subject keeps its framing
        cam["fov"] = round(min(75.0, math.degrees(2 * math.atan(math.tan(math.radians(cam["fov"]) / 2) * before / max(after, 0.3)))), 1)
    return cam


def apply(scene):
    from .config import CONFIG
    style, styles = scene.get("style") or {}, CONFIG.get("styles", {})
    max_len = styles.get("pacing", {}).get(style.get("pacing"), 3.8)
    shake = styles.get("camera", {}).get(style.get("camera"))
    shots = _split_long(sorted(scene["shots"], key=lambda s: s["t"]), scene["duration"], max_len)
    if shake is not None:  # the story's camera style: handheld wobble ... locked tripod; a shot's own shake (scares) wins
        shots = [s if "shake" in s else {**s, "shake": shake} for s in shots]
    ends = [s["t"] for s in shots[1:]] + [scene["duration"]]
    scene["shots"] = [_clamp(scene, s, camera(s, scene, e)) for s, e in zip(shots, ends)]
    return scene
