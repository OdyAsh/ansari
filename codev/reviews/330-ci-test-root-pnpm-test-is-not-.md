# Review: issue #330, root `pnpm test` under load (cap vitest workers)

## Pre-registered protocol for the A/B (written before any of these runs)

Declared 2026-10-28, before the runs below, at the architect's direction, so that no load level is chosen after seeing results.

- **Arms.** CAPPED = the root script as shipped (`turbo run test -- --maxWorkers=4`). UNCAPPED = `turbo run test --`, the pre-fix script, run as `pnpm exec turbo run test --`. Both arms use `TURBO_FORCE=true`, so nothing comes from the cache, and both load `apps/api/.env.ci` and `apps/auth/.env.ci`.
- **Order.** The arms alternate (CAPPED, UNCAPPED, CAPPED, …) to cancel machine drift. Each scenario gets at least 5 pairs.
- **Gate.** Each run starts only when no vitest process from any worktree is alive. The sampler records any foreign vitest that appears mid-run (`foreign_vitest` max), and every overlapped run is reported, not dropped.
- **Scenario 1, concurrent vitest (the failure actually observed).** Two root runs start together in this worktree, with no busy loops. In each arm both copies are capped or both are uncapped. A pair-run is red if either copy exits non-zero.
- **Scenario 2, the #301 harness.** One root run with 28 busy-loop processes on 14 cores.
- **Escalation.** 42 busy loops are run only if both scenarios stay green on both arms.
- **Reporting.** Red/green rates are reported for both arms, whatever they show. If UNCAPPED also stays green, the negative test is recorded as not reproduced, and the PR stands on the fork reduction and the idle wall time.
- Wall time and peak fork counts per package are recorded for every run.
