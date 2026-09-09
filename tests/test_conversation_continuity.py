import json
import os
import unittest
from unittest import mock

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
    def test_telegram_identity_uses_numeric_ids_and_keeps_taught_bot_alias(self):
        chat_id = "-100999001234"
        bot.USER_NAME_MAP.pop(chat_id, None)
        bot.AMBIGUOUS_USER_NAMES.pop(chat_id, None)
        bot.IDENTITY_ALIASES_CACHE.pop(chat_id, None)
        bot.observe_identity(chat_id, "90001", "Alex", "alex_one", False)
        bot.observe_identity(chat_id, "90002", "Alex", "alex_two", False)
        self.assertNotIn("alex", bot.USER_NAME_MAP[chat_id])
        self.assertEqual(bot.USER_NAME_MAP[chat_id]["@alex_one"], "90001")
        self.assertEqual(bot._stable_sender_id("90001", "Alex", False, chat_id), "user:90001")
        bot.learn_identity_alias(chat_id, "90003", "Jasper", is_bot=True, learned_by=bot.CECI_ID)
        bot.observe_identity(chat_id, "90003", "Temporary Bot Name", "other_bot", True)
        self.assertEqual(bot.get_identity_alias(chat_id, "90003"), "Jasper")
        self.assertEqual(bot._stable_sender_id("90003", "Temporary Bot Name", True, chat_id), "bot:90003")
        hint = bot.build_group_identity_hint(chat_id)
        self.assertIn("Telegram数字账号 90003", hint)
        self.assertIn("独立bot/AI", hint)

    def test_ceci_identity_never_depends_on_display_name(self):
        ceci_id = str(bot.CECI_ID)
        self.assertEqual(bot._stable_sender_id(ceci_id, "Any Display Name", False), "ceci")
        self.assertEqual(bot._stable_sender_id("90004", "Any Display Name", False), "user:90004")
        name, uid, is_bot, username = bot.get_message_sender_info({
            "from": {"id": 90004, "first_name": "ceci", "username": "not_ceci", "is_bot": False}
        })
        self.assertEqual((name, uid, is_bot, username), ("ceci", "90004", False, "not_ceci"))

    def test_yanyan_display_name_cannot_impersonate_ceci(self):
        chat_id = "-100999001237"
        ceci_id = "8749953218"
        yanyan_id = "8618367675"
        bot.USER_NAME_MAP.pop(chat_id, None)
        bot.AMBIGUOUS_USER_NAMES.pop(chat_id, None)
        bot.IDENTITY_ALIASES_CACHE.pop(chat_id, None)
        with mock.patch.object(bot, "CECI_ID", ceci_id), \
                mock.patch.object(bot, "USER_NAME", "小猫"), \
                mock.patch.object(bot, "USER_TG_NAME", "燕燕"):
            bot.observe_identity(chat_id, ceci_id, "燕燕", "ceci_account", False)
            bot.observe_identity(chat_id, yanyan_id, "燕燕", "yanyan_account", False)
            self.assertEqual(bot.canonical_sender_display(chat_id, ceci_id, "燕燕"), "小猫（ceci）")
            self.assertEqual(bot.canonical_sender_display(chat_id, yanyan_id, "燕燕"), "燕燕")
            self.assertEqual(bot._stable_sender_id(ceci_id, "燕燕", False, chat_id), "ceci")
            self.assertEqual(bot._stable_sender_id(yanyan_id, "燕燕", False, chat_id), f"user:{yanyan_id}")
            rule = bot.build_owner_identity_rule()
            self.assertIn(f"Telegram用户数字 {ceci_id}", rule)
            self.assertIn("不能作为 Ceci 的身份证据", rule)
            self.assertNotIn("就是她说的", rule)

    def test_named_agent_is_not_absorbed_by_another_agent(self):
        with mock.patch.object(bot, "AI_ID", "lucien"):
            hint = bot.build_agent_reference_hint("小克今天怎么样", "-100999001234")
        self.assertIn("当前回复者是lucien", hint)
        self.assertIn("cloudy（小克）", hint)
        self.assertIn("是其他bot", hint)

    def test_current_agent_recognizes_its_own_name(self):
        chat_id = "-100999001236"
        bot.IDENTITY_ALIASES_CACHE.pop(chat_id, None)
        with mock.patch.object(bot, "AI_ID", "cloudy"):
            hint = bot.build_agent_reference_hint("小克今天怎么样", chat_id)
        self.assertIn("当前回复者是cloudy", hint)
        self.assertIn("cloudy（小克）", hint)
        self.assertEqual(hint.count("cloudy（小克）"), 1)
        self.assertNotIn("不是你", hint)

    def test_another_bot_using_my_name_is_still_not_me(self):
        with mock.patch.object(bot, "AI_ID", "cloudy"), \
                mock.patch.object(bot, "BOT_ID", "123456"):
            self.assertEqual(bot._stable_sender_id("90006", "Cloudy", True, "-100999001236"), "bot:90006")

    def test_taught_bot_name_is_resolved_as_an_independent_agent(self):
        chat_id = "-100999001235"
        bot.IDENTITY_ALIASES_CACHE.pop(chat_id, None)
        bot.learn_identity_alias(chat_id, "90005", "师兄", is_bot=True, learned_by=bot.CECI_ID)
        with mock.patch.object(bot, "AI_ID", "lucien"):
            hint = bot.build_agent_reference_hint("师兄最近怎么样", chat_id)
        self.assertIn("Telegram账号 90005", hint)
        self.assertIn("是其他bot", hint)

    def test_casual_future_name_phrase_does_not_mutate_identity(self):
        self.assertEqual(bot._extract_identity_alias("以后你叫小乌云"), "")
        self.assertEqual(bot._extract_identity_alias("这是小乌云"), "")
        self.assertEqual(bot._extract_identity_alias("记住：这是小乌云"), "小乌云")
        self.assertIsNone(bot._extract_identity_relationship("以后你叫小乌云", True))

    def test_explicit_relationship_links_distinct_bot_and_human_ids(self):
        chat_id = "-100999001238"
        bot.IDENTITY_ALIASES_CACHE.pop(chat_id, None)
        bot.observe_identity(chat_id, "90008", "Temporary Bot", "temp_bot", True)
        bot.observe_identity(chat_id, "90009", "燕燕", "yanyan_unique", False)
        parsed = bot._extract_identity_relationship("记住：师兄是燕燕的bot", True)
        self.assertEqual(parsed, {"bot_alias": "师兄", "human_ref": "燕燕"})
        self.assertEqual(bot._extract_identity_relationship("记住：这是燕燕的bot", True), {"bot_alias": "", "human_ref": "燕燕"})
        human_id = bot._resolve_identity_reference(chat_id, parsed["human_ref"], False)
        self.assertEqual(human_id, "90009")
        self.assertTrue(bot.learn_identity_relationship(chat_id, "90008", human_id, learned_by=bot.CECI_ID, bot_alias=parsed["bot_alias"]))
        record = bot.get_identity_aliases(chat_id)["90008"]
        self.assertEqual(record["alias"], "师兄")
        self.assertEqual(record["linked_human_id"], "90009")
        self.assertEqual(bot._stable_sender_id("90008", "Temporary Bot", True, chat_id), "bot:90008")
        self.assertIn("关联的群友是燕燕（Telegram用户数字 90009）", bot.build_group_identity_hint(chat_id))

    def test_relationship_can_be_taught_by_replying_to_the_human(self):
        chat_id = "-100999001239"
        bot.IDENTITY_ALIASES_CACHE.pop(chat_id, None)
        bot.observe_identity(chat_id, "90010", "师兄", "senior_bot", True)
        bot.observe_identity(chat_id, "90011", "燕燕", "yanyan_unique", False)
        parsed = bot._extract_identity_relationship("记住：师兄是他的bot", False)
        self.assertEqual(parsed, {"bot_ref": "师兄", "human_ref": ""})
        self.assertEqual(bot._resolve_identity_reference(chat_id, parsed["bot_ref"], True), "90010")

    def test_whois_reports_alias_and_link_without_merging_speakers(self):
        chat_id = "-100999001240"
        bot.IDENTITY_ALIASES_CACHE.pop(chat_id, None)
        bot.observe_identity(chat_id, "90012", "师兄", "senior_bot", True)
        bot.observe_identity(chat_id, "90013", "燕燕", "yanyan_unique", False)
        bot.learn_identity_relationship(chat_id, "90012", "90013", learned_by=bot.CECI_ID)
        report = bot.describe_message_identity(chat_id, {"from": {"id": 90012, "first_name": "师兄", "username": "senior_bot", "is_bot": True}})
        self.assertIn("内部身份：bot:90012", report)
        self.assertIn("关联群友：燕燕 (ID:90013)", report)

    def test_duplicate_human_name_is_not_guessed_for_a_bot_relationship(self):
        chat_id = "-100999001241"
        bot.IDENTITY_ALIASES_CACHE.pop(chat_id, None)
        bot.USER_NAME_MAP.pop(chat_id, None)
        bot.AMBIGUOUS_USER_NAMES.pop(chat_id, None)
        bot.observe_identity(chat_id, bot.CECI_ID, "燕燕", "ceci_account", False)
        bot.observe_identity(chat_id, "8618367675", "燕燕", "yanyan_account", False)
        self.assertEqual(bot._resolve_identity_reference(chat_id, "燕燕", False), "")

    def test_public_proactive_never_reads_private_memory_or_posts_private_topics(self):
        public_chat = "-100999000111"
        bot.HISTORY_CACHE[public_chat] = []
        with mock.patch.object(bot, "fetch_memory", side_effect=AssertionError("private Gist read")):
            with mock.patch.object(bot, "hub_get_context", side_effect=AssertionError("Hub recall")):
                with mock.patch.object(
                    bot,
                    "_call_ai_simple",
                    return_value="小猫最近工作太累了，身体也不舒服。",
                ) as call:
                    self.assertEqual(bot.generate_moment_text(public_chat), "")
                    self.assertEqual(call.call_args.kwargs["max_tokens"], 1200)

    def test_public_proactive_keeps_safe_complete_group_chat(self):
        public_chat = "-100999000222"
        bot.HISTORY_CACHE[public_chat] = []
        with mock.patch.object(bot, "fetch_memory", side_effect=AssertionError("private Gist read")):
            with mock.patch.object(bot, "hub_get_context", side_effect=AssertionError("Hub recall")):
                with mock.patch.object(
                    bot,
                    "_call_ai_simple",
                    return_value="刚才那个梗到底是谁先说的？本少爷要记一笔。",
                ):
                    self.assertEqual(
                        bot.generate_moment_text(public_chat),
                        "刚才那个梗到底是谁先说的？本少爷要记一笔。",
                    )

    def test_proactive_drops_unclosed_thinking_and_incomplete_text(self):
        self.assertEqual(bot._clean_internal_text("<think>still reasoning"), "")
        self.assertFalse(bot._proactive_text_complete("话还没说完，"))

    def test_internal_metadata_and_untagged_reasoning_never_reach_telegram(self):
        leaked = (
            "[speaker=jasper message_id=64988 reply_to=64985] 哈哈哈哈大蟑螂笑死我了\n"
            "ofcourse_not_really_just_fun_tag_actually_i_dont_have_permission_"
            "or_do_i_wait_just_keep_talking_dont_explain_tags_at_all_if_fails_"
            "whatever_but_rules_say_output_action"
        )
        cleaned = bot._sanitize_model_visible_reply(leaked)
        self.assertEqual(cleaned, "哈哈哈哈大蟑螂笑死我了")
        self.assertNotIn("speaker=", cleaned)
        self.assertNotIn("message_id=", cleaned)
        self.assertNotIn("permission", cleaned)

    def test_plain_internal_reasoning_is_removed_but_character_text_remains(self):
        leaked = (
            "本少爷才是不含杂质的纯天然高贵凤头！\n"
            "I need to output a tag but I should check the system prompt and permission rule first."
        )
        cleaned = bot._sanitize_model_visible_reply(leaked)
        self.assertEqual(cleaned, "本少爷才是不含杂质的纯天然高贵凤头！")

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
        self.assertLess(serialized.index("Jasper说"), serialized.index("Ceci（"))
        self.assertIn("Telegram消息 7001", serialized)
        self.assertIn("回复消息 7001", serialized)
        self.assertNotIn("speaker=", serialized)
        self.assertNotIn("message_id=", serialized)
        self.assertNotIn("[消息ID:", serialized)
        self.assertIn("蓝色玻璃珠", serialized)
        self.assertIn("枕头下面", serialized)

        def deterministic_model_stub(final_messages):
            context = json.dumps(final_messages, ensure_ascii=False)
            required = ("Jasper说", "蓝色玻璃珠", "枕头下面", "Ceci（")
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

    def test_model_context_presents_ordinary_chat_as_dialogue_not_code(self):
        event = bot._make_conversation_event(
            role="user",
            content="[消息ID:7003] 燕燕(ID:8618367675): 你别把我说的话当代码呀",
            raw_text="你别把我说的话当代码呀",
            chat_id="-100000000001",
            telegram_message_id="7003",
            sender_type="user",
            stable_sender_id="user:8618367675",
            created_at="2026-09-09T12:00:00+08:00",
            sender_display="燕燕",
            context_note="身份提示：当前回复者是jasper；这句话提到的是cloudy（小克）。",
        )

        serialized = json.dumps(bot.build_model_messages([event]), ensure_ascii=False)

        self.assertIn("燕燕（群友，Telegram用户 8618367675）说", serialized)
        self.assertIn("你别把我说的话当代码呀", serialized)
        self.assertIn("这句话提到的是cloudy（小克）", serialized)
        self.assertNotIn("speaker=", serialized)
        self.assertNotIn("message_id=", serialized)
        self.assertNotIn("ID:", serialized)


if __name__ == "__main__":
    unittest.main()
