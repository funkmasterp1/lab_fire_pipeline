"""DuckDB query over the GeoParquet: hotspots by area (bbox) and time window - the 'clip by area and time' pattern."""
import duckdb, sys
f = sys.argv[1] if len(sys.argv) > 1 else "out/mukluk_viirs_hotspots.parquet"   # or s3://BUCKET/parquet/...
con = duckdb.connect()
if f.startswith("s3://"):
    con.sql("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
print(con.sql(f"""
  SELECT CAST(acq_datetime AS DATE) AS day, count(*) AS detections, round(sum(frp),1) AS total_frp_MW, round(max(frp),1) AS max_frp
  FROM read_parquet('{f}')
  WHERE longitude BETWEEN -143.15 AND -142.9 AND latitude BETWEEN 63.3 AND 63.42
    AND acq_datetime BETWEEN '2026-08-17' AND '2026-08-25'
  GROUP BY 1 ORDER BY 1"""))
