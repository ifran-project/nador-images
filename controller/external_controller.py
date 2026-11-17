"""Controller for disposable external probes; does not inject containers."""

import logging
import os
import signal
import threading

import kubernetes
import requests

from probe_api import make_server
from probe_control import reconcile, reconcile_extended
from ue_inventory import build_teid_ue_map, filter_conflicting_ue_teids, ue_mapping_is_complete


def main():
    """Reconcile probe settings and host the controller API."""
    if os.getenv("DISABLE_INJECTED_LATENCY", "1") != "1":
        raise ValueError("This controller only supports external RTT placement")
    logging.basicConfig(level=os.getenv("CONTROLLER_LOG_LEVEL", "WARNING").upper())
    namespace = os.getenv("MONITORING_NS", "monitoring")
    mapper_url = os.getenv("UE_MAPPER_URL", f"http://ue-mapper-api.{namespace}.svc.cluster.local")
    mapper_limit = int(os.getenv("UE_MAPPER_LIMIT", "2000"))
    interval = float(os.getenv("RECONCILE_INTERVAL", "5.0"))
    token = os.getenv("NADOR_CONTROLLER_API_TOKEN")
    if os.getenv("NADOR_CONTROLLER_API_PUBLIC") == "1" and not token:
        raise ValueError("Public controller API requires a bearer token")

    kubernetes.config.load_incluster_config()
    core = kubernetes.client.CoreV1Api()
    apps = kubernetes.client.AppsV1Api()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    def qualify(rows):
        return filter_conflicting_ue_teids([row for row in rows if ue_mapping_is_complete(row)])[0]

    def inventory():
        response = requests.get(f"{mapper_url}/inventory/ues", params={"limit": mapper_limit}, timeout=3)
        response.raise_for_status()
        return response.json()["ues"], qualify, build_teid_ue_map

    with make_server(
        core, namespace, token=token,
        certificate=os.getenv("NADOR_CONTROLLER_API_CERT"), key=os.getenv("NADOR_CONTROLLER_API_KEY"),
    ) as api:
        worker = threading.Thread(target=api.serve_forever, daemon=True)
        worker.start()
        try:
            while not stop.is_set():
                try:
                    reconcile_extended(core, apps, namespace)
                    reconcile(core, namespace, inventory)
                except Exception:
                    logging.exception("External probe reconciliation failed")
                stop.wait(interval)
        finally:
            api.shutdown()
            worker.join()


if __name__ == "__main__":
    main()
