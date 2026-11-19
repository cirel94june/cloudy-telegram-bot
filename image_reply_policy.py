"""Require an explicit model choice before replying to unsolicited group images."""
import json
import re


PROMPT = '''【本轮图片发言选择：覆盖平时收到消息就回应的习惯】
这是群友分享的图片或相册，默认安静看，不必接话。先按你自己的性格、兴趣和当前对话，决定有没有真想说的一句。
仅能描述图片、复述图中文字、泛泛夸好看/可爱、表示看到了，都不构成发言理由。不要为了每张图都有回应而找理由，也不要假装有兴趣。
只有这三种情况可主动回应：direct_question（对方明确向你问图里的问题）；personal_interest（确实碰到你自己明确的兴趣，有具体想说的话）；conversation_link（与当前对话直接相关且你有新的具体补充）。其他情况选 none，安静略过。
本轮只输出一个 JSON 对象，不要分析、解释、动作标签或 JSON 外的文字：
不想说：{"interested": false, "basis": "none", "reply": ""}
真想说：{"interested": true, "basis": "direct_question 或 personal_interest 或 conversation_link 中的一个", "reply": "你实际要说的短句"}
reply 只放对话正文，不要把选择依据写进去。'''


def choose_reply(text):
    """Return (visible body, decision); missing or invalid choices remain silent."""
    value = str(text or '').strip()
    fence = re.fullmatch(r'```(?:json)?\s*\n?(.*?)\n?```', value, re.DOTALL | re.IGNORECASE)
    if fence:
        value = fence.group(1).strip()
    try:
        choice = json.loads(value)
    except (ValueError, TypeError):
        return '', 'unconfirmed'
    if not isinstance(choice, dict):
        return '', 'unconfirmed'
    if choice.get('interested') is False:
        return '', 'declined'
    if (choice.get('interested') is not True
            or choice.get('basis') not in ('direct_question', 'personal_interest', 'conversation_link')
            or not isinstance(choice.get('reply'), str)):
        return '', 'unconfirmed'
    body = choice['reply'].strip()
    if not body or body.upper() == '[SKIP]':
        return '', 'declined'
    return body, 'accepted'
