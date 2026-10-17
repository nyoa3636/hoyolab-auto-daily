import csv,io,json,math,re,time,zipfile,os
from collections import Counter
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
import shapefile

S=requests.Session()
S.headers.update({"User-Agent":"Mozilla/5.0 (personal location research; contact via linked data sources)"})
PREFS="北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 静岡県 愛知県 三重県 京都府 大阪府 兵庫県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県".split()
STATES="北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県".split()
STATE={f"{i:02d}":p for i,p in enumerate(STATES,1)}
SEA=re.compile("港|堤防|防波堤|護岸|磯|岬|埠頭|ふ頭|波止|桟橋|海岸|サーフ|砂浜|海浜|海釣り|防砂堤|漁港|突堤|干潟|地磯|沖磯|浜辺|漁場")
FRESH=re.compile("渓流|上流|中流|湖|池|沼|ダム|釣り堀|管理釣り場|川|河川|滝|沢|用水")
MUNI=re.compile(r"(?:[一-龠々ヶヵぁ-んァ-ヶー]{1,15}郡)?([一-龠々ヶヵぁ-んァ-ヶー]{1,14}?(?:市[一-龠々ヶヵぁ-んァ-ヶー]{1,10}区|市|町|村|区))")
DATA=[];COUNTS=Counter();ERRORS=[]
def fetch(url,timeout=25):
    for trial in range(2):
        try:
            r=S.get(url,timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            if trial: ERRORS.append([url,str(e)[:150]]);return None
            time.sleep(1)
def municipality(addr,pref):
    s=addr or ""
    if pref in s:s=s.split(pref,1)[-1]
    s=re.sub(r"^〒?\d{3}-?\d{4}\s*","",s)
    m=MUNI.search(s)
    return m.group(1) if m else ""
def add(name,pref,city,typ,addr,url,source,grade,lat="",lon="",note=""):
    if not name or pref not in PREFS:return
    DATA.append({"釣り場名":name.strip(),"都道府県":pref,"市区町村":city,"種別":typ,"住所":addr,"緯度":lat,"経度":lon,"確認区分":grade,"釣り可否":"未確認","出典":source,"出典URL":url,"備考":note})
def codes():
    z=fetch("https://code4fukui.github.io/localgovjp/localgovjp-utf8.csv")
    code={}
    if not z:return code
    rd=csv.DictReader(io.StringIO(z.content.decode("utf-8-sig")))
    print("CODE COLUMN HEADERS",rd.fieldnames,flush=True)
    for row in rd:
        c=str(row.get("lgcode") or row.get("code") or row.get("団体コード") or "").strip()
        name=str(row.get("city") or row.get("name") or row.get("自治体名") or "").strip()
        if len(c)>=5 and name:code[c[:5]]=name
    print("CODE MAP",len(code),flush=True)
    return code
def official(code):
    src="https://nlftp.mlit.go.jp/ksj/gml/data/C09/C09-06/C09-06_GML.zip"
    r=fetch(src,timeout=45)
    if not r or r.content[:2]!=b"PK":
        print("OFFICIAL DATA NOT ACCESSIBLE",flush=True);return
    try:
        z=zipfile.ZipFile(io.BytesIO(r.content))
        fns=z.namelist();shps=[x for x in fns if x.lower().endswith("fishingport.shp")]
        print("OFFICIAL ZIP",len(r.content),shps,flush=True)
        for name in shps:
            stem=name[:-4]
            fs={x.lower():x for x in fns}
            kwargs={"shp":io.BytesIO(z.read(name)),"dbf":io.BytesIO(z.read(fs[(stem+".dbf").lower()])),"encoding":"cp932","encodingErrors":"replace"}
            if (stem+".shx").lower() in fs:kwargs["shx"]=io.BytesIO(z.read(fs[(stem+".shx").lower()]))
            reader=shapefile.Reader(**kwargs)
            fields=[f[0] for f in reader.fields[1:]]
            print("OFFICIAL FIELDS",fields,"RECORDS",len(reader),flush=True)
            for ft in reader.iterShapeRecords():
                d=dict(zip(fields,ft.record))
                c=str(d.get("C09_003") or "").strip().zfill(5)
                p=STATE.get(c[:2],"")
                if not p:continue
                xy=ft.shape.points[0] if ft.shape.points else ["",""]
                add(str(d.get("C09_002") or ""),p,code.get(c,""),"漁港","",src,"国土数値情報C09","漁港候補（釣り未確認）",xy[1],xy[0],"所在地・施設データ2006年")
    except Exception as e:print("OFFICIAL ERROR",repr(e),flush=True)
def tsuriba():
    for i in range(1,48):
        root=f"https://tsuriba.info/spotlist/{i}"
        r=fetch(root)
        if not r:continue
        soup=BeautifulSoup(r.content,"html.parser")
        h=soup.find("h1")
        title=h.get_text(" ",strip=True) if h else ""
        p=next((p for p in PREFS if p.replace("県","").replace("府","").replace("都","") in title),None)
        if not p:continue
        body=soup.get_text(" ",strip=True)
        m=re.search(r"計\s*[:：]?\s*(\d+)",body)
        n=min(30,math.ceil(int(m.group(1))/20)) if m else 1
        print("TSURIBA",i,p,n,flush=True)
        for j in range(1,n+1):
            if j>1:
                time.sleep(.4)
                rr=fetch(root+f"?page={j}")
                if not rr:continue
                soup=BeautifulSoup(rr.content,"html.parser")
            count=0
            for a in soup.select('a[href*="/spot/"]'):
                name=a.get_text(" ",strip=True);url=urljoin(root,a.get("href",""))
                if not re.search(r"/spot/\d+",url) or not name or len(name)>60:continue
                ctx=""
                pp=a
                for z in range(5):
                    pp=pp.parent
                    if not pp:break
                    t=pp.get_text(" ",strip=True)
                    if p in t and len(t)<900:
                        ctx=t;break
                if not ctx:
                    ctx=a.parent.parent.get_text(" ",strip=True)[:350] if a.parent and a.parent.parent else ""
                match=re.search(re.escape(p)+r".{0,65}",ctx)
                addr=match.group(0).split("。")[0][:80] if match else ""
                city=municipality(addr,p)
                add(name,p,city,"海釣り場",addr,url,"海の釣り場情報","釣り場サイト掲載")
                count+=1
            if j%8==0:print("LIST PAGE",p,j,"rows",len(DATA),flush=True)
            COUNTS["tsuriba_seen"]+=count
def tsurispot():
    url="https://tsurispot.com/sitemap-page"
    r=fetch(url,90)
    if not r:return
    soup=BeautifulSoup(r.content,"html.parser")
    in_spots=False;pref="";seen=0
    pr=re.compile("|".join(sorted(PREFS,key=len,reverse=True)))
    for el in soup.find_all(["h2","h3","a"]):
        if el.name=="h2":in_spots="釣りスポット（都道府県別）" in el.get_text(" ",strip=True);continue
        if not in_spots:continue
        if el.name=="h3":
            m=pr.search(el.get_text(" ",strip=True));pref=m.group(0) if m else "";continue
        href=el.get("href","")
        if el.name!="a" or pref not in PREFS or "/spot/" not in href:continue
        name=el.get_text(" ",strip=True)
        if not name:continue
        seen+=1
        if FRESH.search(name) and not SEA.search(name):continue
        if not SEA.search(name):continue
        add(name,pref,"","名称から判定した海釣り場候補","",urljoin(url,href),"ツリスポ","海釣り推定（要確認）")
    print("TSURISPOT SITEMAP",seen,"SEA",COUNTS["tsurispot_sea"],"rows",len(DATA),flush=True)
def merge():
    ordered={"海の釣り場情報":0,"国土数値情報C09":1,"ツリスポ":2}
    out={}
    def norm(x):return re.sub(r"[\s　・･（）()\[\]【】／/~\-]+","",x.replace("ヶ","ケ"))
    for r in sorted(DATA,key=lambda x:(not bool(x["市区町村"]),not bool(x["緯度"]),ordered.get(x["出典"],9))):
        key=(r["都道府県"],norm(r["釣り場名"]))
        if key not in out:
            out[key]=r.copy();out[key]["統合した出典"]=r["出典"];continue
        a=out[key]
        if r["出典"] not in a["統合した出典"]:a["統合した出典"]+=";"+r["出典"]
        if not a["市区町村"] and r["市区町村"]:a["市区町村"]=r["市区町村"]
        if not a["緯度"] and r["緯度"]:
            a["緯度"]=r["緯度"];a["経度"]=r["経度"]
    records=sorted(out.values(),key=lambda x:(PREFS.index(x["都道府県"]),x["市区町村"],x["釣り場名"]))
    os.makedirs("output",exist_ok=True)
    if not records:raise RuntimeError("No records")
    with open("output/national_sea_fishing_sites.csv","w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=records[0].keys());w.writeheader();w.writerows(records)
    report={"unique":len(records),"total_source_entries":len(DATA),"municipality_filled":sum(bool(x["市区町村"]) for x in records),"prefectures":len(set(x["都道府県"] for x in records)),"by_source":dict(Counter(x["出典"] for x in records)),"by_prefecture":dict(Counter(x["都道府県"] for x in records)),"errors":ERRORS[:80],"counts":dict(COUNTS)}
    with open("output/research_report.json","w",encoding="utf-8") as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print("FINAL_REPORT",json.dumps(report,ensure_ascii=False),flush=True)
if __name__=="__main__":
    code=codes()
    official(code)
    tsuriba()
    tsurispot()
    merge()
