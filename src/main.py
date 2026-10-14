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
    ranked = sorted(picks.values(), key=lambda x: (-len({v["analyst"] for v in x["views"]}), x["rank"]))[: cfg.get("max_picks", 8)]

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


def _short(title: str, n: int = 18) -> str:
    return title if len(title) <= n else title[:n] + "…"


def picks_texts(report: dict, analysts: list[str], status: dict | None = None, final: bool = True) -> list[str]:
    """分析師短線推薦：每位分析師一則文字訊息，說明選股理由。
    status[分析師] = {"videos": 影片數, "ok": 成功數, "fails": [(標題, 原因)], "list_error": 抓清單失敗原因}
    final=False 表示晚上還會自動補跑。"""
    retry_note = "晚上 20:00、22:00 會自動補跑，成功後補發給你。"
    out = []
    for who in analysts:
        lines = [f"📌 {who}｜短線推薦｜{report['date_label']}"]
        items = [(p, [v for v in p["views"] if v["analyst"] == who]) for p in report["picks"]]
        items = [(p, vs) for p, vs in items if vs][:4]
        st = (status or {}).get(who, {})
        fails = st.get("fails", [])
        if st.get("list_error"):
            lines.append(f"⚠ 抓不到影片清單：{st['list_error']}")
            if not final:
                lines.append(retry_note)
        elif not items:
            if st.get("videos", 1) == 0:
                lines.append("今日沒有新影片。")
            elif fails and st.get("ok", 0) == 0:
                lines.append("⚠ 今日影片分析失敗：")
            else:
                lines.append("今日影片沒有提到其他短線標的。")
        elif fails:
            lines.append(f"（{st['videos']} 支影片中有 {len(fails)} 支分析失敗，內容可能不完整）")
        if fails:
            for title, reason in fails:
                lines.append(f"・{_short(title)}：{reason}")
            if not final:
                lines.append(retry_note)
        for i, (p, vs) in enumerate(items, 1):
            lines.append("")
            lines.append(f"{i}. {p['name']} {p['code']}".strip())
            for v in vs:
                lines.append(f"［{v['stance']}］{v['text']}")
                if v.get("extra"):
                    lines.append(f"　{v['extra']}")
        lines.append("")
        lines.append("※ 分析師觀點整理，非投資建議")
        out.append("\n".join(lines))
    return out


def _state_file(today: dt.date) -> pathlib.Path:
    return ROOT / "images" / f"state-{today:%Y%m%d}.json"


def _run_mode(today_now: dt.datetime) -> str:
    """full：18:15 主排程；retry：20:00 / 22:00 補跑。手動執行可用 MODE 指定。"""
    m = os.environ.get("MODE", "auto")
    if m in ("full", "retry"):
        return m
    return "retry" if today_now.hour >= 19 else "full"


def _status(cfg: dict, st: dict) -> dict:
    out = {}
    for a in cfg["analysts"]:
        who = a["name"].split("（")[0]
        vids = [v for v in st["videos"] if v["analyst"] == a["name"]]
        out[who] = {"videos": len(vids), "ok": sum(v["ok"] for v in vids),
                    "fails": [(v["title"], v["reason"]) for v in vids if not v["ok"]],
                    "list_error": st["list_errors"].get(a["name"], "")}
    return out


def _skip(msg: str) -> None:
    print(msg)
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"skip": True}), encoding="utf-8")


def cmd_build() -> None:
    import time

    from analyst import analyze_video
    from youtube import latest_videos

    cfg = load_config()
    now = dt.datetime.now(TPE)
    today = now.date()
    wl = {str(k): v for k, v in cfg["watchlist"].items()}
    mode = _run_mode(now)
    final = mode == "retry" and now.hour >= 21  # 22:00 那次是最後一次補跑
    sf = _state_file(today)
    st = json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else None

    if mode == "retry":
        if st is None:
            print("今天的主排程沒有留下紀錄（可能中途中止），改跑完整流程。")
            mode = "full"
        elif not any(not v["ok"] for v in st["videos"]) and not st["list_errors"]:
            return _skip("今天的影片都已分析完成，不需要補跑。")

    tech_cache, last_dates = {}, set()
    for code in wl:
        df, sym = fetch_history(code)
        if df is None:
            print(f"[warn] 抓不到 {code} 股價")
            tech_cache[code] = None
            continue
        tech_cache[code] = tech_summary(df)
        last_dates.add(tech_cache[code]["date"])
    if mode == "full" and today not in last_dates and os.environ.get("FORCE_RUN") != "1":
        return _skip(f"今天 {today} 沒有新的收盤資料（休市或資料未更新），不發送。")

    if st is None:
        st = {"date": str(today), "videos": [], "list_errors": {}, "image_sent": False}
    known = {v["url"] for v in st["videos"]}

    # 抓影片清單：主排程抓全部；補跑只重抓上次失敗的分析師
    for a in cfg["analysts"]:
        if mode == "retry" and a["name"] not in st["list_errors"]:
            continue
        try:
            vids = latest_videos(a["channel_id"], cfg.get("video_window_hours", 26), cfg.get("max_videos_per_analyst", 2))
            st["list_errors"].pop(a["name"], None)
        except Exception as e:
            print(f"[warn] 抓 {a['name']} 影片清單失敗：{e}")
            st["list_errors"][a["name"]] = "YouTube 頁面讀取失敗"
            vids = []
        print(f"{a['name']}：{len(vids)} 支新影片")
        for v in vids:
            if v["url"] not in known:
                st["videos"].append({"analyst": a["name"], "url": v["url"], "title": v["title"],
                                     "ok": False, "reason": "尚未分析", "result": None})
                known.add(v["url"])

    # 分析：只處理還沒成功的影片（已成功的保留，不重看）
    deadline = time.time() + cfg.get("analysis_budget_minutes", 60) * 60
    newly_ok = 0
    for v in st["videos"]:
        if v["ok"]:
            continue
        r, reason = analyze_video({"url": v["url"], "title": v["title"]}, v["analyst"], wl,
                                  cfg.get("gemini_model", "gemini-3.8-flash"),
                                  cfg.get("gemini_fallback_models", []), deadline=deadline)
        if r:
            v.update(ok=True, reason="", result=r)
            newly_ok += 1
        else:
            v["reason"] = reason
        sf.parent.mkdir(exist_ok=True)
        sf.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")  # 每支都存檔，中途中止也不會白做

    analyses = [v["result"] for v in st["videos"] if v["ok"]]
    status = _status(cfg, st)
    names = [a["name"].split("（")[0] for a in cfg["analysts"]]
    sf.parent.mkdir(exist_ok=True)

    header = None
    if mode == "retry" and st.get("image_sent"):
        if newly_ok == 0 and not final:
            sf.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
            return _skip("補跑仍未成功，等下一次補跑。")
        if newly_ok == 0 and final:
            # 最後一次補跑仍失敗：只傳一則說明原因，不重發圖卡
            report = build_report(cfg, analyses, tech_cache, max(last_dates) if last_dates else today)
            lines = [f"⚠ 今日補跑結束，以下影片仍無法分析（{report['date_label']}）"]
            for who, s_ in status.items():
                if s_.get("list_error"):
                    lines.append(f"・{who}：抓不到影片清單（{s_['list_error']}）")
                for title, reason in s_["fails"]:
                    lines.append(f"・{who}｜{_short(title)}：{reason}")
            lines.append("")
            lines.append("建議直接看原影片；若連續幾天都是額度問題，可以考慮開啟 Gemini 付費額度。")
            sf.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
            STATE.write_text(json.dumps({"skip": False, "send_image": False, "texts": ["\n".join(lines)]},
                                        ensure_ascii=False), encoding="utf-8")
            print("最後一次補跑仍失敗，傳送原因說明。")
            return
        header = f"🔁 補發：{newly_ok} 支分析師影片已分析完成，以下是更新後的日報。"

    report = build_report(cfg, analyses, tech_cache, max(last_dates) if last_dates else today)
    name = f"{today:%Y%m%d}-{uuid.uuid4().hex[:12]}"
    img_dir = ROOT / "images"
    full = render(report, str(img_dir / f"{name}.png"))
    prev = make_preview(full, str(img_dir / f"{name}-p.png"))
    (img_dir / f"{name}.json").write_text(json.dumps(analyses, ensure_ascii=False, indent=1), encoding="utf-8")
    st["image_sent"] = True
    sf.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    texts = picks_texts(report, names, status, final=final)
    STATE.write_text(json.dumps({"skip": False, "full": f"images/{name}.png", "preview": f"images/{name}-p.png",
                                 "send_image": True, "header": header, "texts": texts}, ensure_ascii=False), encoding="utf-8")
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
    msgs = [text_msg(st["header"])] if st.get("header") else []
    if st.get("send_image", True):
        msgs.append(image_msg(base + st["full"], base + st["preview"]))
    msgs += [text_msg(t) for t in st.get("texts", [])]
    push(msgs[:5])
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
