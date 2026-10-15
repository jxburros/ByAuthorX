import base64
import io

from PIL import Image
from PySide6.QtGui import QColor

from byax import layout
from byax.model import new_document


def png(color):
    out = io.BytesIO()
    Image.new('RGB', (10, 10), color).save(out, format='PNG')
    return out.getvalue()


def test_image_cache_hashes_content_and_is_copy_on_write(monkeypatch):
    layout._IMAGE_CACHE.clear()
    calls = []
    read = layout._read_image

    def counted(raw):
        calls.append(raw)
        return read(raw)

    monkeypatch.setattr(layout, '_read_image', counted)
    red, blue = png('red'), png('blue')
    first = layout._decode_image(red)
    first.fill(QColor('green'))
    assert layout._decode_image(red).pixelColor(0, 0) == QColor('red')
    assert layout._decode_image(blue).pixelColor(0, 0) == QColor('blue')
    assert calls == [red, blue]


def test_image_cache_evicts_to_byte_budget(monkeypatch):
    layout._IMAGE_CACHE.clear()
    monkeypatch.setattr(layout, '_IMAGE_CACHE_BYTES', 450)
    for color in ['red', 'blue', 'green']:
        layout._decode_image(png(color))
    assert len(layout._IMAGE_CACHE) == 1
    assert sum(image.sizeInBytes() for image in layout._IMAGE_CACHE.values()) <= 450
    layout._IMAGE_CACHE.clear()


def test_cached_and_uncached_layout_and_pixels_are_identical(tmp_path, monkeypatch):
    from byax import engine

    doc = new_document(text='Café e\u0301 😀\nSecond page')
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'add_asset', 'data': base64.b64encode(png('red')).decode(), 'mime': 'image/png'},
            ]
        },
    )
    asset = next(iter(doc['assets']))
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'place_image', 'asset': asset, 'alt': 'First red square', 'width': 40, 'height': 40},
                {
                    'type': 'place_image',
                    'asset': asset,
                    'alt': 'Repeated red square',
                    'width': 40,
                    'height': 40,
                },
            ]
        },
    )
    target = doc['blocks'][1]['id']
    doc, _ = engine.apply(
        doc,
        {
            'operations': [
                {'type': 'set_attributes', 'target': target, 'values': {'page_break_before': True}},
            ]
        },
    )
    cached = layout.render(doc, tmp_path / 'cached')
    pdf_cached = layout.export_pdf(doc, tmp_path / 'cached.pdf')
    monkeypatch.setattr(layout, '_decode_image', layout._read_image)
    original = layout._check_glyphs

    def without_cache(*args, **kwargs):
        token = layout._GLYPH_CACHE.set(None)
        try:
            return original(*args, **kwargs)
        finally:
            layout._GLYPH_CACHE.reset(token)

    monkeypatch.setattr(layout, '_check_glyphs', without_cache)
    uncached = layout.render(doc, tmp_path / 'uncached')
    pdf_uncached = layout.export_pdf(doc, tmp_path / 'uncached.pdf')
    assert cached['layout_signature'] == uncached['layout_signature']
    assert pdf_cached['layout_signature'] == pdf_uncached['layout_signature'] == cached['layout_signature']
    for a, b in zip(cached['outputs'], uncached['outputs'], strict=True):
        assert Image.open(a).tobytes() == Image.open(b).tobytes()
    from pypdf import PdfReader

    assert [p.extract_text() for p in PdfReader(tmp_path / 'cached.pdf').pages] == [
        p.extract_text() for p in PdfReader(tmp_path / 'uncached.pdf').pages
    ]
    assert layout._GLYPH_CACHE.get() is None
