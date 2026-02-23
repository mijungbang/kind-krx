import os
import requests
import pandas as pd
import datetime
import logging
import re

# 불필요한 로그는 끄기
logging.basicConfig(level=logging.WARNING, format="%(message)s")

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "http://data.krx.co.kr/"}

def get_nextrade_filtered_symbols(trdDd: str):
    """
    NXT 종목 조회 (로컬 캐시 확인 후 없으면 크롤링)
    """
    # 1. 캐시 경로 설정 (data/2026/02/20260223.csv)
    year, month = trdDd[:4], trdDd[4:6]
    cache_dir = f"data/{year}/{month}"
    cache_path = f"{cache_dir}/{trdDd}.csv"

    # 2. 로컬에 있으면 바로 읽기
    if os.path.exists(cache_path):
        try:
            df_cached = pd.read_csv(cache_path, dtype={'단축코드': str})
            return "Local Cache", df_cached
        except Exception:
            pass # 읽기 실패 시 새로 받아오기

    # 3. 없으면 웹에서 긁어오기
    url = "https://www.nextrade.co.kr/brdinfoTime/brdinfoTimeList.do"
    payload = {
        "_search": "false",
        "nd": str(int(pd.Timestamp.now().timestamp() * 1000)),
        "pageUnit": "900",
        "pageIndex": "1",
        "scAggDd": trdDd,
    }

    try:
        resp = requests.post(url, headers=HEADERS, data=payload, verify=False, timeout=15)
        js = resp.json()
        items = js.get("brdinfoTimeList", [])
        
        if not items:
            return "N/A", pd.DataFrame()

        # 데이터 정리
        data = []
        for it in items:
            data.append({
                "시장구분": it.get("mktNm"),
                "표준코드": it.get("isuCd"),
                "단축코드": it.get("isuSrdCd")[1:] if it.get("isuSrdCd") else "",
                "종목명": it.get("isuAbwdNm"),
                "거래불가사유": it.get("trdIpsbRsn", "")
            })
        
        df = pd.DataFrame(data)

        # 4. 조회 성공했으니 캐시 저장
        os.makedirs(cache_dir, exist_ok=True)
        df.to_csv(cache_path, index=False, encoding="utf-8-sig")
        
        return js.get("setTime", "N/A"), df

    except Exception as e:
        logging.warning(f"🚫 NXT 조회 실패: {e}")
        return "N/A", pd.DataFrame(columns=["종목명"])

# --- 기존 KIND fetch 함수들 (fnc2.py 내용 통합) ---
def kind_fetch(cat, f, t, page_size=100):
    # 기존 kind_fetch 로직 그대로 유지 (생략하지만 실제 파일엔 포함)
    pass

def fetch_investor_warning(f, t, page_size=100):
    # 기존 fetch_investor_warning 로직 그대로 유지
    pass

def fetch_shortterm_overheat(f, t, page_size=100):
    # 기존 fetch_shortterm_overheat 로직 그대로 유지
    pass

def fetch_market_watch(f, t, page_size=100):
    # 기존 fetch_market_watch 로직 그대로 유지
    pass
