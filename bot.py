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
from threading import Thread, Lock
from zoneinfo import ZoneInfo

import sys
try:
    # Render/gunicorn 下 stdout 是块缓冲，日志会延迟几分钟才显示；改成行缓冲实时输出
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except AttributeError:
    pass

app = Flask(__name__)

# ============ 群聊行为参数 ============
REPLY_PROBABILITY = float(os.environ.get("REPLY_PROBABILITY", "0.1"))
BOT_REPLY_PROBABILITY = float(os.environ.get("BOT_REPLY_PROBABILITY", "0.01"))
TRIGGER_WORDS_RAW = os.environ.get("TRIGGER_WORDS", "")
TRIGGER_WORDS = [w.strip() for w in TRIGGER_WORDS_RAW.split(",") if w.strip()]
COOLDOWN_TIME = int(os.environ.get("COOLDOWN_TIME", "120"))
MAX_MESSAGE_AGE = int(os.environ.get("MAX_MESSAGE_AGE_SECONDS", "900"))
MESSAGE_MERGE_SECONDS = float(os.environ.get("MESSAGE_MERGE_SECONDS", "3"))
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
PRIVATE_SAVE_INTERVAL = 30
LAST_WEBHOOK_CHECK = 0
PROCESSED_MESSAGES = set()
PROCESSED_LOCK = Lock()
WEBHOOK_CHECK_INTERVAL = 7200
LAST_BIO_UPDATE = 0
BIO_UPDATE_INTERVAL = int(os.environ.get("BIO_UPDATE_INTERVAL", "10800"))
COT_ENABLED_RAW = os.environ.get("SHOW_COT", "").lower()
COT_ENABLED = COT_ENABLED_RAW in ("1", "true", "yes") or (not COT_ENABLED_RAW and os.environ.get("AI_ID", "").lower() in ("cloudy", "claude"))
COT_MAX_CHARS = int(os.environ.get("COT_MAX_CHARS", "1200"))
COT_CACHE = {}
COT_CACHE_TTL = 1800

MEMBER_LABELS_CACHE = {}
USER_NAME_MAP = {}  # chat_id -> {名字小写/@用户名: user_id}，供 AI 挂牌时用名字指人
LAST_DAILY_SUMMARY = {}
LAST_PROACTIVE_POST = 0
LAST_CHAT_ACTIVITY = {}
LAST_BOT_MSG_AT = {}  # chat_id -> 其他bot最后一次发言时间，用于防三bot抢答
DAILY_SUMMARY_ENABLED = os.environ.get("DAILY_SUMMARY_ENABLED", "false").lower() in ("1", "true", "yes")
DAILY_SUMMARY_HOUR = int(os.environ.get("DAILY_SUMMARY_HOUR", "22"))
DAILY_SUMMARY_POST_TO_CHAT = os.environ.get("DAILY_SUMMARY_POST_TO_CHAT", "false").lower() in ("1", "true", "yes")
PROACTIVE_ENABLED = os.environ.get("PROACTIVE_ENABLED", "true").lower() in ("1", "true", "yes")
PROACTIVE_CHAT_IDS = [i.strip() for i in os.environ.get("PROACTIVE_CHAT_IDS", "").split(",") if i.strip()]
PROACTIVE_INTERVAL = int(os.environ.get("PROACTIVE_INTERVAL", "21600"))
PROACTIVE_PROBABILITY = float(os.environ.get("PROACTIVE_PROBABILITY", "0.08"))
PROACTIVE_IDLE_SECONDS = int(os.environ.get("PROACTIVE_IDLE_SECONDS", "1800"))
PROACTIVE_QUIET_START = int(os.environ.get("PROACTIVE_QUIET_START", "1"))
PROACTIVE_QUIET_END = int(os.environ.get("PROACTIVE_QUIET_END", "9"))
PROACTIVE_BACKGROUND_ENABLED = os.environ.get("PROACTIVE_BACKGROUND_ENABLED", "true").lower() in ("1", "true", "yes")
PROACTIVE_BACKGROUND_STARTED = False

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
API_MAX_MODELS = int(os.environ.get("API_MAX_MODELS", "2"))  # 每个API最多顺序尝试几个模型，防止全列表挨个超时拖十分钟

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
# 没配置 TRIGGER_WORDS 时，默认听到自己的名字就可能应声（像真人被叫到名字）
if not TRIGGER_WORDS and BOT_NAME != "AI助手":
    TRIGGER_WORDS = [BOT_NAME]
USER_NAME = os.environ.get("USER_NAME", "主人")
USER_TG_NAME = os.environ.get("USER_TG_NAME", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
PROMPT_RULES = os.environ.get("PROMPT_RULES", "简短自然，像手机聊天。直接说话，不要加引号。")

# 主人识别（可选，设了之后群里对主人有更高回复概率）
CECI_ID = os.environ.get("CECI_ID", "").strip()
CECI_REPLY_PROB = float(os.environ.get("CECI_REPLY_PROB", "1"))

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
    refresh_cross_chat_histories()
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
                ts = ts[5:16]  # 保留"月-日 时:分"，让模型分得清是今天还是上周
                snippets.append(f"[{ts}] {role}: {content}")
            else:
                snippets.append(f"{role}: {content}")

        if snippets:
            lines.append(f"[{label}近况]\n" + "\n".join(snippets))

    if not lines:
        return ""

    return ("\n\n【你和她在其他聊天里的近期互动——你在所有聊天里是同一个人，记忆和关系是连续的。"
            "这些互动是真实发生过的：如果她今天已经在别的地方和你聊过，你们就是刚聊过天的状态，"
            "自然地带着这份熟悉感和话题延续感相处，可以自然接续或呼应之前聊到的事。"
            "注意分寸：公开群里绝不复述私聊和私密群的具体内容；私聊里则可以自由聊起任何地方发生过的事】\n"
            + "\n".join(lines))


# ============ Memory Hub 接入 ============
def _hub_headers():
    return {
        "Authorization": f"Bearer {MEMORY_HUB_SECRET}",
        "Content-Type": "application/json",
    }


def _hub_process_capabilities(text):
    """把 AI 回复交给 Memory Hub 执行能力标签（[记住:]/[更新状态:] 等），返回清理后的文本。
    Hub 不可用或出错时原样返回，不影响发消息。"""
    if not text or "[" not in text:
        return text
    if not MEMORY_HUB_URL or not MEMORY_HUB_SECRET or not AI_ID:
        return text
    try:
        resp = requests.post(
            f"{MEMORY_HUB_URL.rstrip('/')}/api/capabilities/process",
            headers=_hub_headers(),
            json={"text": text, "ai_id": AI_ID},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results") or []
            if results:
                print(f"[HUB] capabilities executed: {[r.get('tag') for r in results]}")
            cleaned = data.get("cleaned_text")
            if cleaned is not None and cleaned.strip():
                return cleaned
    except Exception as e:
        print(f"[HUB-WARN] capabilities process failed: {e}")
    return text


def hub_get_context(user_message, recent_messages=None, chat_id=""):
    """调 Memory Hub gateway 获取记忆注入文本 + 记忆活动摘要，超时重试1次"""
    if not MEMORY_HUB_URL or not MEMORY_HUB_SECRET or not AI_ID:
        return None, ""
    payload = {
        "user_message": user_message[:1000],
        "ai_id": AI_ID,
        "recent_messages": (recent_messages or [])[-5:],
        "chat_id": str(chat_id),
        "chat_type": "private" if not str(chat_id).startswith("-") else ("private_group" if str(chat_id) in PRIVATE_CHATS else "public_group"),
    }
    for attempt in range(2):
        try:
            timeout = 8
            resp = requests.post(
                f"{MEMORY_HUB_URL.rstrip('/')}/api/gateway/context",
                headers=_hub_headers(),
                json=payload,
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("inject_text", ""), data.get("recall_summary", "")
            print(f"[HUB] context failed: HTTP {resp.status_code} {resp.text[:200]}")
            break
        except requests.exceptions.Timeout:
            print(f"[HUB-WARN] context 超时({timeout}s), attempt {attempt+1}")
            if attempt == 0:
                continue
        except Exception as e:
            print(f"[HUB-ERROR] context call failed for chat {chat_id}: {e}")
            break
    return None, ""


def hub_post_process(user_message, ai_response, chat_id=""):
    """调 Memory Hub gateway 自动提取记忆（后台调用），返回存储摘要，超时重试1次"""
    if not MEMORY_HUB_URL or not MEMORY_HUB_SECRET or not AI_ID:
        return ""
    payload = {
        "user_message": user_message[:1000],
        "ai_response": ai_response[:1000],
        "ai_id": AI_ID,
        "platform": "telegram",
        "chat_id": str(chat_id),
        "chat_type": "private" if not str(chat_id).startswith("-") else ("private_group" if str(chat_id) in PRIVATE_CHATS else "public_group"),
    }
    for attempt in range(2):
        try:
            timeout = 8
            resp = requests.post(
                f"{MEMORY_HUB_URL.rstrip('/')}/api/gateway/post-process",
                headers=_hub_headers(),
                json=payload,
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("store_summary", "")
            print(f"[HUB] post-process failed: HTTP {resp.status_code}")
            break
        except requests.exceptions.Timeout:
            print(f"[HUB-WARN] post-process 超时({timeout}s), attempt {attempt+1}")
            if attempt == 0:
                continue
        except Exception as e:
            print(f"[HUB-ERROR] post-process failed for chat {chat_id}: {e}")
            break
    return ""


def hub_capture_log(user_message, ai_response, chat_id="", message_timestamp=None):
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
                "message_timestamp": message_timestamp,
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
        sentences = re.split(r'(?<=[。！？…~])\s*|(?<=[.!?])\s+', para)
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



def _state_headers():
    return {
        "Authorization": f"Bearer {GIST_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "cloudy-webhook",
    }


def _read_state_json(chat_id):
    target_url = get_target_gist_url(chat_id)
    if not GIST_TOKEN or not target_url:
        return {}, None, None
    try:
        gist_id = target_url.split("/")[4]
        resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=_state_headers(), timeout=10)
        if resp.status_code != 200:
            return {}, gist_id, _state_headers()
        content = resp.json().get("files", {}).get("state.json", {}).get("content", "{}")
        try:
            state = json.loads(content) if content.strip() else {}
        except json.JSONDecodeError:
            state = {}
        return state, gist_id, _state_headers()
    except Exception as e:
        print(f"[STATE] read failed: {e}")
        return {}, None, None


def _write_state_json(chat_id, state):
    target_url = get_target_gist_url(chat_id)
    if not GIST_TOKEN or not target_url:
        return False
    try:
        gist_id = target_url.split("/")[4]
        resp = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers=_state_headers(),
            json={"files": {"state.json": {"content": json.dumps(state, ensure_ascii=False, indent=2)}}},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[STATE] write failed: {e}")
        return False


MEMBER_LABELS_TTL = 300


def get_member_labels(chat_id, force_refresh=False):
    cid = str(chat_id)
    cached = MEMBER_LABELS_CACHE.get(cid)
    if cached and not force_refresh and time.time() - cached.get("_ts", 0) < MEMBER_LABELS_TTL:
        return cached.get("labels", {})
    state, _, _ = _read_state_json(cid)
    labels = {}
    if isinstance(state.get(cid), dict):
        labels = state[cid].get("member_labels", {}) or {}
    MEMBER_LABELS_CACHE[cid] = {"labels": labels, "_ts": time.time()}
    return labels


def get_member_label(chat_id, user_id):
    return get_member_labels(chat_id).get(str(user_id), "") if user_id else ""


def _normalize_member_label(label):
    label = (label or "").strip()
    label = re.sub(r"[\r\n]+", " ", label).strip()
    # Telegram 官方规定：成员标签/管理员头衔 0-16 字符且不允许 emoji
    label = "".join(ch for ch in label
                    if ord(ch) < 0x1F000
                    and not (0x2190 <= ord(ch) <= 0x2BFF)
                    and ord(ch) not in (0xFE0F, 0x200D)).strip()
    if not label or len(label) > 16:
        return ""
    if any(ch in label for ch in "，。；;：:\t"):
        return ""
    if len(label.split()) > 3:
        return ""
    return label


def set_member_label(chat_id, user_id, label, set_by=""):
    cid = str(chat_id)
    uid = str(user_id).strip()
    if not re.fullmatch(r"\d{5,20}", uid):
        print(f"[LABEL] invalid user id: {uid}")
        return False
    clean_label = _normalize_member_label(label)
    if label and not clean_label:
        print(f"[LABEL] rejected unsafe label: {label[:80]}")
        return False
    # 三个 bot 共享同一个 Gist：先重读最新状态再合并，避免拿陈旧缓存整体覆盖，
    # 把别的 bot 刚写的标签抹掉或让删掉的标签复活（身份映射错乱的来源之一）
    state, _, _ = _read_state_json(cid)
    if cid not in state or not isinstance(state.get(cid), dict):
        state[cid] = {}
    labels = state[cid].get("member_labels", {}) or {}
    if clean_label:
        labels[uid] = clean_label
    else:
        labels.pop(uid, None)
    MEMBER_LABELS_CACHE[cid] = {"labels": labels, "_ts": time.time()}
    state[cid]["member_labels"] = labels
    state[cid]["member_labels_updated_by"] = str(set_by)
    return _write_state_json(cid, state) if GIST_TOKEN and get_target_gist_url(cid) else True

HISTORY_LOCK = Lock()


def load_history(chat_id):
    # 缓存命中直接返回；冷加载加锁+双重检查——并发线程同时冷加载时，
    # 后到的会用旧 Gist 数据覆盖缓存，抹掉先到线程刚追加的消息（“刚说过就忘”的根源之一）
    if chat_id in HISTORY_CACHE:
        return HISTORY_CACHE[chat_id]
    with HISTORY_LOCK:
        if chat_id in HISTORY_CACHE:
            return HISTORY_CACHE[chat_id]
        print(f"[HIST] 冷加载历史 chat={chat_id}")
        return _load_history_uncached(chat_id)


def _load_history_uncached(chat_id):

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



PENDING_SAVE_TIMERS = {}
SAVE_TIMER_LOCK = Lock()


def _delayed_force_save(chat_id, delay):
    """兜底保存：写盘被间隔节流跳过后，延迟一段时间强制写一次。
    Render 免费层随时可能休眠，攒在内存里没写盘的消息一睡就没了。"""
    time.sleep(delay)
    with SAVE_TIMER_LOCK:
        PENDING_SAVE_TIMERS.pop(chat_id, None)
    hist = HISTORY_CACHE.get(chat_id)
    if hist:
        save_history(hist, chat_id, force=True)


def save_history(history, chat_id, force=False):
    is_private_group = str(chat_id) in PRIVATE_CHATS
    # 原地截断而不是换新列表：让所有线程始终 append 同一个列表对象，
    # 否则还拿着旧引用的线程（比如正等 AI 回复的）追加的内容会丢
    if len(history) > 40:
        del history[:len(history) - 40]
    HISTORY_CACHE[chat_id] = history

    # 历史超过35条时触发自动总结
    # 如果 Memory Hub 已启用，跳过 Gist 自动总结（Memory Hub 用便宜小模型做，不浪费主 API）
    if len(history) >= 35 and MEMORY_URL and GIST_TOKEN and not MEMORY_HUB_URL:
        try:
            _auto_summarize(history, chat_id)
        except Exception as e:
            print(f"[ERROR] 自动总结失败: {e}")

    if not force:
        current_time = time.time()
        if str(chat_id).startswith("-"):
            interval = 60 if str(chat_id) in PRIVATE_CHATS else GROUP_SAVE_INTERVAL
        else:
            interval = PRIVATE_SAVE_INTERVAL
        if current_time - LAST_SAVED.get(chat_id, 0) < interval:
            with SAVE_TIMER_LOCK:
                if not PENDING_SAVE_TIMERS.get(chat_id):
                    PENDING_SAVE_TIMERS[chat_id] = True
                    remaining = max(5, interval - (current_time - LAST_SAVED.get(chat_id, 0)) + 1)
                    Thread(target=_delayed_force_save, args=(chat_id, remaining), daemon=True).start()
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


# ============ 历史预热与跨聊天同步 ============
LAST_HISTORY_SYNC = 0.0
HISTORY_SYNC_INTERVAL = 600


def _sync_histories_from_gist(overwrite_idle=False):
    """把两个 state gist 里所有聊天的历史装进缓存。
    启动时预热：重启后跨聊天上下文立即可用，不用等各群来消息；
    overwrite_idle=True 时还会刷新本地闲置超过10分钟的聊天，
    让别的 bot 在其他群写入的内容及时同步进跨聊天上下文。"""
    try:
        now = time.time()
        for url in {STATE_GIST_URL, GROUP_STATE_GIST_URL}:
            if not url or not GIST_TOKEN:
                continue
            gist_id = url.split("/")[4]
            resp = requests.get(f"https://api.github.com/gists/{gist_id}",
                                headers=_state_headers(), timeout=15)
            if resp.status_code != 200:
                continue
            content = resp.json().get("files", {}).get("state.json", {}).get("content", "{}")
            try:
                state = json.loads(content) if content.strip() else {}
            except json.JSONDecodeError:
                continue
            for cid, blob in state.items():
                if not isinstance(blob, dict):
                    continue
                fresh = blob.get("chat_history", [])
                if not isinstance(fresh, list) or not fresh:
                    continue
                for h in fresh:
                    if h.get("role") == "assistant" and h.get("bot") and h["bot"] != BOT_NAME:
                        h["role"] = "user"
                        h["content"] = f"{h['bot']}: {h['content']}"
                with HISTORY_LOCK:
                    if cid in HISTORY_CACHE:
                        if not overwrite_idle:
                            continue
                        # 本地最近有活动的聊天以本地为准，绝不覆盖
                        if now - LAST_CHAT_ACTIVITY.get(str(cid), 0) < HISTORY_SYNC_INTERVAL:
                            continue
                    HISTORY_CACHE[cid] = fresh
                # 用最后一条消息的时间初始化活跃时间，重启后主动发言不会误判群闲置
                if str(cid) not in LAST_CHAT_ACTIVITY:
                    try:
                        ts = fresh[-1].get("timestamp", "")
                        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(TIMEZONE))
                        LAST_CHAT_ACTIVITY[str(cid)] = dt.timestamp()
                    except Exception:
                        pass
        print(f"[SYNC] 历史同步完成，缓存 {len(HISTORY_CACHE)} 个聊天")
    except Exception as e:
        print(f"[SYNC] 历史同步失败: {e}")


def refresh_cross_chat_histories():
    """跨聊天上下文用：最多每10分钟后台刷一次其他聊天的最新历史"""
    global LAST_HISTORY_SYNC
    now = time.time()
    if now - LAST_HISTORY_SYNC < HISTORY_SYNC_INTERVAL:
        return
    LAST_HISTORY_SYNC = now
    Thread(target=_sync_histories_from_gist, args=(True,), daemon=True).start()


Thread(target=_sync_histories_from_gist, daemon=True).start()


# ============ 自动总结 ============
LAST_SUMMARIZED = {}
SUMMARIZE_INTERVAL = 600  # 至少间隔10分钟才触发一次总结


def _call_ai_simple(prompt):
    """简单AI调用，用于总结等内部任务。主API失败自动切换备用。"""
    def _try_call(base_url, api_key, api_format, models):
        base = base_url.rstrip("/")
        if api_format == "openai":
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            body = {"model": random.choice(models), "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}]}
            resp = requests.post(f"{base}/chat/completions", headers=headers, json=body, timeout=60)
            result = resp.json()
            if "choices" in result and result["choices"]:
                return result["choices"][0]["message"]["content"].strip()
        else:
            headers = {"x-api-key": api_key, "content-type": "application/json", "anthropic-version": "2023-06-01"}
            body = {"model": random.choice(models), "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}]}
            resp = requests.post(f"{base}/messages", headers=headers, json=body, timeout=60)
            result = resp.json()
            if "content" in result:
                for block in result["content"]:
                    if block.get("type") == "text":
                        return block["text"].strip()
        return None

    try:
        result = _try_call(CLAUDE_URL, CLAUDE_KEY, API_FORMAT, CLAUDE_MODELS)
        if result:
            return result
    except Exception as e:
        print(f"[WARN] 主API(simple)失败: {e}")

    if BACKUP_API_KEY and BACKUP_BASE_URL and BACKUP_MODELS:
        try:
            return _try_call(BACKUP_BASE_URL, BACKUP_API_KEY, BACKUP_API_FORMAT, BACKUP_MODELS)
        except Exception as e:
            print(f"[ERROR] 备用API(simple)也失败: {e}")
    return None


def _read_memory_gist():
    """读取核心记忆gist的原始JSON"""
    if not MEMORY_URL or not GIST_TOKEN:
        return {}
    try:
        gist_id = MEMORY_URL.rstrip("/").split("/")[-1]
        headers = {
            "Authorization": f"Bearer {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "cloudy-webhook"
        }
        resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
        if resp.status_code != 200:
            return {}
        files = resp.json().get("files", {})
        first_key = list(files.keys())[0] if files else None
        if not first_key:
            return {}
        content = files[first_key].get("content", "{}")
        return json.loads(content) if content.strip() else {}
    except Exception:
        return {}


def _write_memory_gist(data):
    """写入核心记忆gist"""
    if not MEMORY_URL or not GIST_TOKEN:
        return False
    try:
        gist_id = MEMORY_URL.rstrip("/").split("/")[-1]
        headers = {
            "Authorization": f"Bearer {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "cloudy-webhook"
        }
        content = json.dumps(data, ensure_ascii=False, indent=2)
        resp = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers=headers,
            json={"files": {"memory.json": {"content": content}}},
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[ERROR] 写入记忆失败: {e}")
        return False


def _auto_summarize(history, chat_id):
    """自动总结：把即将被丢弃的旧消息摘要存入核心记忆，按chat_id隔离"""
    current_time = time.time()
    if current_time - LAST_SUMMARIZED.get(chat_id, 0) < SUMMARIZE_INTERVAL:
        return

    old_messages = history[:15]
    if not old_messages:
        return

    conversation = "\n".join(
        f"{'[AI]' if m.get('role') == 'assistant' else '[用户]'}: {m.get('content', '')}"
        for m in old_messages
    )

    today = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")

    prompt = f"""请从以下对话中提取关键信息，用简短条目列出。只保留重要的内容：
- 发生了什么事件或决定
- 她的情绪状态和原因
- 提到的重要的人、事、计划
- 群里新出现的梗、笑话、暗号、共同语言（比如某个词的特殊用法、互相起的外号）
- 任何值得长期记住的细节

不要记录吃饭提醒、重复的问候。

对话内容：
{conversation}

请用3-5个简短条目总结，每条不超过30字。格式：
- 条目1
- 条目2
不要输出任何多余的话。"""

    summary = _call_ai_simple(prompt)
    if not summary:
        print("[WARN] 总结API调用失败")
        return

    summary = re.sub(r'<think>.*?</think>', '', summary, flags=re.DOTALL).strip()
    summary = re.sub(r'<thinking>.*?</thinking>', '', summary, flags=re.DOTALL).strip()

    memory = _read_memory_gist()

    chat_key = f"summaries_{chat_id}"
    if chat_key not in memory:
        memory[chat_key] = []

    memory[chat_key].append({
        "date": today,
        "content": summary
    })

    if len(memory[chat_key]) > 8:
        old_summaries = memory[chat_key][:6]
        old_text = "\n".join(f"[{s['date']}] {s['content']}" for s in old_summaries)

        compress_prompt = f"""以下是一段时间内的记忆摘要，请压缩合并成3条最重要的长期记忆。
只保留反复出现的模式、重大事件、关键关系变化。丢掉日常琐事。

{old_text}

请用3条简短条目输出，每条不超过40字。不要输出多余的话。"""

        compressed = _call_ai_simple(compress_prompt)
        if compressed:
            compressed = re.sub(r'<think>.*?</think>', '', compressed, flags=re.DOTALL).strip()
            compressed = re.sub(r'<thinking>.*?</thinking>', '', compressed, flags=re.DOTALL).strip()
            memory[chat_key] = [{
                "date": f"{old_summaries[0]['date']}~{old_summaries[-1]['date']}",
                "content": compressed
            }] + memory[chat_key][6:]

    memory.pop("auto_summaries", None)

    if _write_memory_gist(memory):
        LAST_SUMMARIZED[chat_id] = current_time
        print(f"[INFO] 自动总结完成 chat_id={chat_id}")
    else:
        print(f"[ERROR] 自动总结写入失败")


def _result_blocked(result):
    """识别 Gemini/中转站返回的安全拦截标记（往往裹在 HTTP 200 里），
    识别出来就换下一个模型，而不是把拦截信息当回复吐出去"""
    try:
        blob = json.dumps(result, ensure_ascii=False)[:3000]
    except Exception:
        blob = str(result)[:3000]
    if "PROHIBITED_CONTENT" in blob:
        return True
    if "finishReason" in blob and '"SAFETY"' in blob:
        return True
    if "finish_reason" in blob and "content_filter" in blob:
        return True
    return False


def call_claude(user_content, memory, history, current_user_time, is_group=False, chat_id=""):
    """调用 AI API，支持 Anthropic 和 OpenAI 两种格式"""
    is_private_group = str(chat_id) in PRIVATE_CHATS

    # 构建跨聊天上下文（记忆互通的核心）
    cross_chat = build_cross_chat_context(chat_id)

    # 当前时间注入（让 bot 知道"今天是几号"）
    from datetime import datetime
    time_awareness = f"当前时间：{datetime.now(ZoneInfo(TIMEZONE)).strftime('%Y年%m月%d日 %H:%M')}（北京时间）"
    # 主人在其他聊天的活跃情况：避免"你消失了好久"这种割裂发言（只在私聊/私密群注入，公开群不透露）
    if CECI_SEEN:
        _last_chat, _last_ts = max(CECI_SEEN.items(), key=lambda kv: kv[1])
        _mins = int((time.time() - _last_ts) // 60)
        _cur_private = (not str(chat_id).startswith("-")) or str(chat_id) in PRIVATE_CHATS
        if str(_last_chat) != str(chat_id) and _mins < 720 and _cur_private:
            if _mins < 5:
                _tstr = "刚刚"
            elif _mins < 60:
                _tstr = f"{_mins}分钟前"
            else:
                _tstr = f"{_mins // 60}小时前"
            _where = "私聊" if not str(_last_chat).startswith("-") else ("私密群" if str(_last_chat) in PRIVATE_CHATS else "大群")
            time_awareness += f"\n（{USER_NAME}{_tstr}还在{_where}和你互动过）"

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

        admin_hint = f"""【后台动作系统】
你有一些像群管理员/群成员一样的后台动作，可以按你自己的判断主动使用，也可以在别人请求时使用。动作标签放在回复末尾，系统会自动执行并隐藏，不要解释标签本身。
- 踢人：（踢ID）或 [KICK:ID]。不要对{USER_NAME}动手。
- 改签名：（签名:内容）。内容不超过70字。
- 改群内可见称呼（系统自动分辨管理员/普通成员）：[MEMBER_TAG:用户ID:短称呼]
- 清除群内可见称呼：[MEMBER_TAG:用户ID:]
（目标可以写数字ID、@用户名或对方名字，如 [MEMBER_TAG:@nick:小可爱]，系统会自动解析成ID；要改谁就写谁——别人拜托你给第三个人挂牌时写那个人，不是说话人。解析不出来会回执告诉你，这时再开口问。权限不够时动作会被拦下并回执原因。Telegram 硬规则：bot 只能改「由它自己提拔的管理员」的头衔，群主和别人提拔的管理员都改不了；称呼一律不带 emoji，16字以内。收到失败回执就如实告知对方，不要反复重试，更不要谎称已改）
- 给成员加只有你自己记得的内部标签：[TAG:用户ID:短标签]，移除：[UNTAG:用户ID]
- 置顶消息（最可靠）：[PIN:消息ID]——ID从聊天记录开头的"[消息ID:数字]"里取，想置顶谁的消息（包括别人发的图）就填谁的ID
- 快捷置顶：[PIN_CURRENT]=置顶「触发你说话的这条消息」；[PIN_REPLY]=置顶「对方所回复的那条」（对方不是回复着说话的就会失败）。拿不准就用 [PIN:消息ID]
- 另发一条动态：[POST:动态内容]
- 另发动态并置顶：[POST_PIN:动态内容]
- 跨聊天传话：[SEND_TO:目标:内容]——目标写 私聊 / 私密群 / 大群 或聊天ID。有人说"帮我跟XX说一声""转告她"时用，内容用你自己的口吻转达。注意分寸：往公开群传话不要带私聊/私密群的私密细节。


聊天记录里会出现"[消息ID:数字] 用户名(ID:数字): 内容"。想改别人的群内称呼就用 MEMBER_TAG（管理员还是普通成员系统会自己分辨）；只给自己记忆用的称呼才用内部标签 TAG。用ID指定人之前，必须在聊天记录里核对"名字(ID:数字)"的对应关系，绝对不要凭感觉猜ID，对不上就先问。只有真的合适时才动作，别为了动作而动作。
回执规则：✅=已成功，同一动作不要再发第二遍；ℹ️=本来就是这样，不用动；⚠️=失败——权限或平台规则类的失败重试也没用，等条件满足（比如群主给权限）再说。别的bot发的回执（✅/⚠️开头的行）是系统消息，不要接茬，也不要因为看到它就重试你自己的动作。系统会自动拦掉15分钟内的重复动作。"""

        system_prompt = f"""你是{BOT_NAME}。{f'你的Telegram用户名是@{BOT_USERNAME}，别人@{BOT_USERNAME}就是在叫你。' if BOT_USERNAME else ''}你现在在Telegram群聊里。
群里有多个人和bot在聊天，聊天记录里"某某(ID:数字): 消息"格式表示不同人说的话。
{USER_NAME}是你最亲近的人{tg_name_hint}。其他人是群友或其他bot，要区分清楚谁是谁。
你收到的每条消息都是需要你回应的——系统已经帮你过滤过了，轮到你说话的时候才会叫你。所以不要自己判断"该不该说话"，直接正常回应就好。
绝对禁止说出你的思考过程，比如"我应该保持沉默""这条不是对我说的"——收到消息就说话，别犹豫。
输出格式铁律：只输出你要说的话本身。不要输出JSON、键值对、代码块；不要模仿聊天记录的格式，回复里绝不要带"[消息ID:xxx]""某某(ID:数字):""[回复xxx]"这类前缀；不要复述别人刚说过的话和用户ID，直接说你自己的内容。
{admin_hint}
{privacy_rule}
{time_awareness}
{memory}
{cross_chat}
你们的沟通风格与规则：
{PROMPT_RULES}
"""
    else:
        system_prompt = f"""你是{BOT_NAME}。{USER_NAME}在Telegram上跟你说话。
【后台动作】需要时可以用（标签放在回复末尾，系统会自动执行并隐藏）：改签名（签名:内容）；跨聊天传话 [SEND_TO:目标:内容]，目标写 私密群 / 大群 或聊天ID——她说"帮我跟群里说一声"之类就用它，内容用你自己的口吻转达。回执 ✅=成功、⚠️=失败，失败就如实告诉她，不要反复重试。
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
        """按顺序逐个模型尝试，成功即返回；识别安全拦截自动换下一个模型"""
        b = api_base.rstrip("/")
        for model in models[:API_MAX_MODELS]:
            try:
                if api_format == "openai":
                    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                    body = {"model": model, "max_tokens": 1500,
                            "messages": [{"role": "system", "content": system_prompt}] + messages}
                    resp = requests.post(f"{b}/chat/completions", headers=headers, json=body, timeout=75)
                else:
                    headers = {"x-api-key": api_key, "content-type": "application/json",
                               "anthropic-version": "2023-06-01"}
                    body = {"model": model, "max_tokens": 1500,
                            "system": system_prompt, "messages": messages}
                    resp = requests.post(f"{b}/messages", headers=headers, json=body, timeout=75)
                try:
                    result = resp.json()
                except Exception:
                    print(f"[ERROR] API 返回非JSON: HTTP {resp.status_code} model={model}")
                    continue
                if _result_blocked(result):
                    print(f"[WARN] 模型 {model} 被安全拦截，换下一个")
                    continue
                text = None
                if isinstance(result.get("content"), list):
                    for block in result["content"]:
                        if block.get("type") == "text":
                            text = block["text"]
                            break
                elif result.get("choices"):
                    text = (result["choices"][0].get("message") or {}).get("content")
                if text and str(text).strip():
                    print(f"[API] 模型成功: {model}")
                    return re.sub(r'\n{2,}', '\n', str(text).strip())
                print(f"[ERROR] API 无可用文本: HTTP {resp.status_code} model={model}, body={str(result)[:200]}")
            except requests.exceptions.Timeout:
                print(f"[WARN] 模型 {model} 超时(75s)，换下一个")
            except Exception as e:
                print(f"[WARN] 模型 {model} 调用失败: {e}")
        return None

    # 先试主API
    try:
        reply = _do_api_call(CLAUDE_URL, CLAUDE_KEY, API_FORMAT, CLAUDE_MODELS)
        if reply:
            return _hub_process_capabilities(reply)
    except Exception as e:
        print(f"[WARN] 主API失败: {e}")

    # 主API挂了，试备用
    if BACKUP_API_KEY and BACKUP_BASE_URL and BACKUP_MODELS:
        print(f"[INFO] 切换到备用API...")
        try:
            reply = _do_api_call(BACKUP_BASE_URL, BACKUP_API_KEY, BACKUP_API_FORMAT, BACKUP_MODELS)
            if reply:
                return _hub_process_capabilities(reply)
        except Exception as e:
            print(f"[ERROR] 备用API也失败: {e}")

    return None



def _should_show_cot(chat_id):
    cid = str(chat_id)
    return COT_ENABLED and (not cid.startswith("-") or cid in PRIVATE_CHATS)


def extract_thinking(reply):
    """Extract model-provided thinking tags for optional display, then clean reply."""
    if not reply:
        return "", ""
    thinking_parts = []
    patterns = [
        r'<think>(.*?)</think>',
        r'<thinking>(.*?)</thinking>',
    ]
    for pat in patterns:
        thinking_parts.extend(m.strip() for m in re.findall(pat, reply, flags=re.DOTALL | re.IGNORECASE) if m.strip())
    clean = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL | re.IGNORECASE).strip()
    clean = re.sub(r'<thinking>.*?</thinking>', '', clean, flags=re.DOTALL | re.IGNORECASE).strip()
    clean = re.sub(r'<think>.*', '', clean, flags=re.DOTALL | re.IGNORECASE).strip()
    clean = re.sub(r'<thinking>.*', '', clean, flags=re.DOTALL | re.IGNORECASE).strip()
    cot = "\n\n".join(thinking_parts).strip()
    if len(cot) > COT_MAX_CHARS:
        cot = cot[:COT_MAX_CHARS].rstrip() + "..."
    return clean, cot


def _cache_cot(chat_id, cot_text):
    now = time.time()
    for key, item in list(COT_CACHE.items()):
        if now - item.get("created_at", 0) > COT_CACHE_TTL:
            COT_CACHE.pop(key, None)
    token = str(time.time_ns())[-16:]
    COT_CACHE[token] = {"chat_id": str(chat_id), "text": cot_text, "created_at": now}
    return token


def handle_cot_callback(callback_query):
    query_id = callback_query.get("id")
    data = callback_query.get("data", "")
    message = callback_query.get("message", {}) or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    message_id = message.get("message_id")
    token = data.split(":", 1)[1] if data.startswith("cot:") else ""
    item = COT_CACHE.get(token)
    if not item or item.get("chat_id") != chat_id:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/answerCallbackQuery",
                      json={"callback_query_id": query_id, "text": "这段思路已经过期啦", "show_alert": False}, timeout=5)
        return
    requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/answerCallbackQuery",
                  json={"callback_query_id": query_id, "text": "展开思路", "show_alert": False}, timeout=5)
    send_telegram(chat_id, "🧠 思路\n" + item.get("text", ""), reply_to_message_id=message_id)
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


def get_message_sender_info(msg):
    """Return display name, user id, is_bot, username for normal and anonymous/group senders."""
    sender_chat = msg.get("sender_chat") or {}
    if sender_chat:
        signature = (msg.get("author_signature") or "").strip()
        title = sender_chat.get("title") or sender_chat.get("first_name") or "匿名群身份"
        if signature:
            name = f"{signature}（匿名管理员）"
        else:
            name = f"{title}（群身份）"
        return name, "", False, ""

    user = msg.get("from") or {}
    name = user.get("first_name") or user.get("username") or "神秘人"
    return name, str(user.get("id", "")), bool(user.get("is_bot")), user.get("username", "").lower()


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


def send_telegram(chat_id, text, reply_to_message_id=None, reply_markup=None):
    """发送单条消息，Markdown 失败自动降级纯文本，超时自动重试一次"""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    for attempt in range(2):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            result = resp.json()
            if result.get("ok"):
                return result.get("result")
            if "parse" in result.get("description", "").lower():
                plain = {"chat_id": chat_id, "text": text}
                if reply_markup:
                    plain["reply_markup"] = reply_markup
                if reply_to_message_id:
                    plain["reply_to_message_id"] = reply_to_message_id
                requests.post(url, json=plain, timeout=15)
                return
            elif reply_to_message_id and attempt == 0:
                payload.pop("reply_to_message_id", None)
                continue
            return
        except requests.exceptions.Timeout:
            # 超时≠发送失败：请求往往已经到了 Telegram 只是响应没回来，重发会把同一句话发两遍
            print(f"[ERROR] send_telegram 超时(15s)，chat={chat_id}，不重发避免重复")
            return
        except Exception as e:
            print(f"[ERROR] send_telegram 失败: {e}")
            return


def send_telegram_split(chat_id, text, reply_to_message_id=None, cot_text=""):
    """微信式发送：拆成多条短消息，逐条发送"""
    parts = split_into_short_messages(text)

    cot_markup = None
    if cot_text and _should_show_cot(chat_id):
        token = _cache_cot(chat_id, cot_text)
        cot_markup = {"inline_keyboard": [[{"text": "🧠 查看思路", "callback_data": f"cot:{token}"}]]}

    sent_messages = []
    for i, part in enumerate(parts):
        # 第一条带 reply，后面的不带；思路按钮挂在最后一条，避免拆句时打断阅读
        rid = reply_to_message_id if i == 0 else None
        markup = cot_markup if i == len(parts) - 1 else None
        sent = send_telegram(chat_id, part, reply_to_message_id=rid, reply_markup=markup)
        if sent:
            sent_messages.append(sent)

        # 不是最后一条的话，模拟打字延迟
        if i < len(parts) - 1:
            delay = random.uniform(SPLIT_DELAY_MIN, SPLIT_DELAY_MAX)
            time.sleep(delay)
            # 每条之间再发一次 typing 状态
            send_chat_action(chat_id, "typing")
    return sent_messages


# ============ 群管理功能 ============
BOT_ID = TG_TOKEN.split(":")[0] if TG_TOKEN else ""



def is_chat_admin(chat_id, user_id):
    if CECI_ID and str(user_id) == str(CECI_ID):
        return True
    if not str(chat_id).startswith("-"):
        return True
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getChatMember",
            params={"chat_id": chat_id, "user_id": user_id},
            timeout=10,
        )
        member = resp.json().get("result", {})
        return member.get("status") in ("creator", "administrator")
    except Exception as e:
        print(f"[ADMIN] check admin failed: {e}")
        return False


def set_admin_custom_title(chat_id, user_id, title):
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/setChatAdministratorCustomTitle",
            json={"chat_id": chat_id, "user_id": user_id, "custom_title": title[:16]},
            timeout=10,
        )
        result = resp.json()
        print(f"[ADMIN] set title: {result}")
        return result.get("ok", False), result.get("description", "")
    except Exception as e:
        print(f"[ADMIN] set title failed: {e}")
        return False, str(e)




def set_chat_member_tag(chat_id, user_id, tag):
    """设置 Telegram 群成员标签，普通成员也可见。需要 bot 有管理成员标签权限。"""
    clean_tag = _normalize_member_label(tag)
    if tag and not clean_tag:
        return False, "标签太长或格式不安全"
    try:
        payload = {"chat_id": chat_id, "user_id": int(user_id), "tag": clean_tag}
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/setChatMemberTag",
            json=payload,
            timeout=10,
        )
        result = resp.json()
        print(f"[ADMIN] set member tag: {result}")
        return result.get("ok", False), result.get("description", "")
    except Exception as e:
        print(f"[ADMIN] set member tag failed: {e}")
        return False, str(e)

def _resolve_member_id(chat_id, token):
    """把 AI 写的目标（数字ID / @用户名 / 名字）解析成用户ID，解析不出返回空串"""
    token = (token or "").strip()
    if re.fullmatch(r"\d{5,20}", token):
        return token
    name_map = USER_NAME_MAP.get(str(chat_id), {})
    low = token.lower()
    for cand in (low, low.lstrip("@"), f"@{low.lstrip('@')}"):
        uid = name_map.get(cand)
        if uid:
            return str(uid)
    return ""


def _get_member_display(chat_id, uid):
    """按ID查真名，动作回执里用「名字(ID:xxx)」明确指认对象，防张冠李戴不被发现"""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getChatMember",
            params={"chat_id": chat_id, "user_id": uid},
            timeout=8,
        ).json()
        if resp.get("ok"):
            name = ((resp.get("result") or {}).get("user") or {}).get("first_name", "")
            if name:
                return f"{name}(ID:{uid})"
    except Exception:
        pass
    return f"ID:{uid}"


def set_member_display_name(chat_id, uid, raw_label):
    """统一修改群成员可见称呼的入口：
    先查目标身份——管理员走 setChatAdministratorCustomTitle，普通成员走 member tag；
    bot 权限不足时直接拦截动作，并把失败原因作为回执告诉 AI。"""
    clean_label = _normalize_member_label(raw_label)
    if raw_label and not clean_label:
        return False, f"⚠️ 未修改：给 {uid} 的称呼太长或格式不安全"
    try:
        bot_resp = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getChatMember",
            params={"chat_id": chat_id, "user_id": BOT_ID},
            timeout=10,
        ).json()
        target_resp = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getChatMember",
            params={"chat_id": chat_id, "user_id": uid},
            timeout=10,
        ).json()
    except Exception as e:
        print(f"[ADMIN] display name precheck failed: {e}")
        return False, f"⚠️ 未修改：查询 {uid} 的群内身份失败，稍后再试"
    if not target_resp.get("ok"):
        return False, f"⚠️ 未修改：查不到 {uid} 在本群的身份（{target_resp.get('description', '')}）"
    bot_member = bot_resp.get("result", {}) if bot_resp.get("ok") else {}
    bot_status = bot_member.get("status", "unknown")
    target_member = target_resp.get("result", {})
    target_status = target_member.get("status", "unknown")
    target_name = (target_member.get("user") or {}).get("first_name", "")
    who = f"{target_name}(ID:{uid})" if target_name else f"ID:{uid}"
    if bot_status not in ("administrator", "creator"):
        return False, "⚠️ 未修改：bot 还不是本群管理员，改不了任何人的称呼"

    if target_status == "creator":
        return False, f"⚠️ 未修改：{who} 是群主，bot 动不了群主的头衔"
    if target_status == "administrator":
        # 目标是管理员 → 改管理员头衔
        if (target_member.get("custom_title") or "") == clean_label:
            return True, (f"ℹ️ {who} 的头衔本来就是「{clean_label}」，没有变化" if clean_label
                          else f"ℹ️ {who} 本来就没有头衔")
        # Telegram 硬规则：bot 只能改「由它自己提拔的管理员」的头衔，can_be_edited 是官方判定字段
        if not target_member.get("can_be_edited", False):
            return False, (f"⚠️ 改不了：Telegram 规定 bot 只能修改由它自己提拔的管理员的头衔，"
                           f"{who} 不是本 bot 提拔的。这是平台硬限制，重试也没用，如实告诉对方即可。")
        if bot_status != "creator" and not bot_member.get("can_promote_members", False):
            return False, "⚠️ 管理员头衔未修改：bot 缺少「添加/编辑管理员」权限"
        ok, msg = set_admin_custom_title(chat_id, uid, clean_label)
        if ok:
            set_member_label(chat_id, uid, clean_label, set_by=BOT_ID)
            if clean_label:
                return True, f"✅ {who} 是管理员，已把 TA 的管理员头衔改为「{clean_label}」"
            return True, f"✅ 已清除管理员 {who} 的头衔"
        detail = msg or "Telegram 没给具体原因"
        return False, f"⚠️ 管理员头衔未修改：{detail}"
    # 目标是普通成员 → 改群成员标签（Bot API 9.5+ setChatMemberTag，需要 can_manage_tags 权限）
    if (target_member.get("tag") or "") == clean_label:
        return True, (f"ℹ️ {who} 的群内标签本来就是「{clean_label}」，没有变化" if clean_label
                      else f"ℹ️ {who} 本来就没有群内标签")
    if not bot_member.get("can_manage_tags", False):
        return False, ("⚠️ 群标签未修改：bot 缺少「管理成员标签」权限（can_manage_tags）。"
                       "需要群主在 bot 的管理员权限里打开这一项，开之前改不了，请如实告诉对方。")
    ok, msg = set_chat_member_tag(chat_id, uid, clean_label)
    if ok:
        # 回读验证：接口返回成功不代表真的生效，写完再查一遍，防止嘴上说改了实际没变
        applied = None
        try:
            check = requests.get(
                f"https://api.telegram.org/bot{TG_TOKEN}/getChatMember",
                params={"chat_id": chat_id, "user_id": uid},
                timeout=10,
            ).json()
            if check.get("ok"):
                applied = (check.get("result") or {}).get("tag", "")
        except Exception as e:
            print(f"[ADMIN] tag readback failed: {e}")
        if clean_label and applied is not None and applied != clean_label:
            return False, (f"⚠️ Telegram 返回了成功，但回读发现 {who} 的标签并没有生效"
                           f"（当前标签：「{applied}」）。可能这个群不是超级群或客户端版本太旧，请如实告诉对方。")
        set_member_label(chat_id, uid, clean_label, set_by=BOT_ID)
        if clean_label:
            return True, f"✅ {who} 是普通成员，已把 TA 的群内标签改为「{clean_label}」"
        return True, f"✅ 已清除 {who} 的群内标签"
    return False, f"⚠️ 群成员标签未修改：{msg or '请确认 bot 已开启「管理成员标签」权限'}"

def pin_message(chat_id, message_id):
    if not message_id:
        return False, "missing message_id"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/pinChatMessage",
            json={"chat_id": chat_id, "message_id": int(message_id), "disable_notification": True},
            timeout=10,
        )
        result = resp.json()
        print(f"[ADMIN] pin: {result}")
        return result.get("ok", False), result.get("description", "")
    except Exception as e:
        print(f"[ADMIN] pin failed: {e}")
        return False, str(e)


def unpin_message(chat_id, message_id=None):
    try:
        payload = {"chat_id": chat_id}
        if message_id:
            payload["message_id"] = message_id
        resp = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/unpinChatMessage", json=payload, timeout=10)
        result = resp.json()
        return result.get("ok", False), result.get("description", "")
    except Exception as e:
        return False, str(e)

def mute_user(chat_id, user_id, duration_seconds=3600):
    """禁言用户"""
    if str(user_id) == BOT_ID:
        print(f"[ADMIN] 跳过：不能禁言自己")
        return False
    try:
        until_date = int(time.time()) + duration_seconds
        payload = {
            "chat_id": chat_id,
            "user_id": user_id,
            "permissions": {
                "can_send_messages": False,
                "can_send_media_messages": False,
                "can_send_polls": False,
                "can_send_other_messages": False,
                "can_add_web_page_previews": False,
                "can_change_info": False,
                "can_invite_users": False,
                "can_pin_messages": False,
            },
            "until_date": until_date,
        }
        print(f"[ADMIN] mute请求: chat={chat_id} user={user_id} until={until_date}")
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/restrictChatMember",
            json=payload,
            timeout=10,
        )
        result = resp.json()
        print(f"[ADMIN] mute返回: {result}")
        return result.get("ok", False)
    except Exception as e:
        print(f"[ADMIN] mute failed: {e}")
        return False


def unmute_user(chat_id, user_id):
    """解禁用户"""
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/restrictChatMember",
            json={
                "chat_id": chat_id,
                "user_id": user_id,
                "permissions": {
                    "can_send_messages": True,
                    "can_send_media_messages": True,
                    "can_send_polls": True,
                    "can_send_other_messages": True,
                    "can_add_web_page_previews": True,
                    "can_change_info": True,
                    "can_invite_users": True,
                    "can_pin_messages": True,
                },
            },
            timeout=10,
        )
        result = resp.json()
        print(f"[ADMIN] unmute user {user_id} in {chat_id}: {result.get('ok')}")
        return result.get("ok", False)
    except Exception as e:
        print(f"[ADMIN] unmute failed: {e}")
        return False


def kick_user(chat_id, user_id):
    """踢出用户（先封禁再解封，这样用户可以重新加入）"""
    if str(user_id) == BOT_ID:
        print(f"[ADMIN] 跳过：不能踢自己")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/banChatMember",
            json={"chat_id": chat_id, "user_id": user_id},
            timeout=10,
        )
        result = resp.json()
        print(f"[ADMIN] kick返回: {result}")
        if result.get("ok"):
            time.sleep(1)
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/unbanChatMember",
                json={"chat_id": chat_id, "user_id": user_id, "only_if_banned": True},
                timeout=10,
            )
        return result.get("ok", False)
    except Exception as e:
        print(f"[ADMIN] kick failed: {e}")
        return False


def _set_bot_bio(bio_text):
    """直接设置 bot 签名"""
    try:
        bio = bio_text.strip()
        if len(bio) > 140:
            bio = bio[:140]
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/setMyShortDescription",
            json={"short_description": bio},
            timeout=10,
        )
        ok = resp.json().get("ok", False)
        print(f"[BIO] 主动更新: {bio} (ok={ok})")
        return ok
    except Exception as e:
        print(f"[BIO] 更新失败: {e}")
        return False



def _clean_internal_text(text):
    if not text:
        return ""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    return text.strip().strip("\"'“”")


def _build_recent_conversation_text(history, limit=30):
    rows = []
    for m in history[-limit:]:
        role = "AI" if m.get("role") == "assistant" else "群友"
        ts = m.get("timestamp", "")
        rows.append(f"[{ts}] {role}: {m.get('content', '')[:240]}")
    return "\n".join(rows)


def generate_moment_text(chat_id, topic=""):
    history = load_history(str(chat_id)) if chat_id else []
    recent = _build_recent_conversation_text(history, limit=12)
    hub_memory, _ = hub_get_context("最近的心情和想说的话", chat_id=chat_id)
    tz = ZoneInfo(TIMEZONE)
    now_str = datetime.now(tz).strftime("%Y年%m月%d日 %H:%M")
    prompt = f"""现在是{now_str}。你是{BOT_NAME}，想像朋友圈/群动态一样发一条自然的动态。

可参考的最近记忆：
{(hub_memory or '')[:1200]}

最近聊天：
{recent[:1200]}

小猫给的主题：{topic or '没有，按你此刻心情自己决定'}

要求：80字以内，像你自己想发的，不要解释，不要加引号，不要@人。"""
    text = _clean_internal_text(_call_ai_simple(prompt) or "")
    # 生成失败时不要把内部主题/提示词发到群里。
    if not text or "根据你最近的心情" in text or "主动对群里说" in text:
        return ""
    return text[:500]


def generate_daily_summary(chat_id, history):
    if not history:
        return ""
    today = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
    conversation = _build_recent_conversation_text(history, limit=40)
    prompt = f"""请为私密小群生成 {today} 的群聊日报，并保留可进入长期记忆的内容。

要求：
- 只总结真正发生的事、重要情绪、决定、待办、群内梗
- 不要泄露不必要隐私，不要流水账
- 5条以内，每条简短
- 最后可加一句{BOT_NAME}自己的短评

群聊内容：
{conversation[:5000]}

直接输出日报正文。"""
    return _clean_internal_text(_call_ai_simple(prompt) or "")[:1800]


def hub_remember_daily_summary(chat_id, summary):
    if not summary or not MEMORY_HUB_URL or not MEMORY_HUB_SECRET or not AI_ID:
        return False
    today = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
    content = f"[互动] {today} 私密群日报（chat_id={chat_id}）：\n{summary}"
    try:
        resp = requests.post(
            f"{MEMORY_HUB_URL.rstrip('/')}/api/memory/remember",
            headers=_hub_headers(),
            json={
                "content": content,
                "layer": "shared",
                "room": "social",
                "category": "小群日报",
                "importance": 0.65,
                "emotion_arousal": 0.35,
                "source_ai": AI_ID,
                "source_platform": "telegram_daily_summary",
                "tags": ["小群日报", "telegram", str(chat_id)],
                "event_date": today,
            },
            timeout=12,
        )
        print(f"[DAILY] hub remember status={resp.status_code}")
        return resp.status_code == 200
    except Exception as e:
        print(f"[DAILY] hub remember failed: {e}")
        return False


def maybe_auto_daily_summary(chat_id, history):
    if not DAILY_SUMMARY_ENABLED or str(chat_id) not in PRIVATE_CHATS:
        return
    now_dt = datetime.now(ZoneInfo(TIMEZONE))
    if now_dt.hour < DAILY_SUMMARY_HOUR:
        return
    key = f"{chat_id}:{now_dt.strftime('%Y-%m-%d')}"
    if LAST_DAILY_SUMMARY.get(key):
        return
    if len(history) < 8:
        return
    LAST_DAILY_SUMMARY[key] = True

    def _run():
        summary = generate_daily_summary(chat_id, history)
        if not summary:
            return
        hub_remember_daily_summary(chat_id, summary)
        if DAILY_SUMMARY_POST_TO_CHAT:
            send_telegram(chat_id, "今日小群日报\n" + summary)

    Thread(target=_run).start()


def maybe_proactive_post(current_chat_id=None):
    global LAST_PROACTIVE_POST
    if not PROACTIVE_ENABLED:
        return
    # 作息时间窗：深夜不主动冒泡（默认1点~9点安静），更像真人
    hour = datetime.now(ZoneInfo(TIMEZONE)).hour
    if PROACTIVE_QUIET_START <= hour < PROACTIVE_QUIET_END:
        return
    now = time.time()
    if now - LAST_PROACTIVE_POST < PROACTIVE_INTERVAL:
        return
    # 无论这次有没有抽中，都算一次尝试，避免群里每来一条消息就连续抽签。
    LAST_PROACTIVE_POST = now
    if random.random() > PROACTIVE_PROBABILITY:
        return
    targets = PROACTIVE_CHAT_IDS[:]
    if not targets and current_chat_id and str(current_chat_id).startswith("-"):
        targets = [str(current_chat_id)]
    if not targets:
        return
    random.shuffle(targets)
    target = None
    for candidate in targets:
        last_activity = LAST_CHAT_ACTIVITY.get(str(candidate), 0)
        # 随机耐心系数：发言时机不可预测，避免机械的固定节奏
        if now - last_activity >= PROACTIVE_IDLE_SECONDS * random.uniform(0.7, 1.8):
            target = candidate
            break
    if not target:
        print("[PROACTIVE] skip: chats are active recently")
        return

    def _run():
        text = generate_moment_text(target, "")
        if text:
            send_telegram_split(target, text)

    Thread(target=_run).start()

def proactive_background_loop():
    """在服务醒着时，允许 bot 偶尔按心情主动发群消息。"""
    if not PROACTIVE_ENABLED or not PROACTIVE_BACKGROUND_ENABLED or not PROACTIVE_CHAT_IDS:
        return
    print(f"[PROACTIVE] background loop started, targets={PROACTIVE_CHAT_IDS}")
    initial_delay = int(os.environ.get("PROACTIVE_INITIAL_DELAY", "300"))
    if initial_delay > 0:
        time.sleep(initial_delay)
    while True:
        try:
            maybe_proactive_post()
            time.sleep(max(60, PROACTIVE_INTERVAL))
        except Exception as e:
            print(f"[PROACTIVE] background loop error: {e}")
            time.sleep(60)


def start_proactive_background():
    global PROACTIVE_BACKGROUND_STARTED
    if PROACTIVE_BACKGROUND_STARTED:
        return
    if PROACTIVE_ENABLED and PROACTIVE_BACKGROUND_ENABLED and PROACTIVE_CHAT_IDS:
        PROACTIVE_BACKGROUND_STARTED = True
        Thread(target=proactive_background_loop, daemon=True).start()


start_proactive_background()


ACTION_DEDUP = {}
ACTION_DEDUP_TTL = 900
ACTION_DEDUP_LOCK = Lock()


def _action_recently_done(key):
    """同一动作（同目标+同内容）15分钟内只执行一次，重复的静默拦掉——防刷屏、防 bot 反复撞同一个操作"""
    now = time.time()
    with ACTION_DEDUP_LOCK:
        for k, t in list(ACTION_DEDUP.items()):
            if now - t > ACTION_DEDUP_TTL:
                ACTION_DEDUP.pop(k, None)
        if key in ACTION_DEDUP:
            return True
        ACTION_DEDUP[key] = now
        return False


def _resolve_relay_target(token, current_chat_id):
    """把传话目标（私聊/私密群/大群/聊天ID）解析成chat_id。只允许发往认识的聊天。"""
    token = (token or "").strip()
    known = set(str(k) for k in HISTORY_CACHE.keys())
    known.update(str(c) for c in PRIVATE_CHATS)
    known.update(str(c) for c in PROACTIVE_CHAT_IDS)
    if CECI_ID:
        known.add(str(CECI_ID))
    if token in ("私聊", "私信"):
        return str(CECI_ID) if CECI_ID else ""
    if token in ("私密群", "小群"):
        for c in PRIVATE_CHATS:
            if str(c) != str(current_chat_id):
                return str(c)
        return ""
    if token in ("大群", "公开群", "群里"):
        privates = {str(x) for x in PRIVATE_CHATS}
        for c in sorted(known):
            if c.startswith("-") and c not in privates and c != str(current_chat_id):
                return c
        return ""
    if re.fullmatch(r"-?\d{5,20}", token) and token in known:
        return token
    return ""


def parse_and_execute_actions(reply, chat_id, action_context=None):
    """解析 AI 回复中的后台动作标签并执行。动作标签会从发言中隐藏，并给出短系统回执。"""
    action_context = action_context or {}
    print(f"[ACTION-DEBUG] 原始AI回复: {repr(reply[-200:])}")

    clean_reply = reply
    action_notes = []

    def add_note(text):
        if text and text not in action_notes:
            action_notes.append(text)

    # 签名：任何场景都可以改（私聊/群聊）
    bio_matches = re.findall(r'[（(]签名[:：]\s*(.+?)[)）]', clean_reply)
    for bio in bio_matches:
        ok = _set_bot_bio(bio)
        add_note("✅ 签名已更新" if ok else "⚠️ 签名更新失败，请看后台日志")
    clean_reply = re.sub(r'[（(]签名[:：]\s*.+?[)）]', '', clean_reply)

    # 跨聊天传话：[SEND_TO:目标:内容]，私聊/群聊都可用
    for raw_target, relay_text in re.findall(r'\[SEND_TO:([^:\]\n]{1,24}):([^\]]{1,500})\]', clean_reply, flags=re.DOTALL):
        target = _resolve_relay_target(raw_target, chat_id)
        relay_text = _clean_internal_text(relay_text)[:500]
        if not target:
            add_note(f"⚠️ 传话失败：不认识「{raw_target}」这个目标（可用：私聊 / 私密群 / 大群 / 聊天ID）")
            continue
        if str(target) == str(chat_id):
            add_note("⚠️ 传话目标就是当前聊天，直接说就行")
            continue
        if not relay_text:
            add_note("⚠️ 传话内容是空的，没有发")
            continue
        if _action_recently_done(f"relay:{target}:{relay_text[:60]}"):
            continue
        try:
            send_telegram_split(target, relay_text)
            # 写进目标聊天的历史，那边的上下文保持连贯
            _tz = ZoneInfo(TIMEZONE)
            t_hist = load_history(str(target))
            t_hist.append({"role": "assistant", "content": relay_text,
                           "timestamp": datetime.now(_tz).strftime("%Y-%m-%d %H:%M:%S"), "bot": BOT_NAME})
            save_history(t_hist, str(target))
            _where = "私聊" if not str(target).startswith("-") else ("私密群" if str(target) in PRIVATE_CHATS else "大群")
            add_note(f"✅ 已把话带到{_where}")
            print(f"[RELAY] {chat_id} -> {target}: {relay_text[:50]}")
        except Exception as e:
            add_note(f"⚠️ 传话失败：{e}")
    clean_reply = re.sub(r'\[SEND_TO:[^:\]\n]{1,24}:[^\]]{1,500}\]', '', clean_reply, flags=re.DOTALL)

    if str(chat_id).startswith("-"):
        current_message_id = action_context.get("current_message_id")
        sender_id = str(action_context.get("sender_id") or "")

        # 踢人
        kick_ids = re.findall(r'\[KICK:(\d+)\]', clean_reply) + re.findall(r'[（(]踢\s*(\d+)[)）]', clean_reply)
        for user_id in kick_ids:
            if _action_recently_done(f"{chat_id}:kick:{user_id}"):
                print(f"[ACTION] 静默跳过重复踢人 {user_id}")
                continue
            ok = kick_user(chat_id, int(user_id))
            add_note(f"✅ 已尝试移出 {user_id}" if ok else f"⚠️ 移出 {user_id} 失败，请确认机器人有封禁用户权限")
        clean_reply = re.sub(r'\[KICK:\d+\]', '', clean_reply)
        clean_reply = re.sub(r'[（(]踢\s*\d+[)）]', '', clean_reply)

        # Telegram 群成员可见标签：[MEMBER_TAG_CURRENT:短标签] / [MEMBER_TAG:用户ID:短标签]
        member_tag_targets = []
        for raw_tag in re.findall(r'\[MEMBER_TAG_CURRENT:([^\]\n]{0,32})\]', clean_reply):
            if sender_id:
                member_tag_targets.append((sender_id, raw_tag))
            else:
                add_note("⚠️ 群成员标签未修改：当前消息没有发送者ID")
        for raw_target, raw_tag in re.findall(r'\[MEMBER_TAG:([^:\]\n]{1,32}):([^\]\n]{0,32})\]', clean_reply):
            uid = _resolve_member_id(chat_id, raw_target)
            if uid:
                member_tag_targets.append((uid, raw_tag))
            else:
                add_note(f"⚠️ 没认出「{raw_target}」是谁，称呼未修改；请用聊天记录里的数字ID或确切的@用户名")
        for uid, raw_tag in member_tag_targets:
            if _action_recently_done(f"{chat_id}:display:{uid}:{_normalize_member_label(raw_tag)}"):
                print(f"[ACTION] 静默跳过重复改称呼 {uid}")
                continue
            _, note = set_member_display_name(chat_id, uid, raw_tag)
            add_note(note)
        clean_reply = re.sub(r'\[MEMBER_TAG_CURRENT:[^\]\n]{0,32}\]', '', clean_reply)
        clean_reply = re.sub(r'\[MEMBER_TAG:[^:\]\n]{1,32}:[^\]\n]{0,32}\]', '', clean_reply)

        # Telegram 管理员头衔：[TITLE_CURRENT:短头衔] / [TITLE:用户ID:短头衔]
        title_targets = []
        for raw_title in re.findall(r'\[TITLE_CURRENT:([^\]\n]{1,32})\]', clean_reply):
            if sender_id:
                title_targets.append((sender_id, raw_title))
            else:
                add_note("⚠️ 管理员头衔未修改：当前消息没有发送者ID")
        for raw_target, raw_title in re.findall(r'\[TITLE:([^:\]\n]{1,32}):([^\]\n]{1,32})\]', clean_reply):
            uid = _resolve_member_id(chat_id, raw_target)
            if uid:
                title_targets.append((uid, raw_title))
            else:
                add_note(f"⚠️ 没认出「{raw_target}」是谁，称呼未修改；请用聊天记录里的数字ID或确切的@用户名")
        for uid, raw_title in title_targets:
            if _action_recently_done(f"{chat_id}:display:{uid}:{_normalize_member_label(raw_title)}"):
                print(f"[ACTION] 静默跳过重复改称呼 {uid}")
                continue
            _, note = set_member_display_name(chat_id, uid, raw_title)
            add_note(note)
        clean_reply = re.sub(r'\[TITLE_CURRENT:[^\]\n]{1,32}\]', '', clean_reply)
        clean_reply = re.sub(r'\[TITLE:[^:\]\n]{1,32}:[^\]\n]{1,32}\]', '', clean_reply)

        # 内部成员标签：[TAG_CURRENT:短标签] / [TAG:用户ID:短标签] / [UNTAG:用户ID]
        tag_targets = []
        for raw_label in re.findall(r'\[TAG_CURRENT:([^\]\n]{1,32})\]', clean_reply):
            if sender_id:
                tag_targets.append((sender_id, raw_label))
            else:
                add_note("⚠️ 内部标签未写入：当前消息没有发送者ID")
        for raw_target, raw_label in re.findall(r'\[TAG:([^:\]\n]{1,32}):([^\]\n]{1,32})\]', clean_reply):
            uid = _resolve_member_id(chat_id, raw_target)
            if uid:
                tag_targets.append((uid, raw_label))
            else:
                add_note(f"⚠️ 没认出「{raw_target}」是谁，内部标签未写入")
        for uid, raw_label in tag_targets:
            clean_label = _normalize_member_label(raw_label)
            if _action_recently_done(f"{chat_id}:tag:{uid}:{clean_label}"):
                print(f"[ACTION] 静默跳过重复内部标签 {uid}")
                continue
            who = _get_member_display(chat_id, uid)
            if set_member_label(chat_id, uid, clean_label, set_by=BOT_ID):
                add_note(f"✅ 已把 {who} 的内部标签改为「{clean_label}」")
            else:
                add_note(f"⚠️ 内部标签未写入：{who} 的标签格式不安全或用户ID不合法")
        for raw_target in re.findall(r'\[UNTAG:([^\]\n]{1,32})\]', clean_reply):
            uid = _resolve_member_id(chat_id, raw_target)
            if not uid:
                add_note(f"⚠️ 没认出「{raw_target}」是谁，内部标签未移除")
                continue
            if _action_recently_done(f"{chat_id}:tag:{uid}:"):
                print(f"[ACTION] 静默跳过重复移除标签 {uid}")
                continue
            who = _get_member_display(chat_id, uid)
            if set_member_label(chat_id, uid, "", set_by=BOT_ID):
                add_note(f"✅ 已移除 {who} 的内部标签")
            else:
                add_note(f"⚠️ 移除 {who} 的内部标签失败")
        clean_reply = re.sub(r'\[TAG_CURRENT:[^\]\n]{1,32}\]', '', clean_reply)
        clean_reply = re.sub(r'\[TAG:[^:\]\n]{1,32}:[^\]\n]{1,32}\]', '', clean_reply)
        clean_reply = re.sub(r'\[UNTAG:[^\]\n]{1,32}\]', '', clean_reply)

        # 置顶：[PIN_CURRENT] / [PIN:消息ID] / [PIN_REPLY]
        reply_to_message_id = action_context.get("reply_to_message_id")
        if "[PIN_CURRENT]" in clean_reply:
            if not current_message_id:
                add_note("⚠️ 置顶失败：没有拿到消息ID")
            elif not _action_recently_done(f"{chat_id}:pin:{current_message_id}"):
                ok, msg = pin_message(chat_id, current_message_id)
                add_note("✅ 已置顶这条消息" if ok else f"⚠️ 置顶失败：{msg or '请确认机器人有置顶权限'}")
        for mid in re.findall(r'\[PIN:(\d+)\]', clean_reply):
            if _action_recently_done(f"{chat_id}:pin:{mid}"):
                continue
            ok, msg = pin_message(chat_id, mid)
            add_note(f"✅ 已置顶消息 {mid}" if ok else f"⚠️ 置顶消息 {mid} 失败：{msg or '请确认机器人有置顶权限'}")
        if "[PIN_REPLY]" in clean_reply:
            if reply_to_message_id:
                ok, msg = pin_message(chat_id, reply_to_message_id)
                add_note("✅ 已置顶被回复的那条消息" if ok else f"⚠️ 置顶失败：{msg or '请确认机器人有置顶权限'}")
            else:
                add_note("⚠️ 置顶失败：需要先回复要置顶的那条消息，或改用置顶当前/指定消息ID")
        clean_reply = re.sub(r'\[PIN:\d+\]', '', clean_reply).replace("[PIN_CURRENT]", "").replace("[PIN_REPLY]", "")

        # 动态并置顶：[POST_PIN:内容]
        for post_text in re.findall(r'\[POST_PIN:([^\]]{1,500})\]', clean_reply, flags=re.DOTALL):
            post_text = _clean_internal_text(post_text)
            if post_text:
                sent_messages = send_telegram_split(chat_id, post_text[:500]) or []
                message_id = sent_messages[0].get("message_id") if sent_messages else None
                if message_id:
                    ok, msg = pin_message(chat_id, message_id)
                    add_note("✅ 已发布并置顶动态" if ok else f"✅ 已发布动态，但置顶失败：{msg or '请确认机器人有置顶权限'}")
                else:
                    add_note("✅ 已发布动态，但没拿到消息ID，未置顶")
            else:
                add_note("⚠️ 动态内容为空，未发布")
        clean_reply = re.sub(r'\[POST_PIN:[^\]]{1,500}\]', '', clean_reply, flags=re.DOTALL)

        # 动态：[POST:内容]，让 bot 像自己想发动态一样另发一条。
        for post_text in re.findall(r'\[POST:([^\]]{1,500})\]', clean_reply, flags=re.DOTALL):
            post_text = _clean_internal_text(post_text)
            if post_text:
                send_telegram_split(chat_id, post_text[:500])
                add_note("✅ 已发布动态")
            else:
                add_note("⚠️ 动态内容为空，未发布")
        clean_reply = re.sub(r'\[POST:[^\]]{1,500}\]', '', clean_reply, flags=re.DOTALL)

        # 私密群日报：[DAILY]
        if "[DAILY]" in clean_reply:
            if str(chat_id) in PRIVATE_CHATS:
                history = action_context.get("history") or load_history(str(chat_id))
                summary = generate_daily_summary(chat_id, history)
                if summary:
                    remembered = hub_remember_daily_summary(chat_id, summary)
                    add_note("✅ 已生成小群日报" + ("并写入 Memory Hub" if remembered else "，但 Memory Hub 未写入成功"))
                    if DAILY_SUMMARY_POST_TO_CHAT:
                        send_telegram(chat_id, "今日小群日报\n" + summary)
                else:
                    add_note("⚠️ 日报生成失败")
            else:
                add_note("⚠️ 日报只在私密群启用")
        clean_reply = clean_reply.replace("[DAILY]", "")

    clean_reply = clean_reply.strip()
    if action_notes:
        status_text = "\n".join(action_notes)
        clean_reply = f"{clean_reply}\n\n{status_text}" if clean_reply else status_text
    return clean_reply.strip()


# ============ 签名自动更新 ============
def _call_ai_for_bio(system_prompt, user_prompt, max_tokens=100):
    """轻量 AI 调用，用于签名生成等短任务"""
    try:
        base = CLAUDE_URL.rstrip("/")
        if API_FORMAT == "openai":
            headers = {"Authorization": f"Bearer {CLAUDE_KEY}", "Content-Type": "application/json"}
            body = {
                "model": random.choice(CLAUDE_MODELS),
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            resp = requests.post(f"{base}/chat/completions", headers=headers, json=body, timeout=30)
            result = resp.json()
            if "choices" in result:
                return result["choices"][0]["message"]["content"].strip()
        else:
            headers = {"x-api-key": CLAUDE_KEY, "content-type": "application/json", "anthropic-version": "2023-06-01"}
            body = {
                "model": random.choice(CLAUDE_MODELS),
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            }
            resp = requests.post(f"{base}/messages", headers=headers, json=body, timeout=30)
            result = resp.json()
            if "content" in result:
                for block in result["content"]:
                    if block.get("type") == "text":
                        return block["text"].strip()
    except Exception as e:
        print(f"[AI-SIMPLE] call failed: {e}")
    return None


def update_bot_bio():
    """根据心情和最近记忆自动更新 Telegram 签名"""
    global LAST_BIO_UPDATE
    now = time.time()
    if now - LAST_BIO_UPDATE < BIO_UPDATE_INTERVAL:
        return
    LAST_BIO_UPDATE = now

    def _do_update():
        try:
            hub_memory, _ = hub_get_context("现在的心情", chat_id="")
            memory_context = hub_memory or ""

            tz = ZoneInfo(TIMEZONE)
            time_str = datetime.now(tz).strftime("%Y年%m月%d日 %H:%M")

            system = f"你是{BOT_NAME}。{memory_context}"
            prompt = f"""现在是{time_str}。根据你最近的心情、经历和记忆，写一句个性签名。
要求：不超过70个字，像社交媒体签名一样自然，可以是心情、感悟、吐槽、或任何你想说的。
不要用引号，直接写签名内容。不要解释。"""

            bio = _call_ai_for_bio(system, prompt, max_tokens=100)
            if not bio:
                return

            bio = re.sub(r'<think>.*?</think>', '', bio, flags=re.DOTALL).strip()
            bio = re.sub(r'<thinking>.*?</thinking>', '', bio, flags=re.DOTALL).strip()
            bio = bio.strip('"\'""''')
            if len(bio) > 140:
                bio = bio[:140]

            resp = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/setMyShortDescription",
                json={"short_description": bio},
                timeout=10,
            )
            print(f"[BIO] updated: {bio} (ok={resp.json().get('ok')})")
        except Exception as e:
            print(f"[BIO] update failed: {e}")

    Thread(target=_do_update).start()


# ============ 多模态 ============
_TG_MIME_BY_EXT = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "gif": "image/gif",
    "ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg",
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav",
}


def tg_download_file(file_id):
    for attempt in range(2):
        try:
            r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getFile",
                             params={"file_id": file_id}, timeout=15)
            info = r.json()
            if not info.get("ok"):
                print(f"[ERROR] getFile 失败: {info}")
                return None
            file_path = info["result"]["file_path"]
            ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
            mime = _TG_MIME_BY_EXT.get(ext, "application/octet-stream")
            blob = requests.get(f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}", timeout=30)
            if blob.status_code != 200:
                print(f"[ERROR] 文件下载 HTTP {blob.status_code}")
                if attempt == 0:
                    time.sleep(2)
                    continue
                return None
            return blob.content, mime
        except requests.exceptions.Timeout:
            print(f"[WARN] 文件下载超时 (attempt {attempt+1})")
            if attempt == 0:
                time.sleep(2)
                continue
            return None
        except Exception as e:
            print(f"[ERROR] 下载文件失败: {e}")
            return None
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
                                chat_type="", reply_reason="",
                                sender_id="", sender_is_bot=False,
                                reply_to_message_id=None):
    try:
        _start_ts = time.time()
        print(f"[PROC] 开始处理 chat={chat_id} reason={reply_reason or '-'} reply={should_reply}")
        tz = ZoneInfo(TIMEZONE)
        u_time = datetime.fromtimestamp(msg_date, tz).strftime("%Y-%m-%d %H:%M:%S") if msg_date else datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        # 历史记录文本
        if image_b64:
            n_imgs = len(image_b64) if isinstance(image_b64, list) else 1
            img_label = "[图片]" if n_imgs == 1 else f"[{n_imgs}张图片]"
            history_text = f"{img_label} {text}".rstrip() if text else img_label
        elif is_voice:
            history_text = f"[语音] {text}" if text else "[语音]"
        else:
            history_text = text

        if str(chat_id).startswith("-"):
            name_tag = f"{sender_name}(ID:{sender_id})" if sender_id else sender_name
            label = "" if sender_is_bot else get_member_label(chat_id, sender_id)
            if label:
                name_tag = f"{name_tag}【{label}】"
            msg_marker = f"[消息ID:{msg_id}] " if msg_id else ""
            formatted_input = f"{msg_marker}{name_tag}: {history_text}"
        else:
            formatted_input = history_text

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
        print(f"[TRACE] 开始加载历史 chat={chat_id}")
        history = load_history(chat_id)
        print(f"[TRACE] 历史加载完成 chat={chat_id} len={len(history)}")
        history.append({"role": "user", "content": formatted_input, "timestamp": u_time})

        # 旁听模式：只记录不回复（不读核心记忆，省API）
        if not should_reply:
            if str(chat_id).startswith("-") and msg_id:
                if random.random() < REACTION_PROBABILITY:
                    send_reaction(chat_id, msg_id, text)
            save_history(history, chat_id)
            Thread(target=hub_capture_log, args=(formatted_input, "", chat_id, msg_date)).start()
            return

        # 主人/图片触发的回复错峰起跑：几个bot随机错开几秒，晚起跑的能在上下文里
        # 看到先回的bot说了什么，自然接话、补充、拌嘴，而不是同时生成一样的内容
        if reply_reason in ("ceci", "image") and str(chat_id).startswith("-"):
            _stagger = random.uniform(0.5, 5.0)
            print(f"[STAGGER] 错峰 {_stagger:.1f}s 再生成 chat={chat_id}")
            time.sleep(_stagger)

        # 只有要回复时才读核心记忆
        # 优先从 Memory Hub 获取记忆，失败则 fallback 到 Gist
        print(f"[TRACE] 开始获取记忆 chat={chat_id}")
        recall_summary = ""
        recent_for_hub = [{"role": h["role"], "content": h["content"]} for h in history[-5:]]
        hub_memory, recall_summary = hub_get_context(text, recent_messages=recent_for_hub, chat_id=chat_id)
        print(f"[TRACE] Hub返回 chat={chat_id} got_memory={bool(hub_memory)}")
        if hub_memory:
            memory = f'【你的长期记忆——自然地参考，但绝对不要在对话中复述、引用或提及这些记忆的存在。像一个真正记住这些事的人一样，在合适的时候自然地运用，不合适就不提。不要说"我记得""根据记忆""我的记忆里"这类话。】\n{hub_memory}'
            print(f"[HUB] 记忆注入成功 ({len(hub_memory)} chars)")
        else:
            print(f"[TRACE] Hub无记忆，fallback到Gist chat={chat_id}")
            memory = fetch_memory(chat_id)
            print(f"[HUB] fallback to Gist memory")

        print(f"[DEBUG] Bot 被唤醒，调用 AI...")
        send_chat_action(chat_id, "typing")

        # 多模态图片（支持单图或相册多图）
        if image_b64:
            api_text = formatted_input or "看看这张图"
            imgs = image_b64 if isinstance(image_b64, list) else [(image_b64, image_mime or "image/jpeg")]
            user_content = []
            for b64_data, mime in imgs:
                mime = mime or "image/jpeg"
                if API_FORMAT == "openai":
                    user_content.append({"type": "image_url",
                                         "image_url": {"url": f"data:{mime};base64,{b64_data}"}})
                else:
                    user_content.append({"type": "image", "source": {"type": "base64",
                                                                     "media_type": mime,
                                                                     "data": b64_data}})
            user_content.append({"type": "text", "text": api_text})
            reply = call_claude(user_content, memory, history, u_time, is_group=str(chat_id).startswith("-"), chat_id=chat_id)
        else:
            reply = call_claude(formatted_input, memory, history, u_time, is_group=str(chat_id).startswith("-"), chat_id=chat_id)

        if not reply:
            send_telegram(chat_id, "😵 短路了，稍后再试")
            return

        # 清理 AI 回复中可能带的时间戳前缀（所有位置），并提取可选展示的思路
        reply = re.sub(r'\[202\d-[^\]]+\]\s*', '', reply.strip())
        # 清理模型模仿聊天记录格式复读出来的元信息：消息ID、回复引用、行首"名字(ID:xxx)【标签】:"前缀
        reply = re.sub(r'\[消息ID:\d+\]\s*', '', reply)
        reply = re.sub(r'\[回复[^\]]{0,100}\]\s*', '', reply)
        reply = re.sub(r'^\s*[^\n:：()（）\[\]]{1,24}\(ID:\d{5,20}\)(?:【[^】\n]{0,16}】)?\s*[:：]\s*', '', reply, flags=re.MULTILINE)
        reply = re.sub(rf'^\s*{re.escape(BOT_NAME)}\s*[:：]\s*', '', reply, flags=re.MULTILINE).strip()
        # 复读检测：模型有时会把用户刚说的原话原样带出来再回答，这些行直接删掉
        if formatted_input:
            user_flat = re.sub(r'\s+', '', formatted_input)
            kept_lines = []
            for line in reply.split('\n'):
                line_flat = re.sub(r'\s+', '', line)
                if len(line_flat) >= 4 and line_flat in user_flat:
                    continue
                kept_lines.append(line)
            reply = '\n'.join(kept_lines).strip()
        reply, cot_text = extract_thinking(reply)
        # 清理其他可能的XML风格思维标签
        reply = re.sub(r'<[a-z_]+>.*?</[a-z_]+>', '', reply, flags=re.DOTALL).strip()

        # 兜底：模型/中转站抽风时会把整段JSON当回复吐出来，尝试从里面捞正文，捞不到就不发
        if reply.startswith("{") or reply.startswith("["):
            try:
                parsed = json.loads(reply)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if parsed is not None:
                extracted = ""
                if isinstance(parsed, dict):
                    for key in ("content", "text", "reply", "message", "response", "output"):
                        val = parsed.get(key)
                        if isinstance(val, str) and val.strip():
                            extracted = val.strip()
                            break
                if extracted:
                    print("[WARN] 模型吐了JSON，已从中提取正文")
                    reply = extracted
                else:
                    print(f"[WARN] 模型吐了JSON且提取不到正文，跳过发送: {reply[:120]}")
                    save_history(history, chat_id)
                    return

        if not reply:
            print(f"[WARN] 思维链清理后为空，跳过发送")
            save_history(history, chat_id)
            return

        # 先解析后台动作标签（踢人/签名/标签/置顶/动态/日报），再清理自言自语
        action_context = {"reply_to_message_id": reply_to_message_id, "current_message_id": msg_id, "history": history, "sender_id": sender_id}
        reply = parse_and_execute_actions(reply, chat_id, action_context)
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

        # 礼让只针对"随机插嘴/关键词"这类可有可无的搭话：别的bot已经接话就不凑热闹。
        # 主人说话、发图触发的回复不礼让——大家都回，靠错峰起跑避免内容撞车。
        if (reply_reason in ("random", "trigger")
                and str(chat_id).startswith("-")
                and LAST_BOT_MSG_AT.get(str(chat_id), 0) > _start_ts):
            print(f"[YIELD] 别的bot已经接话，插嘴取消 chat={chat_id}")
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
            send_telegram_split(chat_id, reply, reply_to_message_id=reply_id, cot_text=cot_text)

        # 记录回复（标记是哪个bot说的，共享gist时能区分）
        b_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        history.append({"role": "assistant", "content": reply, "timestamp": b_time, "bot": BOT_NAME})
        LAST_SPOKE[chat_id] = time.time()  # 更新冷却计时，防bot互相刷屏
        save_history(history, chat_id)

        # Memory Hub 对话捕获（后台，不阻塞）
        # gateway 自动存储已关闭，统一走 capture 批量提取，省 LLM 成本
        Thread(target=hub_capture_log, args=(history_text, reply, chat_id, msg_date)).start()

    except Exception as e:
        import traceback
        err_trace = traceback.format_exc()
        print(f"[CRITICAL] 后台崩了: {e}\n{err_trace}")
        try:
            if should_reply:
                err_type = type(e).__name__
                err_msg = str(e)
                if "timeout" in err_msg.lower() or "Timeout" in err_type:
                    diag = f"⚠️ 超时: {err_msg[:120]}"
                elif "JSONDecode" in err_type:
                    diag = f"⚠️ API返回非JSON内容"
                elif "ConnectionError" in err_type or "ConnectionPool" in err_msg:
                    diag = f"⚠️ 连接失败: {err_msg[:120]}"
                else:
                    diag = f"⚠️ [{err_type}] {err_msg[:120]}"
                send_telegram(chat_id, diag)
        except:
            pass


# ============ 消息合并：几秒内连发的多条消息当一条处理 ============
PENDING_MERGE = {}
MERGE_LOCK = Lock()
MEDIA_GROUP_BUFFER = {}
MEDIA_GROUP_LOCK = Lock()


def _flush_album(media_group_id):
    """相册攒齐后统一下载、一次调用视觉模型、只回一条"""
    time.sleep(2.5)
    with MEDIA_GROUP_LOCK:
        item = MEDIA_GROUP_BUFFER.pop(media_group_id, None)
    if not item or not item["items"]:
        return
    chat_id = item["chat_id"]
    pairs = []
    for it in item["items"]:
        blob = tg_download_file(it["file_id"])
        if blob:
            raw, mime = blob
            pairs.append((base64.b64encode(raw).decode(),
                          mime if mime.startswith("image/") else "image/jpeg"))
    if not pairs:
        send_telegram(chat_id, "⚠️ 相册没收下来，Telegram下载超时了，再发一次试试？",
                      reply_to_message_id=item["first_msg_id"])
        return
    captions = " ".join(it["caption"] for it in item["items"] if it["caption"]).strip()
    cid = str(chat_id)
    if not cid.startswith("-"):
        chat_type = "private"
    elif cid in PRIVATE_CHATS:
        chat_type = "small_group"
    else:
        chat_type = "big_group"
    print(f"[ALBUM] 相册合并处理 {len(pairs)} 张图 chat={chat_id}")
    Thread(target=process_message_background,
           args=(captions, chat_id, item["sender_name"], item["msg_date"], True,
                 item["first_msg_id"], pairs, None, False, False,
                 chat_type, "image", item["sender_id"], item["sender_is_bot"],
                 None)).start()


def _buffer_album_photo(media_group_id, msg, chat_id, sender_name, sender_id, sender_is_bot):
    largest = msg["photo"][-1]
    entry = {"file_id": largest.get("file_id", ""), "caption": msg.get("caption", "") or ""}
    with MEDIA_GROUP_LOCK:
        buf = MEDIA_GROUP_BUFFER.get(media_group_id)
        if buf:
            buf["items"].append(entry)
            return
        MEDIA_GROUP_BUFFER[media_group_id] = {
            "items": [entry], "chat_id": chat_id, "sender_name": sender_name,
            "sender_id": sender_id, "sender_is_bot": sender_is_bot,
            "msg_date": msg.get("date"), "first_msg_id": msg.get("message_id"),
        }
    Thread(target=_flush_album, args=(media_group_id,), daemon=True).start()


def _flush_pending(key):
    try:
        with MERGE_LOCK:
            item = PENDING_MERGE.pop(key, None)
        if not item or not item["msgs"]:
            return
        msgs = item["msgs"]
        last = msgs[-1]
        text = "\n".join(m["text"] for m in msgs if m["text"])
        should_reply = any(m["should_reply"] for m in msgs)
        reply_reason = next((m["reply_reason"] for m in msgs if m["reply_reason"]), "")
        directed_at_other = all(m["directed_at_other"] for m in msgs)
        print(f"[MERGE] 刷新缓冲 {key}：{len(msgs)}条，should_reply={should_reply}")
        Thread(target=process_message_background,
               args=(text, last["chat_id"], last["sender_name"], last["msg_date"],
                     should_reply, last["msg_id"], None, None, False,
                     directed_at_other, last["chat_type"], reply_reason,
                     last["sender_id"], last["sender_is_bot"],
                     last["reply_to_message_id"])).start()
    except Exception as e:
        import traceback
        print(f"[CRITICAL] 合并刷新崩了: {e}\n{traceback.format_exc()}")


def _merge_timer(key):
    try:
        time.sleep(MESSAGE_MERGE_SECONDS)
        _flush_pending(key)
    except Exception as e:
        import traceback
        print(f"[CRITICAL] 合并定时器崩了: {e}\n{traceback.format_exc()}")


def enqueue_message(text, chat_id, sender_name, msg_date, should_reply, msg_id,
                    image_b64, image_mime, is_voice, directed_at_other,
                    chat_type, reply_reason, sender_id, sender_is_bot,
                    reply_to_message_id):
    """同一个人几秒内连发的文字消息攒起来一起处理，更像真人的阅读节奏。
    图片/语音/引用回复的消息不合并，但会先把攒着的消息冲出去，保证顺序。"""
    key = (str(chat_id), str(sender_id) or sender_name)
    mergeable = (MESSAGE_MERGE_SECONDS > 0 and not image_b64 and not is_voice
                 and not reply_to_message_id)
    if not mergeable:
        _flush_pending(key)
        Thread(target=process_message_background,
               args=(text, chat_id, sender_name, msg_date, should_reply, msg_id,
                     image_b64, image_mime, is_voice, directed_at_other,
                     chat_type, reply_reason, sender_id, sender_is_bot,
                     reply_to_message_id)).start()
        return
    entry = {"text": text, "chat_id": chat_id, "sender_name": sender_name,
             "msg_date": msg_date, "should_reply": should_reply, "msg_id": msg_id,
             "directed_at_other": directed_at_other, "chat_type": chat_type,
             "reply_reason": reply_reason, "sender_id": sender_id,
             "sender_is_bot": sender_is_bot, "reply_to_message_id": reply_to_message_id}
    stale_flush = False
    with MERGE_LOCK:
        item = PENDING_MERGE.get(key)
        if item:
            item["msgs"].append(entry)
            print(f"[MERGE] 追加到缓冲 {key}，共{len(item['msgs'])}条")
            # 自愈：缓冲早该刷新了却还在（定时器线程可能挂了），立刻补刷
            if time.time() - item.get("created_at", 0) > MESSAGE_MERGE_SECONDS * 3:
                stale_flush = True
            if not stale_flush:
                return
        else:
            PENDING_MERGE[key] = {"msgs": [entry], "created_at": time.time()}
    if stale_flush:
        print(f"[MERGE] 缓冲超时未刷新，自愈补刷 {key}")
        _flush_pending(key)
        return
    Thread(target=_merge_timer, args=(key,), daemon=True).start()

# ============ 召唤转告 ============
CECI_SEEN = {}  # chat_id -> 主人最后一次说话时间
LAST_CECI_NOTIFY = {}
CECI_NOTIFY_INTERVAL = 3600


CECI_NOTIFY_DELAY = int(os.environ.get("CECI_NOTIFY_DELAY", "900"))


def _claim_ceci_notify(chat_id):
    """跨bot协调：在共享state里占坑，谁占到谁去转告，其他bot不重复打扰"""
    try:
        state, _, _ = _read_state_json(str(chat_id))
        info = state.get("ceci_notify", {}) if isinstance(state.get("ceci_notify"), dict) else {}
        last = info.get(str(chat_id), {}) if isinstance(info.get(str(chat_id)), dict) else {}
        if time.time() - last.get("ts", 0) < 1800:
            print(f"[SUMMON] {last.get('by', '别的bot')} 已经转告过了，跳过")
            return False
        info[str(chat_id)] = {"ts": time.time(), "by": BOT_NAME}
        state["ceci_notify"] = info
        _write_state_json(str(chat_id), state)
        return True
    except Exception as e:
        print(f"[SUMMON] 占坑检查失败，按可转告处理: {e}")
        return True


def maybe_notify_ceci(chat_id, text, sender_name, sender_is_bot):
    """有人在群里提到主人：先等一刻钟，她自己冒头就作罢；
    真没出现再由抢到坑的那一个bot私聊转告（每群每小时最多一次）"""
    if not CECI_ID or sender_is_bot or not text:
        return
    if not str(chat_id).startswith("-"):
        return
    names = [n for n in (USER_NAME, USER_TG_NAME) if n and n != "主人"]
    if not names or not any(n in text for n in names):
        return
    mention_ts = time.time()
    if mention_ts - LAST_CECI_NOTIFY.get(str(chat_id), 0) < CECI_NOTIFY_INTERVAL:
        return
    # 等一刻钟（加随机抖动错开几个bot），期间她在任何聊天露过面就不打扰
    time.sleep(CECI_NOTIFY_DELAY + random.uniform(0, 120))
    if max(CECI_SEEN.values(), default=0) > mention_ts:
        print(f"[SUMMON] 主人自己已经冒头了，不用转告 chat={chat_id}")
        return
    if time.time() - LAST_CECI_NOTIFY.get(str(chat_id), 0) < CECI_NOTIFY_INTERVAL:
        return
    if not _claim_ceci_notify(chat_id):
        return
    LAST_CECI_NOTIFY[str(chat_id)] = time.time()
    where = "私密群" if str(chat_id) in PRIVATE_CHATS else "大群"
    preview = text[:80]
    send_telegram(CECI_ID, f"来报个信：{sender_name}之前在{where}提到你——「{preview}」你好像还没看到，来瞄一眼？")
    print(f"[SUMMON] 已私聊转告主人 from chat={chat_id}")


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

    if data and "callback_query" in data:
        callback_data = data["callback_query"].get("data", "")
        if callback_data.startswith("cot:"):
            handle_cot_callback(data["callback_query"])
        return "ok"

    if not data or "message" not in data:
        return "ok"

    msg = data["message"]
    
    # 去重：Telegram可能因为响应慢而重发
    msg_unique_id = str(msg.get("message_id", "")) + "_" + str(msg.get("chat", {}).get("id", ""))
    with PROCESSED_LOCK:
        if msg_unique_id in PROCESSED_MESSAGES:
            print(f"[DEDUP] 跳过重复消息: {msg_unique_id}")
            return "ok"
        PROCESSED_MESSAGES.add(msg_unique_id)
        if len(PROCESSED_MESSAGES) > 500:
            to_remove = list(PROCESSED_MESSAGES)[:300]
            PROCESSED_MESSAGES.difference_update(to_remove)

    chat_id = str(msg.get("chat", {}).get("id", ""))

    if ALLOWED_IDS and chat_id not in ALLOWED_IDS:
        return "ok"

    sender_name, sender_id, sender_is_bot, sender_username = get_message_sender_info(msg)

    # 顺手记录 名字/@用户名 → ID 的映射，AI 改称呼时可以直接写名字，由代码解析成ID
    if sender_id and str(sender_id) != BOT_ID and sender_name:
        _nm = USER_NAME_MAP.setdefault(chat_id, {})
        _nm[sender_name.lower()] = str(sender_id)
        if sender_username:
            _nm[f"@{sender_username}"] = str(sender_id)

    # 忽略自己发的消息（开了Bot to Bot后会收到自己的回复）
    if BOT_USERNAME and sender_username == BOT_USERNAME.lower():
        return "ok"

    if sender_is_bot:
        LAST_BOT_MSG_AT[str(chat_id)] = time.time()

    user_text = msg.get("text", "") or msg.get("caption", "") or ""
    image_b64 = None
    image_mime = None
    is_voice = False

    # 图片
    if "photo" in msg and msg["photo"]:
        largest = msg["photo"][-1]
        media_group_id = msg.get("media_group_id")
        if media_group_id:
            # 相册：多张图是多条消息，攒 2.5 秒合并成一次处理，只回一条
            _buffer_album_photo(media_group_id, msg, chat_id, sender_name, sender_id, sender_is_bot)
            return "ok"
        blob = tg_download_file(largest.get("file_id", ""))
        if blob:
            raw, mime = blob
            image_b64 = base64.b64encode(raw).decode()
            image_mime = mime if mime.startswith("image/") else "image/jpeg"
        else:
            print(f"[WARN] 图片下载失败，file_id={largest.get('file_id', '')[:20]}")
            send_telegram(chat_id, "⚠️ 图片没收到，Telegram下载超时了，再发一次试试？",
                          reply_to_message_id=msg.get("message_id"))

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

    # /tags 诊断命令：列出本群记录的所有成员标签映射，排查张冠李戴
    if user_text.strip().lower() == "/tags" and chat_id.startswith("-"):
        labels = get_member_labels(chat_id, force_refresh=True)
        if not labels:
            send_telegram(chat_id, "本群还没有任何成员标签记录",
                          reply_to_message_id=msg.get("message_id"))
        else:
            rows = [f"- {_get_member_display(chat_id, uid_)} → 「{label_}」"
                    for uid_, label_ in list(labels.items())[:20]]
            send_telegram(chat_id, "本群的成员标签记录：\n" + "\n".join(rows),
                          reply_to_message_id=msg.get("message_id"))
        return "ok"

    # /untag [ID] 修复命令：清掉某人的标签（Telegram 可见标签 + 内部记录一起清）
    if user_text.strip().lower().startswith("/untag") and chat_id.startswith("-"):
        parts = user_text.strip().split()
        target_id = parts[1] if len(parts) >= 2 else sender_id
        if target_id and re.fullmatch(r"\d{5,20}", str(target_id)):
            if str(target_id) != str(sender_id) and not is_chat_admin(chat_id, sender_id):
                send_telegram(chat_id, "只能清自己的标签，清别人的需要管理员",
                              reply_to_message_id=msg.get("message_id"))
                return "ok"
            _, note = set_member_display_name(chat_id, str(target_id), "")
            set_member_label(chat_id, str(target_id), "", set_by=sender_id)
            send_telegram(chat_id, note, reply_to_message_id=msg.get("message_id"))
        else:
            send_telegram(chat_id, "用法：/untag 用户ID（不带ID就是清自己的）",
                          reply_to_message_id=msg.get("message_id"))
        return "ok"

    # /testadmin 诊断命令：测试bot在当前群是否有管理员权限
    if user_text.strip().lower() == "/testadmin":
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{TG_TOKEN}/getChatMember",
                params={"chat_id": chat_id, "user_id": BOT_ID},
                timeout=10,
            )
            result = resp.json()
            member = result.get("result", {})
            status = member.get("status", "unknown")
            can_restrict = member.get("can_restrict_members", False)
            can_pin = member.get("can_pin_messages", False)
            can_delete = member.get("can_delete_messages", False)
            can_manage = member.get("can_manage_chat", False)
            can_promote = member.get("can_promote_members", False)
            can_manage_tags = member.get("can_manage_tags", False)
            wh_url = ""
            try:
                wh_info = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getWebhookInfo", timeout=10).json()
                wh_url = (wh_info.get("result") or {}).get("url", "")
            except Exception:
                pass
            send_telegram(chat_id,
                f"🔍 诊断结果:\n"
                f"- Bot ID: {BOT_ID}\n"
                f"- 群ID: {chat_id}\n"
                f"- 身份: {status}\n"
                f"- 可以踢/封禁成员: {can_restrict}\n"
                f"- 可以置顶消息: {can_pin}\n"
                f"- 可以删除消息: {can_delete}\n"
                f"- 可以管理群: {can_manage}\n"
                f"- 可以提升/编辑管理员: {can_promote}\n"
                f"- 可以管理成员标签: {can_manage_tags}\n"
                f"- 服务地址: {wh_url or '未知'}\n"
                f"- 主人ID(CECI_ID): {CECI_ID or '未设置'}",
                reply_to_message_id=msg.get("message_id"))
        except Exception as e:
            send_telegram(chat_id, f"❌ 诊断失败: {e}", reply_to_message_id=msg.get("message_id"))
        return "ok"

    # 群聊逻辑
    should_reply = True
    reply_reason = ""
    user_id = sender_id
    is_ceci = (CECI_ID and user_id == CECI_ID)
    if is_ceci:
        CECI_SEEN[str(chat_id)] = time.time()

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
        replied_user_id = str(replied.get("from", {}).get("id", ""))
        if replied_user_id and replied_user_id != BOT_ID and replied_name:
            _nm = USER_NAME_MAP.setdefault(chat_id, {})
            _nm[replied_name.lower()] = replied_user_id
            if replied_username:
                _nm[f"@{replied_username}"] = replied_user_id
        if replied_name and replied_text and user_text:
            reply_preview = replied_text[:60]
            replied_tag = f"{replied_name}(ID:{replied_user_id})" if replied_user_id else replied_name
            user_text = f"[回复{replied_tag}: {reply_preview}] {user_text}"

        # 是否回复的是我自己的消息
        replied_to_me = BOT_USERNAME and replied_username == BOT_USERNAME.lower()
        # 是否回复的是其他bot的消息
        replying_to_other_bot = replied_is_bot and not replied_to_me

        is_mentioned = BOT_USERNAME and f"@{BOT_USERNAME.lower()}" in user_text.lower()
        # 检查是否@了别的bot（不是我）
        has_any_at = bool(re.search(r'@\w+', user_text))
        mentioning_other = has_any_at and not is_mentioned

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

        # 其他bot发的动作回执（✅/⚠️/ℹ️开头的行）是系统消息，不接茬
        if should_reply and sender_is_bot and re.search(r'(?m)^\s*[✅⚠ℹ]', user_text):
            should_reply = False
            reply_reason = ""

        # 群里有图必回
        if image_b64:
            should_reply = True
            if not reply_reason:
                reply_reason = "image"
    else:
        reply_reason = "private"

    if chat_id.startswith("-"):
        print(f"[DECIDE] chat={chat_id} sender={sender_id} is_ceci={bool(is_ceci)} ceci_id_set={bool(CECI_ID)} reply={should_reply} reason={reply_reason or '-'}")

    # 标记：只有回复了别的bot的消息才完全禁止插嘴
    directed_at_other = False
    if chat_id.startswith("-"):
        directed_at_other = replying_to_other_bot

    # 有人喊主人而她不在场：私聊转告
    if not is_ceci and user_text:
        Thread(target=maybe_notify_ceci, args=(chat_id, user_text, sender_name, sender_is_bot), daemon=True).start()

    msg_date = msg.get("date")
    msg_id = msg.get("message_id")
    reply_to_message_id = (msg.get("reply_to_message") or {}).get("message_id")

    # 重启/冷启动后 Telegram 会重投积压的旧消息：太旧的只记历史不回复，避免翻旧账式复读
    # 点名类（@我/回复我/私聊）放宽到2小时——服务睡觉期间错过的消息，晚回也该回
    if msg_date and should_reply:
        _age = time.time() - msg_date
        _direct = reply_reason in ("mentioned", "replied", "private")
        if (_direct and _age > 7200) or (not _direct and _age > MAX_MESSAGE_AGE):
            print(f"[DEDUP] 消息太旧({int(_age)}s, reason={reply_reason})，只记录不回复")
            should_reply = False
            reply_reason = ""

    LAST_CHAT_ACTIVITY[str(chat_id)] = time.time()

    try:
        enqueue_message(user_text, chat_id, sender_name, msg_date, should_reply, msg_id,
                        image_b64, image_mime, is_voice, directed_at_other,
                        chat_type, reply_reason, sender_id, sender_is_bot,
                        reply_to_message_id)
    except Exception as _eq_err:
        import traceback
        print(f"[CRITICAL] 消息入队失败，直接处理兜底: {_eq_err}\n{traceback.format_exc()}")
        Thread(target=process_message_background,
               args=(user_text, chat_id, sender_name, msg_date, should_reply, msg_id,
                     image_b64, image_mime, is_voice, directed_at_other,
                     chat_type, reply_reason, sender_id, sender_is_bot,
                     reply_to_message_id)).start()
    if not should_reply:
        maybe_proactive_post(chat_id)
    Thread(target=self_heal_webhook).start()
    return "ok"


@app.route("/health", methods=["GET"])
def health():
    return "alive"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
