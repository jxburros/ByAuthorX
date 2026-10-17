"""Per-chapter readability and style reports (competitiveness plan, section 6.3).

The standard indices (Flesch Reading Ease, Flesch-Kincaid grade, Gunning fog, SMOG, Coleman-Liau,
ARI) are computed from sentence, word, letter and syllable counts. Syllables and passive voice are
heuristics, and every index carries a sample-size caveat when the text is too short for it to be
meaningful. Only paragraph text counts: headings, tables and passages marked intentional are left out.
"""

import math
import statistics

from .errors import ByaxError
from .fiction_checks import chapters_of, pacing_series
from .model import new_id
from .prose import count_syllables, dialogue_spans, in_spans, is_adverb, sentences, wordlist, words

INDICES = {
    'flesch_reading_ease': 'Flesch Reading Ease',
    'flesch_kincaid_grade': 'Flesch-Kincaid grade',
    'gunning_fog': 'Gunning fog',
    'smog': 'SMOG',
    'coleman_liau': 'Coleman-Liau',
    'ari': 'Automated Readability Index',
}
# Minimum sample for an index to be treated as reliable: (sentences, words).
MINIMUMS = {
    'flesch_reading_ease': (1, 100),
    'flesch_kincaid_grade': (1, 100),
    'gunning_fog': (1, 100),
    'smog': (30, 0),
    'coleman_liau': (1, 100),
    'ari': (1, 100),
}
SCHEMA_VERSION = 1


def _tally(texts, lists, language):
    """Counts over paragraph texts already filtered to the analysable text of one language."""
    exceptions = lists.get('syllable_exceptions', {})
    abbreviations = frozenset((wordlist('fiction', language) or {}).get('abbreviations', ()))
    totals = {
        'paragraphs': 0,
        'sentences': 0,
        'words': 0,
        'letters': 0,
        'syllables': 0,
        'complex_words': 0,
        'dialogue_words': 0,
        'adverbs': 0,
        'passive': 0,
    }
    lengths = []
    adverb_exceptions = frozenset((wordlist('fiction', language) or {}).get('adverb_exceptions', ()))
    auxiliaries = frozenset(lists.get('passive_auxiliaries', ()))
    participles = frozenset(lists.get('irregular_participles', ()))
    rejected = frozenset(lists.get('not_participles', ()))
    for text in texts:
        spans = sentences(text, abbreviations)
        tokens = words(text)
        if not tokens:
            continue
        quotes = dialogue_spans(text)
        totals['paragraphs'] += 1
        totals['sentences'] += len(spans)
        totals['words'] += len(tokens)
        openers = {next((s for s, _ in tokens if a <= s < b), None) for a, b in spans}
        for number, (s, e) in enumerate(tokens):
            word = text[s:e]
            lower = word.lower()
            totals['letters'] += sum(c.isalpha() for c in word)
            count = count_syllables(word, exceptions)
            totals['syllables'] += count
            stem_count = count
            for suffix in ('ing', 'es', 'ed'):
                if lower.endswith(suffix) and len(lower) > len(suffix) + 2:
                    stem_count = count_syllables(lower[: -len(suffix)], exceptions)
                    break
            if stem_count >= 3 and (not word[0].isupper() or s in openers):
                totals['complex_words'] += 1
            if in_spans(s, quotes):
                totals['dialogue_words'] += 1
            totals['adverbs'] += is_adverb(word, adverb_exceptions)
            if lower in auxiliaries:
                following = tokens[number + 1 : number + 3]
                for s2, e2 in following:
                    nxt = text[s2:e2].lower()
                    if nxt in rejected:
                        break
                    if is_adverb(nxt, adverb_exceptions) or nxt == 'not':
                        continue
                    if nxt in participles or (nxt.endswith('ed') and len(nxt) > 3):
                        totals['passive'] += 1
                    break
        for a, b in spans:
            lengths.append(sum(1 for s, _ in tokens if a <= s < b))
    totals['sentence_lengths'] = lengths
    return totals


def index_values(totals, supported=True):
    """The six indices with reliability and caveats for a tally; None values when not computable."""
    sentences_n, words_n = totals['sentences'], totals['words']
    result = {}
    for key, label in INDICES.items():
        entry = {'label': label, 'value': None, 'reliable': False, 'caveat': None}
        result[key] = entry
        if not supported:
            entry['caveat'] = 'No readability data for this language; the indices are calibrated for English.'
            continue
        if not sentences_n or not words_n:
            entry['caveat'] = 'No sentences to measure.'
            continue
        per_sentence = words_n / sentences_n
        per_word = totals['syllables'] / words_n
        if key == 'flesch_reading_ease':
            value = 206.835 - 1.015 * per_sentence - 84.6 * per_word
        elif key == 'flesch_kincaid_grade':
            value = 0.39 * per_sentence + 11.8 * per_word - 15.59
        elif key == 'gunning_fog':
            value = 0.4 * (per_sentence + 100 * totals['complex_words'] / words_n)
        elif key == 'smog':
            value = 1.043 * math.sqrt(totals['complex_words'] * 30 / sentences_n) + 3.1291
        elif key == 'coleman_liau':
            value = (
                0.0588 * (100 * totals['letters'] / words_n) - 0.296 * (100 * sentences_n / words_n) - 15.8
            )
        else:
            value = 4.71 * (totals['letters'] / words_n) + 0.5 * per_sentence - 21.43
        entry['value'] = round(value, 2)
        need_sentences, need_words = MINIMUMS[key]
        short = []
        if sentences_n < need_sentences:
            short.append(f'{label} needs at least {need_sentences} sentences; this sample has {sentences_n}.')
        if words_n < need_words:
            short.append(f'{label} needs at least {need_words} words; this sample has {words_n}.')
        entry['reliable'] = not short
        entry['caveat'] = ' '.join(short) or None
    return result


def style_values(totals):
    lengths = totals['sentence_lengths']
    words_n = totals['words']
    return {
        'paragraphs': totals['paragraphs'],
        'sentences': totals['sentences'],
        'words': words_n,
        'average_sentence_words': round(words_n / totals['sentences'], 2) if totals['sentences'] else 0,
        'median_sentence_words': statistics.median(lengths) if lengths else 0,
        'longest_sentence_words': max(lengths) if lengths else 0,
        'sentence_length_stdev': round(statistics.pstdev(lengths), 2) if lengths else 0,
        'passive_voice_count': totals['passive'],
        'passive_voice_per_100_sentences': round(100 * totals['passive'] / totals['sentences'], 2)
        if totals['sentences']
        else 0,
        'adverb_count': totals['adverbs'],
        'adverbs_per_100_words': round(100 * totals['adverbs'] / words_n, 2) if words_n else 0,
        'dialogue_ratio': round(totals['dialogue_words'] / words_n, 3) if words_n else 0,
        'reading_minutes': math.ceil(words_n / 225),
    }


def _paragraph_texts(doc, blocks):
    from .checks import _intentional, _language

    result = []
    for block in blocks:
        if block.get('type') == 'paragraph' and block.get('text', '').strip() and not _intentional(block):
            result.append((block['text'], _language(doc, block)))
    return result


def _summarize(doc, blocks, language):
    pairs = [text for text, lang in _paragraph_texts(doc, blocks) if lang == language]
    lists = wordlist('readability', language)
    totals = _tally(pairs, lists or {}, language)
    entry = {'style': style_values(totals), 'indices': index_values(totals, supported=lists is not None)}
    entry['style']['syllables'] = totals['syllables']
    entry['style']['complex_words'] = totals['complex_words']
    entry['caveats'] = sorted({v['caveat'] for v in entry['indices'].values() if v['caveat']})
    return entry


def readability_report(doc, *, target=None):
    """Document and per-chapter readability and style report as data (JSON-safe, deterministic)."""
    from .checks import _language

    chapters = chapters_of(doc)
    if target is not None:
        chapters = [c for c in chapters if c['id'] == target]
        if not chapters:
            raise ByaxError(
                'missing_target',
                'Readability target must be a top-level heading (chapter) ID.',
                target=target,
            )
    language = _language(doc, {}) or ''
    pacing = {p['id']: p for p in pacing_series(doc, target=target)}
    entries = []
    for chapter in chapters:
        entry = {
            'id': chapter['id'],
            'title': (chapter['heading'] or {}).get('text', ''),
            **_summarize(doc, chapter['blocks'], language),
        }
        entry['pacing'] = pacing.get(chapter['id'], {}).get('series', [])
        entries.append(entry)
    blocks = [b for c in chapters for b in c['blocks']]
    overall = _summarize(doc, blocks, language)
    return {
        'schema_version': SCHEMA_VERSION,
        'document_id': doc['id'],
        'revision': doc['revision'],
        'language': language,
        'scope': target or 'document',
        'document': overall,
        'chapters': entries,
        'method': {
            'text': 'Paragraph text only; headings, tables and passages marked intentional are excluded.',
            'sentences': 'Split at terminal punctuation, skipping abbreviations and lower-case continuations.',
            'syllables': 'Heuristic vowel-group counting with a small exception list; expect errors of a few percent.',
            'complex_words': 'Three or more syllables, excluding proper nouns and -ing/-ed/-es endings.',
            'passive_voice': 'Heuristic: a form of "to be" followed by a past participle; adjectival uses are '
            'counted, and agentless or irregular constructions can be missed.',
            'numbers': 'Digits are not counted as words.',
            'caveats': 'Indices are reported even for short samples, with a caveat; do not compare '
            'figures across samples of very different length.',
        },
    }


def readability_document(report, title=None):
    """A native report document: one section per chapter with index and style tables."""
    from .model import new_document

    doc = new_document(title or 'Readability report')
    doc['metadata']['language'] = report.get('language') or 'en'
    doc['blocks'] = []

    def text(value, kind='paragraph', style='body', level=None):
        block = {'id': new_id('p'), 'type': kind, 'text': value, 'style': style, 'marks': [], 'attrs': {}}
        if level:
            block['attrs'] = {'level': level}
        doc['blocks'].append(block)

    def table(rows):
        doc['blocks'].append(
            {
                'id': new_id('table'),
                'type': 'table',
                'text': '',
                'style': 'body',
                'marks': [],
                'attrs': {'header_rows': 1},
                'rows': [
                    {
                        'id': new_id('row'),
                        'cells': [{'id': new_id('cell'), 'text': str(c), 'attrs': {}} for c in row],
                    }
                    for row in rows
                ],
            }
        )

    text(title or 'Readability report', 'heading', 'heading1', 1)
    text(
        'Computed locally and deterministically from paragraph text. Syllable counts and passive-voice '
        'detection are heuristics; read the caveat beside any index before relying on it.'
    )
    sections = [('Whole document', report['document'])] + [
        (c['title'] or c['id'], c) for c in report['chapters']
    ]
    for name, entry in sections:
        text(name, 'heading', 'heading2', 2)
        rows = [['Index', 'Value', 'Note']]
        for item in entry['indices'].values():
            rows.append([item['label'], '' if item['value'] is None else item['value'], item['caveat'] or ''])
        table(rows)
        style = entry['style']
        table(
            [
                ['Style measure', 'Value'],
                ['Words', style['words']],
                ['Sentences', style['sentences']],
                ['Average sentence length (words)', style['average_sentence_words']],
                ['Longest sentence (words)', style['longest_sentence_words']],
                ['Passive-voice constructions (heuristic)', style['passive_voice_count']],
                ['Adverbs per 100 words', style['adverbs_per_100_words']],
                ['Dialogue share of words', style['dialogue_ratio']],
                ['Reading time (minutes)', style['reading_minutes']],
            ]
        )
    return doc
