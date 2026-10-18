#!/usr/bin/env python3
"""Combine independently extracted Geofabrik named beach/harbor shoreline geometries."""
import csv,json,glob,os,collections
source=sorted(glob.glob("regional-download/*/osm_named_features.geojson"))
features={}
counts=collections.Counter()
for file in source:
  obj=json.load(open(file,encoding="utf8"))
  for f in obj.get("features",[]):
    p=f["properties"];oid=p.get("osm_id")
    if oid:features[oid]=f
  counts[os.path.basename(os.path.dirname(file))]=len(obj.get("features",[]))
os.makedirs("output/geo",exist_ok=True)
vals=list(features.values())
with open("output/geo/national_osm_named_features.geojson","w",encoding="utf8") as f:json.dump({"type":"FeatureCollection","features":vals},f,ensure_ascii=False)
columns=["name","prefecture","municipality","feature_type","osm_id","latitude","longitude","west","south","east","north","geometry_type","url"]
with open("output/geo/national_osm_named_features.csv","w",encoding="utf-8-sig",newline="") as f:
 w=csv.DictWriter(f,columns);w.writeheader()
 for v in vals:w.writerow({k:v["properties"].get(k,"") for k in columns})
report={"regional_artifacts":len(source),"raw_features":sum(counts.values()),"unique_named_geometries":len(vals),
"by_type":dict(collections.Counter(v["properties"].get("feature_type") for v in vals)),
"by_prefecture":dict(collections.Counter(v["properties"].get("prefecture") for v in vals)),
"regional_counts":dict(counts),"meaning":"Named mapped coast or port geography; not legal fishing area."}
with open("output/geo/national_osm_report.json","w",encoding="utf8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
print("NATIONAL OSM SUMMARY",json.dumps(report,ensure_ascii=False)[:8000],flush=True)
