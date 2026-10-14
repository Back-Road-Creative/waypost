# Title selection: who decides

`seo_manager.analytics.title_selection.select_title(facts, evidence_snapshot, policy)`
decides an upload's title and returns a `TitleDecision`, a record that can be
stored with the upload and replayed later. It is a pure function: no clock, no
randomness, no I/O, no LLM. The same inputs, in any observation order, give the
same record (`record_to_dict` gives its JSON form).

## Inputs

- **`TitleFacts`**: the format (`short` or `longform`), the caller's route-only
  title (`baseline_title`), and the places a title may name: `origin`,
  `destination`, `region` and an optional `highlight`. Each `Place` carries the
  id of the evidence that puts the footage there; a place without one is
  refused (`TitleSelectionRefused`), never titled.
- **`EvidenceSnapshot`**: `as_of` plus one `TemplateObservation` per template,
  format, traffic source (`search` or `browse`) and metric source: a click
  rate, its denominator `n` (impressions) and its window. Duplicates, rates
  outside [0, 1] and windows ending after `as_of` are refused.
- **`TitlePolicy`**: the traffic source each format is judged on, the minimum
  `n`, the maximum evidence age, the lift a candidate needs over the baseline
  and the title length cap. A changed number is a new `version`; the defaults
  (`DEFAULT_POLICY`) are proposed values, not measured ones.

## Candidates

Candidate 0 is always the baseline: the caller's route-only title, unchanged.
The others come from the fixed `TEMPLATES` list, in order. A template whose
place is missing is recorded with `missing_fact` and no title; one over the
length cap is recorded with `too_long`. Every candidate lists the evidence ids
of the places it names (`claim_evidence`).

## Choosing

Only observations for the upload's format and the policy's traffic source for
that format count, so Shorts and long-form, search and browse, never borrow
each other's evidence. A candidate (the baseline included) is eligible only
when its observation is a thumbnail-impression click rate
(`metric_source.is_thumbnail_click_through`), ended within the evidence age,
and has at least the minimum `n`. Otherwise its `excluded_reason` says which
check failed: `no_evidence`, `metric_not_title_evidence` (a card click rate or
an unlabelled rate), `stale` or `n_below_min`.

The decision `reason` is one of:

- `insufficient_evidence`: the baseline or every other candidate is
  ineligible. The baseline is kept.
- `baseline_not_beaten`: the best eligible candidate does not beat the
  baseline's rate by the policy's relative lift. The baseline is kept.
- `lift_over_baseline`: the highest-scoring eligible candidate (equal scores
  go to the lower index) is chosen.

The record also carries the policy version, the format, the traffic source,
the snapshot's `as_of`, and each candidate's score, `n`, metric source and
evidence window.
