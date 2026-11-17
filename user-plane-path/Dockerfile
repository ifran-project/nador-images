FROM python:3.12-slim

LABEL org.opencontainers.image.version="2026w45-ml" \
      org.opencontainers.image.description="External Linux N3/N6 interface and passive GTP-U path observer."

RUN pip install --no-cache-dir 'prometheus-client>=0.20,<1'
WORKDIR /app
COPY user-plane-path/probe.py /app/probe.py
COPY n3-oneway/target_process.py /app/
CMD ["python", "/app/probe.py"]
