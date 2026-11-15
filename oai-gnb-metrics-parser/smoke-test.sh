#!/usr/bin/env bash
set -euo pipefail

runtime_dir=$(mktemp -d)
container=
cleanup() {
  if [[ -n "$container" ]]; then
    docker rm -f "$container" >/dev/null
  fi
  rm -rf "$runtime_dir"
}
trap cleanup EXIT

printf 'SSB ARFCN 640000\nARFCN 641280 SCS 30 (kHz)\n' > "$runtime_dir/nrRRC_stats.log"
container=$(docker run -d --network none --platform linux/amd64 \
  --mount "type=bind,source=$runtime_dir,target=/oai-gnb-logs,readonly" "$1")
docker exec -i "$container" python3 - <<'PY'
import time
from urllib.error import URLError
from urllib.request import urlopen

for attempt in range(20):
    try:
        with urlopen("http://127.0.0.1:9090/metrics", timeout=1) as response:
            metrics = response.read().decode()
        break
    except URLError:
        if attempt == 19:
            raise
        time.sleep(0.5)

expected = {"oai_gnb_rrc_arfcn 641280.0", "oai_gnb_rrc_scs_khz 30.0"}
missing = expected - set(metrics.splitlines())
if missing:
    raise SystemExit(f"Missing or incorrect metrics: {sorted(missing)}")
PY
