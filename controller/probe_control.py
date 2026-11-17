"""Reconcile disposable one-way probe pods from the monitoring ConfigMap."""
import hashlib
import ipaddress
import json
import logging
import re


SELECTOR = "nador-one-way-managed=true"
MAPPER = "http://ue-mapper-api:8080"
COLLECTOR = "http://nador-one-way-collector:55393"
EXTENDED_PROBES = ("cpu", "drops", "queueing", "tcp", "split-signaling", "measurement-quality")
PAUSED_SELECTOR = "monitoring.blueprint/probe-paused"


def reconcile_extended(core, apps, namespace):
    cm = core.read_namespaced_config_map("nador-one-way", namespace, _request_timeout=5)
    states = json.loads(cm.data["settings.json"]).get("extended", {})
    for kind in EXTENDED_PROBES:
        if type(states.get(kind)) is not bool:
            continue
        try:
            base = f"probe-{kind}-extended"
            if kind == "measurement-quality":
                name = base + "-service"
                desired = int(states[kind])
                current = apps.read_namespaced_deployment(name, namespace, _request_timeout=5)
                if current.spec.replicas != desired:
                    apps.patch_namespaced_deployment(name, namespace, {"spec": {"replicas": desired}}, _request_timeout=5)
            else:
                name = base + "-node"
                current = apps.read_namespaced_daemon_set(name, namespace, _request_timeout=5)
                selector = current.spec.template.spec.node_selector or {}
                if (PAUSED_SELECTOR not in selector) != states[kind]:
                    apps.patch_namespaced_daemon_set(name, namespace, {
                        "spec": {"template": {"spec": {"nodeSelector": {
                            PAUSED_SELECTOR: None if states[kind] else "true",
                        }}}},
                    }, _request_timeout=5)
        except Exception:
            logging.exception("Could not reconcile %s", kind)


def ready_containers(api, target):
    selector = ",".join(f"{k}={v}" for k, v in target["selector"].items())
    pods = api.list_namespaced_pod(target["namespace"], label_selector=selector).items
    return [
        (pod, status)
        for pod in pods if not pod.metadata.deletion_timestamp and pod.spec.node_name
        for status in pod.status.container_statuses or []
        if status.name == target["container"] and status.ready and status.container_id
    ]


def resolve_anchor(target, pod, status):
    interface = target.get("n3_interface") or target["interface"]
    networks = json.loads((pod.metadata.annotations or {}).get("k8s.v1.cni.cncf.io/network-status", "[]"))
    ips = [ip for item in networks if item.get("interface") == interface for ip in item.get("ips", [])]
    if not ips and interface == "eth0" and pod.status.pod_ip:
        ips = [pod.status.pod_ip]
    ipv4 = [str(addr) for ip in ips if (addr := ipaddress.ip_interface(ip).ip).version == 4]
    if len(ipv4) != 1:
        raise ValueError(f'{target["component"]}: cannot resolve one IPv4 address on {interface}')
    point = {"interface": interface, "ip": ipv4[0], "node": pod.spec.node_name, "pod": pod.metadata.name,
             "container_id": status.container_id.split("://", 1)[-1]}
    terminated = {item.name for item in (pod.status.ephemeral_container_statuses or [])
                  if item.state and item.state.terminated}
    if any(container.name not in terminated and
           (container.name.startswith(("latency-probe", "ebpf-latency-probe"))
            or any(env.name == "LATENCY_MODE" for env in (container.env or [])))
           for container in (pod.spec.ephemeral_containers or [])):
        point["injected_rtt_active"] = True
    if any(container.name not in terminated and container.name.startswith("throughput-probe")
           for container in (pod.spec.ephemeral_containers or [])):
        point["injected_throughput_active"] = True
    return point


def anchor(api, target):
    ready = ready_containers(api, target)
    if len(ready) != 1:
        raise ValueError(f'{target["component"]}: expected one ready target, found {len(ready)}')
    return resolve_anchor(target, *ready[0])


def throughput_anchors(api, targets, request):
    if "targets" not in request:
        if "upf" in targets:
            target = targets["upf"]
            yield "throughput-upf", target, anchor(api, target)
        return
    yield from target_points(api, request["targets"], "throughput-upf")


def target_points(api, targets, prefix):
    seen = set()
    for target in targets:
        for pod, status in ready_containers(api, target):
            identity = target["namespace"], pod.metadata.name
            if identity in seen:
                continue
            try:
                point = resolve_anchor(target, pod, status)
            except ValueError as exc:
                logging.warning("Probe target %s/%s: %s", *identity, exc)
                continue
            seen.add(identity)
            suffix = hashlib.sha256("/".join(identity).encode()).hexdigest()[:16]
            yield f"{prefix or target['role']}-{suffix}", target, point


def pod_spec(role, settings, owner_uid, command, *, target=None, metrics_port=None, env=None, path="/metrics"):
    name = f"nador-one-way-{role}"
    pull_secret = settings.get("image_pull_secret")
    fingerprint = hashlib.sha256(json.dumps([settings["image"], pull_secret, command, target, env], sort_keys=True).encode()).hexdigest()
    annotations = {"nador-one-way-config": fingerprint}
    if metrics_port:
        annotations.update({"prometheus.io/scrape": "true", "prometheus.io/port": str(metrics_port)})
        annotations["prometheus.io/path"] = path
    container = {
        "name": role, "image": settings["image"], "imagePullPolicy": "IfNotPresent",
        "command": command,
        "env": [{"name": "NADOR_ONEWAY_TOKEN", "valueFrom": {
            "secretKeyRef": {"name": "nador-one-way-token", "key": "token"}}}],
    }
    spec = {"restartPolicy": "Always", "automountServiceAccountToken": False,
            "terminationGracePeriodSeconds": 10, "containers": [container]}
    if env is not None:
        container["env"] = [{"name": key, "value": value} for key, value in env.items()]
    if pull_secret:
        spec["imagePullSecrets"] = [{"name": pull_secret}]
    if target:
        spec.update(nodeName=target["node"], hostPID=True, tolerations=[{"operator": "Exists"}])
        container["securityContext"] = {"privileged": True}
    labels = {"nador-one-way-managed": "true", "nador-one-way-role": role}
    if target:
        labels["nador-target-pod"] = target["pod"][:63].rstrip("-.")
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": name, "labels": labels,
                     "annotations": annotations, "ownerReferences": [{
                         "apiVersion": "v1", "kind": "ConfigMap", "name": "nador-one-way", "uid": owner_uid}]},
        "spec": spec,
    }


def desired_pods(api, settings, targets, owner):
    desired = {}
    n3, upf = settings["n3"], settings["upf_transit"]
    if not n3["enabled"] and not upf["enabled"]:
        return desired
    if not settings.get("image"):
        raise ValueError("Set one_way.image before enabling probes")
    if upf["enabled"] and (not upf.get("peer") or not settings["test_port"]):
        raise ValueError("UPF transit requires peer and test_port")
    if n3["enabled"]:
        cmd = ["python3", "/app/oneway.py", "collector", "--mapper-url", MAPPER]
        if n3["log_samples"]:
            cmd.append("--log-samples")
        desired["collector"] = pod_spec("collector", settings, owner, cmd, metrics_port=55393)
    multi = "one_way_targets" in settings
    if multi:
        inputs = [target for target in settings["one_way_targets"] if n3["enabled"] or target["role"] == "upf"]
        points = target_points(api, inputs, "")
    else:
        anchors = {role: anchor(api, targets[role]) for role in ("gnb", "upf")}
        points = ((role, targets[role] | {"role": role}, point) for role, point in anchors.items())
    sample = ["--sample", str(settings["sample"])]
    for key, target, point in points:
        role = target["role"]
        if multi:
            local = {role: point["ip"]}
            endpoints = ["--gnb", local.get("gnb", "0.0.0.0"), "--upf", local.get("upf", "0.0.0.0")]
        else:
            endpoints = ["--gnb", anchors["gnb"]["ip"], "--upf", anchors["upf"]["ip"]]
        launcher = ["python3", "/app/target_process.py", point["container_id"]]
        if n3["enabled"]:
            cmd = [*launcher, "/app/oneway.py",
                   "agent", "--role", role, "--url", COLLECTOR, "--interface", point["interface"],
                   "--test-port", str(settings["test_port"]), *sample, *endpoints]
            desired[key] = pod_spec(key, settings, owner, cmd, target=point)
        if upf["enabled"] and role == "upf":
            name = "transit-" + key if multi else "transit"
            cmd = [*launcher, "/app/upf_transit.py",
                   "--n3", point["interface"], "--n6", target.get("n6_interface") or point["interface"],
                   "--bind", "0.0.0.0", "--peer", upf["peer"], "--port", str(settings["test_port"]),
                   "--mapper-url", MAPPER, *sample, *endpoints]
            desired[name] = pod_spec(name, settings, owner, cmd, target=point, metrics_port=55437)
    return desired


def select_ues(rows, request):
    imsis, slices = request.get("imsis", []), request.get("slices", [])
    for values, pattern in ((imsis, r"[0-9]{5,16}"), (slices, r"[0-9a-f]{2}:[0-9a-f]{6}")):
        if not isinstance(values, list) or any(not isinstance(v, str) or not re.fullmatch(pattern, v) for v in values):
            raise ValueError("RTT accepts IMSIs and normalized slice IDs such as 01:ffffff")
    return [row for row in rows
            if (not imsis or row.get("imsi") in imsis) and (not slices or row.get("slice_id") in slices)]


def external_rtt(api, settings, targets, owner, inventory, kind="rtt"):
    request = settings.get(kind, {})
    if not request.get("enabled"):
        return {}
    if not request.get("allowed"):
        raise ValueError(f"{kind} requires configured external targets")
    rows, qualify, build_map = inventory()
    rows = select_ues(qualify(rows), request)
    result = {}
    points = (
        (("upf", key, target, point) for key, target, point in throughput_anchors(api, targets, request))
        if kind == "throughput" else
        ((role, "rtt-" + role, target, anchor(api, target)) for role, target in targets.items())
    )
    for role, key, target, point in points:
        if point.get(f"injected_{kind}_active"):
            logging.warning("External %s blocked by an injected probe on %s", kind, point["pod"])
            continue
        field = "gnb_n3_ip" if role == "gnb" else "upf_n3_ip"
        selected = [row for row in rows if row.get(field) == point["ip"]]
        args = sorted({row["teid_args"] for row in selected if row.get("teid_args")})
        if not args:
            continue
        env = {
            "TEIDS": " ".join(args), "TEID_UE_MAP": json.dumps(build_map(selected), sort_keys=True),
            "IFACE": point["interface"], "PROBE_ROLE": role, "PROBE_POD": point["pod"],
            "PROBE_NODE": point["node"], "PROBE_TARGET": target["container"],
            "LATENCY_MODE": target["latency_mode"], "PACKET_PAIRING": "0",
            "PRIO": "24111", "HANDLE": "27904", "EXPORTER_PORT": "9100",
        }
        if kind == "throughput":
            env.update(NADOR_TARGET_CONTAINER=point["container_id"], HANDLE="27914", PRIO="24121",
                       EXPORTER_PORT="9300")
            result[key] = pod_spec(key, settings | {"image": request["image"]}, owner,
                                   ["python3", "/app/exporter.py"], target=point, metrics_port=9300, env=env)
            continue
        result[key] = pod_spec(key, settings | {"image": request["image"]}, owner,
                               ["bash", "/app/external-rtt.sh", point["container_id"]],
                               target=point, metrics_port=9100, env=env, path="/latency")
    return result


def external_paths(api, settings, targets, owner):
    request = settings.get("user_plane_paths", {})
    if not request.get("enabled"):
        return {}
    if not request.get("allowed") or not request.get("image"):
        raise ValueError("User-plane paths require targets and a path image")
    desired = {}
    points = (target_points(api, request["targets"], "path") if "targets" in request else
              (("path-" + role, target | {"role": role}, anchor(api, target))
               for role, target in targets.items()))
    for key, target, point in points:
        role = target["role"]
        env = {
            "NADOR_TARGET_CONTAINER": point["container_id"],
            "USER_PLANE_PROBE_ANCHOR": role,
            "USER_PLANE_PROBE_N3_INTERFACE": point["interface"],
            "USER_PLANE_PROBE_N6_INTERFACE": target.get("n6_interface") or "",
            "USER_PLANE_PROBE_MONITOR_N6": str(role == "upf").lower(),
            "USER_PLANE_PROBE_METRICS_PORT": str(request["port"]),
            "USER_PLANE_PROBE_COLLECTION_INTERVAL_SECONDS": str(request["interval"]),
        }
        desired[key] = pod_spec(key, settings | {"image": request["image"]}, owner,
                                ["python3", "/app/probe.py"], target=point, metrics_port=request["port"], env=env)
    return desired


def reconcile(api, namespace, inventory=None):
    cm = api.read_namespaced_config_map("nador-one-way", namespace)
    settings, targets = (json.loads(cm.data[key]) for key in ("settings.json", "targets.json"))
    desired = {}
    preserve = set()
    for kind, build in (("one-way", lambda: desired_pods(api, settings, targets, cm.metadata.uid)),
                        ("rtt", lambda: external_rtt(api, settings, targets, cm.metadata.uid, inventory)),
                        ("throughput", lambda: external_rtt(api, settings, targets, cm.metadata.uid, inventory, "throughput")),
                        ("path", lambda: external_paths(api, settings, targets, cm.metadata.uid))):
        try:
            desired.update(build())
        except ValueError as exc:
            logging.warning("Probe waiting for valid targets/settings: %s", exc)
        except Exception:
            if kind not in ("rtt", "throughput"):
                raise
            preserve.add(kind)
            logging.exception("%s reconciliation deferred", kind)
    current = api.list_namespaced_pod(namespace, label_selector=SELECTOR).items
    for pod in current:
        role = pod.metadata.labels["nador-one-way-role"]
        if any(role.startswith(kind + "-") for kind in preserve):
            continue
        expected = desired.pop(role, None)
        if not any(ref.kind == "ConfigMap" and ref.name == "nador-one-way" and ref.uid == cm.metadata.uid
                   for ref in pod.metadata.owner_references or []):
            continue
        if pod.metadata.deletion_timestamp:
            continue
        current_hash = (pod.metadata.annotations or {}).get("nador-one-way-config")
        if not expected or expected["metadata"]["annotations"]["nador-one-way-config"] != current_hash:
            api.delete_namespaced_pod(pod.metadata.name, namespace, body={
                "preconditions": {"uid": pod.metadata.uid},
            })
    for body in desired.values():
        api.create_namespaced_pod(namespace, body)
