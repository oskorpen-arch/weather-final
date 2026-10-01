"""
Regenerates workflow_diagram.svg from the rules and placements actually written in
app.py, so the diagram cannot drift away from the code.

    python make_workflow_diagram.py

Reads RULES and PLACEMENTS with ast (no Numba needed). Re-run after editing rules.
"""
import ast
import os
import textwrap
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))


def grab(name):
    tree = ast.parse(open(os.path.join(HERE, "app.py"), encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return ast.literal_eval(node.value)
    raise SystemExit("could not find %s in app.py" % name)


RULES, PLACEMENTS = grab("RULES"), grab("PLACEMENTS")
by_api = {}
for r in RULES:
    by_api.setdefault(r["api"], []).append(r)
label = {p["id"]: p["label"] for p in PLACEMENTS}


def g(v):
    return ("%g" % v)


def slash(vals):
    return " / ".join(g(v) for v in vals)


def names(rs):
    return ", ".join(label.get(r["system"], r["system"].replace("_", " ").title()) for r in rs)


out = []
W, H = 1600, 1100


def t(x, y, s, size=14, weight=400, anchor="start", fill="#0b0b0b", style=""):
    out.append('<text x="%s" y="%s" font-size="%s" font-weight="%s" text-anchor="%s" fill="%s" %s>%s</text>'
               % (x, y, size, weight, anchor, fill, style, escape(s)))


def box(x, y, w, h, title, lines, num):
    out.append('<rect x="%d" y="%d" width="%d" height="%d" rx="14" fill="#fcfcfb" stroke="#0b0b0b" stroke-width="3.5"/>' % (x, y, w, h))
    t(x + 18, y + 34, "%s  %s" % (num, title), 20, 700)
    yy = y + 64
    for ln in lines:
        for piece in textwrap.wrap(ln, 34) or [""]:
            t(x + 18, yy, piece, 14, 400, fill="#3a3936")
            yy += 20


def arrow(x1, y1, x2, y2, path=None):
    d = path or "M%d %d L%d %d" % (x1, y1, x2, y2)
    out.append('<path d="%s" fill="none" stroke="#0b0b0b" stroke-width="2.5" marker-end="url(#a)"/>' % d)


cols = [40, 355, 670, 985, 1300]
bw = 260

t(40, 52, "MOOD WEATHER  -  workflow and methodology", 30, 700)
t(40, 82, "How the most popular artwork at the Art Institute of Chicago becomes the temperature, air speed, humidity and light of a room.", 15, 400, fill="#52514e")

# ---- row 1: data in -> room out
r1y, r1h = 120, 205
box(cols[0], r1y, bw, r1h, "Art Institute API", [
    "Live ranking of the highlighted works on view (boost_rank).",
    "The top 12 take turns, one per minute.",
    "No live visitor counts exist, so this stands in for 'most popular now'."], "1")
box(cols[1], r1y, bw, r1h, "aic_feed.py", [
    "Looks up what that artwork usually evokes (course psychophysiology pipeline):",
    "valence, arousal, tension, luminance."], "2")
box(cols[2], r1y, bw, r1h, "app.py: IF-THEN", [
    "Five numbers polled every 3 s.",
    "One rule per system, read as IF feed > value THEN setpoint ELSE setpoint.",
    "Rules live in Python only."], "3")
box(cols[3], r1y, bw, r1h, "Building systems", [
    "3 heat pumps, 2 fans, 1 humidifier,",
    "6 can lights (they define the six zones).",
    "Each receives a setpoint."], "4")
box(cols[4], r1y, bw, r1h, "Air simulation", [
    "Real 2D Navier-Stokes, 96 x 64 cells.",
    "Temperature, humidity, wind and light fields in a 12 x 8 x 4 m room.",
    "The room answers over time."], "5")
for i in range(4):
    arrow(cols[i] + bw, r1y + r1h // 2, cols[i + 1], r1y + r1h // 2)

# ---- row 2: out to Website / JSON / Rhino
r2y, r2h = 395, 175
ty = 610   # top of the mechanism table and the Rhino box
box(cols[0], r2y, bw, r2h, "Website", [
    "Draws the room, graphs, live rules and this diagram.",
    "Computes nothing."], "6")
box(cols[2], r2y, bw, r2h, "JSON, every 1 s", [
    "exports/: one file per system, plus _room.json (zone values, current artwork).",
    "Written then renamed, so never half-written."], "7")
box(cols[3], r2y, bw, r2h, "Timer + Read File", [
    "Timer (1000 ms) re-triggers Read File.",
    "A relay script keeps Read File refreshing."], "8")
box(cols[4], r2y, bw, r2h, "Grasshopper script", [
    "Parses the JSON and rebuilds boxes, flow lines, lights and zone colours."], "9")
mid = cols[2] + bw // 2
arrow(mid, r1y + r1h, mid, r2y)
elbow = r1y + r1h + 34
arrow(0, 0, 0, 0, "M%d %d L%d %d L%d %d L%d %d" % (mid - 40, r1y + r1h, mid - 40, elbow, cols[0] + bw // 2, elbow, cols[0] + bw // 2, r2y))
arrow(cols[2] + bw, r2y + r2h // 2, cols[3], r2y + r2h // 2)
arrow(cols[3] + bw, r2y + r2h // 2, cols[4], r2y + r2h // 2)
box(cols[4], ty, bw, 120, "Rhino digital twin", [
    "The same room, same artwork, same setpoints as the page."], "10")
arrow(cols[4] + bw // 2, r2y + r2h, cols[4] + bw // 2, ty)

# ---- mapping table (generated from RULES)
out.append('<rect x="40" y="%d" width="1200" height="456" rx="14" fill="#fcfcfb" stroke="#0b0b0b" stroke-width="3.5"/>' % ty)
t(60, ty + 34, "THE MECHANISM  -  every input, every rule, every output", 18, 700)
hx = [60, 380, 950]
for x, s in zip(hx, ("ARTWORK MEASURE (LIVE INPUT)", "RULE IN app.py", "THE ROOM RESPONDS")):
    t(x, ty + 66, s, 12, 700, fill="#52514e")
out.append('<line x1="60" y1="%d" x2="1220" y2="%d" stroke="#0b0b0b" stroke-width="1.5"/>' % (ty + 76, ty + 76))

hp = by_api.get("aic_valence", [])
fn = by_api.get("aic_arousal", [])
hu = by_api.get("aic_tension", [])
li = by_api.get("aic_luminance", [])
rows = [
    ("Valence", "sad / heavy  <->  happy / uplifting", "drives TEMPERATURE",
     "%s: IF valence > %s THEN %s \u00b0C, ELSE %s \u00b0C." % (names(hp), slash([r["value"] for r in hp]), slash([r["then"] for r in hp]), slash([r["otherwise"] for r in hp])),
     "Sadder artwork, colder room (%s to %s \u00b0C). The happiest artwork warms it up to %s \u00b0C." % (g(min(r["otherwise"] for r in hp)), g(max(r["otherwise"] for r in hp)), g(max(r["then"] for r in hp))) if hp else ""),
    ("Arousal", "gentle  <->  turbulent", "drives AIR SPEED",
     "%s: IF arousal > %s THEN %s m/s, ELSE %s m/s." % (names(fn), slash([r["value"] for r in fn]), slash([r["then"] for r in fn]), slash([r["otherwise"] for r in fn])),
     "Gentle artwork, near-still air. Turbulent artwork, two crossing jets."),
    ("Tension", "slack  <->  taut", "drives HUMIDITY",
     "%s: IF tension > %s THEN %s %%RH, ELSE %s %%RH." % (names(hu), slash([r["value"] for r in hu]), slash([r["then"] for r in hu]), slash([r["otherwise"] for r in hu])) if hu else "",
     "Taut artwork dries the air; slack artwork lets it go damp."),
    ("Luminance", "dark  <->  bright (CIE L*)", "drives LIGHT LEVEL (inverted)",
     "%s: each lit (1.0) only while luminance < %s, otherwise %s." % (", ".join(r["system"].replace("light_", "Light ") for r in li), slash([r["value"] for r in li]), g(li[0]["otherwise"])) if li else "",
     "Brighter artwork, dimmer room: the space supplies what the picture lacks."),
]
ry = ty + 92
for name, scale, drives, rule, effect in rows:
    t(hx[0], ry + 22, name, 18, 700)
    t(hx[0], ry + 44, scale, 13, 400, fill="#52514e")
    t(hx[0], ry + 64, drives, 12, 700, fill="#52514e")
    yy = ry + 22
    for piece in textwrap.wrap(rule, 62):
        t(hx[1], yy, piece, 13, 400)
        yy += 19
    yy = ry + 22
    for piece in textwrap.wrap(effect, 38):
        t(hx[2], yy, piece, 13, 400)
        yy += 19
    if name != rows[-1][0]:
        out.append('<line x1="60" y1="%d" x2="1220" y2="%d" stroke="#c4c4bf" stroke-width="1"/>' % (ry + 84, ry + 84))
    ry += 92

t(40, 1082, "Valence, arousal and tension are research-based estimates computed from image features, not measurements of any visitor. "
            "Thresholds are calibrated to the spread of the current top-ranked works.", 12, 400, fill="#52514e")

svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
       'font-family="Helvetica, Arial, sans-serif">'
       '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
       '<path d="M0 0L10 5L0 10z" fill="#0b0b0b"/></marker></defs>'
       '<rect width="%d" height="%d" fill="#f9f9f7"/>%s</svg>') % (W, H, W, H, W, H, "\n".join(out))
path = os.path.join(HERE, "workflow_diagram.svg")
open(path, "w", encoding="utf-8").write(svg)
print("wrote", path)
