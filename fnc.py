import requests
import pandas as pd
import logging

logging.basicConfig(level=logging.WARNING, format="%(message)s")

def get_krx_market_price_info(trdDd: str):
    """KRX 전체 종목 시세 (시가총액, 거래량 등)"""
    url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
    headers = {
        "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020202",
        "User-Agent": "Mozilla/5.0"
    }
    payload = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
        "locale": "ko_KR",
        "mktId": "ALL",
        "trdDd": trdDd,
        "share": "1",
        "money": "1",
        "csvxls_isNo": "false"
    }

    time = "N/A"
    cols = ["시장구분", "표준코드", "단축코드", "종목명", "시가총액", "상장주식수", "KRX종가", "KRX거래량"]

    try:
        resp = requests.post(url, headers=headers, data=payload, timeout=25)
        resp.raise_for_status()
        data = resp.json()
        time = data.get("CURRENT_DATETIME", "N/A")
        items = data.get("OutBlock_1", [])
    except Exception as e:
        logging.warning(f"🚫 KRX 요청 오류: {e}")
        return time, pd.DataFrame(columns=cols)

    if not items:
        logging.warning("⚠️ KRX 응답 데이터 없음")
        return time, pd.DataFrame(columns=cols)

    df = pd.DataFrame(items)
    df.rename(columns={
        "MKT_NM": "시장구분",
        "ISU_CD": "표준코드",
        "ISU_SRT_CD": "단축코드",
        "ISU_ABBRV": "종목명",
        "MKTCAP": "시가총액",
        "LIST_SHRS": "상장주식수",
        "TDD_CLSPRC": "KRX종가",
        "ACC_TRDVOL": "KRX거래량"
    }, inplace=True)

    df = df[cols]
    df = df[df["시장구분"] != "KONEX"]
    df["시장구분"] = df["시장구분"].str.replace("KOSDAQ GLOBAL", "KOSDAQ")

    # ✅ 숫자형 컬럼 변환 (콤마 제거)
    for col in ["시가총액", "KRX종가", "KRX거래량", "상장주식수"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "").str.strip(), errors="coerce").fillna(0)

    df.reset_index(drop=True, inplace=True)
    return time, df


def get_krx_index(trdDd: str):
    """KOSPI200 / KOSDAQ150 구성종목"""
    url = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
    headers = {
        "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201010106",
        "User-Agent": "Mozilla/5.0"
    }
    payloads = [
        {"indIdx": "1", "indIdx2": "028", "tboxindIdx_finder_equidx0_0": "코스피 200"},
        {"indIdx": "2", "indIdx2": "203", "tboxindIdx_finder_equidx0_0": "코스닥 150"},
    ]

    dfs = []
    for p in payloads:
        p.update({
            "bld": "dbms/MDC/STAT/standard/MDCSTAT00601",
            "locale": "ko_KR",
            "trdDd": trdDd,
            "money": "3",
            "csvxls_isNo": "false",
        })
        try:
            r = requests.post(url, headers=headers, data=p, verify=False, timeout=15)
            r.raise_for_status()
            data = r.json().get("output", [])
            if data:
                df = pd.DataFrame(data)[["ISU_SRT_CD", "ISU_ABBRV"]]
                df.rename(columns={"ISU_SRT_CD": "단축코드", "ISU_ABBRV": "종목명"}, inplace=True)
                df["지수구분"] = "K200" if p["indIdx"] == "1" else "Q150"
                dfs.append(df)
        except Exception as e:
            logging.warning(f"🚫 지수 조회 오류: {e}")

    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame(columns=["지수구분", "단축코드", "종목명"])


def get_nextrade_filtered_symbols(trdDd: str):
    """넥스트레이드 등록 종목"""
    url = "https://www.nextrade.co.kr/brdinfoTime/brdinfoTimeList.do"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.nextrade.co.kr/menu/transactionStatusMain/menuList.do",
        "Origin": "https://www.nextrade.co.kr",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded",
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
        resp.encoding = "utf-8"
        js = resp.json()
        time = js.get("setTime", "N/A")
        items = js.get("brdinfoTimeList", [])
        if not items:
            return time, pd.DataFrame()
        keep = ["mktNm", "isuCd", "isuSrdCd", "isuAbwdNm", "curPrc", "accTdQty", "accTrval", "cptrTrdPmsnCdNm", "trdIpsbRsn"]
        data = [{k: it.get(k, None) for k in keep} for it in items]
        df = pd.DataFrame(data)
        df.columns = ["시장구분", "표준코드", "단축코드", "종목명", "NXT현재가", "NXT거래량", "거래대금", "거래가능시장", "거래불가사유"]
        df["단축코드"] = df["단축코드"].str[1:]
        return time, df
    except Exception as e:
        logging.warning(f"🚫 NXT 요청 오류: {e}")
        return "N/A", pd.DataFrame()
