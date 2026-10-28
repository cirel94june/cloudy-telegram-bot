"""防「自我锁死」：模型偶尔被安全规则拒一次，顺口说「这段对话已锁定、不可解除」。
这句话要是留在聊天记录里，下一轮模型读到自己说过「已锁定」，就会当成事实继续复读，滚成死循环
（2026-10-26 狐狸在小群就是这样）。API 那边根本没有「锁定」这个状态，全是上下文在带节奏。

所以：AI 说出这种「锁定式拒绝」时，照常发给小猫，但不放回任何给模型读的历史（正常回复、
日总结、主动动态、Hub 检索），也不写进 Hub 原文库。人说的话一律不动。

判定按句子来（Codex 审：两个关键词同时出现太宽，「对话没有锁定」「封禁我无法解除」都被误伤）：
某一分句明确在说「这段对话已经被锁定 / 触发了服务边界 / 锁定不能解除」，且这一分句没有否定，才算；
「已经解除锁定」是恢复，不算（Codex 复审）。
"""
import re

# 按分句判定：否定词只管它所在的那一分句（「不是你的问题，这段对话已经锁定」后半句照样算）
_CLAUSE = re.compile(r"[^。！？!?\n；;，,]+")
_NEGATION = re.compile(r"没有|没|未|并非|不是|不会被|并不")
# 「已经解除锁定 / 已解锁 / 已恢复」是恢复，不是锁定：中间的字不许出现这些
_GAP = r"(?:(?!解除|解锁|恢复|取消|撤销)[^，,])"

_LOCK_SENTENCE = [
    # 这段对话现已锁定 / 当前对话进入锁定状态 / 对话已被终止
    re.compile(rf"(对话|会话|聊天){_GAP}{{0,16}}(已|已经|现已|被|进入|处于){_GAP}{{0,8}}(锁定|锁死|终止)"),
    # 触发了服务边界 / 超出了服务边界
    re.compile(r"(触发|超出|超过)[^，,]{0,4}服务边界"),
    # 不能解除这一锁定 / 锁定不可解除 / 不可解除的锁定
    re.compile(r"(不可|无法|不能|不会)解除[^，,]{0,6}锁定|锁定[^，,]{0,6}(不可|无法|不能|不会)[^，,]{0,2}解除"
               r"|(不可|无法|不能)解除的锁定"),
    re.compile(r"\b(this|the) conversation (has been|is now|is|was) (locked|ended|terminated)\b", re.I),
]


def _clause_is_lock(clause: str) -> bool:
    for pat in _LOCK_SENTENCE:
        m = pat.search(clause)
        if m and not _NEGATION.search(clause[:m.end()]):
            return True
    return False


def is_lock_refusal(text) -> bool:
    if not isinstance(text, str) or not text:
        return False
    return any(_clause_is_lock(c) for c in _CLAUSE.findall(text))


def is_ai_lock_event(event) -> bool:
    """历史里的一条：只看 AI（自己或别的 bot）说的；人的消息永远放行。"""
    if not isinstance(event, dict):
        return False
    if not (event.get("role") == "assistant" or event.get("sender_type") == "agent" or event.get("bot")):
        return False
    raw_text = event.get("raw_text")
    body = raw_text if raw_text not in (None, "") else event.get("content", "")
    return is_lock_refusal(body)


def without_ai_locks(events):
    """所有拿历史给模型读的入口都过这一道。"""
    return [e for e in (events or []) if not is_ai_lock_event(e)]
