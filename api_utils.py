import os
import requests
import pandas as pd
import datetime
import logging
import urllib3
import re
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.WARNING, format="%(message)s")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://kind.krx.co.kr/disclosure/todaydisclosure.do",
}

def get_nextrade_filtered_symbols(trdDd: str):
    year, month = trdDd[:4], trdDd[4:6]
    cache_dir = f"data/{year}/{month}"
    cache_path = f"{cache_dir}/{trdDd}.csv"

    if os.path.exists(cache_path):
        try: return "Local Cache", pd.read_csv(cache_path, dtype={'단축코드': str})
        except: pass

    url = "https://www.nextrade.co.kr/brdinfoTime/brdinfoTimeList.do"
    payload = {"_search": "false", "nd": str(int(pd.Timestamp.now().timestamp() * 1000)), "pageUnit": "900", "pageIndex": "1", "scAggDd": trdDd}
    try:
        resp = requests.post(url, data=payload, verify=False, timeout=15)
        js = resp.json()
        items = js.get("brdinfoTimeList", [])
        if not items: return "N/A", pd.DataFrame(columns=["시장구분", "표준코드", "단축코드", "종목명", "거래불가사유"])
        
        df = pd.DataFrame(items)
        df = df[['mktNm', 'isuCd', 'isuSrdCd', 'isuAbwdNm', 'trdIpsbRsn']].copy()
        df.columns = ["시장구분", "표준코드", "단축코드", "종목명", "거래불가사유"]
        df["단축코드"] = df["단축코드"].str[1:]
        os.makedirs(cache_dir, exist_ok=True)
        df.to_csv(cache_path, index=False, encoding="utf-8-sig")
        return js.get("setTime", "N/A"), df
    except:
        return "N/A", pd.DataFrame(columns=["시장구분", "표준코드", "단축코드", "종목명", "거래불가사유"])

def _parse_kind_html(html_text):
    if not html_text: return pd.DataFrame()
    soup = BeautifulSoup(html_text, "lxml")
    table = soup.find("table", {"class": "list type-01"})
    if not table: return pd.DataFrame()
    
    rows = []
    curr_year = datetime.date.today().year
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 5: continue
        time_raw = tds[0].text.strip()
        company = tds[1].text.strip()
        title_td = tds[2]
        title = title_td.text.strip()
        onclick = title_td.find("a").get("onclick", "") if title_td.find("a") else ""
        doc_no = re.search(r"openDisclosure\('(\d+)'", onclick)
        v_url = f"https://kind.krx.co.kr/common/disclsview.do?method=search&docno={doc_no.group(1)}#{title}" if doc_no else title
        
        rows.append({
            "시간": f"{curr_year}-{time_raw}",
            "회사명": company, "공시제목": title, "뷰어URL": v_url, "문서번호": doc_no.group(1) if doc_no else ""
        })
    return pd.DataFrame(rows)

def kind_fetch(category_arg, f, t, page_size=100):
    cat_map = {"halt": "1", "mgmt": "2", "alert": "3", "misc": "6"}
    p = {"method": "searchTodayDisclosureSub", "currentPageSize": page_size, "forward": "todaydisclosure_sub", 
         "searchDisclsType": cat_map.get(category_arg, "1"), "fromDate": f, "toDate": t}
    return _parse_kind_html(requests.post("https://kind.krx.co.kr/disclosure/todaydisclosure.do", data=p, headers=HEADERS).text)

def fetch_investor_warning(f, t, page_size=100):
    p = {"method": "searchTodayDisclosureSub", "currentPageSize": page_size, "forward": "todaydisclosure_sub", "searchDisclsType": "4", "fromDate": f, "toDate": t}
    return _parse_kind_html(requests.post("https://kind.krx.co.kr/disclosure/todaydisclosure.do", data=p, headers=HEADERS).text)

def fetch_shortterm_overheat(f, t, page_size=100):
    p = {"method": "searchTodayDisclosureSub", "currentPageSize": page_size, "forward": "todaydisclosure_sub", "searchDisclsType": "5", "fromDate": f, "toDate": t}
    return _parse_kind_html(requests.post("https://kind.krx.co.kr/disclosure/todaydisclosure.do", data=p, headers=HEADERS).text)

def fetch_market_watch(f, t, page_size=100):
    p = {"method": "searchTodayDisclosureSub", "currentPageSize": page_size, "forward": "todaydisclosure_sub", "reportCd": "1", "fromDate": f, "toDate": t}
    return _parse_kind_html(requests.post("https://kind.krx.co.kr/disclosure/todaydisclosure.do", data=p, headers=HEADERS).text)
