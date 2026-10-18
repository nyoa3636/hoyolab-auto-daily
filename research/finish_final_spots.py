#!/usr/bin/env python3
"""Finish reproducible geographical validation, physical feature bounds and conservative dedup.
Coordinates only from actual site pages, C09 official points, or named GIS features.
A bounding box of geographic features IS NOT a legally authorized fishing area.
"""
import sys,csv,re,math,json,os,time,collections
from urllib.parse import urlparse
import requests
sys.path.insert(0,"research")
import georeference_dedupe as G

INDIR="output/geo/"
INPUT=INDIR+"source_enriched_deduplicated.csv"
OSM=INDIR+"national_coastal_osm_features.csv"
PREFS=G.PREFS
stats=collections.Counter();issues=[]
def reader(p):
    with open(p,encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def write(path,items,columns):
    with open(INDIR+path,"w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,columns,extrasaction="ignore");w.writeheader();w.writerows(items)
def point(r):
    try:
        lat=float(r.get("緯度") or "nan");lon=float(r.get("経度") or "nan")
        if 24<lat<46.7 and 122<lon<155:return lon,lat
    except (TypeError,ValueError):pass
    return None
def cityeq(a,b):
    a=(a or "").replace("郡","").replace("ヶ","ケ").replace("の","ノ")
    b=(b or "").replace("郡","").replace("ヶ","ケ").replace("の","ノ")
    return bool(a and b and (a==b or a.endswith(b) or b.endswith(a)))
def normal(s):
    s=(s or "").replace("ヶ","ケ").replace("の","ノ").replace("髙","高")
    return re.sub(r"[\s　・･．.，,、:：（）()\[\]【】『』「」'’\"／/_－〜~\-]","",s)
def root(s):
    s=normal(s)
    s=re.sub(r"(?:（利用状況を確認）|利用状況を確認|周辺|一帯|釣り場)$","",s)
    return re.sub(r"(?:海水浴場|海浜公園|海岸|サーフ|ビーチ|砂浜|漁港|港湾|ふ頭|埠頭|防波堤|堤防|波止|突堤|港|浜|海浜)$","",s)
def kind(r):
    n=normal(r["釣り場名"])
    if r.get("種別")=="漁港" or re.search("(?:漁港|港)$",n):return "port"
    if re.search("(?:海水浴場|海浜公園|海岸|サーフ|ビーチ|砂浜|浜|海浜)$",n):return "beach"
    if re.search("(?:堤防|防波堤|突堤|ふ頭|埠頭|波止)$",n):return "pier"
    return "other"
def dist(a,b):
    lon,lat=a;lo2,la2=b
    return math.hypot((lat-la2)*111195,(lon-lo2)*111195*math.cos(math.radians((lat+la2)/2)))
def bboxdistance(p,bb):
    lon,lat=p;w,s,e,n=bb
    lon2=min(max(lon,w),e);lat2=min(max(lat,s),n)
    return dist(p,(lon2,lat2))
def is_missing(r):return point(r) is None
def extract_site_geo(url):
    try:
        rr=requests.get(url,timeout=17,headers={"User-Agent":"Personal research on Japanese fishing locations"})
        if rr.status_code!=200:return None,"HTTP "+str(rr.status_code)
        h=rr.text
        # Many site coordinates are React state serialized as \"latitude\":nn, \"longitude\":nn.
        h=re.sub(r'\\+"','"',h)
        matches=re.findall(r'latitude"\s*:\s*(-?\d{2}\.\d+)\s*,\s*"longitude"\s*:\s*(-?\d{3}\.\d+)',h)
        if not matches:
            matches=re.findall(r'lat"\s*:\s*(-?\d{2}\.\d+)\s*,\s*"lng"\s*:\s*(-?\d{3}\.\d+)',h)
        cands=[]
        for lat,lon in matches:
            pos=(float(lon),float(lat))
            if 24<pos[1]<46.7 and 122<pos[0]<155:cands.append(pos)
        if not cands:return None,"No embedded coordinates"
        freq=collections.Counter(cands)
        best,n=freq.most_common(1)[0]
        if n<2 and len(freq)>1:return None,"Multiple competing coordinates"
        return best,"FishCast embedded latitude/longitude"
    except Exception as e:return None,str(e)[:100]
def load_features():
    if not os.path.isfile(OSM):
        print("OSM NATIONAL GIS FILE NOT AVAILABLE",flush=True)
        return []
    features=reader(OSM);out=[]
    for feat in features:
        try:
            box=[float(feat[k]) for k in ("west","south","east","north")]
            pt=(float(feat["longitude"]),float(feat["latitude"]))
            if not (122<pt[0]<155 and 24<pt[1]<47):continue
            out.append(dict(feat,_bbox=box,_point=pt,_root=root(feat["name"])))
        except Exception:continue
    print("NATIONAL GIS FEATURES",len(out),flush=True)
    return out
def match_osm(rows,features):
    byname=collections.defaultdict(list)
    for f in features:
        if len(f["_root"])>=2:
            byname[(f["prefecture"],f["_root"],f["feature_type"])].append(f)
    for r in rows:
        site=point(r);k=kind(r)
        nm=root(r["釣り場名"])
        if len(nm)<2 or k not in ("beach","port","pier"):continue
        options=byname.get((r["都道府県"],nm,k),[])
        if not options:continue
        eligible=[]
        for f in options:
            if r.get("市区町村") and f["municipality"] and not cityeq(r["市区町村"],f["municipality"]):continue
            if site:
                ds=bboxdistance(site,f["_bbox"]) if f["geometry_type"] not in ("Point","MultiPoint") else dist(site,f["_point"])
                if ds>500:continue
            else:ds=0
            eligible.append((ds,f))
        if len(eligible)!=1:continue
        ds,f=eligible[0]
        if not site:
            r["緯度"]=f["latitude"];r["経度"]=f["longitude"]
            r["位置出典"]=f["url"];r["位置照合"]="OSM名称・都道府県・市町村照合（代表点）"
            r["座標確認状況"]="座標あり"
            stats["osm_point_added"]+=1
        if not r["市区町村"] and f["municipality"]:r["市区町村"]=f["municipality"]
        gtype=f["geometry_type"]
        if gtype in ("Polygon","MultiPolygon","LineString","MultiLineString") and r.get("範囲種別") in ("未確認","位置点のみ・区域未確定","不明",""):
            for j,col in enumerate(("範囲西端経度","範囲南端緯度","範囲東端経度","範囲北端緯度")):r[col]=str(f["_bbox"][j])
            r["範囲種別"]="OSM実測地物の外接矩形（"+gtype+"）"
            r["範囲出典"]=f["url"]
            r["範囲ジオメトリID"]=f["osm_id"]
            r["地物区域の検証"]="OSM地物名一致、場所一致、海岸・施設の地理的範囲"
            stats["mapped_extent_added"]+=1
        else:
            r["地物区域の検証"]="OSM名称・位置一致（地理的線・面の範囲なし）"
def lookup_remainder(rows):
    for r in rows:
        if point(r):continue
        url=r["出典URL"]
        if url.startswith("https://fishcast.jp/spots/"):
            c,explanation=extract_site_geo(url)
            if c:
                r["経度"]=f"{c[0]:.8f}";r["緯度"]=f"{c[1]:.8f}"
                r["位置出典"]=url;r["位置照合"]="FishCast原典ページの構造化座標"
                r["座標確認状況"]="座標あり";stats["fishcast_coords_added"]+=1
            else:issues.append(dict(error_type="missing_fishcast",name=r["釣り場名"],url=url,note=explanation))
# Grounded cross-prefecture corrections: administrative polygons + direct tourism/map sources.
# Coordinates are representative location markers, never legal fishing-access zones.
VERIFIED_PLACES={
 "岩井海岸":("千葉県","南房総市",None,None,"https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03.html","県所属誤りを地理境界で訂正"),
 "幕張の浜":("千葉県","千葉市美浜区",None,None,"https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03.html","県所属誤りを地理境界で訂正"),
 "浦安高洲海浜公園":("千葉県","浦安市",None,None,"https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03.html","県所属誤りを地理境界で訂正"),
 "白子海岸":("千葉県","白子町",None,None,"https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03.html","県所属誤りを地理境界で訂正"),
 "宮崎・境海岸(ヒスイ海岸)":("富山県","朝日町",None,None,"https://www.info-toyama.com/attractions/51009","富山県公式観光情報により県所属を訂正"),
 "湯河原海岸":("神奈川県","湯河原町",35.1488889,139.1188889,"https://surf-ok.jp/surf-spot-yugawara-yoshihama/","隣接する吉浜海岸の代表点へ補正、厳密な地点輪郭は未確定"),
 "王子ヶ浜海岸":("和歌山県","新宮市",33.7144949,136.000989,"https://mapfan.com/spots/SCC%2CJ%2C8VYR","和歌山県新宮市の王子ヶ浜名所代表点へ補正、厳密な地点輪郭は未確定"),
}
def apply_known_corrections(rows):
    for r in rows:
        name=r["釣り場名"]
        if name in VERIFIED_PLACES:
            pref,city,lat,lon,src,note=VERIFIED_PLACES[name]
            old=r["都道府県"];r["都道府県"]=pref;r["市区町村"]=city
            if lat is not None:
                r["緯度"]=str(lat);r["経度"]=str(lon)
            r["監査備考"]=(r.get("監査備考") or "")+"; "+note+" (旧県:"+old+")"
            r["位置修正出典"]=src;r["位置照合"]=note
            stats["verified_location_corrections"]+=1
        elif name=="熊野川河口" and r["都道府県"]=="和歌山県":
            # The river mouth has two shores in different prefectures.
            r["市区町村"]="新宮市／紀宝町"
            r["位置照合"]="両県跨り河口・代表点は三重県紀宝町側"
            r["位置修正出典"]="https://tsuriba.info/spot/1681"
            r["監査備考"]=(r.get("監査備考") or "")+"; 和歌山県新宮市と三重県紀宝町の県境を跨ぐ"
            stats["cross_prefecture_river_mouth"]=1
def region_validation(rows):
    cityat=G.municipal_shapes()
    if not cityat:return
    for r in rows:
        pp=point(r)
        if not pp:continue
        loc=cityat(pp,within=0)
        if not loc:continue
        if loc[0]!=r["都道府県"] and not (r["釣り場名"]=="熊野川河口" and loc[0]=="三重県"):
            # Strictly reject coordinates which fall inside another prefecture,
            # rather than allowing false confidence or deduplication based on bad GPS.
            r["監査備考"]=(r.get("監査備考") or "")+"; coordinate geographically in "+loc[0]+" not "+r["都道府県"]
            r["不正座標"]=(r["緯度"]+","+r["経度"])
            r["緯度"]=r["経度"]=""
            r["座標確認状況"]="都道府県不一致・座標保留"
            r["位置照合"]="実際の座標が他県にあるため保留"
            issues.append(dict(error_type="prefecture_mismatch",name=r["釣り場名"],old_geo=r["不正座標"],pref=r["都道府県"],located_pref=loc[0]))
            stats["wrong_prefecture_coords_held"]+=1
        elif not r.get("市区町村") and loc[1]:
            r["市区町村"]=loc[1]
            stats["missing_city_filled"]+=1
def geo_dedupe(rows):
    # Preserve original order to keep the stable source row index and supporting evidence.
    groups=collections.defaultdict(list)
    for i,r in enumerate(rows):
        n=root(r["釣り場名"])
        if len(n)>1:
            groups[(r["都道府県"],n,kind(r))].append(i)
    parent=list(range(len(rows)))
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    def union(i,j):
        a,b=find(i),find(j)
        if a!=b:parent[b]=a
    evidence={}
    for g in groups.values():
        if len(g)<2:continue
        for x in range(len(g)):
            for y in range(x+1,len(g)):
                i,j=g[x],g[y];a,b=rows[i],rows[j]
                p,q=point(a),point(b)
                if not p or not q:continue
                if a.get("市区町村") and b.get("市区町村") and not cityeq(a["市区町村"],b["市区町村"]):continue
                same_id=a.get("範囲ジオメトリID") and a["範囲ジオメトリID"]==b.get("範囲ジオメトリID")
                same_c09=a.get("照合管理ID") and a["照合管理ID"]==b.get("照合管理ID")
                d=dist(p,q)
                if same_c09 or (same_id and d<=1200) or (d<=22 and normal(a["釣り場名"])==normal(b["釣り場名"])):
                    union(i,j);evidence[(i,j)]="共通C09施設" if same_c09 else "同一GIS地物"+str(same_id)+f"距離{d:.1f}m"
    parts=collections.defaultdict(list)
    for i in range(len(rows)):parts[find(i)].append(i)
    olddup=[];retained=set(range(len(rows)))
    def score(x):
        return (bool(x.get("範囲ジオメトリID")),bool(x.get("緯度")),bool(x.get("市区町村")),len(x.get("住所") or ""),bool(x.get("釣果根拠")))
    for arr in parts.values():
        if len(arr)<=1:continue
        keep=max(arr,key=lambda idx:score(rows[idx]))
        master=rows[keep]
        for idx in arr:
            if idx==keep:continue
            other=rows[idx];retained.remove(idx)
            master["統合した出典"]=";".join(dict.fromkeys(filter(None,(master.get("統合した出典","")+";"+other.get("統合した出典","")).split(";"))))
            olddup.append({"保持した元行":master.get("元CSV行",""),"統合した元行":other.get("元CSV行",""),"地点名":other["釣り場名"],
               "保持した地点名":master["釣り場名"],"都道府県":master["都道府県"],"根拠":("同一地理地物ID" if master.get("範囲ジオメトリID") and master["範囲ジオメトリID"]==other.get("範囲ジオメトリID") else "同一公式施設IDまたは座標一致"),"削除元URL":other["出典URL"]})
    return [rows[i] for i in sorted(retained)],olddup
def main():
    rows=reader(INPUT);print("INPUT",len(rows),flush=True)
    for r in rows:
        r["不正座標"]="";r["地物区域の検証"]="未確認";r["位置修正出典"]="";r["範囲の意味"]="地理的物理地物の区域であり釣りの法的な許可区域ではない"
    lookup_remainder(rows)
    apply_known_corrections(rows)
    features=load_features()
    match_osm(rows,features)
    region_validation(rows)
    dedup,deleted=geo_dedupe(rows)
    cols=list(rows[0].keys())
    write("final_all_with_audit.csv",rows,cols)
    write("final_deduplicated.csv",dedup,cols)
    write("final_additional_merges.csv",deleted,["保持した元行","統合した元行","地点名","保持した地点名","都道府県","根拠","削除元URL"])
    missing=[r for r in dedup if not point(r) or r["範囲種別"] in ("未確認","不明","位置点のみ・区域未確定","")]
    write("final_unresolved.csv",missing,cols)
    report={"base_records":len(rows),"final_records":len(dedup),"additional_deletions":len(deleted),
        "coordinates":sum(bool(point(r)) for r in dedup),
        "coordinates_missing":sum(not point(r) for r in dedup),
        "extent_from_official_or_osm":sum(r["範囲種別"] not in ("未確認","不明","位置点のみ・区域未確定","") for r in dedup),
        "extent_not_verified":sum(r["範囲種別"] in ("未確認","不明","位置点のみ・区域未確定","") for r in dedup),
        "survey_notes":"Actual physical footprints from national GIS, not legal boundaries for fishing.",
        "stats":dict(stats),"issues":issues}
    with open(INDIR+"final_validation_report.json","w",encoding="utf8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print("FINAL_RESULT",json.dumps({k:v for k,v in report.items() if k!="issues"},ensure_ascii=False),flush=True)
if __name__=="__main__":main()
