# Viewer-facing locality names

`seo_manager.core.locality` decides which place names may reach viewer text (a
description's "Driving through" sentence, the route line). A reverse geocode
puts tract, subdivision and postal-vicinity names in the `hamlet` slot of a
house or road match ("Longwood", "Oceanmarsh Subdivision"); no viewer searches
for them.

`viewer_localities(records)` takes locality records (dicts with `name`,
`match_type`, `place_class`, `addresstype`, `full_address`; bare
strings are accepted) and returns `ViewerLocalities(names, substitutions)`.

Keep a name when:

- the matched OSM object is itself a city, town, village or hamlet
  (`match_type`), or, with no `match_type`, its `addresstype` is city, town or
  village;
- it is a named landmark: `place_class` historic, leisure, tourism or military,
  or a Fort / Park / Battlefield / Lighthouse / Monument / Preserve / Forest /
  Beach / Pier name;
- it is a named island (ends in "Island");
- the record has no `match_type` and no `addresstype` (an OSM class alone, or nothing, is no evidence to judge by).

Everything else is dropped. A dropped name whose own `full_address` names an
island is replaced by that island; otherwise it is just dropped. No island is
inferred from coordinates: the Census county subdivision named "St. Helena
Island" (`tl_2026_45_cousub.zip`, sha256
`c9470c02f3a6e81166e73f03cc022d34c4cc6c48188d56b6857f0a8d26482083`) is a county
division that also covers Ladys Island Drive points (32.3937,-80.6727 and
32.4067,-80.6580 in the GMS geocode cache), so a coordinate test would label
Lady's Island as St. Helena Island. `substitutions` maps each dropped name to
its island or `""`.
Names come back deduplicated, first-seen order, case-insensitive;
`dedupe_places(names)` does only that.

Real data check (Beaufort SC, 2026-02-20 reverse-geocode cache): Longwood and
Fort Fremont are both `hamlet` of a `house`/`residential` match, `place_class`
`place`/`highway`, importance about 1e-5 to 0.05, and no record names an island.
Longwood is dropped; Fort Fremont stays on its name.
