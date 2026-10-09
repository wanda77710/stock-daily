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
