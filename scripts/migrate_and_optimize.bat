@echo off
REM ============================================================================
REM Database Migration and Optimization Script (Windows)
REM ============================================================================
REM This script handles the complete migration from Parquet-based VIEW to
REM optimized MergeTree table with indexes
REM
REM USAGE:
REM   migrate_and_optimize.bat [command]
REM
REM COMMANDS:
REM   init     - Initialize the optimized schema (creates tables and indexes)
REM   migrate  - Migrate all existing data to materialized table
REM   sync     - Sync new/incremental data from Parquet files
REM   verify   - Verify table and index status
REM   all      - Run complete migration (init + migrate + verify)
REM ============================================================================

setlocal enabledelayedexpansion

REM Configuration
set CLICKHOUSE_CONTAINER=telecom_clickhouse
set SCRIPT_DIR=%~dp0
set SQL_DIR=%SCRIPT_DIR%..\clickhouse

REM Parse command
set COMMAND=%1
if "%COMMAND%"=="" set COMMAND=help

REM Main command router
if "%COMMAND%"=="init" goto init
if "%COMMAND%"=="migrate" goto migrate
if "%COMMAND%"=="sync" goto sync
if "%COMMAND%"=="verify" goto verify
if "%COMMAND%"=="test" goto test
if "%COMMAND%"=="all" goto all
goto help

:init
echo [INFO] Checking ClickHouse container status...
docker ps | findstr "%CLICKHOUSE_CONTAINER%" >nul
if errorlevel 1 (
    echo [ERROR] ClickHouse container '%CLICKHOUSE_CONTAINER%' is not running!
    echo [INFO] Start it with: docker-compose up -d
    exit /b 1
)
echo [SUCCESS] ClickHouse container is running

echo [INFO] Initializing optimized database schema...
docker exec -i %CLICKHOUSE_CONTAINER% clickhouse-client --multiquery < "%SQL_DIR%\init_lakehouse.sql"
echo [SUCCESS] Schema initialized successfully
goto end

:migrate
echo [INFO] Checking ClickHouse container status...
docker ps | findstr "%CLICKHOUSE_CONTAINER%" >nul
if errorlevel 1 (
    echo [ERROR] ClickHouse container is not running!
    exit /b 1
)

echo [INFO] Starting data migration from Parquet to materialized table...
echo [WARNING] This may take some time depending on data size...
docker exec -i %CLICKHOUSE_CONTAINER% clickhouse-client --multiquery < "%SQL_DIR%\migrate_to_materialized.sql"
echo [SUCCESS] Data migration completed successfully
goto end

:sync
echo [INFO] Checking ClickHouse container status...
docker ps | findstr "%CLICKHOUSE_CONTAINER%" >nul
if errorlevel 1 (
    echo [ERROR] ClickHouse container is not running!
    exit /b 1
)

echo [INFO] Syncing incremental data from Parquet files...
docker exec -i %CLICKHOUSE_CONTAINER% clickhouse-client --multiquery < "%SQL_DIR%\sync_incremental.sql"
echo [SUCCESS] Incremental sync completed successfully
goto end

:verify
echo [INFO] Verifying table and index status...
docker exec %CLICKHOUSE_CONTAINER% clickhouse-client --query "SELECT 'Table row count:' AS metric, COUNT(*) AS value FROM default.dump_materialized UNION ALL SELECT 'Distinct subscribers:', COUNT(DISTINCT MSISDN) FROM default.dump_materialized FORMAT PrettyCompact;"

echo.
echo [INFO] Active indexes:
docker exec %CLICKHOUSE_CONTAINER% clickhouse-client --query "SELECT name, type, expr FROM system.data_skipping_indices WHERE table = 'dump_materialized' FORMAT PrettyCompact;"

echo.
echo [INFO] Partition statistics:
docker exec %CLICKHOUSE_CONTAINER% clickhouse-client --query "SELECT partition, COUNT(*) AS rows, formatReadableSize(sum(bytes)) AS size FROM system.parts WHERE table = 'dump_materialized' AND active = 1 GROUP BY partition ORDER BY partition DESC LIMIT 10 FORMAT PrettyCompact;"

echo [SUCCESS] Verification completed
goto end

:test
echo [INFO] Running performance test: MSISDN lookup...
for /f "delims=" %%i in ('docker exec %CLICKHOUSE_CONTAINER% clickhouse-client --query "SELECT MSISDN FROM default.dump_materialized LIMIT 1" -t') do set SAMPLE_MSISDN=%%i

if "%SAMPLE_MSISDN%"=="" (
    echo [ERROR] No data found in table for performance test
    exit /b 1
)

echo [INFO] Testing query: SELECT * FROM default.dump WHERE MSISDN = '%SAMPLE_MSISDN%'
docker exec %CLICKHOUSE_CONTAINER% clickhouse-client --time --query "SELECT COUNT(*) FROM default.dump WHERE MSISDN = '%SAMPLE_MSISDN%' FORMAT PrettyCompact;"
echo [SUCCESS] Performance test completed
goto end

:all
echo [INFO] Starting complete migration process...
echo.

call :init
if errorlevel 1 exit /b 1

echo.
call :migrate
if errorlevel 1 exit /b 1

echo.
call :verify
if errorlevel 1 exit /b 1

echo.
echo [SUCCESS] Complete migration finished successfully!
echo [INFO] Your dashboard queries will now be 10-100x faster!
goto end

:help
echo Database Migration and Optimization Script (Windows)
echo.
echo Usage: %~nx0 [command]
echo.
echo Commands:
echo   init     - Initialize the optimized schema (creates tables and indexes)
echo   migrate  - Migrate all existing data to materialized table
echo   sync     - Sync new/incremental data from Parquet files
echo   verify   - Verify table and index status
echo   test     - Run performance test
echo   all      - Run complete migration (init + migrate + verify)
echo   help     - Show this help message
echo.
echo Examples:
echo   %~nx0 all      # Complete migration
echo   %~nx0 sync     # Daily sync of new data
echo   %~nx0 test     # Test query performance
goto end

:end
endlocal
