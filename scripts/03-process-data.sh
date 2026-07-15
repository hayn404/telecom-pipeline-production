#!/bin/bash
#===================================================================================
# Telecom Pipeline - Production Data Processing
# Step 3: Process LDIF Files (UDC_Details to Parquet, MNP to ClickHouse)
#===================================================================================

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Get CDR date from argument or use today (fallback only - dates are extracted from filenames)
CDR_DATE="${1:-$(date +%Y-%m-%d)}"

# Export for docker-compose
export CDR_DATE

echo "============================================"
echo "Processing LDIF Files - Production"
echo "============================================"
echo ""
echo "Fallback CDR Date: $CDR_DATE"
echo "(Note: Dates are extracted from filenames when available)"
echo ""

cd "$PROJECT_ROOT"

# Check if LDIF files exist
if [ ! "$(ls -A wldif/*.ldif* 2>/dev/null)" ]; then
    echo "Error: No LDIF files found in wldif/"
    echo ""
    echo "Please place LDIF files (.ldif or .ldif.gz) in:"
    echo "  $PROJECT_ROOT/wldif/"
    exit 1
fi

# Categorize files
echo "LDIF files found:"
echo ""
UDC_COUNT=0
MNP_COUNT=0

for f in wldif/*.ldif*; do
    filename=$(basename "$f")
    filesize=$(ls -lh "$f" | awk '{print $5}')

    # Extract date from filename if present
    if [[ $filename =~ ([0-9]{4})([0-9]{2})([0-9]{2}) ]]; then
        file_date="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]}"
    else
        file_date="(no date in filename)"
    fi

    if [[ $filename == MNP* ]] || [[ $filename == mnp* ]]; then
        echo "  [MNP] $filename ($filesize) - Date: $file_date"
        ((MNP_COUNT++))
    else
        echo "  [UDC] $filename ($filesize) - Date: $file_date"
        ((UDC_COUNT++))
    fi
done
echo ""
echo "Summary: $UDC_COUNT UDC file(s), $MNP_COUNT MNP file(s)"
echo ""

# Run Spark processor
echo "============================================"
echo "Starting Data Processing..."
echo "============================================"
echo ""
echo "Processing:"
echo "  - UDC_Details files -> Parquet (Data Lake)"
echo "  - MNP files -> ClickHouse (Direct Insert)"
echo ""
echo "This may take several minutes depending on data size..."
echo ""

# IMPORTANT: Initialize database tables BEFORE processing
# MNP tables must exist before Spark can insert MNP data
echo "============================================"
echo "Step 1: Initializing Database Tables"
echo "============================================"
if [ -f "clickhouse/init_lakehouse.sql" ]; then
    echo "Creating tables (MNP, dump_materialized, etc.)..."
    sudo docker exec -i telecom-prod-clickhouse clickhouse-client --multiquery < clickhouse/init_lakehouse.sql 2>&1 | head -5 || true
    echo "Tables ready!"
else
    echo "Warning: init_lakehouse.sql not found!"
fi
echo ""

# Now run Spark processor (UDC -> Parquet, MNP -> ClickHouse)
echo "============================================"
echo "Step 2: Running Spark Processor"
echo "============================================"
sudo docker compose -f docker-compose-production.yml --profile processor run --rm telecom_prod_spark_processor

echo ""
echo "============================================"
echo "Data Processing Complete!"
echo "============================================"
echo ""

# Sync UDC data to materialized table
echo "============================================"
echo "Step 3: Syncing UDC Data to Materialized Table"
echo "============================================"

# Sync data from Parquet to materialized table
if [ -f "clickhouse/sync_incremental.sql" ]; then
    echo ""
    echo "Syncing data from Parquet to optimized MergeTree table..."
    sudo docker exec -i telecom-prod-clickhouse clickhouse-client --multiquery < clickhouse/sync_incremental.sql 2>&1 | grep -E "(status|metric|Sync completed|rows|final_status)" || true
    echo "Sync complete!"
else
    echo "Sync SQL not found - data available via Parquet VIEW"
fi
echo ""

# Show Parquet files
if [ -d "data/parquet/telecom_data" ]; then
    echo "Parquet files created:"
    find data/parquet/telecom_data -name "*.parquet" 2>/dev/null | head -10
    echo ""

    # Show size
    PARQUET_SIZE=$(du -sh data/parquet/telecom_data 2>/dev/null | cut -f1)
    echo "Total Parquet data: $PARQUET_SIZE"
    echo ""
fi

# UDC Statistics
echo "============================================"
echo "UDC Data Statistics"
echo "============================================"
docker exec telecom-prod-clickhouse clickhouse-client --query "
SELECT
    'Total Records' as Metric,
    toString(COUNT(*)) as Value
FROM default.dump
UNION ALL
SELECT
    'Unique Subscribers' as Metric,
    toString(COUNT(DISTINCT MSISDN)) as Value
FROM default.dump
UNION ALL
SELECT
    'Available Dates' as Metric,
    toString(COUNT(DISTINCT CDRtime)) as Value
FROM default.dump
UNION ALL
SELECT
    'VoLTE Users' as Metric,
    toString(COUNT(DISTINCT MSISDN)) as Value
FROM default.dump
WHERE TICK = '215'
UNION ALL
SELECT
    'VoWiFi Users' as Metric,
    toString(COUNT(DISTINCT MSISDN)) as Value
FROM default.dump
WHERE EpsAccessRestriction = '0'
FORMAT Pretty
" 2>/dev/null || echo "  (No UDC data or query failed)"

echo ""

# MNP Statistics
echo "============================================"
echo "MNP Data Statistics"
echo "============================================"
sudo docker exec telecom-prod-clickhouse clickhouse-client --query "
SELECT
    'Total MNP Records' as Metric,
    toString(COUNT(*)) as Value
FROM default.MNP_details
UNION ALL
SELECT
    'Available Dates' as Metric,
    toString(COUNT(DISTINCT Date)) as Value
FROM default.MNP_details
UNION ALL
SELECT
    'Ported In (QPM)' as Metric,
    toString(SUM(CASE WHEN NPREFIX = 'QPM' THEN 1 ELSE 0 END)) as Value
FROM default.MNP_details
UNION ALL
SELECT
    'Ported to Vodafone (QPI)' as Metric,
    toString(SUM(CASE WHEN NPREFIX = 'QPI' THEN 1 ELSE 0 END)) as Value
FROM default.MNP_details
UNION ALL
SELECT
    'Ported to Orange (QPE)' as Metric,
    toString(SUM(CASE WHEN NPREFIX = 'QPE' THEN 1 ELSE 0 END)) as Value
FROM default.MNP_details
UNION ALL
SELECT
    'Ported to WE (QPQ)' as Metric,
    toString(SUM(CASE WHEN NPREFIX = 'QPQ' THEN 1 ELSE 0 END)) as Value
FROM default.MNP_details
FORMAT Pretty
" 2>/dev/null || echo "  (No MNP data or query failed)"

echo ""

# Show available data dates
echo "============================================"
echo "Available Data Dates"
echo "============================================"
echo ""
echo "UDC Data Dates:"
sudo docker exec telecom-prod-clickhouse clickhouse-client --query "
SELECT CDRtime as Date, COUNT(*) as Records
FROM default.dump
GROUP BY CDRtime
ORDER BY CDRtime DESC
LIMIT 10
" 2>/dev/null || echo "  (No UDC data)"

echo ""
echo "MNP Data Dates:"
sudo docker exec telecom-prod-clickhouse clickhouse-client --query "
SELECT Date, COUNT(*) as Records
FROM default.MNP_details
GROUP BY Date
ORDER BY Date DESC
LIMIT 10
" 2>/dev/null || echo "  (No MNP data)"

echo ""
echo "============================================"
echo "View dashboard: http://localhost:30014"
echo "============================================"
echo ""
