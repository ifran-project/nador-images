#!/bin/bash
set -euo pipefail

pid=$(python3 -c 'import sys; from target_process import find_pid; print(find_pid(sys.argv[1]))' "$1")
export RTT_TARGET_PID="$pid"
export RTT_FILTER_TUNNELS=1
test -e "/proc/$pid/ns/net"
real_tc=/usr/sbin/tc
mkdir -p /tmp/external-rtt
if [[ ! -f /tmp/external-rtt/loader ]]; then
    mv /app/gtp_latency_user /tmp/external-rtt/loader
fi
printf '#!/bin/bash\nexec nsenter -t "$RTT_TARGET_PID" -n /tmp/external-rtt/loader "$@"\n' > /app/gtp_latency_user
printf '#!/bin/bash\nexec nsenter -t "$RTT_TARGET_PID" -n %s "$@"\n' "$real_tc" > /usr/local/bin/tc
chmod +x /app/gtp_latency_user /usr/local/bin/tc
exec /app/entrypoint-latency.sh
