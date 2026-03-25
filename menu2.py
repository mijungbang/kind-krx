# menu2.py
from __future__ import annotations

import streamlit as st
import pandas as pd
import datetime, json, re
from zoneinfo import ZoneInfo
from streamlit.components.v1 import html
from html import escape

from fnc2 import (
    kind_fetch,                 # cat 기반(1/2/3/6)
    fetch_investor_warning,     # 4️⃣ 투자경고·위험
    fetch_shortterm_overheat,   # 5️⃣ 단기과열
    fetch_market_watch,         # ✅ 시장감시위원회(사용자 지정) - halt/multi에만 합침
)

# NXT 종목 조회 (환경에 따라 없을 수 있으므로 안전 처리)
try:
    from fnc import get_nextrade_filtered_symbols  # (trade_date, df)
except Exception:
    def get_nextrade_filtered_symbols(yyyymmdd: str):
        return yyyymmdd, pd.DataFrame(columns=["종목명"])  # 안전 Fallback

# ─────────────────────────────────────────────────────────────
# 상수/유틸
# ─────────────────────────────────────────────────────────────
HALT_PATTERN = re.compile(r"(매매)?거래정지|정지해제|거래정지해제|거래정지기간", re.IGNORECASE)
INV_SUFFIX_EXCLUDE = re.compile(r"\((?:[^)]*우B?)\)\s*$")

# ✅ multi에서 단기과열 (예고)만 제외하기 위한 패턴
OVERHEAT_PATTERN = re.compile(r"단기과열", re.IGNORECASE)
FORECAST_PREFIX = re.compile(r"^\(예고\)")

# 메뉴 스펙(키, 라벨, 들여쓰기 레벨)
MENU_SPEC = [
    ("multi",    "✅ NXT종목 모아보기", 0),
    ("halt",     "1️⃣ 거래정지/재개 종목", 1),
    ("mgmt",     "2️⃣ 관리종목",        1),
    ("alert",    "3️⃣ 투자주의환기 종목", 1),
    ("inv",      "4️⃣ 투자경고·위험 종목", 1),
    ("overheat", "5️⃣ 단기과열 종목",    1),
    ("misc",     "6️⃣ 기타 시장안내",    1),
]

# 실제 동작 맵
FETCHER_MAP = {
    "multi":    ("multi", None, None),
    "halt":     ("cat",   "halt",  HALT_PATTERN),
    "mgmt":     ("cat",   "mgmt",  None),
    "alert":    ("cat",   "alert", None),
    "inv":      ("inv",   None,    None),
    "overheat": ("overheat", None, None),
    "misc":     ("cat",   "misc",  None),
}

# 라벨 포맷터(들여쓰기: U+2003 EM SPACE)
def _menu_label(key: str) -> str:
    for k, label, level in MENU_SPEC:
        if k == key:
            return (" " * level) + label
    return key

# 주말이면 가장 가까운 이전 평일로
def _last_weekday(d: datetime.date) -> datetime.date:
    wd = d.weekday()  # 월0..일6
    if wd == 5:  # 토
        return d - datetime.timedelta(days=1)
    if wd == 6:  # 일
        return d - datetime.timedelta(days=2)
    return d

# 날짜 안전 보정
def _coerce_date_pair(s, e, default_start, default_end):
    import datetime as _dt
    if not isinstance(s, _dt.date):
        s = default_start
    if not isinstance(e, _dt.date):
        e = default_end
    if s > e:
        s, e = e, s
    return s, e

# 하이라이트(당일=🟡)
def style_today_rows(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    highlight = "background-color: #e8f4ff; font-weight: 600;"
    def _row_style(row: pd.Series):
        return [highlight]*len(row) if row.get("당일", "") == "🟡" else [""]*len(row)
    return df.style.apply(_row_style, axis=1)

# KRX 전체에서 NXT 종목만 하이라이트
def style_nxt_rows(df: pd.DataFrame, nxt_set: set) -> pd.io.formats.style.Styler:
    highlight = "background-color: #fff4b6; font-weight: 600;"
    def _row_style(row: pd.Series):
        return [highlight]*len(row) if str(row.get("종목명", "")) in nxt_set else [""]*len(row)
    return df.style.apply(_row_style, axis=1)

# 화면 표시용 변환 (시간 포맷: yy/mm/dd HH:MM)
def build_display_df(df: pd.DataFrame, ref_date: datetime.date) -> pd.DataFrame:
    ts = pd.to_datetime(df.get("시간", ""), errors="coerce")
    time_disp = ts.dt.strftime("%y/%m/%d %H:%M").fillna("")
    is_today = ts.dt.date.eq(ref_date)
    out = (
        pd.DataFrame({
            "당일": is_today.map(lambda x: "🟡" if x else ""),
            "시간": time_disp,
            "종목명": df.get("회사명", "").astype(str),
            "공시제목": df.get("뷰어URL", "").astype(str),
        })
        .sort_values("시간", ascending=False)
        .reset_index(drop=True)
    )
    return out

# 공시제목/링크 분리 복사용
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

# ✅ 행 수 기반으로 dataframe 높이 자동 조절
def _df_height(df: pd.DataFrame,
               base_row_height: int = 30,
               header_height: int = 35,
               max_height: int = 550,
               min_height: int = 150) -> int:
    rows = max(len(df), 1)
    h = header_height + base_row_height * rows + 15
    if h < min_height:
        h = min_height
    if h > max_height:
        h = max_height
    return h

def render_header_with_copy(copy_id: str, caption_text: str, df_display: pd.DataFrame):
    """
    캡션(좌) + 복사 버튼(우)을 한 줄에 배치.
    캡션과 버튼을 아래쪽 기준으로 정렬.
    """
    safe_caption = escape(caption_text).replace("\n", "<br>")

    copy_df = _make_copy_df(df_display)
    clipboard = copy_df.to_csv(sep="\t", index=False)
    js_text = json.dumps(clipboard)

    col1, col2 = st.columns([5, 1.5])
    with col1:
        st.markdown(
            f"""
            <div style="
                display:flex;
                align-items:flex-end;
                height:100%;
                margin: 0 0 2px 0;
                font-size: 0.9rem;
                line-height: 1.2;
                color: rgba(49,51,63,0.75);
            ">{safe_caption}</div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        html(
            f"""
            <div style="
                display:flex;
                justify-content:flex-end;
                align-items:flex-end;
                margin: 0 0 2px 0;
            ">
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
# ✅ halt(cat) + mw(reportCd) 병합 유틸
# ─────────────────────────────────────────────────────────────
def _merge_halt_and_mw(df_halt_cat: pd.DataFrame, df_mw: pd.DataFrame) -> pd.DataFrame:
    dfs = []
    if df_halt_cat is not None and not df_halt_cat.empty:
        dfs.append(df_halt_cat)
    if df_mw is not None and not df_mw.empty:
        dfs.append(df_mw)
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
# ─────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=60)
def _fetch(menu_key: str, f: str, t: str, page_size: int = 100, nonce: int = 0) -> pd.DataFrame:
    # nonce는 캐시 키를 바꾸기 위한 용도(사용 X)
    _ = nonce

    ftype, arg, patt = FETCHER_MAP[menu_key]
    if ftype == "multi":
        return _fetch_multi(f, t, page_size, nonce=nonce)

    if ftype == "inv":
        df_raw = fetch_investor_warning(f, t, page_size=page_size)
        if not df_raw.empty:
            df_raw = df_raw[~df_raw["공시제목"].astype(str).str.contains(INV_SUFFIX_EXCLUDE, na=False)]
        return df_raw.reset_index(drop=True)

    if ftype == "overheat":
        df_raw = fetch_shortterm_overheat(f, t, page_size=page_size)
        if not df_raw.empty:
            df_raw = df_raw[~df_raw["공시제목"].astype(str).str.contains(INV_SUFFIX_EXCLUDE, na=False)]
        return df_raw.reset_index(drop=True)

    # cat
    df_raw = kind_fetch(arg, f, t, page_size=page_size)

    # ✅ 거래정지/재개 메뉴: 기존 halt(cat) + 시장감시(reportCd) 합치기
    if arg == "halt":
        df_mw = fetch_market_watch(f, t, page_size=page_size)
        if not df_mw.empty:
            df_mw = df_mw[~df_mw["공시제목"].astype(str).str.contains(INV_SUFFIX_EXCLUDE, na=False)]

        df_halt = df_raw
        if df_halt is not None and not df_halt.empty and patt is not None:
            df_halt = df_halt[df_halt["공시제목"].astype(str).str.contains(patt, na=False)]

        merged = _merge_halt_and_mw(df_halt, df_mw)
        return merged.reset_index(drop=True) if not merged.empty else pd.DataFrame()

    return df_raw.reset_index(drop=True) if df_raw is not None and not df_raw.empty else pd.DataFrame()

@st.cache_data(show_spinner=False, ttl=60)
def _fetch_multi(f: str, t: str, page_size: int = 100, nonce: int = 0) -> pd.DataFrame:
    _ = nonce

    # 1) halt(cat) + mw 병합 (halt 패턴은 cat에만 적용)
    df_halt_cat = kind_fetch("halt", f, t, page_size=page_size)
    df_halt_cat_f = df_halt_cat
    if df_halt_cat_f is not None and not df_halt_cat_f.empty:
        df_halt_cat_f = df_halt_cat_f[df_halt_cat_f["공시제목"].astype(str).str.contains(HALT_PATTERN, na=False)]

    df_mw = fetch_market_watch(f, t, page_size=page_size)
    if not df_mw.empty:
        df_mw = df_mw[~df_mw["공시제목"].astype(str).str.contains(INV_SUFFIX_EXCLUDE, na=False)]

    df_halt = _merge_halt_and_mw(df_halt_cat_f, df_mw)

    # 2) 나머지 기존 로직 그대로
    df_mgmt  = kind_fetch("mgmt",  f, t, page_size=page_size)
    df_alert = kind_fetch("alert", f, t, page_size=page_size)
    df_misc  = kind_fetch("misc",  f, t, page_size=page_size)

    df_warn  = fetch_investor_warning(f, t, page_size=page_size)
    if not df_warn.empty:
        df_warn = df_warn[~df_warn["공시제목"].astype(str).str.contains(INV_SUFFIX_EXCLUDE, na=False)]

    df_oh    = fetch_shortterm_overheat(f, t, page_size=page_size)
    if not df_oh.empty:
        df_oh = df_oh[~df_oh["공시제목"].astype(str).str.contains(INV_SUFFIX_EXCLUDE, na=False)]

    dfs = [x for x in [df_halt, df_mgmt, df_alert, df_misc, df_warn, df_oh] if x is not None and not x.empty]
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
# App
# ─────────────────────────────────────────────────────────────
def run():
    st.set_page_config(
        page_title="KRX • NXT 공시 모니터",
        layout="centered",
        initial_sidebar_state="expanded",
    )

    # 사이드바 너비 + 라디오 간격 CSS
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

    if "menu_cache" not in st.session_state:
        st.session_state["menu_cache"] = {}
    if "force_nonce" not in st.session_state:
        st.session_state["force_nonce"] = 0

    # ── 사이드바
    with st.sidebar:
        # 1) 📆 기간
        st.markdown("## 📆 KIND 조회 기간")
        today_kst = datetime.datetime.now(ZoneInfo("Asia/Seoul")).date()
        three_weeks_ago = today_kst - datetime.timedelta(days=5) # 5일로 변경

        c1, c2 = st.columns(2)
        with c1:
            start_date = st.date_input("시작일", value=three_weeks_ago, format="YYYY/MM/DD", key="start_date")
        with c2:
            end_date = st.date_input("종료일", value=today_kst, format="YYYY/MM/DD", key="end_date")
        d_start, d_end = _coerce_date_pair(start_date, end_date, three_weeks_ago, today_kst)

        st.markdown("---")
        # 2) 🧭 메뉴
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

        st.markdown("---")
        # 3) 🔎 검색
        st.markdown("## 🔎 제목/종목/시간 검색")
        keyword = st.text_input(
            "공시제목 / 종목명 포함",
            value="",
            label_visibility="collapsed",
            placeholder="*(공란가능)키워드 입력",
        )
        case_sens = False

        # 4) ⏱ 조회 시간
        TIME_START = [("00:00", datetime.time(0, 0)),
                      ("14:28", datetime.time(14, 28)),
                      ("14:30", datetime.time(14, 30))]
        TIME_END   = [("09:00", datetime.time(9, 0)),
                      ("14:31", datetime.time(14, 31)),
                      ("23:59", datetime.time(23, 59))]

        start_labels = [lbl for lbl, _ in TIME_START]
        end_labels   = [lbl for lbl, _ in TIME_END]
        map_start = {lbl: tm for lbl, tm in TIME_START}
        map_end   = {lbl: tm for lbl, tm in TIME_END}

        cst, cet = st.columns(2)
        with cst:
            start_time_lbl = st.selectbox(
                "시작",
                options=start_labels,
                index=0,
                key="start_time_lbl",
                label_visibility="collapsed",
            )
        with cet:
            end_time_lbl = st.selectbox(
                "종료",
                options=end_labels,
                index=len(end_labels) - 1,
                key="end_time_lbl",
                label_visibility="collapsed",
            )
            
        # ✅ 단기과열 (예고) 제외 토글들
        exclude_forecast_main = False
        if menu_key == "overheat":
            exclude_forecast_main = st.checkbox(
                "(예고) 공시 제외",
                value=True,
                help="체크 시 '(예고)'로 시작하는 공시는 숨깁니다.",
            )
        else:
            exclude_forecast_main = False

        exclude_forecast_multi = False
        if menu_key == "multi":
            exclude_forecast_multi = st.checkbox(
                "모아보기에서 단기과열 '(예고)' 공시 제외",
                value=True,
                help="체크 시 모아보기 결과 중 단기과열 공시에서만 '(예고)'로 시작하는 건 제외합니다.",
            )
        else:
            exclude_forecast_multi = False
            
        # 5) 조회/캐시 제어 버튼들
        go = st.button("공시 조회", type="primary", use_container_width=True)

        cA, cB = st.columns(2)
        with cA:
            if st.button("🔄 강제 새로조회", use_container_width=True):
                st.session_state["force_nonce"] += 1
                st.toast("캐시 무시하고 다시 조회합니다.", icon="🔄")
        with cB:
            if st.button("🧹 초기화", use_container_width=True):
                st.cache_data.clear()
                st.cache_resource.clear()
                st.session_state.clear()
                st.toast("캐시/세션을 초기화했습니다.", icon="🧹")
                st.rerun()



    if d_start > d_end:
        st.error("시작일이 종료일보다 이후입니다.")
        return

    f = d_start.strftime("%Y-%m-%d")
    t = d_end.strftime("%Y-%m-%d")

    # ── 수집/캐시: 기간·메뉴 조합만 캐시 키로 사용
    cache_key = (menu_key, f, t)
    df_raw: pd.DataFrame | None = None

    if go:
        try:
            with st.spinner(f"KIND에서 [{_menu_label(menu_key).strip()}] 데이터 수집 중..."):
                df_raw = _fetch(menu_key, f, t, page_size=100, nonce=st.session_state["force_nonce"])
        except Exception as e:
            st.error("KIND 응답이 비정상입니다(차단/오류 가능).")
            st.code(str(e))
            st.info("🔄 강제 새로조회 → 안 되면 🧹 초기화 → 그래도 안 되면 조회기간을 줄이거나 fnc2.py의 sleep을 늘려보세요.")
            return

        # ✅ multi에서만: 단기과열 '(예고)' 공시 선택적으로 제외 (원본 단계에서)
        if menu_key == "multi" and exclude_forecast_multi and df_raw is not None and not df_raw.empty:
            title_col = df_raw.get("공시제목", "").astype(str)
            is_overheat = title_col.str.contains(OVERHEAT_PATTERN, na=False)
            is_forecast = title_col.str.match(FORECAST_PREFIX, na=False)
            df_raw = df_raw[~(is_overheat & is_forecast)]

        if df_raw.empty:
            st.warning("해당 조건에 일치하는 데이터가 없습니다.")
            return
        ts_kst = datetime.datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S KST")
        st.session_state["menu_cache"][cache_key] = {"time_kst": ts_kst, "raw": df_raw}
    else:
        bundle = st.session_state["menu_cache"].get(cache_key)
        if bundle:
            df_raw = bundle.get("raw")

    # 수집 전이면 안내
    if df_raw is None:
        st.info("기간과 카테고리 선택 후 **공시 조회**를 먼저 눌러주세요. (검색/조회 시간은 이후엔 즉시 필터만 적용)")
        return
    if df_raw.empty:
        st.warning("해당 조건에 일치하는 데이터가 없습니다.")
        return

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

    # 단기과열 메뉴: (예고) 제외
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

    # 표시용 변환(주말 보정으로 당일 하이라이트)
    ref_date = _last_weekday(d_end)
    df_all_show = build_display_df(df_view, ref_date)

    # NXT 종목셋 & 거래불가사유 매핑
    nxt_ref_date = _last_weekday(d_end)
    ymd = nxt_ref_date.strftime("%Y%m%d")
    try:
        _trade_date, nxt_df = get_nextrade_filtered_symbols(ymd)
        if nxt_df is None or nxt_df.empty:
            nxt_names = set()
            reason_map = {}
        else:
            nxt_df = nxt_df.copy()
            nxt_df["종목명"] = nxt_df["종목명"].astype(str)

            if "거래불가사유" not in nxt_df.columns:
                nxt_df["거래불가사유"] = ""

            nxt_df["비고"] = nxt_df["거래불가사유"].fillna("").astype(str)
            nxt_df["비고"] = (
                nxt_df["비고"]
                .str.replace(r"투자\s*경고\s*/\s*위험", "경/위", regex=True)
                .str.replace("투자경고/위험", "경/위", regex=False)
                .str.replace("단기과열", "과열", regex=False)
                .str.replace("거래정지", "정지", regex=False)
            )
            reason_map = nxt_df.drop_duplicates("종목명").set_index("종목명")["비고"].to_dict()
            nxt_names = set(nxt_df["종목명"])
    except Exception:
        nxt_names = set()
        reason_map = {}

    # 비고 붙이기(공통)
    df_all_show["비고"] = df_all_show["종목명"].map(reason_map).fillna("")

    # 분기 데이터셋
    df_nxt_trade = df_all_show[df_all_show["종목명"].isin(nxt_names)].copy()

    # 캡션
    caption_head = f"\n선택: {_menu_label(menu_key).strip()} · 기간: {f} ~ {t} · 총 {len(df_all_show)}건"

    # 컬럼 설정
    colcfg = {
        "당일": st.column_config.TextColumn(width=35),
        "시간": st.column_config.TextColumn(width=98),
        "종목명": st.column_config.TextColumn(width=110),
        "비고": st.column_config.TextColumn(width=50, help="NXT 조회 기준 사유(경/위=투자경고/위험, 정지=거래정지)"),
        "공시제목": st.column_config.LinkColumn(
            "공시제목", width=320, help="클릭하면 KRX 뷰어로 이동합니다", display_text=r"#(.+)$"
        ),
    }

    # ── 탭 2개만: 1) 넥스트레이드 종목  2) KRX 전체
    tab1, tab2 = st.tabs(["1) 넥스트레이드 종목", "2) KRX 전체"])

    with tab1:
        if df_nxt_trade.empty:
            render_header_with_copy("copy_tab1", caption_head, df_nxt_trade)
            st.info("넥스트레이드 종목명과 일치하는 공시가 없습니다.")
        else:
            render_header_with_copy("copy_tab1", caption_head, df_nxt_trade)
            styled = style_today_rows(df_nxt_trade)
            st.dataframe(
                styled,
                use_container_width=True,
                hide_index=True,
                height=_df_height(df_nxt_trade),
                column_config=colcfg,
            )

    with tab2:
        render_header_with_copy("copy_tab2", caption_head, df_all_show)
        styled = style_nxt_rows(df_all_show, nxt_names)
        st.dataframe(
            styled,
            use_container_width=True,
            hide_index=True,
            height=_df_height(df_all_show),
            column_config=colcfg,
        )

if __name__ == "__main__":
    run()
