# Same-paragraph co-editing spike — 14 October 2026

Recommendation for 0.2: **no-go for live co-typing**. Continue with host-serialized
operation batches, section leases and reviewable branch merges. The host, identity,
lease and review-request issues (#71, #81, #85, #86) are still prerequisites, not
implemented capabilities of this release. Revisit after those contracts exist.

## What can coexist

A text CRDT can be an input adapter inside a leased section. The host would remain
the authority: it must authenticate the principal, check scope and lease epoch,
validate the entire candidate, enforce protected passages and required checks,
and record one accepted operation batch with its request ID and provenance.
Convergence of a CRDT does not grant permission to commit a candidate.

A client must distinguish provisional local text from the last committed revision.
It cannot claim an edit succeeded until the host acknowledges the corresponding
batch. Reconnecting with an expired lease requires a new review proposal, not
replaying CRDT messages into the current authoritative text.

## Why it is not a drop-in replacement

- The engine uses Unicode scalar offsets; a CRDT typically uses stable character
  identities. Each host commit needs a versioned ID-to-scalar map and checks that
  splices land on grapheme boundaries. IME composition must stay provisional until
  committed; emoji ZWJ sequences and combining sequences cannot be split.
- A global revision precondition correctly rejects a concurrent stale edit.
  Removing that precondition without rebasing offsets can silently edit a
  different word. Request IDs make retries idempotent, not concurrent edits safe.
- Atomic multi-operation batches may include tables, moves, styles or metadata.
  A text-only convergence algorithm cannot independently commit part of that batch.
- Comments and tracked changes use anchors. The host must map their ranges through
  accepted splices, keep unresolved anchors visible, and attribute insertions and
  deletions to the principal that actually submitted them.
- Undo should be a new, principal-scoped inverse proposal against the current
  revision. Restoring an old snapshot would erase other participants' work.
- Formatting marks need a separate conflict policy. Automatically combining text
  does not determine whether a bold range should include another writer's words.
  Version 0.2 conservatively conflicts when both mapped mark sets disagree.

## Prototype and evidence

The bounded prototype is the offline grapheme diff3 in `src/byax/text_merge.py`,
integrated into the existing merge proposal path. It merges disjoint inserts,
deletes and replacements, uses the existing mark splice transformer, and reports
conflicting ranges in base-document scalar coordinates. It is **not a CRDT**, has
no transport or live presence, and does not provide online convergence guarantees.

`tests/test_text_merge.py` exercises concurrent inserts, overlapping replacements,
disjoint deletes/replacements, mark preservation and ambiguous mark changes,
emoji/ZWJ/combining sequences, and 200 seeded commutativity cases.
`tests/test_proposals_history.py` retains merge proposal acceptance, explicit conflict
resolution and history/provenance tests. These are local automated checks, not an
IME or multi-client network qualification.

## Cost and reconsideration criteria

No new dependency is justified yet. A future experiment must compare an established
CRDT implementation with host-side operational transformation. Measure retained
character IDs/tombstones per paragraph, memory after repeated edit/delete cycles,
serialized sync size, reconnect latency and map construction costs. Set explicit
limits for paragraph length, pending operations and retained tombstones before
network exposure; compaction must respect every connected/offline client's base.

Reconsider only when the host can reject a stale lease, unauthenticated edits and
partially valid batches; when dropped/reordered/duplicate messages converge; when
per-author undo preserves other authors' changes; and when range marks, comments,
tracked changes and IME composition pass the same tests on both desktop platforms.
