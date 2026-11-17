import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from probe_api import change
from probe_control import external_rtt, select_ues


class ProbeControlTests(unittest.TestCase):
    def test_scope_is_intersection(self):
        rows = [{"imsi": "001010000000100", "slice_id": "01:ffffff"},
                {"imsi": "001010000000101", "slice_id": "02:000001"}]
        self.assertEqual(select_ues(rows, {"slices": ["01:ffffff"]}), rows[:1])
        self.assertEqual(select_ues(rows, {"imsis": ["001010000000101"], "slices": ["01:ffffff"]}), [])
        with self.assertRaises(ValueError):
            select_ues(rows, {"imsis": "001010000000100"})

    @patch("probe_control.anchor")
    def test_workload_has_only_selected_teid_and_local_exporter(self, anchor):
        row = {"imsi": "001010000000100", "slice_id": "01:ffffff", "upf_n3_ip": "192.0.2.1", "teid_args": "0x11:0x22"}
        anchor.return_value = dict(ip="192.0.2.1", interface="eth0", pod="upf", node="node1", container_id="a" * 64)
        settings = {"rtt": {"enabled": True, "allowed": True, "image": "example:1", "imsis": [row["imsi"]]}}
        targets = {"upf": {"container": "upf", "latency_mode": "CORE"}}
        inventory = lambda: ([row, row | {"imsi": "001010000000101", "teid_args": "0x33:0x44"}],
                             lambda rows: rows, lambda rows: {})
        pod = external_rtt(None, settings, targets, "owner", inventory)["rtt-upf"]
        self.assertTrue(pod["spec"]["hostPID"])
        self.assertNotIn("hostNetwork", pod["spec"])
        self.assertEqual(pod["metadata"]["annotations"]["prometheus.io/path"], "/latency")
        self.assertIn({"name": "TEIDS", "value": "0x11:0x22"}, pod["spec"]["containers"][0]["env"])
        with self.assertLogs(level="WARNING"):
            anchor.return_value["injected_rtt_active"] = True
            self.assertFalse(external_rtt(None, settings, targets, "owner", inventory))
        anchor.return_value.pop("injected_rtt_active")
        settings["rtt"]["imsis"] = ["001010000000102"]
        self.assertFalse(external_rtt(None, settings, targets, "owner", inventory))

    def test_throughput_reconciles_each_ready_upf_without_mixing_sessions(self):
        def pod(i, *, ready=True, injected=False):
            status = NS(name="upf", ready=ready, container_id=f"containerd://id{i}")
            return NS(
                metadata=NS(name=f"upf-{i}", deletion_timestamp=None, annotations={}),
                spec=NS(node_name=f"node-{i}", ephemeral_containers=[NS(name="throughput-probe-1", env=[])] if injected else []),
                status=NS(pod_ip=f"192.0.2.{i}", container_statuses=[status], ephemeral_container_statuses=[]),
            )

        def target(i):
            return {"namespace": "core", "component": f"upf-{i}", "selector": {"app": f"upf-{i}"},
                    "container": "upf", "interface": "eth0", "latency_mode": "CORE"}

        def row(i):
            # The same numeric TEID can have a different owner at each endpoint.
            return dict(upf_n3_ip=f"192.0.2.{i}", imsi=f"00101000000010{i}", slice_id=f"01:00000{i}",
                        teid_args=f"0x11:0x22@192.0.2.{i}")

        pods = {"app=upf-1": [pod(1)], "app=upf-2": [pod(2), pod(3, ready=False)]}
        api = NS(list_namespaced_pod=lambda namespace, label_selector: NS(items=pods[label_selector]))
        request = dict(enabled=True, allowed=True, image="example:1", targets=[target(1), target(2), target(2)])
        settings = {"throughput": request, "extended": {}}
        rows = [row(i) for i in (1, 2, 3)]
        inventory = lambda: (rows, lambda rows: rows, lambda rows: {r["upf_n3_ip"]: r["imsi"] for r in rows})

        def desired():
            return external_rtt(api, settings, {}, "owner", inventory, "throughput")

        all_probes = desired()
        self.assertEqual(len(all_probes), 2)
        for spec in all_probes.values():
            container, = spec["spec"]["containers"]
            env = {entry["name"]: entry["value"] for entry in container["env"]}
            i = int(env["PROBE_POD"][-1])
            self.assertEqual(spec["spec"]["nodeName"], f"node-{i}")
            self.assertEqual(env["NADOR_TARGET_CONTAINER"], f"id{i}")
            self.assertEqual(env["TEIDS"], row(i)["teid_args"])
            self.assertNotIn(row(3-i)["imsi"], env["TEID_UE_MAP"])
            self.assertEqual(spec["metadata"]["annotations"]["prometheus.io/port"], "9300")
        pods["app=upf-2"] = []
        self.assertEqual(len(desired()), 1)
        pods["app=upf-2"] = [pod(2, injected=True)]
        with self.assertLogs(level="WARNING"):
            self.assertEqual(len(desired()), 1)
        pods["app=upf-2"] = [pod(2)]
        change(settings, "throughput", {"enabled": True, "slices": [row(2)["slice_id"]]})
        self.assertEqual(len(scoped := desired()), 1)
        key, = scoped
        self.assertEqual(scoped[key], all_probes[key])
        change(settings, "throughput", {"enabled": False})
        self.assertFalse(desired())
