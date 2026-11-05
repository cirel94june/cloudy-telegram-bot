import os
import unittest
from contextlib import ExitStack
from unittest import mock

import requests

from test_conversation_continuity import bot
import home_tools

ENV = {"AIONSHOME_URL": "https://home.example/", "AIONSHOME_OUTING_KEY": "k" * 32,
       "CF_ACCESS_CLIENT_ID": "cid.access", "CF_ACCESS_CLIENT_SECRET": "csecret"}


class FakeResponse:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class HomeToolsTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_off_without_all_four_settings(self):
        with mock.patch.dict(os.environ, {"CF_ACCESS_CLIENT_SECRET": ""}):
            self.assertFalse(home_tools.enabled())
            self.assertEqual(home_tools.prompt_hint(), "")
        self.assertTrue(home_tools.enabled())
        self.assertIn("[回家:搜索:关键词]", home_tools.prompt_hint("小猫"))

    def test_finds_at_most_two_distinct_requests(self):
        text = "等我[回家:搜索:上海天气][回家：淘宝：猫抓板][回家:搜索:上海天气][回家:读网页:https://a.example]"
        self.assertEqual(home_tools.find_requests(text), [("search", "上海天气"), ("taobao", "猫抓板")])
        self.assertEqual(home_tools.find_requests(None), [])
        self.assertEqual(home_tools.strip_tags("好呀[回家:搜索:天气]"), "好呀")
        self.assertEqual(home_tools.strip_tags("[回家:摄像头:客厅]嗯"), "嗯")  # 不认识的也清掉

    def test_chat_types(self):
        self.assertEqual([home_tools.hub_chat_type(t) for t in ("private", "small_group", "big_group", "???")],
                         ["private", "private_group", "public_group", "public_group"])

    def test_borrow_sends_both_keys_and_returns_result(self):
        session = mock.Mock()
        session.post.return_value = FakeResponse(200, {"ok": True, "result": "晴，18 度"})
        out = home_tools.borrow("search", "上海天气", chat_type="public_group", owner_asked=True, session=session)
        self.assertEqual(out, "晴，18 度")
        url = session.post.call_args.args[0]
        kwargs = session.post.call_args.kwargs
        self.assertEqual(url, "https://home.example/api/outing/tool")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + "k" * 32)
        self.assertEqual(kwargs["headers"]["CF-Access-Client-Id"], "cid.access")
        self.assertFalse(kwargs["allow_redirects"])
        body = kwargs["json"]
        self.assertEqual((body["tool"], body["args"], body["chat_type"], body["owner_asked"]),
                         ("search", {"query": "上海天气"}, "public_group", True))
        self.assertTrue(body["request_id"].startswith("tg-"))

    def test_failures_become_plain_sentences(self):
        session = mock.Mock()
        cases = [
            (requests.ConnectionError("down"), "家里没连上"),
            (FakeResponse(429, {"detail": "这一小时借的次数用完了"}), "这一小时借的次数用完了"),
            (FakeResponse(401, {"detail": "钥匙不对"}), "家里不认这把钥匙"),
            (FakeResponse(302, ValueError()), "家里这次没办成"),
            (FakeResponse(200, {"ok": False, "reason": "家里这个工具还没配好"}), "家里这个工具还没配好"),
        ]
        for outcome, expected in cases:
            with self.subTest(expected=expected):
                session.post.side_effect = outcome if isinstance(outcome, Exception) else None
                session.post.return_value = None if isinstance(outcome, Exception) else outcome
                out = home_tools.borrow("taobao", "猫抓板", chat_type="private", owner_asked=False, session=session)
                self.assertIn(expected, out)
                self.assertTrue(out.startswith("（没借成"))

    def test_reply_flow_borrows_then_asks_again(self):
        cid = "8749953218"
        bot.HISTORY_CACHE[cid] = []
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(bot, "hub_get_context", return_value=("记忆", "")))
            stack.enter_context(mock.patch.object(bot, "send_chat_action"))
            stack.enter_context(mock.patch.object(bot, "parse_and_execute_actions",
                                                  side_effect=lambda text, *a, **k: text))
            model = stack.enter_context(mock.patch.object(
                bot, "call_claude", side_effect=["我去看看[回家:搜索:上海天气]", "明天上海晴，18 度～"]))
            borrow = stack.enter_context(mock.patch.object(home_tools, "borrow_all", return_value="【结果】晴"))
            send = stack.enter_context(mock.patch.object(
                bot, "send_telegram_split", return_value=[{"message_id": 88, "text": "明天上海晴，18 度～"}]))
            stack.enter_context(mock.patch.object(bot, "Thread"))
            bot.process_message_background("明天天气怎么样", cid, "Ceci", should_reply=True, msg_id=77,
                                           sender_id=cid, sender_is_bot=False, chat_type="private",
                                           reply_reason="private")
        self.assertEqual(model.call_count, 2)
        self.assertIn("【结果】晴", model.call_args_list[1].args[1])  # 结果放进第二次的背景
        self.assertEqual(borrow.call_args.kwargs, {"chat_type": "private", "owner_asked": True})
        sent = send.call_args.args[1]
        self.assertIn("明天上海晴", sent)
        self.assertNotIn("回家", sent)

    def test_big_group_message_from_someone_else_is_not_owner_asked(self):
        cid = "-100777"
        bot.HISTORY_CACHE[cid] = []
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(bot, "hub_get_context", return_value=("", "")))
            stack.enter_context(mock.patch.object(bot, "send_chat_action"))
            stack.enter_context(mock.patch.object(bot, "send_reaction"))
            stack.enter_context(mock.patch.object(bot, "parse_and_execute_actions",
                                                  side_effect=lambda text, *a, **k: text))
            stack.enter_context(mock.patch.object(bot, "call_claude",
                                                  side_effect=["[回家:淘宝:猫抓板]", "家里不让我在这儿乱借哦"]))
            borrow = stack.enter_context(mock.patch.object(home_tools, "borrow_all", return_value="（没借成）"))
            stack.enter_context(mock.patch.object(bot, "send_telegram_split", return_value=[{"message_id": 1, "text": "x"}]))
            stack.enter_context(mock.patch.object(bot, "Thread"))
            bot.process_message_background("帮我搜个猫抓板", cid, "朋友", should_reply=True, msg_id=5,
                                           sender_id="123456", sender_is_bot=False, chat_type="big_group",
                                           reply_reason="mention")
        self.assertEqual(borrow.call_args.kwargs, {"chat_type": "public_group", "owner_asked": False})

    def test_disabled_means_tags_are_just_removed(self):
        cid = "8749953218"
        bot.HISTORY_CACHE[cid] = []
        with ExitStack() as stack, mock.patch.dict(os.environ, {"AIONSHOME_URL": ""}):
            stack.enter_context(mock.patch.object(bot, "hub_get_context", return_value=("", "")))
            stack.enter_context(mock.patch.object(bot, "send_chat_action"))
            stack.enter_context(mock.patch.object(bot, "parse_and_execute_actions",
                                                  side_effect=lambda text, *a, **k: text))
            model = stack.enter_context(mock.patch.object(bot, "call_claude", return_value="好呀[回家:搜索:x]"))
            send = stack.enter_context(mock.patch.object(bot, "send_telegram_split",
                                                         return_value=[{"message_id": 1, "text": "好呀"}]))
            stack.enter_context(mock.patch.object(bot, "Thread"))
            bot.process_message_background("hi", cid, "Ceci", should_reply=True, msg_id=1, sender_id=cid,
                                           sender_is_bot=False, chat_type="private", reply_reason="private")
        self.assertEqual(model.call_count, 1)
        self.assertEqual(send.call_args.args[1], "好呀")


if __name__ == "__main__":
    unittest.main()
