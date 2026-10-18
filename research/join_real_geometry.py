#!/usr/bin/env python3
"""Reconcile original fishing spots with real surveyed port extents and named OSM beach polygons.
Strict conservative deduplication; no invented shapes. Input source-enriched CSV when available.
"""
import os,sys,re,csv,json,math,collections
from shapely.geometry import shape,Point,mapping
from shapely.ops import nearest_points
sys.path.insert(0,"research")
import georeference_dedupe as G
OUTDIR="output/geo"
BASE=os.environ.get("SOURCE_INPUT","output/geo/deduplicated.csv")
GIS=os.environ.get("OSM_INPUT","output/geo/national_osm_named_features.geojson")
OUT=OUTDIR+"/final_geolocated_deduplicated.csv"
NAME_REMOVALS=r"(?:周辺|一帯|釣り場)$"
PORT_MATCHING=r"(?:漁港|港)$"
BEACH_MATCHING=r"(?:海水浴場|海岸|サーフ|ビーチ|砂浜|浜|海浜|海浜公園)$"
PIER_MATCHING=r"(?:防波堤|堤防|波止|突堤|ふ頭|埠頭)$"
def norm(s):
    return G.norm(s).replace("ヶ","ケ").replace("ケ","ヶ")
def root(s):
    s=norm(s)
    s=re.sub(NAME_REMOVALS,"",s)
    return re.sub(r"(?:漁港|港湾|海水浴場|海浜公園|海岸|サーフ|ビーチ|砂浜|漁港|港|浜|海浜|波止|ふ頭|埠頭|突堤|堤防|防波堤)$","",s)
def typ(s):
    s=norm(s)
    if re.search(PORT_MATCHING,s):return "port"
    if re.search(BEACH_MATCHING,s):return "beach"
    if re.search(PIER_MATCHING,s):return "pier"
    return "other"
def citysame(a,b):
    A=(a or "").replace("ヶ","ケ");B=(b or "").replace("ヶ","ケ")
    if not A or not B:return False
    return A==B or A.endswith(B) or B.endswith(A)
def distance_m(latlon,geom):
    pt=Point(latlon)
    a,b=nearest_points(pt,geom)
    return G.near((a.x,a.y),(b.x,b.y))
def writecsv(path,rows,cols):
    with open(path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=cols,extrasaction="ignore");w.writeheader();w.writerows(rows)
def main():
    with open(BASE,encoding="utf-8-sig",newline="") as f:rows=list(csv.DictReader(f))
    with open(GIS,encoding="utf8") as f:features=json.load(f)["features"]
    matched=0;unmatched=0;duplicates=[]
    by=collections.defaultdict(list)
    for ft in features:
        p=ft["properties"]
        name=p.get("name") or ""
        k=(p.get("prefecture"),root(name),p.get("feature_type"))
        if len(k[1])>=2:by[k].append(ft)
    detail={"name_exact":0,"root_same":0,"city_conflict":0,"many_candidates":0,"existing_coordinate_far":0,"new_coordinate":0,"new_extent":0}
    geo_added={}
    for row in rows:
        site=typ(row["釣り場名"]);rn=root(row["釣り場名"])
        if site not in ("port","pier","beach") or len(rn)<2:continue
        poss=by.get((row["都道府県"],rn,site),[])
        if not poss:continue
        poss=[ft for ft in poss if citysame(row["市区町村"],ft["properties"].get("municipality"))] if row["市区町村"] else poss
        if not poss:detail["city_conflict"]+=1;continue
        existing=G.coord(row)
        compatible=[]
        for f in poss:
            geom=shape(f["geometry"])
            try:
                if existing is not None and distance_m(existing,geom)>1750:continue
                compatible.append((f,geom))
            except Exception:continue
        if len(compatible)!=1:
            detail["many_candidates"]+=len(compatible)>1
            detail["existing_coordinate_far"]+=(bool(poss) and not compatible)
            continue
        ft,g=compatible[0];p=ft["properties"]
        if not row["市区町村"]:row["市区町村"]=p.get("municipality") or ""
        if existing is None:
            row["緯度"]=str(p.get("latitude") or "")
            row["経度"]=str(p.get("longitude") or "")
            row["位置出典"]=p["url"];row["位置照合"]="OSM地物名称+所在地一致"
            detail["new_coordinate"]+=1
        else:
            row["位置照合"]=(row.get("位置照合") or "")+"; OSM地物形状照合"
        if g.geom_type not in ("Point","MultiPoint") and row["範囲種別"] in ("不明","位置点のみ・区域未確定","未確認",""):
            code=p["osm_id"];bounds=g.bounds
            row["範囲西端経度"]=str(round(bounds[0],7));row["範囲南端緯度"]=str(round(bounds[1],7))
            row["範囲東端経度"]=str(round(bounds[2],7));row["範囲北端緯度"]=str(round(bounds[3],7))
            row["範囲出典"]=p["url"];row["範囲種別"]="地物輪郭（釣り許可範囲ではない）"
            row["範囲ジオメトリID"]=code
            geo_added[code]=ft
            detail["new_extent"]+=1
        if not row.get("照合管理ID"):
            row["照合管理ID"]=p["osm_id"]
        matched+=1
    # Apply extremely conservative merging from the *same* OSM geometry ID; same type + official region.
    par=list(range(len(rows)))
    def find(i):
        while par[i]!=i:par[i]=par[par[i]];i=par[i]
        return i
    def union(a,b):
        x,y=find(a),find(b)
        if x!=y:par[y]=x
    bygeom=collections.defaultdict(list)
    for i,r in enumerate(rows):
        if G.coord(r) is not None and r.get("照合管理ID"):
            bygeom[(r["都道府県"],typ(r["釣り場名"]),r["照合管理ID"])].append(i)
    for key,ids in bygeom.items():
        if key[1] not in ("port","beach","pier"):continue
        # Different names on an exactly identical OSM object should be aliases, not extra fishing sites.
        for k in ids[1:]:
            if citysame(rows[ids[0]]["市区町村"],rows[k]["市区町村"]):
                union(ids[0],k)
    grp=collections.defaultdict(list)
    for i in range(len(rows)):grp[find(i)].append(i)
    keep=set(range(len(rows)))
    for arr in grp.values():
        if len(arr)<2:continue
        def priority(i):
            r=rows[i]
            return (r.get("出典")!="国土数値情報C09",bool(r.get("範囲ジオメトリID")),
                    bool(r.get("住所")),len(r.get("統合した出典","")))
        target=max(arr,key=priority);win=rows[target]
        for idx in arr:
            if idx==target:continue
            loser=rows[idx];keep.discard(idx)
            win["統合した出典"]=";".join(dict.fromkeys(filter(None,
                (win.get("統合した出典","")+";"+loser.get("統合した出典","")).split(";"))))
            if win.get("範囲種別") in ("不明","位置点のみ・区域未確定","未確認") and loser.get("範囲種別") not in ("不明","位置点のみ・区域未確定","未確認"):
                for a in ["範囲西端経度","範囲南端緯度","範囲東端経度","範囲北端緯度","範囲出典","範囲種別","範囲ジオメトリID"]:
                    win[a]=loser[a]
            duplicates.append({"残存元CSV行":win["元CSV行"],"削除元CSV行":loser["元CSV行"],
                "都道府県":loser["都道府県"],"削除名称":loser["釣り場名"],
                "残存名称":win["釣り場名"],"重複根拠":"完全一致するOSM地物ID＋市区町村一致",
                "地物ID":loser["照合管理ID"]})
    clean=[r for i,r in enumerate(rows) if i in keep]
    for r in clean:r["座標確認状況"]="座標あり" if G.coord(r) else "未確認"
    cols=list(clean[0].keys())
    os.makedirs(OUTDIR,exist_ok=True)
    writecsv(OUT,clean,cols)
    writecsv(OUTDIR+"/final_osm_duplicate_trace.csv",duplicates,
             ["残存元CSV行","削除元CSV行","都道府県","削除名称","残存名称","重複根拠","地物ID"])
    with open(OUTDIR+"/final_beach_geometries.geojson","w",encoding="utf8") as f:json.dump({
        "type":"FeatureCollection","features":list(geo_added.values())},f,ensure_ascii=False)
    count=collections.Counter(r["範囲種別"] for r in clean)
    report={"input_records":len(rows),"final_records":len(clean),"OSM_matched":matched,
        "OSM_extra_removed":len(duplicates),"with_coordinate":sum(G.coord(r)!=None for r in clean),
        "without_coordinate":sum(G.coord(r)==None for r in clean),
        "extent_by_type":dict(count),"OSM_beach_geometries_added":len(geo_added),
        "match_statistics":detail,
        "source_baseline":BASE,"geographic_source":GIS,
        "caveats":["地物輪郭は釣り許可区域ではない",
                   "元データの区域境界のうち漁港は2006年度のもの",
                   "推測で空欄の地理的範囲を埋めていない",
                   "地物IDの完全一致と市区町村の一致のみで追加統合した"]}
    with open(OUTDIR+"/final_spatial_report.json","w",encoding="utf8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print("FINAL SPATIAL",json.dumps(report,ensure_ascii=False),flush=True)
if __name__=="__main__":main()
