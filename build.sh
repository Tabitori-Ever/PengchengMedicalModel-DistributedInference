#!/bin/bash
#
# Build script v2.0 - Hospital / Clinic Pod Architecture
# Builds the scheduler (incl. frontend), hospital and clinic images.
# medical-server / part2 images are unchanged since v1.0.
#
set -e

REGISTRY="${REGISTRY:-10.29.182.66:5000}"
PROJECT="${PROJECT:-k8s-repo}"
# 注：clinic 与 scheduler 已在 v2.0 基础上修复后发布为 v2.0.1（本脚本默认 tag），
# hospital 镜像 v2.0 内容未变（yaml 仍引用 hospital:v2.0）。
TAG="${TAG:-v2.0.1}"

echo "============================================"
echo "Multi-Model Edge/Cloud Platform v2.0 Build"
echo "============================================"
echo "  Registry: ${REGISTRY}/${PROJECT}"
echo "  Tag:      ${TAG}"
echo ""

# [0] Build frontend (React dist) - bundled into the scheduler image
echo "[0/4] Building frontend (React)..."
if [ -d frontend/node_modules ]; then
    (cd frontend && npm run build)
else
    (cd frontend && npm ci && npm run build)
fi

echo "[1/4] Building scheduler (incl. frontend)..."
docker build -t inference-scheduler:${TAG} -f scheduler/Dockerfile .

echo "[2/4] Building hospital (worker + part1 merged)..."
docker build -t hospital:${TAG} -f hospital/Dockerfile .

echo "[3/4] Building clinic (memory monitor)..."
docker build -t clinic:${TAG} -f clinic/Dockerfile .

echo "[4/4] Tagging images for registry..."
for img in inference-scheduler hospital clinic; do
    docker tag ${img}:${TAG} ${REGISTRY}/${PROJECT}/${img}:${TAG}
done

echo ""
echo "============================================"
echo "Pushing images to registry..."
echo "============================================"

for img in inference-scheduler hospital clinic; do
    echo "  Pushing ${img}:${TAG}..."
    docker push ${REGISTRY}/${PROJECT}/${img}:${TAG}
done

echo ""
echo "============================================"
echo "Build complete!"
echo "============================================"
echo ""
