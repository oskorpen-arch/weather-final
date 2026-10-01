"""
===============================================================================
 PROMPTING IMPACT / READING BIG DATA AT THE HUMAN SCALE  (IARC 425)
 Exhibition Template - Python Backend
===============================================================================

 This is the "brain" box in the workflow diagram:

     API  ->  PYTHON  ->  JSON  ->  GRASSHOPPER  ->  RHINO
                 |
                 v
              WEBSITE

 It does four jobs:
   1. Polls whatever APIs are registered in API_REGISTRY.
   2. Runs a real 2D incompressible Navier-Stokes simulation of the gallery
      air (Numba JIT compiled), plus photometric light and humidity fields.
   3. Applies IF-THEN rules that let an API value drive a building system.
   4. Serves the website AND writes one JSON per system for Grasshopper.

-------------------------------------------------------------------------------
 AI-AGENT ORIENTATION  (read this before editing - it is the whole map)
-------------------------------------------------------------------------------
 This file is written to be edited by an AI coding agent working from a
 student's plain-English prompt. Everything a student is likely to change
 lives inside a clearly delimited block that looks like this:

     # >>> AI-AGENT EDIT ZONE: <NAME> >>>
     ...
     # <<< END AI-AGENT EDIT ZONE: <NAME> <<<

 There are FOUR of them, in this order:
     1. API_REGISTRY   - which data sources exist
     2. PLACEMENTS     - where the systems sit in the room (simulate mode)
     3. RULES          - the IF-THEN links between an API and a system
     4. TUNING         - physics constants and display ranges

 Do not restructure the solver to satisfy a placement request. Placements,
 rules and APIs are DATA; the solver is machinery. If a student says
 "put the fan in the back left corner blowing right", that is a PLACEMENTS
 edit only.

 Coordinate system, because agents get this wrong:
     x runs 0 -> ROOM_W (12 m), left to right across the front of the room.
     y runs 0 -> ROOM_D (8 m), front (near the viewer) to back.
     rot is degrees, 0 = blowing toward +x (right), 90 = blowing toward +y
     (toward the back), 180 = left, 270 = toward the front. Counter-clockwise
     in plan is increasing degrees.
     Zone numbering matches the six ceiling can lights, left to right then
     front to back:   1 2 3   <- front row
                      4 5 6   <- back row
===============================================================================
"""

import base64
import json
import math
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np
from flask import Flask, Response, jsonify, request, send_from_directory
from numba import njit

from aic_feed import AicFeed

HERE = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(HERE, "exports")
EXPORT_EVERY_S = 1.0          # simulate mode rewrites the JSON this often
LAST_EXPORT = {"ts": 0.0}     # read by the page so the JSON edge can animate

# =============================================================================
# >>> AI-AGENT EDIT ZONE: API_REGISTRY >>>
# =============================================================================
# Every data source the exhibition can listen to. Add one dict per source.
#
# A "synthetic" source needs no network and is the safe classroom default.
# An "http" source is a real API. Only free, no-key endpoints are pre-filled
# so the template runs on first launch; swap in whatever API the student
# actually chose.
#
# Fields:
#   id      unique slug, referenced by RULES below
#   label   what the website shows
#   kind    "synthetic" | "http"
#   units   free text, shown next to the number
#   range   [lo, hi] expected range, used to scale the sparkline only
#   -- synthetic only --
#   shape   "wave" | "walk" | "spike" | "daily"
#   seed    int, makes the feed reproducible
#   -- http only --
#   url     the endpoint
#   path    dot-path into the JSON response; list indices are numbers,
#           e.g. "features.0.properties.mag"
#   every   seconds between polls (be polite, 60+ for public APIs)
#
# TO ADD A STUDENT'S OWN API: copy the CUSTOM_SLOT entry, give it a new id,
# paste the url, and set path to wherever the number lives in the response.
# =============================================================================

# ---- THIS PROJECT: the Art Institute of Chicago's most popular artwork ---------
# All five feeds come from ONE place. aic_feed.py reads the museum's live ranking
# (AIC public API, boosted works on view, ordered by boost_rank), takes the work
# that currently holds the room, and looks up what it usually makes a viewer
# feel (data/enrichment_store.json, the course's psychophysiology pipeline).
# app.py serves the result at /api/aic_now, so each number polls like any other
# http source and any rule below can read it by id.
#
#   valence     sad / heavy   -> happy / uplifting      drives TEMPERATURE
#   arousal     gentle        -> turbulent              drives AIR SPEED
#   tension     slack         -> taut                   drives HUMIDITY
#   luminance   dark          -> bright (CIE L*)        drives LIGHT LEVEL
#   popularity  museum score                            context only
AIC_NOW = "http://127.0.0.1:5000/api/aic_now"

API_REGISTRY = [
    {
        "id": "aic_valence",
        "label": "AIC top artwork - valence (sad to happy)",
        "kind": "http",
        "url": AIC_NOW,
        "path": "valence",
        "units": "/100",
        "range": [40, 90],
        "every": 3,
    },
    {
        "id": "aic_arousal",
        "label": "AIC top artwork - arousal (gentle to turbulent)",
        "kind": "http",
        "url": AIC_NOW,
        "path": "arousal",
        "units": "/100",
        "range": [0, 60],
        "every": 3,
    },
    {
        "id": "aic_tension",
        "label": "AIC top artwork - tension (slack to taut)",
        "kind": "http",
        "url": AIC_NOW,
        "path": "tension",
        "units": "/100",
        "range": [35, 65],
        "every": 3,
    },
    {
        "id": "aic_luminance",
        "label": "AIC top artwork - luminance (dark to bright)",
        "kind": "http",
        "url": AIC_NOW,
        "path": "luminance",
        "units": "L*",
        "range": [40, 90],
        "every": 3,
    },
    {
        "id": "aic_popularity",
        "label": "AIC top artwork - popularity score",
        "kind": "http",
        "url": AIC_NOW,
        "path": "popularity",
        "units": "/100",
        "range": [75, 100],
        "every": 3,
    },
]

# =============================================================================
# <<< END AI-AGENT EDIT ZONE: API_REGISTRY <<<
# =============================================================================


# =============================================================================
# >>> AI-AGENT EDIT ZONE: PLACEMENTS >>>
# =============================================================================
# Where the movable systems sit when the site is in SIMULATE mode.
#
# The student lays these out by dragging in PLACE mode, presses
# "Copy placement for Claude", and pastes the result at you. When that
# happens, REPLACE THIS WHOLE LIST with what they pasted and change nothing
# else in the file.
#
#   type   "heat_pump" | "humidifier" | "fan"
#   x, y   metres, see coordinate note at the top of the file
#   rot    degrees, direction the unit discharges air
#   power  0..1 capacity scalar
#   label  shown on the diagram
#
# The six can lights are NOT in this list. They are fixed to the ceiling
# grid because they are what divides the room into its six zones - see
# CAN_LIGHTS below. They are still individually selectable and rule-driven.
# =============================================================================

# Three heat pumps, two fans and one humidifier. The heat pumps and fans are
# deliberately several units rather than one: each rule is a single
# IF-THEN with two outcomes, so a staircase of staggered thresholds is how one
# artwork produces a graded room (cold -> cool -> warm -> hot) instead of a switch.
PLACEMENTS = [
    {"id": "heatpump_1",   "type": "heat_pump",  "x": 1.20,  "y": 2.00, "rot": 0,   "power": 1.0, "label": "Heat pump A"},
    {"id": "heatpump_2",   "type": "heat_pump",  "x": 1.20,  "y": 6.00, "rot": 0,   "power": 1.0, "label": "Heat pump B"},
    {"id": "heatpump_3",   "type": "heat_pump",  "x": 10.80, "y": 4.00, "rot": 180, "power": 1.0, "label": "Heat pump C"},
    {"id": "fan_1",        "type": "fan",        "x": 6.00,  "y": 7.30, "rot": 270, "power": 1.0, "label": "Fan A"},
    {"id": "fan_2",        "type": "fan",        "x": 11.00, "y": 7.00, "rot": 210, "power": 1.0, "label": "Fan B"},
    {"id": "humidifier_1", "type": "humidifier", "x": 10.60, "y": 2.20, "rot": 180, "power": 1.0, "label": "Humidifier A"},
]

# =============================================================================
# <<< END AI-AGENT EDIT ZONE: PLACEMENTS <<<
# =============================================================================


# =============================================================================
# >>> AI-AGENT EDIT ZONE: RULES >>>
# =============================================================================
# The IF-THEN layer. This is the actual argument of the project: a number
# from the world decides what the room feels like.
#
# One rule per system. Read it as:
#
#     IF  <api>  <op>  <value>   THEN setpoint = <then>   ELSE setpoint = <otherwise>
#
#   op          ">" | "<" | ">=" | "<=" | "between" | "outside"
#   value       the threshold
#   value2      second threshold, only for "between" / "outside"
#   then        setpoint while the condition holds
#   otherwise   setpoint while it does not
#
# This list is the ONLY place rules exist. The website reports them and shows
# which branch is firing, but cannot edit them - there is no endpoint for that.
# A student who wants a new behaviour has to come here and write it, which is
# the point. If asked for "a rule that dims zone 3 when the tide is out", add a
# dict to this list; do not add a form to the page.
#
# What "setpoint" means depends on the system:
#     heat_pump   target air temperature, degC
#     humidifier  target relative humidity, %      (below current = dehumidify)
#     fan         discharge speed, m/s             (0 = off)
#     can_light   luminous intensity, 0..1
#
# A system with no rule just holds its default setpoint. Deleting a rule is
# always safe.
# =============================================================================

# Calibrated to the real spread of the museum's current top-ranked works
# (valence ~59-84, arousal ~11-47, tension ~40-61, luminance ~55-79) so every
# threshold is actually crossed by some artwork in the rotation.
RULES = [
    # ---- TEMPERATURE <- valence: sadder artwork = colder room ------------------
    # Staircase: each unit flips from cold to warm at a higher valence.
    {"system": "heatpump_1", "api": "aic_valence", "op": ">", "value": 62,
     "value2": None, "then": 24.0, "otherwise": 13.0},
    {"system": "heatpump_2", "api": "aic_valence", "op": ">", "value": 68,
     "value2": None, "then": 27.0, "otherwise": 14.0},
    {"system": "heatpump_3", "api": "aic_valence", "op": ">", "value": 74,
     "value2": None, "then": 31.0, "otherwise": 15.0},

    # ---- AIR SPEED <- arousal: gentler artwork = stiller air -------------------
    # Fan A wakes up for a moderately stirring work, Fan B joins for a turbulent one,
    # and their crossing jets make the room genuinely unsettled.
    {"system": "fan_1", "api": "aic_arousal", "op": ">", "value": 24,
     "value2": None, "then": 2.2, "otherwise": 0.5},
    {"system": "fan_2", "api": "aic_arousal", "op": ">", "value": 38,
     "value2": None, "then": 4.8, "otherwise": 0.3},

    # ---- HUMIDITY <- tension (optional, delete if unwanted) --------------------
    # A taut artwork dries the air; a slack one lets it go soft and damp.
    {"system": "humidifier_1", "api": "aic_tension", "op": ">", "value": 50,
     "value2": None, "then": 25.0, "otherwise": 60.0},

    # ---- LIGHT <- luminance, INVERTED: brighter artwork = dimmer room ----------
    # The room supplies what the picture does not. Each can light is lit
    # (1.0) only while the artwork is darker than its own threshold, ordered
    # from the centre of the room outwards, so a dark artwork floods all six zones
    # and the brightest artwork leaves a single zone glowing.
    {"system": "light_2", "api": "aic_luminance", "op": "<", "value": 80,
     "value2": None, "then": 1.0, "otherwise": 0.06},
    {"system": "light_5", "api": "aic_luminance", "op": "<", "value": 75,
     "value2": None, "then": 1.0, "otherwise": 0.06},
    {"system": "light_1", "api": "aic_luminance", "op": "<", "value": 72,
     "value2": None, "then": 1.0, "otherwise": 0.06},
    {"system": "light_3", "api": "aic_luminance", "op": "<", "value": 68,
     "value2": None, "then": 1.0, "otherwise": 0.06},
    {"system": "light_4", "api": "aic_luminance", "op": "<", "value": 64,
     "value2": None, "then": 1.0, "otherwise": 0.06},
    {"system": "light_6", "api": "aic_luminance", "op": "<", "value": 58,
     "value2": None, "then": 1.0, "otherwise": 0.06},
]

# =============================================================================
# <<< END AI-AGENT EDIT ZONE: RULES <<<
# =============================================================================


# =============================================================================
# >>> AI-AGENT EDIT ZONE: TUNING >>>
# =============================================================================
# Room, physics and display constants. Safe to nudge; changing NX/NY changes
# solver cost roughly linearly with cell count.
# =============================================================================

ROOM_W = 12.0          # m, along +x
ROOM_D = 8.0           # m, along +y
ROOM_H = 4.0           # m, floor to ceiling

NXI, NYI = 96, 64      # interior cells; 96/12 = 8 cells per metre
DX = ROOM_W / NXI      # 0.125 m, square cells

SIM_DT = 0.10          # s of simulated time per solver step
SUBSTEPS = 6           # solver steps per published frame
FRAME_HZ = 20.0        # published frames per second
# -> 0.10 * 6 * 20 = 12 s of room time per real second.

VISCOSITY = 1.5e-3     # m^2/s, eddy viscosity (not molecular - this is a room)
DIFF_TEMP = 2.0e-3     # m^2/s, turbulent thermal diffusivity
DIFF_HUM = 2.0e-3      # m^2/s, turbulent moisture diffusivity
BUOY_K = 0.030         # horizontal Boussinesq coefficient, see solver note
PROJ_ITERS = 40        # pressure Poisson SOR sweeps
SOR_OMEGA = 1.92       # over-relaxation, ~2/(1+sin(pi/NXI)) for this grid
DIFF_ITERS = 6         # diffusion Jacobi sweeps
DRAG = 0.06            # 1/s, bulk drag standing in for out-of-plane losses

OUTDOOR_T = 14.0       # degC, what leaks in through the envelope
OUTDOOR_RH = 55.0      # %
ENVELOPE_K = 0.010     # 1/s, wall coupling strength

AMBIENT_LUX = 6.0      # lux of spill light with every can light off
CANDELA_MAX = 4200.0   # cd per can light at intensity 1.0

# Fixed display ranges, so the gradient legend never jumps around.
FIELD_RANGES = {
    "temperature": [10.0, 32.0],
    "humidity": [10.0, 90.0],
    "wind": [0.0, 2.5],
    "light": [0.0, 400.0],
}

# The six ceiling can lights. Their centres ARE the six zone centres.
CAN_LIGHTS = [
    {"id": "light_1", "zone": 1, "x": ROOM_W * 1 / 6, "y": ROOM_D * 1 / 4},
    {"id": "light_2", "zone": 2, "x": ROOM_W * 3 / 6, "y": ROOM_D * 1 / 4},
    {"id": "light_3", "zone": 3, "x": ROOM_W * 5 / 6, "y": ROOM_D * 1 / 4},
    {"id": "light_4", "zone": 4, "x": ROOM_W * 1 / 6, "y": ROOM_D * 3 / 4},
    {"id": "light_5", "zone": 5, "x": ROOM_W * 3 / 6, "y": ROOM_D * 3 / 4},
    {"id": "light_6", "zone": 6, "x": ROOM_W * 5 / 6, "y": ROOM_D * 3 / 4},
]

DEFAULT_SETPOINT = {
    "heat_pump": 21.0,
    "humidifier": 45.0,
    "fan": 0.0,
    "can_light": 0.45,
}

# Plant capacity. Controllers are proportional and clamp to these.
HEATPUMP_W = 9000.0        # W thermal, either direction
HUMIDIFIER_GS = 0.0016     # kg water / s, either direction
FAN_MAX_MS = 6.0           # m/s discharge

# =============================================================================
# <<< END AI-AGENT EDIT ZONE: TUNING <<<
# =============================================================================


NX = NXI + 2               # padded grid, index 0 and N-1 are boundary cells
NY = NYI + 2

AIR_RHO = 1.2              # kg/m^3
AIR_CP = 1005.0            # J/kg.K
CELL_VOL = DX * DX * ROOM_H
CELL_MASS = AIR_RHO * CELL_VOL
CELL_HEAT_CAP = CELL_MASS * AIR_CP


# =============================================================================
# SOLVER  -  2D incompressible Navier-Stokes, staggered MAC grid, Numba JIT
# =============================================================================
#
# Per step:   forces  ->  advect  ->  diffuse  ->  project
#
#   advect    semi-Lagrangian backtrace, bilinear reconstruction (Stam)
#   diffuse   implicit, Gauss-Seidel relaxation of (I - a*lap)x = x0
#   project   pressure Poisson by SOR, then subtract the gradient
#
# The grid is STAGGERED (Marker-and-Cell):
#
#         v[j+1,i]
#      +-----^-----+
#      |           |
#  u[j,i]>  p,T,H  >u[j,i+1]        p, T, H, L, phi   at cell centres
#      |   [j,i]   |                u                 on vertical faces
#      +-----^-----+                v                 on horizontal faces
#         v[j,i]
#
# That choice is not cosmetic. It is what makes the projection exact - see
# the note on project(). Array shapes, with a one-cell ghost ring:
#     cell-centred   (NYI+2, NXI+2)
#     u              (NYI+2, NXI+3)
#     v              (NYI+3, NXI+2)
#
# Why these kernels are serial njit and not parallel=True: a frame is several
# hundred relaxation sweeps, and at 96x64 one sweep is about 25 us of
# arithmetic. Numba's thread dispatch costs more than that per region, so
# parallel=True measured 94 ms/frame against well under 30 ms serial on this
# machine. Small grids want one fast thread. Past roughly 256x256 that flips.
# =============================================================================


@njit(cache=True, fastmath=True, inline="always")
def sample(f, fi, fj):
    """Bilinear probe of an array at fractional index (fi, fj), clamped."""
    ny, nx = f.shape
    if fi < 0.0:
        fi = 0.0
    if fi > nx - 1.001:
        fi = nx - 1.001
    if fj < 0.0:
        fj = 0.0
    if fj > ny - 1.001:
        fj = ny - 1.001
    i0 = int(fi)
    j0 = int(fj)
    s = fi - i0
    t = fj - j0
    return (
        (1.0 - s) * (1.0 - t) * f[j0, i0]
        + s * (1.0 - t) * f[j0, i0 + 1]
        + (1.0 - s) * t * f[j0 + 1, i0]
        + s * t * f[j0 + 1, i0 + 1]
    )


@njit(cache=True, fastmath=True)
def bnd_u(u, nxi, nyi):
    """Left/right walls are solid; top/bottom are free-slip for u."""
    for j in range(u.shape[0]):
        u[j, 0] = 0.0
        u[j, 1] = 0.0
        u[j, nxi + 1] = 0.0
        u[j, nxi + 2] = 0.0
    for i in range(u.shape[1]):
        u[0, i] = u[1, i]
        u[nyi + 1, i] = u[nyi, i]


@njit(cache=True, fastmath=True)
def bnd_v(v, nxi, nyi):
    """Top/bottom walls are solid; left/right are free-slip for v."""
    for i in range(v.shape[1]):
        v[0, i] = 0.0
        v[1, i] = 0.0
        v[nyi + 1, i] = 0.0
        v[nyi + 2, i] = 0.0
    for j in range(v.shape[0]):
        v[j, 0] = v[j, 1]
        v[j, nxi + 1] = v[j, nxi]


@njit(cache=True, fastmath=True)
def bnd_c(f, nxi, nyi):
    """Zero-gradient ghost ring for any cell-centred field."""
    for i in range(1, nxi + 1):
        f[0, i] = f[1, i]
        f[nyi + 1, i] = f[nyi, i]
    for j in range(0, nyi + 2):
        f[j, 0] = f[j, 1]
        f[j, nxi + 1] = f[j, nxi]


@njit(cache=True, fastmath=True)
def advect_u(dst, u0, v0, dt, dx, nxi, nyi):
    for j in range(1, nyi + 1):
        for i in range(2, nxi + 1):
            x = (i - 1) * dx
            y = (j - 0.5) * dx
            uu = u0[j, i]
            vv = 0.25 * (v0[j, i - 1] + v0[j + 1, i - 1] + v0[j, i] + v0[j + 1, i])
            xb = x - dt * uu
            yb = y - dt * vv
            dst[j, i] = sample(u0, xb / dx + 1.0, yb / dx + 0.5)


@njit(cache=True, fastmath=True)
def advect_v(dst, u0, v0, dt, dx, nxi, nyi):
    for j in range(2, nyi + 1):
        for i in range(1, nxi + 1):
            x = (i - 0.5) * dx
            y = (j - 1) * dx
            vv = v0[j, i]
            uu = 0.25 * (u0[j - 1, i] + u0[j - 1, i + 1] + u0[j, i] + u0[j, i + 1])
            xb = x - dt * uu
            yb = y - dt * vv
            dst[j, i] = sample(v0, xb / dx + 0.5, yb / dx + 1.0)


@njit(cache=True, fastmath=True)
def advect_c(dst, s0, u, v, dt, dx, nxi, nyi):
    for j in range(1, nyi + 1):
        for i in range(1, nxi + 1):
            x = (i - 0.5) * dx
            y = (j - 0.5) * dx
            uu = 0.5 * (u[j, i] + u[j, i + 1])
            vv = 0.5 * (v[j, i] + v[j + 1, i])
            xb = x - dt * uu
            yb = y - dt * vv
            dst[j, i] = sample(s0, xb / dx + 0.5, yb / dx + 0.5)


@njit(cache=True, fastmath=True)
def diffuse_u(x, x0, a, iters, nxi, nyi):
    invc = 1.0 / (1.0 + 4.0 * a)
    for _ in range(iters):
        for j in range(1, nyi + 1):
            for i in range(2, nxi + 1):
                x[j, i] = (
                    x0[j, i]
                    + a * (x[j, i - 1] + x[j, i + 1] + x[j - 1, i] + x[j + 1, i])
                ) * invc
        bnd_u(x, nxi, nyi)


@njit(cache=True, fastmath=True)
def diffuse_v(x, x0, a, iters, nxi, nyi):
    invc = 1.0 / (1.0 + 4.0 * a)
    for _ in range(iters):
        for j in range(2, nyi + 1):
            for i in range(1, nxi + 1):
                x[j, i] = (
                    x0[j, i]
                    + a * (x[j, i - 1] + x[j, i + 1] + x[j - 1, i] + x[j + 1, i])
                ) * invc
        bnd_v(x, nxi, nyi)


@njit(cache=True, fastmath=True)
def diffuse_c(x, x0, a, iters, nxi, nyi):
    invc = 1.0 / (1.0 + 4.0 * a)
    for _ in range(iters):
        for j in range(1, nyi + 1):
            for i in range(1, nxi + 1):
                x[j, i] = (
                    x0[j, i]
                    + a * (x[j, i - 1] + x[j, i + 1] + x[j - 1, i] + x[j + 1, i])
                ) * invc
        bnd_c(x, nxi, nyi)


@njit(cache=True, fastmath=True)
def project(u, v, phi, div, dx, iters, omega, nxi, nyi):
    """
    Exact Hodge projection on the staggered grid.

    On a MAC grid the discrete divergence (a one-cell difference of face
    velocities) composed with the discrete gradient (a one-cell difference of
    cell-centred phi, landing back on faces) IS the standard five-point
    Laplacian. So solving lap(phi) = div(u*) and subtracting grad(phi) drives
    the divergence to the solver's tolerance rather than to a checkerboard
    floor. That identity is the whole reason for staggering; a collocated grid
    with central differences cannot do it, which is the well-known wart in
    Stam's original stable-fluids code.

    The Poisson solve is SOR, not plain Gauss-Seidel: same arithmetic per
    sweep, but GS needs O(N^2) sweeps on this operator and SOR near
    omega = 2/(1+sin(pi/N)) needs O(N). phi is deliberately NOT zeroed between
    calls; last frame's solution is a very good initial guess.

    Compatibility: every wall face is held at zero normal velocity, so the
    integral of div over the domain telescopes to exactly zero and the pure
    Neumann problem is solvable. phi is defined up to a constant, which the
    gradient discards.
    """
    inv_dx = 1.0 / dx
    for j in range(1, nyi + 1):
        for i in range(1, nxi + 1):
            div[j, i] = (
                (u[j, i + 1] - u[j, i]) + (v[j + 1, i] - v[j, i])
            ) * inv_dx

    dx2 = dx * dx
    om4 = omega * 0.25
    one_m = 1.0 - omega
    for _ in range(iters):
        bnd_c(phi, nxi, nyi)
        for j in range(1, nyi + 1):
            for i in range(1, nxi + 1):
                phi[j, i] = one_m * phi[j, i] + om4 * (
                    phi[j, i - 1] + phi[j, i + 1]
                    + phi[j - 1, i] + phi[j + 1, i]
                    - dx2 * div[j, i]
                )
    bnd_c(phi, nxi, nyi)

    for j in range(1, nyi + 1):
        for i in range(2, nxi + 1):
            u[j, i] -= (phi[j, i] - phi[j, i - 1]) * inv_dx
    for j in range(2, nyi + 1):
        for i in range(1, nxi + 1):
            v[j, i] -= (phi[j, i] - phi[j - 1, i]) * inv_dx
    bnd_u(u, nxi, nyi)
    bnd_v(v, nxi, nyi)


@njit(cache=True, fastmath=True)
def max_divergence(u, v, dx, nxi, nyi):
    """Diagnostic only: peak |div u| in 1/s, reported to the website."""
    inv_dx = 1.0 / dx
    m = 0.0
    for j in range(1, nyi + 1):
        for i in range(1, nxi + 1):
            d = abs((u[j, i + 1] - u[j, i]) + (v[j + 1, i] - v[j, i])) * inv_dx
            if d > m:
                m = d
    return m


@njit(cache=True, fastmath=True)
def buoyancy_and_drag(u, v, T, k, drag, dt, dx, nxi, nyi):
    """
    In-plane density force plus bulk drag.

    Honest scope, stated once: the domain is a HORIZONTAL slice through the
    gallery at occupant height, so vertical buoyancy is out of plane. What k
    models is the in-plane part - the horizontal density gradient that drives
    a gravity current, warm air spreading away from a warm source and cold air
    pooling toward a cold one. That is a Boussinesq reduction, not a 3D plume.
    drag stands in for the momentum this slice loses to floor, ceiling and the
    third dimension. Everything else in this solver is solved as written.
    """
    inv_dx = 1.0 / dx
    d = 1.0 - drag * dt
    for j in range(1, nyi + 1):
        for i in range(2, nxi + 1):
            u[j, i] = (u[j, i] - k * (T[j, i] - T[j, i - 1]) * inv_dx * dt) * d
    for j in range(2, nyi + 1):
        for i in range(1, nxi + 1):
            v[j, i] = (v[j, i] - k * (T[j, i] - T[j - 1, i]) * inv_dx * dt) * d
    bnd_u(u, nxi, nyi)
    bnd_v(v, nxi, nyi)


@njit(cache=True, fastmath=True)
def envelope_exchange(T, H, t_out, rh_out, k, dt, nxi, nyi):
    """Infiltration through the envelope. Strongest in the perimeter ring."""
    for j in range(1, nyi + 1):
        for i in range(1, nxi + 1):
            edge = i < 5 or i > nxi - 4 or j < 5 or j > nyi - 4
            w = k * dt * (3.0 if edge else 0.35)
            if w > 0.5:
                w = 0.5
            T[j, i] += (t_out - T[j, i]) * w
            H[j, i] += (rh_out - H[j, i]) * w


@njit(cache=True, fastmath=True)
def inject_momentum(u, v, cx, cy, rot_rad, speed, dx, radius_m, nxi, nyi):
    """Blend the faces around a unit toward its discharge velocity."""
    if speed == 0.0:
        return
    tu = math.cos(rot_rad) * speed
    tv = math.sin(rot_rad) * speed
    r = int(radius_m / dx) + 1
    rr = float(r * r)
    i0 = int(cx / dx) + 1
    j0 = int(cy / dx) + 1
    for dj in range(-r, r + 1):
        for di in range(-r, r + 1):
            d2 = float(di * di + dj * dj)
            if d2 > rr:
                continue
            w = math.exp(-d2 / (0.5 * rr + 1e-9))
            if w > 1.0:
                w = 1.0
            j = j0 + dj
            i = i0 + di
            if 1 <= j <= nyi and 2 <= i <= nxi:
                u[j, i] += (tu - u[j, i]) * w
            if 2 <= j <= nyi and 1 <= i <= nxi:
                v[j, i] += (tv - v[j, i]) * w


@njit(cache=True, fastmath=True)
def inject_scalar(T, H, cx, cy, heat_w, moist_gs, dt, dx,
                  cell_heat_cap, cell_mass, radius_m, nxi, nyi):
    """Distribute thermal and moisture output over a unit's footprint."""
    if heat_w == 0.0 and moist_gs == 0.0:
        return
    r = int(radius_m / dx) + 1
    rr = float(r * r)
    i0 = int(cx / dx) + 1
    j0 = int(cy / dx) + 1
    wsum = 0.0
    for dj in range(-r, r + 1):
        for di in range(-r, r + 1):
            d2 = float(di * di + dj * dj)
            if d2 <= rr:
                wsum += math.exp(-d2 / (0.5 * rr + 1e-9))
    if wsum <= 0.0:
        return
    for dj in range(-r, r + 1):
        j = j0 + dj
        if j < 1 or j > nyi:
            continue
        for di in range(-r, r + 1):
            i = i0 + di
            if i < 1 or i > nxi:
                continue
            d2 = float(di * di + dj * dj)
            if d2 > rr:
                continue
            w = math.exp(-d2 / (0.5 * rr + 1e-9)) / wsum
            if heat_w != 0.0:
                T[j, i] += (heat_w * w * dt) / cell_heat_cap
            if moist_gs != 0.0:
                # ~1 g water per kg dry air is ~6 %RH at room conditions
                H[j, i] += (moist_gs * w * dt / cell_mass) * 1000.0 * 6.0
                if H[j, i] < 2.0:
                    H[j, i] = 2.0
                if H[j, i] > 100.0:
                    H[j, i] = 100.0


@njit(cache=True, fastmath=True)
def compute_light(L, lx, ly, lint, dx, h, ambient, cd_max, nxi, nyi):
    """
    Point-source illuminance on the horizontal plane:
        E = I * cos(theta) / d^2,   cos(theta) = h / d
    Real photometry, not a painted radial gradient. Light is the one field in
    the room the air cannot carry, so it is never advected.
    """
    n = lx.shape[0]
    for j in range(1, nyi + 1):
        for i in range(1, nxi + 1):
            x = (i - 0.5) * dx
            y = (j - 0.5) * dx
            tot = ambient
            for k in range(n):
                ddx = x - lx[k]
                ddy = y - ly[k]
                d2 = ddx * ddx + ddy * ddy + h * h
                d = math.sqrt(d2)
                tot += (lint[k] * cd_max) * h / (d2 * d)
            L[j, i] = tot
    bnd_c(L, nxi, nyi)


@njit(cache=True, fastmath=True)
def speed_field(out, u, v, nxi, nyi):
    """Cell-centred wind speed from the surrounding face velocities."""
    for j in range(1, nyi + 1):
        for i in range(1, nxi + 1):
            uc = 0.5 * (u[j, i] + u[j, i + 1])
            vc = 0.5 * (v[j, i] + v[j + 1, i])
            out[j, i] = math.sqrt(uc * uc + vc * vc)
    bnd_c(out, nxi, nyi)


@njit(cache=True, fastmath=True)
def zone_means(f, nxi, nyi):
    """Mean of the interior over the 3x2 zone grid. Returns zones 1..6."""
    out = np.zeros(6, dtype=np.float64)
    cnt = np.zeros(6, dtype=np.float64)
    for j in range(1, nyi + 1):
        zr = 0 if (j - 1) < nyi // 2 else 1
        for i in range(1, nxi + 1):
            zc = (i - 1) * 3 // nxi
            if zc > 2:
                zc = 2
            z = zr * 3 + zc
            out[z] += f[j, i]
            cnt[z] += 1.0
    for z in range(6):
        if cnt[z] > 0.0:
            out[z] /= cnt[z]
    return out


@njit(cache=True, fastmath=True)
def to_uint8(dst, f, lo, hi, nxi, nyi):
    """Quantise the interior to bytes for transport. Row 0 = front of room."""
    inv = 255.0 / (hi - lo) if hi > lo else 0.0
    for j in range(0, nyi):
        for i in range(0, nxi):
            val = (f[j + 1, i + 1] - lo) * inv
            if val < 0.0:
                val = 0.0
            if val > 255.0:
                val = 255.0
            dst[j * nxi + i] = np.uint8(val)


# =============================================================================
# DATA SOURCES
# =============================================================================


def dig(obj, path):
    """Walk a dot-path into decoded JSON. Numeric parts index into lists."""
    cur = obj
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def synthetic_value(shape, seed, t):
    """Deterministic offline feed. Same t always gives the same number."""
    rng = math.sin(seed * 12.9898) * 43758.5453
    ph = (rng - math.floor(rng)) * math.tau
    if shape == "wave":
        base = 50.0 + 34.0 * math.sin(t / 47.0 + ph)
        jitter = 6.0 * math.sin(t / 6.3 + ph * 2.0)
        return max(0.0, min(100.0, base + jitter))
    if shape == "walk":
        # sum of incommensurate sines: wanders like a walk, but replayable
        v = 50.0
        for k in range(1, 7):
            v += (26.0 / k) * math.sin(t / (9.0 * k) + ph * k)
        return max(0.0, min(100.0, v))
    if shape == "spike":
        q = math.sin(t / 3.1 + ph) * math.sin(t / 7.7 + ph * 3.0)
        q = q * q * q * q
        return max(0.0, min(100.0, 8.0 + 100.0 * q))
    if shape == "daily":
        # 24 h compressed into 240 s so a class can watch a whole day
        return 50.0 - 46.0 * math.cos(t * math.tau / 240.0 + ph)
    return 50.0


class ApiPool:
    """Holds the current value of every registered source."""

    def __init__(self, registry):
        self.registry = registry
        self.lock = threading.Lock()
        self.values = {}
        self.t0 = time.time()
        for a in registry:
            self.values[a["id"]] = {
                "value": None,
                "at": None,
                "status": "waiting",
                "history": deque(maxlen=300),
            }

    def snapshot(self):
        with self.lock:
            return {
                k: {
                    "value": v["value"],
                    "at": v["at"],
                    "status": v["status"],
                    "history": list(v["history"]),
                }
                for k, v in self.values.items()
            }

    def get(self, api_id):
        with self.lock:
            rec = self.values.get(api_id)
            return None if rec is None else rec["value"]

    def _store(self, api_id, value, status):
        with self.lock:
            rec = self.values[api_id]
            rec["value"] = value
            rec["status"] = status
            rec["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if value is not None:
                rec["history"].append(round(float(value), 4))

    def tick_synthetic(self):
        t = time.time() - self.t0
        for a in self.registry:
            if a["kind"] == "synthetic":
                self._store(a["id"], synthetic_value(a["shape"], a["seed"], t), "ok")

    def run_http(self):
        """One background thread polls every http source on its own interval."""
        import requests

        next_due = {a["id"]: 0.0 for a in self.registry if a["kind"] == "http"}
        while True:
            now = time.time()
            for a in self.registry:
                if a["kind"] != "http":
                    continue
                if not a.get("url"):
                    self._store(a["id"], None, "no url set")
                    next_due[a["id"]] = now + 30.0
                    continue
                if now < next_due[a["id"]]:
                    continue
                try:
                    r = requests.get(a["url"], timeout=8)
                    r.raise_for_status()
                    val = float(dig(r.json(), a["path"]))
                    self._store(a["id"], val, "ok")
                except Exception as exc:
                    self._store(a["id"], None, "error: %s" % type(exc).__name__)
                next_due[a["id"]] = now + float(a.get("every", 120))
            time.sleep(1.0)


# =============================================================================
# RULE ENGINE
# =============================================================================


def rule_text(rule):
    """Human-readable form of one rule's condition, used in the exports."""
    if rule["op"] in ("between", "outside"):
        lo, hi = sorted([rule["value"],
                         rule["value2"] if rule["value2"] is not None
                         else rule["value"]])
        return "%s %s %g and %g" % (rule["api"], rule["op"], lo, hi)
    return "%s %s %g" % (rule["api"], rule["op"], rule["value"])


def eval_rule(rule, value):
    """Returns (setpoint, condition_is_true_or_None)."""
    if value is None:
        return rule["otherwise"], None
    op = rule["op"]
    a = rule["value"]
    b = rule.get("value2")
    if op == ">":
        ok = value > a
    elif op == "<":
        ok = value < a
    elif op == ">=":
        ok = value >= a
    elif op == "<=":
        ok = value <= a
    elif op == "between":
        lo, hi = sorted([a, b if b is not None else a])
        ok = lo <= value <= hi
    elif op == "outside":
        lo, hi = sorted([a, b if b is not None else a])
        ok = value < lo or value > hi
    else:
        ok = False
    return (rule["then"] if ok else rule["otherwise"]), ok


# =============================================================================
# SIMULATION
# =============================================================================


class Gallery:
    def __init__(self, apis):
        self.apis = apis
        self.lock = threading.Lock()
        self.mode = "simulate"
        self.speed = 1.0
        self.sim_time = 0.0
        self.frame = 0
        self.step_ms = 0.0

        cc = lambda: np.zeros((NY, NX), dtype=np.float64)        # noqa: E731
        fu = lambda: np.zeros((NY, NX + 1), dtype=np.float64)    # noqa: E731
        fv = lambda: np.zeros((NY + 1, NX), dtype=np.float64)    # noqa: E731
        self.u, self.u0 = fu(), fu()
        self.v, self.v0 = fv(), fv()
        self.T, self.T0 = cc(), cc()
        self.H, self.H0 = cc(), cc()
        self.L, self.spd = cc(), cc()
        self.phi, self.div = cc(), cc()
        self.T[:] = 21.0
        self.H[:] = 45.0
        self.max_div = 0.0

        self.lx = np.array([c["x"] for c in CAN_LIGHTS])
        self.ly = np.array([c["y"] for c in CAN_LIGHTS])
        self.lint = np.full(len(CAN_LIGHTS), DEFAULT_SETPOINT["can_light"])

        self.systems = []
        self.rules = {}
        self.history = {}
        self.last_hist = 0.0
        self.load(PLACEMENTS, RULES)

    # -- configuration ----------------------------------------------------

    def load(self, placements, rules):
        systems = []
        for p in placements:
            systems.append(
                {
                    "id": p["id"],
                    "type": p["type"],
                    "x": float(p["x"]),
                    "y": float(p["y"]),
                    "rot": float(p.get("rot", 0.0)),
                    "power": float(p.get("power", 1.0)),
                    "label": p.get("label", p["id"]),
                    "movable": True,
                    "setpoint": DEFAULT_SETPOINT[p["type"]],
                    "output": 0.0,
                }
            )
        for c in CAN_LIGHTS:
            systems.append(
                {
                    "id": c["id"],
                    "type": "can_light",
                    "x": c["x"],
                    "y": c["y"],
                    "rot": 0.0,
                    "power": 1.0,
                    "label": "Can light %d" % c["zone"],
                    "zone": c["zone"],
                    "movable": False,
                    "setpoint": DEFAULT_SETPOINT["can_light"],
                    "output": DEFAULT_SETPOINT["can_light"],
                }
            )
        self.systems = systems
        self.rules = {r["system"]: dict(r) for r in rules}
        self.history = {
            s["id"]: deque(maxlen=240) for s in systems
        }

    def by_id(self, sid):
        for s in self.systems:
            if s["id"] == sid:
                return s
        return None

    # -- one published frame ----------------------------------------------

    def step(self):
        t_start = time.perf_counter()
        dt = SIM_DT * self.speed

        for s in self.systems:
            rule = self.rules.get(s["id"])
            if rule:
                val = self.apis.get(rule["api"])
                sp, _ = eval_rule(rule, val)
                s["setpoint"] = float(sp)

        for _ in range(SUBSTEPS):
            self._substep(dt)
            self.sim_time += dt

        self.lint[:] = [
            self.by_id(c["id"])["output"] for c in CAN_LIGHTS
        ]
        compute_light(
            self.L, self.lx, self.ly, self.lint, DX, ROOM_H,
            AMBIENT_LUX, CANDELA_MAX, NXI, NYI,
        )
        speed_field(self.spd, self.u, self.v, NXI, NYI)
        self.max_div = max_divergence(self.u, self.v, DX, NXI, NYI)

        now = time.time()
        if now - self.last_hist > 0.35:
            self.last_hist = now
            self._sample_history()

        self.frame += 1
        self.step_ms = (time.perf_counter() - t_start) * 1000.0

    def _substep(self, dt):
        # --- plant: proportional control toward each setpoint -------------
        for sysm in self.systems:
            if sysm["type"] == "can_light":
                sysm["output"] = max(0.0, min(1.0, sysm["setpoint"] * sysm["power"]))
                continue

            i = max(4, min(NXI - 3, int(sysm["x"] / DX) + 1))
            j = max(4, min(NYI - 3, int(sysm["y"] / DX) + 1))
            rot = math.radians(sysm["rot"])

            if sysm["type"] == "heat_pump":
                local = float(self.T[j - 3:j + 4, i - 3:i + 4].mean())
                err = sysm["setpoint"] - local
                q = max(-1.0, min(1.0, err / 2.5)) * HEATPUMP_W * sysm["power"]
                sysm["output"] = q
                # a heat pump is also a fan: it throws its own air
                inject_momentum(self.u, self.v, sysm["x"], sysm["y"], rot,
                                1.6 * sysm["power"], DX, 0.55, NXI, NYI)
                inject_scalar(self.T, self.H, sysm["x"], sysm["y"], q, 0.0,
                              dt, DX, CELL_HEAT_CAP, CELL_MASS, 0.55, NXI, NYI)

            elif sysm["type"] == "humidifier":
                local = float(self.H[j - 3:j + 4, i - 3:i + 4].mean())
                err = sysm["setpoint"] - local
                g = max(-1.0, min(1.0, err / 8.0)) * HUMIDIFIER_GS * sysm["power"]
                sysm["output"] = g
                inject_momentum(self.u, self.v, sysm["x"], sysm["y"], rot,
                                0.9 * sysm["power"], DX, 0.45, NXI, NYI)
                inject_scalar(self.T, self.H, sysm["x"], sysm["y"], 0.0, g,
                              dt, DX, CELL_HEAT_CAP, CELL_MASS, 0.45, NXI, NYI)

            elif sysm["type"] == "fan":
                spd = max(0.0, min(FAN_MAX_MS, sysm["setpoint"])) * sysm["power"]
                sysm["output"] = spd
                inject_momentum(self.u, self.v, sysm["x"], sysm["y"], rot,
                                spd, DX, 0.5, NXI, NYI)

        bnd_u(self.u, NXI, NYI)
        bnd_v(self.v, NXI, NYI)

        # --- momentum: force, advect, diffuse, project --------------------
        buoyancy_and_drag(self.u, self.v, self.T, BUOY_K, DRAG, dt, DX, NXI, NYI)

        self.u0[:] = self.u
        self.v0[:] = self.v
        advect_u(self.u, self.u0, self.v0, dt, DX, NXI, NYI)
        advect_v(self.v, self.u0, self.v0, dt, DX, NXI, NYI)
        bnd_u(self.u, NXI, NYI)
        bnd_v(self.v, NXI, NYI)

        a_v = VISCOSITY * dt / (DX * DX)
        self.u0[:] = self.u
        self.v0[:] = self.v
        diffuse_u(self.u, self.u0, a_v, DIFF_ITERS, NXI, NYI)
        diffuse_v(self.v, self.v0, a_v, DIFF_ITERS, NXI, NYI)

        project(self.u, self.v, self.phi, self.div, DX,
                PROJ_ITERS, SOR_OMEGA, NXI, NYI)

        # --- transported scalars ------------------------------------------
        self.T0[:] = self.T
        advect_c(self.T, self.T0, self.u, self.v, dt, DX, NXI, NYI)
        bnd_c(self.T, NXI, NYI)
        self.T0[:] = self.T
        diffuse_c(self.T, self.T0, DIFF_TEMP * dt / (DX * DX), DIFF_ITERS, NXI, NYI)

        self.H0[:] = self.H
        advect_c(self.H, self.H0, self.u, self.v, dt, DX, NXI, NYI)
        bnd_c(self.H, NXI, NYI)
        self.H0[:] = self.H
        diffuse_c(self.H, self.H0, DIFF_HUM * dt / (DX * DX), DIFF_ITERS, NXI, NYI)

        envelope_exchange(self.T, self.H, OUTDOOR_T, OUTDOOR_RH,
                          ENVELOPE_K, dt, NXI, NYI)
        np.clip(self.H, 2.0, 100.0, out=self.H)
        np.clip(self.T, -20.0, 60.0, out=self.T)
        bnd_c(self.T, NXI, NYI)
        bnd_c(self.H, NXI, NYI)

    def _sample_history(self):
        zt = zone_means(self.T, NXI, NYI)
        zh = zone_means(self.H, NXI, NYI)
        zs = zone_means(self.spd, NXI, NYI)
        zl = zone_means(self.L, NXI, NYI)
        for s in self.systems:
            if s["type"] == "heat_pump":
                val = self._probe(self.T, s)
            elif s["type"] == "humidifier":
                val = self._probe(self.H, s)
            elif s["type"] == "fan":
                val = self._probe(self.spd, s)
            else:
                val = float(zl[s["zone"] - 1])
            self.history[s["id"]].append(round(val, 3))
        self._zone_now = {
            "temperature": [round(float(x), 2) for x in zt],
            "humidity": [round(float(x), 2) for x in zh],
            "wind": [round(float(x), 3) for x in zs],
            "light": [round(float(x), 1) for x in zl],
        }

    def _probe(self, f, s):
        i = max(4, min(NX - 5, int(s["x"] / DX) + 1))
        j = max(4, min(NY - 5, int(s["y"] / DX) + 1))
        return float(f[j - 4:j + 5, i - 4:i + 5].mean())

    # -- what the website asks for ----------------------------------------

    def field_by_name(self, name):
        return {
            "temperature": self.T,
            "humidity": self.H,
            "wind": self.spd,
            "light": self.L,
        }[name]

    def frame_payload(self, field_name):
        with self.lock:
            f = self.field_by_name(field_name)
            lo, hi = FIELD_RANGES[field_name]
            buf = np.empty(NXI * NYI, dtype=np.uint8)
            to_uint8(buf, f, lo, hi, NXI, NYI)
            interior = f[1:-1, 1:-1]
            zones = getattr(self, "_zone_now", None) or {
                k: [0] * 6 for k in FIELD_RANGES
            }
            apis = self.apis.snapshot()

            sysout = []
            for s in self.systems:
                rule = self.rules.get(s["id"])
                cond = None
                if rule:
                    _, cond = eval_rule(rule, apis.get(rule["api"], {}).get("value"))
                sysout.append(
                    {
                        "id": s["id"],
                        "type": s["type"],
                        "label": s["label"],
                        "x": round(s["x"], 3),
                        "y": round(s["y"], 3),
                        "rot": s["rot"],
                        "power": s["power"],
                        "zone": s.get("zone"),
                        "setpoint": round(s["setpoint"], 3),
                        "output": round(s["output"], 5),
                        "reading": round(
                            self.history[s["id"]][-1] if self.history[s["id"]] else 0.0,
                            3,
                        ),
                        "history": list(self.history[s["id"]]),
                        "rule": rule,
                        "rule_true": cond,
                    }
                )

            return {
                "frame": self.frame,
                "sim_time_s": round(self.sim_time, 1),
                "step_ms": round(self.step_ms, 2),
                "max_div": round(self.max_div, 6),
                "mode": self.mode,
                "speed": self.speed,
                "last_export_age_s": (round(time.time() - LAST_EXPORT["ts"], 2)
                                      if LAST_EXPORT["ts"] else None),
                "field": field_name,
                "range": [lo, hi],
                "nx": NXI,
                "ny": NYI,
                "min": round(float(interior.min()), 3),
                "max": round(float(interior.max()), 3),
                "mean": round(float(interior.mean()), 3),
                "data": base64.b64encode(buf.tobytes()).decode("ascii"),
                "zones": zones,
                "systems": sysout,
                "apis": apis,
            }

    # -- exports ------------------------------------------------------------

    def export_payload(self):
        with self.lock:
            apis = self.apis.snapshot()
            zones = getattr(self, "_zone_now", None) or {
                k: [0] * 6 for k in FIELD_RANGES
            }
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            docs = {}
            for s in self.systems:
                rule = self.rules.get(s["id"])
                cond = None
                api_val = None
                if rule:
                    api_val = apis.get(rule["api"], {}).get("value")
                    _, cond = eval_rule(rule, api_val)
                zone = s.get("zone") or self.zone_of(s["x"], s["y"])
                docs[s["id"]] = {
                    "schema": "iarc425.system.v1",
                    "exported_utc": stamp,
                    "frame": self.frame,
                    "sim_time_s": round(self.sim_time, 1),
                    "system_id": s["id"],
                    "system_type": s["type"],
                    "label": s["label"],
                    "position_m": {"x": round(s["x"], 4), "y": round(s["y"], 4),
                                   "z": ROOM_H if s["type"] == "can_light" else 1.2},
                    "rotation_deg": s["rot"],
                    "power_scalar": s["power"],
                    "zone": zone,
                    "setpoint": round(s["setpoint"], 4),
                    "output": round(s["output"], 6),
                    "output_units": {
                        "heat_pump": "W_thermal",
                        "humidifier": "kg_per_s",
                        "fan": "m_per_s",
                        "can_light": "intensity_0_1",
                    }[s["type"]],
                    "reading": round(
                        self.history[s["id"]][-1] if self.history[s["id"]] else 0.0, 4
                    ),
                    "linked_api": None if not rule else {
                        "id": rule["api"],
                        "value": api_val,
                        "condition": rule_text(rule),
                        "condition_met": cond,
                        "then": rule["then"],
                        "otherwise": rule["otherwise"],
                    },
                    "series": list(self.history[s["id"]]),
                }
            room = {
                "schema": "iarc425.room.v1",
                "exported_utc": stamp,
                "room_m": {"w": ROOM_W, "d": ROOM_D, "h": ROOM_H},
                "zone_grid": {"cols": 3, "rows": 2},
                "zone_centres_m": [
                    {"zone": c["zone"], "x": round(c["x"], 4), "y": round(c["y"], 4)}
                    for c in CAN_LIGHTS
                ],
                "zone_values": zones,
                "field_ranges": FIELD_RANGES,
            }
            return docs, room

    def zone_of(self, x, y):
        col = min(2, max(0, int(x / (ROOM_W / 3.0))))
        row = 0 if y < ROOM_D / 2.0 else 1
        return row * 3 + col + 1


# =============================================================================
# FLASK APP
# =============================================================================

app = Flask(__name__, static_folder=None)
apis = ApiPool(API_REGISTRY)
gallery = Gallery(apis)
aic = AicFeed()


@app.after_request
def relax_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.get("/api/config")
def api_config():
    """Everything the page needs once at boot."""
    return jsonify(
        {
            "room": {"w": ROOM_W, "d": ROOM_D, "h": ROOM_H},
            "grid": {"nx": NXI, "ny": NYI, "dx": DX},
            "fields": FIELD_RANGES,
            "apis": [
                {k: a[k] for k in ("id", "label", "kind", "units", "range")}
                for a in API_REGISTRY
            ],
            "can_lights": CAN_LIGHTS,
            "defaults": DEFAULT_SETPOINT,
            "fan_max": FAN_MAX_MS,
            "placements": PLACEMENTS,
            "rules": RULES,
            "sim": {
                "dt": SIM_DT,
                "substeps": SUBSTEPS,
                "hz": FRAME_HZ,
                "room_s_per_real_s": round(SIM_DT * SUBSTEPS * FRAME_HZ, 2),
            },
        }
    )


@app.get("/api/aic_now")
def api_aic_now():
    """The artwork currently holding the room, as numbers the rules can read."""
    return jsonify(aic.now())


@app.get("/api/frame")
def api_frame():
    field = request.args.get("field", "temperature")
    if field not in FIELD_RANGES:
        return jsonify({"error": "unknown field"}), 400
    return jsonify(gallery.frame_payload(field))


@app.post("/api/mode")
def api_mode():
    body = request.get_json(force=True)
    m = body.get("mode")
    if m not in ("place", "simulate"):
        return jsonify({"error": "mode must be place or simulate"}), 400
    with gallery.lock:
        gallery.mode = m
    return jsonify({"mode": m})


@app.post("/api/speed")
def api_speed():
    body = request.get_json(force=True)
    with gallery.lock:
        gallery.speed = max(0.0, min(4.0, float(body.get("speed", 1.0))))
    return jsonify({"speed": gallery.speed})


@app.post("/api/systems")
def api_systems():
    """
    Place mode writes the live layout here. Movable units may be moved,
    rotated and powered; can lights may only be re-powered.
    """
    body = request.get_json(force=True)
    with gallery.lock:
        for item in body.get("systems", []):
            s = gallery.by_id(item.get("id"))
            if s is None:
                continue
            if s["movable"]:
                s["x"] = max(0.15, min(ROOM_W - 0.15, float(item.get("x", s["x"]))))
                s["y"] = max(0.15, min(ROOM_D - 0.15, float(item.get("y", s["y"]))))
                s["rot"] = float(item.get("rot", s["rot"])) % 360.0
            s["power"] = max(0.0, min(1.0, float(item.get("power", s["power"]))))
            if "label" in item:
                s["label"] = str(item["label"])[:48]
    return jsonify({"ok": True})


@app.get("/api/placement")
def api_placement():
    """
    The 'Copy placement for Claude' payload. Returns a ready-to-paste Python
    block that replaces the two AI-AGENT edit zones in this file.
    """
    with gallery.lock:
        movable = [s for s in gallery.systems if s["movable"]]
        lines = [
            "# ==== PASTE OVER THE PLACEMENTS EDIT ZONE IN app.py ====",
            "PLACEMENTS = [",
        ]
        for s in movable:
            lines.append(
                '    {"id": "%s", "type": "%s", "x": %.2f, "y": %.2f, '
                '"rot": %d, "power": %.2f, "label": "%s"},'
                % (s["id"], s["type"], s["x"], s["y"], round(s["rot"]),
                   s["power"], s["label"])
            )
        lines.append("]")
        lines.append("")
        lines.append("# ==== PASTE OVER THE RULES EDIT ZONE IN app.py ====")
        lines.append("RULES = [")
        for r in gallery.rules.values():
            v2 = "None" if r.get("value2") is None else "%.3f" % r["value2"]
            lines.append(
                '    {"system": "%s", "api": "%s", "op": "%s", "value": %s,'
                ' "value2": %s, "then": %s, "otherwise": %s},'
                % (r["system"], r["api"], r["op"], r["value"], v2,
                   r["then"], r["otherwise"])
            )
        lines.append("]")
        lines.append("")
        lines.append("# Can lights are fixed to the ceiling zone grid at:")
        for c in CAN_LIGHTS:
            lines.append(
                "#   %s  zone %d  x=%.2f  y=%.2f  power=%.2f"
                % (c["id"], c["zone"], c["x"], c["y"],
                   gallery.by_id(c["id"])["power"])
            )
        return Response("\n".join(lines), mimetype="text/plain")


@app.get("/api/export")
def api_export_preview():
    docs, room = gallery.export_payload()
    return jsonify({"room": room, "systems": docs})


def _atomic_json(path, payload):
    """Write-then-rename so Grasshopper never reads a half-written file.
    If the reader holds the file open (Windows), skip this tick quietly."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    try:
        os.replace(tmp, path)
    except PermissionError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    return True


def write_exports():
    """One JSON per system on disk, for the Grasshopper file-reader + timer."""
    docs, room = gallery.export_payload()
    cur = aic.now()
    room["exhibition"] = {
        "artwork": cur["artwork"],
        "valence": cur["valence"],
        "arousal": cur["arousal"],
        "tension": cur["tension"],
        "luminance": cur["luminance"],
        "position": cur["position"],
        "queue_length": cur["queue_length"],
        "next_change_in_s": cur["next_change_in_s"],
        "source": cur["source"],
    }
    os.makedirs(EXPORT_DIR, exist_ok=True)
    written = []
    for sid, doc in docs.items():
        if _atomic_json(os.path.join(EXPORT_DIR, "%s.json" % sid), doc):
            written.append("%s.json" % sid)
    for name, payload in (("_room.json", room), ("_all_systems.json", docs)):
        if _atomic_json(os.path.join(EXPORT_DIR, name), payload):
            written.append(name)
    LAST_EXPORT["ts"] = time.time()
    return written


@app.post("/api/export")
def api_export_write():
    written = write_exports()
    return jsonify({"ok": True, "dir": EXPORT_DIR, "files": written})


@app.post("/api/reset")
def api_reset():
    with gallery.lock:
        gallery.u[:] = 0.0
        gallery.v[:] = 0.0
        gallery.phi[:] = 0.0
        gallery.T[:] = 21.0
        gallery.H[:] = 45.0
        gallery.sim_time = 0.0
        for k in gallery.history:
            gallery.history[k].clear()
    return jsonify({"ok": True})


# =============================================================================
# BACKGROUND LOOPS
# =============================================================================


def sim_loop():
    period = 1.0 / FRAME_HZ
    while True:
        t = time.perf_counter()
        apis.tick_synthetic()
        with gallery.lock:
            gallery.step()
        rest = period - (time.perf_counter() - t)
        if rest > 0:
            time.sleep(rest)


def export_loop():
    """Keep exports/ in step with the room so the Rhino twin matches the page."""
    while True:
        time.sleep(EXPORT_EVERY_S)
        if gallery.mode == "simulate":
            try:
                write_exports()
            except Exception as exc:  # never let a bad write kill the loop
                print("export skipped: %s" % exc, flush=True)


def warm_up():
    """Force Numba to compile before the first request, not during it."""
    t = time.perf_counter()
    with gallery.lock:
        gallery.step()
    return (time.perf_counter() - t) * 1000.0


def main():
    print("=" * 70)
    print(" IARC 425  Exhibition Template  -  Python backend")
    print("=" * 70)
    print(" grid          %d x %d cells at %.3f m" % (NXI, NYI, DX))
    print(" room          %.1f x %.1f x %.1f m, 6 zones" % (ROOM_W, ROOM_D, ROOM_H))
    print(" room time     %.1f s per real second"
          % (SIM_DT * SUBSTEPS * FRAME_HZ))
    print(" compiling Numba kernels ...", flush=True)
    ms = warm_up()
    print(" first step    %.0f ms (compile). steady state next." % ms)
    ms2 = warm_up()
    print(" steady step   %.2f ms per frame" % ms2)

    threading.Thread(target=sim_loop, daemon=True).start()
    threading.Thread(target=apis.run_http, daemon=True).start()
    threading.Thread(target=aic.run, daemon=True).start()
    threading.Thread(target=export_loop, daemon=True).start()

    print("")
    print("  open  ->  http://127.0.0.1:5000")
    print("")
    app.run(host="127.0.0.1", port=5000, threaded=True,
            debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
