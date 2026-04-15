# 📡 KRX · NXT 공시 모니터

KRX KIND(기업공시채널)에서 **시장조치 관련 공시**를 자동 수집하고,  
**넥스트레이드(NXT) 등록 종목**과 매핑하여 한눈에 모니터링하는 Streamlit 대시보드입니다.

---

## 목차

1. [개요](#1-개요)
2. [파일 구조](#2-파일-구조)
3. [설치 및 실행](#3-설치-및-실행)
4. [메뉴 구성](#4-메뉴-구성)
5. [데이터 수집 방식](#5-데이터-수집-방식)
6. [NXT 종목 매핑](#6-nxt-종목-매핑)
7. [UI 기능 상세](#7-ui-기능-상세)
8. [fnc.py — 외부 데이터 조회](#8-fncpy--외부-데이터-조회)
9. [fnc2.py — KIND 크롤링 엔진](#9-fnc2py--kind-크롤링-엔진)
10. [menu2.py — Streamlit 앱](#10-menu2py--streamlit-앱)
11. [종목코드 체계](#11-종목코드-체계)
12. [캐시 및 세션 관리](#12-캐시-및-세션-관리)
13. [에러 핸들링](#13-에러-핸들링)
14. [커스터마이징](#14-커스터마이징)
15. [주의사항](#15-주의사항)

---

## 1. 개요

한국거래소(KRX) KIND 시스템에서 다음과 같은 **시장조치 공시**를 수집합니다:

| 구분 | 내용 |
|------|------|
| 거래정지/재개 | 매매거래 정지, 재개, 단기과열완화장치 발동 등 |
| 관리종목 | 관리종목 지정/해제 |
| 투자주의환기 | 투자주의환기종목 지정/해제 |
| 투자경고·위험 | 투자경고종목 지정/재지정/해제, 투자위험종목 지정/해제 |
| 단기과열 | 단기과열종목 지정/해제/예고 |
| 기타 시장안내 | 기타 시장 관련 안내 |
| 상장폐지 | 상장폐지 결정/사유 등 |

수집된 공시는 **넥스트레이드(NXT)** 등록 종목과 **종목코드 기준으로 매핑**되어,  
NXT 거래 대상 종목에 해당하는 공시만 별도 탭에서 확인할 수 있습니다.

---

## 2. 파일 구조

```
├── fnc.py        # KRX 시세, KOSPI200/KOSDAQ150 지수, NXT 종목 조회
├── fnc2.py       # KIND 공시 크롤링 엔진 (카테고리별 수집 함수)
├── menu2.py      # Streamlit 앱 (UI, 필터, NXT 매핑, 표시)
└── README.md     # 이 문서
```

---

## 3. 설치 및 실행

### 의존성

```
pip install streamlit pandas requests beautifulsoup4
```

### 실행

```bash
streamlit run menu2.py
```

> Python 3.9 이상 권장 (`zoneinfo`, `from __future__ import annotations` 사용)

---

## 4. 메뉴 구성

사이드바 라디오 버튼으로 카테고리를 선택합니다.

| 키 | 라벨 | 수집 방식 | 설명 |
|----|------|-----------|------|
| `multi` | ✅ NXT종목 모아보기 | 전체 통합 | 아래 1~6 + 상장폐지를 모두 수집 후 병합 |
| `halt` | 1️⃣ 거래정지/재개 종목 | cat(`0311`) + market_watch | 카테고리 수집 + 시장감시위원회 reportCd 병합 |
| `mgmt` | 2️⃣ 관리종목 | cat(`0350`) | 카테고리 수집 |
| `alert` | 3️⃣ 투자주의환기 종목 | cat(`0356`) | 카테고리 수집 |
| `inv` | 4️⃣ 투자경고·위험 종목 | warn payload | 12개 reportCd 순회 (유가+코스닥) |
| `overheat` | 5️⃣ 단기과열 종목 | warn payload | `reportNm="단기과열"` 단일 조건 |
| `misc` | 6️⃣ 기타 시장안내 | cat(`0305`) | 카테고리 수집 |
| `delist` | ⚠️ 상장폐지 | warn payload | 2개 reportCd (유가 `68051` + 코스닥 `70769`) |

### "모아보기"의 수집 순서

`_fetch_multi()` 함수에서 아래 순서로 수집 후 `pd.concat` → 문서번호 중복 제거 → 시간 내림차순 정렬:

1. `halt`(cat) → HALT_PATTERN 필터 적용
2. `market_watch` (시장감시위원회 reportCd 목록)
3. 1+2 병합 (문서번호 중복 제거)
4. `mgmt`(cat)
5. `alert`(cat)
6. `misc`(cat)
7. `investor_warning` (12개 reportCd)
8. `shortterm_overheat` (`reportNm="단기과열"`)
9. `delist` (2개 reportCd)

---

## 5. 데이터 수집 방식

KIND에서 공시를 수집하는 방식은 **두 가지**로 나뉩니다:

### 5-1. 카테고리 기반 (cat)

KIND의 **상세검색 → 카테고리(disclosureType02)** 를 이용합니다.

```
POST https://kind.krx.co.kr/disclosure/details.do
disclosureType02 = "0311|"   ← 카테고리 코드
fromDate / toDate            ← 조회 기간
```

사용 메뉴: `halt`, `mgmt`, `alert`, `misc`

코드 매핑 (`CODE_MAP`):
| 키 | 코드 | 의미 |
|----|------|------|
| `halt` | `0311` | 거래정지/재개 |
| `mgmt` | `0350` | 관리종목 |
| `alert` | `0356` | 투자주의·환기 |
| `misc` | `0305` | 기타 시장안내 |

### 5-2. reportCd 기반 (warn payload)

KIND의 **제목검색(reportNm) + reportCd** 조합을 이용합니다.  
카테고리 코드 없이, `bfrDsclsType=on`이 포함된 페이로드를 사용합니다.

```
POST https://kind.krx.co.kr/disclosure/details.do
reportNm = "투자경고종목지정"
reportCd = "68809"
bfrDsclsType = "on"
```

사용 메뉴: `inv`, `overheat`, `delist`, 그리고 `halt`에 병합되는 `market_watch`

### reportCd 목록

#### 투자경고·위험 (`TARGETS_WARN`) — 12개

| 공시 유형 | reportCd (유가) | reportCd (코스닥) |
|-----------|-----------------|-------------------|
| 투자경고종목 지정 | 68809 | 70804 |
| 투자경고종목 지정(재지정) | 68823 | 72049 |
| 투자경고종목 지정해제 | 68824 | 72056 |
| [투자주의] 지정해제 및 재지정 예고 | 68810 | 70820 |
| 투자위험종목 지정 | 68812 | 70832 |
| 투자위험종목 지정해제 | 68813 | 70834 |

#### 시장감시위원회 (`TARGETS_MARKET_WATCH`) — 24개

유가증권 12개 + 코스닥 12개로 구성. 단기과열완화장치 발동/예고, 매매거래 정지/재개(투자경고·위험 지정중), 장애종목 매매거래 정지/재개 등이 포함됩니다.

주요 reportCd:

| 유형 | 유가 | 코스닥 |
|------|------|--------|
| 단기과열완화장치 발동예고 | 99432 | 70729 |
| 단기과열완화장치 발동 | 99431 | 70728 |
| 매매거래 정지/재개 (투자경고 지정중) | 68818 | 70837 |
| 매매거래 정지/재개 (투자위험 지정중) | 68815 | 70836 |
| 매매거래 정지/재개 (투자위험 최초지정) | 68819 | 70838 |

#### 상장폐지 (`TARGETS_DELIST`) — 2개

| 시장 | reportCd |
|------|----------|
| 유가증권 | 68051 |
| 코스닥 | 70769 |

---

## 6. NXT 종목 매핑

### 매핑 방식: 종목코드 기준

넥스트레이드(NXT) 종목과 KIND 공시를 **종목코드**로 매핑합니다.

두 시스템의 종목코드 체계가 다르기 때문에 변환이 필요합니다:

```
NXT  원본:  A005680  (7자리, isuSrdCd)
       ↓  fnc.py에서 str[1:] 처리
NXT  단축코드: 005680  (6자리)
       ↓  menu2.py에서 str[:5] 처리
매핑키:     00568   (5자리)

KIND 종목코드: 00568   (5자리, 마지막 체크디짓 누락)
```

### 코드 흐름

```python
# menu2.py 내부
nxt_df["_code5"] = nxt_df["단축코드"].astype(str).str[:5]  # 6자리 → 5자리
nxt_codes = set(nxt_df["_code5"]) - {""}

# KIND 공시의 종목코드(5자리)와 직접 비교
df_nxt_trade = df_all_show[df_all_show["종목코드"].isin(nxt_codes)]
```

### 비고(거래불가사유) 매핑

NXT 데이터에 포함된 `거래불가사유` 컬럼을 축약하여 비고란에 표시합니다:

| 원본 | 축약 |
|------|------|
| 투자경고/위험 | 경/위 |
| 단기과열 | 과열 |
| 거래정지 | 정지 |

---

## 7. UI 기능 상세

### 사이드바

| 영역 | 설명 |
|------|------|
| 📆 KIND 조회 기간 | 시작일/종료일 선택 (기본: 최근 5일) |
| ⚠️ KIND 시장조치 공시 | 카테고리 라디오 버튼 (8개 메뉴) |
| 🔎 제목/종목/시간 검색 | 키워드 필터 (공시제목 + 종목명 대상, 대소문자 무시) |
| 조회 시간 | 시작/종료 시간 프리셋 (00:00~23:59, 14:28~14:31 등) |
| 체크박스 | 단기과열 `(예고)` 공시 제외 옵션 (메뉴별 조건부 표시) |
| 버튼 | 공시 조회 / 🔄 강제 새로조회 / 🧹 초기화 |

### 메인 영역 — 탭 2개

| 탭 | 내용 |
|----|------|
| 1) 넥스트레이드 종목 | NXT 등록 종목에 해당하는 공시만 필터. 당일 공시는 🟡 + 파란 하이라이트 |
| 2) KRX 전체 | 전체 공시 표시. NXT 종목 행은 노란 하이라이트 |

### 표시 컬럼

| 컬럼 | 설명 |
|------|------|
| 당일 | 종료일 기준 당일이면 🟡 |
| 시간 | `yy/mm/dd HH:MM` 형식 |
| 종목명 | 회사명 (스팩 제외) |
| 종목코드 | 매칭용 (화면에 표시하지 않음) |
| 비고 | NXT 거래불가사유 축약 |
| 공시제목 | KRX KIND 뷰어 링크 (클릭 시 이동) |

### 복사 기능

각 탭 상단의 📋 복사 버튼으로 탭 구분 텍스트를 클립보드에 복사합니다.  
복사 내용: `당일 / 시간 / 종목명 / 비고 / 공시제목 / 링크`

---

## 8. fnc.py — 외부 데이터 조회

### `get_krx_market_price_info(trdDd)`

KRX 전체 종목 시세를 조회합니다. (`data.krx.co.kr`)

- 반환: `(조회시간, DataFrame)`
- 컬럼: 시장구분, 표준코드, 단축코드, 종목명, 시가총액, 상장주식수, KRX종가, KRX거래량
- KONEX 제외, KOSDAQ GLOBAL → KOSDAQ 변환

### `get_krx_index(trdDd)`

KOSPI 200 / KOSDAQ 150 구성종목을 조회합니다.

- 반환: `DataFrame` (지수구분, 단축코드, 종목명)

### `get_nextrade_filtered_symbols(trdDd)`

넥스트레이드(NXT) 등록 종목을 조회합니다. (`nextrade.co.kr`)

- 반환: `(조회시간, DataFrame)`
- 컬럼: 시장구분, 표준코드, **단축코드**, 종목명, NXT현재가, NXT거래량, 거래대금, 거래가능시장, **거래불가사유**
- 원본 `isuSrdCd`(예: `A005680`)에서 앞 `A`를 제거하여 6자리 단축코드(`005680`)로 저장

---

## 9. fnc2.py — KIND 크롤링 엔진

### 공개 함수

| 함수 | 설명 | 수집 방식 |
|------|------|-----------|
| `kind_fetch(category, from_date, to_date)` | 카테고리 기반 수집 (halt/mgmt/alert/misc) | cat |
| `fetch_investor_warning(from_date, to_date)` | 투자경고·위험 12개 reportCd 순회 | warn payload |
| `fetch_shortterm_overheat(from_date, to_date)` | 단기과열 (`reportNm="단기과열"`) | warn payload |
| `fetch_market_watch(from_date, to_date)` | 시장감시위원회 24개 reportCd 순회 | warn payload |
| `fetch_delist(from_date, to_date)` | 상장폐지 2개 reportCd | warn payload |

### 내부 함수

| 함수 | 역할 |
|------|------|
| `_kind_disclosure_search()` | 카테고리 기반 페이지네이션 수집 (GET 워밍업 → POST 반복) |
| `_fetch_reportcd_with_warn_payload()` | reportCd 목록 × 페이지네이션 수집 (공통 엔진) |
| `_parse_rows_html()` | BeautifulSoup으로 KIND 테이블 HTML 파싱 |
| `_extract_company_cell()` | 회사명 셀에서 시장/플래그/회사명/종목코드 추출 |
| `_make_df()` | 문서번호 중복 제거 + 시간 내림차순 + 스팩 제외 |
| `_looks_like_valid_kind_table()` | 정상 응답인지 검증 (차단/오류 감지) |

### 파싱 결과 컬럼

```
번호, 시간, 시장, 플래그, 회사명, 종목코드, 공시제목, 문서번호, 뷰어URL, 제출인
```

- **종목코드**: KIND HTML에서 `companysummary_open('00568')` onclick에서 추출, **5자리** (마지막 체크디짓 누락)
- **뷰어URL**: `https://kind.krx.co.kr/common/disclsviewer.do?...#공시제목` 형태
- **스팩 자동 제외**: 회사명에 "스팩" 포함 시 제거

---

## 10. menu2.py — Streamlit 앱

### 주요 상수/패턴

| 이름 | 용도 |
|------|------|
| `HALT_PATTERN` | halt(cat) 결과에서 거래정지 관련 공시만 필터링하는 정규식 |
| `INV_SUFFIX_EXCLUDE` | 우선주(예: `(우)`, `(우B)`)로 끝나는 공시 제외 정규식 |
| `OVERHEAT_PATTERN` | 단기과열 공시 식별용 정규식 |
| `FORECAST_PREFIX` | `(예고)`로 시작하는 공시 식별용 정규식 |

### 데이터 흐름

```
[사이드바 입력]
    ↓
[_fetch()] — 메뉴별 분기
    ├─ multi → _fetch_multi() → 전체 수집 + 병합
    ├─ cat   → kind_fetch() + (halt일 때 market_watch 병합)
    ├─ inv   → fetch_investor_warning()
    ├─ overheat → fetch_shortterm_overheat()
    └─ delist → fetch_delist()
    ↓
[키워드 필터] — 공시제목 + 종목명 대상
    ↓
[(예고) 제외] — overheat/multi 메뉴에서 선택적 적용
    ↓
[시간 필터] — 시작/종료 시간 범위
    ↓
[build_display_df()] — 표시용 변환 (시간 포맷, 당일 표시, 종목코드 포함)
    ↓
[NXT 매핑] — 종목코드(5자리) 기준 매핑 + 비고 부착
    ↓
[탭 1: NXT 종목] ← 종목코드 필터
[탭 2: KRX 전체] ← NXT 종목 하이라이트
```

### 스타일링

| 함수 | 대상 | 스타일 |
|------|------|--------|
| `style_today_rows()` | 탭1 (NXT) | 당일(🟡) 행 → 파란 배경 (`#e8f4ff`) |
| `style_nxt_rows()` | 탭2 (KRX 전체) | NXT 종목 행 → 노란 배경 (`#fff4b6`) |

---

## 11. 종목코드 체계

이 프로젝트에서 다루는 종목코드의 변환 관계:

```
KRX 표준코드:  KR7005680005  (12자리 ISIN)
KRX 단축코드:  005680        (6자리)
NXT isuSrdCd:  A005680       (7자리, 앞에 A 접두)
NXT 단축코드:  005680        (6자리, fnc.py에서 A 제거 후)
KIND 종목코드: 00568         (5자리, 마지막 체크디짓 누락)

매핑 방법:
  NXT 단축코드[:5] == KIND 종목코드
  예) "005680"[:5] = "00568" == KIND "00568" ✅
```

---

## 12. 캐시 및 세션 관리

| 메커니즘 | 설명 |
|----------|------|
| `@st.cache_data(ttl=60)` | `_fetch()`, `_fetch_multi()`에 적용. 동일 (메뉴, 시작일, 종료일) 조합은 60초간 캐시 |
| `st.session_state["menu_cache"]` | 조회 결과를 세션에 저장. 메뉴/기간 전환 시 재활용 |
| `st.session_state["force_nonce"]` | 🔄 강제 새로조회 시 nonce 증가 → 캐시 키 변경으로 강제 재수집 |
| 🧹 초기화 | `st.cache_data.clear()` + `st.session_state.clear()` + `st.rerun()` |

### 캐시 키 구조

```python
cache_key = (menu_key, from_date_str, to_date_str)
# 예: ("multi", "2026-04-10", "2026-04-15")
```

키워드 필터와 시간 필터는 캐시된 원본 데이터에 대해 **클라이언트 측에서만** 적용되므로,  
필터를 변경해도 KIND에 재요청하지 않습니다.

---

## 13. 에러 핸들링

### KIND 차단/비정상 응답 감지

```python
def _looks_like_valid_kind_table(html: str) -> bool:
    return 'list type-00 mt10' in html
```

KIND가 200 OK를 반환하지만 실제 테이블이 없는 경우 (IP 차단, 세션 만료, 점검 등)를  
감지하여 `RuntimeError`를 발생시킵니다.

### NXT 조회 실패 시 Fallback

```python
try:
    from fnc import get_nextrade_filtered_symbols
except Exception:
    def get_nextrade_filtered_symbols(yyyymmdd):
        return yyyymmdd, pd.DataFrame(columns=["종목명"])
```

`fnc.py`가 없거나 import 실패 시에도 앱은 정상 동작합니다.  
단, NXT 매핑이 비활성화되어 탭1이 항상 비어있게 됩니다.

---

## 14. 커스터마이징

### reportCd 추가

`fnc2.py`의 `TARGETS_*` 리스트에 튜플을 추가합니다:

```python
TARGETS_DELIST.append(
    ("상장폐지", "새로운코드", "상장폐지", "상장폐지"),
)
```

튜플 형식: `(reportNm, reportCd, reportNmTemp, reportNmPop)`

### 메뉴 추가

1. `fnc2.py`에 수집 함수 추가 (또는 기존 함수 재활용)
2. `menu2.py`의 `MENU_SPEC`에 항목 추가
3. `FETCHER_MAP`에 동작 매핑 추가
4. `_fetch()`에 분기 추가
5. (선택) `_fetch_multi()`에 모아보기 통합

### sleep 조절

KIND 서버 부하를 고려하여 요청 간 대기 시간이 설정되어 있습니다:

| 함수 | 기본 sleep | 비고 |
|------|-----------|------|
| `_kind_disclosure_search` | 5초 | 카테고리 수집 (페이지 간) |
| `fetch_investor_warning` | 1초 | reportCd당 |
| `fetch_shortterm_overheat` | 3초 | 페이지 간 |
| `fetch_market_watch` | 3초 | reportCd당 |
| `fetch_delist` | 3초 | reportCd당 |

차단이 잦다면 sleep 값을 늘려보세요.

---

## 15. 주의사항

- **KIND 차단**: 짧은 시간에 과다 요청 시 IP 차단될 수 있습니다. sleep 값을 충분히 유지하세요.
- **SSL 인증서**: `verify=False`로 설정되어 있어 SSL 경고가 발생할 수 있습니다. 운영 환경에서는 인증서 설정을 검토하세요.
- **종목코드 5자리**: KIND HTML 파싱 특성상 마지막 체크디짓이 누락됩니다. 외부 시스템 연동 시 주의가 필요합니다.
- **주말/공휴일**: 종료일이 주말이면 가장 가까운 이전 평일로 자동 보정됩니다 (공휴일은 미반영).
- **스팩 제외**: 회사명에 "스팩"이 포함된 종목은 자동으로 결과에서 제외됩니다.
- **우선주 제외**: 공시제목이 `(우)`, `(우B)` 등으로 끝나는 항목은 투자경고·위험, 단기과열, 상장폐지, 시장감시위원회 수집 시 제외됩니다.
