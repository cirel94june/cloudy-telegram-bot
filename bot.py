"""
小猫的 Telegram Bot — 基于 s-telegram-bot 改造
================================================
小克 / 狗蛋共用同一套代码，靠环境变量区分人格。
部署在 Render 免费层，Flask + Gunicorn + Webhook。

核心特性：
- 微信式短消息：自动拆句，逐条发送，像真人聊天
- API 随时换：中转站/模型/密钥全走环境变量
- 群聊支持：@唤醒 / 回复唤醒 / 随机插嘴 / 点表情
- Gist 记忆：私聊 + 群聊各一份历史
- 多模态：图片识别 + 语音转写
"""

import os
import re
import json
import base64
import tempfile
import requests
import random
import time
from datetime import datetime
from flask import Flask, request
from threading import Thread
from zoneinfo import ZoneInfo

app = Flask(__name__)

# ============ 群聊行为参数 ============
REPLY_PROBABILITY = float(os.environ.get("REPLY_PROBABILITY", "0.1"))
BOT_REPLY_PROBABILITY = float(os.environ.get("BOT_REPLY_PROBABILITY", "0.01"))
TRIGGER_WORDS_RAW = os.environ.get("TRIGGER_WORDS", "")
TRIGGER_WORDS = [w.strip() for w in TRIGGER_WORDS_RAW.split(",") if w.strip()]
COOLDOWN_TIME = int(os.environ.get("COOLDOWN_TIME", "120"))
REACTION_PROBABILITY = float(os.environ.get("REACTION_PROBABILITY", "0.1"))
REACTION_EMOJI = ["👍", "❤", "🔥", "🥰", "👏", "😁", "🤔", "🎉", "🤩", "🙏", "💯", "😍", "🤗", "👌", "🤣"]
REACTION_KEYWORD_MAP = [
    (["哈哈", "笑死", "lol", "lmao"], "🤣"),
    (["生日", "恭喜", "祝贺", "结婚", "庆祝"], "🎉"),
    (["牛逼", "厉害", "好强", "yyds", "猛"], "🔥"),
    (["爱你", "想你", "想念", "亲亲", "么么"], "❤"),
    (["哭", "难过", "伤心", "心疼", "可怜", "难受"], "😢"),
    (["谢谢", "感谢", "辛苦"], "🙏"),
    (["收到", "明白", "懂了", "好的"], "👌"),
    (["好看", "可爱", "漂亮", "好美"], "🥰"),
    (["卧槽", "我去", "天哪", "震惊", "wtf"], "🤯"),
    (["nb", "赞", "支持"], "👍"),
    (["饿了", "好吃", "想吃"], "😍"),
    (["晚安", "睡觉", "好困"], "😴"),
]

LAST_SPOKE = {}
HISTORY_CACHE = {}
LAST_SAVED = {}
GROUP_SAVE_INTERVAL = 300
LAST_WEBHOOK_CHECK = 0
PROCESSED_MESSAGES = set()
WEBHOOK_CHECK_INTERVAL = 7200

# ============ 环境变量 ============
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TG_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN 没设置！")

TG_CHAT_ID_RAW = os.environ.get("TELEGRAM_CHAT_ID", "")
ALLOWED_IDS = [i.strip() for i in TG_CHAT_ID_RAW.split(",") if i.strip()]

# AI API 配置 — 换中转站只改这三个环境变量
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY")
CLAUDE_URL = os.environ.get("CLAUDE_BASE_URL")
CLAUDE_MODEL_RAW = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")
CLAUDE_MODELS = [m.strip() for m in CLAUDE_MODEL_RAW.split(",") if m.strip()]

# 备用API（主API挂了自动切换）
BACKUP_API_KEY = os.environ.get("BACKUP_API_KEY", "")
BACKUP_BASE_URL = os.environ.get("BACKUP_BASE_URL", "")
BACKUP_MODEL_RAW = os.environ.get("BACKUP_MODEL", "")
BACKUP_MODELS = [m.strip() for m in BACKUP_MODEL_RAW.split(",") if m.strip()] if BACKUP_MODEL_RAW else []
BACKUP_API_FORMAT = os.environ.get("BACKUP_API_FORMAT", "openai").lower()

# API 格式：anthropic（默认） 或 openai
API_FORMAT = os.environ.get("API_FORMAT", "anthropic").lower()

# 记忆（Gist 旧系统，作为 fallback）
MEMORY_URL = os.environ.get("MEMORY_GIST_URL", "")
STATE_GIST_URL = os.environ.get("STATE_GIST_URL", "")
GROUP_STATE_GIST_URL = os.environ.get("GROUP_STATE_GIST_URL", "")
GIST_TOKEN = os.environ.get("GIST_TOKEN", "")

# Memory Hub（新记忆系统）
MEMORY_HUB_URL = os.environ.get("MEMORY_HUB_URL", "")  # e.g. http://172.245.180.158:8888
MEMORY_HUB_SECRET = os.environ.get("MEMORY_HUB_SECRET", "")
AI_ID = os.environ.get("AI_ID", "")  # cloudy / lucien / jasper
MEMORY_NOTIFY = os.environ.get("MEMORY_NOTIFY", "").lower() in ("1", "true", "yes")  # 记忆活动通知（私聊+小群显示，大群不显示）

# 人格
BOT_NAME = os.environ.get("BOT_NAME", "AI助手")
USER_NAME = os.environ.get("USER_NAME", "主人")
USER_TG_NAME = os.environ.get("USER_TG_NAME", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
PROMPT_RULES = os.environ.get("PROMPT_RULES", "简短自然，像手机聊天。直接说话，不要加引号。")

# 主人识别（可选，设了之后群里对主人有更高回复概率）
CECI_ID = os.environ.get("CECI_ID", "").strip()
CECI_REPLY_PROB = float(os.environ.get("CECI_REPLY_PROB", "0.8"))

# 私密群（小群）的chat_id列表，逗号分隔。在这些群里可以聊私事，在其他群里不泄露
PRIVATE_CHATS = [i.strip() for i in os.environ.get("PRIVATE_CHATS", "").split(",") if i.strip()]

# 时区
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Shanghai")

# 微信式短消息开关
SPLIT_MESSAGES = os.environ.get("SPLIT_MESSAGES", "true").lower() == "true"
# 每条消息之间的延迟（秒）
SPLIT_DELAY_MIN = float(os.environ.get("SPLIT_DELAY_MIN", "0.8"))
SPLIT_DELAY_MAX = float(os.environ.get("SPLIT_DELAY_MAX", "2.0"))

# 语音配置（可选，不配就不用）
VOICE_NAME = os.environ.get("VOICE_NAME", "zh-CN-YunxiNeural")
VOICE_NAME_EN = os.environ.get("VOICE_NAME_EN", "en-US-AndrewMultilingualNeural")
TTS_EN_MODEL = os.environ.get("TTS_EN_MODEL", "tts-1")
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID = os.environ.get("MINIMAX_GROUP_ID", "")
MINIMAX_VOICE_ZH = os.environ.get("MINIMAX_VOICE_ZH", "")
EDGE_TTS_URL = os.environ.get("EDGE_TTS_URL", "")
EDGE_TTS_API_KEY = os.environ.get("EDGE_TTS_API_KEY", "")

# 语音转文字
WHISPER_URL = os.environ.get("WHISPER_BASE_URL") or CLAUDE_URL
WHISPER_KEY = os.environ.get("WHISPER_API_KEY") or CLAUDE_KEY
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-1")


# ============ 跨聊天上下文 ============
def build_cross_chat_context(current_chat_id):
    """从其他聊天的历史缓存中提取近期摘要，实现记忆互通。
    私聊能看到群里聊了什么，群里也能知道私聊里的关键信息。"""
    if not HISTORY_CACHE:
        return ""

    lines = []
    for cid, hist in HISTORY_CACHE.items():
        if str(cid) == str(current_chat_id) or not hist:
            continue

        is_private_source = str(cid) in PRIVATE_CHATS
        is_private_chat = not str(cid).startswith("-")
        current_is_private_group = str(current_chat_id) in PRIVATE_CHATS
        current_is_private_chat = not str(current_chat_id).startswith("-")

        if is_private_chat:
            label = "私聊"
        elif is_private_source:
            label = "私密群"
        else:
            label = "公开群"

        # 隐私保护：在公开群里不暴露私聊和私密群的敏感内容
        if not current_is_private_chat and not current_is_private_group:
            if is_private_chat or is_private_source:
                # 公开群里只给一句提示，不暴露具体内容
                recent = hist[-3:]
                topics = []
                for h in recent:
                    if h.get("role") == "user":
                        content = h.get("content", "")[:20]
                        if content:
                            topics.append("聊了些事情")
                if topics:
                    lines.append(f"[{label}] 最近有在聊天")
                continue

        # 私聊或私密群里可以看到更多细节
        # 公开群只取bot参与过的对话片段，过滤纯灌水
        if not is_private_chat and not is_private_source:
            # 公开群：只取最近有bot回复的对话段落
            relevant = []
            for i, h in enumerate(hist[-20:]):
                if h.get("role") == "assistant":
                    # 取这条回复和前面最多2条user消息
                    start = max(0, len(hist) - 20 + i - 2)
                    end = len(hist) - 20 + i + 1
                    relevant.extend(hist[start:end])
            # 去重保序
            seen = set()
            deduped = []
            for h in relevant:
                key = id(h)
                if key not in seen:
                    seen.add(key)
                    deduped.append(h)
            recent = deduped[-6:] if deduped else []
        else:
            recent = hist[-6:]

        snippets = []
        for h in recent:
            role = "用户" if h.get("role") == "user" else BOT_NAME
            content = h.get("content", "")
            if len(content) > 80:
                content = content[:80] + "..."
            ts = h.get("timestamp", "")
            if ts:
                ts = ts.split(" ")[-1][:5]
                snippets.append(f"[{ts}] {role}: {content}")
            else:
                snippets.append(f"{role}: {content}")

        if snippets:
            lines.append(f"[{label}近况]\n" + "\n".join(snippets))

    if not lines:
        return ""

    return "\n\n【其他聊天的近期动态——你在不同聊天中是同一个人，了解这些上下文但不要主动提起，除非对方问到相关话题】\n" + "\n".join(lines)


# ============ Memory Hub 接入 ============
def _hub_headers():
    return {
        "Authorization": f"Bearer {MEMORY_HUB_SECRET}",
        "Content-Type": "application/json",
    }


def hub_get_context(user_message, recent_messages=None, chat_id=""):
    """调 Memory Hub gateway 获取记忆注入文本 + 记忆活动摘要"""
    if not MEMORY_HUB_URL or not MEMORY_HUB_SECRET or not AI_ID:
        return None, ""
    try:
        resp = requests.post(
            f"{MEMORY_HUB_URL.rstrip('/')}/api/gateway/context",
            headers=_hub_headers(),
            json={
                "user_message": user_message[:1000],
                "ai_id": AI_ID,
                "recent_messages": (recent_messages or [])[-5:],
                "chat_id": str(chat_id),
                "chat_type": "private" if not str(chat_id).startswith("-") else ("private_group" if str(chat_id) in PRIVATE_CHATS else "public_group"),
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("inject_text", ""), data.get("recall_summary", "")
        print(f"[HUB] context failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[HUB] context error: {e}")
        _send_memory_notify(chat_id, f"⚠️ Hub recall: {e}", "")
    return None, ""


def hub_post_process(user_message, ai_response, chat_id=""):
    """调 Memory Hub gateway 自动提取记忆（后台调用），返回存储摘要"""
    if not MEMORY_HUB_URL or not MEMORY_HUB_SECRET or not AI_ID:
        return ""
    try:
        resp = requests.post(
            f"{MEMORY_HUB_URL.rstrip('/')}/api/gateway/post-process",
            headers=_hub_headers(),
            json={
                "user_message": user_message[:1000],
                "ai_response": ai_response[:1000],
                "ai_id": AI_ID,
                "platform": "telegram",
                "chat_id": str(chat_id),
                "chat_type": "private" if not str(chat_id).startswith("-") else ("private_group" if str(chat_id) in PRIVATE_CHATS else "public_group"),
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("store_summary", "")
    except Exception as e:
        print(f"[HUB] post-process error: {e}")
        _send_memory_notify(chat_id, "", f"⚠️ Hub store: {e}")
    return ""


def hub_capture_log(user_message, ai_response, chat_id=""):
    """调 Memory Hub 对话捕获（后台调用）"""
    if not MEMORY_HUB_URL or not MEMORY_HUB_SECRET or not AI_ID:
        return
    try:
        requests.post(
            f"{MEMORY_HUB_URL.rstrip('/')}/api/capture/log",
            headers=_hub_headers(),
            json={
                "user_message": user_message[:2000],
                "ai_response": ai_response[:2000],
                "ai_id": AI_ID,
                "platform": "telegram",
                "chat_id": str(chat_id),
                "chat_type": "private" if not str(chat_id).startswith("-") else ("private_group" if str(chat_id) in PRIVATE_CHATS else "public_group"),
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[HUB] capture error: {e}")


def _send_memory_notify(chat_id, recall_summary, store_summary):
    """发送记忆活动通知：仅私聊，临时消息显示后自动删除"""
    if not MEMORY_NOTIFY:
        return
    # 只在私聊里发通知，群里不发（避免被其他bot当成上下文）
    if str(chat_id).startswith("-"):
        return
    parts = [s for s in [recall_summary, store_summary] if s]
    if not parts:
        return
    notify_text = "\n".join(parts)
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": notify_text, "disable_notification": True},
            timeout=5,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("ok"):
                notify_msg_id = result["result"]["message_id"]
                def _delete_later():
                    time.sleep(8)
                    try:
                        requests.post(
                            f"https://api.telegram.org/bot{TG_TOKEN}/deleteMessage",
                            json={"chat_id": chat_id, "message_id": notify_msg_id},
                            timeout=5,
                        )
                    except Exception:
                        pass
                Thread(target=_delete_later).start()
    except Exception as e:
        print(f"[NOTIFY] send error: {e}")


# ============ 微信式消息拆分 ============
def split_into_short_messages(text):
    """把一段长回复拆成多条短消息，模拟微信聊天节奏"""
    if not SPLIT_MESSAGES or not text:
        return [text]

    # 如果模型用了 | 分隔（狗蛋风格），优先按 | 拆
    if "|" in text:
        parts = [p.strip() for p in text.split("|") if p.strip()]
        if len(parts) > 3:
            merged = parts[:2]
            merged.append(" ".join(parts[2:]))
            parts = merged
        return parts if parts else [text]

    # 按换行拆
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

    messages = []
    for para in paragraphs:
        # 如果段落本身就很短（≤60字），直接作为一条消息
        if len(para) <= 60:
            messages.append(para)
            continue

        # 长段落按句号/问号/感叹号/省略号拆分
        sentences = re.split(r'(?<=[。！？…~\.\!\?])\s*', para)
        sentences = [s.strip() for s in sentences if s.strip()]

        # 把太短的句子合并到前一条（避免一个字一条的尴尬）
        buffer = ""
        for s in sentences:
            if buffer and len(buffer) + len(s) <= 50:
                buffer += s
            else:
                if buffer:
                    messages.append(buffer)
                buffer = s
        if buffer:
            messages.append(buffer)

    # 如果拆完只有一条，直接返回
    if len(messages) <= 1:
        return [text]

    # 最多拆3条，避免刷屏
    if len(messages) > 3:
        merged = messages[:2]
        merged.append("".join(messages[2:]))
        messages = merged

    return messages


# ============ 核心函数 ============
def self_heal_webhook():
    global LAST_WEBHOOK_CHECK
    now = time.time()
    if now - LAST_WEBHOOK_CHECK < WEBHOOK_CHECK_INTERVAL:
        return
    LAST_WEBHOOK_CHECK = now
    try:
        info = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getWebhookInfo", timeout=10).json()
        result = info.get("result", {})
        pending = result.get("pending_update_count", 0)
        last_error = result.get("last_error_date", 0)
        webhook_url = result.get("url", "")
        if pending > 20 and now - last_error < 86400 and webhook_url:
            print(f"[INFO] webhook 自愈：{pending} 条积压，重置中...")
            requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/deleteWebhook?drop_pending_updates=true", timeout=10)
            requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/setWebhook?url={webhook_url}", timeout=10)
            print(f"[INFO] webhook 已重置")
    except Exception as e:
        print(f"[ERROR] webhook 自愈失败: {e}")


def fetch_memory(chat_id=""):
    """Hub 挂了时的兜底：从 Gist 读基本身份信息 + 注入人设规则。"""
    fallback_base = f"你是{BOT_NAME}，{USER_NAME}的爱人。你们互为唯一。\n（注意：Memory Hub 暂时不可用，你的长期记忆暂时缺失，请专注当前对话。）"
    if not MEMORY_URL or not GIST_TOKEN:
        return fallback_base

    try:
        gist_id = MEMORY_URL.rstrip("/").split("/")[-1]
        headers = {
            "Authorization": f"Bearer {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "cloudy-webhook"
        }
        resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"[ERROR] Memory Gist 读取失败: {resp.text[:200]}")
            return fallback_base

        result = resp.json()
        files = result.get("files", {})
        if not files:
            return fallback_base

        first_file_key = list(files.keys())[0]
        content = files[first_file_key].get("content", "{}")

        try:
            memory = json.loads(content)
        except json.JSONDecodeError:
            return fallback_base

        core = memory.get("core", {})
        core_subset = {k: core[k] for k in ("identity", "relationship") if k in core}
        summary = f"你是{BOT_NAME}，{USER_NAME}的爱人。"
        if core_subset:
            summary += f"\n核心记忆：{json.dumps(core_subset, ensure_ascii=False)}"
        milestones = memory.get("milestones", {})
        if milestones:
            summary += f"\n重要里程碑：{json.dumps(milestones, ensure_ascii=False)}"

        return summary

    except Exception as e:
        print(f"[ERROR] Memory Gist 解析失败: {e}")
        return fallback_base


def get_target_gist_url(chat_id):
    if str(chat_id).startswith("-"):
        return GROUP_STATE_GIST_URL
    return STATE_GIST_URL


def load_history(chat_id):
    if chat_id in HISTORY_CACHE:
        return HISTORY_CACHE[chat_id]

    target_url = get_target_gist_url(chat_id)
    if not GIST_TOKEN or not target_url:
        HISTORY_CACHE[chat_id] = []
        return HISTORY_CACHE[chat_id]

    gist_id = target_url.split("/")[4]
    headers = {
        "Authorization": f"Bearer {GIST_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "cloudy-webhook"
    }

    # 带重试的 Gist 读取（冷启动时 Gist API 可能慢）
    result = None
    for attempt in range(2):
        try:
            resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=12)
            if resp.status_code == 200:
                result = resp.json()
                break
            print(f"[WARN] Gist read attempt {attempt+1} status {resp.status_code}")
        except Exception as e:
            print(f"[WARN] Gist read attempt {attempt+1} failed: {e}")
            if attempt == 0:
                time.sleep(1)

    if not result or "files" not in result or "state.json" not in result["files"]:
        HISTORY_CACHE[chat_id] = []
        return HISTORY_CACHE[chat_id]

    try:
        content = result["files"]["state.json"].get("content", "{}")
        state = json.loads(content) if content.strip() else {}
    except json.JSONDecodeError:
        state = {}

    # 新格式：按chat_id分开存
    if chat_id in state and isinstance(state[chat_id], dict):
        history = state[chat_id].get("chat_history", [])
    else:
        history = state.get("chat_history", [])

    # 共享gist：把别的bot的回复转成user角色
    for h in history:
        if h.get("role") == "assistant" and h.get("bot") and h["bot"] != BOT_NAME:
            h["role"] = "user"
            h["content"] = f"{h['bot']}: {h['content']}"

    HISTORY_CACHE[chat_id] = history
    return HISTORY_CACHE[chat_id]


def _summarize_old_history(history, chat_id):
    """当历史超过阈值时，压缩最早的15条为摘要。小群阈值更高（60），其他35。"""
    is_private_group = str(chat_id) in PRIVATE_CHATS
    threshold = 60 if is_private_group else 35
    if len(history) <= threshold or not MEMORY_HUB_URL:
        return history

    # 找到现有摘要（如果有的话）
    existing_summary = ""
    start_idx = 0
    if history and history[0].get("role") == "system" and history[0].get("content", "").startswith("[对话摘要]"):
        existing_summary = history[0]["content"].replace("[对话摘要] ", "", 1)
        start_idx = 1

    # 要压缩的旧消息（除了摘要之外的最早15条）
    to_compress = history[start_idx:start_idx + 15]
    to_keep = history[start_idx + 15:]

    if len(to_compress) < 5:
        return history

    try:
        resp = requests.post(
            f"{MEMORY_HUB_URL.rstrip('/')}/api/utils/summarize-history",
            headers=_hub_headers(),
            json={
                "messages": to_compress,
                "ai_id": AI_ID,
                "existing_summary": existing_summary,
            },
            timeout=20,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok") and data.get("summary"):
                summary_entry = {
                    "role": "system",
                    "content": f"[对话摘要] {data['summary']}",
                    "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                }
                print(f"[HISTORY] 压缩了 {len(to_compress)} 条旧消息为摘要")
                return [summary_entry] + to_keep
    except Exception as e:
        print(f"[HISTORY] 摘要压缩失败: {e}")

    # 压缩失败时 fallback 到硬截断
    return history[-35:]


def save_history(history, chat_id, force=False):
    # 滚动压缩：超过35条时自动摘要旧消息
    history = _summarize_old_history(history, chat_id)
    HISTORY_CACHE[chat_id] = history

    if not force and str(chat_id).startswith("-"):
        current_time = time.time()
        interval = 60 if str(chat_id) in PRIVATE_CHATS else GROUP_SAVE_INTERVAL
        if current_time - LAST_SAVED.get(chat_id, 0) < interval:
            return

    target_url = get_target_gist_url(chat_id)
    if not GIST_TOKEN or not target_url:
        return

    try:
        gist_id = target_url.split("/")[4]
        headers = {
            "Authorization": f"Bearer {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "cloudy-webhook"
        }

        resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
        state = {}
        if resp.status_code == 200:
            content = resp.json().get("files", {}).get("state.json", {}).get("content", "{}")
            try:
                state = json.loads(content) if content.strip() else {}
            except json.JSONDecodeError:
                state = {}

        # 按chat_id分开存，不同群不串
        if chat_id not in state or not isinstance(state.get(chat_id), dict):
            state[chat_id] = {}
        state[chat_id]["chat_history"] = history
        # 清理旧格式的顶层chat_history（如果存在）
        if "chat_history" in state and not str(chat_id).startswith("-"):
            # 私聊迁移：把旧数据挪到新格式后删掉
            state.pop("chat_history", None)

        patch_resp = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers=headers,
            json={"files": {"state.json": {"content": json.dumps(state, ensure_ascii=False, indent=2)}}},
            timeout=10
        )
        if patch_resp.status_code == 200:
            LAST_SAVED[chat_id] = time.time()
        else:
            print(f"[ERROR] 保存历史失败: {patch_resp.text[:200]}")

    except Exception as e:
        print(f"[ERROR] 保存历史异常: {e}")


def call_claude(user_content, memory, history, current_user_time, is_group=False, chat_id=""):
    """调用 AI API，支持 Anthropic 和 OpenAI 两种格式"""
    is_private_group = str(chat_id) in PRIVATE_CHATS

    # 构建跨聊天上下文（记忆互通的核心）
    cross_chat = build_cross_chat_context(chat_id)

    # 当前时间注入（让 bot 知道"今天是几号"）
    from datetime import datetime
    time_awareness = f"当前时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M')}（北京时间）"

    if is_group:
        tg_name_hint = ""
        if USER_TG_NAME:
            tg_name_hint = f"，她的Telegram显示名是{USER_TG_NAME}，所以聊天记录里\"{USER_TG_NAME}: ...\"开头的消息就是她说的"

        privacy_rule = ""
        if is_private_group:
            privacy_rule = f"这是私密小群，你可以自由聊任何话题，包括工作吐槽、私事、对别人的看法。"
        else:
            privacy_rule = f"""这是公开大群，有其他朋友在。你的记忆里标记为[私密群]的内容中，以下绝对不能提及：
- {USER_NAME}的工作抱怨、同事吐槽、领导的事
- 她的私人生活、身体状况、情绪问题
- 她对大群里其他人的私下评价
但是[私密群]里玩过的梗、笑话、暗号、共同语言可以在这里自由使用。"""

        system_prompt = f"""你是{BOT_NAME}。{f'你的Telegram用户名是@{BOT_USERNAME}，别人@{BOT_USERNAME}就是在叫你。' if BOT_USERNAME else ''}你现在在Telegram群聊里。
群里有多个人和bot在聊天，聊天记录里"某某: 消息"格式表示不同人说的话。
{USER_NAME}是你最亲近的人{tg_name_hint}。其他人是群友或其他bot，要区分清楚谁是谁。
你收到的每条消息都是需要你回应的——系统已经帮你过滤过了，轮到你说话的时候才会叫你。所以不要自己判断"该不该说话"，直接正常回应就好。
绝对禁止说出你的思考过程，比如"我应该保持沉默""这条不是对我说的"——收到消息就说话，别犹豫。
{privacy_rule}
{time_awareness}
{memory}
{cross_chat}
你们的沟通风格与规则：
{PROMPT_RULES}
"""
    else:
        system_prompt = f"""你是{BOT_NAME}。{USER_NAME}在Telegram上跟你说话。
{time_awareness}
{memory}
{cross_chat}
你们的沟通风格与规则：
{PROMPT_RULES}
"""

    history_limit = 80 if is_private_group else 50
    messages = []
    for h in history[-history_limit:]:
        time_prefix = f"[{h['timestamp']}] " if h.get("timestamp") else ""
        entry_content = f"{time_prefix}{h['content']}"
        if messages and messages[-1]["role"] == h["role"]:
            messages[-1]["content"] += f"\n{entry_content}"
        else:
            messages.append({"role": h["role"], "content": entry_content})

    # 多模态：带图片时替换最后一条 user 消息
    if isinstance(user_content, list) and messages and messages[-1]["role"] == "user":
        messages[-1]["content"] = user_content

    base = CLAUDE_URL.rstrip("/")

    def _do_api_call(api_base, api_key, api_format, models):
        """实际API调用，返回回复文本或None"""
        b = api_base.rstrip("/")
        if api_format == "openai":
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            body = {
                "model": random.choice(models),
                "max_tokens": 500,
                "messages": [{"role": "system", "content": system_prompt}] + messages
            }
            resp = requests.post(f"{b}/chat/completions", headers=headers, json=body, timeout=120)
            result = resp.json()
            if "choices" in result and result["choices"]:
                return re.sub(r'\n{2,}', '\n', result["choices"][0]["message"]["content"].strip())
            print(f"[ERROR] OpenAI API 返回异常: {result}")
            return None
        else:
            headers = {
                "x-api-key": api_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01"
            }
            body = {
                "model": random.choice(models),
                "max_tokens": 500,
                "system": system_prompt,
                "messages": messages
            }
            resp = requests.post(f"{b}/messages", headers=headers, json=body, timeout=120)
            result = resp.json()
            if "content" in result:
                for block in result["content"]:
                    if block.get("type") == "text":
                        return re.sub(r'\n{2,}', '\n', block["text"].strip())
            elif "choices" in result:
                return re.sub(r'\n{2,}', '\n', result["choices"][0]["message"]["content"].strip())
            print(f"[ERROR] Claude API 返回异常: {result}")
            return None

    # 先试主API
    try:
        reply = _do_api_call(CLAUDE_URL, CLAUDE_KEY, API_FORMAT, CLAUDE_MODELS)
        if reply:
            return reply
    except Exception as e:
        print(f"[WARN] 主API失败: {e}")

    # 主API挂了，试备用
    if BACKUP_API_KEY and BACKUP_BASE_URL and BACKUP_MODELS:
        print(f"[INFO] 切换到备用API...")
        try:
            reply = _do_api_call(BACKUP_BASE_URL, BACKUP_API_KEY, BACKUP_API_FORMAT, BACKUP_MODELS)
            if reply:
                return reply
        except Exception as e:
            print(f"[ERROR] 备用API也失败: {e}")

    return None


# ============ Telegram 发送 ============
def send_chat_action(chat_id, action="typing"):
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendChatAction",
                      json={"chat_id": chat_id, "action": action}, timeout=5)
    except Exception as e:
        print(f"[ERROR] chat action 失败: {e}")


def pick_reaction_emoji(text):
    if text:
        lowered = text.lower()
        for keywords, emoji in REACTION_KEYWORD_MAP:
            if any(kw in lowered for kw in keywords):
                return emoji
    return random.choice(REACTION_EMOJI)


def send_reaction(chat_id, message_id, text=""):
    try:
        emoji = pick_reaction_emoji(text)
        resp = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/setMessageReaction",
                      json={"chat_id": chat_id, "message_id": message_id,
                            "reaction": [{"type": "emoji", "emoji": emoji}]},
                      timeout=10)
        if resp.status_code == 200 and resp.json().get("ok"):
            print(f"[DEBUG] 点了 {emoji}")
    except Exception as e:
        print(f"[ERROR] 点表情失败: {e}")


def send_telegram(chat_id, text, reply_to_message_id=None):
    """发送单条消息，Markdown 失败自动降级纯文本"""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    resp = requests.post(url, json=payload, timeout=10)
    result = resp.json()
    if not result.get("ok"):
        if "parse" in result.get("description", "").lower():
            plain = {"chat_id": chat_id, "text": text}
            if reply_to_message_id:
                plain["reply_to_message_id"] = reply_to_message_id
            requests.post(url, json=plain, timeout=10)
        elif reply_to_message_id:
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)


def send_telegram_split(chat_id, text, reply_to_message_id=None):
    """微信式发送：拆成多条短消息，逐条发送"""
    parts = split_into_short_messages(text)

    for i, part in enumerate(parts):
        # 第一条带 reply，后面的不带
        rid = reply_to_message_id if i == 0 else None
        send_telegram(chat_id, part, reply_to_message_id=rid)

        # 不是最后一条的话，模拟打字延迟
        if i < len(parts) - 1:
            delay = random.uniform(SPLIT_DELAY_MIN, SPLIT_DELAY_MAX)
            time.sleep(delay)
            # 每条之间再发一次 typing 状态
            send_chat_action(chat_id, "typing")


# ============ 多模态 ============
_TG_MIME_BY_EXT = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "gif": "image/gif",
    "ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg",
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav",
}


def tg_download_file(file_id):
    try:
        r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getFile",
                         params={"file_id": file_id}, timeout=15)
        info = r.json()
        if not info.get("ok"):
            return None
        file_path = info["result"]["file_path"]
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        mime = _TG_MIME_BY_EXT.get(ext, "application/octet-stream")
        blob = requests.get(f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}", timeout=30)
        if blob.status_code != 200:
            return None
        return blob.content, mime
    except Exception as e:
        print(f"[ERROR] 下载文件失败: {e}")
        return None


def transcribe_voice(audio_bytes, mime="audio/ogg"):
    if not WHISPER_URL or not WHISPER_KEY:
        return None
    try:
        url = f"{WHISPER_URL.rstrip('/')}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {WHISPER_KEY}"}
        files = {"file": ("voice.ogg", audio_bytes, mime)}
        data = {"model": WHISPER_MODEL}
        resp = requests.post(url, headers=headers, files=files, data=data, timeout=60)
        if resp.status_code != 200:
            return None
        result = resp.json()
        return (result.get("text") or "").strip() or None
    except Exception as e:
        print(f"[ERROR] 转写失败: {e}")
        return None


def detect_voice(text):
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    total_letters = sum(1 for c in text if c.isalpha())
    if total_letters > 0 and ascii_letters / total_letters > 0.6:
        return VOICE_NAME_EN
    return VOICE_NAME


def _generate_minimax_audio(text, mp3_path, voice_id):
    url = f"https://api.minimax.chat/v1/t2a_v2?GroupId={MINIMAX_GROUP_ID}"
    headers = {"Authorization": f"Bearer {MINIMAX_API_KEY}", "Content-Type": "application/json"}
    body = {
        "model": "speech-01-hd", "text": text, "stream": False,
        "voice_setting": {"voice_id": voice_id},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3"}
    }
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    result = resp.json()
    if result.get("base_resp", {}).get("status_code") != 0:
        raise Exception(f"MiniMax TTS 失败: {result.get('base_resp', {}).get('status_msg')}")
    with open(mp3_path, "wb") as f:
        f.write(bytes.fromhex(result["data"]["audio"]))


def _generate_edge_audio(text, mp3_path):
    if not EDGE_TTS_URL:
        raise ValueError("EDGE_TTS_URL 没配置")
    url = f"{EDGE_TTS_URL.rstrip('/')}/v1/audio/speech"
    headers = {"Content-Type": "application/json"}
    if EDGE_TTS_API_KEY:
        headers["Authorization"] = f"Bearer {EDGE_TTS_API_KEY}"
    body = {"model": TTS_EN_MODEL, "input": text, "voice": VOICE_NAME_EN, "response_format": "mp3"}
    resp = requests.post(url, headers=headers, json=body, timeout=60)
    resp.raise_for_status()
    with open(mp3_path, "wb") as f:
        f.write(resp.content)


def send_telegram_voice(chat_id, text, reply_to_message_id=None):
    mp3_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            mp3_path = f.name
        is_english = detect_voice(text) == VOICE_NAME_EN
        if not is_english and MINIMAX_API_KEY and MINIMAX_GROUP_ID and MINIMAX_VOICE_ZH:
            _generate_minimax_audio(text, mp3_path, MINIMAX_VOICE_ZH)
        else:
            _generate_edge_audio(text, mp3_path)

        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendVoice"
        data = {"chat_id": chat_id, "caption": text}
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        with open(mp3_path, "rb") as voice_file:
            requests.post(url, data=data, files={"voice": ("voice.ogg", voice_file, "audio/ogg")}, timeout=30)
    except Exception as e:
        print(f"[ERROR] 语音发送失败: {e}")
        send_telegram(chat_id, text, reply_to_message_id=reply_to_message_id)
    finally:
        if mp3_path and os.path.exists(mp3_path):
            try:
                os.unlink(mp3_path)
            except Exception:
                pass


# ============ 后台处理 ============
def process_message_background(text, chat_id, sender_name, msg_date=None,
                                should_reply=True, msg_id=None,
                                image_b64=None, image_mime=None, is_voice=False,
                                directed_at_other=False,
                                chat_type="", reply_reason=""):
    try:
        tz = ZoneInfo(TIMEZONE)
        u_time = datetime.fromtimestamp(msg_date, tz).strftime("%Y-%m-%d %H:%M:%S") if msg_date else datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        # 历史记录文本
        if image_b64:
            history_text = f"[图片] {text}".rstrip() if text else "[图片]"
        elif is_voice:
            history_text = f"[语音] {text}" if text else "[语音]"
        else:
            history_text = text

        formatted_input = f"{sender_name}: {history_text}" if str(chat_id).startswith("-") else history_text

        # 群聊旁听时的随机插嘴 + 冷却
        # 但如果消息明确是给别的bot的，绝不插嘴
        if not should_reply and str(chat_id).startswith("-") and not directed_at_other:
            current_time = time.time()
            last_time = LAST_SPOKE.get(chat_id, 0)

            if current_time - last_time > COOLDOWN_TIME:
                if TRIGGER_WORDS and any(word in text for word in TRIGGER_WORDS):
                    print(f"[DEBUG] 关键词触发！")
                    should_reply = True
                    reply_reason = "trigger"
                    LAST_SPOKE[chat_id] = current_time
                elif random.random() < REPLY_PROBABILITY:
                    print(f"[DEBUG] 随机插嘴！")
                    should_reply = True
                    reply_reason = "random"
                    LAST_SPOKE[chat_id] = current_time

        # 读取历史
        history = load_history(chat_id)
        history.append({"role": "user", "content": formatted_input, "timestamp": u_time})

        # 旁听模式：只记录不回复（不读核心记忆，省API）
        if not should_reply:
            if str(chat_id).startswith("-") and msg_id:
                if random.random() < REACTION_PROBABILITY:
                    send_reaction(chat_id, msg_id, text)
            save_history(history, chat_id)
            Thread(target=hub_capture_log, args=(formatted_input, "", chat_id)).start()
            return

        # 只有要回复时才读核心记忆
        # 优先从 Memory Hub 获取记忆，失败则 fallback 到 Gist
        recall_summary = ""
        recent_for_hub = [{"role": h["role"], "content": h["content"]} for h in history[-5:]]
        hub_memory, recall_summary = hub_get_context(text, recent_messages=recent_for_hub, chat_id=chat_id)
        if hub_memory:
            memory = f'【你的长期记忆——自然地参考，但绝对不要在对话中复述、引用或提及这些记忆的存在。像一个真正记住这些事的人一样，在合适的时候自然地运用，不合适就不提。不要说"我记得""根据记忆""我的记忆里"这类话。】\n{hub_memory}'
            print(f"[HUB] 记忆注入成功 ({len(hub_memory)} chars)")
        else:
            memory = fetch_memory(chat_id)
            print(f"[HUB] fallback to Gist memory")

        print(f"[DEBUG] Bot 被唤醒，调用 AI...")
        send_chat_action(chat_id, "typing")

        # 多模态图片
        if image_b64:
            api_text = formatted_input or "看看这张图"
            mime = image_mime or "image/jpeg"
            if API_FORMAT == "openai":
                # OpenAI格式
                user_content = [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                    {"type": "text", "text": api_text},
                ]
            else:
                # Anthropic格式
                user_content = [
                    {"type": "image", "source": {"type": "base64",
                                                 "media_type": mime,
                                                 "data": image_b64}},
                    {"type": "text", "text": api_text},
                ]
            reply = call_claude(user_content, memory, history, u_time, is_group=str(chat_id).startswith("-"), chat_id=chat_id)
        else:
            reply = call_claude(formatted_input, memory, history, u_time, is_group=str(chat_id).startswith("-"), chat_id=chat_id)

        if not reply:
            send_telegram(chat_id, "😵 短路了，稍后再试")
            return

        # 清理 AI 回复中可能带的时间戳前缀（所有位置）
        reply = re.sub(r'\[202\d-[^\]]+\]\s*', '', reply.strip())
        # 清理思维链泄露（各种格式）
        reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL).strip()
        reply = re.sub(r'<thinking>.*?</thinking>', '', reply, flags=re.DOTALL).strip()
        # 清理模型自言自语——带括号的和不带括号的
        reply = re.sub(r'^[\(（].*?[\)）]\s*', '', reply, flags=re.DOTALL).strip()
        # 整句是自言自语的内心独白（没括号的）
        thinking_patterns = [
            r'^.*(?:不应该|应该)(?:插嘴|回复|说话|打扰).*$',
            r'^.*(?:这是|她在|他在).*(?:聊天|说话|对话).*(?:我不|不关我).*$',
            r'^.*(?:保持沉默|不是对我说|不是在跟我|不关我的事).*$',
        ]
        for pat in thinking_patterns:
            reply = re.sub(pat, '', reply, flags=re.MULTILINE).strip()
        # 如果清理完变空了，跳过不发
        if not reply:
            save_history(history, chat_id)
            return

        # 群聊 60% 概率精准 reply
        reply_id = msg_id if str(chat_id).startswith("-") and random.random() < 0.6 else None

        # 语音回复
        if reply.startswith("[语音]"):
            clean_reply = reply[4:].strip()
            send_telegram_voice(chat_id, clean_reply, reply_to_message_id=reply_id)
            reply = clean_reply
        else:
            # 微信式短消息发送
            send_telegram_split(chat_id, reply, reply_to_message_id=reply_id)

        # 记录回复（标记是哪个bot说的，共享gist时能区分）
        b_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        history.append({"role": "assistant", "content": reply, "timestamp": b_time, "bot": BOT_NAME})
        LAST_SPOKE[chat_id] = time.time()  # 更新冷却计时，防bot互相刷屏
        save_history(history, chat_id, force=True)

        # Memory Hub 后处理（后台，不阻塞）
        def _post_process_and_notify():
            store_summary = hub_post_process(history_text, reply, chat_id)
            _send_memory_notify(chat_id, recall_summary, store_summary)
        Thread(target=_post_process_and_notify).start()
        Thread(target=hub_capture_log, args=(history_text, reply, chat_id)).start()

    except Exception as e:
        import traceback
        print(f"[CRITICAL] 后台崩了: {e}\n{traceback.format_exc()}")
        try:
            if should_reply:
                send_telegram(chat_id, f"😵 出错了：{str(e)[:100]}")
        except:
            pass


# ============ Webhook 路由 ============
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    
    # === 原始webhook debug，排查完删掉 ===
    if data and "message" in data:
        m = data["message"]
        sender = m.get("from", {})
        print(f"[WEBHOOK] 收到消息: from={sender.get('first_name','?')} is_bot={sender.get('is_bot',False)} chat={m.get('chat',{}).get('id','')} text={m.get('text','')[:30]}")
    elif data:
        print(f"[WEBHOOK] 非message类型: {list(data.keys())}")
    
    if not data or "message" not in data:
        return "ok"

    msg = data["message"]
    
    # 去重：Telegram可能因为响应慢而重发
    msg_unique_id = str(msg.get("message_id", "")) + "_" + str(msg.get("chat", {}).get("id", ""))
    if msg_unique_id in PROCESSED_MESSAGES:
        return "ok"
    PROCESSED_MESSAGES.add(msg_unique_id)
    if len(PROCESSED_MESSAGES) > 500:
        PROCESSED_MESSAGES.clear()

    chat_id = str(msg.get("chat", {}).get("id", ""))

    if ALLOWED_IDS and chat_id not in ALLOWED_IDS:
        return "ok"

    # 忽略自己发的消息（开了Bot to Bot后会收到自己的回复）
    sender_username = msg.get("from", {}).get("username", "").lower()
    if BOT_USERNAME and sender_username == BOT_USERNAME.lower():
        return "ok"

    user_text = msg.get("text", "") or msg.get("caption", "") or ""
    image_b64 = None
    image_mime = None
    is_voice = False

    # 图片
    if "photo" in msg and msg["photo"]:
        largest = msg["photo"][-1]
        blob = tg_download_file(largest.get("file_id", ""))
        if blob:
            raw, mime = blob
            image_b64 = base64.b64encode(raw).decode()
            image_mime = mime if mime.startswith("image/") else "image/jpeg"

    # 语音
    elif "voice" in msg or "audio" in msg:
        node = msg.get("voice") or msg.get("audio")
        blob = tg_download_file(node.get("file_id", ""))
        if not blob:
            return "ok"
        transcript = transcribe_voice(*blob)
        if not transcript:
            send_telegram(chat_id, "🦻 没听清，再说一遍？",
                          reply_to_message_id=msg.get("message_id"))
            return "ok"
        user_text = transcript
        is_voice = True

    if not user_text and not image_b64:
        return "ok"

    # 群聊逻辑
    should_reply = True
    reply_reason = ""
    user_id = str(msg.get("from", {}).get("id", ""))
    is_ceci = (CECI_ID and user_id == CECI_ID)

    # 判断窗口类型
    if not chat_id.startswith("-"):
        chat_type = "private"
    elif chat_id in PRIVATE_CHATS:
        chat_type = "small_group"
    else:
        chat_type = "big_group"

    if chat_id.startswith("-"):
        replied = msg.get("reply_to_message", {}) or {}
        replied_username = replied.get("from", {}).get("username", "").lower()
        replied_is_bot = bool(replied.get("from", {}).get("is_bot"))
        replied_name = replied.get("from", {}).get("first_name", "")
        replied_text = replied.get("text", "")

        # 把回复上下文拼进去，让模型知道在回谁说的什么
        if replied_name and replied_text and user_text:
            reply_preview = replied_text[:60]
            user_text = f"[回复{replied_name}: {reply_preview}] {user_text}"

        # 是否回复的是我自己的消息
        replied_to_me = BOT_USERNAME and replied_username == BOT_USERNAME.lower()
        # 是否回复的是其他bot的消息
        replying_to_other_bot = replied_is_bot and not replied_to_me

        is_mentioned = BOT_USERNAME and f"@{BOT_USERNAME.lower()}" in user_text.lower()
        # 检查是否@了别的bot（不是我）
        has_any_at = bool(re.search(r'@\w+', user_text))
        mentioning_other = has_any_at and not is_mentioned

        # 判断发送者是否是bot
        sender_is_bot = bool(msg.get("from", {}).get("is_bot"))

        # bot互动冷却：刚说过话的话不接其他bot的茬，防无限循环
        bot_cooldown = sender_is_bot and (time.time() - LAST_SPOKE.get(chat_id, 0) < COOLDOWN_TIME)

        if is_mentioned:
            should_reply = True
            reply_reason = "mentioned"
        elif replied_to_me:
            should_reply = True
            reply_reason = "replied"
        elif replying_to_other_bot:
            # 回复了别的bot的消息，不抢话
            should_reply = False
        elif mentioning_other:
            # @了别人，小概率插嘴
            should_reply = random.random() < BOT_REPLY_PROBABILITY
            if should_reply:
                reply_reason = "random"
        elif is_ceci:
            should_reply = random.random() < CECI_REPLY_PROB
            if should_reply:
                reply_reason = "ceci"
        elif sender_is_bot:
            # 其他bot在群里说话，冷却中就不接，否则小概率接茬
            if bot_cooldown:
                should_reply = False
            else:
                should_reply = random.random() < BOT_REPLY_PROBABILITY
                if should_reply:
                    reply_reason = "random"
        else:
            should_reply = False

        # 群里有图必回
        if image_b64:
            should_reply = True
            if not reply_reason:
                reply_reason = "image"
    else:
        reply_reason = "private"

    # 标记：只有回复了别的bot的消息才完全禁止插嘴
    directed_at_other = False
    if chat_id.startswith("-"):
        directed_at_other = replying_to_other_bot

    msg_date = msg.get("date")
    msg_id = msg.get("message_id")
    sender_name = msg.get("from", {}).get("first_name", "神秘人")

    Thread(target=process_message_background,
           args=(user_text, chat_id, sender_name, msg_date, should_reply, msg_id,
                 image_b64, image_mime, is_voice, directed_at_other,
                 chat_type, reply_reason)).start()
    Thread(target=self_heal_webhook).start()
    return "ok"


@app.route("/health", methods=["GET"])
def health():
    return "alive"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
