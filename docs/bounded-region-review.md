# Bounded retained-route region review

Waypost reviews the final title, on-screen text, thumbnail and description
against the retained route supplied by the video pipeline. A county or state
may replace required place names only when the region claim contains all of
them. The usual rule still requires full sampled GPS coverage, no gaps and no
observed samples outside the region.

`BOUNDARY_PROOF_VERSION = "tiger2026-tailte2026-bounded-v2"` adds a narrow alternative for
gaps or lower sampled coverage. The producer must check every retained segment
against the **pinned 2026 Census TIGER/Line county or state polygon** before
rendering. Its `boundary_proof` must retain `verified`, the source archive and
SHA-256, region, Census state FIPS, recording date, maximum speed, GPS error
allowance and longest gap. Waypost accepts the envelope only for the pinned
archives, a matching 2026 recording date and region, at least 70 m/s speed and
100 m GPS error allowances, and a longest gap no shorter than the recorded
gaps, both compared at millisecond precision (a gap end rebuilt as start plus
length carries float noise). A state abbreviation must match the full state and FIPS code. An
observed sample outside the region always refuses, even with a proof.

An Irish county claim uses the same envelope with the **pinned 2026 Tailte
Éireann statutory county map** (`TAILTE2026/COUNTIES/counties-national-statutory-boundaries-ungeneralised-2026.geojson`,
CC BY 4.0, © Tailte Éireann) in place of TIGER. That map can be newer than
the drive, so the rule differs in three ways: the recording may be from 2026
or earlier; the proof carries `country: "IE"` in place of a state FIPS code;
and it must carry `margin_m` of at least 250 m, an inward allowance the
producer added to every boundary distance so a boundary edit between the drive
and the map's date cannot move the route outside. The basis is
`pinned_tailte_polygon_bounded_speed_error_and_margin`. A TIGER proof still
needs a 2026 recording.

Waypost does **not** recompute the geometry from GPX. GMS owns that calculation
and checks the official ZIP hash; Waypost binds the producer's result to the
review and keeps the original `coverage`, `gaps` and `samples_outside` values.
No proof, a malformed proof, a different source or stale review keeps the
existing refusal. The proof is a bounded model under the stated GPS error and
speed assumptions; it cannot guarantee the actual path when those assumptions
are false.

The review version is bumped when this contract narrows. Accepting a new
source, as the Tailte branch did, leaves every stored review valid, so only
`BOUNDARY_PROOF_VERSION` moved. Stored reviews from
an older version fail upload verification and must be rebuilt from their
source and plan. GMS must pin a Waypost release exposing the exact proof
version before it uses the fallback; a pipeline with an older Waypost must
refuse the candidate label before video encoding.
