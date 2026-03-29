#!/bin/bash
#===============================================================================
# IPW Audit Pipeline - Weekly VoLTE Reconciliation
#
# Compares 3 IMS node files (PIPW, R1IPW, YIPW) to detect:
#   - MSISDNs missing from one or more files
#   - MSISDNs with inconsistent NAPTR patterns across files
#
# Data Strategy:
#   - IPW Data: REPLACE weekly (no accumulation, always fresh)
#
# Schedule: Every Friday at 11:00 AM (cron: 0 11 * * 5)
#===============================================================================

set -e  # Exit on any error

#-------------------------------------------------------------------------------
# CONFIGURATION
#-------------------------------------------------------------------------------
PROJECT_DIR="/mnt/raid5/production-deployment"
IPW_DIR="${PROJECT_DIR}/ipw_audit"
PARQUET_DIR="${PROJECT_DIR}/data/parquet"
LOG_DIR="${PROJECT_DIR}/logs"
DOCKER_COMPOSE_FILE="${PROJECT_DIR}/docker-compose-production.yml"

# Remote Server Configuration (same server as daily pipeline)
REMOTE_USER="coreftp"
REMOTE_HOST="10.74.145.213"
REMOTE_PASSWORD="123456789"
REMOTE_IPW_PATH="/data/IPWs_audit"

# Date formats
TODAY=$(date +%Y%m%d)
TODAY_DASH=$(date +%Y-%m-%d)

# ClickHouse container
CLICKHOUSE_CONTAINER="telecom-prod-clickhouse"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

#-------------------------------------------------------------------------------
# LOGGING FUNCTIONS  (identical to daily_pipeline.sh)
#-------------------------------------------------------------------------------
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/ipw_pipeline_${TODAY_DASH}.log"

log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "${LOG_FILE}"
}

log_step() {
    echo "" | tee -a "${LOG_FILE}"
    echo -e "${BLUE}==========================================${NC}" | tee -a "${LOG_FILE}"
    echo -e "${BLUE}$1${NC}" | tee -a "${LOG_FILE}"
    echo -e "${BLUE}==========================================${NC}" | tee -a "${LOG_FILE}"
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

    if ! command -v sshpass &> /dev/null; then
        log_error "sshpass is not installed! Run: apt-get install sshpass"
        exit 1
    fi
    log_success "sshpass is available"

    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed!"
        exit 1
    fi
    log_success "Docker is available"

    if ! docker ps | grep -q "${CLICKHOUSE_CONTAINER}"; then
        log_error "ClickHouse container '${CLICKHOUSE_CONTAINER}' is not running!"
        log_error "Start it with: docker-compose -f ${DOCKER_COMPOSE_FILE} up -d telecom_prod_clickhouse"
        exit 1
    fi
    log_success "ClickHouse container is running"
}

#-------------------------------------------------------------------------------
# STEP 2: CREATE DIRECTORIES
#-------------------------------------------------------------------------------
create_directories() {
    log_step "STEP 2: Creating Directories"
    mkdir -p "${IPW_DIR}" "${PARQUET_DIR}" "${LOG_DIR}"
    log_success "Directories ready"
    log "  IPW raw files : ${IPW_DIR}"
    log "  Parquet output: ${PARQUET_DIR}/ipw_data"
    log "  Logs          : ${LOG_DIR}"
}

#-------------------------------------------------------------------------------
# STEP 3: CLEAN OLD LOCAL IPW FILES (no accumulation — weekly fresh replace)
#-------------------------------------------------------------------------------
clean_old_ipw_files() {
    log_step "STEP 3: Cleaning Old Local IPW Files"

    local count
    count=$(ls "${IPW_DIR}"/*.txt 2>/dev/null | wc -l)

    if [ "$count" -gt 0 ]; then
        log "Removing ${count} old IPW file(s) from ${IPW_DIR}..."
        rm -f "${IPW_DIR}"/*.txt
        log_success "Old IPW files removed"
    else
        log "No old IPW files to remove"
    fi
}

#-------------------------------------------------------------------------------
# STEP 4: DOWNLOAD IPW FILES FROM REMOTE SERVER
# Uses same sshpass + scp pattern as daily_pipeline.sh
# Wildcards (*PIPW.txt etc.) handle weekly prefix rotation (test2_, test3_, ...)
#-------------------------------------------------------------------------------
download_ipw_files() {
    log_step "STEP 4: Downloading IPW Files from ${REMOTE_HOST}:${REMOTE_IPW_PATH}"

    sshpass -p "${REMOTE_PASSWORD}" scp -o StrictHostKeyChecking=no \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_IPW_PATH}/*PIPW.txt" \
        "${IPW_DIR}/" 2>/dev/null \
        && log_success "PIPW file downloaded" \
        || log_warning "No PIPW file found on server"

    sshpass -p "${REMOTE_PASSWORD}" scp -o StrictHostKeyChecking=no \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_IPW_PATH}/*R1IPW.txt" \
        "${IPW_DIR}/" 2>/dev/null \
        && log_success "R1IPW file downloaded" \
        || log_warning "No R1IPW file found on server"

    sshpass -p "${REMOTE_PASSWORD}" scp -o StrictHostKeyChecking=no \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_IPW_PATH}/*YIPW.txt" \
        "${IPW_DIR}/" 2>/dev/null \
        && log_success "YIPW file downloaded" \
        || log_warning "No YIPW file found on server"

    # Abort if no files downloaded
    local downloaded
    downloaded=$(ls "${IPW_DIR}"/*.txt 2>/dev/null | wc -l)

    if [ "$downloaded" -eq 0 ]; then
        log_error "No IPW files were downloaded. Check server connectivity and path."
        exit 1
    fi

    log "Downloaded ${downloaded}/3 IPW files:"
    ls -lh "${IPW_DIR}"/*.txt 2>/dev/null | tee -a "${LOG_FILE}"
}

#-------------------------------------------------------------------------------
# STEP 5: CLEAN OLD IPW PARQUET (weekly replace, no accumulation)
#-------------------------------------------------------------------------------
clean_old_parquet() {
    log_step "STEP 5: Cleaning Old IPW Parquet Data"

    if [ -d "${PARQUET_DIR}/ipw_data" ]; then
        log "Removing old IPW parquet directory: ${PARQUET_DIR}/ipw_data"
        rm -rf "${PARQUET_DIR}/ipw_data"
        log_success "Old IPW parquet removed"
    else
        log "No old IPW parquet to remove"
    fi
}

#-------------------------------------------------------------------------------
# STEP 6: PROCESS IPW FILES → PARQUET
# Runs processor_ipw.py inside the existing Spark processor Docker container.
# NOTE: After adding processor_ipw.py, rebuild the image:
#   docker-compose -f docker-compose-production.yml build telecom_prod_spark_processor
#   (or re-run scripts/00-build-and-save-images.sh)
#-------------------------------------------------------------------------------
run_ipw_processor() {
    log_step "STEP 6: Processing IPW Files → Parquet (chunked, 500K rows/chunk)"

    log "Running processor_ipw.py in Docker..."

    PROCESS_DATE="${TODAY_DASH}" \
    docker-compose -f "${DOCKER_COMPOSE_FILE}" \
        --profile processor run --rm \
        -e PROCESS_DATE="${TODAY_DASH}" \
        -e IPW_DIR="/ipw_audit" \
        -e PARQUET_DIR="/data/parquet" \
        telecom_prod_spark_processor \
        python processor_ipw.py \
        2>&1 | tee -a "${LOG_FILE}"

    log_success "IPW Parquet processing complete"
    log "Output: ${PARQUET_DIR}/ipw_data/process_date=${TODAY_DASH}/"
}

#-------------------------------------------------------------------------------
# STEP 7: SYNC IPW PARQUET → CLICKHOUSE
#-------------------------------------------------------------------------------
sync_to_clickhouse() {
    log_step "STEP 7: Syncing IPW Parquet Data to ClickHouse"

    log "Running ipw_sync.sql (TRUNCATE + INSERT from Parquet)..."
    docker exec -i "${CLICKHOUSE_CONTAINER}" clickhouse-client --multiquery \
        < "${PROJECT_DIR}/clickhouse/ipw_sync.sql" \
        2>&1 | tee -a "${LOG_FILE}"

    log_success "ClickHouse sync complete"
}

#-------------------------------------------------------------------------------
# STEP 8: STATUS — Show reconciliation summary
#-------------------------------------------------------------------------------
show_status() {
    log_step "STEP 8: Reconciliation Summary"

    docker exec "${CLICKHOUSE_CONTAINER}" clickhouse-client --query="
        SELECT
            process_date,
            countIf(status = 'OK')               AS consistent,
            countIf(status = 'MISSING')          AS missing,
            countIf(status = 'PATTERN_MISMATCH') AS mismatch,
            count()                              AS total
        FROM default.ipw_reconciliation
        GROUP BY process_date
        ORDER BY process_date DESC
        LIMIT 1
        FORMAT Pretty
    " 2>/dev/null | tee -a "${LOG_FILE}" \
        || log_warning "Could not read reconciliation view — check ClickHouse schema"
}

#-------------------------------------------------------------------------------
# MAIN
#-------------------------------------------------------------------------------
main() {
    echo ""
    echo "============================================"
    echo "  IPW Audit Pipeline - ${TODAY_DASH}"
    echo "============================================"

    START_TIME=$(date +%s)

    check_prerequisites
    create_directories
    clean_old_ipw_files      # Remove stale local files
    download_ipw_files       # Fresh download from FTP server
    clean_old_parquet        # Remove stale parquet
    run_ipw_processor        # IPW txt → Parquet (chunked)
    sync_to_clickhouse       # Parquet → ClickHouse ipw_raw
    show_status              # Print reconciliation summary

    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))

    echo ""
    echo "============================================"
    echo -e "${GREEN}  COMPLETED in ${DURATION}s${NC}"
    echo "============================================"
    echo "  Log: ${LOG_FILE}"
    echo "  Dashboard: http://localhost:30014 → Reconciliation tab"
    echo ""
}

main "$@"
