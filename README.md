[![Syntax Status](https://github.com/openemr/openemr/actions/workflows/syntax.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/syntax.yml)
[![Styling Status](https://github.com/openemr/openemr/actions/workflows/styling.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/styling.yml)
[![Testing Status](https://github.com/openemr/openemr/actions/workflows/test.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/test.yml)
[![JS Unit Testing Status](https://github.com/openemr/openemr/actions/workflows/js-test.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/js-test.yml)
[![PHPStan](https://github.com/openemr/openemr/actions/workflows/phpstan.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/phpstan.yml)
[![Rector](https://github.com/openemr/openemr/actions/workflows/rector.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/rector.yml)
[![ShellCheck](https://github.com/openemr/openemr/actions/workflows/shellcheck.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/shellcheck.yml)
[![Docker Compose Linting](https://github.com/openemr/openemr/actions/workflows/docker-compose-lint.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/docker-compose-lint.yml)
[![Dockerfile Linting](https://github.com/openemr/openemr/actions/workflows/docker-lint-hadolint.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/docker-lint-hadolint.yml)
[![Isolated Tests](https://github.com/openemr/openemr/actions/workflows/isolated-tests.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/isolated-tests.yml)
[![Inferno Certification Test](https://github.com/openemr/openemr/actions/workflows/inferno-test.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/inferno-test.yml)
[![Composer Checks](https://github.com/openemr/openemr/actions/workflows/composer.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/composer.yml)
[![Composer Require Checker](https://github.com/openemr/openemr/actions/workflows/composer-require-checker.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/composer-require-checker.yml)
[![API Docs Freshness Checks](https://github.com/openemr/openemr/actions/workflows/api-docs.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/api-docs.yml)
[![codecov](https://codecov.io/gh/openemr/openemr/graph/badge.svg?token=7Eu3U1Ozdq)](https://codecov.io/gh/openemr/openemr)

[![Backers on Open Collective](https://opencollective.com/openemr/backers/badge.svg)](#backers) [![Sponsors on Open Collective](https://opencollective.com/openemr/sponsors/badge.svg)](#sponsors)

# AgentForge: Clinical Co-Pilot (OpenEMR fork)

An AI Clinical Co-Pilot for a primary care physician, built on OpenEMR. Week 1 deliverables:

| Doc | Purpose |
|-----|---------|
| [docs/week1/AUDIT.md](docs/week1/AUDIT.md) | Security, performance, architecture, data quality and compliance audit |
| [docs/week1/USERS.md](docs/week1/USERS.md) | Target user, workflow and use cases (UC-1 to UC-4) |
| [docs/week1/ARCHITECTURE.md](docs/week1/ARCHITECTURE.md) | Agent integration plan, verification strategy, tradeoffs (section 12 = Checkpoint 2 as built, section 13 = Checkpoint 3) |
| [docs/week1/PERFORMANCE.md](docs/week1/PERFORMANCE.md) | Load-test baselines (10 and 50 users), bottleneck analysis, rerun commands |
| [docs/week1/AI_COST_ANALYSIS.md](docs/week1/AI_COST_ANALYSIS.md) | Measured cost per request, 100 to 100K user projections, architecture changes per tier |
| [agent/README.md](agent/README.md) | Co-Pilot setup guide, tests, endpoints, observability |

**Deployed app:** _TBD (see [deploy/](deploy/))_ (demo data only, no real PHI)

## Week 1 baseline and Week 2 (multimodal)

This repository has two layers. Everything outside the "Week 2" column below exists today and is the tagged baseline (`w1-baseline`). Anything marked **(planned)** is design only and does not run yet. Week 2 items without that mark are built (checkpoint C1) and only run with `W2_ENABLED=true`.

| | Week 1 baseline (built, tagged `w1-baseline`) | Week 2 multimodal (in progress on branch `week2-agentforge`) |
|---|---|---|
| What it does | Answers a physician's questions about the open chart, with cited, verified answers | Adds lab PDF and intake form upload, extraction with source citations, and guideline evidence retrieval |
| Endpoint | `POST /v1/chat` | `POST /v1/w2/chat` **(planned)** |
| Switch | Always on | `W2_ENABLED`, off by default. With it off, behavior is identical to Week 1 |
| Design doc | [docs/week1/ARCHITECTURE.md](docs/week1/ARCHITECTURE.md) | [W2_ARCHITECTURE.md](W2_ARCHITECTURE.md) |
| Status | Agent, module, verifier, audit chain, dashboard, evals and load tests built | C0 (baseline and architecture) and C1 (upload, extract, retrieve) done. C2 to C5 not started. `POST /v1/w2/chat` arrives in C2; C1 has `/v1/w2/documents`, `/v1/w2/documents/{id}`, `/v1/w2/documents/{id}/links` and `/v1/w2/retrieve` |
| Measured baseline | [docs/week2/BASELINE.md](docs/week2/BASELINE.md): 522 offline agent tests passed (30 live tests skipped), 60 of 60 stub evals passed | The same results must hold with `W2_ENABLED` at 0 and 1 |

Week 2 planning documents: [W2_ARCHITECTURE.md](W2_ARCHITECTURE.md) (architecture and defense), [docs/week2/ROADMAP.md](docs/week2/ROADMAP.md) (roadmap and checkpoints), [docs/week2/BASELINE.md](docs/week2/BASELINE.md) (measured Week 1 baseline).

### Environment variables

Agent settings live in `agent/.env` (copy from `agent/.env.example`; git-ignored, never commit real values). Defaults are those in `agent/app/config.py`.

| Variable | Default | Needed by | Purpose |
|---|---|---|---|
| `OPENEMR_BASE_URL` | `https://localhost:9300` | Week 1 | OpenEMR address as seen from the agent |
| `OPENEMR_VERIFY_TLS` | `true` | Week 1 | Set `false` only for the local self-signed certificate |
| `LLM_PROVIDER` | `stub` | Week 1 | `stub` (no network, deterministic) or `openrouter` |
| `OPENROUTER_API_KEY` | empty | Week 1, only with `openrouter` | Model provider key |
| `OPENROUTER_MODEL` | `anthropic/claude-sonnet-4.5` | Week 1 | Model name |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Week 1 | Provider endpoint |
| `MODULE_SIGNING_SECRET` | empty | Week 1 | Signs module-to-agent requests (integrity only) |
| `AUDIT_HMAC_KEY` | empty | Week 1 | Key for the audit hash chain |
| `DASHBOARD_TOKEN` | empty (dashboard off) | Week 1 | Password for `/dashboard` |
| `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET` | empty | Week 1 | OAuth client the module registered for the agent |
| `DATA_DIR` | `./data` | Week 1 | Agent SQLite files (traces, audit) |
| `TOOL_TIMEOUT_S` | `5.0` | Week 1 | Timeout per OpenEMR tool call |
| `MAX_INFLIGHT_CHATS` | `0` (off) | Week 1 | Optional cap on chats in progress |
| `RATE_LIMIT_PER_MIN` | `60` | Week 1 | Per-user request limit, 0 disables |
| `COPILOT_FIXTURE_PASSWORD` | none | Week 1 setup scripts | Password for seeded fixture users (12+ characters) |
| `W2_ENABLED` | off | Week 2 | Turns the Week 2 flow on (routes are 404 without it) |
| `W2_VLM_MODEL` | `anthropic/claude-sonnet-4.5` | Week 2 | Vision model for document extraction, through OpenRouter |
| `W2_RERANK_MODEL` | `cohere/rerank-v3.5` | Week 2 | Reranker for guideline retrieval, called through OpenRouter `/rerank` with `OPENROUTER_API_KEY`; with no key the offline lexical reranker is used |
| `W2_EMBED_MODEL` | `openai/text-embedding-3-small` | Week 2 | Hosted dense embeddings (cached); CI uses a hashed embedder |
| `W2_USE_STUB_VLM` | `false` | Week 2 | Canned extraction, no network. Offline demos and CI only |
| `W2_MAX_UPLOAD_BYTES`, `W2_MAX_PAGES` | 8 MiB, 5 | Week 2 | Upload caps, enforced before any model call |

The Week 2 variables are read only when `W2_ENABLED=true`; with it off the agent behaves exactly as at `w1-baseline`. C1 demo: `scripts/copilot/demo_c1.sh` (offline, stub model) and [docs/week2/checkpoints/C1-mvp-ingest-extract-retrieve.md](docs/week2/checkpoints/C1-mvp-ingest-extract-retrieve.md).

## Architecture overview

A thin OpenEMR module (`oe-module-copilot`) mounts a chat dock in the Tabs shell and forwards signed, patient-scoped requests to a separate Python agent service. The agent reads OpenEMR only over FHIR/REST with the physician's own OAuth2 token, through six typed read-only tools. Every answer passes a verification layer (source attribution plus deterministic clinical rules) before display. The agent writes its own append-only audit log and a SQLite trace store (served at `/dashboard`; Langfuse is not deployed), each tied to one correlation ID that also goes out on every call to OpenEMR and the model provider. Details, trust boundaries and tradeoffs: [docs/week1/ARCHITECTURE.md](docs/week1/ARCHITECTURE.md). Status: the agent, module, verifier, audit chain, dashboard, evals and load tests are built (Checkpoints 1 to 3). Setup, endpoints and tests: [agent/README.md](agent/README.md). On-call: [agent/RUNBOOK.md](agent/RUNBOOK.md). Authorization findings: [agent/AUTHZ_PROOF.md](agent/AUTHZ_PROOF.md). Eval results: [evals/results/stub-latest.md](evals/results/stub-latest.md).

## Run the agent (Week 1 baseline)

After OpenEMR is up (below), the agent is a separate container. Short version, full steps in [agent/README.md](agent/README.md):

```bash
cp agent/.env.example agent/.env      # fill in the secrets; LLM_PROVIDER=stub needs no model key
docker compose -f agent/docker-compose.yml up -d --build
curl localhost:8400/ready             # checks OpenEMR, the LLM provider, the data directory and the audit chain
cd agent && python -m pytest -q       # unit tests, no network; evals: PYTHONPATH=agent python -m evals.run
```

Evals run on demand (stub tier under pytest, live tier with a spend cap); they are not wired into a CI workflow.
The runnable API collection, including signed requests, is in [docs/api-collection/copilot](docs/api-collection/copilot/README.md).

## Run locally

Requires Docker (about 8 GB RAM). First boot takes about 5 minutes.

```bash
./scripts/copilot/setup-local.sh      # starts OpenEMR; seeds 13 synthetic patients only if missing (safe to re-run)
./scripts/copilot/setup-local.sh --reseed       # recreate the synthetic patients (other data untouched)
./scripts/copilot/setup-local.sh --reset-demo   # DESTRUCTIVE: wipe DB, reload stock demo data, then reseed
```

On a first run against a fresh stack, use `--reset-demo` once to load OpenEMR's stock demo patients (pid 1-3).

- App: http://localhost:8300 (login `admin` / `pass`), phpMyAdmin: http://localhost:8310
- The stack is `docker/development-easy-light` (OpenEMR flex image + MariaDB). Dev credentials are defaults, for local use only.
- `scripts/copilot/seed_demo_patients.py` is deterministic. It adds meds, labs, vitals and encounters, plus deliberate data-quality defects (missing DOB, a duplicate patient, stale labs) used by the audit.

## Browser tests (Playwright)

```bash
cd tests/playwright && npm install && npx playwright install chromium
npm test                                   # local stack
BASE_URL=https://<deployed-url> npm test   # deployed app
```

## Deploy

On a VPS with Docker and ports 80/443 open: `cd deploy && cp .env.example .env`, set strong passwords and `DOMAIN`, then run `./deploy.sh`. It uses Caddy for automatic HTTPS in front of OpenEMR and MariaDB.

---

# OpenEMR

[OpenEMR](https://open-emr.org) is a Free and Open Source electronic health records and medical practice management application. It features fully integrated electronic health records, practice management, scheduling, electronic billing, internationalization, free support, a vibrant community, and a whole lot more. It runs on Windows, Linux, Mac OS X, and many other platforms.

### Contributing

OpenEMR is a leader in healthcare open source software and comprises a large and diverse community of software developers, medical providers and educators with a very healthy mix of both volunteers and professionals. [Join us and learn how to start contributing today!](https://open-emr.org/wiki/index.php/FAQ#How_do_I_begin_to_volunteer_for_the_OpenEMR_project.3F)

> Already comfortable with git? Check out [CONTRIBUTING.md](CONTRIBUTING.md) for quick setup instructions and requirements for contributing to OpenEMR by resolving a bug or adding an awesome feature 😊.

### Support

Community and Professional support can be found [here](https://open-emr.org/wiki/index.php/OpenEMR_Support_Guide).

Extensive documentation and forums can be found on the [OpenEMR website](https://open-emr.org) that can help you to become more familiar about the project 📖.

### Reporting Issues and Bugs

Report these on the [Issue Tracker](https://github.com/openemr/openemr/issues). If you are unsure if it is an issue/bug, then always feel free to use the [Forum](https://community.open-emr.org/) and [Chat](https://www.open-emr.org/chat/) to discuss about the issue 🪲.

### Reporting Security Vulnerabilities

Check out [SECURITY.md](.github/SECURITY.md)

### API

Check out [API_README.md](API_README.md)

### Docker

Check out [DOCKER_README.md](DOCKER_README.md)

### FHIR

Check out [FHIR_README.md](FHIR_README.md)

### For Developers

If using OpenEMR directly from the code repository, then the following commands will build OpenEMR (Node.js version 24.* is required) :

```shell
composer install --no-dev
npm install
npm run build
composer dump-autoload -o
```

### Contributors

This project exists thanks to all the people who have contributed. [[Contribute]](CONTRIBUTING.md).
<a href="https://github.com/openemr/openemr/graphs/contributors"><img src="https://opencollective.com/openemr/contributors.svg?width=890" /></a>


### Sponsors

Thanks to our [ONC Certification Major Sponsors](https://www.open-emr.org/wiki/index.php/OpenEMR_Certification_Stage_III_Meaningful_Use#Major_sponsors)!


### License

[GNU GPL](LICENSE)
