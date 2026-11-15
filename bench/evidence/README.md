# Evidence

Raw outputs kept for the final technical documentation. Nothing here is
edited by hand; files are copied verbatim from where the work produced them.

- `results/` (one level up, `bench/results/`) — one JSON per benchmark run
  (`run.sh`), plus `*.rows.json.gz` (full NetBox end state). From 2026-11-15
  ~09:10 on, each run also has `*.logs/` with the full container logs.
- `task-outputs/` — console output of the background jobs (benchmark
  batteries, stress loops, test suites), named `<mtime>-<job id>.txt`.
  Before 2026-11-15 ~09:10 the container logs were not kept, so these and the
  JSON summaries are the only record of those runs.
- `logs/` — `run.sh` console logs that lived in `bench/out/` (overwritten per
  run before they were archived here).
- `overnight-plan-snapshot.md` — the autonomous-loop plan and its LOG.

## Index of task outputs

| File | What |
|---|---|
| 20261114-0759-berm2anwi | plugin test suite, NetBox v4.7 (after upstream sync) |
| 20261114-0922-bqismvk3c | first harness boot: build baseline 519ccec, NetBox seed |
| 20261114-1003-brs3evxsu | smoke, baseline (P-1 first seen) |
| 20261114-1045-b1gwga9fx | std ×3, baseline default config (600 FAILED run) |
| 20261114-1113-breci2ind | std ×3, candidate M-1 (14f4147) |
| 20261114-1408-bb4ivro30 | std ×3, candidate M-1+M-2 (14ba79f) |
| 20261114-1437-bkewdjbxi | std ×3, baseline re-run with full state dump |
| 20261114-1813-b7n0xwbyr | large ×2, candidate c=4 |
| 20261114-1836-bkf35pc0h | branch-merge-race, harness bug (nbshell parse) |
| 20261114-1924-b8z634a7j | branch-merge-race ×5 (F2) |
| 20261114-1953-bmgd24ig2 | branch-double-merge ×1 (F1, trial) |
| 20261114-2022-b1ymceo48 | branch-double-merge ×5 (F1) |
| 20261114-2055-bp4q5bkzj | std ×3, router A/B with/without netbox-branching (F7) |
| 20261114-2214-bbhpg9ygc | std ×5, c=4, M-4 (91ecf9d) |
| 20261114-2229-b8yk8wu4s | stress c=8 batch 25 (25 FAILED, P-9) |
| 20261114-2310-bs3juc4fd | stress c=8 batch 25 ×3 (summary) |
| 20261114-2321-bb2hix6eq | stress loop killed for session restart |
| 20261115-0520-b1cfe3fr1 | stress ×8, no reproduction (P-9 step 1a) |
| 20261115-0635-b0ogkuxh9 | stress ×3: P-9 victim found (ltree ValidationError) |
| 20261115-0650-b66qrlq8s | plugin suite v4.7 with M-6: 1015 tests OK (lint step failed: no ruff in image; ruff run on host instead) |
| 20261115-0802-bwh476wl5 | validation M-6: stress ×5 + std c=4 ×3 |
| 20261115-0847-bxytgcj0w | interleaved A/B plugin ed63aff × ae04547, 3 pairs |
| 20261115-0910-bafg4kms4 | branch-sync-ingest trial (batch 100, c=1 default): sync fell inside one in-flight request, F6 window not exercised |

Helper scripts used for the stress and A/B batteries: `bench/tools/`.
