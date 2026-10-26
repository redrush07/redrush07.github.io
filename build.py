# -*- coding: utf-8 -*-
"""
주식 AI 사이트 키트 — 종목별 페이지 자동 생성기

  python build.py            # 실제 데이터로 전체 사이트 생성 → site/ 폴더
  python build.py --limit 50 # 시가총액 상위 50종목만 (빠른 테스트)
  python build.py --demo     # 인터넷 없이 가짜 샘플 데이터로 화면만 확인

데이터: 1순위 FinanceDataReader(네이버·KRX), 실패하면 2순위 다음 금융.
"""
import argparse, json, math, os, random, re, shutil, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "site"
KST = timezone(timedelta(hours=9))
# ───── 사이트별로 바꾸는 값의 기본값 (config.json에 같은 이름으로 적으면 덮어씀) ─────
DEFAULTS = {
    "theme": {  # 색상
        "ink": "#0F1B2D", "ink2": "#16263D", "bg": "#F4F5F7",
        "accent": "#B3122B", "ai": "#5B3DF5", "highlight": "#FFE14D",
        "up": "#D21F2D", "down": "#1B5FCC",
    },
    "copy": {  # 화면 문구 (일부는 HTML 태그 <b>, <br> 사용 가능)
        "home_title": "{site} | AI 주가예측 무료 — 코스피·코스닥 전 종목",
        "home_desc": "종목명만 입력하면 AI가 5거래일 뒤 상승 확률과 예상 가격 범위를 무료로 계산해 드립니다. 코스피·코스닥 {count}개 전 종목, 매 거래일 장 마감 후 갱신.",
        "promo": "AI 주가예측 전 종목 <b>무료</b> 공개 · 매 거래일 장 마감 후 갱신",
        "nav_ai": "AI 주가예측",
        "hero_badges": ["100% 무료", "가입 없이 바로"],
        "hero_title": "종목만 입력하세요.<br>AI가 주가를 예측합니다.",
        "hero_sub": "머신러닝 AI가 매일 장 마감 후 전 종목을 다시 학습해 5거래일 뒤 상승 확률을 계산합니다.",
        "tool_button": "AI 예측 돌리기",
        "news_title": "오늘의 주요 종목 뉴스",
        "hooks_title": "기본 예측으로 부족하다면",
        "hooks": [
            {"style": "ai", "tag": "AI 심층분석", "title": "AI가 더 깊게 분석합니다",
             "bullets": ["매수·매도 타이밍 구간 분석", "수급·변동성·업종 흐름까지 반영한 심층 리포트", "목표 구간과 손절 구간 설명"],
             "button": "AI 심층분석 받기", "hook": "AI 심층분석",
             "sub": "번호를 남기시면 심층분석 결과를 전화·카카오톡으로 직접 설명해 드립니다."},
            {"style": "pro", "tag": "전문가 종목상담", "title": "전문가에게 물어보세요", "title_nostock": "보유·관심 종목, 전문가에게 물어보세요",
             "bullets": ["지금 사도 될지, 팔아야 할지 1:1 상담", "물린 종목 대응 전략 상담", "상담 비용 없음"],
             "button": "전문가 상담 신청", "hook": "전문가 종목상담",
             "sub": "번호를 남기시면 전문가가 직접 연락드립니다."},
        ],
        "lead": {
            "badge": "상담 비용 0원 · 100% 무료",
            "title_1": "전문가", "title_2": "무료 종목상담",
            "sub": "휴대폰 번호만 남기시면 영업일 기준 {reply_days}일 안에 전문가가 직접 연락드립니다.",
            "points": ["보유·관심 종목 1:1 진단", "AI 분석 결과 상세 설명", "물린 종목 대응 전략 상담"],
            "free_big": "0원", "free_text": "가입·결제 없이 번호만 남기면 끝",
            "button": "전문가 무료 종목상담 신청", "hook": "전문가 무료 종목상담",
        },
        "popup_title": "{stock}, 지금 사도 될까요?",
        "popup_sub": "AI 분석 결과를 바탕으로 무료로 상담해 드립니다. 번호만 남겨주세요.",
        "comment_label": "AI 코멘트",
    },
    "design": {  # 디자인 프리셋: night | paper | neon | soft | grid | big | aurora
        "preset": "night",
        "home_sections": ["news", "hooks", "reports"],  # 메인에서 검색 도구 아래 섹션 순서
    },
    "features": {  # 기능 켜고 끄기
        "news": True, "reports": True, "comment": True, "popup": True, "ai_page": True,
    },
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


CFG = _merge(DEFAULTS, json.loads((ROOT / "config.json").read_text(encoding="utf-8")))

DAUM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Referer": "https://finance.daum.net/",
    "Accept": "application/json",
}


def log(*a):
    print(datetime.now(KST).strftime("%H:%M:%S"), *a, flush=True)


# ════════════════════════ 1. 데이터 수집 ════════════════════════

def listing_fdr():
    import FinanceDataReader as fdr
    df = fdr.StockListing("KRX")
    df = df.rename(columns={"Code": "code", "Name": "name", "Market": "market", "Marcap": "marcap", "Stocks": "shares"})
    df["market"] = df["market"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})
    df = df[["code", "name", "market", "marcap", "shares"]]
    try:  # 업종 정보 (실패해도 진행)
        desc = fdr.StockListing("KRX-DESC").rename(columns={"Code": "code", "Sector": "sector", "Industry": "industry"})
        df = df.merge(desc[["code", "sector", "industry"]], on="code", how="left")
    except Exception as e:
        log("업종 정보 생략:", e)
    return df


def listing_daum():
    import requests
    rows = []
    for m in CFG["markets"]:
        for p in range(1, 15):
            url = f"https://finance.daum.net/api/trend/market_capitalization?page={p}&perPage=200&fieldName=marketCap&order=desc&market={m}&pagination=true"
            j = requests.get(url, headers=DAUM_HEADERS, timeout=15).json()
            data = j.get("data") or []
            for d in data:
                rows.append({"code": d["symbolCode"][1:], "name": d["name"], "market": m,
                             "marcap": d.get("marketCap"), "shares": d.get("listedShareCount")})
            if len(data) < 200:
                break
    return pd.DataFrame(rows)


def get_sectors_naver():
    """네이버 증권 업종 분류(약 80개)를 종목코드 → 업종명으로 가져옴"""
    import requests
    H = {"User-Agent": DAUM_HEADERS["User-Agent"], "Referer": "https://finance.naver.com/",
         "Accept-Language": "ko-KR,ko;q=0.9"}
    base = "https://finance.naver.com"
    r = requests.get(base + "/sise/sise_group.naver?type=upjong", headers=H, timeout=15)
    r.encoding = "euc-kr"
    groups = dict(re.findall(r'type=upjong&(?:amp;)?no=(\d+)[^>]*>\s*([^<]+?)\s*</a>', r.text))
    if len(groups) < 10:
        raise RuntimeError(f"네이버 업종 목록 인식 실패 (HTTP {r.status_code}, {len(r.text)}자, 업종 {len(groups)}개)")
    out = {}

    def one(item):
        no, name = item
        rr = requests.get(f"{base}/sise/sise_group_detail.naver?type=upjong&no={no}", headers=H, timeout=15)
        rr.encoding = "euc-kr"
        return name.strip(), re.findall(r'code=(\d{6})', rr.text)

    with ThreadPoolExecutor(6) as ex:
        for name, codes in ex.map(one, groups.items()):
            for c in codes:
                out.setdefault(c, name)
    return out


def get_sectors_kind():
    """한국거래소 KIND 상장회사 목록의 '업종' (예: 반도체 제조업)"""
    import requests, html as _html
    url = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
    r = requests.get(url, headers={"User-Agent": DAUM_HEADERS["User-Agent"]}, timeout=30)
    r.encoding = "euc-kr"
    out = {}
    for row in re.findall(r"<tr>(.*?)</tr>", r.text, re.S):
        cells = [_html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        # 회사명, 시장구분, 종목코드, 업종, 주요제품, ... (열 순서가 바뀌어도 6자리 코드 다음 칸을 업종으로 봄)
        for i, c in enumerate(cells):
            if re.fullmatch(r"[0-9A-Z]{6}", c) and i + 1 < len(cells):
                if cells[i + 1]:
                    out[c] = cells[i + 1]
                break
    if len(out) < 500:
        raise RuntimeError(f"KIND 업종 인식 실패 (HTTP {r.status_code}, {len(r.text)}자, {len(out)}종목)")
    return out


def get_sectors():
    for fn in (get_sectors_naver, get_sectors_kind):
        try:
            sec = fn()
            log(f"업종 분류({fn.__name__}): {len(set(sec.values()))}개 업종, {len(sec)}개 종목")
            if len(sec) > 500:
                return sec
        except Exception as e:
            log(f"{fn.__name__} 실패:", repr(e)[:200])
    return {}


def fetch_news(name, limit=5, days=7):
    """구글 뉴스 RSS에서 종목 관련 최신 기사 제목·언론사·시간·링크만 가져옴 (본문은 가져오지 않음)"""
    import requests, urllib.parse, xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    q = urllib.parse.quote(f'"{name}" 주가 OR "{name}" 주식')
    url = f"https://news.google.com/rss/search?q={q}+when:{days}d&hl=ko&gl=KR&ceid=KR:ko"
    r = requests.get(url, headers={"User-Agent": DAUM_HEADERS["User-Agent"]}, timeout=15)
    r.raise_for_status()
    items = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3].strip()
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
        except Exception:
            dt = None
        link = (it.findtext("link") or "").strip()
        if title and link.startswith("https://"):
            items.append({"title": title, "source": src, "link": link,
                          "time": dt.strftime("%m.%d %H:%M") if dt else "", "ts": dt.timestamp() if dt else 0})
    items.sort(key=lambda x: -x["ts"])
    seen, out = set(), []
    for x in items:
        k = x["title"][:30]
        if k not in seen:
            seen.add(k)
            out.append(x)
        if len(out) >= limit:
            break
    return out


def get_news(stocks):
    n = int(CFG.get("news_top_n", 300))
    targets = sorted(stocks, key=lambda s: -(s["marcap"] or 0))[:n]
    got, fail = 0, 0

    def one(s):
        for attempt in range(2):
            try:
                return s, fetch_news(s["name"])
            except Exception:
                time.sleep(1 + attempt)
        return s, None

    with ThreadPoolExecutor(4) as ex:
        for s, news in ex.map(one, targets):
            if news is None:
                fail += 1
            elif news:
                s["news"] = news
                got += 1
    log(f"종목 뉴스: {got}개 종목 수집 (대상 {len(targets)}, 실패 {fail})")


def get_listing():
    for fn in (listing_fdr, listing_daum):
        try:
            df = fn()
            if len(df) > 500:
                log(f"종목 목록: {fn.__name__} → {len(df)}개")
                return df
        except Exception as e:
            log(f"{fn.__name__} 실패:", e)
    sys.exit("종목 목록을 가져오지 못했습니다.")


def hist_fdr(code, start):
    import FinanceDataReader as fdr
    df = fdr.DataReader(code, start)
    return df[["Open", "High", "Low", "Close", "Volume"]]


def hist_daum(code, start):
    import requests
    rows, sym = [], "A" + code
    for p in range(1, 5):
        url = f"https://finance.daum.net/api/quote/{sym}/days?symbolCode={sym}&page={p}&perPage=100&pagination=true"
        data = requests.get(url, headers=DAUM_HEADERS, timeout=15).json().get("data") or []
        for d in data:
            rows.append({"Date": d["date"][:10], "Open": d["openingPrice"], "High": d["highPrice"],
                         "Low": d["lowPrice"], "Close": d["tradePrice"], "Volume": d["accTradeVolume"]})
        if len(data) < 100 or (data and data[-1]["date"][:10] < start):
            break
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])
    return df.set_index("Date").sort_index()


def clean_ohlc(df):
    """거래정지일처럼 가격이 0이거나 비정상인 날을 걸러냄"""
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    df = df[(df["Volume"] > 0) & (df["Close"] > 0) & (df["High"] > 0) & (df["Low"] > 0)].copy()
    df["Open"] = df["Open"].where(df["Open"] > 0, df["Close"])
    df["High"] = df[["High", "Open", "Close"]].max(axis=1)
    df["Low"] = df[["Low", "Open", "Close"]].min(axis=1)
    return df


def get_history(code, start):
    for fn in (hist_fdr, hist_daum):
        for attempt in range(2):
            try:
                df = fn(code, start)
                df = clean_ohlc(df)
                if len(df) >= 2:
                    return df
            except Exception:
                time.sleep(0.5 + attempt)
    return None


# ════════════════════════ 데모 데이터 (테스트 전용) ════════════════════════

def demo_data(n=40):
    """실제 회사가 아닌 가짜 종목. 화면 확인용."""
    random.seed(7)
    sectors = ["반도체", "2차전지", "바이오", "자동차부품", "게임", "조선"]
    lst, hist = [], {}
    end = datetime.now(KST).date()
    days = pd.bdate_range(end=end, periods=260)
    for i in range(n):
        code = f"9{i:05d}"
        price = random.choice([3000, 12000, 45000, 180000])
        o = []
        for d in days:
            ch = random.gauss(0.0006, 0.022)
            if d == days[-1] and i % 6 == 0:  # 데모: 급등·거래량 폭발 화면 확인용
                ch = random.uniform(0.12, 0.30)
            op = price * (1 + random.gauss(0, 0.008))
            cl = max(100, op * (1 + ch))
            hi, lo = max(op, cl) * (1 + abs(random.gauss(0, 0.01))), min(op, cl) * (1 - abs(random.gauss(0, 0.01)))
            vol = int(abs(random.gauss(1, 0.4)) * 300000 * (3 if random.random() < 0.03 else 1) * (12 if d == days[-1] and i % 6 == 0 else 1))
            o.append([d, round(op), round(hi), round(lo), round(cl), vol])
            price = cl
        df = pd.DataFrame(o, columns=["Date", "Open", "High", "Low", "Close", "Volume"]).set_index("Date")
        shares = random.choice([5e6, 2e7, 8e7])
        lst.append({"code": code, "name": f"샘플종목{i+1:02d}", "market": "KOSPI" if i % 2 else "KOSDAQ",
                    "marcap": df.Close.iloc[-1] * shares, "shares": shares, "sector": sectors[i % len(sectors)]})
        hist[code] = df
    return pd.DataFrame(lst), hist


# ════════════════════════ 2. 지표 계산 ════════════════════════

def pct(a, b):
    return (a / b - 1) * 100 if b else None


def compute(row, df):
    df = clean_ohlc(df).tail(260)
    if len(df) < 2:
        raise ValueError("유효한 시세가 부족합니다")
    last, prev = df.iloc[-1], df.iloc[-2]
    c = float(last.Close)
    y = df.tail(250)
    hi52, lo52 = float(y.High.max()), float(y.Low.min())
    vol20 = float(df.Volume.iloc[-21:-1].mean()) if len(df) > 21 else None

    def ret(n):
        return pct(c, float(df.Close.iloc[-n - 1])) if len(df) > n else None

    shares = row.get("shares")
    marcap = row.get("marcap")
    if (not marcap or pd.isna(marcap)) and shares:
        marcap = c * shares

    recent = []
    rd = df.tail(11)
    for i in range(len(rd) - 1, 0, -1):
        r, p = rd.iloc[i], rd.iloc[i - 1]
        recent.append({"date": rd.index[i].strftime("%m.%d"), "close": float(r.Close),
                       "chg": pct(float(r.Close), float(p.Close)), "volume": int(r.Volume)})

    sector = row.get("sector")
    sector = sector if isinstance(sector, str) and sector.strip() else None
    # 연속 상승·하락 일수 (종가 기준)
    cl = df["Close"].astype(float).values
    su = sd = 0
    for i in range(len(cl) - 1, 0, -1):
        if cl[i] > cl[i - 1] and sd == 0:
            su += 1
        elif cl[i] < cl[i - 1] and su == 0:
            sd += 1
        else:
            break
    streak_gain = pct(c, float(cl[-su - 1])) if su else None
    return {
        "streak_up": su, "streak_dn": sd, "streak_gain": streak_gain,
        "hi_today": pct(float(last.High), float(prev.Close)),
        "code": row["code"], "name": row["name"], "market": row["market"],
        "sector": sector,
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "date_kr": f"{df.index[-1].month}월 {df.index[-1].day}일",
        "open": float(last.Open), "high": float(last.High), "low": float(last.Low), "close": c,
        "prev": float(prev.Close), "change": c - float(prev.Close), "chg": pct(c, float(prev.Close)),
        "volume": int(last.Volume), "amount": c * float(last.Volume),
        "vol_ratio": (float(last.Volume) / vol20) if vol20 else None,
        "marcap": float(marcap) if marcap and not pd.isna(marcap) else None,
        "shares": float(shares) if shares and not pd.isna(shares) else None,
        "hi52": hi52, "lo52": lo52,
        "from_hi": pct(c, hi52), "from_lo": pct(c, lo52),
        "r1w": ret(5), "r1m": ret(20), "r3m": ret(60), "r6m": ret(120), "r1y": ret(245),
        "recent": recent,
        "chart": chart_svg(df.tail(120)),
        **tech_stats(df),
    }


def tech_stats(df):
    """AI 코멘트용 기술 지표"""
    c = df["Close"].astype(float)
    ret = c.pct_change()
    out = {"ma20": None, "ma60": None, "volat": None, "rsi": None, "hi20": None, "lo20": None}
    if len(c) >= 20:
        out["ma20"] = float(c.tail(20).mean())
        out["hi20"] = float(df["High"].tail(20).max())
        out["lo20"] = float(df["Low"].tail(20).min())
        sd = ret.tail(20).std()
        if sd == sd:
            out["volat"] = float(sd) * math.sqrt(252) * 100
    if len(c) >= 60:
        out["ma60"] = float(c.tail(60).mean())
    if len(c) >= 15:
        g = ret.clip(lower=0).tail(14).mean()
        l = (-ret.clip(upper=0)).tail(14).mean()
        if g + l > 0:
            out["rsi"] = float(100 * g / (g + l))
    return out


def ai_comment(s):
    """'AI 단기 코멘트' — 숫자 먼저, 짧게. (투자 권유 문구 없음)"""
    import hashlib
    seed = int(hashlib.md5((s["code"] + s["date"]).encode()).hexdigest(), 16)
    pick = lambda opts, k=0: opts[(seed >> (k * 3)) % len(opts)]
    c = s["close"]
    ma20, ma60 = s.get("ma20"), s.get("ma60")
    vr = s.get("vol_ratio")
    pts = []

    # 1) 오늘 (급등스캐너 고유: 등락·거래량·연속일수를 한 줄로)
    today = [f"{fmt_pct(s['chg'])}"]
    if vr:
        today.append(f"거래량 {vr:.1f}배")
    if s.get("streak_up", 0) >= 2:
        today.append(f"{s['streak_up']}일 연속 상승(누적 {fmt_pct(s['streak_gain'])})")
    elif s.get("streak_dn", 0) >= 2:
        today.append(f"{s['streak_dn']}일 연속 하락")
    if s.get("hi_today") is not None and s["chg"] is not None and s["hi_today"] - s["chg"] >= 5:
        today.append(f"장중 고가 {fmt_pct(s['hi_today'])}에서 밀림")
    pts.append(("오늘", " · ".join(today) + "."))

    # 2) 추세
    trend = "중립"
    if ma20 and ma60:
        a20, a60 = c >= ma20, c >= ma60
        trend = "상승" if a20 and a60 else "반등" if a20 else "조정" if a60 else "하락"
        lab = {"상승": "20·60일선 위", "반등": "20일선 위 / 60일선 아래", "조정": "20일선 아래 / 60일선 위", "하락": "20·60일선 아래"}[trend]
        gap20 = pct(c, ma20)
        pts.append(("추세", f"{lab}. 20일선 대비 {fmt_pct(gap20)} (20일 {fmt_price(ma20)} · 60일 {fmt_price(ma60)})."))

    # 3) 모멘텀
    if s.get("r1w") is not None or s.get("r1m") is not None:
        m = f"1주 {fmt_pct(s.get('r1w'))} · 1개월 {fmt_pct(s.get('r1m'))}"
        rsi = s.get("rsi")
        if rsi is not None:
            m += f" · RSI {rsi:.0f}" + (" (과열)" if rsi >= 70 else " (과매도)" if rsi <= 30 else "")
        pts.append(("모멘텀", m + "."))

    # 4) 변동성
    vol = s.get("volat")
    if vol is not None:
        lvl = "매우 큼" if vol >= 80 else "큼" if vol >= 50 else "보통" if vol >= 25 else "작음"
        pos = f"연 변동성 {vol:.0f}%({lvl})"
        if s.get("from_hi") is not None:
            pos += f" · 52주 고점 대비 {fmt_pct(s['from_hi'])}"
        pts.append(("변동성", pos + "."))

    # 5) AI
    ai = s.get("ai") or {}
    if ai.get("p") is not None:
        pts.append(("AI 5일", f"상승 확률 {ai['p']*100:.0f}% ({ai['signal']}) · 범위 {fmt_price(ai['lo'])}~{fmt_price(ai['hi'])}원."))

    # 헤드라인 — 숫자 먼저
    chg = s["chg"] or 0
    if chg >= 15:
        head = f"{fmt_pct(chg)} 급등" + (f" · 거래량 {vr:.1f}배" if vr and vr >= 2 else "") + " · 변동성 주의"
    elif vr and vr >= 5:
        head = f"거래량 {vr:.1f}배 폭발 · {fmt_pct(chg)}"
    elif s.get("streak_up", 0) >= 4:
        head = f"{s['streak_up']}일 연속 상승 · 누적 {fmt_pct(s['streak_gain'])}"
    elif s.get("streak_dn", 0) >= 4:
        head = f"{s['streak_dn']}일 연속 하락 · {fmt_pct(chg)}"
    else:
        head = {"상승": pick(["추세 위 · 단기 탄력 확인", "평균선 위 흐름 유지"], 4),
                "반등": pick(["단기 반등 · 60일선이 관건", "20일선 회복 · 다음은 60일선"], 4),
                "조정": pick(["단기 조정 · 지지 확인 구간", "20일선 이탈 · 숨 고르기"], 4),
                "하락": pick(["평균선 아래 · 반등 신호 대기", "약세 지속 · 거래량 변화 체크"], 4),
                "중립": "방향 탐색 · 박스권"}[trend]

    watch = []
    if s.get("hi20") and s.get("lo20"):
        watch.append(f"20일 고점 {fmt_price(s['hi20'])}원 돌파")
        watch.append(f"20일 저점 {fmt_price(s['lo20'])}원 지지")
    if chg >= 10:
        watch.append("다음 날 시가 갭과 거래량 유지 여부")
    if ma60 and c < ma60:
        watch.append(f"60일선 {fmt_price(ma60)}원 회복")
    return {"head": head, "points": pts, "watch": watch[:3]}


# ════════════════════════ 3. 차트 (서버에서 SVG로 그림) ════════════════════════

def chart_svg(df, W=760, H=320):
    n = len(df)
    pad_l, pad_r, pad_t = 8, 64, 12
    ph, vh, gap = 210, 56, 22
    cw = (W - pad_l - pad_r) / n
    hi, lo = float(df.High.max()), float(df.Low.min())
    rng = (hi - lo) or 1
    vmax = float(df.Volume.max()) or 1
    Y = lambda v: pad_t + (hi - v) / rng * ph
    vy0 = pad_t + ph + gap
    parts = []
    # 가로 눈금
    for k in range(5):
        v = lo + rng * k / 4
        yy = Y(v)
        parts.append(f'<line x1="{pad_l}" x2="{W-pad_r}" y1="{yy:.1f}" y2="{yy:.1f}" class="g"/>'
                     f'<text x="{W-pad_r+6}" y="{yy+4:.1f}" class="t">{fmt_price(v)}</text>')
    for i, (d, r) in enumerate(df.iterrows()):
        x = pad_l + i * cw + cw / 2
        up = r.Close >= r.Open
        cls = "u" if up else "d"
        top, bot = Y(max(r.Open, r.Close)), Y(min(r.Open, r.Close))
        bw = max(1.0, cw * 0.62)
        parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{Y(r.High):.1f}" y2="{Y(r.Low):.1f}" class="{cls}"/>'
                     f'<rect x="{x-bw/2:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{max(1, bot-top):.1f}" class="{cls}"/>')
        vhgt = r.Volume / vmax * vh
        parts.append(f'<rect x="{x-bw/2:.1f}" y="{vy0+vh-vhgt:.1f}" width="{bw:.1f}" height="{max(0.5, vhgt):.1f}" class="v{cls}"/>')
    for i in (0, n // 2, n - 1):
        x = pad_l + i * cw + cw / 2
        anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
        parts.append(f'<text x="{x:.1f}" y="{H-4}" class="t" text-anchor="{anchor}">{df.index[i].strftime("%y.%m.%d")}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="최근 {n}거래일 일봉 차트와 거래량">'
            + "".join(parts) + "</svg>")


# ════════════════════════ 4. 문장 자동 생성 ════════════════════════

def fmt_price(v):
    return f"{v:,.0f}"


def fmt_won(v):
    if v is None:
        return "-"
    if v >= 1e12:
        jo, eok = divmod(round(v / 1e8), 10000)
        return f"{jo:,}조 {eok:,}억원" if eok else f"{jo:,}조원"
    if v >= 1e8:
        return f"{v/1e8:,.0f}억원"
    return f"{v/1e4:,.0f}만원"


def fmt_pct(v, sign=True):
    if v is None:
        return "-"
    return f"{v:+.2f}%" if sign else f"{v:.2f}%"


def josa(word, a="은", b="는"):
    ch = word.strip()[-1:] if word else ""
    if "가" <= ch <= "힣":
        return word + (a if (ord(ch) - 0xAC00) % 28 else b)
    return f"{word}{a}({b})"


def summary(s):
    nm, out = s["name"], []
    nm_t = josa(nm)
    if s["chg"] is None or abs(s["chg"]) < 0.005:
        out.append(f"{nm_t} {s['date_kr']} {fmt_price(s['close'])}원에 거래를 마치며 전일과 같은 가격을 유지했습니다.")
    else:
        word = "상승" if s["chg"] > 0 else "하락"
        out.append(f"{nm_t} {s['date_kr']} 전일보다 {fmt_price(abs(s['change']))}원({abs(s['chg']):.2f}%) {word}한 "
                   f"{fmt_price(s['close'])}원에 거래를 마쳤습니다.")
    if s["marcap"]:
        out.append(f"종가 기준 시가총액은 {fmt_won(s['marcap'])}입니다.")
    if s["vol_ratio"]:
        vr = s["vol_ratio"]
        if vr >= 2:
            out.append(f"거래량은 {s['volume']:,}주로 최근 20거래일 평균의 {vr:.1f}배까지 늘었습니다.")
        elif vr <= 0.6:
            out.append(f"거래량은 {s['volume']:,}주로 최근 20거래일 평균보다 적었습니다.")
        else:
            out.append(f"거래량은 {s['volume']:,}주로 최근 20거래일 평균과 비슷한 수준입니다.")
    if s["r1m"] is not None:
        r = s["r1m"]
        out.append(f"최근 1개월 동안 주가는 {abs(r):.1f}% {'올랐' if r >= 0 else '내렸'}고, "
                   + (f"3개월 기준으로는 {fmt_pct(s['r3m'])}입니다." if s["r3m"] is not None else "") )
    if s["from_hi"] is not None and s["from_lo"] is not None:
        if s["from_hi"] > -1:
            out.append(f"현재 주가는 52주 최고가({fmt_price(s['hi52'])}원) 부근에 있습니다.")
        else:
            out.append(f"현재 주가는 52주 최고가 {fmt_price(s['hi52'])}원보다 {abs(s['from_hi']):.1f}% 낮고, "
                       f"52주 최저가 {fmt_price(s['lo52'])}원보다는 {s['from_lo']:.1f}% 높은 위치입니다.")
    return out


def faqs(s):
    nm = s["name"]
    q = [
        (f"{nm} 오늘 주가는 얼마인가요?",
         f"{s['date_kr']} 종가는 {fmt_price(s['close'])}원으로, 전일 대비 {fmt_pct(s['chg'])} 움직였습니다."),
        (f"{nm} 종목코드는 무엇인가요?",
         f"{nm}의 종목코드는 {s['code']}이며 {s['market']}에 상장되어 있습니다."),
        (f"{nm} 52주 최고가와 최저가는?",
         f"최근 52주 최고가는 {fmt_price(s['hi52'])}원, 최저가는 {fmt_price(s['lo52'])}원입니다."),
        (f"{nm} 시가총액은 얼마인가요?",
         f"{s['date_kr']} 종가 기준 시가총액은 {fmt_won(s['marcap'])}입니다." if s["marcap"] else "시가총액 정보가 없습니다."),
    ]
    ai = s.get("ai") or {}
    if ai.get("p") is not None:
        q.insert(1, (f"{nm} 주가 예측은 어떻게 나오나요?",
                     f"{CFG['site_name']} AI 모델이 계산한 {nm}의 5거래일 뒤 상승 확률은 {ai['p']*100:.0f}%({ai['signal']})이며, "
                     f"최근 변동성 기준 예상 가격 범위는 {fmt_price(ai['lo'])}원~{fmt_price(ai['hi'])}원입니다. "
                     f"과거 데이터로 계산한 통계적 추정이며 실제 주가와 다를 수 있습니다. AI 예측은 전 종목 무료로 제공됩니다."))
    return q


# ════════════════════════ 5. 페이지 생성 ════════════════════════

def safe_round(v):
    try:
        return round(float(v))
    except (TypeError, ValueError, OverflowError):
        return None


def slug(text):
    return re.sub(r"[^0-9A-Za-z가-힣]+", "-", text).strip("-")


def build(stocks, base_date):
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets").mkdir(parents=True)
    # 폴더째 올라가지 않고 파일이 맨 바깥에 흩어져 있어도 찾을 수 있게 처리
    static_dir = ROOT / "static" if (ROOT / "static").is_dir() else ROOT
    for name in ("site.css", "site.js"):
        src = static_dir / name
        if not src.exists():
            sys.exit(f"{name} 파일을 찾을 수 없습니다.")
        shutil.copy(src, OUT / "assets" / name)

    tpl_dirs = [d for d in (ROOT / "templates", ROOT) if d.is_dir()]
    env = Environment(loader=FileSystemLoader(tpl_dirs), autoescape=select_autoescape(["html"]))
    env.filters.update(price=fmt_price, won=fmt_won, pct=fmt_pct, slug=slug)
    base = CFG["base_path"].rstrip("/")
    site_url = CFG["site_url"].rstrip("/") + base
    public_cfg = {k: CFG[k] for k in ("site_name", "sheet_url", "kakao_url", "phone", "phone_hours", "biz_info")}
    cp = json.loads(json.dumps(CFG["copy"], ensure_ascii=False)
                    .replace("{site}", CFG["site_name"]).replace("{reply_days}", str(CFG.get("reply_days", "1"))))
    common = {"cfg": CFG, "cp": cp, "th": CFG["theme"], "ft": CFG["features"], "dz": CFG["design"],
              "base": base, "site_url": site_url, "base_date": base_date,
              "public_cfg": json.dumps(public_cfg, ensure_ascii=False),
              "year": datetime.now(KST).year, "ai_report": MODEL_INFO["report"]}

    def write(out_path, tpl, **ctx):
        p = OUT / out_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(env.get_template(tpl).render(**common, **ctx), encoding="utf-8")

    stocks = sorted(stocks, key=lambda s: -(s["marcap"] or 0))
    rank = {s["code"]: i for i, s in enumerate(stocks)}
    by_sector, by_market = {}, {}
    for s in stocks:
        by_market.setdefault(s["market"], []).append(s)
        if s["sector"]:
            by_sector.setdefault(s["sector"], []).append(s)

    urls = [("/", "1.0"), ("/ai/", "0.9"), ("/market/kospi/", "0.8"), ("/market/kosdaq/", "0.8"), ("/privacy/", "0.2")]

    # 종목 페이지 (한 종목에서 문제가 생겨도 나머지는 계속 생성)
    skipped = []
    for s in stocks:
        peers = []
        if s["sector"] and len(by_sector[s["sector"]]) > 1:
            peers = [p for p in by_sector[s["sector"]] if p["code"] != s["code"]][:8]
        if len(peers) < 8:
            mk = by_market[s["market"]]
            i = mk.index(s)
            near = mk[max(0, i - 6): i] + mk[i + 1: i + 7]
            peers += [p for p in near if p not in peers][: 8 - len(peers)]
        try:
            s["comment"] = ai_comment(s) if CFG["features"].get("comment", True) else None
            write(f"stock/{s['code']}/index.html", "stock.html", s=s, peers=peers,
                  summary=summary(s), faqs=faqs(s), rank=rank[s["code"]] + 1, total=len(stocks),
                  sector_slug=slug(s["sector"]) if s["sector"] and len(by_sector.get(s["sector"], [])) >= 3 else None)
            urls.append((f"/stock/{s['code']}/", "0.7"))
        except Exception as e:
            skipped.append(s["code"])
            log("페이지 생성 건너뜀", s["code"], s["name"], repr(e)[:120])

    # 업종 페이지 (3종목 이상)
    sectors = sorted([(k, v) for k, v in by_sector.items() if len(v) >= 3], key=lambda kv: -len(kv[1]))
    for name, lst in sectors:
        write(f"sector/{slug(name)}/index.html", "list.html", title=f"{name} 관련주", heading=f"{name} 업종 종목",
              desc=f"{name} 업종 {len(lst)}개 종목의 {base_date} 주가, 등락률, 시가총액을 한눈에 확인하세요.",
              items=lst, path=f"/sector/{slug(name)}/")
        urls.append((f"/sector/{slug(name)}/", "0.6"))

    # 급등 레이더 페이지 (급등스캐너 고유)
    radar_nav = []
    if RADAR:
        pages = radar_pages(base_date)
        radar_nav = [(k, label) for k, label, *_ in pages]
        for key, label, title, desc, stats, sections in pages:
            write(f"{key}/index.html", "radar.html", key=key, label=label, title=title, desc=desc,
                  stats=[x for x in stats if x], sections=sections, radar_nav=radar_nav)
            urls.insert(1, (f"/{key}/", "0.9"))

    # 시장 목록
    for m in ("KOSPI", "KOSDAQ"):
        lst = by_market.get(m, [])
        name = "코스피" if m == "KOSPI" else "코스닥"
        write(f"market/{m.lower()}/index.html", "list.html", title=f"{name} 전 종목 시세", heading=f"{name} 전 종목",
              desc=f"{name} 상장 {len(lst)}개 종목의 {base_date} 종가, 등락률, 시가총액 순위.",
              items=lst, path=f"/market/{m.lower()}/")

    # 증시 리포트 (매일 쌓이는 글)
    posts = load_posts()
    by_date = {}
    for p in posts:
        by_date.setdefault(p["date"], []).append(p)
    for p in posts:
        full, short = _kdate(p["date"])
        same_day = [x for x in by_date[p["date"]] if x["slug"] != p["slug"]][:12]
        write(f"report/{p['date']}/{p['slug']}/index.html", "post.html", p=p, date_full=full, related=same_day)
        urls.append((f"/report/{p['date']}/{p['slug']}/", "0.6"))
    dates = sorted(by_date, reverse=True)
    for d in dates:
        full, short = _kdate(d)
        write(f"report/{d}/index.html", "report_list.html", title=f"{full} 증시 리포트", path=f"/report/{d}/",
              heading=f"{full} 증시 리포트", groups=[(d, full, by_date[d])], dates=dates[:60])
        urls.append((f"/report/{d}/", "0.5"))
    if dates:
        write("report/index.html", "report_list.html", title="증시 리포트 — 매일 장 마감 후 자동 발행", path="/report/",
              heading="증시 리포트", groups=[(d, _kdate(d)[0], by_date[d]) for d in dates[:3]], dates=dates[:60])
        urls.append(("/report/", "0.8"))
    today_posts = by_date.get(dates[0], []) if dates else []

    # 메인
    def top(key, rev=True, n=10, flt=None):
        pool = [s for s in stocks if s[key] is not None and (flt is None or flt(s))]
        return sorted(pool, key=lambda s: s[key], reverse=rev)[:n]
    liquid = lambda s: s["amount"] >= 1e9
    ai_pool = [s for s in stocks if s["ai"].get("p") is not None and liquid(s)]
    ai_up = sorted(ai_pool, key=lambda s: -s["ai"]["p"])[:10]
    ai_down = sorted(ai_pool, key=lambda s: s["ai"]["p"])[:10]
    # 메인 '오늘의 주요 종목 뉴스': 시총 상위 종목 뉴스를 모아 최신순
    top_news, seen = [], set()
    for st in stocks[:40]:
        for n in st.get("news") or []:
            k = n["title"][:30]
            if k in seen:
                continue
            seen.add(k)
            top_news.append({**n, "code": st["code"], "name": st["name"]})
    top_news = sorted(top_news, key=lambda x: -x.get("ts", 0))[:15]
    write("index.html", "index.html", top_news=top_news,
          gainers=top("chg", flt=liquid), losers=top("chg", rev=False, flt=liquid),
          amount=top("amount"), bigcaps=stocks[:10], sectors=sectors[:24], count=len(stocks),
          ai_up=ai_up, ai_down=ai_down, today_posts=today_posts, radar=RADAR)
    write("ai/index.html", "ai.html", ai_up=ai_up, ai_down=ai_down, count=len(stocks))

    write("privacy/index.html", "privacy.html")
    write("404.html", "404.html")

    # 검색용 목록, 사이트맵, robots
    (OUT / "stocks.json").write_text(json.dumps([[s["code"], s["name"], s["market"], s["close"], round(s["chg"] or 0, 2),
                    round(s["ai"]["p"], 3) if s["ai"].get("p") is not None else None,
                    safe_round(s["ai"]["lo"]), safe_round(s["ai"]["hi"])] for s in stocks], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    sm += [f"<url><loc>{site_url}{u}</loc><lastmod>{base_date}</lastmod><priority>{p}</priority></url>" for u, p in urls]
    sm.append("</urlset>")
    (OUT / "sitemap.xml").write_text("\n".join(sm), encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {site_url}/sitemap.xml\n", encoding="utf-8")
    (OUT / ".nojekyll").write_text("")
    if skipped:
        log(f"건너뛴 종목 {len(skipped)}개: {', '.join(skipped[:20])}")
    log(f"완료: 종목 {len(stocks)}개, 업종 {len(sectors)}개, 리포트 {len(posts)}개(누적), 총 {len(urls)}개 URL → {OUT}")


# ════════════════════════ 급등 레이더 (급등스캐너 고유 기능) ════════════════════════
# 매일 종가 기준으로
#  - /surge/         +15% 이상 급등주(상한가 포함) + 5~15% 강세주
#  - /volume-spike/  거래량이 평소(20일 평균)의 10배 이상 / 5~10배
#  - /streak/        연속 상승 일수 순위 / 연속 하락 일수 순위
# 를 만들고, 메인 첫 화면 '오늘의 급등 TOP 10' 카드와 매일 리포트에도 씁니다.

RADAR = {}


def build_radar(stocks, base_date):
    today = [s for s in stocks if s["date"] == base_date and s["chg"] is not None]
    liquid = [s for s in today if s["amount"] >= 5e8]
    by_chg = sorted(liquid, key=lambda s: -s["chg"])
    surge = [s for s in by_chg if s["chg"] >= 15]
    strong = [s for s in by_chg if 5 <= s["chg"] < 15][:40]
    spike_pool = sorted([s for s in today if s["amount"] >= 1e8 and s["vol_ratio"]], key=lambda s: -s["vol_ratio"])
    spike10 = [s for s in spike_pool if s["vol_ratio"] >= 10]
    spike5 = [s for s in spike_pool if 5 <= s["vol_ratio"] < 10][:40]
    streak = sorted([s for s in liquid if s["streak_up"] >= 3], key=lambda s: (-s["streak_up"], -(s["streak_gain"] or 0)))[:50]
    streak_dn = sorted([s for s in liquid if s["streak_dn"] >= 3], key=lambda s: (-s["streak_dn"], s["chg"]))[:30]
    rank = {s["code"]: i + 1 for i, s in enumerate(by_chg)}
    for s in stocks:
        s["chg_rank"] = rank.get(s["code"])
    return {"top10": by_chg[:10], "surge": surge, "strong": strong, "limit_up": [s for s in surge if s["chg"] >= 29.5],
            "spike10": spike10, "spike5": spike5, "streak": streak, "streak_dn": streak_dn,
            "n_liquid": len(liquid), "n_up": sum(s["chg"] > 0 for s in today), "n_down": sum(s["chg"] < 0 for s in today)}


def _rows(xs, fn):
    return [{"s": s, "extra": fn(s)} for s in xs]


def radar_pages(base_date):
    R = RADAR
    _, short = _kdate(base_date)
    vr = lambda s: f"{s['vol_ratio']:.1f}배" if s["vol_ratio"] else "-"
    pages = [
        ("surge", "급등주", f"오늘의 급등주 — +15% 이상 {len(R['surge'])}개 ({short})",
         f"{short} 코스피·코스닥 +15% 이상 급등주 {len(R['surge'])}개" + (f", 상한가 {len(R['limit_up'])}개" if R["limit_up"] else "") + ". 거래대금 5억원 이상 종목만. 장 마감 후 자동 갱신.",
         [f"+15% 이상 {len(R['surge'])}개" + (f" · 상한가 {len(R['limit_up'])}개" if R["limit_up"] else ""),
          f"+5% 이상 {len(R['surge']) + len(R['strong'])}개",
          f"상승 {R['n_up']:,} / 하락 {R['n_down']:,}"],
         [("+15% 이상 급등주", "거래량", _rows(R["surge"], vr), "오늘 +15% 이상 오른 종목이 없습니다."),
          ("+5% ~ +15% 강세주", "거래량", _rows(R["strong"], vr), "해당 종목이 없습니다.")]),
        ("volume-spike", "거래량 폭발", f"거래량 폭발 종목 — 평소의 10배 이상 {len(R['spike10'])}개 ({short})",
         f"{short} 거래량이 최근 20일 평균의 10배 이상 터진 종목 {len(R['spike10'])}개와 5~10배 종목. 거래대금 1억원 이상. 장 마감 후 자동 갱신.",
         [f"10배 이상 {len(R['spike10'])}개", f"5~10배 {len(R['spike5'])}개",
          (f"1위 {R['spike10'][0]['name']} {R['spike10'][0]['vol_ratio']:.0f}배" if R["spike10"] else "")],
         [("거래량 10배 이상", "평소 대비", _rows(R["spike10"], vr), "오늘 10배 이상 터진 종목이 없습니다."),
          ("거래량 5~10배", "평소 대비", _rows(R["spike5"], vr), "해당 종목이 없습니다.")]),
        ("streak", "연속 상승", f"연속 상승 일수 순위 — {R['streak'][0]['streak_up'] if R['streak'] else 0}일 연속 상승 1위 ({short})",
         f"{short} 기준 3일 이상 연속으로 오른 종목 {len(R['streak'])}개를 연속 일수 순으로 정리. 연속 하락 순위도 함께.",
         [f"3일+ 연속 상승 {len(R['streak'])}개",
          (f"최장 {R['streak'][0]['streak_up']}일 · {R['streak'][0]['name']}" if R["streak"] else ""),
          f"3일+ 연속 하락 {len(R['streak_dn'])}개"],
         [("연속 상승 순위", "연속 · 누적", _rows(R["streak"], lambda s: f"{s['streak_up']}일 · {fmt_pct(s['streak_gain'])}"), "3일 이상 연속 상승한 종목이 없습니다."),
          ("연속 하락 순위", "연속", _rows(R["streak_dn"], lambda s: f"{s['streak_dn']}일"), "3일 이상 연속 하락한 종목이 없습니다.")]),
    ]
    return pages


# ════════════════════════ 7. 매일 자동 리포트 글 ════════════════════════
# 거래일마다 실제 데이터로 리포트 글을 만들어 posts/날짜/글.json 으로 저장하고,
# 저장된 글은 지우지 않고 쌓아서 /report/ 아래 페이지로 계속 보여줍니다.

POSTS_DIR = ROOT / "posts"
MAX_SECTOR_POSTS = 60


def _row(s, extra=""):
    return {"code": s["code"], "name": s["name"], "market": s["market"],
            "close": s["close"], "chg": round(s["chg"] or 0, 2), "extra": extra}


def _kdate(d):
    y, m, dd = d.split("-")
    return f"{int(y)}년 {int(m)}월 {int(dd)}일", f"{int(m)}월 {int(dd)}일"


def make_posts(stocks, base_date):
    today = [s for s in stocks if s["date"] == base_date and s["chg"] is not None]
    if len(today) < 20:
        return []
    full, short = _kdate(base_date)
    liquid = [s for s in today if s["amount"] >= 5e8]
    posts = []

    def add(slug, kind, title, desc, intro, tables, tags):
        posts.append({"date": base_date, "slug": slug, "kind": kind, "title": title, "desc": desc,
                      "intro": [x for x in intro if x], "tables": tables, "tags": tags})

    def name_list(xs, k=3):
        return ", ".join(f"{x['name']}({fmt_pct(x['chg'])})" for x in xs[:k])

    # 1) 시장 마감 요약
    mk = {}
    for m in ("KOSPI", "KOSDAQ"):
        xs = [s for s in today if s["market"] == m]
        if not xs:
            continue
        chg = sorted(s["chg"] for s in xs)
        mk[m] = {"n": len(xs), "up": sum(c > 0 for c in chg), "down": sum(c < 0 for c in chg),
                 "med": chg[len(chg) // 2], "amt": sum(s["amount"] for s in xs)}
    gain = sorted(liquid, key=lambda s: -s["chg"])
    lose = sorted(liquid, key=lambda s: s["chg"])
    amt = sorted(today, key=lambda s: -s["amount"])
    intro = []
    for m, nm in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
        if m in mk:
            v = mk[m]
            mood = "오른 종목이 더 많았습니다" if v["up"] > v["down"] else "내린 종목이 더 많았습니다" if v["down"] > v["up"] else "오른 종목과 내린 종목 수가 비슷했습니다"
            intro.append(f"{short} {nm}에서는 {v['n']:,}개 종목 중 {v['up']:,}개가 오르고 {v['down']:,}개가 내려 {mood}. "
                         f"종목 등락률 중앙값은 {fmt_pct(v['med'])}, 거래대금 합계는 {fmt_won(v['amt'])}입니다.")
    intro.append(f"거래대금 10억원 이상 종목 가운데 가장 많이 오른 종목은 {name_list(gain)}이고, 가장 많이 내린 종목은 {name_list(lose)}입니다.")
    intro.append(f"거래대금은 {amt[0]['name']}({fmt_won(amt[0]['amount'])})에 가장 많이 몰렸습니다.")
    add("market-summary", "summary", f"{short} 마감 스캔 | 상승·하락 종목 수, 급등·급락·거래대금 TOP",
        f"{short} 코스피·코스닥 마감 결과. 상승·하락 종목 수, 상승률·하락률 상위, 거래대금 상위 종목을 정리했습니다.",
        intro,
        [{"title": "상승률 상위 10", "extra": "거래대금", "rows": [_row(s, fmt_won(s["amount"])) for s in gain[:10]]},
         {"title": "하락률 상위 10", "extra": "거래대금", "rows": [_row(s, fmt_won(s["amount"])) for s in lose[:10]]},
         {"title": "거래대금 상위 10", "extra": "거래대금", "rows": [_row(s, fmt_won(s["amount"])) for s in amt[:10]]}],
        ["증시 마감", "코스피", "코스닥"])

    # 1-1) 급등 레이더 리포트 (급등스캐너 고유)
    R = RADAR
    if R.get("surge"):
        sg = R["surge"]
        add("surge-15", "radar", f"{short} +15% 급등주 {len(sg)}개" + (f" · 상한가 {len(R['limit_up'])}개" if R["limit_up"] else "") + f" | 1위 {sg[0]['name']} {fmt_pct(sg[0]['chg'])}",
            f"{full} +15% 이상 급등주 {len(sg)}개. 1위 {sg[0]['name']} {fmt_pct(sg[0]['chg'])}, 거래대금 5억원 이상 기준.",
            [f"+15% 이상: {len(sg)}개" + (f" (상한가 {len(R['limit_up'])}개)" if R["limit_up"] else "") + ".",
             f"1위 {sg[0]['name']} {fmt_pct(sg[0]['chg'])} · 거래량 {sg[0]['vol_ratio'] or 0:.1f}배 · 거래대금 {fmt_won(sg[0]['amount'])}.",
             f"급등주 평균 거래량은 평소의 {sum((x['vol_ratio'] or 0) for x in sg) / len(sg):.1f}배."],
            [{"title": "+15% 이상 급등주", "extra": "거래량", "rows": [_row(x, f"{x['vol_ratio'] or 0:.1f}배") for x in sg]}],
            ["급등주", "상한가", "단타"])
    if len(R.get("spike10", [])) >= 3:
        sp = R["spike10"]
        add("volume-10x", "radar", f"{short} 거래량 10배 폭발 {len(sp)}개 | 1위 {sp[0]['name']} {sp[0]['vol_ratio']:.0f}배",
            f"{full} 거래량이 평소(20일 평균)의 10배 이상 터진 종목 {len(sp)}개. 1위 {sp[0]['name']} {sp[0]['vol_ratio']:.0f}배, 주가 {fmt_pct(sp[0]['chg'])}.",
            [f"10배 이상: {len(sp)}개.",
             f"1위 {sp[0]['name']} {sp[0]['vol_ratio']:.0f}배 · 주가 {fmt_pct(sp[0]['chg'])}.",
             f"이 중 상승 {sum(x['chg'] > 0 for x in sp)}개 · 하락 {sum(x['chg'] < 0 for x in sp)}개."],
            [{"title": "거래량 10배 이상", "extra": "평소 대비", "rows": [_row(x, f"{x['vol_ratio']:.1f}배") for x in sp[:40]]}],
            ["거래량 폭발", "거래량 급증", "단타"])
    if len(R.get("streak", [])) >= 5:
        st = R["streak"]
        add("streak-up", "radar", f"{short} 연속 상승 순위 | {st[0]['name']} {st[0]['streak_up']}일 연속",
            f"{full} 3일 이상 연속 상승 종목 {len(st)}개. 최장 {st[0]['name']} {st[0]['streak_up']}일 연속, 누적 {fmt_pct(st[0]['streak_gain'])}.",
            [f"3일 이상 연속 상승: {len(st)}개.",
             f"최장 {st[0]['name']} {st[0]['streak_up']}일 · 누적 {fmt_pct(st[0]['streak_gain'])}.",
             f"5일 이상: {sum(x['streak_up'] >= 5 for x in st)}개."],
            [{"title": "연속 상승 순위", "extra": "연속 · 누적", "rows": [_row(x, f"{x['streak_up']}일 · {fmt_pct(x['streak_gain'])}") for x in st[:30]]}],
            ["연속 상승", "추세주"])

    # 2) 상승률·하락률 TOP 30
    if gain:
        add("top-gainers", "rank", f"{short} 상승률 TOP 30 | 1위 {gain[0]['name']} {fmt_pct(gain[0]['chg'])}",
            f"{short} 코스피·코스닥 상승률 상위 30개 종목. 1위 {gain[0]['name']} {fmt_pct(gain[0]['chg'])}.",
            [f"{short} 거래대금 5억원 이상 종목 중 상승률 1위는 {gain[0]['name']}로 {fmt_pct(gain[0]['chg'])} 올랐습니다.",
             f"상위 30개 종목의 평균 상승률은 {fmt_pct(sum(s['chg'] for s in gain[:30]) / len(gain[:30]))}입니다."],
            [{"title": "상승률 TOP 30", "extra": "거래대금", "rows": [_row(s, fmt_won(s["amount"])) for s in gain[:30]]}],
            ["상승률 순위", "급등주"])
        add("top-losers", "rank", f"{short} 하락률 TOP 30 | 1위 {lose[0]['name']} {fmt_pct(lose[0]['chg'])}",
            f"{short} 코스피·코스닥 하락률 상위 30개 종목. 1위 {lose[0]['name']} {fmt_pct(lose[0]['chg'])}.",
            [f"{short} 거래대금 5억원 이상 종목 중 가장 많이 내린 종목은 {lose[0]['name']}({fmt_pct(lose[0]['chg'])})입니다."],
            [{"title": "하락률 TOP 30", "extra": "거래대금", "rows": [_row(s, fmt_won(s["amount"])) for s in lose[:30]]}],
            ["하락률 순위", "급락주"])

    # 3) 거래량 급증
    surge = sorted([s for s in liquid if (s["vol_ratio"] or 0) >= 2], key=lambda s: -s["vol_ratio"])[:30]
    if len(surge) >= 5:
        add("volume-surge", "rank", f"{full} 거래량 급증 종목 — 평소보다 거래가 몰린 주식 {len(surge)}개",
            f"{short} 최근 20일 평균보다 거래량이 2배 이상 늘어난 종목. 1위 {surge[0]['name']} {surge[0]['vol_ratio']:.1f}배.",
            [f"{short} 거래량이 최근 20거래일 평균의 2배를 넘은 종목은 거래대금 5억원 이상 기준 {len(surge)}개입니다.",
             f"가장 크게 늘어난 종목은 {surge[0]['name']}로 평소의 {surge[0]['vol_ratio']:.1f}배가 거래됐고, 주가는 {fmt_pct(surge[0]['chg'])} 움직였습니다."],
            [{"title": "거래량 급증 순위", "extra": "평소 대비", "rows": [_row(s, f"{s['vol_ratio']:.1f}배") for s in surge]}],
            ["거래량 급증", "수급"])

    # 4) 52주 신고가·신저가 근접
    hi = sorted([s for s in liquid if s["from_hi"] is not None and s["from_hi"] > -1], key=lambda s: -s["chg"])[:30]
    if len(hi) >= 3:
        add("new-high", "rank", f"{full} 52주 신고가 근접 종목 {len(hi)}개",
            f"{short} 52주 최고가 부근(1% 이내)에서 거래를 마친 종목. {name_list(hi, 2)} 등.",
            [f"{short} 종가가 52주 최고가의 1% 이내에 있는 종목은 {len(hi)}개입니다.",
             f"이 중 당일 상승률이 높은 종목은 {name_list(hi)}입니다."],
            [{"title": "52주 신고가 근접", "extra": "52주 최고가", "rows": [_row(s, fmt_price(s["hi52"])) for s in hi]}],
            ["52주 신고가", "신고가"])
    lo = sorted([s for s in liquid if s["from_lo"] is not None and s["from_lo"] < 1], key=lambda s: s["chg"])[:30]
    if len(lo) >= 3:
        add("new-low", "rank", f"{full} 52주 신저가 근접 종목 {len(lo)}개",
            f"{short} 52주 최저가 부근(1% 이내)에서 거래를 마친 종목. {name_list(lo, 2)} 등.",
            [f"{short} 종가가 52주 최저가의 1% 이내에 있는 종목은 {len(lo)}개입니다."],
            [{"title": "52주 신저가 근접", "extra": "52주 최저가", "rows": [_row(s, fmt_price(s["lo52"])) for s in lo]}],
            ["52주 신저가", "신저가"])

    # 5) AI 상승 확률 상위
    aip = sorted([s for s in liquid if s["ai"].get("p") is not None], key=lambda s: -s["ai"]["p"])[:20]
    if len(aip) >= 5:
        add("ai-top", "ai", f"{full} AI 주가예측 — 5거래일 상승 확률 상위 20 종목",
            f"{short} 기준 AI 모델이 계산한 5거래일 뒤 상승 확률 상위 종목. 1위 {aip[0]['name']} {aip[0]['ai']['p']*100:.0f}%.",
            [f"{CFG['site_name']} AI 모델이 {short} 종가까지의 데이터로 계산한 5거래일 뒤 상승 확률 상위 종목입니다.",
             f"1위는 {aip[0]['name']}({aip[0]['ai']['p']*100:.0f}%)입니다. AI 예측은 통계적 추정이며 매수 추천이 아닙니다."],
            [{"title": "AI 상승 확률 상위 20", "extra": "상승 확률", "rows": [_row(s, f"{s['ai']['p']*100:.0f}%") for s in aip]}],
            ["AI 주가예측", "주가 예측"])

    # 6) 업종별
    by = {}
    for s in today:
        if s["sector"]:
            by.setdefault(s["sector"], []).append(s)
    secs = sorted([(k, v) for k, v in by.items() if len(v) >= 5], key=lambda kv: -sum(x["amount"] for x in kv[1]))[:MAX_SECTOR_POSTS]
    for sec, xs in secs:
        xs = sorted(xs, key=lambda s: -s["chg"])
        avg = sum(s["chg"] for s in xs) / len(xs)
        up, down = sum(s["chg"] > 0 for s in xs), sum(s["chg"] < 0 for s in xs)
        lead = max(xs, key=lambda s: s["amount"])
        tone = ("강세" if avg >= 1 else "약보합" if avg < 0 and avg > -1 else "약세" if avg <= -1 else "강보합")
        add(f"sector-{slug(sec)}", "sector", f"{full} {sec} 관련주 주가 — 업종 {tone}, 평균 {fmt_pct(avg)}",
            f"{short} {sec} 업종 {len(xs)}개 종목 등락률. 상승 {up}개·하락 {down}개, 최고 상승 {xs[0]['name']} {fmt_pct(xs[0]['chg'])}.",
            [f"{short} {sec} 업종 {len(xs)}개 종목의 평균 등락률은 {fmt_pct(avg)}로 {tone}를 보였습니다. 상승 {up}개, 하락 {down}개입니다.",
             f"가장 많이 오른 종목은 {name_list(xs, 3)}이고, 가장 많이 내린 종목은 {xs[-1]['name']}({fmt_pct(xs[-1]['chg'])})입니다.",
             f"업종 내 거래대금이 가장 많은 종목은 {lead['name']}({fmt_won(lead['amount'])}, {fmt_pct(lead['chg'])})입니다."],
            [{"title": f"{sec} 관련주 등락률", "extra": "시가총액", "rows": [_row(s, fmt_won(s["marcap"])) for s in xs[:40]]}],
            [f"{sec} 관련주", sec])
    return posts


def save_posts(posts):
    for p in posts:
        d = POSTS_DIR / p["date"]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{p['slug']}.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")


def load_posts():
    out = []
    if POSTS_DIR.is_dir():
        for f in sorted(POSTS_DIR.glob("*/*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception as e:
                log("리포트 읽기 실패", f, e)
    order = {"summary": 0, "radar": 1, "rank": 2, "ai": 3, "sector": 4}
    out.sort(key=lambda p: (p["date"], -order.get(p["kind"], 9)), reverse=True)
    return out


# ════════════════════════ 6. AI 주가예측 모델 ════════════════════════
# 전 종목의 일봉에서 기술적 특징을 뽑아, "5거래일 뒤 종가가 오늘보다 높을까?"를
# 맞히도록 로지스틱 회귀(머신러닝) 모델을 매일 새로 학습합니다.
# 최근 60거래일은 학습에서 빼고 검증용으로 써서, 실제 정확도를 그대로 공개합니다.

HORIZON = 5
TEST_DAYS = 60
FEATS = ["r1", "r5", "r20", "r60", "vol20", "vr", "hi_dist", "lo_dist", "rsi", "gap20", "gap60", "range5"]
MODEL_INFO = {"report": None}


def features(df):
    c, v = df["Close"].astype(float), df["Volume"].astype(float)
    ret = c.pct_change()
    f = pd.DataFrame(index=df.index)
    for n in (1, 5, 20, 60):
        f[f"r{n}"] = c.pct_change(n)
    f["vol20"] = ret.rolling(20).std()
    f["vr"] = (v / v.rolling(20).mean().shift(1)).clip(0, 20).apply(lambda x: math.log1p(x) if pd.notna(x) else x)
    f["hi_dist"] = c / df["High"].rolling(250, min_periods=60).max() - 1
    f["lo_dist"] = c / df["Low"].rolling(250, min_periods=60).min() - 1
    gain, loss = ret.clip(lower=0).rolling(14).mean(), (-ret.clip(upper=0)).rolling(14).mean()
    f["rsi"] = (gain / (gain + loss)).fillna(0.5) - 0.5
    f["gap20"] = c / c.rolling(20).mean() - 1
    f["gap60"] = c / c.rolling(60).mean() - 1
    f["range5"] = (df["High"].rolling(5).max() / df["Low"].rolling(5).min() - 1)
    f["y"] = (c.shift(-HORIZON) > c).astype(float).where(c.shift(-HORIZON).notna())
    return f.replace([float("inf"), float("-inf")], pd.NA)


def train_model(dfs):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score

    frames = [features(d) for d in dfs if len(d) >= 80]
    data = pd.concat(frames).dropna(subset=FEATS + ["y"])
    dates = sorted(data.index.unique())
    if len(dates) < TEST_DAYS + 40 or len(data) < 2000:
        log("학습 데이터 부족 — AI 예측 생략")
        return None, None
    cut = dates[-TEST_DAYS]
    train, test = data[data.index < cut], data[data.index >= cut]
    if len(train) > 600_000:
        train = train.sample(600_000, random_state=0)
    clip = lambda X: X.astype(float).clip(-5, 5)
    model = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=300))
    model.fit(clip(train[FEATS]), train["y"])

    p = model.predict_proba(clip(test[FEATS]))[:, 1]
    y = test["y"].values
    acc = float(((p >= 0.5) == (y == 1)).mean())
    base = float(max(y.mean(), 1 - y.mean()))
    strong = (p >= 0.6) | (p <= 0.4)
    acc_strong = float(((p[strong] >= 0.5) == (y[strong] == 1)).mean()) if strong.sum() > 100 else None
    try:
        auc = float(roc_auc_score(y, p))
    except Exception:
        auc = None
    top = p >= np.quantile(p, 0.9)
    report = {
        "train_rows": int(len(train)), "test_rows": int(len(test)),
        "test_from": pd.Timestamp(cut).strftime("%Y-%m-%d"), "test_to": pd.Timestamp(dates[-1]).strftime("%Y-%m-%d"),
        "acc": acc, "base": base, "acc_strong": acc_strong, "strong_share": float(strong.mean()), "auc": auc,
        "top_up_rate": float(y[top].mean()), "all_up_rate": float(y.mean()),
    }
    log(f"AI 검증: 정확도 {acc:.1%} (기준 {base:.1%}), AUC {auc}, 상위10% 실제상승률 {report['top_up_rate']:.1%} vs 전체 {report['all_up_rate']:.1%}")

    # 검증까지 끝난 뒤, 최신 데이터까지 포함해 다시 학습해서 오늘 예측에 사용
    full = data if len(data) <= 600_000 else data.sample(600_000, random_state=0)
    model.fit(clip(full[FEATS]), full["y"])
    return model, report


def predict(model, df):
    c = float(df["Close"].iloc[-1])
    sd = df["Close"].pct_change().tail(20).std()
    sd = 0.0 if sd is None or not math.isfinite(float(sd)) else float(sd)
    band = sd * math.sqrt(HORIZON)
    out = {"lo": c * (1 - band), "hi": c * (1 + band), "band": band * 100, "p": None, "signal": None}
    if model is None or len(df) < 80:
        return out
    f = features(df).iloc[[-1]][FEATS]
    if f.isna().any(axis=None):
        return out
    p = float(model.predict_proba(f.astype(float).clip(-5, 5))[:, 1][0])
    out["p"] = p
    out["signal"] = "상승 우위" if p >= 0.55 else "하락 우위" if p <= 0.45 else "중립"
    out["cls"] = "up" if p >= 0.55 else "down" if p <= 0.45 else "flat"
    return out


# ════════════════════════ 실행 ════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.demo:
        lst, hist = demo_data()
        get = lambda code: hist.get(code)
    else:
        lst = get_listing()
        lst = lst[lst["market"].isin(CFG["markets"])]
        if CFG.get("exclude_name_regex"):
            lst = lst[~lst["name"].str.contains(CFG["exclude_name_regex"], regex=True, na=False)]
        if CFG.get("min_marcap"):
            lst = lst[lst["marcap"].fillna(0) >= CFG["min_marcap"]]
        # 업종: 네이버 업종 우선, 실패하면 KRX 업종 중 '소속부' 같은 값은 버림
        sec = get_sectors()
        old_sec = lst["sector"] if "sector" in lst.columns else pd.Series([None] * len(lst), index=lst.index)
        bad = old_sec.astype(str).str.contains(r"부$|소속부|관리종목|투자주의|nan", regex=True)
        lst["sector"] = [sec.get(c) or (o if not b else None) for c, o, b in zip(lst["code"], old_sec, bad)]
        lst = lst.sort_values("marcap", ascending=False)
        if args.limit:
            lst = lst.head(args.limit)
        start = (datetime.now(KST) - timedelta(days=CFG["history_days"])).strftime("%Y-%m-%d")
        get = lambda code: get_history(code, start)

    rows = lst.to_dict("records")
    log(f"{len(rows)}개 종목 시세 수집 시작")
    pairs, fail = [], 0
    with ThreadPoolExecutor(CFG.get("workers", 8)) as ex:
        futs = {ex.submit(get, r["code"]): r for r in rows}
        for i, f in enumerate(as_completed(futs), 1):
            r = futs[f]
            try:
                df = f.result()
                if df is not None and len(df) >= 2:
                    pairs.append((r, df))
                else:
                    fail += 1
            except Exception as e:
                fail += 1
                log("오류", r["code"], r["name"], e)
            if i % 300 == 0:
                log(f"  {i}/{len(rows)} (실패 {fail})")
    if not pairs:
        sys.exit("시세를 하나도 받지 못했습니다.")
    if fail > len(rows) * 0.3:
        sys.exit(f"실패가 너무 많아 배포를 중단합니다 ({fail}/{len(rows)}). 기존 사이트는 그대로 유지됩니다.")

    # AI 모델 학습 → 종목별 예측
    model, MODEL_INFO["report"] = train_model([df for _, df in pairs])
    stocks = []
    for r, df in pairs:
        try:
            s = compute(r, df)
            s["ai"] = predict(model, df)
            stocks.append(s)
        except Exception as e:
            log("계산 오류", r["code"], r["name"], e)

    # 기준일: 가장 많은 종목이 가진 마지막 날짜
    base_date = pd.Series([s["date"] for s in stocks]).mode()[0]
    stale = [s for s in stocks if s["date"] < base_date]
    if stale:
        log(f"거래정지 등으로 기준일과 다른 종목 {len(stale)}개 (그대로 표시)")
    try:
        if not args.demo and CFG["features"].get("news", True):
            get_news(stocks)
        elif os.environ.get("DEMO_NEWS"):
            for i, st in enumerate(sorted(stocks, key=lambda x: -(x["marcap"] or 0))[:6]):
                st["news"] = [{"title": f"{st['name']} 관련 샘플 기사 제목입니다 {j}", "source": "샘플일보", "link": "https://example.com/", "time": f"10.26 1{j}:0{i}", "ts": 100 * j + i} for j in range(3)]
    except Exception as e:
        log("뉴스 수집 실패 (사이트는 계속 생성):", repr(e)[:200])
    try:
        RADAR.update(build_radar(stocks, base_date))
    except Exception as e:
        log("급등 레이더 계산 실패 (사이트는 계속 생성):", repr(e)[:200])
    try:
        new_posts = make_posts(stocks, base_date) if CFG["features"].get("reports", True) else []
        save_posts(new_posts)
        log(f"오늘의 리포트 {len(new_posts)}개 생성 ({base_date})")
    except Exception as e:
        log("리포트 생성 실패 (사이트는 계속 생성):", repr(e)[:200])
    build(stocks, base_date)


if __name__ == "__main__":
    main()
