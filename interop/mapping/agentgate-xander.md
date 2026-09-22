# Agentgate ↔ Xander semantic mapping

| Semantic concept | Xander consumer contract | Agentgate scan-execution/v1 | Equivalence |
|---|---|---|---|
| required work | `required_components` | `components[].required` / `required` | equivalent set cardinality, different shape |
| completed work | `completed_components` | `components[].status=completed` / `completed` | equivalent |
| failed work | `failed_components` | `components[].status=failed` / `failed` | equivalent |
| execution state | `completeness_status` | `state` | equivalent safety meaning; names differ |
| output presence | `output_exists`, `output_present` | `components[].output_present` | equivalent when component-scoped |
| output parseability | `output_parseable` | `components[].output_parseable` | equivalent when component-scoped |
| semantic validity | `result_semantics_consistent` | `semantic_consistency` | equivalent safety meaning; Xander is aggregate |
| findings | receipt/evidence report | `components[].findings` | NOT_EQUIVALENT: Xander receipt does not expose severity counts |
| digest binding | `evidence_digest` and Core evidence check | `digest.matches` | NOT_EQUIVALENT: binding locations and trust model differ |

The shared safety rule is narrower than field equality: unknown status,
unknown semantic consistency, unknown severity, incomplete, failed, malformed,
or unverified evidence is never clean-eligible.
