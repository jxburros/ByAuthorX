"""Self-contained HTML review copy: one file a beta reader opens in any browser, on a phone or a desktop.

``build_html_copy`` writes the text of a snapshot as a single HTML page with inline CSS and JavaScript and no
network access of any kind: the Content-Security-Policy meta element forbids every fetch, frame, font and
form post, and only inline style, inline script and ``data:`` images are allowed. The reader selects text,
adds comments (or a suggested replacement) and downloads a ``<title>.byaxcomments.json`` file to send back;
comments are kept in the browser's ``localStorage`` under the review identifier until then. ``companion=True``
writes the same page as a read-only viewer without any script or comment interface.

Every paragraph and table cell is an element carrying its block identifier (``data-b``) and its text is the
block text exactly (``white-space: pre-wrap``, no inserted nodes), so a comment's anchor is the block ID plus
the three selectors of ``quote_match.selectors`` (position, quote, 32 characters either side). The output
depends only on the arguments: no clock, no random values, the same bytes for the same snapshot.

The comment file's schema and its strict import live in ``beta_readers``.
"""

from __future__ import annotations

import hashlib
import html
import itertools
import json
import re
from typing import Any

from .docx_review import _clean, _loss, _unit_kind, snapshot_hash

HTML_FORMAT = 'byax-review-html'
COMMENTS_FORMAT = 'byax-comments'
COMMENTS_VERSION = 1
NOTE_LIMIT = 2000
READER_LIMIT = 80
SELECTOR_CONTEXT = 32
CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; "
    "base-uri 'none'; form-action 'none'"
)
CSP_COMPANION = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
)
_LANGUAGE = re.compile(r'[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*')
_IMAGES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp')
_TAGS = {
    'bold': 'strong',
    'italic': 'em',
    'underline': 'u',
    'strikethrough': 's',
    'code': 'code',
    'superscript': 'sup',
    'subscript': 'sub',
}


def html_projection(snapshot) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """``(candidate, losses, units)``: the blocks an HTML review copy carries, with located losses.

    ``units`` lists every block written to the copy in body order as ``{id, kind, text, cells}`` (the shape
    ``docx_review.snapshot_hash`` digests), so the pinned snapshot reproduces it when comments come back.
    """
    from .formats import FORMATS, output_document

    candidate, policy = output_document(snapshot, 'html')
    losses = [
        _loss(
            'review_block_not_sent',
            'Excluded from the review copy by the authored output policy; it stays in your document.',
            entry.get('target'),
        )
        for entry in policy
    ]
    capabilities = FORMATS['html']['blocks']
    kept = []
    for block in candidate['blocks']:
        kind = block.get('type')
        if kind in ('page_break', 'section_break', 'footnote'):
            kept.append(block)
            continue
        if capabilities.get(kind, 'full') == 'hidden':
            losses.append(
                _loss(
                    'review_block_not_sent',
                    f'{kind} is not sent in the HTML review copy; it stays unchanged in your document.',
                    block['id'],
                )
            )
            continue
        if kind == 'image' and not _image(candidate, block):
            losses.append(
                _loss(
                    'review_block_not_sent',
                    'Linked image without embedded bytes is sent as its description only.',
                    block['id'],
                )
            )
        kept.append(block)
    candidate['blocks'] = kept
    review = snapshot.get('review', {})
    open_comments = [c for c in review.get('comments', []) if c.get('status') != 'resolved']
    if open_comments:
        losses.append(
            _loss(
                'review_comments_not_shown',
                f'Your {len(open_comments)} open comment(s) are not shown in the HTML copy; readers see the text only.',
                'review',
                count=len(open_comments),
            )
        )
    for change in review.get('tracked_changes', []):
        if change.get('status') == 'pending':
            losses.append(
                _loss(
                    'review_tracked_changes_flattened',
                    'A pending tracked change is sent as current text; accept or reject it in ByAuthorX.',
                    change.get('target'),
                )
            )
    if any(p.get('status') == 'pending' for p in review.get('proposals', [])):
        losses.append(
            _loss(
                'review_proposals_not_sent', 'Pending proposals stay in ByAuthorX and are not sent.', 'review'
            )
        )
    units = block_units(kept)
    return candidate, losses, units


def block_units(blocks) -> list[dict[str, Any]]:
    """``{id, kind, text, cells}`` for every block, the shape ``docx_review.snapshot_hash`` digests."""
    units = []
    for block in blocks:
        kind = _unit_kind(block)
        unit = {
            'id': block['id'],
            'kind': kind,
            'text': _clean(block.get('text', '')) if kind == 'text' else '',
        }
        if kind == 'table':
            unit['cells'] = [
                [{'id': cell['id'], 'text': _clean(cell.get('text', ''))} for cell in row.get('cells', [])]
                for row in block.get('rows', [])
            ]
        units.append(unit)
    return units


def _image(doc, block):
    asset = doc.get('assets', {}).get(block.get('attrs', {}).get('asset'), {})
    return asset if asset.get('mime') in _IMAGES and asset.get('data') else None


def _inline(block) -> str:
    text = _clean(block.get('text', ''))
    marks = block.get('marks', [])
    bounds = sorted(
        {0, len(text), *[max(0, min(len(text), int(m.get(k, 0)))) for m in marks for k in ('start', 'end')]}
    )
    out = []
    for start, end in itertools.pairwise(bounds):
        value = html.escape(text[start:end])
        for mark in marks:
            tag = _TAGS.get(mark.get('type'))
            if (
                tag
                and mark.get('value', True)
                and mark.get('start', 0) <= start
                and mark.get('end', 0) >= end
            ):
                value = f'<{tag}>{value}</{tag}>'
        out.append(value)
    return ''.join(out)


def _list_kind(attrs):
    value = attrs.get('list')
    return value.get('kind', 'bullet') if isinstance(value, dict) else value


def _render_body(candidate, *, commentable) -> str:
    out: list[str] = []
    open_list = None
    # One tab stop for the text: Up and Down move between blocks, so a keyboard reader is not made to
    # tab through every paragraph.
    tab = iter(itertools.chain([' tabindex="0"'], itertools.repeat(' tabindex="-1"')))

    def close():
        nonlocal open_list
        if open_list:
            out.append(f'</{open_list}>')
            open_list = None

    for block in candidate['blocks']:
        kind, attrs, ident = block.get('type'), block.get('attrs', {}), html.escape(block['id'], quote=True)
        align = attrs.get('alignment')
        css = f' class="a-{align}"' if align in ('center', 'right') else ''
        focus = (
            next(tab) if commentable and kind not in ('page_break', 'section_break', 'image', 'table') else ''
        )
        base = f'data-b="{ident}" dir="auto"{focus}{css}'
        if kind in ('page_break', 'section_break'):
            close()
            out.append('<hr class="break">')
        elif kind == 'table':
            close()
            rows = []
            for index, row in enumerate(block.get('rows', [])):
                tag = 'th' if index < attrs.get('header_rows', 0) else 'td'
                scope = ' scope="col"' if tag == 'th' else ''
                cells = ''.join(
                    f'<{tag}{scope} data-b="{html.escape(cell["id"], quote=True)}" dir="auto"'
                    f'{next(tab) if commentable else ""}>{_inline(cell)}</{tag}>'
                    for cell in row.get('cells', [])
                )
                rows.append(f'<tr>{cells}</tr>')
            out.append(f'<div class="table-wrap"><table>{"".join(rows)}</table></div>')
        elif kind == 'image':
            close()
            asset = _image(candidate, block)
            alt = html.escape(attrs.get('alt', ''), quote=True)
            if asset:
                out.append(
                    f'<figure><img src="data:{asset["mime"]};base64,{asset["data"]}" alt="{alt}"></figure>'
                )
            else:
                out.append(f'<p class="missing">{alt or "[image not included]"}</p>')
        elif kind == 'heading':
            close()
            level = max(2, min(6, int(attrs.get('level', 1)) + 1))
            out.append(f'<h{level} {base}>{_inline(block)}</h{level}>')
        elif attrs.get('list'):
            tag = 'ol' if _list_kind(attrs) in ('ordered', 'numbered') else 'ul'
            if open_list != tag:
                close()
                out.append(f'<{tag}>')
                open_list = tag
            out.append(f'<li {base}>{_inline(block)}</li>')
        else:
            close()
            if block.get('style') == 'code':
                out.append(f'<pre {base}>{_inline(block)}</pre>')
            elif block.get('style') == 'quotation':
                out.append(f'<blockquote {base}>{_inline(block)}</blockquote>')
            else:
                out.append(f'<p {base}>{_inline(block)}</p>')
    close()
    return '\n'.join(out)


def review_html_id(doc, reviewer='', *, companion=False) -> str:
    """Identifier of an HTML review copy of ``doc``'s current revision (its own namespace, not Word's)."""
    parts = ['review', 'html', doc['id'], doc['revision'], reviewer or '']
    if companion:
        parts.append('companion')
    return 'rv_' + hashlib.sha256('\x1f'.join(parts).encode('utf-8')).hexdigest()[:16]


def _json_script(value) -> str:
    text = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    return text.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def build_html_copy(
    snapshot, *, review_id, reviewer='', note='', companion=False
) -> tuple[bytes, dict[str, Any]]:
    """Bytes of the review page and ``{losses, units, snapshot_hash, sha256}``. No I/O, no clock.

    The output depends only on the arguments, so the same snapshot and identifier give the same bytes.
    """
    from .errors import ByaxError

    if not isinstance(note, str) or len(note) > NOTE_LIMIT:
        raise ByaxError(
            'invalid_input', f'The note must be text of at most {NOTE_LIMIT} characters', field='note'
        )
    if not isinstance(reviewer, str) or len(reviewer) > READER_LIMIT:
        raise ByaxError('invalid_input', 'The reader name is too long', field='reviewer')
    candidate, losses, units = html_projection(snapshot)
    digest = snapshot_hash(units)
    title = _clean(snapshot.get('metadata', {}).get('title', '')).strip() or 'Untitled'
    language = snapshot.get('metadata', {}).get('language', 'en')
    language = language if isinstance(language, str) and _LANGUAGE.fullmatch(language) else 'en'
    config = {
        'format': HTML_FORMAT,
        'version': COMMENTS_VERSION,
        'review_id': review_id,
        'document_id': snapshot['id'],
        'snapshot_hash': digest,
        'title': title,
        'reviewer': _clean(reviewer),
        'companion': bool(companion),
    }
    body = _render_body(candidate, commentable=not companion)
    esc = html.escape
    note_html = (
        f'<section class="note" aria-label="Note from the writer"><p>{esc(_clean(note))}</p></section>'
        if note.strip()
        else ''
    )
    kicker = 'Read-only copy' if companion else 'Review copy'
    if reviewer.strip():
        kicker += f' for {esc(_clean(reviewer).strip())}'
    if companion:
        main = f'<main id="doc" tabindex="-1">\n{body}\n</main>'
        scripts = ''
        chrome = ''
        policy = CSP_COMPANION
        help_text = (
            '<p class="hint">This is a read-only copy of the manuscript. It cannot take comments; '
            'send your notes to the writer another way.</p>'
        )
    else:
        main = f'<div class="layout"><main id="doc" tabindex="-1">\n{body}\n</main>{_SIDE}</div>'
        chrome = _BAR + _DIALOGS
        scripts = f'<script id="byax-review" type="application/json">{_json_script(config)}</script>\n<script>{_SCRIPT}</script>'
        policy = CSP
        help_text = _HELP
    page = (
        f'<!doctype html>\n<html lang="{language}" data-companion="{1 if companion else 0}">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{esc(policy, quote=True)}">\n'
        '<meta name="color-scheme" content="light dark">\n'
        '<meta name="generator" content="ByAuthorX review copy">\n'
        f'<meta name="byax-review-id" content="{esc(review_id, quote=True)}">\n'
        f'<meta name="byax-snapshot" content="{digest}">\n'
        f'<title>{esc(title)}</title>\n<style>{_STYLE}</style>\n</head>\n<body>\n'
        '<a class="skip" href="#doc">Skip to the text</a>\n'
        f'<header class="top"><p class="kicker">{kicker}</p><h1>{esc(title)}</h1>{note_html}{help_text}</header>\n'
        f'{main}\n{chrome}\n<div id="status" class="sr" role="status" aria-live="polite"></div>\n'
        f'{scripts}\n</body>\n</html>\n'
    )
    data = page.encode('utf-8')
    return data, {
        'losses': losses,
        'units': units,
        'snapshot_hash': digest,
        'sha256': hashlib.sha256(data).hexdigest(),
        'review_id': review_id,
    }


_HELP = (
    '<details class="help"><summary>How to comment</summary><ul>'
    '<li>Select some text, then press <strong>Comment</strong>. On a phone, press and hold a word and drag.</li>'
    '<li>With a keyboard: Tab into the text, move between paragraphs with the Up and Down arrow keys and press '
    '<kbd>C</kbd> to comment on the paragraph. To comment on part of a paragraph, select it with the mouse or '
    'touch and press <kbd>C</kbd>.</li>'
    '<li>You can also suggest replacement text or deleting the selection; the writer decides.</li>'
    '<li>Your comments stay in this browser on this device. When you are done, press '
    '<strong>Download my comments</strong> and send the file back to the writer.</li>'
    '<li>Nothing is sent over the internet by this page.</li></ul></details>'
)

_SIDE = (
    '<aside id="comments" aria-labelledby="comments-h"><h2 id="comments-h">Your comments '
    '<span id="count" class="count">0</span></h2>'
    '<p class="who">Reading as <strong id="who-name">(not set)</strong> '
    '<button type="button" id="change-name" class="link">Change name</button></p>'
    '<p id="empty" class="hint">No comments yet. Select text and press Comment.</p>'
    '<ol id="list" aria-label="Your comments"></ol>'
    '<div class="actions"><button type="button" id="export" class="primary">Download my comments</button></div>'
    '<details class="fallback"><summary>Download did not start?</summary>'
    '<p class="hint">Copy this text into a file named <code>comments.byaxcomments.json</code> and send it.</p>'
    '<textarea id="raw" readonly rows="6" aria-label="Comment file text"></textarea></details></aside>'
)

_BAR = (
    '<div class="bar" role="toolbar" aria-label="Comment tools">'
    '<button type="button" id="add" class="primary">Comment</button>'
    '<a href="#comments" id="jump">Comments (<span id="count2">0</span>)</a>'
    '<button type="button" id="export2">Download</button></div>'
)

_DIALOGS = (
    '<dialog id="composer" aria-labelledby="composer-h"><form id="composer-form" method="dialog">'
    '<h2 id="composer-h">Add a comment</h2>'
    '<blockquote id="composer-quote" class="quote" hidden></blockquote>'
    '<fieldset id="kinds"><legend>What would you like to do?</legend>'
    '<label><input type="radio" name="kind" value="comment" checked> Comment</label>'
    '<label><input type="radio" name="kind" value="replace"> Suggest replacing the selected text</label>'
    '<label><input type="radio" name="kind" value="delete"> Suggest deleting the selected text</label></fieldset>'
    '<label for="c-repl" id="c-repl-l">Replacement text</label>'
    '<textarea id="c-repl" rows="3" maxlength="5000"></textarea>'
    '<label for="c-text" id="c-text-l">Your comment</label>'
    '<textarea id="c-text" rows="4" maxlength="5000"></textarea>'
    '<p id="composer-error" class="error" role="alert" hidden></p>'
    '<div class="actions"><button type="submit" id="c-save" class="primary">Save comment</button>'
    '<button type="button" id="c-cancel">Cancel</button></div></form></dialog>'
    '<dialog id="who" aria-labelledby="who-h"><form id="who-form" method="dialog"><h2 id="who-h">Who is reading?</h2>'
    '<p>Your name goes on every comment so the writer knows who said what. It stays in this file and the '
    'comment file you send back.</p>'
    '<label for="who-input">Your name</label><input id="who-input" type="text" maxlength="80" autocomplete="name">'
    '<p id="who-error" class="error" role="alert" hidden></p>'
    '<div class="actions"><button type="submit" class="primary">Continue</button>'
    '<button type="button" id="who-cancel">Cancel</button></div></form></dialog>'
)

_STYLE = """
:root{color-scheme:light dark;--bg:#f4eee3;--surface:#fffcf7;--ink:#252927;--muted:#52615b;--rule:#d7d0c3;
--accent:#246052;--accent-ink:#fff;--mark:#fbe7a1;--error:#ad283e;--font:Georgia,'Iowan Old Style','Times New Roman',serif;
--ui:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#1c201e;--surface:#262b29;--ink:#ece8df;--muted:#b4beb9;--rule:#3d4542;
--accent:#74c7b3;--accent-ink:#10231e;--mark:#5a4d17;--error:#ff8a9b}}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:1.125rem/1.65 var(--font);overflow-wrap:anywhere;padding-bottom:5rem}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.skip{position:absolute;left:.5rem;top:-4rem;background:var(--surface);color:var(--ink);padding:.5rem 1rem;z-index:30;
border:2px solid var(--accent);border-radius:6px}
.skip:focus{top:.5rem}
.top,main,#comments{max-width:42rem;margin-inline:auto;padding-inline:1rem}
.top{padding-top:1.25rem}
.kicker{margin:0;font:600 .8rem/1.2 var(--ui);letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
h1{font-size:1.9rem;line-height:1.2;margin:.3rem 0 .8rem}
h2,h3,h4,h5,h6{line-height:1.25;margin:1.8rem 0 .6rem}
.note{background:var(--surface);border-left:4px solid var(--accent);border-radius:6px;padding:.1rem 1rem;margin:1rem 0;
font:1rem/1.5 var(--ui);white-space:pre-wrap}
.hint{font:.95rem/1.5 var(--ui);color:var(--muted)}
.help{font:.95rem/1.5 var(--ui);margin:.5rem 0 1rem}
.help summary,.fallback summary{cursor:pointer;min-height:2.75rem;display:flex;align-items:center;color:var(--accent);font-weight:600}
.help li{margin:.3rem 0}
kbd{font:.85em var(--ui);border:1px solid var(--rule);border-bottom-width:2px;border-radius:4px;padding:0 .3em;background:var(--surface)}
main{padding-top:.5rem;padding-bottom:1rem}
main:focus{outline:none}
[data-b]{white-space:pre-wrap;position:relative}
p[data-b],li[data-b],blockquote[data-b],pre[data-b]{margin:0 0 1rem}
blockquote[data-b]{border-left:3px solid var(--rule);padding-left:1rem;font-style:italic}
pre[data-b]{font:.95rem/1.5 ui-monospace,Menlo,Consolas,monospace;overflow-x:auto;background:var(--surface);padding:.75rem;border-radius:6px}
li[data-b]{margin-bottom:.4rem}
.a-center{text-align:center}.a-right{text-align:right}
[tabindex="0"]:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:3px}
[data-n]::after{content:' \\2022 ' attr(data-n);font:.75rem var(--ui);color:var(--accent);white-space:nowrap}
.table-wrap{overflow-x:auto;margin:0 0 1rem}
table{border-collapse:collapse;width:100%;table-layout:fixed;font-size:1rem}
th,td{border:1px solid var(--rule);padding:.4rem .6rem;vertical-align:top;text-align:left}
th{background:var(--surface)}
figure{margin:1rem 0}img{max-width:100%;height:auto}
.missing{color:var(--muted);font-style:italic}
hr.break{border:0;border-top:1px solid var(--rule);margin:2rem auto;width:40%}
::highlight(byax-comment){background-color:var(--mark);color:inherit}
.layout{display:block}
#comments{padding-top:1rem;padding-bottom:2rem;font:1rem/1.5 var(--ui)}
#comments h2{font-size:1.2rem;margin-top:.5rem}
.count{font-weight:400;color:var(--muted)}
.who{margin:.2rem 0 .8rem}
#list{list-style:none;margin:0;padding:0}
#list li{background:var(--surface);border:1px solid var(--rule);border-radius:8px;padding:.6rem .8rem;margin:0 0 .6rem}
#list .q{font-style:italic;color:var(--muted);margin:0 0 .3rem;border-left:3px solid var(--rule);padding-left:.6rem}
#list .meta{font-size:.85rem;color:var(--muted);margin:0 0 .2rem}
#list .body{margin:0 0 .4rem;white-space:pre-wrap}
#list .warn{color:var(--error);font-size:.9rem;margin:0 0 .3rem}
button,input[type=text],textarea{font:inherit;font-family:var(--ui)}
button,.bar a{min-height:2.75rem;min-width:2.75rem;padding:.4rem 1rem;border-radius:6px;border:1px solid var(--accent);
background:var(--surface);color:var(--accent);font-weight:600;font-size:1rem;cursor:pointer}
button.primary{background:var(--accent);color:var(--accent-ink)}
button.link{border:0;background:none;text-decoration:underline;min-height:2.75rem;padding:.2rem .4rem}
button.small{min-height:2.75rem;font-size:.9rem}
button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.actions{display:flex;flex-wrap:wrap;gap:.6rem;margin:.8rem 0}
textarea,input[type=text]{width:100%;background:var(--surface);color:var(--ink);border:1px solid var(--muted);border-radius:6px;padding:.5rem;font-size:1rem}
.bar{position:fixed;left:0;right:0;bottom:0;z-index:20;display:flex;gap:.5rem;align-items:center;justify-content:center;
padding:.5rem max(.75rem,env(safe-area-inset-left)) calc(.5rem + env(safe-area-inset-bottom));background:var(--surface);
border-top:1px solid var(--rule);font-family:var(--ui)}
.bar a,.bar button{display:inline-flex;align-items:center;justify-content:center;text-decoration:none;white-space:nowrap;padding:.4rem .6rem;font-size:.95rem;flex:1 1 auto;max-width:12rem}
dialog{width:min(36rem,calc(100vw - 1.5rem));max-height:calc(100vh - 1.5rem);overflow:auto;background:var(--surface);color:var(--ink);
border:2px solid var(--accent);border-radius:8px;padding:1rem;font:1rem/1.5 var(--ui)}
dialog::backdrop{background:rgba(0,0,0,.45)}
dialog h2{margin-top:0;font-size:1.25rem}
dialog label{display:block;margin:.8rem 0 .2rem;font-weight:600}
fieldset{border:1px solid var(--rule);border-radius:6px;margin:.8rem 0;padding:.4rem .8rem}
fieldset label{font-weight:400;margin:.2rem 0;min-height:2.75rem;display:flex;align-items:center;gap:.5rem}
input[type=radio]{width:1.25rem;height:1.25rem}
.quote{margin:.5rem 0;padding-left:.8rem;border-left:3px solid var(--accent);font-style:italic;max-height:6rem;overflow:auto;white-space:pre-wrap}
.error{color:var(--error);margin:.4rem 0}
@media (min-width:64rem){.layout{display:grid;grid-template-columns:minmax(0,42rem) 20rem;gap:2rem;max-width:64rem;margin-inline:auto;align-items:start}
.layout main{margin:0;max-width:none;padding-inline:0}
#comments{margin:0;padding-inline:0;position:sticky;top:1rem;max-height:calc(100vh - 6rem);overflow:auto}
.bar #jump{display:none}.top{padding-inline:0;max-width:64rem}}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{animation:none!important;transition:none!important}}
@media print{.bar,#comments,.skip,.help{display:none}body{background:#fff;color:#000;padding:0}}
"""

_SCRIPT = r"""
(function () {
'use strict';
var cfg = JSON.parse(document.getElementById('byax-review').textContent);
var KEY = 'byax-review:' + cfg.review_id;
var CONTEXT = 32;
var memory = null;
var state = { reader: '', comments: [] };
var blocks = {};
var pending = null;       // {block, start, end} or {block} captured for the composer
var editing = null;       // comment being edited
var lastSelection = null;
var pressing = false;
var $ = function (id) { return document.getElementById(id); };

function load() {
  var raw = null;
  try { raw = window.localStorage.getItem(KEY); } catch (e) { raw = memory; }
  if (!raw) { return; }
  try {
    var saved = JSON.parse(raw);
    if (saved && Array.isArray(saved.comments)) { state.comments = saved.comments.filter(validStored); }
    if (saved && typeof saved.reader === 'string' && saved.reader) { state.reader = saved.reader; }
  } catch (e) { /* a damaged store is ignored */ }
}
function save() {
  var raw = JSON.stringify(state);
  try { window.localStorage.setItem(KEY, raw); } catch (e) { memory = raw; }
}
function validStored(c) {
  return c && typeof c.id === 'string' && typeof c.block === 'string' && typeof c.text === 'string';
}
function announce(message) { $('status').textContent = ''; window.setTimeout(function () { $('status').textContent = message; }, 30); }
function wellFormed(text) {
  return Array.from(text).filter(function (ch) {
    var code = ch.charCodeAt(0);
    return ch.length > 1 || code < 0xD800 || code > 0xDFFF;
  }).join('');
}
function cpIndex(text, index) { return Array.from(text.slice(0, index)).length; }
function u16Index(text, cp) { return Array.from(text).slice(0, cp).join('').length; }
function textOf(el) { return el.textContent; }
function blockOf(node) {
  while (node && node !== document.body) {
    if (node.nodeType === 1 && node.hasAttribute('data-b')) { return node; }
    node = node.parentNode;
  }
  return null;
}
function offsetIn(block, node, offset) {
  var range = document.createRange();
  range.selectNodeContents(block);
  range.setEnd(node, offset);
  return range.toString().length;
}
function readSelection() {
  var sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) { return null; }
  var range = sel.getRangeAt(0);
  var first = blockOf(range.startContainer);
  var last = blockOf(range.endContainer);
  if (!first) { return null; }
  var start = offsetIn(first, range.startContainer, range.startOffset);
  var end = first === last ? offsetIn(first, range.endContainer, range.endOffset) : textOf(first).length;
  if (end <= start || !textOf(first).slice(start, end).trim()) { return null; }
  return { block: first.getAttribute('data-b'), start: start, end: end, clipped: first !== last };
}
function currentTarget() {
  var selected = readSelection() || (pressing ? lastSelection : null);
  if (selected) { return selected; }
  var active = blockOf(document.activeElement);
  if (active) { return { block: active.getAttribute('data-b') }; }
  return lastSelection;
}
function selectors(block, start, end) {
  var text = textOf(blocks[block]);
  var before = Array.from(text.slice(0, start)).slice(-CONTEXT).join('');
  var after = Array.from(text.slice(end)).slice(0, CONTEXT).join('');
  return { quote: text.slice(start, end), prefix: before, suffix: after };
}
function locate(comment) {
  var el = blocks[comment.block];
  if (!el) { return null; }
  if (typeof comment.quote !== 'string' || !comment.quote) { return { start: 0, end: 0 }; }
  var text = textOf(el);
  if (text.slice(comment.start, comment.end) === comment.quote) { return { start: comment.start, end: comment.end }; }
  var best = null;
  var at = text.indexOf(comment.quote);
  while (at >= 0) {
    var score = 0;
    var before = text.slice(Math.max(0, at - CONTEXT), at);
    var after = text.slice(at + comment.quote.length, at + comment.quote.length + CONTEXT);
    var i = 0;
    while (i < before.length && before[before.length - 1 - i] === (comment.prefix || '')[(comment.prefix || '').length - 1 - i]) { i++; }
    score += i; i = 0;
    while (i < after.length && after[i] === (comment.suffix || '')[i]) { i++; }
    score += i;
    var distance = Math.abs(at - comment.start);
    if (!best || score > best.score || (score === best.score && distance < best.distance)) {
      best = { start: at, end: at + comment.quote.length, score: score, distance: distance };
    }
    at = text.indexOf(comment.quote, at + 1);
  }
  return best;
}
function rangeFor(el, start, end) {
  var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  var range = document.createRange();
  var seen = 0, node, startSet = false;
  while ((node = walker.nextNode())) {
    var length = node.nodeValue.length;
    if (!startSet && start <= seen + length) { range.setStart(node, start - seen); startSet = true; }
    if (startSet && end <= seen + length) { range.setEnd(node, end - seen); return range; }
    seen += length;
  }
  return null;
}
function paint() {
  var counts = {};
  var ranges = [];
  state.comments.forEach(function (c) {
    var el = blocks[c.block];
    if (!el) { return; }
    counts[c.block] = (counts[c.block] || 0) + 1;
    var found = locate(c);
    if (found && found.end > found.start && window.CSS && CSS.highlights && window.Highlight) {
      var range = rangeFor(el, found.start, found.end);
      if (range) { ranges.push(range); }
    }
  });
  Object.keys(blocks).forEach(function (id) {
    if (counts[id]) { blocks[id].setAttribute('data-n', String(counts[id])); } else { blocks[id].removeAttribute('data-n'); }
  });
  if (window.CSS && CSS.highlights && window.Highlight) {
    CSS.highlights.delete('byax-comment');
    if (ranges.length) {
      var highlight = new Highlight();
      ranges.forEach(function (range) { highlight.add(range); });
      CSS.highlights.set('byax-comment', highlight);
    }
  }
}
function render() {
  var list = $('list');
  list.textContent = '';
  state.comments.forEach(function (c, index) {
    var li = document.createElement('li');
    var found = locate(c);
    if (c.quote) {
      var q = document.createElement('p'); q.className = 'q'; q.textContent = c.quote; li.appendChild(q);
    }
    var meta = document.createElement('p'); meta.className = 'meta';
    meta.textContent = c.kind === 'suggestion' ? (c.replacement ? 'Suggested replacement' : 'Suggested deletion') : 'Comment';
    li.appendChild(meta);
    if (c.kind === 'suggestion' && c.replacement) {
      var r = document.createElement('p'); r.className = 'body'; r.textContent = 'Replace with: ' + c.replacement; li.appendChild(r);
    }
    if (c.text) { var b = document.createElement('p'); b.className = 'body'; b.textContent = c.text; li.appendChild(b); }
    if (!found) {
      var w = document.createElement('p'); w.className = 'warn';
      w.textContent = 'This passage is no longer in the text; the writer will see it as a note on the paragraph.';
      li.appendChild(w);
    }
    var actions = document.createElement('div'); actions.className = 'actions';
    var goto = document.createElement('button'); goto.type = 'button'; goto.className = 'small'; goto.textContent = 'Show in text';
    goto.setAttribute('aria-label', 'Show comment ' + (index + 1) + ' in the text');
    goto.addEventListener('click', function () { var el = blocks[c.block]; if (el) { el.scrollIntoView({ block: 'center' }); el.focus({ preventScroll: true }); } });
    var edit = document.createElement('button'); edit.type = 'button'; edit.className = 'small'; edit.textContent = 'Edit';
    edit.setAttribute('aria-label', 'Edit comment ' + (index + 1));
    edit.addEventListener('click', function () { openComposer(c); });
    var del = document.createElement('button'); del.type = 'button'; del.className = 'small'; del.textContent = 'Delete';
    del.setAttribute('aria-label', 'Delete comment ' + (index + 1));
    del.addEventListener('click', function () {
      state.comments.splice(state.comments.indexOf(c), 1); save(); render(); announce('Comment deleted.');
    });
    [goto, edit, del].forEach(function (button) { actions.appendChild(button); });
    li.appendChild(actions);
    list.appendChild(li);
  });
  $('empty').hidden = state.comments.length > 0;
  $('count').textContent = String(state.comments.length);
  $('count2').textContent = String(state.comments.length);
  $('who-name').textContent = state.reader || '(not set)';
  paint();
}
function openDialog(dialog) {
  if (typeof dialog.showModal === 'function') { dialog.showModal(); } else { dialog.setAttribute('open', ''); }
}
function closeDialog(dialog) {
  if (typeof dialog.close === 'function') { dialog.close(); } else { dialog.removeAttribute('open'); }
}
function askName(then) {
  var input = $('who-input');
  input.value = state.reader || cfg.reviewer || '';
  $('who-error').hidden = true;
  $('who-form').onsubmit = function (event) {
    event.preventDefault();
    var name = wellFormed(input.value).trim();
    if (!name) { $('who-error').textContent = 'Please enter your name.'; $('who-error').hidden = false; input.focus(); return; }
    state.reader = name.slice(0, 80); save(); closeDialog($('who')); render();
    if (then) { then(); }
  };
  openDialog($('who'));
  input.focus();
}
function kindValue() { return document.querySelector('#kinds input:checked').value; }
function syncKind() {
  var kind = kindValue();
  $('c-repl').hidden = $('c-repl-l').hidden = kind !== 'replace';
  $('c-text-l').textContent = kind === 'comment' ? 'Your comment' : 'Why? (optional)';
}
function openComposer(existing) {
  var target = existing ? { block: existing.block, start: existing.start, end: existing.end } : currentTarget();
  if (!target) { announce('Select some text, or focus a paragraph and press C, then press Comment.'); return; }
  if (!state.reader) { askName(function () { openComposer(existing); }); return; }
  editing = existing || null;
  pending = target;
  var hasRange = typeof target.start === 'number' && target.end > target.start;
  var quote = hasRange ? textOf(blocks[target.block]).slice(target.start, target.end) : '';
  $('composer-h').textContent = existing ? 'Edit your comment' : 'Add a comment';
  $('composer-quote').hidden = !hasRange;
  $('composer-quote').textContent = quote;
  $('kinds').hidden = !hasRange;
  var kind = existing && existing.kind === 'suggestion' ? (existing.replacement ? 'replace' : 'delete') : 'comment';
  document.querySelector('#kinds input[value="' + kind + '"]').checked = true;
  $('c-repl').value = existing ? (existing.replacement || '') : '';
  $('c-text').value = existing ? existing.text : '';
  $('composer-error').hidden = true;
  syncKind();
  openDialog($('composer'));
  $('c-text').focus();
}
function saveComment(event) {
  event.preventDefault();
  var kind = pending && typeof pending.start === 'number' ? kindValue() : 'comment';
  var text = wellFormed($('c-text').value).trim();
  var replacement = kind === 'replace' ? wellFormed($('c-repl').value) : '';
  var error = '';
  if (kind === 'comment' && !text) { error = 'Please write a comment.'; }
  if (kind === 'replace' && !replacement.trim()) { error = 'Please write the replacement text.'; }
  if (error) { $('composer-error').textContent = error; $('composer-error').hidden = false; return; }
  var comment = editing || { id: 'cm_' + randomId(), created: new Date().toISOString(), block: pending.block };
  comment.kind = kind === 'comment' ? 'comment' : 'suggestion';
  comment.text = text;
  comment.replacement = replacement;
  if (typeof pending.start === 'number' && pending.end > pending.start && !editing) {
    var s = selectors(pending.block, pending.start, pending.end);
    comment.start = pending.start; comment.end = pending.end;
    comment.quote = wellFormed(s.quote); comment.prefix = wellFormed(s.prefix); comment.suffix = wellFormed(s.suffix);
  }
  if (!editing) { state.comments.push(comment); }
  save(); closeDialog($('composer')); render();
  var selection = window.getSelection(); if (selection) { selection.removeAllRanges(); }
  lastSelection = null;
  announce(editing ? 'Comment updated.' : 'Comment saved.');
  editing = null;
}
function randomId() {
  var bytes = new Uint8Array(9);
  if (window.crypto && window.crypto.getRandomValues) { window.crypto.getRandomValues(bytes); }
  else { for (var i = 0; i < bytes.length; i++) { bytes[i] = Math.floor(Math.random() * 256); } }
  return Array.prototype.map.call(bytes, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
}
function fileData() {
  var comments = state.comments.map(function (c) {
    var el = blocks[c.block];
    var out = { id: c.id, block: c.block, kind: c.kind || 'comment', text: c.text || '', created: c.created || '' };
    var found = c.quote ? locate(c) : null;
    if (c.quote) {
      out.quote = c.quote; out.prefix = c.prefix || ''; out.suffix = c.suffix || '';
      var text = el ? textOf(el) : '';
      var start = found ? found.start : (typeof c.start === 'number' ? c.start : 0);
      out.position = { start: cpIndex(text, start), end: cpIndex(text, start + c.quote.length) };
    }
    if (c.kind === 'suggestion') { out.replacement = c.replacement || ''; }
    return out;
  });
  return {
    format: 'byax-comments', version: 1, review_id: cfg.review_id, document_id: cfg.document_id,
    snapshot_hash: cfg.snapshot_hash, title: cfg.title, reader: state.reader,
    exported: new Date().toISOString(), comments: comments
  };
}
function fileName() {
  var base = cfg.title.replace(/[\\\/:*?"<>|\u0000-\u001f]/g, '_').trim().slice(0, 80) || 'comments';
  return base + '.byaxcomments.json';
}
function exportFile() {
  if (!state.reader) { askName(exportFile); return; }
  if (!state.comments.length) { announce('There are no comments to download yet.'); return; }
  var json = JSON.stringify(fileData(), null, 2);
  $('raw').value = json;
  var blob = new Blob([json], { type: 'application/json' });
  var link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = fileName();
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  window.setTimeout(function () { URL.revokeObjectURL(link.href); link.remove(); }, 1000);
  announce('Your comment file is downloading. Send it to the writer.');
}
function init() {
  Array.prototype.forEach.call(document.querySelectorAll('[data-b]'), function (el) { blocks[el.getAttribute('data-b')] = el; });
  load();
  document.addEventListener('selectionchange', function () {
    var selected = readSelection();
    if (selected) { lastSelection = selected; } else if (!pressing) { lastSelection = null; }
  });
  ['add', 'export', 'export2', 'jump', 'change-name'].forEach(function (id) {
    var el = $(id); if (!el) { return; }
    el.addEventListener('pointerdown', function () { pressing = true; window.setTimeout(function () { pressing = false; }, 700); });
    el.addEventListener('mousedown', function (event) { if (id === 'add') { event.preventDefault(); } });
  });
  $('add').addEventListener('click', function () { openComposer(null); });
  $('export').addEventListener('click', exportFile);
  $('export2').addEventListener('click', exportFile);
  $('change-name').addEventListener('click', function () { askName(null); });
  $('composer-form').addEventListener('submit', saveComment);
  $('c-cancel').addEventListener('click', function () { closeDialog($('composer')); editing = null; });
  $('who-cancel').addEventListener('click', function () { closeDialog($('who')); });
  Array.prototype.forEach.call(document.querySelectorAll('#kinds input'), function (el) { el.addEventListener('change', syncKind); });
  var order = Array.prototype.slice.call(document.querySelectorAll('[data-b]'));
  document.addEventListener('keydown', function (event) {
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && event.target.hasAttribute && event.target.hasAttribute('data-b')
        && !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey) {
      var next = order[order.indexOf(event.target) + (event.key === 'ArrowDown' ? 1 : -1)];
      if (next) { event.preventDefault(); next.focus(); }
      return;
    }
    if (event.key !== 'c' && event.key !== 'C') { return; }
    if (event.ctrlKey || event.metaKey || event.altKey) { return; }
    var tag = event.target && event.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'BUTTON' || tag === 'SELECT' || tag === 'A') { return; }
    if (document.querySelector('dialog[open]')) { return; }
    event.preventDefault();
    openComposer(null);
  });
  render();
}
init();
}());
"""
