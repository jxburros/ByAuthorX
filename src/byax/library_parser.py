"""Isolated, resource-bounded library text extraction (never executes file content)."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def extract(path, maximum):
    path = Path(path)
    if path.suffix.lower() == '.pdf':
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
        # A malicious archive/parser cannot grow without bound. Parent also enforces wall time.
        if sys.platform != 'win32':
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
            resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        value = {'passages': extract(args[0], int(args[1]))}
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
    if args and args[0] == '--byax-library-parser':
        if len(args) != 4:
            raise SystemExit(2)
        raise SystemExit(main(args[1:]))


if __name__ == '__main__':
    main()
