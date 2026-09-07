#!/bin/bash
#
# Deploy script v3.0 - data center / hospital / clinic
# Order: RBAC -> dc services -> hospital -> clinic -> medical-server ->
#        scheduler (moved to node3) -> legacy cleanup -> rollouts
#
set -e

echo "============================================"
echo "Deploying v3.0 (云/边/端 · 四类任务)"
echo "============================================"

echo "[Phase 1] RBAC (clinic metrics + scheduler editor + routine Jobs)..."
kubectl apply -f k8s/clinic-rbac.yaml
kubectl apply -f k8s/scheduler-cluster-rbac.yaml

echo "[Phase 2] Infrastructure (redis/monitoring/prediction)..."
kubectl apply -f k8s/redis-deployment.yaml
kubectl apply -f k8s/monitoring-deployment.yaml
kubectl apply -f k8s/prediction-deployment.yaml 2>/dev/null || true

echo "[Phase 3] Data center services (patient-db + compute worker)..."
kubectl apply -f k8s/dc-services.yaml

echo "[Phase 4] Hospital pods (edge)..."
kubectl apply -f k8s/hospital-a-deployment.yaml -f k8s/hospital-a-service.yaml
kubectl apply -f k8s/hospital-b-deployment.yaml -f k8s/hospital-b-service.yaml

echo "[Phase 5] Clinic pods (terminal)..."
kubectl apply -f k8s/clinic-1-deployment.yaml -f k8s/clinic-1-service.yaml
kubectl apply -f k8s/clinic-2-deployment.yaml -f k8s/clinic-2-service.yaml

echo "[Phase 6] Medical server (data center)..."
kubectl apply -f k8s/medical-server-deployment.yaml
kubectl apply -f k8s/medical-server-service.yaml

echo "[Phase 7] Scheduler (moved to node3 = data center)..."
kubectl apply -f k8s/scheduler-deployment.yaml
kubectl apply -f k8s/scheduler-service.yaml

echo "[Phase 8] Waiting for rollouts..."
kubectl rollout status deployment/dc-services --timeout=300s || true
kubectl rollout status deployment/hospital-a --timeout=900s || true
kubectl rollout status deployment/hospital-b --timeout=900s || true
kubectl rollout status deployment/clinic-1 --timeout=300s || true
kubectl rollout status deployment/clinic-2 --timeout=300s || true
kubectl rollout status deployment/scheduler --timeout=300s || true

echo "[Phase 9] Legacy cleanup (part2 / alexnet removed in v3.0)..."
kubectl delete deployment part2 --ignore-not-found || true
kubectl delete service part2-service --ignore-not-found || true
kubectl delete servicemonitor alexnet-part1-monitor alexnet-part2-monitor \
  --ignore-not-found -n monitoring 2>/dev/null || true

echo "[Phase 10] ServiceMonitors..."
kubectl apply -f k8s/service-monitors.yaml 2>/dev/null || echo "  ServiceMonitors skipped"

echo ""
echo "============================================"
echo "v3.0 deployment complete!"
echo "============================================"
kubectl get pods -o wide | grep -E "hospital|clinic|scheduler|dc-services|medical-server|redis" || true
echo ""
echo "Frontend: http://<control-plane>:30080/app  (NodePort 30080)"
