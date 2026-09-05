#!/bin/bash
#
# Deploy script v2.0 - Hospital / Clinic Pod Architecture
# Order: RBAC -> infrastructure -> hospital pods -> clinic pods ->
#        cloud services -> scheduler -> legacy cleanup
#
set -e

echo "============================================"
echo "Deploying Multi-Model Edge/Cloud Platform v2.0"
echo "  (hospital + clinic pod architecture)"
echo "============================================"

# Phase 0: Node labels (verify first)
echo ""
echo "[Phase 0] Verify node labels:"
echo "  kubectl get nodes --show-labels | grep -E 'role|hospital|zone'"
echo "  Expected: node1 role=edge; node2 role=edge; node3 role=cloud"
echo ""

# Phase 1: RBAC (clinic metrics reader + scheduler cluster editor)
echo "[Phase 1] Deploying RBAC..."
kubectl apply -f k8s/clinic-rbac.yaml
kubectl apply -f k8s/scheduler-cluster-rbac.yaml
echo "  RBAC deployed"

# Phase 2: Infrastructure (unchanged from v1.x)
echo "[Phase 2] Deploying infrastructure..."
kubectl apply -f k8s/redis-deployment.yaml
kubectl apply -f k8s/monitoring-deployment.yaml
kubectl apply -f k8s/prediction-deployment.yaml
echo "  Infrastructure deployed"

# Phase 3: Hospital pods (worker + alexnet part1 merged)
echo "[Phase 3] Deploying hospital pods..."
kubectl apply -f k8s/hospital-a-deployment.yaml
kubectl apply -f k8s/hospital-a-service.yaml
kubectl apply -f k8s/hospital-b-deployment.yaml
kubectl apply -f k8s/hospital-b-service.yaml
echo "  Hospital pods deployed"

# Phase 4: Clinic pods (memory monitor)
echo "[Phase 4] Deploying clinic pods..."
kubectl apply -f k8s/clinic-1-deployment.yaml
kubectl apply -f k8s/clinic-1-service.yaml
kubectl apply -f k8s/clinic-2-deployment.yaml
kubectl apply -f k8s/clinic-2-service.yaml
echo "  Clinic pods deployed"

# Phase 5: Cloud-side model services (medical-server / part2, unchanged)
echo "[Phase 5] Deploying cloud-side services..."
kubectl apply -f k8s/medical-server-deployment.yaml
kubectl apply -f k8s/medical-server-service.yaml
kubectl apply -f k8s/part2-deployment.yaml
kubectl apply -f k8s/part2-service.yaml
echo "  Cloud services deployed"

# Phase 6: Scheduler (v2.0)
echo "[Phase 6] Deploying scheduler..."
kubectl apply -f k8s/scheduler-deployment.yaml
kubectl apply -f k8s/scheduler-service.yaml
echo "  Scheduler deployed"

# Phase 7: Wait for hospital + clinic rollouts
echo "[Phase 7] Waiting for rollouts..."
kubectl rollout status deployment/hospital-a --timeout=600s || true
kubectl rollout status deployment/hospital-b --timeout=600s || true
kubectl rollout status deployment/clinic-1 --timeout=300s || true
kubectl rollout status deployment/clinic-2 --timeout=300s || true
kubectl rollout status deployment/scheduler --timeout=300s || true

# Phase 8: Legacy cleanup (part1 / medical-worker replaced by hospital pods)
echo "[Phase 8] Cleaning up legacy deployments (part1 / medical-worker)..."
kubectl delete deployment part1 medical-worker 2>/dev/null || echo "  (already removed)"
kubectl delete service part1-service medical-worker-service 2>/dev/null || echo "  (already removed)"

echo ""
echo "============================================"
echo "Deployment complete!"
echo "============================================"
echo ""
echo "Checking status:"
kubectl get pods -o wide | grep -E "hospital|clinic|scheduler|medical-server|part2|redis|monitoring|prediction" || true
echo ""
kubectl get svc | grep -E "hospital|clinic|scheduler|medical-server|part2|redis" || true
echo ""
echo "Access scheduler (frontend) at: http://<control-plane>:30080/app"
echo ""
echo "Run tests:"
echo "  python test/submit_task.py --hospital hospital-a --model medical --priority 9"
echo "  python test/query_result.py <task_id> --wait"
echo "  python test/e2e_test.py --model clinic"
