"""
TG 大群同步到家里（方案：AnimalsHome 仓库 docs/施工方案-bot出门.md 末尾）。三只 bot 用同一份这个文件。

- 只传一个群：允许名单里唯一的、不在 PRIVATE_CHATS 里的群；有两个以上就不传，免得传错。
- 用现有的回家配置（home_tools 那四个环境变量），不用新配置；没配齐就什么都不做。
- 传不回家不影响 bot 聊天：排队、后台发、满了丢弃，日志里不写聊天内容。
- 小猫回复一条说 /带回家：家里自己处理，bot 不回话（is_bring_home）。
- 家里窗口说的话由一只 bot 代发（SENDER_AI），发一次，失败不重发。
"""

from __future__ import annotations

import os
import queue
import threading
import time

import requests

import home_tools

SENDER_AI = "cloudy"
QUEUE_SIZE = 200
TIMEOUT_SECONDS = 8
POLL_SECONDS = 6
BRING_COMMANDS = {"/带回家", "／带回家"}

_queue: "queue.Queue[dict]" = queue.Queue(maxsize=QUEUE_SIZE)
_worker_lock = threading.Lock()
_worker_started = False
_sender_started = False


def sync_chat(allowed_ids, private_chats) -> str:
    groups = [str(i) for i in (allowed_ids or []) if str(i).startswith("-") and str(i) not in {str(p) for p in (private_chats or [])}]
    return groups[0] if len(groups) == 1 else ""


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ.get('AIONSHOME_OUTING_KEY', '')}",
            "CF-Access-Client-Id": os.environ.get("CF_ACCESS_CLIENT_ID", ""),
            "CF-Access-Client-Secret": os.environ.get("CF_ACCESS_CLIENT_SECRET", "")}


def _url(path: str) -> str:
    return os.environ.get("AIONSHOME_URL", "").rstrip("/") + "/api/outing/tg-group/" + path


def _worker() -> None:
    while True:
        body = _queue.get()
        for attempt in range(2):
            try:
                status = requests.post(_url("message"), json=body, headers=_headers(), timeout=TIMEOUT_SECONDS).status_code
                if status < 500:
                    if status != 200:
                        print(f"[HOME-SYNC] 家里没收 status={status}")
                    break
            except Exception as error:
                print(f"[HOME-SYNC] 传回家失败 type={type(error).__name__} try={attempt + 1}")
            time.sleep(1)
        _queue.task_done()


def _enqueue(body: dict) -> None:
    global _worker_started
    if not home_tools.enabled():
        return
    with _worker_lock:
        if not _worker_started:
            threading.Thread(target=_worker, daemon=True).start()
            _worker_started = True
    try:
        _queue.put_nowait(body)
    except queue.Full:
        print("[HOME-SYNC] 队列满了，丢弃一条")


def _message_text(msg: dict, text: str) -> str:
    if text:
        return text
    if msg.get("photo"):
        return "[图片]"
    if msg.get("voice") or msg.get("audio"):
        return "[语音]"
    if msg.get("sticker"):
        return "[贴纸]"
    return ""


def observe(msg: dict, chat_id, text, *, target_chat: str, bot_id: str, sender_name, sender_id, sender_is_bot, is_owner: bool) -> None:
    """webhook 收到的每条消息过一下这里。"""
    if not target_chat or str(chat_id) != target_chat:
        return
    content = _message_text(msg, text or "")
    if not content:
        return
    _enqueue({"chat_id": str(chat_id), "message_id": msg.get("message_id"), "ts": msg.get("date"),
              "sender_id": str(sender_id), "sender_name": sender_name, "is_bot": bool(sender_is_bot),
              "is_owner": bool(is_owner), "text": content, "reporter_bot_id": str(bot_id),
              "reply_to": (msg.get("reply_to_message") or {}).get("message_id")})


def observe_sent(chat_id, sent, text, *, target_chat: str, bot_id: str) -> None:
    """自己发出去的话 Telegram 不会再推给自己，发成功后在这里传回家。"""
    if not target_chat or str(chat_id) != target_chat or not isinstance(sent, dict):
        return
    sender = sent.get("from") or {}
    _enqueue({"chat_id": str(chat_id), "message_id": sent.get("message_id"), "ts": sent.get("date"),
              "sender_id": str(bot_id), "sender_name": sender.get("first_name") or "bot", "is_bot": True,
              "is_owner": False, "text": str(sent.get("text") or text or ""), "reporter_bot_id": str(bot_id),
              "reply_to": (sent.get("reply_to_message") or {}).get("message_id")})


def is_bring_home(chat_id, text, *, target_chat: str, is_owner: bool, is_reply: bool) -> bool:
    return bool(target_chat) and str(chat_id) == target_chat and is_owner and is_reply and (text or "").strip() in BRING_COMMANDS


def _send_loop(send, user_name: str) -> None:
    while True:
        time.sleep(POLL_SECONDS)
        try:
            response = requests.post(_url("outbox"), json={}, headers=_headers(), timeout=TIMEOUT_SECONDS)
            item = response.json().get("item") if response.status_code == 200 else None
        except Exception as error:
            print(f"[HOME-SYNC] 取发件箱失败 type={type(error).__name__}")
            continue
        if not item:
            continue
        try:
            ok = bool(send(item["chat_id"], f"{user_name}：{item['text']}"))
        except Exception as error:
            print(f"[HOME-SYNC] 代发失败 type={type(error).__name__}")
            ok = False
        try:
            requests.post(_url("ack"), json={"id": item["id"], "ok": ok}, headers=_headers(), timeout=TIMEOUT_SECONDS)
        except Exception as error:
            print(f"[HOME-SYNC] 回执失败 type={type(error).__name__}")


def start_sender(ai_id: str, target_chat: str, send, user_name: str) -> None:
    """只有代发的那只 bot 启动。"""
    global _sender_started
    if ai_id != SENDER_AI or not target_chat or not home_tools.enabled():
        return
    with _worker_lock:
        if _sender_started:
            return
        _sender_started = True
    threading.Thread(target=_send_loop, args=(send, user_name), daemon=True).start()
