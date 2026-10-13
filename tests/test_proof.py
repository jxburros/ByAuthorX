import copy
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from byax.cli import execute
from byax.errors import ByaxError
from byax.model import new_document, new_id
from byax.proof import create_proof
from byax.storage import save_document


def mixed_pages():
    doc = new_document('Proof fixture', 'First portrait page with an anchored paragraph.')
    doc['blocks'].extend([
        {'id': new_id('section'), 'type': 'section_break', 'text': '', 'style': 'body', 'marks': [],
         'attrs': {'layout': {'width': 792, 'height': 612}}},
        {'id': new_id('p'), 'type': 'paragraph', 'text': 'Second landscape page.', 'style': 'body', 'marks': [], 'attrs': {}},
    ])
    return doc


def test_proof_uses_physical_pages_crop_points_and_non_destructive_overlay(tmp_path):
    doc = mixed_pages()
    original = copy.deepcopy(doc)
    out = tmp_path / 'proof'
    report = create_proof(doc, out, crops=[{'page': 2, 'rect': [72, 72, 100, 40]}],
                          overlays=[{'target': doc['blocks'][0]['id'], 'label': 'Check this paragraph'}],
                          columns=2, thumbnail_width=200)
    assert doc == original
    assert report['pages'] == [1, 2]
    assert report['dpi'] == 144
    with Image.open(out / 'page-0001.png') as image:
        assert image.size == (1224, 1584)
    with Image.open(out / 'page-0002.png') as image:
        assert image.size == (1584, 1224)
    with Image.open(out / 'crop-0001-page-0002.png') as crop:
        assert crop.size == (200, 80)
        with Image.open(out / 'page-0002.png') as page:
            expected = page.crop((144, 144, 344, 224))
            assert ImageChops.difference(crop.convert('RGB'), expected.convert('RGB')).getbbox() is None
    with Image.open(out / 'page-0001.png') as page, Image.open(out / 'overlay-page-0001.png') as annotated:
        assert ImageChops.difference(page.convert('RGB'), annotated.convert('RGB')).getbbox()
    with Image.open(out / 'contact-sheet.png') as sheet:
        assert list(sheet.size) == report['contact_sheet']['pixels']
    placements = report['contact_sheet']['pages']
    assert placements[0]['source_pixels'] == [1224, 1584]
    assert placements[1]['source_pixels'] == [1584, 1224]
    assert placements[0]['pixels'][3] > placements[1]['pixels'][3]
    assert report['overlays'][0]['target'] == doc['blocks'][0]['id']
    assert all(Path(path).is_file() for path in report['outputs'])


def test_proof_bounds_errors_and_existing_output_remain_intact(tmp_path):
    doc = mixed_pages()
    out = tmp_path / 'proof'
    with pytest.raises(ByaxError, match='outside'):
        create_proof(doc, out, crops=[{'page': 1, 'rect': [600, 0, 50, 50]}])
    assert not out.exists()
    with pytest.raises(ByaxError, match='selected'):
        create_proof(doc, out, pages=[1], crops=[{'page': 2, 'rect': [0, 0, 50, 50]}])
    report = create_proof(doc, out, pages=[1], contact=False)
    original = Path(report['outputs'][0]).read_bytes()
    with pytest.raises(ByaxError) as exc:
        create_proof(doc, out)
    assert exc.value.code == 'conflict'
    assert Path(report['outputs'][0]).read_bytes() == original
    assert not list(tmp_path.glob('.byax-proof-*'))


def test_cli_proof_retains_original_native_document(tmp_path):
    doc = mixed_pages()
    native = tmp_path / 'private.byax'
    save_document(doc, native)
    before = native.read_bytes()
    result = execute('proof', {'path': str(native), 'out': str(tmp_path / 'proof'), 'pages': [1],
        'input': {'overlays': [{'page': 1, 'rect': [60, 60, 150, 60], 'label': 'Review'}]},
        'thumbnail_width': 160})
    assert result['execution'] == 'completed'
    assert result['document_id'] == doc['id']
    assert native.read_bytes() == before
    assert any(path.endswith('contact-sheet.png') for path in result['outputs'])
