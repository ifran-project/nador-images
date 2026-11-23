#!/bin/sh
# join.awk on recorded inputs: each cases/<name>/ holds ue_names, core.log, inventory.json, node_ips, the RAN
# files (<component>@<oai|ocudu>.txt) and "expected": the core type on its first line, then the ran_ue_info lines.
# Runs with any POSIX awk; CI runs it inside the image, with the busybox awk the mapper uses.
set -u
here=$(cd "$(dirname "$0")" && pwd)
join="${JOIN_AWK:-$here/../join.awk}"
failed=0
for case in "$here"/cases/*/; do
  name=$(basename "$case")
  core=$(head -n 1 "$case/expected")
  # shellcheck disable=SC2046 # RAN file names have no spaces
  awk -v core="$core" -f "$join" "$case/ue_names" "$case/core.log" "$case/inventory.json" "$case/node_ips" \
    $(ls "$case" | grep '@' | sed "s|^|$case|") | grep -v '^#' | sort > "/tmp/rnti-mapper-$name.out"
  if tail -n +2 "$case/expected" | sort | cmp -s - "/tmp/rnti-mapper-$name.out"; then
    echo "ok   $name"
  else
    echo "FAIL $name"; tail -n +2 "$case/expected" | sort | diff - "/tmp/rnti-mapper-$name.out"; failed=1
  fi
done
exit "$failed"
