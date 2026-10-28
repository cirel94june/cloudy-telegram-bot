import os
import unittest
from unittest import mock

from test_conversation_continuity import bot
import owner_dm


class OwnerDMTest(unittest.TestCase):
    def setUp(self):
        self.limiter = owner_dm.OwnerDM()
        self.env = mock.patch.dict(os.environ, {"OWNER_DM_ENABLED": "true"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_destination_is_fixed_and_source_must_be_group(self):
        send = mock.Mock(return_value=[{"message_id": 1, "text": "hello"}])
        for owner, source in (("", "-100123"), ("-100123", "-100123"),
                              ("8749953218", "8749953218"), ("8749953218", "-oops")):
            self.assertEqual(self.limiter.deliver(owner, source, "hello", send)[0], "unavailable")
        send.assert_not_called()
        self.assertEqual(self.limiter.deliver("8749953218", "-100123", "hello", send)[0], "sent")
        send.assert_called_once_with("8749953218", "hello")

    def test_cooldown_and_rolling_day_limit(self):
        send = mock.Mock(return_value=[{"message_id": 1}])
        with mock.patch.object(owner_dm.time, "monotonic") as clock:
            for now in (100, 3700, 7300):
                clock.return_value = now
                self.assertEqual(self.limiter.deliver("8749953218", "-100123", "hi", send)[0], "sent")
            for now in (7301, 10900):
                clock.return_value = now
                self.assertEqual(self.limiter.deliver("8749953218", "-100123", "hi", send)[0], "limited")
            clock.return_value = 86501
            self.assertEqual(self.limiter.deliver("8749953218", "-100123", "hi", send)[0], "sent")

    def test_failure_backs_off_without_consuming_success_budget(self):
        send = mock.Mock(side_effect=[RuntimeError("network"), [{"message_id": 1}]])
        with mock.patch.object(owner_dm.time, "monotonic") as clock:
            clock.return_value = 100
            self.assertEqual(self.limiter.deliver("8749953218", "-100123", "hi", send)[0], "failed")
            clock.return_value = 101
            self.assertEqual(self.limiter.deliver("8749953218", "-100123", "hi", send)[0], "limited")
            clock.return_value = 161
            self.assertEqual(self.limiter.deliver("8749953218", "-100123", "hi", send)[0], "sent")
            self.assertEqual(len(self.limiter.sent_at), 1)

    def test_inflight_send_blocks_concurrent_send(self):
        def send(*args):
            state, _ = self.limiter.deliver("8749953218", "-100123", "nested", mock.Mock())
            self.assertEqual(state, "limited")
            return [{"message_id": 1}]
        self.assertEqual(self.limiter.deliver("8749953218", "-100123", "hi", send)[0], "sent")

    def test_disable_switch(self):
        with mock.patch.dict(os.environ, {"OWNER_DM_ENABLED": "false"}):
            send = mock.Mock()
            self.assertEqual(self.limiter.deliver("8749953218", "-100123", "hi", send)[0], "disabled")
            send.assert_not_called()

    def test_parser_sends_once_to_owner_records_delivery_and_stays_silent_in_group(self):
        delivered = [{"message_id": 1, "text": "private hello"}]
        with mock.patch.object(bot, "CECI_ID", "8749953218"), \
                mock.patch.object(owner_dm, "OWNER_DM", self.limiter), \
                mock.patch.object(bot, "send_telegram_split", return_value=delivered) as send, \
                mock.patch.object(bot, "_record_delivered_agent_messages") as record, \
                mock.patch.object(bot, "Thread"), \
                mock.patch.object(bot, "hub_capture_log"):
            reply = bot._sanitize_model_visible_reply("group hello\n[DM_CECI:private hello]\n[DM_CECI:second]")
            result = bot.parse_and_execute_actions(reply, "-100123", {"sender_id": "9999999"})
            self.assertIn("group hello", result)
            self.assertNotIn("private hello", result)
            self.assertNotIn("DM_CECI", result)
            send.assert_called_once_with("8749953218", "private hello")
            record.assert_called_once_with("8749953218", delivered, fallback_text="private hello")

    def test_failed_delivery_is_not_recorded(self):
        with mock.patch.object(bot, "CECI_ID", "8749953218"), \
                mock.patch.object(owner_dm, "OWNER_DM", self.limiter), \
                mock.patch.object(bot, "send_telegram_split", return_value=[]), \
                mock.patch.object(bot, "_record_delivered_agent_messages") as record:
            self.assertNotIn("DM_CECI", bot.parse_and_execute_actions("[DM_CECI:hello]", "-100123"))
            record.assert_not_called()

    def test_malformed_and_history_tags_are_not_visible(self):
        self.assertNotIn("DM_CECI", bot.parse_and_execute_actions("[DM_CECI:" + "x" * 501 + "]", "-100123"))
        self.assertNotIn("DM_CECI", bot.parse_and_execute_actions("hello [DM_CECI:unfinished", "-100123"))
        self.assertEqual(bot._strip_action_artifacts("hello [DM_CECI:private]"), "hello")

    def test_bio_respects_telegram_120_character_limit(self):
        response = mock.Mock()
        response.json.return_value = {"ok": True}
        with mock.patch.object(bot.requests, "post", return_value=response) as post:
            self.assertTrue(bot._set_bot_bio("x" * 140))
            self.assertEqual(len(post.call_args.kwargs["json"]["short_description"]), 120)

