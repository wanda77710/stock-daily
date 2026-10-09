"""用 Gemini 直接「看」YouTube 影片，整理分析師對個股的看法與短線推薦。"""
from __future__ import annotations

import json
import os
import time

from google import genai
from google.genai import types

PROMPT = """你是投資研究助理。請完整看完這支台股分析影片（分析師：{analyst}，標題：{title}），
只根據影片中分析師「明確說出」的內容整理，不要自行推測或補充。

我關注的股票：{watchlist}

請輸出 JSON，格式如下：
{{
  "market_view": "分析師對近期盤勢的一句話看法（20 字內，沒提到就空字串）",
  "watchlist": [
    {{"code": "股票代號", "name": "名稱", "mentioned": true 或 false,
      "stance": "偏多" 或 "中性" 或 "偏空",
      "view": "看法重點（30 字內）",
      "entry": "建議進場價位或條件（沒提到就空字串）",
      "stop": "停損價位或條件（沒提到就空字串）"}}
  ],
  "picks": [
    {{"code": "台股代號（不確定就空字串）", "name": "股票名稱",
      "stance": "偏多" 或 "偏空",
      "reason": "選股理由：分析師為什麼看好，包含題材、產業趨勢、營收或籌碼等依據（80 字內）",
      "entry": "建議進場價位或條件（沒提到就空字串）",
      "stop": "停損價位或條件（沒提到就空字串）",
      "target": "目標價（沒提到就空字串）"}}
  ]
}}

規則：
- watchlist 必須逐一列出我關注的每一檔；影片沒提到的，mentioned 設為 false，其他欄位空字串。
- picks 只放分析師在影片中「短線看好、建議可以留意或進場」的個股，不要包含我關注的股票，最多 5 檔。
- 全部使用繁體中文。只輸出 JSON。
"""


def _client() -> genai.Client:
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def analyze_video(video: dict, analyst: str, watchlist: dict[str, str], model: str,
                  fallbacks: list[str] | None = None, retries: int = 4) -> dict | None:
    """Gemini 忙線（503）或暫時錯誤時，等待後重試，並輪流嘗試備用模型。"""
    wl = "、".join(f"{n}({c})" for c, n in watchlist.items())
    prompt = PROMPT.format(analyst=analyst, title=video["title"], watchlist=wl)
    client = _client()
    models = [model] + [m for m in (fallbacks or []) if m != model]
    last_err = None
    for i in range(retries):
        for m in list(models):
            try:
                resp = client.models.generate_content(
                    model=m,
                    contents=types.Content(parts=[
                        types.Part(file_data=types.FileData(file_uri=video["url"])),
                        types.Part(text=prompt),
                    ]),
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
                )
                data = json.loads(resp.text)
                data["analyst"] = analyst
                data["video_title"] = video["title"]
                data["video_url"] = video["url"]
                data["model"] = m
                return data
            except Exception as e:
                last_err = e
                if "404" in str(e) and len(models) > 1:  # 模型已停用或不存在，不再嘗試
                    models.remove(m)
        if i < retries - 1:
            time.sleep(30 * (i + 1))
    print(f"[analyst] 影片分析失敗 {video['url']}: {last_err}")
    return None
