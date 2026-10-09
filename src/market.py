"""抓股價並計算技術指標。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def fetch_history(code: str, period: str = "9mo") -> tuple[pd.DataFrame | None, str]:
    """回傳 (日K資料, yahoo代號)。上市用 .TW，上櫃用 .TWO，自動判斷。"""
    import yfinance as yf

    for suffix in (".TW", ".TWO"):
        sym = f"{code}{suffix}"
        try:
            df = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=False)
        except Exception:
            df = None
        if df is not None and len(df) > 30:
            df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
            df.index = pd.to_datetime(df.index).tz_localize(None)
            return df, sym
    return None, ""


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c = d["close"]
    for n in (5, 20, 60):
        d[f"ma{n}"] = c.rolling(n).mean()
    # KD (9,3,3) 台灣常用算法
    low9, high9 = d["low"].rolling(9).min(), d["high"].rolling(9).max()
    rsv = ((c - low9) / (high9 - low9).replace(0, np.nan) * 100).fillna(50)
    k, dd, ks, ds = [], [], 50.0, 50.0
    for v in rsv:
        ks = ks * 2 / 3 + v / 3
        ds = ds * 2 / 3 + ks / 3
        k.append(ks)
        dd.append(ds)
    d["k"], d["d"] = k, dd
    # RSI 14
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    # MACD (12,26,9)
    dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    d["macd_hist"] = dif - dif.ewm(span=9, adjust=False).mean()
    d["vol_ratio"] = d["volume"] / d["volume"].rolling(5).mean().shift(1)
    return d


def tech_summary(df: pd.DataFrame) -> dict:
    d = indicators(df)
    t, p = d.iloc[-1], d.iloc[-2]
    score, signals = 0, []

    # 均線
    if t.ma5 > t.ma20 > t.ma60:
        ma_txt, ma_c = "多頭排列", 1
    elif t.ma5 < t.ma20 < t.ma60:
        ma_txt, ma_c = "空頭排列", -1
    elif p.close >= p.ma20 and t.close < t.ma20:
        ma_txt, ma_c = "跌破月線", -1
    elif p.close <= p.ma20 and t.close > t.ma20:
        ma_txt, ma_c = "站上月線", 1
    else:
        ma_txt, ma_c = ("月線之上" if t.close > t.ma20 else "月線之下"), (1 if t.close > t.ma20 else -1)
    score += ma_c * 2

    # KD
    if p.k <= p.d and t.k > t.d:
        kd_txt, kd_c = ("低檔金叉" if t.k < 30 else "黃金交叉"), 1
    elif p.k >= p.d and t.k < t.d:
        kd_txt, kd_c = ("高檔死叉" if t.k > 70 else "死亡交叉"), -1
    else:
        kd_txt, kd_c = f"K{t.k:.0f} / D{t.d:.0f}", (1 if t.k > t.d else -1)
    score += kd_c

    score += 1 if t.macd_hist > 0 else -1
    rsi = float(t.rsi)
    vr = float(t.vol_ratio) if pd.notna(t.vol_ratio) else 1.0

    if rsi > 80:
        signals.append("RSI 過熱")
    if rsi < 20:
        signals.append("RSI 超賣")
    if vr >= 2:
        signals.append(f"爆量 {vr:.1f} 倍")
    if ma_txt in ("跌破月線", "站上月線"):
        signals.append(ma_txt)
    if kd_txt in ("低檔金叉", "高檔死叉"):
        signals.append("KD " + kd_txt)

    support = float(d["low"].tail(20).min())
    resist = float(d["high"].tail(20).max())
    close = float(t.close)
    if close <= support * 1.02:
        signals.append("接近支撐")
    if close >= resist * 0.98:
        signals.append("接近壓力")

    tech = "偏多" if score >= 2 else "偏空" if score <= -2 else "盤整"
    return {
        "date": d.index[-1].date(),
        "close": close,
        "chg_pct": (close / float(p.close) - 1) * 100,
        "ma_txt": ma_txt, "ma_c": ma_c,
        "kd_txt": kd_txt, "kd_c": kd_c,
        "rsi": rsi, "vol_ratio": vr,
        "support": support, "resist": resist,
        "tech": tech, "score": score, "signals": signals,
        "chart": d.tail(40)[["open", "high", "low", "close", "ma5", "ma20"]],
    }


def verdict(stances: list[str], tech: str) -> tuple[str, str]:
    """結合分析師看法與技術面。回傳 (標籤, 說明)。"""
    bull, bear = stances.count("偏多"), stances.count("偏空")
    a = "偏多" if bull > bear else "偏空" if bear > bull else ("中性" if stances else "")
    if not a:
        return ("技術" + tech if tech != "盤整" else "盤整"), "分析師未提及，僅看技術面"
    if a == tech:
        return a, f"分析師{a}・技術面同向"
    if a in ("偏多", "偏空") and tech in ("偏多", "偏空"):
        return "分歧", f"分析師{a}，但技術面{tech}"
    return "觀望", f"分析師{a}・技術面{tech}"
