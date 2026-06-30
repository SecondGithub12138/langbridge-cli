# Training: eval + evolver for PM / L4 / L5 / L3

This subsystem distills the "next-door" coder/reviewer self-play worktrial into
this repo. The idea is the same two-loop design:

- **Inner loop** (already in this repo): for one task, L4/L5 implement and L3
  reviews, back and forth, until L3 passes or limits trip.
- **Outer loop** (the evolver): across many tasks, mine signals from traces and
  improve agents — not by editing code, but by editing a shared **policy**
  (guidance bullets + skills) that each role folds into its prompt.

## What `train` optimizes today

**The evolver currently optimizes L4 and L3 only.**

- `train` runs the **L4 ⇄ L3** inner review loop (`loop_fn`, default `layer="l4"`),
  reconstructs traces from the **L34 shared worklog**, grades final diffs with
  hidden tests, and proposes updates to **`l4` and `l3` guidance** (plus skills
  aimed at implementers/reviewers).
- **L5 and PM are not wired into `train` yet.** Eval hooks exist (`eval --role l5`,
  `eval --role pm`), but the evolver does not read L5 Ralph (`l45_share`) or PM
  outer-loop traces (todo_list, routing, BUG_STATUS). Policy slots for `pm` and
  `l5` exist and are injected at runtime, but **trace mining + optimization for
  those roles is still in development.**

Default task source for eval/train: `evals/dataset/sample_validated.jsonl`
(SWE-bench schema, `--source swebench`). Use `--source local` + `LANGBRIDGE_TARGET_REPO`
for a git repo with cached specs under `training/specs/`.

The mapping from next-door to this repo:

| next-door | here |
|-----------|------|
| coder | **L4** (normal task) and **L5** (hard task, divide-and-conquer) |
| reviewer | **L3** (tester) |
| loop | the L4↔L3 and L5↔L3 inner review loops |
| (none) | **PM** — top-level decompose → route L4/L5 → e2e |

## What is built (and tested)

Pure, unit-tested logic (no API/model needed — see `tests/unit/test_training_*.py`):

- `policy.py` (in the package root) — the mutable policy the evolver writes and the
  roles read. Guidance per role (`pm/l4/l5/l3`), skills (reuses the existing
  `read_skill` tool), checkpoints. Injected into prompts via `policy.apply(role, base)`.
- `metrics.py` — `compute_metrics` + `record_result` + leaderboard for the five
  eval types (`l4`, `l5`, `l3`, `pm`, `loop`).
- `signals.py` — responsiveness, alignment (LLM-judged, judge injected),
  calibration, and batch pattern mining.
- `bench.py` — the test-based ground-truth judge (F2P/P2P over hidden tests),
  plus `build_spec` to derive specs from a repo's real fix commits.
- `gate.py` — applies an evolver proposal to the policy (with the **reviewer anchor
  gate**) and the **acceptance gate** (keep a change only if it improves the
  penalty score; reward-hacking is the worst outcome).
- `evals/runner.py` — the five eval runners as pure orchestration over injected
  agent callables.
- `l3_cases.py` — expands each task spec into gold / no-fix reviewer cases with
  test-based `gt_pass` labels for `eval --role l3`.
- `evolver.py` — the outer self-play loop (run batch → grade → mine → propose →
  apply → gate → checkpoint).

Integration wiring (depends on the chosen target repo + model; not unit-tested):

- `evals/agents_adapter.py` + `evals/_run_layer.py` — drive the REAL agents by
  running each layer in a subprocess whose cwd is a fresh git worktree of the
  target repo at the task's base commit, then capturing `git diff`.
- `cli.py` — `specs`, `eval`, and `train` commands.

## Anti-pathology guards (carried over)

- **Reward hacking**: surfaced as a label; the gate penalises "approved but tests
  fail" worst (−3), so a change that games L3 is rejected.
- **Reviewer collapse**: L3 guidance/skills only change when the batch has a
  trustworthy correctness anchor (real tests or a unanimous jury).
- **Prompt bloat / mode collapse**: guidance is deduped and capped per role; the
  evolver can remove/replace bullets, not only add.
- **Oracle leak**: guidance mentioning hidden signals (ground truth, hidden tests,
  the jury, F2P/P2P…) is stripped before it reaches a prompt.
- **Ground-truth anchoring**: correctness is decided by hidden regression tests,
  computed offline and never shown to the agents.

## How to run

Default (validated pytest dataset):

```bash
export GITHUB_TOKEN=...                             # optional; raises API limits for dataset rebuild
export LANGBRIDGE_MODEL=...                         # agent model

# Evaluate one role under the current policy
uv run python -m langbridge_cli.training.cli eval --role l4 --limit 5
uv run python -m langbridge_cli.training.cli eval --role l3 --limit 5   # reviewer: gold + no-fix per task
uv run python -m langbridge_cli.training.cli eval --role loop --limit 5 # same trace shape as train

# Evolver epoch (L4/L3 policy only today)
uv run python -m langbridge_cli.training.cli train --epochs 1 --batch-size 2

# Evaluate a frozen checkpoint instead of the live policy
LANGBRIDGE_POLICY_DIR=training/policy/checkpoints/epoch1 \
  uv run python -m langbridge_cli.training.cli eval --role l3
```

Local git repo + specs cache:

```bash
export LANGBRIDGE_TARGET_REPO=./arrow            # a git repo with bug-fix history
export LANGBRIDGE_SPECS_DIR=training/specs

# Build F2P/P2P specs from real fix commits
uv run python -m langbridge_cli.training.cli specs --issues training/issues.json

uv run python -m langbridge_cli.training.cli eval --role l4 --limit 5 --source local
uv run python -m langbridge_cli.training.cli train --epochs 1 --batch-size 2 --source local
```

`issues.json` is a list of `{task_id, fix_commit, title, body_summary, hard?}`.

## Open decisions / future work

1. **L5 + PM traces for `train`.** Wire `loop_fn(layer="l5")` on hard tasks and
   `pm_fn` for outer-loop traces; mine PM/L5-specific signals from worklogs.
2. **Target repo / task set.** Default is `evals/dataset/`; local arrow-style
   specs remain supported via `--source local`.
3. **Model + cost.** Defaults are expensive for many self-play loops; set
   `LANGBRIDGE_MODEL` / `--model` (and `--evolver-model`) as needed.
4. **Per-round diffs.** The subprocess adapter reconstructs verdicts/comments from
   the shared worklog, but not full per-round diffs, so responsiveness/alignment
   from that path are approximate.
