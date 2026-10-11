# Recommendation auto-applier (`analytics/recommendation_auto_applier.py`)

- **Two gates, both required.** A type applies only when it has a handler in
  `self._handlers` AND its `AutoApplierConfig` flag is on (`_can_auto_apply`).
  A flag alone never green-lights a type — that combination is what made
  `allow_posting_time` a no-op for as long as POSTING_TIME had no handler.
- **Handled today:** TITLE_LENGTH, TAG_RECOMMENDATION, THUMBNAIL_STYLE (on by
  default), DESCRIPTION_TEMPLATE and POSTING_TIME (**off** by default —
  `allow_description_template`, `allow_posting_time`). KEYWORD_PRIORITY has no
  handler and no producer: no category in `_determine_rec_type` maps to it.
- **Explicit fields, never parsed prose.** DESCRIPTION_TEMPLATE reads
  `rec["template"]` (non-empty, ≤5000 chars → `descriptions.default_template`);
  POSTING_TIME reads `rec["posting_time"]` in 24-hour `HH:MM`
  (→ `posting.default_time`). A recommendation missing or malforming its field
  is refused and lands in `rejected` as `apply_failed` — nothing is defaulted.
- **The record cites its evidence, or says it has none.** `AppliedRecommendation`
  carries `evidence: Optional[Dict]` — the data the change was computed from.
  `apply_learned_config` cites the learned-config section it read plus the
  config's `generated_at`, so two changes to the same key a month apart are
  told apart; a handler cites the `issue`/`suggestion` text it parsed
  (`kind: recommendation` — provenance, not a measurement: nothing upstream
  attaches a `content_id`). A call site with nothing real to cite leaves it
  `None`. Do **not** fill it with a plausible-looking blob — `None` means
  nobody recorded what justified the change, and that is the finding.
- **There is no `expected_impact`.** A stated impact nobody measured would be a
  constant on every record, like the `(BOLD, 0.0)` thumbnail default one module
  over. `_load_state` drops unknown keys (logging them) so an audit trail
  written by an older version still loads instead of raising `TypeError` and
  discarding the history.
- **The record carries no `confidence`, and no call site can invent one.**
  Nothing in this service computes a confidence for an applied change, so the
  record has no such field and `_apply_config_change` takes no `confidence=`
  parameter — same reason as for `expected_impact` and
  `SEORecommendation.confidence`: a number shaped like a measurement needs a
  measurement behind it, and a type that cannot hold the value beats a reviewer
  who has to notice it.
- **A per-section confidence would not gate anything — do not add a gate.**
  `_apply_config_change` takes no threshold and `_can_auto_apply` takes only a
  `rec_type`, so **all three sections apply whenever their flag is on**.
  `tests/unit/test_auto_applier_confidence_is_not_invented.py` pins this,
  including that moving `min_confidence_for_auto` to 1.01 changes nothing on
  this path. The per-type `allow_*` flags are the only
  per-section control, and adding a second allowlist beside them would just be
  the flag table twice.
- **The caller-supplied confidence stays.** `process_recommendations` compares
  `rec.get("confidence", 0)` against `min_confidence_for_auto` /
  `min_confidence_for_suggest` and buckets as `low_confidence` below both. That
  number arrives from the caller rather than being typed in here, so it is
  input, not a fabrication — and an absent one is `0`, never defaulted up into
  applying. `_load_state` drops an unknown `confidence` key (logging it),
  so an audit trail written by an older version still loads and still rolls
  back.
- Every handler goes through `_apply_config_change`, which backs up
  `learned_config.json` and records the old value, so
  `rollback_recommendation(rec_id)` restores it for every type alike.
- **One gate, not one per caller.** `apply_learned_config` is the second entry
  point (the nightly `--apply` run) and reaches `_apply_config_change` without
  going through a handler, so it checks `_can_auto_apply` per section itself
  and logs `not applying learned '<section>': <flag> is off` when it refuses.
  The daemon does not filter the sections before handing them over: a filter
  there would protect the nightly run and no other caller. Do **not** add a
  filter upstream — a new caller of `apply_learned_config` is gated by
  construction, and a second copy of the flag table only drifts.
- **Sections it applies:** `title`, `tags`, `thumbnails` — and those are now the
  only three the daemon emits. `_generate_template_config` emits no `posting`
  section (there is no `DaemonState.best_posting_times`, and
  `apply_learned_config` has no branch for one) and no `descriptions` section.
  POSTING_TIME and DESCRIPTION_TEMPLATE reach config
  through `process_recommendations` alone, so `allow_posting_time` is a real
  control over that path and not over the nightly run.
- **`_load_state` tolerates schema drift.** It filters `daemon_state.json` to
  the keys `DaemonState` actually declares and warns naming the ones it drops.
  Passing the whole file to `DaemonState(**data)` would let a single removed or
  added key raise `TypeError` into a bare `except` and return a fresh
  `DaemonState()` — discarding every learned value, with one log line to say so.
