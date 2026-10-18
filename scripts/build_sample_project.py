#!/usr/bin/env python3
"""Regenerate the bundled sample project; the same inputs always give the same document.

    uv run python scripts/build_sample_project.py            # write src/byax/resources/samples/
    uv run python scripts/build_sample_project.py --check    # fail if the committed file is stale

The project is built only through canonical engine operations (the same ones the GUI, CLI and MCP
use), with identities and timestamps pinned, so the output depends on this file alone. It teaches
the semantic model: named styles, a binder with an outline, a proposal that is not yet accepted, a
comment, a citation with a source record, and a recorded clean checking report. The text is
original and written for this project.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from byax import citations, engine, project  # noqa: E402
from byax.citations import import_sources  # noqa: E402
from byax.model import new_document, validate_document  # noqa: E402
from byax.storage import save_document  # noqa: E402

OUTPUT = ROOT / 'src' / 'byax' / 'resources' / 'samples' / 'sample-project.byax'
EPOCH = datetime(2026, 1, 5, 9, 0, 0, tzinfo=UTC)
# The day recorded as the source's retrieval date; citations stamp date.today(), which would make the
# bundled sample stale every day.
RETRIEVED = date(2026, 10, 17)

TITLE = 'The Lamp at Harrow Point'
CHAPTER_ONE = 'Chapter one: Arrival'
CHAPTER_TWO = 'Chapter two: The long night'
SCENES = {
    'tide': ('The evening tide', 'Mara reaches the island and meets the keeper.'),
    'keeper': ('The keeper', 'Tobias shows her the lamp and the rules of the house.'),
    'night': ('The failing lamp', 'The lamp fails at midnight and Mara has to decide what to do.'),
}
NOTES = [
    'This is a sample project. Every feature it shows is one you can use in your own manuscripts, and '
    'none of it is stored anywhere except in this file.',
    'Styles: headings and body text use named styles, and the quotation in chapter one uses a style '
    'called Margin note that this project defines. Change the style once and every paragraph that uses '
    'it follows.',
    'Outline and binder: each chapter and scene is a binder item with a status, a synopsis and a word '
    'target. Open the Outline panel to move whole sections without losing their text.',
    'Proposal: the second paragraph of chapter one carries a pending proposal from a reviewer. Nothing '
    'changes until you accept it, and you can accept or reject each edit on its own.',
    'Comment: a comment is attached to the first paragraph. Replying or resolving it never alters the '
    'manuscript text.',
    'Citation: the quotation in the first scene cites a source record for a report that exists only in '
    'this sample. Citations point at records, so the reference list always matches the text.',
    'Checks: run Review, then Run document checks. The last recorded report is clean, and the same '
    'checks run again whenever you ask.',
]
BODY = {
    'tide': [
        'The boat came in on the evening tide, and Mara counted the gulls to keep from counting the '
        'waves. Nobody had told her the island would be this small.',
        'A man stood at the end of the jetty with a lantern in one hand and a rope in the other. He '
        'did not wave. He waited until the boat touched the stones and then threw her the rope.',
        'The first rule of the light is that the lamp never goes dark. The old report puts it simply: '
        'a light that fails is worse than a rock, because a rock does not promise anything.',
    ],
    'keeper': [
        'His name was Tobias, and he spoke as if each word cost him a coin. He led her up the spiral '
        'stairs without once looking back to see whether she followed.',
        'At the top the lamp sat inside its glass like a held breath. He showed her the wick, the '
        'oil and the brass key, and then he showed her the logbook, where every night was one line.',
        'She read the last entry twice. It said the sea was calm, the lamp was bright and the keeper '
        'was well, in a hand that shook a little on the final word.',
    ],
    'night': [
        'At midnight the lamp flickered, steadied and went out. The room fell into a darkness so '
        'complete that Mara heard her own pulse.',
        'She found the matches by touch, the way she had found everything on the island. The wick '
        'was dry. The oil was gone, and the spare can stood empty beside the door.',
        'Below her the sea kept its slow count. Somewhere out in it a boat was looking for the light, '
        'and Mara had one lamp, one match and perhaps an hour of night left to solve the problem.',
    ],
}
SOURCE = {
    'id': 'harrow1891',
    'type': 'report',
    'title': 'Annual Report of the Harrow Point Light Station',
    'author': [{'literal': 'Harrow Point Lighthouse Board'}],
    'issued': {'date-parts': [[1891]]},
    'note': 'A fictional source created for the ByAuthorX sample project.',
}


def _install_determinism():
    """Pin identities and timestamps; every ``new_id`` and engine timestamp then repeats exactly."""
    counter = itertools.count(1)
    ticks = itertools.count()

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (EPOCH + timedelta(minutes=next(ticks))).astimezone(tz or UTC)

    class Today(date):
        @classmethod
        def today(cls):
            return RETRIEVED

    uuid.uuid4 = lambda: uuid.UUID(int=next(counter))  # type: ignore[assignment]
    engine.datetime = Clock  # type: ignore[attr-defined]
    citations.date = Today  # type: ignore[attr-defined]


def _block(identity, text, kind='paragraph', style='body', **attrs):
    return {'id': identity, 'type': kind, 'text': text, 'style': style, 'marks': [], 'attrs': attrs}


def _heading(identity, text, level):
    style = f'heading{level}'
    return _block(identity, text, 'heading', style, level=level)


def build():
    _install_determinism()
    doc = new_document(TITLE)
    doc['id'], doc['revision'] = 'doc_sample_project', 'rev_sample_project_0'
    doc['metadata'].update(
        {
            'author': 'ByAuthorX sample (original text, MIT licence)',
            'language': 'en',
            'subject': 'A short manuscript that demonstrates styles, the binder, review and checks',
        }
    )
    doc['settings']['sample'] = {'generator': 'scripts/build_sample_project.py', 'version': 1}
    blocks = [
        _block('title', TITLE, style='title'),
        _block('subtitle', 'A sample project for ByAuthorX', style='subtitle'),
        _heading('ch1', CHAPTER_ONE, 1),
        _heading('s_tide', SCENES['tide'][0], 2),
        *[_block(f'tide_{i}', text) for i, text in enumerate(BODY['tide'], 1)],
        _heading('s_keeper', SCENES['keeper'][0], 2),
        *[_block(f'keeper_{i}', text) for i, text in enumerate(BODY['keeper'], 1)],
        _heading('ch2', CHAPTER_TWO, 1),
        _heading('s_night', SCENES['night'][0], 2),
        *[_block(f'night_{i}', text) for i, text in enumerate(BODY['night'], 1)],
        _heading('notes', 'About this sample', 1),
        *[_block(f'note_{i}', text) for i, text in enumerate(NOTES, 1)],
    ]
    doc['blocks'] = blocks
    validate_document(doc)

    def apply(operations, **envelope):
        nonlocal doc
        doc, _ = engine.apply(doc, {'operations': operations, **envelope})

    # Styles: one custom named style, applied to the sentence that quotes the report.
    apply(
        [
            {
                'type': 'define_style',
                'name': 'margin_note',
                'values': {'based_on': 'body', 'italic': True, 'left_margin': 24, 'font_size': 10},
            },
            {'type': 'apply_style', 'target': 'tide_3', 'style': 'margin_note'},
        ]
    )
    # Binder: chapters, scenes and a note, with workflow state and targets.
    apply([{'type': 'project_init', 'kinds': {'1': 'chapter', '2': 'scene'}}])
    item = project.item_id_for
    apply(
        [
            {'type': 'project_set_meta', 'target': item('notes'), 'values': {'kind': 'note'}},
            {'type': 'project_set_target', 'words': 12000},
            *[
                {
                    'type': 'project_set_meta',
                    'target': item(heading),
                    'values': {'synopsis': SCENES[key][1], 'label': 'sample'},
                }
                for key, heading in (('tide', 's_tide'), ('keeper', 's_keeper'), ('night', 's_night'))
            ],
            {'type': 'project_set_status', 'target': item('s_tide'), 'status': 'revised'},
            {'type': 'project_set_status', 'target': item('s_keeper'), 'status': 'draft'},
            {'type': 'project_set_target', 'target': item('s_tide'), 'words': 400},
            {'type': 'project_set_target', 'target': item('s_keeper'), 'words': 400},
            {'type': 'project_set_target', 'target': item('s_night'), 'words': 500},
        ]
    )
    # Citation: a source record and a quotation that points at it.
    record = import_sources(json.dumps([SOURCE]), 'csl-json')[0]
    text = doc['blocks'][next(i for i, b in enumerate(doc['blocks']) if b['id'] == 'tide_3')]['text']
    start = text.index('a light that fails')
    end = text.index('promise anything') + len('promise anything')
    apply(
        [
            {'type': 'add_reference', 'id': record['id'], 'values': record},
            {
                'type': 'cite',
                'target': 'tide_3',
                'start': start,
                'end': end,
                'source': record['id'],
                'kind': 'quotation',
                'locator': {'page': '14'},
            },
        ]
    )
    # Comment: a note to the writer that never alters the text.
    apply(
        [
            {
                'type': 'add_comment',
                'target': 'tide_1',
                'start': 0,
                'end': 30,
                'text': 'Try reading this opening aloud. Does the second sentence earn its place?',
                'author': 'Sample editor',
            }
        ]
    )
    # Proposal: a reviewer's suggested edit, left pending for the reader to accept or reject.
    apply(
        [
            {
                'type': 'propose',
                'values': {
                    'explanation': 'Make the keeper arrive on the page before he speaks.',
                    'author': 'Sample editor',
                    'operations': [
                        {
                            'type': 'replace_text',
                            'target': 'tide_2',
                            'match': 'A man stood',
                            'replacement': 'A tall man stood',
                        }
                    ],
                },
            }
        ]
    )
    # Names are not in the spelling dictionary; the project's own word list says so once.
    apply([{'type': 'set_settings', 'values': {'dictionary': ['ByAuthorX', 'Mara', 'Tobias', 'Harrow']}}])
    # Checks: record the default suites so the Review panel opens with a clean report.
    apply([{'type': 'record_checks', 'values': {}}])
    validate_document(doc)
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, default=OUTPUT)
    parser.add_argument('--check', action='store_true', help='Exit 1 when --out differs from a rebuild')
    args = parser.parse_args(argv)
    doc = build()
    if args.check:
        import tempfile

        from byax.model import canonical_json
        from byax.storage import load_document

        # Compare the documents, not the ZIP bytes: deflate output differs between zlib builds.
        with tempfile.TemporaryDirectory() as scratch:
            fresh = Path(scratch) / 'sample.byax'
            save_document(doc, fresh)
            if not args.out.is_file() or canonical_json(load_document(args.out)) != canonical_json(
                load_document(fresh)
            ):
                print(f'{args.out} is stale; run scripts/build_sample_project.py', file=sys.stderr)
                return 1
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_document(doc, args.out)
    print(args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
