# Grasshopper digital twin - wiring guide

Goal: the room in Rhino matches the website, driven by the JSON files in `exports/`
that `app.py` rewrites once a second while it is in **Simulate** mode.

> **Tested / not tested.** The script's logic (parsing, colour ramps, label text,
> geometry calls) was run against a stand-in for RhinoCommon using the real exported
> JSON. It has **not** been run inside Rhino or Grasshopper, so do the two-minute
> check at the end of this page the first time.

## 1. Start the Python side

```
python app.py
```

Leave it in **Simulate** mode. Open `exports/` and confirm the files' "modified"
time changes every second (`_room.json`, `_all_systems.json`, `heatpump_1.json` ...).

## 2. Build the canvas (five components)

| # | Component | Setting |
|---|---|---|
| 1 | **Panel** (text) | Paste the full path of your `exports` folder, e.g. `C:/Users/you/mood-weather/exports` |
| 2 | **Timer** | Interval `1000` ms, Active = true |
| 3 | **Python script** (relay) | Paste `path_relay.py`. Add inputs `folder` (Item, str) and `tick` (Item). Outputs `all_path`, `room_path`. Wire Panel to `folder`, Timer to `tick` |
| 4 | **Read File** x 2 | First gets `all_path`, second gets `room_path` |
| 5 | **Python script** (twin) | Paste `mood_weather_twin.py` (Rhino 8: *Python 3 Script*, Rhino 7: *GhPython Script*). Inputs below. |

Why the relay: a Timer only re-runs components downstream of it. Without the relay
Read File would keep showing the first version of the file.

Inputs of the twin script (all Item access):

| Input | Wire it to |
|---|---|
| `all_json` | Read File #1 |
| `room_json` | Read File #2 |
| `tick` | Timer |
| `field` | a Number Slider, 0-3 (0 temperature, 1 humidity, 2 wind, 3 light) |
| `unit_scale` | a Number: `1` if your Rhino file is in metres, `1000` for mm, `3.28084` for feet |
| `folder` | *(optional)* the Panel from step 1. If Read File gives you trouble, leave `all_json` / `room_json` unwired and the script reads the files itself every tick |

Outputs to preview:

| Output | What you see |
|---|---|
| `field_mesh` | six zone tiles at the 1.2 m simulation slice, coloured like the website's gradient |
| `room_edges` | the 12 x 8 x 4 m room |
| `unit_boxes` | heat pumps, fans, humidifier, turned to face their discharge |
| `flow_lines` | discharge direction; fan lines grow with m/s |
| `light_spheres` | can lights; radius follows intensity |
| `labels` + `label_pts` | feed both into a **Text Tag 3D** (or Bifocals) to hang labels on the units |
| `artwork` | connect to a Panel: the artwork currently driving the room |
| `log` | connect to a Panel: problems appear here first |

Mesh colours show in the Grasshopper preview directly; no Custom Preview needed.

## 3. The two-minute check

1. `log` reads `showing temperature | 12 systems | ok`.
2. `artwork` shows the same title as the website's "Now showing" panel.
3. When the website's artwork changes (every 60 s), tile colours, light spheres and
   flow-line lengths change in Rhino within about a second.
4. Flip `field` to 3: tiles should match the website's **Light** view.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `waiting for JSON` | Read File has no path, or the path is wrong. Wire the `folder` input instead |
| `exports are NN s old` | `app.py` stopped, or it is in **Place** mode (exports only write in Simulate) |
| `JSON not readable this tick` | The file was replaced while being read. Harmless; next tick succeeds |
| Everything tiny or huge | Set `unit_scale` for your Rhino document units |
| Nothing updates | Timer is not Active, or not wired into the relay's `tick` |
