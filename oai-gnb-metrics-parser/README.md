# OAI gNB metrics parser

Builds the `2026.w38` parser as
`ghcr.io/ifran-project/oai-gnb-metrics-parser:2026.w38`. Mount the OAI gNB
log directory at `/oai-gnb-logs`; the parser exposes Prometheus metrics at
`http://<host>:9090/metrics`.

The upstream parser files are redistributed with permission; this import does
not change their licensing terms.
