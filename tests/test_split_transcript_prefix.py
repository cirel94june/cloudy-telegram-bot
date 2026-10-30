import unittest
from unittest import mock
from test_conversation_continuity import bot


class SplitTranscriptPrefixTest(unittest.TestCase):
    def test_logged_two_message_transcript_keeps_both_bodies(self):
        text = (
            "李狗蛋说（Telegram消息 141026，时间 2026-10-29 17:49:32.483120+\n"
            "08:00）：小猫，你这就扎心了啊！\n"
            "李狗蛋\n说（Telegram消息 141027，时间 2026-10-29\n"
            " 17:49:32.483120+08:00）：就算\n我是鸟，那也是见过世面的。"
        )
        expected = "小猫，你这就扎心了啊！\n就算\n我是鸟，那也是见过世面的。"
        self.assertEqual(bot._sanitize_model_visible_reply(text), expected)

    def test_screenshot_shape_split_name_and_timestamp(self):
        text = "李狗\n蛋说（Telegram消息 141026，时间 202\n6-10-29 17:49:32.483120+08:00）：正文。"
        self.assertEqual(bot._sanitize_model_visible_reply(text), "正文。")

    def test_multiline_headers_with_identity_annotations(self):
        cases = [
            "Jasper（你自己，Telegram bot）说（Telegram消息 141026，\n时间 2026-10-29T17:49:32+08:00）：正文。",
            "Lucien说（回复消息\n123，时间 2026-10-29\n17:49:32+08:00）：正文。",
            "燕燕（群友，Telegram用户 123）说（Telegram消息 12，\n时间 2026-10-29 17:49）：正文。",
            "Jas\nper说（Telegram消\n息 123）：正文。",
            "Assistant: 李狗\n蛋说（Telegram消息 123，\n时间 2026-10-29）：Cloudy说：正文。",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(bot._sanitize_model_visible_reply(text), "正文。")

    def test_metadata_only_and_numeric_truncation_never_sent(self):
        for text in (
            "李狗\n蛋说（Telegram消息 141026，时间 202\n6-10-29 17:49:32.483120+08:00）：",
            "李狗蛋说（Telegram消息 141026，时间 202",
        ):
            self.assertEqual(bot._sanitize_model_visible_reply(text), "")

    def test_multiline_prose_dates_and_quotes_are_preserved(self):
        for text in (
            "李狗\n蛋今天很高兴。",
            "小克说（有点生气）：我不同意。",
            "我刚听见小克说：今天不想加班。",
            "时间是 2026-10-29 17:49:32+08:00。",
            "示例代码：\nmessage_id = 123",
            "我说（时间过得真快）：还没吃饭呢。",
        ):
            self.assertEqual(bot._sanitize_model_visible_reply(text), text)

    def test_real_split_pipeline_gets_only_body(self):
        text = "李狗\n蛋说（Telegram消息 141026，时间 202\n6-10-29 17:49:32.483120+08:00）：第一句。\n第二句。"
        with mock.patch.object(bot, "split_into_short_messages",
                               wraps=bot.split_into_short_messages) as split, \
                mock.patch.object(bot, "send_telegram",
                                  side_effect=lambda cid, part, **kw: {"message_id": 1, "text": part}) as send, \
                mock.patch.object(bot.time, "sleep"), \
                mock.patch.object(bot, "send_chat_action"):
            delivered = bot.send_telegram_split("8749953218", text)
        self.assertEqual(split.call_args.args[0], "第一句。\n第二句。")
        visible = "".join(call.args[1] for call in send.call_args_list)
        self.assertNotIn("Telegram", visible)
        self.assertNotIn("2026", visible)
        self.assertIn("第一句", visible)
        self.assertIn("第二句", visible)
        self.assertTrue(delivered)

