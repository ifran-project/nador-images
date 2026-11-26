# OAI gNB metrics parser

Builds the `2026.w46-1` parser as
`ghcr.io/ifran-project/oai-gnb-metrics-parser:2026.w46-1` (`2026.w46` with the
cell labels below; `2026.w46` and `2026.w38` stay published). Mount the OAI gNB
log directory at `/oai-gnb-logs`; the parser exposes Prometheus metrics at
`http://<host>:9090/metrics`.

`2026.w46` reads the UE header of OAI 2026.w46 and later
(`UE RNTI <rnti> PCI <pci> CellID <cell> CU-UE-ID <id> ...`) as well as the
older one without PCI and cell ID: without it, the per-UE MAC metrics
(power headroom, PCMAX, RSRP and the lines that follow the header) are lost.

An RNTI is unique only within a cell, so the per-UE MAC metrics carry the cell
of the UE header (`pci`, `cell_id`), and the cell-level RRC metrics (SSB ARFCN,
ARFCN, SCS, PRBs) the cell of the DU list of `nrRRC_stats.log`; the ARFCN and
PRBs are the downlink ones (FDD). Before OAI 2026.w46 these labels are empty.

Several gNBs, CUs or DUs run in several pods, each with its own parser: their
metrics are told apart by their pod, whatever the number of cells. The one
case that is not supported is **one OAI gNB or DU pod serving several cells**,
and only for the L1 metrics (`oai_gnb_l1_*`, from `nrL1_stats.log`): OAI writes
no cell in that file, and every L1 instance of the pod rewrites the same file
every second, so the values are those of whichever cell wrote last. The PRB
saturation of the KPI calculator uses the PRBs of the UE's cell
(`oai_gnb_rrc_prbs`) when the pod has them (a gNB), else the L1 total.

The upstream parser files are redistributed with permission; this import does
not change their licensing terms.
