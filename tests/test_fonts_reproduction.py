"""Font identity/permissions, glyph-level fallback hashes and reproduction result states."""

import struct

import pytest

from byax import fonts
from byax.errors import ByaxError
from byax.exchange import export_document
from byax.model import new_document
from byax.publication import export_clean, reproduce, reproduction_record

MIXED = 'Latin ελληνικά עברית العربية é 文字 \U0010fffd'


def _with_fs_type(tmp_path, value):
    raw = bytearray(fonts.bundled_font_path('DejaVu Sans').read_bytes())
    count = struct.unpack('>H', raw[4:6])[0]
    for index in range(count):
        tag, _checksum, start, _length = struct.unpack('>4sIII', raw[12 + index * 16 : 28 + index * 16])
        if tag == b'OS/2':
            raw[start + 8 : start + 10] = struct.pack('>H', value)
    path = tmp_path / f'fs{value}.ttf'
    path.write_bytes(bytes(raw))
    return path


@pytest.mark.parametrize(
    ('fs_type', 'embedding', 'embeddable'),
    [
        (0, 'installable', True),
        (0x0002, 'restricted', False),
        (0x0004, 'preview_print', False),
        (0x0008, 'editable', True),
    ],
)
def test_declared_embedding_permissions_are_read_from_os2(tmp_path, fs_type, embedding, embeddable):
    info = fonts.font_file_info(_with_fs_type(tmp_path, fs_type))
    assert (
        info['family'] == 'DejaVu Sans'
        and info['embedding'] == embedding
        and info['embeddable'] is embeddable
    )


def test_font_inspection_reports_identity_and_substitution():
    available = fonts.inspect_font('DejaVu Sans')
    assert (
        available['status'] == 'available'
        and available['file_family'] == 'DejaVu Sans'
        and len(available['sha256']) == 64
    )
    missing = fonts.inspect_font('No Such Family Zq')
    assert missing['substituted'] and missing['status'] in ('substituted', 'unavailable')
    with pytest.raises(ByaxError) as error:
        fonts.embed_font(new_document(), 'No Such Family Zq')
    assert error.value.code == 'missing_dependency'


def test_embedding_only_when_permitted_and_clean_publication_keeps_it(tmp_path, monkeypatch):
    doc = new_document('Fonts', 'Body')
    embedded, info = fonts.embed_font(doc, 'DejaVu Sans')
    reference = embedded['layout']['fonts']['DejaVu Sans']['regular']
    assert info['embedding'] == 'installable' and reference['font_asset'] in embedded['assets']
    assert 'path' not in info and doc['assets'] == {}
    out = tmp_path / 'clean.byax'
    export_clean(embedded, out)
    from byax.storage import load_document

    assert reference['font_asset'] in load_document(out)['assets']
    restricted = _with_fs_type(tmp_path, 0x0002)
    monkeypatch.setattr(fonts, 'resolve_font_file', lambda *args, **kwargs: restricted)
    with pytest.raises(ByaxError) as error:
        fonts.embed_font(doc, 'DejaVu Sans')
    assert (
        error.value.code == 'unsupported_feature' and error.value.details['font']['embedding'] == 'restricted'
    )


def test_glyph_fallbacks_capture_mixed_script_fonts_and_missing_glyphs():
    result = fonts.glyph_fallbacks(new_document('Mixed', MIXED))
    families = {font['family']: font for font in result['fonts']}
    assert 'DejaVu Sans' in families and not families['DejaVu Sans']['fallback']
    assert all(len(font['tables_sha256']) == 64 for font in result['fonts'])
    assert 'U+0065 U+0301' in families['DejaVu Sans']['sample']
    assert {'codepoints': 'U+10FFFD', 'requested_families': ['DejaVu Sans']} in result['missing_glyphs']
    cjk = [font for font in result['fonts'] if 'U+6587' in font['sample']]
    if not cjk:
        pytest.skip('No CJK-capable fallback font installed')
    assert cjk[0]['fallback'] and cjk[0]['requested_families'] == ['DejaVu Sans']


def test_pdf_export_records_actual_glyph_fonts(tmp_path):
    report = export_document(new_document('Mixed', MIXED), tmp_path / 'mixed.pdf')
    assert report['font_dependencies']['fonts'] and report['font_dependencies']['missing_glyphs']


def test_reproduction_distinguishes_renderable_matched_drift_and_mismatch(tmp_path):
    doc = new_document('Repro', MIXED)
    record = reproduction_record(doc, {'reference_dir': str(tmp_path / 'reference')})
    assert record['glyph_fallbacks']['fonts']
    matched = reproduce(doc, record, tmp_path / 'again')['reproduction']
    assert matched['state'] == 'matched' and matched['states'] == {
        'renderable': True,
        'dependencies_matched': True,
        'reference_verified': True,
        'drift': False,
        'mismatch': False,
    }
    tampered = dict(
        record,
        glyph_fallbacks={
            **record['glyph_fallbacks'],
            'fonts': [dict(record['glyph_fallbacks']['fonts'][0], tables_sha256='0' * 64)]
            + record['glyph_fallbacks']['fonts'][1:],
        },
    )
    drifted = reproduce(doc, tampered, tmp_path / 'drift')['reproduction']
    assert drifted['state'] == 'drift' and 'glyph_fallbacks' in drifted['dependency_drift']
    assert drifted['fallback_drift'][0]['change'] == 'tables_sha256'
    no_reference = reproduce(doc, reproduction_record(doc), tmp_path / 'plain')
    assert (
        no_reference['reproduction']['state'] == 'renderable' and no_reference['validation'] == 'incomplete'
    )
    changed = new_document('Repro', MIXED + ' more text that reflows the page')
    changed['id'], changed['revision'] = doc['id'], doc['revision']
    mismatch = reproduce(changed, record, tmp_path / 'changed')['reproduction']
    assert mismatch['states']['drift'] and not mismatch['states']['reference_verified']


def test_reproduction_settings_never_store_credentials():
    with pytest.raises(ByaxError):
        reproduction_record(new_document(), {'api_key': 'sk-test-secret'})
