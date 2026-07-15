#!/bin/bash
#===================================================================================
# Telecom Pipeline - Production Deployment
# Step 1: Load Docker Images (Offline)
#===================================================================================

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
IMAGES_DIR="$PROJECT_ROOT/docker-images"

echo "============================================"
echo "Loading Docker Images - Production"
echo "============================================"
echo ""

# Check if images directory exists
if [ ! -d "$IMAGES_DIR" ]; then
    echo "❌ Error: Docker images directory not found: $IMAGES_DIR"
    exit 1
fi

# Load ClickHouse
echo "📦 Loading ClickHouse image..."
if [ -f "$IMAGES_DIR/telecom-prod-clickhouse.tar" ]; then
    sudo docker load -i "$IMAGES_DIR/telecom-prod-clickhouse.tar"
    echo "✓ ClickHouse loaded (telecom-prod-clickhouse:23.8)"
else
    echo "❌ Error: ClickHouse image not found"
    exit 1
fi
echo ""

# Load Spark Processor
echo "📦 Loading Spark Processor image..."
if [ -f "$IMAGES_DIR/telecom-prod-spark-processor.tar" ]; then
    sudo docker load -i "$IMAGES_DIR/telecom-prod-spark-processor-v1.0.tar"
    echo "✓ Spark Processor loaded (telecom-prod-spark-processor:v1.0)"
else
    echo "❌ Error: Spark Processor image not found"
    exit 1
fi
echo ""

# Load Streamlit
echo "📦 Loading Streamlit Dashboard image..."
if [ -f "$IMAGES_DIR/telecom-prod-streamlit-app.tar" ]; then
    sudo docker load -i "$IMAGES_DIR/telecom-prod-streamlit-app.tar"
    echo "✓ Streamlit Dashboard loaded (telecom-prod-streamlit-app:v1.0)"
else
    echo "❌ Error: Streamlit image not found"
    exit 1
fi
echo ""

echo "============================================"
echo "✅ All Docker Images Loaded Successfully!"
echo "============================================"
echo ""

# Verify loaded images
echo "Loaded images:"
sudo docker images | grep "telecom-prod"
echo ""

echo "Next step: Run ./02-deploy-production.sh"
