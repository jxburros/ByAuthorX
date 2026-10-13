"""Hostile-input and authoritative-state regression tests using bounded fixtures."""
import base64
import copy
import hashlib
import io
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

from byax.errors import ByaxError
from byax.model import new_document, validate_document
from byax.storage import DocumentLock, load_document, save_document


def archive_bytes(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return stream.getvalue()


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'assets/../../outside', 'assets\\outside', 'C:/outside'])
def test_native_rejects_unsafe_members_without_writing(name, tmp_path):
    path = tmp_path / 'unsafe.byax'
    path.write_bytes(archive_bytes([(name, b'untrusted')]))
    with pytest.raises(ByaxError):
        load_document(path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ['unsafe.byax']


def test_native_rejects_duplicate_members(tmp_path):
    path = tmp_path / 'duplicate.byax'
    with pytest.warns(UserWarning):
        path.write_bytes(archive_bytes([('manifest.json', b'{}'), ('manifest.json', b'{}')]))
    with pytest.raises(ByaxError):
        load_document(path)


def test_native_deep_json_has_structured_failure(tmp_path):
    path = tmp_path / 'deep.byax'
    path.write_bytes(archive_bytes([('manifest.json', '[' * 1500 + '0' + ']' * 1500), ('document.json', '{}')]))
    with pytest.raises(ByaxError):
        load_document(path)


def test_exchange_xml_external_entities_never_resolve(tmp_path):
    from byax.exchange import import_document
    secret = tmp_path / 'private.txt'
    secret.write_text('PRIVATE-TOKEN-DO-NOT-LOAD')
    xml = f'<!DOCTYPE x [<!ENTITY private SYSTEM "{secret.as_uri()}">]><x>&private;</x>'
    path = tmp_path / 'hostile.odt'
    path.write_bytes(archive_bytes([('content.xml', xml)]))
    with pytest.raises(ByaxError) as failure:
        import_document(path)
    assert secret.read_text() == 'PRIVATE-TOKEN-DO-NOT-LOAD'
    assert 'PRIVATE-TOKEN' not in str(failure.value)


def test_exchange_archive_expansion_budget_is_checked_before_parse(tmp_path, monkeypatch):
    from byax import exchange
    path = tmp_path / 'expanding.odt'
    path.write_bytes(archive_bytes([('content.xml', '<root>' + 'x' * 2048 + '</root>')]))
    monkeypatch.setattr(exchange, 'MAX_EXPANDED', 1000)
    with pytest.raises(ByaxError):
        exchange.import_document(path)


def test_html_has_no_network_or_active_content_execution(tmp_path, monkeypatch):
    import socket

    from byax.exchange import import_document
    def reject_network(*args, **kwargs):
        raise AssertionError('Import attempted a network connection')
    monkeypatch.setattr(socket, 'create_connection', reject_network)
    path = tmp_path / 'active.html'
    path.write_text('<html><head><script>fetch("https://example.invalid");</script></head>'
        '<body><p>Visible prose.</p><img src="https://example.invalid/private.png" alt="External image">'
        '<script>RUN_THIS_INSTRUCTION</script><p><a href="javascript:alert(1)">Safe label</a></p></body></html>')
    doc, report = import_document(path)
    text = '\n'.join(b.get('text', '') for b in doc['blocks'])
    assert 'Visible prose.' in text and 'Safe label' in text
    assert 'RUN_THIS_INSTRUCTION' not in text
    assert all(m.get('value') != 'javascript:alert(1)' for b in doc['blocks'] for m in b.get('marks', []))
    assert any(loss['code'] == 'external_image' for loss in report['losses'])
    assert any(loss['code'] == 'html_inactive_content' for loss in report['losses'])


def test_export_cannot_bypass_an_open_document_lock(tmp_path):
    path = tmp_path / 'private.byax'
    doc = new_document(text='Unsaved GUI state must be authoritative')
    save_document(doc, path)
    before = path.read_bytes()
    code = '''
import sys
from byax.exchange import export_document
from byax.model import new_document
from byax.errors import ByaxError
try:
    export_document(new_document(text='Conflicting external edit'), sys.argv[1], format='byax')
except ByaxError as exc:
    print(exc.code)
    sys.exit(exc.exit_code)
'''
    with DocumentLock(path):
        result = subprocess.run([sys.executable, '-c', code, str(path)], check=False, capture_output=True, text=True, timeout=15)
    assert result.returncode == 3, result.stdout + result.stderr
    assert path.read_bytes() == before


def test_source_pack_bounds_total_before_reading_all_members(tmp_path, monkeypatch):
    from byax import storage
    source = tmp_path / 'source'
    source.mkdir()
    for name in ('first.json', 'second.json', 'third.json'):
        (source / name).write_bytes(b'123456')
    monkeypatch.setattr(storage, 'MAX_PACKAGE_BYTES', 10)
    original_read = Path.read_bytes
    reads = []
    def counted_read(path):
        if path.parent == source:
            reads.append(path.name)
        return original_read(path)
    monkeypatch.setattr(Path, 'read_bytes', counted_read)
    with pytest.raises(ByaxError):
        storage.pack(source, tmp_path / 'output.byax')
    assert len(reads) <= 1, 'Aggregate limit must be enforced before reading additional members'


@pytest.fixture(scope='module')
def qt_app():
    from byax.layout import ensure_app
    return ensure_app()


def test_noop_native_gui_mapping_has_no_document_operations(qt_app):
    from byax.engine import apply
    from byax.gui_editor import SemanticEditor
    from byax.layout import build_document
    doc = new_document(text='Heading\nBody text')
    doc['blocks'][0]['type'] = 'heading'
    doc['blocks'][0]['attrs'] = {'level': 1}
    doc, _ = apply(doc, {'operations': [{'type': 'insert_table', 'after': doc['blocks'][1]['id'], 'rows': 2, 'columns': 2}]})
    before = copy.deepcopy(doc)
    editor = SemanticEditor()
    editor.setDocument(build_document(doc))
    editor.capture_baseline()
    operations, tags = editor.collect_changes(doc)
    assert operations == []
    assert tags == []
    assert doc == before
    editor.close()


def test_typing_deletion_of_image_is_canonical_or_explicitly_rejected(qt_app):
    from PIL import Image

    from byax.gui_editor import SemanticEditor
    from byax.layout import build_document
    raw = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(raw, format='PNG')
    image = raw.getvalue()
    digest = hashlib.sha256(image).hexdigest()
    doc = new_document(text='Before image')
    doc['assets'][digest] = {'data': base64.b64encode(image).decode(), 'mime': 'image/png', 'name': 'pixel.png'}
    doc['blocks'].append({'id': 'image_target', 'type': 'image', 'text': '', 'style': 'body', 'marks': [], 'attrs': {'asset': digest, 'alt': 'Red square', 'width': 8, 'height': 8}})
    editor = SemanticEditor()
    editor.setDocument(build_document(doc))
    editor.capture_baseline()
    assert editor.navigate('image_target')
    cursor = editor.textCursor()
    cursor.deleteChar()
    try:
        operations, _ = editor.collect_changes(doc)
    except (ValueError, ByaxError):
        return  # Explicit rejection lets the GUI restore the authoritative model.
    assert any(op['type'] == 'delete_block' and op['target'] == 'image_target' for op in operations), 'Image vanished visually without any canonical operation or conflict'
    editor.close()


def test_render_oversized_page_rejects_before_allocating(qt_app, tmp_path):
    from byax.layout import render
    doc = new_document(text='A page must have a bounded bitmap allocation.')
    doc['layout'].update(width=1_000_000_000, height=1_000_000_000)
    with pytest.raises(ByaxError):
        render(doc, tmp_path / 'output')


def test_page_range_rejects_out_of_bounds_before_expansion(monkeypatch):
    import builtins

    from byax import layout
    def bounded_range(*args):
        actual = builtins.range(*args)
        if len(actual) > 1000:
            raise AssertionError('Out-of-bounds page range expanded before validation')
        return actual
    monkeypatch.setattr(layout, 'range', bounded_range, raising=False)
    with pytest.raises((ByaxError, ValueError)):
        layout._selected_pages('1-1000000000', 1)


def test_image_pixel_budget_rejects_header_before_decode(qt_app):
    # Tiny original PNG with a forged huge IHDR, using a valid CRC. It must be
    # rejected by dimension preflight before the decoder considers pixel data.
    from PIL import Image

    from byax.layout import build_document
    buffer = io.BytesIO()
    Image.new('RGB', (1, 1), 'white').save(buffer, format='PNG')
    raw = bytearray(buffer.getvalue())
    raw[16:24] = struct.pack('>II', 100_000, 100_000)
    raw[29:33] = struct.pack('>I', zlib.crc32(raw[12:29]) & 0xffffffff)
    digest = hashlib.sha256(raw).hexdigest()
    doc = new_document(text='Image safety')
    doc['assets'][digest] = {'data': base64.b64encode(raw).decode(), 'mime': 'image/png', 'name': 'oversized.png'}
    with pytest.raises(ByaxError):
        build_document(doc)


def test_invalid_imported_html_table_never_returns_unsavable_document(tmp_path):
    from byax.exchange import import_document
    path = tmp_path / 'uneven.html'
    path.write_text('<table><tr><td>A</td><td>B</td></tr><tr><td>C</td></tr></table>')
    try:
        doc, _report = import_document(path)
    except ByaxError:
        return  # Explicit rejection is preferable to pretending import succeeded.
    validate_document(doc)
    save_document(doc, tmp_path / 'imported.byax')


@pytest.mark.parametrize('kind', ['paragraph', 'table', 'image'])
def test_excluded_content_survives_unrelated_visible_typing(qt_app, kind):
    from byax.engine import apply
    from byax.gui_editor import SemanticEditor
    from byax.layout import build_document
    doc = new_document(text='Visible text\nPrivate alternate paragraph')
    hidden = doc['blocks'][1]
    hidden['type'] = kind
    if kind == 'table':
        hidden['rows'] = [{'id': 'hidden_row', 'cells': [{'id': 'hidden_cell', 'text': 'Unpublished table value', 'attrs': {}}]}]
    if kind == 'image':
        from PIL import Image
        image_buffer = io.BytesIO()
        Image.new('RGB', (1, 1), 'white').save(image_buffer, format='PNG')
        image_bytes = image_buffer.getvalue()
        digest = hashlib.sha256(image_bytes).hexdigest()
        doc['assets'][digest] = {'data': base64.b64encode(image_bytes).decode(), 'mime': 'image/png', 'name': 'hidden.png'}
        hidden['attrs']['asset'] = digest
        hidden['attrs']['alt'] = 'Hidden original artwork'
    hidden['attrs']['excluded'] = True
    editor = SemanticEditor()
    editor.setDocument(build_document(doc))
    editor.capture_baseline()
    assert editor.navigate(doc['blocks'][0]['id'])
    editor.insertPlainText('New ')
    operations, _tags = editor.collect_changes(doc)
    changed, _ = apply(doc, {'operations': operations})
    assert next((b for b in changed['blocks'] if b['id'] == hidden['id']), None) == hidden
    editor.close()


def test_unrenderable_character_has_located_font_finding(qt_app):
    from byax.layout import build_document, resolved_page_count
    doc = new_document(text='Missing character: \U0010ffff')
    qdoc = build_document(doc)
    resolved_page_count(qdoc, doc)
    assert any(f['code'] == 'missing_glyph' and f['target'] == doc['blocks'][0]['id'] for f in qdoc.byax_findings)
