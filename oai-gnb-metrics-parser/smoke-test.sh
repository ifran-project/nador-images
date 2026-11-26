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
# UE header of OAI 2026.w46 and later, with the PCI and cell ID of the UE (R2Lab, 21 November 2026), then the older
# header without them (OAI before 2026.w46)
printf 'UE RNTI e8a9 PCI 1 CellID 2 CU-UE-ID 1 in-sync PH 48 dB PCMAX 24 dBm, average RSRP -74 (32 meas)\n' \
  > "$runtime_dir/nrMAC_stats.log"
printf 'UE RNTI 4601 CU-UE-ID 2 in-sync PH 40 dB PCMAX 23 dBm, average RSRP -80 (16 meas)\n' \
  >> "$runtime_dir/nrMAC_stats.log"
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

expected = {
    "oai_gnb_rrc_arfcn 641280.0",
    "oai_gnb_rrc_scs_khz 30.0",
    'oai_gnb_mac_ph{rnti="e8a9"} 48.0',
    'oai_gnb_mac_pcmax{rnti="e8a9"} 24.0',
    'oai_gnb_mac_avg_rsrp{rnti="e8a9"} -74.0',
    'oai_gnb_mac_ph{rnti="4601"} 40.0',
    'oai_gnb_mac_pcmax{rnti="4601"} 23.0',
    'oai_gnb_mac_avg_rsrp{rnti="4601"} -80.0',
}
missing = expected - set(metrics.splitlines())
if missing:
    raise SystemExit(f"Missing or incorrect metrics: {sorted(missing)}")
PY
