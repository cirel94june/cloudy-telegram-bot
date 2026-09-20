import time
import unittest
from threading import Event, Lock
from unittest import mock

from test_conversation_continuity import bot
from hub_window import WindowRecovery, events_from_response, merge_events


def turn(**overrides):
    row = dict(ai_id="jasper", chat_id="-10", thread_id="", turn_id="t1",
               message_id="11", sender_id="8749953218", sender_type="user",
               reply_to_id="9", user_text="Where is the blue glass bead?",
               ai_text="I put it under the pillow.", created_at="2026-09-20T00:00:00Z")
    row.update(overrides)
    return row


def response(*rows, chat_id="-10", thread_id=""):
    return dict(chat_id=chat_id, thread_id=thread_id, turns=list(rows))


class WindowRecoveryTest(unittest.TestCase):
    def convert(self, data, cid="-10", tid="", ai="jasper"):
        return events_from_response(data, cid, tid, ai, "8749953218",
                                    bot._make_conversation_event)

    def test_identity_and_reply_ids(self):
        events = self.convert(response(turn()))
        self.assertEqual(events[0]["stable_sender_id"], "ceci")
        self.assertEqual(events[0]["telegram_message_id"], "11")
        self.assertEqual(events[1]["stable_sender_id"], "jasper")
        self.assertEqual(events[1]["telegram_message_id"], "")
        self.assertEqual(events[1]["reply_to_message_id"], "11")

    def test_other_bot_is_not_self_and_human_not_ceci(self):
        events = self.convert(response(turn(ai_id="lucien", sender_id="8618367675")))
        self.assertEqual(events[0]["stable_sender_id"], "user:8618367675")
        with mock.patch.object(bot, "AI_ID", "jasper"):
            messages = bot.build_model_messages(events)
        self.assertTrue(all(m["role"] == "user" for m in messages))
        self.assertIn("lucien", str(messages))

    def test_scope_validation(self):
        cases = [
            response(turn(chat_id="-20")),
            response(turn(thread_id="42")),
            response(turn(), chat_id="-20"),
            response(turn(), thread_id="42"),
            {"turns": []},
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.convert(data)

    def test_private_agent_isolation_and_alias(self):
        with self.assertRaises(ValueError):
            self.convert(response(turn(chat_id="10", ai_id="lucien"), chat_id="10"), cid="10")
        events = self.convert(response(turn(chat_id="10", ai_id="claude"), chat_id="10"),
                              cid="10", ai="cloudy")
        self.assertEqual(events[1]["stable_sender_id"], "cloudy")

    def test_old_identity_not_guessed(self):
        events = self.convert(response(turn(sender_id="", sender_type="", message_id="")))
        self.assertEqual(events[0]["stable_sender_id"], "user:unknown")
        self.assertEqual(events[0]["telegram_message_id"], "")

    def test_same_group_trigger_dedup_keeps_each_agents_reply(self):
        events = self.convert(response(turn(), turn(ai_id="lucien", turn_id="t2")))
        merged = merge_events(events, [])
        self.assertEqual(len(merged), 3)
        self.assertEqual(len(merge_events(events, merged)), 3)

    def test_live_message_wins_without_losing_new_message(self):
        old = self.convert(response(turn()))
        live = dict(old[0], raw_text="Live version")
        new = dict(live, telegram_message_id="12", raw_text="New message")
        merged = merge_events(old, [live, new])
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0]["raw_text"], "Live version")
        self.assertEqual(merged[-1]["raw_text"], "New message")

    def call_restore(self, recovery, history, allow=True):
        recovery.restore(history, "-10", "", url="https://hub.example",
                         headers={"Authorization": "test"}, ai_id="jasper",
                         ceci_id="8749953218", make_event=bot._make_conversation_event,
                         history_lock=Lock(), allow=allow)

    def test_cold_restore_contract_and_warm_no_network(self):
        history = []
        recovery = WindowRecovery()
        with mock.patch("hub_window.requests.post") as post:
            post.return_value.json.return_value = response(turn())
            self.call_restore(recovery, history)
            self.call_restore(recovery, history)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(post.call_args.args[0], "https://hub.example/api/window/context")
            self.assertEqual(post.call_args.kwargs["json"]["chat_id"], "-10")
        self.assertEqual(len(history), 2)
        with mock.patch.object(bot, "AI_ID", "jasper"):
            messages = bot.build_model_messages(history)
        self.assertEqual(messages[-1]["role"], "assistant")
        self.assertIn("under the pillow", messages[-1]["content"])

    def test_listener_does_not_prevent_cold_recovery(self):
        history = []
        recovery = WindowRecovery()
        self.call_restore(recovery, history, allow=False)
        history.append(dict(role="user", raw_text="new", telegram_message_id="12"))
        with mock.patch("hub_window.requests.post") as post:
            post.return_value.json.return_value = response(turn())
            self.call_restore(recovery, history)
        self.assertEqual(len(history), 3)

    def test_failure_retries_without_erasing_live_history(self):
        history = []
        recovery = WindowRecovery(retry_after=0)
        with mock.patch("hub_window.requests.post", side_effect=ValueError("bad")):
            self.call_restore(recovery, history)
        history.append(dict(role="user", raw_text="new", telegram_message_id="12"))
        with mock.patch("hub_window.requests.post") as post:
            post.return_value.json.return_value = response(turn())
            self.call_restore(recovery, history)
        self.assertEqual(history[-1]["raw_text"], "new")
        self.assertEqual(len(history), 3)

    def test_deadline_no_thread_pileup_or_late_mutation(self):
        started, release, finished = Event(), Event(), Event()
        def slow(*args, **kwargs):
            started.set()
            release.wait(2)
            finished.set()
            result = mock.Mock()
            result.json.return_value = response(turn())
            return result
        recovery = WindowRecovery(timeout=0.01, retry_after=0)
        history = []
        with mock.patch("hub_window.requests.post", side_effect=slow) as post:
            try:
                before = time.monotonic()
                self.call_restore(recovery, history)
                self.assertLess(time.monotonic() - before, 0.5)
                self.assertTrue(started.is_set())
                self.call_restore(recovery, history)
                self.assertEqual(post.call_count, 1)
            finally:
                release.set()
                finished.wait(1)
        self.assertEqual(history, [])

    def test_topic_cache_is_not_main_chat_or_other_topic(self):
        bot.WINDOW_HISTORY_CACHE.clear()
        bot.HISTORY_CACHE["-10"] = []
        a = bot._load_window_history("-10", "42")
        a.append(dict(thread_id="42", raw_text="topic"))
        bot._save_window_history(a, "-10", "42")
        self.assertEqual(bot._load_window_history("-10", "43"), [])
        self.assertEqual(bot._load_window_history("-10"), [])
        self.assertEqual(bot._load_window_history("-10", "42")[0]["raw_text"], "topic")

    def test_capture_fields_are_strings(self):
        with mock.patch.object(bot, "MEMORY_HUB_URL", "https://hub.example"), \
                mock.patch.object(bot, "MEMORY_HUB_SECRET", "test"), \
                mock.patch.object(bot.requests, "post") as post:
            bot.hub_capture_log("hi", "delivered", "-10", 1000,
                                message_id=11, sender_id=22, sender_type="user",
                                thread_id=42, reply_to_id=9)
        data = post.call_args.kwargs["json"]
        for field, value in (("message_id", "11"), ("sender_id", "22"),
                             ("thread_id", "42"), ("reply_to_id", "9"),
                             ("sender_type", "user")):
            self.assertEqual(data[field], value)

    def test_truncated_turn_explicitly_marked(self):
        events = self.convert(response(turn(truncated=True)))
        self.assertIn("incomplete", events[1]["context_note"])

    def test_process_restores_before_model_and_captures_only_delivered_text(self):
        from contextlib import ExitStack
        cid = "8749953218"
        bot.HISTORY_CACHE[cid] = []
        restored = response(turn(chat_id=cid), chat_id=cid)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(bot, "MEMORY_HUB_URL", "https://hub.example"))
            stack.enter_context(mock.patch.object(bot, "MEMORY_HUB_SECRET", "test"))
            stack.enter_context(mock.patch.object(bot, "WINDOW_RECOVERY", WindowRecovery()))
            stack.enter_context(mock.patch.object(bot, "hub_get_context", return_value=("", "")))
            stack.enter_context(mock.patch.object(bot, "send_chat_action"))
            stack.enter_context(mock.patch.object(bot, "parse_and_execute_actions",
                                                  side_effect=lambda text, *a, **k: text))
            model = stack.enter_context(mock.patch.object(bot, "call_claude",
                                                          return_value="Delivered. Unsent tail."))
            stack.enter_context(mock.patch.object(bot, "send_telegram_split",
                                return_value=[{"message_id": 88, "text": "Delivered."}]))
            threads = stack.enter_context(mock.patch.object(bot, "Thread"))
            post = stack.enter_context(mock.patch("hub_window.requests.post"))
            post.return_value.json.return_value = restored
            bot.process_message_background(
                "What did you just say?", cid, "Ceci", should_reply=True,
                msg_id=77, sender_id=cid, sender_is_bot=False,
                chat_type="private", reply_reason="private", reply_to_message_id=11,
            )
        self.assertTrue(model.called)
        model_history = model.call_args.args[2]
        self.assertTrue(any("under the pillow" in e.get("raw_text", "") for e in model_history))
        captures = [c for c in threads.call_args_list if c.kwargs.get("target") is bot.hub_capture_log]
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].kwargs["args"][1], "Delivered.")
        self.assertEqual(captures[0].kwargs["kwargs"]["message_id"], 77)
        self.assertEqual(captures[0].kwargs["kwargs"]["sender_id"], cid)
        self.assertEqual(captures[0].kwargs["kwargs"]["reply_to_id"], 11)

    def test_merge_buffer_separates_topics(self):
        bot.PENDING_MERGE.clear()
        with mock.patch.object(bot, "MESSAGE_MERGE_SECONDS", 1), mock.patch.object(bot, "Thread"):
            for tid in ("42", "43"):
                bot.enqueue_message("hello", "-10", "Ceci", 1, True, 9,
                                    None, None, False, False, "small_group", "ceci",
                                    "8749953218", False, None, tid)
        self.assertEqual(len(bot.PENDING_MERGE), 2)
        bot.PENDING_MERGE.clear()

