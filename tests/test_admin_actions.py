import unittest
from unittest import mock

from test_conversation_continuity import bot


class AdminActionsTest(unittest.TestCase):
    def setUp(self):
        bot.ACTION_DEDUP.clear()

    def tearDown(self):
        bot.ACTION_DEDUP.clear()

    def test_failed_pin_can_retry_and_success_is_deduplicated(self):
        with mock.patch.object(bot, "pin_message", side_effect=[
            (False, "temporary failure"), (True, "")
        ]) as pin:
            for _ in range(3):
                bot.parse_and_execute_actions("[PIN:123]", "-100123")
            self.assertEqual(pin.call_count, 2)

    def test_reply_pin_shares_dedup_with_explicit_pin(self):
        with mock.patch.object(bot, "pin_message", return_value=(True, "")) as pin:
            bot.parse_and_execute_actions(
                "[PIN_REPLY]", "-100123", {"reply_to_message_id": 123}
            )
            bot.parse_and_execute_actions("[PIN:123]", "-100123")
            pin.assert_called_once_with("-100123", 123)

    def test_negated_delete_allows_pin_but_unpin_blocks_it(self):
        with mock.patch.object(bot, "pin_message", return_value=(True, "")) as pin:
            bot.parse_and_execute_actions("别删了，我要置顶。[PIN:123]", "-100123")
            pin.assert_called_once()
            bot.parse_and_execute_actions("取消置顶。[PIN:456]", "-100123")
            pin.assert_called_once()

    def test_failed_tag_can_retry(self):
        with mock.patch.dict(bot.USER_NAME_MAP, {"-100123": {"friend": "123456"}}), \
                mock.patch.object(bot, "_resolve_member_id", return_value="123456"), \
                mock.patch.object(bot, "set_member_display_name", side_effect=[
                    (False, "temporary failure"), (True, "")
                ]) as tag:
            for _ in range(3):
                bot.parse_and_execute_actions("[MEMBER_TAG:123456:hello]", "-100123")
            self.assertEqual(tag.call_count, 2)

    def test_output_guard_preserves_explicit_actions(self):
        text = "这句留着。\n[PIN:123]\n[MEMBER_TAG:123456:hello]\n（签名:hello）"
        self.assertEqual(bot._sanitize_model_visible_reply(text), text)
