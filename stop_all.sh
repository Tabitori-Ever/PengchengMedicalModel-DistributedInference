#!/bin/bash
# Stop all Edge Inference services (v2.0)
for port in 8000 8002 8006 8007 9001; do
    fuser -k $port/tcp 2>/dev/null && echo "Stopped port $port" || true
done
echo "All services stopped."
