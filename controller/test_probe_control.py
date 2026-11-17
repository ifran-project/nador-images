import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from probe_api import change
from probe_control import desired_pods, external_paths, scoped_probe_pods, select_ues


class ProbeControlTests(unittest.TestCase):
    @patch("probe_control.target_points")
    def test_multi_target_rtt_scopes_to_each_anchor_and_slice(self, points):
        points.return_value = [
            (f"rtt-{i}", {"role": role, "container": role, "latency_mode": "BOTH"},
             dict(ip=f"192.0.2.{i}", interface="n3", pod=f"nf{i}", node=f"node{i}", container_id=f"id{i}"))
            for i, role in enumerate(("gnb", "gnb", "upf", "upf"), 1)
        ]
        rows = [
            {"gnb_n3_ip": f"192.0.2.{g}", "upf_n3_ip": f"192.0.2.{u}", "teid_args": f"teid-{g}-{u}",
             "imsi": f"0010100000001{g}{u}", "slice_id": f"01:00000{u}"}
            for g in (1, 2) for u in (3, 4)
        ]
        inventory = lambda: (rows, lambda rows: rows, lambda rows: {r["teid_args"]:r["imsi"] for r in rows})
        settings = {"extended": {}, "rtt": dict(enabled=True, allowed=True, image="rtt:test",
                                               targets=[t for _, t, _ in points.return_value])}

        def selected():
            pods = scoped_probe_pods(None, settings, {}, "owner", inventory)
            return {key: dict((e["name"], e["value"]) for e in pod["spec"]["containers"][0]["env"])["TEIDS"]
                    for key, pod in pods.items()}

        self.assertEqual(selected(), {
            "rtt-1":"teid-1-3 teid-1-4", "rtt-2":"teid-2-3 teid-2-4",
            "rtt-3":"teid-1-3 teid-2-3", "rtt-4":"teid-1-4 teid-2-4",
        })
        change(settings, "rtt", {"enabled":True, "slices":["01:000004"]})
        self.assertEqual(selected(), {"rtt-1":"teid-1-4", "rtt-2":"teid-2-4", "rtt-4":"teid-1-4 teid-2-4"})
        self.assertNotIn("rtt-pairer", scoped_probe_pods(None, settings, {}, "owner", inventory))

        settings["rtt"]["pairer_image"] = "pairer:test"
        pods = scoped_probe_pods(None, settings, {}, "owner", inventory)
        pairer = pods.pop("rtt-pairer")
        container, = pairer["spec"]["containers"]
        self.assertEqual(container["image"], "pairer:test")
        self.assertEqual(set(json.loads(container["env"][0]["value"])), {"teid-1-4", "teid-2-4"})
        self.assertEqual(pairer["metadata"]["annotations"]["prometheus.io/port"], "9200")
        self.assertEqual(set(pods), {"rtt-1", "rtt-2", "rtt-4"})
        for pod in pods.values():
            env = {entry["name"]: entry["value"] for entry in pod["spec"]["containers"][0]["env"]}
            self.assertEqual(env["PACKET_PAIRING"], "1")
            self.assertEqual(env["PACKET_PAIRER_HOST"], "packet-pairer")
            self.assertEqual(env["PACKET_PAIRER_PORT"], "9201")
        change(settings, "rtt", {"enabled": False})
        self.assertEqual(scoped_probe_pods(None, settings, {}, "owner", inventory), {})

    @patch("probe_control.target_points")
    def test_one_way_and_paths_run_once_per_anchor(self, points):
        points.return_value = [
            (f"{role}-{i}", {"role": role}, {"ip": f"192.0.2.{i}", "node": f"node{i}", "pod": f"nf{i}",
                                             "container_id": f"id{i}", "interface": "n3"})
            for i, role in enumerate(("gnb", "gnb", "upf", "upf"), 1)
        ]
        targets = [target for _, target, _ in points.return_value]
        settings = {"n3": {"enabled": True, "log_samples": False},
                    "upf_transit": {"enabled": True, "peer": "198.51.100.1"}, "test_port": 5201,
                    "sample": 16, "image": "oneway:test", "one_way_targets": targets, "extended": {}}
        desired = desired_pods(None, settings, {}, "owner")
        self.assertEqual(set(desired), {"collector", "gnb-1", "gnb-2", "upf-3", "upf-4", "transit-upf-3", "transit-upf-4"})
        for key in ("gnb-2", "upf-4", "transit-upf-3"):
            command = desired[key]["spec"]["containers"][0]["command"]
            gnb = command[command.index("--gnb") + 1]
            upf = command[command.index("--upf") + 1]
            self.assertEqual(gnb, "192.0.2.2" if key == "gnb-2" else "0.0.0.0")
            self.assertEqual(upf, "0.0.0.0" if key == "gnb-2" else f"192.0.2.{key[-1]}")
        paths = external_paths(None, {"user_plane_paths": dict(enabled=True, allowed=True, image="paths:test",
                                                              targets=targets, port=9403, interval=5)}, {}, "owner")
        self.assertEqual({p["metadata"]["labels"]["nador-target-pod"] for p in paths.values()},
                         {"nf1", "nf2", "nf3", "nf4"})
        settings["one_way_targets"] = targets[2:]
        points.return_value = points.return_value[2:]
        change(settings, "n3", {"enabled": False})
        self.assertEqual(set(desired_pods(None, settings, {}, "owner")), {"transit-upf-3", "transit-upf-4"})
        with self.assertRaisesRegex(ValueError, "at least one gNB"):
            change(settings, "n3", {"enabled": True})

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
        pod = scoped_probe_pods(None, settings, targets, "owner", inventory)["rtt-upf"]
        self.assertTrue(pod["spec"]["hostPID"])
        self.assertNotIn("hostNetwork", pod["spec"])
        self.assertEqual(pod["metadata"]["annotations"]["prometheus.io/path"], "/latency")
        self.assertIn({"name": "TEIDS", "value": "0x11:0x22"}, pod["spec"]["containers"][0]["env"])
        with self.assertLogs(level="WARNING"):
            anchor.return_value["injected_rtt_active"] = True
            self.assertFalse(scoped_probe_pods(None, settings, targets, "owner", inventory))
        anchor.return_value.pop("injected_rtt_active")
        settings["rtt"]["imsis"] = ["001010000000102"]
        self.assertFalse(scoped_probe_pods(None, settings, targets, "owner", inventory))

    def test_throughput_reconciles_each_ready_upf_without_mixing_sessions(self):
        def pod(i, *, ready=True, injected=False):
            status = NS(name="upf", ready=ready, container_id=f"containerd://id{i}")
            return NS(
                metadata=NS(name=f"upf-{i}", deletion_timestamp=None, annotations={}),
                spec=NS(node_name=f"node-{i}", ephemeral_containers=[NS(name="throughput-probe-1", env=[])] if injected else []),
                status=NS(pod_ip=f"192.0.2.{i}", container_statuses=[status], ephemeral_container_statuses=[]),
            )

        def target(i):
            return {"namespace": "core", "role": "upf", "component": f"upf-{i}", "selector": {"app": f"upf-{i}"},
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
            return scoped_probe_pods(api, settings, {}, "owner", inventory, "throughput")

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
