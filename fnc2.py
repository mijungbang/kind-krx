# fnc2_selenium.py
# ─────────────────────────────────────────────────────────────
# kind-krx의 fnc2.py를 Selenium 기반으로 전환한 버전.
#
# 동작 원리
#   1) Selenium으로 실제 Chrome을 띄워 KIND 상세검색 페이지에 접속
#      → 정상적인 쿠키 / TLS·브라우저 지문 / Referer가 자동으로 확보됨
#   2) 그 브라우저 "안에서" JavaScript fetch로 기존과 동일한
#      searchDetailsSub POST를 실행하고 HTML을 돌려받음
#   3) 이후 파싱(BeautifulSoup)·DataFrame 가공은 원본 fnc2.py와 동일
#
# 공개 API는 원본과 동일해서 노트북/menu2.py 쪽 수정이 거의 필요 없음:
#   kind_fetch, fetch_investor_warning, fetch_shortterm_overheat,
#   fetch_market_watch, fetch_delist
#
# 필요 패키지: selenium>=4.10 (Selenium Manager가 chromedriver 자동 관리),
#              beautifulsoup4, pandas, lxml
#   pip install selenium beautifulsoup4 pandas lxml
# ─────────────────────────────────────────────────────────────
from __future__ import annotations

import atexit
import re
import time
from typing import Optional, Dict, List, Tuple

import pandas as pd
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import WebDriverException
import shutil

__all__ = [
    "CODE_MAP",
    "KindBrowser",
    "kind_fetch",
    "fetch_investor_warning",
    "fetch_shortterm_overheat",
    "fetch_market_watch",
    "fetch_delist",
    "close_browser",
]

# ─────────────────────────────────────────────────────────────
# 상수 (원본 fnc2.py와 동일)
# ─────────────────────────────────────────────────────────────
BASE = "https://kind.krx.co.kr"
KIND_URL = f"{BASE}/disclosure/details.do"

VIEWER_BASE = (
    "https://kind.krx.co.kr/common/disclsviewer.do?"
    "method=search&acptno={docno}&docno=&viewerhost=&viewerport="
)

CODE_MAP: Dict[str, str] = {
    "halt":  "0311",  # 거래정지/재개
    "mgmt":  "0350",  # 관리종목
    "alert": "0356",  # 투자주의·환기
    "misc":  "0305",  # 기타 시장안내
}

# ─────────────────────────────────────────────────────────────
# Selenium 브라우저 래퍼
# ─────────────────────────────────────────────────────────────
class KindBrowser:
    """
    KIND용 Chrome 세션 관리자.
    - warm(): 상세검색 페이지를 실제로 로드해 세션 확보
    - post_search(payload): 브라우저 내부 fetch로 searchDetailsSub POST 실행
    """

    def __init__(self, headless: bool = True, page_load_timeout: int = 60):
        opts = Options()
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1400,900")
        opts.add_argument("--lang=ko-KR")
        # 자동화 흔적 최소화
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_argument("--disable-blink-features=AutomationControlled")

        # ── 브라우저/드라이버 자동 탐지 ──────────────────────
        # Streamlit Cloud(리눅스): packages.txt로 설치된 chromium 사용
        # 로컬 PC: 설치된 Chrome + Selenium Manager가 드라이버 자동 관리
        chrome_bin = (
            shutil.which("chromium")
            or shutil.which("chromium-browser")
            or shutil.which("google-chrome")
        )
        driver_bin = shutil.which("chromedriver")

        if chrome_bin and "chromium" in chrome_bin:
            opts.binary_location = chrome_bin

        if driver_bin:
            self.driver = webdriver.Chrome(service=Service(driver_bin), options=opts)
        else:
            self.driver = webdriver.Chrome(options=opts)
        self.driver.set_page_load_timeout(page_load_timeout)
        self.driver.set_script_timeout(page_load_timeout)

        # navigator.webdriver 숨기기
        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluatedOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
            )
        except Exception:
            pass

        self._warmed_key: Optional[str] = None

    # -- 세션 확보 ------------------------------------------------
    def warm(self, dis_type_value: str = "") -> None:
        """상세검색 페이지 실제 로드 (카테고리별로 한 번씩만)."""
        key = dis_type_value or "_main"
        if self._warmed_key == key:
            return
        if dis_type_value:
            url = f"{KIND_URL}?method=searchDetailsMain&disclosureType=02&disTypevalue={dis_type_value}"
        else:
            url = f"{KIND_URL}?method=searchDetailsMain"
        self.driver.get(url)
        time.sleep(1.0)  # 페이지 초기 스크립트 실행 대기
        self._warmed_key = key

    # -- 브라우저 내부 fetch로 POST ---------------------------------
    _FETCH_JS = """
        const payload = arguments[0];
        const done = arguments[arguments.length - 1];
        const body = new URLSearchParams();
        for (const [k, v] of Object.entries(payload)) body.append(k, v);
        fetch('/disclosure/details.do', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: body.toString(),
            credentials: 'include'
        })
        .then(r => r.text())
        .then(t => done({ok: true, html: t}))
        .catch(e => done({ok: false, error: String(e)}));
    """

    def post_search(self, payload: Dict[str, str], *, retries: int = 3, backoff: float = 5.0) -> str:
        """searchDetailsSub POST → HTML. 차단 감지 시 페이지 재로드 후 재시도."""
        last_err = ""
        for attempt in range(1, retries + 1):
            try:
                res = self.driver.execute_async_script(self._FETCH_JS, payload)
            except WebDriverException as e:
                res = {"ok": False, "error": f"webdriver: {e}"}

            if res.get("ok"):
                html = res.get("html", "")
                if _looks_like_valid_kind_table(html):
                    return html
                last_err = "응답이 정상 테이블이 아님(차단/빈응답 가능): " + re.sub(r"\s+", " ", html)[:200]
            else:
                last_err = res.get("error", "unknown fetch error")

            # 재시도 전: 세션 리프레시
            if attempt < retries:
                time.sleep(backoff * attempt)
                self._warmed_key = None
                self.warm(payload.get("disclosureTypeArr02", ""))

        raise RuntimeError(f"KIND(Selenium) 요청 실패 ({retries}회 시도): {last_err}")

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass


# 모듈 전역 브라우저 (원본의 requests.Session 역할)
_browser: Optional[KindBrowser] = None


def _get_browser(headless: bool = True) -> KindBrowser:
    global _browser
    if _browser is None:
        _browser = KindBrowser(headless=headless)
        atexit.register(close_browser)
    return _browser


def close_browser():
    """전역 브라우저 종료 (노트북 마지막 셀에서 호출 권장)."""
    global _browser
    if _browser is not None:
        _browser.close()
        _browser = None


# ─────────────────────────────────────────────────────────────
# 유틸 (원본 fnc2.py와 동일)
# ─────────────────────────────────────────────────────────────
def _date_to_str(d) -> str:
    if isinstance(d, pd.Timestamp):
        return d.strftime("%Y-%m-%d")
    s = str(d)
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _extract_company_cell(company_td) -> Tuple[str, List[str], str, str]:
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
        m = re.search(r"companysummary_open\('(\w+)'\)", comp_a["onclick"])
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

        market, flags, company_name, code_num = _extract_company_cell(tds[2])

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
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        rows,
        columns=["번호","시간","시장","플래그","회사명","종목코드","공시제목","문서번호","뷰어URL","제출인"]
    )
    df = df.drop_duplicates(subset=["문서번호"], keep="first")
    df["__ts"] = pd.to_datetime(df["시간"], errors="coerce")
    df = df.sort_values("__ts", ascending=False).drop(columns="__ts")
    df["회사명"] = df["회사명"].astype(str)
    df = df[~df["회사명"].str.contains("스팩", na=False)]
    return df.reset_index(drop=True)


def _looks_like_valid_kind_table(html: str) -> bool:
    return ('table class="list type-00 mt10"' in html) or ("list type-00 mt10" in html)


# ─────────────────────────────────────────────────────────────
# 페이로드 (원본 fnc2.py와 동일)
# ─────────────────────────────────────────────────────────────
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

TARGETS_DELIST: List[Tuple[str,str,str,str]] = [
    ("상장폐지", "68051", "상장폐지", "상장폐지"),   # 유가증권
    ("상장폐지", "70769", "상장폐지", "상장폐지"),   # 코스닥
]


# ─────────────────────────────────────────────────────────────
# 공통 상세검색 (카테고리: halt/mgmt/alert/misc)
# ─────────────────────────────────────────────────────────────
def _kind_disclosure_search(
    from_date: str,
    to_date: str,
    code: str,
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    sleep: float = 2,
    headless: bool = True,
    report_nm: Optional[str] = None,
    report_cd: Optional[str] = None,
) -> pd.DataFrame:
    f = _date_to_str(from_date)
    t = _date_to_str(to_date)

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

    br = _get_browser(headless=headless)
    br.warm(code)

    for page in range(1, max_pages + 1):
        data["pageIndex"] = str(page)
        html = br.post_search(data)

        added = 0
        for row in _parse_rows_html(html):
            rows.append([page] + row)
            added += 1

        if added == 0 or added < int(page_size):
            break
        if sleep:
            time.sleep(sleep)

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
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
    headless: bool = True,
) -> pd.DataFrame:
    """cat 기반: halt/mgmt/alert/misc (원본과 동일 시그니처 + headless 옵션)"""
    code = CODE_MAP[category]
    df = _kind_disclosure_search(
        from_date, to_date, code,
        page_size=page_size, max_pages=max_pages,
        report_nm=report_nm, report_cd=report_cd,
        headless=headless,
    )
    return df.reset_index(drop=True) if df is not None and not df.empty else pd.DataFrame()


# ─────────────────────────────────────────────────────────────
# 투자경고·위험 / 단기과열 / 시장감시위원회 / 상장폐지
# ─────────────────────────────────────────────────────────────
def _fetch_reportcd_with_warn_payload(
    from_date: str,
    to_date: str,
    targets: List[Tuple[str,str,str,str]],
    *,
    page_size: int = 100,
    max_pages: int = 1000,
    sleep: float = 2,
    headless: bool = True,
) -> pd.DataFrame:
    f = _date_to_str(from_date)
    t = _date_to_str(to_date)

    rows: List[List[str]] = []
    br = _get_browser(headless=headless)
    br.warm("")  # searchDetailsMain 기본 페이지

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
            html = br.post_search(payload)

            before = len(rows)
            rows += _parse_rows_html(html)
            added = len(rows) - before

            if added == 0 or added < int(page_size):
                break
            if sleep:
                time.sleep(sleep)

    return _make_df(rows)


def fetch_investor_warning(from_date, to_date, *, page_size=100, max_pages=1000, sleep=2, headless=True) -> pd.DataFrame:
    """투자경고·위험: 여러 reportCd × 페이지네이션 전체 수집 → 문서번호 중복 제거."""
    return _fetch_reportcd_with_warn_payload(
        from_date, to_date, TARGETS_WARN,
        page_size=page_size, max_pages=max_pages, sleep=sleep, headless=headless,
    )


def fetch_shortterm_overheat(from_date, to_date, *, page_size=100, max_pages=1000, sleep=2, headless=True) -> pd.DataFrame:
    """단기과열: reportNm='단기과열' 단일 조건 페이지네이션 수집."""
    f = _date_to_str(from_date)
    t = _date_to_str(to_date)

    rows: List[List[str]] = []
    br = _get_browser(headless=headless)
    br.warm("")

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
        html = br.post_search(payload)

        before = len(rows)
        rows += _parse_rows_html(html)
        added = len(rows) - before

        if added == 0 or added < int(page_size):
            break
        if sleep:
            time.sleep(sleep)

    return _make_df(rows)


def fetch_market_watch(from_date, to_date, *, page_size=100, max_pages=1000, sleep=2, headless=True) -> pd.DataFrame:
    """시장감시위원회: 지정 reportCd 목록 조회."""
    return _fetch_reportcd_with_warn_payload(
        from_date, to_date, TARGETS_MARKET_WATCH,
        page_size=page_size, max_pages=max_pages, sleep=sleep, headless=headless,
    )


def fetch_delist(from_date, to_date, *, page_size=100, max_pages=1000, sleep=2, headless=True) -> pd.DataFrame:
    """상장폐지: 유가증권(68051) + 코스닥(70769)."""
    return _fetch_reportcd_with_warn_payload(
        from_date, to_date, TARGETS_DELIST,
        page_size=page_size, max_pages=max_pages, sleep=sleep, headless=headless,
    )


# ─────────────────────────────────────────────────────────────
# 단독 실행 테스트
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import datetime as dt
    today = dt.date.today()
    week_ago = today - dt.timedelta(days=7)
    try:
        df = fetch_delist(str(week_ago), str(today), headless=True)
        print(f"상장폐지 공시 {len(df)}건")
        print(df.head(10).to_string())
    finally:
        close_browser()
