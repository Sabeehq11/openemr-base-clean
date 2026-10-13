# real-calibration

**data_source: real**  model: anthropic/claude-sonnet-4.5  users: 1  duration: 173.1 s  requests: 12

| metric | value |
|---|---|
| p50 ms | 13683.2 |
| p95 ms | 18664.8 |
| p99 ms | 18664.8 |
| time to first event p50/p95 ms | 2.6/12.8 |
| requests/s | 0.07 |
| error rate (unexpected outcomes) | 0.0 |
| unexpected | {} |
| outcomes | {'answer': 12} |
| spend usd (real only) | 0.340593 |

docker stats: {"agent-load": {"cpu_avg_pct": 1.3, "cpu_max_pct": 11.3, "mem_avg_mib": 60.6, "mem_max_mib": 61.5, "samples": 38}, "development-easy-light-openemr-1": {"cpu_avg_pct": 10.2, "cpu_max_pct": 79.37, "mem_avg_mib": 528.9, "mem_max_mib": 532.1, "samples": 38}, "development-easy-light-mysql-1": {"cpu_avg_pct": 1.6, "cpu_max_pct": 9.65, "mem_avg_mib": 547.0, "mem_max_mib": 547.6, "samples": 38}}

| span | n | p50 ms | p95 ms | errors |
|---|---|---|---|---|
| authz:patient_access | 12 | 292.38 | 901.05 | 0 |
| llm:complete | 30 | 3020.21 | 8735.37 | 0 |
| tool:get_allergies | 10 | 131.51 | 181.38 | 0 |
| tool:get_changes_since | 3 | 247.26 | 263.67 | 0 |
| tool:get_labs | 12 | 147.4 | 274.69 | 0 |
| tool:get_medications | 10 | 157.44 | 217.36 | 0 |
| tool:get_patient_summary | 10 | 149.38 | 187.04 | 0 |
| tool:get_problems | 10 | 150.12 | 163.28 | 0 |
| verifier:verify | 12 | 1.02 | 3.08 | 0 |
