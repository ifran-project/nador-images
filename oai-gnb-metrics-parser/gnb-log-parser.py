from prometheus_client import Gauge, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST
from http.server import BaseHTTPRequestHandler, HTTPServer
import re
import os

registry = CollectorRegistry()
metric_defs = {}

log_paths = {
    "mac": "/oai-gnb-logs/nrMAC_stats.log",
    "l1": "/oai-gnb-logs/nrL1_stats.log",
    "rrc": "/oai-gnb-logs/nrRRC_stats.log",
}

# Labels of a UE's MAC metrics: an RNTI is unique only within a cell, so its cell (PCI, NR cell ID) is part of the
# identity. OAI 2026.w46 writes them in the UE header; with older OAI they are empty (absent in Prometheus).
UE_LABELS = ["rnti", "pci", "cell_id"]
# Labels of the cell-level RRC metrics (one per cell of the DU list of nrRRC_stats.log, empty before OAI 2026.w46)
CELL_LABELS = ["pci", "cell_id"]

# Cells of this pod at the last parse: more than one makes the L1 metrics unattributable (see warn_cells)
warned_cells = 0

def warn_cells(cells):
    """Print once, at each change, that the L1 metrics of a pod with several cells cannot be attributed."""
    global warned_cells
    if cells > 1 and cells != warned_cells:
        print(f"[WARNING] {cells} cells in this gNB/DU: the L1 metrics (oai_gnb_l1_*) cannot be attributed to a cell "
              "(nrL1_stats.log names none and every L1 instance rewrites it); use them only with one cell per pod")
    warned_cells = cells

# A value OAI prints as "%d.%d" (or "%d,%d") of x / 10 and x % 10: C truncates both toward zero, so -3.5 is written
# "-3.-5" and -0.5 "0.-5". TENTHS matches it (two groups) and tenths() gives the value back.
TENTHS = r"(-?\d+)[.,](-?\d)"

def tenths(whole, frac):
    """The value of an OAI "%d.%d" pair of x / 10 and x % 10 (either part carries the sign of x)."""
    value = abs(int(whole)) * 10 + abs(int(frac))
    return -value / 10 if whole.startswith("-") or frac.startswith("-") else value / 10

def get_or_create_metric(metric_name, description, labelnames):
    if metric_name not in metric_defs:
        metric_defs[metric_name] = Gauge(metric_name, description, labelnames=labelnames, registry=registry)
    return metric_defs[metric_name]

def parse_logs():
    global registry, metric_defs
    # Reset registry and metrics to avoid exporting stale values
    registry = CollectorRegistry()
    metric_defs = {}

    # L1 stats
    try:
        if os.path.isfile(log_paths["l1"]):
            prb_noise_values = {}  # PRB index -> I0 deviation (dB); blacklisted PRBs (" X.") have none
            prb = 0
            collecting_matrix = False
            current_ul_rnti = None  # per-antenna ULSCH lines (4-RX RUs) follow their header line

            with open(log_paths["l1"], "r") as f:
                for line in f:
                    if match := re.search(r"Blacklisted PRBs (\d+)/(\d+)", line):
                        collecting_matrix = True
                        prb_noise_values = {}
                        prb = 0
                        blacklisted = int(match.group(1))
                        total = int(match.group(2))
                        get_or_create_metric("oai_gnb_l1_total_prbs", "Total number of PRBs", []).set(total)
                        get_or_create_metric("oai_gnb_l1_blacklisted_prbs_total", "Number of blacklisted PRBs", []).set(blacklisted)
                        get_or_create_metric("oai_gnb_l1_blacklisted_prbs_ratio", "Ratio of blacklisted PRBs", []).set(blacklisted / total if total > 0 else 0)
                    elif match := re.search(r"DLSCH RNTI (\w+): current_Qm (\d+), current_RI (\d+), total_bytes TX (\d+)", line):
                        get_or_create_metric("oai_gnb_l1_dlsch_current_qm", "Current DLSCH Modulation Order", ["rnti"]).labels(rnti=match.group(1)).set(int(match.group(2)))
                        get_or_create_metric("oai_gnb_l1_dlsch_current_ri", "Current DLSCH Rank Indicator", ["rnti"]).labels(rnti=match.group(1)).set(int(match.group(3)))
                        get_or_create_metric("oai_gnb_l1_dlsch_tx_bytes", "DLSCH TX Bytes", ["rnti"]).labels(rnti=match.group(1)).set(int(match.group(4)))
                    elif match := re.search(rf"ULSCH RNTI\s+(\w+), \d+: .*?ulsch_power\[0\] {TENTHS} ulsch_noise_power\[0\] {TENTHS}, sync_pos (-?\d+)", line):
                        # "ulsch_power[0] %d,%d ulsch_noise_power[0] %d.%d" (OAI dump_pusch_stats), e.g. "-3,-5" = -3.5
                        rnti = match.group(1)
                        current_ul_rnti = rnti
                        power = tenths(match.group(2), match.group(3))
                        noise = tenths(match.group(4), match.group(5))
                        sync_pos = int(match.group(6))
                        get_or_create_metric("oai_gnb_l1_ulsch_power", "ULSCH Power", ["rnti"]).labels(rnti=rnti).set(power)
                        get_or_create_metric("oai_gnb_l1_ulsch_noise_power", "ULSCH Noise Power", ["rnti"]).labels(rnti=rnti).set(noise)
                        get_or_create_metric("oai_gnb_l1_ulsch_sync_pos", "ULSCH Sync Position", ["rnti"]).labels(rnti=rnti).set(sync_pos)
                        get_or_create_metric("oai_gnb_l1_ulsch_power_antenna", "ULSCH Power per RX antenna", ["rnti", "antenna"]).labels(rnti=rnti, antenna="0").set(power)
                        get_or_create_metric("oai_gnb_l1_ulsch_noise_power_antenna", "ULSCH Noise Power per RX antenna", ["rnti", "antenna"]).labels(rnti=rnti, antenna="0").set(noise)
                    elif current_ul_rnti and (match := re.search(rf"^\s*ulsch_power\[(\d+)\] {TENTHS}, ulsch_noise_power\[\d+\] {TENTHS}", line)):
                        # Continuation line for RX antenna >= 1, e.g. "ulsch_power[1] 54.8, ulsch_noise_power[1] 52.9"
                        get_or_create_metric("oai_gnb_l1_ulsch_power_antenna", "ULSCH Power per RX antenna", ["rnti", "antenna"]).labels(rnti=current_ul_rnti, antenna=match.group(1)).set(tenths(match.group(2), match.group(3)))
                        get_or_create_metric("oai_gnb_l1_ulsch_noise_power_antenna", "ULSCH Noise Power per RX antenna", ["rnti", "antenna"]).labels(rnti=current_ul_rnti, antenna=match.group(1)).set(tenths(match.group(4), match.group(5)))
                    elif match := re.search(r"max_IO = (-?\d+) \((\d+)\), min_I0 = (-?\d+) \((\d+)\), avg_I0 = (-?\d+)", line):
                        collecting_matrix = False
                        if ant := re.search(r"avg_I0 = -?\d+ dB\(([-\d.]+)\)", line):
                            for aa, v in enumerate(re.findall(r"-?\d+", ant.group(1))):
                                get_or_create_metric("oai_gnb_l1_i0_avg_antenna_db", "Average I0 per RX antenna (dB)", ["antenna"]).labels(antenna=str(aa)).set(int(v))
                        get_or_create_metric("oai_gnb_l1_i0_max_db", "Max subband I0 (dB)", []).set(int(match.group(1)))
                        get_or_create_metric("oai_gnb_l1_i0_min_db", "Min subband I0 (dB)", []).set(int(match.group(3)))
                        get_or_create_metric("oai_gnb_l1_i0_avg_db", "Average I0 across subbands (dB)", []).set(int(match.group(5)))
                    elif match := re.search(rf"PRACH I0 = {TENTHS} dB", line):
                        prach_i0 = tenths(match.group(1), match.group(2))
                        get_or_create_metric("oai_gnb_l1_prach_i0_db", "PRACH I0 value (dB)", []).set(prach_i0)
                    elif collecting_matrix:
                        # One "%2d." per PRB, " X." for a blacklisted one, 25 per line and not always space-separated
                        # (" 2.-1. X."): each token is one PRB, so a blacklisted PRB keeps the indices of the next ones
                        for token in re.findall(r"(-?\d+|X)\.", line):
                            if token != "X":
                                prb_noise_values[prb] = int(token)
                            prb += 1

            for i, val in prb_noise_values.items():
                get_or_create_metric("oai_gnb_l1_i0_noise_offset_db", "PRB I0 noise deviation from average in dB", ["prb", "prb_index"]).labels(prb=str(i).zfill(3), prb_index=str(i)).set(val)

    except Exception as e:
        print(f"[ERROR] L1 parsing failed: {e}")

    # MAC stats
    try:
        if os.path.isfile(log_paths["mac"]):
            with open(log_paths["mac"], "r") as f:
                lines = f.readlines()
                current_rnti = None
                current_cu_ue_id = None
                current_ue = {}
                # One "=== Cell <i> ===" section per cell since OAI 2026.w46 (none before)
                cells = sum(1 for line in lines if line.startswith("=== Cell "))
                if cells:
                    get_or_create_metric("oai_gnb_cells", "Cells of this gNB or DU (more than 1: the L1 metrics are not per cell)", []).set(cells)
                warn_cells(cells)
                for line in lines:
                    if match := re.search(r"CU-UE-ID \(none\)", line):
                        current_rnti = None
                        current_cu_ue_id = None
                        continue  # skip this UE completely

                    if match := re.search(r"UE RNTI (\w+)(?: PCI (\d+) CellID (\d+))? CU-UE-ID (\d+).*?PH (-?\d+) dB.*?PCMAX (-?\d+) dBm(?:.*?average RSRP (-?\d+))?", line):
                        current_rnti = match.group(1)
                        current_cu_ue_id = match.group(4)
                        current_ue = {"rnti": current_rnti, "pci": match.group(2) or "", "cell_id": match.group(3) or ""}
                        get_or_create_metric("oai_gnb_mac_ph", "Power Headroom", UE_LABELS).labels(**current_ue).set(int(match.group(5)))
                        get_or_create_metric("oai_gnb_mac_pcmax", "PCMAX", UE_LABELS).labels(**current_ue).set(int(match.group(6)))
                        if match.group(7) is not None:
                            get_or_create_metric("oai_gnb_mac_avg_rsrp", "Average RSRP", UE_LABELS).labels(**current_ue).set(int(match.group(7)))
                        # "average SINR %d.%d" of OAI 2026.w42 and later (x10 / 10, x10 % 10), after the RSRP
                        if sinr := re.search(rf"average SINR {TENTHS} \(", line):
                            get_or_create_metric("oai_gnb_mac_avg_sinr_db", "Average SINR (dB)", UE_LABELS).labels(**current_ue).set(tenths(sinr.group(1), sinr.group(2)))
                    elif line.startswith("UE RNTI "):
                        # A UE header this parser cannot read: skip that UE rather than count its lines under the previous one
                        print(f"[WARNING] Unparsed MAC UE header, UE skipped: {line.strip()}")
                        current_rnti = None
                        current_cu_ue_id = None

                    if not current_rnti:
                        continue

                    if match := re.search(r"UE .*?CQI (\d+),? RI (\d+),? PMI\s*\(?([^,)\s\]]+(?:,[^,)\s\]]+)*)\)?", line):
                        get_or_create_metric("oai_gnb_mac_cqi", "CQI", UE_LABELS).labels(**current_ue).set(int(match.group(1)))
                        get_or_create_metric("oai_gnb_mac_ri", "Rank Indicator", UE_LABELS).labels(**current_ue).set(int(match.group(2)))

                        pmi_raw = match.group(3).replace(" ", "")  # normalize spacing
                        pmi_parts = pmi_raw.split(",")

                        for i, v in enumerate(pmi_parts):
                            get_or_create_metric("oai_gnb_mac_pmi", "PMI component", [*UE_LABELS, "index"]).labels(**current_ue, index=str(i)).set(int(v))

                    if match := re.search(r"UE .*? dlsch_rounds (\d+(?:/\d+)*), dlsch_errors (\d+), pucch0_DTX (\d+)(?: \(SNR ([+-]?\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?: dB)?\))?(?: RSSI -?\d+(?:\.\d+)?)?, BLER ([\d.]+) MCS \((\d+)\) (\d+)(?: \(Qm \d+\))? CCE fail (\d+)(?:, goodput ([\d.]+) Mbps)?", line):
                        # One count per HARQ round: OAI prints dl_harq_round_max of them (configurable, 4 by default)
                        for rnd, count in enumerate(match.group(1).split("/")):
                            get_or_create_metric("oai_gnb_mac_dlsch_rounds", "DLSCH transmissions per HARQ round", [*UE_LABELS, "round"]).labels(**current_ue, round=str(rnd)).set(int(count))
                            # Deprecated: rounds 0 to 3 as _a to _d, use oai_gnb_mac_dlsch_rounds{round=...}
                            if rnd < 4:
                                get_or_create_metric(f"oai_gnb_mac_dlsch_rounds_{'abcd'[rnd]}", f"Deprecated: DLSCH HARQ round {rnd}, use oai_gnb_mac_dlsch_rounds", UE_LABELS).labels(**current_ue).set(int(count))
                        get_or_create_metric("oai_gnb_mac_dlsch_errors", "DLSCH Errors", UE_LABELS).labels(**current_ue).set(int(match.group(2)))
                        get_or_create_metric("oai_gnb_mac_pucch0_dtx", "PUCCH0 DTX", UE_LABELS).labels(**current_ue).set(int(match.group(3)))

                        if match.group(4) is not None:
                            get_or_create_metric("oai_gnb_mac_pucch_snr_avg_db", "Average PUCCH SNR", UE_LABELS).labels(**current_ue).set(float(match.group(4)))
                            get_or_create_metric("oai_gnb_mac_pucch_snr_delta_db", "Difference between measured and target PUCCH SNR", UE_LABELS).labels(**current_ue).set(float(match.group(5)))

                        get_or_create_metric("oai_gnb_mac_dl_bler", "DLSCH BLER", UE_LABELS).labels(**current_ue).set(float(match.group(6)))
                        get_or_create_metric("oai_gnb_mac_dl_mcs", "DLSCH MCS", UE_LABELS).labels(**current_ue).set(int(match.group(8)))
                        get_or_create_metric("oai_gnb_mac_dl_cce_fail", "DLSCH CCE allocation failures", UE_LABELS).labels(**current_ue).set(int(match.group(9)))
                        if match.group(10) is not None:
                            get_or_create_metric("oai_gnb_mac_dl_goodput_mbps","DL goodput Mbps",UE_LABELS).labels(**current_ue).set(float(match.group(10)))
                        if rssi := re.search(r"\) RSSI (-?\d+(?:\.\d+)?), BLER", line):
                            get_or_create_metric("oai_gnb_mac_pucch_rssi", "PUCCH RSSI", UE_LABELS).labels(**current_ue).set(float(rssi.group(1)))

                    if match := re.search(r"UE .*?ulsch_rounds (\d+(?:/\d+)*), ulsch_errors (\d+), ulsch_DTX (\d+), BLER ([\d.]+) MCS \((\d+)\) (\d+) \(Qm (\d+) deltaMCS ([+-]?\d+(?:\.\d+)?)(?: dB)?\) NPRB (\d+) SNR ([+-]?\d+(?:\.\d+)?) \(([+-]?\d+(?:\.\d+)?)\)(?: dB)?(?: RSSI -?\d+(?:\.\d+)?)? CCE fail (\d+)(?:, goodput ([\d.]+) Mbps)?", line):
                        # One count per HARQ round: OAI prints ul_harq_round_max of them (configurable, 4 by default)
                        for rnd, count in enumerate(match.group(1).split("/")):
                            get_or_create_metric("oai_gnb_mac_ulsch_rounds", "ULSCH transmissions per HARQ round", [*UE_LABELS, "round"]).labels(**current_ue, round=str(rnd)).set(int(count))
                            # Deprecated: rounds 0 to 3 as _a to _d, use oai_gnb_mac_ulsch_rounds{round=...}
                            if rnd < 4:
                                get_or_create_metric(f"oai_gnb_mac_ulsch_rounds_{'abcd'[rnd]}", f"Deprecated: ULSCH HARQ round {rnd}, use oai_gnb_mac_ulsch_rounds", UE_LABELS).labels(**current_ue).set(int(count))
                        get_or_create_metric("oai_gnb_mac_ulsch_errors", "ULSCH Errors", UE_LABELS).labels(**current_ue).set(int(match.group(2)))
                        get_or_create_metric("oai_gnb_mac_ulsch_dtx", "ULSCH DTX", UE_LABELS).labels(**current_ue).set(int(match.group(3)))
                        get_or_create_metric("oai_gnb_mac_ul_bler", "ULSCH BLER", UE_LABELS).labels(**current_ue).set(float(match.group(4)))
                        get_or_create_metric("oai_gnb_mac_ul_mcs_index", "ULSCH MCS index in parentheses", UE_LABELS).labels(**current_ue).set(int(match.group(5)))
                        get_or_create_metric("oai_gnb_mac_ul_mcs", "ULSCH MCS value", UE_LABELS).labels(**current_ue).set(int(match.group(6)))
                        get_or_create_metric("oai_gnb_mac_qm", "Modulation Order Qm", UE_LABELS).labels(**current_ue).set(int(match.group(7)))
                        get_or_create_metric("oai_gnb_mac_delta_mcs", "Delta MCS dB", UE_LABELS).labels(**current_ue).set(float(match.group(8)))
                        get_or_create_metric("oai_gnb_mac_nprb", "Allocated PRBs", UE_LABELS).labels(**current_ue).set(int(match.group(9)))
                        get_or_create_metric("oai_gnb_mac_ul_snr_avg_db", "Average ULSCH SNR", UE_LABELS).labels(**current_ue).set(float(match.group(10)))
                        get_or_create_metric("oai_gnb_mac_ul_snr_delta_db", "Difference between measured and target ULSCH SNR", UE_LABELS).labels(**current_ue).set(float(match.group(11)))
                        get_or_create_metric("oai_gnb_mac_ul_cce_fail", "ULSCH CCE allocation failures", UE_LABELS).labels(**current_ue).set(int(match.group(12)))
                        # Deprecated: the UL value, which it always ended up holding (the ULSCH line overwrote the DLSCH
                        # one); use oai_gnb_mac_dl_cce_fail and oai_gnb_mac_ul_cce_fail
                        get_or_create_metric("oai_gnb_mac_cce_fail", "Deprecated: ULSCH CCE failures, use oai_gnb_mac_ul_cce_fail", UE_LABELS).labels(**current_ue).set(int(match.group(12)))
                        if match.group(13) is not None:
                            get_or_create_metric("oai_gnb_mac_ul_goodput_mbps", "UL Goodput", UE_LABELS).labels(**current_ue).set(float(match.group(13)))
                        if rssi := re.search(r"RSSI (-?\d+(?:\.\d+)?) CCE fail", line):
                            get_or_create_metric("oai_gnb_mac_ul_rssi", "ULSCH RSSI", UE_LABELS).labels(**current_ue).set(float(rssi.group(1)))

                    # Goodput on its own line: "UE 218f: LCID 1,2,4, goodput DL    0.00 UL    0.00 Mbps"
                    if match := re.search(r"goodput DL\s+([\d.]+)\s+UL\s+([\d.]+)\s+Mbps", line):
                        get_or_create_metric("oai_gnb_mac_dl_goodput_mbps","DL goodput Mbps",UE_LABELS).labels(**current_ue).set(float(match.group(1)))
                        get_or_create_metric("oai_gnb_mac_ul_goodput_mbps", "UL Goodput", UE_LABELS).labels(**current_ue).set(float(match.group(2)))
                        # Active logical channels listed before goodput, e.g. "LCID 1,2,4,"
                        # (LCID 4 = first DRB, used to count UEs with an established data bearer)
                        if lcids := re.search(r"LCID ([\d,]+), goodput", line):
                            for lcid in filter(None, lcids.group(1).split(",")):
                                get_or_create_metric("oai_gnb_mac_lcid_active", "Logical channel present for this UE (1 = listed in MAC stats)", [*UE_LABELS, "lcid"]).labels(**current_ue, lcid=lcid).set(1)

                    if match := re.search(r"LCID (\d+): TX\s+(\d+)\s+RX\s+(\d+)", line):
                        get_or_create_metric("oai_gnb_mac_lcid_tx_bytes", "LCID TX Bytes", [*UE_LABELS, "lcid"]).labels(**current_ue, lcid=match.group(1)).set(int(match.group(2)))
                        get_or_create_metric("oai_gnb_mac_lcid_rx_bytes", "LCID RX Bytes", [*UE_LABELS, "lcid"]).labels(**current_ue, lcid=match.group(1)).set(int(match.group(3)))
                        get_or_create_metric("oai_gnb_mac_lcid_active", "Logical channel present for this UE (1 = listed in MAC stats)", [*UE_LABELS, "lcid"]).labels(**current_ue, lcid=match.group(1)).set(1)
    except Exception as e:
        print(f"[ERROR] MAC parsing failed: {e}")

    # RRC stats
    try:
        if os.path.isfile(log_paths["rrc"]):
            with open(log_paths["rrc"], "r") as f:
                lines = f.readlines()

                # Parse cell-level parameters after UE blocks. Since OAI 2026.w46 the DU list gives each cell:
                #   "  [1] nrCellID 12345678, PCI 1, Mode TDD, SSB ARFCN 640000"
                #   "      TDD: band 78 ARFCN 641280 SCS 30 (kHz) PRB 106"   (FDD: a "DL:" and a "UL:" line)
                # and the values that follow a cell line belong to it.
                cell = {"pci": "", "cell_id": ""}
                for line in lines:
                    if match := re.search(r"nrCellID (\d+), PCI (\d+)", line):
                        cell = {"pci": match.group(2), "cell_id": match.group(1)}
                    # SSB ARFCN
                    if match := re.search(r"SSB ARFCN (\d+)", line):
                        get_or_create_metric("oai_gnb_rrc_ssb_arfcn", "SSB ARFCN", CELL_LABELS).labels(**cell).set(int(match.group(1)))
                    # ARFCN and SCS of the downlink (TDD: the only carrier); the FDD uplink line is not the cell's ARFCN
                    if line.lstrip().startswith("UL:"):
                        continue
                    if match := re.search(r"ARFCN (\d+) SCS (\d+) \(kHz\)", line):
                        get_or_create_metric("oai_gnb_rrc_arfcn", "ARFCN", CELL_LABELS).labels(**cell).set(int(match.group(1)))
                        get_or_create_metric("oai_gnb_rrc_scs_khz", "Subcarrier Spacing (kHz)", CELL_LABELS).labels(**cell).set(int(match.group(2)))
                        # PRBs of the cell (OAI 2026.w46 and later): the denominator of a UE's PRB saturation per cell
                        if prbs := re.search(r"\(kHz\) PRB (\d+)", line):
                            get_or_create_metric("oai_gnb_rrc_prbs", "PRBs of the cell (downlink)", CELL_LABELS).labels(**cell).set(int(prbs.group(1)))
    except Exception as e:
        print(f"[ERROR] RRC parsing failed: {e}")

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            parse_logs()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.end_headers()
            self.wfile.write(generate_latest(registry))
        else:
            self.send_response(404)
            self.end_headers()

def start_server(port=9090):
    print(f"Starting Prometheus exporter on port {port}")
    server = HTTPServer(('0.0.0.0', port), MetricsHandler)
    server.serve_forever()

if __name__ == '__main__':
    start_server()
