import unittest
from unittest.mock import patch

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
