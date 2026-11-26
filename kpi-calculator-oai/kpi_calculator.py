#----------------------------------------------------------------------------
# Author: Niloy Saha
# Email: niloysaha.ns@gmail.com
# version ='1.0.0'
# ---------------------------------------------------------------------------
"""
Prometheus exporter which exports RAN KPI.
For use with the 5G-MONARCH project and OAI.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import os
import logging
import time
import requests
import prometheus_client as prom
import argparse

from dotenv import load_dotenv

load_dotenv()
MONARCH_THANOS_URL = os.getenv("MONARCH_THANOS_URL")
DEFAULT_UPDATE_PERIOD = 1
UPDATE_PERIOD = int(os.environ.get('UPDATE_PERIOD', DEFAULT_UPDATE_PERIOD))
EXPORTER_PORT = 9000
TIME_RANGE = os.getenv("TIME_RANGE", "1s")


# Prometheus variables
# A UE is identified by its radio (namespace and service of the metrics-parser) and, as an RNTI is unique only within
# a cell, by its cell (pci, empty before OAI 2026.w46) and RNTI.
MAC_THROUGHPUT = prom.Gauge(
    'mac_throughput', 'MAC throughput per radio, cell and UE RNTI (bits/sec)',
    ['source_namespace', 'ran_component', 'pci', 'rnti', 'direction'],
)
NUMBER_UES = prom.Gauge(
    'number_ues', 'Number of connected UEs (LCID 4 present) of each gNB or DU', ['source_namespace', 'ran_component']
)
SATURATION_PERCENTAGE = prom.Gauge(
    'saturation_percentage', 'PRBs scheduled for a connected UE, in percent of the PRBs of its gNB or DU',
    ['source_namespace', 'ran_component', 'pci', 'rnti'],
)

# get rid of bloat
prom.REGISTRY.unregister(prom.PROCESS_COLLECTOR)
prom.REGISTRY.unregister(prom.PLATFORM_COLLECTOR)
prom.REGISTRY.unregister(prom.GC_COLLECTOR)

def query_prometheus(params, url):
    """
    Query Prometheus using requests and return value.
    params: The parameters for the Prometheus query.
    url: The URL of the Prometheus server.
    Returns: The result of the Prometheus query.
    """
    try:
        r = requests.get(url + '/api/v1/query', params)
        data = r.json()

        results = data["data"]["result"]
        return results
        
    except requests.exceptions.RequestException as e:
        log.error(f"Failed to query Prometheus: {e}")
    except (KeyError, IndexError, ValueError) as e:
        log.error(f"Failed to parse Prometheus response: {e}")
        log.warning("No data available!")


def _radio(metric):
    """(source_namespace, ran_component) of a metrics-parser series."""
    return metric.get("namespace", ""), metric.get("service", "")


def _ue(metric):
    """(source_namespace, ran_component, pci, rnti) of a per-UE series, or None without an RNTI."""
    rnti = metric.get("rnti")
    return (*_radio(metric), metric.get("pci", ""), rnti) if rnti else None


def _aggregate_by_radio_rnti(data):
    """
    Sums LCID bytes within one source namespace, radio service, cell and RNTI.
    Returns: {(source_namespace, ran_component, pci, rnti): value}
    """
    agg = defaultdict(float)
    for r in data or []:
        identity = _ue(r["metric"])
        if identity:
            agg[identity] += float(r["value"][1])
    return agg

def get_mac_throughput_per_rnti_and_direction(direction):
    """
    Returns throughput per UE RNTI for the specified direction: 'uplink' or 'downlink'.
    Uses Prometheus metrics: oai_gnb_mac_lcid_tx_bytes or oai_gnb_mac_lcid_rx_bytes
    Returns {(source_namespace, ran_component, pci, rnti): value (bits/sec)}
    """
    if direction == "downlink":
        metric = "oai_gnb_mac_lcid_tx_bytes"
    elif direction == "uplink":
        metric = "oai_gnb_mac_lcid_rx_bytes"
    else:
        log.warning(f"Invalid MAC direction: {direction}")
        return {}

    utc = timezone.utc
    end_time = datetime.now(utc)
    start_time = end_time - timedelta(seconds=int(TIME_RANGE[:-1]))

    end_data = query_prometheus({
        "query": f"{metric}",
        "time": end_time.replace(tzinfo=None).timestamp()
    }, MONARCH_THANOS_URL)

    start_data = query_prometheus({
        "query": f"{metric}",
        "time": start_time.replace(tzinfo=None).timestamp()
    }, MONARCH_THANOS_URL)

    # Aggregate LCIDs without combining different radios using the same RNTI.
    end_agg = _aggregate_by_radio_rnti(end_data)
    start_agg = _aggregate_by_radio_rnti(start_data)

    throughput_per_rnti = {}

    for identity, end_value in end_agg.items():
        start_value = start_agg.get(identity)

        if start_value is None:
            continue

        delta_bytes = end_value - start_value
        bits_per_sec = (delta_bytes * 8) / int(TIME_RANGE[:-1])

        throughput_per_rnti[identity] = bits_per_sec

    return throughput_per_rnti
   
def _connected_ues():
    """UEs with LCID 4 (a data bearer): {(source_namespace, ran_component, pci, rnti)}."""
    results = query_prometheus({"query": 'oai_gnb_mac_lcid_tx_bytes{lcid="4"}'}, MONARCH_THANOS_URL)
    if not results:
        log.warning("No LCID=4 results found")
        return set()
    return {identity for result in results if (identity := _ue(result["metric"]))}


def get_number_ues():
    """
    Connected UEs per gNB or DU (presence of LCID 4): {(source_namespace, ran_component): count}.
    Two UEs with the same RNTI on two radios or two cells are two UEs.
    """
    counts = defaultdict(int)
    for identity in _connected_ues():
        counts[identity[:2]] += 1
    log.info(f"Found {sum(counts.values())} connected UEs (LCID=4 presence) on {len(counts)} radios")
    return dict(counts)

def get_saturation_percentage_per_rnti():
    """
    PRBs scheduled for each UE, in percent of the PRBs of its cell: oai_gnb_rrc_prbs of the same radio and cell when
    the radio has them (a gNB, OAI 2026.w46 and later: its RRC lists its cells), else oai_gnb_l1_total_prbs of the same
    radio (one L1 per pod). 0 for a UE without LCID 4 (not connected).
    Returns {(source_namespace, ran_component, pci, rnti): value (percentage)}
    """
    connected = _connected_ues()
    log.info(f"Found {len(connected)} connected UEs (LCID=4)")

    total_prbs = {}
    for result in query_prometheus({"query": "oai_gnb_l1_total_prbs"}, MONARCH_THANOS_URL) or []:
        try:
            total_prbs[_radio(result["metric"])] = float(result["value"][1])
        except (KeyError, ValueError) as e:
            log.warning(f"Failed to parse total PRBs: {e}")
    cell_prbs = {}
    for result in query_prometheus({"query": 'oai_gnb_rrc_prbs{pci!=""}'}, MONARCH_THANOS_URL) or []:
        try:
            cell_prbs[(*_radio(result["metric"]), result["metric"]["pci"])] = float(result["value"][1])
        except (KeyError, ValueError) as e:
            log.warning(f"Failed to parse cell PRBs: {e}")
    if not total_prbs and not cell_prbs:
        log.warning("No results for oai_gnb_l1_total_prbs or oai_gnb_rrc_prbs")
        return {}

    saturation = {}
    for result in query_prometheus({"query": "oai_gnb_mac_nprb"}, MONARCH_THANOS_URL) or []:
        identity = _ue(result["metric"])
        total = (cell_prbs.get(identity[:3]) or total_prbs.get(identity[:2])) if identity else None
        if not total:  # no total for this radio or cell, or zero
            continue
        try:
            nprb = float(result["value"][1]) if identity in connected else 0.0
        except (KeyError, ValueError) as e:
            log.warning(f"Failed to parse NPRB result: {e}")
            continue
        saturation[identity] = nprb / total * 100
        log.debug(f"Saturation of {identity}: {saturation[identity]:.2f}%")
    return saturation

def main():
    log.info("Starting Prometheus server on port {}".format(EXPORTER_PORT))

    if not MONARCH_THANOS_URL:
        log.error("MONARCH_THANOS_URL is not set")
        return 

    log.info(f"Monarch Thanos URL: {MONARCH_THANOS_URL}")
    log.info(f"Time range: {TIME_RANGE}")
    log.info(f"Update period: {UPDATE_PERIOD}")
    prom.start_http_server(EXPORTER_PORT)

    while True:
        try:
            run_kpi_computation()
        except Exception as e:
            log.error(f"Failing to run KPI computation: {e}")
        time.sleep(UPDATE_PERIOD)

def export_mac_throughput_to_prometheus(identity, direction, value):
    source_namespace, ran_component, pci, rnti = identity
    value_mbits = round(value / 10 ** 6, 6)
    log.info(f"RNTI={rnti} PCI={pci} | DIR={direction} | RATE (Mbps)={value_mbits}")
    MAC_THROUGHPUT.labels(
        source_namespace=source_namespace, ran_component=ran_component,
        pci=pci, rnti=rnti, direction=direction,
    ).set(value)

def export_number_ues_to_prometheus(radio, value):
    source_namespace, ran_component = radio
    log.info(f"{ran_component}: {value} UEs")
    NUMBER_UES.labels(source_namespace=source_namespace, ran_component=ran_component).set(value)

def export_saturation_percentage_to_prometheus(identity, value):
    source_namespace, ran_component, pci, rnti = identity
    log.info(f"RNTI={rnti} PCI={pci} | VALUE ={value}")
    SATURATION_PERCENTAGE.labels(
        source_namespace=source_namespace, ran_component=ran_component, pci=pci, rnti=rnti,
    ).set(value)

def run_kpi_computation():
    # Clear stale labelled metrics before each update so removed UEs do not remain exported.
    MAC_THROUGHPUT.clear()
    NUMBER_UES.clear()
    SATURATION_PERCENTAGE.clear()

    directions = ["uplink", "downlink"]

    for direction in directions:
        mac_throughput = get_mac_throughput_per_rnti_and_direction(direction)
        for identity, value in mac_throughput.items():
            export_mac_throughput_to_prometheus(identity, direction, value)
    
    for radio, count in get_number_ues().items():
        export_number_ues_to_prometheus(radio, count)

    for identity, value in get_saturation_percentage_per_rnti().items():
        export_saturation_percentage_to_prometheus(identity, value)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='KPI calculator.')
    parser.add_argument('--log', default='info', help='Log verbosity level. Default is "info". Options are "debug", "info", "warning", "error", "critical".')

    args = parser.parse_args()

    # Convert log level from string to logging level
    log_level = getattr(logging, args.log.upper(), None)
    if not isinstance(log_level, int):
        raise ValueError(f'Invalid log level: {args.log}')
    
    # setup logger for console output
    log = logging.getLogger(__name__)
    log.setLevel(log_level)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    log.addHandler(console_handler)
        
    main()

    
    


