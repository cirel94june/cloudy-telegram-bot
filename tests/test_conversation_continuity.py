import json
import os
import unittest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:test-token")
os.environ["BOT_NAME"] = "Jasper"
os.environ["AI_ID"] = "jasper"
os.environ["CECI_ID"] = "8749953218"
os.environ["PROACTIVE_ENABLED"] = "false"
os.environ["PROACTIVE_BACKGROUND_ENABLED"] = "false"
os.environ["GIST_HISTORY_IO_ENABLED"] = "false"
os.environ["MEMORY_RECALL_ENABLED"] = "false"

import bot


class ConversationContinuityTest(unittest.TestCase):
    def test_jasper_remembers_its_own_previous_message_without_hub(self):
        chat_id = "-100000000001"
        history = [
            bot._make_conversation_event(
                role="assistant",
                content="我把一颗蓝色玻璃珠藏在枕头下面。",
                raw_text="我把一颗蓝色玻璃珠藏在枕头下面。",
                chat_id=chat_id,
                telegram_message_id="7001",
                sender_type="agent",
                stable_sender_id="jasper",
                created_at="2026-07-21T12:00:00+08:00",
                bot_name="Jasper",
            ),
            bot._make_conversation_event(
                role="user",
                content="ceci(ID:8749953218): 刚才是谁说把什么藏在哪里？",
                raw_text="刚才是谁说把什么藏在哪里？",
                chat_id=chat_id,
                telegram_message_id="7002",
                sender_type="user",
                stable_sender_id="ceci",
                reply_to_message_id="7001",
                created_at="2026-07-21T12:00:05+08:00",
            ),
        ]

        messages = bot.build_model_messages(history, history_limit=50)
        serialized = json.dumps(messages, ensure_ascii=False)
        self.assertLess(serialized.index("speaker=jasper"), serialized.index("speaker=ceci"))
        self.assertIn("message_id=7001", serialized)
        self.assertIn("reply_to=7001", serialized)
        self.assertIn("蓝色玻璃珠", serialized)
        self.assertIn("枕头下面", serialized)

        def deterministic_model_stub(final_messages):
            context = json.dumps(final_messages, ensure_ascii=False)
            required = ("speaker=jasper", "蓝色玻璃珠", "枕头下面", "speaker=ceci")
            if all(item in context for item in required):
                return "Jasper自己刚才说，把一颗蓝色玻璃珠藏在枕头下面。"
            return "上下文缺失"

        raw_output = deterministic_model_stub(messages)
        self.assertEqual(
            raw_output,
            "Jasper自己刚才说，把一颗蓝色玻璃珠藏在枕头下面。",
        )

        report = {
            "telegram_raw_messages": [
                {"message_id": "7001", "sender": "jasper", "text": history[0]["raw_text"]},
                {"message_id": "7002", "sender": "ceci", "text": history[1]["raw_text"]},
            ],
            "conversation_store": history,
            "final_messages": messages,
            "model_raw_output": raw_output,
            "memory_hub_called": False,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    unittest.main()
