from __future__ import annotations
import streamlit as st
import pandas as pd
import datetime, json, re
from zoneinfo import ZoneInfo
from streamlit.components.v1 import html
from html import escape
import api_utils as api

# ─────────────────────────────────────────────────────────────
# 1. 상수 및 UI 유틸 (원본 코드 유지)
# ─────────────────────────────────────────────────────────────
HALT_PATTERN = re.compile(r"(매매)?거래정지|정지해제|거래정지해제|거래정지기간", re.IGNORECASE)
INV_SUFFIX_EXCLUDE = re.compile(r"\((?:[^)]*우B?)\)\s*$")
OVERHEAT_PATTERN = re.compile(r"단기과열", re.IGNORECASE)
FORECAST_PREFIX = re.compile(r"^\(예고\)")

MENU_SPEC = [
    ("multi",    "✅ NXT종목 모아보기", 0),
    ("halt",     "1️⃣ 거래정지/재개 종목", 1),
    ("mgmt",     "2️⃣ 관리종목",         1),
    ("alert",    "3️⃣ 투자주의환기 종목", 1),
    ("inv",      "4️⃣ 투자경고·위험 종목", 1),
    ("overheat", "5️⃣ 단기과열 종목",     1),
    ("misc",     "6️⃣ 기타 시장안내",     1),
]

FETCHER_MAP = {
    "multi": ("multi", None, None),
    "halt": ("cat", "halt", HALT_PATTERN),
    "mgmt": ("cat", "mgmt", None),
    "alert": ("cat", "alert", None),
    "inv": ("inv", None, None),
    "overheat": ("overheat", None, None),
    "misc": ("cat", "misc", None),
}

def _menu_label(key: str) -> str:
    for k, label, level in MENU_SPEC:
        if k == key: return (" " * level) + label
    return key

def _last_weekday(d: datetime.date) -> datetime.date:
    wd = d.weekday()
    if wd == 5: return d - datetime.timedelta(days=1)
    if wd == 6: return d - datetime.timedelta(days=2)
    return d

def _coerce_date_pair(s, e, default_start, default_end):
    if not isinstance(s, datetime.date): s = default_start
    if not isinstance(e, datetime.date): e = default_end
    if s > e: s, e = e, s
    return s, e

def style_today_rows(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    highlight = "background-color: #e8f4ff; font-weight: 600;"
    def _row_style(row: pd.Series):
        return [highlight]*len(row) if row.get("당일", "") == "🟡" else [""]*len(row)
    return df.style.apply(_row_style, axis=1)

def style_nxt_rows(df: pd.DataFrame, nxt_set: set) -> pd.io.formats.style.Styler:
    highlight = "background-color: #fff4b6; font-weight: 600;"
    def _row_style(row: pd.Series):
        return [highlight]*len(row) if str(row.get("종목명", "")) in nxt_set else [""]*len(row)
    return df.style.apply(_row_style, axis=1)

def build_display_df(df: pd.DataFrame, ref_date: datetime.date) -> pd.DataFrame:
    ts = pd.to_datetime(df.get("시간", ""), errors="coerce")
    time_disp = ts.dt.strftime("%y/%m/%d %H:%M").fillna("")
    is_today = ts.dt.date.eq(ref_date)
    out = pd.DataFrame({
        "당일": is_today.map(lambda x: "🟡" if x else ""),
        "시간": time_disp,
        "종목명": df.get("회사명", "").astype(company_str:=str),
        "공시제목": df.get("뷰어URL", "").astype(str),
    })
    return out.sort_values("시간", ascending=False).reset_index(drop=True)

def _split_title_and_link(url_series: pd.Series) -> tuple[pd.Series, pd.Series]:
    url_series = url_series.astype(str)
    title = url_series.str.extract(r"#(.+)$")[0].fillna(url_series)
    link = url_series.str.replace(r"#.+$", "", regex=True)
    return title, link

def _make_copy_df(df_display: pd.DataFrame) -> pd.DataFrame:
    tmp = df_display.copy()
    title, link = _split_title_and_link(tmp["공시제목"])
    tmp["공시제목"], tmp["링크"] = title, link
    cols = ["당일", "시간", "종목명", "공시제목", "링크"]
    if "비고" in tmp.columns: cols.insert(3, "비고")
    return tmp[cols]

def _df_height(df: pd.DataFrame) -> int:
    return min(max(35 + 30 * len(df) + 15, 150), 550)

def render_header_with_copy(copy_id: str, caption_text: str, df_display: pd.DataFrame):
    safe_caption = escape(caption_text).replace("\n", "<br>")
    js_text = json.dumps(_make_copy_df(df_display).to_csv(sep="\t", index=False))
    col1, col2 = st.columns([5, 1.5])
    with col1:
        st.markdown(f'<div style="display:flex; align-items:flex-end; height:100%; font-size: 0.9rem; color: rgba(49,51,63,0.75);">{safe_caption}</div>', unsafe_allow_html=True)
    with col2:
        html(f"""<button id="{copy_id}" onclick="copy_{copy_id}()" style="font-size:15px; padding:6px 12px; width:100%; background-color:#4CAF50; color:white; border:none; border-radius:4px; cursor:pointer;">📋 복사</button>
            <script>function copy_{copy_id}(){{ const t={js_text}; navigator.clipboard.writeText(t).then(()=>{{ var b=document.getElementById("{copy_id}"); b.innerText="✅ 복사 완료"; b.style.backgroundColor="#777"; setTimeout(()=>{{b.innerText="📋 복사"; b.style.backgroundColor="#4CAF50";}},2000); }}); }}</script>""", height=45)

def _merge_halt_and_mw(df_halt_cat, df_mw):
    merged = pd.concat([x for x in [df_halt_cat, df_mw] if x is not None and not x.empty], ignore_index=True)
    if merged.empty: return pd.DataFrame()
    return merged.drop_duplicates(subset=["문서번호"]).sort_values("시간", ascending=False).reset_index(drop=True)

# ─────────────────────────────────────────────────────────────
# 2. 데이터 페치 로직 (api_utils 연결)
# ─────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False, ttl=60)
def _fetch(menu_key, f, t):
    ftype, arg, patt = FETCHER_MAP[menu_key]
    if ftype == "multi":
        return _fetch_multi(f, t)
    
    if ftype == "inv": df_raw = api.fetch_investor_warning(f, t)
    elif ftype == "overheat": df_raw = api.fetch_shortterm_overheat(f, t)
    else: df_raw = api.kind_fetch(arg, f, t)
    
    if not df_raw.empty and ftype in ["inv", "overheat"]:
        df_raw = df_raw[~df_raw["공시제목"].astype(str).str.contains(INV_SUFFIX_EXCLUDE, na=False)]
    
    if arg == "halt":
        df_mw = api.fetch_market_watch(f, t)
        if not df_mw.empty: df_mw = df_mw[~df_mw["공시제목"].astype(str).str.contains(INV_SUFFIX_EXCLUDE, na=False)]
        df_h = df_raw[df_raw["공시제목"].astype(str).str.contains(patt, na=False)] if patt and not df_raw.empty else df_raw
        return _merge_halt_and_mw(df_h, df_mw)
    
    return df_raw.reset_index(drop=True)

def _fetch_multi(f, t):
    h_cat = api.kind_fetch("halt", f, t)
    if not h_cat.empty: h_cat = h_cat[h_cat["공시제목"].astype(str).str.contains(HALT_PATTERN, na=False)]
    mw = api.fetch_market_watch(f, t)
    halt = _merge_halt_and_mw(h_cat, mw)
    
    dfs = [halt, api.kind_fetch("mgmt", f, t), api.kind_fetch("alert", f, t), api.kind_fetch("misc", f, t), 
           api.fetch_investor_warning(f, t), api.fetch_shortterm_overheat(f, t)]
    
    merged = pd.concat([x for x in dfs if not x.empty], ignore_index=True)
    return merged.drop_duplicates(subset=["문서번호"]).sort_values("시간", ascending=False).reset_index(drop=True) if not merged.empty else pd.DataFrame()

# ─────────────────────────────────────────────────────────────
# 3. Main 실행
# ─────────────────────────────────────────────────────────────
def run():
    st.set_page_config(page_title="KRX • NXT 공시 모니터", layout="centered", initial_sidebar_state="expanded")
    
    st.markdown("""<style>
    [data-testid="stSidebar"] { min-width: 380px; max-width: 380px; }
    #menu-radio-wrap [role="radiogroup"] { display: flex; flex-direction: column; row-gap: 10px; }
    </style>""", unsafe_allow_html=True)

    st.markdown("### 📡 KRX • NXT 공시 모니터")
    if "menu_cache" not in st.session_state: st.session_state["menu_cache"] = {}

    with st.sidebar:
        st.markdown("## 📆 KIND 조회 기간")
        today = datetime.datetime.now(ZoneInfo("Asia/Seoul")).date()
        c1, c2 = st.columns(2)
        d_start, d_end = _coerce_date_pair(c1.date_input("시작일", today-datetime.timedelta(days=21)), 
                                          c2.date_input("종료일", today), today-datetime.timedelta(days=21), today)
        
        st.markdown("---")
        menu_key = st.radio("카테고리 선택", options=[k for k,_,_ in MENU_SPEC], format_func=_menu_label)
        keyword = st.text_input("🔎 검색어", placeholder="제목/종목명 입력")
        go = st.button("공시 조회", type="primary", use_container_width=True)

    f, t = d_start.strftime("%Y-%m-%d"), d_end.strftime("%Y-%m-%d")
    cache_key = (menu_key, f, t)

    if go:
        with st.spinner("데이터 수집 중..."):
            df_raw = _fetch(menu_key, f, t)
            st.session_state["menu_cache"][cache_key] = df_raw
    else:
        df_raw = st.session_state["menu_cache"].get(cache_key)

    if df_raw is None:
        st.info("조회 버튼을 눌러주세요.")
        return
    if df_raw.empty:
        st.warning("일치하는 공시가 없습니다.")
        return

    # 필터 및 가공 로직 (원본 동일)
    df_view = df_raw.copy()
    if keyword:
        p = re.compile(re.escape(keyword), re.IGNORECASE)
        df_view = df_view[df_view["공시제목"].str.contains(p) | df_view["회사명"].str.contains(p)]

    ref_date = _last_weekday(d_end)
    df_show = build_display_df(df_view, ref_date)
    
    # NXT 캐시 연동
    _, nxt_df = api.get_nextrade_filtered_symbols(ref_date.strftime("%Y%m%d"))
    nxt_names = set(nxt_df["종목명"]) if not nxt_df.empty else set()
    reason_map = nxt_df.set_index("종목명")["거래불가사유"].to_dict() if not nxt_df.empty else {}
    
    df_show["비고"] = df_show["종목명"].map(reason_map).fillna("")
    df_nxt = df_show[df_show["종목명"].isin(nxt_names)]

    colcfg = {
        "당일": st.column_config.TextColumn(width=35),
        "시간": st.column_config.TextColumn(width=98),
        "종목명": st.column_config.TextColumn(width=110),
        "비고": st.column_config.TextColumn(width=80),
        "공시제목": st.column_config.LinkColumn("공시제목", width=320, display_text=r"#(.+)$")
    }

    tab1, tab2 = st.tabs(["1) 넥스트레이드 종목", "2) KRX 전체"])
    caption = f"조회: {_menu_label(menu_key).strip()} | {f} ~ {t} | 총 {len(df_show)}건"
    
    with tab1:
        render_header_with_copy("cp1", caption, df_nxt)
        st.dataframe(style_today_rows(df_nxt), use_container_width=True, hide_index=True, height=_df_height(df_nxt), column_config=colcfg)
    with tab2:
        render_header_with_copy("cp2", caption, df_show)
        st.dataframe(style_nxt_rows(df_show, nxt_names), use_container_width=True, hide_index=True, height=_df_height(df_show), column_config=colcfg)

if __name__ == "__main__":
    run()
