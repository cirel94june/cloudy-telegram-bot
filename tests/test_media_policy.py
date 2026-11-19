import json
import time
import unittest
from contextlib import ExitStack
from unittest import mock

from test_conversation_continuity import bot


class MediaPolicyTest(unittest.TestCase):
    def test_optional_image_prompt_allows_skip_in_both_api_formats(self):
        for api_format in ('anthropic', 'openai'):
            with self.subTest(api_format=api_format), ExitStack() as stack:
                for name, value in {'CLAUDE_URL': 'https://primary.invalid/v1',
                                    'CLAUDE_KEY': 'test', 'CLAUDE_MODELS': ['test-model'],
                                    'API_FORMAT': api_format, 'CECI_SEEN': {},
                                    'BACKUP_API_KEY': ''}.items():
                    stack.enter_context(mock.patch.object(bot, name, value))
                stack.enter_context(mock.patch.object(bot, 'build_cross_chat_context', return_value=''))
                stack.enter_context(mock.patch.object(bot, 'build_group_identity_hint', return_value=''))
                stack.enter_context(mock.patch.object(bot, '_hub_process_capabilities', side_effect=lambda x: x))
                response = mock.Mock(status_code=200)
                response.json.return_value = {'content': [{'type': 'text', 'text': '[SKIP]'}]}
                response.content = json.dumps(response.json.return_value).encode('utf-8')
                post = stack.enter_context(mock.patch.object(bot.requests, 'post', return_value=response))
                result = bot.call_claude([{'type': '_bot_image', 'data': 'aGVsbG8=', 'media_type': 'image/jpeg'}],
                                        '', [{'role': 'user', 'content': '[图片]'}], '',
                                        is_group=True, chat_id='-100123', image_reply_optional=True)
                self.assertEqual(result['text'], '[SKIP]')
                payload = post.call_args.kwargs['json']
                prompt = str(payload.get('system', '')) + str(payload.get('messages', []))
                self.assertIn('默认安静看，不必接话', prompt)
                self.assertNotIn('不要自己判断"该不该说话"', prompt)

    def test_voice_and_audio_are_silent_even_with_caption(self):
        for kind in ('voice', 'audio'):
            with self.subTest(kind=kind), ExitStack() as stack:
                stack.enter_context(mock.patch.object(bot, 'ALLOWED_IDS', set()))
                stack.enter_context(mock.patch.object(bot, 'observe_identity'))
                download = stack.enter_context(mock.patch.object(bot, 'tg_download_file'))
                transcribe = stack.enter_context(mock.patch.object(bot, 'transcribe_voice'))
                enqueue = stack.enter_context(mock.patch.object(bot, 'enqueue_message'))
                send = stack.enter_context(mock.patch.object(bot, 'send_telegram'))
                sync = stack.enter_context(mock.patch.object(bot.home_sync, 'observe'))
                message = {'message_id': 10000 if kind == 'voice' else 10001,
                           'chat': {'id': 123}, 'from': {'id': 456, 'first_name': 'Guest'},
                           kind: {'file_id': 'test'}, 'caption': 'please reply'}
                response = bot.app.test_client().post('/webhook', json={'message': message})
                self.assertEqual(response.status_code, 200)
                for effect in (download, transcribe, enqueue, send, sync):
                    effect.assert_not_called()

    def test_queued_voice_never_calls_model(self):
        with mock.patch.object(bot, 'call_claude') as call:
            bot.process_message_background('transcript', '123', 'Guest', is_voice=True)
        call.assert_not_called()

    def process_image(self, result, sender='456', album=False):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in {'CECI_ID': '123', 'MEMORY_HUB_URL': '',
                            'MEMORY_HUB_SECRET': '', 'CECI_SEEN': {}}.items():
            stack.enter_context(mock.patch.object(bot, name, value))
        for name in ('_refresh_cross_chat_context', '_save_window_history',
                     'send_chat_action', 'hub_capture_log', 'Thread'):
            stack.enter_context(mock.patch.object(bot, name))
        if hasattr(bot, 'start_typing_indicator'):
            stack.enter_context(mock.patch.object(bot, 'start_typing_indicator'))
        stack.enter_context(mock.patch.object(bot, '_load_window_history', return_value=[]))
        stack.enter_context(mock.patch.object(bot, 'hub_get_context', return_value=('', '')))
        stack.enter_context(mock.patch.object(bot, 'fetch_memory', return_value=''))
        stack.enter_context(mock.patch.object(bot.home_tools, 'enabled', return_value=False))
        call = stack.enter_context(mock.patch.object(bot, 'call_claude', return_value={'text': result}))
        send = stack.enter_context(mock.patch.object(bot, 'send_telegram_split', return_value=[]))
        direct = stack.enter_context(mock.patch.object(bot, 'send_telegram'))
        actions = stack.enter_context(mock.patch.object(bot, 'parse_and_execute_actions', side_effect=lambda x, *a: x))
        image = [('aGVsbG8=', 'image/jpeg')] * 2 if album else 'aGVsbG8='
        bot.process_message_background('', '-100123', 'Guest', msg_date=time.time(),
                                       image_b64=image, image_mime='image/jpeg', sender_id=sender)
        return call, send, direct, actions

    def test_single_and_album_can_skip_without_sending_or_actions(self):
        for album in (False, True):
            for result in ('[SKIP]', '<think>internal</think>[SKIP]'):
                with self.subTest(album=album, result=result):
                    call, send, direct, actions = self.process_image(result, album=album)
                    self.assertTrue(call.call_args.kwargs['image_reply_optional'])
                    send.assert_not_called()
                    direct.assert_not_called()
                    actions.assert_not_called()

    def test_interested_image_can_reply_and_owner_keeps_normal_rule(self):
        for sender, optional in (('456', True), ('123', False)):
            with self.subTest(sender=sender):
                call, send, _, _ = self.process_image('这张猫猫好可爱。', sender=sender)
                self.assertEqual(call.call_args.kwargs['image_reply_optional'], optional)
                send.assert_called_once()

    def test_album_flush_preserves_optional_sender_policy(self):
        item = {'items': [{'file_id': 'test', 'caption': ''}], 'chat_id': '-100123',
                'sender_name': 'Guest', 'sender_id': '456', 'sender_is_bot': False,
                'msg_date': time.time(), 'first_msg_id': 10, 'thread_id': None}
        with mock.patch.object(bot, 'MEDIA_GROUP_BUFFER', {'test-album': item}), \
                mock.patch.object(bot.time, 'sleep'), \
                mock.patch.object(bot, 'tg_download_file', return_value=(b'image', 'image/jpeg')), \
                mock.patch.object(bot, 'enqueue_process_message') as enqueue:
            bot._flush_album('test-album')
        self.assertEqual(enqueue.call_args.args[12], '456')
        self.assertIsInstance(enqueue.call_args.args[6], list)


if __name__ == '__main__':
    unittest.main()
