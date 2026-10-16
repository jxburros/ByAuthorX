"""Report sentences longer than options.max_words words.

Protocol (api_version 1): read one JSON object from stdin, write one JSON object to stdout.
Input:  {"api_version": 1, "extension": ..., "check": ..., "language": "en",
         "targets": [{"id": ..., "block": ..., "type": ..., "text": ..., "language": ...}],
         "options": {...}}
Output: {"api_version": 1, "findings": [{"rule", "target", "start", "end", "severity", "message"}]}
Offsets are Unicode scalar values (Python string indexes) into the target's text.
"""

import json
import re
import sys

SENTENCE = re.compile(r'[^.!?]+(?:[.!?]+|$)')


def main():
    request = json.load(sys.stdin)
    limit = int(request.get('options', {}).get('max_words', 40))
    findings = []
    for target in request['targets']:
        text = target['text']
        for match in SENTENCE.finditer(text):
            sentence = match.group()
            words = sentence.split()
            if len(words) > limit:
                start = match.start() + (len(sentence) - len(sentence.lstrip()))
                end = match.end()
                findings.append(
                    {
                        'rule': 'too-long',
                        'target': target['id'],
                        'start': start,
                        'end': end,
                        'severity': 'advisory',
                        'message': f'This sentence has {len(words)} words; the limit is {limit}.',
                        'measured': {'words': len(words), 'limit': limit},
                    }
                )
    json.dump({'api_version': 1, 'findings': findings}, sys.stdout, ensure_ascii=False)


if __name__ == '__main__':
    main()
