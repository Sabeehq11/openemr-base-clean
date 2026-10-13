#!/usr/bin/env bash
# Block until OpenEMR CPU is below 10%, so one run's backlog never leaks into the next run's numbers.
for _ in $(seq 1 40); do
  v=$(docker stats --no-stream --format '{{.CPUPerc}}' development-easy-light-openemr-1 | tr -d '%')
  awk "BEGIN{exit !($v < 10)}" && exit 0
  sleep 10
done
echo "OpenEMR did not go idle" >&2; exit 1
