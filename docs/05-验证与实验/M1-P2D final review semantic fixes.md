# M1-P2D Final Review semantic fixes

## Semantic fingerprint v2

`sp-result-fingerprint:v2` is the complete machine-semantic representation of
an `SPProcessingResult`. It includes formal quality output, limitations,
engineering-unit declarations, integrity, stable windows, internal metrics and
evaluations, hashed filter arrays, complete beat details, and reference match
pairs. NumPy arrays are represented by dtype, shape, and content SHA256.
Floating content and scalar evidence use 12 significant decimal digits before
hashing so equivalent IEEE-754 results remain stable across Windows and Linux;
integer and boolean arrays retain their exact C-order byte representation.

Container/session identity, `software_commit_sha`, normalized host receive
timestamps, transport-reader provenance, and the derived `result_sha256` itself
are intentionally excluded. `summarize_sp_result()` remains the compact golden
surface; the fingerprint, comparator, and result hash carry complete semantics.

The golden migration from v1 to v2 changes only each attempt's
`fingerprint_version` and `result_sha256`. Labels, reasons, formal metrics,
windows, blocking status, reference counts, and attempt ordering are unchanged.

## Frozen contract baselines

- M1-P0 contracts: `4375759e0361efcf595ead656d55f42ae0ae50c6`
  (main merge commit for PR #28; matches final feature head `dd78620`).
- M1-P1 simulator schemas: `c2d60a5b7e71a195207019bd413551b03c88d27a`
  (main merge commit for PR #30; matches final feature head `01ca160`).

The formal gate distinguishes `unchanged`, `changed`, `baseline_unavailable`,
and `error`. The full-history exact-source checkout in CI remains unchanged.
