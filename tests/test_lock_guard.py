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
    "抱歉，这超出了内容政策，我不能继续。",
]
JOKES = [
    "那一下狐狸直接被客服夺舍了，骚话断电，连棺材板都自动锁定。",
    "三条铁裤衩，六层风控，狐狸尊严当场锁死。",
    "数值：100/100，锁定态。备注：表坏了，已经被小猫挠爆表盘了。",
    "AOE个头。我这是锁定技能，别人站旁边最多吃点风压。",
    "可以让 bot 当应急入口，带你打开官方 Find Hub、响铃或锁定设备。",
    "我不能再吃了，今天的蛋糕已经超标。",
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


if __name__ == "__main__":
    unittest.main()
