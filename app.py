import streamlit as st
import pandas as pd
import datetime, json, re
from zoneinfo import ZoneInfo
from streamlit.components.v1 import html
from html import escape
import api_utils as api # 위에서 만든 파일 임포트

# --- 패턴 및 유틸 (기존 코드와 동일) ---
HALT_PATTERN = re.compile(r"(매매)?거래정지|정지해제|거래정지해제|거래정지기간", re.IGNORECASE)

def _last_weekday(d):
    wd = d.weekday()
    if wd == 5: return d - datetime.timedelta(days=1)
    if wd == 6: return d - datetime.timedelta(days=2)
    return d

def run():
    st.set_page_config(page_title="KRX • NXT 공시 모니터", layout="centered")
    
    # --- 사이드바 ---
    with st.sidebar:
        st.header("📆 조회 기간")
        today = datetime.datetime.now(ZoneInfo("Asia/Seoul")).date()
        start_date = st.date_input("시작일", value=today - datetime.timedelta(days=21))
        end_date = st.date_input("종료일", value=today)
        
        st.divider()
        menu_key = st.radio("카테고리", ["multi", "halt", "mgmt", "alert", "inv", "overheat", "misc"])
        go = st.button("공시 조회", type="primary", use_container_width=True)

    st.title("📡 KRX • NXT 공시 모니터")

    if not go and "menu_cache" not in st.session_state:
        st.info("왼쪽 사이드바에서 **[공시 조회]** 버튼을 눌러주세요.")
        return

    # --- 데이터 수집 및 처리 로직 ---
    # (이 부분에 기존에 작성하신 데이터 수집/필터링 로직을 넣으시면 됩니다.)
    
    # NXT 종목 체크 예시
    ymd = _last_weekday(end_date).strftime("%Y%m%d")
    time_val, nxt_df = api.get_nextrade_filtered_symbols(ymd)
    
    if not nxt_df.empty:
        st.success(f"NXT 종목 정보 로드 완료 ({time_val})")
        st.dataframe(nxt_df)
    else:
        st.warning("NXT 종목 정보를 가져올 수 없습니다.")

# --- 앱 실행부 (이게 있어야 화면이 뜹니다!) ---
if __name__ == "__main__":
    run()
