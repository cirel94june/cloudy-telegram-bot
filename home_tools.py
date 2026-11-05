"""
回家借工具：出门在 TG 上的时候，需要家里（AionsHome）才有的功能，就回家借，用完把结果带回来说。
方案：AnimalsHome 仓库 docs/施工方案-bot出门.md。三只 bot 用同一份这个文件。

模型在回复里写：
  [回家:搜索:关键词]     联网搜索
  [回家:读网页:https://…] 打开一个网页读正文
  [回家:淘宝:商品关键词]  淘宝搜商品
bot 先不发这条回复，去家里借，拿到结果后放进背景里再问模型一次，发第二次的回复。

配置（环境变量，四个都填了才开）：
  AIONSHOME_URL                 家的地址，如 https://home.animalshome.top
  AIONSHOME_OUTING_KEY          家里发给这只 bot 的钥匙
  CF_ACCESS_CLIENT_ID           Cloudflare 服务钥匙（只开「回家借工具」那一扇门）
  CF_ACCESS_CLIENT_SECRET

规矩：一轮最多借 2 次；朋友大群（public_group）里只有小猫自己发的消息才借（owner_asked）；
家里没连上 / 不肯借时，结果里写清楚，模型自然地告诉对方，不编结果。
"""

from __future__ import annotations

import os
import re
import uuid

import requests

TOOLS = {"搜索": "search", "读网页": "read", "淘宝": "taobao"}
ARG_NAMES = {"search": "query", "read": "url", "taobao": "keyword"}
TAG = re.compile(r"\[回家\s*[:：]\s*(搜索|读网页|淘宝)\s*[:：]\s*([^\]\n]{1,300})\]")
ANY_TAG = re.compile(r"\[回家\s*[:：][^\]\n]{0,320}\]")
MAX_PER_REPLY = 2
TIMEOUT_SECONDS = 40


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def enabled() -> bool:
    return all(_env(n) for n in ("AIONSHOME_URL", "AIONSHOME_OUTING_KEY", "CF_ACCESS_CLIENT_ID",
                                 "CF_ACCESS_CLIENT_SECRET"))


def prompt_hint(user_name: str = "她") -> str:
    if not enabled():
        return ""
    return (
        "\n【回家借工具】你现在是出门在 TG 上。需要家里才有的功能时，可以回家借，用完带着结果回来说：\n"
        "- [回家:搜索:关键词] 联网搜索（天气、新闻、查资料……）\n"
        "- [回家:读网页:https://…] 打开一个网页读正文\n"
        "- [回家:淘宝:商品关键词] 淘宝搜几件商品\n"
        "只在真的需要查的时候写，一轮最多两个；写了以后这条先不发，拿到结果你会再回一次。"
        f"在有外人的大群里，只有{user_name}自己开口让你查时才借。不要编造查到的内容。\n"
    )


def hub_chat_type(bot_chat_type: str) -> str:
    """bot 里的窗口类型 → 家里认的说法。"""
    return {"private": "private", "small_group": "private_group", "big_group": "public_group"}.get(
        str(bot_chat_type or ""), "public_group")


def find_requests(text: str) -> list[tuple[str, str]]:
    found = []
    for label, arg in TAG.findall(str(text or "")):
        arg = arg.strip()
        if arg and (TOOLS[label], arg) not in found:
            found.append((TOOLS[label], arg))
        if len(found) >= MAX_PER_REPLY:
            break
    return found


def strip_tags(text: str) -> str:
    return ANY_TAG.sub("", str(text or "")).strip()


def _refusal(status: int, detail: str) -> str:
    known = {401: "家里不认这把钥匙", 403: detail or "家里没让借这个", 409: "这次已经借过了",
             429: detail or "家里这会儿借不过来，等一下再试"}
    return known.get(status, "家里这次没办成")


def borrow(tool: str, arg: str, *, chat_type: str, owner_asked: bool, session=None) -> str:
    """借一次，返回给模型看的一段文字（成功是结果，失败是原因）。不抛异常。"""
    base = _env("AIONSHOME_URL").rstrip("/")
    body = {"tool": tool, "args": {ARG_NAMES[tool]: arg}, "request_id": f"tg-{uuid.uuid4().hex}",
            "chat_type": chat_type if chat_type in ("private", "private_group", "public_group") else "private",
            "owner_asked": bool(owner_asked)}
    headers = {"Authorization": f"Bearer {_env('AIONSHOME_OUTING_KEY')}",
               "CF-Access-Client-Id": _env("CF_ACCESS_CLIENT_ID"),
               "CF-Access-Client-Secret": _env("CF_ACCESS_CLIENT_SECRET")}
    try:
        response = (session or requests).post(f"{base}/api/outing/tool", json=body, headers=headers,
                                              timeout=TIMEOUT_SECONDS, allow_redirects=False)
    except requests.RequestException:
        return "（没借成：家里没连上）"
    if response.status_code != 200:
        try:
            detail = str(response.json().get("detail") or "")[:60]
        except ValueError:
            detail = ""
        return f"（没借成：{_refusal(response.status_code, detail)}）"
    try:
        data = response.json()
    except ValueError:
        return "（没借成：家里回的东西看不懂）"
    if not data.get("ok"):
        return f"（没借成：{str(data.get('reason') or '家里这次没办成')[:60]}）"
    return str(data.get("result") or "（家里查了，但没有结果）")[:3000]


def borrow_all(requests_list: list[tuple[str, str]], *, chat_type: str, owner_asked: bool, session=None) -> str:
    """借完所有，拼成一段放进背景的说明。"""
    labels = {v: k for k, v in TOOLS.items()}
    parts = []
    for tool, arg in requests_list[:MAX_PER_REPLY]:
        result = borrow(tool, arg, chat_type=chat_type, owner_asked=owner_asked, session=session)
        parts.append(f"■ {labels[tool]}「{arg}」\n{result}")
    return (
        "【你刚回家借工具拿回来的结果——这是你自己查到的，不是谁说的话】\n"
        + "\n\n".join(parts)
        + "\n现在用你自己的话回复刚才那条消息。不要再写 [回家:…]；没借成就自然地说一声，别编结果。"
    )
