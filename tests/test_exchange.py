import zipfile

import pytest

from byax.errors import ByaxError
from byax.exchange import export_document, import_document
from byax.model import new_document, validate_document


def test_markdown_roundtrip_preserves_source_and_local_edits(tmp_path):
    source = b'# Title\r\n\r\nA **bold** paragraph.\r\n\r\nSecond line.\r\n'
    original = tmp_path / 'source.md'
    original.write_bytes(source)
    doc, _report = import_document(original)
    validate_document(doc)
    assert doc['blocks'][1]['marks'][0]['type'] == 'bold'
    exported = tmp_path / 'export.md'
    export_document(doc, exported)
    assert exported.read_bytes() == source
    doc['blocks'][2]['text'] = 'Changed line.'
    export_document(doc, exported)
    assert exported.read_bytes() == source.replace(b'Second line.', b'Changed line.')
    assert original.read_bytes() == source
    with pytest.raises(ByaxError, match='preserve'):
        export_document(doc, original)


def test_markdown_semantic_table_lists_and_unicode(tmp_path):
    path = tmp_path / 'example.md'
    path.write_text('# مرحبا\n\n- first\n- second\n\n| A | B |\n|---|---|\n| é | 文 |\n', encoding='utf-8')
    doc, _ = import_document(path)
    assert doc['blocks'][1]['attrs']['list'] == 'bullet'
    table = doc['blocks'][-1]
    assert table['type'] == 'table'
    assert table['rows'][1]['cells'][1]['text'] == '文'
    validate_document(doc)


def test_html_does_not_import_active_content(tmp_path):
    path = tmp_path / 'input.html'
    path.write_text('<h1>Safe</h1><script>SECRET()</script><p>A <b>bold</b> <a href="javascript:alert(1)">bad</a></p><img src="https://example.invalid/a.png" alt="remote"><table><tr><th>H</th></tr><tr><td>cell</td></tr></table>')
    doc, report = import_document(path)
    assert all('SECRET' not in block.get('text', '') for block in doc['blocks'])
    assert not any(mark.get('type') == 'link' for b in doc['blocks'] for mark in b.get('marks', []))
    assert doc['blocks'][-1]['rows'][1]['cells'][0]['text'] == 'cell'
    assert any(item['code'] == 'html_inactive_content' for item in report['losses'])


@pytest.mark.parametrize('fmt', ['txt', 'md', 'html', 'rtf', 'docx', 'odt'])
def test_editable_exchange_retains_unicode_text(tmp_path, fmt):
    doc = new_document('Test', 'Hello café 文')
    path = tmp_path / ('output.' + fmt)
    report = export_document(doc, path)
    imported, result = import_document(path)
    assert 'Hello café 文' in '\n'.join(b.get('text', '') for b in imported['blocks'])
    assert report['validation'] == 'not_run'
    assert result['direction'] == 'import'
    validate_document(imported)


def test_zip_traversal_and_entities_rejected(tmp_path):
    path = tmp_path / 'unsafe.docx'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('../bad.xml', '<a/>')
    with pytest.raises(ByaxError) as error:
        import_document(path)
    assert error.value.code == 'unsafe_archive'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', '<!DOCTYPE x [<!ENTITY a "abc">]><x>&a;</x>')
    with pytest.raises(ByaxError) as error:
        import_document(path)
    assert error.value.code == 'unsafe_xml'


def test_failed_export_keeps_previous_output(tmp_path):
    target = tmp_path / 'out.txt'
    target.write_text('Original')
    with pytest.raises(UnicodeEncodeError):
        export_document(new_document('Title', '文'), target, profile={'encoding': 'ascii'})
    assert target.read_text() == 'Original'


def test_doc_requires_local_adapter(tmp_path, monkeypatch):
    path = tmp_path / 'old.doc'
    path.write_bytes(b'fake')
    monkeypatch.delenv('BYAX_LIBREOFFICE', raising=False)
    with pytest.raises(ByaxError) as error:
        import_document(path)
    assert error.value.code == 'adapter_unavailable'


@pytest.mark.parametrize('fmt', ['docx', 'odt'])
def test_rich_exchange_preserves_named_styles_marks_and_hyperlinks(tmp_path, fmt):
    doc = new_document('Rich', 'Bold linked words')
    doc['styles']['Special'] = {'based_on': 'body', 'font_family': 'DejaVu Sans', 'font_size': 16, 'bold': True}
    block = doc['blocks'][0]
    block['style'] = 'Special'
    block['marks'] = [{'start': 0, 'end': 4, 'type': 'bold', 'value': True}, {'start': 5, 'end': 11, 'type': 'link', 'value': 'https://example.com'}, {'start': 12, 'end': 17, 'type': 'italic', 'value': True}]
    path = tmp_path / ('rich.'+fmt)
    export_document(doc, path)
    loaded, _ = import_document(path)
    paragraph = loaded['blocks'][0]
    assert paragraph['text'] == block['text']
    assert 'Special' in loaded['styles']
    assert loaded['styles']['Special']['font_size'] == 16
    assert any(m['type'] == 'link' and m['value'] == 'https://example.com' for m in paragraph['marks'])
    assert any(m['type'] == 'italic' and m['value'] for m in paragraph['marks'])


def test_rtf_import_formats_tables_unicode_and_does_not_execute_objects(tmp_path):
    path = tmp_path / 'formatted.rtf'
    path.write_text(r'{\rtf1\ansi{\fonttbl{\f0 Arial;}}\f0\fs28 Plain {\b bold} and {\i italic} \u-10179?\u-8704?\par\trowd\cellx2000\cellx4000\intbl A\cell B\cell\row{\object\objdata SECRET}}')
    doc, report = import_document(path)
    paragraph = doc['blocks'][0]
    assert paragraph['text'] == 'Plain bold and italic 😀'
    assert any(m['type'] == 'bold' and paragraph['text'][m['start']:m['end']] == 'bold' for m in paragraph['marks'])
    assert doc['blocks'][1]['rows'][0]['cells'][1]['text'] == 'B'
    assert any(loss['code'] == 'rtf_object' for loss in report['losses'])
    assert 'SECRET' not in '\n'.join(b.get('text', '') for b in doc['blocks'])


def test_odt_preserves_footnote_association(tmp_path):
    doc = new_document('Notes', 'Reference')
    doc['blocks'].append({'id': 'note_test', 'type': 'footnote', 'text': 'Retained note', 'style': 'body', 'marks': [], 'attrs': {'reference': doc['blocks'][0]['id']}})
    path = tmp_path / 'notes.odt'
    export_document(doc, path)
    loaded, _ = import_document(path)
    note = next(b for b in loaded['blocks'] if b['type'] == 'footnote')
    assert note['text'] == 'Retained note'
    assert note['attrs']['reference'] == loaded['blocks'][0]['id']
