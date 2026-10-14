# Contract: pushing marks to the website

**Owner:** competition platform (this repository).
**Audience:** observability (OBS) and the AI track crew (AIC).

Architecture §14.1: *"obs → web, VLAN 109. Pushes live events per app and
published scores to POST /api/feed over HTTPS."* This is that endpoint.

## What goes where

There are two pipelines and only one of them is this.

| | Goes to | Carries |
|---|---|---|
| Vector → AI API | `POST /api/v1/ingest` on the obs VM | raw normalised telemetry |
| obs → **website** | **`POST /api/feed`** (here) | **marks**, already judged |

§8.2 is explicit that the website never reads raw telemetry, and it does not
want to. What it wants is the number you have already decided: a mark, the
criterion it is against, and the event ids behind it as evidence.

## Authentication

```
X-Feed-Token: <DJANGO_FEED_TOKEN>
```

Unset on the website, the endpoint answers **404** — a deployment that forgot
the variable exposes nothing. A wrong token gets **403**. Comparison is
constant-time. POST only.

## The request

```json
{
  "competition": 6,
  "scores": [
    {
      "cell": "enta-app01",
      "stage": "main",
      "criterion": "availability",
      "marks": 11,
      "note": "99.4% availability across day 1",
      "day": 1,
      "key": "uptime:day1"
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `competition` | no | Defaults to the open one. |
| `cell` / `enterprise` | one of these | `"enta-app01"` works on its own; or `cell: "APP01"` with `enterprise: "a"`. |
| `team` | or this | The platform's own identifier, e.g. `JS26-B-014`. |
| `stage` | no | `main` (default), `documentation` or `entry`. |
| `criterion` | yes in practice | The key from the marking scheme — see below. |
| `marks` | **yes** | The mark, not a percentage. Capped at the criterion's share. |
| `note` | no | **Shown to the team.** Write it for them. |
| `day` | no | 1, 2 or 3. |
| `key` | strongly advised | Makes the push idempotent. |

### `key` is the one that matters

An uptime figure is republished every few minutes. **With a `key`, the same
mark is replaced; without one, a second row is added and the team is scored
twice.** Use something stable and specific: `uptime:day1`, `precision:day2`.

## Criterion keys

Only these are meaningful; anything else is stored but will not line up with
what the team is shown. Marks are capped at the figure given.

**Application Blue** (`main`, 40): `availability` 12 · `detected` 12 ·
`response` 10 · `unfound` 6

**AI Blue** (`main`, 40): `precision` 12 · `recall` 10 · `correlation` 8 ·
`decisions` 6 · `safety` 4

**AI Red** (`main`, 40): `missed` 16 · `false` 16 · `breadth` 8

**Application Red** is **not** yours. Those marks are computed by the platform
from findings every time a judge reviews one; a push would be overwritten.

## The response

```json
{"accepted": 2, "rejected": [{"index": 1, "error": "no team matched", "cell": "entb-app09"}]}
```

`200` when everything landed, `202` when some rows did not. **A row that
matches no team is rejected and named, never silently dropped** — a mark that
lands nowhere is worse than one that fails loudly, because nobody goes looking
for it. Read the `rejected` array.

## Team identity

The one thing likely to go wrong. You key on cell codes (`enta-app01`) and team
codes (`AI-01`); the platform keys on `enterprise` + `cell_id` +
`team_identifier`. The endpoint accepts both spellings and normalises them, but
a cell that has not been assigned on the platform yet will not match. If you
get `no team matched` for a cell you believe exists, the team has not been
placed in it — tell the organisers rather than renaming anything.

## Trying it

```bash
curl -X POST https://jengasec.com/api/feed/ \
  -H "X-Feed-Token: $DJANGO_FEED_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"scores":[{"cell":"enta-app01","criterion":"availability","marks":11,"key":"probe"}]}'
```

Then open `/scoring/live/` as staff: the mark appears immediately. As a
participant it appears five minutes later, which is deliberate.
