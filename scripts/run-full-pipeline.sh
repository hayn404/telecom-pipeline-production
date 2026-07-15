#!/bin/bash
#===================================================================================
# Telecom Pipeline - Full Pipeline Automation
# Runs the complete pipeline: Deploy -> Process -> Dashboard
#===================================================================================

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_step() {
    echo ""
    echo -e "${BLUE}============================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}============================================${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}$1${NC}"
}

print_warning() {
    echo -e "${YELLOW}$1${NC}"
}

print_error() {
    echo -e "${RED}$1${NC}"
}

echo ""
echo "============================================"
echo "  Telecom Pipeline - Full Automation"
echo "============================================"
echo ""
echo "Project Root: $PROJECT_ROOT"
echo ""

cd "$PROJECT_ROOT"

#===================================================================================
# Step 1: Check Prerequisites
#===================================================================================
print_step "Step 1: Checking Prerequisites"

# Check Docker
if ! command -v docker &> /dev/null; then
    print_error "Docker is not installed!"
    exit 1
fi
print_success "Docker is available"

# Check Docker Compose
if ! command -v sudo docker compose &> /dev/null && ! docker compose version &> /dev/null; then
    print_error "Docker Compose is not installed!"
    exit 1
fi
print_success "Docker Compose is available"

# Check if images exist
if sudo docker images | grep -q "telecom-prod-clickhouse"; then
    print_success "Docker images are loaded"
else
    print_warning "Docker images not found!"
    echo ""
    echo "Please either:"
    echo "  1. Run ./00-build-and-save-images.sh (on build machine)"
    echo "  2. Run ./01-load-images.sh (on production server)"
    echo ""
    read -p "Do you want to build images now? (y/N): " build_choice
    if [[ $build_choice == "y" || $build_choice == "Y" ]]; then
        "$SCRIPT_DIR/00-build-and-save-images.sh"
    else
        exit 1
    fi
fi

# Check for LDIF files
if [ "$(ls -A wldif/*.ldif* 2>/dev/null)" ]; then
    LDIF_COUNT=$(ls wldif/*.ldif* 2>/dev/null | wc -l)
    print_success "Found $LDIF_COUNT LDIF file(s) in wldif/"
else
    print_warning "No LDIF files found in wldif/"
    echo "Please place your LDIF files in: $PROJECT_ROOT/wldif/"
    read -p "Continue anyway? (y/N): " continue_choice
    if [[ $continue_choice != "y" && $continue_choice != "Y" ]]; then
        exit 1
    fi
fi

#===================================================================================
# Step 2: Create Directories
#===================================================================================
print_step "Step 2: Creating Required Directories"

mkdir -p wldif
mkdir -p data/parquet
mkdir -p dump
mkdir -p docker-images

print_success "Directories created"

#===================================================================================
# Step 3: Deploy Services
#===================================================================================
print_step "Step 3: Deploying Services"

# Stop any existing services
echo "Stopping any existing services..."
sudo docker compose -f docker-compose-production.yml down 2>/dev/null || true

# Start ClickHouse
echo ""
echo "Starting ClickHouse..."
sudo docker compose -f docker-compose-production.yml up -d telecom_prod_clickhouse

# Wait for ClickHouse to be healthy
echo "Waiting for ClickHouse to be ready..."
max_wait=60
waited=0
while ! sudo docker exec telecom-prod-clickhouse clickhouse-client --query "SELECT 1" &>/dev/null; do
    sleep 2
    waited=$((waited + 2))
    if [ $waited -ge $max_wait ]; then
        print_error "ClickHouse failed to start within $max_wait seconds"
        exit 1
    fi
    echo "  Waiting... ($waited/$max_wait seconds)"
done
print_success "ClickHouse is ready!"

# Start Streamlit
echo ""
echo "Starting Streamlit Dashboard..."
sudo docker compose -f docker-compose-production.yml up -d telecom_prod_streamlit_app

# Wait for Streamlit to be healthy
echo "Waiting for Streamlit to be ready..."
max_wait=60
waited=0
while ! curl -s http://localhost:30014/_stcore/health &>/dev/null; do
    sleep 2
    waited=$((waited + 2))
    if [ $waited -ge $max_wait ]; then
        print_warning "Streamlit health check timed out (may still be starting)"
        break
    fi
    echo "  Waiting... ($waited/$max_wait seconds)"
done
print_success "Streamlit Dashboard is starting!"

#===================================================================================
# Step 4: Initialize Database Schema (BEFORE processing)
#===================================================================================
print_step "Step 4: Initializing Database Schema"

echo "Creating database tables (MNP, dump_materialized, etc.)..."

# Run init_lakehouse.sql BEFORE Spark processor so MNP tables exist
if [ -f "$PROJECT_ROOT/clickhouse/init_lakehouse.sql" ]; then
    sudo docker exec -i telecom-prod-clickhouse clickhouse-client --multiquery < "$PROJECT_ROOT/clickhouse/init_lakehouse.sql" 2>&1 | head -10 || true
    print_success "Database schema initialized"
else
    print_warning "init_lakehouse.sql not found!"
fi

#===================================================================================
# Step 5: Process Data (if files exist)
#===================================================================================
if [ "$(ls -A wldif/*.ldif* 2>/dev/null)" ]; then
    print_step "Step 5: Processing LDIF Files"

    # Show files to be processed
    echo "Files to process:"
    for f in wldif/*.ldif*; do
        filename=$(basename "$f")
        filesize=$(ls -lh "$f" | awk '{print $5}')

        if [[ $filename =~ ([0-9]{4})([0-9]{2})([0-9]{2}) ]]; then
            file_date="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]}"
        else
            file_date="(no date)"
        fi

        if [[ $filename == MNP* ]] || [[ $filename == mnp* ]]; then
            echo "  [MNP] $filename ($filesize) - Date: $file_date"
        else
            echo "  [UDC] $filename ($filesize) - Date: $file_date"
        fi
    done
    echo ""

    # Run processor
    echo "Running Spark processor..."
    sudo docker compose -f docker-compose-production.yml --profile processor run --rm telecom_prod_spark_processor

    print_success "Data processing complete!"
else
    print_step "Step 5: Skipping Data Processing (no files)"
    print_warning "No LDIF files found. Add files to wldif/ and run:"
    echo "  ./03-process-data.sh"
fi

#===================================================================================
# Step 6: Sync Data to Materialized Table
#===================================================================================
print_step "Step 6: Syncing Data to Optimized Tables"

# Sync data from Parquet to materialized table
if [ -f "$PROJECT_ROOT/clickhouse/sync_incremental.sql" ]; then
    echo ""
    echo "Syncing data to materialized MergeTree table..."
    echo "(This handles both initial load and incremental updates)"
    echo ""
    sudo docker exec -i telecom-prod-clickhouse clickhouse-client --multiquery < "$PROJECT_ROOT/clickhouse/sync_incremental.sql" 2>&1 | grep -E "(status|metric|Sync completed|rows|final_status)" || true
    print_success "Data sync completed"

    # Verify
    echo ""
    echo "Materialized table status:"
    docker exec telecom-prod-clickhouse clickhouse-client --query "
        SELECT
            COUNT(*) as total_records,
            COUNT(DISTINCT MSISDN) as unique_subscribers,
            COUNT(DISTINCT CDRtime) as data_dates
        FROM default.dump_materialized
    " 2>/dev/null || echo "  (materialized table not ready yet)"
else
    print_warning "Sync SQL not found - using Parquet VIEW (slower queries)"
    echo "For optimized performance, run: ./migrate_and_optimize.sh all"
fi

#===================================================================================
# Step 7: Show Status
#===================================================================================
print_step "Step 7: Pipeline Status"

echo "Running Services:"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep "telecom-prod" || echo "  (no services running)"
echo ""

# Show data statistics
echo "Data Statistics:"
echo ""
echo "UDC Records:"
sudo docker exec telecom-prod-clickhouse clickhouse-client --query \
    "SELECT COUNT(*) as total, COUNT(DISTINCT MSISDN) as subscribers FROM default.dump" 2>/dev/null || echo "  (no data)"
echo ""
echo "MNP Records:"
sudo docker exec telecom-prod-clickhouse clickhouse-client --query \
    "SELECT COUNT(*) as total, COUNT(DISTINCT Date) as dates FROM default.MNP_details" 2>/dev/null || echo "  (no data)"

#===================================================================================
# Summary
#===================================================================================
echo ""
echo "============================================"
echo "  Pipeline Ready!"
echo "============================================"
echo ""
echo "Access Points:"
echo "  Dashboard:     http://localhost:30014"
echo "  ClickHouse:    http://localhost:30012"
echo ""
echo "Useful Commands:"
echo "  Process new data:    ./scripts/03-process-data.sh"
echo "  Check status:        ./scripts/status.sh"
echo "  View logs:           ./scripts/check-logs.sh"
echo "  Stop services:       ./scripts/stop-production.sh"
echo ""
echo "Data Directories:"
echo "  LDIF Input:    $PROJECT_ROOT/wldif/"
echo "  Parquet Data:  $PROJECT_ROOT/data/parquet/"
echo ""
