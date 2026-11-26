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
ARFCN, SCS) the cell of the DU list of `nrRRC_stats.log`; the ARFCN is the
downlink one (FDD). Before OAI 2026.w46 these labels are empty. The L1
metrics (`oai_gnb_l1_*`, from `nrL1_stats.log`) have no cell: OAI writes none
in that file, and every L1 instance of a gNB writes the same file, so they are
right only with one cell per gNB or DU.

The upstream parser files are redistributed with permission; this import does
not change their licensing terms.
