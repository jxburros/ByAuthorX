"""EPUB 3 export, import and the built-in structural validator."""

import base64
import copy
import hashlib
import importlib.util
import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from byax import epub
from byax.errors import ByaxError
from byax.exchange import EXPORT_FORMATS, conversion_preview, export_document, import_document
from byax.formats import FORMATS
from byax.model import new_document, validate_document

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT / 'scripts'))
    return module


FIXTURES = _load('generate_fixtures')
SCRIPT = _load('epubcheck_fixtures')
PNG = SCRIPT.PNG
NS = {
    'x': 'http://www.w3.org/1999/xhtml',
    'o': 'http://www.idpf.org/2007/opf',
    'd': 'http://purl.org/dc/elements/1.1/',
}


def block(ident, text='', kind='paragraph', **attrs):
    style = attrs.pop('style', 'heading1' if kind == 'heading' else 'body')
    return {'id': ident, 'type': kind, 'text': text, 'style': style, 'marks': [], 'attrs': attrs}


def book(*blocks, title='A Book', **metadata):
    doc = new_document(title)
    doc['id'] = 'doc_epub_test'
    doc['metadata'].update({'author': 'Pat Writer', **metadata})
    doc['blocks'] = list(blocks) or [block('p1', 'Hello.')]
    return doc


def add_asset(doc, data, mime='image/png'):
    key = hashlib.sha256(data).hexdigest()
    doc['assets'][key] = {'data': base64.b64encode(data).decode(), 'mime': mime, 'name': 'x'}
    return key


def members(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def xml(path, name):
    return ET.fromstring(members(path)[name])


def codes(report):
    return [loss['code'] for loss in report['losses']]


def rewrite(source, target, edit):
    """Copy an EPUB, letting ``edit`` change its members (name to bytes, None removes)."""
    files = members(source)
    edit(files)
    with zipfile.ZipFile(target, 'w') as archive:
        archive.writestr('mimetype', files.pop('mimetype', b'application/epub+zip'), zipfile.ZIP_STORED)
        for name, data in files.items():
            if data is not None:
                archive.writestr(name, data, zipfile.ZIP_DEFLATED)
    return target


# --- registry and reports ----------------------------------------------------------------------


def test_epub_is_registered_as_an_export_format_and_file_view():
    assert 'epub' in EXPORT_FORMATS and 'epub' in FORMATS
    entry = FORMATS['epub']
    assert entry['fidelity'] == 'semantic' and not entry['paged'] and not entry['source']
    assert entry['blocks']['footnote'] == 'full' and entry['lists'] == 'full'


def test_conversion_preview_reports_the_same_losses_as_the_export(tmp_path):
    doc = SCRIPT.rich_document()
    preview = conversion_preview(doc, 'epub')
    report = export_document(doc, tmp_path / 'rich.epub')
    assert preview['preview'] and preview['losses'] == report['losses']
    assert {'image_alt_missing', 'image_not_embedded', 'link_dropped', 'empty_heading'} <= set(codes(report))
    assert report['format'] == 'epub' and report['structure_check'] == 'passed'
    assert all(loss['target'] for loss in report['losses'])


# --- determinism and package shape -------------------------------------------------------------


def test_export_is_byte_reproducible_and_the_zip_is_canonical(tmp_path):
    doc = SCRIPT.rich_document()
    first, second = tmp_path / 'one' / 'a.epub', tmp_path / 'two' / 'b.epub'
    export_document(doc, first)
    export_document(copy.deepcopy(doc), second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        infos = archive.infolist()
        assert infos[0].filename == 'mimetype' and infos[0].compress_type == zipfile.ZIP_STORED
        assert archive.read('mimetype') == b'application/epub+zip' and not infos[0].extra
        assert [i.filename for i in infos[1:]] == sorted(i.filename for i in infos[1:])
        assert {i.date_time for i in infos} == {(1980, 1, 1, 0, 0, 0)}
        assert {i.create_system for i in infos} == {3}


def test_same_bytes_whatever_the_wall_clock(tmp_path, monkeypatch):
    doc = book(block('p1', 'Text.'))
    first = tmp_path / 'a.epub'
    export_document(doc, first)

    class Frozen(epub.datetime):
        @classmethod
        def now(cls, tz=None):
            raise AssertionError('the clock must not be read')

    monkeypatch.setattr(epub, 'datetime', Frozen)
    second = tmp_path / 'b.epub'
    export_document(doc, second)
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize(
    'name', [n for n in FIXTURES.NAMES if n not in ('long_500', 'flowing_500', 'flowing_2000')]
)
def test_every_fixture_exports_a_structurally_valid_epub(name, tmp_path):
    path = tmp_path / (name + '.epub')
    report = export_document(FIXTURES.make_fixture(name), path)
    result = epub.validate(path)
    assert result['valid'], result['errors']
    assert not result['warnings']
    assert report['structure_check'] == 'passed' and report['epub']['version'] == '3.0'


def test_rich_document_is_valid_and_ids_are_unique(tmp_path):
    path = tmp_path / 'rich.epub'
    export_document(SCRIPT.rich_document(), path)
    result = epub.validate(path)
    assert result['valid'] and result['checked']['xhtml_documents'] == 4
    for name, data in members(path).items():
        if name.endswith('.xhtml'):
            ids = [e.get('id') for e in ET.fromstring(data).iter() if e.get('id')]
            assert len(ids) == len(set(ids)), name


# --- metadata ----------------------------------------------------------------------------------


def test_package_metadata_is_taken_from_the_document(tmp_path):
    doc = book(
        block('p1', 'Text.'),
        title='Tides & Moons',
        language='fr_CA',
        publisher='Marée',
        description='About tides',
        rights='CC0',
        subjects=['Sea', 'Moon'],
        modified='2026-03-04T05:06:07+01:00',
    )
    path = tmp_path / 'm.epub'
    export_document(doc, path)
    opf = xml(path, 'OEBPS/content.opf')
    meta = opf.find('o:metadata', NS)
    assert meta.find('d:title', NS).text == 'Tides & Moons'
    assert meta.find('d:creator', NS).text == 'Pat Writer'
    assert meta.find('d:language', NS).text == 'fr-CA'
    assert meta.find('d:publisher', NS).text == 'Marée'
    assert [e.text for e in meta.findall('d:subject', NS)] == ['Sea', 'Moon']
    modified = [e.text for e in meta.findall('o:meta', NS) if e.get('property') == 'dcterms:modified']
    assert modified == ['2026-03-04T04:06:07Z']
    identifier = meta.find('d:identifier', NS).text
    assert identifier.startswith('urn:uuid:') and opf.get('unique-identifier') == 'pub-id'
    again = tmp_path / 'again.epub'
    export_document(copy.deepcopy(doc), again)
    assert xml(again, 'OEBPS/content.opf').find('o:metadata/d:identifier', NS).text == identifier
    other = copy.deepcopy(doc)
    other['id'] = 'doc_other'
    export_document(other, tmp_path / 'other.epub')
    assert (
        xml(tmp_path / 'other.epub', 'OEBPS/content.opf').find('o:metadata/d:identifier', NS).text
        != identifier
    )


def test_modified_falls_back_to_history_then_a_fixed_date(tmp_path):
    doc = book()
    report = export_document(doc, tmp_path / 'a.epub')
    assert (
        report['epub']['modified'] == epub.DEFAULT_MODIFIED and report['epub']['modified_source'] == 'default'
    )
    doc['history']['revisions'] = [
        {'revision': 'r1', 'timestamp': '2026-01-01T00:00:00+00:00', 'snapshot': {}},
        {'revision': 'r2', 'timestamp': '2026-02-03T04:05:06.789+00:00', 'snapshot': {}},
    ]
    report = export_document(doc, tmp_path / 'b.epub')
    assert (
        report['epub']['modified'] == '2026-02-03T04:05:06Z'
        and report['epub']['modified_source'] == 'history'
    )


def test_missing_title_and_bad_language_are_reported(tmp_path):
    doc = book(title='', language='not a tag!')
    report = export_document(doc, tmp_path / 'a.epub')
    assert {'metadata_missing', 'metadata_language'} <= set(codes(report))
    opf = xml(tmp_path / 'a.epub', 'OEBPS/content.opf')
    assert opf.find('o:metadata/d:title', NS).text == 'Untitled'
    assert opf.find('o:metadata/d:language', NS).text == 'en'
    assert epub.validate(tmp_path / 'a.epub')['valid']


def test_accessibility_metadata_and_options(tmp_path):
    doc = book(block('h1', 'One', 'heading', level=1), block('p1', 'Text.'))
    path = tmp_path / 'a.epub'
    export_document(doc, path)
    props = {}
    for e in xml(path, 'OEBPS/content.opf').findall('o:metadata/o:meta', NS):
        props.setdefault(e.get('property'), []).append(e.text)
    assert props['schema:accessMode'] == ['textual']
    assert props['schema:accessibilityHazard'] == ['none']
    assert {'structuralNavigation', 'tableOfContents', 'readingOrder'} <= set(
        props['schema:accessibilityFeature']
    )
    assert props['schema:accessibilitySummary'][0].startswith('This publication is reflowable text')
    export_document(
        doc,
        tmp_path / 'b.epub',
        profile={'epub': {'accessibility': {'summary': 'Custom summary.', 'hazards': ['noFlashingHazard']}}},
    )
    props = {
        e.get('property'): e.text
        for e in xml(tmp_path / 'b.epub', 'OEBPS/content.opf').findall('o:metadata/o:meta', NS)
    }
    assert props['schema:accessibilitySummary'] == 'Custom summary.'
    assert props['schema:accessibilityHazard'] == 'noFlashingHazard'
    with pytest.raises(ByaxError, match='vocabulary'):
        export_document(doc, tmp_path / 'c.epub', profile={'epub': {'accessibility': {'hazards': ['bogus']}}})


# --- navigation and chapters -------------------------------------------------------------------


def nav_tree(path):
    nav = xml(path, 'OEBPS/nav.xhtml')
    toc = next(
        n for n in nav.iter('{' + NS['x'] + '}nav') if n.get('{http://www.idpf.org/2007/ops}type') == 'toc'
    )

    def walk(ol):
        out = []
        for li in ol.findall('x:li', NS):
            link = li.find('x:a', NS)
            child = li.find('x:ol', NS)
            out.append((link.text, link.get('href'), walk(child) if child is not None else []))
        return out

    return walk(toc.find('x:ol', NS))


def test_navigation_is_built_from_the_outline_and_chapters_split_at_level_one(tmp_path):
    doc = book(
        block('h1', 'Part One', 'heading', level=1),
        block('p1', 'a'),
        block('h2', 'Section', 'heading', level=2),
        block('h3', 'Deep', 'heading', level=3),
        block('h4', 'Part Two', 'heading', level=1),
        block('p2', 'b'),
    )
    path = tmp_path / 'n.epub'
    report = export_document(doc, path)
    tree = nav_tree(path)
    assert [t[0] for t in tree] == ['Part One', 'Part Two']
    assert tree[0][2][0][0] == 'Section' and tree[0][2][0][2][0][0] == 'Deep'
    assert tree[0][1].startswith('text/chapter-001.xhtml#') and tree[1][1].startswith(
        'text/chapter-002.xhtml#'
    )
    assert [c['title'] for c in report['epub']['chapters']] == ['Part One', 'Part Two']
    names = members(path)
    assert 'OEBPS/text/chapter-001.xhtml' in names and 'OEBPS/text/chapter-002.xhtml' in names
    ncx = xml(path, 'OEBPS/toc.ncx')
    ns = {'n': 'http://www.daisy.org/z3986/2005/ncx/'}
    assert [t.text for t in ncx.findall('.//n:navLabel/n:text', ns)] == [
        'Part One',
        'Section',
        'Deep',
        'Part Two',
    ]
    assert [p.get('playOrder') for p in ncx.iter('{' + ns['n'] + '}navPoint')] == ['1', '2', '3', '4']
    # Heading targets exist: the built-in check follows every nav fragment.
    assert epub.validate(path)['valid']


def test_documents_without_headings_still_get_a_navigation_entry(tmp_path):
    path = tmp_path / 'n.epub'
    export_document(book(block('p1', 'Only text.'), title='Plain'), path)
    assert nav_tree(path) == [('Plain', 'text/chapter-001.xhtml', [])]
    assert epub.validate(path)['valid']


def test_split_points_titles_include_ranges_css_and_overrides(tmp_path):
    doc = book(*(block(f'p{i}', f'Paragraph {i}') for i in range(1, 7)))
    path = tmp_path / 's.epub'
    report = epub.export_epub(
        doc,
        path,
        {
            'split_at': [{'id': 'p3', 'title': 'Middle'}, 'p5'],
            'include': [['p2', 'p5']],
            'css': 'p { color: #112233 }',
            'language': 'de',
            'identifier': 'urn:isbn:9780000000002',
            'modified': '2027-01-02T03:04:05Z',
            'title': 'Override',
            'author': 'Someone Else',
            'toc_title': 'Inhalt',
            'ncx': False,
        },
    )
    assert [c['blocks'] for c in report['epub']['chapters']] == [['p2'], ['p3', 'p4'], ['p5']]
    assert [t[0] for t in nav_tree(path)] == ['Middle']  # only titled splits and headings are outline entries
    assert 'OEBPS/toc.ncx' not in members(path)
    assert b'#112233' in members(path)['OEBPS/styles/book.css']
    opf = xml(path, 'OEBPS/content.opf')
    assert opf.find('o:metadata/d:language', NS).text == 'de'
    assert opf.find('o:metadata/d:identifier', NS).text == 'urn:isbn:9780000000002'
    assert opf.find('o:metadata/d:creator', NS).text == 'Someone Else'
    assert opf.find('o:spine', NS).get('toc') is None
    assert b'Inhalt' in members(path)['OEBPS/nav.xhtml']
    assert epub.validate(path)['valid']
    with pytest.raises(ByaxError) as error:
        epub.export_epub(doc, tmp_path / 'x.epub', {'split_at': ['nope']})
    assert error.value.code == 'missing_target'
    with pytest.raises(ByaxError) as error:
        epub.export_epub(doc, tmp_path / 'x.epub', {'include': [['p4', 'p2']]})
    assert error.value.code == 'invalid_input'
    with pytest.raises(ByaxError, match='Unknown EPUB option'):
        epub.export_epub(doc, tmp_path / 'x.epub', {'bogus': 1})


def test_single_document_when_split_level_is_zero_and_options_in_a_saved_profile(tmp_path):
    doc = book(block('h1', 'A', 'heading', level=1), block('h2', 'B', 'heading', level=1))
    doc['profiles']['book'] = {'format': 'epub', 'epub': {'split_level': 0}}
    report = export_document(doc, tmp_path / 'a.epub', profile='book')
    assert len(report['epub']['chapters']) == 1


# --- cover, images, alt text -------------------------------------------------------------------


def test_cover_from_metadata_is_packaged_as_cover_image_and_first_page(tmp_path):
    doc = book(block('p1', 'Text.'))
    doc['metadata']['cover'] = add_asset(doc, PNG)
    path = tmp_path / 'c.epub'
    report = export_document(doc, path)
    opf = xml(path, 'OEBPS/content.opf')
    covers = [i for i in opf.findall('o:manifest/o:item', NS) if i.get('properties') == 'cover-image']
    assert len(covers) == 1 and covers[0].get('media-type') == 'image/png'
    assert opf.find('o:spine/o:itemref', NS).get('idref') == 'cover-page'
    assert b'epub:type="cover"' in members(path)['OEBPS/text/cover.xhtml']
    assert report['epub']['cover'].startswith('OEBPS/images/') and epub.validate(path)['valid']


def test_cover_block_is_used_and_not_repeated_and_a_missing_cover_is_reported(tmp_path):
    doc = book(block('p1', 'Text.'))
    doc['blocks'].insert(
        0, block('cover_image', kind='image', asset=add_asset(doc, PNG), alt='Cover art', cover=True)
    )
    path = tmp_path / 'c.epub'
    export_document(doc, path)
    assert b'<img' not in members(path)['OEBPS/text/chapter-001.xhtml']
    assert b'cover-image' in members(path)['OEBPS/content.opf']
    doc['metadata']['cover'] = 'f' * 64
    report = export_document(doc, tmp_path / 'd.epub')
    assert 'cover_missing' in codes(report)
    assert b'cover-image' not in members(tmp_path / 'd.epub')['OEBPS/content.opf']
    # An explicit option beats the metadata.
    other = add_asset(doc, base64.b64decode(base64.b64encode(PNG)) + b'')
    doc['assets'][other]['data'] = base64.b64encode(PNG).decode()
    option = epub.export_epub(doc, tmp_path / 'e.epub', {'cover': other})
    assert 'cover_missing' not in codes(option)


def test_images_without_alt_text_are_reported_decorative_images_are_not(tmp_path):
    doc = book(block('p0', 'Intro'))
    key = add_asset(doc, PNG)
    doc['blocks'] += [
        block('with_alt', kind='image', asset=key, alt='A pixel'),
        block('no_alt', kind='image', asset=key, alt='  '),
        block('decorative', kind='image', asset=key, alt='', decorative=True),
    ]
    path = tmp_path / 'i.epub'
    report = export_document(doc, path)
    missing = [loss for loss in report['losses'] if loss['code'] == 'image_alt_missing']
    assert [loss['target'] for loss in missing] == ['no_alt']
    page = members(path)['OEBPS/text/chapter-001.xhtml'].decode()
    assert page.count('<img') == 3 and 'role="presentation"' in page and 'alt="A pixel"' in page
    props = {e.text for e in xml(path, 'OEBPS/content.opf').findall('o:metadata/o:meta', NS)}
    assert 'visual' in props and 'alternativeText' not in props
    assert any('lack a text alternative' in p for p in props)
    assert len([n for n in members(path) if n.startswith('OEBPS/images/')]) == 1  # shared asset, one file


def test_complete_alt_text_declares_the_alternative_text_feature(tmp_path):
    doc = book()
    doc['blocks'].append(block('i1', kind='image', asset=add_asset(doc, PNG), alt='A pixel'))
    path = tmp_path / 'i.epub'
    report = export_document(doc, path)
    assert 'image_alt_missing' not in codes(report)
    assert 'alternativeText' in report['epub']['accessibility']['features']


def test_unsupported_or_mismatched_images_are_not_packaged(tmp_path):
    doc = book()
    doc['blocks'] += [
        block('bmp', kind='image', asset=add_asset(doc, b'BM' + bytes(40), 'image/bmp'), alt='bmp'),
        block('lie', kind='image', asset=add_asset(doc, b'not a png', 'image/png'), alt='lie'),
        block('gone', kind='image', alt='external', link='dep'),
    ]
    path = tmp_path / 'i.epub'
    report = export_document(doc, path)
    assert [loss['target'] for loss in report['losses'] if loss['code'] == 'image_not_embedded'] == [
        'bmp',
        'lie',
        'gone',
    ]
    assert not [n for n in members(path) if n.startswith('OEBPS/images/')]
    assert epub.validate(path)['valid']


# --- fonts -------------------------------------------------------------------------------------


def font_files(path):
    return sorted(n for n in members(path) if n.startswith('OEBPS/fonts/'))


def test_permitted_bundled_fonts_are_embedded_with_face_rules(tmp_path):
    doc = book(block('h1', 'Title', 'heading', level=1), block('p1', 'Body'))
    path = tmp_path / 'f.epub'
    report = export_document(doc, path)
    assert [f['face'] for f in report['epub']['fonts']] == ['regular', 'bold']
    assert all(f['embedded'] and f['embedding'] == 'installable' for f in report['epub']['fonts'])
    assert len(font_files(path)) == 2
    css = members(path)['OEBPS/styles/book.css'].decode()
    assert css.count('@font-face') == 2 and 'font-weight: bold' in css
    opf = xml(path, 'OEBPS/content.opf')
    assert {
        i.get('media-type')
        for i in opf.findall('o:manifest/o:item', NS)
        if '/fonts/' in 'OEBPS/' + i.get('href')
    } == {'font/ttf'}
    off = tmp_path / 'off.epub'
    assert export_document(doc, off, profile={'epub': {'embed_fonts': False}})['epub']['fonts'] == []
    assert not font_files(off)


def restricted_font_document(fs_type):
    """A document embedding DejaVu Sans with its OS/2 fsType changed to ``fs_type``."""
    from byax.fonts import bundled_font_path

    raw = bytearray(bundled_font_path('DejaVu Sans').read_bytes())
    count = int.from_bytes(raw[4:6], 'big')
    for index in range(count):
        record = 12 + index * 16
        if raw[record : record + 4] == b'OS/2':
            start = int.from_bytes(raw[record + 8 : record + 12], 'big')
            raw[start + 8 : start + 10] = fs_type.to_bytes(2, 'big')
    doc = book(block('p1', 'Body'))
    key = add_asset(doc, bytes(raw), 'font/ttf')
    doc['layout']['fonts'] = {'DejaVu Sans': {'regular': {'font_asset': key}}}
    return doc


def test_fonts_whose_permission_forbids_embedding_are_not_embedded(tmp_path):
    doc = restricted_font_document(0x0002)  # restricted license embedding
    path = tmp_path / 'f.epub'
    report = export_document(doc, path)
    assert not font_files(path)
    loss = next(loss for loss in report['losses'] if loss['code'] == 'font_not_embedded')
    assert 'restricted' in loss['message'] and loss['target'] == 'fonts/DejaVu Sans/regular'
    assert report['epub']['fonts'][0]['embedded'] is False
    allowed = tmp_path / 'ok.epub'
    export_document(restricted_font_document(0x0008), allowed)  # editable embedding is permitted
    assert len(font_files(allowed)) == 1


def test_a_missing_font_is_reported_instead_of_substituted(tmp_path):
    doc = book(block('p1', 'Body'))
    doc['styles']['body']['font_family'] = 'Nonexistent Display Face'
    report = export_document(doc, tmp_path / 'f.epub')
    assert 'font_unavailable' in codes(report) and not font_files(tmp_path / 'f.epub')
    css = members(tmp_path / 'f.epub')['OEBPS/styles/book.css'].decode()
    assert 'font-family: "Nonexistent Display Face", serif' in css


# --- footnotes, lists, tables, marks, safety ---------------------------------------------------


def test_footnotes_use_epub_3_notes_with_references_in_reading_order(tmp_path):
    doc = book(
        block('p1', 'First paragraph.'),
        block('n1', 'Note one.', 'footnote', reference='p1', offset=5),
        block('p2', 'Second paragraph.'),
        block('n2', 'Note two.', 'footnote', reference='p2', offset=6),
        block('n3', 'Loose note.', 'footnote'),
    )
    path = tmp_path / 'n.epub'
    report = export_document(doc, path)
    page = members(path)['OEBPS/text/chapter-001.xhtml'].decode()
    assert page.count('epub:type="noteref"') == 2 and page.count('epub:type="footnote"') == 2
    assert page.index('id="fn-1"') < page.index('id="fnref-2"') < page.index('id="fn-2"')
    assert 'First<a class="noteref"' in page and 'Loose note.' in page
    assert [loss['target'] for loss in report['losses'] if loss['code'] == 'structural_object'] == ['n3']
    assert epub.validate(path)['valid'] and report['epub']['footnotes'] == 2


def test_citation_footnotes_are_native_notes(tmp_path):
    doc = book(block('p1', 'Claim to cite.'))
    doc['references']['smith'] = {
        'type': 'book',
        'title': 'Tides',
        'author': [{'family': 'Smith', 'given': 'J'}],
        'issued': {'date-parts': [[2020]]},
    }
    doc['settings']['citations'] = {'style': 'apa', 'presentation': 'footnotes', 'bibliography': False}
    doc['blocks'][0]['marks'] = [{'type': 'citation', 'value': 'smith', 'start': 0, 'end': 5}]
    report = export_document(doc, tmp_path / 'c.epub')
    assert 'citation_footnotes_as_endnotes' not in codes(report)
    assert b'epub:type="footnote"' in members(tmp_path / 'c.epub')['OEBPS/text/chapter-001.xhtml']


def test_lists_nest_tables_keep_headers_and_spans_and_marks_become_markup(tmp_path):
    path = tmp_path / 'r.epub'
    export_document(SCRIPT.rich_document(), path)
    page = members(path)['OEBPS/text/chapter-001.xhtml'].decode()
    assert re.search(r'<ul>\s*<li>Bullet one\s*<ul>\s*<li>Nested bullet\s*<ol>', page)
    assert '<ol start="7">' in page
    assert '<thead><tr><th scope="col"><strong>Site</strong></th>' in page
    assert '<td rowspan="2">North</td><td colspan="2">Spans two columns</td>' in page
    assert '<strong>Bold</strong>' in page and '<code>code</code>' in page and '<sup>super</sup>' in page
    assert '<a href="https://example.com/a?b=1&amp;c=2">link</a>' in page
    assert '<a href="chapter-001.xhtml#p_target">internal</a>' in page
    assert 'href="other.html"' not in page and '>relative<' not in page.replace('relative</p>', '')
    assert 'lang="fr"' in page and '<pre class="s-code">' in page and '<blockquote' in page
    css = members(path)['OEBPS/styles/book.css'].decode()
    assert (
        '.s-lead' in css
        and 'text-align: center' in css
        and '.s-emphasis { font-style: italic; color: #336699 }' in css
    )
    assert '\x01' not in page


def test_untrusted_values_never_reach_markup_or_css(tmp_path):
    doc = book(
        block('p1', '<script>alert(1)</script> & "quotes"'),
        title='<b>Title</b>',
        author='A & B',
    )
    doc['blocks'][0]['marks'] = [
        {'type': 'color', 'start': 0, 'end': 5, 'value': 'red; background: url(http://evil/x)'},
        {'type': 'font_family', 'start': 5, 'end': 9, 'value': 'x"}</style><script>'},
        {'type': 'link', 'start': 9, 'end': 12, 'value': 'javascript:alert(1)'},
    ]
    doc['styles']['body']['color'] = 'expression(alert(1))'
    path = tmp_path / 'x.epub'
    export_document(doc, path)
    files = members(path)
    for name, data in files.items():
        if name.endswith(('.xhtml', '.css', '.opf', '.ncx')):
            text = data.decode()
            assert '<script' not in text and 'evil' not in text and 'javascript:' not in text, name
    assert epub.validate(path)['valid']
    assert b'&lt;scri' in files['OEBPS/text/chapter-001.xhtml']


def test_policy_excluded_blocks_do_not_reach_the_epub(tmp_path):
    doc = book(block('p1', 'Keep'), block('p2', 'Secret'), block('p3', 'Print only'))
    doc['blocks'][1]['attrs']['exclude_outputs'] = ['epub']
    doc['blocks'][2]['attrs']['outputs'] = ['pdf']
    report = export_document(doc, tmp_path / 'e.epub')
    page = members(tmp_path / 'e.epub')['OEBPS/text/chapter-001.xhtml'].decode()
    assert 'Keep' in page and 'Secret' not in page and 'Print only' not in page
    assert [loss['target'] for loss in report['losses'] if loss['code'] == 'output_excluded'] == ['p2', 'p3']


# --- validator ---------------------------------------------------------------------------------


@pytest.fixture
def good(tmp_path):
    path = tmp_path / 'good.epub'
    export_document(SCRIPT.rich_document(), path)
    return path


def broken(good, tmp_path, edit):
    result = epub.validate(rewrite(good, tmp_path / 'bad.epub', edit))
    assert not result['valid']
    return {e['code'] for e in result['errors']}


def test_validator_accepts_the_export_and_rejects_non_zip_input(good, tmp_path):
    assert epub.validate(good)['valid']
    assert epub.validate(good.read_bytes())['valid']
    (tmp_path / 'x.epub').write_bytes(b'not a zip')
    assert epub.validate(tmp_path / 'x.epub')['errors'][0]['code'] == 'not_zip'


def test_validator_reports_container_and_package_defects(good, tmp_path):
    def drop_member(files):
        files['OEBPS/images/' + next(n.split('/')[-1] for n in files if n.startswith('OEBPS/images/'))] = None

    assert 'manifest_missing' in broken(good, tmp_path, drop_member)
    assert 'member_missing' in broken(good, tmp_path, lambda f: f.pop('META-INF/container.xml'))
    assert 'mimetype_content' in broken(good, tmp_path, lambda f: f.update(mimetype=b'application/zip'))

    def bad_opf(old, new):
        return lambda f: f.update({'OEBPS/content.opf': f['OEBPS/content.opf'].replace(old, new, 1)})

    assert 'spine_idref' in broken(good, tmp_path, bad_opf(b'idref="chapter-001"', b'idref="nope"'))
    assert 'manifest_id' in broken(good, tmp_path, bad_opf(b'id="css"', b'id="nav"'))
    assert 'nav_count' in broken(good, tmp_path, bad_opf(b' properties="nav"', b''))
    assert 'identifier' in broken(
        good, tmp_path, bad_opf(b'unique-identifier="pub-id"', b'unique-identifier="x"')
    )
    assert 'modified' in broken(good, tmp_path, bad_opf(b'dcterms:modified', b'dcterms:other'))
    assert 'metadata_title' in broken(
        good, tmp_path, bad_opf(b'<dc:title id="title">The Lighthouse Keeper', b'<dc:title id="title">')
    )
    assert 'spine_type' in broken(good, tmp_path, bad_opf(b'idref="chapter-001"', b'idref="css"'))


def test_validator_reports_content_defects(good, tmp_path):
    def chapter(edit):
        return lambda f: f.update({'OEBPS/text/chapter-001.xhtml': edit(f['OEBPS/text/chapter-001.xhtml'])})

    assert 'xml_invalid' in broken(good, tmp_path, chapter(lambda d: d.replace(b'</body>', b'')))
    assert 'duplicate_id' in broken(
        good, tmp_path, chapter(lambda d: d.replace(b'id="h_sec"', b'id="h_part1"'))
    )
    assert 'dangling_fragment' in broken(good, tmp_path, chapter(lambda d: d.replace(b'#fn-1"', b'#fn-99"')))
    assert 'dangling_reference' in broken(
        good, tmp_path, chapter(lambda d: d.replace(b'../styles/book.css', b'../styles/gone.css'))
    )
    assert 'img_alt' in broken(
        good,
        tmp_path,
        chapter(lambda d: d.replace(b' alt="A blue square standing in for a photograph"', b'', 1)),
    )
    assert 'script' in broken(
        good, tmp_path, chapter(lambda d: d.replace(b'<section', b'<script>1</script><section', 1))
    )
    assert 'remote_resource' in broken(
        good, tmp_path, chapter(lambda d: d.replace(b'<img src="../images', b'<img src="http://x/images', 1))
    )
    assert 'nav_empty' in broken(
        good,
        tmp_path,
        lambda f: f.update(
            {
                'OEBPS/nav.xhtml': re.sub(
                    rb'<nav epub:type="toc".*?</nav>',
                    b'<nav epub:type="toc" id="toc"><ol/></nav>',
                    f['OEBPS/nav.xhtml'],
                    flags=re.DOTALL,
                )
            }
        ),
    )
    assert 'nav_target' in broken(
        good,
        tmp_path,
        lambda f: f.update(
            {
                'OEBPS/nav.xhtml': f['OEBPS/nav.xhtml'].replace(
                    b'href="text/cover.xhtml"', b'href="styles/book.css"'
                )
            }
        ),
    )
    assert 'image_type' in broken(
        good, tmp_path, lambda f: f.update({n: b'junk' for n in f if n.endswith('.jpg')})
    )
    assert 'css_resource' in broken(
        good,
        tmp_path,
        lambda f: f.update(
            {
                'OEBPS/styles/book.css': f['OEBPS/styles/book.css']
                + b'\n@font-face{src:url("../fonts/zzz.ttf")}'
            }
        ),
    )


def test_validator_requires_a_stored_mimetype_first(good, tmp_path):
    reordered = tmp_path / 'order.epub'
    with zipfile.ZipFile(reordered, 'w') as archive:
        archive.writestr('META-INF/container.xml', members(good)['META-INF/container.xml'])
        archive.writestr('mimetype', b'application/epub+zip')
    assert 'mimetype_first' in {e['code'] for e in epub.validate(reordered)['errors']}
    deflated = tmp_path / 'deflated.epub'
    with zipfile.ZipFile(deflated, 'w') as archive:
        archive.writestr('mimetype', b'application/epub+zip', zipfile.ZIP_DEFLATED)
    assert 'mimetype_stored' in {e['code'] for e in epub.validate(deflated)['errors']}


def test_a_failed_structure_check_stops_publication(tmp_path, monkeypatch):
    target = tmp_path / 'out.epub'
    monkeypatch.setattr(
        epub, 'validate', lambda path: {'valid': False, 'errors': [{'code': 'x'}], 'warnings': []}
    )
    with pytest.raises(ByaxError) as error:
        export_document(book(), target)
    assert error.value.code == 'execution_failure' and not target.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith('.byax-export')]


# --- import ------------------------------------------------------------------------------------


def test_import_round_trip_keeps_text_structure_metadata_notes_and_images(tmp_path):
    doc = SCRIPT.rich_document()
    path = tmp_path / 'rich.epub'
    export_document(doc, path)
    back, report = import_document(path)
    validate_document(back)
    kinds = [b['type'] for b in back['blocks']]
    texts = [b['text'] for b in back['blocks'] if b['type'] in ('heading', 'paragraph')]
    assert (
        back['metadata']['title'] == 'The Lighthouse Keeper' and back['metadata']['author'] == 'Ada Example'
    )
    assert back['metadata']['language'] == 'en-GB' and back['metadata']['publisher'] == 'Example Press'
    assert back['metadata']['modified'] == '2026-10-01T10:00:00Z'
    assert back['metadata']['cover'] in back['assets']
    assert [b['text'] for b in back['blocks'] if b['type'] == 'heading'][:3] == [
        'Part One: The Light',
        'The tower',
        'Stairs',
    ]
    assert 'Part Two: Ελληνικά, עברית, 日本語' in texts
    assert (
        'Bold italic underline strike code super link colour highlight serif sized style internal relative'
        in texts
    )
    bold = next(b for b in back['blocks'] if b['text'].startswith('Bold italic'))
    assert {m['type'] for m in bold['marks']} >= {
        'bold',
        'italic',
        'underline',
        'strikethrough',
        'code',
        'superscript',
        'link',
    }
    assert next(m for m in bold['marks'] if m['type'] == 'link')['value'] == 'https://example.com/a?b=1&c=2'
    assert 'footnote' in kinds and kinds.count('image') == 3
    target = next(b for b in back['blocks'] if b['text'].startswith('Target of an'))
    assert target['text'] == 'Target of an internal link, with a note.'  # note numbers are not text
    notes = [
        b for b in back['blocks'] if b['type'] == 'footnote' and b['attrs'].get('reference') == target['id']
    ]
    assert [n['text'] for n in notes] == [
        'First note, with a *marked* word.',
        'Second note on the same paragraph.',
    ]
    assert [n['attrs']['offset'] for n in notes] == [6, 14]
    table = next(b for b in back['blocks'] if b['type'] == 'table')
    assert [c['text'] for c in table['rows'][0]['cells']] == ['Site', 'Count', 'Note']
    alt = [b['attrs']['alt'] for b in back['blocks'] if b['type'] == 'image']
    assert 'A blue square standing in for a photograph' in alt
    assert any(b['attrs'].get('decorative') for b in back['blocks'] if b['type'] == 'image')
    assert report['format'] == 'epub' and report['direction'] == 'import'
    assert 'epub_internal_link' in codes(report)
    assert back['metadata']['import']['original_asset'] in back['assets']
    # The identifier survives, so re-exporting keeps the book's identity.
    again = tmp_path / 'again.epub'
    export_document(back, again)
    assert (
        xml(again, 'OEBPS/content.opf').find('o:metadata/d:identifier', NS).text
        == xml(path, 'OEBPS/content.opf').find('o:metadata/d:identifier', NS).text
    )
    assert epub.validate(again)['valid']


def test_import_skips_cover_page_and_navigation_and_reads_the_spine_in_order(tmp_path):
    doc = book(block('p1', 'First'), block('h2', 'Second', 'heading', level=1), block('p2', 'Third'))
    doc['metadata']['cover'] = add_asset(doc, PNG)
    path = tmp_path / 'o.epub'
    export_document(doc, path)
    back, _ = import_document(path)
    assert [b['text'] for b in back['blocks']] == ['First', 'Second', 'Third']


def test_import_follows_relative_paths_and_ignores_scripts(tmp_path):
    def container(rootfile):
        return (
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            f'<rootfiles><rootfile full-path="{rootfile}" media-type="application/oebps-package+xml"/></rootfiles></container>'
        )

    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="i">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Old Book</dc:title><dc:creator>A</dc:creator>'
        '<dc:creator>B</dc:creator><dc:language>en</dc:language><dc:identifier id="i">x</dc:identifier>'
        '<meta name="cover" content="img"/></metadata><manifest>'
        '<item id="c" href="text/c.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="img" href="pics/a.png" media-type="image/png"/></manifest>'
        '<spine><itemref idref="c"/></spine></package>'
    )
    page = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter</h1><p>Text <script>alert(1)</script>after</p>'
        '<img src="../pics/a.png" alt="a pixel"/><img src="../missing.png" alt="gone"/></body></html>'
    )
    path = tmp_path / 'old.epub'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', b'application/epub+zip', zipfile.ZIP_STORED)
        archive.writestr('META-INF/container.xml', container('OPS/book.opf'))
        archive.writestr('OPS/book.opf', opf)
        archive.writestr('OPS/text/c.xhtml', page)
        archive.writestr('OPS/pics/a.png', PNG)
    back, report = import_document(path)
    assert back['metadata']['author'] == 'A, B' and back['metadata']['cover'] in back['assets']
    images = [b for b in back['blocks'] if b['type'] == 'image']
    assert images[0]['attrs']['asset'] in back['assets'] and images[0]['attrs']['alt'] == 'a pixel'
    assert 'asset' not in images[1]['attrs']
    assert 'alert' not in ' '.join(b['text'] for b in back['blocks'])
    assert {'html_inactive_content', 'external_image'} <= set(codes(report))
    assert any(loss['target'].startswith('OPS/text/c.xhtml') for loss in report['losses'])


@pytest.fixture
def exported(tmp_path):
    path = tmp_path / 'ok.epub'
    export_document(book(block('p1', 'Text.')), path)
    return path


def test_hostile_archives_are_rejected_with_the_html_import_limits(exported, tmp_path):
    def attempt(edit, name='hostile.epub'):
        target = rewrite(exported, tmp_path / name, edit)
        with pytest.raises(ByaxError) as error:
            import_document(target)
        return error.value.code

    assert attempt(lambda f: f.update({'../evil.xhtml': b'x'})) == 'unsafe_archive'
    assert attempt(lambda f: f.update({'/abs.xhtml': b'x'})) == 'unsafe_archive'
    assert (
        attempt(lambda f: f.update({'META-INF/container.xml': b'<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>'}))
        == 'unsafe_xml'
    )
    assert (
        attempt(
            lambda f: f.update({'OEBPS/content.opf': b'<!DOCTYPE p [<!ENTITY a "b">]><package>&a;</package>'})
        )
        == 'unsafe_xml'
    )
    assert (
        attempt(
            lambda f: f.update(
                {'OEBPS/text/chapter-001.xhtml': b'<!DOCTYPE h [<!ENTITY a "b">]><html>&a;</html>'}
            )
        )
        == 'unsafe_xml'
    )
    assert attempt(lambda f: f.pop('META-INF/container.xml')) == 'invalid_input'
    assert (
        attempt(
            lambda f: f.update(
                {
                    'META-INF/container.xml': b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="../x.opf"/></rootfiles></container>'
                }
            )
        )
        == 'invalid_input'
    )
    assert attempt(lambda f: f.update({'OEBPS/bomb.xhtml': b'0' * (40 * 1024 * 1024)})) == 'resource_limit'
    drm = (
        b'<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:e="http://www.w3.org/2001/04/xmlenc#">'
        b'<e:EncryptedData><e:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/></e:EncryptedData></encryption>'
    )
    assert attempt(lambda f: f.update({'META-INF/encryption.xml': drm})) == 'encrypted_input'
    (tmp_path / 'junk.epub').write_bytes(b'PK not really a zip')
    with pytest.raises(ByaxError) as error:
        import_document(tmp_path / 'junk.epub')
    assert error.value.code == 'invalid_input'


def test_oversized_spine_and_nested_input_are_bounded(exported, tmp_path, monkeypatch):
    monkeypatch.setattr(epub, 'MAX_SPINE', 1)

    def many(files):
        opf = files['OEBPS/content.opf']
        files['OEBPS/content.opf'] = opf.replace(b'</spine>', b'<itemref idref="chapter-001"/></spine>')

    target = rewrite(exported, tmp_path / 'many.epub', many)
    with pytest.raises(ByaxError) as error:
        import_document(target)
    assert error.value.code == 'resource_limit'
    flat = rewrite(
        exported,
        tmp_path / 'deep.epub',
        lambda f: f.update(
            {
                'OEBPS/text/chapter-001.xhtml': b'<html><body>'
                + b'<div>' * 5000
                + b'x'
                + b'</div>' * 5000
                + b'</body></html>'
            }
        ),
    )
    monkeypatch.setattr(epub, 'MAX_SPINE', 5000)
    back, _ = import_document(flat)
    assert any('x' in b['text'] for b in back['blocks'])


def test_epub_cannot_overwrite_its_own_imported_original(exported):
    back, _ = import_document(exported)
    with pytest.raises(ByaxError) as error:
        export_document(back, exported)
    assert error.value.code == 'conflict'


# --- interfaces --------------------------------------------------------------------------------


def test_cli_and_mcp_export_epub(tmp_path):
    from byax.cli import execute
    from byax.mcp import handle
    from byax.storage import save_document

    source = tmp_path / 'doc.byax'
    save_document(book(block('h1', 'Start', 'heading', level=1), block('p1', 'Body')), source)
    result = execute('export', {'path': str(source), 'out': str(tmp_path / 'cli.epub')})
    assert result['format'] == 'epub' and result['structure_check'] == 'passed'
    assert epub.validate(tmp_path / 'cli.epub')['valid']
    dry = execute('export', {'path': str(source), 'out': str(tmp_path / 'never.epub'), 'dry_run': True})
    assert dry['preview'] and not (tmp_path / 'never.epub').exists()
    rpc = handle(
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {
                'name': 'byax_export',
                'arguments': {'path': str(source), 'out': str(tmp_path / 'mcp.epub')},
            },
        }
    )
    assert not rpc['result'].get('isError') and epub.validate(tmp_path / 'mcp.epub')['valid']
    imported = execute('convert', {'path': str(tmp_path / 'cli.epub'), 'out': str(tmp_path / 'again.odt')})
    assert imported['import_report']['format'] == 'epub'
    caps = execute('capabilities', {})
    assert 'epub' in caps['formats']
    json.dumps(result)  # the report is plain JSON


def test_library_entry_point_matches_export_document(tmp_path):
    doc = SCRIPT.rich_document()
    one = epub.export_epub(doc, tmp_path / 'a.epub', {'split_level': 2})
    two = export_document(doc, tmp_path / 'b.epub', profile={'epub': {'split_level': 2}})
    assert one == {**two, 'outputs': one['outputs']}
    assert (tmp_path / 'a.epub').read_bytes() == (tmp_path / 'b.epub').read_bytes()
    assert not (tmp_path / 'exists.epub').exists()
    epub.export_epub(doc, tmp_path / 'exists.epub')
    with pytest.raises(ByaxError) as error:
        epub.export_epub(doc, tmp_path / 'exists.epub', overwrite=False)
    assert error.value.code == 'conflict'


def test_io_bytes_validation_helper_accepts_file_objects(good):
    assert epub.validate(io.BytesIO(good.read_bytes()))['valid']


def test_gui_lists_epub_in_the_export_menus_and_exports_and_opens_it(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QFileDialog

    from byax.gui import FILE_FILTER, MainWindow

    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.doc = book(block('h1', 'Chapter', 'heading', level=1), block('p1', 'Words.'))
    window.refresh(0)
    ribbon = [a for a in window.ribbon_actions['export'].menu().actions() if a.data() == 'epub']
    assert [a.text() for a in ribbon] == ['EPUB (.epub)…']
    assert any(a.data() == 'epub' for a in window.export_as_menu.actions())
    assert '*.epub' in FILE_FILTER
    assert window.set_file_view('epub') and 'EPUB' in window.view_status.text()
    assert window.set_file_view('byax')
    target = tmp_path / 'gui.epub'
    seen = []
    monkeypatch.setattr(
        QFileDialog, 'getSaveFileName', lambda *a, **k: seen.append(a[2:4]) or (str(target), '')
    )
    reports = []
    monkeypatch.setattr(window, 'report', lambda title, result, art=None: reports.append(result))
    ribbon[0].trigger()
    qtbot.waitUntil(lambda: bool(reports), timeout=30000)
    QApplication.processEvents()
    assert seen[0][0].endswith('.epub') and 'EPUB e-book (*.epub)' in seen[0][1]
    assert reports[0]['format'] == 'epub' and epub.validate(target)['valid']
    window.dirty = False
    assert window.open_path(target)
    assert [b['text'] for b in window.doc['blocks']] == ['Chapter', 'Words.']
    window.dirty = False


def test_document_properties_dialog_chooses_the_cover(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QComboBox, QDialog

    from byax.gui import MainWindow

    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.IniFormat)
    monkeypatch.setattr('byax.gui.QSettings', lambda *_: settings)
    window = MainWindow()
    qtbot.addWidget(window)
    window.doc = book(block('p1', 'Text.'))
    key = add_asset(window.doc, PNG)
    add_asset(window.doc, b'font bytes', 'font/ttf')  # only embeddable image types are offered
    window.refresh(0)

    def choose(dialog):
        box = dialog.findChild(QComboBox)
        assert box.accessibleName() == 'EPUB cover image'
        assert [box.itemData(i) for i in range(box.count())] == ['', key]
        box.setCurrentIndex(1)
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, 'exec', choose)
    window.document_properties()
    assert window.doc['metadata']['cover'] == key
    report = export_document(window.doc, tmp_path / 'cover.epub')
    assert report['epub']['cover'].startswith('OEBPS/images/')
    window.dirty = False


def test_options_must_be_an_object_and_a_cover_block_without_an_asset_is_not_swallowed(tmp_path):
    with pytest.raises(ByaxError, match='must be an object'):
        export_document(book(), tmp_path / 'a.epub', profile={'epub': ['split_level']})
    doc = book(block('p1', 'Text.'))
    doc['blocks'].append(block('c', kind='image', alt='Not embedded', cover=True))
    report = export_document(doc, tmp_path / 'b.epub')
    assert [loss['target'] for loss in report['losses'] if loss['code'] == 'image_not_embedded'] == ['c']
    assert 'Not embedded' in members(tmp_path / 'b.epub')['OEBPS/text/chapter-001.xhtml'].decode()


def test_a_repeated_image_is_read_once_and_remote_css_is_flagged(good, tmp_path):
    page = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>x</p>'
        + '<img src="a.png" alt="a"/>' * 50
        + '</body></html>'
    )
    opf = (
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="i">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="i">x</dc:identifier></metadata>'
        '<manifest><item id="c" href="c.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="c"/></spine></package>'
    )
    container = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="o.opf"/></rootfiles></container>'
    )
    path = tmp_path / 'many.epub'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', b'application/epub+zip', zipfile.ZIP_STORED)
        archive.writestr('META-INF/container.xml', container)
        archive.writestr('o.opf', opf)
        archive.writestr('c.xhtml', page)
        archive.writestr('a.png', PNG)
    back, _ = import_document(path)
    assert sum(b['type'] == 'image' for b in back['blocks']) == 50
    assert len([k for k, a in back['assets'].items() if a['mime'] == 'image/png']) == 1
    css = members(good)['OEBPS/styles/book.css'] + b'\n@font-face { src: url("http://example.com/f.ttf") }'
    broken_css = rewrite(good, tmp_path / 'css.epub', lambda f: f.update({'OEBPS/styles/book.css': css}))
    assert 'remote_resource' in {e['code'] for e in epub.validate(broken_css)['errors']}
