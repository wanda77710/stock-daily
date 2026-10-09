"""抓分析師頻道的最新影片（讀取頻道「影片」與「直播」頁面，不需要金鑰）。

YouTube 的 RSS 已無法穩定使用，所以改讀頻道頁面內嵌的 ytInitialData。
"""
from __future__ import annotations

import datetime as dt
import json
import re

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}
COOKIES = {"CONSENT": "YES+1", "SOCS": "CAI"}

_UNIT_HOURS = {
    "秒": 1 / 3600, "分鐘": 1 / 60, "小時": 1, "天": 24, "週": 168, "個月": 720, "年": 8760,
    "second": 1 / 3600, "minute": 1 / 60, "hour": 1, "day": 24, "week": 168, "month": 720, "year": 8760,
}
_AGO_RE = re.compile(r"(\d+)\s*(秒|分鐘|小時|天|週|個月|年|second|minute|hour|day|week|month|year)s?\s*(前|ago)")


def _age_hours(text: str) -> float | None:
    m = _AGO_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1)) * _UNIT_HOURS[m.group(2)]


def _initial_data(html: str) -> dict:
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
    if not m:
        raise ValueError("頁面中找不到 ytInitialData（可能被 YouTube 擋下）")
    return json.loads(m.group(1))


def _collect(node, out: list) -> None:
    if isinstance(node, dict):
        if "lockupViewModel" in node:
            lk = node["lockupViewModel"]
            meta = lk.get("metadata", {}).get("lockupMetadataViewModel", {})
            title = meta.get("title", {}).get("content", "")
            parts = []
            for row in meta.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", []):
                for p in row.get("metadataParts", []):
                    parts.append(p.get("text", {}).get("content", ""))
            vid = lk.get("contentId", "")
            if len(vid) == 11:
                out.append({"video_id": vid, "title": title, "age_text": " ".join(parts)})
        elif "videoRenderer" in node:  # 舊版版面
            vr = node["videoRenderer"]
            title = "".join(r.get("text", "") for r in vr.get("title", {}).get("runs", []))
            out.append({"video_id": vr.get("videoId", ""), "title": title,
                        "age_text": vr.get("publishedTimeText", {}).get("simpleText", "")})
        for v in node.values():
            _collect(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect(v, out)


def latest_videos(channel_id: str, window_hours: int, limit: int) -> list[dict]:
    """回傳 window_hours 小時內上傳的影片（含直播），新到舊，最多 limit 支。"""
    found: dict[str, dict] = {}
    errors = []
    for tab in ("videos", "streams"):
        url = f"https://www.youtube.com/channel/{channel_id}/{tab}"
        try:
            r = requests.get(url, headers=HEADERS, cookies=COOKIES, timeout=30)
            r.raise_for_status()
            items: list = []
            _collect(_initial_data(r.text), items)
        except Exception as e:
            errors.append(f"{tab}: {e}")
            continue
        for it in items:
            age = _age_hours(it["age_text"])
            if age is None or age > window_hours or it["video_id"] in found:
                continue
            found[it["video_id"]] = {
                "video_id": it["video_id"],
                "url": f"https://www.youtube.com/watch?v={it['video_id']}",
                "title": it["title"],
                "description": "",
                "published": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=age),
            }
    if not found and len(errors) == 2:
        raise RuntimeError("；".join(errors))
    vids = sorted(found.values(), key=lambda v: v["published"], reverse=True)
    return vids[:limit]
"""抓分析師頻道的最新影片（使用 YouTube 公開 RSS，不需要金鑰）。"""
from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET

import requests

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}
UA = {"User-Agent": "Mozilla/5.0 (stock-daily bot)"}


def latest_videos(channel_id: str, window_hours: int, limit: int) -> list[dict]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=window_hours)
    out = []
    for e in root.findall("atom:entry", NS):
        published = dt.datetime.fromisoformat(e.find("atom:published", NS).text.replace("Z", "+00:00"))
        if published < cutoff:
            continue
        vid = e.find("yt:videoId", NS).text
        desc_el = e.find("media:group/media:description", NS)
        out.append({
            "video_id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "title": e.find("atom:title", NS).text or "",
            "description": (desc_el.text or "") if desc_el is not None else "",
            "published": published,
        })
    out.sort(key=lambda v: v["published"], reverse=True)
    return out[:limit]
