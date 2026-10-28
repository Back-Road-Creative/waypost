# Titles for a verified single region

A retained drive can cross several towns while staying inside one verified
county or state. In that case, the video pipeline may supply a long-form
`TitleFacts` record with a single `region` and no invented origin or
destination. The region must carry the evidence ID of the route check; Waypost
does not determine the video's geographic extent itself.

This shape has its own `region_only_lf` baseline and `region_drive_lf`
candidate. They use long-form search traffic and thumbnail-impression click
rate evidence. As with route titles, a candidate replaces the caller's factual
baseline only when both have enough recent observations and the candidate
beats the baseline by the policy margin. With no measured comparison, Waypost
keeps the baseline and records `insufficient_evidence` with the available
candidates. Region-only measurements are not pooled with route or Short
measurements. The title policy is version 2 for this new candidate set.

The GMS pre-encode gate checks the region against the retained route before
passing facts here, then checks Waypost's chosen title again before rendering.
Waypost's final-text review binds the chosen title and visible text to the
same route. A region claim with gaps needs the separate bounded-boundary proof
contract; the title selector never treats a geographic label as proof.

When no town is held long enough to name, the route itself is empty and the
region claims carry the whole drive. The final-text review then requires every
whole-video surface and the title to name a region that holds the drive; see
[final-text-review.md](final-text-review.md).
