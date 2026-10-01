# Mood Weather

IARC 425 · *Reading Big Data at the Human Scale* · University of Tennessee, Knoxville

An exhibition where the artwork **is** the interior environment. The most popular
artwork at the Art Institute of Chicago is turned into numbers, and those numbers
move the temperature, air speed, humidity and light of a 12 × 8 × 4 m gallery.
Built on the course's exhibition template.

## Run it

```
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>. First launch spends a few seconds compiling Numba.
Needs internet only for the live Art Institute ranking and the artwork thumbnail;
offline it falls back to `data/artworks_data.json` and says so on the page.

## How the data reaches the room

```
Art Institute API -> aic_feed.py -> app.py (IF-THEN rules) -> systems -> air simulation
                                                   |-> Website (index.html)
                                                   '-> exports/*.json (1 s) -> Grasshopper -> Rhino
```

| Step | What happens |
|---|---|
| Which artwork | `aic_feed.py` asks the AIC API for boosted works on view, ordered by `boost_rank`. The top 12 take turns, **60 s each** (`ROTATE_SECONDS`). |
| What it feels like | Looked up in `data/enrichment_store.json` (course psychophysiology pipeline): `valence_score`, `arousal_score`, `tension_score`, and mean luminance. |
| Rules | `app.py` → `RULES`. Five numbers polled from `/api/aic_now` every 3 s. |
| Room | Real 2D Navier–Stokes solve; heat pumps, fans, humidifier and six can lights receive setpoints. |
| Hand-off | `exports/` is rewritten every second in Simulate mode (write-then-rename, never half a file). |

## The mapping

| Artwork measure | Drives | Rule |
|---|---|---|
| Valence (sad ↔ happy) | 3 heat pumps | Flip from cold (13/14/15 °C) to warm (24/27/31 °C) as valence clears 62 / 68 / 74 |
| Arousal (gentle ↔ turbulent) | 2 fans | Fan A 0.5 → 2.2 m/s above 24; Fan B 0.3 → 4.8 m/s above 38 |
| Tension (slack ↔ taut) | humidifier | 60 %RH → 25 %RH above 50 *(optional, delete if unwanted)* |
| Luminance (dark ↔ bright) | 6 can lights | **Inverted.** Each full while luminance is below 80 / 75 / 72 / 68 / 64 / 58 |

Why a staircase of several units: a rule has two outcomes. Staggered thresholds are
how one artwork produces a *graded* room instead of a switch. Thresholds were set
against the real spread of the current top works (valence ≈ 59–84, arousal ≈ 11–47,
tension ≈ 40–61, luminance ≈ 55–79). Your collection skews positive, so "sad" here
means *sadder than its neighbours*, below about 62.

## Honest limits

- The Art Institute publishes **no live visitor counts**. "Most popular right now"
  is its current top-ranked works on a rotation. Set `TOP_N = 1` in `aic_feed.py`
  to hold the single top-ranked work instead.
- Valence, arousal and tension are **estimates from image features**, not
  measurements of any visitor.
- Only works already measured in `enrichment_store.json` can take a turn.

## Deliverables → files

| Brief | File |
|---|---|
| Workflow diagram | `workflow_diagram.svg` / `.png` (regenerate: `python make_workflow_diagram.py`) |
| Website: axon, workflow, inputs/outputs, statement | `index.html`, served by `app.py` |
| Grasshopper digital twin | `grasshopper/` (scripts + wiring guide) |

## Changes outside the template's edit zones

Small and deliberate; everything else is in the marked zones.

- `aic_feed.py` (new) and the `/api/aic_now` route.
- `write_exports()` + background `export_loop` so Grasshopper's timer sees live
  data without pressing the export button.
- `index.html`: "Now showing" panel, mechanism table, workflow explanations.
