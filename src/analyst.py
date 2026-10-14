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


CALL_TIMEOUT_SEC = 900  # 等 Gemini 回覆的上限 15 分鐘（影片仍會完整看完；這只防止連線卡死）


def _client() -> genai.Client:
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                        http_options=types.HttpOptions(timeout=CALL_TIMEOUT_SEC * 1000))


def explain_error(e: Exception | str) -> str:
    """把 Gemini 的錯誤訊息翻成看得懂的原因，會直接顯示在 LINE 上。"""
    s = str(e)
    low = s.lower()
    if "429" in s and "perday" in low.replace("_", "").replace(" ", ""):
        return "Gemini 今日免費額度已用完"
    if "429" in s or "resource_exhausted" in low:
        return "Gemini 用量達到短時間上限"
    if "timeout" in low or "timed out" in low or "deadline" in low:
        return "Gemini 超過 15 分鐘沒有回應"
    if any(x in s for x in ("503", "500", "UNAVAILABLE", "INTERNAL")) or "overloaded" in low:
        return "Gemini 忙線"
    if "404" in s or "not_found" in low:
        return "Gemini 模型不存在或已停用（需更新 config.yaml 的 gemini_model）"
    if "400" in s and any(x in low for x in ("token", "too long", "exceed", "duration")):
        return "影片太長，超過 Gemini 單次上限"
    if "403" in s or "permission" in low or "private" in low:
        return "影片無法讀取（可能是會員限定或私人影片）"
    if isinstance(e, json.JSONDecodeError) or "expecting" in low:
        return "Gemini 回傳的格式錯誤"
    return "其他錯誤：" + s[:80]


def _is_resolution_error(e: Exception) -> bool:
    s = str(e).lower()
    return "400" in s and "media_resolution" in s


def analyze_video(video: dict, analyst: str, watchlist: dict[str, str], model: str,
                  fallbacks: list[str] | None = None, retries: int = 3,
                  deadline: float | None = None) -> tuple[dict | None, str]:
    """完整看完一支影片並整理成 JSON。回傳 (結果, 失敗原因)；成功時原因為空字串。

    - 影片以「低畫質」送出：仍完整看完、聽完整支影片，但用量約為原本的三分之一，較不會超過免費額度。
    - Gemini 忙線或暫時錯誤時，等待後重試，並輪流嘗試備用模型。
    - deadline：time.time() 的截止時間；超過就放棄，讓日報先用已完成的內容發出。
    """
    wl = "、".join(f"{n}({c})" for c, n in watchlist.items())
    prompt = PROMPT.format(analyst=analyst, title=video["title"], watchlist=wl)
    client = _client()
    models = [model] + [m for m in (fallbacks or []) if m != model]
    low_res = True
    last_err: Exception | str = "未執行"
    for i in range(retries):
        for m in list(models):
            if deadline and time.time() > deadline - 60:
                print(f"[analyst] 分析時間已用完，略過：{video['url']}")
                return None, "分析時間用完（其他影片花太久），稍後補跑"
            try:
                cfg = dict(response_mime_type="application/json", temperature=0.2)
                if low_res:
                    cfg["media_resolution"] = types.MediaResolution.MEDIA_RESOLUTION_LOW
                resp = client.models.generate_content(
                    model=m,
                    contents=types.Content(parts=[
                        types.Part(file_data=types.FileData(file_uri=video["url"])),
                        types.Part(text=prompt),
                    ]),
                    config=types.GenerateContentConfig(**cfg),
                )
                data = json.loads(resp.text)
                data["analyst"] = analyst
                data["video_title"] = video["title"]
                data["video_url"] = video["url"]
                data["model"] = m
                return data, ""
            except Exception as e:
                last_err = e
                print(f"[analyst] {m} 失敗（{explain_error(e)}）：{video['url']} :: {str(e)[:300]}")
                if low_res and _is_resolution_error(e):  # 這個模型不支援低畫質設定，改用一般畫質
                    low_res = False
                    continue
                if "429" in str(e) and "perday" in str(e).lower().replace("_", ""):  # 今日額度已用完，重試也沒用
                    return None, explain_error(e)
                if ("404" in str(e) or "NOT_FOUND" in str(e)) and len(models) > 1:  # 模型已停用或不存在
                    models.remove(m)
        if i < retries - 1:
            if deadline and time.time() + 30 * (i + 1) > deadline - 60:
                break
            time.sleep(30 * (i + 1))
    return None, explain_error(last_err)
