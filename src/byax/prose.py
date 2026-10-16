"""Text primitives shared by the fiction checks and readability reports.

Everything here is deterministic and offline. Word lists are data files under
``resources/<kind>/<language>.json`` so another language is a new file, not new code.
"""

import json
import re
from pathlib import Path

RESOURCES = Path(__file__).parent / 'resources'
WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
_TERMINATOR = re.compile(r'[.!?…]+["”’»)\]]*(?=\s|$)')
_OPENERS = '“«'
_CLOSERS = '”»'
_LISTS = {}


def wordlist(kind, language):
    """Return the word-list data for ``kind`` and a language code, or None when no pack exists."""
    key = (kind, language)
    if key not in _LISTS:
        path = RESOURCES / kind / f'{language}.json'
        try:
            _LISTS[key] = json.loads(path.read_text(encoding='utf-8')) if language.isalpha() else None
        except (OSError, ValueError):
            _LISTS[key] = None
    return _LISTS[key]


def languages(kind):
    """Language codes that ship a word list for ``kind``."""
    return sorted(p.stem for p in (RESOURCES / kind).glob('*.json'))


def words(text):
    """Word tokens as ``(start, end)`` offsets; an internal apostrophe stays inside the word."""
    return [(m.start(), m.end()) for m in WORD.finditer(text)]


def sentences(text, abbreviations=()):
    """Sentence spans as ``(start, end)`` offsets without surrounding whitespace.

    A terminator ends a sentence unless an abbreviation or initial precedes it or the next
    word starts in lower case (``"No!" he said.`` is one sentence).
    """
    spans, begin = [], 0
    for match in _TERMINATOR.finditer(text):
        end = match.end()
        before = re.search(r"([\w.']+)$", text[begin : match.start()])
        if match.group()[0] == '.' and before:
            token = before.group(1).lower().rstrip('.')
            if token in abbreviations or (len(token) == 1 and token.isalpha()):
                continue
        following = re.match(r'\s*(\S)', text[end:])
        if following and following.group(1).islower():
            continue
        spans.append((begin, end))
        begin = end
    spans.append((begin, len(text)))
    result = []
    for start, end in spans:
        piece = text[start:end]
        start += len(piece) - len(piece.lstrip())
        end -= len(piece) - len(piece.rstrip())
        if end > start and WORD.search(text[start:end]):
            result.append((start, end))
    return result


def dialogue_spans(text):
    """Quoted speech as ``(start, end)`` offsets including the quotation marks.

    Curly and angle quotes nest by pair; straight quotes alternate. An unclosed curly quote runs
    to the end of the paragraph and an unmatched straight quote is ignored.
    """
    spans, index, length = [], 0, len(text)
    while index < length:
        char = text[index]
        if char in _OPENERS:
            close = next((i for i in range(index + 1, length) if text[i] in _CLOSERS), None)
            end = length if close is None else close + 1
            spans.append((index, end))
            index = end
        elif char == '"':
            close = text.find('"', index + 1)
            if close < 0:
                break
            spans.append((index, close + 1))
            index = close + 1
        else:
            index += 1
    return spans


def in_spans(position, spans):
    return any(start <= position < end for start, end in spans)


def count_syllables(word, exceptions=None):
    """Estimate syllables in an English word (heuristic: vowel groups with silent-e/-ed handling)."""
    letters = re.sub(r'[^a-z]', '', word.lower())
    if not letters:
        return 0
    if exceptions and letters in exceptions:
        return exceptions[letters]
    if len(letters) <= 3:
        return 1
    stem = letters
    if not re.search(r'[td]ed$', stem):
        stem = re.sub(r'(?:[^laeiouy]es|ed|[^laeiouy]e)$', '', stem)
    stem = re.sub(r'^y', '', stem)
    return max(1, len(re.findall(r'[aeiouy]{1,2}', stem)))


def is_adverb(word, exceptions=()):
    lower = word.lower()
    return len(lower) > 4 and lower.endswith('ly') and lower not in exceptions
