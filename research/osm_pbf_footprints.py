#!/usr/bin/env python3
"""Directly process Japanese OSM PBF extracts to obtain true mapped beach outlines."""
import os,sys,requests,math,csv,json,collections,time
import osmium
from shapely.geometry import Point,LineString,Polygon,mapping
sys.path.insert(0,"research")
from georeference_dedupe import municipal_shapes
REGIONS=os.environ.get("REGIONS","shikoku").split(",")
BASE="https://download.geofabrik.de/asia/japan/"
OUT="output/geo"
os.makedirs(OUT,exist_ok=True)
class Beaches(osmium.SimpleHandler):
    def __init__(self):
        super().__init__();self.features=[];self.count=collections.Counter()
    def feature(self,name,g,kind,tt,oid):
        if g is None or g.is_empty:return
        if not name or len(name)>80:return
        self.features.append({"name":name,"geom":g,"kind":kind,"type":tt,"id":oid})
        self.count[kind]+=1
    def node(self,n):
        tags=n.tags
        kind="beach" if tags.get("natural")=="beach" else ("port" if tags.get("harbour")=="yes" else None)
        if not kind:return
        name=tags.get("name:ja") or tags.get("name")
        if name and n.location.valid():
            self.feature(name,Point(n.location.lon,n.location.lat),kind,"node",n.id)
    def way(self,w):
        tags=w.tags
        kind=("beach" if tags.get("natural")=="beach" or tags.get("landuse")=="beach" else
            "pier" if tags.get("man_made") in ("pier","breakwater") else
            "port" if tags.get("harbour")=="yes" or tags.get("seamark:type")=="harbour" else None)
        if not kind:return
        name=tags.get("name:ja") or tags.get("name")
        if not name:return
        try:
            xy=[(n.location.lon,n.location.lat) for n in w.nodes if n.location.valid()]
            if len(xy)<2:return
            if len(xy)>=4 and xy[0]==xy[-1]:
                geo=Polygon(xy)
                if not geo.is_valid:geo=geo.buffer(0)
            else:geo=LineString(xy)
            self.feature(name,geo,kind,"way",w.id)
        except Exception as e:
            self.count["geometry_parse_error"]+=1

def fetch_region(r):
    target="/tmp/region-"+r+".osm.pbf"
    urls=[BASE+r+"-latest.osm.pbf",
       "https://download-ext2.geofabrik.de/asia/japan/"+r+"-latest.osm.pbf"]
    failures=[]
    for attempt in range(9):
        size=os.path.getsize(target) if os.path.exists(target) else 0
        headers={"Range":"bytes="+str(size)+"-"} if size else {}
        addr=urls[attempt%len(urls)]
        print("DOWNLOAD_RESUME",r,attempt,addr,"downloaded",size,flush=True)
        try:
            with requests.get(addr,timeout=(20,65),stream=True,allow_redirects=True,headers=headers) as res:
                res.raise_for_status()
                if size and res.status_code==206:
                    mode="ab"
                    expected=res.headers.get("Content-Range","")
                    if not expected.startswith("bytes "+str(size)+"-"):
                        raise RuntimeError("Content-Range invalid: "+expected)
                else:
                    mode="wb"
                with open(target,mode) as f:
                    for chunk in res.iter_content(chunk_size=1024*1024):
                        if chunk:f.write(chunk)
            print("DOWNLOAD_DONE",r,os.path.getsize(target),flush=True)
            return target
        except Exception as ex:
            failures.append(str(ex)[:200])
            print("DOWNLOAD_RETRY",r,attempt,str(ex)[:250],flush=True)
            time.sleep(min(attempt+1,7))
    raise RuntimeError("Failed to download "+r+": "+" | ".join(failures[-3:]))
def main():
    cityat=municipal_shapes()
    allrows=[];cnt=collections.Counter()
    for region in REGIONS:
        fn=fetch_region(region)
        h=Beaches()
        h.apply_file(fn,locations=True,idx="sparse_mmap_array")
        os.unlink(fn)
        cnt.update(h.count)
        print("REGION_FEATURES",region,len(h.features),dict(h.count),flush=True)
        for v in h.features:
            g=v["geom"]
            pt=g.representative_point()
            x,y=pt.x,pt.y
            loc=cityat((x,y)) if cityat else None
            if not loc:continue
            pref,city,how=loc
            p=g.bounds
            oid=f'OSM:{v["type"]}/{v["id"]}'
            allrows.append({"name":v["name"],"prefecture":pref,"municipality":city,
                "feature_type":v["kind"],"osm_id":oid,"latitude":y,"longitude":x,
                "west":p[0],"south":p[1],"east":p[2],"north":p[3],
                "geometry_type":g.geom_type,"url":f'https://www.openstreetmap.org/{v["type"]}/{v["id"]}',
                "geometry":mapping(g)})
    print("ALL_FEATURES_WITH_MUNI",len(allrows),flush=True)
    with open(OUT+"/osm_named_features.geojson","w",encoding="utf8") as f:
        json.dump({"type":"FeatureCollection","features":[
          {"type":"Feature","geometry":r["geometry"],"properties":{k:v for k,v in r.items() if k!="geometry"}}
          for r in allrows]},f,ensure_ascii=False)
    cols=["name","prefecture","municipality","feature_type","osm_id","latitude","longitude","west","south","east","north","geometry_type","url"]
    with open(OUT+"/osm_named_features.csv","w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,cols,extrasaction="ignore");w.writeheader();w.writerows(allrows)
    with open(OUT+"/osm_pbf_report.json","w",encoding="utf8") as f:
        json.dump({"regions":REGIONS,"OSM_geometry_entities":len(allrows),"types":dict(cnt),
                   "remarks":"OSM geom defines named physical beach/pier boundaries, not the legal fishing permitted area."},f,ensure_ascii=False,indent=2)
    print("OSM_RESULT",len(allrows),dict(cnt),flush=True)
if __name__=="__main__":main()
