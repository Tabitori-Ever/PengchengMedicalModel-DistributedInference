#!/bin/bash
# Start all services (v3.0) for local development on one machine:
#   hospital(edge) 8006, clinic(terminal) 8007, dc 8010,
#   medical-server 9001, scheduler 8000. No AlexNet/part1/part2.
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

WORKER_WEIGHTS="$ROOT/z-鹏城医疗模型代码/worker/weights_bigcrop/multi/best_model_fold1.pth"
SERVER_WEIGHTS="$ROOT/z-鹏城医疗模型代码/server/weights_bigcrop/multi/best_model_fold1.pth"

echo "=== Starting v3.0 services ==="

for port in 8000 8006 8007 8010 9001; do
    fuser -k $port/tcp 2>/dev/null && echo "  Freed port $port" || true
done
sleep 1

# 1. Hospital (edge) - local emulation of hospital-a/b
echo "[1/5] Hospital pod (8006)..."
cd "$ROOT/hospital"
HOSPITAL_NAME=hospital-a NODE_NAME=localhost POD_NAME=hospital-a-local MODEL_PATH="$WORKER_WEIGHTS" \
nohup python -m uvicorn app:app --host 0.0.0.0 --port 8006 > /tmp/hospital.log 2>&1 &

# 2. Clinic (terminal) - local emulation of clinic-1/2
echo "[2/5] Clinic pod (8007)..."
cd "$ROOT/clinic"
CLINIC_NAME=clinic-1 POD_NAME=clinic-1-local NODE_NAME=localhost \
nohup python -m uvicorn app:app --host 0.0.0.0 --port 8007 > /tmp/clinic.log 2>&1 &

# 3. Data center services (patient db + compute worker)
echo "[3/5] dc-services (8010)..."
cd "$ROOT/dc"
NODE_NAME=localhost nohup python -m uvicorn app:app --host 0.0.0.0 --port 8010 > /tmp/dc.log 2>&1 &

# 4. Medical server (cloud)
echo "[4/5] Medical server (9001)..."
cd "$ROOT/medical-server"
MODEL_PATH="$SERVER_WEIGHTS" nohup python -m uvicorn app:app --host 0.0.0.0 --port 9001 > /tmp/server.log 2>&1 &

# 5. Scheduler (data center scheduling)
echo "[5/5] Scheduler (8000)..."
cd "$ROOT"
HOSPITAL_A_URL=http://localhost:8006 \
HOSPITAL_B_URL=http://localhost:8006 \
CLINIC_1_URL=http://localhost:8007 \
CLINIC_2_URL=http://localhost:8007 \
MEDICAL_SERVER_URL=http://localhost:9001/infer \
DC_SERVICES_URL=http://localhost:8010 \
nohup python -m uvicorn scheduler.api:app --host 0.0.0.0 --port 8000 > /tmp/scheduler.log 2>&1 &

echo ""
echo "Waiting for services..."
for port in 8006 8007 8010 9001 8000; do
    for i in $(seq 1 60); do
        if curl -s --max-time 1 http://localhost:$port/ > /dev/null 2>&1; then
            echo "  Port $port: READY"; break
        fi
        sleep 2
    done
done

echo ""
echo "=== v3.0 services ready ==="
echo "Frontend: http://localhost:8000/app/"
echo "Stop: bash stop_all.sh"
