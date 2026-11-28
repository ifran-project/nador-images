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
# DU list of OAI 2026.w46 and later: each cell with its PCI, ARFCN, SCS and PRBs
printf '%s\n' '[1] DU ID 3584 (gnb) integrated DU-CU: 1 cell' \
  '  [1] nrCellID 12345678, PCI 1, Mode TDD, SSB ARFCN 650016' \
  '      TDD: band 78 ARFCN 651000 SCS 30 (kHz) PRB 106' >> "$runtime_dir/nrRRC_stats.log"
# UE header of OAI 2026.w46 and later, with the PCI and cell ID of the UE (R2Lab, 21 November 2026), a second cell
# with a UE of the same RNTI (an RNTI is unique only within a cell), then the older header without them (OAI before
# 2026.w46)
printf '%s\n' '=== Cell 0 ===' \
  'UE RNTI e8a9 PCI 1 CellID 2 CU-UE-ID 1 in-sync PH 48 dB PCMAX 24 dBm, average RSRP -74 (32 meas)' \
  '=== Cell 1 ===' \
  'UE RNTI e8a9 PCI 3 CellID 4 CU-UE-ID 3 in-sync PH 30 dB PCMAX 22 dBm, average RSRP -90 (10 meas)' \
  > "$runtime_dir/nrMAC_stats.log"
printf 'UE RNTI 4601 CU-UE-ID 2 in-sync PH 40 dB PCMAX 23 dBm, average RSRP -80 (16 meas)\n' \
  >> "$runtime_dir/nrMAC_stats.log"
# OAI 2026.w42 dump_mac_stats: a negative "average SINR %d.%d" (-3.5 printed "-3.-5") and the CCE failures of the
# DLSCH and ULSCH lines, which are two counters
printf '%s\n' \
  'UE RNTI c0de CU-UE-ID 5 in-sync PH 20 dB PCMAX 23 dBm, average RSRP -95 (8 meas), average SINR -3.-5 (8 meas)' \
  'UE c0de: dlsch_rounds 100/2/0/0, dlsch_errors 0, pucch0_DTX 1 (SNR 20.0+1.5) RSSI -60.0, BLER 0.01000 MCS (1) 20 (Qm 6) CCE fail 7' \
  'UE c0de: ulsch_rounds 50/1/0/0, ulsch_errors 0, ulsch_DTX 0, BLER 0.00000 MCS (0) 9 (Qm 2 deltaMCS 0) NPRB 5 SNR 15.0 (+0.5) RSSI -70.0 CCE fail 3' \
  'UE c0de: LCID 1,2,4, goodput DL    0.00 UL    0.00 Mbps' \
  >> "$runtime_dir/nrMAC_stats.log"
# A power-limited UE (negative PH) and HARQ round counts other than 4 (dl_harq_round_max 1, ul_harq_round_max 6)
printf '%s\n' \
  'UE RNTI beef CU-UE-ID 6 in-sync PH -5 dB PCMAX 23 dBm, average RSRP -110 (4 meas)' \
  'UE beef: dlsch_rounds 40, dlsch_errors 1, pucch0_DTX 0 (SNR 5.0-2.0) RSSI -80.0, BLER 0.10000 MCS (1) 4 (Qm 2) CCE fail 0' \
  'UE beef: ulsch_rounds 30/2/1/0/0/1, ulsch_errors 1, ulsch_DTX 0, BLER 0.20000 MCS (0) 2 (Qm 2 deltaMCS 0) NPRB 3 SNR 2.0 (-1.0) RSSI -90.0 CCE fail 0' \
  >> "$runtime_dir/nrMAC_stats.log"
# OAI 2026.w42 dump_nr_I0_stats and dump_pusch_stats: 30 PRBs, PRBs 3 and 27 blacklisted (" X."), one "%2d." per PRB
# with no space before a negative value, and negative "%d.%d" / "%d,%d" values (-1.5 is "-1.-5", -0.5 is "0.-5")
printf '%s\n' 'Blacklisted PRBs 2/30' \
  '-2.-1. 0. X. 2.-2.-1. 0. 1. 2.-2.-1. 0. 1. 2.-2.-1. 0. 1. 2.-2.-1. 0. 1. 2.' \
  '-2.-1. X. 1. 2.' \
  'max_IO = 32 (4), min_I0 = 28 (0), avg_I0 = 30 dB(29.31.)' \
  'PRACH I0 = -1.-5 dB' \
  'ULSCH RNTI c0de, 512: ulsch_power[0] -3,-5 ulsch_noise_power[0] 0.-5, sync_pos 2' \
  '                  ulsch_power[1] -2.-1, ulsch_noise_power[1] 4.0' \
  > "$runtime_dir/nrL1_stats.log"
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
    'oai_gnb_rrc_arfcn{cell_id="",pci=""} 641280.0',
    'oai_gnb_rrc_scs_khz{cell_id="",pci=""} 30.0',
    'oai_gnb_rrc_arfcn{cell_id="12345678",pci="1"} 651000.0',
    'oai_gnb_rrc_ssb_arfcn{cell_id="12345678",pci="1"} 650016.0',
    'oai_gnb_rrc_prbs{cell_id="12345678",pci="1"} 106.0',
    'oai_gnb_cells 2.0',
    'oai_gnb_mac_ph{cell_id="2",pci="1",rnti="e8a9"} 48.0',
    'oai_gnb_mac_pcmax{cell_id="2",pci="1",rnti="e8a9"} 24.0',
    'oai_gnb_mac_avg_rsrp{cell_id="2",pci="1",rnti="e8a9"} -74.0',
    'oai_gnb_mac_ph{cell_id="4",pci="3",rnti="e8a9"} 30.0',
    'oai_gnb_mac_ph{cell_id="",pci="",rnti="4601"} 40.0',
    'oai_gnb_mac_pcmax{cell_id="",pci="",rnti="4601"} 23.0',
    'oai_gnb_mac_avg_rsrp{cell_id="",pci="",rnti="4601"} -80.0',
    'oai_gnb_mac_avg_sinr_db{cell_id="",pci="",rnti="c0de"} -3.5',
    'oai_gnb_mac_dl_cce_fail{cell_id="",pci="",rnti="c0de"} 7.0',
    'oai_gnb_mac_ul_cce_fail{cell_id="",pci="",rnti="c0de"} 3.0',
    'oai_gnb_mac_cce_fail{cell_id="",pci="",rnti="c0de"} 3.0',
    'oai_gnb_mac_dlsch_rounds{cell_id="",pci="",rnti="c0de",round="1"} 2.0',
    'oai_gnb_mac_dlsch_rounds_b{cell_id="",pci="",rnti="c0de"} 2.0',
    'oai_gnb_mac_ulsch_rounds_a{cell_id="",pci="",rnti="c0de"} 50.0',
    'oai_gnb_mac_ph{cell_id="",pci="",rnti="beef"} -5.0',
    'oai_gnb_mac_avg_rsrp{cell_id="",pci="",rnti="beef"} -110.0',
    'oai_gnb_mac_dlsch_rounds{cell_id="",pci="",rnti="beef",round="0"} 40.0',
    'oai_gnb_mac_dl_bler{cell_id="",pci="",rnti="beef"} 0.1',
    'oai_gnb_mac_dl_mcs{cell_id="",pci="",rnti="beef"} 4.0',
    'oai_gnb_mac_ulsch_rounds{cell_id="",pci="",rnti="beef",round="5"} 1.0',
    'oai_gnb_mac_ul_bler{cell_id="",pci="",rnti="beef"} 0.2',
    'oai_gnb_mac_ul_snr_delta_db{cell_id="",pci="",rnti="beef"} -1.0',
    'oai_gnb_mac_ul_cce_fail{cell_id="",pci="",rnti="beef"} 0.0',
    'oai_gnb_l1_blacklisted_prbs_total 2.0',
    'oai_gnb_l1_i0_noise_offset_db{prb="000",prb_index="0"} -2.0',
    'oai_gnb_l1_i0_noise_offset_db{prb="001",prb_index="1"} -1.0',
    'oai_gnb_l1_i0_noise_offset_db{prb="004",prb_index="4"} 2.0',
    'oai_gnb_l1_i0_noise_offset_db{prb="005",prb_index="5"} -2.0',
    'oai_gnb_l1_i0_noise_offset_db{prb="024",prb_index="24"} 2.0',
    'oai_gnb_l1_i0_noise_offset_db{prb="025",prb_index="25"} -2.0',
    'oai_gnb_l1_i0_noise_offset_db{prb="028",prb_index="28"} 1.0',
    'oai_gnb_l1_i0_noise_offset_db{prb="029",prb_index="29"} 2.0',
    'oai_gnb_l1_i0_avg_antenna_db{antenna="1"} 31.0',
    'oai_gnb_l1_prach_i0_db -1.5',
    'oai_gnb_l1_ulsch_power{rnti="c0de"} -3.5',
    'oai_gnb_l1_ulsch_noise_power{rnti="c0de"} -0.5',
    'oai_gnb_l1_ulsch_sync_pos{rnti="c0de"} 2.0',
    'oai_gnb_l1_ulsch_power_antenna{antenna="1",rnti="c0de"} -2.1',
    'oai_gnb_l1_ulsch_noise_power_antenna{antenna="1",rnti="c0de"} 4.0',
}
missing = expected - set(metrics.splitlines())
if missing:
    raise SystemExit(f"Missing or incorrect metrics: {sorted(missing)}")
# A blacklisted PRB has no I0 value; 28 PRBs have one
prbs = [line for line in metrics.splitlines() if line.startswith("oai_gnb_l1_i0_noise_offset_db{")]
unexpected = [line for line in prbs if 'prb="003"' in line or 'prb="027"' in line]
if unexpected or len(prbs) != 28:
    raise SystemExit(f"Wrong per-PRB I0 series ({len(prbs)}): {unexpected or prbs}")
# The negative-PH UE's lines are its own, not the previous UE's (c0de keeps its DLSCH MCS 20)
if 'oai_gnb_mac_dl_mcs{cell_id="",pci="",rnti="c0de"} 20.0' not in metrics.splitlines():
    raise SystemExit("The DLSCH line of UE beef was counted under UE c0de")
# The deprecated _a to _d names stop at round 3
if 'oai_gnb_mac_ulsch_rounds_e' in metrics:
    raise SystemExit("Unexpected oai_gnb_mac_ulsch_rounds_e")
PY
