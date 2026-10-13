# fail-openemr-slow-1cpu

**data_source: stub**  model: stub  users: 10  duration: 147.2 s  requests: 98

| metric | value |
|---|---|
| p50 ms | 6100.7 |
| p95 ms | 36738.4 |
| p99 ms | 39490.3 |
| time to first event p50/p95 ms | 5.9/73.7 |
| requests/s | 0.67 |
| error rate (unexpected outcomes) | 0.8469 |
| unexpected | {'UC-2->denied': 19, 'UC-1->denied': 43, 'declined->denied': 2, 'UC-3->denied': 17, 'UC-4->denied': 2} |
| outcomes | {'denied': 84, 'answer': 14} |
| spend usd (real only) | None |

docker stats: {"agent-load": {"cpu_avg_pct": 2.8, "cpu_max_pct": 5.06, "mem_avg_mib": 48.0, "mem_max_mib": 49.1, "samples": 33}, "development-easy-light-openemr-1": {"cpu_avg_pct": 97.7, "cpu_max_pct": 107.67, "mem_avg_mib": 809.7, "mem_max_mib": 873.9, "samples": 33}, "development-easy-light-mysql-1": {"cpu_avg_pct": 10.4, "cpu_max_pct": 19.86, "mem_avg_mib": 676.5, "mem_max_mib": 677.9, "samples": 33}}

| span | n | p50 ms | p95 ms | errors |
|---|---|---|---|---|
| authz:patient_access | 98 | 4862.86 | 6094.04 | 51 |
| llm:complete | 61 | 0.03 | 0.13 | 0 |
| tool:get_allergies | 16 | 2402.25 | 3797.95 | 0 |
| tool:get_labs | 13 | 2387.93 | 3807.27 | 0 |
| tool:get_medications | 23 | 3182.69 | 4105.02 | 0 |
| tool:get_problems | 28 | 2809.56 | 3688.04 | 0 |
| verifier:verify | 14 | 0.56 | 1.79 | 0 |
