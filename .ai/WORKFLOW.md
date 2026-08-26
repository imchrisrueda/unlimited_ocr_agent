# Deterministic workflow

## Project flow

```text
OBJECTIVE -> DISCOVERY -> DEFINITION -> [ARCHITECTURE/SCIENTIFIC DESIGN]
          -> PLAN -> TASK -> EXECUTION -> VALIDATION -> REVIEW -> ACCEPTED
```

Project phases are `uninitialized`, `discovery`, `defined`, `planned`, `executing`, `validating`, `reviewing`, `accepted`, `paused` and `archived`.

Use `projectctl project transition --to PHASE` to change project phase. The normal path is:

```text
discovery -> defined -> planned -> executing -> validating -> reviewing -> accepted -> archived
```

Active phases may move to `paused` or `archived`. Resuming from `paused` requires an explicit destination. `archived` is terminal.

For SOFTWARE and SCIENTIFIC projects, phase transitions enforce progressive documentation gates:

- `defined`: project brief completed.
- `planned`: project plan completed.
- `executing`: architecture and risks completed.
- `accepted`: at least one TASK exists and every TASK is `ACCEPTED`.

Strict validation also reports a project phase that is behind its most advanced TASK. Minimum alignment is:

```text
PLANNED/READY -> planned
EXECUTING -> executing
TESTING -> validating
REVIEW/CHANGES_REQUIRED/HUMAN_REVIEW_REQUIRED -> reviewing
```

Accepted TASKs may coexist with new work in earlier active phases. When every TASK is accepted, the project must be at least `reviewing` and can then transition to `accepted`.

## TASK states

Only these states are valid:

```text
BACKLOG PLANNED BLOCKED READY EXECUTING TESTING REVIEW
CHANGES_REQUIRED ACCEPTED HUMAN_REVIEW_REQUIRED
```

Allowed transitions:

```text
BACKLOG          -> PLANNED | BLOCKED
PLANNED          -> READY | BLOCKED
READY            -> EXECUTING | BLOCKED
EXECUTING        -> TESTING | BLOCKED
TESTING          -> REVIEW | CHANGES_REQUIRED | BLOCKED
REVIEW           -> ACCEPTED | CHANGES_REQUIRED | HUMAN_REVIEW_REQUIRED
CHANGES_REQUIRED -> READY | HUMAN_REVIEW_REQUIRED
BLOCKED          -> PLANNED | READY | HUMAN_REVIEW_REQUIRED
```

Use `projectctl task transition` for ordinary transitions. `ACCEPTED` can only be produced by `projectctl review record --result pass` with a valid review.

## Review cycles

A correction cycle is a `CHANGES_REQUIRED` review followed by a new delivery. Three correction cycles are allowed. If another review requests changes, the TASK becomes `HUMAN_REVIEW_REQUIRED`.

Substantial work sets `independent_review_required: true`. If executor and reviewer are equal, a PASS request becomes `HUMAN_REVIEW_REQUIRED`.

Reviews are immutable records. STATUS and BACKLOG are generated views and must not be hand-edited.
