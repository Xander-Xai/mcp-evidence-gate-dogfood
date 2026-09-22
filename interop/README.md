# Cross-implementation scanner execution corpus

This is a frozen, dependency-free interoperability corpus. It compares the
safety outcome (`clean_eligible`) rather than implementation-specific verdict
strings. The corpus is evidence for independent implementation corroboration;
it does not claim Registry adoption or an MCP standard.

Provenance:

- Xander Producer `mcp-evidence-producer-trivy` main: `94403156c57059dcce3d224f9b4c321fee32445e`
- Xander Core `mcp-evidence-gate` main: `1306bdcb52aaba025819611435682e69b4bff4f0`
- Xander Dogfood main: `29f5172466204a5c589e126a0e92acfe5e7a3bfb`
- Agentgate audited commit: `fc97e7546e2ad3f52bfefa5153d01088635c8098`
- Agentgate source: `docs/spec/scan-execution-v1.md`

The fixtures are intentionally local JSON projections. CI does not install,
clone, or execute Agentgate; the immutable source reference and mapping are
the reproducibility boundary.
