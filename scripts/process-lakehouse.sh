#!/bin/bash
# Lakehouse Architecture Processing Script
# LDIF → Spark → Parquet → ClickHouse → Streamlit

set -e

echo "============================================"
echo "Telecom Pipeline - Lakehouse Architecture"
echo "============================================"
echo ""
echo "Architecture:"
echo "  LDIF Files → Spark → Parquet (Data Lake) ← ClickHouse ← Streamlit"
echo ""

# Configuration
CDR_DATE="${CDR_DATE:-$(date +%Y-%m-%d)}"
COMPOSE_FILE="docker-compose-lakehouse.yml"

echo "Processing Date: $CDR_DATE"
echo ""

# Check if LDIF files exist
if [ ! -d "./wldif" ] || [ -z "$(ls -A ./wldif/*.ldif* 2>/dev/null)" ]; then
    echo "❌ Error: No LDIF files found in ./wldif/"
    echo "   Place your .ldif or .ldif.gz files in the wldif directory"
    exit 1
fi

echo "✓ LDIF files found:"
ls -lh ./wldif/*.ldif* 2>/dev/null || true
echo ""

# Create Parquet directory
mkdir -p ./data/parquet
echo "✓ Parquet directory ready: ./data/parquet"
echo ""

# Step 1: Process LDIF files to Parquet using Spark
echo "============================================"
echo "Step 1: Transform LDIF → Parquet (Spark)"
echo "============================================"
CDR_DATE=$CDR_DATE docker-compose -f $COMPOSE_FILE run --rm telecom_spark_processor

if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Spark processing failed!"
    exit 1
fi

echo ""
echo "✓ Parquet files created successfully!"
echo ""

# Check Parquet files
echo "Parquet files:"
find ./data/parquet -name "*.parquet" -type f | head -10
echo ""

# Step 2: Start ClickHouse (which will query Parquet files)
echo "============================================"
echo "Step 2: Start ClickHouse (Query Layer)"
echo "============================================"

if ! docker ps | grep -q telecom-pipeline-clickhouse; then
    echo "Starting ClickHouse..."
    docker-compose -f $COMPOSE_FILE up -d telecom_clickhouse
    echo "Waiting for ClickHouse to be ready..."
    sleep 10
fi

# Verify ClickHouse is healthy
if ! docker exec telecom-pipeline-clickhouse clickhouse-client --query "SELECT 1" > /dev/null 2>&1; then
    echo "❌ Error: ClickHouse is not responding"
    exit 1
fi

echo "✓ ClickHouse is ready (querying Parquet data lake)"
echo ""

# Step 3: Start Streamlit Dashboard
echo "============================================"
echo "Step 3: Start Streamlit Dashboard"
echo "============================================"

if ! docker ps | grep -q telecom-pipeline-streamlit-app; then
    echo "Starting Streamlit..."
    docker-compose -f $COMPOSE_FILE up -d telecom_streamlit_app
    sleep 5
fi

echo "✓ Streamlit is running"
echo ""

# Show statistics
echo "============================================"
echo "✓ Lakehouse Pipeline Ready!"
echo "============================================"
echo ""

echo "Data Lake Statistics:"
docker exec telecom-pipeline-clickhouse clickhouse-client --query "
SELECT 
    CDRtime,
    COUNT(*) as total_records,
    COUNT(DISTINCT MSISDN) as unique_subscribers,
    COUNT(DISTINCT source_file) as source_files
FROM default.dump
GROUP BY CDRtime
ORDER BY CDRtime DESC
LIMIT 5
FORMAT Pretty"

echo ""
echo "============================================"
echo "Access Points:"
echo "============================================"
echo "📊 Streamlit Dashboard: http://localhost:30014"
echo "🗄️  ClickHouse HTTP:     http://localhost:30012"
echo "📁 Parquet Data Lake:   ./data/parquet/"
echo "============================================"
