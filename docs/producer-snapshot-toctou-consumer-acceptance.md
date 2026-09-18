# Producer snapshot TOCTOU Consumer Acceptance

This Dogfood layer independently checks the promoted Producer main at
`32eb55168b82991df689c943ae23fbb0ae6867dd` against promoted Core
`52c224b36a54e450dbe25ad56988f29e4795753e`. The previously accepted
pre-merge Producer head `3462989a9f52a14e680e0c63b402beb84649f922` remains
historical and is not an active runtime pin.

The acceptance does not import Producer self-tests as proof. It imports the
exact checked-out Producer in a separate runner harness and exercises its real
`run()` paths with adversarial subprocess boundaries:

```text
subject A -> Producer snapshot(A) -> source becomes B -> scanner reads A
```

It records scanner argv, proves the target is not the original subject path,
checks the bytes actually read, verifies receipt/evidence/scan-input identity,
and sends the same receipt/evidence through promoted Core twice. Core admits
the restored A subject and rejects the same evidence against subject B.

The matrix also covers OSV, snapshot mutation after a valid scanner result,
missing-source failure without an empty SHA-256 receipt, and concurrent
invocation metadata isolation. Real pinned Trivy and OSV runs, OCI identity
non-regression, and composition coverage are retained as hosted artifacts.

The temporary snapshot path is scanner execution metadata only. Receipt and
evidence subject references remain the original consumer path, and downstream
Core does not require the temporary path to survive. Producer correctness does
not weaken Core/Dogfood consumer distrust. The historical Dogfood B2 head
`3077a2df228831e32ec08f83a2c8672e6040e71a` remains pre-merge Producer
acceptance evidence only.
