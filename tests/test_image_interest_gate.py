import json
import unittest
from contextlib import ExitStack
from unittest import mock

from test_conversation_continuity import bot
from image_reply_policy import choose_reply


class ImageInterestGateTest(unittest.TestCase):
    def test_plain_descriptions_and_unconfirmed_choices_are_silent(self):
        for value in ('这张图真好看。', '[SKIP]', '{}', '[]',
                      '{"interested":"true","basis":"personal_interest","reply":"正文"}',
                      '{"interested":true,"basis":"generic_praise","reply":"真可爱"}',
                      '{"interested":true,"basis":"personal_interest","reply":null}'):
            with self.subTest(value=value):
                self.assertEqual(choose_reply(value)[0], '')

    def test_decline_wins_over_any_reply_and_accept_requires_specific_basis(self):
        self.assertEqual(choose_reply(json.dumps({
            'interested': False, 'basis': 'none', 'reply': '[DM_CECI:do not send]'})), ('', 'declined'))
        for basis in ('direct_question', 'personal_interest', 'conversation_link'):
            with self.subTest(basis=basis):
                value = json.dumps({'interested': True, 'basis': basis, 'reply': '具体想说的话。'})
                self.assertEqual(choose_reply(value), ('具体想说的话。', 'accepted'))
                self.assertEqual(choose_reply('```json\n' + value + '\n```'), ('具体想说的话。', 'accepted'))

    def route(self, model_text, backup=False, optional=True):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in {'CLAUDE_URL': 'https://primary.invalid/v1',
                            'CLAUDE_KEY': 'test', 'CLAUDE_MODELS': ['test-model'],
                            'API_FORMAT': 'anthropic', 'CECI_SEEN': {},
                            'PROMPT_RULES': '平时收到消息必须回复。',
                            'BACKUP_BASE_URL': 'https://backup.invalid/v1',
                            'BACKUP_API_KEY': 'test-backup' if backup else '',
                            'BACKUP_MODELS': ['backup-model'], 'BACKUP_API_FORMAT': 'anthropic'}.items():
            stack.enter_context(mock.patch.object(bot, name, value))
        for name in ('build_cross_chat_context', 'build_group_identity_hint'):
            stack.enter_context(mock.patch.object(bot, name, return_value=''))
        caps = stack.enter_context(mock.patch.object(bot, '_hub_process_capabilities', side_effect=lambda x: x))

        def response(url, **kwargs):
            payload = {} if backup and 'primary.invalid' in url else {'content': [{'type': 'text', 'text': model_text}]}
            result = mock.Mock(status_code=200, content=json.dumps(payload).encode('utf-8'))
            result.json.return_value = payload
            return result

        post = stack.enter_context(mock.patch.object(bot.requests, 'post', side_effect=response))
        result = bot.call_claude('图片', '', [{'role': 'user', 'content': '[图片]'}], '',
                                is_group=True, chat_id='-100123', image_reply_optional=optional)
        return result, caps, post

    def test_primary_and_backup_drop_plain_ungated_reply_before_capabilities(self):
        for backup in (False, True):
            with self.subTest(backup=backup):
                result, caps, _ = self.route('这张图真好看。[DM_CECI:must not execute]', backup=backup)
                self.assertEqual(result['text'], '[SKIP]')
                caps.assert_not_called()

    def test_decline_never_invokes_capabilities_or_backup_retry(self):
        result, caps, post = self.route(json.dumps({'interested': False, 'basis': 'none', 'reply': ''}))
        self.assertEqual(result['text'], '[SKIP]')
        caps.assert_not_called()
        self.assertEqual(post.call_count, 1)

    def test_accepted_choice_extracts_only_body_and_rule_follows_persona(self):
        value = json.dumps({'interested': True, 'basis': 'conversation_link', 'reply': '具体补充。'})
        result, caps, post = self.route(value)
        self.assertEqual(result['text'], '具体补充。')
        self.assertEqual(result['cot'], '')
        caps.assert_not_called()
        payload = post.call_args.kwargs['json']
        system = payload['system']
        if isinstance(system, list):
            system = '\n'.join(block['text'] for block in system)
        self.assertGreater(system.rfind('本轮图片发言选择'), system.find('平时收到消息必须回复。'))

    def test_normal_text_route_remains_unchanged(self):
        result, caps, _ = self.route('正常文字回复。', optional=False)
        self.assertEqual(result['text'], '正常文字回复。')
        caps.assert_called_once_with('正常文字回复。')


if __name__ == '__main__':
    unittest.main()
