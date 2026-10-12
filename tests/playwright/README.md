# Playwright browser tests

```bash
cd tests/playwright
npm install && npx playwright install chromium
npm test                                   # against http://localhost:8300
BASE_URL=https://your-deployed-url npm test   # smoke-check the deployment
```

Env: `OE_USER`/`OE_PASS` (default admin/pass). Patient specs expect the data from
`scripts/seed_demo_patients.py` (run by `scripts/setup-local.sh`).

Planned next (Early submission): chat tab opens, answers carry citations, unauthorized
patient is refused.
