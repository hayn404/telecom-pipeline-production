#!/bin/bash
#===================================================================================
# Telecom Pipeline - Production Deployment
# Step 2: Deploy Services
#===================================================================================

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "============================================"
echo "Deploying Telecom Pipeline - Production"
echo "============================================"
echo ""

cd "$PROJECT_ROOT"

# Create required directories
echo "📁 Creating required directories..."
mkdir -p wldif
mkdir -p data/parquet
mkdir -p dump
echo "✓ Directories created"
echo ""

# Check if images are loaded
echo "🔍 Checking Docker images..."
if ! docker images | grep -q "telecom-prod-clickhouse"; then
    echo "❌ Error: Docker images not loaded!"
    echo "Please run: ./01-load-images.sh first"
    exit 1
fi
echo "✓ Docker images found"
echo ""

# Stop any existing services
echo "🛑 Stopping any existing services..."
docker-compose -f docker-compose-production.yml down 2>/dev/null || true
echo "✓ Existing services stopped"
echo ""

# Start production services
echo "🚀 Starting production services..."
echo ""

echo "Starting ClickHouse..."
docker-compose -f docker-compose-production.yml up -d telecom_prod_clickhouse
echo "✓ ClickHouse starting..."
echo ""

echo "Waiting for ClickHouse to be healthy..."
timeout 60 bash -c 'until docker exec telecom-prod-clickhouse clickhouse-client --query "SELECT 1" &>/dev/null; do sleep 2; done'
echo "✓ ClickHouse is ready!"
echo ""

echo "Starting Streamlit Dashboard..."
docker-compose -f docker-compose-production.yml up -d telecom_prod_streamlit_app
echo "✓ Streamlit Dashboard started"
echo ""

# Show running services
echo "============================================"
echo "✅ Production Services Running!"
echo "============================================"
echo ""

docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep "telecom-prod"
echo ""

echo "============================================"
echo "🌐 Access Points:"
echo "============================================"
echo "📊 Streamlit Dashboard: http://localhost:30014"
echo "🗄️  ClickHouse HTTP:     http://localhost:30012"
echo "📁 Parquet Data Lake:   $PROJECT_ROOT/data/parquet/"
echo ""

echo "============================================"
echo "📝 Next Steps:"
echo "============================================"
echo "1. Place LDIF files in: $PROJECT_ROOT/wldif/"
echo "2. Process data: ./03-process-data.sh"
echo "3. Monitor logs: docker logs -f telecom-prod-streamlit-app"
echo ""
