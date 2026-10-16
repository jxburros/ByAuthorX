# Benchmarks

Measurements recorded as JSON next to the code that produced them. Each file holds the raw samples,
the hardware and software identity (`environment`), the definitions it uses and what it did **not**
measure. This host is not a qualified reference machine, so absolute latencies are indicative; compare
files that were measured on the same host.

| Files | Produced by | What |
| --- | --- | --- |
| `0.2-layout-before.json`, `0.2-layout-after.json`, `0.2-scaling.json` | `scripts/benchmark.py` | Layout, commit and GUI latencies (see the [acceptance matrix](../acceptance-matrix.md)) |
| `context-0.2-baseline.json` | `scripts/eval_retrieval.py --baseline` | Retrieval baseline of the context engine: keyword search on the 0.2.0 engine |
| `context-<version>-<config>.json` | `scripts/eval_retrieval.py --configs <config>` | The same measurements for another engine version or retrieval configuration |

## Retrieval evaluation (`context-*.json`)

The harness is `src/byax/evaluation.py`; the fixtures, golden queries and the CI floors are under
`tests/fixtures/eval/` (see [Development](../development.md#retrieval-evaluation-harness)). Every
report has one block per library (`novel`, `series`, `humanities`, `webclips`, `papers`), an optional
`scale` block (synthetic corpus) and a `summary`.

| Field | Meaning |
| --- | --- |
| `metrics.recall@5`, `recall@20` | Share of a query's relevant locators covered by a hit in the top 5 or 20 |
| `metrics.ndcg@10` | Binary-gain nDCG; a hit gains only when it newly covers a relevant locator |
| `metrics.mrr` | Reciprocal rank of the first relevant hit within the top 20 |
| `by_type` | The same metrics per query type (`known_item_quote`, `factual_lookup`, `thematic`, `citation_locating`, `continuity`, `absence_probe`) |
| `absence` | Literal absence probes found, and whether expected-absent probes returned nothing |
| `latency_ms`, `latency_cpu_ms` | p50, p95, mean and max of one search call per query, after one warm-up: wall time, and the process CPU time of the same calls (meaningful on a shared host) |
| `index` | Documents, chunks, bytes of the derived SQLite index, `build_seconds` (parse, chunk, FTS) and `prepare_seconds` (embedding) |
| `alignment` | Hits whose chunk could not be mapped back to source offsets (should be 0) |

Labels are locators (`{path, page}` or `{path, start, end}`), never chunk IDs, so a new chunker does not
invalidate them. A hit is relevant when its own span overlaps a locator.

**The 0.2.0 baseline** (`context-0.2-baseline.json`; keyword search, measured on 0.3.0 because the harness
needs this checkout and retrieval is unchanged between the two releases). Host: 4 logical CPUs (Intel Xeon
2.1 GHz), 15.7 GiB, Python 3.13, an unqualified machine that shared its CPUs with other jobs (load average 5.7 at
the start; process CPU time of the searches is recorded beside wall time as `latency_cpu_ms` and is within
about 20% of it):

| Library | Queries | recall@5 | recall@20 | nDCG@10 | MRR | p95 ms | Chunks | Index MB | Build s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `novel` | 125 | 0.841 | 0.862 | 0.813 | 0.800 | 522 | 12,961 | 9.0 | 2.5 |
| `series` | 124 | 0.857 | 0.904 | 0.851 | 0.836 | 896 | 18,404 | 12.9 | 3.8 |
| `humanities` | 109 | 0.870 | 0.919 | 0.883 | 0.877 | 1,523 | 26,642 | 18.5 | 8.2 |
| `webclips` | 130 | 0.804 | 0.939 | 0.762 | 0.727 | 189 | 14,198 | 9.6 | 8.2 |
| `papers` (30 PDFs) | 160 | 0.911 | 1.000 | 0.883 | 0.864 | 68 | 859 | 4.7 | 19.1 |
| macro average | 648 | 0.857 | 0.925 | 0.839 | 0.821 | | | | |
| `scale` (10,000 text files) | 50 | 1.000 | 1.000 | 1.000 | 1.000 | 43,817 | 40,051 | 71.9 | 2,020 |

How to read it: the engine ORs every query term and chunks hard-wrapped text line by line, so verbatim quotes
(`known_item_quote`, recall@20 1.00 everywhere) are found, while questions and themes are not (`factual_lookup`
recall@20 0.76–0.88 on the text libraries and 0.79 on web clips; `thematic` 0.18 on the novel and 0.43 on
Gibbon). Absent single words return nothing, but absent hyphenated terms, word pairs and phrases still return
passages for the words they contain (for example 8 of 16 absence probes returned passages in `series`). Keyword
latency misses the plan's 100 ms p95 target on every library except `papers`, and at 10,000 documents a query
takes a median 1.1 s and a p95 of 44 s, because `LibraryIndex.search` checks the file of every matching chunk on
disk. Building that index takes 34 minutes because each file is parsed in its own isolated subprocess. These
are the numbers each later phase of the [context engine plan](../context-engine-plan.md) has to beat; they
must never regress.

### Reproducing

```bash
uv run python scripts/fetch_eval_fixtures.py            # once: 27 PLOS papers into your cache (about 22 MB)
uv run python scripts/eval_retrieval.py --baseline --scale 10000   # context-0.2-baseline.json
uv run python scripts/eval_retrieval.py --configs keyword hybrid-fake --out-dir artifacts/bench
```

The full run indexes about 11 MB of text and takes several minutes; `--scale 10000` adds a synthetic
10,000-document corpus (generated by `scripts/generate_fixtures.py --eval-scale`, never committed) and
takes much longer, because every file is parsed in its own isolated subprocess. `--sample` runs the same
fast subset that `pytest` uses.
