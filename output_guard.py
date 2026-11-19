"""Separate relay reasoning markup before actions, history reuse, and delivery."""
import re


_REASONING_TAG = re.compile(
    r'<\s*(/?)\s*(?:antml(?:\s+|:))?'
    r'(?:thinking|think|analysis|reasoning)\b[^>]*>', re.IGNORECASE
)


def split_reasoning(text):
    if not text:
        return '', ''
    text = str(text)
    visible, hidden = [], []
    depth, cursor = 0, 0
    for match in _REASONING_TAG.finditer(text):
        segment = text[cursor:match.start()]
        (hidden if depth else visible).append(segment)
        if match.group(1):
            if depth:
                depth -= 1
            else:
                # Missing opening tag: all preceding text may be a draft.
                visible.clear()
        else:
            depth += 1
        cursor = match.end()
    (hidden if depth else visible).append(text[cursor:])
    return ''.join(visible).strip(), '\n'.join(s.strip() for s in hidden if s.strip())
