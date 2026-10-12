# API collection (Bruno)

Open this folder in [Bruno](https://www.usebruno.com/) and select the `local` environment, or run headless:
`npx @usebruno/cli run --env local` from this folder.

The agent has no browser-facing API of its own. Authenticated chat and tool calls only come from the OpenEMR module,
signed with a shared secret and carrying the physician's OAuth token, so they cannot be reproduced by hand in a
collection. What the collection covers is everything an operator or an attacker can do without that:
liveness and readiness, the refusals (unsigned chat, unsigned tool call, dashboard without credentials, FHIR without
a token) and the authenticated dashboard. The full signed flow is exercised by `agent/tests/test_gateway_live.py`
and by the Playwright specs in `tests/playwright/specs/copilot-*.spec.ts`.
