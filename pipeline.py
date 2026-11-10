"""Mukluk Fire (Tok, Alaska, 2026) burn-severity pipeline.
Sentinel-2 L2A (Earth Search STAC) -> dNBR -> burn-scar mask validated by VIIRS hotspots
-> Cloud Optimized GeoTIFF + GeoParquet fire points + STAC items.
Run: python pipeline.py  (needs rasterio rio-cogeo pystac geopandas pyarrow scipy pyproj)
"""
import json, os, urllib.request as u, datetime as dt
import numpy as np, pandas as pd, rasterio, geopandas as gpd
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles
from pyproj import Transformer
from scipy import ndimage as ndi
import pystac

BUCKET = os.environ.get("BUCKET", "peter-fire-lab121267")
AOI = (-143.35, 63.25, -142.70, 63.47)
PRE, POST = "S2C_7VCL_20260711_0_L2A", "S2B_7VCL_20260924_0_L2A"
OUT = "out"; CACHE = "cache"; os.makedirs(OUT, exist_ok=True); os.makedirs(CACHE, exist_ok=True)
SEARCH = "https://earth-search.aws.element84.com/v1/search"

def post(url, body):
    r = u.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(u.urlopen(r, timeout=60))

def fetch(url, path):
    if not os.path.exists(path):
        open(path, "wb").write(u.urlopen(url, timeout=600).read())
    return path

def load(scene):
    it = post(SEARCH, {"collections": ["sentinel-2-l2a"], "ids": [scene]})["features"][0]
    d = {}
    for k in ("nir08", "swir22", "scl"):
        with rasterio.open(fetch(it["assets"][k]["href"], f"{CACHE}/{scene}_{k}.tif")) as s:
            b = transform_bounds("EPSG:4326", s.crs, *AOI)
            w = from_bounds(*b, s.transform).round_offsets().round_lengths()
            d[k] = s.read(1, window=w).astype("float32")
            if k == "nir08": tr, crs = s.window_transform(w), s.crs
    return it, d, tr, crs

(it_pre, a, tr, crs), (it_post, b, _, _) = load(PRE), load(POST)
h = min(a["nir08"].shape[0], b["nir08"].shape[0]); w = min(a["nir08"].shape[1], b["nir08"].shape[1])
nbr = lambda d: ((d["nir08"][:h,:w]-d["swir22"][:h,:w]) / (d["nir08"][:h,:w]+d["swir22"][:h,:w]+1e-9))
bad = lambda d: np.isin(d["scl"][:h,:w], [0,3,8,9,10,11])      # nodata, shadow, cloud, cirrus
dnbr = nbr(a) - nbr(b); dnbr[bad(a) | bad(b)] = np.nan

# --- VIIRS hotspots -> GeoParquet (+ validation of scar mask)
df = pd.read_csv("firms_raw.csv")
df["acq_datetime"] = pd.to_datetime(df.acq_date + " " + df.acq_time.astype(str).str.zfill(4), format="%Y-%m-%d %H%M", utc=True)
gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.longitude, df.latitude), crs=4326)
gdf.to_parquet(f"{OUT}/mukluk_viirs_hotspots.parquet")

x, y = Transformer.from_crs(4326, crs, always_xy=True).transform(df.longitude.values, df.latitude.values)
inv = ~tr
cols, rows = inv * (np.array(x), np.array(y)); rows = rows.astype(int); cols = cols.astype(int)
ok = (rows>=0)&(rows<h)&(cols>=0)&(cols<w)

m = ndi.binary_opening(np.nan_to_num(dnbr, nan=-9) >= 0.27, iterations=2)
lab, _ = ndi.label(m)
keep_ids = {lab[r,c] for r,c in zip(rows[ok], cols[ok]) if lab[r,c] > 0}
scar = np.isin(lab, list(keep_ids))
acres = scar.sum() * (tr.a*abs(tr.e)) / 4046.856
print(f"Burn scar from dNBR>=0.27 connected to VIIRS hotspots: {acres:,.0f} acres (official 7,335)")

sev = np.zeros(dnbr.shape, "uint8")
for i, t in enumerate([0.10,0.27,0.44,0.66], 1): sev[dnbr >= t] = i
sev[~scar] = 255                      # only keep the validated scar
prof = dict(driver="GTiff", height=h, width=w, count=1, crs=crs, transform=tr)
with rasterio.open("sev_raw.tif","w",dtype="uint8",nodata=255,**prof) as d: d.write(sev,1)
dn = np.where(scar, dnbr, np.nan).astype("float32")
with rasterio.open("dnbr_raw.tif","w",dtype="float32",nodata=np.nan,**prof) as d: d.write(dn,1)
cog_translate("dnbr_raw.tif", f"{OUT}/mukluk_dnbr_cog.tif", cog_profiles.get("deflate"), nodata=np.nan, overview_resampling="average", quiet=True)
cog_translate("sev_raw.tif", f"{OUT}/mukluk_burn_severity_cog.tif", cog_profiles.get("deflate"), nodata=255, overview_resampling="nearest", quiet=True)
for i, n in enumerate(["unburned/low-signal","low","moderate-low","moderate-high","high"]):
    print(f"  class {i} {n}: {((sev==i)).sum()*(tr.a*abs(tr.e))/4046.856:,.0f} acres")

# --- STAC
with rasterio.open(f"{OUT}/mukluk_burn_severity_cog.tif") as s:
    bb = transform_bounds(s.crs, "EPSG:4326", *s.bounds)
poly = {"type":"Polygon","coordinates":[[[bb[0],bb[1]],[bb[2],bb[1]],[bb[2],bb[3]],[bb[0],bb[3]],[bb[0],bb[1]]]]}
cat = pystac.Catalog("mukluk-fire-lab", "Mukluk Fire (Tok, AK, 2026) derived products", href=f"s3://{BUCKET}/stac/catalog.json")
for name, title, desc, extra in [
    ("mukluk_burn_severity_cog", "Burn severity classes (dNBR)", "0=low signal 1=low 2=mod-low 3=mod-high 4=high; 255=outside validated scar", {}),
    ("mukluk_dnbr_cog", "dNBR (continuous)", "NBR(pre) - NBR(post), NBR=(B8A-B12)/(B8A+B12)", {})]:
    item = pystac.Item(name, poly, list(bb), dt.datetime(2026,9,24,tzinfo=dt.timezone.utc),
        {"title": title, "description": desc, "processing:level": "L3", "processing:software": {"pipeline.py": "1.0"},
         "mukluk:pre_scene": PRE, "mukluk:post_scene": POST, "mukluk:scar_acres_estimate": round(float(acres))})
    item.add_asset("data", pystac.Asset(f"s3://{BUCKET}/cogs/{name}.tif", media_type=pystac.MediaType.COG, roles=["data"], title=title))
    item.add_link(pystac.Link("derived_from", f"https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/{PRE}"))
    item.add_link(pystac.Link("derived_from", f"https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/{POST}"))
    cat.add_item(item)
hot = pystac.Item("mukluk_viirs_hotspots", poly, list(bb), dt.datetime(2026,8,19,tzinfo=dt.timezone.utc),
    {"title":"VIIRS active fire detections (NASA FIRMS)", "start_datetime":gdf.acq_datetime.min().isoformat(), "end_datetime":gdf.acq_datetime.max().isoformat(), "processing:level":"L2"})
hot.add_asset("data", pystac.Asset(f"s3://{BUCKET}/parquet/mukluk_viirs_hotspots.parquet", media_type="application/vnd.apache.parquet", roles=["data"]))
cat.add_item(hot)
cat.normalize_hrefs(f"{OUT}/stac"); cat.save(pystac.CatalogType.SELF_CONTAINED)
print("STAC items:", [i.id for i in cat.get_items()])
