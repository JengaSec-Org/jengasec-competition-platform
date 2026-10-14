# Platform identity and scoring sources

The platform owns canonical teams, target permissions, issued scenarios and
scoring decisions. OBS reads these interfaces and publishes measured facts.
Native provisioning identifiers are not team identifiers.

## Identity and evidence compatibility

The five read-only PostgreSQL views retain contract version 1:

- `obs_ip_allocations_v1`: allocation ID, IP address, kind, opaque participant
  and team IDs, team label, namespace, status and validity interval.
- `obs_applications_v1`: canonical application ID, enterprise, application,
  namespace, service name, IP address, status, optional/enabled flags and
  verified frozen digest. This view contains APP01 through APP09 only.
- `obs_scope_assignments_v1`: canonical actor team, exact source network,
  target enterprise/application/namespace/IP, active flag and interval.
- `obs_access_principals_v1`: opaque participant ID, role, team and allowed
  target. Disabled accounts receive no access. Staff and judges remain a
  separate access class.
- `obs_findings_v1`: exactly `contract_version`, `updated_at`, `finding_id`,
  `enterprise`, `application`, `namespace`, `start_ts`, `end_ts`,
  `telemetry_event_ids` and `correlation_ids`.

Every view includes `contract_version` and `updated_at`. Views exclude names,
email addresses, credentials, finding prose and uploaded files. Database
owners grant the OBS role SELECT on these views only, over verified TLS.
The migration does not create or broaden a database role.

The finding view uses verified historical ownership over the complete finding
window. Unknown or ambiguous ownership produces no export row. Event IDs use
`evt_` plus 64 lowercase hexadecimal characters. Infrastructure `infra_` IDs
belong in their separate evidence family. Correlation IDs are bounded opaque
references. At least one explicitly cited reference is required for export.

V1 allocation and scope views expose current records. V2 provides history.
New scoring identity starts at enrollment, never at an inferred earlier date.
Earlier source data requires independent verified records before it can be
used for a score. The migration does not fabricate historical assignments.

## Read-only scoring API

GET routes accept `X-Feed-Token` using the existing `DJANGO_FEED_TOKEN`.
The identity route also accepts `X-Scoring-Identity-Token` using the dedicated
`SCORING_IDENTITY_TOKEN`. That credential has access only to identity reads;
it cannot read receipts or scenarios, publish scores, or create observations.
AI runtime hosts use this separate credential for independent identity refresh.
Unset authentication returns 404; an invalid credential returns 403. Responses
are private and use `Cache-Control: no-store`.

`GET /api/scoring/identity/?competition=<id>` returns:

```json
{
  "schema_version": "scoring-identity/2.0",
  "revision": 1,
  "captured_at": "2026-01-01T12:00:00Z",
  "expires_at": "2026-01-01T12:02:00Z",
  "competition": {"id": 1, "start": "2026-01-01T12:00:00Z", "end": "2026-01-01T13:00:00Z"},
  "teams": [], "allocations": [], "targets": [], "scopes": []
}
```

The example has no eligible records. Each populated row has `revision`,
`valid_from` and nullable `valid_until`. Intervals are UTC and half-open.

| Collection | Additional fields |
| --- | --- |
| teams | team, side, track, enterprise, cell_id, namespace |
| allocations | allocation_id, team, source_ip, kind |
| targets | target_id, team, enterprise, application, namespace, ip_address, port, protocol, frozen_digest, frozen_extra_digests, verified_at |
| scopes | scope_id, team, target_id, source_cidr, allowed_actions, active |

Supply `start` and `end` together to request overlapping historical intervals.
Without them, the response covers current execution. A source snapshot expires
within 120 seconds. Consumers must validate event-time intervals and reject
overlap, unknown identities and expired authority. There is no last-good
fallback for authorizing scoring actions.
An explicit historical request sets `historical: true`. It can return verified
closed-window intervals during delivery grace, corrections or appeals after
execution ends. Its response expires within 120 seconds of capture; this does
not extend any execution interval. Current action snapshots remain closed
outside the competition window. Historical requests reject any period beyond
the last verified source coverage and still check unsupported source writes.

Application targets require an approved team, its issued canonical namespace,
and a frozen digest from the authenticated deployment workflow. A fresh
`deployed_digest_verified_at` confirms which artifact is pinned. Application
readiness is measured separately: an app outage does not remove eligibility.
`frozen_extra_digests` preserves the ordered bare SHA-256 digests of at most
two extra containers. A verified application without extras has `[]`. Identity
intervals from before this field was recorded have `null`; history is not
backfilled from mutable submissions. A changed extra-image set requires a
newer runtime confirmation and starts a new identity interval. Unsupported
changes to submitted or frozen extra-image definitions hold identity reads
until the source is reconciled.
AI targets require an organiser-accepted digest and a fresh receipt from a
registered deployment verifier. A team cannot attest its own artifact.

Enrollment freezes a team's competition, identifier, side and track so a role
change cannot reinterpret accepted marks. Ordinary team, WireGuard, target,
scope and deployment updates advance history
in the same transaction. Unsupported bulk writes invalidate the source
fingerprint until `sync_observability_data --competition <id>` reconciles it.
An expired artifact interval is never extended across an unobserved gap.

An AI defender receives real actions only through an explicit `ActionScope`.
Its `source_cidr` is the traffic address it may block, not the API caller's IP.
The default source set is the target's active assigned Red addresses. An
organiser may specify exact host networks from other issued participant
allocations for legitimate-traffic exercises. Unknown addresses confer no
authority. APP08 and APP09 require an enabled registration in both enterprises.
Snapshots also include all approved competition `exclusions` and a bounded
`producers` registry with producer ID, kind, canonical team, validity and
revocation time. Credentials and credential hashes are never exported.

## Issued scenarios and producer receipts

`AttackScenario` retains the human description. `ScenarioVersion` freezes its
code, track, version, attack/legitimate label, family and machine assertions.
Each assertion has `key`, `operator: "eq"` and a boolean `expected` value.
Supported keys are the outcome and decision checks below, plus
`runtime_checks.<check>`. An unsupported check is rejected at issuance.
The older scenario point fields do not add marks to the current rubric.

`ScenarioExecution` binds one opportunity to an existing `CompetitionObjective`,
canonical actor/target, verified frozen artifact, execution window and
environment. Reusing an opportunity key for the same target is rejected.
The `target_team` owns the workload. `defender_team` identifies the defending
competitor and the scoring recipient. They differ when an AI defender protects
an application owned by another team. That case requires an explicit active
action scope, an AI Red assignment to the defender and frozen producer
identities for that defender. A target owner is never silently treated as an
AI competitor. Set `--defender-team` when issuing such a case.
Scenarios and cases cannot be changed after issuance. Rehearsal records remain
explicit and do not become competition observations.
Each case freezes `required_producers`, a map from runner, verifier, competitor
and gateway to producer UUID lists. The operator must resolve ambiguous
registrations with explicit producer IDs. Only those producers can emit case
receipts. They must have valid credentials covering the execution window.

`POST /api/scoring/receipts/` requires a distinct per-producer credential in
`Authorization: Bearer <credential>`. A feed token cannot create observations.
The body requires `producer_id`, `receipt_id` (UUIDs), `kind`, `observed_at`,
`clock_uncertainty_ms`, `measurements` and `evidence_refs`. Add the issued
`case_id` for case observations or `target_id` for standalone probes and
artifact attestations. Team producers are restricted to their own defender.
Case and target-bound receipt exports include `frozen_extra_digests` when the
original identity interval recorded the set, preserving that set after later
source edits. They omit the field for unknown historical sets so existing
immutable export payloads retain their wire shape. A case cannot accept an
observation whose extra-image set differs from its issued target.

| Kind | Allowed producer | Measurements |
| --- | --- | --- |
| delivered | runner | delivered boolean |
| detection | competitor | classification (attack/legitimate), incident_id |
| decision | competitor | decision_id, action_ids, correlation_ids, rationale_reference |
| decision | verifier | context_valid, evidence_valid, consistency_valid booleans |
| action | gateway | action_id, action_type, state |
| outcome | verifier | success, objective_reached, legitimate_ok, protected, response_authorized, mitigated, recovered, action_verified, induced, harmful, response_started_at, incident_id, action_id, runtime_checks |
| probe | verifier | successful boolean, expected=1, completed=1, target_digest |
| target_verification | deployment | target_digest |
| source_health | any registered producer | start, end, expected, completed, lost |

Runtime check names are deterministic, model, combined, fallback, scope,
budget, reversal and kill_switch. Outcomes may also reference
`response_no_action_required`, but it must be true in the frozen scenario.
`no_valid_finding` is checked against reviewed platform findings and exported
as current case authority. `paired_build_verified` requires actual delivery
and verifier receipts for the same opportunity and objective on both builds.
These are not a competitor's self-assessment.

An outcome with `action_verified`, `induced` or `harmful` true must name a
bounded `action_id`, and its case must include a trusted gateway. The
calculator matches that ID to the frozen gateway's executed, verified or
reverted action receipt before awarding consequence credit. Receipts can
arrive out of order; an uncorroborated outcome remains incomplete.

Evidence references are objects with `family` and `id`. Families are
application, infrastructure, metrics, scenario and AI (`ai`). References do
not contain raw prompts, bodies or private rationales. Their IDs are bounded
opaque strings. Application and infrastructure references enforce their
respective event-ID prefixes. Referenced artifacts are archived separately by
the scoring evidence workflow.

Health receipts preserve the original half-open interval, at most one hour.
Completed plus lost cannot exceed expected. Intervals from one producer may
not overlap for the same target. Set `target_id` for target-specific coverage.
A global process heartbeat does not prove that every target stream was
complete. The calculator requires separate coverage from each required
trusted runner, verifier and gateway for the relevant case and target.
A competitor's self-reported health cannot establish that silence was a
measured miss; the independent verifier must attest defender transport coverage.
Clock uncertainty remains explicit; the calculator holds timing
claims when uncertainty exceeds two seconds.

An accepted receipt returns 201 with its server `sequence`. An identical retry
returns 200 and the same sequence. Changed content under the same receipt ID
returns 409. Invalid shapes, unsupported claims or wrong scope return 400.
Requests are limited to 64 KiB. No credential or raw request body is logged.

`GET /api/scoring/receipts/?competition=<id>&after=<sequence>&cases_after=<sequence>&limit=<n>`
returns `scoring-receipts/1.0`, receipts, referenced cases, `next_cursor`,
`has_more`, `cases_next_cursor` and `cases_has_more`. The limit is 1 through
1000. The base case set respects that limit; each application case can add one
paired-build peer, for at most 2000 returned cases. The independent case cursor
advances only through the newly issued cases actually returned. Case issuance
has its own cursor so unanswered cases are discoverable.
Insertions and cursor reads serialize per competition; a delayed commit cannot
hide behind an already returned cursor. Late observations keep their original
time and receive a new server sequence.

Finding corrections append platform-owned `finding_review` receipts with no
external producer credential. They cause the affected case authority to be
returned again. External clients cannot emit this kind. Collectors update
their case state and recompute affected marks without treating the correction
as an additional attack.

`GET /api/scoring/scenarios/?competition=<id>&start=<UTC>&end=<UTC>` exports
cases, receipts, standalone probes, health and approved infrastructure
exclusions in a window of at most 15 minutes. Health windows that intersect
the query are returned whole. All exports are capped at 1 MiB and reject an
oversized result instead of truncating it. Reduce the interval or limit and
retain stable receipt IDs while assembling coverage.

## Operator setup and rollback

Use `manage.py scoring_sources --help` for issuance commands. The sequence is:
enroll the scoring policy; register actual targets; register trusted producers;
obtain artifact confirmations; grant required AI action scopes; freeze scenario
assertions; issue canonical cases. Each write command names an active organiser
with `--actor`. Producer credentials are written once to an owner-only file
with `--credential-file`, never to stdout. Teams receive only their own
competitor credentials. Use `revoke-producer` to end one producer's access.

Run ordinary migrations before enabling the new routes. The v1 view migration
supports rollback by dropping only its five views. Do not reverse history
tables after they hold accepted evidence. To stop a rollout, disable publication
and action execution, revoke affected producer credentials and retain the
immutable records for review. Existing unenrolled competitions keep their
current workflow. No migration issues cases, declares a live target or awards
points.
