"""Compile pipeline (CP B2): profiles, the transient compiled document, targets, preflight, determinism."""

import base64
import copy
import difflib
import io
import json
import re
import threading
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from byax import compile as book
from byax import profiles
from byax.cli import execute
from byax.engine import apply
from byax.errors import ByaxError
from byax.exchange import import_document
from byax.model import canonical_json, new_document

PROSE = 'The rain had not stopped for three days, and the station smelled of wet wool and coal. '


def _heading(ident, text, level):
    return {
        'id': ident,
        'type': 'heading',
        'text': text,
        'style': f'heading{level}',
        'marks': [],
        'attrs': {'level': level},
    }


def _para(ident, text, marks=()):
    return {
        'id': ident,
        'type': 'paragraph',
        'text': text,
        'style': 'body',
        'marks': list(marks),
        'attrs': {},
    }


def manuscript():
    """A small novel project: two parts, three chapters, scenes, italics and a note item."""
    doc = new_document('The Long Rain')
    doc['id'] = 'doc_compile_fixture'
    doc['metadata'].update(
        author='Mara Quill',
        language='en',
        year='2026',
        dedication='For everyone who waited out the rain.',
        contact={'name': 'Mara Quill', 'address': '12 Harbour Road\nPortsmouth', 'email': 'mara@example.com'},
    )
    italic = {'start': 4, 'end': 8, 'type': 'italic', 'value': True}
    bold = {'start': 0, 'end': 3, 'type': 'bold', 'value': True}
    doc['blocks'] = [
        _heading('part_1', 'Part 1', 1),
        _heading('ch_1', 'Chapter 1', 2),
        _heading('sc_1_1', 'Arrival', 3),
        _para('p_1_1_a', '“Mara stepped off the train into the rain. ' + PROSE * 3, [italic]),
        _para('p_1_1_b', PROSE * 4),
        _heading('sc_1_2', 'Night', 3),
        _para('p_1_2_a', 'Night came early. ' + PROSE * 3, [bold]),
        _heading('ch_2', 'The Storm', 2),
        _heading('sc_2_1', 'Harbour', 3),
        _para('p_2_1_a', 'The harbour lights went out one by one. ' + PROSE * 5),
        _heading('part_2', 'After', 1),
        _heading('ch_3', 'Chapter 3', 2),
        _para('p_3_a', 'Morning, at last. ' + PROSE * 4),
        _heading('notes', 'Research notes', 1),
        _para('notes_p', 'Check train timetables for 1952.'),
    ]
    doc, _ = apply(
        doc, {'operations': [{'type': 'project_init', 'kinds': {'1': 'part', '2': 'chapter', '3': 'scene'}}]}
    )
    note = next(item for item in doc['project']['items'].values() if item['heading'] == 'notes')
    doc, _ = apply(
        doc, {'operations': [{'type': 'project_set_meta', 'target': note['id'], 'values': {'kind': 'note'}}]}
    )
    return doc


def _item(doc, heading):
    return next(item['id'] for item in doc['project']['items'].values() if item['heading'] == heading)


def _by_id(doc):
    return {block['id']: block for block in doc['blocks']}


# Profiles and presets ----------------------------------------------------------------------------


@pytest.mark.parametrize('name', book.BUILTIN_PRESETS)
def test_presets_are_settings_collections_with_a_valid_compile_profile(name):
    collection = profiles.read_collection(book.PRESET_ROOT / f'{name}.json')
    assert collection['kind'] == 'byax-settings-collection'
    profile = collection['collections']['compile'][name]
    assert book.validate_profile(profile) == profile
    assert profile['label'] and profile['description']
    assert {entry['name'] for entry in book.list_presets()} == set(book.BUILTIN_PRESETS)


def test_profile_validation_rejects_unknown_keys_and_wrong_values():
    for bad, field in (
        ({'colour': 'red'}, 'profile'),
        ({'items': {'chapter': {'start': 'sometimes'}}}, 'profile.items.chapter.start'),
        ({'items': {'poem': {}}}, 'profile.items'),
        ({'design': {'trim': '9x99'}}, 'profile.design.trim'),
        ({'design': {'margins': {'gutter': 3}}}, 'profile.design.margins'),
        ({'front_matter': [{'kind': 'foreword'}]}, 'profile.front_matter[0]'),
        ({'epub': {'include': []}}, 'profile.epub'),
        ({'target': 'odt'}, 'profile.target'),
    ):
        with pytest.raises(ByaxError) as caught:
            book.validate_profile(bad)
        assert caught.value.details['field'] == field, bad
    assert book.trim_size('6x9') == (432.0, 648.0)
    assert book.trim_size('A5') == pytest.approx((419.528, 595.276))
    assert book.trim_size({'width': 400, 'height': 600}) == (400.0, 600.0)


def test_number_words_and_manuscript_rounding():
    assert [book.number_words(n) for n in (1, 12, 21, 40, 105, 1000)] == [
        'One',
        'Twelve',
        'Twenty-One',
        'Forty',
        'One Hundred Five',
        'One Thousand',
    ]
    assert book.format_number(14, 'roman') == 'XIV'
    assert book.format_number(3, 'upper_words') == 'THREE'
    assert book.rounded_words(3449) == '3,400'
    assert book.rounded_words(103681) == '104,000'
    assert book.drop_cap_end('“Mara stepped') == 2
    assert book.lead_in_end('“Mara stepped off the train', 2, 3) == len('“Mara stepped off')


def test_saved_and_file_presets_extend_a_bundled_preset(tmp_path):
    doc = manuscript()
    operations = book.save_preset_operations(doc, 'mine', {'design': {'trim': '6x9'}, 'extends': 'novel'})
    doc, _ = apply(doc, {'operations': operations})
    profile, source = book.resolve_profile(doc, 'mine')
    assert source == {'source': 'document', 'name': 'mine', 'extends': 'novel'}
    assert profile['design']['trim'] == '6x9' and profile['items']['chapter']['drop_cap'] is True
    path = tmp_path / 'mine.json'
    profiles.write_collection(book.preset_collection('mine', profile), path)
    from_file, source = book.resolve_profile(None, preset_file=path)
    assert from_file == profile and source['source'] == 'file'
    # Saved presets travel with the other reusable settings.
    collection = profiles.export_settings(doc)
    assert set(collection['collections']['compile']) == {'mine'}
    fresh = manuscript()
    compiled = profiles.compile_collection(fresh, collection, on_conflict='replace')
    assert {
        'type': 'set_settings',
        'values': {'compile_presets': doc['settings']['compile_presets']},
    } in compiled['operations']
    assert 'compile' not in profiles.export_settings(fresh)['collections']
    with pytest.raises(ByaxError):
        book.save_preset_operations(doc, '../escape', profile)


# The compiled document ---------------------------------------------------------------------------


def test_compile_builds_a_transient_document_and_never_changes_the_master():
    doc = manuscript()
    doc['review']['comments'].append(
        {
            'id': 'c1',
            'target': 'p_1_1_b',
            'start': 0,
            'end': 3,
            'text': 'tighten',
            'author': 'me',
            'status': 'open',
            'replies': [],
        }
    )
    before = canonical_json(doc)
    profile, _ = book.resolve_profile(doc, 'novel')
    compiled, info = book.build(doc, profile, 'pdf')
    assert canonical_json(doc) == before
    assert 'project' not in compiled and compiled['review']['comments'] == []
    assert compiled['history']['revisions'] == []
    assert info['excluded']['comments'] == 1
    blocks = _by_id(compiled)
    # The note item is excluded by default, with its text.
    assert 'notes' not in blocks and 'notes_p' not in blocks
    assert _item(doc, 'notes') in info['excluded_items']
    # Generic chapter titles become the numbered label; real titles keep a label line above them.
    assert blocks['ch_1']['text'] == 'Chapter One' and blocks['ch_1']['type'] == 'heading'
    labels = [b for b in compiled['blocks'] if b['style'] == 'compile_chapter_label']
    assert [b['text'] for b in labels] == ['Chapter Two']
    order = [b['id'] for b in compiled['blocks']]
    assert order.index(labels[0]['id']) == order.index('ch_2') - 1
    assert blocks['ch_3']['text'] == 'Chapter Three'  # numbering continues across parts
    assert blocks['part_1']['text'] == 'Part One' and blocks['part_2']['text'] == 'After'
    # The first scene's heading goes (the chapter opens it); later scenes become separators.
    assert 'sc_1_1' not in blocks and 'sc_2_1' not in blocks
    assert blocks['sc_1_2'] == {
        'id': 'sc_1_2',
        'type': 'paragraph',
        'text': '* * *',
        'style': 'compile_scene_break',
        'marks': [],
        'attrs': {},
    }
    # Chapter openers: no indent, drop cap (with the opening quote) and a small-caps lead-in.
    first = blocks['p_1_1_a']
    assert first['style'] == 'compile_first'
    styles = {(m['start'], m['end'], m['value']) for m in first['marks'] if m['type'] == 'character_style'}
    assert (0, 2, 'compile_drop_cap') in styles
    assert (2, len('“Mara stepped off the'), 'compile_lead_in') in styles
    assert {m['type'] for m in first['marks']} >= {'italic'}  # authored formatting stays
    assert blocks['p_1_2_a']['style'] == 'compile_first'  # after a scene break
    assert blocks['p_1_1_b']['style'] == 'body'
    # Print structure: section breaks open pages; the first body section restarts the folios.
    sections = [b for b in compiled['blocks'] if b['type'] == 'section_break']
    first_body = next(b for b in sections if 'header' in b['attrs'])
    assert first_body['attrs']['page_number_start'] == 1
    assert first_body['attrs']['header']['first'] == ''
    assert order.index(first_body['id']) == order.index('part_1') - 1
    assert compiled['layout']['mirror_margins'] is True and compiled['layout']['page_parity'] == 'physical'
    assert (compiled['layout']['width'], compiled['layout']['height']) == (396.0, 612.0)
    # Front matter from metadata; empty epigraph and back matter are skipped and said so.
    texts = [b['text'] for b in compiled['blocks'][:12]]
    assert 'The Long Rain' in texts and 'Mara Quill' in texts
    assert 'Copyright © 2026 Mara Quill' in texts and 'For everyone who waited out the rain.' in texts
    assert any(b['type'] == 'toc' for b in compiled['blocks'])
    skipped = {note['kind'] for note in info['matter'] if note.get('skipped')}
    assert {'epigraph', 'about_author', 'acknowledgments', 'also_by'} <= skipped


def test_include_exclude_and_order_choose_binder_items():
    doc = manuscript()
    chapter_2, chapter_1 = _item(doc, 'ch_2'), _item(doc, 'ch_1')
    profile, _ = book.resolve_profile(
        doc, 'novel', overrides={'exclude': [_item(doc, 'part_2')], 'order': [chapter_2, chapter_1]}
    )
    compiled, info = book.build(doc, profile, 'epub')
    ids = [b['id'] for b in compiled['blocks']]
    assert 'part_2' not in ids and 'ch_3' not in ids and 'p_3_a' not in ids
    assert ids.index('ch_2') < ids.index('ch_1')
    assert _item(doc, 'ch_3') in info['excluded_items']
    # An explicit include list is exact: here only the second chapter (and its scene).
    profile, _ = book.resolve_profile(doc, 'novel', overrides={'include': [chapter_2, _item(doc, 'sc_2_1')]})
    compiled, _ = book.build(doc, profile, 'epub')
    body = [b['id'] for b in compiled['blocks'] if not b['id'].startswith('compile_')]
    assert body == ['ch_2', 'p_2_1_a']
    with pytest.raises(ByaxError):
        book.build(
            doc,
            book.resolve_profile(doc, 'novel', overrides={'order': [chapter_1, _item(doc, 'part_2')]})[0],
            'pdf',
        )
    with pytest.raises(ByaxError):
        book.build(doc, book.resolve_profile(doc, 'novel', overrides={'exclude': ['item_missing']})[0], 'pdf')


def test_documents_without_a_binder_compile_level_one_headings_as_chapters():
    doc = new_document('Essays')
    doc['blocks'] = [
        _heading('h1', 'On Walking', 1),
        _para('a', PROSE),
        _heading('h2', 'Detail', 2),
        _para('b', PROSE),
    ]
    profile, _ = book.resolve_profile(doc, 'nonfiction')
    compiled, info = book.build(doc, profile, 'pdf')
    assert info['structure'] == 'headings'
    blocks = _by_id(compiled)
    assert blocks['h2']['type'] == 'heading'  # a subheading, kept
    assert [b['text'] for b in compiled['blocks'] if b['style'] == 'compile_chapter_label'] == ['Chapter 1']


# Targets and determinism ----------------------------------------------------------------------------


def test_epub_compile_is_byte_reproducible_valid_and_split_per_opener(tmp_path):
    from byax.epub import validate

    doc = manuscript()
    first = book.compile_document(doc, tmp_path / 'a.epub', preset='novel', overwrite=True)
    second = book.compile_document(copy.deepcopy(doc), tmp_path / 'b.epub', preset='novel', overwrite=True)
    assert (tmp_path / 'a.epub').read_bytes() == (tmp_path / 'b.epub').read_bytes()
    assert first['compile']['compiled']['sha256'] == second['compile']['compiled']['sha256']
    assert validate(tmp_path / 'a.epub')['valid'] is True
    assert first['compile']['structure_check'] == 'passed'
    chapters = first['compile']['epub']['chapters']
    starts = [chapter['blocks'][0] for chapter in chapters]
    assert 'part_1' in starts and 'ch_1' in starts
    assert any(start.startswith('compile_part_label') for start in starts)  # "Part Two" above "After"
    opener = next(chapter['file'] for chapter in chapters if chapter['blocks'][0] == 'ch_1')
    with zipfile.ZipFile(tmp_path / 'a.epub') as archive:
        css = archive.read('OEBPS/styles/book.css').decode()
        text = archive.read(opener).decode()
    assert '.s-compile-drop-cap { float: left' in css
    assert '<span class="s-compile-drop-cap">“M</span>' in text
    assert '<span class="s-compile-lead-in">' in text and '<p class="s-compile-first">' in text
    assert '* * *' in archive_text(tmp_path / 'a.epub')
    assert first['compile']['epub']['modified'] == second['compile']['epub']['modified']


def archive_text(path):
    with zipfile.ZipFile(path) as archive:
        return ''.join(archive.read(n).decode() for n in archive.namelist() if n.endswith('.xhtml'))


def _layout_pages(compiled):
    from byax.layout import resolve_pages

    return resolve_pages(compiled)['pages']


def test_pdf_compile_is_reproducible_with_running_heads_suppressed_on_openers(tmp_path):
    from byax.layout import _region_parts

    doc = manuscript()
    first = book.compile_document(doc, tmp_path / 'a.pdf', preset='novel', overwrite=True)
    second = book.compile_document(copy.deepcopy(doc), tmp_path / 'b.pdf', preset='novel', overwrite=True)
    data, again = first['compile'], second['compile']
    assert data['layout_signature'] and data['layout_signature'] == again['layout_signature']
    assert data['page_starts'] == again['page_starts'] and data['page_count'] == again['page_count']
    assert (tmp_path / 'a.pdf').read_bytes()[:5] == b'%PDF-'
    profile, _ = book.resolve_profile(doc, 'novel')
    compiled, _ = book.build(doc, profile, 'pdf')
    pages = _layout_pages(compiled)
    assert len(pages) == data['page_count']
    openers = {
        number
        for number, start in enumerate(data['page_starts'], 1)
        if start in ('ch_1', 'part_1', 'part_2', 'ch_3')
    }
    labelled = {
        number
        for number, start in enumerate(data['page_starts'], 1)
        if start and start.startswith(('compile_chapter_label', 'compile_part_label'))
    }
    openers |= labelled
    assert openers
    body = False
    for number, page in enumerate(pages, 1):
        layout = page['layout']
        kwargs = {
            'number': page['number'],
            'section_page': page['section_page'],
            'section_pages': page['section_pages'],
            'parity': page['parity'],
        }
        header = _region_parts(layout.get('header'), number - 1, len(pages), **kwargs)
        footer = _region_parts(layout.get('footer'), number - 1, len(pages), **kwargs)
        if number in openers:
            body = True
            assert header == {} and footer == {'center': str(page['number'])}, number
        elif body and not page['blank'] and data['page_starts'][number - 1]:
            side = 'right' if page['physical_number'] % 2 else 'left'
            assert header[side] == str(page['number']), number
            assert header['center'] == ('The Long Rain' if side == 'right' else 'Mara Quill')
        # Mirrored margins: the gutter side alternates with the sheet side.
        inside = 54 + 9
        assert page['body_left'] == (inside if page['physical_number'] % 2 else 45), number
    # Parts and chapters that open on a recto skip to it with a blank verso; blanks carry nothing.
    parts = [n for n, start in enumerate(data['page_starts'], 1) if start in ('part_1', 'part_2')]
    assert all(n % 2 == 1 for n in parts)
    assert any(page['blank'] for page in pages)
    # The first body page is folio 1 even after unnumbered front matter.
    assert pages[parts[0] - 1]['number'] == 1


def test_bleed_and_crop_marks_enlarge_the_sheet_around_the_trim(tmp_path):
    doc = manuscript()
    report = book.compile_document(
        doc,
        tmp_path / 'bleed.pdf',
        preset='novel',
        profile=book.resolve_profile(doc, 'novel', overrides={'design': {'bleed': 9, 'crop_marks': True}})[0],
        overwrite=True,
    )
    plain = book.compile_document(doc, tmp_path / 'plain.pdf', preset='novel', overwrite=True)
    printing = report['compile']['print']
    assert printing['trim'] == [396.0, 612.0] and printing['bleed'] == 9 and printing['crop_marks'] is True
    assert printing['media'] == [396.0 + 2 * 30, 612.0 + 2 * 30]
    boxes = re.findall(rb'/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]', (tmp_path / 'bleed.pdf').read_bytes())
    assert boxes and all(float(w) == pytest.approx(456) and float(h) == pytest.approx(672) for w, h in boxes)
    # Bleed changes the sheet, not the page breaks.
    assert report['compile']['layout_signature'] == plain['compile']['layout_signature']
    with pytest.raises(ByaxError):
        book.resolve_profile(doc, 'novel', overrides={'design': {'bleed': 200}})


def _aligned(compiled, reimported):
    keys_a = [(b['type'], b.get('text', '')) for b in compiled['blocks']]
    keys_b = [(b['type'], b.get('text', '')) for b in reimported['blocks']]
    return difflib.SequenceMatcher(None, keys_a, keys_b, autojunk=False).get_opcodes()


def _covered(block, kind):
    return {
        i
        for m in block.get('marks', [])
        if m.get('type') == kind and m.get('value', True)
        for i in range(m['start'], m['end'])
    }


def test_standard_manuscript_round_trips_through_word_with_no_unreported_losses(tmp_path):
    """Exit criterion 4.3: compile manuscript -> DOCX -> import; every difference is in the loss report."""
    doc = manuscript()
    report = book.compile_document(doc, tmp_path / 'ms.docx', preset='manuscript', overwrite=True)
    assert report['compile']['target'] == 'docx'
    reimported, _ = import_document(tmp_path / 'ms.docx')
    profile, _ = book.resolve_profile(doc, 'manuscript')
    compiled, _ = book.build(doc, profile, 'docx')
    assert book.compiled_digest(compiled) == report['compile']['compiled']['sha256']
    located = {loss.get('target') for loss in report['losses']}
    unreported = []
    for tag, i1, i2, j1, j2 in _aligned(compiled, reimported):
        if tag != 'equal':
            unreported += [b['id'] for b in compiled['blocks'][i1:i2] if b['id'] not in located]
            continue
        for a, b in zip(compiled['blocks'][i1:i2], reimported['blocks'][j1:j2]):
            same = (
                a['attrs'].get('level') == b['attrs'].get('level')
                and all(_covered(a, k) == _covered(b, k) for k in ('italic', 'bold', 'underline'))
                and a['attrs'].get('alignment') == b['attrs'].get('alignment')
                and bool(a['attrs'].get('page_break_before')) == bool(b['attrs'].get('page_break_before'))
                and a['attrs'].get('space_before') == b['attrs'].get('space_before')
            )
            if not same and a['id'] not in located:
                unreported.append(a['id'])
    assert unreported == []
    for key in ('header', 'header_align', 'width', 'height', 'margins'):
        if compiled['layout'].get(key) != reimported['layout'].get(key):
            assert 'layout/' + key in located or 'layout' in located, key
    # The things standard format needs survive the round trip itself.
    assert reimported['layout']['header'] == {'first': '', 'text': 'Quill / THE LONG RAIN / {page}'}
    assert reimported['layout']['header_align'] == 'right'
    body = reimported['styles']['Normal']
    assert (
        body['font_family'] == 'Times New Roman' and body['font_size'] == 12 and body['line_spacing'] == 2.0
    )
    texts = [b['text'] for b in reimported['blocks']]
    assert texts[0] == 'About 300 words' and 'mara@example.com' in texts and 'by Mara Quill' in texts
    assert texts.count('#') == 1 and texts[-1] == 'END'
    chapter = next(b for b in reimported['blocks'] if b['text'] == 'Chapter One')
    assert chapter['type'] == 'heading' and chapter['attrs']['page_break_before'] is True
    italic = next(b for b in reimported['blocks'] if b['text'].startswith('“Mara'))
    assert _covered(italic, 'italic') == set(range(4, 8))
    assert report['compile']['roundtrip']['differences'] == sum(
        loss['code'] == 'roundtrip_difference' for loss in report['losses']
    )


def test_markdown_target_round_trips_with_located_losses(tmp_path):
    doc = manuscript()
    report = book.compile_document(doc, tmp_path / 'book.md', preset='novel', overwrite=True)
    assert report['compile']['target'] == 'md'
    text = (tmp_path / 'book.md').read_text(encoding='utf-8')
    assert '# Part One' in text and '## Chapter One' in text
    roundtrip = report['compile']['roundtrip']
    assert roundtrip['format'] == 'md' and roundtrip['differences'] == sum(
        loss['code'] == 'roundtrip_difference' for loss in report['losses']
    )
    fields = {loss['field'] for loss in report['losses'] if loss['code'] == 'roundtrip_difference'}
    assert 'marks.character_style' in fields and 'text.whitespace' in fields


def test_word_regions_fields_spacing_and_page_breaks_round_trip(tmp_path):
    from byax.exchange import export_document

    doc = new_document('Fields', 'First\nSecond')
    doc['layout'].update(
        header={'first': '', 'text': 'Name / TITLE / {page} of {pages}'},
        header_align='right',
        footer='{page}',
    )
    doc['styles']['body'].update(line_spacing=2.0, alignment='left')
    doc['blocks'][1]['attrs'].update(page_break_before=True, line_spacing=1.5)
    export_document(doc, tmp_path / 'f.docx')
    back, _ = import_document(tmp_path / 'f.docx')
    assert back['layout']['header'] == {'first': '', 'text': 'Name / TITLE / {page} of {pages}'}
    assert back['layout']['footer'] == '{page}' and back['layout']['header_align'] == 'right'
    assert back['styles']['Normal']['line_spacing'] == 2.0 and back['styles']['Normal']['alignment'] == 'left'
    assert (
        back['blocks'][1]['attrs']['page_break_before'] is True
        and back['blocks'][1]['attrs']['line_spacing'] == 1.5
    )
    with zipfile.ZipFile(tmp_path / 'f.docx') as archive:
        header = next(n for n in archive.namelist() if n.startswith('word/header'))
        assert b'w:instr="PAGE"' in archive.read(header) or any(
            b'w:instr="PAGE"' in archive.read(n) for n in archive.namelist() if n.startswith('word/header')
        )


# Preflight ------------------------------------------------------------------------------------------


def _png(width, height):
    from PIL import Image

    output = io.BytesIO()
    Image.new('RGB', (width, height), '#335577').save(output, format='PNG')
    return output.getvalue()


def test_preflight_reports_with_the_findings_model():
    doc = manuscript()
    doc['metadata']['author'] = ''
    raw = _png(200, 100)
    digest = 'a' * 64
    doc['assets'][digest] = {'data': base64.b64encode(raw).decode(), 'mime': 'image/png', 'name': 'map.png'}
    doc['blocks'].insert(
        4,
        {
            'id': 'map',
            'type': 'image',
            'text': '',
            'style': 'body',
            'marks': [],
            'attrs': {'asset': digest, 'width': 288},
        },
    )
    doc['blocks'].insert(14, _heading('ch_empty', 'Empty', 2))
    doc, _ = apply(
        doc, {'operations': [{'type': 'project_add_item', 'kind': 'chapter', 'target': 'ch_empty'}]}
    )
    doc['blocks'] = [b for b in doc['blocks'] if b['id'] != 'sc_2_1']  # orphan a binder item
    profile, _ = book.resolve_profile(doc, 'novel')
    report = book.compile_document(doc, preset='novel', target='pdf', preflight_only=True)
    rules = {f['rule']: f for f in report['findings']}
    assert rules['compile.metadata_missing']['affected_ids'] == ['metadata/author']
    assert rules['compile.image_alt_missing']['affected_ids'] == ['map']
    assert rules['compile.image_resolution']['measured'] == {'dpi': 50.0, 'required': 300}
    assert rules['compile.binder_orphan']['affected_ids'] == [_item(doc, 'sc_2_1')]
    assert rules['compile.empty_item']['affected_ids'] == ['ch_empty']
    assert 'compile.heading_without_content' in rules
    for finding in report['findings']:
        assert {'id', 'rule', 'severity', 'explanation', 'affected_ids', 'status', 'suite'} <= set(finding)
        assert finding['status'] == 'open' and finding['suite'] == 'compile'
    assert report['preflight']['summary']['warnings'] >= 5
    assert report['compile']['page_count'] and report['compile']['layout_signature']
    assert report['outputs'] == [] and report['preflight_only'] is True
    coverage = {c['rule']: c['status'] for c in report['coverage']}
    assert coverage['compile.orphan_heading'] == 'completed'
    # Without pagination the orphan-heading rule is reported as skipped, not passed.
    dry = book.compile_document(doc, preset='novel', target='pdf', dry_run=True)
    assert {c['rule']: c['status'] for c in dry['coverage']}['compile.orphan_heading'] == 'skipped'
    assert profile


def test_orphan_headings_are_found_from_the_page_starts():
    compiled = {'blocks': [_para('a', 'x'), _heading('h', 'Title', 1), _para('b', 'y'), _para('c', 'z')]}
    findings = book.orphan_headings(compiled, ['a', 'b', 'c'])
    assert [(f['affected_ids'], f['measured']) for f in findings] == [(['h'], {'page': 1})]


def test_fonts_that_are_missing_or_forbid_embedding_are_preflight_warnings(monkeypatch):
    import byax.fonts

    def inspect(family, bold=False, italic=False):
        if family == 'Times New Roman':
            return {
                'resolved': 'DejaVu Serif',
                'substituted': True,
                'status': 'substituted',
                'embeddable': True,
            }
        return {
            'resolved': family,
            'substituted': False,
            'status': 'available',
            'embeddable': False,
            'embedding': 'restricted',
        }

    monkeypatch.setattr(byax.fonts, 'inspect_font', inspect)
    doc = manuscript()
    report = book.compile_document(doc, preset='manuscript', target='pdf', dry_run=True)
    assert {f['rule'] for f in report['findings']} >= {'compile.font_unavailable'}
    report = book.compile_document(doc, preset='novel', target='pdf', dry_run=True)
    assert 'compile.font_not_embeddable' in {f['rule'] for f in report['findings']}


def test_preflight_errors_refuse_to_write(tmp_path):
    doc = new_document('Nothing', '')
    with pytest.raises(ByaxError) as caught:
        book.compile_document(doc, tmp_path / 'x.pdf', preset='novel')
    assert caught.value.code == 'failed_requirement'
    assert not (tmp_path / 'x.pdf').exists()


def test_dry_run_and_preflight_write_nothing_and_report_losses(tmp_path):
    doc = manuscript()
    report = book.compile_document(doc, tmp_path / 'never.epub', preset='novel', dry_run=True)
    assert not (tmp_path / 'never.epub').exists() and report['dry_run'] is True
    assert report['outputs'] == []
    assert report['compile']['target'] == 'epub'
    with pytest.raises(ByaxError):
        book.compile_document(doc, tmp_path / 'wrong.epub', preset='novel', target='pdf')


# Interfaces ----------------------------------------------------------------------------------------


def test_cli_compiles_lists_saves_and_exports_presets(tmp_path):
    from byax.storage import load_document, save_document

    path = tmp_path / 'novel.byax'
    save_document(manuscript(), path)
    listed = execute('compile', {'path': str(path), 'action': 'presets'})
    assert [p['name'] for p in listed['presets']] == list(book.BUILTIN_PRESETS)
    result = execute(
        'compile', {'path': str(path), 'preset': 'novel', 'target': 'epub', 'out': str(tmp_path / 'b.epub')}
    )
    assert result['execution'] == 'completed' and Path(result['outputs'][0]).name == 'b.epub'
    with pytest.raises(ByaxError) as caught:
        execute('compile', {'path': str(path), 'out': str(tmp_path / 'b.epub'), 'target': 'epub'})
    assert caught.value.code == 'conflict'
    with pytest.raises(ByaxError):
        execute('compile', {'path': str(path), 'action': 'transmogrify'})
    saved = execute(
        'compile', {'path': str(path), 'action': 'save-preset', 'name': 'six-by-nine', 'trim': '6x9'}
    )
    assert 'six-by-nine' in {p['name'] for p in saved['presets']}
    assert load_document(path)['settings']['compile_presets']['six-by-nine']['design']['trim'] == '6x9'
    shown = execute('compile', {'path': str(path), 'action': 'show', 'preset': 'six-by-nine'})
    assert shown['profile']['design']['trim'] == '6x9' and shown['preset']['source'] == 'document'
    exported = execute(
        'compile',
        {
            'path': str(path),
            'action': 'export-preset',
            'preset': 'six-by-nine',
            'out': str(tmp_path / 'p.json'),
        },
    )
    collection = json.loads((tmp_path / 'p.json').read_text())
    assert set(collection['collections']['compile']) == {'six-by-nine'} and exported['outputs']
    preflight = execute(
        'compile',
        {'path': str(path), 'preset_file': str(tmp_path / 'p.json'), 'preflight_only': True, 'target': 'pdf'},
    )
    assert preflight['compile']['design']['trim'] == [432.0, 648.0] and preflight['outputs'] == []
    strict = execute(
        'compile', {'path': str(path), 'target': 'md', 'out': str(tmp_path / 'b.md'), 'strict': True}
    )
    from byax.cli import result_exit_code

    assert strict['review'] == 'required' and result_exit_code(strict) == 5


def test_compile_has_mcp_and_rest_parity_with_path_scoping(tmp_path):
    from byax.mcp import handle
    from byax.service import make_server
    from byax.storage import save_document

    path = tmp_path / 'novel.byax'
    save_document(manuscript(), path)
    arguments = {'path': str(path), 'preset': 'manuscript', 'dry_run': True}
    library = execute('compile', arguments)
    response = handle(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': 'byax_compile', 'arguments': arguments},
        }
    )['result']
    mcp = response['structuredContent']
    assert mcp['compile']['compiled'] == library['compile']['compiled'] and mcp['exit_code'] == 0
    token = 'compile-parity-token-with-32-characters'
    server = make_server([tmp_path], token=token, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def rest(args):
        request = Request(
            f'http://127.0.0.1:{server.server_port}/v1/execute',
            data=json.dumps({'command': 'compile', 'arguments': args}).encode(),
            method='POST',
            headers={'Authorization': 'Bearer ' + token},
        )
        try:
            with urlopen(request, timeout=30) as reply:
                return reply.status, json.load(reply)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    try:
        status, body = rest(arguments)
        assert status == 200 and body['compile']['compiled'] == library['compile']['compiled']
        outside = tmp_path.parent / 'outside-preset.json'
        status, body = rest({**arguments, 'preset_file': str(outside)})
        assert status >= 400 and body['error']['code'] in ('permission_denied', 'invalid_input', 'forbidden')
        status, body = rest(
            {'path': str(path), 'out': str(tmp_path.parent / 'escape.epub'), 'target': 'epub'}
        )
        assert status >= 400 and not (tmp_path.parent / 'escape.epub').exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# The 100,000-word fixture (exit criterion 4.3) --------------------------------------------------------


def test_100k_word_project_compiles_to_a_reproducible_pdf(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from generate_fixtures import make_fixture

    doc = make_fixture('project_100k')
    first = book.compile_document(doc, tmp_path / 'a.pdf', preset='novel', overwrite=True)
    second = book.compile_document(
        make_fixture('project_100k'), tmp_path / 'b.pdf', preset='novel', overwrite=True
    )
    assert first['compile']['layout_signature'] == second['compile']['layout_signature']
    assert first['compile']['page_starts'] == second['compile']['page_starts']
    assert first['compile']['page_count'] > 300
    assert first['compile']['compiled']['sha256'] == second['compile']['compiled']['sha256']
