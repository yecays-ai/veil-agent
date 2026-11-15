"""Ticket 355: one message that holds several blank-line-separated paragraphs goes out as several bubbles.

Some models write a whole reply as one send_message with paragraphs separated by blank lines. A person on a
chat app sends those as separate messages, and Lan asks for 1 to 4 bubbles. The program does the split so the
rhythm no longer depends on which model is answering. A single paragraph is never touched, and text that
holds a code fence is left alone because its blank lines belong to the code.
"""
import re

MAX_BUBBLES = 4
_BLANK = re.compile(r'\n[ \t]*\n+')


def split_bubbles(text, limit=MAX_BUBBLES):
    """Return the paragraphs of text as at most limit bubbles; one item when it should not be split."""
    if not isinstance(text, str) or '```' in text:
        return [text]
    paragraphs = [part.strip() for part in _BLANK.split(text.strip()) if part.strip()]
    if len(paragraphs) < 2:
        return [text]
    if len(paragraphs) > limit:
        # Keep the first limit-1 paragraphs as they are; the rest stay together in the last bubble.
        paragraphs = paragraphs[:limit - 1] + ['\n\n'.join(paragraphs[limit - 1:])]
    return paragraphs
