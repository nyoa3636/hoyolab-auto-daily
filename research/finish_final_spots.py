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
    """Remove strong duplicate names from the same physical facility, respecting spot type."""
    groups=collections.defaultdict(list)
    for i,r in enumerate(rows):
        n=root(r["釣り場名"])
        if len(n)>=2:
            groups[(r["都道府県"],n,kind(r))].append(i)
    parent=list(range(len(rows)))
    reasons={}
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    def union(i,j,why):
        a,b=find(i),find(j)
        if a!=b:parent[b]=a
        reasons[(min(i,j),max(i,j))]=why
    def related_muni(a,b):
        if cityeq(a,b):return True
        a=re.sub(r"[\s　]",'',str(a or ""))
        b=re.sub(r"[\s　]",'',str(b or ""))
        if a and b and (a in b or b in a):return True
        def simple(x):return re.sub(r"[市町村区]+$","",x)
        return bool(a and b and simple(a)==simple(b))
    for g in groups.values():
        if len(g)<2:continue
        for x in range(len(g)):
            for y in range(x+1,len(g)):
                i,j=g[x],g[y]
                a,b=rows[i],rows[j]
                p,q=point(a),point(b)
                if not p or not q:continue
                # Vast surf-area labels are not the same as an individual beach.
                if a["釣り場名"] in BROAD_AREA_NAMES or b["釣り場名"] in BROAD_AREA_NAMES:continue
                t=kind(a);d=dist(p,q)
                id1=a.get("範囲ジオメトリID");id2=b.get("範囲ジオメトリID")
                same_shape=bool(id1 and id1==id2)
                cid1=a.get("照合管理ID");cid2=b.get("照合管理ID")
                same_c09=bool(cid1 and cid1==cid2)
                muni_ok=related_muni(a.get("市区町村"),b.get("市区町村"))
                exact=normal(a["釣り場名"])==normal(b["釣り場名"])
                why=""
                if t=="port":
                    if same_c09 or (same_shape and d<1500):why="同一漁港地物識別子"
                    elif d<=3:why="名称同一・公的／掲載元のGPS位置が3m以内"
                    elif d<=150 and muni_ok:why="名称同一・行政地名一致・150m以内"
                    elif d<=90 and (a["出典"]=="国土数値情報C09" or b["出典"]=="国土数値情報C09"):
                        why="国土数値情報と民間掲載の同名漁港・90m以内"
                elif t=="beach":
                    if same_shape and d<=1500:why="海浜名称一致・同一GIS地物"
                    elif d<=12 and (muni_ok or not a.get("市区町村") or not b.get("市区町村")):
                        why="同一海岸の別表記・12m以内"
                elif t=="pier":
                    if same_shape and d<250:why="同一GIS堤防地物"
                    elif exact and d<=12:why="同名堤防のGPS位置一致"
                if why:union(i,j,why)
    components=collections.defaultdict(list)
    for i in range(len(rows)):components[find(i)].append(i)
    removals=[];keepers=set(range(len(rows)))
    def score(r):
        # For wrongly mapped legacy C09 municipalities, prefer modern local fishing pages.
        return (r["出典"]!="国土数値情報C09",bool(r.get("範囲ジオメトリID")),
          bool(r.get("市区町村")),bool(r.get("緯度")),len(r.get("住所") or ""))
    for ids in components.values():
        if len(ids)<2:continue
        master_idx=max(ids,key=lambda i:score(rows[i]))
        master=rows[master_idx]
        for i in ids:
            if i==master_idx:continue
            child=rows[i]
            keepers.discard(i)
            oldshape=child.get("範囲ジオメトリID")
            if not master.get("範囲ジオメトリID") and oldshape:
                for field in ["範囲西端経度","範囲南端緯度","範囲東端経度","範囲北端緯度",
                              "範囲ジオメトリID","範囲種別","範囲出典"]:
                    master[field]=child.get(field,"")
            if not master.get("照合管理ID") and child.get("照合管理ID"):
                master["照合管理ID"]=child["照合管理ID"]
            master["統合した出典"]=";".join(dict.fromkeys(filter(None,
               (master.get("統合した出典","")+";"+child.get("統合した出典","")).split(";"))))
            removals.append({"保持した元行":master.get("元CSV行",""),"統合した元行":child.get("元CSV行",""),
                "地点名":child["釣り場名"],"保持した地点名":master["釣り場名"],
                "都道府県":master["都道府県"],
                "根拠":"同一地名・同一施設の座標またはGIS境界に基づく照合",
                "削除元URL":child["出典URL"]})
            stats["safe_duplicate_merges"]+=1
    return [rows[i] for i in sorted(keepers)],removals

# Remaining single-point locations were checked against published map positions.
# A 'reference-area' point is not claimed to be on the beach. Preserve precision labels.
ADDITIONAL_POINTS={
 "波立海岸":("福島県","いわき市",37.1131605,140.9986935,"https://mapfan.com/spots/SCC%2CJ%2CAZN7","海岸入口の公表位置・海岸実地点の代理代表点"),
 "萱浜海岸":("福島県","南相馬市",37.625583,141.011417,"https://postcode.teraren.com/postcodes/9750036","萱浜町域代表点（海岸への実際のアクセス地点は未特定）"),
 "青塚海岸":("茨城県","鹿嶋市",36.0482141,140.6263016,"https://jl-db.nfaj.go.jp/location/080100055/","茨城県鹿嶋市青塚の所在地代表点（海岸実地点は未特定）"),
 "平砂浦海岸":("千葉県","館山市",34.9405480,139.8110676,"https://tsuriba.info/spot/","同一海岸を示す既存掲載地点の代表座標"),
 "太海海岸":("千葉県","鴨川市",35.0802634277004,140.09920208978,"https://maruchiba.jp/spot/detail_10683.html","千葉県公式観光サイトの太海海水浴場位置"),
 "浜野海岸":("静岡県","掛川市",34.65538499814429,138.03353501684808,"https://fish-beginner.com/hamanokaigan","釣り場紹介ページの地図埋込中心点"),
 "鮫島海岸":("静岡県","磐田市",34.6586671,137.8481526,"https://www.ishiguro-gr.com/enjoy/fishing-post/detail.php?id=315","釣具店の鮫島海岸アクセスマップ位置"),
 "廿日市・大野浦海岸":("広島県","廿日市市",34.285,132.261,"https://tsurispot.com/spots/hatsukaichi-onoura-c8","元掲載ページのGPS位置"),
 "高松市・大的場海岸護岸":("香川県","高松市",34.348,134.055,"https://tsurispot.com/spots/takamatsu-omatoba-kaigan","元掲載ページのGPS位置"),
}
BROAD_AREA_NAMES={
 "県北サーフ":("宮城県","north",None),
 "県南サーフ":("宮城県","south",None),
 "仙台サーフ":("宮城県","municipality","仙台"),
 "小田原サーフ":("神奈川県","municipality","小田原"),
 "平塚サーフ":("神奈川県","municipality","平塚"),
 "舞阪サーフ":("静岡県","municipality","浜松"),
 "伊良湖サーフ":("愛知県","municipality","田原"),
}
def fill_remaining_coordinates(rows):
    for r in rows:
        if point(r):continue
        n=r["釣り場名"]
        if n not in ADDITIONAL_POINTS:continue
        pref,city,lat,lon,url,note=ADDITIONAL_POINTS[n]
        r["都道府県"]=pref;r["市区町村"]=city
        r["緯度"]=str(lat);r["経度"]=str(lon);r["位置出典"]=url
        r["位置照合"]=note;r["位置修正出典"]=url
        r["座標品質"]="町域の参考座標" if n in ("萱浜海岸","青塚海岸") else (
            "近傍アクセス点（海岸点未特定）" if n=="波立海岸" else "掲載元地図代表点")
        r["監査備考"]=(r.get("監査備考") or "")+"; "+note
        stats["additional_named_spots_filled"]+=1
    for r in rows:
        if point(r) or r["釣り場名"] not in BROAD_AREA_NAMES:continue
        pref,kind0,mark=BROAD_AREA_NAMES[r["釣り場名"]]
        candidates=[]
        for x in rows:
            if x is r or x["都道府県"]!=pref or G.typ(x)!="beach":continue
            c=point(x)
            if c is None:continue
            if kind0=="north" and c[1]<38.28:continue
            if kind0=="south" and c[1]>=38.28:continue
            if kind0=="municipality" and mark not in (x["市区町村"]+" "+x["釣り場名"]):continue
            candidates.append(c)
        if not candidates:
            stats["regional_area_no_references"]+=1;continue
        lons=[c[0] for c in candidates];lats=[c[1] for c in candidates]
        r["経度"]=str((min(lons)+max(lons))/2)
        r["緯度"]=str((min(lats)+max(lats))/2)
        r["座標品質"]="広域名の収録済み地点の外接矩形中心・釣り場地点ではない"
        r["位置照合"]="広域ラベルを既収録の同エリア海岸地点から概括"
        r["位置出典"]="既収録の同エリア海岸地点の代表座標群"
        r["釣り場種別"]="広域エリア（単一点ではない）"
        r["参考広域西端"]=str(min(lons));r["参考広域南端"]=str(min(lats))
        r["参考広域東端"]=str(max(lons));r["参考広域北端"]=str(max(lats))
        stats["broad_region_labels_centered"]+=1
    # Distinguish real mapped outlines from a reproducible search/view window.
    for r in rows:
        c=point(r)
        if not c:continue
        if not r.get("座標品質"):
            r["座標品質"]="GISまたは掲載元の代表座標"
        if r.get("参考広域西端"):
            bb=(float(r["参考広域西端"]),float(r["参考広域南端"]),
                float(r["参考広域東端"]),float(r["参考広域北端"]))
            r["参考探索矩形の意味"]="収録済み下位地点の範囲・法的釣り可能区域ではない"
        else:
            lon,lat=c
            width=.3/111.195/math.cos(math.radians(lat))
            height=.3/111.195
            bb=(lon-width,lat-height,lon+width,lat+height)
            r["参考探索矩形の意味"]="代表点の周囲約300m・地理的地点の正式境界ではない"
        r["参考探索西端"]=f"{bb[0]:.8f}"
        r["参考探索南端"]=f"{bb[1]:.8f}"
        r["参考探索東端"]=f"{bb[2]:.8f}"
        r["参考探索北端"]=f"{bb[3]:.8f}"

def main():
    rows=reader(INPUT);print("INPUT",len(rows),flush=True)
    for r in rows:
        r["不正座標"]="";r["地物区域の検証"]="未確認";r["位置修正出典"]="";r["座標品質"]="";r["参考広域西端"]="";r["参考広域南端"]="";r["参考広域東端"]="";r["参考広域北端"]="";r["範囲の意味"]="地理的物理地物の区域であり釣りの法的な許可区域ではない"
    lookup_remainder(rows)
    apply_known_corrections(rows)
    features=load_features()
    match_osm(rows,features)
    fill_remaining_coordinates(rows)
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
