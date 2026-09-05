#!/bin/bash
# Start all Edge Inference services (v2.0) for local development.
# Local mode emulates the pod architecture on one machine:
#   hospital (worker+part1) on 8006, clinic (mem monitor) on 8007,
#   medical-server 9001, part2 8002, scheduler 8000.
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Weight file paths
WORKER_WEIGHTS="$ROOT/z-鹏城医疗模型代码/worker/weights_bigcrop/multi/best_model_fold1.pth"
SERVER_WEIGHTS="$ROOT/z-鹏城医疗模型代码/server/weights_bigcrop/multi/best_model_fold1.pth"

echo "=== Starting Edge Inference Services (v2.0) ==="
echo "Root: $ROOT"

# Kill any existing services on these ports
for port in 8000 8002 8006 8007 9001; do
    fuser -k $port/tcp 2>/dev/null && echo "  Freed port $port" || true
done
sleep 1

# 1. Hospital (medical worker + AlexNet part1 merged) - local emulation of
#    hospital-a; hospital-b maps to the same local port.
echo "[1/5] Starting Hospital pod (8006)..."
cd "$ROOT/hospital"
HOSPITAL_NAME=hospital-a NODE_NAME=localhost MODEL_PATH="$WORKER_WEIGHTS" \
nohup python -m uvicorn app:app --host 0.0.0.0 --port 8006 > /tmp/hospital.log 2>&1 &

# 2. Clinic (pod memory monitor) - local emulation of clinic-1
echo "[2/5] Starting Clinic pod (8007)..."
cd "$ROOT/clinic"
CLINIC_NAME=clinic-1 POD_NAME=clinic-1-local NODE_NAME=localhost NAMESPACE=default \
nohup python -m uvicorn app:app --host 0.0.0.0 --port 8007 > /tmp/clinic.log 2>&1 &

# 3. Medical Server (Cloud)
echo "[3/5] Starting Medical Server (9001)..."
cd "$ROOT/medical-server"
MODEL_PATH="$SERVER_WEIGHTS" nohup python -m uvicorn app:app --host 0.0.0.0 --port 9001 > /tmp/server.log 2>&1 &

# 4. AlexNet Part2
echo "[4/5] Starting AlexNet Part2 (8002)..."
cd "$ROOT"
nohup python -m uvicorn part2.app:app --host 0.0.0.0 --port 8002 > /tmp/part2.log 2>&1 &

# 5. Scheduler (must come last, after others are up)
echo "[5/5] Starting Scheduler (8000)..."
cd "$ROOT"
HOSPITAL_A_URL=http://localhost:8006 \
HOSPITAL_B_URL=http://localhost:8006 \
CLINIC_1_URL=http://localhost:8007 \
CLINIC_2_URL=http://localhost:8007 \
MEDICAL_SERVER_URL=http://localhost:9001/infer \
PART2_URL=http://localhost:8002/infer \
nohup python -m uvicorn scheduler.api:app --host 0.0.0.0 --port 8000 > /tmp/scheduler.log 2>&1 &

# Wait for all services to be ready
echo ""
echo "Waiting for services to start..."
for port in 8006 8007 9001 8002 8000; do
    for i in $(seq 1 60); do
        if curl -s --max-time 1 http://localhost:$port/ > /dev/null 2>&1; then
            echo "  Port $port: READY"
            break
        fi
        sleep 2
    done
done

echo ""
echo "=== All services ready ==="
echo "Frontend: http://localhost:8000/app/index.html"
echo ""
echo "To stop: bash stop_all.sh"
