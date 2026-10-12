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
| [AUDIT.md](AUDIT.md) | Security, performance, architecture, data quality and compliance audit |
| [USERS.md](USERS.md) | Target user, workflow and use cases (UC-1 to UC-4) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Agent integration plan, verification strategy, tradeoffs |

**Deployed app:** _TBD (see [deploy/](deploy/))_ (demo data only, no real PHI)

## Architecture overview

A thin OpenEMR module (`oe-module-copilot`) mounts a chat dock in the Tabs shell and forwards signed, patient-scoped requests to a separate Python agent service. The agent reads OpenEMR only over FHIR/REST with the physician's own OAuth2 token, through six typed read-only tools. Every answer passes a verification layer (source attribution plus deterministic clinical rules) before display. The agent writes its own append-only audit log and self-hosted Langfuse traces, each tied to a correlation ID. Details, trust boundaries and tradeoffs: [ARCHITECTURE.md](ARCHITECTURE.md). Status: the agent is planned, not built. The MVP covers the audit, users and plan.

## Run locally

Requires Docker (about 8 GB RAM). First boot takes about 5 minutes.

```bash
./scripts/setup-local.sh      # starts OpenEMR; seeds 13 synthetic patients only if missing (safe to re-run)
./scripts/setup-local.sh --reseed       # recreate the synthetic patients (other data untouched)
./scripts/setup-local.sh --reset-demo   # DESTRUCTIVE: wipe DB, reload stock demo data, then reseed
```

On a first run against a fresh stack, use `--reset-demo` once to load OpenEMR's stock demo patients (pid 1-3).

- App: http://localhost:8300 (login `admin` / `pass`), phpMyAdmin: http://localhost:8310
- The stack is `docker/development-easy-light` (OpenEMR flex image + MariaDB). Dev credentials are defaults, for local use only.
- `scripts/seed_demo_patients.py` is deterministic. It adds meds, labs, vitals and encounters, plus deliberate data-quality defects (missing DOB, a duplicate patient, stale labs) used by the audit.

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
