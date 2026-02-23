# ... (앞부분 동일)

def _merge_halt_and_mw(df_halt_cat, df_mw):
    # 빈 리스트일 경우 예외 처리 추가
    to_concat = [x for x in [df_halt_cat, df_mw] if x is not None and not x.empty]
    if not to_concat: 
        return pd.DataFrame(columns=["시간", "회사명", "공시제목", "뷰어URL", "문서번호"])
    
    merged = pd.concat(to_concat, ignore_index=True)
    if "문서번호" in merged.columns: 
        merged = merged.drop_duplicates(subset=["문서번호"], keep="first")
    if "시간" in merged.columns:
        merged["__ts"] = pd.to_datetime(merged["시간"], errors="coerce")
        merged = merged.sort_values("__ts", ascending=False).drop(columns="__ts")
    return merged.reset_index(drop=True)

# ... (중략)

def _fetch_multi(f, t):
    h_cat = api.kind_fetch("halt", f, t)
    if not h_cat.empty: 
        h_cat = h_cat[h_cat["공시제목"].astype(str).str.contains(HALT_PATTERN, na=False)]
    
    mw = api.fetch_market_watch(f, t)
    halt = _merge_halt_and_mw(h_cat, mw)
    
    # 각 카테고리별 데이터 수집
    dfs = [
        halt, 
        api.kind_fetch("mgmt", f, t), 
        api.kind_fetch("alert", f, t), 
        api.kind_fetch("misc", f, t), 
        api.fetch_investor_warning(f, t), 
        api.fetch_shortterm_overheat(f, t)
    ]
    
    # concat 전 비어있는지 확인 (에러 방지 핵심)
    valid_dfs = [x for x in dfs if x is not None and not x.empty]
    if not valid_dfs:
        return pd.DataFrame(columns=["시간", "회사명", "공시제목", "뷰어URL", "문서번호"])

    merged = pd.concat(valid_dfs, ignore_index=True)
    return merged.drop_duplicates(subset=["문서번호"]).sort_values("시간", ascending=False).reset_index(drop=True)

# ... (뒷부분 동일)
