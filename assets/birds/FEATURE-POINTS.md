# PR-ready illustrations — feature points

All 18 species in this folder are PR-ready as of 2026-10-27:
checked-in quantized grayscale PNG (8-bit, values are multiples of
17, matching the existing 3), plus a verified feature point in
`birds_render.py` (`FEATURE_FRACTION`).

## How the points were verified

Each point was measured on the checked-in PR PNG itself, not on a
page paste or a different crop, and each point lands on the single
feature named below. That matters because a page paste crops to the
ink bounding box, which shifts fractions — the three original points
were re-measured on the PR assets on 2026-10-27 and corrected for
that reason (kinglet eye, creeper bill tip).

## Mark labels for future snapshots

A snapshot's `mark_label` for a species should name the single
feature below, so the line's terminus and its label agree. The sample
snapshot still uses a few combined labels (e.g. "black cap, white
cheek", "crest, black necklace"); those are legacy sample text, and
the point in each case is the first-named feature below.

| slug | species | verified feature (mark label) | fraction (x, y) |
|---|---|---|---|
| house-finch | House Finch | streaked breast | (0.39, 0.56) |
| ruby-crowned-kinglet | Ruby-crowned Kinglet | bold white eye ring | (0.22, 0.26) |
| brown-creeper | Brown Creeper | long curved bill | (0.01, 0.26) |
| black-capped-chickadee | Black-capped Chickadee | black cap | (0.67, 0.15) |
| blue-jay | Blue Jay | crest | (0.41, 0.09) |
| northern-flicker | Northern Flicker | black bib | (0.52, 0.38) |
| spotted-towhee | Spotted Towhee | white spots on black wings | (0.60, 0.27) |
| evening-grosbeak | Evening Grosbeak | huge pale bill | (0.81, 0.23) |
| downy-woodpecker | Downy Woodpecker | spotted wings | (0.35, 0.50) |
| chipping-sparrow | Chipping Sparrow | brown cap | (0.71, 0.10) |
| dark-eyed-junco | Dark-eyed Junco | dark head | (0.38, 0.30) |
| yellow-rumped-warbler | Yellow-rumped Warbler | streaked sides | (0.44, 0.58) |
| white-crowned-sparrow | White-crowned Sparrow | bold crown stripes | (0.65, 0.15) |
| orange-crowned-warbler | Orange-crowned Warbler | faint eye ring (eye itself) | (0.33, 0.29) |
| gray-catbird | Gray Catbird | black cap | (0.50, 0.29) |
| townsend-solitaire | Townsend's Solitaire | white eye ring (eye itself) | (0.39, 0.18) |
| lincolns-sparrow | Lincoln's Sparrow | streaked breast | (0.74, 0.43) |
| western-tanager | Western Tanager | black wings | (0.37, 0.44) |

## Provenance

Illustrations are AI-generated black-and-white line illustrations,
converted to quantized grayscale PNGs. They are not eBird / Macaulay
photos. Katherine confirmed 2026-10-27 that AI drawings are okay to
commit as assets to this project — a fair-use personal calendar for
the family — so there is no issue with using them here.
