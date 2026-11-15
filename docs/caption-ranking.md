# Caption ranking: Meta performance re-orders the opener rotation

`seo_manager.analytics.caption_ranking.rank_captions(platform, rotation, evidence, policy)`
re-ranks the order a caption generator walks its opener templates in, from
Facebook or Instagram evidence, and returns a `CaptionRanking` record that can
be stored with the post and replayed later. It is a pure function: no clock, no
randomness, no I/O, no LLM. The same inputs, in any observation order, give the
same record (`record_to_dict` gives its JSON form).

## Why it re-orders instead of choosing

A Meta caption must open differently from every recent post (the generator's
anti-spam gate refuses a repeated six-word opener), so one winning opener
cannot be used every time. Ranking changes how often each opener comes up
instead: proven openers are picked first, proven losers last.

## Inputs

- **`platform`**: `facebook` or `instagram`.
- **`rotation`**: the generator's opener template ids in the order it would
  use them. Distinct and non-empty, or refused (`CaptionRankingRefused`).
- **`CaptionEvidence`**: `as_of` plus one `CaptionObservation` per platform,
  template and metric source: the median of a per-post metric over `n`
  settled posts, and the window those posts' day-7 measurements fall in.
  Duplicates, negative values and windows ending after `as_of` are refused.
- **`CaptionPolicy`**: the metric each platform is judged on
  (`facebook_reel_reach_day7`, `instagram_reel_reach_day7`), the minimum `n`
  (10 posts), the maximum evidence age (120 days), the margin around the pool
  median (10%) and how many measured openers a ranking needs (2). A changed
  number is a new `version`; the defaults are proposed values, not measured
  ones.

## Ranking

Each rotation entry is checked in this order and the first failure is
recorded as its `excluded_reason`: `no_evidence`,
`metric_not_caption_evidence` (any metric other than the platform's),
`stale`, `n_below_min`. Evidence for a template outside `rotation` is ignored;
ranking never adds an opener.

With at least `min_measured` measured openers, the reference is the median of
their scores. An opener above the reference by more than the margin is `lead`,
one below it by more than the margin is `lag`, and everything else, including
every unmeasured opener, is `middle`. The order is leads, then middles, then
lags, each in rotation order, so it is always a permutation of `rotation`.

`reason` is `insufficient_evidence` (fewer than `min_measured` measured; the
rotation is returned unchanged), `no_spread` (measured, but none outside the
margin; unchanged) or `ranked`.

A median comparison across openers is not a causal experiment: place, season
and posting time differ between posts. The rotation spreads those across
openers, and the margin keeps small differences from moving anything.
