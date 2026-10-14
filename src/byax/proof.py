"""Review artifacts composed from the shared 144 DPI physical-page renderer.

Coordinates are page-local points (1/72 inch), page numbers start at one.
Diagnostic overlays are derived images and never become document content.
"""

from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .errors import ByaxError

MAX_PIXELS = 40_000_000
MAX_PAGES = 100
SCALE = 2


def _rectangle(value, width, height):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ByaxError('invalid_input', 'Proof rectangle must be [x, y, width, height] in points')
    if any(type(number) not in (int, float) or not math.isfinite(number) for number in value):
        raise ByaxError('invalid_input', 'Proof rectangle values must be finite numbers')
    x, y, w, h = value
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height:
        raise ByaxError(
            'invalid_input',
            'Proof rectangle lies outside the selected page',
            rectangle=value,
            page_size=[width, height],
        )
    return [float(number) for number in value]


def _pixel_box(rect):
    x, y, width, height = rect
    return (
        math.floor(x * SCALE),
        math.floor(y * SCALE),
        math.ceil((x + width) * SCALE),
        math.ceil((y + height) * SCALE),
    )


def _semantic_bounds(doc, resolved, target):
    """Find the visible intersections of a semantic text block or table cells."""
    from .model import find_object

    obj = find_object(doc, target)
    ids = [target]
    if obj.get('type') == 'table':
        ids = [cell['id'] for row in obj['rows'] for cell in row['cells']]
    regions = []
    for page_number, page in enumerate(resolved['pages'], start=1):
        per_page = []
        for fragment in page['fragments']:
            if 'document' not in fragment or fragment['kind'] == 'table_header':
                continue
            # Fragments map a source rectangle of a shared flow document onto the page.
            part, placement, source = fragment['document'], fragment['rect'], fragment['source']
            for ident in ids:
                position = part.byax_positions.get(ident)
                if position is None:
                    continue
                block_rect = part.documentLayout().blockBoundingRect(part.findBlock(position))
                left = max(source.left(), block_rect.left())
                top = max(source.top(), block_rect.top())
                right = min(source.right(), block_rect.right())
                bottom = min(source.bottom(), block_rect.bottom())
                if right > left and bottom > top:
                    per_page.append(
                        [
                            placement.left() + left - source.left(),
                            placement.top() + top - source.top(),
                            right - left,
                            bottom - top,
                        ]
                    )
        # Keep column intersections separate: a block can span physical columns.
        regions.extend(
            {
                'page': page_number,
                'rect': rect,
                'target': target,
                'bounds_kind': 'resolved Qt text block intersection',
            }
            for rect in per_page
        )
    return regions


def contact_sheet(images, output, *, columns=3, thumbnail_width=320):
    """Compose labeled, aspect-preserving page thumbnails of variable dimensions."""
    if type(columns) is not int or not 1 <= columns <= 12:
        raise ByaxError('invalid_input', 'Contact sheet columns must be between 1 and 12')
    if type(thumbnail_width) is not int or not 64 <= thumbnail_width <= 2048:
        raise ByaxError('invalid_input', 'Thumbnail width must be between 64 and 2048 pixels')
    if not images:
        raise ByaxError('invalid_input', 'Contact sheet requires at least one page')
    gap, label_height = 16, 28
    sizes = []
    for page, path in images:
        with Image.open(path) as image:
            sizes.append(
                (page, path, image.size, max(1, round(image.height * thumbnail_width / image.width)))
            )
    columns = min(columns, len(images))
    rows = math.ceil(len(images) / columns)
    row_heights = [
        max(size[3] for size in sizes[row * columns : (row + 1) * columns]) + label_height
        for row in range(rows)
    ]
    width = columns * thumbnail_width + gap * (columns + 1)
    height = sum(row_heights) + gap * (rows + 1)
    if width * height > MAX_PIXELS:
        raise ByaxError(
            'limit_exceeded',
            'Contact sheet exceeds 40 million pixels; reduce thumbnail width or page selection',
        )
    sheet = Image.new('RGB', (width, height), '#e9edf2')
    draw, font = ImageDraw.Draw(sheet), ImageFont.load_default(size=14)
    placements = []
    y = gap
    for row in range(rows):
        for column, (page, path, original_size, thumb_height) in enumerate(
            sizes[row * columns : (row + 1) * columns]
        ):
            x = gap + column * (thumbnail_width + gap)
            with Image.open(path) as image:
                thumb = ImageOps.contain(
                    image.convert('RGB'), (thumbnail_width, thumb_height), Image.Resampling.LANCZOS
                )
                sheet.paste(thumb, (x, y))
            draw.text(
                (x, y + row_heights[row] - label_height + 6),
                f'Page {page} | {original_size[0] / SCALE:g} x {original_size[1] / SCALE:g} pt',
                fill='#243147',
                font=font,
            )
            placements.append(
                {
                    'page': page,
                    'pixels': [x, y, thumb.width, thumb.height],
                    'source_pixels': list(original_size),
                }
            )
        y += row_heights[row] + gap
    sheet.save(output, 'PNG')
    return {'pixels': [width, height], 'columns': columns, 'pages': placements}


def create_proof(
    doc,
    out_dir,
    *,
    pages=None,
    crops=None,
    overlays=None,
    contact=True,
    columns=3,
    thumbnail_width=320,
    owner=None,
):
    """Stage page images, crops, overlays and a contact sheet as one new directory."""
    from .layout import build_document, render, resolve_layout
    from .storage import DocumentLock

    if (
        crops is not None
        and not isinstance(crops, list)
        or overlays is not None
        and not isinstance(overlays, list)
    ):
        raise ByaxError('invalid_input', 'Proof crops and overlays must be arrays')
    if len(crops or []) > 1000 or len(overlays or []) > 1000:
        raise ByaxError('limit_exceeded', 'Proof supports at most 1000 crop or overlay selections')
    qdoc = build_document(doc)
    resolved = resolve_layout(qdoc, doc)
    total = len(resolved['pages'])
    selected = list(range(1, total + 1)) if pages is None else pages
    if (
        not isinstance(selected, list)
        or not selected
        or any(type(page) is not int or not 1 <= page <= total for page in selected)
    ):
        raise ByaxError('invalid_input', f'Proof pages must be integers in 1..{total}')
    selected = sorted(set(selected))
    if len(selected) > MAX_PAGES:
        raise ByaxError('limit_exceeded', 'Proof is bounded to 100 selected pages per call')
    prepared_crops, prepared_overlays, findings = [], [], []

    def explicit_region(item):
        page = item.get('page')
        if type(page) is not int or page not in selected:
            raise ByaxError(
                'invalid_input', 'Crop/overlay page must belong to the selected proof pages', page=page
            )
        physical = resolved['pages'][page - 1]
        return {'page': page, 'rect': _rectangle(item.get('rect'), physical['width'], physical['height'])}

    for item in crops or []:
        if not isinstance(item, dict) or set(item) - {'page', 'rect'}:
            raise ByaxError('invalid_input', 'Crop accepts page and rect only')
        prepared_crops.append(explicit_region(item))
    for item in overlays or []:
        if not isinstance(item, dict) or set(item) - {'page', 'rect', 'target', 'label'}:
            raise ByaxError('invalid_input', 'Overlay accepts target or page/rect, with optional label')
        if 'target' in item:
            if not isinstance(item['target'], str) or set(item) & {'page', 'rect'}:
                raise ByaxError('invalid_input', 'An anchored overlay requires one target ID')
            regions = [
                region
                for region in _semantic_bounds(doc, resolved, item['target'])
                if region['page'] in selected
            ]
            if not regions:
                findings.append(
                    {
                        'code': 'proof.bounds_unresolved',
                        'target': item['target'],
                        'severity': 'warning',
                        'message': 'No measurable text/frame bounds were found on selected pages; use an explicit page rectangle.',
                    }
                )
            prepared_overlays.extend(
                dict(region, label=str(item.get('label', item['target']))) for region in regions
            )
        else:
            prepared_overlays.append(
                dict(explicit_region(item), label=str(item.get('label', 'Diagnostic region')))
            )
    destination = Path(out_dir)
    with DocumentLock(destination, owner=owner):
        if destination.exists():
            raise ByaxError(
                'conflict', 'Proof output must be a new directory; existing artifacts are preserved'
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.byax-proof-', dir=destination.parent) as temporary:
            stage = Path(temporary) / 'proof'
            stage.mkdir()
            rendered = render(doc, stage, pages=selected)
            page_paths = {page: stage / f'page-{page:04d}.png' for page in selected}
            outputs = [path.name for path in page_paths.values()]
            crop_report = []
            for index, item in enumerate(prepared_crops, start=1):
                path = stage / f'crop-{index:04d}-page-{item["page"]:04d}.png'
                box = _pixel_box(item['rect'])
                with Image.open(page_paths[item['page']]) as image:
                    image.crop(box).save(path, 'PNG')
                outputs.append(path.name)
                crop_report.append(
                    dict(item, output=str(destination / path.name), pixels=[box[2] - box[0], box[3] - box[1]])
                )
            overlay_paths = dict(page_paths)
            for page in selected:
                regions = [region for region in prepared_overlays if region['page'] == page]
                if not regions:
                    continue
                with Image.open(page_paths[page]) as original:
                    image = original.convert('RGBA')
                annotation = Image.new('RGBA', image.size)
                painter = ImageDraw.Draw(annotation)
                for region in regions:
                    box = _pixel_box(region['rect'])
                    painter.rectangle(box, fill=(255, 180, 45, 30), outline=(200, 65, 30, 255), width=3)
                    label = region['label'][:120]
                    painter.text(
                        (box[0] + 4, max(0, box[1] - 20)),
                        label,
                        fill=(145, 35, 15, 255),
                        font=ImageFont.load_default(size=16),
                    )
                path = stage / f'overlay-page-{page:04d}.png'
                Image.alpha_composite(image, annotation).convert('RGB').save(path, 'PNG')
                outputs.append(path.name)
                overlay_paths[page] = path
            sheet = None
            if contact:
                sheet = contact_sheet(
                    list(overlay_paths.items()),
                    stage / 'contact-sheet.png',
                    columns=columns,
                    thumbnail_width=thumbnail_width,
                )
                outputs.append('contact-sheet.png')
            if destination.exists():
                raise ByaxError(
                    'conflict', 'Proof destination appeared during rendering; existing artifacts preserved'
                )
            os.rename(stage, destination)
    return {
        'schema_version': 1,
        'execution': 'completed',
        'validation': 'incomplete' if findings else 'not_run',
        'document_id': doc['id'],
        'revision': doc['revision'],
        'review': 'required',
        'outputs': [str(destination.resolve() / name) for name in outputs],
        'pages': selected,
        'dpi': 144,
        'coordinate_system': 'page-local points, zero-based top-left, 1-based page numbers',
        'crops': crop_report,
        'overlays': prepared_overlays,
        'contact_sheet': sheet,
        'findings': rendered.get('findings', []) + findings,
        'coverage': {
            'rendering': 'shared physical-page layout',
            'overlays': 'derived review images only',
            'bounds': 'Qt text-block intersections; unresolved objects reported',
            'publication_validation': 'not_run',
        },
    }
