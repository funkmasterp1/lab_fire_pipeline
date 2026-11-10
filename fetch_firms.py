"""Download VIIRS active-fire detections from NASA FIRMS for the Mukluk Fire area.
Get a free MAP_KEY at https://firms.modaps.eosdis.nasa.gov/api/map_key/ and export it:
    export FIRMS_MAP_KEY=your_key
"""
import os, io, datetime as dt, urllib.request as u, pandas as pd
KEY = os.environ["FIRMS_MAP_KEY"]
BBOX = "-143.9,63.0,-142.3,63.7"
frames = []
for src in ["VIIRS_SNPP_SP", "VIIRS_NOAA20_SP", "VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT"]:
    d = dt.date(2026, 8, 10)
    while d < dt.date(2026, 9, 20):
        t = u.urlopen(f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{KEY}/{src}/{BBOX}/5/{d}", timeout=60).read().decode()
        if t.startswith("latitude"):
            df = pd.read_csv(io.StringIO(t)); df["source"] = src; frames.append(df)
        d += dt.timedelta(days=5)
pd.concat(frames).drop_duplicates(["latitude", "longitude", "acq_date", "acq_time", "satellite"]).to_csv("firms_raw.csv", index=False)
