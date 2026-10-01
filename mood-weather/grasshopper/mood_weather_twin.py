"""
===============================================================================
 MOOD WEATHER  -  Grasshopper digital twin
===============================================================================
 Paste this whole file into ONE Python script component
   Rhino 8:  "Python 3 Script"      Rhino 7:  "GhPython Script"
 It rebuilds the room from the JSON that app.py writes once a second, so the
 model in Rhino shows the same heat pumps, fans, lights, zone values and
 artwork as the website.

 INPUTS  (Item Access for all; Type hint: none needed)
   all_json    text of exports/_all_systems.json     <- Read File
   room_json   text of exports/_room.json            <- Read File
   tick        anything - wire the Timer here so this script re-runs each tick
   field       0 temperature | 1 humidity | 2 wind | 3 light   (default 0)
   unit_scale  metres -> Rhino units   1 = metres (default), 1000 = mm, 3.28084 = ft
   folder      OPTIONAL path to the exports folder. Only used when all_json /
               room_json are empty: the script then reads the files itself.

 OUTPUTS
   field_mesh      six coloured zone tiles at the 1.2 m simulation slice
   room_edges      the 12 x 8 x 4 m room as lines
   unit_boxes      heat pumps, fans, humidifier (boxes turned to face their discharge)
   flow_lines      discharge direction of each unit (fans scale with m/s)
   light_spheres   can lights, sphere radius follows intensity
   labels          one text line per system
   label_pts       where to hang each label
   artwork         the artwork currently driving the room
   log             status / problems (read this first if something looks wrong)

 COORDINATES  x runs left to right, y runs front (0) to back, rotation_deg is the
 plan direction a unit discharges (0 = +x, 90 = +y), exactly as on the website.

 NOTE  Written to run on both IronPython 2.7 (Rhino 7) and Python 3 (Rhino 8):
 no f-strings, no type hints.
===============================================================================
"""
from __future__ import print_function, division

import json
import math
import os
from datetime import datetime

try:
    import Rhino.Geometry as rg
    import System.Drawing as sd
except ImportError:          # running outside Rhino (unit tests)
    rg = None
    sd = None


# --------------------------------------------------------------------------
# inputs that may or may not be wired
# --------------------------------------------------------------------------
def _inp(name, default=None):
    v = globals().get(name)
    return default if v is None else v


def _as_text(v):
    """Read File can hand back one string or a list of lines."""
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return "\n".join([str(x) for x in v])
    return str(v)


# --------------------------------------------------------------------------
# pure-Python part (no Rhino needed)
# --------------------------------------------------------------------------
RAMPS = {
    # field: (range key, stops as (position 0..1, (r, g, b)))
    "temperature": [(0.0, (40, 90, 170)), (0.5, (245, 245, 240)), (1.0, (190, 45, 40))],
    "humidity":    [(0.0, (250, 250, 245)), (1.0, (30, 110, 150))],
    "wind":        [(0.0, (250, 250, 245)), (1.0, (40, 40, 40))],
    "light":       [(0.0, (20, 20, 20)), (1.0, (255, 236, 170))],
}
FIELD_NAMES = ["temperature", "humidity", "wind", "light"]
FIELD_UNITS = {"temperature": "C", "humidity": "%RH", "wind": "m/s", "light": "lux"}


def ramp_rgb(field, t):
    """Approximate the website's colour ramps (three/two-stop blend)."""
    t = max(0.0, min(1.0, t))
    stops = RAMPS[field]
    for i in range(len(stops) - 1):
        p0, c0 = stops[i]
        p1, c1 = stops[i + 1]
        if t <= p1:
            u = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
            return tuple(int(round(c0[k] + (c1[k] - c0[k]) * u)) for k in range(3))
    return stops[-1][1]


def age_seconds(stamp):
    """Seconds since an ISO-8601 UTC stamp like 2026-09-30T20:57:03+00:00."""
    try:
        then = datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
        return (datetime.utcnow() - then).total_seconds()
    except Exception:
        return None


def load_payload(all_text, room_text, folder):
    """Returns (systems dict, room dict, problems list)."""
    problems = []
    all_text, room_text = _as_text(all_text), _as_text(room_text)
    if (not all_text or not room_text) and folder:
        try:
            with open(os.path.join(folder, "_all_systems.json")) as fh:
                all_text = all_text or fh.read()
            with open(os.path.join(folder, "_room.json")) as fh:
                room_text = room_text or fh.read()
        except Exception as exc:
            problems.append("could not read files from folder: %s" % exc)
    if not all_text or not room_text:
        problems.append("waiting for JSON (check Read File paths, or set 'folder')")
        return None, None, problems
    try:
        return json.loads(all_text), json.loads(room_text), problems
    except ValueError as exc:
        # Python may be mid-write on some platforms; the next tick will succeed.
        problems.append("JSON not readable this tick (%s)" % exc)
        return None, None, problems


def zone_tiles(room, field):
    """[(corner points (x,y) x4, (r,g,b), value)] for the six zones."""
    W, D = room["room_m"]["w"], room["room_m"]["d"]
    cols, rows = room["zone_grid"]["cols"], room["zone_grid"]["rows"]
    lo, hi = room["field_ranges"][field]
    vals = room["zone_values"][field]
    tiles = []
    for r in range(rows):
        for c in range(cols):
            z = r * cols + c
            x0, x1 = W * c / cols, W * (c + 1) / cols
            y0, y1 = D * r / rows, D * (r + 1) / rows
            t = (vals[z] - lo) / (hi - lo) if hi != lo else 0.0
            tiles.append(([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], ramp_rgb(field, t), vals[z]))
    return tiles


def label_for(s):
    typ = s["system_type"]
    sp, rd = s["setpoint"], s["reading"]
    if typ == "heat_pump":
        return "%s  target %.1f C  reads %.1f C" % (s["label"], sp, rd)
    if typ == "humidifier":
        return "%s  target %.0f %%RH  reads %.1f %%RH" % (s["label"], sp, rd)
    if typ == "fan":
        return "%s  %.1f m/s  reads %.2f m/s" % (s["label"], sp, rd)
    return "%s  intensity %.2f  %.0f lux" % (s["label"], sp, rd)


UNIT_SIZE = {             # (length along discharge, width, height) in metres
    "heat_pump":  (0.90, 0.35, 0.60),
    "fan":        (0.35, 0.70, 0.70),
    "humidifier": (0.45, 0.45, 0.90),
}


def arrow_length(s):
    typ = s["system_type"]
    if typ == "fan":
        return 0.6 + 0.5 * max(0.0, s["output"])
    if typ == "heat_pump":
        return 1.1
    if typ == "humidifier":
        return 0.8
    return 0.0


# --------------------------------------------------------------------------
# Rhino part
# --------------------------------------------------------------------------
def build(systems, room, field_idx, k):
    H = room["room_m"]["h"]
    W, D = room["room_m"]["w"], room["room_m"]["d"]
    field = FIELD_NAMES[field_idx]

    # zone tiles as ONE mesh with per-quad vertex colours
    mesh = rg.Mesh()
    slice_z = 1.2
    for corners, rgb, _v in zone_tiles(room, field):
        i0 = mesh.Vertices.Count
        for (x, y) in corners:
            mesh.Vertices.Add(x * k, y * k, slice_z * k)
            mesh.VertexColors.Add(sd.Color.FromArgb(rgb[0], rgb[1], rgb[2]))
        mesh.Faces.AddFace(i0, i0 + 1, i0 + 2, i0 + 3)
    mesh.Normals.ComputeNormals()

    # room outline
    c = [(0, 0), (W, 0), (W, D), (0, D)]
    edges = []
    for z in (0.0, H):
        for i in range(4):
            a, b = c[i], c[(i + 1) % 4]
            edges.append(rg.Line(rg.Point3d(a[0] * k, a[1] * k, z * k),
                                 rg.Point3d(b[0] * k, b[1] * k, z * k)))
    for (x, y) in c:
        edges.append(rg.Line(rg.Point3d(x * k, y * k, 0), rg.Point3d(x * k, y * k, H * k)))

    boxes, flows, spheres, labels, label_pts = [], [], [], [], []
    for sid in sorted(systems.keys()):
        s = systems[sid]
        p = s["position_m"]
        origin = rg.Point3d(p["x"] * k, p["y"] * k, p["z"] * k)
        typ = s["system_type"]
        if typ == "can_light":
            r = (0.12 + 0.30 * max(0.0, min(1.0, s["setpoint"]))) * k
            spheres.append(rg.Sphere(rg.Point3d(origin.X, origin.Y, origin.Z - 0.2 * k), r).ToBrep())
        else:
            ang = math.radians(s["rotation_deg"])
            xax = rg.Vector3d(math.cos(ang), math.sin(ang), 0.0)
            yax = rg.Vector3d(-math.sin(ang), math.cos(ang), 0.0)
            plane = rg.Plane(origin, xax, yax)
            L, Wd, Ht = UNIT_SIZE[typ]
            box = rg.Box(plane, rg.Interval(-L / 2 * k, L / 2 * k),
                         rg.Interval(-Wd / 2 * k, Wd / 2 * k), rg.Interval(-Ht / 2 * k, Ht / 2 * k))
            boxes.append(box.ToBrep())
            start = origin + xax * (L / 2 * k)
            flows.append(rg.Line(start, start + xax * (arrow_length(s) * k)))
        labels.append(label_for(s))
        label_pts.append(rg.Point3d(origin.X, origin.Y, origin.Z + 0.7 * k))
    return mesh, edges, boxes, flows, spheres, labels, label_pts


# --------------------------------------------------------------------------
# component body
# --------------------------------------------------------------------------
def run():
    field_idx = int(_inp("field", 0))
    if field_idx < 0 or field_idx > 3:
        field_idx = 0
    k = float(_inp("unit_scale", 1.0))
    systems, room, problems = load_payload(_inp("all_json"), _inp("room_json"), _inp("folder"))
    if systems is None:
        return None, None, None, None, None, None, None, "", "; ".join(problems)

    age = age_seconds(room.get("exported_utc", ""))
    if age is not None and age > 5:
        problems.append("exports are %.0f s old - is app.py running in Simulate mode?" % age)
    out = build(systems, room, field_idx, k)

    ex = room.get("exhibition") or {}
    art = ex.get("artwork") or {}
    artwork = ""
    if art:
        artwork = "%s - %s (%s)   [%s/%s]" % (art.get("title", ""), art.get("artist", ""),
                                               art.get("date", ""), ex.get("position", "?"),
                                               ex.get("queue_length", "?"))
    log = "showing %s | %d systems | %s" % (
        FIELD_NAMES[field_idx], len(systems),
        ("; ".join(problems)) if problems else "ok")
    return out + (artwork, log)


if rg is not None:
    (field_mesh, room_edges, unit_boxes, flow_lines, light_spheres,
     labels, label_pts, artwork, log) = run()
