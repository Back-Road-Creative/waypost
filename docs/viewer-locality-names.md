# Viewer-facing locality names

`seo_manager.core.locality` decides which place names may reach viewer text (a
description's "Driving through" sentence, the route line). A reverse geocode
puts tract, subdivision and postal-vicinity names in the `hamlet` slot of a
house or road match ("Longwood", "Oceanmarsh Subdivision"); no viewer searches
for them.

`viewer_localities(records)` takes locality records (dicts with `name`,
`match_type`, `place_class`, `addresstype`, `full_address`, and the entry
coordinates as `entry_latitude`/`entry_longitude` or `latitude`/`longitude`;
bare strings are accepted) and returns `ViewerLocalities(names, substitutions)`.

Keep a name when:

- the matched OSM object is itself a city, town, village or hamlet
  (`match_type`), or, with no `match_type`, its `addresstype` is city, town or
  village;
- it is a named landmark: `place_class` historic, leisure, tourism or military,
  or a Fort / Park / Battlefield / Lighthouse / Monument / Preserve / Forest /
  Beach / Pier name;
- it is a named island (ends in "Island");
- the record has no `match_type` and no `addresstype` (an OSM class alone, or nothing, is no evidence to judge by).

Everything else is dropped. A dropped name lying on a named island is replaced
by that island: the island in its `full_address`, else the `NAMED_ISLANDS`
bounding box holding its coordinates (St. Helena Island today; add rows, never
guess). `substitutions` maps each dropped name to its island or `""`.
Names come back deduplicated, first-seen order, case-insensitive;
`dedupe_places(names)` does only that.

Real data check (Beaufort SC, 2026-02-20 reverse-geocode cache): Longwood and
Fort Fremont are both `hamlet` of a `house`/`residential` match, `place_class`
`place`/`highway`, importance about 1e-5 to 0.05, and no record names an island.
Longwood is dropped and replaced by St. Helena Island via the box; Fort Fremont
stays on its name.
