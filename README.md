# mcp-evidence-gate-dogfood

Cross-repository dogfood for the [MCP Evidence Gate](https://github.com/yandexuanxuan/mcp-evidence-gate) GitHub Action.

This repository is an executable specification for downstream consumers. It keeps a small artifact and receipt fixtures, invokes the Action by immutable commit SHA, and asserts the expected release decision for each case.

## Decision matrix

| Case | Expected decision | Meaning |
| --- | --- | --- |
| matching clean receipt | `PASS` | The receipt binds to the artifact and satisfies the permissive policy. |
| warnings + permissive | `WARN` | Warning is preserved and explicitly allowed by policy; Action succeeds. |
| warnings + strict-release-example | `FAIL` | Warning is preserved but explicitly blocked by policy; Action fails. |
| digest mismatch | `INCONCLUSIVE` | The evidence cannot support this artifact; this is not a server-safety claim. |
| stale receipt | `INCONCLUSIVE` | The evidence freshness window has expired. |
| findings receipt | `FAIL` | The scanner reported findings. |
| malformed receipt | `FAIL` | The receipt fails structural conformance. |
| explicit evidence match (permissive) | `PASS` | Bound artifact and locally verified evidence report satisfy permissive policy; Action succeeds. |
| explicit evidence mismatch (permissive) | `INCONCLUSIVE` | Explicitly supplied evidence report fails digest binding even under optional policy; Action fails. |
| malformed optional evidence digest | `FAIL` | Malformed known SHA-256 evidence digest fails validation even when no evidence path was supplied; Action fails. |
| unrequested unsupported evidence digest | `PASS` | Well-formed non-SHA-256 evidence digest is ignored when no evidence was supplied under optional policy; Action succeeds. |

The workflow uses `continue-on-error: true` for the Action step because `FAIL` and `INCONCLUSIVE` are intentional test outcomes. A following assertion checks both the emitted `decision` and the GitHub Actions step `outcome`. `PASS` must produce a successful step; `FAIL` and `INCONCLUSIVE` must produce a failed Action step. The workflow is green only when both values match the matrix.

## Scanner execution completeness

Zero findings do not prove that a scanner completed. The Producer PR
`Xander-Xai/mcp-evidence-producer-trivy#4` is pinned at
`deb5c2cf225f8043b133e5bf1a39813c4c65f6c1` and records the project-defined
`scanner_execution` evidence extension. This repository adds a separate
consumer policy in [`scripts/scanner_completeness_policy.py`](scripts/scanner_completeness_policy.py):
an evidence file must be digest-bound and have complete required execution
evidence before it is eligible for Core Gate evaluation.

[`scanner-completeness-consumer.yml`](.github/workflows/scanner-completeness-consumer.yml)
executes complete-clean, complete-findings, incomplete/failed zero-findings,
missing/malformed completeness, and evidence-tampering cases. It also invokes
the real Producer on a consumer-owned clean artifact, then sends the retained
evidence through the immutable Core Gate. The current Core verifier does not
interpret project-defined scanner metadata, so a self-consistent false-clean
case is retained as `CORE_CHANGE_REQUIRED` evidence: Core's raw decision may be
`PASS`, while the dogfood consumer admission remains blocked.

## Workflow

The workflow runs on pushes and manual dispatch:

- `.github/workflows/mcp-evidence-gate.yml` calls the reviewed Action head `9bed76881fa040c78618c23d911664e919ad90cf` by full immutable commit SHA.
- `.github/workflows/real-trivy-producer.yml` is an isolated real-scanner consumer: it verifies pinned Linux Trivy v0.74.0 bytes, checks out Producer PR head `deb5c2cf225f8043b133e5bf1a39813c4c65f6c1`, and calls gate `d404b38f0ac0303438b561fe7358b0eec487c962`. Its only scanned input is the consumer-owned `evidence/real-trivy/requirements.txt`; runtime artifacts stay in the CI temp directory.
- `.github/workflows/real-osv-producer.yml`, `.github/workflows/real-multi-receipt-composition.yml`, and `.github/workflows/real-oci-identity.yml` use the same immutable Producer PR head and retain their scanner-specific source, composition, and OCI identity assertions.
- `.github/workflows/scanner-completeness-consumer.yml` is the promotion evidence for the Producer execution-completeness boundary. It keeps Core's raw decision separate from the dogfood consumer admission and records `CORE_CHANGE_REQUIRED` when the pinned Core verifier ignores incomplete project-defined execution metadata.
- `dist/example-artifact.bin` is marked as binary in `.gitattributes` so Windows line-ending conversion cannot change its digest.
- Receipts live under `evidence/` and are intentionally small, deterministic fixtures.

This repository runs deterministic fixture checks plus explicitly retained real Producer/Consumer workflows; it does not claim that the example server is safe. It verifies the downstream release-admission contract: scanner verdict, scanner execution, artifact binding, freshness, policy decision, and CI step outcome remain distinct. Warning blocking is an explicit policy choice.
