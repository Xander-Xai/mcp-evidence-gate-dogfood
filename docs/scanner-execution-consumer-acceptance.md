# Scanner execution consumer acceptance

Status: project-defined dogfood evidence. This document does not change the
MCP Registry receipt schema or the Core Gate implementation.

## Scope and immutable inputs

- Dogfood baseline `main`: `e9db561` (audited before modification).
- Producer PR: `Xander-Xai/mcp-evidence-producer-trivy#4`.
- Promoted Producer `main`: `3b4862245ce1778d52d6a3b58f8b1b8cb4906dfb` (CURRENT).
- Producer PR #4 pre-merge head: `575a1230290b610297152e44dc6dd5b6ac6c04e9` (SUPERSEDED AS ACTIVE IDENTITY).
- Producer baseline `main`: `4c4d9bd476396cd7e34e9d4182900ac00b03d17b`.
- Core promoted `main`: `1c5a6cfae2901b97fc0925d0b102710d9a73cb82` (CURRENT).
- Core PR #14 pre-merge accepted head: `b8e39635350929e93d4f302d423ea551cd7da763` (PRE_MERGE_ACCEPTED_CORE_HEAD; SUPERSEDED AS ACTIVE IDENTITY).
- Previous Core acceptance: `c5467b94d9bc80c4728cceabfe567ef78ff1fa0c` (SUPERSEDED).
- Previous Dogfood acceptance head: `a431faf84bd69a0d4ad79731ad8e7c29880c690e` (SUPERSEDED).
- Registry compatibility profile (unchanged):
  `registry-pr-1404@20747d3253ba8638161dd95f1cec70df02993c22`.

The workflows use the promoted Core `main` Action directly at
`Xander-Xai/mcp-evidence-gate@1c5a6cfae2901b97fc0925d0b102710d9a73cb82` and,
on the same fixture, compare it with the bundled `dist/action/index.cjs` from
that exact checkout. The remote job asserts `REMOTE_ACTION_LOAD=PASS`, every
declared Action output, and equality of the remote and bundled decisions,
integrity, receipt, policy, admission, scanner-execution, and reason-code
fields.

The promoted Producer `main` and Core PR #14 identities were independently
queried through GitHub before this repository was changed. The Producer's
Trivy, OSV, and OCI paths use the additive
`project-defined-scanner-execution-v1` evidence extension. The earlier
Producer acceptance at `deb5c2cf225f8043b133e5bf1a39813c4c65f6c1` is
historical and explicitly `SUPERSEDED`; all prior evidence remains retained
for audit only.

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

## External Core P1/P2 acceptance

`scripts/run_core_snapshot_acceptance.mjs` checks the exact built Core commit
`1c5a6cfae2901b97fc0925d0b102710d9a73cb82` from an external consumer. P1
injects a reader that would return bytes A and then B, and asserts Core reads
once and verifies the detached A snapshot for both binding and scanner
semantics. P2 invokes the Core CLI boundary surface for:

| Boundary | Expected result |
| --- | --- |
| no evidence path | scanner `missing`, integrity `inconclusive`, decision non-PASS |
| missing evidence file | scanner `missing`, integrity `inconclusive`, decision non-PASS |
| present digest mismatch | scanner `unverified`, integrity `inconclusive`, decision `inconclusive` |
| valid digest + missing scanner object | integrity `pass`, scanner `missing`, strict decision `fail` |

The script does not reimplement Core verification; it imports Core's snapshot
and scanner functions for P1 and invokes Core's built CLI for P2.

## Producer-generated acceptance

The workflow invokes promoted Producer `main` against the consumer-owned
`evidence/real-trivy/clean/requirements.txt`, retains `receipt.json`,
`evidence.json`, `trivy.raw.json`, and the scanner-execution record, then runs
the remote Core Action at the exact SHA with `strict-scanner-completeness`
(`decision=pass`, `integrity-status=pass`, `receipt-status=valid`, and
`scanner-execution-status=complete`) and compares it with the immutable Core
CLI. A deterministic derivation changes only the
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
`3b4862245ce1778d52d6a3b58f8b1b8cb4906dfb` and Core
`1c5a6cfae2901b97fc0925d0b102710d9a73cb82`. Existing OSV workflow assertions
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

The current acceptance is tied to the new Dogfood commit and the fresh
workflow run/job IDs recorded in PR #12. Those runs must all target the same
new Dogfood head, promoted Producer `3b4862245ce1778d52d6a3b58f8b1b8cb4906dfb`,
and Core `1c5a6cfae2901b97fc0925d0b102710d9a73cb82`; local tests are supporting
evidence rather than a substitute for the hosted run. The prior Dogfood head
`a431faf84bd69a0d4ad79731ad8e7c29880c690e` and its old Producer/Core pins are
`SUPERSEDED`.

The exact branch-head acceptance is `POST_MERGE_EXACT_MAIN_ACCEPTANCE = PASS`:
the aggregate promotion gate and each independent consumer workflow completed
successfully with zero skipped critical jobs. The branch remains intentionally
unmerged; the exact workflow run/job identifiers are recorded in PR #12.
The immediately previous PR #12 head `3600e62afc6596d2f9fb9ccbb0ad7b1ea1ba2a21`
is retained as `SUPERSEDED_AS_ACTIVE_IDENTITY`.

The promoted Core packaging check is `POST_MERGE_REMOTE_ACTION_LOAD = PASS`:
the remote Action loaded `action.yml` and `dist/action/index.cjs` from Core
`1c5a6cfae2901b97fc0925d0b102710d9a73cb82`, and all ten declared outputs plus
the remote-vs-bundled comparison were asserted successfully.

## Core promotion gate

`CORE_CHANGE_VERIFIED = YES`.

At Core `1c5a6cfae2901b97fc0925d0b102710d9a73cb82`, strict-scanner-completeness
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
