#!/usr/bin/env python3
"""Auditable Japanese sea fishing locations georeference and conservative deduplication."""
import os,sys,csv,io,json,math,re,zipfile,time,collections,concurrent.futures
from urllib.parse import urljoin
import requests,shapefile
from shapely.geometry import shape,Point,LineString,MultiLineString,Polygon,GeometryCollection,mapping
from shapely.strtree import STRtree
from shapely.ops import unary_union

SRC="output/national_sea_fishing_sites_surf_expanded.csv"
OUTDIR="output/geo"
os.makedirs(OUTDIR,exist_ok=True)
PREFS="北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 静岡県 愛知県 三重県 京都府 大阪府 兵庫県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県".split()
ALLPREF="北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県".split()
STATE={f"{i:02d}":x for i,x in enumerate(ALLPREF,1)}
S=requests.Session();S.headers.update({"User-Agent":"Japan Fishing Personal Research geospatial crossreference/1.0 (responsible public data use)"})
ERR=[]; STATS=collections.Counter()
def get(u,timeout=50):
    for k in range(2):
        try:
            r=S.get(u,timeout=timeout);r.raise_for_status();return r
        except Exception as e:
            if k==1:ERR.append([u,str(e)[:200]])
            else:time.sleep(1.2)
    return None
def short(s):return str(s or "").strip()
def norm(s):
    s=str(s or "").replace("ヶ","ケ").replace("髙","高").replace("之","ノ").replace("の","ノ")
    return re.sub(r"[\s　・･．.，,、:：（）()\[\]【】『』「」'’\"／/_－〜~\-]","",s)
SUFFIXES=r"(?:海水浴場|海浜公園|海岸|サーフ|ビーチ|砂浜|漁港|港湾|ふ頭|埠頭|防波堤|堤防|波止|突堤|港|浜|海浜)"
def root(s):
    s=norm(s);s=re.sub(r"(?:周辺|一帯|釣り場)$","",s)
    return re.sub(SUFFIXES+r"$","",s)
def typ(r):
    n=norm(r["釣り場名"])
    if r.get("種別")=="漁港" or re.search(r"(?:漁港|港)$",n):return "port"
    if re.search(r"(?:海水浴場|海浜公園|海岸|サーフ|ビーチ|砂浜|浜|海浜)$",n):return "beach"
    if re.search(r"(?:堤防|防波堤|突堤|ふ頭|埠頭|波止)$",n):return "pier"
    return "other"
def coord(r):
    try:
        la,lo=float(r.get("緯度") or "nan"),float(r.get("経度") or "nan")
        if 24<=la<=46.5 and 122<=lo<=154:return (lo,la)
    except ValueError:pass
    return None
def near(a,b):
    # equirectangular distance in meters suitable for Japan and short offsets
    lon1,lat1=a;lon2,lat2=b
    return math.hypot((lat1-lat2)*111195,(lon1-lon2)*111195*math.cos(math.radians((lat1+lat2)*.5)))
def prefname(c):
    s=str(c or "").zfill(5)
    return STATE.get(s[:2],"")
def rowload():
    with open(SRC,encoding="utf-8-sig",newline="") as f:rows=list(csv.DictReader(f))
    for i,r in enumerate(rows,2):
        r["元CSV行"]=str(i);r["行政区域照合"]="未検証"
        r["位置出典"]="国土数値情報C09 (2006)" if r["出典"]=="国土数値情報C09" and coord(r) else ("既存収集座標（要精査）" if coord(r) else "未確認")
        r["位置照合"]="元レコードの座標" if coord(r) else "未解決"
        r["範囲出典"]="未確認";r["範囲種別"]="不明";r["範囲ジオメトリID"]=""
        r["範囲西端経度"]=r["範囲南端緯度"]=r["範囲東端経度"]=r["範囲北端緯度"]=""
        r["照合管理ID"]=""
        r["監査備考"]=""
    return rows
def reader_shp(z,filename):
    files={x.lower():x for x in z.namelist()}
    stem=filename[:-4]
    k=(stem+".dbf").lower()
    if k not in files:return None
    kw={"shp":io.BytesIO(z.read(files[filename.lower()])),"dbf":io.BytesIO(z.read(files[k]])),"encoding":"cp932","encodingErrors":"replace"}
    if (stem+".shx").lower() in files:kw["shx"]=io.BytesIO(z.read(files[(stem+".shx").lower()]))
    return shapefile.Reader(**kw)
def load_ports():
    u="https://nlftp.mlit.go.jp/ksj/gml/data/C09/C09-06/C09-06_GML.zip"
    r=get(u,120)
    if not r or r.content[:2]!=b"PK":print("NO OFFICIAL SHAPEFILE",flush=True);return [],{}
    z=zipfile.ZipFile(io.BytesIO(r.content))
    files={x.lower():x for x in z.namelist()}
    print("OFFICIAL ZIP files",[s for s in z.namelist() if s.endswith(".shp")],flush=True)
    portfile=next((x for x in z.namelist() if x.lower().endswith("fishingport.shp")),None)
    linefile=next((x for x in z.namelist() if x.lower().endswith("fishingportboundary.shp")),None)
    ports=[];bound=collections.defaultdict(list)
    if portfile:
        sr=reader_shp(z,portfile)
        fields=[f[0] for f in sr.fields[1:]]
        print("PORT FIELDS",fields,"ROWS",len(sr),flush=True)
        for z0 in sr.iterShapeRecords():
            d=dict(zip(fields,z0.record))
            pts=z0.shape.points
            if not pts:continue
            p=prefname(d.get("C09_003"))
            if p not in PREFS:continue
            pt=(float(pts[0][0]),float(pts[0][1]))
            if not (122<=pt[0]<155 and 24<=pt[1]<47):continue
            code=str(d.get("C09_001") or "").strip().zfill(7)
            ports.append({"name":str(d.get("C09_002") or ""),"root":root(d.get("C09_002")),"pref":p,
                         "municode":str(d.get("C09_003") or "").strip().zfill(5),"port_code":code,
                         "point":pt})
    if linefile:
        sr=reader_shp(z,linefile)
        fields=[f[0] for f in sr.fields[1:]]
        print("BOUNDARY FIELDS",fields,"ROWS",len(sr),flush=True)
        for sf in sr.iterShapeRecords():
            d=dict(zip(fields,sf.record))
            nums=[(k,v) for k,v in d.items() if k in ("C09_012","C09_052","C09_001")]
            key=str(nums[0][1]).strip().zfill(7) if nums else ""
            try:
                pts=sf.shape.points
                if not pts or len(pts)<2:continue
                # Preserve multipart survey boundaries; never interpret a nonclosed line as a polygon.
                a=sf.shape.parts[:]
                b=a[1:]+[len(pts)]
                lines=[LineString(pts[x:y]) for x,y in zip(a,b) if y-x>=2]
                if lines:bound[key].extend(lines)
            except Exception as e:ERR.append(["port boundary parse",str(e)[:150]])
    print("PORTS",len(ports),"BOUNDARY CODES",len(bound),flush=True)
    return ports,bound
def municipal_shapes():
    polygon=[];meta=[]
    rooturl="https://raw.githubusercontent.com/smartnews-smri/japan-topography/main/data/municipality/geojson/s0010/"
    for i in range(1,48):
        u=rooturl+f"N03-21_{i:02d}_210101.json"
        r=get(u,35)
        if not r:continue
        try:
            for ft in r.json().get("features",[]):
                props=ft["properties"]
                p=props.get("N03_001") or props.get("prefecture") or props.get("PREF_NAME") or ""
                city=props.get("N03_004") or props.get("CITY_NAME") or props.get("city") or ""
                if p not in PREFS:continue
                g=shape(ft["geometry"])
                if g.is_empty or not g.is_valid:g=g.buffer(0)
                if g.is_empty:continue
                polygon.append(g);meta.append((p,city))
        except Exception as e:ERR.append([u,"geojson "+str(e)[:140]])
    print("MUNICIPAL POLYGONS",len(polygon),flush=True)
    if not polygon:return None
    tree=STRtree(polygon);index={id(g):i for i,g in enumerate(polygon)}
    def cityat(c,within=700):
        pt=Point(c)
        for v in tree.query(pt):
            i=int(v) if not hasattr(v,"geom_type") else index.get(id(v),-1)
            if i>=0 and polygon[i].covers(pt):return meta[i]+("contained",)
        # For mapped coastal seawards points, choose nearby land within 700 m, but mark as inferred.
        if not within:return None
        q=pt.buffer(within/111195)
        best=(float("inf"),None)
        for v in tree.query(q):
            i=int(v) if not hasattr(v,"geom_type") else index.get(id(v),-1)
            if i<0:continue
            dst=polygon[i].distance(pt)*111195
            if dst<best[0]:best=(dst,meta[i])
        if best[0]<=within and best[1]:return best[1]+("near_coast",)
        return None
    return cityat
def validatecity(c):
    c=str(c or "").strip()
    if c and len(c)>19:return ""
    # Mistaken extraction sometimes reads '余市' from region but actual municipality '余市町'
    return c
def enrich_ports(rows,ports,bound,cityat):
    byname=collections.defaultdict(list)
    for i,p in enumerate(ports):byname[(p["pref"],p["root"])].append(i)
    matches={}
    for r in rows:
        if typ(r)!="port" or len(root(r["釣り場名"]))<2:continue
        key=(r["都道府県"],root(r["釣り場名"]))
        candidates=[ports[i] for i in byname.get(key,[])]
        if not candidates:continue
        old=coord(r)
        best=[]
        for p in candidates:
            if old and near(old,p["point"])>900:continue
            loc=cityat(p["point"]) if cityat else None
            if loc and loc[0]!=p["pref"]:continue
            city=validatecity(r["市区町村"])
            if city and loc and loc[1] and not (city==loc[1] or city.endswith(loc[1]) or loc[1].endswith(city)):
                continue
            best.append((p,loc))
        if len(best)!=1:continue
        p,loc=best[0]
        if old and near(old,p["point"])>650:continue
        r["緯度"]=f'{p["point"][1]:.7f}';r["経度"]=f'{p["point"][0]:.7f}'
        r["位置出典"]="国土数値情報C09 漁港位置点(2006)"
        r["位置照合"]="漁港名称と行政区域または既存座標で一致"
        r["照合管理ID"]="C09:"+p["port_code"]
        if loc and (not r["市区町村"] or r["市区町村"]!=loc[1]):
            if not r["市区町村"]:r["市区町村"]=loc[1]
            elif loc[2]=="contained":r["市区町村"]=loc[1]
        matches[r["元CSV行"]]=p["port_code"]
        STATS["official_port_matches"]+=1
    return matches
def apply_city(rows,cityat):
    if not cityat:return
    for r in rows:
        c=coord(r)
        if c is None:continue
        loc=cityat(c)
        if not loc:continue
        p,city,how=loc
        if p!=r["都道府県"]:
            r["監査備考"]="座標と都道府県が不一致（自動修正せず）"
            STATS["coord_pref_mismatches"]+=1
            # Disallow using mismatched coordinate for dedupe.
            r["位置照合"]="都道府県不一致・要確認"
        else:
            r["行政区域照合"]=how
            if not r["市区町村"] and city:
                r["市区町村"]=city
                STATS["municipality_filled"]+=1
            elif r["市区町村"] and city and validatecity(r["市区町村"])!=city and how=="contained":
                r["監査備考"]="市区町村照合相違:既存="+r["市区町村"]+" / 地理ポリゴン="+city
                STATS["municipality_conflicts"]+=1
def set_geom(r,g,kind,url,geokey,geom_out):
    if g is None or g.is_empty:return
    bounds=g.bounds
    r["範囲西端経度"]=f"{bounds[0]:.7f}";r["範囲南端緯度"]=f"{bounds[1]:.7f}"
    r["範囲東端経度"]=f"{bounds[2]:.7f}";r["範囲北端緯度"]=f"{bounds[3]:.7f}"
    r["範囲出典"]=url;r["範囲種別"]=kind;r["範囲ジオメトリID"]=geokey
    geom_out[geokey]={"type":"Feature","properties":{"geometry_id":geokey,"kind":kind,"source_url":url},"geometry":mapping(g)}
def official_geometry(rows,bound,matches,geoout):
    for r in rows:
        id=matches.get(r["元CSV行"])
        if not id or id not in bound:continue
        try:
            g=unary_union(bound[id])
            set_geom(r,g,"漁港区域境界線（2006年度）","https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-C09.html","C09:boundary:"+id,geoout)
            STATS["official_port_line_extents"]+=1
        except Exception as e:ERR.append([id,str(e)[:130]])
OSM_BOXES=[
 (24,122,28.5,129),(27.5,127,32,132),(31,128,35,133),
 (33,130,37,136),(33,134,37,140),(34.8,137.5,39.3,144),
 (37.5,138.5,42,145),(40.5,139.5,46,148),
 (33.8,131,37.4,137),(32.8,133.4,36.7,141),
 (28,129.2,33.3,136),(30,129,34.3,132)
]
def osm_fetch(box):
    s,w,n,e=box
    query=('[out:json][timeout:100];('
      f'nwr["natural"="beach"]["name"]({s},{w},{n},{e});'
      f'nwr["landuse"="beach"]["name"]({s},{w},{n},{e});'
      f'nwr["man_made"~"^(pier|breakwater)$"]["name"]({s},{w},{n},{e});'
      f'nwr["harbour"="yes"]["name"]({s},{w},{n},{e});'
      f'nwr["seamark:type"="harbour"]["name"]({s},{w},{n},{e});'
      ');out body geom;')
    hosts=["https://overpass.kumi.systems/api/interpreter",
           "https://overpass.nchc.org.tw/api/interpreter",
           "https://overpass-api.de/api/interpreter"]
    for host in hosts:
        try:
            r=requests.post(host,data={"data":query},timeout=135,
               headers={"User-Agent":"Personal Japanese fishing location research, OpenStreetMap crossreference/1.0"})
            if r.status_code in (429,504,503):continue
            r.raise_for_status()
            data=r.json()
            if "elements" in data:return data["elements"]
        except Exception as e:ERR.append([host,str(e)[:170]])
    return []
def osm_shapes(cityat):
    if os.getenv("SKIP_OSM")=="1":return []
    allfeatures={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for i,features in enumerate(pool.map(osm_fetch,OSM_BOXES),1):
            print("OSM BOX",i,"FEATURES",len(features),flush=True)
            for f in features:allfeatures[(f["type"],f["id"])]=f
    out=[]
    for f in allfeatures.values():
        tags=f.get("tags") or {};name=tags.get("name:ja") or tags.get("name") or ""
        if not name or len(name)>80:continue
        try:
            p=f.get("geometry")
            if p and len(p)>1:
                xy=[(float(pt["lon"]),float(pt["lat"])) for pt in p]
                if f["type"]=="way" and xy[0]==xy[-1] and len(xy)>=4:
                    g=Polygon(xy)
                    if not g.is_valid:g=g.buffer(0)
                else:g=LineString(xy)
                c=(float(g.representative_point().x),float(g.representative_point().y))
            else:
                c=(float(f["lon"]),float(f["lat"]));g=Point(c)
            if not (122<c[0]<154 and 24<c[1]<47):continue
            loc=cityat(c) if cityat else None
            if not loc:continue
            pref,municip,how=loc
            if pref not in PREFS:continue
            kind="beach" if tags.get("natural")=="beach" or tags.get("landuse")=="beach" else (
                "pier" if "man_made" in tags else "port")
            out.append({"name":name,"root":root(name),"pref":pref,"city":municip,"point":c,
                "g":g,"kind":kind,"id":f"OSM:{f['type']}/{f['id']}","url":f"https://www.openstreetmap.org/{f['type']}/{f['id']}"})
        except Exception as e:ERR.append(["OSM decode",str(e)[:140]])
    print("OSM UNIQUE FEATURES",len(allfeatures),"ASSIGNED",len(out),flush=True)
    STATS["osm_geometries"]=len(out)
    return out
def osm_match(rows,features,cityat,geoout):
    byname=collections.defaultdict(list)
    for f in features:
        if len(f["root"])>=2:byname[(f["pref"],f["root"],f["kind"])].append(f)
    for r in rows:
        type0=typ(r)
        if type0 not in ("beach","port","pier"):continue
        rn=root(r["釣り場名"])
        if len(rn)<2:continue
        items=byname.get((r["都道府県"],rn,type0),[])
        if not items:continue
        existing=coord(r)
        muni=validatecity(r["市区町村"])
        valid=[]
        for f in items:
            city=f["city"]
            if muni and city and not (muni==city or muni.endswith(city) or city.endswith(muni)):
                continue
            if existing and near(existing,f["point"])>650:continue
            valid.append(f)
        if len(valid)!=1:continue
        f=valid[0]
        if not existing:
            r["経度"]=f'{f["point"][0]:.7f}';r["緯度"]=f'{f["point"][1]:.7f}'
            r["位置出典"]=f["url"];r["位置照合"]="OSM正式地物名・市区町村一致"
            STATS["osm_coordinates_filled"]+=1
        if not r["市区町村"] and f["city"]:r["市区町村"]=f["city"]
        if not r["照合管理ID"]:r["照合管理ID"]=f["id"]
        if f["g"].geom_type in ("Polygon","MultiPolygon","LineString","MultiLineString"):
            # Bounding rectangle is metadata for the mapped feature, not an invented fishing-permission area.
            kind="海岸・施設のOSM輪郭" if f["g"].geom_type in ("Polygon","MultiPolygon") else "OSM海岸・施設線分"
            set_geom(r,f["g"],kind,f["url"],f["id"],geoout)
            STATS["osm_extents_filled"]+=1
def normalize_ids(rows):
    for r in rows:
        c=coord(r)
        if c is None:
            r["緯度"]=r["経度"]=""
        if not r["範囲種別"] or r["範囲種別"]=="不明":
            r["範囲種別"]="位置点のみ・区域未確定" if c else "未確認"
        r["座標確認状況"]="座標あり" if c and not r["位置照合"].startswith("都道府県不一致") else "未確認/所在地矛盾"
def dsu_merge(rows):
    # Merge only geometrically corroborated identical facilities, not merely nearby names.
    bykey=collections.defaultdict(list)
    for i,r in enumerate(rows):
        nm=root(r["釣り場名"]);t=typ(r)
        if t not in ("port","beach","pier") or len(nm)<2:continue
        bykey[(r["都道府県"],nm,t)].append(i)
    par=list(range(len(rows)))
    def find(i):
        while par[i]!=i:par[i]=par[par[i]];i=par[i]
        return i
    def join(a,b):
        a=find(a);b=find(b)
        if a!=b:par[b]=a
    evidence={}
    for items in bykey.values():
        for ia in range(len(items)):
            for ib in range(ia+1,len(items)):
                a=items[ia];b=items[ib];x=rows[a];y=rows[b]
                if not x["市区町村"] or not y["市区町村"]:continue
                cx=validatecity(x["市区町村"]);cy=validatecity(y["市区町村"])
                if cx!=cy and not cx.endswith(cy) and not cy.endswith(cx):continue
                p=coord(x);q=coord(y)
                sameID=x["照合管理ID"] and x["照合管理ID"]==y["照合管理ID"]
                if not p or not q:continue
                dst=near(p,q)
                if sameID or (dst<=35 and norm(x["釣り場名"])==norm(y["釣り場名"])):
                    join(a,b);evidence[(a,b)]=("地物ID一致" if sameID else "名称と位置座標35m以内",dst)
    groups=collections.defaultdict(list)
    for i in range(len(rows)):groups[find(i)].append(i)
    master={};to_remove=[];matchlog=[]
    def score(r):
        return (r["出典"]!="国土数値情報C09",bool(r["市区町村"]),
          r["範囲種別"] not in ("不明","位置点のみ・区域未確定","未確認"),
          len(r["住所"]),bool(r["釣果根拠"]))
    for ids in groups.values():
        if len(ids)==1:continue
        keep=max(ids,key=lambda i:score(rows[i]))
        for i in ids:
            if i==keep:continue
            old=rows[i];new=rows[keep]
            to_remove.append(i)
            master[i]=keep
            if not new["範囲ジオメトリID"] and old["範囲ジオメトリID"]:
                for f in ["範囲ジオメトリID","範囲種別","範囲出典","範囲西端経度","範囲南端緯度","範囲東端経度","範囲北端緯度"]:new[f]=old[f]
            new["統合した出典"]=";".join(dict.fromkeys(filter(None,(new["統合した出典"]+";"+old["統合した出典"]).split(";"))))
            new["監査備考"]=(new["監査備考"]+"; 統合元:"+old["元CSV行"]).strip("; ")
            reason="公的漁港コード・OSM地物ID一致" if new["照合管理ID"] and new["照合管理ID"]==old["照合管理ID"] else "同一名称・同一座標35m以内"
            matchlog.append({"残存CSV元行":new["元CSV行"],"削除CSV元行":old["元CSV行"],
                 "地点名":old["釣り場名"],"都道府県":old["都道府県"],"市区町村":old["市区町村"],
                 "統合理由":reason,"共通地物ID":new["照合管理ID"],"削除元URL":old["出典URL"]})
    cleaned=[r for i,r in enumerate(rows) if i not in set(to_remove)]
    print("DEDUP REMOVED",len(to_remove),"RETAIN",len(cleaned),flush=True)
    return cleaned,matchlog
def savecsv(name,rows,cols):
    with open(OUTDIR+"/"+name,"w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,cols,extrasaction="ignore");w.writeheader();w.writerows(rows)
def main():
    data=rowload();print("INPUT",len(data),flush=True)
    ports,bound=load_ports()
    cityat=municipal_shapes()
    matched=enrich_ports(data,ports,bound,cityat)
    apply_city(data,cityat)
    geoms={}
    official_geometry(data,bound,matched,geoms)
    features=osm_shapes(cityat)
    osm_match(data,features,cityat,geoms)
    apply_city(data,cityat)
    normalize_ids(data)
    dedup,removed=dsu_merge(data)
    originals=list(data[0].keys())
    savecsv("all_georeferenced.csv",data,originals)
    savecsv("deduplicated.csv",dedup,originals)
    savecsv("removed_duplicates.csv",removed,
       ["残存CSV元行","削除CSV元行","地点名","都道府県","市区町村","統合理由","共通地物ID","削除元URL"])
    unresolved=[x for x in dedup if x["座標確認状況"]!="座標あり" or x["範囲種別"] in ("不明","位置点のみ・区域未確定","未確認")]
    savecsv("unresolved_geometry.csv",unresolved,originals)
    with open(OUTDIR+"/known_geometries.geojson","w",encoding="utf-8") as f:
        json.dump({"type":"FeatureCollection","features":list(geoms.values())},f,ensure_ascii=False)
    report={"input_count":len(data),"deduplicated_count":len(dedup),"removed_confident":len(removed),
       "with_coordinate":sum(x["座標確認状況"]=="座標あり" for x in dedup),
       "without_coordinate":sum(x["座標確認状況"]!="座標あり" for x in dedup),
       "with_mapped_extent":sum(x["範囲種別"] not in ("不明","位置点のみ・区域未確定","未確認") for x in dedup),
       "without_mapped_extent":sum(x["範囲種別"] in ("不明","位置点のみ・区域未確定","未確認") for x in dedup),
       "geometry_features":len(geoms),"stats":dict(STATS),
       "errors":ERR[:100],
       "notes":["漁港位置データと区域境界は2006年度。区域境界線を閉領域と誤認せず線として保存。",
                "OSMは現況と異なる可能性があり、釣り許可範囲を意味しない。",
                "地図上の実際の境界が取得できないレコードは推測した矩形で補完せず未確認とした。",
                "同名の港と海岸・防波堤は原則別地点として維持。",
                "重複削除は座標付き・行政区域一致・同一地物IDまたは35m以内の厳密な重複に限定。"]}
    with open(OUTDIR+"/georef_dedup_report.json","w",encoding="utf-8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print("RESULT",json.dumps(report,ensure_ascii=False)[:10000],flush=True)
if __name__=="__main__":main()
