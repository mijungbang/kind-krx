import os
import requests
import pandas as pd
import datetime
import logging

logging.basicConfig(level=logging.WARNING, format="%(message)s")

# --- 설정값 ---
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "http://data.krx.co.kr/"}

def get_nextrade_filtered_symbols(trdDd: str):
    """
    NXT 종목 가져오기 (로컬 CSV 캐시 우선)
    """
    # 1. 캐시 경로 설정 (data/2026/02/20260223.csv)
    year, month = trdDd[:4], trdDd[4:6]
    cache_dir = f"data/{year}/{month}"
    cache_path = f"{cache_dir}/{trdDd}.csv"

    # 로컬에 있으면 바로 읽기
    if os.path.exists(cache_path):
        try:
            df_cached = pd.read_csv(cache_path, dtype={'단축코드': str})
            return "Local Cache", df_cached
        except:
            pass

    # 없으면 공식 사이트 호출
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
        if not items: return "N/A", pd.DataFrame()

        df = pd.DataFrame(items)
        # 필요한 컬럼만 정리
        df = df[['mktNm', 'isuCd', 'isuSrdCd', 'isuAbwdNm', 'trdIpsbRsn']].copy()
        df.columns = ["시장구분", "표준코드", "단축코드", "종목명", "거래불가사유"]
        df["단축코드"] = df["단축코드"].str[1:] # 맨 앞 영문자 제거

        # 캐시 저장
        os.makedirs(cache_dir, exist_ok=True)
        df.to_csv(cache_path, index=False, encoding="utf-8-sig")
        
        return js.get("setTime", "N/A"), df
    except:
        return "N/A", pd.DataFrame(columns=["종목명"])

# --- KIND 데이터 수집 (기존 fnc2 로직) ---
def kind_fetch(arg, f, t, page_size=100):
    # 실제 구현 시 여기에 기존 kind_fetch(cat기반) 로직을 넣으세요.
    # 현재는 구조를 위해 빈 프레임 반환 예시만 둠
    return pd.DataFrame()

def fetch_investor_warning(f, t, page_size=100):
    return pd.DataFrame()

def fetch_shortterm_overheat(f, t, page_size=100):
    return pd.DataFrame()

def fetch_market_watch(f, t, page_size=100):
    return pd.DataFrame()
