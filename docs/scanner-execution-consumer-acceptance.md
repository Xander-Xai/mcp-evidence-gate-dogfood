# Scanner execution consumer acceptance

Status: project-defined dogfood evidence. This document does not change the
MCP Registry receipt schema or the Core Gate implementation.

## Scope and immutable inputs

- Dogfood baseline `main`: `e9db561` (audited before modification).
- Producer PR: `Xander-Xai/mcp-evidence-producer-trivy#4`.
- Producer exact PR head: `575a1230290b610297152e44dc6dd5b6ac6c04e9`.
- Producer baseline `main`: `4c4d9bd476396cd7e34e9d4182900ac00b03d17b`.
- Core Gate exact SHA used by the new acceptance workflow:
  `c5467b94d9bc80c4728cceabfe567ef78ff1fa0c`.
- Registry compatibility profile (unchanged):
  `registry-pr-1404@20747d3253ba8638161dd95f1cec70df02993c22`.

The Producer PR was independently checked through GitHub before this
repository was changed: it remained OPEN at the exact head above, its
pull-request CI runs targeted that SHA, and its Trivy, OSV, and OCI paths used
the additive `project-defined-scanner-execution-v1` evidence extension. The
earlier Dogfood acceptance at Producer
`deb5c2cf225f8043b133e5bf1a39813c4c65f6c1` is historical and explicitly
`SUPERSEDED`; it is retained for audit only.

## Baseline gap

The dogfood baseline had deterministic receipt/Action cases and real Trivy,
OSV, multi-receipt, and OCI workflows, but no consumer check for the
Producer-owned `scanner_execution` evidence. Core Gate verifies evidence
content binding but does not interpret that project-defined metadata. Therefore
`clean receipt + digest-bound incomplete execution evidence` was a confirmed
Core-layer gap before this change.

## Consumer acceptance invariant

```text
INCOMPLETE_SCANNER_EXECUTION -> MUST NOT PASS
```

The dogfood policy requires the evidence digest to match the receipt and
requires all Producer execution predicates to be true before the case is
eligible for Core evaluation. Missing, malformed, incomplete, or failed
execution evidence is `blocked` (project-defined `INCONCLUSIVE` boundary).
This policy is not Core `SecurityScanReceipt v1` semantic validation.

## Deterministic matrix

| Case | Findings | Execution | Policy | Core at pinned SHA | Overall dogfood admission |
| --- | ---: | --- | --- | --- | --- |
| complete-clean | 0 | complete | eligible | PASS | PASS candidate |
| complete-findings | >0 | complete | eligible | FAIL | blocked |
| producer-results-empty | 0 (`Results=[]`) | incomplete | blocked | FAIL | blocked |
| incomplete-zero-findings | 0 | incomplete | blocked | FAIL | blocked |
| failed-zero-findings | 0 | failed | blocked | FAIL | blocked |
| missing-completeness | 0 | absent | blocked | FAIL | blocked |
| malformed-completeness | 0 | invalid | blocked | FAIL | blocked |
| contradictory-complete | 0 | contradictory | blocked | FAIL | blocked |
| tampered-evidence | 0 | failed after mutation | blocked | INCONCLUSIVE | blocked |

The stable Producer-schema fixture is
`evidence/scanner-completeness/zero-findings-incomplete/`; its raw report has
zero findings while its execution status is `failed`. The
`producer-results-empty` case is generated from the checked-out Producer with
an otherwise valid Trivy report whose `Results` array is empty; the Producer
returns exit `1`, receipt `inconclusive`, and failed `result_sections` rather
than manufacturing a clean verdict.

## Producer-generated acceptance

The workflow invokes the pinned Producer against the consumer-owned
`evidence/real-trivy/clean/requirements.txt`, retains `receipt.json`,
`evidence.json`, `trivy.raw.json`, and the scanner-execution record, then runs
the immutable Core CLI. A deterministic derivation changes only the
Producer-generated execution record to create:

1. a self-consistent clean receipt with failed scanner execution; and
2. a tampered evidence file whose receipt digest is intentionally stale.

Expected and independently asserted results are:

```text
Producer-generated clean: findings=0, scanner_execution=complete
Producer-generated false-clean: Core=FAIL (strict), dogfood admission=BLOCKED
Tampered evidence: binding failure, dogfood admission=BLOCKED
```

The first line is the Producer result and the strict Core result is `PASS` for
that complete-clean evidence. The second and third lines prove that strict
scanner-completeness evaluation, integrity verification, and dogfood semantic
admission remain separate axes.

The dedicated `producer-results-empty-acceptance` job runs the same checked-out
Producer with a process-local scanner stub that returns `Results=[]`. It
asserts Producer exit `1`, receipt `inconclusive`, execution
`incomplete/result_sections` with reason `trivy_result_sections_missing`,
strict Core `FAIL`, and Dogfood admission `blocked`.

The OSV `source-binding-mismatch-acceptance` job supplies a report whose source
path differs from the consumer artifact. It asserts Producer exit `1`, receipt
`inconclusive`, execution `incomplete/source_binding` with reason
`artifact_ref_mismatch`, strict Core `FAIL`, and Dogfood admission `blocked`.

## OCI and OSV non-regression

Existing real OCI workflow assertions continue to require root index digest,
selected platform descriptor, exact platform manifest bytes, selected digest
binding, and complete scanner execution while checking out Producer
`575a1230290b610297152e44dc6dd5b6ac6c04e9` and Core
`c5467b94d9bc80c4728cceabfe567ef78ff1fa0c`. Existing OSV workflow assertions
keep exit 0/1, package/source/lockfile, raw-result consistency, and the
unavailable database snapshot boundary while requiring complete execution
evidence; its additional source-binding mismatch job is a P2 negative
acceptance. The real Trivy consumer and multi-receipt composition use the same
active pins, and strict composition proves one incomplete receipt cannot be
hidden by a complete receipt.

## Exact-head validation record

The previous hosted acceptance runs
(`35056731744`, with implementation head `41171c05e45ef679b648f3fe62c0d078bb45806c`,
and the later seven-case Dogfood head `ec4522cd846f79720b5742c17e2189be5e0bfe6a`)
are retained as historical evidence and are `SUPERSEDED` because they used
Producer `deb5c2cf225f8043b133e5bf1a39813c4c65f6c1` and did not include the
`Results=[]` or OSV `source_binding` acceptance.

The current acceptance is tied to the final Dogfood commit and the fresh
workflow run/job IDs recorded in PR #12. Those runs must all target the same
final Dogfood head, Producer `575a1230290b610297152e44dc6dd5b6ac6c04e9`, and
Core `c5467b94d9bc80c4728cceabfe567ef78ff1fa0c`; local tests are supporting
evidence rather than a substitute for the hosted run.

## Core promotion gate

`CORE_CHANGE_VERIFIED = YES`.

At Core `c5467b94d9bc80c4728cceabfe567ef78ff1fa0c`, strict-scanner-completeness
evaluation returns `pass` only for complete-clean evidence. A digest-bound
false-clean, a Producer `Results=[]` receipt, and an OSV source-binding
mismatch all return Core `fail` with scanner execution incomplete; the Dogfood
policy independently returns `blocked` with integrity `pass`. This verifies the
Core-side strict boundary without changing Registry v1 fields.

## Boundaries

This work does not modify Core, Registry #1404, `scan_scope`,
`install_time_execution`, package/name custody, publisher identity, signatures,
scanner reputation, or upstream repositories. It does not claim Registry
adoption or server safety.
