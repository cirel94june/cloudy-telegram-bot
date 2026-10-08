"""
入口包装器 - 给 bot.py 加上兄弟广播 + 记忆缓存
=============================================
原理：不改 bot.py，在外面包一层。
Render 启动命令改成: gunicorn app:app

环境变量（三个 bot 都要加）：
  SIBLING_WEBHOOKS  - 另外两个 bot 的地址，逗号分隔
                      例: https://bot-b.onrender.com,https://bot-c.onrender.com
  RELAY_SECRET      - 广播密钥，三个 bot 设同一个值（随便写一串，防外人）
"""
import time
import bot
from bot import app, load_history, save_history, BOT_NAME, HISTORY_CACHE
from relay import relay_to_siblings, is_valid_relay, SIBLING_URLS
from flask import request as flask_request


# ====== 1. /relay 端点：接收兄弟 bot 的广播 ======
@app.route("/relay", methods=["POST"])
def relay_endpoint():
    data = flask_request.get_json()
    if not data or not is_valid_relay(data):
        return "rejected", 403

    chat_id = data["chat_id"]
    sender = data.get("sender_name", "Bot")
    text = data["text"]
    ts = data.get("timestamp", "")

    formatted = f"{sender}: {text}" if str(chat_id).startswith("-") else text

    history = load_history(chat_id)
    history.append({"role": "user", "content": formatted, "timestamp": ts})
    save_history(history, chat_id)

    print(f"[RELAY] Got: {sender} @ {chat_id}")
    return "ok"


# ====== 2. 包装 process_message_background，发完消息后广播 ======
_original_process = bot.process_message_background


def _patched_process(text, chat_id, sender_name, msg_date=None,
                     should_reply=True, msg_id=None,
                     image_b64=None, image_mime=None, is_voice=False,
                     directed_at_other=False):
    _original_process(text, chat_id, sender_name, msg_date,
                      should_reply, msg_id, image_b64, image_mime,
                      is_voice, directed_at_other)

    h = HISTORY_CACHE.get(chat_id, [])
    if h and h[-1].get("role") == "assistant":
        last = h[-1]
        relay_to_siblings(
            chat_id, BOT_NAME,
            last["content"], last.get("timestamp", "")
        )


bot.process_message_background = _patched_process


# ====== 3. 记忆缓存（5分钟内不重复请求 Gist） ======
_MEM_CACHE = {}
_MEM_CACHE_T = {}
_MEM_TTL = 300

_original_fetch = bot.fetch_memory


def _cached_fetch(chat_id=""):
    key = str(chat_id)
    now = time.time()
    if key in _MEM_CACHE and now - _MEM_CACHE_T.get(key, 0) < _MEM_TTL:
        return _MEM_CACHE[key]
    result = _original_fetch(chat_id)
    _MEM_CACHE[key] = result
    _MEM_CACHE_T[key] = now
    return result


bot.fetch_memory = _cached_fetch


print(f"[INIT] Relay loaded, siblings: {len(SIBLING_URLS)}")
print(f"[INIT] Memory cache ON (TTL={_MEM_TTL}s)")
