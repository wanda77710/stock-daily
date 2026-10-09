"""透過 LINE 官方帳號 Messaging API 推播給自己。"""
from __future__ import annotations

import os

import requests

API = "https://api.line.me/v2/bot/message/push"


def push(messages: list[dict]) -> None:
    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    user_id = os.environ["LINE_USER_ID"]
    r = requests.post(
        API,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": user_id, "messages": messages[:5]},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"LINE 推播失敗 {r.status_code}: {r.text}")


def image_msg(url: str, preview_url: str) -> dict:
    return {"type": "image", "originalContentUrl": url, "previewImageUrl": preview_url}


def text_msg(text: str) -> dict:
    return {"type": "text", "text": text[:4900]}
