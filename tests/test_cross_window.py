import os
import threading
import unittest
from datetime import datetime, timezone, timedelta
from unittest import mock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:test-token")
os.environ["PROACTIVE_ENABLED"] = "false"
os.environ["PROACTIVE_BACKGROUND_ENABLED"] = "false"
os.environ["GIST_HISTORY_IO_ENABLED"] = "false"
os.environ["MEMORY_RECALL_ENABLED"] = "false"

import bot
import cross_window
from cross_window import CrossWindowContext, permitted


def event(text, role="user", age=0):
    return {"role": role, "content": text, "raw_text": text,
            "timestamp": (datetime.now(timezone.utc) - timedelta(hours=age)).isoformat(),
            "stable_sender_id": "ceci" if role == "user" else "lucien"}


def render(events, **kwargs):
    return [{"content": e["raw_text"]} for e in events]


class CrossWindowTest(unittest.TestCase):
    def test_short_public_history_and_listening_are_visible(self):
        cache = CrossWindowContext()
        result = cache.build("123", {("-10", ""): [event("hello"), event("reply", "assistant")]},
                             private_chats=["-20"], ceci_id="123", render=render)
        self.assertIn("hello", result)
        self.assertIn("reply", result)
        result = cache.build("123", {("-10", ""): [event("heard-without-reply")]},
                             private_chats=[], ceci_id="123", render=render)
        self.assertIn("heard-without-reply", result)

    def test_public_destination_excludes_private_content_and_presence(self):
        cache = CrossWindowContext()
        cache.cache = {("123", ""): [event("health-secret")],
                       ("-20", ""): [event("work-secret")]}
        result = cache.build("-10", {("-30", ""): [event("public-topic")]},
                             private_chats=["-20"], ceci_id="123", render=render)
        self.assertIn("public-topic", result)
        self.assertNotIn("secret", result)
        self.assertNotIn("私密群 -20", result)
        self.assertNotIn("私聊 123", result)

    def test_private_group_can_read_owner_but_not_other_human_dm(self):
        cache = CrossWindowContext()
        result = cache.build("-20", {("123", ""): [event("owner-topic")],
                                    ("456", ""): [event("other-human-secret")]},
                             private_chats=["-20"], ceci_id="123", render=render)
        self.assertIn("owner-topic", result)
        self.assertNotIn("other-human-secret", result)
        self.assertFalse(permitted("-10", "456", ["-20"], "123"))

    def test_current_window_stale_and_unknown_dates_excluded(self):
        cache = CrossWindowContext()
        result = cache.build("123", {("123", ""): [event("same-window")],
                                    ("-10", ""): [event("stale", age=49)],
                                    ("-20", ""): [{"role": "user", "content": "undated"}]},
                             private_chats=["-20"], ceci_id="123", render=render)
        self.assertEqual(result, "")

    def test_topics_keep_separate_source_labels(self):
        cache = CrossWindowContext()
        result = cache.build("123", {("-10", "1"): [event("topic-one")],
                                    ("-10", "2"): [event("topic-two")]},
                             private_chats=[], ceci_id="123", render=render)
        self.assertIn("话题 1", result)
        self.assertIn("话题 2", result)

    def test_refresh_is_nonblocking_single_worker_and_uses_existing_contract(self):
        cache = CrossWindowContext()
        entered, release = threading.Event(), threading.Event()
        real_thread = threading.Thread
        workers = []

        def thread_factory(*args, **kwargs):
            worker = real_thread(*args, **kwargs)
            workers.append(worker)
            return worker

        def post(*args, **kwargs):
            entered.set()
            release.wait(2)
            response = mock.Mock()
            response.json.return_value = {"chat_id": "-10", "thread_id": "", "turns": [{
                "chat_id": "-10", "thread_id": "", "ai_id": "lucien",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "user_text": "cold-restored", "ai_text": "", "sender_id": "123",
                "message_id": "1", "sender_type": "user",
            }]}
            return response

        with mock.patch.object(cross_window.requests, "post", side_effect=post) as request, \
                mock.patch.object(cross_window, "Thread", side_effect=thread_factory):
            try:
                args = dict(url="https://hub.invalid", headers={"Authorization": "test"},
                            ai_id="lucien", ceci_id="123", make_event=lambda **kw: kw)
                cache.refresh([("-10", "")], **args)
                self.assertTrue(entered.wait(1))
                self.assertTrue(cache.busy)
                cache.refresh([("-10", "")], **args)
                self.assertEqual(len(workers), 1)
            finally:
                release.set()
                for worker in workers:
                    worker.join(2)
            result = cache.build("123", {}, private_chats=[], ceci_id="123", render=render)
            self.assertIn("cold-restored", result)
            self.assertEqual(request.call_args.kwargs["json"]["ai_id"], "lucien")
            cache.refresh([("-10", "")], **args)
            self.assertEqual(request.call_count, 1)

    def test_failed_hub_does_not_hide_live_history(self):
        cache = CrossWindowContext()
        with mock.patch.object(cross_window.requests, "post", side_effect=RuntimeError("offline")), \
                mock.patch.object(cross_window, "Thread") as thread:
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs["target"]()
            cache.refresh([("-10", "")], url="https://hub.invalid", headers={"x": "test"},
                          ai_id="lucien", ceci_id="123", make_event=lambda **kw: kw)
        self.assertFalse(cache.busy)
        self.assertIn("live", cache.build("123", {("-10", ""): [event("live")]},
                                        private_chats=[], ceci_id="123", render=render))

    def test_bot_wrapper_enforces_privacy_before_fetch_and_keeps_local_history(self):
        cache = CrossWindowContext()
        live = [event("live-public")]
        with mock.patch.object(cross_window, "CONTEXT", cache), \
                mock.patch.object(bot, "HISTORY_CACHE", {"-30": live, "456": [event("other-dm")]}), \
                mock.patch.object(bot, "WINDOW_HISTORY_CACHE", {}), \
                mock.patch.object(bot, "PRIVATE_CHATS", ["-20"]), \
                mock.patch.object(bot, "CECI_ID", "123"), \
                mock.patch.object(bot, "ALLOWED_IDS", ["123", "-10", "-20", "-30"]), \
                mock.patch.object(bot, "PROACTIVE_CHAT_IDS", []), \
                mock.patch.object(bot, "MEMORY_HUB_URL", "https://hub.invalid"), \
                mock.patch.object(bot, "MEMORY_HUB_SECRET", "test"), \
                mock.patch.object(cache, "refresh") as refresh, \
                mock.patch.object(bot, "build_model_messages", side_effect=render):
            result = bot.build_cross_chat_context("-10")
            self.assertIn("live-public", result)
            self.assertEqual(refresh.call_args.args[0], [("-30", "")])
            self.assertIs(bot.HISTORY_CACHE["-30"], live)


if __name__ == "__main__":
    unittest.main()
