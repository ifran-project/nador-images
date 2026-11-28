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

## Values read from OAI's stats files

Checked against the stats writers of OAI 2026.w42 (`dump_mac_stats`,
`dump_nr_I0_stats`, `dump_pusch_stats`):

- **CCE failures**: the DLSCH and ULSCH lines each print one, and they are
  exported separately as `oai_gnb_mac_dl_cce_fail` and
  `oai_gnb_mac_ul_cce_fail`. The former `oai_gnb_mac_cce_fail` is
  **deprecated**: it still holds the UL value (what it always ended up holding,
  as the ULSCH line overwrote the DLSCH one) and will be removed in a later
  release.
- **Per-PRB I0** (`oai_gnb_l1_i0_noise_offset_db`): OAI writes one value per PRB
  and ` X.` for a blacklisted PRB. Each value keeps its PRB index (`prb`,
  `prb_index`), and a blacklisted PRB has no series. Before this fix the
  blacklisted PRBs were skipped, which shifted the index of every later PRB.
- **Negative tenths**: OAI prints the PRACH I0 (`PRACH I0 = %d.%d`), the ULSCH
  power and noise power (`ulsch_power[0] %d,%d`, `%d.%d`) and the UE's average
  SINR as the integer and tenth parts of the value, both truncated toward zero,
  so -3.5 dB is written `-3.-5` and -0.5 dB `0.-5`. These are now read
  correctly; before, a negative PRACH I0 or ULSCH power was not exported.
- **Average SINR** (`oai_gnb_mac_avg_sinr_db`, dB): the `average SINR` of the UE
  header line (OAI 2026.w42 and later), when OAI has SINR measurements.
- **Negative power headroom**: a power-limited UE reports a negative PH (OAI:
  -32 to 38 dB, then normalised to the allocation), and PCMAX can be negative
  too. Both are read; before, such a UE's header line was not matched, and its
  DLSCH, ULSCH and LCID lines were counted under the previous UE. A UE header
  the parser still cannot read now skips that UE, with a warning.
- **HARQ rounds**: OAI prints one count per HARQ round, as many as
  `dl_harq_round_max` / `ul_harq_round_max` (4 by default). They are exported
  as `oai_gnb_mac_dlsch_rounds` and `oai_gnb_mac_ulsch_rounds` with a `round`
  label (0 = first transmission). The former `oai_gnb_mac_dlsch_rounds_a` to
  `_d` (and `ulsch`) are **deprecated**: they still hold rounds 0 to 3 and will
  be removed in a later release. Before, with any other number of rounds the
  whole DLSCH or ULSCH line (BLER, MCS, errors, CCE failures) was not exported.

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
