# OAI gNB metrics parser

Builds the `2026.w46` parser as
`ghcr.io/ifran-project/oai-gnb-metrics-parser:2026.w46`. Mount the OAI gNB
log directory at `/oai-gnb-logs`; the parser exposes Prometheus metrics at
`http://<host>:9090/metrics`.

`2026.w46` reads the UE header of OAI 2026.w46 and later
(`UE RNTI <rnti> PCI <pci> CellID <cell> CU-UE-ID <id> ...`) as well as the
older one without PCI and cell ID: without it, the per-UE MAC metrics
(power headroom, PCMAX, RSRP and the lines that follow the header) are lost.

The upstream parser files are redistributed with permission; this import does
not change their licensing terms.
