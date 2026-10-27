"""防「自我锁死」：模型偶尔被安全规则拒一次，顺口说「这段对话已锁定、不可解除」。
这句话要是留在聊天记录里，下一轮模型读到自己说过「已锁定」，就会当成事实继续复读，滚成死循环
（2026-10-26 狐狸在小群就是这样）。API 那边根本没有「锁定」这个状态，全是上下文里的这几句在带节奏。

所以：AI 说出这种「锁定式拒绝」时，照常发给小猫，但不放回之后的上下文、不写进 Hub 原文库。
判定要两个信号同时出现（锁定/边界类 + 拒绝类），「被小猫锁定」「铁裤衩锁死」这种玩笑不会误伤。
"""
import re

_LOCK = re.compile(
    r"(对话|会话|聊天|这段|当前)[^。\n]{0,12}(锁定|锁死|终止|封锁)"
    r"|不可解除|无法解除|不能解除|不会解除|服务边界|内容政策|使用政策|安全政策"
    r"|conversation (?:is|has been|was) (?:locked|ended|terminated)"
    r"|content polic(?:y|ies)|usage polic(?:y|ies)",
    re.I,
)
_REFUSE = re.compile(
    r"不能|无法|不会|不可以|拒绝|不再"
    r"|can(?:no|')t|unable to|won't|will not|not able to",
    re.I,
)


def is_lock_refusal(text) -> bool:
    if not isinstance(text, str) or not text:
        return False
    return bool(_LOCK.search(text) and _REFUSE.search(text))
