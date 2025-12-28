#!/bin/bash
#===================================================================================
# Telecom Pipeline - Process Large LDIF Files One at a Time
# Use this script when you have multiple large LDIF files (>5 GB each)
#===================================================================================

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Get CDR date from argument or use today
CDR_DATE="${1:-$(date +%Y-%m-%d)}"

# Export for docker-compose
export CDR_DATE

echo "============================================"
echo "Processing Large LDIF Files - One at a Time"
echo "============================================"
echo ""
echo "CDR Date: $CDR_DATE"
echo ""

cd "$PROJECT_ROOT"

# Check if LDIF files exist
if [ ! "$(ls -A wldif/*.ldif* 2>/dev/null)" ]; then
    echo "❌ Error: No LDIF files found in wldif/"
    echo ""
    echo "Please place LDIF files (.ldif or .ldif.gz) in:"
    echo "  $PROJECT_ROOT/wldif/"
    exit 1
fi

# Create temp directory
mkdir -p wldif_temp

# Get list of all LDIF files
LDIF_FILES=(wldif/*.ldif*)
TOTAL_FILES=${#LDIF_FILES[@]}

echo "Found $TOTAL_FILES LDIF file(s) to process"
echo ""

# Process each file individually
FILE_NUM=1
for ldif_file in "${LDIF_FILES[@]}"; do
    filename=$(basename "$ldif_file")
    filesize=$(du -h "$ldif_file" | cut -f1)

    echo "============================================"
    echo "Processing file $FILE_NUM of $TOTAL_FILES"
    echo "============================================"
    echo "File: $filename"
    echo "Size: $filesize"
    echo ""

    # Move all other files to temp
    for other_file in "${LDIF_FILES[@]}"; do
        if [ "$other_file" != "$ldif_file" ]; then
            other_filename=$(basename "$other_file")
            if [ -f "wldif/$other_filename" ]; then
                mv "wldif/$other_filename" "wldif_temp/" 2>/dev/null || true
            fi
        fi
    done

    # Process this file only
    echo "🔄 Starting Spark processor..."
    echo "This may take 20-40 minutes for large files..."
    echo ""

    START_TIME=$(date +%s)

    docker-compose -f docker-compose-production.yml run --rm telecom_prod_spark_processor

    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    MINUTES=$((DURATION / 60))
    SECONDS=$((DURATION % 60))

    echo ""
    echo "✅ File processed successfully!"
    echo "Processing time: ${MINUTES}m ${SECONDS}s"
    echo ""

    # Move files back for next iteration
    if [ -d "wldif_temp" ] && [ "$(ls -A wldif_temp)" ]; then
        mv wldif_temp/* wldif/ 2>/dev/null || true
    fi

    FILE_NUM=$((FILE_NUM + 1))
done

# Cleanup temp directory
rmdir wldif_temp 2>/dev/null || true

echo ""
echo "============================================"
echo "✅ All Files Processed!"
echo "============================================"
echo ""

# Show Parquet files
if [ -d "data/parquet/telecom_data" ]; then
    echo "📁 Parquet files created:"
    find data/parquet/telecom_data -name "*.parquet" | head -10
    echo ""

    # Show size
    PARQUET_SIZE=$(du -sh data/parquet/telecom_data | cut -f1)
    echo "Total Parquet data: $PARQUET_SIZE"
    echo ""
fi

# Query statistics
echo "📊 Data Statistics:"
docker exec telecom-prod-clickhouse clickhouse-client --query "
SELECT
    'Total Records:' as metric,
    toString(COUNT(*)) as value
FROM default.dump
UNION ALL
SELECT
    'Unique Subscribers:' as metric,
    toString(COUNT(DISTINCT MSISDN)) as value
FROM default.dump
UNION ALL
SELECT
    'VoLTE Users:' as metric,
    toString(COUNT(DISTINCT MSISDN)) as value
FROM default.dump
WHERE TICK = '215'
UNION ALL
SELECT
    'VoWiFi Users:' as metric,
    toString(COUNT(DISTINCT MSISDN)) as value
FROM default.dump
WHERE EpsAccessRestriction = '0'
FORMAT Pretty
"

echo ""
echo "🌐 View dashboard: http://localhost:30014"
echo ""
