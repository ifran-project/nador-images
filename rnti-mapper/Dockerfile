# RNTI mapper: every PERIOD seconds, joins the RAN view (RNTI <-> RAN UE id, from the gNB/CU logs) with the core
# view (RAN UE id <-> IMSI, from the AMF and the UE mapper) and serves ran_ue_info on PORT for Prometheus.
# Alpine: busybox awk, wget, nice and httpd (busybox-extras), and kubectl from the signed Alpine community repository.
FROM alpine:3.21

LABEL org.opencontainers.image.description="NADOR RNTI mapper: ran_ue_info{ran_node,ran_component,rnti,ran_ue_id,imsi,ue_name} for Grafana joins on RNTI."

RUN apk add --no-cache busybox-extras kubectl \
    && { command -v httpd > /dev/null || ln -s /bin/busybox-extras /usr/local/bin/httpd; } \
    && command -v httpd && kubectl version --client \
    && mkdir -p /www \
    && chown 65534:65534 /www

COPY run.sh join.awk /app/

# kubectl authenticates with the pod's service account; its cache goes to a writable HOME.
ENV HOME=/tmp
USER 65534:65534
CMD ["/bin/sh", "/app/run.sh"]
