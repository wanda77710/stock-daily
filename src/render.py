"""把當日資料畫成手機直式圖卡（紅漲綠跌）。"""
from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm

W = 1080
UP, DN, INK, SUB, BG, CARD, WARN, NEU = "#D93B3B", "#1E9E5A", "#1F2430", "#6B7280", "#F6F7F9", "#FFFFFF", "#B45309", "#6B7280"
TAG_C = {"偏多": UP, "偏空": DN, "中性": NEU, "盤整": NEU, "觀望": WARN, "分歧": WARN, "技術偏多": UP, "技術偏空": DN}
_FONT_CANDIDATES = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"),
]


def _fonts():
    for r, b in _FONT_CANDIDATES:
        if os.path.exists(r):
            return r, (b if os.path.exists(b) else r)
    raise FileNotFoundError("找不到中文字型 Noto Sans CJK")


_R, _B = _fonts()
F = lambda s: fm.FontProperties(fname=_R, size=s)
B = lambda s: fm.FontProperties(fname=_B, size=s)


def _wrap(text: str, size: int, max_px: int) -> list[str]:
    if not text:
        return []
    lines, cur, w = [], "", 0.0
    for ch in text:
        cw = size * 1.39 * (0.58 if ord(ch) < 128 else 1.0)
        if w + cw > max_px and cur:
            lines.append(cur)
            cur, w = "", 0.0
        cur += ch
        w += cw
    if cur:
        lines.append(cur)
    return lines


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def _px(v: float) -> str:
    return f"{v:,.0f}" if v >= 1000 else f"{v:,.1f}" if v >= 100 else f"{v:,.2f}"


class Canvas:
    def __init__(self, height: int):
        self.H = height
        self.fig = plt.figure(figsize=(W / 100, height / 100), dpi=100)
        self.fig.patch.set_facecolor(BG)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, W)
        self.ax.set_ylim(height, 0)
        self.ax.axis("off")

    def text(self, x, y, s, fp, color=INK, **kw):
        self.ax.text(x, y, s, fontproperties=fp, color=color, va="center", **kw)

    def tag(self, x, y, s, size=14, color=None, **kw):
        c = color or TAG_C.get(s, NEU)
        self.ax.text(x, y, f" {s} ", fontproperties=B(size), color="white", va="center",
                     bbox=dict(boxstyle="round,pad=0.3", fc=c, ec="none"), **kw)

    def card(self, y, h):
        self.ax.add_patch(plt.Rectangle((40, y), W - 80, h, color=CARD, zorder=0))

    def kline(self, x, y, w, h, chart):
        a = self.fig.add_axes([x / W, 1 - (y + h) / self.H, w / W, h / self.H])
        n = len(chart)
        for i, (_, r) in enumerate(chart.iterrows()):
            col = UP if r.close >= r.open else DN
            a.plot([i, i], [r.low, r.high], color=col, lw=1)
            a.add_patch(plt.Rectangle((i - .3, min(r.open, r.close)), .6, max(abs(r.close - r.open), (r.high - r.low) * .02 + 1e-9), color=col))
        a.plot(range(n), chart["ma5"].values, color="#F59E0B", lw=1.6)
        a.plot(range(n), chart["ma20"].values, color="#3B82F6", lw=1.6)
        a.set_xlim(-1, n)
        a.axis("off")


# ---------- 版面計算 ----------
TEXT_W = W - 80 - 60  # 卡片內文字寬度


def _analyst_lines(items: list[dict]) -> list[tuple]:
    """items: [{analyst, stance, text, extra}] -> 繪製指令列表與高度"""
    rows = []
    for it in items:
        body = _wrap(it["text"], 16, TEXT_W - 230)
        rows.append(("head", it["analyst"], it["stance"], body[0] if body else ""))
        for ln in body[1:]:
            rows.append(("cont", ln))
        if it.get("extra"):
            for ln in _wrap(it["extra"], 15, TEXT_W - 230):
                rows.append(("extra", ln))
    return rows


def _stock_card_height(card: dict) -> int:
    h = 370
    if card.get("tech") and card["tech"]["signals"]:
        h += 40
    h += len(_analyst_lines(card["views"])) * 36 + 20
    return h


def _draw_stock_card(cv: Canvas, y: int, card: dict) -> int:
    h = _stock_card_height(card)
    cv.card(y, h)
    t = card.get("tech")
    cv.text(70, y + 45, f"{card['name']} {card['code']}", B(24))
    cv.tag(W - 80, y + 45, card["verdict"], 18, ha="right")
    if not t:
        cv.text(70, y + 100, "查無股價資料", F(18), SUB)
    else:
        cc = UP if t["chg_pct"] >= 0 else DN
        cv.text(70, y + 98, _px(t["close"]), B(30))
        cv.text(70 + len(_px(t["close"])) * 25 + 30, y + 100, _pct(t["chg_pct"]), B(22), cc)
        cv.text(70, y + 140, "綜合判斷：" + card["verdict_txt"], F(16), TAG_C.get(card["verdict"], NEU))
        cv.kline(70, y + 165, 420, 160, t["chart"])
        cv.text(70, y + 345, "── 5日線", F(12), "#F59E0B")
        cv.text(170, y + 345, "── 20日線", F(12), "#3B82F6")
        rows = [
            ("均線", t["ma_txt"], UP if t["ma_c"] > 0 else DN),
            ("KD", t["kd_txt"], UP if t["kd_c"] > 0 else DN),
            ("RSI", f"{t['rsi']:.0f}", WARN if t["rsi"] > 80 or t["rsi"] < 20 else NEU),
            ("量能", f"{t['vol_ratio']:.1f} 倍", UP if t["vol_ratio"] >= 1.5 else NEU),
        ]
        for i, (k, v, c) in enumerate(rows):
            cv.text(540, y + 185 + i * 40, k, F(16), SUB)
            cv.text(630, y + 185 + i * 40, v, B(17), c)
        cv.text(540, y + 345, f"壓力 {_px(t['resist'])}｜支撐 {_px(t['support'])}", F(15))
    yy = y + 370
    if t and t["signals"]:
        cv.text(70, yy + 10, "訊號：" + "、".join(t["signals"]), B(16), WARN)
        yy += 40
    for r in _analyst_lines(card["views"]):
        yy += 36
        if r[0] == "head":
            if r[2]:
                cv.text(70, yy, r[1][:6], B(16))
                cv.tag(200, yy, r[2], 12)
                cv.text(290, yy, r[3], F(16))
            else:
                cv.text(70, yy, r[3], F(16), SUB)
        elif r[0] == "cont":
            cv.text(290, yy, r[1], F(16))
        else:
            cv.text(290, yy, r[1], F(15), WARN)
    return y + h + 20


def render(report: dict, out_path: str) -> str:
    sections = [("我的關注標的", report["watch_cards"])]
    reminders = report["reminders"]
    sources = report["sources"]

    H = 140
    for _, cards in sections:
        H += 70 + (sum(_stock_card_height(c) + 20 for c in cards) if cards else 100)
    rem_lines = [ln for r in reminders for ln in _wrap("・" + r, 16, TEXT_W)] or ["・今日無特別提醒"]
    H += 90 + len(rem_lines) * 34 + 20
    src_lines = [ln for s in sources for ln in _wrap(s, 12, TEXT_W)]
    H += 30 + len(src_lines) * 24 + 60

    cv = Canvas(H)
    cv.text(55, 55, "台股盤後選股日報", B(34))
    cv.text(55, 105, report["date_label"], F(16), SUB)
    y = 140
    for title, cards in sections:
        cv.text(55, y + 35, title, B(24))
        y += 70
        if not cards:
            cv.card(y, 80)
            cv.text(70, y + 40, "今日分析師無新的短線推薦" if "推薦" in title else "無資料", F(17), SUB)
            y += 100
        for c in cards:
            y = _draw_stock_card(cv, y, c)
    h = 70 + len(rem_lines) * 34 + 20
    cv.card(y, h)
    cv.text(70, y + 40, "⚠ 今日提醒", B(20), WARN)
    for i, ln in enumerate(rem_lines):
        cv.text(70, y + 85 + i * 34, ln, F(16))
    y += h + 30
    cv.text(55, y, "影片來源", B(13), SUB)
    for i, ln in enumerate(src_lines):
        cv.text(55, y + 28 + i * 24, ln, F(12), SUB)
    cv.text(W / 2, H - 30, "資訊整理，非投資建議", F(13), SUB, ha="center")
    cv.fig.savefig(out_path, facecolor=BG)
    plt.close(cv.fig)
    return out_path


def make_preview(src: str, dst: str, width: int = 480) -> str:
    """LINE 預覽圖需較小尺寸。"""
    img = plt.imread(src)
    h = int(img.shape[0] * width / img.shape[1])
    fig = plt.figure(figsize=(width / 100, h / 100), dpi=100)
    a = fig.add_axes([0, 0, 1, 1])
    a.imshow(img)
    a.axis("off")
    fig.savefig(dst)
    plt.close(fig)
    return dst
