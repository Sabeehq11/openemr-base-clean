# Authorization proof (gate for all clinical tools)

Status: **PASSED** on the local stack (synthetic data only). Re-run with
`cd agent && OE_LIVE=1 .venv/bin/python -m pytest tests/test_authz_unit.py tests/test_authz_live.py`.
Fixture: `scripts/authz_fixture.php` (idempotent): physicians A and B, a nurse, a front-desk user, patients
pid 100-111 split between A (even) and B (odd), one active CareTeam per patient naming only the owner.

## What OpenEMR itself enforces (measured, not assumed)

| Check | Result |
|---|---|
| Token comes from the authorization-code flow as the logged-in user | Yes. Introspected `sub` equals the user's UUID (A, B, nurse). |
| Password grant for the Co-Pilot client | Was enabled (`oauth_password_grant=3`) and returned a token. Set to 0; now refused with HTTP 400. Tested. |
| Garbage or missing bearer token | 401. |
| Revoked token (`api_token.revoked=1`) | 401 on FHIR; introspection reports inactive. |
| Token past DB expiry | Introspection still says active (it checks the JWT `exp`), but FHIR reads with the token are refused. The agent treats any failure as denial. |
| Physician A's token reading physician B's patient (`Patient/{id}`, `Condition?patient=`) | **200, data returned.** OpenEMR checks the scope and ACL section, not the patient. |
| Nurse and other physician reading any patient | Also 200. |
| `providerID` | Not exposed in FHIR (`generalPractitioner` absent), and not a safe sole basis anyway. |
| SMART EHR-launch token bound to a patient | Narrows `patient/*` scope reads to the launched patient, but the launch context is not checked against the user. `checkUserHasAccessToPatient()` in `BearerTokenAuthorizationStrategy.php` is a stub that returns `true`. Useful as narrowing, not authorization. |
| Front-desk user (`front` ACL group) reading CareTeam | 403 from OpenEMR's ACL. |

## Decision

OpenEMR does not provide patient-level denial, so the agent adds one from OpenEMR data on every request
(`app/authz.py`, `PatientAuthorizer`):

1. Introspect the user's token at OpenEMR. It must be active and unexpired.
2. It must carry `user/Patient.rs` and `user/CareTeam.rs`.
3. Its `sub` must equal the user the caller claims, when a claim is made.
4. That `sub` must be an active Practitioner member of an active CareTeam for that patient, read through FHIR
   **with the user's own token** (so OpenEMR's own ACL also applies).
5. Any error, timeout, malformed body, or missing piece is a denial. No tool runs and the reason is logged.

HMAC signing between the module and the agent proves only that the request was not altered. It is never an
input to this decision. A `providerID` or user claim from the caller cannot grant access (test
`test_provider_id_style_claims_are_ignored`).

## Remaining limits (stated, not hidden)

- The care team is the access list. Anyone who can edit care teams in OpenEMR can change who the Co-Pilot lets in.
  Care-team edits should be treated as an access-control change. Out of scope here: audit of those edits.
- Membership is checked for any care-team practitioner. A nurse added to a team would be admitted; the seeded
  nurse is on no team, so is denied. Restricting to physician roles is a policy choice for Checkpoint 3.
- Introspection trusts the JWT `exp`; the CareTeam read with the user's token is the second check that catches
  DB-side expiry.
- The probe OAuth client was registered by dynamic registration and enabled by hand in the DB (equivalent to
  admin approval). Registered scopes are limited to `openid fhirUser user/Patient.rs user/CareTeam.rs ...` read scopes.
- Local TLS is self-signed (`OPENEMR_VERIFY_TLS=false` in dev only).
