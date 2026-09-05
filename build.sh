#!/bin/bash
#
# Build script v2.0 - Hospital / Clinic Pod Architecture
# 各镜像当前 tag（与 k8s/*.yaml 一致）：
#   scheduler = v2.0.2（含 test 数据集 + React 前端；修复 part2 探测）
#   clinic    = v2.0.1（修复 metrics 用量解析）
#   hospital  = v2.0  （worker + part1 合并，内容未变）
#
set -e

REGISTRY="${REGISTRY:-10.29.182.66:5000}"
PROJECT="${PROJECT:-k8s-repo}"
SCHED_TAG="${SCHED_TAG:-v2.0.3}"
CLINIC_TAG="${CLINIC_TAG:-v2.0.1}"
HOSP_TAG="${HOSP_TAG:-v2.0}"

echo "============================================"
echo "Multi-Model Edge/Cloud Platform v2.0 Build"
echo "============================================"
echo "  Registry: ${REGISTRY}/${PROJECT}"
echo "  scheduler=${SCHED_TAG}  clinic=${CLINIC_TAG}  hospital=${HOSP_TAG}"
echo ""

# [0] Build frontend (React dist) - bundled into the scheduler image
echo "[0/4] Building frontend (React)..."
if [ -d frontend/node_modules ]; then
    (cd frontend && npm run build)
else
    (cd frontend && npm ci && npm run build)
fi

echo "[1/4] Building scheduler (incl. frontend + test dataset)..."
docker build -t inference-scheduler:${SCHED_TAG} -f scheduler/Dockerfile .
docker tag inference-scheduler:${SCHED_TAG} ${REGISTRY}/${PROJECT}/inference-scheduler:${SCHED_TAG}

echo "[2/4] Building hospital (worker + part1 merged)..."
docker build -t hospital:${HOSP_TAG} -f hospital/Dockerfile .
docker tag hospital:${HOSP_TAG} ${REGISTRY}/${PROJECT}/hospital:${HOSP_TAG}

echo "[3/4] Building clinic (memory monitor)..."
docker build -t clinic:${CLINIC_TAG} -f clinic/Dockerfile .
docker tag clinic:${CLINIC_TAG} ${REGISTRY}/${PROJECT}/clinic:${CLINIC_TAG}

echo "[4/4] Pushing images..."
docker push ${REGISTRY}/${PROJECT}/inference-scheduler:${SCHED_TAG}
docker push ${REGISTRY}/${PROJECT}/hospital:${HOSP_TAG}
docker push ${REGISTRY}/${PROJECT}/clinic:${CLINIC_TAG}

echo ""
echo "============================================"
echo "Build complete!"
echo "============================================"
echo ""
