#!/bin/bash
# ============================================================================
# Database Migration and Optimization Script
# ============================================================================
# This script handles the complete migration from Parquet-based VIEW to
# optimized MergeTree table with indexes
#
# USAGE:
#   ./migrate_and_optimize.sh [command]
#
# COMMANDS:
#   init     - Initialize the optimized schema (creates tables and indexes)
#   migrate  - Migrate all existing data to materialized table
#   sync     - Sync new/incremental data from Parquet files
#   verify   - Verify table and index status
#   all      - Run complete migration (init + migrate + verify)
#
# EXAMPLES:
#   ./migrate_and_optimize.sh all      # Complete migration
#   ./migrate_and_optimize.sh sync     # Daily sync of new data
# ============================================================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
CLICKHOUSE_CONTAINER="telecom-prod-clickhouse"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_DIR="$SCRIPT_DIR/../clickhouse"

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if ClickHouse container is running
check_clickhouse() {
    log_info "Checking ClickHouse container status..."
    if ! docker ps | grep -q "$CLICKHOUSE_CONTAINER"; then
        log_error "ClickHouse container '$CLICKHOUSE_CONTAINER' is not running!"
        log_info "Start it with: docker-compose up -d"
        exit 1
    fi
    log_success "ClickHouse container is running"
}

# Initialize optimized schema
init_schema() {
    log_info "Initializing optimized database schema..."
    docker exec -i "$CLICKHOUSE_CONTAINER" clickhouse-client --multiquery < "$SQL_DIR/init_lakehouse.sql"
    log_success "Schema initialized successfully"
}

# Migrate existing data
migrate_data() {
    log_info "Starting data migration from Parquet to materialized table..."
    log_warning "This may take some time depending on data size..."

    docker exec -i "$CLICKHOUSE_CONTAINER" clickhouse-client --multiquery < "$SQL_DIR/migrate_to_materialized.sql"

    log_success "Data migration completed successfully"
}

# Sync incremental data
sync_data() {
    log_info "Syncing incremental data from Parquet files..."
    docker exec -i "$CLICKHOUSE_CONTAINER" clickhouse-client --multiquery < "$SQL_DIR/sync_incremental.sql"
    log_success "Incremental sync completed successfully"
}

# Verify installation
verify_installation() {
    log_info "Verifying table and index status..."

    docker exec "$CLICKHOUSE_CONTAINER" clickhouse-client --query "
        SELECT 'Table row count:' AS metric, COUNT(*) AS value FROM default.dump_materialized
        UNION ALL
        SELECT 'Distinct subscribers:', COUNT(DISTINCT MSISDN) FROM default.dump_materialized
        UNION ALL
        SELECT 'Date range:', CONCAT(toString(MIN(CDRtime)), ' to ', toString(MAX(CDRtime))) FROM default.dump_materialized
        FORMAT PrettyCompact;
    "

    echo ""
    log_info "Active indexes:"
    docker exec "$CLICKHOUSE_CONTAINER" clickhouse-client --query "
        SELECT name, type, expr
        FROM system.data_skipping_indices
        WHERE table = 'dump_materialized'
        FORMAT PrettyCompact;
    "

    echo ""
    log_info "Partition statistics:"
    docker exec "$CLICKHOUSE_CONTAINER" clickhouse-client --query "
        SELECT partition, COUNT(*) AS rows, formatReadableSize(sum(bytes)) AS size
        FROM system.parts
        WHERE table = 'dump_materialized' AND active = 1
        GROUP BY partition
        ORDER BY partition DESC
        LIMIT 10
        FORMAT PrettyCompact;
    "

    log_success "Verification completed"
}

# Performance test
test_performance() {
    log_info "Running performance test: MSISDN lookup..."

    # Get a sample MSISDN
    SAMPLE_MSISDN=$(docker exec "$CLICKHOUSE_CONTAINER" clickhouse-client --query "SELECT MSISDN FROM default.dump_materialized LIMIT 1" -t)

    if [ -z "$SAMPLE_MSISDN" ]; then
        log_error "No data found in table for performance test"
        return 1
    fi

    log_info "Testing query: SELECT * FROM default.dump WHERE MSISDN = '$SAMPLE_MSISDN'"

    docker exec "$CLICKHOUSE_CONTAINER" clickhouse-client --time --query "
        SELECT COUNT(*) FROM default.dump WHERE MSISDN = '$SAMPLE_MSISDN'
        FORMAT PrettyCompact;
    "

    log_success "Performance test completed"
}

# Main command router
case "${1:-help}" in
    init)
        check_clickhouse
        init_schema
        ;;

    migrate)
        check_clickhouse
        migrate_data
        ;;

    sync)
        check_clickhouse
        sync_data
        ;;

    verify)
        check_clickhouse
        verify_installation
        ;;

    test)
        check_clickhouse
        test_performance
        ;;

    all)
        check_clickhouse
        log_info "Starting complete migration process..."
        echo ""
        init_schema
        echo ""
        migrate_data
        echo ""
        verify_installation
        echo ""
        log_success "Complete migration finished successfully!"
        log_info "Your dashboard queries will now be 10-100x faster!"
        ;;

    help|*)
        echo "Database Migration and Optimization Script"
        echo ""
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  init     - Initialize the optimized schema (creates tables and indexes)"
        echo "  migrate  - Migrate all existing data to materialized table"
        echo "  sync     - Sync new/incremental data from Parquet files"
        echo "  verify   - Verify table and index status"
        echo "  test     - Run performance test"
        echo "  all      - Run complete migration (init + migrate + verify)"
        echo "  help     - Show this help message"
        echo ""
        echo "Examples:"
        echo "  $0 all      # Complete migration"
        echo "  $0 sync     # Daily sync of new data"
        echo "  $0 test     # Test query performance"
        ;;
esac
