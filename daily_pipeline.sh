#!/bin/bash
#===============================================================================
# Telecom Pipeline - Daily Automated ETL Script
# Project: /mnt/raid5/production-deployment
# Schedule: Daily at 3:00 PM
#
# Data Flow:
#   - UDC Data: Daily REPLACE (delete old, insert new)
#   - MNP Data: Daily ACCUMULATE (append new data)
#===============================================================================

set -e  # Exit on any error

#-------------------------------------------------------------------------------
# CONFIGURATION
#-------------------------------------------------------------------------------
PROJECT_DIR="/mnt/raid5/production-deployment"
LDIF_DIR="${PROJECT_DIR}/wldif"
PARQUET_DIR="${PROJECT_DIR}/data/parquet"
LOG_DIR="${PROJECT_DIR}/logs"
DOCKER_COMPOSE_FILE="${PROJECT_DIR}/docker-compose-production.yml"

# Remote Server Configuration
REMOTE_USER="coreftp"
REMOTE_HOST="10.74.145.213"
REMOTE_PASSWORD="123456789"
REMOTE_PATH="/home/coreftp"

# Date formats
TODAY=$(date +%Y%m%d)
TODAY_DASH=$(date +%Y-%m-%d)

# ClickHouse container
CLICKHOUSE_CONTAINER="telecom-prod-clickhouse"

# Retention settings
LDIF_RETENTION_DAYS=5
PARQUET_RETENTION_DAYS=30
MNP_RETENTION_DAYS=365

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

#-------------------------------------------------------------------------------
# LOGGING FUNCTIONS
#-------------------------------------------------------------------------------
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/pipeline_${TODAY_DASH}.log"

log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "${LOG_FILE}"
}

log_step() {
    echo "" | tee -a "${LOG_FILE}"
    echo -e "${BLUE}==========================================${NC}" | tee -a "${LOG_FILE}"
    echo -e "${BLUE}$1${NC}" | tee -a "${LOG_FILE}"
    echo -e "${BLUE}==========================================${NC}" | tee -a "${LOG_FILE}"
    echo "" | tee -a "${LOG_FILE}"
}

log_success() {
    echo -e "${GREEN}✓ $1${NC}" | tee -a "${LOG_FILE}"
}

log_warning() {
    echo -e "${YELLOW}⚠ $1${NC}" | tee -a "${LOG_FILE}"
}

log_error() {
    echo -e "${RED}✗ ERROR: $1${NC}" | tee -a "${LOG_FILE}" >&2
}

#-------------------------------------------------------------------------------
# STEP 1: CHECK PREREQUISITES
#-------------------------------------------------------------------------------
check_prerequisites() {
    log_step "STEP 1: Checking Prerequisites"

    # Check Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed!"
        exit 1
    fi
    log_success "Docker is available"

    # Check Docker Compose
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        log_error "Docker Compose is not installed!"
        exit 1
    fi
    log_success "Docker Compose is available"

    # Check sshpass for SCP
    if ! command -v sshpass &> /dev/null; then
        log_error "sshpass is not installed! Install with: apt-get install sshpass"
        exit 1
    fi
    log_success "sshpass is available"

    # Check if images exist
    if docker images | grep -q "telecom-prod-clickhouse"; then
        log_success "Docker images are loaded"
    else
        log_error "Docker images not found! Run ./scripts/01-load-images.sh first"
        exit 1
    fi
}

#-------------------------------------------------------------------------------
# STEP 2: CREATE DIRECTORIES
#-------------------------------------------------------------------------------
create_directories() {
    log_step "STEP 2: Creating Required Directories"

    mkdir -p "${LDIF_DIR}"
    mkdir -p "${PARQUET_DIR}"
    mkdir -p "${PROJECT_DIR}/dump"
    mkdir -p "${LOG_DIR}"

    log_success "Directories created"
}

#-------------------------------------------------------------------------------
# STEP 3: DOWNLOAD FILES FROM REMOTE SERVER
#-------------------------------------------------------------------------------
download_files() {
    log_step "STEP 3: Downloading Files from Remote Server"

    # Download MNP files (Node 31)
    log "Downloading MNP files from Node 31..."
    sshpass -p "${REMOTE_PASSWORD}" scp -o StrictHostKeyChecking=no \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/MNP_31_${TODAY}*" \
        "${LDIF_DIR}/" 2>/dev/null && log_success "MNP_31 files downloaded" || log_warning "No MNP_31 files found for today"

    # Download MNP files (Node 32)
    log "Downloading MNP files from Node 32..."
    sshpass -p "${REMOTE_PASSWORD}" scp -o StrictHostKeyChecking=no \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/MNP_32_${TODAY}*" \
        "${LDIF_DIR}/" 2>/dev/null && log_success "MNP_32 files downloaded" || log_warning "No MNP_32 files found for today"

    # Download UDC_Details files (Node 31)
    log "Downloading UDC_Details from Node 31..."
    sshpass -p "${REMOTE_PASSWORD}" scp -o StrictHostKeyChecking=no \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/UDC_Details_31_${TODAY}*" \
        "${LDIF_DIR}/" 2>/dev/null && log_success "UDC_Details_31 files downloaded" || log_warning "No UDC_Details_31 files found"

    # Download UDC_Details files (Node 32)
    log "Downloading UDC_Details from Node 32..."
    sshpass -p "${REMOTE_PASSWORD}" scp -o StrictHostKeyChecking=no \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/UDC_Details_32_${TODAY}*" \
        "${LDIF_DIR}/" 2>/dev/null && log_success "UDC_Details_32 files downloaded" || log_warning "No UDC_Details_32 files found"

    # List files for processing
    log "Files ready for Spark processing:"
    ls -lh "${LDIF_DIR}"/*${TODAY}* 2>/dev/null | tee -a "${LOG_FILE}" || log_warning "No files found for today"
}

#-------------------------------------------------------------------------------
# STEP 4: DEPLOY SERVICES
#-------------------------------------------------------------------------------
deploy_services() {
    log_step "STEP 4: Deploying Services"

    cd "${PROJECT_DIR}"

    # Check if ClickHouse is running
    if docker ps | grep -q "${CLICKHOUSE_CONTAINER}"; then
        log "ClickHouse is already running, checking health..."
    else
        log "Starting ClickHouse..."
        docker-compose -f "${DOCKER_COMPOSE_FILE}" up -d telecom_prod_clickhouse
    fi

    # Wait for ClickHouse to be healthy
    log "Waiting for ClickHouse to be ready..."
    max_wait=60
    waited=0
    while ! docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query "SELECT 1" &>/dev/null; do
        sleep 2
        waited=$((waited + 2))
        if [ $waited -ge $max_wait ]; then
            log_error "ClickHouse failed to start within $max_wait seconds"
            exit 1
        fi
        echo "  Waiting... ($waited/$max_wait seconds)"
    done
    log_success "ClickHouse is ready!"

    # Check if Streamlit is running
    if docker ps | grep -q "telecom-prod-streamlit-app"; then
        log_success "Streamlit Dashboard is already running"
    else
        log "Starting Streamlit Dashboard..."
        docker-compose -f "${DOCKER_COMPOSE_FILE}" up -d telecom_prod_streamlit_app
        log_success "Streamlit Dashboard started"
    fi
}

#-------------------------------------------------------------------------------
# STEP 5: INITIALIZE DATABASE SCHEMA
#-------------------------------------------------------------------------------
init_database_schema() {
    log_step "STEP 5: Initializing Database Schema"

    if [ -f "${PROJECT_DIR}/clickhouse/init_lakehouse.sql" ]; then
        log "Running init_lakehouse.sql..."
        docker exec -i ${CLICKHOUSE_CONTAINER} clickhouse-client --multiquery \
            < "${PROJECT_DIR}/clickhouse/init_lakehouse.sql" 2>&1 | head -10 || true
        log_success "Database schema initialized"
    else
        log_warning "init_lakehouse.sql not found - schema may already exist"
    fi
}

#-------------------------------------------------------------------------------
# STEP 6: DELETE OLD UDC DATA (Replace Strategy)
#-------------------------------------------------------------------------------
delete_old_udc_data() {
    log_step "STEP 6: Deleting Old UDC Data (Replace Strategy)"

    # Extract unique dates from UDC LDIF files to be processed
    log "Detecting dates from UDC files..."

    # Use associative array to track unique dates (avoid duplicates from node 31/32)
    declare -A processed_dates

    for f in ${LDIF_DIR}/UDC_Details_*; do
        if [ -f "$f" ]; then
            filename=$(basename "$f")
            # Extract date from filename (format: UDC_Details_31_YYYYMMDD or UDC_Details_32_YYYYMMDD)
            if [[ $filename =~ ([0-9]{4})([0-9]{2})([0-9]{2}) ]]; then
                year_val="${BASH_REMATCH[1]}"
                month_val="${BASH_REMATCH[2]}"
                day_val="${BASH_REMATCH[3]}"
                file_date="${year_val}-${month_val}-${day_val}"

                # Skip if already processed this date
                if [[ -n "${processed_dates[$file_date]}" ]]; then
                    continue
                fi
                processed_dates[$file_date]=1

                log "Deleting existing UDC data for ${file_date}..."

                # Delete from materialized table (MergeTree)
                docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query="
                    ALTER TABLE default.dump_materialized DELETE WHERE CDRtime = '${file_date}'
                " 2>&1 | tee -a "${LOG_FILE}" || true

                # Delete old Parquet files for this date
                # Note: Parquet partitions use integers without leading zeros (day=7 not day=07)
                month_int=$((10#${month_val}))  # Remove leading zero
                day_int=$((10#${day_val}))      # Remove leading zero

                parquet_path="${PARQUET_DIR}/telecom_data/year=${year_val}/month=${month_int}/day=${day_int}"
                log "Deleting Parquet path: ${parquet_path}"
                rm -rf "${parquet_path}" 2>/dev/null || true

                # Also try with leading zeros (in case of mixed formats)
                parquet_path_padded="${PARQUET_DIR}/telecom_data/year=${year_val}/month=${month_val}/day=${day_val}"
                if [ "${parquet_path}" != "${parquet_path_padded}" ]; then
                    rm -rf "${parquet_path_padded}" 2>/dev/null || true
                fi

                log_success "Deleted existing data for ${file_date}"
            fi
        fi
    done

    # Also delete any UDC source files from ClickHouse by source_file name pattern
    log "Deleting UDC records by source_file pattern..."
    for f in ${LDIF_DIR}/UDC_Details_*; do
        if [ -f "$f" ]; then
            filename=$(basename "$f")
            docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query="
                ALTER TABLE default.dump_materialized DELETE WHERE source_file = '${filename}'
            " 2>&1 | tee -a "${LOG_FILE}" || true
        fi
    done

    # Wait for mutations to complete
    log "Waiting for ClickHouse mutations to complete..."
    sleep 10

    # Verify deletion
    log "Verifying data deletion..."
    docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query="
        SELECT COUNT(*) as remaining_records FROM default.dump_materialized WHERE CDRtime = '${TODAY_DASH}'
    " 2>/dev/null | tee -a "${LOG_FILE}" || true

    log_success "Old UDC data cleanup complete"
}

#-------------------------------------------------------------------------------
# STEP 6.5: DELETE OLD UDC FILES (Keep Only Today's Files)
#-------------------------------------------------------------------------------
delete_old_udc_files() {
    log_step "STEP 6.5: Deleting Old UDC Files (Keep Only Today)"

    log "Removing UDC files from previous days..."

    # Delete UDC files that are NOT from today
    for f in ${LDIF_DIR}/UDC_Details_*; do
        if [ -f "$f" ]; then
            filename=$(basename "$f")
            # Check if filename contains today's date
            if [[ ! $filename == *"${TODAY}"* ]]; then
                log "Deleting old UDC file: $filename"
                rm -f "$f"
            fi
        fi
    done

    # List remaining files
    log "UDC files remaining for processing:"
    ls -lh ${LDIF_DIR}/UDC_Details_*${TODAY}* 2>/dev/null | tee -a "${LOG_FILE}" || log_warning "No UDC files for today"

    log_success "Old UDC files cleaned up"
}

#-------------------------------------------------------------------------------
# STEP 7: RUN SPARK PROCESSOR
#-------------------------------------------------------------------------------
run_spark_processor() {
    log_step "STEP 7: Running Spark Processor"

    cd "${PROJECT_DIR}"

    # Check if there are files to process
    if [ ! "$(ls -A ${LDIF_DIR}/*${TODAY}* 2>/dev/null)" ]; then
        log_warning "No LDIF files found for today (${TODAY}). Skipping processing."
        return 0
    fi

    # Show files to be processed
    log "Files to process:"
    for f in ${LDIF_DIR}/*${TODAY}*; do
        filename=$(basename "$f")
        filesize=$(ls -lh "$f" | awk '{print $5}')
        if [[ $filename == MNP* ]] || [[ $filename == mnp* ]]; then
            echo "  [MNP] $filename ($filesize)" | tee -a "${LOG_FILE}"
        else
            echo "  [UDC] $filename ($filesize)" | tee -a "${LOG_FILE}"
        fi
    done

    # Run Spark processor with today's date
    log "Starting Spark processor for date: ${TODAY_DASH}..."
    CDR_DATE="${TODAY_DASH}" docker-compose -f "${DOCKER_COMPOSE_FILE}" \
        --profile processor run --rm telecom_prod_spark_processor \
        2>&1 | tee -a "${LOG_FILE}"

    if [ $? -eq 0 ]; then
        log_success "Spark processing completed successfully"
    else
        log_error "Spark processing failed!"
        exit 1
    fi
}

#-------------------------------------------------------------------------------
# STEP 8: SYNC DATA TO MATERIALIZED TABLE
#-------------------------------------------------------------------------------
sync_to_materialized() {
    log_step "STEP 8: Syncing Data to Materialized Table"

    if [ -f "${PROJECT_DIR}/clickhouse/sync_incremental.sql" ]; then
        log "Running sync_incremental.sql..."
        docker exec -i ${CLICKHOUSE_CONTAINER} clickhouse-client --multiquery \
            < "${PROJECT_DIR}/clickhouse/sync_incremental.sql" 2>&1 \
            | grep -E "(status|metric|Sync completed|rows|final_status)" | tee -a "${LOG_FILE}" || true
        log_success "Data sync completed"

        # Verify materialized table
        log "Materialized table status:"
        docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query "
            SELECT
                COUNT(*) as total_records,
                COUNT(DISTINCT MSISDN) as unique_subscribers,
                COUNT(DISTINCT CDRtime) as data_dates
            FROM default.dump_materialized
        " 2>/dev/null | tee -a "${LOG_FILE}" || log_warning "Materialized table not ready yet"
    else
        log_warning "sync_incremental.sql not found - using Parquet VIEW"
    fi
}

#-------------------------------------------------------------------------------
# STEP 9: VERIFY DATA INGESTION
#-------------------------------------------------------------------------------
verify_data() {
    log_step "STEP 9: Verifying Data Ingestion"

    # Count UDC records for today
    log "UDC Records for ${TODAY_DASH}:"
    docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query="
        SELECT
            COUNT(*) as total_records,
            COUNT(DISTINCT MSISDN) as unique_subscribers
        FROM default.dump
        WHERE CDRtime = '${TODAY_DASH}'
    " 2>/dev/null | tee -a "${LOG_FILE}" || log_warning "No UDC data"

    # Count MNP records for today
    log "MNP Records for ${TODAY_DASH}:"
    docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query="
        SELECT COUNT(*) as total_records
        FROM default.MNP
        WHERE Date = '${TODAY_DASH}'
    " 2>/dev/null | tee -a "${LOG_FILE}" || log_warning "No MNP data"

    # Verify Parquet files
    PARQUET_COUNT=$(ls -1 "${PARQUET_DIR}"/*${TODAY_DASH}*.parquet 2>/dev/null | wc -l || echo "0")
    log "Parquet files created: ${PARQUET_COUNT}"
}

#-------------------------------------------------------------------------------
# STEP 10: CLEANUP OLD DATA
#-------------------------------------------------------------------------------
cleanup_old_data() {
    log_step "STEP 10: Cleanup Old Data"

    # Delete UDC data older than 30 days
    # Note: default.dump is a VIEW - can't DELETE from it, only from materialized table
    log "Deleting UDC data older than 30 days from materialized table..."
    docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query="
        ALTER TABLE default.dump_materialized DELETE WHERE CDRtime < now() - INTERVAL 30 DAY
    " 2>&1 | tee -a "${LOG_FILE}" || true

    # Delete MNP data older than 1 year
    log "Deleting MNP data older than 1 year..."
    docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query="
        ALTER TABLE default.MNP DELETE WHERE Date < now() - INTERVAL ${MNP_RETENTION_DAYS} DAY
    " 2>&1 | tee -a "${LOG_FILE}" || true

    # Delete old LDIF files
    log "Deleting LDIF files older than ${LDIF_RETENTION_DAYS} days..."
    find "${LDIF_DIR}" -type f \( -name "*.ldif" -o -name "*.gz" \) -mtime +${LDIF_RETENTION_DAYS} -exec rm -f {} \;

    # Delete old Parquet files
    log "Deleting Parquet files older than ${PARQUET_RETENTION_DAYS} days..."
    find "${PARQUET_DIR}" -type f -name "*.parquet" -mtime +${PARQUET_RETENTION_DAYS} -exec rm -f {} \;

    # Delete old log files
    log "Deleting log files older than 30 days..."
    find "${LOG_DIR}" -type f -name "*.log" -mtime +30 -exec rm -f {} \;

    log_success "Cleanup complete"
}

#-------------------------------------------------------------------------------
# STEP 11: SHOW STATUS
#-------------------------------------------------------------------------------
show_status() {
    log_step "STEP 11: Pipeline Status"

    log "Running Services:"
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep "telecom-prod" || echo "  (no services running)"

    echo ""
    log "Data Statistics:"
    echo ""
    log "Total UDC Records:"
    docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query \
        "SELECT COUNT(*) as total, COUNT(DISTINCT MSISDN) as subscribers, COUNT(DISTINCT CDRtime) as dates FROM default.dump" 2>/dev/null || echo "  (no data)"

    echo ""
    log "Total MNP Records:"
    docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query \
        "SELECT COUNT(*) as total, COUNT(DISTINCT Date) as dates FROM default.MNP" 2>/dev/null || echo "  (no data)"
}

#-------------------------------------------------------------------------------
# MAIN EXECUTION
#-------------------------------------------------------------------------------
main() {
    echo ""
    echo "============================================"
    echo "  Telecom Pipeline - Daily Automation"
    echo "  Date: ${TODAY_DASH}"
    echo "============================================"
    echo ""

    START_TIME=$(date +%s)

    # Execute pipeline steps
    check_prerequisites
    create_directories
    download_files
    deploy_services
    init_database_schema
    delete_old_udc_data
    delete_old_udc_files    # Delete old UDC files, keep only today's
    run_spark_processor
    sync_to_materialized
    verify_data
    cleanup_old_data
    show_status

    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))

    echo ""
    echo "============================================"
    echo -e "${GREEN}  PIPELINE COMPLETED SUCCESSFULLY${NC}"
    echo "  Total Duration: ${DURATION} seconds ($(($DURATION / 60)) minutes)"
    echo "============================================"
    echo ""
    echo "Access Points:"
    echo "  Dashboard:     http://localhost:30014"
    echo "  ClickHouse:    http://localhost:30012"
    echo ""
    echo "Log File: ${LOG_FILE}"
    echo ""

    log "Pipeline completed successfully in ${DURATION} seconds"
}

# Run main function
main "$@"
