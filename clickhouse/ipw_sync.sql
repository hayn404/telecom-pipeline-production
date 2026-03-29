-- ============================================================================
-- IPW Audit Sync: Parquet → ClickHouse ipw_raw
-- Weekly replace strategy — truncate then reload fresh data
--
-- USAGE (called by ipw_audit_pipeline.sh):
--   docker exec telecom-prod-clickhouse clickhouse-client --multiquery < ipw_sync.sql
--
-- STRATEGY:
--   TRUNCATE ipw_raw  →  INSERT from ipw_source (Parquet)  →  Verify
--   No accumulation: every weekly run starts from a clean slate.
-- ============================================================================

-- Step 1: Check state before sync
SELECT 'State before sync:' AS status;
SELECT count() AS rows_before FROM default.ipw_raw;

-- Step 2: Truncate (weekly replace — no history accumulation)
TRUNCATE TABLE default.ipw_raw;

-- Step 3: Insert fresh data from Parquet via ipw_source VIEW
--         ipw_source reads: /var/lib/clickhouse/user_files/parquet/ipw_data/**/*.parquet
INSERT INTO default.ipw_raw (process_date, msisdn, naptrTxt, source_file)
SELECT
    toDate(ifNull(process_date, toString(today())))  AS process_date,
    ifNull(msisdn,      '')                          AS msisdn,
    ifNull(naptrTxt,    '')                          AS naptrTxt,
    ifNull(source_file, '')                          AS source_file
FROM default.ipw_source;

-- Step 4: Verify rows loaded per source file
SELECT 'Sync complete. Rows loaded per source file:' AS status;
SELECT
    source_file,
    count()       AS rows,
    process_date
FROM default.ipw_raw
GROUP BY source_file, process_date
ORDER BY source_file;

-- Step 5: Quick reconciliation preview
SELECT 'Reconciliation summary:' AS status;
SELECT
    countIf(status = 'OK')               AS consistent,
    countIf(status = 'MISSING')          AS missing,
    countIf(status = 'PATTERN_MISMATCH') AS mismatch,
    count()                              AS total
FROM default.ipw_reconciliation;

SELECT 'IPW sync completed successfully!' AS final_status;
