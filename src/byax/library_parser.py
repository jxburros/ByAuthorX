"""Isolated, resource-bounded library text extraction (never executes file content).

PDFs are read with pypdf by default. Docling is temporarily disabled pending support for
security-fixed Pillow 13. Its retained extraction implementation (MIT) runs
only here, in this subprocess: it reads reading order and headings with a layout model, keeps the page
and bounding box of every passage, and imports the PDF's own annotations as linked notes. It runs
offline on the layout model installed with ``byax models`` and never fetches one itself. PyMuPDF is not
used because its AGPL licence conflicts with the project's MIT licence.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DOCLING_UNAVAILABLE = (
    'The Docling PDF engine is temporarily unavailable because its dependencies require '
    'Pillow below the security-fixed version 13. Choose the pypdf engine instead.'
)
DOCLING_LAYOUT_MODEL = 'docling-layout-heron'
# Docling labels that are page furniture, not text a writer would cite.
DOCLING_SKIPPED = {'page_header', 'page_footer', 'footnote'}


def _round(values):
    return [round(float(v), 2) for v in values]


def pdf_annotations(path, start_ordinal=0):
    """PDF annotations that carry text (comments, highlights with notes) as passages linked to page and box.

    Each passage is a separate note: ``locator.note`` names the annotation type and ``bbox`` its rectangle
    in top-left-origin PDF points. Annotations without text are skipped.
    """
    from pypdf import PdfReader

    reader = PdfReader(path, strict=True)
    passages = []
    for number, page in enumerate(reader.pages, 1):
        height = float(page.mediabox.height)
        for annotation in page.get('/Annots') or []:
            try:
                item = annotation.get_object()
                text = str(item.get('/Contents') or '').strip()
                if not text:
                    continue
                left, bottom, right, top = (float(v) for v in item['/Rect'])
                subtype = str(item.get('/Subtype', '/Text')).lstrip('/')
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            x0, x1 = sorted((left, right))
            y0, y1 = sorted((height - top, height - bottom))
            passages.append(
                {
                    'text': text,
                    'locator': {
                        'page': number,
                        'ordinal': start_ordinal + len(passages),
                        'heading_path': [],
                        'note': subtype,
                        'bbox': _round([x0, y0, x1, y1]),
                    },
                }
            )
    return passages


def docling_passages(document, maximum):
    """Passages from a Docling document: reading order, heading path, page and bounding box per item."""
    passages, headings, size = [], [], 0
    for item, _ in document.iterate_items():
        label = str(getattr(getattr(item, 'label', ''), 'value', getattr(item, 'label', '')))
        if label in DOCLING_SKIPPED:
            continue
        text = getattr(item, 'text', None)
        if text is None and hasattr(item, 'export_to_markdown'):
            try:
                text = item.export_to_markdown(doc=document)
            except Exception:  # noqa: BLE001 - a table that cannot be flattened is skipped, never fatal
                text = ''
        text = (text or '').strip()
        if not text:
            continue
        size += len(text)
        if size > maximum:
            raise ValueError('Maximum extracted text exceeded')
        if label == 'section_header' or label == 'title':
            level = int(getattr(item, 'level', 1) or 1)
            headings = headings[: max(0, level - 1)] + [text]
        locator = {'ordinal': len(passages), 'heading_path': list(headings)}
        provenance = (getattr(item, 'prov', None) or [None])[0]
        if provenance is not None:
            locator['page'] = int(provenance.page_no)
            page = document.pages.get(provenance.page_no)
            box = provenance.bbox
            if page is not None:
                box = box.to_top_left_origin(page_height=page.size.height)
            locator['bbox'] = _round([box.l, box.t, box.r, box.b])
        passages.append({'text': text, 'locator': locator})
    return passages


def extract_pdf_docling(path, maximum):
    """Read a PDF with Docling, offline, on the installed layout model; add the PDF's own annotations."""
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise ValueError(
            'The Docling PDF engine is not installed: install byauthorx[docling] or choose the pypdf engine'
        ) from exc
    from .errors import ByaxError
    from .local_models import ready_path

    try:
        folder = ready_path(DOCLING_LAYOUT_MODEL)
    except ByaxError as exc:
        raise ValueError(
            f'{exc.message}. Install it with: byax models --action download --name {DOCLING_LAYOUT_MODEL} --confirm'
        ) from exc
    options = PdfPipelineOptions(artifacts_path=str(folder), do_ocr=False, do_table_structure=False)
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    document = converter.convert(str(path)).document
    passages = docling_passages(document, maximum)
    size = sum(len(p['text']) for p in passages)
    notes = pdf_annotations(path, start_ordinal=len(passages))
    if size + sum(len(n['text']) for n in notes) > maximum:
        raise ValueError('Maximum extracted text exceeded')
    return passages + notes


def extract(path, maximum, engine='pypdf'):
    path = Path(path)
    if path.suffix.lower() == '.pdf':
        if engine == 'docling':
            raise ValueError(DOCLING_UNAVAILABLE)
        from pypdf import PdfReader

        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise ValueError('Encrypted PDF is not supported')
        passages = []
        size = 0
        for ordinal, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ''
            size += len(text)
            if size > maximum:
                raise ValueError('Maximum extracted text exceeded')
            if text.strip():
                passages.append({'text': text, 'locator': {'page': ordinal}})
        return passages
    from .exchange import import_document

    doc, _ = import_document(path)
    passages, headings, size = [], [], 0
    for ordinal, block in enumerate(doc['blocks']):
        text = block.get('text', '')
        if block.get('type') == 'table':
            text = '\n'.join(
                cell.get('text', '') for row in block.get('rows', []) for cell in row.get('cells', [])
            )
        if block.get('type') == 'heading':
            level = int(block.get('attrs', {}).get('level', 1))
            headings = headings[: max(0, level - 1)] + [text]
        size += len(text)
        if size > maximum:
            raise ValueError('Maximum extracted text exceeded')
        if text.strip():
            locator = {'ordinal': ordinal, 'heading_path': list(headings)}
            if path.suffix.lower() == '.byax':
                locator['block'] = block['id']
            passages.append({'text': text, 'locator': locator})
    return passages


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        # args: source, maximum characters, result path, then optionally the PDF engine and CPU seconds.
        engine = args[3] if len(args) > 3 else 'pypdf'
        seconds = int(args[4]) if len(args) > 4 else 30
        # A malicious archive/parser cannot grow without bound. Parent also enforces wall time.
        from .platform_support import apply_parser_limits

        # macOS may refuse or ignore RLIMIT_AS; the parent's wall-clock timeout still applies.
        if engine == 'pypdf':
            apply_parser_limits(768 * 1024**2, 30)
        else:
            # A layout model needs far more address space than a text parser; CPU time stays bounded
            # (several threads share the budget) and the parent still enforces wall time.
            apply_parser_limits(None, max(30, seconds * 4))
        value = {'passages': extract(args[0], int(args[1]), engine)}
    except Exception as exc:  # noqa: BLE001 - isolated parser reports all malformed inputs
        value = {'error': str(exc)[:400]}
    serialized = json.dumps(value, ensure_ascii=False)
    if len(args) > 2:
        Path(args[2]).write_text(serialized, encoding='utf-8')
    else:
        print(serialized)
    return 0


def dispatch_worker(argv=None):
    """Frozen entrypoints dispatch before importing GUI/transport modules."""
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == '--byax-extension-worker':
        from .extension_worker import dispatch_worker as dispatch_extension

        dispatch_extension(args)
    if args and args[0] == '--byax-library-parser':
        if len(args) not in (4, 6):
            raise SystemExit(2)
        raise SystemExit(main(args[1:]))


if __name__ == '__main__':
    main()
