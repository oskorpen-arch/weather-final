"""
===============================================================================
 AIC FEED  -  "what is the most popular artwork at the Art Institute of Chicago
 right now, and what does it usually make a person feel?"
===============================================================================
 This module is the first box of the workflow:

     Art Institute of Chicago API  ->  aic_feed.py  ->  app.py (rules)  ->  room

 It turns one artwork into five plain numbers that the IF-THEN rules in app.py
 can read, exactly like any other API in API_REGISTRY:

     valence     0-100   sad / heavy  ->  happy / uplifting   (-> temperature)
     arousal     0-100   gentle       ->  turbulent           (-> air speed)
     tension     0-100   slack        ->  taut                (-> humidity)
     luminance   0-100   dark         ->  bright (CIE L*)     (-> light level)
     popularity  0-100   museum popularity score

 WHERE THE NUMBERS COME FROM
   * WHICH artwork:  the museum's own ranking, read live from the AIC public
     API (boosted works that are on view, ordered by boost_rank). If the API is
     unreachable the same ranking is rebuilt from data/artworks_data.json.
   * WHAT it feels like:  data/enrichment_store.json, the per-artwork
     "psychophysiology" block produced by the course's visual_enrichment.py
     pipeline (valence_score, arousal_score, tension_score) plus the measured
     mean luminance. These are research-based estimates from image features,
     not measurements of any visitor.

 HONEST LIMIT: the Art Institute publishes no per-minute visitor counts, so
 "most popular right now" cannot be observed directly. The exhibition instead
 cycles through the museum's current top-N ranking on a clock (ROTATE_SECONDS
 per artwork). Set TOP_N = 1 to hold the single top-ranked work instead.
===============================================================================
"""

import json
import os
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")

AIC_SEARCH_URL = "https://api.artic.edu/api/v1/artworks/search"
IIIF = "https://www.artic.edu/iiif/2"

TOP_N = 12              # how many of the museum's top-ranked works take a turn
ROTATE_SECONDS = 60     # how long one artwork holds the room (class demo pace)
REFRESH_SECONDS = 1800  # how often to re-read the live ranking from AIC


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


class AicFeed:
    def __init__(self, data_dir=DATA_DIR):
        self.lock = threading.Lock()
        self.enrich = {}
        self.local = []
        self.queue = []
        self.source = "starting"
        self.refreshed = None
        self.note = ""
        self._load_local(data_dir)
        # usable immediately, before any network call has finished
        self._set_queue(self._local_ranking(), "local-cache",
                        "Using data/artworks_data.json until the AIC API answers.")

    # -- local data ---------------------------------------------------------

    def _load_local(self, data_dir):
        with open(os.path.join(data_dir, "enrichment_store.json"), encoding="utf-8") as fh:
            self.enrich = json.load(fh)
        with open(os.path.join(data_dir, "artworks_data.json"), encoding="utf-8") as fh:
            self.local = json.load(fh)["artworks"]

    def _local_ranking(self):
        """The same ranking the AIC query asks for, rebuilt offline."""
        pool = [a for a in self.local
                if a.get("is_on_view") and str(a["id"]) in self.enrich]
        boosted = [a for a in pool if a.get("is_boosted") and a.get("boost_rank")]
        boosted.sort(key=lambda a: a["boost_rank"])
        rest = [a for a in pool if a not in boosted]
        rest.sort(key=lambda a: -_num(a.get("popularity_score")))
        return [a["id"] for a in (boosted + rest)]

    # -- live ranking -------------------------------------------------------

    def refresh_live(self):
        """Ask the AIC API for its current highlighted works. Never raises."""
        import requests

        body = {
            "query": {"bool": {"must": [
                {"term": {"is_on_view": True}},
                {"term": {"is_boosted": True}},
                {"exists": {"field": "image_id"}},
            ]}},
            "sort": [{"boost_rank": {"order": "asc"}}],
            "size": 60,
            "fields": ["id", "title", "boost_rank", "is_on_view"],
        }
        try:
            r = requests.post(
                AIC_SEARCH_URL, json=body, timeout=12,
                headers={"User-Agent": "IARC425-Exhibition/1.0 (student project)"},
            )
            r.raise_for_status()
            ids = [int(d["id"]) for d in r.json().get("data", [])]
        except Exception as exc:  # network down, rate limit, bad JSON ...
            with self.lock:
                self.note = "AIC API unreachable (%s); using local ranking." % type(exc).__name__
            return False

        # keep only works the feature pipeline has already measured
        ids = [i for i in ids if str(i) in self.enrich]
        for i in self._local_ranking():       # top up if the live list is short
            if len(ids) >= TOP_N:
                break
            if i not in ids:
                ids.append(i)
        self._set_queue(ids, "aic-live", "Ranking read live from the AIC API.")
        return True

    def run(self):
        """Background thread body: refresh the live ranking forever."""
        while True:
            self.refresh_live()
            time.sleep(REFRESH_SECONDS)

    def _set_queue(self, ids, source, note):
        by_id = {a["id"]: a for a in self.local}
        queue = []
        for aid in ids[:TOP_N]:
            e = self.enrich.get(str(aid))
            a = by_id.get(aid, {})
            if not e:
                continue
            emo = e["psychophysiology"]["emotional"]
            queue.append({
                "id": aid,
                "title": a.get("title", "Untitled"),
                "artist": a.get("artist_title", "Unknown"),
                "date": a.get("date_display", ""),
                "image_id": e.get("image_id") or a.get("image_id"),
                "boost_rank": a.get("boost_rank"),
                "popularity": _num(a.get("popularity_score")),
                "valence": _num(emo.get("valence_score")),
                "arousal": _num(emo.get("arousal_score")),
                "tension": _num(emo.get("tension_score")),
                "luminance": _num(e["color"].get("mean_luminance")),
                "mood": emo.get("mood_atmosphere", ""),
                "valence_label": emo.get("valence_label", ""),
                "arousal_label": emo.get("arousal_label", ""),
            })
        with self.lock:
            if queue:
                self.queue = queue
                self.source = source
                self.note = note
                self.refreshed = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # -- what the rest of the app reads ---------------------------------------

    def now(self, t=None):
        t = time.time() if t is None else t
        with self.lock:
            q = list(self.queue)
            source, note, refreshed = self.source, self.note, self.refreshed
        slot = int(t // ROTATE_SECONDS)
        idx = slot % len(q)
        cur = q[idx]
        into = t - slot * ROTATE_SECONDS
        return {
            "artwork": {
                "id": cur["id"],
                "title": cur["title"],
                "artist": cur["artist"],
                "date": cur["date"],
                "image_url": "%s/%s/full/400,/0/default.jpg" % (IIIF, cur["image_id"]),
                "boost_rank": cur["boost_rank"],
                "mood": cur["mood"],
                "valence_label": cur["valence_label"],
                "arousal_label": cur["arousal_label"],
            },
            "valence": cur["valence"],
            "arousal": cur["arousal"],
            "tension": cur["tension"],
            "luminance": cur["luminance"],
            "popularity": cur["popularity"],
            "position": idx + 1,
            "queue_length": len(q),
            "rotate_seconds": ROTATE_SECONDS,
            "next_change_in_s": round(ROTATE_SECONDS - into, 1),
            "source": source,
            "note": note,
            "refreshed_utc": refreshed,
            "queue": [
                {"position": i + 1, "id": x["id"], "title": x["title"],
                 "artist": x["artist"], "valence": x["valence"],
                 "arousal": x["arousal"], "tension": x["tension"],
                 "luminance": x["luminance"], "current": i == idx}
                for i, x in enumerate(q)
            ],
        }
