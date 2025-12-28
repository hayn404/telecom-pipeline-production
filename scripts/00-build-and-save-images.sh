#!/bin/bash
#===================================================================================
# Telecom Pipeline - Build and Save Docker Images
# Step 0: Build all images and save them for offline transfer to production server
#===================================================================================

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
IMAGES_DIR="$PROJECT_ROOT/docker-images"

echo "============================================"
echo "Building Docker Images for Production"
echo "============================================"
echo ""
echo "Project Root: $PROJECT_ROOT"
echo "Images Output: $IMAGES_DIR"
echo ""

# Create images directory
mkdir -p "$IMAGES_DIR"

cd "$PROJECT_ROOT"

#===================================================================================
# Build ClickHouse Image
#===================================================================================
echo "============================================"
echo "1/3 Building ClickHouse Image..."
echo "============================================"

# Use official ClickHouse image and tag it for production
docker pull clickhouse/clickhouse-server:23.8
docker tag clickhouse/clickhouse-server:23.8 telecom-prod-clickhouse:23.8

echo "   Saving telecom-prod-clickhouse:23.8..."
docker save -o "$IMAGES_DIR/telecom-prod-clickhouse-23.8.tar" telecom-prod-clickhouse:23.8
echo "   Saved: telecom-prod-clickhouse-23.8.tar"
echo ""

#===================================================================================
# Build Spark Processor Image
#===================================================================================
echo "============================================"
echo "2/3 Building Spark Processor Image..."
echo "============================================"

docker build -t telecom-prod-spark-processor:v1.0 -f spark-processor/Dockerfile spark-processor/

echo "   Saving telecom-prod-spark-processor:v1.0..."
docker save -o "$IMAGES_DIR/telecom-prod-spark-processor-v1.0.tar" telecom-prod-spark-processor:v1.0
echo "   Saved: telecom-prod-spark-processor-v1.0.tar"
echo ""

#===================================================================================
# Build Streamlit Dashboard Image
#===================================================================================
echo "============================================"
echo "3/3 Building Streamlit Dashboard Image..."
echo "============================================"

docker build -t telecom-prod-streamlit-app:v1.0 -f streamlit-app/Dockerfile streamlit-app/

echo "   Saving telecom-prod-streamlit-app:v1.0..."
docker save -o "$IMAGES_DIR/telecom-prod-streamlit-app-v1.0.tar" telecom-prod-streamlit-app:v1.0
echo "   Saved: telecom-prod-streamlit-app-v1.0.tar"
echo ""

#===================================================================================
# Summary
#===================================================================================
echo "============================================"
echo "All Images Built and Saved!"
echo "============================================"
echo ""

# Show image sizes
echo "Docker Images (in docker-images/):"
ls -lh "$IMAGES_DIR"/*.tar | awk '{print "  " $NF " (" $5 ")"}'
echo ""

TOTAL_SIZE=$(du -sh "$IMAGES_DIR" | cut -f1)
echo "Total size: $TOTAL_SIZE"
echo ""

# Show built images
echo "Loaded Docker Images:"
docker images | grep "telecom-prod" | awk '{print "  " $1 ":" $2 " (" $7 $8 ")"}'
echo ""

echo "============================================"
echo "Transfer Instructions"
echo "============================================"
echo ""
echo "1. Copy the entire 'production-deployment' folder to the server:"
echo "   scp -r production-deployment/ user@server:/path/to/"
echo ""
echo "   Or copy just the docker-images folder:"
echo "   scp -r $IMAGES_DIR/ user@server:/path/to/production-deployment/"
echo ""
echo "2. On the server, run:"
echo "   cd /path/to/production-deployment/scripts"
echo "   ./01-load-images.sh"
echo "   ./02-deploy-production.sh"
echo ""
echo "============================================"
