"""A tiny stand-in for the LanguageTool HTTP API, used by the tests (never by the application).

It answers /v2/healthcheck, /v2/languages and /v2/check on 127.0.0.1 with a handful of canned rules and
reports offsets in UTF-16 code units, like the Java server does. Run it as a script to stand in for the
`java` launcher: ``fake_languagetool.py ... --port N``.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

LANGUAGES = [
    {'name': 'English (US)', 'code': 'en', 'longCode': 'en-US'},
    {'name': 'English (GB)', 'code': 'en', 'longCode': 'en-GB'},
    {'name': 'German', 'code': 'de', 'longCode': 'de-DE'},
]
# phrase, rule id, issue type, category, replacements
RULES = [
    ('very unique', 'VERY_UNIQUE', 'style', 'STYLE', ['unique']),
    ('teh', 'MORFOLOGIK_RULE_EN_US', 'misspelling', 'TYPOS', ['the']),
    ('Their is', 'THEIR_IS', 'grammar', 'GRAMMAR', ['There is']),
    ('  ', 'WHITESPACE_RULE', 'whitespace', 'TYPOGRAPHY', [' ']),
    ('cat are', 'SUBJECT_VERB', 'grammar', 'GRAMMAR', ['cat is', 'cats are']),
]


def check(text):
    units = text.encode('utf-16-le')
    matches = []
    for phrase, rule_id, issue, category, replacements in RULES:
        needle = phrase.encode('utf-16-le')
        start = 0
        while True:
            index = units.find(needle, start)
            if index < 0:
                break
            if index % 2 == 0:
                matches.append(
                    {
                        'message': f'Possible problem: {phrase.strip()}',
                        'shortMessage': '',
                        'offset': index // 2,
                        'length': len(needle) // 2,
                        'replacements': [{'value': value} for value in replacements],
                        'rule': {
                            'id': rule_id,
                            'description': rule_id,
                            'issueType': issue,
                            'category': {'id': category, 'name': category.title()},
                        },
                    }
                )
            start = index + 2
    return sorted(matches, key=lambda m: m['offset'])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body, kind='application/json'):
        payload = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == '/v2/healthcheck':
            self._send(200, 'OK', 'text/plain')
        elif self.path == '/v2/languages':
            self._send(200, LANGUAGES)
        else:
            self._send(404, 'not found', 'text/plain')

    def do_POST(self):
        if self.path != '/v2/check':
            self._send(404, 'not found', 'text/plain')
            return
        form = parse_qs(self.rfile.read(int(self.headers.get('Content-Length', 0))).decode('utf-8'))
        language = form.get('language', [''])[0]
        if language not in {entry['longCode'] for entry in LANGUAGES}:
            self._send(
                400, f"Error: '{language}' is not a language code known to LanguageTool.", 'text/plain'
            )
            return
        text = form.get('text', [''])[0]
        self._send(
            200,
            {
                'software': {'name': 'LanguageTool', 'version': '6.6-fake'},
                'language': {'name': language, 'code': language},
                'matches': check(text),
            },
        )


def make_server(port=0):
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    arguments = sys.argv[1:]
    server = make_server(int(arguments[arguments.index('--port') + 1]))
    server.serve_forever()
