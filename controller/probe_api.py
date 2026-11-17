"""Loopback-only probe control, reached using Kubernetes port-forward RBAC."""
import json
import hmac
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from probe_control import select_ues

CONFIG = "nador-one-way"
EXTERNAL = {"rtt", "n3", "upf_transit", "throughput", "user_plane_paths"}
SCOPED = {"rtt", "throughput"}


def change(settings, kind, request):
    if not isinstance(request, dict) or type(request.get("enabled")) is not bool:
        raise ValueError("enabled must be true or false")
    if (kind not in EXTERNAL or kind not in settings) and kind not in settings["extended"]:
        raise KeyError(kind)
    allowed = {"enabled"} | ({"imsis", "slices"} if kind in SCOPED else {"log_samples"} if kind == "n3" else set())
    if request.keys() - allowed:
        raise ValueError(f"Allowed fields: {', '.join(sorted(allowed))}")
    if kind in EXTERNAL:
        state = settings[kind] | request
        if kind in SCOPED or kind == "user_plane_paths":
            if state["enabled"] and not state.get("allowed"):
                raise ValueError("Configure external placement and a compatible probe image in monitoring.yaml")
            if kind in SCOPED:
                select_ues([], state)
        else:
            if state["enabled"] and not settings.get("image"):
                raise ValueError("Configure one_way.image first")
            if "log_samples" in request and type(request["log_samples"]) is not bool:
                raise ValueError("log_samples must be true or false")
            if kind == "upf_transit" and state["enabled"] and (not state.get("peer") or not settings["test_port"]):
                raise ValueError("Configure the UPF peer and test port first")
        settings[kind] = state
        return
    settings["extended"][kind] = request["enabled"]


def make_server(core, namespace, port=8787, *, token=None, certificate=None, key=None):
    if token is not None and (len(token) < 32 or not token.isascii() or not certificate or not key):
        raise ValueError("External API requires TLS files and an ASCII token of at least 32 characters")
    context = None
    if token is not None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate, key)

    class Server(ThreadingHTTPServer):
        def finish_request(self, request, address):
            if context is None:
                return super().finish_request(request, address)
            request.settimeout(5)
            with context.wrap_socket(request, server_side=True) as secured:
                super().finish_request(secured, address)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def reply(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def dispatch(self):
            if token is not None and not hmac.compare_digest(
                self.headers.get("Authorization", "").encode(), f"Bearer {token}".encode(),
            ):
                return self.reply(401, {"error": "Bearer authentication required"})
            try:
                cm = core.read_namespaced_config_map(CONFIG, namespace, _request_timeout=5)
                settings = json.loads(cm.data["settings.json"])
                if self.command == "GET" and self.path == "/v1/probes":
                    states = {name: {"enabled": enabled} for name, enabled in settings["extended"].items()}
                    for name in EXTERNAL.intersection(settings):
                        states[name] = {k: v for k, v in settings[name].items()
                                        if k in ("enabled", "imsis", "slices", "log_samples", "allowed")}
                    return self.reply(200, {"desired": states})
                if self.command != "PUT" or not self.path.startswith("/v1/probes/"):
                    return self.reply(404, {"error": "Unknown endpoint"})
                if self.headers.get_content_type() != "application/json":
                    return self.reply(415, {"error": "Expected application/json"})
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8192:
                    return self.reply(400, {"error": "Invalid request size"})
                kind = self.path.removeprefix("/v1/probes/")
                try:
                    change(settings, kind, json.loads(self.rfile.read(size)))
                except KeyError:
                    return self.reply(404, {"error": "Probe is not configured for controller management"})
                core.patch_namespaced_config_map(CONFIG, namespace, {
                    "metadata": {"resourceVersion": cm.metadata.resource_version},
                    "data": {"settings.json": json.dumps(settings)},
                }, _request_timeout=5)
                return self.reply(202, {"probe": kind, "state": "requested"})
            except (ValueError, TimeoutError) as exc:
                return self.reply(400, {"error": str(exc)})
            except Exception as exc:
                if getattr(exc, "status", None) == 409:
                    return self.reply(409, {"error": "Concurrent update; retry the request"})
                return self.reply(503, {"error": "Kubernetes state unavailable"})

        do_GET = dispatch
        do_PUT = dispatch

    return Server(("0.0.0.0" if context else "127.0.0.1", port), Handler)
