import streamlit as st
import pandas as pd
import datetime, json, re
from zoneinfo import ZoneInfo
from streamlit.components.v1 import html
from html import escape
import api_utils as api  # 통합된 유틸 파일 임포트

# 기본 패턴/상수 정의 (기존 코드 그대로)
HALT_PATTERN = re.compile(r"(매매)?거래정지|정지해제|거래정지해제|거래정지기간", re.IGNORECASE)
INV_SUFFIX_EXCLUDE = re.compile(r"\((?:[^)]*우B?)\)\s*$")
OVERHEAT_PATTERN = re.compile(r"단기과열", re.IGNORECASE)
FORECAST_PREFIX = re.compile(r"^\(예고\)")

MENU_SPEC = [
    ("multi", "✅ NXT종목 모아보기", 0),
    ("halt", "1️⃣ 거래정지/재개 종목", 1),
    ("mgmt", "2️⃣ 관리종목", 1),
    ("alert", "3️⃣ 투자주의환기 종목", 1),
    ("inv", "4️⃣ 투자경고·위험 종목", 1),
    ("overheat", "5️⃣ 단기과열 종목", 1),
    ("misc", "6️⃣ 기타 시장안내", 1),
]

# (기존 유틸 함수들 _menu_label, _last_weekday, build_display_df 등 유지)
# ... [생략] ...

def run():
    st.set_page_config(page_title="KRX • NXT 공시 모니터", layout="centered")
    
    # 사이드바 설정
    with st.sidebar:
        # 날짜/카테고리/검색어 UI 구성 (기존 코드 유지)
        pass

    # 메인 로직 실행 시 api.get_nextrade_filtered_symbols 호출
    # ... [생략] ...
    
    try:
        # 캐시 기능이 포함된 함수 호출
        _trade_date, nxt_df = api.get_nextrade_filtered_symbols(ymd)
        # 이후 비고(사유) 매핑 로직 진행
    except Exception:
        nxt_names = set()
        reason_map = {}

    # 탭 구성 및 데이터프레임 렌더링 (기존 코드 유지)

if __name__ == "__main__":
    run()
