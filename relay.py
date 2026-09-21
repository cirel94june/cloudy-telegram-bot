"""
兄弟广播模块 - 让多个 bot 能"看到"彼此在群里说了什么
=======================================================
Telegram Bot API 的硬规则：Bot 收不到其他 Bot 的消息。
这个模块通过 HTTP 广播绕过限制：Bot 发完消息后，
主动把内容 POST 给兄弟 bot 的 /relay 端点。

环境变量：
  SIBLING_WEBHOOKS  - 逗号分隔的兄弟 bot 地址
                      例: https://bot-b.onrender.com,https://bot-c.onrender.com
  RELAY_SECRET      - 广播密钥（所有 bot 设同一个值，防外人伪造）
"""

import os
import json
import time
import requests
from threading import Thread

# 兄弟 bot 的地址列表
_raw = os.environ.get("SIBLING_WEBHOOKS", "")
SIBLING_URLS = [u.strip().rstrip("/") for u in _raw.split(",") if u.strip()]

# 共享密钥
RELAY_SECRET = os.environ.get("RELAY_SECRET", "catbot-relay-2026")


def relay_to_siblings(chat_id, bot_name, text, timestamp):
    """发完消息后调用：把自己说的话广播给兄弟 bot"""
    if not SIBLING_URLS:
        return

    payload = {
        "relay_secret": RELAY_SECRET,
        "chat_id": str(chat_id),
        "sender_name": bot_name,
        "text": text,
        "timestamp": timestamp,
        "role": "user"  # 对兄弟来说，我说的话算"群聊里别人说的"
    }

    def _send(url):
        try:
            resp = requests.post(
                f"{url}/relay",
                json=payload,
                timeout=5
            )
            if resp.status_code == 200:
                print(f"[RELAY] OK -> {url}")
            else:
                print(f"[RELAY] FAIL {resp.status_code} -> {url}")
        except Exception as e:
            print(f"[RELAY] ERROR -> {url}: {e}")

    for url in SIBLING_URLS:
        Thread(target=_send, args=(url,), daemon=True).start()


def is_valid_relay(data):
    """验证收到的广播是不是自己人发的"""
    return (
        isinstance(data, dict)
        and data.get("relay_secret") == RELAY_SECRET
        and data.get("chat_id")
        and data.get("text")
    )
