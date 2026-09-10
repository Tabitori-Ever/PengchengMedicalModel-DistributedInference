#!/bin/bash
#
# Build script v3.0 - 云/边/端 (data center / hospital / clinic)
# AlexNet(part1/part2) removed. Four task types.
# 镜像 tag 均为 v3.0（与 k8s/*.yaml 一致）：
#   inference-scheduler / hospital / clinic / dc
#
set -e

REGISTRY="${REGISTRY:-10.29.182.66:5000}"
PROJECT="${PROJECT:-k8s-repo}"
SCHED_TAG="${SCHED_TAG:-v3.0.7}"
TAG="${TAG:-v3.0}"

echo "============================================"
echo "v3.0 Build (data center / hospital / clinic)"
echo "  Registry: ${REGISTRY}/${PROJECT}   scheduler=${SCHED_TAG} other=${TAG}"
echo ""

# [0] Build frontend (React dist) - bundled into the scheduler image
echo "[0/5] Building frontend (React)..."
(cd frontend && if [ -d node_modules ]; then npm run build; else npm ci && npm run build; fi)

echo "[1/5] Building scheduler (incl. frontend + test dataset)..."
docker build -t inference-scheduler:${SCHED_TAG} -f scheduler/Dockerfile .
docker tag inference-scheduler:${SCHED_TAG} ${REGISTRY}/${PROJECT}/inference-scheduler:${SCHED_TAG}

echo "[2/5] Building hospital (edge)..."
docker build -t hospital:${TAG} -f hospital/Dockerfile .
docker tag hospital:${TAG} ${REGISTRY}/${PROJECT}/hospital:${TAG}

echo "[3/5] Building clinic (terminal)..."
docker build -t clinic:${TAG} -f clinic/Dockerfile .
docker tag clinic:${TAG} ${REGISTRY}/${PROJECT}/clinic:${TAG}

echo "[4/5] Building dc (patient-db + compute worker)..."
docker build -t dc:${TAG} -f dc/Dockerfile .
docker tag dc:${TAG} ${REGISTRY}/${PROJECT}/dc:${TAG}

echo "[5/5] Pushing images..."
docker push ${REGISTRY}/${PROJECT}/inference-scheduler:${SCHED_TAG}
for img in hospital clinic dc; do
  docker push ${REGISTRY}/${PROJECT}/${img}:${TAG}
done

echo ""
echo "============================================"
echo "v3.0 Build complete!"
echo "============================================"
