import csv, json, os
import augment_surf as a

fast="output/national_sea_fishing_sites_surf_expanded.csv"
if not os.path.exists(fast):
    raise SystemExit("Fast surf fishing dataset has not completed.")
with open(fast,encoding="utf-8-sig",newline="") as fp:
    original=list(csv.DictReader(fp))
a.OUT="output/national_sea_fishing_sites_surf_and_osm.csv"
a.SURF="output/surf_beach_sites_and_osm.csv"
a.REPORT="output/surf_osm_research_report.json"
a.STATS["before_osm_total"]=len(original)
a.STATS["before_osm_surf"]=sum(x.get("サーフ重点")=="はい" for x in original)
elements=a.osm_beaches()
coasts=a.municipality_lookup(elements)
a.merge(original,coasts)
