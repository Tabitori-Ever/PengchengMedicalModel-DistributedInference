#!/bin/bash
#
# Deploy model weights to running pods (temporary, for testing).
# For permanent deployment, copy the weight file to /data/medical-model/weights/
# on each node's host filesystem (node1, node2, node3).
#
# Weight source: z-鹏城医疗模型代码/worker/weights_bigcrop/multi/best_model_fold1.pth
#
set -e

WEIGHT_SRC="z-鹏城医疗模型代码/worker/weights_bigcrop/multi/best_model_fold1.pth"

if [ ! -f "$WEIGHT_SRC" ]; then
    echo "ERROR: weight file not found at $WEIGHT_SRC"
    exit 1
fi

echo "Copying weights to medical-worker pods..."
for pod in $(kubectl get pods -l app=medical-worker -o jsonpath='{.items[*].metadata.name}'); do
    echo "  → $pod"
    kubectl cp "$WEIGHT_SRC" "$pod:/app/weights/best_model_fold1.pth"
done

echo "Copying weights to medical-server pods..."
for pod in $(kubectl get pods -l app=medical-server -o jsonpath='{.items[*].metadata.name}'); do
    echo "  → $pod"
    kubectl cp "$WEIGHT_SRC" "$pod:/app/weights/best_model_fold1.pth"
done

echo "Restarting pods to reload model..."
kubectl rollout restart deployment/medical-worker
kubectl rollout restart deployment/medical-server

echo "Waiting for rollout..."
kubectl rollout status deployment/medical-worker --timeout=120s
kubectl rollout status deployment/medical-server --timeout=120s

echo ""
echo "Verifying model loaded..."
for pod in $(kubectl get pods -l app=medical-worker -o jsonpath='{.items[*].metadata.name}'); do
    echo "  $pod:"
    kubectl exec "$pod" -- python3 -c "import urllib.request, json; print(json.loads(urllib.request.urlopen('http://localhost:8006/health').read()))" 2>/dev/null || echo "    (starting...)"
done
for pod in $(kubectl get pods -l app=medical-server -o jsonpath='{.items[*].metadata.name}'); do
    echo "  $pod:"
    kubectl exec "$pod" -- python3 -c "import urllib.request, json; print(json.loads(urllib.request.urlopen('http://localhost:9001/health').read()))" 2>/dev/null || echo "    (starting...)"
done

echo ""
echo "Done. Run: python test/e2e_test.py"
