#!/bin/bash
# Stop all v3.0 services
for port in 8000 8006 8007 8010 9001; do
    fuser -k $port/tcp 2>/dev/null && echo "Stopped port $port" || true
done
echo "All services stopped."
