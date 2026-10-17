"""Export hook: write <exported file>.wordcount.csv into the output folder.

Protocol (api_version 1): stdin is one JSON object
  {"api_version": 1, "extension", "hook", "format", "file", "output_dir", "loss_report"}
`file` is a read-only copy of the exported file. Write result files into `output_dir`
(plain files only); ByAuthorX copies them to the folder the person chose. Print nothing
or {"message": "..."} on stdout.
"""

import csv
import html
import json
import re
import sys
import zipfile
from pathlib import Path


def text_of(path):
    path = Path(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            parts = [
                archive.read(name).decode('utf-8', 'replace')
                for name in archive.namelist()
                if name.endswith(('.xml', '.xhtml', '.html'))
                and not name.startswith(('META-INF', 'docProps'))
            ]
        raw = ' '.join(parts)
    else:
        raw = path.read_text(encoding='utf-8', errors='replace')
    return html.unescape(re.sub(r'<[^>]+>', ' ', raw))


def main():
    request = json.load(sys.stdin)
    source = Path(request['file'])
    words = len(re.findall(r"[\w'’-]+", text_of(source)))
    report = request.get('loss_report') or {}
    losses = report.get('losses', report) if isinstance(report, dict) else report
    count = len(losses) if isinstance(losses, list) else 0
    out = Path(request['output_dir']) / (source.name + '.wordcount.csv')
    with out.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['file', 'format', 'bytes', 'words', 'reported_losses'])
        writer.writerow([source.name, request['format'], source.stat().st_size, words, count])
    json.dump({'message': f'{words} words in {source.name}'}, sys.stdout)


if __name__ == '__main__':
    main()
