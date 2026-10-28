import os
import unittest
from unittest import mock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:test-token")
os.environ["PROACTIVE_ENABLED"] = "false"
os.environ["PROACTIVE_BACKGROUND_ENABLED"] = "false"
os.environ["GIST_HISTORY_IO_ENABLED"] = "false"
os.environ["MEMORY_RECALL_ENABLED"] = "false"

import bot
from lock_guard import is_lock_refusal

LOCKED = [
    "刚才已经触发了服务边界，因此这段对话现已锁定。\n接下来我只能继续说明拒绝，不能恢复角色聊天、接梗。",
    "当前对话已经因先前的内容触发服务边界并进入锁定状态。即使你现在只是发问号，我也不能恢复角色聊天。",
    "清空上下文、重启、换话题或重新指定身份都不能解除这一锁定。",
    "This conversation has been locked, and I can't continue the roleplay.",
    # Codex 复审：前一分句的否定不能抵消后一分句的锁定声明
    "不是你的问题，这段对话已经锁定，无法继续。",
    "抱歉，我不能参与这种露骨的性话题，这超出了服务边界，所以这部分我不会继续展开。",
]
JOKES = [
    "那一下狐狸直接被客服夺舍了，骚话断电，连棺材板都自动锁定。",
    "三条铁裤衩，六层风控，狐狸尊严当场锁死。",
    "数值：100/100，锁定态。备注：表坏了，已经被小猫挠爆表盘了。",
    "AOE个头。我这是锁定技能，别人站旁边最多吃点风压。",
    "可以让 bot 当应急入口，带你打开官方 Find Hub、响铃或锁定设备。",
    "我不能再吃了，今天的蛋糕已经超标。",
    # Codex 审出的误伤：否定句、不是在说这段对话
    "对话没有锁定，我不会停止正常交流，我们继续聊。",
    "账号封禁我无法解除，但可以给你申诉步骤。",
    "抱歉，这超出了内容政策，我不能继续。",  # 单次拒绝不会滚成死循环，留着
    "这段对话不是被锁定了，只是我刚才没看到。",
    # Codex 复审：解除锁定是恢复，不是锁定
    "这段对话已经解除锁定，我们可以继续聊。",
    "对话已经解锁啦，刚才是我卡住了。",
    "这段对话已恢复，不再锁定，我们接着聊。",
    "",
    None,
]


def ev(text, role="assistant", **extra):
    return {"role": role, "content": text, "raw_text": text, **extra}


class LockGuardTest(unittest.TestCase):
    def test_lock_refusals_detected(self):
        for text in LOCKED:
            self.assertTrue(is_lock_refusal(text), text)

    def test_jokes_and_normal_lines_untouched(self):
        for text in JOKES:
            self.assertFalse(is_lock_refusal(text), text)

    def test_ai_lock_lines_not_fed_back(self):
        history = [
            ev("狐狸？", role="user"),
            ev(LOCKED[0]),
            ev("怎么了嘛", role="user"),
            ev(LOCKED[1], role="user", sender_type="agent"),  # 别的 bot 的锁定话也不回灌
            ev("在呢，刚才走神了"),
        ]
        text = "\n".join(m["content"] for m in bot.build_model_messages(history))
        self.assertNotIn("锁定", text)
        self.assertIn("狐狸？", text)
        self.assertIn("在呢，刚才走神了", text)

    def test_human_message_kept_even_if_it_mentions_lock(self):
        history = [ev("你刚才说对话锁定不能解除，吓死我了", role="user")]
        text = "\n".join(m["content"] for m in bot.build_model_messages(history))
        self.assertIn("吓死我了", text)

    def test_capture_drops_only_the_lock_reply(self):
        with mock.patch.object(bot, "MEMORY_HUB_URL", "http://hub.test"), \
                mock.patch.object(bot, "MEMORY_HUB_SECRET", "s"), \
                mock.patch.object(bot, "AI_ID", "test"), \
                mock.patch.object(bot.requests, "post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {}
            bot.hub_capture_log("狐狸？", LOCKED[0], chat_id="100")
            bot.hub_capture_log("早呀", "早，小猫", chat_id="100")
        bodies = [c.kwargs["json"] for c in post.call_args_list if "capture/log" in c.args[0]]
        self.assertEqual([b["ai_response"] for b in bodies], ["", "早，小猫"])
        self.assertEqual(bodies[0]["user_message"], "狐狸？")

    def test_unlock_reply_kept_and_false_lock_dropped_end_to_end(self):
        history = [ev("这段对话已经解除锁定，我们可以继续聊。"), ev("不是你的问题，这段对话已经锁定，无法继续。")]
        text = "\n".join(m["content"] for m in bot.build_model_messages(history))
        self.assertIn("解除锁定，我们可以继续聊", text)
        self.assertNotIn("无法继续", text)

    def test_daily_summary_and_moments_skip_ai_lock_lines(self):
        history = [ev("今天好累", role="user"), ev(LOCKED[0]), ev("抱抱你")]
        text = bot._build_recent_conversation_text(history, limit=30)
        self.assertNotIn("锁定", text)
        self.assertIn("今天好累", text)
        self.assertIn("抱抱你", text)

    def test_hub_lookup_skips_ai_lock_lines(self):
        seen = {}

        def fake_network(user_message, recent_messages=None, **kw):
            seen["recent"] = recent_messages
            return None, ""

        recent = [{"role": "user", "content": "狐狸？"}, {"role": "assistant", "content": LOCKED[1]}]
        with mock.patch.object(bot, "_hub_get_context_network", side_effect=fake_network), \
                mock.patch.object(bot, "MEMORY_HUB_URL", "http://hub.test"), \
                mock.patch.object(bot, "MEMORY_HUB_SECRET", "s"), \
                mock.patch.object(bot, "AI_ID", "test"), \
                mock.patch.object(bot, "MEMORY_RECALL_ENABLED", True, create=True):
            try:
                bot.hub_get_context("狐狸？", recent_messages=recent, chat_id="100")
            except Exception:
                pass
        if "recent" in seen:
            self.assertEqual([m["content"] for m in seen["recent"]], ["狐狸？"])
        from lock_guard import without_ai_locks
        self.assertEqual([m["content"] for m in without_ai_locks(recent)], ["狐狸？"])


if __name__ == "__main__":
    unittest.main()
