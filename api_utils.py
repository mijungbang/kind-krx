import os
import requests
import pandas as pd
import datetime
import logging
import urllib3
import re
from bs4 import BeautifulSoup

# SSL 경고 및 로그 설정
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.WARNING, format="%(message)s")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://kind.krx.co.kr/disclosure/todaydisclosure.do",
    "Origin": "https://kind.krx.co.kr",
}

# ─────────────────────────────────────────────────────────────
# 1. NXT 종목 조회 (로컬 CSV 캐시 엔진)
# ─────────────────────────────────────────────────────────────
def get_nextrade_filtered_symbols(trdDd: str):
    year, month = trdDd[:4], trdDd[4:6]
    cache_dir = f"data/{year}/{month}"
    cache_path = f"{cache_dir}/{trdDd}.csv"

    if os.path.exists(cache_path):
        try:
            df_cached = pd.read_csv(cache_path, dtype={'단축코드': str})
            return "Local Cache", df_cached
        except: pass

    url = "https://www.nextrade.co.kr/brdinfoTime/brdinfoTimeList.do"
    payload = {
        "_search": "false",
        "nd": str(int(pd.Timestamp.now().timestamp() * 1000)),
        "pageUnit": "900", "pageIndex": "1", "scAggDd": trdDd,
    }
    try:
        resp = requests.post(url, headers=HEADERS, data=payload, verify=False, timeout=15)
        js = resp.json()
        items = js.get("brdinfoTimeList", [])
        if not items: return "N/A", pd.DataFrame()
        
        df = pd.DataFrame(items)
        df = df[['mktNm', 'isuCd', 'isuSrdCd', 'isuAbwdNm', 'trdIpsbRsn']].copy()
        df.columns = ["시장구분", "표준코드", "단축코드", "종목명", "거래불가사유"]
        df["단축코드"] = df["단축코드"].str[1:]

        os.makedirs(cache_dir, exist_ok=True)
        df.to_csv(cache_path, index=False, encoding="utf-8-sig")
        return js.get("setTime", "N/A"), df
    except:
        return "N/A", pd.DataFrame(columns=["종목명"])

# ─────────────────────────────────────────────────────────────
# 2. KIND 데이터 수집 엔진 (기존 fnc2 로직 복원)
# ─────────────────────────────────────────────────────────────
def _kind_post(payload):
    url = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"
    try:
        r = requests.post(url, data=payload, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logging.error(f"KIND API Error: {e}")
        return ""

def _parse_kind_html(html_text):
    if not html_text: return pd.DataFrame()
    soup = BeautifulSoup(html_text, "lxml")
    table = soup.find("table", {"class": "list type-01"})
    if not table: return pd.DataFrame()
    
    rows = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 5: continue
        
        time = tds[0].text.strip()
        company = tds[1].text.strip()
        title_td = tds[2]
        title = title_td.text.strip()
        
        # 상세 뷰어 링크 추출 (KIND 특유의 JS 함수 파싱)
        onclick = title_td.find("a").get("onclick", "") if title_td.find("a") else ""
        doc_no = re.search(r"openDisclosure\('(\d+)'", onclick)
        viewer_url = f"https://kind.krx.co.kr/common/disclsview.do?method=search&docno={doc_no.group(1)}#{title}" if doc_no else title
        
        rows.append({
            "시간": f"{datetime.date.today().year}-{time}", # 연도 보정
            "회사명": company,
            "공시제목": title,
            "뷰어URL": viewer_url,
            "문서번호": doc_no.group(1) if doc_no else ""
        })
    return pd.DataFrame(rows)

def kind_fetch(category_arg, f, t, page_size=100):
    # category_arg에 따른 KIND 내부 파라미터 매핑
    cat_map = {"halt": "1", "mgmt": "2", "alert": "3", "misc": "6"}
    cat_code = cat_map.get(category_arg, "1")
    
    payload = {
        "method": "searchTodayDisclosureSub",
        "currentPageSize": page_size,
        "forward": "todaydisclosure_sub",
        "searchDisclsType": cat_code,
        "fromDate": f,
        "toDate": t,
    }
    return _parse_kind_html(_kind_post(payload))

def fetch_investor_warning(f, t, page_size=100):
    payload = {
        "method": "searchTodayDisclosureSub",
        "currentPageSize": page_size,
        "forward": "todaydisclosure_sub",
        "searchDisclsType": "4", # 투자경고/위험
        "fromDate": f, "toDate": t,
    }
    return _parse_kind_html(_kind_post(payload))

def fetch_shortterm_overheat(f, t, page_size=100):
    payload = {
        "method": "searchTodayDisclosureSub",
        "currentPageSize": page_size,
        "forward": "todaydisclosure_sub",
        "searchDisclsType": "5", # 단기과열
        "fromDate": f, "toDate": t,
    }
    return _parse_kind_html(_kind_post(payload))

def fetch_market_watch(f, t, page_size=100):
    # 시장감시위원회 공시는 reportCd 등으로 별도 조회하는 로직 (원본 기반)
    payload = {
        "method": "searchTodayDisclosureSub",
        "currentPageSize": page_size,
        "forward": "todaydisclosure_sub",
        "reportCd": "1", # 시장감시
        "fromDate": f, "toDate": t,
    }
    return _parse_kind_html(_kind_post(payload))
