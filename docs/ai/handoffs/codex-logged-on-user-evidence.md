# Reported user evidence for the approved endpoint resolver

Base: af5dfcadba7fc1a41090b53f235ed988daa3033d.

The approved UniqueOS own-workstation resolver needs MeshCentral's reported users.
Expose this through the SDK instead of making Django parse vendor raw payloads.
client.py adds a trailing defaulted Device field and validates the payload;
README documents unknown/empty distinctions, retained qualifiers and evidence limits.
test_client.py covers those states, malformed fields, immutable copies and
backward-compatible construction. No authentication, share, installation, permission
or application pin change.

Validation: Python 3.14, PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q:
26 passed. Before implementation, the initial regression tests produced six
assertion failures and 19 passes. Ruff check/format use --isolated because the
parent application's vendor exclusion otherwise silently selects no files.
No live vendor calls or credential access. No other Python version tested.

Risk: malformed users now rejects device listing with a fixed protocol error,
preventing ambiguous data from becoming identity evidence. Valid domain/case
qualifiers are unchanged; no ownership/freshness guarantee is inferred.
Rollback: revert SDK change. Owner reviews/merges; only then pin and consume
in UniqueOS. Live account/enrollment readiness remains a separate gate.
