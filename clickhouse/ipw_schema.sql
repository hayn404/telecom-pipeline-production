-- ============================================================================
-- IPW Audit Schema
-- VoLTE MSISDN reconciliation across SIPW, KIPW, YIPW IMS nodes
--
-- USAGE (run once to initialise):
--   docker exec telecom-prod-clickhouse clickhouse-client --multiquery < ipw_schema.sql
--
-- TABLES / VIEWS:
--   1. ipw_source        — VIEW reading fresh Parquet files (same pattern as dump_source)
--   2. ipw_raw           — ReplacingMergeTree table: one row per MSISDN per source file per date
--   3. ipw_reconciliation — VIEW: one row per MSISDN, status per process_date
--   4. ipw_summary        — VIEW: aggregated counts per process_date
-- ============================================================================

-- ============================================================================
-- 1. SOURCE VIEW — reads Parquet written by processor_ipw.py
--    Mirrors the dump_source pattern from init_lakehouse.sql
--    Path: /var/lib/clickhouse/user_files/parquet/ipw_data/ (mounted from host)
-- ============================================================================
CREATE OR REPLACE VIEW default.ipw_source AS
SELECT
    CAST(process_date AS Nullable(String)) AS process_date,
    CAST(msisdn       AS Nullable(String)) AS msisdn,
    CAST(naptrTxt     AS Nullable(String)) AS naptrTxt,
    CAST(source_file  AS Nullable(String)) AS source_file
FROM file(
    '/var/lib/clickhouse/user_files/parquet/ipw_data/**/*.parquet',
    'Parquet',
    'process_date String, msisdn String, naptrTxt String, source_file String'
)
WHERE _path NOT LIKE '%_temporary%'
SETTINGS
    input_format_parquet_import_nested         = 1,
    input_format_parquet_allow_missing_columns = 1;

-- ============================================================================
-- 2. RAW TABLE — one row per (MSISDN, source_file, process_date)
--    Changed from MergeTree to ReplacingMergeTree to handle duplicate inserts
--    Populated by ipw_sync.sql (INSERT weekly, no TRUNCATE)
--    Partitioned by month for efficient management
--    Retention: 7 days (old partitions deleted by pipeline script)
-- ============================================================================
CREATE TABLE IF NOT EXISTS default.ipw_raw
(
    process_date Date,
    msisdn       String,
    naptrTxt     String,
    source_file  LowCardinality(String)   -- 'SIPW', 'KIPW', 'YIPW'
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(process_date)
ORDER BY (process_date, source_file, msisdn)
SETTINGS index_granularity = 8192;

-- Bloom filter on msisdn for fast single-MSISDN lookups from the dashboard
ALTER TABLE default.ipw_raw
    ADD INDEX IF NOT EXISTS idx_ipw_msisdn msisdn TYPE bloom_filter GRANULARITY 1;

-- ============================================================================
-- 3. RECONCILIATION VIEW
--    One row per (msisdn, process_date).
--    Uses conditional aggregation (countIf / maxIf) — no FULL OUTER JOIN needed.
--    ClickHouse evaluates this in a single GROUP BY pass over ipw_raw.
--    FINAL keyword ensures deduplication when querying ReplacingMergeTree.
--
--    status values:
--      'OK'               — present in all 3 files with identical naptrTxt
--      'MISSING'          — absent from one or more files
--      'PATTERN_MISMATCH' — present in all 3 files but naptrTxt differs
-- ============================================================================
CREATE OR REPLACE VIEW default.ipw_reconciliation AS
SELECT
    msisdn,
    process_date,

    -- Pattern seen in each file (empty string if absent)
    maxIf(naptrTxt, source_file = 'SIPW')   AS sipw_pattern,
    maxIf(naptrTxt, source_file = 'KIPW')  AS kipw_pattern,
    maxIf(naptrTxt, source_file = 'YIPW')   AS yipw_pattern,

    -- Presence flags (1 = present, 0 = absent)
    countIf(source_file = 'SIPW')  > 0      AS in_sipw,
    countIf(source_file = 'KIPW') > 0      AS in_kipw,
    countIf(source_file = 'YIPW')  > 0      AS in_yipw,

    -- Status classification
    multiIf(
        -- Not in all 3 files
        NOT (
            countIf(source_file = 'SIPW')  > 0 AND
            countIf(source_file = 'KIPW') > 0 AND
            countIf(source_file = 'YIPW')  > 0
        ), 'MISSING',
        -- In all 3 but patterns differ
        maxIf(naptrTxt, source_file = 'SIPW') != maxIf(naptrTxt, source_file = 'KIPW') OR
        maxIf(naptrTxt, source_file = 'SIPW') != maxIf(naptrTxt, source_file = 'YIPW'),
        'PATTERN_MISMATCH',
        -- All good
        'OK'
    ) AS status,

    -- Human-readable description of which files are missing
    multiIf(
        -- Missing from all 3
        NOT (countIf(source_file = 'SIPW')  > 0) AND
        NOT (countIf(source_file = 'KIPW') > 0) AND
        NOT (countIf(source_file = 'YIPW')  > 0),
            'Missing from all 3',

        -- Missing from 2 files
        NOT (countIf(source_file = 'SIPW')  > 0) AND
        NOT (countIf(source_file = 'KIPW') > 0),
            'Missing from SIPW, KIPW',

        NOT (countIf(source_file = 'SIPW')  > 0) AND
        NOT (countIf(source_file = 'YIPW')  > 0),
            'Missing from SIPW, YIPW',

        NOT (countIf(source_file = 'KIPW') > 0) AND
        NOT (countIf(source_file = 'YIPW')  > 0),
            'Missing from KIPW, YIPW',

        -- Missing from exactly 1 file
        NOT (countIf(source_file = 'SIPW')  > 0), 'Missing from SIPW',
        NOT (countIf(source_file = 'KIPW') > 0), 'Missing from KIPW',
        NOT (countIf(source_file = 'YIPW')  > 0), 'Missing from YIPW',

        -- Not missing (consistent or mismatch)
        ''
    ) AS missing_from

FROM default.ipw_raw
GROUP BY msisdn, process_date;

-- ============================================================================
-- 4. SUMMARY VIEW — aggregated counts per process_date
--    Used by the Streamlit Reconciliation tab summary metrics row
--    FINAL ensures deduplication from ReplacingMergeTree
-- ============================================================================
CREATE OR REPLACE VIEW default.ipw_summary AS
SELECT
    process_date,
    countIf(status = 'OK')               AS ok_count,
    countIf(status = 'MISSING')          AS missing_count,
    countIf(status = 'PATTERN_MISMATCH') AS mismatch_count,
    count()                              AS total_count
FROM default.ipw_reconciliation
GROUP BY process_date
ORDER BY process_date DESC;

-- Verify schema created
SELECT 'IPW schema initialised successfully!' AS status;
SELECT 'Tables / views:' AS info;
SELECT name, engine
FROM system.tables
WHERE database = 'default' AND name LIKE 'ipw%'
ORDER BY name;
