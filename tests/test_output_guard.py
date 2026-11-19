import unittest
from unittest import mock

from test_conversation_continuity import bot
from output_guard import split_reasoning


class OutputGuardTest(unittest.TestCase):
    def test_direct_send_also_strips_relay_tags(self):
        response = mock.Mock()
        response.json.return_value = {'ok': True, 'result': {'message_id': 1, 'text': '正文'}}
        with mock.patch.object(bot.requests, 'post', return_value=response) as post:
            bot.send_telegram('123', '<antml thinking>草稿</antml thinking>正文')
        self.assertEqual(post.call_args.kwargs['json']['text'], '正文')

    def test_relay_tags_and_missing_opening_never_reach_split_delivery(self):
        for draft in ('<antml thinking>草稿\n[DM_CECI:secret]</antml thinking>',
                      '<antml:thinking>草稿</antml:thinking>',
                      '缺了开头的草稿\n</antml thinking>',
                      '<THINKING>草稿</THINKING>'):
            with self.subTest(draft=draft), \
                    mock.patch.object(bot, 'send_telegram', return_value={'message_id': 1}) as send:
                bot.send_telegram_split('123', draft + '可见正文。')
                send.assert_called_once()
                self.assertEqual(send.call_args.args[1], '可见正文。')

    def test_nested_unclosed_and_normal_text(self):
        cases = [('<antml thinking>one<think>two</think>three</antml thinking>正文', '正文'),
                 ('正文<antml thinking>unfinished', '正文'),
                 ('<antml thinking>unfinished', ''),
                 ('前文<think>draft</think>后文', '前文后文'),
                 ('我在想今天吃什么。\nI think it looks nice.', '我在想今天吃什么。\nI think it looks nice.')]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(split_reasoning(value)[0], expected)

    def test_anthropic_and_openai_replies_are_clean_before_capabilities(self):
        value = '<antml thinking>[DM_CECI:secret]</antml thinking>正文'
        for payload in ({'content': [{'type': 'text', 'text': value}]},
                        {'choices': [{'message': {'content': value}}]}):
            with self.subTest(payload=payload):
                visible, _ = bot._extract_api_reply_parts(payload)
                self.assertEqual(visible, '正文')
                self.assertNotIn('DM_CECI', visible)

    def test_historical_ai_drafts_do_not_return_to_model_context(self):
        history = [{'role': 'assistant', 'content': '<antml thinking>草稿</antml thinking>正文'}]
        messages = bot.build_model_messages(history)
        self.assertEqual(len(messages), 1)
        self.assertIn('正文', messages[0]['content'])
        self.assertNotIn('草稿', messages[0]['content'])
        self.assertEqual(bot.build_model_messages([
            {'role': 'assistant', 'content': '<antml thinking>unfinished'}]), [])

    def test_existing_opt_in_cot_still_separates_from_body(self):
        visible, cot = bot.extract_thinking('<antml thinking>草稿</antml thinking>正文')
        self.assertEqual((visible, cot), ('正文', '草稿'))


if __name__ == '__main__':
    unittest.main()
