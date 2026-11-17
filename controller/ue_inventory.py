"""Qualification and endpoint-scoped attribution for UE mapper records."""

from tunnel_identity import session_tunnels, tunnel_key


def normalize_teid(value) -> str:
    if value is None:
        return ""
    try:
        return f"0x{int(str(value).strip(), 0):08x}"
    except Exception:
        return ""


def parse_teids_from_arg(teid_arg: str) -> tuple[str, str]:
    teid_pair = (teid_arg or "").strip().split("@", 1)[0]
    if not teid_pair:
        return "", ""

    if ":" in teid_pair:
        ul, dl = teid_pair.split(":", 1)
        return normalize_teid(ul), normalize_teid(dl)

    return normalize_teid(teid_pair), ""


def extract_ue_teids(ue: dict) -> tuple[str, str]:
    teid_arg = (ue.get("teid_args") or "").strip()
    parsed_ul, parsed_dl = parse_teids_from_arg(teid_arg)
    ul_teid = normalize_teid(ue.get("ul_teid")) or parsed_ul
    dl_teid = normalize_teid(ue.get("dl_teid")) or parsed_dl
    return ul_teid, dl_teid


def ue_mapping_is_complete(ue: dict) -> bool:
    ul_teid, dl_teid = extract_ue_teids(ue)
    imsi = str(ue.get("imsi") or "").strip()
    return bool(
        (ue.get("teid_args") or "").strip()
        and ul_teid
        and dl_teid
        and imsi
        and imsi.lower() not in {"unknown", "null", "none", "-"}
        and str(ue.get("ue_ip") or "").strip()
        and str(ue.get("slice_id") or "").strip()
    )


def filter_conflicting_ue_teids(ues: list[dict]) -> tuple[list[dict], int]:
    """Reject conflicting owners of the same endpoint/TEID, not TEID reuse."""
    owners = {}
    conflicts = set()
    for index, ue in enumerate(ues):
        for _direction, endpoint, teid in session_tunnels(ue):
            key = tunnel_key(endpoint, teid)
            if not key:
                conflicts.add(index)
                continue
            previous = owners.setdefault(key, index)
            previous_owner = ues[previous].get("session_key") or ues[previous].get("imsi")
            owner = ue.get("session_key") or ue.get("imsi")
            if previous != index and previous_owner != owner:
                conflicts.update((previous, index))
    return [ue for index, ue in enumerate(ues) if index not in conflicts], len(conflicts)


def build_teid_ue_map(ues: list[dict]) -> dict:
    """Build unambiguous tunnel labels while retaining gNB and session identity."""
    mapping = {}
    for ue in ues:
        for direction, endpoint, teid in session_tunnels(ue):
            key = tunnel_key(endpoint, teid)
            if key:
                mapping[key] = {
                    **{field: str(ue.get(field) or "") for field in
                       ("imsi", "ue_ip", "ran_ue_id", "slice_id", "sst", "sd", "session_key", "gnb_id",
                        "upf_seid", "smf_seid", "upf_pfcp_ip")},
                    "teid_direction": direction,
                }
    return mapping
