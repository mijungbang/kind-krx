import os
import requests
import pandas as pd
import datetime
import logging

# 조용하게 실행
logging.basicConfig(level=logging.WARNING, format="%(message)s")

# --- NXT 종목 조회 (캐시 적용 버전) ---
def get_nextrade_filtered_symbols(trdDd: str):
    """
    NXT 종목 조회: 로컬 CSV가 있으면 읽고, 없으면 웹에서 긁어와서 저장함.
    trdDd: '20260223' 형태
    """
    year, month = trdDd[:4], trdDd[4:6]
    cache_dir = f"data/{year}/{month}"
    cache_path = f"{cache_dir}/{trdDd}.csv"

    # 1. 로컬 캐시 확인
    if os.path.exists(cache_path):
        try:
            df_cached = pd.read_csv(cache_path, dtype={'단축코드': str})
            return "Local Cache", df_cached
        except:
            pass # 에러 나면 그냥 새로 받음

    # 2. 웹에서 데이터 수집 (기존 get_nextrade_filtered_symbols 로직)
    url = "https://www.nextrade.co.kr/brdinfoTime/brdinfoTimeList.do"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.nextrade.co.kr/menu/transactionStatusMain/menuList.do",
    }
    payload = {
        "_search": "false",
        "nd": str(int(pd.Timestamp.now().timestamp() * 1000)),
        "pageUnit": "900",
        "pageIndex": "1",
        "scAggDd": trdDd,
    }

    try:
        resp = requests.post(url, headers=headers, data=payload, verify=False, timeout=15)
        js = resp.json()
        items = js.get("brdinfoTimeList", [])
        if not items:
            return "N/A", pd.DataFrame()

        # 데이터 프레임 변환
        df = pd.DataFrame(items)
        df = df[['mktNm', 'isuCd', 'isuSrdCd', 'isuAbwdNm', 'trdIpsbRsn']].copy()
        df.columns = ["시장구분", "표준코드", "단축코드", "종목명", "거래불가사유"]
        df["단축코드"] = df["단축코드"].str[1:] # 앞자리 문자 제거

        # 3. 성공 시 캐시 저장
        os.makedirs(cache_dir, exist_ok=True)
        df.to_csv(cache_path, index=False, encoding="utf-8-sig")
        
        return js.get("setTime", "N/A"), df
    except Exception as e:
        return "N/A", pd.DataFrame()

# --- 아래는 기존 fnc2.py에 있던 엔진 함수들 그대로 복사 ---

def kind_fetch(arg, f, t, page_size=100):
    # (여기에 기존 kind_fetch 로직 전체 복사)
    pass

def fetch_investor_warning(f, t, page_size=100):
    # (여기에 기존 fetch_investor_warning 로직 전체 복사)
    pass

def fetch_shortterm_overheat(f, t, page_size=100):
    # (여기에 기존 fetch_shortterm_overheat 로직 전체 복사)
    pass

def fetch_market_watch(f, t, page_size=100):
    # (여기에 기존 fetch_market_watch 로직 전체 복사)
    pass
