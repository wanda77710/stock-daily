"""每日選股日報主程式。

用法：
  python src/main.py build   # 抓資料、分析、產生圖卡（images/ 底下）
  python src/main.py send    # 把剛產生的圖卡推播到 LINE
環境變數：GEMINI_API_KEY、LINE_CHANNEL_ACCESS_TOKEN、LINE_USER_ID、
          GITHUB_REPOSITORY（Actions 自動提供）、FORCE_RUN=1（休市也強制執行）
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import uuid

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from market import fetch_history, tech_summary, verdict  # noqa: E402
from render import make_preview, render  # noqa: E402

TPE = dt.timezone(dt.timedelta(hours=8))
STATE = ROOT / "images" / "latest.json"
WEEK = "一二三四五六日"


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def _clean_code(s: str) -> str:
    s = "".join(ch for ch in (s or "") if ch.isalnum())
    return s if s[:4].isdigit() else ""


def build_report(cfg: dict, analyses: list[dict], tech_cache: dict, today: dt.date) -> dict:
    wl = {str(k): v for k, v in cfg["watchlist"].items()}

    # ---- 關注標的 ----
    watch_cards = []
    for code, name in wl.items():
        views = []
        for a in analyses:
            for w in a.get("watchlist", []):
                if _clean_code(w.get("code")) == code or w.get("name") == name:
                    if w.get("mentioned"):
                        extra = "　".join(x for x in [f"進場：{w['entry']}" if w.get("entry") else "",
                                                       f"停損：{w['stop']}" if w.get("stop") else ""] if x)
                        views.append({"analyst": a["analyst"].split("（")[0], "stance": w.get("stance", "中性"),
                                      "text": w.get("view", ""), "extra": extra})
        t = tech_cache.get(code)
        v, vt = verdict([x["stance"] for x in views], t["tech"] if t else "盤整")
        if not views:
            views = [{"analyst": "", "stance": "", "text": "分析師今日影片未提及", "extra": ""}]
        watch_cards.append({"code": code, "name": name, "tech": t, "views": views, "verdict": v, "verdict_txt": vt})

    # ---- 分析師推薦 ----
    picks: dict[str, dict] = {}
    order: dict[str, int] = {}  # 每位分析師各自的推薦順序，排序時輪流取，避免只出現同一位
    for a in analyses:
        for p in a.get("picks", []):
            code = _clean_code(p.get("code"))
            if code in wl:
                continue
            key = code or p.get("name", "")
            if not key:
                continue
            who = a["analyst"].split("（")[0]
            rank = order.get(who, 0)
            order[who] = rank + 1
            item = picks.setdefault(key, {"code": code, "name": p.get("name", ""), "views": [], "rank": rank})
            item["rank"] = min(item["rank"], rank)
            extra = "　".join(x for x in [f"進場：{p['entry']}" if p.get("entry") else "",
                                           f"停損：{p['stop']}" if p.get("stop") else "",
                                           f"目標：{p['target']}" if p.get("target") else ""] if x)
            item["views"].append({"analyst": a["analyst"].split("（")[0], "stance": p.get("stance", "偏多"),
                                  "text": p.get("reason", ""), "extra": extra})
    ranked = sorted(picks.values(), key=lambda x: (-len({v["analyst"] for v in x["views"]}), x["rank"]))[: cfg.get("max_picks", 6)]

    # ---- 提醒 ----
    reminders = []
    for c in watch_cards:
        if c["verdict"] == "分歧":
            reminders.append(f"{c['name']}：{c['verdict_txt']}，訊號分歧")
    for c in watch_cards:
        t = c["tech"]
        if t and t["signals"]:
            reminders.append(f"{c['name']}：" + "、".join(t["signals"]))
    for a in analyses:
        if a.get("market_view"):
            reminders.append(f"{a['analyst'].split('（')[0]}盤勢看法：{a['market_view']}")

    sources = [f"{a['analyst']}｜{a['video_title']}" for a in analyses] or ["今日兩位分析師皆無新影片，僅呈現技術面"]
    return {
        "date_label": f"{today:%Y/%m/%d}（{WEEK[today.weekday()]}）盤後",
        "watch_cards": watch_cards,
        "picks": ranked,
        "reminders": reminders,
        "sources": sources,
    }


def picks_text(report: dict) -> str:
    """分析師短線推薦：用文字說明選股理由。"""
    lines = [f"📌 分析師短線推薦｜{report['date_label']}"]
    if not report["picks"]:
        lines.append("今日兩位分析師沒有提到其他短線標的。")
    for i, p in enumerate(report["picks"], 1):
        who = "、".join(dict.fromkeys(v["analyst"] for v in p["views"]))
        lines.append("")
        lines.append(f"{i}. {p['name']} {p['code']}（{who}）".replace("  ", " "))
        for v in p["views"]:
            lines.append(f"・{v['analyst']}［{v['stance']}］{v['text']}")
            if v.get("extra"):
                lines.append(f"　{v['extra']}")
    lines.append("")
    lines.append("※ 分析師觀點整理，非投資建議")
    return "\n".join(lines)


def cmd_build() -> None:
    from analyst import analyze_video
    from youtube import latest_videos

    cfg = load_config()
    today = dt.datetime.now(TPE).date()
    wl = {str(k): v for k, v in cfg["watchlist"].items()}

    tech_cache, last_dates = {}, set()
    for code in wl:
        df, sym = fetch_history(code)
        if df is None:
            print(f"[warn] 抓不到 {code} 股價")
            tech_cache[code] = None
            continue
        tech_cache[code] = tech_summary(df)
        last_dates.add(tech_cache[code]["date"])
    if today not in last_dates and os.environ.get("FORCE_RUN") != "1":
        print(f"今天 {today} 沒有新的收盤資料（休市或資料未更新），不發送。")
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps({"skip": True}), encoding="utf-8")
        return

    analyses = []
    for a in cfg["analysts"]:
        try:
            vids = latest_videos(a["channel_id"], cfg.get("video_window_hours", 26), cfg.get("max_videos_per_analyst", 2))
        except Exception as e:
            print(f"[warn] 抓 {a['name']} 影片清單失敗：{e}")
            vids = []
        print(f"{a['name']}：{len(vids)} 支新影片")
        for v in vids:
            r = analyze_video(v, a["name"], wl, cfg.get("gemini_model", "gemini-3.8-flash"))
            if r:
                analyses.append(r)

    report = build_report(cfg, analyses, tech_cache, max(last_dates) if last_dates else today)
    name = f"{today:%Y%m%d}-{uuid.uuid4().hex[:12]}"
    img_dir = ROOT / "images"
    img_dir.mkdir(exist_ok=True)
    full = render(report, str(img_dir / f"{name}.png"))
    prev = make_preview(full, str(img_dir / f"{name}-p.png"))
    (img_dir / f"{name}.json").write_text(json.dumps(analyses, ensure_ascii=False, indent=1), encoding="utf-8")
    STATE.write_text(json.dumps({"skip": False, "full": f"images/{name}.png", "preview": f"images/{name}-p.png",
                                 "picks_text": picks_text(report)}, ensure_ascii=False), encoding="utf-8")
    print("圖卡已產生：", full)


def cmd_send() -> None:
    from line_push import image_msg, push, text_msg

    st = json.loads(STATE.read_text(encoding="utf-8"))
    if st.get("skip"):
        print("今日不發送。")
        return
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    base = f"https://raw.githubusercontent.com/{repo}/{branch}/"
    push([image_msg(base + st["full"], base + st["preview"]), text_msg(st["picks_text"])])
    print("LINE 推播完成")


def cmd_cleanup(days: int = 30) -> None:
    """依檔名日期刪除 N 天前的圖卡，避免儲存庫越來越大。"""
    cutoff = dt.datetime.now(TPE).date() - dt.timedelta(days=days)
    for f in (ROOT / "images").glob("*.png"):  # 分析紀錄 .json 保留，供日後驗證分析師準確度
        try:
            d = dt.datetime.strptime(f.name[:8], "%Y%m%d").date()
        except ValueError:
            continue
        if d < cutoff:
            f.unlink()


if __name__ == "__main__":
    {"build": cmd_build, "send": cmd_send, "cleanup": cmd_cleanup}[sys.argv[1] if len(sys.argv) > 1 else "build"]()
