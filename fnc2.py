# fnc2.py
from __future__ import annotations

import re
import time
from typing import List, Tuple

import requests
import pandas as pd
from bs4 import BeautifulSoup

__all__ = [
    "VIEWER_BASE",
    "fetch_by_templates",
    "fetch_mgmt",
    "fetch_caution",
    "fetch_delist",
    "fetch_lp",
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

KIND_URL = "https://kind.krx.co.kr/disclosure/details.do"

HEADERS_MENU = {
    "User-Agent": UA,
    "Accept": "text/html, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://kind.krx.co.kr",
    "Referer": "https://kind.krx.co.kr/disclosure/details.do?method=searchDetailsMain",
    "X-Requested-With": "XMLHttpRequest",
}

BASE_PAYLOAD = {
    "method": "searchDetailsSub",
    "currentPageSize": "100",
    "pageIndex": "1",
    "orderMode": "1",
    "orderStat": "D",
    "forward": "details_sub",
    "disclosureType01": "",
    "disclosureType02": "",
    "disclosureType03": "",
    "disclosureType04": "",
    "disclosureType05": "",
    "disclosureType06": "",
    "disclosureType07": "",
    "disclosureType08": "",
    "disclosureType09": "",
    "disclosureType10": "",
    "disclosureType11": "",
    "disclosureType13": "",
    "disclosureType14": "",
    "disclosureType20": "",
    "pDisclosureType01": "",
    "pDisclosureType02": "",
    "pDisclosureType03": "",
    "pDisclosureType04": "",
    "pDisclosureType05": "",
    "pDisclosureType06": "",
    "pDisclosureType07": "",
    "pDisclosureType08": "",
    "pDisclosureType09": "",
    "pDisclosureType10": "",
    "pDisclosureType11": "",
    "pDisclosureType13": "",
    "pDisclosureType14": "",
    "pDisclosureType20": "",
    "searchCodeType": "",
    "repIsuSrtCd": "",
    "allRepIsuSrtCd": "",
    "oldSearchCorpName": "",
    "disclosureType": "",
    "disTypevalue": "",
    "searchCorpName": "",
    "business": "",
    "marketType": "",
    "settlementMonth": "",
    "securities": "",
    "submitOblgNm": "",
    "enterprise": "",
    "bfrDsclsType": "on",
}

# ─────────────────────────────────────────────────────────────
# 템플릿 정의
# ─────────────────────────────────────────────────────────────
TARGETS_MGMT: List[Tuple[str, str, str, str]] = [
    ("ETF관리종목지정", "99350", "ETF 관리종목 지정", "ETF 관리종목 지정"),
    ("ETF관리종목지정사유변경", "99351", "ETF 관리종목지정 사유변경", "ETF 관리종목지정 사유변경"),
    ("ETF관리종목지정해제", "99352", "ETF관리종목지정해제", "ETF관리종목지정해제"),
]

TARGETS_CAUTION: List[Tuple[str, str, str, str]] = [
    ("ETF투자유의안내", "68615", "ETF 투자유의 안내", "ETF 투자유의 안내"),
    ("ETF투자유의종목지정예고", "99356", "ETF 투자유의종목 지정예고", "ETF 투자유의종목 지정예고"),
    ("ETF투자유의종목지정", "99357", "ETF 투자유의종목 지정", "ETF 투자유의종목 지정"),
    ("ETF투자유의종목지정연장", "99359", "ETF 투자유의종목 지정연장", "ETF 투자유의종목 지정연장"),
    ("ETF투자유의종목지정해제", "99358", "ETF 투자유의종목 지정해제", "ETF 투자유의종목 지정해제"),
    ("ETF투자유의종목적출", "99355", "ETF 투자유의종목 적출", "ETF 투자유의종목 적출"),
]

TARGETS_DELIST: List[Tuple[str, str, str, str]] = [
    ("ETF상장폐지사유발생(운용사공시)", "68616", "ETF 상장폐지 사유 발생(운용사 공시)", "ETF 상장폐지 사유 발생(운용사 공시)"),
    ("ETF상장폐지유예", "99354", "ETF 상장폐지 유예", "ETF 상장폐지 유예"),
    ("ETF상장폐지", "70857", "ETF상장폐지", "ETF상장폐지"),
    ("ETF상장폐지우려예고", "70854", "ETF상장폐지우려예고", "ETF상장폐지우려예고"),
]

TARGETS_LP: List[Tuple[str, str, str, str]] = [
    ("ETF유동성공급자(LP)와유동성공급계약의체결ㆍ변경ㆍ해지안내", "68624", "ETF유동성공급자(LP)와유동성공급계약의체결ㆍ변경ㆍ해지안내", "ETF유동성공급자(LP)와유동성공급계약의체결ㆍ변경ㆍ해지안내"),
    ("ETF유동성공급자(LP)시스템장애발생또는장애해소안내", "68625", "ETF유동성공급자(LP)시스템장애발생또는장애해소안내", "ETF유동성공급자(LP)시스템장애발생또는장애해소안내"),
]

# ─────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────
def _date_to_str(d: str | pd.Timestamp) -> str:
    if isinstance(d, pd.Timestamp):
        return d.strftime("%Y-%m-%d")
    s = str(d)
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _extract_company_cell(company_td) -> Tuple[str, List[str], str, str]:
    """
    회사명 셀에서 시장/플래그/회사명/종목코드 추출
    - 기존 참조 코드 방식 유지: a[id='companysum'] 기준
    """
    market = ""
    flags: List[str] = []

    icons = company_td.select("img.legend[alt]")
    market_keywords = {"코스피", "코스닥", "KOSPI", "KOSDAQ", "유가증권", "KONEX", "ETF", "ETN"}

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

        out.append([
            no, ts, market, ",".join(flags), company_name, code_num,
            title, docno, viewer, submitter
        ])

    return out


def _make_df(rows: List[List[str]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=["번호", "시간", "시장", "플래그", "회사명", "종목코드", "공시제목", "문서번호", "뷰어URL", "제출인"]
        )

    df = pd.DataFrame(
        rows,
        columns=["번호", "시간", "시장", "플래그", "회사명", "종목코드", "공시제목", "문서번호", "뷰어URL", "제출인"]
    )

    if "종목코드" in df.columns:
        # 기존 방식대로 숫자 종목코드를 읽은 뒤,
        # 마지막 한 자리가 누락된 값이므로 오른쪽에 0을 붙여 비교용 코드로 보정
        df["종목코드"] = (
            df["종목코드"]
            .astype(str)
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
            .str.replace(r"[^0-9]", "", regex=True)
            + "0"
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
    return ('table class="list type-00 mt10"' in html) or ("list type-00 mt10" in html)


# ─────────────────────────────────────────────────────────────
# 공통 조회 함수
# ─────────────────────────────────────────────────────────────
def fetch_by_templates(
    from_date: str,
    to_date: str,
    targets: List[Tuple[str, str, str, str]],
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    sleep: float = 0.15,
    timeout: int = 30,
    verify_ssl: bool = False,
) -> pd.DataFrame:
    if not targets:
        return pd.DataFrame(
            columns=["번호", "시간", "시장", "플래그", "회사명", "종목코드", "공시제목", "문서번호", "뷰어URL", "제출인"]
        )

    f = _date_to_str(from_date)
    t = _date_to_str(to_date)

    rows: List[List[str]] = []

    with requests.Session() as s:
        s.headers.update(HEADERS_MENU)

        for nm, cd, nm_temp, nm_pop in targets:
            for page in range(1, max_pages + 1):
                payload = {
                    **BASE_PAYLOAD,
                    "currentPageSize": str(page_size),
                    "pageIndex": str(page),
                    "fromDate": f,
                    "toDate": t,
                    "reportNm": nm,
                    "reportCd": cd,
                    "reportNmTemp": nm_temp,
                    "reportNmPop": nm_pop,
                }

                r = s.post(KIND_URL, data=payload, timeout=timeout, verify=verify_ssl)
                r.raise_for_status()
                html = r.text

                if not _looks_like_valid_kind_table(html):
                    snippet = re.sub(r"\s+", " ", html)[:300]
                    raise RuntimeError(
                        f"KIND 응답이 정상 테이블이 아님(차단/오류 가능). 응답 일부: {snippet}"
                    )

                before = len(rows)
                rows += _parse_rows_html(html)
                added = len(rows) - before

                if added == 0 or added < int(page_size):
                    break

                if sleep:
                    time.sleep(sleep)

    return _make_df(rows)


# ─────────────────────────────────────────────────────────────
# 메뉴별 wrapper
# ─────────────────────────────────────────────────────────────
def fetch_mgmt(
    from_date: str,
    to_date: str,
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    sleep: float = 0.15,
) -> pd.DataFrame:
    return fetch_by_templates(
        from_date, to_date, TARGETS_MGMT,
        page_size=page_size, max_pages=max_pages, sleep=sleep
    )


def fetch_caution(
    from_date: str,
    to_date: str,
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    sleep: float = 0.15,
) -> pd.DataFrame:
    return fetch_by_templates(
        from_date, to_date, TARGETS_CAUTION,
        page_size=page_size, max_pages=max_pages, sleep=sleep
    )


def fetch_delist(
    from_date: str,
    to_date: str,
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    sleep: float = 0.15,
) -> pd.DataFrame:
    return fetch_by_templates(
        from_date, to_date, TARGETS_DELIST,
        page_size=page_size, max_pages=max_pages, sleep=sleep
    )


def fetch_lp(
    from_date: str,
    to_date: str,
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    sleep: float = 0.15,
) -> pd.DataFrame:
    return fetch_by_templates(
        from_date, to_date, TARGETS_LP,
        page_size=page_size, max_pages=max_pages, sleep=sleep
    )
