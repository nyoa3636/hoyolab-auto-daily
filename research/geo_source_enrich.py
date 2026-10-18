#!/usr/bin/env python3
"""Extract exact page-provided map coordinates for fishing sites, without location guessing."""
import csv,io,os,time,json,re,collections,threading,concurrent.futures,sys
from urllib.parse import urlparse,parse_qs,unquote
from bs4 import BeautifulSoup
import requests
sys.path.insert(0,"research")
import georeference_dedupe as G

BASE="output/geo/deduplicated.csv"
OUTDIR="output/geo"
lock=threading.Lock()
last_req=collections.defaultdict(float)
rate_failure=collections.Counter()
stop=set()
STATS=collections.Counter()
FAIL=[]
BADS=[]
def norm(s):return G.norm(s)
def get(url):
    domain=urlparse(url).netloc
    if domain in stop:return None,"source_rate_limited"
    delay={"tsuriba.info":.45,"tsurispot.com":1.5,"fishcast.jp":.6}.get(domain,1.0)
    with lock:
        now=time.monotonic()
        dt=last_req[domain]+delay-now
        if dt>0:time.sleep(dt)
        last_req[domain]=time.monotonic()
    try:
        rr=requests.get(url,timeout=26,headers={"User-Agent":"Mozilla/5.0 (personal Japanese sea fishing location study)"})
        if rr.status_code in (403,429):
            with lock:
                rate_failure[domain]+=1
                if rate_failure[domain]>=3:stop.add(domain)
            return None,"HTTP:"+str(rr.status_code)
        rr.raise_for_status()
        return rr,""
    except Exception as e:return None,str(e)[:160]
def valid(lat,lon):
    try:
        lat=float(lat);lon=float(lon)
        if 24<=lat<=46.5 and 122<=lon<=155:return (lon,lat)
    except (ValueError,TypeError):pass
    return None
def tsuriba(html):
    soup=BeautifulSoup(html,"html.parser")
    out=[]
    for tag in soup.find_all("iframe"):
        link=(tag.get("src","")+" "+tag.get("data-src",""))
        if not ("google.com/maps/embed/v1/place" in link):continue
        m=re.search(r"[?&]q=(-?[0-9]{2}\.[0-9]+),(-?[0-9]{3}\.[0-9]+)",link)
        if not m:continue
        loc=valid(m.group(1),m.group(2))
        if loc:out.append(loc)
    if len(set(out))==1:return out[0],"Google Maps埋込URL（掲載元指定座標）"
    return None,"iframe地図に一意な位置なし"
def walk(o,arr):
    if isinstance(o,dict):
        geo=o.get("geo") or o.get("location")
        if isinstance(geo,dict):
            c=valid(geo.get("latitude"),geo.get("longitude"))
            if c:arr.append((o.get("name",""),c))
        for v in o.values():walk(v,arr)
    elif isinstance(o,list):
        for v in o:walk(v,arr)
def tsurispot(html,target):
    soup=BeautifulSoup(html,"html.parser")
    cand=[]
    for s in soup.select('script[type="application/ld+json"]'):
        try:walk(json.loads(s.string or s.get_text()),cand)
        except (json.JSONDecodeError,TypeError):continue
    good=[c for nm,c in cand if norm(target) in norm(nm) or norm(nm) in norm(target)]
    unique=list(dict.fromkeys(good))
    if len(unique)==1:return unique[0],"ページ固有JSON-LD GeoCoordinates"
    coords=list(dict.fromkeys(c for nm,c in cand))
    if len(coords)==1:return coords[0],"ページのJSON-LD GeoCoordinates"
    # Page's own Google Maps directions URI is an independently checkable source.
    out=[]
    for a in soup.select('a[href*="google.com/maps/dir/"]'):
        q=parse_qs(urlparse(a.get("href","")).query)
        for dst in q.get("destination",[]):
            m=re.fullmatch(r"\s*(-?[0-9.]+)\s*,\s*(-?[0-9.]+)\s*",dst)
            if m:
                c=valid(m.group(1),m.group(2))
                if c:out.append(c)
    if len(set(out))==1:return out[0],"地図経路リンクのdestination"
    return None,"個別地点の座標抽出不可"
def fishcast(html,target):
    x,source=tsurispot(html,target)
    if x:return x,source
    soup=BeautifulSoup(html,"html.parser")
    out=[]
    for a in soup.select('a[href*="google.com/maps"]'):
        u=a.get("href","")
        m=re.search(r"(?:q=|query=|destination=)(-?\d{2}\.\d+),(-?\d{3}\.\d+)",unquote(u))
        if m:
            c=valid(m.group(1),m.group(2))
            if c:out.append(c)
    if len(set(out))==1:return out[0],"個別ページの地図位置"
    return None,"該当ページ座標なし"
def obtain(r):
    link=r.get("出典URL") or ""
    if not link.startswith("https://"):return (r,None,"未対応URL")
    domain=urlparse(link).netloc
    if domain not in ("tsuriba.info","tsurispot.com","fishcast.jp"):return (r,None,"URL対象外")
    h,err=get(link)
    if not h:return (r,None,err)
    if domain=="tsuriba.info":c,note=tsuriba(h.text)
    elif domain=="tsurispot.com":c,note=tsurispot(h.text,r["釣り場名"])
    else:c,note=fishcast(h.text,r["釣り場名"])
    return (r,c,note)
def main():
    with open(BASE,encoding="utf-8-sig",newline="") as f:rows=list(csv.DictReader(f))
    missing=[r for r in rows if G.coord(r) is None]
    bydomain=collections.Counter(urlparse(r.get("出典URL") or "").netloc for r in missing)
    print("SOURCE BASE",len(rows),"MISSING",len(missing),"BYDOMAIN",dict(bydomain),flush=True)
    logs=[];accepted=0
    # Do not query already geocoded records.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for i,(r,c,note) in enumerate(pool.map(obtain,missing),1):
            if c:
                lon,lat=c
                r["緯度"]=f"{lat:.7f}";r["経度"]=f"{lon:.7f}"
                r["位置出典"]=r["出典URL"]
                r["位置照合"]="掲載元の地図座標を取得"
                accepted+=1
                status="座標取得"
            else:
                status="未取得"
            STATS[status]+=1
            if status=="未取得":
                STATS["missing:"+note[:70]]+=1
            logs.append({"元CSV行":r["元CSV行"],"釣り場名":r["釣り場名"],"都道府県":r["都道府県"],
              "掲載元":r["出典"],"URL":r["出典URL"],"抽出結果":status,"理由":note,
              "緯度":r["緯度"],"経度":r["経度"]})
            if i%150==0:print("SOURCE CHECK",i,"/",len(missing),"NEW",accepted,"STOPPED",list(stop),flush=True)
    # Recheck all location coordinates against polygonal administrative regions.
    cities=G.municipal_shapes()
    G.apply_city(rows,cities)
    G.normalize_ids(rows)
    cleaned,removed=G.dsu_merge(rows)
    flds=list(rows[0].keys())
    G.savecsv("source_enriched_all.csv",rows,flds)
    G.savecsv("source_enriched_deduplicated.csv",cleaned,flds)
    G.savecsv("source_enrichment_trace.csv",logs,["元CSV行","釣り場名","都道府県","掲載元","URL","抽出結果","理由","緯度","経度"])
    G.savecsv("source_extra_duplicates.csv",removed,
       ["残存CSV元行","削除CSV元行","地点名","都道府県","市区町村","統合理由","共通地物ID","削除元URL"])
    report={"input":len(rows),"original_coordinate_missing":len(missing),
     "new_page_coordinates":accepted,"updated_row_count":len(cleaned),
     "source_extra_duplicates":len(removed),
     "remaining_without_coordinate":sum(G.coord(r) is None for r in cleaned),
     "with_coordinate":sum(G.coord(r) is not None for r in cleaned),
     "with_mapped_extent":sum(r["範囲種別"] not in ("不明","位置点のみ・区域未確定","未確認") for r in cleaned),
     "source_counts":dict(bydomain),"statuses":dict(STATS),"rate_limited_domains":list(stop),
     "geo_polygon_statistics":dict(G.STATS),"notes":[
       "個別サイトのGoogle Maps埋込座標または構造化データのみ採用し、推定市町村中心は使用しない。",
       "座標は地点の代表点であり、釣り可能範囲とは異なる。",
       "座標・範囲が未収録の場合は未確認のままにしている。",
       "釣り許可は別途管理者等の最新案内が必要。"]}
    with open(OUTDIR+"/source_enrichment_report.json","w",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
    print("SOURCE FINAL",json.dumps(report,ensure_ascii=False)[:5000],flush=True)
if __name__=="__main__":main()
