#!/bin/bash
#===============================================================================
# Telecom Pipeline - Daily Automated ETL Script
#
# Data Strategy:
#   - UDC Data: REPLACE daily (keep 7 days of history)
#   - MNP Data: ACCUMULATE (keep 30 days of history)
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
UDC_RETENTION_DAYS=7   # Keep 7 days of UDC data
MNP_RETENTION_DAYS=30  # Keep 30 days of MNP data

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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

    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed!"
        exit 1
    fi
    log_success "Docker is available"

    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        log_error "Docker Compose is not installed!"
        exit 1
    fi
    log_success "Docker Compose is available"

    if ! command -v sshpass &> /dev/null; then
        log_error "sshpass is not installed!"
        exit 1
    fi
    log_success "sshpass is available"
}

#-------------------------------------------------------------------------------
# STEP 2: CREATE DIRECTORIES
#-------------------------------------------------------------------------------
create_directories() {
    log_step "STEP 2: Creating Directories"
    mkdir -p "${LDIF_DIR}" "${PARQUET_DIR}" "${LOG_DIR}"
    log_success "Directories ready"
}

#-------------------------------------------------------------------------------
# STEP 3: CLEAN OLD UDC DATA (Keep last 7 days)
#-------------------------------------------------------------------------------
clean_old_udc() {
    log_step "STEP 3: Cleaning Old UDC Data (Keep ${UDC_RETENTION_DAYS} days)"

    # Build list of valid dates (today and last 6 days = 7 days total)
    VALID_DATES=()
    for i in $(seq 0 $((UDC_RETENTION_DAYS - 1))); do
        VALID_DATES+=($(date -d "-${i} days" +%Y%m%d))
    done
    log "Keeping UDC data for dates: ${VALID_DATES[*]}"

    # Delete old UDC files from wldif (older than 7 days)
    log "Deleting old UDC files..."
    for f in "${LDIF_DIR}"/UDC_Details_*; do
        if [ -f "$f" ]; then
            filename=$(basename "$f")
            keep=false
            for valid_date in "${VALID_DATES[@]}"; do
                if [[ $filename == *"${valid_date}"* ]]; then
                    keep=true
                    break
                fi
            done
            if [ "$keep" = false ]; then
                log "  Removing: $filename"
                rm -f "$f"
            fi
        fi
    done

    # Delete old Parquet data (older than 7 days)
    log "Deleting old Parquet data..."
    if [ -d "${PARQUET_DIR}/telecom_data" ]; then
        CUTOFF_DATE=$(date -d "-${UDC_RETENTION_DAYS} days" +%Y%m%d)

        for year_dir in "${PARQUET_DIR}/telecom_data"/year=*; do
            [ -d "$year_dir" ] || continue
            year_val=$(basename "$year_dir" | sed 's/year=//')

            for month_dir in "$year_dir"/month=*; do
                [ -d "$month_dir" ] || continue
                month_val=$(basename "$month_dir" | sed 's/month=//')

                for day_dir in "$month_dir"/day=*; do
                    [ -d "$day_dir" ] || continue
                    day_val=$(basename "$day_dir" | sed 's/day=//')

                    # Build date string for comparison (YYYYMMDD)
                    partition_date=$(printf "%04d%02d%02d" "$year_val" "$month_val" "$day_val")

                    if [ "$partition_date" -lt "$CUTOFF_DATE" ]; then
                        log "  Removing old partition: $day_dir"
                        rm -rf "$day_dir"
                    fi
                done

                # Remove empty month dir
                rmdir "$month_dir" 2>/dev/null || true
            done

            # Remove empty year dir
            rmdir "$year_dir" 2>/dev/null || true
        done
    fi

    log_success "Old UDC data cleaned"
}

#-------------------------------------------------------------------------------
# STEP 4: DOWNLOAD TODAY'S FILES
#-------------------------------------------------------------------------------
download_files() {
    log_step "STEP 4: Downloading Today's Files"

    # Download MNP files
    log "Downloading MNP files..."
    sshpass -p "${REMOTE_PASSWORD}" scp -o StrictHostKeyChecking=no \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/MNP_31_${TODAY}*" \
        "${LDIF_DIR}/" 2>/dev/null && log_success "MNP_31 downloaded" || log_warning "No MNP_31 files"

    sshpass -p "${REMOTE_PASSWORD}" scp -o StrictHostKeyChecking=no \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/MNP_32_${TODAY}*" \
        "${LDIF_DIR}/" 2>/dev/null && log_success "MNP_32 downloaded" || log_warning "No MNP_32 files"

    # Download UDC files
    log "Downloading UDC files..."
    sshpass -p "${REMOTE_PASSWORD}" scp -o StrictHostKeyChecking=no \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/UDC_Details_31_${TODAY}*" \
        "${LDIF_DIR}/" 2>/dev/null && log_success "UDC_31 downloaded" || log_warning "No UDC_31 files"

    sshpass -p "${REMOTE_PASSWORD}" scp -o StrictHostKeyChecking=no \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/UDC_Details_32_${TODAY}*" \
        "${LDIF_DIR}/" 2>/dev/null && log_success "UDC_32 downloaded" || log_warning "No UDC_32 files"

    # List downloaded files
    log "Files for processing:"
    ls -lh "${LDIF_DIR}"/*${TODAY}* 2>/dev/null | tee -a "${LOG_FILE}" || log_warning "No files for today"
}

#-------------------------------------------------------------------------------
# STEP 5: START SERVICES
#-------------------------------------------------------------------------------
start_services() {
    log_step "STEP 5: Starting Services"
    cd "${PROJECT_DIR}"

    # Start ClickHouse
    if ! docker ps | grep -q "${CLICKHOUSE_CONTAINER}"; then
        log "Starting ClickHouse..."
        docker-compose -f "${DOCKER_COMPOSE_FILE}" up -d telecom_prod_clickhouse
    fi

    # Wait for ClickHouse
    log "Waiting for ClickHouse..."
    for i in {1..30}; do
        if docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query "SELECT 1" &>/dev/null; then
            log_success "ClickHouse ready"
            break
        fi
        sleep 2
    done

    # Start Streamlit
    if ! docker ps | grep -q "telecom-prod-streamlit-app"; then
        log "Starting Streamlit..."
        docker-compose -f "${DOCKER_COMPOSE_FILE}" up -d telecom_prod_streamlit_app
    fi
    log_success "Services running"
}

#-------------------------------------------------------------------------------
# STEP 6: RUN SPARK PROCESSOR
#-------------------------------------------------------------------------------
run_processor() {
    log_step "STEP 6: Running Spark Processor"
    cd "${PROJECT_DIR}"

    # Check for files
    if [ ! "$(ls -A ${LDIF_DIR}/*${TODAY}* 2>/dev/null)" ]; then
        log_warning "No files for today. Skipping."
        return 0
    fi

    # Run processor
    log "Processing date: ${TODAY_DASH}"
    CDR_DATE="${TODAY_DASH}" docker-compose -f "${DOCKER_COMPOSE_FILE}" \
        --profile processor run --rm telecom_prod_spark_processor \
        2>&1 | tee -a "${LOG_FILE}"

    log_success "Processing complete"
}

#-------------------------------------------------------------------------------
# STEP 7: SYNC UDC DATA TO CLICKHOUSE
#-------------------------------------------------------------------------------
sync_udc_data() {
    log_step "STEP 7: Syncing UDC Data to ClickHouse"

    # Delete old UDC data from ClickHouse (keep last 7 days)
    log "Deleting UDC data older than ${UDC_RETENTION_DAYS} days from ClickHouse..."
    docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query="
        ALTER TABLE default.dump_materialized DELETE WHERE CDRtime < today() - ${UDC_RETENTION_DAYS}
    " 2>&1 | tee -a "${LOG_FILE}" || true

    # Wait for mutation to complete
    sleep 5

    log "Running sync_incremental.sql..."
    docker exec -i ${CLICKHOUSE_CONTAINER} clickhouse-client --multiquery \
        < "${PROJECT_DIR}/clickhouse/sync_incremental.sql" \
        2>&1 | tee -a "${LOG_FILE}"

    log_success "Data sync completed"
}

#-------------------------------------------------------------------------------
# STEP 8: CLEANUP OLD MNP DATA (Keep 30 days)
#-------------------------------------------------------------------------------
cleanup_old_mnp() {
    log_step "STEP 8: Cleanup Old MNP Data (Keep ${MNP_RETENTION_DAYS} days)"

    # Delete MNP data older than 30 days from ClickHouse
    log "Deleting MNP data older than ${MNP_RETENTION_DAYS} days..."
    docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query="
        ALTER TABLE default.MNP DELETE WHERE Date < today() - ${MNP_RETENTION_DAYS}
    " 2>&1 | tee -a "${LOG_FILE}" || true

    docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query="
        ALTER TABLE default.MNP_details DELETE WHERE Date < today() - ${MNP_RETENTION_DAYS}
    " 2>&1 | tee -a "${LOG_FILE}" || true

    # Delete old MNP files (older than 30 days)
    log "Deleting old MNP files..."
    find "${LDIF_DIR}" -name "MNP_*" -type f -mtime +${MNP_RETENTION_DAYS} -exec rm -f {} \;

    # Delete old log files
    find "${LOG_DIR}" -name "*.log" -type f -mtime +30 -exec rm -f {} \;

    log_success "Cleanup complete"
}

#-------------------------------------------------------------------------------
# STEP 9: VERIFY & STATUS
#-------------------------------------------------------------------------------
show_status() {
    log_step "STEP 9: Status"

    log "UDC Records for ${TODAY_DASH}:"
    docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query="
        SELECT COUNT(*) as records, COUNT(DISTINCT MSISDN) as subscribers
        FROM default.dump WHERE CDRtime = '${TODAY_DASH}'
    " 2>/dev/null || echo "  (no data)"

    log "MNP Records (last 30 days):"
    docker exec ${CLICKHOUSE_CONTAINER} clickhouse-client --query="
        SELECT COUNT(*) as total, COUNT(DISTINCT Date) as days
        FROM default.MNP WHERE Date >= today() - 30
    " 2>/dev/null || echo "  (no data)"

    log "Services:"
    docker ps --format "table {{.Names}}\t{{.Status}}" | grep "telecom-prod" || echo "  (none)"
}

#-------------------------------------------------------------------------------
# MAIN
#-------------------------------------------------------------------------------
main() {
    echo ""
    echo "============================================"
    echo "  Telecom Pipeline - ${TODAY_DASH}"
    echo "============================================"

    START_TIME=$(date +%s)

    check_prerequisites
    create_directories
    clean_old_udc        # Delete all old UDC first
    download_files       # Then download today's files
    start_services
    run_processor
    sync_udc_data        # Sync Parquet to ClickHouse
    cleanup_old_mnp      # Clean old MNP (keep 30 days)
    show_status

    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))

    echo ""
    echo "============================================"
    echo -e "${GREEN}  COMPLETED in ${DURATION}s${NC}"
    echo "============================================"
    echo "  Dashboard: http://localhost:30014"
    echo "  Log: ${LOG_FILE}"
    echo ""
}

main "$@"
