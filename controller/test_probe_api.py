import json
import threading
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace

from probe_api import make_server


class ProbeApiTests(unittest.TestCase):
    def test_http_switches_and_scope(self):
        state = {"rtt": {"allowed": True, "enabled": False}, "n3_one_way": {"enabled": False},
                 "upf_transit": {"enabled": False}, "host_probes": {"cpu": False}}

        class Core:
            def read_namespaced_config_map(self, *args, **kwargs):
                return SimpleNamespace(data={"settings.json": json.dumps(state)},
                                       metadata=SimpleNamespace(resource_version="1"))

            def patch_namespaced_config_map(self, name, namespace, body, **kwargs):
                assert body["metadata"]["resourceVersion"] == "1"
                state.update(json.loads(body["data"]["settings.json"]))

        server = make_server(Core(), "monitoring", 0)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/v1/probes"
        try:
            def put(kind, data):
                req = urllib.request.Request(f"{url}/{kind}", method="PUT", data=json.dumps(data).encode(),
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=2) as result:
                    return result.status

            self.assertEqual(put("rtt", {"enabled": True, "slices": ["01:ffffff"]}), 202)
            self.assertEqual(put("cpu", {"enabled": True}), 202)
            self.assertEqual(put("cpu", {"enabled": False}), 202)
            with self.assertRaises(urllib.error.HTTPError) as err:
                put("cpu", {"enabled": True, "slices": ["01:ffffff"]})
            self.assertEqual(err.exception.code, 400)
            err.exception.close()
            with urllib.request.urlopen(url, timeout=2) as response:
                desired = json.load(response)["desired"]
            self.assertEqual(desired["rtt"]["slices"], ["01:ffffff"])
            self.assertFalse(desired["cpu"]["enabled"])
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
