"""Exchange fidelity fixtures: review/fields/notes, RTF named styles, non-Latin text and located losses."""

import base64
import os
import shutil
import subprocess
import zipfile

import pytest

from byax.engine import apply
from byax.errors import ByaxError
from byax.exchange import conversion_preview, export_document, import_document
from byax.model import new_document, validate_document

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
MIXED = 'Ελληνικά עברית العربية é 文字 👩‍👩‍👧'
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=='
)


def _docx(path, body, *, footnotes=None, comments=None, numbering=None):
    types = [
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    ]
    rels = []
    parts = {}
    for name, xml, kind, ctype in (
        ('footnotes', footnotes, 'footnotes', 'footnotes'),
        ('comments', comments, 'comments', 'comments'),
        ('numbering', numbering, 'numbering', 'numbering'),
    ):
        if xml is not None:
            parts[f'word/{name}.xml'] = xml
            types.append(
                f'<Override PartName="/word/{name}.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{ctype}+xml"/>'
            )
            rels.append(f'<Relationship Id="r{name}" Type="{R}/{kind}" Target="{name}.xml"/>')
    rels.append(
        f'<Relationship Id="rlink" Type="{R}/hyperlink" Target="https://example.com/x" TargetMode="External"/>'
    )
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>' + ''.join(types) + '</Types>',
        )
        archive.writestr(
            '_rels/.rels',
            f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            'word/_rels/document.xml.rels',
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + ''.join(rels)
            + '</Relationships>',
        )
        archive.writestr(
            'word/document.xml',
            f'<?xml version="1.0"?><w:document xmlns:w="{W}" xmlns:r="{R}" '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body>'
            + body
            + '</w:body></w:document>',
        )
        for name, xml in parts.items():
            archive.writestr(name, xml)
    return path


def _p(inner, ppr=''):
    return f'<w:p>{ppr}{inner}</w:p>'


def _r(text):
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def test_docx_review_fields_notes_bookmarks_and_textboxes_are_editable(tmp_path):
    body = (
        _p(
            _r('Keep ')
            + f'<w:ins w:id="1" w:author="Ann">{_r("added")}</w:ins>'
            + '<w:del w:id="2" w:author="Bob"><w:r><w:delText>removed</w:delText></w:r></w:del>'
            + _r(' end')
        )
        + _p(
            '<w:bookmarkStart w:id="0" w:name="intro"/>'
            + '<w:commentRangeStart w:id="5"/>'
            + _r('Commented')
            + '<w:commentRangeEnd w:id="5"/><w:r><w:commentReference w:id="5"/></w:r>'
            + _r(' text')
            + '<w:r><w:footnoteReference w:id="7"/></w:r><w:bookmarkEnd w:id="0"/>'
        )
        + _p(
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> DATE \\@ "yyyy" </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            + _r('2024')
            + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            + f'<w:fldSimple w:instr=" HYPERLINK &quot;https://example.org&quot; ">{_r("site")}</w:fldSimple>'
            + '<w:hyperlink r:id="rlink">'
            + _r(' rel')
            + '</w:hyperlink>'
        )
        + _p(
            _r('Anchor')
            + '<w:r><w:drawing><mc:AlternateContent><mc:Choice><w:txbxContent>'
            + _p(_r('Boxed words'))
            + '</w:txbxContent></mc:Choice><mc:Fallback><w:txbxContent>'
            + _p(_r('Boxed words'))
            + '</w:txbxContent></mc:Fallback></mc:AlternateContent></w:drawing></w:r>'
        )
        + _p(_r('Numbered'), '<w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="3"/></w:numPr></w:pPr>')
    )
    footnotes = (
        f'<w:footnotes xmlns:w="{W}"><w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:t>---</w:t></w:r></w:p></w:footnote>'
        f'<w:footnote w:id="7">{_p(_r("Note body"))}</w:footnote></w:footnotes>'
    )
    comments = f'<w:comments xmlns:w="{W}"><w:comment w:id="5" w:author="Cy">{_p(_r("Check this"))}</w:comment></w:comments>'
    numbering = (
        f'<w:numbering xmlns:w="{W}"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl>'
        '<w:lvl w:ilvl="1"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum><w:num w:numId="3"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
    )
    doc, report = import_document(
        _docx(tmp_path / 'review.docx', body, footnotes=footnotes, comments=comments, numbering=numbering)
    )
    validate_document(doc)
    texts = [b['text'] for b in doc['blocks']]
    assert texts[0] == 'Keep added end'
    change = doc['review']['tracked_changes'][0]
    assert (
        change['before'] == 'Keep removed end'
        and change['after'] == 'Keep added end'
        and change['authors'] == ['Ann', 'Bob']
    )
    commented = doc['blocks'][1]
    comment = doc['review']['comments'][0]
    assert (
        comment['text'] == 'Check this'
        and commented['text'][comment['start'] : comment['end']] == 'Commented'
    )
    note = doc['blocks'][2]
    assert (
        note['type'] == 'footnote'
        and note['text'] == 'Note body'
        and note['attrs']['reference'] == commented['id']
    )
    assert doc['settings']['bookmarks']['intro'] == {'target': commented['id'], 'offset': 0}
    fields = next(b for b in doc['blocks'] if b['text'].startswith('2024'))
    assert fields['attrs']['fields'][0]['instruction'].startswith('DATE')
    assert {m['value'] for m in fields['marks'] if m['type'] == 'link'} == {
        'https://example.org',
        'https://example.com/x',
    }
    assert texts.count('Boxed words') == 1
    numbered = next(b for b in doc['blocks'] if b['text'] == 'Numbered')
    assert numbered['attrs']['list'] == 'ordered' and numbered['attrs']['level'] == 2
    codes = {loss['code'] for loss in report['losses']}
    assert {'docx_field', 'docx_textbox'} <= codes
    assert report['content_audit']['status'] == 'complete'
    # Imported review is native and actionable.
    rejected, _ = apply(doc, {'operations': [{'type': 'reject_change', 'target': change['id']}]})
    assert rejected['blocks'][0]['text'] == 'Keep removed end'


def test_docx_unrepresented_text_is_reported_at_its_location(tmp_path):
    body = _p(_r('Visible')) + '<w:customBlock><w:p><w:r><w:t>Hidden island</w:t></w:r></w:p></w:customBlock>'
    _doc, report = import_document(_docx(tmp_path / 'odd.docx', body))
    assert report['content_audit']['status'] == 'omissions_reported'
    assert any(
        loss['code'] == 'content_omitted' and loss['target'] == 'word/document.xml#p2'
        for loss in report['losses']
    )


def test_docx_comments_and_bookmarks_round_trip(tmp_path):
    doc = new_document('Review', 'Alpha beta')
    target = doc['blocks'][0]['id']
    doc['review']['comments'].append(
        {'id': 'comment_1', 'target': target, 'text': 'Consider gamma', 'author': 'Ed', 'status': 'open'}
    )
    doc['settings']['bookmarks'] = {'start': {'target': target, 'offset': 0}}
    path = tmp_path / 'review.docx'
    report = export_document(doc, path)
    assert not any(loss['code'] == 'review_not_exported' for loss in report['losses'])
    loaded, _ = import_document(path)
    assert loaded['review']['comments'][0]['text'] == 'Consider gamma'
    assert loaded['review']['comments'][0]['author'] == 'Ed'
    assert loaded['settings']['bookmarks']['start']['target'] == loaded['blocks'][0]['id']


def test_odt_annotations_bookmarks_fields_and_tracked_changes(tmp_path):
    office = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'
    text = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
    content = (
        f'<office:document-content xmlns:office="{office}" xmlns:text="{text}" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<office:body><office:text><text:tracked-changes>'
        '<text:changed-region text:id="c1"><text:insertion/></text:changed-region>'
        '<text:changed-region text:id="c2"><text:deletion><text:p>gone</text:p></text:deletion></text:changed-region>'
        '</text:tracked-changes>'
        '<text:p><text:bookmark text:name="mark"/>Start <text:change-start text:change-id="c1"/>new<text:change-end text:change-id="c1"/>'
        '<text:change text:change-id="c2"/> <office:annotation office:name="a1"><dc:creator>Di</dc:creator><text:p>Remark</text:p>'
        '</office:annotation>noted<office:annotation-end office:name="a1"/> page <text:page-number>3</text:page-number></text:p>'
        '</office:text></office:body></office:document-content>'
    )
    path = tmp_path / 'review.odt'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', 'application/vnd.oasis.opendocument.text')
        archive.writestr('content.xml', content)
    doc, report = import_document(path)
    validate_document(doc)
    block = doc['blocks'][0]
    assert block['text'] == 'Start new noted page 3'
    assert 'Remark' not in block['text']
    comment = doc['review']['comments'][0]
    assert comment['author'] == 'Di' and block['text'][comment['start'] : comment['end']] == 'noted'
    assert doc['settings']['bookmarks']['mark']['target'] == block['id']
    assert block['attrs']['fields'][0]['instruction'] == 'page-number'
    assert doc['review']['tracked_changes'][0]['before'] == 'Start gone noted page 3'
    assert report['content_audit']['status'] == 'complete'


def test_rtf_named_styles_regions_notes_fields_pictures_and_annotations(tmp_path):
    picture = PNG.hex()
    source = (
        '{\\rtf1\\ansi{\\fonttbl{\\f0 Arial;}}{\\stylesheet{\\s0 Normal;}{\\s1\\b\\fs32\\sbasedon0 heading 1;}{\\s2\\i Quote Style;}}'
        '\\paperw12240\\paperh15840\\margl1440'
        '{\\header \\pard\\plain Running head\\par}{\\footer \\pard\\plain Page foot\\par}'
        '\\pard\\plain\\s1 Title text\\par'
        '\\pard\\plain\\s2 Quoted{\\super\\chftn}{\\footnote\\pard\\plain{\\super\\chftn} Footnote words} words'
        '{\\*\\atnid A}{\\*\\annotation\\pard\\plain Annotated remark}\\par'
        '\\pard\\plain {\\field{\\*\\fldinst HYPERLINK "https://example.net"}{\\fldrslt link}} and '
        '{\\field{\\*\\fldinst PAGE}{\\fldrslt 7}}\\par'
        '{\\*\\shppict{\\pict\\pngblip\\picw1\\pich1 '
        + picture
        + '}}{\\nonshppict{\\pict\\wmetafile8 0102}}\\par '
        'Caf\\u233? \\é bad\\par}'
    )
    path = tmp_path / 'styled.rtf'
    path.write_text(source, encoding='utf-8')
    doc, report = import_document(path)
    validate_document(doc)
    title, quoted = doc['blocks'][0], doc['blocks'][1]
    assert title['type'] == 'heading' and title['attrs']['level'] == 1 and title['style'] == 'heading 1'
    assert doc['styles']['heading 1']['bold'] and doc['styles']['heading 1']['font_size'] == 16
    assert doc['styles']['heading 1']['based_on'] == 'Normal'
    assert quoted['style'] == 'Quote Style' and quoted['text'] == 'Quoted words'
    assert doc['blocks'][2] == {**doc['blocks'][2], 'type': 'footnote', 'text': 'Footnote words'}
    assert doc['blocks'][2]['attrs']['reference'] == quoted['id']
    assert doc['layout']['header'] == 'Running head' and doc['layout']['footer'] == 'Page foot'
    assert doc['layout']['width'] == 612 and doc['layout']['margins']['left'] == 72
    assert doc['review']['comments'][0]['text'] == 'Annotated remark'
    fields = doc['blocks'][3]
    assert fields['text'] == 'link and 7'
    assert any(m['type'] == 'link' and m['value'] == 'https://example.net' for m in fields['marks'])
    assert fields['attrs']['fields'][0]['instruction'] == 'PAGE'
    image = next(b for b in doc['blocks'] if b['type'] == 'image')
    assert base64.b64decode(doc['assets'][image['attrs']['asset']]['data']) == PNG
    assert doc['blocks'][-1]['text'].startswith('Café')
    assert {'rtf_field', 'rtf_picture', 'rtf_page_region'} <= {loss['code'] for loss in report['losses']}


def test_rtf_export_round_trips_named_styles_tables_notes_and_images(tmp_path):
    doc = new_document('Styled', 'Heading words')
    doc['styles']['Callout'] = {'based_on': 'body', 'bold': True, 'font_size': 14}
    doc['blocks'][0].update(type='heading', style='heading1', attrs={'level': 1})
    doc['blocks'].append(
        {'id': 'p_callout', 'type': 'paragraph', 'text': MIXED, 'style': 'Callout', 'marks': [], 'attrs': {}}
    )
    doc['blocks'].append(
        {
            'id': 'note_1',
            'type': 'footnote',
            'text': 'A note',
            'style': 'body',
            'marks': [],
            'attrs': {'reference': 'p_callout'},
        }
    )
    doc['blocks'].append(
        {
            'id': 't1',
            'type': 'table',
            'text': '',
            'style': 'body',
            'marks': [],
            'attrs': {},
            'rows': [{'id': 'r1', 'cells': [{'id': 'c1', 'text': 'A'}, {'id': 'c2', 'text': '文'}]}],
        }
    )
    key = __import__('hashlib').sha256(PNG).hexdigest()
    doc['assets'][key] = {'data': base64.b64encode(PNG).decode(), 'mime': 'image/png', 'name': 'dot.png'}
    doc['blocks'].append(
        {
            'id': 'img',
            'type': 'image',
            'text': '',
            'style': 'body',
            'marks': [],
            'attrs': {'asset': key, 'alt': 'dot'},
        }
    )
    doc['layout']['header'] = 'Head'
    path = tmp_path / 'styled.rtf'
    report = export_document(doc, path)
    codes = {loss['code'] for loss in report['losses']}
    assert not codes & {
        'named_styles',
        'table_flattened',
        'structural_object',
        'image_not_embedded',
        'heading_flattened',
    }
    loaded, _ = import_document(path)
    assert loaded['blocks'][0]['type'] == 'heading'
    callout = next(b for b in loaded['blocks'] if b['text'] == MIXED)
    assert callout['style'] == 'Callout' and loaded['styles']['Callout']['based_on'] == 'body'
    assert any(
        b['type'] == 'footnote' and b['text'] == 'A note' and b['attrs']['reference'] == callout['id']
        for b in loaded['blocks']
    )
    table = next(b for b in loaded['blocks'] if b['type'] == 'table')
    assert [c['text'] for c in table['rows'][0]['cells']] == ['A', '文']
    assert key in loaded['assets']
    assert loaded['layout']['header'] == 'Head'


@pytest.mark.parametrize('fmt', ['txt', 'md', 'html', 'rtf', 'docx', 'odt'])
def test_non_latin_bidi_and_combining_text_survives_import_export_and_pdf(tmp_path, fmt):
    doc = new_document('Mixed', MIXED)
    doc['blocks'].append(
        {
            'id': 'h1',
            'type': 'heading',
            'text': 'שלום عالم',
            'style': 'heading1',
            'marks': [],
            'attrs': {'level': 1},
        }
    )
    path = tmp_path / ('mixed.' + fmt)
    export_document(doc, path)
    imported, report = import_document(path)
    texts = [b.get('text', '') for b in imported['blocks']]
    assert MIXED in texts and 'שלום عالم' in texts
    validate_document(imported)
    native = tmp_path / ('mixed-' + fmt + '.byax')
    export_document(imported, native)
    reloaded, _ = import_document(native)
    assert MIXED in [b.get('text', '') for b in reloaded['blocks']]
    pdf = export_document(reloaded, tmp_path / ('mixed-' + fmt + '.pdf'))
    assert pdf['execution'] == 'completed' and (tmp_path / ('mixed-' + fmt + '.pdf')).stat().st_size > 0
    assert report['content_audit']['status'] in ('complete', 'not_audited')


def test_conversion_preview_writes_nothing_and_lists_located_losses(tmp_path):
    doc = new_document('Preview', 'Underlined')
    doc['blocks'][0]['marks'] = [{'start': 0, 'end': 5, 'type': 'underline', 'value': True}]
    before = set(os.listdir(tmp_path))
    report = conversion_preview(doc, 'md')
    assert report['preview'] and report['outputs'] == [] and report['review'] == 'required'
    assert any(loss['target'] == doc['blocks'][0]['id'] for loss in report['markdown']['unrepresentable'])
    assert set(os.listdir(tmp_path)) == before


def test_export_overwrite_refusal_and_interrupted_writes_leave_no_partial_output(tmp_path, monkeypatch):
    doc = new_document('Out', 'Body')
    target = tmp_path / 'out.docx'
    export_document(doc, target)
    original = target.read_bytes()
    with pytest.raises(ByaxError) as error:
        export_document(doc, target, overwrite=False)
    assert error.value.code == 'conflict' and target.read_bytes() == original
    from byax import exchange

    def broken(candidate, stage):
        stage.write_bytes(b'PK\x03\x04partial')
        raise OSError('disk full')

    monkeypatch.setattr(exchange, '_export_docx', broken)
    with pytest.raises(OSError):
        export_document(doc, target)
    with pytest.raises(OSError):
        export_document(doc, tmp_path / 'new.docx')
    assert target.read_bytes() == original and not (tmp_path / 'new.docx').exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith('.byax-export-')]


def test_malformed_xml_is_invalid_input_not_unsafe(tmp_path):
    path = tmp_path / 'broken.docx'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document><w:body>')
    with pytest.raises(ByaxError) as error:
        import_document(path)
    assert error.value.code == 'invalid_input'
    odt = tmp_path / 'broken.odt'
    with zipfile.ZipFile(odt, 'w') as archive:
        archive.writestr('content.xml', '<office:document-content')
    with pytest.raises(ByaxError) as error:
        import_document(odt)
    assert error.value.code == 'invalid_input'


def test_rtf_backslash_before_non_ascii_letter_is_structured(tmp_path):
    path = tmp_path / 'bad.rtf'
    path.write_bytes(b'{\\rtf1\\ansi Hello \\\xc3\xa9 world}')
    doc, _ = import_document(path)
    assert doc['blocks'][0]['text'].startswith('Hello')


@pytest.mark.skipif(
    not (shutil.which('soffice') or os.environ.get('BYAX_LIBREOFFICE')), reason='LibreOffice is not installed'
)
def test_libreoffice_doc_adapter_converts_historical_word_file(tmp_path, monkeypatch):
    executable = os.environ.get('BYAX_LIBREOFFICE') or shutil.which('soffice')
    source = new_document('Legacy', MIXED)
    source['blocks'].append(
        {
            'id': 'h',
            'type': 'heading',
            'text': 'Legacy heading',
            'style': 'heading1',
            'marks': [],
            'attrs': {'level': 1},
        }
    )
    docx = tmp_path / 'legacy.docx'
    export_document(source, docx)
    profile = tmp_path / 'profile'
    result = subprocess.run(
        [
            executable,
            f'-env:UserInstallation={profile.as_uri()}',
            '--headless',
            '--convert-to',
            'doc',
            '--outdir',
            str(tmp_path),
            str(docx),
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    legacy = tmp_path / 'legacy.doc'
    if result.returncode or not legacy.is_file():
        pytest.skip('LibreOffice could not produce a .doc fixture in this environment')
    monkeypatch.setenv('BYAX_LIBREOFFICE', executable)
    doc, report = import_document(legacy)
    texts = [b['text'] for b in doc['blocks']]
    assert MIXED in texts and 'Legacy heading' in texts
    assert any(loss['code'] == 'doc_adapter' for loss in report['losses'])
    assert report['adapter']['name'] == 'libreoffice' and report['adapter']['version']
    assert doc['metadata']['import']['format'] == 'doc'
