# Shared response deadline

Base55d899b053862b77f822ddd42baa3ec59b0f2724. UniqueOS #4504 owner-merged publishing admission; tracked prerequisite in UniqueOS docs/audit/convergence-mesh-sdk/DEADLINE.md.

Use one monotonic deadline after sending a command. Each receive gets its remaining budget; unrelated/non-object/mismatched messages do not reset it. Reject a returned response at/after expiry and retain MeshTimeout conversion, connection cleanup, separate connect/close timeouts and no command retries. README states this is a response budget, not an end-to-end send/setup/teardown deadline.

Five fake-clock regressions fail baseline, 12 existing tests pass. Corrected suite17 passed; isolated Ruff check and diff check pass. Tests cover unrelated object/list/mismatched messages, correlated success before expiry, late correlated rejection, existing timeout/auth/protocol and share contracts. No live MeshCentral call, secret, provisioning or endpoint action.

Risk low: preserves public APIs and timeout exception, intentionally ends event-starved waits. Owner merges SDK first; then a separate UniqueOS pin-bump can consume it. No app pin to an unmerged SDK branch.
