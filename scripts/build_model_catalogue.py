"""Regenerate ``src/byax/resources/models.json`` from pinned upstream revisions.

Maintainer tool; nothing in the application runs it. For every model it reads the Hugging Face
tree API at a pinned revision: LFS files carry their SHA-256 (``lfs.oid``) and exact size, and the
small non-LFS files are downloaded once and hashed here. Model weights are never committed.

    uv run python scripts/build_model_catalogue.py            # rewrite the catalogue
    uv run python scripts/build_model_catalogue.py --check    # verify the committed pins still hold

Licences below were read from the primary model cards on the date recorded in ``verified``.
Mirror upload commands are printed by ``scripts/build.py --models-help``.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / 'src' / 'byax' / 'resources' / 'models.json'
VERIFIED = '2026-10-16'
MIRROR_REPOSITORY = 'jxburros/ByAuthorX'
MIRROR_TAG = 'models-v1'
HF = 'https://huggingface.co'
HHEM_REVISION = 'de9a5e6ba9ba49531a022dba17e35ffeb2a6fad8'
FLAN_REVISION = '7bcac572ce56db69c1ea7c8af255c5d7c9672fc2'

# (card repository, card revision) holds the licence statement; (export repository, revision) the ONNX files.
MODELS = [
    {
        'name': 'qwen3-embedding-0.6b-int8',
        'title': 'Qwen3-Embedding-0.6B (int8 ONNX, 512 dimensions)',
        'kind': 'embedder',
        'version': '1',
        'bundled': True,
        'description': 'Dense embedder for on-device semantic search. Multilingual, 32k context; int8 CPU export.',
        'licence': {
            'spdx': 'Apache-2.0',
            'source': f'{HF}/Qwen/Qwen3-Embedding-0.6B (model card front matter: license: apache-2.0)',
        },
        'base': ('Qwen/Qwen3-Embedding-0.6B', '97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3'),
        'export': ('onnx-community/Qwen3-Embedding-0.6B-ONNX', 'c25a394dd583836952667c12f008335071b3f43d'),
        'files': [
            ('onnx/model_int8.onnx', 'model.onnx', 'model'),
            ('tokenizer.json', 'tokenizer.json', 'tokenizer'),
            ('config.json', 'config.json', 'config'),
        ],
        'runtime': {
            'family': 'qwen3-embedding',
            'pooling': 'last_token',
            'native_dimension': 1024,
            'dimension': 512,
            'matryoshka': True,
            'max_tokens': 1024,
            'query_instruction': "Given a search query, retrieve relevant passages from the writer's documents",
        },
    },
    {
        'name': 'bge-reranker-v2-m3-int8',
        'title': 'bge-reranker-v2-m3 (int8 ONNX cross-encoder)',
        'kind': 'reranker',
        'version': '1',
        'bundled': False,
        'description': 'Multilingual cross-encoder reranker. Accurate but large; installed only on request.',
        'licence': {
            'spdx': 'Apache-2.0',
            'source': f'{HF}/BAAI/bge-reranker-v2-m3 (model card front matter: license: apache-2.0)',
        },
        'base': ('BAAI/bge-reranker-v2-m3', '953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'),
        'export': ('onnx-community/bge-reranker-v2-m3-ONNX', '6f5ff65298512715a1e669753bc754d2bc8f367b'),
        'files': [
            ('onnx/model_int8.onnx', 'model.onnx', 'model'),
            ('tokenizer.json', 'tokenizer.json', 'tokenizer'),
            ('config.json', 'config.json', 'config'),
        ],
        'runtime': {'family': 'cross-encoder', 'max_tokens': 512, 'pad_token_id': 1},
    },
    {
        'name': 'answerai-colbert-small-v1',
        'title': 'answerai-colbert-small-v1 (33M ColBERT reranker, ONNX)',
        'kind': 'reranker',
        'version': '1',
        'bundled': True,
        'description': 'Small late-interaction (MaxSim) reranker, English. fp32 export that includes the 96-dimension projection.',
        'licence': {
            'spdx': 'Apache-2.0',
            'source': f'{HF}/answerdotai/answerai-colbert-small-v1 (model card front matter: license: apache-2.0)',
        },
        'base': ('answerdotai/answerai-colbert-small-v1', '934fa8bb4ce2284f4c2baa232d81aca4d076fa5e'),
        'export': ('answerdotai/answerai-colbert-small-v1', '934fa8bb4ce2284f4c2baa232d81aca4d076fa5e'),
        'files': [
            ('vespa_colbert.onnx', 'model.onnx', 'model'),
            ('tokenizer.json', 'tokenizer.json', 'tokenizer'),
            ('onnx_config.json', 'config.json', 'config'),
        ],
        'runtime': {
            'family': 'colbert',
            'max_query_tokens': 32,
            'max_document_tokens': 300,
            'embedding_dim': 96,
            'query_prefix_id': 1,
            'document_prefix_id': 2,
        },
    },
    {
        'name': 'docling-layout-heron',
        'title': 'Docling layout model "heron" (PDF quality path)',
        'kind': 'layout',
        'version': '1',
        'bundled': False,
        'description': 'Page layout analysis for the optional Docling PDF engine (byauthorx[docling]). '
        'Install it before choosing the Docling engine; Docling is then run offline.',
        'licence': {
            'spdx': 'Apache-2.0',
            'source': f'{HF}/docling-project/docling-layout-heron (model card front matter: license: apache-2.0)',
        },
        'base': ('docling-project/docling-layout-heron', '8f39ad3c0b4c58e9c2d2c84a38465abf757272d8'),
        'export': ('docling-project/docling-layout-heron', '8f39ad3c0b4c58e9c2d2c84a38465abf757272d8'),
        'files': [
            ('model.safetensors', 'docling-project--docling-layout-heron/model.safetensors', 'model'),
            ('config.json', 'docling-project--docling-layout-heron/config.json', 'config'),
            (
                'preprocessor_config.json',
                'docling-project--docling-layout-heron/preprocessor_config.json',
                'config',
            ),
        ],
        'runtime': {'family': 'docling-layout', 'artifacts_folder': 'docling-project--docling-layout-heron'},
    },
    {
        # The project's own ONNX build of an upstream model that publishes no ONNX file (no trust_remote_code
        # at run time). ``fixed`` files carry the size and SHA-256 of the exact bytes produced by
        # ``scripts/export_hhem_onnx.py`` (deterministic for the pinned library versions in its docstring);
        # ``mirror_only`` files are served from the project mirror release, never from an upstream host.
        'name': 'hhem-2.1-open-int8',
        'title': 'HHEM-2.1-Open (int8 ONNX entailment checker, English)',
        'kind': 'entailment',
        'version': '1',
        'bundled': False,
        'description': 'Checks whether a claim is supported by the text it cites (cascade stage 2). '
        'English only; advisory. Installed only on request.',
        'licence': {
            'spdx': 'Apache-2.0',
            'source': f'{HF}/vectara/hallucination_evaluation_model (model card front matter: '
            f'license: apache-2.0); base model {HF}/google/flan-t5-base (license: apache-2.0)',
            'verified': '2026-10-17',
        },
        'base': ('vectara/hallucination_evaluation_model', HHEM_REVISION),
        'export': ('vectara/hallucination_evaluation_model', HHEM_REVISION),
        'export_recipe': 'scripts/export_hhem_onnx.py',
        'files': [],
        'fixed': [
            {
                'path': 'model.onnx',
                'role': 'model',
                'size': 166804567,
                'sha256': '8ac275e8177561ed5d94ef836b83931738d39945f4275b1b179e35f478050dbe',
                'mirror_only': True,
            },
        ],
        'extra': [
            ('google/flan-t5-base', FLAN_REVISION, 'tokenizer.json', 'tokenizer.json', 'tokenizer'),
            ('google/flan-t5-base', FLAN_REVISION, 'README.md', 'BASE-MODEL-CARD.md', 'card'),
        ],
        'runtime': {
            'family': 'hhem-t5',
            'language': 'en',
            'max_tokens': 1024,
            'pad_token_id': 0,
            'prompt': '<pad> Determine if the hypothesis is true given the premise?\n\n'
            'Premise: {text1}\n\nHypothesis: {text2}',
            'supported_at': 0.8,
            'unsupported_at': 0.65,
        },
    },
]


def tree(client, repository, revision):
    response = client.get(f'{HF}/api/models/{repository}/tree/{revision}', params={'recursive': 'true'})
    response.raise_for_status()
    return {item['path']: item for item in response.json() if item['type'] == 'file'}


def digest(client, url):
    sha = hashlib.sha256()
    size = 0
    with client.stream('GET', url) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes(1 << 20):
            sha.update(chunk)
            size += len(chunk)
    return sha.hexdigest(), size


def file_entry(client, repository, revision, source, destination, role, cache):
    if (repository, revision) not in cache:
        cache[repository, revision] = tree(client, repository, revision)
    item = cache[repository, revision][source]
    url = f'{HF}/{repository}/resolve/{revision}/{source}'
    lfs = item.get('lfs')
    if lfs:
        sha256, size = lfs['oid'], int(item['size'])
    else:
        sha256, size = digest(client, url)
    return {'path': destination, 'role': role, 'size': size, 'sha256': sha256, 'url': url}


def build(client):
    cache = {}
    models = []
    for spec in MODELS:
        repository, revision = spec['export']
        files = [
            file_entry(client, repository, revision, source, destination, role, cache)
            for source, destination, role in spec['files']
        ]
        base_repository, base_revision = spec['base']
        files.append(
            file_entry(client, base_repository, base_revision, 'README.md', 'MODEL-CARD.md', 'card', cache)
        )
        for repo, rev, source, destination, role in spec.get('extra', []):
            files.append(file_entry(client, repo, rev, source, destination, role, cache))
        mirror = f'https://github.com/{MIRROR_REPOSITORY}/releases/download/{MIRROR_TAG}'
        for fixed in reversed(spec.get('fixed', [])):
            files.insert(0, {**fixed, 'url': f'{mirror}/{spec["name"]}__{fixed["path"]}'})
        for item in files:
            item['mirror_asset'] = f'{spec["name"]}__{item["path"]}'
        models.append(
            {
                'name': spec['name'],
                'title': spec['title'],
                'kind': spec['kind'],
                'version': spec['version'],
                'description': spec['description'],
                'bundled': spec['bundled'],
                'licence': {**spec['licence'], 'verified': spec['licence'].get('verified', VERIFIED)},
                'upstream': {
                    'model': base_repository,
                    'export': repository,
                    'revision': revision,
                    'url': f'{HF}/{repository}/tree/{revision}',
                    **({'export_recipe': spec['export_recipe']} if 'export_recipe' in spec else {}),
                },
                'runtime': spec['runtime'],
                'size_bytes': sum(f['size'] for f in files),
                'files': files,
            }
        )
    return {
        'schema': 1,
        'catalogue_version': 1,
        'mirror': {
            'repository': MIRROR_REPOSITORY,
            'release_tag': MIRROR_TAG,
            'base_url': f'https://github.com/{MIRROR_REPOSITORY}/releases/download/{MIRROR_TAG}',
            'checksums_asset': 'SHA256SUMS.txt',
            'populated': False,
            'note': 'The mirror release has not been created yet; downloads use the pinned upstream revision.',
        },
        'models': models,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Fail if the committed catalogue differs')
    args = parser.parse_args()
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        fresh = build(client)
    if args.check:
        current = json.loads(CATALOGUE.read_text(encoding='utf-8'))
        fresh['mirror'] = current['mirror']
        if fresh != current:
            print('models.json differs from the pinned upstream revisions', file=sys.stderr)
            return 1
        print('models.json matches upstream')
        return 0
    if CATALOGUE.exists():
        # Keep the maintainer's decision about whether the mirror exists.
        fresh['mirror'] = json.loads(CATALOGUE.read_text(encoding='utf-8')).get('mirror', fresh['mirror'])
    CATALOGUE.write_text(json.dumps(fresh, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(CATALOGUE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
