# C5. Deploy, performance, defend

Status: not started. High-level only; expanded when we reach C5.

Everything named here that is new in Week 2 is **(planned)**.

## Goal
A publicly reachable deployment with the Week 2 core flow working, plus the evidence needed to defend it.

## Scope
- Deployment through `deploy/` with the agent service added; `/ready` green; browser test of the Week 2 flow against it.
- Click-to-source UI with a document preview and bounding-box overlay.
- Baselines for ingestion, extraction, retrieval and the full run, compared with Week 1 shared paths. Cost and latency report with p50, p95 and bottlenecks.
- Backup and recovery document with RPO and RTO, and a restore drill. Demo video.
- Stretch only if time remains: critic agent, third document type, lab trend chart.

## Acceptance (summary)
- A grader can run the Week 2 flow on the deployed app and from the API collection.
- Reports and the demo video exist.

## Depends on
C4.
