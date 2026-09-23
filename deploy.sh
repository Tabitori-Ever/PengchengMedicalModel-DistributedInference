#!/bin/bash
#
# Deploy script v3.2 - data center / hospital / clinic + benchmark-site
# Order: RBAC -> dc services -> hospital -> clinic (local execution) ->
#        medical-server -> scheduler (mode/degradation) -> benchmark-site ->
#        rollouts
#
# 已清理（不再部署）：part1 / part2 / medical-worker / prediction（AlexNet 时代遗留）
#
# Image tags (v3.3): hospital v3.6, medical-server v1.1, scheduler v3.3.5,
#                    clinic v3.3, benchmark-site v1.2
#
set -e

echo "============================================"
echo "Deploying v3.3 (云/边/端 · 四类任务 + 执行模式/降级 + 套件对比站点)"
echo "============================================"

echo "[Phase 1] RBAC (clinic metrics + scheduler editor + routine Jobs)..."
kubectl apply -f k8s/clinic-rbac.yaml
kubectl apply -f k8s/scheduler-cluster-rbac.yaml

echo "[Phase 2] Infrastructure (redis/monitoring)..."
kubectl apply -f k8s/redis-deployment.yaml
kubectl apply -f k8s/monitoring-deployment.yaml

echo "[Phase 3] Data center services (patient-db + compute worker)..."
kubectl apply -f k8s/dc-services.yaml

echo "[Phase 4] Hospital pods (edge)..."
kubectl apply -f k8s/hospital-a-deployment.yaml -f k8s/hospital-a-service.yaml
kubectl apply -f k8s/hospital-b-deployment.yaml -f k8s/hospital-b-service.yaml

echo "[Phase 5] Clinic pods (terminal, 4 entities)..."
for n in 1 2 3 4; do
  kubectl apply -f k8s/clinic-$n-deployment.yaml -f k8s/clinic-$n-service.yaml
done

# v3.3 协议顺序：medical-server 必须先于 hospital/scheduler 更新
# （新调度器向医院索取紧凑特征并原样转发；旧 server 会 422，调度器虽已内置回退）
echo "[Phase 6] Medical server (data center, compact-feature protocol)..."
kubectl apply -f k8s/medical-server-deployment.yaml
kubectl rollout status deployment/medical-server --timeout=300s || true
kubectl apply -f k8s/medical-server-service.yaml

echo "[Phase 7] Scheduler (moved to node3 = data center)..."
kubectl apply -f k8s/scheduler-deployment.yaml
kubectl apply -f k8s/scheduler-service.yaml

echo "[Phase 7b] Benchmark site (测试下发 / 性能对比, NodePort 30082)..."
kubectl apply -f k8s/benchmark-site.yaml

echo "[Phase 8] Waiting for rollouts..."
kubectl rollout status deployment/dc-services --timeout=300s || true
kubectl rollout status deployment/hospital-a --timeout=900s || true
kubectl rollout status deployment/hospital-b --timeout=900s || true
for n in 1 2 3 4; do
  kubectl rollout status deployment/clinic-$n --timeout=300s || true
done
kubectl rollout status deployment/scheduler --timeout=300s || true
kubectl rollout status deployment/benchmark-site --timeout=300s || true

echo "[Phase 9] ServiceMonitors..."
kubectl apply -f k8s/service-monitors.yaml 2>/dev/null || echo "  ServiceMonitors skipped"

echo ""
echo "============================================"
echo "v3.0 deployment complete!"
echo "============================================"
kubectl get pods -o wide | grep -E "hospital|clinic|scheduler|dc-services|medical-server|redis|benchmark" || true
echo ""
echo "平台前端:   http://<node-ip>:30080/app       (NodePort 30080)"
echo "测试/对比站点: http://<node-ip>:30082/        (NodePort 30082)"
echo ""
echo "验证:"
echo "  python test/local_exec_smoke.py --hospital http://<node-ip>:<np> ..."
echo "  python test/scheduler_mode_test.py --base http://<node-ip>:30080 --expect-auto-collab"
echo "  python test/degradation_drill.py --with-kubectl"
