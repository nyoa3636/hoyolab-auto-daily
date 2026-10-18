#!/usr/bin/env python3
# Source-based nationwide beach/surf fishing research, 2026-10-18
import csv, io, json, os, re, time, concurrent.futures, math, collections, threading
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import requests

BASE="output/national_sea_fishing_sites.csv"
OUT="output/national_sea_fishing_sites_surf_expanded.csv"
SURF="output/surf_beach_sites.csv"
REPORT="output/surf_research_report.json"
PREFS="北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 静岡県 愛知県 三重県 京都府 大阪府 兵庫県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県".split()
COAST=re.compile(r"海岸|サーフ|砂浜|海水浴場|浜辺|ビーチ|砂丘|海浜|(?<!河)(?<!湖)(?<!漁)(?<!波)浜(?:$|[（(・])|(?<!河)(?<!湖)(?<!漁)(?<!波)浜$")
INLAND=re.compile(r"湖|池|川|沼|ダム|釣り堀|河川|海中公園")
MUNI=re.compile(r"(?:[一-龠々ヶヵぁ-んァ-ヶー]{1,12}郡)?([一-龠々ヶヵぁ-んァ-ヶー]{1,14}?(?:市[一-龠々ヶヵぁ-んァ-ヶー]{1,8}区|市|町|村|区))")
FIELDS=["釣り場名","都道府県","市区町村","種別","住所","緯度","経度","確認区分","釣り可否","出典","出典URL","備考","統合した出典","サーフ重点","釣果根拠","根拠URL","所在地確認レベル"]
S=requests.Session();S.headers.update({"User-Agent":"Personal research on Japanese fishing locations (contact site admin for data policy)"})
lock=threading.Lock()
STATS=collections.Counter();ERRORS=[];EXTRAS=[];ANG=[]

def log(*a):print(*a,flush=True)
def get(url,timeout=35):
    for attempt in range(2):
        try:
            r=S.get(url,timeout=timeout)
            r.raise_for_status()
            if len(r.content)<120:return None
            return r
        except Exception as e:
            if attempt:
                with lock:ERRORS.append([url,str(e)[:180]])
            else:time.sleep(.8)
    return None

def norm(x):
    x=re.sub(r"[\s　・･（）()\[\]【】／/~\-]+","",str(x or ""))
    return x.replace("ヶ","ケ").replace("髙","高")
def classify(name):
    return bool(COAST.search(name)) and not (INLAND.search(name) and not re.search(r"海岸|サーフ|海浜",name))
def extract_address(text,pref):
    # Restrict address extraction to labelled location fields.
    for pat in [
      r"(?:所在地|住所|アクセス住所)\s*[:：]\s*(?:〒\d{3}-?\d{4}\s*)?("+re.escape(pref)+r"[^\s。、。]{2,45})",
      r"(?:〒\d{3}-?\d{4}\s*)("+re.escape(pref)+r"[^\s。、。]{2,45})"]:
        m=re.search(pat,text)
        if m:return m.group(1)
    return ""
def city_for(addr,pref):
    if not addr:return ""
    t=addr.split(pref,1)[-1] if pref in addr else addr
    m=MUNI.search(t)
    return m.group(1) if m else ""
def city_clean(n):
    n=n or ""
    # Omit obviously malformed municipal readings extracted from description.
    if len(n)>18:return ""
    return n
def extract_coords(html):
    pats=[
      (r'"latitude"\s*:\s*([\-0-9.]+)',r'"longitude"\s*:\s*([\-0-9.]+)'),
      (r'"lat"\s*:\s*([\-0-9.]+)',r'"lng"\s*:\s*([\-0-9.]+)'),
      (r'["'+"'" + r']latitude["'+"'" + r']\s*:\s*([\-0-9.]+)',r'["'+"'" + r']longitude["'+"'" + r']\s*:\s*([\-0-9.]+)'),
    ]
    for p,q in pats:
        a,b=re.search(p,html),re.search(q,html)
        if a and b:
            try:
                lat,lon=float(a.group(1)),float(b.group(1))
                if 24<lat<47 and 122<lon<155:return str(lat),str(lon)
            except ValueError:pass
    return "",""
def make(name,pref,city="",typ="サーフ・海岸",address="",url="",source="",grade="",lat="",lon="",catch="",note=""):
    if not name or pref not in PREFS:return None
    return {"釣り場名":name.strip(),"都道府県":pref,"市区町村":city_clean(city),
      "種別":typ,"住所":address,"緯度":lat,"経度":lon,"確認区分":grade,
      "釣り可否":"要現地確認","出典":source,"出典URL":url,"備考":note,
      "統合した出典":source,"サーフ重点":"はい","釣果根拠":catch,"根拠URL":url if catch else "",
      "所在地確認レベル":"住所・地点照合済" if city else ("都道府県のみ" if pref else "不明")}
def initial():
    with open(BASE,encoding="utf-8-sig",newline="") as f:
        original=list(csv.DictReader(f))
    for r in original:
        r["サーフ重点"]="はい" if classify(r["釣り場名"]) else ""
        r["釣果根拠"]=""
        r["根拠URL"]=""
        r["所在地確認レベル"]="住所・地点照合済" if r["市区町村"] else "都道府県のみ"
    log("BASE ROWS",len(original),"BASE SURF",sum(bool(r["サーフ重点"]) for r in original))
    return original

def tsurispot_candidates():
    u="https://tsurispot.com/sitemap-page"
    r=get(u,90)
    if not r:return []
    soup=BeautifulSoup(r.text,"html.parser")
    headings=soup.find_all(["h2","h3","a"])
    collecting=False;pref="";found={};all_sites=0
    for e in headings:
        if e.name=="h2":
            collecting="釣りスポット" in e.get_text(" ",strip=True) and "都道府県別" in e.get_text(" ",strip=True)
            continue
        if not collecting:continue
        if e.name=="h3":
            title=e.get_text(" ",strip=True)
            pref=next((p for p in PREFS if p in title),"")
            continue
        href=e.get("href","")
        if e.name!="a" or not pref or "/spots/" not in href:continue
        all_sites+=1
        name=e.get_text(" ",strip=True)
        if classify(name):found[(pref,norm(name))]=(name,pref,urljoin(u,href))
    STATS["tsurispot_sitemap_entries"]=all_sites
    STATS["tsurispot_beach_matches"]=len(found)
    log("TSURISPOT ALL",all_sites,"BEACH LIST",len(found))
    return list(found.values())
def enrich_tsurispot(x):
    name,pref,url=x
    rr=get(url,22)
    if not rr:return make(name,pref,url=url,source="ツリスポ",grade="海岸釣り場として掲載（詳細未取得）")
    soup=BeautifulSoup(rr.text,"html.parser")
    txt=soup.get_text(" ",strip=True)
    addr=extract_address(txt,pref)
    city=city_for(addr,pref)
    lat,lon=extract_coords(rr.text)
    return make(name,pref,city,address=addr,url=url,source="ツリスポ",grade="海岸釣り場として掲載",lat=lat,lon=lon)

def fishcast_candidates():
    u="https://fishcast.jp/spots"
    r=get(u,90)
    if not r:return []
    soup=BeautifulSoup(r.text,"html.parser")
    found={}
    for a in soup.select('a[href^="/spots/"]'):
        href=a.get("href","")
        if len(href.strip("/").split("/"))!=2:continue
        name=a.get_text(" ",strip=True)
        if not name or not classify(name):continue
        found[href]=(name,urljoin(u,href))
    log("FISHCAST BEACH LIST",len(found));STATS["fishcast_beach_links"]=len(found)
    return list(found.values())
def enrich_fishcast(x):
    name,url=x
    r=get(url,23)
    if not r:return None
    soup=BeautifulSoup(r.text,"html.parser")
    text=soup.get_text(" ",strip=True)
    # Ex: 神奈川県藤沢市の辻堂海岸
    h=soup.find("h1");heading=h.get_text(" ",strip=True) if h else ""
    p=next((x for x in PREFS if x in text[:1800]),"")
    if not p:
        m=re.search("|".join(PREFS),text)
        p=m.group(0) if m else ""
    if not p:return None
    addr=extract_address(text,p)
    city=city_for(addr,p)
    if not city:
        m=re.search(re.escape(p)+r"([^\s。、]{1,14}?(?:市|町|村|区))",text[:1400])
        if m:city=m.group(1)
    lat,lon=extract_coords(r.text)
    return make(name,p,city,address=addr,url=url,source="FishCast",grade="海岸釣り場として掲載",lat=lat,lon=lon,note="釣り可否・制限は個別ページと現地掲示を参照")

# Hand-checked web-indexed ANGLERS area pages with at least one fishing report.
# These are corroboration of historical fishing activity, NOT proof of current legal access.
ANG_INDEX=[
 ("阿字ヶ浦海岸","茨城県","ひたちなか市",3447),
 ("河原子海岸","茨城県","日立市",5448),
 ("波立海岸","福島県","いわき市",5343),
 ("萱浜海岸","福島県","南相馬市",4252),
 ("平砂浦海岸","千葉県","館山市",653),
 ("太海海岸","千葉県","鴨川市",3845),
 ("三島海岸","千葉県","南房総市",3846),
 ("青塚海岸","千葉県","銚子市",3852),
 ("県北サーフ","宮城県","",3989),
 ("県南サーフ","宮城県","",3988),
 ("仙台サーフ","宮城県","仙台市",3986),
 ("小田原サーフ","神奈川県","小田原市",4),
 ("平塚サーフ","神奈川県","平塚市",32),
 ("舞阪サーフ","静岡県","浜松市",3831),
 ("鮫島海岸","静岡県","磐田市",2283),
 ("浜野海岸","静岡県","掛川市",3155),
 ("伊良湖サーフ","愛知県","田原市",3151),
]
def anglers():
    out=[]
    for name,p,c,i in ANG_INDEX:
        u=f"https://anglers.jp/areas/{i}"
        out.append(make(name,p,c,url=u,source="アングラーズ（検索索引照合）",
          grade="釣果投稿を確認（釣り許可は未確認）",catch="釣果投稿あり",note="検索索引で釣果を確認。現地釣り可能区域とは別"))
    STATS["anglers_indexed"]=len(out);return out

# OpenStreetMap natural=beach: coast candidates only, NOT evidence of fishing permission.
# Split queries to respect Overpass resources; bboxes include archipelagos.
BBOX=[
 (24,122,28.2,128),(26.5,127.5,31.7,132.5),(31.5,128,35,134),
 (33.5,132,36.8,138),(34.5,136,39,143),(37.5,139,41.8,143),
 (40.5,139,45.8,146),(33.5,128,36.5,133),(33.5,134,36,137),
 (34.2,137.0,37.9,141),(30.5,129.5,34,136),(27.5,128,33.2,131)
]
def osm_fetch_box(bounds):
    south,west,north,east=bounds
    q=f'[out:json][timeout:95];nwr["natural"="beach"]["name"]({south},{west},{north},{east});out center tags;'
    for host in ["https://overpass.kumi.systems/api/interpreter","https://overpass.nchc.org.tw/api/interpreter","https://overpass-api.de/api/interpreter"]:
        try:
            r=S.post(host,data={"data":q},timeout=115)
            if r.status_code==200 and r.text.startswith("{"):
                return r.json().get("elements",[])
        except Exception as e:
            with lock:ERRORS.append([host,str(e)[:130]])
    return []
def osm_beaches():
    # Need municipal boundaries to prevent lake and inland beaches from being mislabelled as sea.
    # Shortlist names by Japanese name; detailed geolocation enrichment is resolved by admin areas.
    gathered={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for results in pool.map(osm_fetch_box,BBOX):
            for el in results:
                key=(el.get("type"),el.get("id"))
                gathered[key]=el
    STATS["osm_named_beach_features"]=len(gathered)
    log("OSM RAW BEACHES",len(gathered))
    return gathered

def municipality_lookup(elements):
    if not elements:return []
    try:
        from shapely.geometry import shape, Point
        from shapely.strtree import STRtree
    except ImportError:
        log("NO SHAPELY");return []
    # municipal polygons from national topography dataset, simplified, 2021 geometry.
    polys=[];attrs=[]
    for no in range(1,48):
        fn=f"N03-21_{no:02d}_210101.json"
        url="https://raw.githubusercontent.com/smartnews-smri/japan-topography/main/data/municipality/geojson/s0010/"+fn
        r=get(url,35)
        if not r:continue
        try:
            ff=r.json()["features"]
            for feature in ff:
                pp=feature.get("properties",{})
                pname=pp.get("N03_001") or pp.get("prefecture") or ""
                cname=pp.get("N03_004") or pp.get("city") or ""
                if pname not in PREFS:continue
                g=shape(feature["geometry"])
                if g.is_empty:continue
                polys.append(g);attrs.append((pname,cname))
        except Exception as e:
            ERRORS.append([url,"parse "+str(e)[:100]])
    log("MUNICIPAL POLYGONS",len(polys))
    if not polys:return []
    tree=STRtree(polys)
    # Works with Shapely 1.8 (geometry returns) and 2 (index returns).
    ids={id(g):i for i,g in enumerate(polys)}
    def locate(lon,lat):
        pt=Point(lon,lat)
        matches=tree.query(pt)
        for x in matches:
            i=int(x) if not hasattr(x,"geom_type") else ids.get(id(x),-1)
            if i<0:continue
            if polys[i].covers(pt):return attrs[i]
        # Sea-side OSM nodes could fall just outside municipal land polygon
        return None
    out=[]
    for el in elements.values():
        tags=el.get("tags",{})
        name=tags.get("name:ja") or tags.get("name") or ""
        if not name or len(name)>60:continue
        center=el.get("center") or el
        try:
            lat,lon=float(center["lat"]),float(center["lon"])
        except (KeyError,TypeError,ValueError):continue
        location=locate(lon,lat)
        if not location:continue
        pref,city=location
        if not pref or not city:continue
        u=f"https://www.openstreetmap.org/{el['type']}/{el['id']}"
        obj=make(name,pref,city,typ="地理的海岸候補（自然砂浜）",url=u,source="OpenStreetMap",
          grade="地理的海岸候補（釣り実績未確認）",lat=str(lat),lon=str(lon),
          note="自然地物 natural=beach。内水面・遊泳専用・立入禁止の可能性あり。釣り可能とは断定できない")
        if obj:out.append(obj)
    STATS["osm_municipality_joined"]=len(out)
    log("OSM GEOLOCATED",len(out))
    return out

def merge(base,new):
    grouped={}
    for r in base:
        key=(r["都道府県"],norm(r["釣り場名"]))
        grouped[key]=r.copy()
    nnew=0
    for r in new:
        if not r:continue
        key=(r["都道府県"],norm(r["釣り場名"]))
        if key not in grouped:
            grouped[key]=r.copy();nnew+=1
        else:
            old=grouped[key]
            old["サーフ重点"]="はい"
            old["統合した出典"]=";".join(dict.fromkeys(filter(None,(old.get("統合した出典","")+";"+r["出典"]).split(";"))))
            if r.get("釣果根拠") and not old.get("釣果根拠"):
                old["釣果根拠"]=r["釣果根拠"];old["根拠URL"]=r["根拠URL"]
            if not old.get("市区町村") and r.get("市区町村"):
                old["市区町村"]=r["市区町村"];old["所在地確認レベル"]="住所・地点照合済"
            if not old.get("緯度") and r.get("緯度"):
                old["緯度"]=r["緯度"];old["経度"]=r["経度"]
            if "漁港候補" in old.get("確認区分","") and "釣り場" in r.get("確認区分",""):
                old["確認区分"]=r["確認区分"]
                old["釣果根拠"]=r.get("釣果根拠") or old.get("釣果根拠","")
    rows=list(grouped.values())
    rows.sort(key=lambda x:(PREFS.index(x["都道府県"]),not bool(x.get("サーフ重点")),x.get("市区町村",""),x["釣り場名"]))
    os.makedirs("output",exist_ok=True)
    def write(path,rs):
        with open(path,"w",encoding="utf-8-sig",newline="") as f:
            w=csv.DictWriter(f,FIELDS,extrasaction="ignore");w.writeheader();w.writerows(rs)
    write(OUT,rows)
    sea=[r for r in rows if r.get("サーフ重点")]
    write(SURF,sea)
    report={"all_sites":len(rows),"new_sites_added":nnew,"surf_coast_total":len(sea),
      "beach_with_fishing_report":sum(bool(r.get("釣果根拠")) for r in sea),
      "surf_with_municipality":sum(bool(r.get("市区町村")) for r in sea),
      "surf_missing_municipality":sum(not bool(r.get("市区町村")) for r in sea),
      "surf_by_prefecture":dict(collections.Counter(r["都道府県"] for r in sea)),
      "source_presence":dict(collections.Counter(src for r in sea for src in set(r.get("統合した出典","").split(";")) if src)),
      "stats":dict(STATS),"errors":ERRORS[:70],
      "caveats":["釣果投稿や紹介記事は現時点の釣り許可を保証しない",
                 "OSMのnatural=beachは釣り場ではなく地理的候補",
                 "名称・都道府県で重複除去。同名別地点は統合される可能性がある",
                 "地理データの市区町村は2021年版を利用。改編後の市町村は要再検証"]}
    with open(REPORT,"w",encoding="utf-8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
    log("FINAL",json.dumps(report,ensure_ascii=False)[:7000])

if __name__=="__main__":
    original=initial()
    a=tsurispot_candidates()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        t=list(pool.map(enrich_tsurispot,a))
    log("TSURISPOT ENRICHED",len(t),"CITY",sum(bool(x["市区町村"]) for x in t if x))
    b=fishcast_candidates()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        fc=list(pool.map(enrich_fishcast,b))
    log("FISHCAST ENRICHED",len(fc),"VALID",sum(x is not None for x in fc))
    ang=anglers()
    osm=osm_beaches()
    osm2=municipality_lookup(osm)
    merge(original,t+fc+ang+osm2)
