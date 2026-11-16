# Diode, diode-netbox-plugin and netbox-branching: correctness and performance fixes

Technical documentation for an external review by the NetBox Labs engineering team.

**Author:** Alioth Latour (fork owner) · **Date:** 2026-11-16 · **Status:** proposed changes, carried in personal forks only; nothing has been submitted upstream.

---

## Contents

1. [Overview](#1-overview)
2. [Repositories and branches](#2-repositories-and-branches)
3. [Summary of results](#3-summary-of-results)
4. [Changes in Diode (diode-server)](#4-changes-in-diode-diode-server)
5. [Changes in diode-netbox-plugin](#5-changes-in-diode-netbox-plugin)
6. [Changes in netbox-branching](#6-changes-in-netbox-branching)
7. [Benchmark methodology](#7-benchmark-methodology)
8. [Benchmark results](#8-benchmark-results)
9. [Validation](#9-validation)
10. [Upstream compatibility](#10-upstream-compatibility)
11. [Limitations and open items](#11-limitations-and-open-items)
12. [How to reproduce](#12-how-to-reproduce)
13. [Questions for the reviewers](#13-questions-for-the-reviewers)

---

## 1. Overview

### Purpose

This work started as a performance comparison of Diode against its upstream code. While measuring, the benchmark exposed correctness problems that matter more than throughput: under moderate, realistic load the upstream pipeline **duplicates objects, loses updates and records ingestion results that do not match what NetBox contains**. When Diode writes into a netbox-branching branch, further problems appear: writes **lost during a merge**, branch operations **running twice concurrently**, and main changes **never reaching a branch** after a sync.

The goal was to:

1. find each problem with a reproducible scenario, not by reading code alone;
2. fix it in the smallest reasonable way, in the component where it originates;
3. show the effect of each fix with a before/after measurement on the same harness;
4. keep every raw output, so the conclusions can be checked independently.

### What was done

- An **end-to-end benchmark harness** (`bench/` in the Diode fork) that runs the full stack in Docker: SDK-built load → ingester → reconciler → diode-netbox-plugin → NetBox (+ netbox-branching). It compares the upstream code ("baseline") with the fork ("candidate") on identical infrastructure, and checks the final NetBox state, not only throughput.
- **Scenario scripts** for netbox-branching race conditions (merge or sync while Diode is writing into the branch, double merge, late-committing main transactions).
- **Fixes in three repositories**:
  - 10 commits in diode-server;
  - 3 in diode-netbox-plugin;
  - 12 code commits in netbox-branching.
- **A written diagnostic and one specification per change** (`specs/` in each fork). These follow a spec-driven workflow and record decisions, rejected alternatives and a timestamped log. They are written in Portuguese (the author's working language); this document is the English summary of them.

### Problems addressed

| Area | Problem | Impact |
|---|---|---|
| Diode | `bulk-plan-apply` resent after a 5 s client timeout while NetBox was still applying the first request | duplicated IPs, lost updates, false ingestion results |
| Diode | claimed batch applied out of ingestion order | ingestion log credits the wrong entity |
| Diode | Postgres deadlocks in the reconciler's repository, and a retention truncate failure reported as a persist failure | whole batches recorded as FAILED |
| Diode | a non-JSON NetBox error body could not be stored | entities stranded in APPLYING, silently |
| Diode + NetBox/granian | a retry sent on a keep-alive connection that a 503 had left unread | apply hangs until the 120 s timeout, batch FAILED |
| Diode + netbox-branching | a branch that is syncing/merging answered **400** to every request; Diode recorded the batch as failed | data loss for the whole sync window |
| diode-netbox-plugin | NetBox 4.7's ltree "hierarchy was modified concurrently" error was not retried | a whole batch returned 500 |
| diode-netbox-plugin | the apply was not atomic on the branch connection | partial writes; false "deleted in main" |
| netbox-branching | writes in progress when a merge/sync read the change log were never merged (F2) | **silent data loss**, made permanent by `DROP SCHEMA` on archive |
| netbox-branching | status transitions without compare-and-set (F1) | two merges of the same branch; a merged branch reopened |
| netbox-branching | sync watermark based on `ObjectChange.time` (F3) | a main change committed late never reaches the branch |
| netbox-branching | `ChangeDiff.current` not updated while syncing (F4); sync commits on two connections (F15) | missed conflicts; a sync that fails forever on replay |

---

## 2. Repositories and branches

| Project | Upstream | Fork | Branch with the changes |
|---|---|---|---|
| Diode (diode-server) | [netboxlabs/diode](https://github.com/netboxlabs/diode) | [aliothbl/diode](https://github.com/aliothbl/diode) | [`develop`](https://github.com/aliothbl/diode/tree/develop) (code changes only), [`perf/bench-vs-upstream`](https://github.com/aliothbl/diode/tree/perf/bench-vs-upstream) (code + benchmark harness + evidence + this document) |
| diode-netbox-plugin | [netboxlabs/diode-netbox-plugin](https://github.com/netboxlabs/diode-netbox-plugin) | [aliothbl/diode-netbox-plugin](https://github.com/aliothbl/diode-netbox-plugin) | [`develop`](https://github.com/aliothbl/diode-netbox-plugin/tree/develop) |
| netbox-branching | [netboxlabs/netbox-branching](https://github.com/netboxlabs/netbox-branching) | [aliothbl/netbox-branching](https://github.com/aliothbl/netbox-branching) | [`main`](https://github.com/aliothbl/netbox-branching/tree/main) (one `spec/*` branch per change is also pushed) |
| diode-sdk-python | [netboxlabs/diode-sdk-python](https://github.com/netboxlabs/diode-sdk-python) | [aliothbl/diode-sdk-python](https://github.com/aliothbl/diode-sdk-python) | no code change; the SDK is used unmodified by the load generator |

Base revisions (the fork branches contain all upstream commits up to these points):

- **diode:** upstream `develop` at `519ccec` (2026-11-05);
- **diode-netbox-plugin:** upstream `develop` at `8c3a3bb`;
- **netbox-branching:** upstream `main` at `bf2e88d` (merged into the fork on 2026-11-16, see §10).

Key locations in the Diode fork (`perf/bench-vs-upstream`):

- `bench/`: the harness ([`bench/README.md`](../bench/README.md), `run.sh`, `compose*.yaml`, `loadgen.py`, `branch_scenarios.py`, `tools/`).
- `bench/results/`: one JSON per run, `*.rows.json.gz` (full NetBox end state), and `*.logs/` (all container logs, from 2026-11-15 on).
- [`bench/evidence/`](../bench/evidence/README.md): console output of every benchmark battery and test suite, indexed.
- `specs/`: diagnostic and specifications for Diode; `specs/active/SPEC-20261114-0807-benchmark-performance-vs-upstream/report.md` is the original (Portuguese) benchmark report.

---

## 3. Summary of results

**Diode against upstream** (scenario `std`: 200 devices, 800 interfaces and 800 IP addresses = 1,800 entities per phase; NetBox 4.7.0; mean ± standard deviation):

| Configuration | Metric | Upstream | Fork |
|---|---|---|---|
| Default (concurrency 1) | create throughput | 8.66 ± 1.89 ent/s | **11.34 ± 0.20 ent/s** (+31%) |
| | apply timeouts per run | ~44 | **0** |
| | IP addresses in NetBox (expected 800) | 1276 / 962 / 934 / 974 / 950 / 962 | **800 in every run** |
| Concurrency 4 | create throughput | 15.98 ± 0.29 ent/s ¹ | **20.08 ± 0.17 ent/s** |
| | identical re-ingest | 16.4 ± 0.2 ent/s | **84.7 ± 0.0 ent/s** |
| | entities recorded FAILED | **100%** | 0% |
| | IP addresses in NetBox | 1518–1608 | **800** |
| | interfaces with a lost update | 136–189 | **0** |
| **Best configuration of each** | create | 8.66 ent/s, wrong final state | **20.08 ent/s, correct final state (2.3×)** |

¹ With concurrency 4 the upstream "throughput" measures how fast entities *finish* (as FAILED), not how fast they are applied correctly.

Every fork run ends with a **byte-for-byte identical** NetBox state (hashed dump, §7). Every upstream run ends in a different state.

**netbox-branching race conditions** (Diode writing into a branch; NetBox 4.7.5):

| Finding | Scenario | Before | After |
|---|---|---|---|
| F6: branch syncing → HTTP 400 | `branch-sync-ingest` ×5 | 190–265 of 540 entities FAILED; devices never reach the branch | **0 FAILED, 0 missing, 0 timeouts (5/5)** |
| F2: writes during merge | `branch-merge-race` ×5 | **45–112 objects lost per merge** | **0 lost (5/5)** |
| F1: double merge | `branch-double-merge` ×8 | update: merged twice (5/5); create: branch ends READY after its merge (3/3) | **exactly one merge applies, final MERGED (8/8)** |
| F3: late-committing main change | `branch-sync-watermark` ×5 | the change never reaches the branch (5/5) | **synced by the next sync (5/5)** |

A final regression battery over the latest code of all three repositories (19 runs) was green: 0 FAILED, 0 timeouts, 0 lost or missing objects (§9.3).

---

## 4. Changes in Diode (diode-server)

All changes are in `diode-server/`. Commit IDs refer to the fork's `develop`; the same changes exist on `perf/bench-vs-upstream` under different IDs, listed in brackets.

### 4.1 M-1: never resend a `bulk-plan-apply` that reached NetBox — [`e7d79d8`](https://github.com/aliothbl/diode/commit/e7d79d8) [`14f4147`]

**Problem (P-1, critical).** The reconciler's NetBox client used one HTTP transport for reads (`/bulk-plan/`, `/default-branch/`) and for writes (`/bulk-plan-apply/`):
- `ResponseHeaderTimeout` is 5 s;
- `go-retryablehttp` uses its default policy, which **retries on timeout**. This is documented as intentional in `netboxdiodeplugin/client.go`.

The plugin applies a batch of 100 entities serially, one transaction per entity, which took about 9 s on the test machine. When the 5 s timeout expired, the NetBox worker kept applying; the reconciler resent the batch, and a second worker applied the same entities in parallel. NetBox accepts duplicate IP addresses by default, so both copies were created about 20 ms apart. Observed effects:
- 134–476 extra IP addresses per run (concurrency 1), 718–808 (concurrency 4);
- 136–189 interfaces without their new description (concurrency 4);
- entities created by the request that timed out recorded as `NO_CHANGES`;
- at concurrency 4, 100% of entities recorded as `FAILED` although the data was in NetBox;
- each batch applied up to 4 times (1 + 3 retries), saturating NetBox.

The problem reproduces with only 50 entities (smoke scenario: 3 timeouts, 4 duplicate IP addresses).

**Change.** The apply client has its own transport and timeout (`NETBOX_DIODE_PLUGIN_APPLY_TIMEOUT_SECONDS`, default **120 s**). Reads keep the 5 s timeout and the existing retry. The apply is retried **only** when the request provably did not start:
- a connection (dial) error;
- 429 or 503, which a server sends before processing (e.g. granian backpressure).

**Trade-off.** If an apply really exceeds 120 s, the batch is now recorded as `FAILED` instead of being resent. NetBox still finishes it, and the next re-ingest records `NO_CHANGES`. A conservative record is preferred over duplicated data.

**Tests:** `netboxdiodeplugin/client_apply_retry_test.go`, 6 cases:
- no resend after a timeout;
- retry on 503 and on 429;
- no retry on 500;
- apply timeout independent of the read timeout;
- `/bulk-plan/` still retried;
- invalid timeout rejected.

### 4.2 M-2: apply a claimed batch in ingestion order — [`67fbd71`](https://github.com/aliothbl/diode/commit/67fbd71) [`14ba79f`]

**Problem (P-7).** The claim query selects the oldest rows (`ORDER BY id` in a subquery), but `UPDATE … RETURNING` does not preserve that order; the plan joined through a `HashAggregate`. In a fresh load an IP address (which carries its interface and device nested) was often applied before its device. It created the device, and the device's own entity ended `NO_CHANGES` (173 of 200 devices in one run). NetBox's final state was the same, but the record of which entity did what was wrong and changed between runs.

**Change.** `ClaimQueuedIngestionLogs` and `ClaimQueuedForAutoApply` sort the batch by id before returning it (`dbstore/postgres/repository.go`). **Tests:** `claim_order_internal_test.go`.

**Effect.** In the initial load all 1,800 entities end `APPLIED` (before: up to 146 false `NO_CHANGES` at concurrency 1). About 23 remain at concurrency 4, which is expected from concurrency between different batches.

### 4.3 Default auto-apply concurrency 4 — [`5564c69`](https://github.com/aliothbl/diode/commit/5564c69) (develop only)

**Change.** `AUTO_APPLY_PROCESSOR_CONCURRENCY` defaults to 4 in `reconciler/config.go` and `docker/sample.env`. The profile (§8.4) shows that at concurrency 1 only one of NetBox's 4 workers is busy, because the cost is Python CPU in a single worker. With M-1 in place, concurrency 4 is safe and nearly doubles throughput.

**Not changed: the Helm chart.** It is deliberately left identical to upstream. Helm users opt in with `diodeReconciler.config.autoApplyProcessorConcurrency`. Rule of thumb: concurrency ≈ number of NetBox API workers.

This is a default-value change and is the most debatable item; it can be dropped from an upstream proposal independently of everything else. The benchmark branch keeps the default at 1 and sets the concurrency explicitly per run.

### 4.4 M-4: retry repository transactions that lose a deadlock — [`d9dd64f`](https://github.com/aliothbl/diode/commit/d9dd64f) [`91ecf9d`]

**Problem (P-9, part 1).** Under concurrent auto-apply (8 workers, batch 25), the reconciler's Postgres raised `40P01` deadlocks between claim, dedup and the counter triggers, and a whole batch ended `FAILED`.

**Change.** Repository transactions are re-executed on serialization or deadlock errors: up to 5 attempts, with backoff and jitter. In later stress runs, deadlocks on claim still occurred but were absorbed without error.

### 4.5 M-5: a post-commit truncate failure is not a persist failure — [`93aa636`](https://github.com/aliothbl/diode/commit/93aa636) [`cff6f70`]

**Problem (latent, found by reading the code).** `BulkPersistChangeSets` returned the failure of the retention truncate, which runs **after** the commit, as a failure of the persist. An already `APPLIED` batch could be marked `FAILED`, and in the plan-only flow change sets were written twice.

**Change.** The truncate failure is logged, not returned (`persistThenTruncate`). **Tests:** 3 unit tests (`persist_truncate_internal_test.go`). This was not the cause observed in P-9 (that was M-6, §5.1), but it closes a path to the same symptom.

### 4.6 M-7: a non-JSON NetBox error body cannot strand entities — [`1c5b5dd`](https://github.com/aliothbl/diode/commit/1c5b5dd) [`9e453e1`]

**Problem (P-11).** When NetBox answered an apply with a non-JSON error body (for example netbox-branching's plain-text 400 during a sync), `changeset.NewError` built an invalid JSON detail. Storing the `FAILED` state then failed, and the batch stayed in `APPLYING` with no error. The entities disappeared silently: 190 entities in the trial run that found it.

**Change.** Non-JSON details are quoted as a JSON string (`reconciler/changeset/changeset.go`), and the stored error is built with `ingestionLogErrorJSON`. **Tests:** `changeset/error_test.go`, `error_json_internal_test.go`.

### 4.7 Requeue a batch NetBox refuses as unavailable — [`f082189`](https://github.com/aliothbl/diode/commit/f082189) [`2919153`] (SPEC-1748)

**Problem (F6, Diode side).** A branch that is syncing or merging is temporarily unusable. The reconciler treated any status ≥ 400 as a failure of every entity in the batch.

**Change.** This works with the netbox-branching change in §6.1. `netboxdiodeplugin.UnavailableError` (status, `Retry-After`, `retryable`, body) is returned for:
- 503 and 429;
- 409 whose JSON body says `"retryable": false`.

`reconciler/ops.go` (`deferBulkPlanApply`) puts the batch's rows back to `QUEUED` (`RequeueApplyingIngestionLogs`) and returns every result as `Deferred`, with no `FAILED` and no failure placeholder:
- **retryable:** wait `Retry-After`, or 5 s by default;
- **branch unusable** (409: the default branch is merged, archived, failed or pending migrations): wait at least 30 s, log an ERROR, and request a refresh of the cached default branch. While the configured branch stays unusable, ingestion **fails closed**: the backlog waits instead of being consumed as failures.

The auto-apply worker waits `RetryAfter` before it claims again. The policy-driven auto-apply path in `ingestion_log_processor.go` handles `Deferred` too.

**Tests:**
- `ops_unavailable_test.go`: 503 with and without `Retry-After`, 409 fail-closed;
- `auto_apply_deferred_internal_test.go`: the wait, and the wait honouring cancellation;
- `client_apply_retry_body_test.go`.

### 4.8 Send each `bulk-plan-apply` on its own connection — [`40956af`](https://github.com/aliothbl/diode/commit/40956af) [`117b107`]

**Problem (P-10 / P-12).** Under granian, a response sent **without reading the request body** leaves the keep-alive connection desynchronised. This applies to netbox-branching's 503 (before §6.1's body drain) and to granian's own backpressure 503. The Go client's retry on that connection never completed and hit the 120 s timeout, and the batch became `FAILED` with no error on the NetBox side.

**Change.** `req.Close = true` (`Connection: close`) on apply requests. An apply lasts seconds, so a new connection per apply has no measurable cost, and a poisoned connection can never be reused.

**Validation.** An A/B test under `GRANIAN_BACKPRESSURE=1` (§8.3) went from 208–338 apply timeouts per run to 0.

### 4.9 Metrics for refused batches — [`d366af2`](https://github.com/aliothbl/diode/commit/d366af2) [`032e894`]

Two counters, `bulk_apply_deferred_total` and `bulk_apply_deferred_ingestion_logs_total`, both with attribute `reason` = `unavailable` | `branch_unusable`.
- An alert on `branch_unusable > 0` catches a default branch setting that points at a merged or archived branch.
- A sustained `unavailable` rate points at a branch stuck in a transitional status.

**Tests:** the processor tests require exactly this metric with the reason and batch size.

---

## 5. Changes in diode-netbox-plugin

### 5.1 M-6: retry NetBox 4.7's ltree hierarchy deadlock, per entity — [`ae04547`](https://github.com/aliothbl/diode-netbox-plugin/commit/ae04547)

**Problem (P-9, observed cause).** NetBox 4.7 maintains tree paths with triggers. `netbox/models/ltree.py` converts the resulting `40P01` deadlock into `ValidationError("The hierarchy was modified concurrently; please retry.")`. The plugin's deadlock retry recognised only `OperationalError`, so the whole batch was answered with HTTP 500.

**Change (`api/views.py`).** This error is now retried like a database deadlock. After the retries are exhausted, it becomes an error of that entity only (`db_deadlock_after_retries`), not of the batch.

**Tests:** 9 (`tests/v4.7.x/tests/test_apply_hierarchy_deadlock_retry.py`).

**Applies to** upstream `8c3a3bb` with any NetBox ≥ 4.7.

### 5.2 Make an apply atomic on the active branch's connection — [`163d202`](https://github.com/aliothbl/diode-netbox-plugin/commit/163d202), [`938afa0`](https://github.com/aliothbl/diode-netbox-plugin/commit/938afa0)

**Problem (P-13).** With netbox-branching, the ORM routes writes to the branch's own database alias (`schema_<id>`), but the plugin's `transaction.atomic()` only covers `default`. The apply's writes therefore ran in autocommit on the branch connection:
- a failure in the middle of an entity left partial writes;
- an object became visible before its `ChangeDiff`, so a concurrent request could see a false "deleted in main".

This showed up as 2–4 FAILED "deleted in main" in 2 of 5 runs of the F6 scenario after the other F6 fixes.

**Change.**
- `api/branch_transaction.py`: `active_branch_atomic()` opens an atomic block on the active branch's connection, or does nothing without a branch.
- `api/views.py`: the apply runs inside it (outer) plus `default` (inner).
- `api/applier.py`: the create-or-find fallback takes a savepoint on **both** connections (`with active_branch_atomic(), transaction.atomic():`). Without the second savepoint an IntegrityError aborted the branch transaction, and the fallback's lookup got a 500. That regression was seen in the F6 v3 run: 75–125 FAILED.

**Tests:** `tests/v4.7.x/tests/test_apply_branch_atomic.py`.

---

## 6. Changes in netbox-branching

The numbering F1–F15 comes from the diagnostic in the fork, `specs/DIAGNOSTICO-2026-11-14.md` on branch [`spec/20261114-1736-benchmark-reproducao`](https://github.com/aliothbl/netbox-branching/tree/spec/20261114-1736-benchmark-reproducao). Each change has a specification under `specs/active/` on `main`.

### 6.1 HTTP status for an unavailable branch (F6) — [`44f5b50`](https://github.com/aliothbl/netbox-branching/commit/44f5b50), [`e3bcd51`](https://github.com/aliothbl/netbox-branching/commit/e3bcd51) (SPEC-1741)

**Problem.** Any request naming a branch that is not `ready` received `400 "Selected branch is not ready."`, whether the branch was briefly syncing or permanently merged. A client cannot tell "wait" from "give up". Diode recorded every entity of every batch sent during a sync as failed: 190–265 of 540 entities in the scenario, and 21–29 devices never reached the branch.

**Change (`middleware.py`, `utilities.py`).**
- **Transitional statuses** (provisioning, syncing, migrating, merging, reverting) → `503 Service Unavailable` with `Retry-After` (setting `unavailable_retry_after`, default 5 s).
- **Terminal or blocked statuses** (merged, archived, failed, pending-migrations) → `409 Conflict`.
- Body `{"detail": "Selected branch is not ready.", "retryable": true|false}`. It deliberately does not name the branch or its status, because it is returned before authentication.
- A header naming a branch that does not exist stays `400`.
- **The request body is drained before refusing** (`discard_request_body`). Without this, granian's keep-alive connection was left desynchronised (P-12; see §4.8).

**Documented in** `docs/rest-api.md`.

### 6.2 Wait for in-flight writes before a merge or sync reads the change log (F2) — [`bd9e7be`](https://github.com/aliothbl/netbox-branching/commit/bd9e7be) (SPEC-1738)

**Problem (critical).** `merge()` read and materialised the branch's change log, and only then set `MERGING`. The middleware checks `ready` only at the start of each request. A write request that started before the merge, such as a Diode `bulk-plan-apply` of tens of seconds, kept committing into the branch schema after the read:
1. those changes never reached main;
2. the branch became `MERGED` with no warning;
3. archiving the branch (manually or by auto-archive) runs `DROP SCHEMA`, which deleted them for good.

`sync()` had the same window. Measured: **41–76** objects lost per merge on 2026-11-14 and **45–112** on 2026-11-15, in 6/6 and 5/5 runs.

**Change.**
- **Write requests take a shared lock.** Every write request (non-safe method) to a branch takes a session-level **shared advisory lock** keyed by the branch (`pg_try_advisory_lock_shared(0x4E425752, branch.pk)`) on the default connection, and holds it until the response (released in `finally`).
  - **Session-level, not transaction-level:** a write request spans several transactions on two connections (branch + default), so the original spec's `pg_advisory_xact_lock` would not cover the whole request.
  - **The request never waits:** with `pg_try_…`, if an operation holds the lock exclusively, the request gets the 503 of §6.1 at once.
  - **The status is re-read under the lock:** a merge may have switched the status and be waiting for this very lock.
- **Merge and sync take it exclusively.** The order is now **switch status → take the lock exclusively → read the change log**. The exclusive lock is taken in a savepoint with a local `lock_timeout` (`write_drain_timeout`, default 300 s). On timeout the status returns to `READY` and the operation fails (`BranchWriteLockTimeout`).
- **Only HTTP write requests take the lock.** Reads don't need it. Writes outside HTTP (scripts, jobs using `activate_branch`) do not take it either; §6.5 adds a check that catches them.

**Tests:** `tests/test_merge_concurrency.py` (7):
- a write request holds the lock until its response;
- a read request does not take it;
- a write is refused while an operation holds the lock;
- the status is re-checked under the lock;
- a merge gives up (back to `READY`) if in-flight writes do not finish;
- merge and sync read the change log only after switching the status and taking the lock.

### 6.3 Compare-and-set on every status transition (F1, F14) — [`ee957fe`](https://github.com/aliothbl/netbox-branching/commit/ee957fe), [`23c4558`](https://github.com/aliothbl/netbox-branching/commit/23c4558) (SPEC-1737)

**Problem.**
- **Unconditional transitions.** `sync()`, `merge()`, `revert()` and `migrate()` checked `self.ready` on an in-memory instance, then ran an unconditional `update(status=…)`. With two RQ workers and two requests (double click, client retry), both jobs passed the check.
  - A branch with only UPDATEs was merged twice.
  - When the second merge failed on duplicate CREATEs, its rollback set `READY` over the first merge's `MERGED`. The merged branch accepted writes and merges again.
  - The F2 lock made this worse: the second merge now waited for the first and then ran.
- **F14.** The `record_applied_change` `post_save` handler was disconnected only on the happy path or in the `except`. An exception after the commit, e.g. in a `post_merge` receiver, left it connected in the worker.

**Change (`models/branches.py`).**
- **One helper for every transition.** `_transition_status(expected, new, **fields)` runs `UPDATE … WHERE pk = %s AND status IN (expected)` and reports whether it did. It is used for:
  - entering sync, merge, revert and migrate;
  - every rollback, which only restores the previous status if the branch is still in this job's transitional status;
  - every final transition, with the lifecycle fields (`last_sync`, `merged_time`, `merged_by`) written in the same conditional UPDATE.
- **Provision** claims `NEW → PROVISIONING` before creating anything. Before, a second provision job (an RQ retry, a re-enqueue) tried to recreate the schema, failed, and its failure path ran `DROP SCHEMA` on the schema the first job had built.
- **Archive** flips to `ARCHIVED` *before* `DROP SCHEMA`, in the same transaction, and only from a status no job is working in. The conditional UPDATE locks the branch row:
  - a revert or sync that has not started yet waits, then finds the branch archived;
  - one already running makes the archive abort without dropping the schema it is using.

  New exception: `BranchStatusChanged`.
- **F14.** `post_save.disconnect` moved to `finally`.

**Tests:** `tests/test_status_transitions.py` (12):
- basic CAS;
- second merge/sync aborts without effect;
- a failed merge does not reopen a merged branch;
- the final transition is conditional;
- a `post_merge` exception disconnects the receiver;
- provision only from `NEW`;
- archive refused in every transitional status;
- archive during a revert keeps the schema;
- double archive records one event.

### 6.4 Sync watermark that respects commit visibility (F3) — [`fd67a8a`](https://github.com/aliothbl/netbox-branching/commit/fd67a8a), [`69bdb57`](https://github.com/aliothbl/netbox-branching/commit/69bdb57), [`ca39c1c`](https://github.com/aliothbl/netbox-branching/commit/ca39c1c) (SPEC-1739)

**Problem.** `ObjectChange.time` is set by Python when the change is saved, not when its transaction commits.
- A main transaction saves a change at T0 and commits at T2.
- A sync starts at T1 (T0 < T1 < T2): it does not see the change and stores `last_sync = T1`.
- Every later sync filters `time > T1` and skips the change forever.

Diode's own bulk applies are long transactions in main, so this is not a theoretical window.

**Change.**
- **The watermark reaches back to the oldest open transaction.** The sync reads the start of the oldest open transaction in `pg_stat_activity` and stores the watermark as `min(sync start, that transaction's start) − sync_watermark_margin` (default 5 s, for clock skew between NetBox and PostgreSQL).
  - The look-back is capped at 1 hour (`SYNC_WATERMARK_MAX_LOOKBACK`), so a connection left idle in a transaction cannot make every sync re-read hours of changes.
- **Deduplication.** The changes of the overlap window are deduplicated by a new model, **`SyncedChange`** (branch, main ObjectChange; unique). Each sync records what it applied, `get_unsynced_changes()` excludes those, and rows older than the new watermark are pruned at the end of each sync.
- **A separate field.** The watermark is stored in a new field, **`Branch.sync_watermark`**. `last_sync` keeps its meaning (sync time, used for staleness). The first version stored the watermark in `last_sync`; it fixed the scenario but broke 12 existing tests, because `last_sync` moved backwards.
- **Migration** `0010_syncedchange` (the field plus the model), a `sync_watermark` filter, and the `sync_watermark_margin` setting documented in `docs/configuration.md`.

**Rejected alternative:** a watermark from the PostgreSQL snapshot (`xmin` / `pg_current_snapshot()`). The 32-bit `xmin` does not convert safely to the `xid8` that `pg_visible_in_snapshot` takes, and VACUUM freezes `xmin`.

**Tests:** `tests/test_sync_watermark.py` (6):
- watermark without open transactions;
- watermark reaching an open transaction;
- bounded look-back;
- **a change committed after the sync's read arrives in the next sync** (fails before the fix);
- already-synced changes skipped;
- uniqueness.

### 6.5 Merge check and `DROP SCHEMA` guard (F2, defence in depth) — [`3379225`](https://github.com/aliothbl/netbox-branching/commit/3379225) (SPEC-1738)

- **Merge check.** Before a merge commits, it verifies that the branch holds no change beyond the change log it read. A write made outside a request (which does not take the §6.2 lock) now fails the merge: the transaction rolls back, the branch stays `READY`, and the next merge includes the change.
  - The original spec proposed comparing the branch's ObjectChange count with AppliedChange. That was rejected: AppliedChange links the branch to the ObjectChanges the merge creates **in main**, which never match one-to-one, because squash collapses changes and iterative can add side effects.
- **`DROP SCHEMA` guard.** `archive()` refuses to drop the schema of a merged branch that holds changes recorded after `merged_time` (`BranchHasUnmergedChanges`). The message suggests reverting and merging again, or deleting the branch to discard them. Auto-archive logs the error and continues with the next branch.

**Tests:** `tests/test_merge_verification.py` (4, with real provisioned schemas):
- a late write during an iterative merge and during a squash merge → merge rolled back, nothing in main, the next merge applies both changes;
- archive refused with a post-merge change, and the schema kept;
- a normal archive works.

### 6.6 Sync robustness: F15, monotonic watermark, F4 — [`ca0ca8b`](https://github.com/aliothbl/netbox-branching/commit/ca0ca8b) (SPEC-1739)

- **F15: commit order across two connections.** The sync writes main's changes into the branch schema through the branch connection, and its bookkeeping (`SyncedChange`, `ChangeDiff`, watermark) through `default`. These are two commits with no 2PC; `PREPARE TRANSACTION` needs `max_prepared_transactions > 0`, which defaults to 0.
  - **Before:** if the default commit was lost after the branch commit, the next sync replayed every change onto a branch that already had them, and failed on each CREATE ("Site with this ID already exists"). The branch's sync was then stuck for good.
  - **Change:** both connections reach the **same database**, so `SyncedChange` rows and `sync_watermark` are now written **through the branch connection** (raw SQL with main's schema name explicit, since that connection's `search_path` puts the branch schema first). They commit atomically with the changes they describe.
  - **What can still be lost** if the default commit fails: `ChangeDiff` updates and the pruning of old `SyncedChange` rows. That leaves stale conflict data until the next change to the object, and extra rows that the next sync prunes; nothing is replayed.
- **Monotonic watermark (a bug in the F3 fix, found by the F15 test).** The watermark (sync start − margin) could fall **before** the lower bound the sync read from. This happens when a sync runs within the margin of provisioning, whose `last_sync` is its end time. The next sync then re-read changes the provisioning snapshot already contained, and failed on their CREATEs. The watermark is now `max(computed, current lower bound)`.
- **F4.** `ChangeDiff.current` was updated from main only for `READY` branches. A change main made while a branch was syncing or migrating was never reflected, so a later merge overwrote it without reporting a conflict. It is now tracked in `READY`, `SYNCING`, `MIGRATING` and `PENDING_MIGRATIONS`. `MERGING` and `REVERTING` are excluded, because main's changes in those statuses are the merge or revert itself.

**Tests:** `tests/test_sync_replay.py` (3, provisioned schemas):
- **F15:** the default connection's commit really fails after the branch commit, injected only on the sync's closing commit. The next sync neither fails nor alters the branch.
- **Monotonic watermark:** a sync right after provisioning, then another.
- **F4:** a main change during `SYNCING`/`MIGRATING`/`PENDING_MIGRATIONS` reaches `ChangeDiff.current` and is reported as a conflict.

The first two tests were also run with the respective fix removed, and fail. Those logs are kept in the evidence.

### 6.7 409 for branch actions refused because of the branch's status — [`2647696`](https://github.com/aliothbl/netbox-branching/commit/2647696) (SPEC-1737)

**Change.** `sync/`, `merge/`, `revert/`, `archive/` and `recover/` answered `400` (plain text) when the branch was not in a status from which the action can start. They now answer **`409 Conflict`** with `{"detail": "…"}`:
- the request is well-formed, and the conflict is with the resource's state;
- this matches what the plugin already returns for unacknowledged conflicts and, since §6.1, for a terminal branch.

`400` remains for refusals on other grounds (a pre-action validator). The new 409 responses are declared in the OpenAPI schema and documented in `docs/rest-api.md`.

**This is a public API contract change** and was an explicit decision of the fork owner. It is independent of every other change and can be dropped.

### 6.8 Require NetBox 4.7.4 (test suite green on the declared minimum) — [`86f9568`](https://github.com/aliothbl/netbox-branching/commit/86f9568) (SPEC-1916)

**Problem.** On NetBox 4.7.0 (then the declared minimum), 33 tests of the plugin's own suite fail on **unmodified** `main`, mostly cable merge/revert/sync tests. Bisecting across NetBox patch releases gives failures per version:

| NetBox | 4.7.0 | 4.7.1 | 4.7.2 | 4.7.3 | 4.7.4 |
|---|---|---|---|---|---|
| failures in those 33 tests | 33 | 33 | 12 | 1 | **0** |

The cause is in NetBox core: before 4.7.4 some cable changes are not change-logged.

**Change.** `min_version = '4.7.4'`, CI also tests `v4.7.4` (`.github/workflows/test.yml`), and `COMPATIBILITY.md`, `docs/netbox-docker.md`, `AGENTS.md` and `.copier-answers.yml` are updated. The full suite is green on 4.7.4 and 4.7.5.

---

## 7. Benchmark methodology

### 7.1 Machine and limits

- **Host:** 12 cores, 31 GB RAM, Linux 6.8, Docker 29.1.3. One machine for every run; see the limitations in §11.
- **Container limits, identical for baseline and candidate:**
  - NetBox: 4 CPU / 4 GB, 4 granian workers;
  - each Postgres (NetBox's and Diode's): 2 CPU / 1 GB;
  - reconciler: 2 CPU;
  - ingester: 2 CPU.

### 7.2 Stack (`bench/compose.yaml`)

- **NetBox** 4.7.0 for the Diode comparison and 4.7.5 for the branching scenarios (`BENCH_NETBOX_VERSION`). One image for both sides; the plugin code is mounted, so **only the code under test changes**.
- **Diode ingester and reconciler** run as statically built binaries, mounted into the containers.
  - Baseline: upstream `519ccec`.
  - Candidate: the fork commit named in each result file.
  - Both are built with Go 1.27.2; upstream's `go.mod` asks for ≥ 1.26.10.
- **Separate Redis and Postgres** for NetBox and for Diode.
- **Not in the stack, on either side:** Hydra and nginx. A stub (`bench/auth_stub.py`) answers token and introspection requests. This measures the data plane; the plugin caches introspection, so in production that cost is amortised too.

### 7.3 Load and phases

**Load (`bench/loadgen.py`).** Entities are built with the official Python SDK classes and sent over gRPC directly to the ingester, 50 entities per request, with a fixed seed. Scenarios:
- `smoke`: 50 entities;
- `std`: 200 devices, 800 interfaces and 800 IP addresses = 1,800 entities per phase;
- `large`: 1,000 devices, 8,000 interfaces and 8,000 IP addresses = 17,000 entities.

**Phases.**
- `initial`: create.
- `reingest`: identical payload.
- `update`: interface descriptions change.

A phase ends when the stream is empty and no ingestion log is `QUEUED` or `APPLYING` for two consecutive reads.

### 7.4 Clean start, final state and resources

- **Clean start.** Every run restores a snapshot of the migrated NetBox database ("seed", keyed by the plugin and branching code and the NetBox version) and empties Diode's database.
- **Final state (`bench/netbox/dump_state.py`).** Every row of the touched models is dumped without primary keys or timestamps. Relations, including GenericFKs, are rendered by the related object, and tree paths are ignored. Two correct runs produce the same hash.
- **Resources and diagnostics.**
  - `docker stats` is sampled continuously.
  - From 2026-11-15, each run also keeps every container's full log and samples `pg_stat_activity` for long-running statements.
  - Each run records errors seen on the NetBox side.

### 7.5 netbox-branching scenarios (`bench/branch_scenarios.py`)

Diode writes into a branch (`X-NetBox-Branch`) while the scenario triggers the race:

- **`branch-sync-ingest` (F6):** ingestion into a branch while main receives 2,000 changes and the branch is synced mid-load. Measured: FAILED entities, objects missing from the branch, sync duration.
- **`branch-merge-race` (F2):** ingestion into a branch; `Branch.merge()` (what `MergeBranchJob` runs) starts while batches are in flight. Measured: objects in the branch schema that are not in main after `MERGED`.
- **`branch-double-merge` (F1):** two merges of the same branch released together (an `update` and a `create` variant). Measured: number of merges applied, `MERGED` events, final status.
- **`branch-sync-watermark` (F3):** a main transaction saves a change, a sync runs, then the transaction commits; a second sync follows. Measured: whether the late change is in the branch.

### 7.6 netbox-branching test suite

The plugin's own suite runs inside the official `netboxcommunity/netbox` image (`bench/tools/nbb-test.sh`), the way its CI does. `testing/configuration.py` is used, with Postgres and Redis in containers, and the test database is kept between runs (`--keepdb`).

---

## 8. Benchmark results

All numbers are means ± standard deviation over n runs of the same configuration. Each run's JSON is in `bench/results/`, and the console of each battery is in `bench/evidence/task-outputs/` (index: [`bench/evidence/README.md`](../bench/evidence/README.md)).

### 8.1 Diode: upstream against fork (`std`, NetBox 4.7.0)

Upstream with the default configuration: n = 6, in two batches of 3. Everything else: n = 3. Fork = M-1 + M-2 (`14ba79f`).

| Configuration | Metric | Upstream | Fork | Difference |
|---|---|---|---|---|
| Default (concurrency 1) | create (ent/s) | 8.66 ± 1.89 | **11.34 ± 0.20** | +31% |
| | identical re-ingest (ent/s) | 48.0 ± 8.8 | **50.5 ± 0.4** | +5% |
| | update (ent/s) | 9.80 ± 1.74 | **10.52 ± 0.05** | +7% |
| | apply timeouts per run | ~44 | 0 | |
| | IP addresses in NetBox (expected 800) | 1276 / 962 / 934 / 974 / 950 / 962 | 800 in every run | |
| | mean NetBox CPU | 101–138% | 74% | −35% |
| Concurrency 4 | create (ent/s) | 15.98 ± 0.29 ¹ | **20.08 ± 0.17** | +26% ¹ |
| | identical re-ingest (ent/s) | 16.4 ± 0.2 ¹ | **84.7 ± 0.0** | +415% |
| | apply timeouts per run | ~5,500 | 0 | |
| | entities recorded FAILED | 100% | 0% | |
| | IP addresses in NetBox | 1518–1608 | 800 | |
| | interfaces with a lost update | 136–189 | 0 | |
| | mean NetBox CPU | 400% (saturated) | 236% | −41% |

¹ Upstream at concurrency 4 "completes" entities as FAILED; the rate is not a rate of correct applies.

**Variance.** Upstream's create phase took between 182 s and 369 s across identical runs; the fork's between 157 s and 162 s. One upstream run ended its create phase with 600 entities FAILED.

**Final state.** All six fork runs (concurrency 1 and 4) end with **byte-identical** NetBox state. Every upstream run differs:
- 150–174 duplicate IP addresses per run at concurrency 1 (the 3 runs with a full dump);
- 718–808 duplicate IP addresses plus 136–189 lost interface updates at concurrency 4.

With the duplicates removed and the lost updates accounted for, upstream's content equals the fork's.

### 8.2 Scale (`large`, fork only)

17,000 entities per phase. Concurrency 4: 2 runs. Concurrency 1: 1 run.

| Phase | Concurrency 4 | Concurrency 1 | Gain |
|---|---|---|---|
| create | 18.1 ent/s (~15.6 min) | 9.4 ent/s (~30 min) | 1.9× |
| identical re-ingest | 73.7 ± 3.2 ent/s | 43.2 ent/s | 1.7× |
| update | 15.4 ± 0.4 ent/s | 5.7 ent/s | 2.7× |

- **Errors:** 0 apply timeouts, 0 deadlock retries in the plugin, 0 reconciler errors.
- **Final state:** identical in all 3 runs, at concurrency 1 and 4.
- **Resources:** NetBox at ~253% CPU and ≤ 1.5 GB RAM; the reconciler at 20 MB.
- **Scaling:** throughput drops only from 20.1 (`std`) to 18.1 ent/s (`large`).
- **Not investigated:** at concurrency 1 the update rate drops from 10.5 to 5.7 ent/s.

**Upstream was not run at this scale:** it does not converge at `std` already.

### 8.3 P-10: granian backpressure

Configuration: `std`, concurrency 8, auto-apply batch 25, `GRANIAN_BACKPRESSURE=1` (`bench/compose.backpressure.yaml`), ×3 each side.

| Diode | apply timeouts per run | FAILED per run |
|---|---|---|
| `2919153` (requeue on 503, without `Connection: close`) | 208–338 | 133–309 |
| `117b107` (with `Connection: close`) | **0** | **0** |

### 8.4 Where the time goes (profile)

py-spy on NetBox's 4 granian workers during a create load: 90 s at 100 Hz, 7,440 samples inside the plugin's request.

| Phase | Share of request time |
|---|---|
| Apply: NetBox/DRF serializers (`is_valid`, `create`) | 56% |
| Plan: `generate_changeset` (transformer 15%, matcher 10%) | 27% |
| Change-log buffer (snapshots) | 14% |
| Waiting on Postgres (`Connection.wait`) | ~9% |

- At concurrency 1 **only one of the four workers works**. The bottleneck is Python CPU in one worker, not the database, which is why concurrency 4 nearly doubles the fork's throughput.
- The reconciler and the ingester (Go) average under 1% CPU: they are not bottlenecks.
- **The plugin's code path was not optimised in this work.** The fork's throughput gain comes from not doing duplicate work (M-1) and from being able to use NetBox's workers safely. The profile above is the map for the next step.

### 8.5 netbox-branching overhead (F7, measured, not changed)

The router calls `supports_branching()` on every ORM query, before checking whether a branch is active.
- **Cost:** 2.38 µs per call, or 0.08 µs with a per-model cache (30×); `db_for_read`/`db_for_write` take 3.0–3.2 µs with no active branch.
- **Share of the request:** 1.36% of py-spy samples inside Diode's request (1.04% in `supports_branching`).
- **End-to-end A/B** with and without netbox-branching installed: 17.8 ± 1.9 vs 19.0 ± 1.4 ent/s, which is within that run's noise (~10%).

Conclusion: a fast path would save about 1% of request time. That is real but small, so it was **not** implemented. Evidence: `bench/evidence/f7-20261115-1038/`.

### 8.6 netbox-branching race scenarios

| Finding | Scenario | Before (code) | After (code) |
|---|---|---|---|
| F6 | `branch-sync-ingest` ×5 | 190–265 FAILED of 540; 21–29 devices missing (`20261115-1010`) | **0 FAILED, 0 missing, 0 timeouts** (`20261115-162825-*`) |
| F2 | `branch-merge-race` ×5 | 45–112 objects lost per merge (`20261115-193520-*`) | **0 lost** (`20261115-204522-*`, `bd9e7be`) |
| F1 | `branch-double-merge` ×8 | update ×5: merged twice; create ×3: final READY with data already in main (`20261115-213541-*`, `-215426-*`) | **one merge applies, the other aborts in ≤ 11 ms; final MERGED, 1 MERGED event** (`20261116-042348-*`, `-044607-*`, `ee957fe`) |
| F3 | `branch-sync-watermark` ×5 | late change missing 5/5 (`20261116-0549`) | **synced 5/5** (`20261116-075335-*`, `69bdb57`) |

**The F6 fix needed four iterations, all kept in the evidence.**
1. 503 + requeue: 0 "not ready", but 100 FAILED per run from 120 s timeouts (the dirty keep-alive connection).
2. Plus body drain and `Connection: close`: 3/5 clean, and 2/5 with "deleted in main" (the non-atomic plugin apply).
3. Plus an atomic apply: a regression, 500s from the create-or-find fallback.
4. Plus a savepoint on the branch connection: 5/5 clean.

---

## 9. Validation

### 9.1 Unit and integration tests

| Repository | Suite | Result |
|---|---|---|
| diode-server (`develop` and `perf/bench-vs-upstream`) | `go test ./...` | 22 packages OK; `go vet` and `gofmt` clean |
| diode-netbox-plugin (`develop`) | full suite, NetBox v4.7 test environment | **1018 tests OK** (`bench/evidence/logs/20261115-plugin-suite-v47-branch-savepoint.log`) |
| netbox-branching (`main`, before the upstream merge) | full suite, NetBox 4.7.5 | **620 tests OK** (2 expected failures already marked upstream) (`bench/evidence/logs/20261116-nbb-test-full-1737-api-409.log`) |
| netbox-branching (`main` merged with upstream `bf2e88d`) | full suite, NetBox 4.7.5 | 632 tests; the only 9 failures also fail on unmodified upstream `main` (§9.4) |

**Notes on the netbox-branching suite:**
- Each change was compared with the suite on unmodified `main`; no change introduced a failure.
- On NetBox 4.7.0 the suite has the 33 core-caused failures of §6.8, also on unmodified `main`.
- Running only `test_api`, `test_stuck_branches` and `test_status_transitions` together gives 7 "relation does not exist" errors. They are **identical on unmodified `main`**, so this is a test-isolation issue of that subset (`…-main-ordem-api.log`); in the full suite those tests pass.

**Tests that fail without the fix** (run with the fix temporarily removed, logs kept):
- F4 and F15 (`test_sync_replay`);
- the monotonic watermark;
- F3 (`test_sync_watermark`, versus the old `last_sync = sync start` behaviour).

The netbox-branching concurrency tests use a second real database connection, not mocks, to play the other process.

### 9.2 Benchmark validation of each fix

Each fix was run in the same scenario before and after. "Before" runs use the code with every *previous* fix applied, so each comparison isolates one change (§8.6). Some "before" results got worse as other fixes landed: F1 worsened once F2's lock serialised the two merges. This is recorded in the specs rather than hidden.

### 9.3 Final regression battery

Run 2026-11-16 11:11–12:32 over the latest code of the time:
- diode `1abd1a8`;
- plugin `938afa0`;
- netbox-branching `00f6744` (`bench/tools/regression.sh`; output in `bench/evidence/logs/20261116-1111-regression-battery.log`).

| Step | Runs | Result |
|---|---|---|
| `std` c=4, NetBox 4.7.0, without netbox-branching | 3 | 0 FAILED, 0 timeouts, 18–22 ent/s |
| stress `std` c=8, batch 25 | 5 | 0 FAILED, 0 timeouts, 24–26 ent/s |
| `branch-sync-ingest` | 3 | 0 missing, sync 41–44 s |
| `branch-merge-race` | 3 | 0 lost, MERGED |
| `branch-double-merge` (update ×2, create ×1) | 3 | exactly one merge, final MERGED |
| `branch-sync-watermark` | 2 | late change synced |

After the last netbox-branching changes (§6.5–6.6), the sync and merge scenarios were run again on `f82d2b5` (`bench/evidence/logs/20261116-1545-sync-reval-f82d2b5.log`):
- `branch-sync-watermark` ×3: synced;
- `branch-sync-ingest` ×2: 0 missing;
- `branch-merge-race` ×1: 0 lost.

The first watermark attempt aborted before running: the NetBox seed had to be rebuilt for the new code, and the first boot exceeded the 900 s health-check start period. It passed on re-run.

### 9.4 Fork merged with current upstream

On 2026-11-16 the fork's netbox-branching `main` was merged with upstream `main` (`bf2e88d`: #737, "skip tables missing from main when provisioning", and #724, tests). The merge had no conflicts.

**Full suite on the merged code** (NetBox 4.7.5): 632 tests, 6 failures and 3 errors (`bench/evidence/logs/20261116-nbb-test-full-main-com-upstream.log`).

All 9 are tests from upstream's #724 ("test merging and reverting side-effect updates") and `test_revert_cable_create`. The same 9 tests also fail on **unmodified upstream `main` (`bf2e88d`)** in the same image (`…-upstream-bf2e88d-9-testes.log`). Upstream's CI now runs against NetBox `main` only ("features gated on unreleased NetBox"), and these tests depend on behaviour NetBox 4.7.5 does not have yet. No other test failed.

In the full run, two of them ended as ERROR in the fork where upstream has FAIL: the iterative merge applied an interface UPDATE before the CREATE of the wireless link (or MAC address) it points to. Re-running the three tests twice on each code base gives **identical results** (`…-fork-vs-upstream-3-testes.log`). The difference was the order of change-log entries with the **same `time`**, which `order_by('time')` leaves to PostgreSQL.

This is an upstream observation, not something the fork changes: ordering the merge by `('time', 'pk')` would make it deterministic. It is listed in §11.

---

## 10. Upstream compatibility

### Diode

- **New environment variable:** `NETBOX_DIODE_PLUGIN_APPLY_TIMEOUT_SECONDS` (default 120). Read timeouts and retry behaviour are unchanged.
- **Behaviour change:**
  - An apply that times out is no longer resent; it is recorded `FAILED`, and NetBox finishes it.
  - 503/429/409-non-retryable answers now requeue the batch instead of failing it.
  - With an older netbox-branching that answers 400 for an unavailable branch, behaviour is the same as upstream: the batch fails. The requeue only applies when NetBox says the refusal is temporary.
- **Default change (develop only):** `AUTO_APPLY_PROCESSOR_CONCURRENCY` 1 → 4; the Helm chart is unchanged.
- **New metrics** (§4.9). No database schema change in Diode.

### diode-netbox-plugin

- No API or model changes.
- `active_branch_atomic()` does nothing when no branch is active or netbox-branching is not installed.

### netbox-branching

- **Migration `0010_syncedchange`** (new field `Branch.sync_watermark`, new model `SyncedChange`). Upstream's latest migration is `0009`. If upstream adds its own `0010` before this is reviewed, the migration must be renumbered or rebased.
- **New settings** with safe defaults, validated in `ready()`: `unavailable_retry_after` (5), `write_drain_timeout` (300), `sync_watermark_margin` (5).
- **HTTP contract changes:**
  - **Requests naming an unavailable branch:** 503 + `Retry-After` for a transitional status, and 409 for a terminal status (was 400 for both); a JSON body with `detail` and `retryable`.
  - **Branch actions:** 409 (was 400) when the status does not allow the action.

  Clients that treat any 4xx as failure keep working, but clients that test for exactly 400 will see different codes.
- **`min_version` raised from 4.7.0 to 4.7.4.**
- **PostgreSQL requirements:**
  - Advisory locks are used (namespace `0x4E425752`, "NBWR"); they need no configuration.
  - The F3 watermark reads `pg_stat_activity.xact_start` of other sessions. That works when NetBox uses one database user, the normal setup. With different users the role needs `pg_read_all_stats`; without it, the behaviour falls back to "sync start − margin".
- **Behaviour changes users may notice:**
  - A merge or sync waits up to `write_drain_timeout` for in-flight write requests.
  - A merge fails, without effect, if the branch changed during it.
  - `archive()` refuses a merged branch with post-merge changes.
  - Archive of a branch with a running job is refused.
- **Upstream sync:** merged with upstream `main` at `bf2e88d` on 2026-11-16, without conflicts (§9.4).

### Supported versions tested

- NetBox 4.7.0 for the Diode comparison and the plugin suite.
- NetBox 4.7.4 and 4.7.5 for netbox-branching (full suite, both green).
- The branch benchmark scenarios on 4.7.5.

---

## 11. Limitations and open items

### Benchmark limitations

- One machine, one dataset shape (devices / interfaces / IP addresses), and small samples (n = 3–6).
- Some A/B tests had no resolution because the run-to-run noise (7–10%) was larger than the effect:
  - the M-6 throughput A/B (24.2 ± 2.3 vs 22.6 ± 1.8 ent/s; per-pair difference −1.7 ± 3.8);
  - the F7 end-to-end A/B.

  The conclusions do not rely on them.
- Authentication (Hydra/nginx) is stubbed on both sides.
- The upstream baseline was not run at the `large` scale, nor on NetBox 4.7.5.

### Known limitations of the fixes

- **F2 lock:** only HTTP write requests take it. Writes from scripts or jobs using `activate_branch()` are caught by the merge check (§6.5) and rolled back, not waited for.
- **F3:** a change saved in a transaction open for more than 1 hour and committed after a sync is still missed (the look-back cap).
- **`DROP SCHEMA` guard:** a non-HTTP write whose transaction started before `merged_time` and committed after the merge check has a `time` earlier than the merge and is not detected. That window is the milliseconds between the check and the commit.
- **F15:** losing the default commit still loses `ChangeDiff` updates for that sync (stale conflict display until the object changes again).
- **The Diode apply timeout (120 s)** turns a legitimately longer apply into `FAILED` instead of resending it; it is configurable.

### Diagnostic findings not addressed

All are documented in `specs/DIAGNOSTICO-2026-11-14.md` with code references.

| ID | Finding | Notes |
|---|---|---|
| F5 | revert does not check conflicts or dependencies; undoing a CREATE can cascade-delete objects created in main later | high; needs design discussion |
| F7 | `supports_branching()` on every query | measured ~1% of request time (§8.5) |
| F8 | an extra `ChangeDiff` SELECT on every main UPDATE/DELETE | |
| F9 | branch looked up twice per request | |
| F10 | merge/revert load the whole change log into memory, with N+1 queries | |
| F11 | no authorisation to activate a branch; `owner` not enforced | security; needs design discussion |
| F12 | a table created by `migrate` in a branch gets its own sequence and may collide with main's IDs | plausible, not reproduced |
| F13 | one persistent connection per branch per worker | operational |
| — | merge/sync order the change log by `time` only; entries saved in the same instant (e.g. an object and the side-effect UPDATE pointing to it) are applied in arbitrary order (§9.4) | observed in upstream's #724 tests; ordering by `('time', 'pk')` is a candidate fix |

### Other open items

- **Diode, not changed:**
  - P-6: a platform shared between manufacturers makes every re-ingest an UPDATE;
  - P-4: the ingester does not verify the JWT signature and relies on nginx;
  - the plugin's apply path was not optimised.
- **diode-netbox-plugin's v4.7 test environment** pins `netboxlabs-netbox-branching==1.2.0` from PyPI (`docker/v4.7.x/requirements-diode-netbox-plugin.txt`). The plugin's 1018-test run used that version, not the fork; the fork's branching code was exercised together with the plugin by the benchmark scenarios. A spec in the plugin fork (SPEC-1747) proposes moving the test environment to 1.2.3.

---

## 12. How to reproduce

From a clone of `aliothbl/diode`, branch `perf/bench-vs-upstream`, with `diode-netbox-plugin` and `netbox-branching` checked out as siblings:

```bash
# Diode upstream vs fork (std scenario, 3 runs each)
bench/run.sh --version baseline  --scenario std --runs 3
bench/run.sh --version candidate --scenario std --runs 3
CONCURRENCY=4 bench/run.sh --version candidate --scenario std --runs 3
bench/.venv/bin/python bench/compare.py --state bench/results/*.json

# netbox-branching race scenarios (NetBox 4.7.5 by default)
bench/run.sh --version candidate --scenario branch-merge-race --runs 5
DOUBLE_MERGE_KIND=update bench/run.sh --version candidate --scenario branch-double-merge --runs 5
CONCURRENCY=4 BATCH_SIZE=25 SYNC_OBJECTS=2000 bench/run.sh --version candidate --scenario branch-sync-ingest --runs 5
bench/run.sh --version candidate --scenario branch-sync-watermark --runs 5

# P-10 A/B under granian backpressure
bench/tools/p10-ab.sh

# full regression battery
bench/tools/regression.sh

# netbox-branching test suite in the NetBox image
NBB_IMAGE=netboxcommunity/netbox:v4.7.5 KEEP_DB=1 bench/tools/nbb-test.sh
```

**Knobs:** `CONCURRENCY`, `BATCH_SIZE`, `RATE_RPS`, `PLUGIN_TIMEOUT`, `NETBOX_WORKERS`, `BENCH_NETBOX_VERSION`. See [`bench/README.md`](../bench/README.md).

**First run:** it builds the baseline binaries from upstream `519ccec` and creates a NetBox seed (about 8–15 min); later runs reuse both.

**Binary hashes** of the baseline and candidate builds used in §8.1 are listed in `specs/active/SPEC-20261114-0807-benchmark-performance-vs-upstream/report.md`, §8.

---

## 13. Questions for the reviewers

1. Do the problems described here match your understanding of how Diode, the plugin and netbox-branching are meant to work, or does any of them come from a configuration that is not supported?
2. **Diode M-1.** Is "no retry of the apply after it may have reached NetBox" acceptable, or would you prefer an idempotency key on `bulk-plan-apply`?
3. **netbox-branching F2.** Is a session-level advisory lock taken by the middleware acceptable, or would you prefer another mechanism, e.g. a per-branch row lock, or refusing writes earlier?
4. **netbox-branching F3.** Is reading `pg_stat_activity` acceptable for the watermark? Is the new `SyncedChange` model the right place for sync deduplication?
5. **HTTP status codes** (503/409 for an unavailable branch; 409 for refused actions): do they fit your API conventions? Should any of them be split out or dropped?
6. **The default concurrency of 4 in Diode**: should defaults change upstream, or stay as documentation only?
7. **Is the benchmark methodology sound enough** to support the conclusions, and what would you want measured differently?
8. **Splitting into PRs:** one per finding (e.g. Diode M-1, M-2, …; netbox-branching F6, F2, F1, F3, …), or another split you would prefer?
