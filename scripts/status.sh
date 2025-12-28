#!/bin/bash
#===================================================================================
# Telecom Pipeline - Check Production Status
#===================================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "============================================"
echo "Production Services Status"
echo "============================================"
echo ""

cd "$PROJECT_ROOT"

# Check running containers
echo "🔍 Running Services:"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "NAME|telecom-prod"
echo ""

# Check ClickHouse health
if docker ps | grep -q "telecom-prod-clickhouse"; then
    echo "🗄️  ClickHouse Status:"
    if docker exec telecom-prod-clickhouse clickhouse-client --query "SELECT 1" &>/dev/null; then
        echo "  ✅ Healthy"

        # Quick stats
        echo ""
        echo "📊 UDC Statistics:"
        docker exec telecom-prod-clickhouse clickhouse-client --query "
        SELECT
            COUNT(*) as total_records,
            COUNT(DISTINCT MSISDN) as unique_subscribers,
            COUNT(DISTINCT CDRtime) as data_dates
        FROM default.dump
        FORMAT Pretty
        " 2>/dev/null || echo "  No UDC data processed yet"

        echo ""
        echo "📱 MNP Statistics:"
        docker exec telecom-prod-clickhouse clickhouse-client --query "
        SELECT
            COUNT(*) as total_records,
            COUNT(DISTINCT Date) as data_dates
        FROM default.MNP_details
        FORMAT Pretty
        " 2>/dev/null || echo "  No MNP data processed yet"
    else
        echo "  ❌ Not responding"
    fi
else
    echo "🗄️  ClickHouse: Not running"
fi
echo ""

# Check Streamlit health
if docker ps | grep -q "telecom-prod-streamlit-app"; then
    echo "📊 Streamlit Dashboard:"
    if curl -sf http://localhost:30014/_stcore/health &>/dev/null; then
        echo "  ✅ Healthy - http://localhost:30014"
    else
        echo "  ⚠️  Starting up..."
    fi
else
    echo "📊 Streamlit Dashboard: Not running"
fi
echo ""

# Check data
echo "📁 Data Status:"
if [ -d "data/parquet/telecom_data" ]; then
    PARQUET_SIZE=$(du -sh data/parquet/telecom_data 2>/dev/null | cut -f1)
    PARQUET_FILES=$(find data/parquet/telecom_data -name "*.parquet" 2>/dev/null | wc -l)
    echo "  Parquet files: $PARQUET_FILES files ($PARQUET_SIZE)"
else
    echo "  No processed data yet"
fi

if [ -d "wldif" ] && [ "$(ls -A wldif/*.ldif* 2>/dev/null)" ]; then
    LDIF_COUNT=$(ls -1 wldif/*.ldif* 2>/dev/null | wc -l)
    echo "  LDIF files: $LDIF_COUNT files ready"
else
    echo "  No LDIF files in wldif/"
fi
echo ""

# Check disk usage
echo "💾 Disk Usage:"
du -sh data/ 2>/dev/null | awk '{print "  Data directory: " $1}' || echo "  Data directory: 0"
docker system df --format "table {{.Type}}\t{{.TotalCount}}\t{{.Size}}" | grep -E "TYPE|Images|Containers|Volumes"
echo ""
