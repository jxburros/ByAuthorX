"""Word review round trip: review package, three-way merge of returned copies, located losses.

Fixtures in ``tests/fixtures/docx-review`` come from ``scripts/docx_review_fixtures.py``: ``sent.byax`` is the
manuscript after "Send for review", ``word-returned.docx`` and ``word-unsupported.docx`` are the review copy
edited the way Word saves revisions (``tests/docx_word.py``), and the two ``libreoffice-*`` files were written
by LibreOffice 24.2 (one re-saved from the Word file, one edited by LibreOffice itself with change recording).
"""

import copy
import io
import json
import shutil
import threading
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from docx_word import JANE, PARAGRAPHS, TOM, WordEditor, q, sample_document, word_session
from lxml import etree

from byax import docx_review, engine, exchange
from byax.cli import execute, main
from byax.errors import ByaxError
from byax.mcp import handle
from byax.service import make_server
from byax.storage import load_document, save_document

FIXTURES = Path(__file__).parent / 'fixtures' / 'docx-review'
TOKEN = 'docx-review-token-with-32-characters!'


@pytest.fixture
def sent():
    return load_document(FIXTURES / 'sent.byax')


@pytest.fixture
def review_copy():
    return (FIXTURES / 'review-copy.docx').read_bytes()


def plan(doc, path):
    operations, report = docx_review.plan_review_merge(doc, path)
    proposals = [op['values'] for op in operations if op['type'] == 'propose']
    others = [op for op in operations if op['type'] not in ('propose', 'set_metadata')]
    return proposals, others, report


def change_list(proposals):
    return [
        (c['kind'], c['tracked'], c['authors'], c['dates'], c.get('base'))
        for p in proposals
        for c in p['review_source']['changes']
    ]


def accept_all(doc):
    for proposal in list(doc['review']['proposals']):
        if proposal['status'] == 'pending' and not proposal.get('conflict'):
            doc, _ = engine.apply(
                doc, {'operations': [{'type': 'accept_proposal', 'target': proposal['id']}]}
            )
    return doc


def table_of(doc):
    return next(block for block in doc['blocks'] if block['type'] == 'table')


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def body_of(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


# -- The review copy ---------------------------------------------------------------------------


def test_review_copy_is_deterministic_and_carries_identity_tracking_and_threads():
    doc = sample_document()
    first, operations, info = docx_review.prepare_review_package(doc, created='2026-10-01T08:00:00Z')
    again, _, _ = docx_review.prepare_review_package(doc, created='2026-10-01T08:00:00Z')
    assert first == again
    parts = body_of(first)
    custom = etree.fromstring(parts['docProps/custom.xml'])
    properties = {p.get('name'): p[0].text for p in custom}
    assert properties['ByAuthorXReviewId'] == info['review_id'] == docx_review.review_identifier(doc)
    assert (
        properties['ByAuthorXDocumentId'] == doc['id'] and properties['ByAuthorXRevision'] == doc['revision']
    )
    settings = etree.fromstring(parts['word/settings.xml'])
    names = [etree.QName(child).localname for child in settings]
    assert names.index('trackRevisions') < names.index('documentProtection') < names.index('defaultTabStop')
    protection = settings.find(q('w:documentProtection'))
    assert protection.get(q('w:edit')) == 'trackedChanges' and protection.get(q('w:enforcement')) == '1'
    document = etree.fromstring(parts['word/document.xml'])
    paragraphs = document.find(q('w:body')).findall(q('w:p'))
    bookmarks = [p.find(q('w:bookmarkStart')).get(q('w:name')) for p in paragraphs]
    text_blocks = [b['id'] for b in doc['blocks'] if b['type'] != 'table']
    assert bookmarks == [docx_review.unit_bookmark(identity) for identity in text_blocks]
    para_ids = [p.get(q('w14:paraId')) for p in document.iter(q('w:p'))]
    assert len(set(para_ids)) == len(para_ids) and all(int(v, 16) < 0x80000000 for v in para_ids)
    # The writer's comment keeps its exact range and its reply is a Word reply.
    start = document.find('.//' + q('w:commentRangeStart'))
    following = ''.join(t.text for t in start.getparent().iterfind('.//' + q('w:t')))
    assert 'the rocks' in following
    extended = etree.fromstring(parts['word/commentsExtended.xml'])
    entries = extended.findall(q('w15:commentEx'))
    assert entries[1].get(q('w15:paraIdParent')) == entries[0].get(q('w15:paraId'))
    ids = etree.fromstring(parts['word/commentsIds.xml'])
    thread = doc['review']['comments'][0]
    assert ids[0].get(q('w16cid:durableId')) == docx_review.comment_durable_id(thread['id'])
    comment = etree.fromstring(parts['word/comments.xml']).find(q('w:comment'))
    assert q('w:date') not in comment.attrib  # no clock in the output
    assert operations[0] == {
        'type': 'pin_merge_base',
        'peer': 'review:' + info['review_id'],
        'revision': doc['revision'],
    }


@pytest.mark.parametrize(('restrict', 'edit'), [('comments', 'comments'), ('none', None)])
def test_restriction_choices(restrict, edit):
    data, _, _ = docx_review.prepare_review_package(sample_document(), restrict=restrict)
    settings = etree.fromstring(body_of(data)['word/settings.xml'])
    assert settings.find(q('w:trackRevisions')) is not None
    protection = settings.find(q('w:documentProtection'))
    assert (protection.get(q('w:edit')) if protection is not None else None) == edit
    with pytest.raises(ByaxError, match='restrict must be'):
        docx_review.prepare_review_package(sample_document(), restrict='forms')


def test_create_pins_the_snapshot_records_the_package_and_reports_losses(tmp_path):
    doc = sample_document()
    engine_doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'insert_paragraph', 'text': 'Note to self', 'after': 'p_7', 'id': 'p_hidden'}
            ]
        },
    )
    engine_doc, _ = engine.apply(
        engine_doc,
        {'operations': [{'type': 'set_attributes', 'target': 'p_hidden', 'values': {'excluded': True}}]},
    )
    out = tmp_path / 'copy.docx'
    dry, preview, data = docx_review.create_review_package(engine_doc, out, dry_run=True)
    assert dry is engine_doc and data is None and not out.exists() and preview['dry_run']
    updated, result, data = docx_review.create_review_package(engine_doc, out, reviewer='Jane Editor')
    record = result['review_package']
    assert updated['history']['peer_bases'][record['peer']] == engine_doc['revision'] == record['revision']
    assert updated['metadata']['review_packages'][record['id']]['sha256'] == record['sha256']
    assert {loss['code'] for loss in result['export_report']['losses']} >= {'review_block_not_sent'}
    assert any(loss['target'] == 'p_hidden' for loss in result['export_report']['losses'])
    assert not out.exists()  # the caller saves the document first, then publishes
    docx_review.publish_review_copy(data, out)
    assert out.read_bytes() == data
    listed = docx_review.review_packages(updated)['review_packages'][record['id']]
    assert listed['pinned'] is True and listed['status'] == 'sent'
    with pytest.raises(ByaxError, match='must be a .docx'):
        docx_review.create_review_package(engine_doc, tmp_path / 'copy.odt')


def test_ordinary_docx_export_and_import_keep_comment_ranges_threads_and_dates(tmp_path):
    doc = sample_document()
    thread = doc['review']['comments'][0]
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'add_comment',
                    'target': 'p_3',
                    'start': 27,
                    'end': 39,
                    'text': 'Dated',
                    'author': 'Ed',
                    'id': 'comment_dated',
                    'date': '2026-09-30T10:00:00Z',
                }
            ]
        },
    )
    exchange.export_document(doc, tmp_path / 'plain.docx')
    imported, _report = exchange.import_document(tmp_path / 'plain.docx')
    comments = {c['text']: c for c in imported['review']['comments']}
    first = comments['Too gentle?']
    block = next(b for b in imported['blocks'] if b['id'] == first['target'])
    assert block['text'][first['start'] : first['end']] == 'the rocks'
    assert [r['text'] for r in first['replies']] == ['Or keep it?'] and first['replies'][0][
        'author'
    ] == 'A. Writer'
    dated = comments['Dated']
    assert dated['date'] == '2026-09-30T10:00:00Z'
    dated_block = next(b for b in imported['blocks'] if b['id'] == dated['target'])
    assert dated_block['text'][dated['start'] : dated['end']] == 'eleven years'
    assert thread['id'] != first['id']


# -- Merging a copy returned from Word ---------------------------------------------------------


def test_word_round_trip_maps_every_revision_to_a_proposal_with_exact_ranges_and_attribution(sent):
    proposals, _others, report = plan(sent, FIXTURES / 'word-returned.docx')
    table = table_of(sent)
    cell = table['rows'][1]['cells'][1]['id']
    heading = sent['blocks'][0]['id']
    jane, tom = ['Jane Editor'], ['Tom Copyeditor']
    jd, td = [JANE['date']], [TOM['date']]
    assert change_list(proposals) == [
        ('replacement', True, jane, jd, {'target': 'p_1', 'start': 33, 'end': 37, 'text': 'dusk'}),
        (
            'deletion',
            True,
            tom,
            td,
            {'target': 'p_1', 'start': 37, 'end': 63, 'text': ', counting each step aloud'},
        ),
        ('insertion', True, jane, jd, {'target': 'p_2', 'start': 11, 'end': 11, 'text': ''}),
        ('paragraph_insertion', True, jane, jd, {'target': 'p_3', 'start': 65, 'end': 65, 'text': ''}),
        ('split', True, jane, jd, {'target': 'p_6', 'start': 29, 'end': 29, 'text': ''}),
        (
            'paragraph_deletion',
            True,
            jane,
            jd,
            {'target': 'p_7', 'start': 0, 'end': 0, 'text': PARAGRAPHS[6], 'through': None},
        ),
        ('formatting', True, tom, td, {'target': 'p_4', 'start': 0, 'end': 7, 'text': 'Tonight'}),
        ('table_cell', True, tom, td, {'target': cell, 'text': 'Lamp cleaned'}),
        ('move', True, jane, jd, {'target': 'p_5', 'move': 'move463915822'}),
        ('paragraph_format', True, jane, jd, {'target': heading}),
    ]
    operations = [p['operations'] for p in proposals]
    assert operations[0] == [
        {
            'type': 'replace_text',
            'target': 'p_1',
            'match': 'dusk',
            'replacement': 'nightfall',
            'occurrence': 1,
        }
    ]
    assert operations[1] == [
        {
            'type': 'delete_text',
            'target': 'p_1',
            'start': 37,
            'end': 63,
            'expected_text': ', counting each step aloud',
        }
    ]
    assert operations[2] == [{'type': 'insert_text', 'target': 'p_2', 'offset': 11, 'text': 'grey '}]
    assert (
        operations[3][0]['after'] == 'p_3'
        and operations[3][0]['block']['text'] == 'Nobody came to relieve her.'
    )
    assert operations[4][0] == {
        'type': 'split_block',
        'target': 'p_6',
        'offset': 29,
        'id': operations[4][0]['id'],
    }
    assert operations[5] == [{'type': 'delete_block', 'target': 'p_7'}]
    assert operations[6] == [
        {'type': 'format_text', 'target': 'p_4', 'start': 0, 'end': 7, 'marks': {'bold': True}}
    ]
    assert operations[7] == [
        {
            'type': 'update_cell',
            'target': cell,
            'text': 'Lamp cleaned and trimmed',
            'expected_text': 'Lamp cleaned',
        }
    ]
    assert operations[8] == [{'type': 'move_block', 'target': 'p_5', 'before': 'p_4'}]
    assert operations[9] == [{'type': 'set_attributes', 'target': heading, 'values': {'alignment': 'center'}}]
    assert all(
        p['author'] in ('Jane Editor', 'Tom Copyeditor') and p['kind'] == 'docx_review' for p in proposals
    )
    assert report['conflicts'] == [] and report['losses'] == []
    assert report['review_merge']['matched_by'] == 'custom_property'


def test_word_comments_replies_and_done_state_become_threads_on_the_right_ranges(sent):
    _, others, report = plan(sent, FIXTURES / 'word-returned.docx')
    writer_thread = sent['review']['comments'][0]
    added = [op for op in others if op['type'] == 'add_comment']
    replies = [op for op in others if op['type'] == 'reply_comment']
    assert [(op['target'], op['start'], op['end'], op['text'], op['author'], op['date']) for op in added] == [
        ('p_3', 27, 39, 'Check against chapter two.', 'Jane Editor', JANE['date']),
        ('p_4', 42, 45, 'Lovely.', 'Tom Copyeditor', TOM['date']),
    ]
    assert PARAGRAPHS[2][27:39] == 'eleven years' and PARAGRAPHS[3][42:45] == 'fog'
    assert {'type': 'resolve_comment', 'target': added[1]['id']} in others
    assert [(op['target'], op['text'], op['author']) for op in replies] == [
        (writer_thread['id'], 'Agreed, I sharpened it.', 'Jane Editor'),
        (added[0]['id'], 'Chapter two says twelve.', 'Tom Copyeditor'),
    ]
    # The writer's own comment and reply came back and are recognized, not duplicated.
    assert report['comments'] == {'added': 2, 'replies': 2, 'resolved': 1, 'existing': 1, 'marked_done': []}


def test_merging_adds_pending_proposals_only_and_accepting_them_gives_the_editors_text(sent, tmp_path):
    merged, result = docx_review.merge_reviewed_copy(sent, FIXTURES / 'word-returned.docx')
    assert [b['text'] for b in merged['blocks']] == [b['text'] for b in sent['blocks']]
    assert len(result['proposal_ids']) == 10
    assert all(p['status'] == 'pending' for p in merged['review']['proposals'])
    record = merged['metadata']['review_packages'][result['review_merge']['review_id']]
    assert record['status'] == 'merged' and record['returns'][0]['proposals'] == 10
    accepted = accept_all(merged)
    texts = [b.get('text') for b in accepted['blocks'] if b['type'] != 'table']
    assert texts == [
        'Chapter One',
        'The keeper climbed the stairs at nightfall.',
        'Below, the grey sea worked at the rocks with a patience she envied.',
        PARAGRAPHS[2],
        'Nobody came to relieve her.',
        PARAGRAPHS[4],
        PARAGRAPHS[3],
        'She wrote the time in the log',
        ' and went back up.',
    ]
    assert accepted['blocks'][0]['attrs']['alignment'] == 'center'
    assert table_of(accepted)['rows'][1]['cells'][1]['text'] == 'Lamp cleaned and trimmed'
    bold = [
        m for m in next(b for b in accepted['blocks'] if b['id'] == 'p_4')['marks'] if m['type'] == 'bold'
    ]
    assert bold == [{'start': 0, 'end': 7, 'type': 'bold', 'value': True}]
    authors = {c.get('author') for c in accepted['review']['tracked_changes']}
    assert authors <= {'Jane Editor', 'Tom Copyeditor'}
    save_document(accepted, tmp_path / 'accepted.byax')
    assert load_document(tmp_path / 'accepted.byax')['blocks'] == accepted['blocks']


def test_proposals_can_be_accepted_in_any_order_on_the_same_paragraph(sent):
    merged, _result = docx_review.merge_reviewed_copy(sent, FIXTURES / 'word-returned.docx')
    on_p1 = [p['id'] for p in merged['review']['proposals'] if p['edits'][0].get('target') == 'p_1']
    assert len(on_p1) == 2
    for proposal in reversed(on_p1):
        merged, _ = engine.apply(merged, {'operations': [{'type': 'accept_proposal', 'target': proposal}]})
    assert (
        next(b for b in merged['blocks'] if b['id'] == 'p_1')['text']
        == 'The keeper climbed the stairs at nightfall.'
    )


def test_writer_edits_after_sending_are_never_overwritten(sent, tmp_path):
    ours, _ = engine.apply(
        sent,
        {
            'operations': [
                {'type': 'replace_text', 'target': 'p_1', 'match': 'keeper', 'replacement': 'old keeper'},
                {'type': 'replace_text', 'target': 'p_1', 'match': 'dusk', 'replacement': 'evening'},
                {'type': 'replace_text', 'target': 'p_2', 'match': 'the sea', 'replacement': 'the dark sea'},
                {'type': 'delete_block', 'target': 'p_7'},
                {
                    'type': 'insert_paragraph',
                    'text': 'A new scene by the writer.',
                    'after': 'p_3',
                    'id': 'p_new',
                },
            ]
        },
    )
    proposals, _, report = plan(ours, FIXTURES / 'word-returned.docx')
    first = {c['kind']: p for p in proposals for c in p['review_source']['changes']}
    # Both rewrote "dusk": a flagged conflict whose edit replaces only that region, and only if accepted.
    dusk = first['replacement']
    assert dusk['conflict'] == {
        'reason': 'You changed this passage after sending the review copy; accepting replaces your newer text here.',
        'base': 'dusk',
        'ours': 'evening',
        'theirs': 'nightfall',
    }
    assert dusk['operations'] == [
        {
            'type': 'replace_text',
            'target': 'p_1',
            'match': 'evening',
            'replacement': 'nightfall',
            'occurrence': 1,
        }
    ]
    assert dusk['explanation'].startswith('Conflict:')
    # The other ranges move with the writer's edits on the same paragraphs.
    assert first['deletion']['operations'] == [
        {
            'type': 'delete_text',
            'target': 'p_1',
            'start': 44,
            'end': 70,
            'expected_text': ', counting each step aloud',
        }
    ]
    assert first['insertion']['operations'] == [
        {'type': 'insert_text', 'target': 'p_2', 'offset': 16, 'text': 'grey '}
    ]
    # The writer already removed the paragraph the editor deleted; nothing is proposed for it.
    assert 'paragraph_deletion' not in first and report['conflicts'] == []
    merged, _ = docx_review.merge_reviewed_copy(ours, FIXTURES / 'word-returned.docx')
    accepted = accept_all(merged)
    texts = {b['id']: b.get('text') for b in accepted['blocks']}
    assert texts['p_1'] == 'The old keeper climbed the stairs at evening.'
    assert texts['p_2'] == 'Below, the dark grey sea worked at the rocks with a patience she envied.'
    assert texts['p_new'] == 'A new scene by the writer.' and 'p_7' not in texts
    pending = [p for p in accepted['review']['proposals'] if p['status'] == 'pending']
    assert [p['conflict']['theirs'] for p in pending] == ['nightfall']
    resolved, _ = engine.apply(
        accepted, {'operations': [{'type': 'accept_proposal', 'target': pending[0]['id']}]}
    )
    assert next(b for b in resolved['blocks'] if b['id'] == 'p_1')['text'] == (
        'The old keeper climbed the stairs at nightfall.'
    )


def test_a_returned_copy_is_merged_once(sent, tmp_path):
    merged, _first = docx_review.merge_reviewed_copy(sent, FIXTURES / 'word-returned.docx')
    again, second = docx_review.merge_reviewed_copy(merged, FIXTURES / 'word-returned.docx')
    assert again is merged and second['review_merge']['already_merged'] and second['changed_ids'] == []
    dry, preview = docx_review.merge_reviewed_copy(sent, FIXTURES / 'word-returned.docx', dry_run=True)
    assert dry is sent and preview['execution'] == 'dry_run' and preview['proposals'] == 10


def test_untracked_edits_are_diffed_against_the_snapshot_and_proposed(sent, review_copy, tmp_path):
    editor = WordEditor(review_copy, **JANE)
    p3 = PARAGRAPHS[2]
    editor.untracked(3, p3.index('eleven'), p3.index('eleven') + len('eleven'), 'twelve')
    paragraphs = editor.paragraphs()
    paragraphs[7].getparent().remove(paragraphs[7])  # "The end of the watch." deleted with tracking off
    added = copy.deepcopy(paragraphs[1])
    for node in list(added):
        if node.tag != q('w:pPr'):
            added.remove(node)
    added.append(editor._new_run('An untracked new paragraph.'))
    paragraphs[1].addnext(added)
    returned = write(tmp_path, 'untracked.docx', editor.save(last_modified_by='Jane Editor'))
    proposals, _, _report = plan(sent, returned)
    assert change_list(proposals) == [
        (
            'replacement',
            False,
            ['Jane Editor'],
            [],
            {'target': 'p_3', 'start': 27, 'end': 33, 'text': 'eleven'},
        ),
        (
            'paragraph_deletion',
            False,
            ['Jane Editor'],
            [],
            {'target': 'p_7', 'start': 0, 'end': 21, 'text': PARAGRAPHS[6]},
        ),
        ('paragraph_insertion', False, ['Jane Editor'], [], None),
    ]
    assert proposals[2]['operations'][0]['after'] == 'p_1'
    assert all('without Track Changes' in p['explanation'] for p in proposals)


def test_a_moved_paragraph_keeps_its_identity_even_when_word_drops_its_bookmark(sent, review_copy, tmp_path):
    editor = WordEditor(review_copy, **JANE)
    editor.move_paragraph(1, 4)  # "The keeper…" to before "Tonight…"
    returned = write(tmp_path, 'moved.docx', editor.save())
    proposals, _, _ = plan(sent, returned)
    assert [p['operations'] for p in proposals] == [[{'type': 'move_block', 'target': 'p_1', 'after': 'p_3'}]]
    assert proposals[0]['review_source']['changes'][0]['tracked'] is True


# -- Third-party files written by LibreOffice ---------------------------------------------------


def test_libreoffice_resaved_copy_merges_to_the_same_proposals(sent):
    word, _, _ = plan(sent, FIXTURES / 'word-returned.docx')
    office, others, report = plan(sent, FIXTURES / 'libreoffice-converted.docx')
    expected = [(kind, base) for kind, _, _, _, base in change_list(word)]
    # LibreOffice writes a deleted paragraph as deleted text in a paragraph that stays.
    expected[5] = ('deletion', {'target': 'p_7', 'start': 0, 'end': 21, 'text': PARAGRAPHS[6]})
    assert [(kind, base) for kind, _, _, _, base in change_list(office)] == expected
    assert [op['text'] for op in others if op['type'] in ('add_comment', 'reply_comment')] == [
        'Check against chapter two.',
        'Lovely.',
        'Agreed, I sharpened it.',
        'Chapter two says twelve.',
    ]
    # LibreOffice adds paragraph properties to the heading's tracked change; that part is reported.
    assert [loss['code'] for loss in report['losses']] == ['docx_review_paragraph_format']


def test_libreoffice_edited_copy_gives_exact_proposals_and_its_comment(sent):
    proposals, others, report = plan(sent, FIXTURES / 'libreoffice-edited.docx')
    changes = change_list(proposals)
    assert [(kind, authors, base) for kind, _, authors, _, base in changes] == [
        ('replacement', ['Lee Reviewer'], {'target': 'p_1', 'start': 11, 'end': 18, 'text': 'climbed'}),
        ('insertion', ['Lee Reviewer'], {'target': 'p_2', 'start': 11, 'end': 11, 'text': ''}),
        (
            'deletion',
            ['Lee Reviewer'],
            {'target': 'p_2', 'start': 34, 'end': 61, 'text': ' with a patience she envied'},
        ),
    ]
    assert all(dates and dates[0][:4] == '2026' for _, _, _, dates, _ in changes)
    assert [
        op['operations'][0].get('text', op['operations'][0].get('replacement')) for op in proposals[:2]
    ] == [
        'hauled herself up',
        'restless ',
    ]
    comment = next(op for op in others if op['type'] == 'add_comment')
    assert (comment['target'], comment['start'], comment['end'], comment['author']) == (
        'p_3',
        27,
        39,
        'Lee Reviewer',
    )
    assert 'date' not in comment  # LibreOffice wrote an invalid placeholder date
    assert report['losses'] == []


# -- Located losses ------------------------------------------------------------------------------


def test_every_unsupported_construct_is_a_located_loss(sent):
    proposals, _, report = plan(sent, FIXTURES / 'word-unsupported.docx')
    table = table_of(sent)['id']
    located = sorted((loss['code'], loss['target']) for loss in report['losses'])
    assert located == sorted(
        [
            ('docx_review_field', 'p_1'),
            ('docx_review_smartart', 'p_2'),
            ('docx_review_content_control', 'p_3'),
            ('docx_review_ink', 'p_4'),
            ('docx_review_equation', 'p_5'),
            ('docx_review_revision_unsupported', table),
        ]
    )
    assert all(loss['location']['part'] == 'word/document.xml' for loss in report['losses'])
    field = next(loss for loss in report['losses'] if loss['code'] == 'docx_review_field')
    assert field['location']['instruction'] == 'PAGE'
    # Visible text the editor added with them is still proposed, never applied silently.
    assert [p['operations'][0]['text'] for p in proposals] == ['3', ' [client]']


def test_header_footnote_and_macro_changes_are_reported(sent, review_copy, tmp_path):
    parts = body_of(review_copy)
    w = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    parts['word/header1.xml'] = (
        f'<w:hdr xmlns:w="{w}"><w:p><w:ins w:id="1" w:author="Jane"><w:r><w:t>Draft</w:t></w:r></w:ins></w:p></w:hdr>'
    ).encode()
    parts['word/vbaProject.bin'] = b'\x00' * 16
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    returned = write(tmp_path, 'regions.docx', output.getvalue())
    _, _, report = plan(sent, returned)
    assert sorted((loss['code'], loss['location']['part']) for loss in report['losses']) == [
        ('docx_review_macros', 'word/vbaProject.bin'),
        ('docx_review_region_changes', 'word/header1.xml'),
    ]


# -- Matching and refusals -------------------------------------------------------------------------


def test_the_returned_file_must_match_this_documents_review_package(sent, review_copy, tmp_path):
    other = sample_document()
    with pytest.raises(ByaxError, match='different document') as caught:
        docx_review.plan_review_merge(other, FIXTURES / 'word-returned.docx')
    assert caught.value.code == 'conflict'
    with pytest.raises(ByaxError, match='different review package'):
        docx_review.plan_review_merge(sent, FIXTURES / 'word-returned.docx', review_id='rv_0000000000000000')
    editor = WordEditor(review_copy, **JANE)
    editor.remove_custom_properties()
    editor.insert(1, 0, 'So ')
    anonymous = write(tmp_path, 'anonymous.docx', editor.save())
    with pytest.raises(ByaxError, match='no ByAuthorX review identifier') as caught:
        docx_review.plan_review_merge(sent, anonymous)
    package = next(iter(sent['metadata']['review_packages']))
    assert caught.value.details['candidates'] == [package]
    _operations, report = docx_review.plan_review_merge(sent, anonymous, review_id=package)
    assert report['review_merge']['matched_by'] == 'argument' and report['proposals'] == 1
    unpinned, _ = engine.apply(
        sent, {'operations': [{'type': 'unpin_merge_base', 'peer': 'review:' + package}]}
    )
    with pytest.raises(ByaxError, match='no longer pinned'):
        docx_review.plan_review_merge(unpinned, FIXTURES / 'word-returned.docx')


@pytest.mark.parametrize(
    ('mutate', 'code'),
    [
        (lambda parts: parts.update({'../evil.xml': b'<x/>'}), 'unsafe_archive'),
        (
            lambda parts: parts.update(
                {
                    'word/document.xml': b'<!DOCTYPE x [<!ENTITY a "aaaa">]><w:document xmlns:w="w">&a;</w:document>'
                }
            ),
            'unsafe_xml',
        ),
        (lambda parts: parts.update({'word/document.xml': b'<w:document'}), 'invalid_input'),
        (lambda parts: parts.pop('word/document.xml'), 'invalid_input'),
        (
            lambda parts: parts.update({'word/bomb.xml': b'<a>' + b' ' * (40 * 1024 * 1024) + b'</a>'}),
            'resource_limit',
        ),
    ],
)
def test_hostile_or_malformed_returned_files_are_refused_without_changes(
    sent, review_copy, tmp_path, mutate, code
):
    parts = body_of(review_copy)
    mutate(parts)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    path = write(tmp_path, 'hostile.docx', output.getvalue())
    before = copy.deepcopy(sent)
    with pytest.raises(ByaxError) as caught:
        docx_review.merge_reviewed_copy(sent, path)
    assert caught.value.code == code and sent == before
    write(tmp_path, 'not-a-zip.docx', b'PK\x03\x04 truncated')
    with pytest.raises(ByaxError) as caught:
        docx_review.merge_reviewed_copy(sent, tmp_path / 'not-a-zip.docx')
    assert caught.value.code == 'invalid_input'


def test_two_proposals_on_one_paragraph_accept_in_either_order():
    doc = sample_document()
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'propose',
                    'values': {
                        'operations': [{'type': 'insert_text', 'target': 'p_1', 'offset': 4, 'text': 'old '}]
                    },
                },
                {
                    'type': 'propose',
                    'values': {
                        'operations': [{'type': 'delete_text', 'target': 'p_1', 'start': 37, 'end': 63}]
                    },
                },
            ]
        },
    )
    early, late = (p['id'] for p in doc['review']['proposals'])
    for order in ((early, late), (late, early)):
        current = doc
        for proposal in order:
            current, _ = engine.apply(
                current, {'operations': [{'type': 'accept_proposal', 'target': proposal}]}
            )
        assert (
            next(b for b in current['blocks'] if b['id'] == 'p_1')['text']
            == 'The old keeper climbed the stairs at dusk.'
        )


def test_comment_operations_accept_stable_identities_and_dates():
    doc = sample_document()
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {
                    'type': 'add_comment',
                    'target': 'p_1',
                    'text': 'x',
                    'id': 'comment_fixed',
                    'date': '2026-01-01',
                    'source': 'docx_review',
                },
                {
                    'type': 'reply_comment',
                    'target': 'comment_fixed',
                    'text': 'y',
                    'id': 'comment_reply',
                    'date': '2026-01-02',
                },
            ]
        },
    )
    added = next(c for c in doc['review']['comments'] if c['id'] == 'comment_fixed')
    assert added['date'] == '2026-01-01' and added['replies'][0]['id'] == 'comment_reply'
    for operation, message in (
        ({'type': 'add_comment', 'target': 'p_1', 'text': 'x', 'id': 'comment_fixed'}, 'already exists'),
        ({'type': 'add_comment', 'target': 'p_1', 'text': 'x', 'id': 'note_1'}, 'start with comment_'),
        ({'type': 'add_comment', 'target': 'p_1', 'text': 'x', 'date': 5}, 'Expected a string'),
    ):
        with pytest.raises(ByaxError, match=message):
            engine.apply(doc, {'operations': [operation]})


# -- CLI, MCP and REST ---------------------------------------------------------------------------


@pytest.fixture
def rest(tmp_path):
    server = make_server([tmp_path], token=TOKEN, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/v1/execute'

    def call(command, arguments):
        request = Request(
            url,
            data=json.dumps({'command': command, 'arguments': arguments}).encode(),
            method='POST',
            headers={'Authorization': 'Bearer ' + TOKEN},
        )
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    yield call
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def mcp(command, arguments):
    reply = handle(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': 'byax_' + command, 'arguments': arguments},
        }
    )['result']
    return reply['structuredContent'], reply['isError']


def test_cli_sends_a_package_and_merges_the_returned_copy(tmp_path, capsys):
    document = tmp_path / 'Lighthouse.byax'
    save_document(sample_document(), document)
    out = tmp_path / 'Lighthouse (review).docx'
    assert (
        main(['review-package', str(document), '--out', str(out), '--reviewer', 'Jane Editor', '--json']) == 0
    )
    sent_result = json.loads(capsys.readouterr().out)
    package = sent_result['review_package']['id']
    saved = load_document(document)
    assert saved['history']['peer_bases']['review:' + package] == sent_result['review_package']['revision']
    assert out.is_file() and sent_result['outputs'] == [str(out.resolve())]
    with pytest.raises(ByaxError, match='already exists'):
        execute('review-package', {'path': str(document), 'out': str(out)})
    listed = execute('review-package', {'path': str(document), 'action': 'list'})
    assert listed['review_packages'][package]['pinned'] is True
    returned = write(tmp_path, 'returned.docx', word_session(out.read_bytes()))
    assert main(['merge-review', str(document), '--reviewed-path', str(returned), '--dry-run']) == 0
    assert json.loads(capsys.readouterr().out)['proposals'] == 10
    assert load_document(document)['revision'] == saved['revision']
    assert main(['merge-review', str(document), '--reviewed-path', str(returned)]) == 0
    merged = json.loads(capsys.readouterr().out)
    assert merged['proposals'] == 10 and len(merged['proposal_ids']) == 10
    assert (
        len([p for p in load_document(document)['review']['proposals'] if p['kind'] == 'docx_review']) == 10
    )
    again = execute('merge-review', {'path': str(document), 'reviewed_path': str(returned)})
    assert again['review_merge']['already_merged'] is True
    with pytest.raises(ByaxError, match='requires --out'):
        execute('review-package', {'path': str(document)})
    with pytest.raises(ByaxError, match='Unknown review-package action'):
        execute('review-package', {'path': str(document), 'action': 'delete'})
    with pytest.raises(ByaxError, match='Missing required argument: reviewed_path'):
        execute('merge-review', {'path': str(document)})


def test_mcp_and_rest_give_the_same_merge_and_scope_the_reviewed_path(tmp_path, rest):
    for name in ('a', 'b'):
        shutil.copyfile(FIXTURES / 'sent.byax', tmp_path / f'{name}.byax')
    returned = tmp_path / 'returned.docx'
    shutil.copyfile(FIXTURES / 'word-returned.docx', returned)
    arguments = {'reviewed_path': str(returned), 'dry_run': True}
    structured, is_error = mcp('merge_review', {'path': str(tmp_path / 'a.byax'), **arguments})
    status, body = rest('merge-review', {'path': str(tmp_path / 'b.byax'), **arguments})
    assert not is_error and status == 200
    assert (
        structured['proposal_summaries'] == body['proposal_summaries']
        and len(body['proposal_summaries']) == 10
    )
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.mkdir()
    shutil.copyfile(returned, outside / 'returned.docx')
    status, body = rest(
        'merge-review', {'path': str(tmp_path / 'a.byax'), 'reviewed_path': str(outside / 'returned.docx')}
    )
    assert status == 400 and 'outside authorized' in body['error']['message']
    status, body = rest(
        'review-package', {'path': str(tmp_path / 'a.byax'), 'out': str(outside / 'copy.docx')}
    )
    assert status == 400 and 'outside authorized' in body['error']['message']
