"""提示词缓存：中转站拒绝时只按剩余时间重发一次；只收字符串 system 的也能退回；按中转站地址各记各的；置顶状态不进缓存段。"""
import unittest
from contextlib import ExitStack
from unittest import mock

from test_conversation_continuity import bot


class PromptCacheTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in {
            "CLAUDE_URL": "https://primary.invalid/v1",
            "CLAUDE_KEY": "test-primary",
            "CLAUDE_MODELS": ["primary-one"],
            "API_FORMAT": "anthropic",
            "BACKUP_BASE_URL": "https://backup.invalid/v1",
            "BACKUP_API_KEY": "test-backup",
            "BACKUP_MODELS": ["backup-one"],
            "BACKUP_API_FORMAT": "anthropic",
            "CECI_SEEN": {},
            "PROMPT_CACHE_ENABLED": True,
            "_CACHE_MARKER_REJECTED_AT": set(),
        }.items():
            self.stack.enter_context(mock.patch.object(bot, name, value))
        for name in ("build_cross_chat_context", "build_group_identity_hint"):
            self.stack.enter_context(mock.patch.object(bot, name, return_value=""))
        self.stack.enter_context(mock.patch.object(bot, "_hub_process_capabilities", side_effect=lambda text: text))
        if hasattr(bot, "_decode_model_json"):
            self.stack.enter_context(mock.patch.object(bot, "_decode_model_json", side_effect=lambda r: r.json()))
        self.stack.enter_context(mock.patch.object(bot, "_model_api_hard_timeout", return_value=20))

    @staticmethod
    def ok(text):
        r = mock.Mock(status_code=200, text="")
        r.json.return_value = {"content": [{"type": "text", "text": text}]}
        return r

    @staticmethod
    def refused(status, text):
        r = mock.Mock(status_code=status, text=text)
        r.json.return_value = {"error": text}
        return r

    def call(self, **kw):
        return bot.call_claude("hello", "", [{"role": "user", "content": "hello"}], "", chat_id="123", **kw)

    def test_plain_retry_uses_only_the_time_left(self):
        now = [100.0]
        sent = []

        def post(url, **kw):
            sent.append((url, isinstance(kw["json"]["system"], list), kw["timeout"]))
            if "primary" in url and isinstance(kw["json"]["system"], list):
                now[0] += 19
                return self.refused(400, '{"error":"unknown field cache_control"}')
            return self.ok("primary" if "primary" in url else "backup")

        with mock.patch.object(bot.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(bot.requests, "post", side_effect=post):
            self.assertEqual(self.call()["text"], "primary")
        self.assertEqual([s[:2] for s in sent], [("https://primary.invalid/v1/messages", True),
                                                 ("https://primary.invalid/v1/messages", False)])
        connect, read = sent[1][2]
        self.assertLessEqual(read, 1.0)
        self.assertLessEqual(connect, 1.0)

    def test_no_plain_retry_once_the_route_is_out_of_time(self):
        now = [100.0]
        sent = []

        def post(url, **kw):
            sent.append((url, isinstance(kw["json"]["system"], list)))
            if "primary" in url:
                now[0] += 21
                return self.refused(400, '{"error":"unknown field cache_control"}')
            return self.ok("backup")

        with mock.patch.object(bot.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(bot.requests, "post", side_effect=post):
            self.assertEqual(self.call()["text"], "backup")
        self.assertEqual(sent[0], ("https://primary.invalid/v1/messages", True))
        self.assertEqual([u for u, _ in sent].count("https://primary.invalid/v1/messages"), 1)

    def test_string_only_relay_falls_back_and_only_that_relay_is_remembered(self):
        sent = []

        def post(url, **kw):
            marked = isinstance(kw["json"]["system"], list)
            sent.append((url, marked))
            if "primary" in url and marked:
                return self.refused(422, '{"detail":[{"loc":["body","system"],"msg":"Input should be a valid string"}]}')
            return self.ok("ok")

        with mock.patch.object(bot.requests, "post", side_effect=post):
            self.assertEqual(self.call()["text"], "ok")
            self.assertEqual(sent, [("https://primary.invalid/v1/messages", True),
                                    ("https://primary.invalid/v1/messages", False)])
            self.assertEqual(bot._CACHE_MARKER_REJECTED_AT, {"https://primary.invalid/v1"})
            sent.clear()
            self.assertEqual(self.call()["text"], "ok")
            self.assertEqual(sent, [("https://primary.invalid/v1/messages", False)])
        # 另一家没被拒绝过，照样带缓存标记
        self.assertIsInstance(bot._anthropic_system("静态", "动态", "https://backup.invalid/v1"), list)

    def test_unrelated_400_is_not_retried(self):
        with mock.patch.object(bot.requests, "post",
                               return_value=self.refused(400, '{"error":"max_tokens too large"}')) as post:
            self.assertIsNone(self.call())
        self.assertTrue(all(isinstance(c.kwargs["json"]["system"], list) for c in post.call_args_list))
        self.assertEqual(bot._CACHE_MARKER_REJECTED_AT, set())

    def test_pin_state_stays_out_of_the_cached_part(self):
        systems = []

        def post(url, **kw):
            systems.append(kw["json"]["system"])
            return self.ok("ok")

        for pin in ("111", "222"):
            with mock.patch.dict(bot.PINNED_MESSAGE_CACHE, {"-100123": pin}), \
                    mock.patch.object(bot.requests, "post", side_effect=post):
                bot.call_claude("hello", "", [{"role": "user", "content": "hello"}], "",
                                is_group=True, chat_id="-100123")
        self.assertEqual(systems[0][0]["text"], systems[1][0]["text"])
        self.assertNotIn("111", systems[0][0]["text"])
        self.assertIn("111", systems[0][-1]["text"])
        self.assertIn("222", systems[1][-1]["text"])


if __name__ == "__main__":
    unittest.main()
