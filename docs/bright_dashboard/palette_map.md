# Palette map (bright operator dashboard, 2026-10-04)

Every distinct colour in `frontend/app|components|lib|hooks` on main (`95fa356`), its role, and its new value.
`frontend/scripts/verify_palette.mjs` reads this file. It fails on any colour in those folders that is neither in a
`new` column below nor in the keep list. Case and whitespace do not matter. `rgb()` and `rgba()` are covered.

Replaces the 2026-09-24 "light muted palette" decision (flagged in MEMORY.md).

## A. By value (old to new)

| old | uses on main | role | new |
|---|---|---|---|
| #8F4424 | 23 | loss text, alarm red, ORB alert border | #C2300F |
| #2F6B4C | 13 | gain text | #0A7D53 |
| #5D5A73 | 11 | muted text, neutral ink | #5B6283 |
| #3E3A57 | 8 | soft body text, secondary button text | #2A3150 |
| #5E9A7A | 7 | sage, running dot | #0A7D53 |
| #FAF0E6 | 7 | warning banner background (theme tint is separate) | #FFF4DB |
| #7A3E1D | 7 | warning banner text | #8A4B00 |
| #F6E3DA | 7 | loss background | #FFEFEA |
| #3E4478 | 7 | lavender ink | #4A2AB5 |
| #E4EFE7 | 7 | gain background | #E9F8F0 |
| #FFFFFF | 7 | white | keep |
| #F3F1EA | 7 | tile / flat chip background | #F1F3FB |
| #8189C4 | 7 | lavender | #6D3BFF |
| #C9CCD9 | 7 | label text on the dark card | #FFFFFF |
| #4A5190 | 6 | link, focus outline, selected button border | #2B4BFF |
| #A9553A | 6 | terracotta, alarm button and icon | #C2300F |
| #EFD8C5 | 6 | warning banner border | #F0D79A |
| #A7A2B8 | 6 | neutral bar, grey dot | #8A91B0 |
| #ECE9DE | 6 | neutral band, grey fear level | #E6EAF5 |
| #EEEFF7 | 5 | info banner and lavender tint | #F0EBFF |
| #EFE4D2 | 5 | line, divider | #DDE2F2 |
| #FAF8F2 | 5 | hover and table header background | #F6F8FE |
| #C47A88 | 4 | chart gradient end, Big News bar | #D61F7A |
| #2F5A45 | 4 | sage text on tint (theme ink is separate) | #0B5A3C |
| #7E9CC8 | 4 | swing blob and bars | #2B4BFF |
| #E7E3D6 | 4 | neutral track | #E3E7F3 |
| #1D1A33 | 3 | ink, chart label text | #0E1330 |
| #E8B09E | 3 | unsold banner border, strip loss colour | #F5B7A8 |
| #D9DCEB | 3 | info banner border | #DDD2FF |
| #3F7D5C | 3 | safety shield tile, Running label | #0A7D53 |
| #D5E2D6 | 3 | sage border | #BFE6D3 |
| #EDF3EE | 3 | sage button background | #E9F8F0 |
| #EDF4F7 | 3 | Tesla tile background | #EEF1FF |
| #2F5368 | 3 | Tesla tranche tile text | #1E36B8 |
| #4A4760 | 3 | grey fear level text | #3F4663 |
| #2F5A4B | 3 | safety card text | #0B5A3C |
| #F3ECDF | 3 | swing bar track | #E3E7F3 |
| #F7F3EC | 2 | ground | #EEF1FA |
| #7A3343 | 2 | chart Now label; Big News ink | #2B4BFF |
| #A9D3BC | 2 | wins number on dark card | #C6F432 |
| #BCCBEA | 2 | countdown number on dark card | #FFFFFF |
| #FBF8F2 | 2 | hover and table header background | #F6F8FE |
| #2E3244 | 2 | dark card, swing card text | #0E1330 |
| #000000 | 1 | error page background (out of scope) | keep |
| #353B70 | 1 | link hover | #1F3AD6 |
| #CFC6B3 | 1 | chart zero line | #C4CBE3 |
| #E2D6C2 | 1 | Show pro words border | #C9D0E8 |
| #F2C9A0 | 1 | trades-today number on dark card | #FFFFFF |
| #E9EFE8 | 1 | safety card background | #E9F8F0 |
| #8A5A12 | 1 | amber chip text | #8A4B00 |
| #E7E8F2 | 1 | swing card background | #F0EBFF |

Values that only existed inside a theme, or in something that was removed, are in the next tables.

## B. Old values that are gone (theme entries and removed decoration)

| old | uses on main | where it was |
|---|---|---|
| #6E9C82 | 2 | Ride bar |
| #D98B5F | 2 | ORB bar |
| #A884B5 | 2 | swing bar gradient (now solid) |
| #DCE9EF | 2 | Tesla band |
| #7299AF | 2 | Tesla bar |
| #DFEAF0 | 2 | Tesla track |
| #F3E1CF | 1 | drift blob (removed) |
| #E2E1F1 | 1 | drift blob (removed) |
| #F5EEE2 | 1 | recent trades row border (component deleted) |
| #E3B77F | 1 | safety meter gradient end (now solid) |
| #8FB8A0 | 1 | swing bar gradient (now solid) |
| #D8A0A8 | 1 | swing bar gradient (now solid) |
| #EAE3CF | 1 | Coeur band |
| #65542E | 1 | Coeur ink |
| #F5F1E5 | 1 | Coeur tint |
| #AD985E | 1 | Coeur bar |
| #EBE4D2 | 1 | Coeur track |
| #F4E0CF | 1 | ORB band |
| #F2E3D5 | 1 | ORB track |
| #DCE8DE | 1 | Ride band |
| #DDE9E0 | 1 | Ride track |
| #F1DDDF | 1 | Big News band |
| #F7EBED | 1 | Big News tint |
| #EFDFE2 | 1 | Big News track |
| #E0E1F0 | 1 | Snap Back band |
| #E2E3F1 | 1 | Snap Back track |
| #E9E5D6 | 1 | fear NORMAL bg |
| #F2E2BC | 1 | fear ELEVATED bg |
| #5C4310 | 1 | fear ELEVATED ink |
| #E6C57E | 1 | fear CRISIS bg |
| #4A3208 | 1 | fear CRISIS ink |

## C. Strategy themes (all five values per theme, new)

`ink` reaches 4.5:1 on white, on `tint` and on `band` (checked by the contrast script). `bar` stays bright; it is a
graphic (icon tile, hours bar), which needs 3:1. ORB's ink is #8F3600, not the plan's example #B23E0B: that value reads
as loss red to the existing "no red in the holding banner" colour check (R above 150 with G and B below 90), and ORB
must never look like a loss.

| theme | new band | new ink | new tint | new bar | new track |
|---|---|---|---|---|---|
| Tesla (both plans) | #DDE4FF | #1E36B8 | #EEF1FF | #2B4BFF | #E1E7FF |
| Coeur | #FBEBB8 | #7A5900 | #FFF6DD | #B88600 | #F7EDC8 |
| Opening Range Breakout | #FFDFCF | #8F3600 | #FFF0E8 | #E85A1B | #FCE5D9 |
| Ride the Trend | #CDEFF1 | #06707A | #E6F7F8 | #0A8F9C | #D5F0F2 |
| Big News | #FFD6EA | #A3135B | #FFEAF4 | #D61F7A | #FBDDEC |
| Snap Back | #E2D8FF | #4A2AB5 | #F0EBFF | #6D3BFF | #E6DEFF |
| Overnight (NVDA, IREN, HUT) | #E3E6F1 | #0E1330 | #F1F3FA | #0E1330 | #DFE3F0 |
| Neutral (unknown id, manual trade) | #E6EAF5 | #4B5273 | #F1F3FB | #8A91B0 | #E3E7F3 |

## D. Fear gauge scale (one neutral scale, then amber; never a strategy colour)

| level | new bg | new ink |
|---|---|---|
| LOW and unknown | #E6EAF5 | #3F4663 |
| NORMAL | #DDE2F2 | #3F4663 |
| ELEVATED | #FFE9A8 | #6A4500 |
| CRISIS | #FFC94D | #4A3000 |

## E. Tokens and values with no single old counterpart (new)

| name | new |
|---|---|
| ground | #EEF1FA |
| ink | #0E1330 |
| muted | #5B6283 |
| line | #DDE2F2 |
| accent (darkcard token) | #2B4BFF |
| gain, sage | #0A7D53 |
| gainbg | #E9F8F0 |
| loss, terracotta | #C2300F |
| lossbg | #FFEFEA |
| lime | #C6F432 |
| warn | #8A4B00 |
| warnbg | #FFF4DB |
| lavender | #6D3BFF |
| soft text | #2A3150 |
| tile | #F1F3FB |
| hover | #F6F8FE |

## F. rgb and rgba

| old | role | new |
|---|---|---|
| rgba(255,255,255,0.06) to 0.8 (several) | glass utilities in globals.css, translucent whites | keep |
| rgba(15,15,20,0.72), rgba(22,22,30,0.6) | legacy dark glass for `app/error.tsx` (out of scope) | keep |
| rgba(58,42,158,0.45) | card hover shadow | rgba(43,75,255,0.35) |
| rgba(14,138,98,0.2) | safety card divider | #BFE6D3 |
| rgba(212,204,255,0.6) | swing empty slot border | rgba(255,255,255,0.6) |

## Keep

Pure white and pure black, and white alphas, are kept. Nothing else.

```
#FFFFFF
#000000
rgba(255,255,255,0.06)
rgba(255,255,255,0.08)
rgba(255,255,255,0.1)
rgba(255,255,255,0.12)
rgba(255,255,255,0.14)
rgba(255,255,255,0.18)
rgba(255,255,255,0.2)
rgba(255,255,255,0.3)
rgba(255,255,255,0.6)
rgba(255,255,255,0.8)
rgba(15,15,20,0.72)
rgba(22,22,30,0.6)
```
