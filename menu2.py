# menu2.py
# 상장폐지 추가, 종목코드 매핑 전환
# + 403 대응: 진행률 표시 / 소스별 부분 실패 허용 / 세션 리셋 / 진단 버튼
from __future__ import annotations

import streamlit as st
import pandas as pd
import datetime, json, re, time
from zoneinfo import ZoneInfo
from streamlit.components.v1 import html
from html import escape

from fnc2 import (
    kind_fetch,                # cat 기반(1/2/3/6)
    fetch_investor_warning,    # 4️⃣ 투자경고·위험
    fetch_shortterm_overheat,  # 5️⃣ 단기과열
    fetch_market_watch,        # ✅ 시장감시위원회 - halt/multi에만 합침
    fetch_delist,              # ⚠️ 상장폐지
)

# 신버전 fnc2.py에만 있는 함수들 (구버전 호환)
try:
    from fnc2 import reset_session as _kind_reset_session
except Exception:
    def _kind_reset_session():
        return None

try:
    from fnc2 import diagnose as _kind_diagnose
except Exception:
    _kind_diagnose = None

try:
    from fnc2 import pacer_status as _kind_pacer_status
except Exception:
    def _kind_pacer_status():
        return {"delay": 0}

# NXT 종목 조회 (환경에 따라 없을 수 있으므로 안전 처리)
try:
    from fnc import get_nextrade_filtered_symbols   # (trade_date, df)
except Exception:
    def get_nextrade_filtered_symbols(yyyymmdd: str):
        return yyyymmdd, pd.DataFrame(columns=["종목명"])   # 안전 Fallback


# ─────────────────────────────────────────────────────────────
# 상수/유틸
# ─────────────────────────────────────────────────────────────
HALT_PATTERN = re.compile(r"(매매)?거래정지|정지해제|거래정지해제|거래정지기간", re.IGNORECASE)
INV_SUFFIX_EXCLUDE = re.compile(r"\((?:[^)]*우B?)\)\s*$")

OVERHEAT_PATTERN = re.compile(r"단기과열", re.IGNORECASE)
FORECAST_PREFIX = re.compile(r"^\(예고\)")

# 메뉴 스펙(키, 라벨, 들여쓰기 레벨)
MENU_SPEC = [
    ("multi",    "✅ NXT종목 모아보기",     0),
    ("halt",     "1️⃣ 거래정지/재개 종목",   1),
    ("mgmt",     "2️⃣ 관리종목",           1),
    ("alert",    "3️⃣ 투자주의환기 종목",    1),
    ("inv",      "4️⃣ 투자경고·위험 종목",   1),
    ("overheat", "5️⃣ 단기과열 종목",       1),
    ("misc",     "6️⃣ 기타 시장안내",       1),
    ("delist",   "⚠️ 상장폐지",            1),
]

FETCHER_MAP = {
    "multi":    ("multi", None,    None),
    "halt":     ("cat",   "halt",  HALT_PATTERN),
    "mgmt":     ("cat",   "mgmt",  None),
    "alert":    ("cat",   "alert", None),
    "inv":      ("inv",   None,    None),
    "overheat": ("overheat", None, None),
    "misc":     ("cat",   "misc",  None),
    "delist":   ("delist", None,   None),
}

# 메뉴별 대략적인 KIND 요청 횟수 (예상 소요시간 안내용)
APPROX_REQUESTS = {
    "multi": 43, "halt": 25, "mgmt": 1, "alert": 1,
    "inv": 12, "overheat": 1, "misc": 1, "delist": 2,
}


def _menu_label(key: str) -> str:
    for k, label, level in MENU_SPEC:
        if k == key:
            return ("　" * level) + label
    return key


def _last_weekday(d: datetime.date) -> datetime.date:
    wd = d.weekday()          # 월0..일6
    if wd == 5:               # 토
        return d - datetime.timedelta(days=1)
    if wd == 6:               # 일
        return d - datetime.timedelta(days=2)
    return d


def _coerce_date_pair(s, e, default_start, default_end):
    import datetime as _dt
    if not isinstance(s, _dt.date):
        s = default_start
    if not isinstance(e, _dt.date):
        e = default_end
    if s > e:
        s, e = e, s
    return s, e


def _drop_pref(df: pd.DataFrame) -> pd.DataFrame:
    """우선주 접미 공시 제외."""
    if df is None or df.empty:
        return df
    return df[~df["공시제목"].astype(str).str.contains(INV_SUFFIX_EXCLUDE, na=False)]


def style_today_rows(df: pd.DataFrame):
    highlight = "background-color: #e8f4ff; font-weight: 600;"
    def _row_style(row: pd.Series):
        return [highlight] * len(row) if row.get("당일", "") == "🟡" else [""] * len(row)
    return df.style.apply(_row_style, axis=1)


def style_nxt_rows(df: pd.DataFrame, nxt_codes: set):
    highlight = "background-color: #fff4b6; font-weight: 600;"
    def _row_style(row: pd.Series):
        return [highlight] * len(row) if str(row.get("종목코드", "")) in nxt_codes else [""] * len(row)
    return df.style.apply(_row_style, axis=1)


def build_display_df(df: pd.DataFrame, ref_date: datetime.date) -> pd.DataFrame:
    ts = pd.to_datetime(df.get("시간", ""), errors="coerce")
    time_disp = ts.dt.strftime("%y/%m/%d %H:%M").fillna("")
    is_today = ts.dt.date.eq(ref_date)

    out = (
        pd.DataFrame({
            "당일":   is_today.map(lambda x: "🟡" if x else ""),
            "시간":   time_disp,
            "종목명": df.get("회사명", "").astype(str),
            "종목코드": df.get("종목코드", "").astype(str),
            "공시제목": df.get("뷰어URL", "").astype(str),
        })
        .sort_values("시간", ascending=False)
        .reset_index(drop=True)
    )
    return out


def _split_title_and_link(url_series: pd.Series) -> tuple[pd.Series, pd.Series]:
    url_series = url_series.astype(str)
    title = url_series.str.extract(r"#(.+)$")[0]
    title = title.where(title.notna() & (title != ""), url_series)
    link = url_series.str.replace(r"#.+$", "", regex=True)
    return title, link


def _make_copy_df(df_display: pd.DataFrame) -> pd.DataFrame:
    cols = ["당일", "시간", "종목명"]
    tmp = df_display.copy()
    title, link = _split_title_and_link(tmp["공시제목"])
    tmp["공시제목"], tmp["링크"] = title, link
    if "비고" in tmp.columns:
        cols.append("비고")
    return tmp[cols + ["공시제목", "링크"]]


def _df_height(df: pd.DataFrame,
               base_row_height: int = 30,
               header_height: int = 35,
               max_height: int = 550,
               min_height: int = 150) -> int:
    rows = max(len(df), 1)
    h = header_height + base_row_height * rows + 15
    return max(min_height, min(h, max_height))


def render_header_with_copy(copy_id: str, caption_text: str, df_display: pd.DataFrame):
    """캡션(좌) + 복사 버튼(우)을 한 줄에 배치."""
    safe_caption = escape(caption_text).replace("\n", "<br>")
    copy_df = _make_copy_df(df_display)
    clipboard = copy_df.to_csv(sep="\t", index=False)
    js_text = json.dumps(clipboard)

    col1, col2 = st.columns([5, 1.5])
    with col1:
        st.markdown(
            f"""
            <div style="display:flex; align-items:flex-end; height:100%;
                        margin: 0 0 2px 0; font-size: 0.9rem; line-height: 1.2;
                        color: rgba(49,51,63,0.75);">{safe_caption}</div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        html(
            f"""
            <div style="display:flex; justify-content:flex-end; align-items:flex-end; margin: 0 0 2px 0;">
              <button id="{copy_id}" onclick="copy_{copy_id}()" style="
                font-size:15px; padding:6px 12px; width:180px;
                background-color:#4CAF50; color:white; border:none; border-radius:4px;">
                📋 복사
              </button>
            </div>
            <script>
            function copy_{copy_id}() {{
              const text = {js_text};
              navigator.clipboard.writeText(text).then(() => {{
                var b=document.getElementById("{copy_id}");
                b.innerText="✅ 복사 완료"; b.style.backgroundColor="#777";
                setTimeout(()=>{{b.innerText="📋 복사"; b.style.backgroundColor="#4CAF50";}},2000);
              }});
            }}
            </script>
            """,
            height=50,
        )


# ─────────────────────────────────────────────────────────────
# halt(cat) + mw(reportCd) 병합 유틸
# ─────────────────────────────────────────────────────────────
def _merge_frames(dfs: list) -> pd.DataFrame:
    dfs = [d for d in dfs if d is not None and not d.empty]
    if not dfs:
        return pd.DataFrame()
    merged = pd.concat(dfs, ignore_index=True, sort=False)
    if "문서번호" in merged.columns:
        merged = merged.drop_duplicates(subset=["문서번호"], keep="first")
    if "시간" in merged.columns:
        merged["__ts"] = pd.to_datetime(merged["시간"], errors="coerce")
        merged = merged.sort_values("__ts", ascending=False).drop(columns="__ts")
    return merged.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# 데이터 페치
#   반환: (DataFrame, 실패한 소스 메시지 리스트)
#   _progress: 언더스코어 접두 → st.cache_data가 해싱하지 않음
# ─────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=60)
def _fetch(menu_key: str, f: str, t: str, page_size: int = 100,
           nonce: int = 0, _progress=None) -> tuple[pd.DataFrame, list]:
    _ = nonce
    ftype, arg, patt = FETCHER_MAP[menu_key]

    def tick(i, n, label):
        if _progress:
            _progress(i / n, label)

    if ftype == "multi":
        return _fetch_multi(f, t, page_size, nonce=nonce, _progress=_progress)

    if ftype == "inv":
        tick(0, 1, "투자경고·위험 (12개 코드)")
        df = _drop_pref(fetch_investor_warning(f, t, page_size=page_size))
        tick(1, 1, "완료")
        return (df.reset_index(drop=True) if df is not None and not df.empty else pd.DataFrame()), []

    if ftype == "overheat":
        tick(0, 1, "단기과열")
        df = _drop_pref(fetch_shortterm_overheat(f, t, page_size=page_size))
        tick(1, 1, "완료")
        return (df.reset_index(drop=True) if df is not None and not df.empty else pd.DataFrame()), []

    if ftype == "delist":
        tick(0, 1, "상장폐지 (2개 코드)")
        df = _drop_pref(fetch_delist(f, t, page_size=page_size))
        tick(1, 1, "완료")
        return (df.reset_index(drop=True) if df is not None and not df.empty else pd.DataFrame()), []

    # cat 계열
    if arg == "halt":
        # halt는 cat + market_watch 두 소스 → 한쪽이 죽어도 나머지는 살린다
        errors = []
        tick(0, 2, "거래정지/재개 (카테고리)")
        try:
            df_halt = kind_fetch("halt", f, t, page_size=page_size)
            if df_halt is not None and not df_halt.empty and patt is not None:
                df_halt = df_halt[df_halt["공시제목"].astype(str).str.contains(patt, na=False)]
        except Exception as e:
            df_halt, _ = pd.DataFrame(), errors.append(f"거래정지 카테고리: {e}")

        tick(1, 2, "시장감시위원회 (24개 코드)")
        try:
            df_mw = _drop_pref(fetch_market_watch(f, t, page_size=page_size))
        except Exception as e:
            df_mw, _ = pd.DataFrame(), errors.append(f"시장감시위원회: {e}")

        tick(2, 2, "완료")
        if errors and df_halt.empty and (df_mw is None or df_mw.empty):
            raise RuntimeError(" / ".join(errors))
        return _merge_frames([df_halt, df_mw]), errors

    tick(0, 1, _menu_label(menu_key).strip())
    df = kind_fetch(arg, f, t, page_size=page_size)
    tick(1, 1, "완료")
    return (df.reset_index(drop=True) if df is not None and not df.empty else pd.DataFrame()), []


@st.cache_data(show_spinner=False, ttl=60)
def _fetch_multi(f: str, t: str, page_size: int = 100,
                 nonce: int = 0, _progress=None) -> tuple[pd.DataFrame, list]:
    _ = nonce

    def cat_halt():
        df = kind_fetch("halt", f, t, page_size=page_size)
        if df is not None and not df.empty:
            df = df[df["공시제목"].astype(str).str.contains(HALT_PATTERN, na=False)]
        return df

    # (라벨, 수집함수) — 하나가 실패해도 나머지는 계속 진행
    steps = [
        ("거래정지/재개 (카테고리)",     cat_halt),
        ("시장감시위원회 (24개 코드)",   lambda: _drop_pref(fetch_market_watch(f, t, page_size=page_size))),
        ("관리종목",                   lambda: kind_fetch("mgmt", f, t, page_size=page_size)),
        ("투자주의환기",                lambda: kind_fetch("alert", f, t, page_size=page_size)),
        ("기타 시장안내",               lambda: kind_fetch("misc", f, t, page_size=page_size)),
        ("투자경고·위험 (12개 코드)",    lambda: _drop_pref(fetch_investor_warning(f, t, page_size=page_size))),
        ("단기과열",                   lambda: _drop_pref(fetch_shortterm_overheat(f, t, page_size=page_size))),
        ("상장폐지 (2개 코드)",         lambda: _drop_pref(fetch_delist(f, t, page_size=page_size))),
    ]

    frames, errors = [], []
    n = len(steps)
    for i, (label, fn) in enumerate(steps):
        if _progress:
            _progress(i / n, label)
        try:
            df = fn()
            if df is not None and not df.empty:
                frames.append(df)
        except Exception as e:
            errors.append(f"{label}: {e}")

    if _progress:
        _progress(1.0, "병합 중")

    # 전부 실패하면 예외로 올려서 기존 에러 화면을 띄운다
    if not frames and errors:
        raise RuntimeError(" / ".join(errors))

    return _merge_frames(frames), errors


# ─────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────
def run():
    st.set_page_config(
        page_title="KRX • NXT 공시 모니터",
        layout="centered",
        initial_sidebar_state="expanded",
    )

    SIDEBAR_PX = 380
    st.markdown(f"""
    <style>
    [data-testid="stSidebar"] {{
        min-width: {SIDEBAR_PX}px; max-width: {SIDEBAR_PX}px;
    }}
    [data-testid="stSidebar"] > div:first-child {{ width: {SIDEBAR_PX}px; }}
    #menu-radio-wrap [role="radiogroup"] {{
        display: flex; flex-direction: column; row-gap: 10px;
    }}
    #menu-radio-wrap [role="radiogroup"] > *:hover {{
        background: rgba(0,0,0,0.03); border-radius: 8px;
    }}
    @media (max-width: 1100px) {{
        [data-testid="stSidebar"] {{ min-width: 320px; max-width: 320px; }}
        [data-testid="stSidebar"] > div:first-child {{ width: 320px; }}
    }}
    </style>
    """, unsafe_allow_html=True)

    st.markdown("### 📡 KRX • NXT 공시 모니터")
    st.caption("최근 업데이트: ① 종목코드 기반 NXT매핑 ② 상장폐지 공시 추가 ③ 403 대응(재시도·부분수집)")

    st.session_state.setdefault("menu_cache", {})
    st.session_state.setdefault("force_nonce", 0)

    # ── 사이드바
    with st.sidebar:
        st.markdown("## 📆 KIND 조회 기간")
        today_kst = datetime.datetime.now(ZoneInfo("Asia/Seoul")).date()
        default_start = today_kst - datetime.timedelta(days=5)

        c1, c2 = st.columns(2)
        with c1:
            start_date = st.date_input("시작일", value=default_start, format="YYYY/MM/DD", key="start_date")
        with c2:
            end_date = st.date_input("종료일", value=today_kst, format="YYYY/MM/DD", key="end_date")

        d_start, d_end = _coerce_date_pair(start_date, end_date, default_start, today_kst)

        st.markdown("---")

        st.markdown("## ⚠️ KIND 시장조치 공시")
        st.markdown('<div id="menu-radio-wrap">', unsafe_allow_html=True)
        menu_key = st.radio(
            "카테고리 선택",
            options=[k for k, _, _ in MENU_SPEC],
            index=0,
            format_func=_menu_label,
            label_visibility="collapsed",
        )
        st.markdown("</div>", unsafe_allow_html=True)

        # 예상 소요시간 안내 (정상 응답 기준. 차단이 걸리면 자동으로 느려짐)
        n_req = APPROX_REQUESTS.get(menu_key, 1)
        if n_req >= 10:
            st.caption(f"⏱ KIND 요청 약 {n_req}회 · 정상 시 {n_req}~{n_req * 2}초")

        st.markdown("---")

        st.markdown("## 🔎 제목/종목/시간 검색")
        keyword = st.text_input(
            "공시제목 / 종목명 포함",
            value="",
            label_visibility="collapsed",
            placeholder="*(공란가능)키워드 입력",
        )
        case_sens = False

        TIME_START = [("00:00", datetime.time(0, 0)),
                      ("14:28", datetime.time(14, 28)),
                      ("14:30", datetime.time(14, 30))]
        TIME_END = [("09:00", datetime.time(9, 0)),
                    ("14:31", datetime.time(14, 31)),
                    ("23:59", datetime.time(23, 59))]

        map_start = {lbl: tm for lbl, tm in TIME_START}
        map_end = {lbl: tm for lbl, tm in TIME_END}

        cst, cet = st.columns(2)
        with cst:
            start_time_lbl = st.selectbox("시작", options=list(map_start), index=0,
                                          key="start_time_lbl", label_visibility="collapsed")
        with cet:
            end_time_lbl = st.selectbox("종료", options=list(map_end), index=len(map_end) - 1,
                                        key="end_time_lbl", label_visibility="collapsed")

        exclude_forecast_main = False
        if menu_key == "overheat":
            exclude_forecast_main = st.checkbox(
                "(예고) 공시 제외", value=True,
                help="체크 시 '(예고)'로 시작하는 공시는 숨깁니다.")

        exclude_forecast_multi = False
        if menu_key == "multi":
            exclude_forecast_multi = st.checkbox(
                "모아보기에서 단기과열 '(예고)' 공시 제외", value=True,
                help="체크 시 모아보기 결과 중 단기과열 공시에서만 '(예고)'로 시작하는 건 제외합니다.")

        # ── 버튼
        go = st.button("공시 조회", type="primary", use_container_width=True)

        cA, cB = st.columns(2)
        with cA:
            if st.button("🔄 강제 새로조회", use_container_width=True):
                st.session_state["force_nonce"] += 1
                _kind_reset_session()          # KIND 세션(쿠키)도 새로 발급
                st.session_state["auto_go"] = True
                st.toast("KIND 세션을 새로 열고 다시 조회합니다.", icon="🔄")
                st.rerun()
        with cB:
            if st.button("🧹 초기화", use_container_width=True):
                _kind_reset_session()
                st.cache_data.clear()
                st.cache_resource.clear()
                st.session_state.clear()
                st.toast("캐시/세션을 초기화했습니다.", icon="🧹")
                st.rerun()

        # 진단
        if _kind_diagnose is not None:
            with st.expander("🩺 접속 진단"):
                st.caption("배포 환경에서 KIND가 어느 단계부터 막히는지 확인합니다.")
                if st.button("진단 실행", use_container_width=True):
                    with st.spinner("진단 중..."):
                        try:
                            st.session_state["diag"] = _kind_diagnose()
                        except Exception as e:
                            st.session_state["diag"] = {"error": str(e)}
                if st.session_state.get("diag"):
                    st.json(st.session_state["diag"], expanded=False)

    # 강제 새로조회 후 자동 실행
    go = go or st.session_state.pop("auto_go", False)

    if d_start > d_end:
        st.error("시작일이 종료일보다 이후입니다.")
        return

    f = d_start.strftime("%Y-%m-%d")
    t = d_end.strftime("%Y-%m-%d")

    cache_key = (menu_key, f, t)
    df_raw: pd.DataFrame | None = None
    fetch_errors: list = []

    if go:
        prog_box = st.container()
        with prog_box:
            bar = st.progress(0.0, text="KIND 접속 준비 중...")

        def _progress(frac: float, label: str):
            try:
                bar.progress(min(max(frac, 0.0), 1.0), text=f"수집 중 · {label}")
            except Exception:
                pass

        t0 = time.time()
        try:
            df_raw, fetch_errors = _fetch(
                menu_key, f, t, page_size=100,
                nonce=st.session_state["force_nonce"],
                _progress=_progress,
            )
        except Exception as e:
            bar.empty()
            st.error("KIND 응답이 비정상입니다(차단/오류 가능).")
            st.code(str(e))
            if "403" in str(e):
                st.warning(
                    "**403은 서버 IP 차단일 가능성이 큽니다.** "
                    "사이드바의 🩺 접속 진단을 실행해 `main_get` / `details_get` 단계부터 "
                    "막히는지 확인해 보세요. GET부터 403이면 코드로는 해결되지 않고, "
                    "국내 리전(예: Oracle Cloud 춘천/서울, NHN Cloud)으로 옮겨야 합니다."
                )
            else:
                st.info("🔄 강제 새로조회 → 안 되면 🧹 초기화 → 그래도 안 되면 조회기간을 줄여보세요.")
            return

        bar.empty()
        elapsed = time.time() - t0

        # 차단 감지로 속도를 늦춘 상태면 알려준다
        cur_delay = _kind_pacer_status().get("delay", 0)
        if cur_delay >= 2:
            st.info(
                f"⏳ KIND 쪽 저항이 감지돼 요청 간격을 {cur_delay}초로 늦춘 상태입니다. "
                "연속 성공하면 자동으로 다시 빨라집니다."
            )

        if df_raw is None or df_raw.empty:
            st.warning("해당 조건에 일치하는 데이터가 없습니다.")
            if fetch_errors:
                with st.expander(f"⚠️ 일부 수집 실패 ({len(fetch_errors)}건)"):
                    for msg in fetch_errors:
                        st.text(msg)
            return

        # multi 전용: 단기과열 '(예고)' 제외 (원본 단계)
        if menu_key == "multi" and exclude_forecast_multi:
            title_col = df_raw.get("공시제목", "").astype(str)
            is_overheat = title_col.str.contains(OVERHEAT_PATTERN, na=False)
            is_forecast = title_col.str.match(FORECAST_PREFIX, na=False)
            df_raw = df_raw[~(is_overheat & is_forecast)]

        ts_kst = datetime.datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S KST")
        st.session_state["menu_cache"][cache_key] = {
            "time_kst": ts_kst, "raw": df_raw,
            "errors": fetch_errors, "elapsed": elapsed,
        }
    else:
        bundle = st.session_state["menu_cache"].get(cache_key)
        if bundle:
            df_raw = bundle.get("raw")
            fetch_errors = bundle.get("errors", [])

    if df_raw is None:
        st.info("기간과 카테고리 선택 후 **공시 조회**를 먼저 눌러주세요. "
                "(검색/조회 시간은 이후엔 즉시 필터만 적용)")
        return

    if df_raw.empty:
        st.warning("해당 조건에 일치하는 데이터가 없습니다.")
        return

    # 부분 실패 안내 — 데이터는 보여주되 불완전함을 명시
    if fetch_errors:
        st.warning(f"⚠️ {len(fetch_errors)}개 소스 수집에 실패해 결과가 불완전합니다.")
        with st.expander("실패 상세"):
            for msg in fetch_errors:
                st.text(msg)

    # ── (1) 키워드 필터
    df_view = df_raw.copy()
    if keyword.strip():
        flags = 0 if case_sens else re.IGNORECASE
        patt = re.compile(re.escape(keyword.strip()), flags)
        mask = (
            df_view.get("공시제목", "").astype(str).str.contains(patt, na=False) |
            df_view.get("회사명", "").astype(str).str.contains(patt, na=False)
        )
        df_view = df_view[mask]

    if menu_key == "overheat" and not df_view.empty and exclude_forecast_main:
        df_view = df_view[~df_view.get("공시제목", "").astype(str).str.match(r"^\(예고\)")]

    # ── (2) 조회 시간 필터
    st_tm = map_start[start_time_lbl]
    en_tm = map_end[end_time_lbl]
    if not df_view.empty:
        ts_all = pd.to_datetime(df_view["시간"], errors="coerce")
        tt = ts_all.dt.time
        if st_tm <= en_tm:
            mask_time = (tt >= st_tm) & (tt <= en_tm)
        else:
            mask_time = (tt >= st_tm) | (tt <= en_tm)
        df_view = df_view[mask_time]

    if df_view.empty:
        st.warning("필터 조건에 해당하는 데이터가 없습니다.")
        return

    ref_date = _last_weekday(d_end)
    df_all_show = build_display_df(df_view, ref_date)

    # ── NXT 종목셋 & 거래불가사유 매핑 (종목코드 기준)
    ymd = ref_date.strftime("%Y%m%d")
    nxt_codes, reason_map, nxt_failed = set(), {}, False
    try:
        _trade_date, nxt_df = get_nextrade_filtered_symbols(ymd)
        if nxt_df is not None and not nxt_df.empty:
            nxt_df = nxt_df.copy()
            nxt_df["종목명"] = nxt_df["종목명"].astype(str)

            if "단축코드" in nxt_df.columns:
                nxt_df["_code5"] = nxt_df["단축코드"].astype(str).str[:5]
            else:
                nxt_df["_code5"] = ""

            if "거래불가사유" not in nxt_df.columns:
                nxt_df["거래불가사유"] = ""

            nxt_df["비고"] = (
                nxt_df["거래불가사유"].fillna("").astype(str)
                .str.replace(r"투자\s*경고\s*/\s*위험", "경/위", regex=True)
                .str.replace("투자경고/위험", "경/위", regex=False)
                .str.replace("단기과열", "과열", regex=False)
                .str.replace("거래정지", "정지", regex=False)
            )
            reason_map = nxt_df.drop_duplicates("_code5").set_index("_code5")["비고"].to_dict()
            nxt_codes = set(nxt_df["_code5"]) - {""}
    except Exception:
        nxt_failed = True

    if nxt_failed or not nxt_codes:
        st.info("NXT 종목 정보를 가져오지 못했습니다. 탭1이 비어 보일 수 있습니다.")

    df_all_show["비고"] = df_all_show["종목코드"].map(reason_map).fillna("")
    df_nxt_trade = df_all_show[df_all_show["종목코드"].isin(nxt_codes)].copy()

    bundle = st.session_state["menu_cache"].get(cache_key, {})
    ts_txt = bundle.get("time_kst", "")
    el = bundle.get("elapsed")
    el_txt = f" · 수집 {el:.0f}초" if el else ""
    caption_head = (f"\n선택: {_menu_label(menu_key).strip()} · 기간: {f} ~ {t} "
                    f"· 총 {len(df_all_show)}건{el_txt}\n조회: {ts_txt}")

    colcfg = {
        "당일":   st.column_config.TextColumn(width=35),
        "시간":   st.column_config.TextColumn(width=98),
        "종목명": st.column_config.TextColumn(width=110),
        "종목코드": None,
        "비고":   st.column_config.TextColumn(
            width=50, help="NXT 조회 기준 사유(경/위=투자경고/위험, 정지=거래정지)"),
        "공시제목": st.column_config.LinkColumn(
            "공시제목", width=320, help="클릭하면 KRX 뷰어로 이동합니다", display_text=r"#(.+)$"),
    }

    tab1, tab2 = st.tabs(["1) 넥스트레이드 종목", "2) KRX 전체"])

    with tab1:
        render_header_with_copy("copy_tab1", caption_head, df_nxt_trade)
        if df_nxt_trade.empty:
            st.info("넥스트레이드 종목과 일치하는 공시가 없습니다.")
        else:
            st.dataframe(
                style_today_rows(df_nxt_trade),
                use_container_width=True,
                hide_index=True,
                height=_df_height(df_nxt_trade),
                column_config=colcfg,
            )

    with tab2:
        render_header_with_copy("copy_tab2", caption_head, df_all_show)
        st.dataframe(
            style_nxt_rows(df_all_show, nxt_codes),
            use_container_width=True,
            hide_index=True,
            height=_df_height(df_all_show),
            column_config=colcfg,
        )


if __name__ == "__main__":
    run()
