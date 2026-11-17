FROM python:3.11-slim

LABEL org.opencontainers.image.title="Nador external probe controller"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir kubernetes requests

COPY controller/external_controller.py controller/probe_control.py controller/probe_api.py \
     controller/ue_inventory.py common/tunnel_identity.py /app/
CMD ["python", "/app/external_controller.py"]
