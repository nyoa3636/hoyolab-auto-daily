#!/usr/bin/env python3
"""Verify 2006 named C23 nationwide Japanese coastlines against existing personal sea fishing sites.
C23 measures a coastal waterline, NOT the actual public fishing-access perimeter.
"""
import os,io,csv,json,zipfile,requests,collections,concurrent.futures,math,time,re
import shapefile
from shapely.geometry import Point,box,LineString,shape,mapping
from shapely.strtree import STRtree
from shapely.ops import nearest_points

SRC="output/geo/final_deduplicated.csv"
DIR="output/geo/"
ALL="北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県".split()
PREF="北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 静岡県 愛知県 三重県 京都府 大阪府 兵庫県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県".split()
INDEX={p:i for i,p in enumerate(ALL,1)}
STAT=collections.Counter();ERR=[];RESULT=[]
def load():
    with open(SRC,encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def norm(s):
    s=(s or "").replace("ヶ","ケ").replace("之","ノ").replace("の","ノ")
    return re.sub(r"[\s　・･．.,，、:：（）()\[\]【】『』「」'’\"／/_－〜~\-]","",s)
def root(s):
    s=norm(s)
    return re.sub(r"(?:海水浴場|海浜公園|海岸|サーフ|ビーチ|砂浜|浜|海浜|周辺)$","",s)
def m_dist(p,q):
    x,y=p;xx,yy=q
    return math.hypot((yy-y)*111195,(xx-x)*111195*math.cos(math.radians((y+yy)/2)))
def get_zip(pref):
    i=INDEX[pref]
    url=f"https://nlftp.mlit.go.jp/ksj/gml/data/C23/C23-06/C23-06_{i:02d}_GML.zip"
    for attempt in range(2):
        try:
            r=requests.get(url,timeout=55)
            r.raise_for_status()
            if r.content[:2]!=b"PK":raise ValueError("non-zip")
            return (r.content,url,"")
        except Exception as e:
            if attempt==1:return (None,url,str(e)[:160])
            time.sleep(.3)
def zip_features(pref):
    data,url,err=get_zip(pref)
    if data is None:return None,url,err
    z=zipfile.ZipFile(io.BytesIO(data))
    fn=next((x for x in z.namelist() if x.endswith("Coastline.shp")),None)
    if not fn:return None,url,"no coastline shape"
    stem=fn[:-4]
    r=shapefile.Reader(shp=io.BytesIO(z.read(fn)),shx=io.BytesIO(z.read(stem+".shx")),
        dbf=io.BytesIO(z.read(stem+".dbf")),encoding="cp932",encodingErrors="replace")
    columns=[f[0] for f in r.fields[1:]]
    segments=[]
    for ind,sr in enumerate(r.iterShapeRecords()):
        props=dict(zip(columns,sr.record))
        try:
            geom=shape(sr.shape.__geo_interface__)
            if geom.is_empty:continue
            if not geom.is_valid:geom=geom.buffer(0)
            if geom.is_empty:continue
            if geom.geom_type not in ("LineString","MultiLineString"):continue
            segments.append({"id":f"C23:{INDEX[pref]:02d}:{ind}",
                             "name":str(props.get("C23_004") or "").strip(),
                             "admin":str(props.get("C23_001") or ""),
                             "manager":str(props.get("C23_006") or "").strip(),
                             "geometry":geom})
        except Exception as e:ERR.append([pref,"invalid line",str(e)[:90]])
    return segments,url,""
def process_pref(pref,rows):
    segs,url,err=zip_features(pref)
    if segs is None:return {"pref":pref,"error":err,"rows":rows,"features":[],"found":0}
    shapes=[seg["geometry"] for seg in segs];tree=STRtree(shapes)
    identity={id(g):i for i,g in enumerate(shapes)}
    features=[];counts=collections.Counter()
    for r in rows:
        for k in ("C23海岸線距離m","C23海岸保全区域名","C23海岸管理者",
            "C23海岸線地物ID","C23水際線出典","C23参照海岸線形状",
            "C23海岸線西端経度","C23海岸線南端緯度","C23海岸線東端経度","C23海岸線北端緯度",
            "C23範囲照合区分"):
            r[k]=""
        r["C23水際線の意味"]="2006年の国交省海岸線の隣接区間。釣り場や立入可能区域の境界ではない"
        try:
            lat,lon=float(r["緯度"]),float(r["経度"])
            if not (24<lat<47 and 122<lon<155):continue
        except (ValueError,TypeError):continue
        pt=Point(lon,lat)
        candidates=[]
        for obj in tree.query(pt.buffer(.03)):
            ix=int(obj) if not hasattr(obj,"geom_type") else identity.get(id(obj),-1)
            if ix<0:continue
            seg=segs[ix]
            near=nearest_points(pt,seg["geometry"])[1]
            d=m_dist((lon,lat),(near.x,near.y))
            if d>3000:continue
            site=root(r["釣り場名"]);coast=root(seg["name"])
            # C23 official name describes the coastal protection district, not a beach access permit.
            name_equiv=len(site)>=2 and len(coast)>=2 and (site==coast or site in coast or coast in site)
            candidates.append((d,not name_equiv,ix,name_equiv))
        if not candidates:
            counts["outside3km"]+=1;continue
        candidates.sort()
        named=[c for c in candidates if c[3] and c[0]<=750]
        # Prioritize exact named coastal district if spatially close; else closest surveyed shore.
        selected=min(named) if named else candidates[0]
        d,_,idx,exact=selected
        seg=segs[idx];r["C23海岸線距離m"]=f"{d:.1f}"
        r["C23海岸保全区域名"]=seg["name"];r["C23海岸管理者"]=seg["manager"]
        r["C23海岸線地物ID"]=seg["id"];r["C23水際線出典"]=url
        if exact and d<=750:status="海岸名一致・2006年の公的海岸線参照"
        elif d<=250:status="海岸名一致なし・近傍2006年水際線"
        elif d<=750:status="近傍2006年水際線（750m以内）"
        elif d<=2000:status="遠方2006年水際線（2000m以内）"
        else:status="水際線2km超・再検査"
        r["C23範囲照合区分"]=status
        counts[status]+=1
        # Do not copy a 50km coastline as if it were a small site.
        # Export the real surveyed line cut within a 500-metre local viewing window.
        width=500/(111195*math.cos(math.radians(lat)));height=500/111195
        cut=seg["geometry"].intersection(box(lon-width,lat-height,lon+width,lat+height))
        if cut.is_empty:
            nearest=nearest_points(pt,seg["geometry"])[1]
            width=500/(111195*math.cos(math.radians(nearest.y)));height=500/111195
            cut=seg["geometry"].intersection(box(nearest.x-width,nearest.y-height,nearest.x+width,nearest.y+height))
        if cut.is_empty:continue
        if cut.geom_type not in ("LineString","MultiLineString","GeometryCollection"):continue
        b=cut.bounds
        for k,v in zip(("C23海岸線西端経度","C23海岸線南端緯度","C23海岸線東端経度","C23海岸線北端緯度"),b):
            r[k]=f"{v:.7f}"
        r["C23参照海岸線形状"]=f"C23:clip:{r.get('元CSV行')}"
        features.append({"type":"Feature","geometry":mapping(cut),
            "properties":{"csv_original_row":r.get("元CSV行"),"site":r["釣り場名"],
                "prefecture":pref,"c23_id":seg["id"],"c23_name":seg["name"],
                "distance_m":float(r["C23海岸線距離m"]),
                "scope":"近傍国土数値情報2006年海岸線の500m表示用区間・釣り場利用可能範囲ではない"}})
    return {"pref":pref,"rows":rows,"features":features,"counts":dict(counts),"segments":len(segs),"url":url}
def main():
    rows=load();by=collections.defaultdict(list)
    for r in rows:by[r["都道府県"]].append(r)
    jobs=list(by.items());allfeatures=[];reports=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        fut=[ex.submit(process_pref,p,rr) for p,rr in jobs]
        for f in concurrent.futures.as_completed(fut):
            a=f.result();reports.append({k:v for k,v in a.items() if k not in ("rows","features")})
            allfeatures.extend(a["features"])
            print("C23_PREF",a["pref"],a.get("segments"),len(a["features"]),a.get("counts"),a.get("error"),flush=True)
    cols=list(rows[0])
    with open(DIR+"c23_shoreline_enriched.csv","w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,cols,extrasaction="ignore");w.writeheader();w.writerows(rows)
    with open(DIR+"c23_nearby_shoreline_500m.geojson","w",encoding="utf-8") as f:
        json.dump({"type":"FeatureCollection","features":allfeatures},f,ensure_ascii=False)
    totals=collections.Counter();total_seg=0
    for r in reports:
        totals.update(r.get("counts") or {});total_seg+=r.get("segments") or 0
    stat={"records":len(rows),"prefectures":len(reports),"c23_shoreline_segments_examined":total_seg,
        "matched_shoreline_features":len(allfeatures),"classification":dict(totals),
        "missing_c23_prefectures":[r["pref"] for r in reports if r.get("error")],
        "sources":"国土数値情報 C23-06、平成18年作成・JGD2000",
        "important_caveat":"海岸線は釣り場区域または立入・釣り許可区域の確定範囲ではない。位置近傍の公的水際線である。",
        "by_pref":sorted(reports,key=lambda x:PREF.index(x["pref"]))}
    with open(DIR+"c23_shoreline_report.json","w",encoding="utf-8") as f:json.dump(stat,f,ensure_ascii=False,indent=2)
    print("C23_NATIONAL",json.dumps({k:v for k,v in stat.items() if k!="by_pref"},ensure_ascii=False),flush=True)
if __name__=="__main__":main()
