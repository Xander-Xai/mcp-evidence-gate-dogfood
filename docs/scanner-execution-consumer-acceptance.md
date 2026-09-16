# Scanner execution consumer acceptance

Status: project-defined dogfood evidence. This document does not change the
MCP Registry receipt schema or the Core Gate implementation.

## Scope and immutable inputs

- Dogfood baseline `main`: `e9db561` (audited before modification).
- Producer PR: `Xander-Xai/mcp-evidence-producer-trivy#4`.
- Producer exact PR head: `deb5c2cf225f8043b133e5bf1a39813c4c65f6c1`.
- Producer baseline `main`: `4c4d9bd476396cd7e34e9d4182900ac00b03d17b`.
- Core Gate exact SHA used by the new acceptance workflow:
  `d5abcdc28d6e288da15ec69b16901ec604da5617` (`0.1.0-alpha.3`).
- Registry compatibility profile (unchanged):
  `registry-pr-1404@20747d3253ba8638161dd95f1cec70df02993c22`.

The Producer PR was independently checked through GitHub before this
repository was changed: it remained OPEN at the exact head above, its
pull-request CI runs targeted that SHA, and its Trivy, OSV, and OCI paths used
the additive `project-defined-scanner-execution-v1` evidence extension.

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
| incomplete-zero-findings | 0 | incomplete | blocked | PASS (Core gap evidence) | blocked |
| failed-zero-findings | 0 | failed | blocked | PASS (Core gap evidence) | blocked |
| missing-completeness | 0 | absent | blocked | PASS (Core gap evidence) | blocked |
| malformed-completeness | 0 | invalid | blocked | PASS (Core gap evidence) | blocked |
| tampered-evidence | 0 | failed after mutation | blocked | INCONCLUSIVE | blocked |

The stable Producer-schema fixture is
`evidence/scanner-completeness/zero-findings-incomplete/`; its raw report has
zero findings while its execution status is `failed`.

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
Producer-generated false-clean: Core=PASS, dogfood admission=BLOCKED
Tampered evidence: binding failure, dogfood admission=BLOCKED
```

The first line is the producer result; the second and third lines prove that
integrity verification and semantic admission are separate axes.

## OCI and OSV non-regression

Existing real OCI workflow assertions continue to require root index digest,
selected platform descriptor, exact platform manifest bytes, selected digest
binding, and complete scanner execution. Existing OSV workflow assertions keep
exit 0/1, package/source/lockfile, raw-result consistency, and the unavailable
database snapshot boundary while requiring complete execution evidence.

## Exact-head validation record

Executable acceptance head: `41171c05e45ef679b648f3fe62c0d078bb45806c`.
The exact-head hosted validation run for that implementation head was
successful:

- [MCP Evidence Gate Dogfood promotion](https://github.com/Xander-Xai/mcp-evidence-gate-dogfood/actions/runs/35056731744)
  — head SHA `41171c05e45ef679b648f3fe62c0d078bb45806c`; it includes the
  scanner-completeness seven-case matrix and Producer-generated acceptance
  (`producer-generated acceptance` job `104668379125`), Trivy (`104668378974`),
  OSV (`104668379181`), OCI (`104668379074`), multi-receipt (`104668379130`),
  and the final `promotion-gate` (`104668474298`).

The branch may carry a later documentation-only closure commit; that commit
does not change the executable acceptance tree validated above. The hosted
external-runtime predicate is therefore `PASS`; local tests remain supporting
evidence rather than a substitute for the hosted run.

## Core promotion gate

`CORE_CHANGE_REQUIRED = YES`.

Evidence: at the pinned Core SHA, a self-consistent receipt with
`verdict=clean`, matching artifact/evidence digests, and
`scanner_execution.completeness_status=failed` returns Core decision `pass`.
The dogfood consumer policy returns `blocked` with integrity `pass`, so the
overall dogfood admission cannot pass. This is a concrete Core gap, not an
assumption; the next Core task may add an explicit project-defined evidence
policy hook without changing Registry v1 fields.

## Boundaries

This work does not modify Core, Registry #1404, `scan_scope`,
`install_time_execution`, package/name custody, publisher identity, signatures,
scanner reputation, or upstream repositories. It does not claim Registry
adoption or server safety.
