import unittest

from test_conversation_continuity import bot


class TopicThreadTest(unittest.TestCase):
    def test_plain_reply_in_regular_group_shares_main_window(self):
        msg = {"message_id": 12, "message_thread_id": 5, "reply_to_message": {"message_id": 5}}
        self.assertIsNone(bot._topic_thread_id(msg))

    def test_forum_topic_message_keeps_its_window(self):
        msg = {"message_id": 12, "message_thread_id": 7, "is_topic_message": True}
        self.assertEqual(bot._topic_thread_id(msg), 7)

    def test_message_without_thread(self):
        self.assertIsNone(bot._topic_thread_id({"message_id": 1}))


if __name__ == "__main__":
    unittest.main()
