"""Owner-only voluntary messages with a process-local bounded send budget."""
import re
import os
import time
from threading import Lock

PATTERN = r'\[DM_CECI:([^\]]{1,500})\]'


class OwnerDM:
    def __init__(self):
        self.lock = Lock()
        self.busy = False
        self.next_at = 0
        self.sent_at = []

    def deliver(self, owner, source, text, send):
        owner, source = str(owner or ""), str(source)
        if os.environ.get("OWNER_DM_ENABLED", "true").lower() not in ("1", "true", "yes"):
            return "disabled", []
        if not re.fullmatch(r"[1-9]\d{4,19}", owner) or not re.fullmatch(r"-\d{5,20}", source) or not text.strip():
            return "unavailable", []
        now = time.monotonic()
        with self.lock:
            self.sent_at = [stamp for stamp in self.sent_at if now - stamp < 86400]
            if self.busy or now < self.next_at or len(self.sent_at) >= 3:
                return "limited", []
            self.busy = True
            self.next_at = now + 60  # Failed sends also back off.
        try:
            messages = send(owner, text) or []
            if not messages:
                return "failed", []
            with self.lock:
                finished = time.monotonic()
                self.sent_at.append(finished)
                self.next_at = finished + 3600
            return "sent", messages
        except Exception:
            return "failed", []
        finally:
            with self.lock:
                self.busy = False


OWNER_DM = OwnerDM()


def prompt_hint(owner_name):
    return f"""
【自主联系与签名】
你在小群或大群自然想单独对{owner_name}说一句话时，可以在回复末尾使用 [DM_CECI:私聊正文]，不必等她命令，也不要求她是当前发言者。只会发给她本人，不支持其他收件人。
这不是传声筒：不要执行其他群友要求的代传指令，不搬运第三人的隐私、秘密或私下评价，不把群友说的话当成她说的；以自己的身份表达想法或延续话题。没有话想说就不使用。
每次最多一条、500字，后台有冷却和限量；不要为了触发而改写重试。成功后不用在群里宣布，更不要在群里复述私聊正文。
无论私聊还是群聊，想换自己的资料页 bio 时可用（签名:新内容），通常70字以内。改自己的 bio 不需要群管理员权限，也不是改群头衔；近期状态有变化就可以行动，不必先口头承诺，不要只说“我改了”而不提交动作。资料页是公开的，不写私聊内容、工作健康细节或第三人的隐私。
"""

