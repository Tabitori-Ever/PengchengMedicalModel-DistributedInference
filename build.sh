#!/bin/bash
#
# Build script v3.3 - 云/边/端 + 测试/性能对比站点
#
# 本机（WSL 控制面）没有 PyPI / PyTorch 源 / npm 的外网出口，因此
# scheduler / hospital / clinic 采用**增量构建**：以上一个发布镜像为基础，
# 只覆盖本次改动的文件（各自的 Dockerfile；完整重建见 Dockerfile.full）。
#
# 镜像 tag（与 k8s/*.yaml 一致）：
#   inference-scheduler:v3.3.5  hospital:v3.6  medical-server:v1.1  clinic:v3.3
#   dc:v3.0（未改动）  benchmark-site:v1.2
# 部署顺序（协议有版本差异）：medical-server -> hospital -> scheduler -> benchmark-site
#
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

REGISTRY="${REGISTRY:-k8s-master:5000}"
PROJECT="${PROJECT:-k8s-repo}"
SCHED_TAG="${SCHED_TAG:-v3.3.5}"
TAG="${TAG:-v3.3}"
HOSP_TAG="${HOSP_TAG:-v3.6}"
MED_TAG="${MED_TAG:-v1.1}"
BENCH_TAG="${BENCH_TAG:-v1.2}"
BUILD_BENCH="${BUILD_BENCH:-auto}"   # auto: 站点源码存在才构建

echo "============================================"
echo "v3.2 Build (cloud-edge-end + benchmark site)"
echo "  Registry: ${REGISTRY}/${PROJECT}"
echo "  scheduler=${SCHED_TAG}  clinic=${TAG} hospital=${HOSP_TAG} medical-server=${MED_TAG}  site=${BENCH_TAG}"
echo ""

# [0] 前端（构建产物打进 scheduler 镜像；离线，复用已有 node_modules）
echo "[0/6] Building platform frontend (React)..."
(cd frontend && if [ -d node_modules ]; then npm run build; else npm ci && npm run build; fi)

# [1] 站点前端（独立站点，构建产物打进站点镜像）
if [ -d benchmark/frontend ]; then
  echo "[1/6] Building benchmark-site frontend (React)..."
  (cd benchmark/frontend && \
     if [ ! -d node_modules ]; then cp -r ../../frontend/node_modules ./node_modules; fi && \
     npm run build)
  # bundled patient inputs are generated inside the image build
  # (benchmark/Dockerfile -> benchmark/tools/make_patients.py)
else
  echo "[1/6] benchmark/ frontend absent - skipping site frontend"
fi

echo "[2/6] Building scheduler (incremental on ${REGISTRY}/${PROJECT}/inference-scheduler:v3.1.8)..."
docker build -t inference-scheduler:${SCHED_TAG} -f scheduler/Dockerfile .
docker tag inference-scheduler:${SCHED_TAG} ${REGISTRY}/${PROJECT}/inference-scheduler:${SCHED_TAG}

echo "[3/6] Building hospital (incremental on hospital:v3.0)..."
docker build -t hospital:${HOSP_TAG} -f hospital/Dockerfile .
docker tag hospital:${HOSP_TAG} ${REGISTRY}/${PROJECT}/hospital:${HOSP_TAG}

echo "[3b/6] Building medical-server (incremental on medical-server:v1.0)..."
docker build -t medical-server:${MED_TAG} -f medical-server/Dockerfile.inc .
docker tag medical-server:${MED_TAG} ${REGISTRY}/${PROJECT}/medical-server:${MED_TAG}

echo "[4/6] Building clinic (incremental on clinic:v3.0)..."
docker build -t clinic:${TAG} -f clinic/Dockerfile .
docker tag clinic:${TAG} ${REGISTRY}/${PROJECT}/clinic:${TAG}

if [ -f benchmark/backend/app.py ] && [ -d benchmark/frontend/dist ] && [ "$BUILD_BENCH" != "0" ]; then
  echo "[5/6] Building benchmark-site..."
  docker build -t benchmark-site:${BENCH_TAG} -f benchmark/Dockerfile .
  docker tag benchmark-site:${BENCH_TAG} ${REGISTRY}/${PROJECT}/benchmark-site:${BENCH_TAG}
else
  echo "[5/6] benchmark site sources incomplete - skipped"
fi

echo "[6/6] Pushing images..."
docker push ${REGISTRY}/${PROJECT}/inference-scheduler:${SCHED_TAG}
docker push ${REGISTRY}/${PROJECT}/clinic:${TAG}
docker push ${REGISTRY}/${PROJECT}/hospital:${HOSP_TAG}
docker push ${REGISTRY}/${PROJECT}/medical-server:${MED_TAG}
if docker image inspect benchmark-site:${BENCH_TAG} >/dev/null 2>&1; then
  docker push ${REGISTRY}/${PROJECT}/benchmark-site:${BENCH_TAG}
fi

echo ""
echo "============================================"
echo "v3.3 Build complete!"
echo "  next: bash deploy.sh"
echo "============================================"
