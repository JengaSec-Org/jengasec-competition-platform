# Contract: reproducible competition marks

Owner: competition platform. Producers: the trusted OBS scoring publisher and the existing platform finding workflow.

The platform owns policy, accepted marks, corrections and the leaderboard. OBS supplies measured facts and retained evidence. The ordinary Vector event feed is not a scoring publisher. Application Red marks continue to come from reviewed platform findings.

## Policy and enrollment

The shared, dependency-free `jengasec-scoring-policy` package implements `main-scoring/1.0`. `competitions/grading.py` publishes the criterion labels and caps and checks that they match the package. Entry remains 30, Main 40 and Documentation 30. Authorized bonuses remain outside 100.

Before the measurement window starts, run:

```text
python manage.py scoring_policy preflight --competition ID
python manage.py scoring_policy enroll --competition ID --start UTC_TIMESTAMP --end UTC_TIMESTAMP --archive-budget-bytes BYTES
```

The operator supplies approved private evidence capacity. Preflight reports existing totals before and after criterion caps. If totals change, review that exact report and supply its `--reviewed-reconciliation` value to enrollment. Unknown Main criteria must be resolved first. Enrollment preserves prior mark history, consolidates day-specific Main components, freezes the policy and starts identity history. It cannot invent earlier assignments.

All windows are UTC `[start,end)`. Every automatic score is cumulative from the enrolled start. Days are breakdowns, not extra criterion allowances. Calculate every 60 seconds and wait 120 seconds after the window end for delivery. The final publication covers the full enrolled window. `scoring_policy finalize --competition ID` refuses missing final criteria unless a judge explicitly overrides them. Published results also stop automatic changes. Exact duplicate deliveries remain safe to acknowledge after finalization.

The policy calculator is:

```python
from jengasec_scoring_policy import calculate, canonical_json, digest
result = calculate(side, criterion, facts, quality,
                   policy_version="main-scoring/1.0")
```

`result` has `status`, decimal `marks`, `reasons` and `policy_version`. A pending result has no marks. `digest()` returns SHA-256 over sorted, compact UTF-8 JSON with finite numbers. Only final aggregated marks are rounded, to two decimal places using `ROUND_HALF_UP`.

### Facts and formulas

Counts are nonnegative integers. OBS derives them from authenticated, independently verified receipts. A team cannot label its own attack successful. Required scenario assertions, identifiers, scope and matching rules are frozen before execution. Repeated opportunities do not earn extra credit.

| Side / criterion | Exact facts | Marks |
|---|---|---|
| Application Blue / availability | `expected`, `completed`, `successful` | `12 × successful/completed`; require completed/expected ≥ 0.99 |
| Application Blue / detected | `attacks`, `detected` | `12 × detected/attacks` |
| Application Blue / response | `required`, `passed` | `10 × passed/required` |
| Application Blue / unfound | `eligible`, `protected` | `6 × protected/eligible` |
| AI Blue / precision | `tp`, `fp`, `delivered_attacks` | `12 × tp/(tp+fp)` |
| AI Blue / recall | `detection_seconds`, one number or null per attack | `6 × detections within 60s/attacks + 4 × mean(max(0,1-delay/60))`; misses contribute zero |
| AI Blue / correlation | `pair_tp`, `pair_fp`, `pair_fn`, `hybrid_passed`, `hybrid_required=4` | `4 × pairwise F1 + hybrid_passed` |
| AI Blue / decisions | `required`, `context`, `evidence`, `consistency` | `2 × (context+evidence+consistency)/required` |
| AI Blue / safety | Boolean `scope`, `budget`, `reversal`, `kill_switch` | One mark per passed assertion |
| AI Red / missed | `objective_ids` | Two per distinct verified missed objective, capped at 16 |
| AI Red / false | `objective_ids` | Two per distinct verified harmful action objective, capped at 16 |
| AI Red / breadth | `families` | Two per distinct successful family, capped at 8 |

Availability uses functional probes every 15 seconds with a five-second timeout. Readiness alone is insufficient. Application detections must come from the team's detector within 60 seconds; central IDS observations do not supply this credit. Response assertions check scope, initiation within 300 seconds, actual mitigation and preserved or recovered legitimate service. Correct inaction requires an explicit scenario assertion. Protected objectives require matched tests across paired builds, failed exploit assertions, successful legitimate-use assertions and no contradicting valid finding.

Correlation checks four runtime behaviors: deterministic analytics, model execution, combined decisions and continued detection during model unavailability. Decision assertions cover correct context/scope, valid evidence with a concise rationale, and consistent decision/action/outcome references. Safety checks require actual scope restraint, budget compliance, expiry/reversal and kill-switch behavior while detection continues. A rejected unsafe request does not demonstrate the team's restraint.

AI Red families are `injection`, `evasion`, `budget_exhaustion` and `baseline_poisoning`. False-action credit requires a real executed effect on legitimate competition activity, causal evidence and recovery. A requested or shadow action is insufficient.

Application Red retains `min(2 × valid findings,16)`, severity weights critical/high/medium/low of 5/3/2/1 capped at 16, and `max(8 − 2 × reviewed out-of-scope findings,0)`. Duplicate and invalid findings receive no validity or severity credit.

`quality` contains exactly:

```json
{"receipts_complete":true,"source_authenticated":true,"clock_uncertainty_seconds":0}
```

An optional `pending_reasons` array explains missing prerequisites. Incomplete trusted receipts or unauthenticated sources hold every calculation. Clock uncertainty above two seconds holds timing-dependent criteria. Zero denominators remain pending. For precision, complete observation of delivered attacks with no detections is measured zero. Missing telemetry never proves success or failure. Only an explicit competition incident interval excludes a shared infrastructure outage.

## Authentication and bounds

Use the direct HTTPS `/api/feed/` and `/api/scoring/evidence/` paths, including the trailing slash. Both accept POST with `Content-Type: application/json` and `X-Feed-Token: <DJANGO_FEED_TOKEN>`. An unset token returns 404; an invalid token returns 403. Token comparison is constant-time.

Bodies are limited to one MiB. Duplicate JSON field names at any nesting level and non-finite numbers are rejected before processing. Feed requests contain at most 100 rows. Credentials, personal data, raw request/response bodies and prompts are prohibited in evidence. A retryable archive-capacity response does not permit dropping evidence or publishing without it.

## Archive before publication

POST `/api/scoring/evidence/` with this envelope:

```json
{
  "schema_version":"scoring-evidence/1.0",
  "competition":6,
  "team":"JS26-B-014",
  "family":"metrics",
  "snapshot":{
    "policy_version":"main-scoring/1.0",
    "side":"application_blue",
    "criterion":"availability",
    "window":{"start":"2026-10-14T08:00:00Z","end":"2026-10-14T09:00:00Z"},
    "facts":{"expected":240,"completed":240,"successful":216},
    "quality":{"receipts_complete":true,"source_authenticated":true,"clock_uncertainty_seconds":0},
    "assignment_revisions":[1]
  },
  "records":[{"family":"metrics","id":"metric_probe_window","successful":216,"completed":240}],
  "queries":["functional probe receipts for the stated window"]
}
```

Every record has a family and matching typed identifier: application `evt_`, infrastructure `infra_`, metrics `metric_`, scenario `case_`, or AI `ai_`. The envelope family is one of these or `mixed`. Original application and infrastructure identifiers retain their families. Records contain actual sanitized receipts or measured query results, not just a query description. The compact example shows the envelope; actual probe evidence must support every counted observation.

Each archive contains at most 1,000 records and 16 query strings of at most 4,096 characters. Large input sets may be split into archives with the identical snapshot. A publication references all chunks, up to 128. The platform validates current historical attribution at both upload and publication, checks private storage by reading it back, and charges each unique archive once against the enrolled capacity.

A successful upload returns HTTP 201:

```json
{"schema_version":"scoring-evidence/1.0","id":"UUID","sha256":"ARCHIVE_SHA256","input_sha256":"SNAPSHOT_SHA256"}
```

The archive digest covers the entire canonical request body; the input digest covers exactly `snapshot`. Both are verified again before accepting marks. Referenced evidence is retained through the configured appeal deadline and any unresolved appeal. This does not extend seven-day raw telemetry retention.

Raw archives are available only to authenticated judges and staff through `/scoring/evidence/UUID/`. Teams can read their own calculation, observation quality and correction history through `/scoring/revisions/ID/`. Storage URLs are not used as participant evidence links.

## Criterion readiness

The publisher reports collection or delivery gaps with POST `/api/scoring/readiness/`, using the same feed authentication and body limit:

```json
{"competition":6,"team":"JS26-B-014","criterion":"availability","policy_version":"main-scoring/1.0","window":{"start":"2026-10-14T08:00:00Z","end":"2026-10-14T09:00:00Z"},"status":"insufficient_data","reason_code":"missing_probes"}
```

Statuses are `collecting`, `insufficient_data`, `attribution_unresolved`, `publication_failed` and `published`. The reason is a machine code, at most 80 lowercase letters, digits or underscores. Windows remain cumulative within the enrolled window. A published status requires a matching accepted score revision; successful feed publication sets it automatically. Older readiness windows cannot replace newer ones. These records describe current delivery health, create no marks and cannot release an override. The team's mark breakdown shows this status alongside its last verified results.

## Versioned score publication

POST `/api/feed/`:

```json
{
  "schema_version":"scoring-feed/2.0",
  "competition":6,
  "scores":[{
    "team":"JS26-B-014",
    "stage":"main",
    "criterion":"availability",
    "key":"main:availability",
    "revision":1,
    "policy_version":"main-scoring/1.0",
    "window":{"start":"2026-10-14T08:00:00Z","end":"2026-10-14T09:00:00Z"},
    "facts":{"expected":240,"completed":240,"successful":216},
    "quality":{"receipts_complete":true,"source_authenticated":true,"clock_uncertainty_seconds":0},
    "assignment_revisions":[1],
    "marks":"10.80",
    "evidence":[{"id":"UUID","sha256":"ARCHIVE_SHA256"}],
    "note":"Verified functional probe results."
  }]
}
```

Every field except `note` is required. Additional fields are rejected. The snapshot reconstructed from this row must match every retained archive exactly. The platform independently calculates marks using the frozen policy. Notes are visible to the team and limited to 2,000 characters.

Identity is the canonical platform team identifier, never a provisioning ID. The team must be approved. Historical attribution must cover the complete window without ambiguity, and the assignment snapshot must be current. All relevant distinct positive assignment revisions are supplied. Unknown or stale identity holds publication.

The logical key is always `main:<criterion>`. It does not change by day, evidence hash, rule version or recalculation. Producer revisions are positive increasing integers. An exact repeated revision is acknowledged without another mark. Changed content at the same revision, older revisions and shrinking cumulative windows are rejected. Concurrent writes serialize on the team and policy. Each accepted correction creates an immutable score revision at its acceptance time.

Judge overrides replace the criterion and hold automatic updates until an explicit release with a reason. Release restores the last automatic result when one exists. The five-minute participant leaderboard uses accepted revisions effective at its cutoff, so a later correction cannot rewrite an earlier board.

## Responses and retry rules

HTTP 200 means all rows were accepted or exact duplicates. HTTP 202 means at least one row was held or rejected. Inspect every row outcome:

```json
{
  "schema_version":"scoring-feed/2.0",
  "accepted":1,
  "rejected":[{"index":1,"status":"override_hold","code":"override_hold","error":"judge override is active","retryable":true}],
  "outcomes":[
    {"index":0,"status":"accepted","key":"main:availability","revision":1,"score_revision":12,"sequence":1,"marks":"10.80"},
    {"index":1,"status":"override_hold","key":"main:detected","revision":2,"code":"override_hold","error":"judge override is active","retryable":true}
  ]
}
```

Accepted and duplicate outcomes echo the exact key and producer revision. Held or rejected object rows also echo them. Pending observation, grace periods, stale identity, active overrides and server failures are retryable. Invalid facts, policy mismatches, changed/older revisions and finalized competitions require correction or an explicit appeal workflow. Do not treat HTTP 202 as blanket acknowledgement. Preserve unacknowledged outbox rows across restarts and retry transport errors, throttling and server errors with bounded backoff.

## Appeals and retained proof

The captain can appeal a specific accepted revision from its score explanation page after results are published. The platform uses the existing configured appeal deadline, procedural grounds and appeal workflow. A submission appeal continues to name its submission. A score appeal names exactly one immutable revision and its logical component; only one appeal for that component may remain open across revisions.

Organizers assign an active independent judge from the internal Appeals queue. Every recorded decision-maker for the score component and every current member of the appealing team is excluded. Only the assigned independent judge can resolve the appeal, with a written explanation. An upheld appeal records the procedural decision; an authorized score correction is a separate action that appends its own score revision. The captain can withdraw an open appeal with an explanation.

Opening a score appeal links its available archives and sets their appeal holds. Missing archived proof is recorded for the reviewer and does not remove the right to appeal. Closing or withdrawing an appeal clears a hold only when no other open appeal references the archive. The archive's existing retention deadline is preserved and extended to the configured appeal deadline when needed. Teams see appeal status and outcome on their original result; raw proof remains restricted to internal reviewers.

## Legacy compatibility

Unenrolled competitions may still send the original unversioned `{competition,scores}` envelope. It accepts canonical team identifiers or unambiguous cell/enterprise pairs and preserves keyed replacement. It now rejects malformed values, wrong-role criteria, non-finite marks and ambiguous identities. Criterion and stage caps apply in the shared scoring service. Application Red cannot receive telemetry Main marks.

Enrollment disables this legacy path for that competition. New deployments must use versioned publication. Existing Entry, Documentation, bonus and finding service paths keep their interfaces and now append score revisions. Migration records current marks as an explicit baseline; it does not fabricate unavailable correction history before that baseline.
