"""Declared Markdown dialect, localized source edits, LF/CRLF preservation and round-trip fixtures."""

import difflib
from pathlib import Path

import pytest

from byax.errors import ByaxError
from byax.exchange import MARKDOWN_DIALECT, export_document, import_document

FIXTURE = Path(__file__).parent / 'fixtures' / 'markdown' / 'nested.md'


def _load(tmp_path, ending='\n'):
    source = FIXTURE.read_text(encoding='utf-8').replace('\n', ending)
    path = tmp_path / 'source.md'
    path.write_bytes(source.encode('utf-8'))
    doc, report = import_document(path)
    return doc, report, source


def _export(doc, tmp_path, name='out.md', **profile):
    path = tmp_path / name
    report = export_document(doc, path, profile=profile or None)
    return path.read_bytes().decode('utf-8'), report


def _changed_lines(before, after):
    return [
        line
        for line in difflib.ndiff(before.splitlines(keepends=True), after.splitlines(keepends=True))
        if line.startswith(('- ', '+ '))
    ]


def _block(doc, text):
    return next(b for b in doc['blocks'] if b.get('text', '').startswith(text))


def test_declared_dialect_covers_required_constructs(tmp_path):
    doc, report, _ = _load(tmp_path)
    assert report['dialect']['name'] == MARKDOWN_DIALECT['name'] == doc['metadata']['import']['dialect']
    assert set(MARKDOWN_DIALECT['constructs']) >= {
        'headings',
        'emphasis',
        'lists',
        'links',
        'images',
        'quotations',
        'code',
        'thematic_breaks',
        'tables',
    }
    kinds = [(b['type'], b['style']) for b in doc['blocks']]
    assert ('heading', 'body') in kinds and ('table', 'body') in kinds
    intro = _block(doc, 'Intro paragraph')
    assert {m['type'] for m in intro['marks']} == {'italic', 'bold', 'code', 'link'}
    assert _block(doc, 'Nested child')['attrs']['level'] == 2
    assert _block(doc, 'Ordered one')['attrs']['list'] == 'ordered'
    assert _block(doc, 'quoted list item')['attrs']['quote_depth'] == 1
    assert _block(doc, 'def keep')['attrs']['code_language'] == 'python'
    assert _block(doc, 'indented code block')['style'] == 'code'
    assert any(b['attrs'].get('thematic_break') for b in doc['blocks'])
    assert _block(doc, 'Diagram')['attrs']['external_images'][0]['src'] == 'images/diagram.png'
    table = next(b for b in doc['blocks'] if b['type'] == 'table')
    assert [c['text'] for c in table['rows'][2]['cells']] == ['β', '文']


@pytest.mark.parametrize('ending', ['\n', '\r\n'])
def test_edit_free_view_switching_is_byte_identical(tmp_path, ending):
    doc, _report, source = _load(tmp_path, ending)
    text, report = _export(doc, tmp_path)
    assert text == source
    assert report['markdown']['mode'] == 'verbatim'
    assert not any(loss['code'] == 'markdown_normalized' for loss in report['losses'])
    again, _ = import_document(tmp_path / 'out.md')
    assert [b.get('text') for b in again['blocks']] == [b.get('text') for b in doc['blocks']]


@pytest.mark.parametrize('ending', ['\n', '\r\n'])
def test_visual_edits_localize_source_changes_in_nested_constructs(tmp_path, ending):
    doc, _report, source = _load(tmp_path, ending)
    _block(doc, 'Nested child')['text'] = 'Nested child edited'
    quoted = _block(doc, 'Quoted paragraph')
    quoted['text'] = 'Quoted paragraph\nnow rewritten.'
    _block(doc, 'Project Notes')['text'] = 'Project Journal'
    _block(doc, 'def keep')['text'] = 'def keep():\n    return "still tildes"'
    table = next(b for b in doc['blocks'] if b['type'] == 'table')
    table['rows'][1]['cells'][1]['text'] = '2'
    text, report = _export(doc, tmp_path)
    assert report['markdown']['mode'] == 'localized'
    assert ending in text and (ending == '\r\n') == ('\r\n' in text)
    changes = _changed_lines(source, text)
    removed = sorted(line[2:].rstrip('\r\n') for line in changes if line.startswith('- '))
    added = sorted(line[2:].rstrip('\r\n') for line in changes if line.startswith('+ '))
    assert removed == sorted(
        ['Project Notes', '  - Nested child', '> spanning two lines.', '    return "tildes"', '| alpha | 1 |']
    )
    assert sorted(added) == sorted(
        [
            'Project Journal',
            '  - Nested child edited',
            '> now rewritten.',
            '    return "still tildes"',
            '| alpha | 2 |',
        ]
    )
    assert '=============' in text and '~~~python' in text
    reimported, _ = import_document(tmp_path / 'out.md')
    assert _block(reimported, 'Nested child edited')['attrs']['level'] == 2
    assert _block(reimported, 'Project Journal')['type'] == 'heading'


def test_inserted_and_deleted_blocks_touch_only_their_lines(tmp_path):
    doc, _report, source = _load(tmp_path)
    blocks = doc['blocks']
    intro = blocks.index(_block(doc, 'Intro paragraph'))
    new = {
        'id': 'p_new',
        'type': 'paragraph',
        'text': '# not a heading',
        'style': 'body',
        'marks': [],
        'attrs': {},
    }
    blocks.insert(intro + 1, new)
    blocks.insert(
        blocks.index(_block(doc, 'Second item')) + 1,
        {
            'id': 'p_item',
            'type': 'paragraph',
            'text': 'Third item',
            'style': 'body',
            'marks': [],
            'attrs': {'list': 'bullet', 'level': 1},
        },
    )
    blocks.remove(_block(doc, 'Trailing paragraph'))
    text, report = _export(doc, tmp_path)
    assert report['markdown']['mode'] == 'localized'
    assert {edit['change'] for edit in report['markdown']['edits']} == {'inserted', 'deleted'}
    changes = _changed_lines(source, text)
    assert [line for line in changes if line.startswith('- ')] == [
        '- \n',
        '- Trailing paragraph   with  spacing.\n',
    ]
    assert '\\# not a heading\n' in text and '- Third item\n' in text
    reimported, _ = import_document(tmp_path / 'out.md')
    assert _block(reimported, '# not a heading')['type'] == 'paragraph'
    assert _block(reimported, 'Third item')['attrs']['list'] == 'bullet'


def test_unrepresentable_formatting_requires_explicit_conversion(tmp_path):
    doc, _report, _source = _load(tmp_path)
    intro = _block(doc, 'Intro paragraph')
    intro['marks'].append({'start': 0, 'end': 5, 'type': 'underline', 'value': True})
    with pytest.raises(ByaxError) as error:
        _export(doc, tmp_path)
    assert error.value.code == 'review_required'
    assert error.value.details['losses'][0]['target'] == intro['id']
    assert not (tmp_path / 'out.md').exists()
    text, report = _export(doc, tmp_path, allow_markdown_losses=True)
    assert '<u>' not in text and '{' not in text.split('\n')[3]
    assert any(
        loss['code'] == 'inline_formatting' and loss['target'] == intro['id'] for loss in report['losses']
    )
    native = tmp_path / 'converted.byax'
    export_document(doc, native)
    restored, _ = import_document(native)
    assert any(m['type'] == 'underline' for m in _block(restored, 'Intro paragraph')['marks'])


def test_structural_list_changes_fall_back_to_reported_normalization(tmp_path):
    doc, _report, _source = _load(tmp_path)
    _block(doc, 'First item')['attrs'].update(list='ordered')
    text, report = _export(doc, tmp_path)
    assert report['markdown']['mode'] == 'regenerated'
    assert any(loss['code'] == 'markdown_normalized' for loss in report['losses'])
    assert '1. First item' in text


def test_reordered_blocks_are_regenerated_with_crlf_kept(tmp_path):
    doc, _report, _source = _load(tmp_path, '\r\n')
    doc['blocks'][0], doc['blocks'][1] = doc['blocks'][1], doc['blocks'][0]
    text, report = _export(doc, tmp_path)
    assert report['markdown']['mode'] == 'regenerated'
    assert '\r\n' in text and '\n' not in text.replace('\r\n', '')


def test_plain_text_line_endings_are_preserved_after_edits(tmp_path):
    path = tmp_path / 'notes.txt'
    path.write_bytes(b'one\r\ntwo\r\n')
    doc, _ = import_document(path)
    doc['blocks'][1]['text'] = 'three'
    out = tmp_path / 'out.txt'
    export_document(doc, out)
    assert out.read_bytes() == b'one\r\nthree'
