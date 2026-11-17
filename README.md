# NADOR Images

Source and Dockerfiles for the monitoring containers used by [NADOR](https://github.com/ifran-project/nador). Deployment image pins live in NADOR, not in this repository.

| Source | Purpose |
| --- | --- |
| `controller/` | Deploys and removes external probe workloads. |
| `latency-probe/`, `throughput-probe/`, `user-plane-path/` | TCP RTT, per-UE N3 throughput, and interface counters. |
| `amf-sniffer/`, `smf-sniffer/`, `ue-mapper/` | Control-plane observations and UE, session, and slice mapping. |
| `oai-gnb-metrics-parser/`, `kpi-calculator-oai/` | OAI log parsing and KPI calculation. |
| `packet-pairer/` | Matches gNB and UPF RTT observations for the same packet. |

## Build and publish

[`images.json`](images.json) lists the Dockerfiles, build contexts, and GHCR names for the standard images. For example, build the latency probe from the repository root:

```bash
docker build --platform linux/amd64 -f latency-probe/Dockerfile -t nador-latency:local .
```

The standard workflow builds changed components on pull requests and publishes successful `main` builds to [GHCR](https://github.com/orgs/ifran-project/packages). Use a `sha-<commit>` tag or a digest to select an exact image. The OAI gNB metrics parser has its own workflow.

The legacy injector and standalone throughput images are retired. Current Nador deployments use the external controller and throughput probe.

Build checks do not replace live network validation. The external controller and probes were tested on single-gNB OAI RFsim and OCUDU ZMQ using the image versions pinned by NADOR.

Original contributions are licensed under the [BSD 3-Clause Clear License](LICENSE). Inherited code and dependencies retain their original licenses.
