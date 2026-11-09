# Bounded native CI execution

This describes execution infrastructure, not evidence that the current source
has passed Windows or macOS validation. A source-only Linux result cannot prove
a native package, display/backend behavior, or a clean-machine installation.

## Cost and trigger policy

The existing `push`, `pull_request`, and `workflow_dispatch` triggers remain.
There is **no permanent quota gate** in this workflow. Before any hosted run,
verify the account's currently available included Actions quota and spending
settings, including the runner types and worst-case job duration. Do not launch
when free capacity cannot be verified. This integration's source commit must use
`[skip ci]`; no native run is authorized merely by the infrastructure change.
GitHub's skip instructions apply to push/pull-request workflows, not manual
`workflow_dispatch`. A later ordinary push may run this workflow automatically.

References: [GitHub skip behavior](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs)
and [workflow job/time-budget syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).

Manual dispatch selects exactly one standard runner: `windows-2022` (default)
or `macos-15`. Start with Windows; review its result and actual account usage
before separately dispatching macOS. Push/PR still validate both platforms,
now sequentially (`max-parallel: 1`); no required check or trigger is removed.
Each job runs
at most two local pytest subprocesses concurrently. No paid runner upgrade,
extra hosted test matrix, test retry loop, or dependency expansion is introduced.
A timeout is an upper bound, not an estimate or evidence of free quota.

The manual job's `contains()` platform guard compares strings without case
sensitivity; it rejects other labels, prefixes/suffixes and surrounding spaces,
not case-only variants. Matrix formatting preserves the supplied spelling rather
than normalizing it. Use the declared lowercase choice values when dispatching.
Local expression tests do not establish whether the dispatch API accepts an
undeclared spelling or whether a hosted runner is allocated for it; they are
not a runner-label resolution test.

Runs using this workflow version share one concurrency group across refs/events.
An in-progress run is not cancelled by another dispatch. GitHub keeps at most
one pending run and may replace it with a newer pending run; this is neither
FIFO delivery nor persistent deduplication of a source commit. Check queued,
running and completed runs before dispatching, and do not double-click dispatch.
Manual reruns using this version (`run_attempt > 1`) skip the desktop job, rather than spend another
native run automatically. Diagnose first, recheck quota, then use a fresh single-
platform dispatch if justified. Push/PR reruns remain available for existing checks.

These protections are not retroactive: rerunning a historical run or dispatching
an old ref can use its older workflow definition, without this guard or shared
concurrency group. Inspect the workflow revision as well as the source SHA before
execution. Never rerun a historical build to bypass quota/spending preflight.

Screenshots, packaging and package validation require prior success. Failure
diagnostics still run with a five-minute step limit; job timeout/cancellation or
runner loss can prevent their delivery. The full native regression and manifest
requirements are unchanged: Linux receipts are useful evidence but cannot be
substituted for this runner's complete test receipts. No automatic test retry,
partial-test certification or relaxed package gate is added.

Before integration, use `[skip ci]` on every source commit and inspect the run
queue after pushing; a normal push or PR can still consume two native jobs.
Do not use skip markers to evade a required merge check (skipped checks may stay
pending). Manual dispatch ignores the marker. This workflow does not query billing
or enforce an account spending cap: **do not dispatch until the actual remaining
compute/storage allowance and hard stop against paid overage are verified**.
The user's monthly allowance is not their current remaining balance. Never enable
paid overage or change billing/security settings as part of a build.

The Windows job has a 200-minute hard timeout, including setup/build/upload;
macOS retains 100 minutes. At the standard rates verified in November 2026,
200 Windows minutes correspond to at most approximately $2.00 and 100 macOS
minutes to $6.20 of compute usage; these are planning
figures, not account-balance claims or authorization to charge. Allow for rounding,
cleanup, other runs and separate artifact/cache storage; recheck pricing and the
account immediately before execution. Do not use historical 2x/10x multipliers
as exact current allowance deductions. See [runner pricing](https://docs.github.com/en/billing/reference/actions-runner-pricing).

## Local / native command

Windows full validation uses the measured scheduling profile and one explicit
slow-module exception. This is still a full `tests` collection, not a selection:

```sh
python scripts/run_bounded_tests.py --output build/test-shards --workers 2 --target-nodes 200 --collection-timeout 180 --shard-timeout 900 --suite-timeout 9000 --cost-profile scripts/windows_full_costs.json --target-seconds 450 --isolated-module-timeout tests/test_final_frame_preview_oracle.py=2100
```

macOS and the runner defaults retain the previous limits and unweighted command.
Install the project's pinned test dependencies first. Run from the source root:

```sh
python scripts/run_bounded_tests.py --output build/test-shards --workers 2 --target-nodes 200 --collection-timeout 180 --shard-timeout 900 --suite-timeout 3600
```

The output directory must not already exist. Use a different output directory
for another attempt, preserving failed evidence. The default selection is all
`tests`; optional positional file/node selectors are explicitly recorded in the
summary and do **not** represent full-suite validation. For example, the native
inspector preflight intentionally executes its three selected UI modules in one
fresh process; the subsequent full regression also covers those modules. These
are separate gates, not duplicate execution inside the full-suite receipt.

Windows uses a 150-minute whole-suite budget (including collection), a
152-minute full-test step, and a 200-minute job hard stop. The collection cap
remains 180 seconds and normal shard cap remains 900 seconds. Only the explicitly
named `test_final_frame_preview_oracle.py` singleton shard may use 2100 seconds.
macOS retains the 60-minute suite, 62-minute step, 100-minute job and 900-second
shard budgets. The suite's remaining time always clips a shard's actual timeout.
Process cleanup may add a small bounded grace period after a test budget.

`--isolated-module-timeout MODULE=SECONDS` is explicit, repeatable and separate
from scheduling weights. A module must be collected whole from a file/directory
selection and occupy exactly one shard without other modules; node/class-only
selection of that target is rejected. With an override, external `@response`
selectors and pytest option/selection arguments such as `-k`, `-m` or
`--deselect` are also rejected before collection. Internal exact shard response
files are unaffected; calls without overrides retain their prior behavior.
Duplicate, unknown, uncollected, mixed-
shard, nonpositive/nonfinite or greater-than-suite overrides fail before any
test shard launches. Cost profiles cannot silently enlarge timeouts. The runner
records requested overrides and resolved per-shard caps in plan/summary, and the
suite-clipped effective timeout in each process status. This is an execution
contract, not a sandbox against arbitrary plugins changing collection.

### Why the Windows limits changed, and what remains unknown

Windows targeted run `42445430613`, exact source `4b832adcce3f75281c4376bb2ce744d77b0c20ce`,
completed on 2026-11-08: 131 unique nodes, 129 passed, two explained platform skips,
no omissions or retries. The frozen/current-code module alone took 1389.828
seconds with one worker; the production-runtime module took 253.891 seconds.
The former cannot fit the old 900-second cap. Merely lowering `target-nodes`
cannot help because a module is never split. The maximum single node was
160.666 seconds; the accumulated module, not one long test, caused this mismatch.
This evidence does not certify a full regression or native package.

The profile combines that targeted run with full-run `42440005164` receipts.
At source `4b832adc`, 178 modules / 16,773 nodes had complete passing Windows
measurements, while 292 modules / 4,159 nodes had no complete native measurement.
That is 20,932 baseline nodes, not a count promised for future commits. Added
runner/policy checks are collected anew; their estimates are not represented as
native measurements. Profile provenance retains exact run IDs, archive hashes,
measured spans and unknown counts. It never supplies acceptance results.

Measured module spans receive 25% scheduling margin plus two seconds (four-second
minimum). Unmeasured modules use two seconds per node (five-second minimum), or
1.5 times an existing Linux range estimate if larger. Linux weights are explicitly
not Windows measurements. Module-preserving, long-first packing targets 450
estimated seconds and 200 nodes per ordinary shard. The known slow oracle goes
first in its own shard. Its 2100-second explicit cap is about 51% above the measured
1389.828 seconds; two-worker resource contention remains unmeasured.

With the baseline collection and known margin, sensitivity estimates for unknown
nodes at 0.5 / 1 / 2 / 3 seconds each gave roughly 84 / 99 / 133 / 167 minutes
with two workers, including the secondary Linux lower bounds. These are model
scenarios, not probabilities or deadlines guaranteed to suffice. The 150-minute
suite is a bounded compromise and can still time out, particularly in the
3-second unknown-tail scenario. Added checks and runner variability also matter.
The budget-change candidate freshly collects 21,051 nodes: all 20,932 baseline
nodes plus 119 runner/policy checks, without a removed or duplicate baseline
node. Its actual profile yields 98 shards, with the 41-node oracle first and
alone. Giving the two modified test modules at least two seconds per current
node updates those four modeled scenarios to approximately 87 / 103 / 136 / 170
minutes. Fresh collection and a computed plan are not native test completion.
The 200-minute job leaves up to 50 minutes outside a maximally used test suite,
but does not cover the sum of all other step caps (about 101 minutes). Do not
claim every stage can exhaust its cap and still finish. Setup, alignment,
PyInstaller, smoke and uploads retain independent limits. A timeout remains
failure; there is no automatic retry, partial certification or relaxed gate.

The default native dependency set is `.[test,build]`, not the optional `agents`,
`search`, or `tts` extras. Platform/dependency skips can differ from an enriched
Linux development environment. The collection receipt retains skipped-module
reasons; per-test phase reports and JUnit retain test skip/xfail reasons. Use the
actual per-platform counts and explanations, never copy a Linux pass total into
a native result.

## Exact-coverage contract

1. A clean Git-root run records source identity before collection and rechecks
   it before success: exact HEAD/tree plus a canonical SHA-256 over tracked
   relative paths, Git modes, and actual file bytes. Dirty tracked/index state,
   nonignored untracked files, symlinks/gitlinks, or source changes fail closed.
   Ignored generated outputs are excluded. Source-ZIP/non-Git roots remain
   usable, but record provenance as unavailable and cannot certify a native
   package. One fresh subprocess collects the entire requested selection. Duplicate,
   empty, multiline, errored, or timed-out collection fails before testing.
2. Stable largest-module-first packing assigns each collected node to exactly
   one shard. Whole modules remain together, including modules larger than the
   target size. This preserves module fixtures but does not reproduce all
   cross-module state of a single pytest session.
3. Each subprocess receives exact node IDs via pytest's response-file support,
   avoiding Windows command-line length limits. Hidden inherited pytest options
   and automatic third-party plugin loading are disabled. Required project
   conftests and the explicit evidence plugin still load. Temp paths, pytest
   caches, and known screenshot/render evidence paths are unique per shard.
4. Every shard must exit successfully and match its expected collection, starts,
   finishes, setup/call/teardown results, and JUnit testcase count. Setup skips
   omit a call but still require successful teardown. Explicit skips/xfails are
   counted separately; a teardown error, missing phase, duplicate phase, crash,
   dropped test, or extra test cannot become success.
5. The final gate rechecks all receipts and exact-once union coverage, then writes
   aggregate JUnit and a successful summary. There is no retry or silent resume.
   Failure stops new shards; already-running shards finish within their deadlines.
   Unstarted shards and incomplete evidence remain failures.

## Retained evidence

`build/test-shards/` contains:

- `plan.json`: full ordered collection, SHA-256 of its canonical node-ID list,
  and deterministic shard assignments. This digest identifies collection, not
  source provenance or cryptographic attestation. The source snapshot in the
  summary is separate consistency metadata, not a security attestation.
- `collection/`: collection log, status, append-only events, and final receipt.
- `shard-NNN/`: exact selector response file, pytest log, flushed event journal,
  atomic final receipt when available, JUnit, process status, and local artifacts.
- `summary.json`: limits, requested selection, actual skip/pass/xfail counts,
  completed/unstarted shards, duration, failure information, exact coverage,
  and start/end source provenance (or explicit unavailable reasons).
- `results.xml`: aggregate JUnit, created only after every shard is verified.

The journal records each start, report phase, and finish immediately, so a
hard-killed or crashed child still leaves useful partial evidence. A missing
final receipt is not a pass. On POSIX, timeout cleanup signals the entire process
group; on Windows it uses bounded `taskkill /T /F` and a direct-process fallback.
This covers ordinary spawned FFmpeg processes, not a sandbox against a test that
deliberately detaches itself from its process group or overrides the tooling.

CI attempts diagnostic upload after ordinary step failures, including the bounded-test directories,
preflight, UI alignment, FFmpeg/font provenance, frozen runtime logs/results,
build log, and PyInstaller warnings/xrefs/TOCs/spec. The smoke launch wrapper
uses `build/native-smoke-launch/` for combined stdout/stderr and process status,
leaving `build/native-smoke/` exclusively for the app to create. It requires a
zero process exit plus `passed: true`, `frozen: true`, and the matching platform.
CI supplies the exact archive via `--archive`; the wrapper fingerprints that
archive and the extracted executable before launch and after success, failing
if either changes. The final native manifest requires the same archive hash.
This is consistency checking, not an app-tree or security attestation;
a bootstrap failure before app output is created still has a launch receipt. Large caches, extracted build
trees, and temporary media are excluded from diagnostic uploads. Outer job cancellation, timeout, or runner loss can prevent the upload; local
flushed receipts are not a guarantee of artifact delivery. Native archives
remain unsigned drafts, and successful packaging alone is not release acceptance.


Windows caption shaping runtime
-------------------------------

Pillow 13 requires an external FriBidi provider for RAQM on Windows; a font
installation alone is insufficient. Before test collection, CI runs
`python scripts/provision_caption_runtime.py --build`. This downloads only the
SHA-256-pinned official FriBidi 1.0.18 source release, builds a shared x64 DLL with
MSVC and pinned Meson/Ninja, and checks an actual RAQM shaping call. No system
DLL is installed. The report records capability before and after provisioning.
The application preloads the verified DLL by absolute path before Pillow import;
missing capability is an error, never a BASIC-layout fallback.

The unsigned draft includes the DLL, its integrity receipt, LGPL-2.1-or-later
COPYING, complete corresponding upstream source archive and rebuild/replacement
instructions. Modified shared libraries can be used by updating the local DLL
hash in the receipt; these are consistency checks, not signatures or a restriction
on LGPL library replacement. Frozen smoke must recheck the shipped provider, and
the final manifest binds its observed versions to the provisioning report.

Local Windows developers use `pip install -e ".[test,build]"`, the Visual Studio
C toolchain, and the same provisioning command before pytest. `LUMEN_FRIBIDI_DIR`
can select a development payload directory; frozen applications ignore that
override and use their own packaged directory. Linux/macOS require a working
native RAQM provider and do not build or load the Windows DLL.

### Frozen-code versus production-runtime range diagnostics

Earlier range-only diagnostics used a one-attempt, 20-minute job cap,
480-second shard cap, 660-second suite cap and two workers. The optional
`--cost-profile scripts/range_diagnostic_costs.json --target-seconds 180` groups
whole modules by measured cost as well as node count. It never removes, retries,
reorders nodes within a shard, or splits a module. An oversized module occupies
its own shard. The 180-second target is a scheduling estimate, not an additional
hard timeout or a promise about Windows performance. Existing hard limits and
exact-once receipts remain authoritative. The profile hash binds the exact
bytes used to plan the run. The current Windows full-suite profile and limits
are described above; macOS/default scheduling remains unchanged. The current
manual final-frame dual-runtime diagnostic uses a separate 65-minute job cap
and is not a full-suite or packaging gate.

Each pytest phase now records duration and wall-clock endpoints. Stage JSONL
outside pytest temporary directories records reference/candidate runtime setup,
compilation and FFmpeg execution, including a start with no finish if interrupted.
Those stage records use node hashes and exception types, not media paths or
exception bodies. Python CPU time excludes child FFmpeg CPU. Existing pytest
failure logs retain their original detailed diagnostics and remain private.

Standalone SFNT caption fonts are validated from an owned byte snapshot and
published byte-for-byte in each renderer's private cache. Family and glyph checks
still run on every call. Content/face/policy keys invalidate changed sources;
cache bytes are verified, temporary publication is atomic, and cancelled writes
leave no partial file. TTC files still extract the requested face with stable
source timestamps; only bounded hash metadata, not font files, is cached in the
process. Stable discovery symlinks retain the existing resolve behavior, while
snapshot/cache identity checks reject replaced or nonregular entries. Frozen
application ZIPs, manifests and font selection/coverage assertions are unchanged.

In three local CJK cold-cache samples, preserving the original 16,437,364-byte
font reduced preparation from 18.90–19.62 seconds to 0.098–0.143 seconds. Strict
old/new masks, metrics and RAQM layout matched in 15 cases. Hot validation costs
increased from about 0.045–0.051 seconds to 0.101–0.102 seconds because current
source and cache bytes are checked. These are Linux measurements, not native
Windows results or cross-FFmpeg output-equivalence claims.
