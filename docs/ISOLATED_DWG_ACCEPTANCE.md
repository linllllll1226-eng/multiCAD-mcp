# Isolated DWG acceptance

This opt-in runner supplies file-lifecycle evidence for an explicitly authorized
test drawing. It does not modify the normal guarded server's tool list and does
not start AutoCAD. The supported target is an already running AutoCAD 2022 COM
session. Use an empty drawing template and a fresh evidence directory per run.

```powershell
python scripts/Run-Isolated-Dwg-Acceptance.py --evidence-dir D:\CAD-tests\new-run
```

The project defaults to the script's parent repository; `--project` can select
another checkout. The runner exposes the normal guarded tools plus
`cad_acceptance_document(action)`. Starting the stdio server alone makes no CAD
connection. Lifecycle calls acquire the CAD operation lock and initialize COM
on the calling thread.

1. Call `prepare` to create an empty `TEST_fixture_block_<run-id>.dwg`. The
   existing documents' paths, save flags, DBMOD, entity counts and on-disk hashes
   are recorded before creation. The test uses millimetres. An existing run or
   nonempty template causes a failure instead of overwriting drawings.
2. Perform all geometry writes through `cad_plan_validate`, `cad_execute_plan`
   and `cad_verify_execution`, bound to this test drawing. Keep their complete
   outputs in the evidence directory. `snapshot` records observed state only.
3. Call `save_close`. It saves only the active, exact test path, requires a clean
   saved state, records the SHA-256, closes it without a second save, then checks
   that the target is absent and the other documents match their baseline.
4. Stop the runner and start a **new server process** with the same evidence
   directory. Call `reopen`. It rejects the closing process's PID, missing close
   evidence, changed saved bytes, or an already open target. It reacquires
   `ActiveDocument` rather than trusting the generic COM return from `Open`.
5. Read and compare real entities after reopening, including native dimension
   types, definition points and measurements. Preserve these fresh results next
   to the plan, execution, verification, lifecycle JSON and source comparison.

A successful `reopen` explicitly returns `geometry_verified: false`: process and
file evidence does not establish entity correctness, source completeness, or
production-release readiness. PID checks and local JSON are an operational audit
trail, not tamper-proof attestations. Unit tests simulate process identities; they
are not live-DWG acceptance. Do not attach historical DWG results to a new build
as if that build had been exercised.

## Failure and recovery

The state file is atomically replaced and an exclusive `.acceptance.lock` blocks
concurrent calls. A process crash can leave the lock behind: inspect the process
and CAD state before manually recovering it. Never automatically remove a lock
or reset a pending phase. Any change to another open drawing, including removal
of an initial blank document by AutoCAD, fails the strict baseline comparison.

Saving, closing and reopening record a pending phase **before** their COM call.
An interrupted operation cannot be retried as though nothing happened. The
special `reclose` action is available only in `reopening`, after a failed open
left the exact test target active. It requires a clean document and unchanged
saved bytes, then records a new closing PID. Start another server process before
retrying `reopen`. Other pending phases require manual inspection; preserve the
failed evidence and use a fresh run for a new acceptance attempt.

COM calls themselves are not forcibly cancellable. The active-document polling
after `Open` is bounded to ten seconds; this does not bound `Open` itself.
The state schema is version 1 and deliberately rejects the older one-off script's
state files. Keep those historical artifacts intact and use a fresh directory.

## Native dimension readback

Radial and diametric dimensions can omit definition points from their COM
properties. The verifier then uses a fixed read-only AutoLISP expression to read
DXF groups 10 and 15. Responses are bound to a nonce, handle, active drawing and
dimension type; incomplete or nonfinite coordinates fail verification. Polling
for the response is bounded, but AutoCAD's `SendCommand` itself is not cancellable.
The fallback does not create or change entities. A failed read remains a
verification error, including layout-only checks. Explicit `measurement` takes
precedence; otherwise the planned radius or diameter is compared with the native
dimension's reported measurement.
