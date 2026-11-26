# OAI gNB metrics parser

The OAI gNB metrics parser, built, tested and published like the other images
(`images.json`, `images.yml`): `ghcr.io/ifran-project/oai-gnb-metrics-parser`,
tagged with its version and with `sha-<commit>`, and pinned in NADOR's
`images.json` (`oai_gnb_metrics_parser`) by the update pull requests. Earlier
releases, built by a workflow of their own, are tagged with the OAI release
they read (`2026.w38`, `2026.w46`) and stay published. Mount the OAI gNB log
directory at `/oai-gnb-logs`; the parser exposes Prometheus metrics at
`http://<host>:9090/metrics`.

It reads the UE header of OAI 2026.w46 and later
(`UE RNTI <rnti> PCI <pci> CellID <cell> CU-UE-ID <id> ...`) as well as the
older one without PCI and cell ID.

## Several cells

An RNTI is unique only within a cell, so the per-UE MAC metrics carry the cell
of the UE header (`pci`, `cell_id`), and the cell-level RRC metrics (SSB ARFCN,
ARFCN, SCS, PRBs) the cell of the DU list of `nrRRC_stats.log`; the ARFCN and
PRBs are the downlink ones (FDD). Before OAI 2026.w46 these labels are empty.

Several gNBs, CUs or DUs run in several pods, each with its own parser: their
metrics are told apart by their pod. This is the usual deployment, and it is
fully supported.

> **Warning: one OAI gNB or DU pod serving several cells.** The L1 metrics
> (`oai_gnb_l1_*`, from `nrL1_stats.log`: I0 noise, PRACH I0, blacklisted PRBs,
> DLSCH, ULSCH) **cannot be attributed to a cell**: OAI writes no cell in that
> file, and every L1 instance of the pod rewrites it every second, so the values
> are those of whichever cell wrote last, and two UEs with one RNTI on two cells
> share one series. Do not use them for such a pod. The parser exports the
> number of cells of its pod (`oai_gnb_cells`) and logs a warning when there is
> more than one. The PRB saturation of the KPI calculator uses the PRBs of the
> UE's cell (`oai_gnb_rrc_prbs`) when the pod lists its cells (a gNB), else the
> L1 total of the pod.

The upstream parser files are redistributed with permission; this import does
not change their licensing terms.
