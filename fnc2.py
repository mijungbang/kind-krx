# fnc2.py
# 상장폐지 추가 + 403 대응 패치
#  - 전역 세션 재사용 + GET 워밍업(JSESSIONID 확보)
#  - 브라우저 유사 헤더(Accept-Language, Sec-Fetch-*)
#  - 403/429/503 시 세션 재생성 + 지수 백오프 재시도
#  - 적응형 페이싱: 평상시엔 대기 없음, 저항이 감지될 때만 느려지고 회복되면 다시 빨라짐
#  - sleep 인자는 하위호환용으로만 남아 있으며 무시됨(_PACER가 대체)
from __future__ import annotations

import re
import time
import random
import threading
from typing import Optional, Dict, List, Tuple

import requests
import pandas as pd
from bs4 import BeautifulSoup

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

__all__ = [
    "CODE_MAP",
    "kind_fetch",
    "fetch_investor_warning",
    "fetch_shortterm_overheat",
    "fetch_market_watch",
    "fetch_delist",
    "reset_session",
    "pacer_status",
    "diagnose",
]

# ─────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────
BASE = "https://kind.krx.co.kr"
KIND_URL = f"{BASE}/disclosure/details.do"
MAIN_URL = f"{BASE}/main.do"

VIEWER_BASE = (
    "https://kind.krx.co.kr/common/disclsviewer.do?"
    "method=search&acptno={docno}&docno=&viewerhost=&viewerport="
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

# 문서 요청용(워밍업)
NAV_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="141", "Not?A_Brand";v="24", "Google Chrome";v="141"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# XHR 요청용
AJAX_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": BASE,
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "sec-ch-ua": '"Chromium";v="141", "Not?A_Brand";v="24", "Google Chrome";v="141"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

DETAILS_REFERER = f"{KIND_URL}?method=searchDetailsMain"

# ─────────────────────────────────────────────────────────────
# 적응형 페이싱
#   정상일 때는 대기 없음. 저항(403/429/비정상HTML)이 감지될 때만 지연이 붙고,
#   연속 성공하면 다시 0으로 내려온다.
# ─────────────────────────────────────────────────────────────
PACE_FLOOR = 0.3      # 평상시 최소 간격(초). 0으로 두면 전혀 쉬지 않음
PACE_FIRST = 2.0      # 첫 저항 감지 시 걸리는 지연
PACE_CEILING = 30.0   # 지연 상한
PACE_RECOVER_AFTER = 4  # 연속 성공 N회마다 지연 절반으로


class _Pacer:
    """저항이 있을 때만 느려지는 요청 간격 조절기."""

    def __init__(self):
        self.delay = PACE_FLOOR
        self.ok_streak = 0
        self.last_at = 0.0

    def wait(self) -> None:
        if self.delay <= 0:
            self.last_at = time.time()
            return
        gap = time.time() - self.last_at
        if gap < self.delay:
            time.sleep(self.delay - gap + random.uniform(0, self.delay * 0.2))
        self.last_at = time.time()

    def on_success(self) -> None:
        self.ok_streak += 1
        if self.delay > PACE_FLOOR and self.ok_streak >= PACE_RECOVER_AFTER:
            self.delay = max(PACE_FLOOR, self.delay / 2)
            self.ok_streak = 0

    def on_trouble(self) -> None:
        self.ok_streak = 0
        self.delay = min(PACE_CEILING, max(PACE_FIRST, self.delay * 2))

    def status(self) -> dict:
        return {"delay": round(self.delay, 2), "ok_streak": self.ok_streak}


_PACER = _Pacer()


def pacer_status() -> dict:
    """menu2.py에서 현재 지연 상태를 보여줄 때 사용."""
    return _PACER.status()

# 카테고리 코드 (세부검색 disTypevalue)
CODE_MAP: Dict[str, str] = {
    "halt":  "0311",   # 거래정지/재개
    "mgmt":  "0350",   # 관리종목
    "alert": "0356",   # 투자주의·환기
    "misc":  "0305",   # 기타 시장안내
}

# ─────────────────────────────────────────────────────────────
# 세션 관리 (전역 1개 재사용 + 워밍업)
# ─────────────────────────────────────────────────────────────
_SESSION: Optional[requests.Session] = None
_LOCK = threading.Lock()


def _build_session(timeout: int = 30) -> requests.Session:
    """새 세션 + 메인/상세검색 GET 워밍업으로 JSESSIONID 확보."""
    s = requests.Session()
    s.headers.update(NAV_HEADERS)

    _PACER.wait()
    s.get(
        MAIN_URL, params={"method": "loadInitPage"},
        timeout=timeout, verify=False,
        headers={"Sec-Fetch-Site": "none"},
    )

    _PACER.wait()
    s.get(
        KIND_URL, params={"method": "searchDetailsMain"},
        timeout=timeout, verify=False,
        headers={"Referer": f"{MAIN_URL}?method=loadInitPage"},
    )
    return s


def get_session(force_new: bool = False) -> requests.Session:
    global _SESSION
    with _LOCK:
        if force_new and _SESSION is not None:
            try:
                _SESSION.close()
            except Exception:
                pass
            _SESSION = None
        if _SESSION is None:
            _SESSION = _build_session()
        return _SESSION


def reset_session() -> None:
    """menu2.py의 🧹 초기화 버튼에서 함께 호출하면 좋습니다."""
    global _SESSION
    with _LOCK:
        if _SESSION is not None:
            try:
                _SESSION.close()
            except Exception:
                pass
        _SESSION = None


# ─────────────────────────────────────────────────────────────
# POST + 재시도
# ─────────────────────────────────────────────────────────────
def _looks_like_valid_kind_table(html: str) -> bool:
    return ('table class="list type-00 mt10"' in html) or ("list type-00 mt10" in html)


def _post_kind(
    payload: dict,
    *,
    referer: str = DETAILS_REFERER,
    tries: int = 4,
    timeout: int = 60,
) -> str:
    """
    KIND POST. 403/429/503 또는 비정상 HTML이면 세션을 새로 만들어 백오프 재시도.
    성공 시 HTML 문자열 반환.
    """
    last_err = None
    for attempt in range(tries):
        s = get_session(force_new=(attempt > 0))
        headers = {**AJAX_HEADERS, "Referer": referer}
        try:
            _PACER.wait()
            r = s.post(KIND_URL, data=payload, headers=headers,
                       timeout=timeout, verify=False)

            if r.status_code in (403, 429, 503):
                last_err = f"HTTP {r.status_code}"
            else:
                r.raise_for_status()
                r.encoding = r.apparent_encoding
                html = r.text
                if _looks_like_valid_kind_table(html):
                    _PACER.on_success()      # ✅ 잘 돌면 지연이 다시 줄어든다
                    return html
                snippet = re.sub(r"\s+", " ", html)[:200]
                last_err = f"정상 테이블 아님: {snippet}"

        except requests.RequestException as e:
            last_err = f"{type(e).__name__}: {e}"

        # ⚠️ 여기부터가 "늘어질 때"— 이제서야 지연을 건다
        _PACER.on_trouble()
        if attempt < tries - 1:
            time.sleep((2 ** attempt) * 4 + random.uniform(0, 2))

    raise RuntimeError(
        f"KIND 요청 실패(재시도 {tries}회). 마지막 오류: {last_err}\n"
        "→ 403이 반복되면 배포 서버 IP가 차단된 상태일 가능성이 큽니다."
    )


# ─────────────────────────────────────────────────────────────
# 유틸 / 파싱
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
    """회사명 셀에서 시장/플래그/회사명/종목코드 추출"""
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

        out.append([no, ts, market, ",".join(flags), company_name,
                    code_num, title, docno, viewer, submitter])
    return out


def _make_df(rows: List[List[str]]) -> pd.DataFrame:
    """rows → DF, 문서번호 중복 제거 + 시간 내림차순 + 스팩 제외"""
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        rows,
        columns=["번호", "시간", "시장", "플래그", "회사명", "종목코드",
                 "공시제목", "문서번호", "뷰어URL", "제출인"]
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
    sleep: float = 0,
    report_nm: Optional[str] = None,
    report_cd: Optional[str] = None,
) -> pd.DataFrame:
    """
    KIND 상세검색(카테고리) 페이지네이션 수집.
    반환 컬럼:
      [페이지, 번호, 시간, 시장, 플래그, 회사명, 종목코드, 공시제목, 문서번호, 뷰어URL, 제출인]
    """
    f = _date_to_str(from_date)
    t = _date_to_str(to_date)

    referer = f"{KIND_URL}?method=searchDetailsMain&disclosureType=02&disTypevalue={code}"

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
        "disclosureType01": "", "disclosureType03": "", "disclosureType04": "", "disclosureType05": "",
        "disclosureType06": "", "disclosureType07": "", "disclosureType08": "", "disclosureType09": "",
        "disclosureType10": "", "disclosureType11": "", "disclosureType13": "", "disclosureType14": "",
        "disclosureType20": "", "pDisclosureType01": "", "pDisclosureType03": "", "pDisclosureType04": "",
        "pDisclosureType05": "", "pDisclosureType06": "", "pDisclosureType07": "", "pDisclosureType08": "",
        "pDisclosureType09": "", "pDisclosureType10": "", "pDisclosureType11": "", "pDisclosureType13": "",
        "pDisclosureType14": "", "pDisclosureType20": "", "searchCodeType": "", "repIsuSrtCd": "",
        "allRepIsuSrtCd": "", "oldSearchCorpName": "", "searchCorpName": "",
        "business": "", "marketType": "", "settlementMonth": "", "securities": "", "submitOblgNm": "",
        "enterprise": "",
    }

    cols = ["페이지", "번호", "시간", "시장", "플래그", "회사명", "종목코드",
            "공시제목", "문서번호", "뷰어URL", "제출인"]
    rows: List[List[str]] = []

    for page in range(1, max_pages + 1):
        data["pageIndex"] = str(page)
        html = _post_kind(data, referer=referer)

        added = 0
        for row in _parse_rows_html(html):
            rows.append([page] + row)
            added += 1

        if added == 0 or added < int(page_size):
            break
        # 페이싱은 _post_kind 내부의 _PACER가 담당 (정상이면 대기 없음)

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
# 투자경고·위험 / 단기과열 / 시장감시위원회 / 상장폐지 (warn 페이로드)
# ─────────────────────────────────────────────────────────────
BASE_PAYLOAD_WARN = {
    "method": "searchDetailsSub", "currentPageSize": "15", "pageIndex": "1",
    "orderMode": "1", "orderStat": "D", "forward": "details_sub",
    "disclosureType01": "", "disclosureType02": "", "disclosureType03": "", "disclosureType04": "",
    "disclosureType05": "", "disclosureType06": "", "disclosureType07": "", "disclosureType08": "",
    "disclosureType09": "", "disclosureType10": "", "disclosureType11": "", "disclosureType13": "",
    "disclosureType14": "", "disclosureType20": "",
    "pDisclosureType01": "", "pDisclosureType02": "", "pDisclosureType03": "", "pDisclosureType04": "",
    "pDisclosureType05": "", "pDisclosureType06": "", "pDisclosureType07": "", "pDisclosureType08": "",
    "pDisclosureType09": "", "pDisclosureType10": "", "pDisclosureType11": "", "pDisclosureType13": "",
    "pDisclosureType14": "", "pDisclosureType20": "",
    "searchCodeType": "", "repIsuSrtCd": "", "allRepIsuSrtCd": "", "oldSearchCorpName": "",
    "disclosureType": "", "disTypevalue": "",
    "searchCorpName": "", "business": "", "marketType": "", "settlementMonth": "",
    "securities": "", "submitOblgNm": "", "enterprise": "",
    "bfrDsclsType": "on",
}

TARGETS_WARN: List[Tuple[str, str, str, str]] = [
    ("투자경고종목지정", "68809", "투자경고종목 지정", "투자경고종목 지정"),
    ("투자경고종목지정", "70804", "투자경고종목지정", "투자경고종목지정"),
    ("투자경고종목지정(재지정)", "68823", "투자경고종목 지정(재지정)", "투자경고종목 지정(재지정)"),
    ("투자경고종목지정(재지정)", "72049", "투자경고종목지정(재지정)", "투자경고종목지정(재지정)"),
    ("투자경고종목지정해제", "68824", "투자경고종목 지정해제", "투자경고종목 지정해제"),
    ("투자경고종목지정해제", "72056", "투자경고종목 지정해제", "투자경고종목 지정해제"),
    ("[투자주의]투자경고종목지정해제및재지정예고", "70820",
     "[투자주의]투자경고종목 지정해제 및 재지정 예고",
     "[투자주의]투자경고종목 지정해제 및 재지정 예고"),
    ("[투자주의]투자경고종목지정해제및재지정예고", "68810",
     "[투자주의]투자경고종목 지정해제 및 재지정 예고",
     "[투자주의]투자경고종목 지정해제 및 재지정 예고"),
    ("투자위험종목지정", "68812", "투자위험종목지정", "투자위험종목지정"),
    ("투자위험종목지정", "70832", "투자위험종목지정", "투자위험종목지정"),
    ("투자위험종목지정해제", "68813", "투자위험종목지정해제", "투자위험종목지정해제"),
    ("투자위험종목지정해제", "70834", "투자위험종목지정해제", "투자위험종목지정해제"),
]

TARGETS_MARKET_WATCH: List[Tuple[str, str, str, str]] = [
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
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 접속매매 방식 재개)",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 접속매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(유가증권시장/종가단일가매매방식재개)", "99459",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 종가단일가매매 방식 재개)",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 종가단일가매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(유가증권시장/시간외단일가매매방식재개)", "99462",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 시간외단일가매매 방식 재개)",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 시간외단일가매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(유가증권시장/시간외종가매매방식재개)", "99461",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 시간외종가매매 방식 재개)",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 시간외종가매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(유가증권시장/시간외종가매매호가접수시간대재개)", "99460",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 시간외종가매매 호가접수시간대 재개)",
     "장애종목 매매거래재개 시장안내 (유가증권시장 / 시간외종가매매 호가접수시간대 재개)"),
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
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 접속매매 방식 재개)",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 접속매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(코스닥시장/종가단일가매매방식재개)", "72118",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 종가단일가매매 방식 재개)",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 종가단일가매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(코스닥시장/시간외단일가매매방식재개)", "72121",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 시간외단일가매매 방식 재개)",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 시간외단일가매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(코스닥시장/시간외종가매매방식재개)", "72120",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 시간외종가매매 방식 재개)",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 시간외종가매매 방식 재개)"),
    ("장애종목매매거래재개시장안내(코스닥시장/시간외종가매매호가접수시간대재개)", "72119",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 시간외종가매매 호가접수시간대 재개)",
     "장애종목 매매거래재개 시장안내 (코스닥시장 / 시간외종가매매 호가접수시간대 재개)"),
]

TARGETS_DELIST: List[Tuple[str, str, str, str]] = [
    ("상장폐지", "68051", "상장폐지", "상장폐지"),   # 유가증권
    ("상장폐지", "70769", "상장폐지", "상장폐지"),   # 코스닥
]


def _fetch_reportcd_with_warn_payload(
    from_date: str,
    to_date: str,
    targets: List[Tuple[str, str, str, str]],
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    sleep: float = 0,
) -> pd.DataFrame:
    f = _date_to_str(from_date)
    t = _date_to_str(to_date)
    rows: List[List[str]] = []

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
            html = _post_kind(payload)

            before = len(rows)
            rows += _parse_rows_html(html)
            added = len(rows) - before

            if added == 0 or added < int(page_size):
                break

    return _make_df(rows)


def fetch_investor_warning(
    from_date: str, to_date: str, *,
    page_size: int = 100, max_pages: int = 1000, sleep: float = 0,
) -> pd.DataFrame:
    """투자경고·위험: 여러 reportCd × 페이지네이션 전체 수집 → 문서번호 중복 제거."""
    return _fetch_reportcd_with_warn_payload(
        from_date, to_date, TARGETS_WARN,
        page_size=page_size, max_pages=max_pages, sleep=sleep
    )


def fetch_shortterm_overheat(
    from_date: str, to_date: str, *,
    page_size: int = 100, max_pages: int = 1000, sleep: float = 0,
) -> pd.DataFrame:
    """단기과열: reportNm='단기과열' 단일 조건 페이지네이션 수집."""
    f = _date_to_str(from_date)
    t = _date_to_str(to_date)
    rows: List[List[str]] = []

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
        html = _post_kind(payload)

        before = len(rows)
        rows += _parse_rows_html(html)
        added = len(rows) - before

        if added == 0 or added < int(page_size):
            break

    return _make_df(rows)


def fetch_market_watch(
    from_date: str, to_date: str, *,
    page_size: int = 100, max_pages: int = 1000, sleep: float = 0,
) -> pd.DataFrame:
    """시장감시위원회: reportCd 목록을 warn 페이로드 방식으로 조회."""
    return _fetch_reportcd_with_warn_payload(
        from_date, to_date, TARGETS_MARKET_WATCH,
        page_size=page_size, max_pages=max_pages, sleep=sleep
    )


def fetch_delist(
    from_date: str, to_date: str, *,
    page_size: int = 100, max_pages: int = 1000, sleep: float = 0,
) -> pd.DataFrame:
    """상장폐지: 유가증권(68051) + 코스닥(70769)."""
    return _fetch_reportcd_with_warn_payload(
        from_date, to_date, TARGETS_DELIST,
        page_size=page_size, max_pages=max_pages, sleep=sleep
    )


# ─────────────────────────────────────────────────────────────
# 진단용 — 배포 환경에서 어디서 막히는지 확인
# ─────────────────────────────────────────────────────────────
def diagnose(timeout: int = 20) -> dict:
    """
    menu2.py에서:
        import fnc2; st.json(fnc2.diagnose())
    로 호출해 배포 서버에서 어느 단계가 막히는지 확인.
    """
    out = {}
    s = requests.Session()
    s.headers.update(NAV_HEADERS)

    for label, url, params in [
        ("main_get", MAIN_URL, {"method": "loadInitPage"}),
        ("details_get", KIND_URL, {"method": "searchDetailsMain"}),
    ]:
        try:
            r = s.get(url, params=params, timeout=timeout, verify=False)
            out[label] = {"status": r.status_code, "len": len(r.text)}
        except Exception as e:
            out[label] = {"error": f"{type(e).__name__}: {e}"}

    out["cookies"] = {k: v[:8] + "..." for k, v in s.cookies.get_dict().items()}

    # 최소 POST 1회
    try:
        payload = {
            **BASE_PAYLOAD_WARN,
            "currentPageSize": "15", "pageIndex": "1",
            "fromDate": "2026-07-01", "toDate": "2026-07-31",
            "reportNm": "상장폐지", "reportCd": "68051",
            "reportNmTemp": "상장폐지", "reportNmPop": "상장폐지",
        }
        r = s.post(KIND_URL, data=payload,
                   headers={**AJAX_HEADERS, "Referer": DETAILS_REFERER},
                   timeout=timeout, verify=False)
        out["details_post"] = {
            "status": r.status_code,
            "len": len(r.text),
            "valid_table": _looks_like_valid_kind_table(r.text),
            "snippet": re.sub(r"\s+", " ", r.text)[:200],
        }
    except Exception as e:
        out["details_post"] = {"error": f"{type(e).__name__}: {e}"}

    # 비교군: data.krx.co.kr 도 막히는지 (= IP 차단 여부 판별)
    try:
        r = s.get("https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd",
                  timeout=timeout, verify=False)
        out["data_krx_get"] = {"status": r.status_code, "len": len(r.text)}
    except Exception as e:
        out["data_krx_get"] = {"error": f"{type(e).__name__}: {e}"}

    s.close()
    return out
