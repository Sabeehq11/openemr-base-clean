# fail-llm-unavailable

**data_source: stub**  model: stub  users: 10  duration: 91.5 s  requests: 402

| metric | value |
|---|---|
| p50 ms | 437.9 |
| p95 ms | 583.9 |
| p99 ms | 900.4 |
| time to first event p50/p95 ms | 3.8/8.9 |
| requests/s | 4.39 |
| error rate (unexpected outcomes) | 0.9652 |
| unexpected | {'UC-1->degraded': 208, 'UC-2->degraded': 80, 'UC-3->degraded': 67, 'UC-4->degraded': 33} |
| outcomes | {'degraded': 388, 'declined': 5, 'denied': 9} |
| spend usd (real only) | None |

docker stats: {"agent-load": {"cpu_avg_pct": 6.1, "cpu_max_pct": 15.98, "mem_avg_mib": 48.6, "mem_max_mib": 49.2, "samples": 21}, "development-easy-light-openemr-1": {"cpu_avg_pct": 131.3, "cpu_max_pct": 477.45, "mem_avg_mib": 579.5, "mem_max_mib": 594.5, "samples": 21}, "development-easy-light-mysql-1": {"cpu_avg_pct": 12.9, "cpu_max_pct": 38.91, "mem_avg_mib": 673.1, "mem_max_mib": 675.4, "samples": 21}}

| span | n | p50 ms | p95 ms | errors |
|---|---|---|---|---|
| authz:patient_access | 402 | 430.33 | 574.32 | 9 |
| llm:complete | 388 | 0.0 | 0.01 | 388 |
