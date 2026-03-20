# fnc2.py
from __future__ import annotations

import re
import time
from typing import Optional, Dict, List, Tuple

import requests
import pandas as pd
from bs4 import BeautifulSoup

__all__ = [
    "CODE_MAP",
    "kind_fetch",
    "fetch_investor_warning",
    "fetch_shortterm_overheat",
    "fetch_market_watch",
]

# ─────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────
VIEWER_BASE = (
    "https://kind.krx.co.kr/common/disclsviewer.do?"
    "method=search&acptno={docno}&docno=&viewerhost=&viewerport="
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

# 카테고리 코드 (세부검색 disTypevalue)
CODE_MAP: Dict[str, str] = {
    "halt":  "0311",  # 거래정지/재개
    "mgmt":  "0350",  # 관리종목
    "alert": "0356",  # 투자주의·환기
    "misc":  "0305",  # 기타 시장안내
}

KIND_URL = "https://kind.krx.co.kr/disclosure/details.do"

# ─────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────
def _date_to_str(d: str | pd.Timestamp) -> str:
    """'YYYY-MM-DD' 또는 'YYYYMMDD' 또는 pandas.Timestamp → 'YYYY-MM-DD'"""
    if isinstance(d, pd.Timestamp):
        return d.strftime("%Y-%m-%d")
    s = str(d)
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _extract_company_cell(company_td) -> Tuple[str, List[str], str, str]:
    """
    회사명 셀에서 시장/플래그/회사명/종목코드 추출
    """
    market = ""
    flags: List[str] = []

    icons = company_td.select("img.legend[alt]")
    market_keywords = {"코스피", "코스닥", "KOSPI", "KOSDAQ", "유가증권", "KONEX"}
    for img in icons:
        alt = (img.get("alt") or "").strip()
        if not alt:
            continue
        if not market and alt in market_keywords:
            market = alt
        else:
            flags.append(alt)

    comp_a = company_td.find("a", id="companysum")
    company_name = (
        (comp_a.get("title") or comp_a.get_text(strip=True)).strip()
        if comp_a else company_td.get_text(strip=True)
    )

    code_num = ""
    if comp_a and comp_a.has_attr("onclick"):
        m = re.search(r"companysummary_open\('(\d+)'\)", comp_a["onclick"])
        if m:
            code_num = m.group(1)

    return market, flags, company_name, code_num


def _parse_rows_html(html: str) -> List[List[str]]:
    """
    상세검색 테이블 파싱 → 행 배열
    반환: [번호, 시간, 시장, 플래그, 회사명, 종목코드, 공시제목, 문서번호, 뷰어URL, 제출인]
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="list type-00 mt10")
    if not table or not table.tbody:
        return []
    out: List[List[str]] = []

    for tr in table.tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue

        no = tds[0].get_text(strip=True)
        ts = tds[1].get_text(strip=True)

        company_td = tds[2]
        market, flags, company_name, code_num = _extract_company_cell(company_td)

        title_td = tds[3]
        a = title_td.find("a", onclick=True)
        title = (
            (a.get("title") or title_td.get_text(strip=True)).strip()
            if a else title_td.get_text(strip=True)
        )

        docno = ""
        if a and a.has_attr("onclick"):
            m = re.search(r"openDisclsViewer\('(\d+)'", a["onclick"])
            if m:
                docno = m.group(1)

        viewer = f"{VIEWER_BASE.format(docno=docno)}#{title}" if docno else ""
        submitter = tds[4].get_text(strip=True)

        out.append([no, ts, market, ",".join(flags), company_name, code_num, title, docno, viewer, submitter])

    return out


def _make_df(rows: List[List[str]]) -> pd.DataFrame:
    """rows → DF, 문서번호 중복 제거 + 시간 내림차순 + 스팩 제외"""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        rows,
        columns=["번호","시간","시장","플래그","회사명","종목코드","공시제목","문서번호","뷰어URL","제출인"]
    )
    if "문서번호" in df.columns:
        df = df.drop_duplicates(subset=["문서번호"], keep="first")
    if "시간" in df.columns:
        df["__ts"] = pd.to_datetime(df["시간"], errors="coerce")
        df = df.sort_values("__ts", ascending=False).drop(columns="__ts")
    if "회사명" in df.columns:
        df["회사명"] = df["회사명"].astype(str)
        df = df[~df["회사명"].str.contains("스팩", na=False)]
    return df.reset_index(drop=True)


def _looks_like_valid_kind_table(html: str) -> bool:
    # 정상 응답이면 보통 아래 테이블이 존재
    return ('table class="list type-00 mt10"' in html) or ("list type-00 mt10" in html)


# ─────────────────────────────────────────────────────────────
# 공통 상세검색 (카테고리 1~4/6)
# ─────────────────────────────────────────────────────────────
def _kind_disclosure_search(
    from_date: str,
    to_date: str,
    code: str,
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    sleep: float = 1,
    timeout: int = 60,
    verify_ssl: bool = False,
    session: Optional[requests.Session] = None,
    report_nm: Optional[str] = None,
    report_cd: Optional[str] = None,
) -> pd.DataFrame:
    """
    KIND 상세검색(카테고리) 페이지네이션 수집.
    반환 컬럼:
    [페이지, 번호, 시간, 시장, 플래그, 회사명, 종목코드, 공시제목, 문서번호, 뷰어URL, 제출인]
    """
    BASE = "https://kind.krx.co.kr"
    GET_URL = f"{BASE}/disclosure/details.do"
    POST_URL = f"{BASE}/disclosure/details.do"

    f = _date_to_str(from_date)
    t = _date_to_str(to_date)

    base_headers = {"User-Agent": UA, "Accept": "text/html, */*; q=0.01"}
    ajax_headers = {
        **base_headers,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://kind.krx.co.kr",
        "Referer": f"{GET_URL}?method=searchDetailsMain&disclosureType=02&disTypevalue={code}",
        "X-Requested-With": "XMLHttpRequest",
    }

    warm_params = {"method": "searchDetailsMain", "disclosureType": "02", "disTypevalue": code}

    data = {
        "method": "searchDetailsSub",
        "currentPageSize": str(page_size),
        "pageIndex": "1",
        "orderMode": "1",
        "orderStat": "D",
        "forward": "details_sub",
        "disclosureType02": f"{code}|",
        "pDisclosureType02": f"{code}|",
        "disclosureTypeArr02": code,
        "fromDate": f,
        "toDate": t,
        "reportNm": report_nm or "",
        "reportNmTemp": report_nm or "",
        "reportNmPop": report_nm or "",
        "reportCd": (str(report_cd) if report_cd is not None else ""),

        # 나머지 공란(원형 유지)
        "disclosureType01": "","disclosureType03": "","disclosureType04": "","disclosureType05": "",
        "disclosureType06": "","disclosureType07": "","disclosureType08": "","disclosureType09": "",
        "disclosureType10": "","disclosureType11": "","disclosureType13": "","disclosureType14": "",
        "disclosureType20": "","pDisclosureType01": "","pDisclosureType03": "","pDisclosureType04": "",
        "pDisclosureType05": "","pDisclosureType06": "","pDisclosureType07": "","pDisclosureType08": "",
        "pDisclosureType09": "","pDisclosureType10": "","pDisclosureType11": "","pDisclosureType13": "",
        "pDisclosureType14": "","pDisclosureType20": "","searchCodeType": "","repIsuSrtCd": "",
        "allRepIsuSrtCd": "","oldSearchCorpName": "","searchCorpName": "",
        "business": "","marketType": "","settlementMonth": "","securities": "","submitOblgNm": "",
        "enterprise": "",
    }

    cols = ["페이지","번호","시간","시장","플래그","회사명","종목코드","공시제목","문서번호","뷰어URL","제출인"]
    rows: List[List[str]] = []

    close_after = False
    s = session
    if s is None:
        s = requests.Session()
        close_after = True

    try:
        s.headers.update(base_headers)
        s.get(GET_URL, params=warm_params, timeout=timeout, verify=False)

        for page in range(1, max_pages + 1):
            data["pageIndex"] = str(page)
            r = s.post(POST_URL, data=data, headers=ajax_headers, timeout=timeout, verify=False)
            r.raise_for_status()
            r.encoding = r.apparent_encoding
            html = r.text

            # ✅ 200 OK 차단/오류 HTML도 여기서 걸러서 "캐싱"을 방지
            if not _looks_like_valid_kind_table(html):
                snippet = re.sub(r"\s+", " ", html)[:300]
                raise RuntimeError(f"KIND 응답이 정상 테이블이 아님(차단/오류 가능). 응답 일부: {snippet}")

            added = 0
            for row in _parse_rows_html(html):
                rows.append([page] + row)
                added += 1

            if added == 0 or added < int(page_size):
                break
            if sleep:
                time.sleep(sleep)

    finally:
        if close_after:
            s.close()

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty and "회사명" in df.columns:
        df["회사명"] = df["회사명"].astype(str)
        df = df[~df["회사명"].str.contains("스팩", na=False)]
    return df.reset_index(drop=True)


def kind_fetch(
    category: str,
    from_date: str,
    to_date: str,
    page_size: int = 100,
    max_pages: int = 1000,
    *,
    report_nm: Optional[str] = None,
    report_cd: Optional[str] = None,
) -> pd.DataFrame:
    """cat 기반(기존): halt/mgmt/alert/misc"""
    code = CODE_MAP[category]
    df = _kind_disclosure_search(
        from_date, to_date, code,
        page_size=page_size, max_pages=max_pages,
        report_nm=report_nm, report_cd=report_cd
    )
    return df.reset_index(drop=True) if df is not None and not df.empty else pd.DataFrame()


# ─────────────────────────────────────────────────────────────
# 투자경고·위험 / 단기과열 / 시장감시위원회 (warn 페이로드)
# ─────────────────────────────────────────────────────────────
HEADERS_MENU_WARN = {
    "User-Agent": UA,
    "Accept": "text/html, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://kind.krx.co.kr",
    "Referer": "https://kind.krx.co.kr/disclosure/details.do?method=searchDetailsMain",
    "X-Requested-With": "XMLHttpRequest",
}
BASE_PAYLOAD_WARN = {
    "method":"searchDetailsSub","currentPageSize":"15","pageIndex":"1",
    "orderMode":"1","orderStat":"D","forward":"details_sub",
    "disclosureType01":"","disclosureType02":"","disclosureType03":"","disclosureType04":"",
    "disclosureType05":"","disclosureType06":"","disclosureType07":"","disclosureType08":"",
    "disclosureType09":"","disclosureType10":"","disclosureType11":"","disclosureType13":"",
    "disclosureType14":"","disclosureType20":"",
    "pDisclosureType01":"","pDisclosureType02":"","pDisclosureType03":"","pDisclosureType04":"",
    "pDisclosureType05":"","pDisclosureType06":"","pDisclosureType07":"","pDisclosureType08":"",
    "pDisclosureType09":"","pDisclosureType10":"","pDisclosureType11":"","pDisclosureType13":"",
    "pDisclosureType14":"","pDisclosureType20":"",
    "searchCodeType":"","repIsuSrtCd":"","allRepIsuSrtCd":"","oldSearchCorpName":"",
    "disclosureType":"","disTypevalue":"",
    "searchCorpName":"","business":"","marketType":"","settlementMonth":"",
    "securities":"","submitOblgNm":"","enterprise":"",
    "bfrDsclsType":"on",
}

TARGETS_WARN: List[Tuple[str,str,str,str]] = [
    ("투자경고종목지정",         "68809", "투자경고종목 지정",              "투자경고종목 지정"),
    ("투자경고종목지정",         "70804", "투자경고종목지정",                "투자경고종목지정"),
    ("투자경고종목지정(재지정)", "68823", "투자경고종목 지정(재지정)",       "투자경고종목 지정(재지정)"),
    ("투자경고종목지정(재지정)", "72049", "투자경고종목지정(재지정)",         "투자경고종목지정(재지정)"),
    ("투자경고종목지정해제",     "68824", "투자경고종목 지정해제",           "투자경고종목 지정해제"),
    ("투자경고종목지정해제",     "72056", "투자경고종목 지정해제",           "투자경고종목 지정해제"),
    ("[투자주의]투자경고종목지정해제및재지정예고", "70820",
     "[투자주의]투자경고종목 지정해제 및 재지정 예고",
     "[투자주의]투자경고종목 지정해제 및 재지정 예고"),
    ("[투자주의]투자경고종목지정해제및재지정예고", "68810",
     "[투자주의]투자경고종목 지정해제 및 재지정 예고",
     "[투자주의]투자경고종목 지정해제 및 재지정 예고"),
    ("투자위험종목지정",         "68812", "투자위험종목지정",                "투자위험종목지정"),
    ("투자위험종목지정",         "70832", "투자위험종목지정",                "투자위험종목지정"),
    ("투자위험종목지정해제",     "68813", "투자위험종목지정해제",            "투자위험종목지정해제"),
    ("투자위험종목지정해제",     "70834", "투자위험종목지정해제",            "투자위험종목지정해제"),
]

# ✅ "시장감시위원회" 메뉴에서 보여줄 reportCd 세트 (사용자 제공)
TARGETS_MARKET_WATCH: List[Tuple[str,str,str,str]] = [
    # [유가증권]
    ("기타시장안내(단기과열완화장치발동예고)", "99432",
     "기타시장안내 (단기과열완화장치 발동예고)", "기타시장안내 (단기과열완화장치 발동예고)"),
    ("단기과열완화장치발동(매매거래정지및단일가매매적용)", "99431",
     "단기과열완화장치 발동(매매거래정지 및 단일가매매 적용)", "단기과열완화장치 발동(매매거래정지 및 단일가매매 적용)"),
    ("매매거래정지및재개(투자경고종목지정중)", "68818",
     "매매거래 정지 및 재개(투자경고종목 지정중)", "매매거래 정지 및 재개(투자경고종목 지정중)"),
    ("매매거래정지및재개(투자위험종목지정중)", "68815",
     "매매거래 정지 및 재개(투자위험종목 지정중)", "매매거래 정지 및 재개(투자위험종목 지정중)"),
    ("매매거래정지및재개(투자위험종목최초지정)", "68819",
     "매매거래 정지 및 재개(투자위험종목 최초지정)", "매매거래 정지 및 재개(투자위험종목 최초지정)"),
    ("매매거래정지및신규호가접수중지안내", "99306",
     "매매거래정지 및 신규호가접수중지 안내", "매매거래정지 및 신규호가접수중지 안내"),
    ("장애종목매매거래정지시장안내(유가증권시장)", "99457",
     "장애종목 매매거래정지 시장안내 (유가증권시장)", "장애종목 매매거래정지 시장안내 (유가증권시장)"),
    ("장애종목매매거래재개시장안내(유가증권시장/접속매매방식재개)", "99458",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 접속매매 방식 재개)", "장애종목 매매거래재개 시장안내 (유가증권시장 / 접속매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(유가증권시장/종가단일가매매방식재개)", "99459",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 종가단일가매매 방식 재개)", "장애종목 매매거래재개 시장안내 (유가증권시장 / 종가단일가매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(유가증권시장/시간외단일가매매방식재개)", "99462",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 시간외단일가매매 방식 재개)", "장애종목 매매거래재개 시장안내 (유가증권시장 / 시간외단일가매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(유가증권시장/시간외종가매매방식재개)", "99461",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 시간외종가매매 방식 재개)", "장애종목 매매거래재개 시장안내 (유가증권시장 / 시간외종가매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(유가증권시장/시간외종가매매호가접수시간대재개)", "99460",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 시간외종가매매 호가접수시간대 재개)", "장애종목 매매거래재개 시장안내 (유가증권시장 / 시간외종가매매 호가접수시간대 재개)"),

    # [코스닥]
    ("기타시장안내(단기과열완화장치발동예고)", "70729",
     "기타시장안내 (단기과열완화장치 발동예고)", "기타시장안내 (단기과열완화장치 발동예고)"),
    ("단기과열완화장치발동(매매거래정지및단일가매매적용)", "70728",
     "단기과열완화장치 발동(매매거래정지 및 단일가매매 적용)", "단기과열완화장치 발동(매매거래정지 및 단일가매매 적용)"),
    ("매매거래정지및재개(투자경고종목지정중)", "70837",
     "매매거래 정지 및 재개(투자경고종목 지정중)", "매매거래 정지 및 재개(투자경고종목 지정중)"),
    ("매매거래정지및재개(투자위험종목지정중)", "70836",
     "매매거래 정지 및 재개(투자위험종목 지정중)", "매매거래 정지 및 재개(투자위험종목 지정중)"),
    ("매매거래정지및재개(투자위험종목최초지정)", "70838",
     "매매거래 정지 및 재개(투자위험종목 최초지정)", "매매거래 정지 및 재개(투자위험종목 최초지정)"),
    ("장애종목매매거래정지시장안내(코스닥시장)", "72116",
     "장애종목 매매거래정지 시장안내 (코스닥시장)", "장애종목 매매거래정지 시장안내 (코스닥시장)"),
    ("장애종목매매거래재개시장안내(코스닥시장/접속매매방식재개)", "72117",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 접속매매 방식 재개)", "장애종목 매매거래재개 시장안내 (코스닥시장 / 접속매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(코스닥시장/종가단일가매매방식재개)", "72118",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 종가단일가매매 방식 재개)", "장애종목 매매거래재개 시장안내 (코스닥시장 / 종가단일가매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(코스닥시장/시간외단일가매매방식재개)", "72121",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 시간외단일가매매 방식 재개)", "장애종목 매매거래재개 시장안내 (코스닥시장 / 시간외단일가매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(코스닥시장/시간외종가매매방식재개)", "72120",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 시간외종가매매 방식 재개)", "장애종목 매매거래재개 시장안내 (코스닥시장 / 시간외종가매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(코스닥시장/시간외종가매매호가접수시간대재개)", "72119",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 시간외종가매매 호가접수시간대 재개)", "장애종목 매매거래재개 시장안내 (코스닥시장 / 시간외종가매매 호가접수시간대 재개)"),
]


def _fetch_reportcd_with_warn_payload(
    from_date: str,
    to_date: str,
    targets: List[Tuple[str,str,str,str]],
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    sleep: float = 1,
) -> pd.DataFrame:
    f = _date_to_str(from_date)
    t = _date_to_str(to_date)

    rows: List[List[str]] = []
    with requests.Session() as s:
        s.headers.update(HEADERS_MENU_WARN)

        for nm, cd, nm_temp, nm_pop in targets:
            for page in range(1, max_pages + 1):
                payload = {
                    **BASE_PAYLOAD_WARN,
                    "currentPageSize": str(page_size),
                    "pageIndex": str(page),
                    "fromDate": f,
                    "toDate": t,
                    "reportNm": nm,
                    "reportCd": cd,
                    "reportNmTemp": nm_temp,
                    "reportNmPop": nm_pop,
                }
                r = s.post(KIND_URL, data=payload, timeout=30, verify=False)
                r.raise_for_status()
                html = r.text

                # ✅ 200 OK 차단/오류 HTML도 여기서 걸러서 "캐싱"을 방지
                if not _looks_like_valid_kind_table(html):
                    snippet = re.sub(r"\s+", " ", html)[:300]
                    raise RuntimeError(f"KIND(warn payload) 응답이 정상 테이블이 아님(차단/오류 가능). 응답 일부: {snippet}")

                before = len(rows)
                rows += _parse_rows_html(html)
                added = len(rows) - before

                if added == 0 or added < int(page_size):
                    break
                if sleep:
                    time.sleep(sleep)

    return _make_df(rows)


def fetch_investor_warning(
    from_date: str,
    to_date: str,
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    sleep: float = 1,
) -> pd.DataFrame:
    """투자경고·위험: 여러 reportCd × 페이지네이션 전체 수집 → 문서번호 중복 제거."""
    return _fetch_reportcd_with_warn_payload(
        from_date, to_date, TARGETS_WARN,
        page_size=page_size, max_pages=max_pages, sleep=sleep
    )


def fetch_shortterm_overheat(
    from_date: str,
    to_date: str,
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    sleep: float = 1,
) -> pd.DataFrame:
    """단기과열: reportNm='단기과열' 단일 조건 페이지네이션 수집."""
    f = _date_to_str(from_date)
    t = _date_to_str(to_date)

    rows: List[List[str]] = []
    with requests.Session() as s:
        s.headers.update(HEADERS_MENU_WARN)

        for page in range(1, max_pages + 1):
            payload = {
                **BASE_PAYLOAD_WARN,
                "currentPageSize": str(page_size),
                "pageIndex": str(page),
                "fromDate": f,
                "toDate": t,
                "reportNm": "단기과열",
                "reportCd": "",
                "reportNmTemp": "단기과열",
                "reportNmPop": "",
            }
            r = s.post(KIND_URL, data=payload, timeout=30, verify=False)
            r.raise_for_status()
            html = r.text

            if not _looks_like_valid_kind_table(html):
                snippet = re.sub(r"\s+", " ", html)[:300]
                raise RuntimeError(f"KIND(단기과열) 응답이 정상 테이블이 아님(차단/오류 가능). 응답 일부: {snippet}")

            before = len(rows)
            rows += _parse_rows_html(html)
            added = len(rows) - before

            if added == 0 or added < int(page_size):
                break
            if sleep:
                time.sleep(sleep)

    return _make_df(rows)


def fetch_market_watch(
    from_date: str,
    to_date: str,
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    sleep: float = 0.15,
) -> pd.DataFrame:
    """시장감시위원회(사용자 지정): 사용자가 준 reportCd 목록을 warn 페이로드 방식으로 조회."""
    return _fetch_reportcd_with_warn_payload(
        from_date, to_date, TARGETS_MARKET_WATCH,
        page_size=page_size, max_pages=max_pages, sleep=sleep
    )
