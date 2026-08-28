-- ============================================================================
-- IPW Audit Sync: Parquet → ClickHouse ipw_raw + pre-computed summary
--
-- USAGE (called by ipw_audit_pipeline.sh):
--   docker exec telecom-prod-clickhouse clickhouse-client --multiquery < ipw_sync.sql
--
-- STRATEGY:
--   1. TRUNCATE ipw_raw → INSERT fresh Parquet (latest sync only, no accumulation)
--   2. Run the expensive dump × ipw_raw JOIN once here, store in ipw_volte_summary
--   3. Dashboard reads ipw_volte_summary — zero JOIN at query time
-- ============================================================================

-- Ensure the summary table exists (safe to run repeatedly)
CREATE TABLE IF NOT EXISTS default.ipw_volte_summary (
    process_date      Date,
    computed_at       DateTime DEFAULT now(),
    total_volte       UInt64,
    cnt_healthy       UInt64,
    cnt_no_profile    UInt64,
    cnt_wrong_apn     UInt64,
    cnt_missing_impi  UInt64,
    cnt_not_in_ipw    UInt64,
    cnt_ipw_missing   UInt64,
    cnt_ipw_mismatch  UInt64,
    excl_no_profile   UInt64,
    excl_not_in_ipw   UInt64,
    excl_wrong_apn    UInt64,
    excl_missing_impi UInt64,
    excl_ipw_missing  UInt64,
    excl_ipw_mismatch UInt64,
    excl_healthy      UInt64
) ENGINE = ReplacingMergeTree(computed_at)
ORDER BY process_date;

-- Step 1: Clear previous data so only the latest sync is kept
TRUNCATE TABLE default.ipw_raw;

-- Step 2: Insert fresh data from Parquet via ipw_source VIEW
--         ipw_source reads: /var/lib/clickhouse/user_files/parquet/ipw_data/**/*.parquet
INSERT INTO default.ipw_raw (process_date, msisdn, naptrTxt, source_file)
SELECT
    toDate(ifNull(process_date, toString(today())))  AS process_date,
    ifNull(msisdn,      '')                          AS msisdn,
    ifNull(naptrTxt,    '')                          AS naptrTxt,
    ifNull(source_file, '')                          AS source_file
FROM default.ipw_source;

-- Step 3: Verify rows loaded per source file
SELECT 'Sync complete. Rows loaded per source file:' AS status;
SELECT
    source_file,
    count()       AS rows,
    process_date
FROM default.ipw_raw
GROUP BY source_file, process_date
ORDER BY process_date DESC, source_file;

-- Step 4: Pre-compute VoLTE reconciliation summary
--   Purge jemalloc arenas first so the tracker starts from actual RSS,
--   giving maximum headroom for the JOIN.
SYSTEM JEMALLOC PURGE;

SELECT 'Pre-computing VoLTE reconciliation summary...' AS status;

TRUNCATE TABLE default.ipw_volte_summary;

INSERT INTO default.ipw_volte_summary
SELECT
    (SELECT max(process_date) FROM default.ipw_raw) AS process_date,
    now()                       AS computed_at,
    count()                     AS total_volte,
    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND d.EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
        AND d.IMPI != ''
        AND ifNull(ipw.status, '') = 'OK'
    )                           AS cnt_healthy,
    countIf(d.EpsProfileId = '' OR d.EpsProfileId IS NULL)  AS cnt_no_profile,
    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND d.EpsIndMappingContextId NOT IN ('15$2008300586', '15$1008300586')
    )                           AS cnt_wrong_apn,
    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND d.EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
        AND (d.IMPI = '' OR d.IMPI IS NULL)
    )                           AS cnt_missing_impi,
    countIf(ifNull(ipw.msisdn, '') = '')                          AS cnt_not_in_ipw,
    countIf(ifNull(ipw.status, '') = 'MISSING')                   AS cnt_ipw_missing,
    countIf(ifNull(ipw.status, '') = 'PATTERN_MISMATCH')          AS cnt_ipw_mismatch,
    countIf(d.EpsProfileId = '' OR d.EpsProfileId IS NULL)        AS excl_no_profile,
    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND ifNull(ipw.msisdn, '') = ''
    )                           AS excl_not_in_ipw,
    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND ifNull(ipw.msisdn, '') != ''
        AND d.EpsIndMappingContextId NOT IN ('15$2008300586', '15$1008300586')
    )                           AS excl_wrong_apn,
    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND ifNull(ipw.msisdn, '') != ''
        AND d.EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
        AND (d.IMPI = '' OR d.IMPI IS NULL)
    )                           AS excl_missing_impi,
    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND ifNull(ipw.msisdn, '') != ''
        AND d.EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
        AND d.IMPI != ''
        AND ifNull(ipw.status, '') = 'MISSING'
    )                           AS excl_ipw_missing,
    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND ifNull(ipw.msisdn, '') != ''
        AND d.EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
        AND d.IMPI != ''
        AND ifNull(ipw.status, '') = 'PATTERN_MISMATCH'
    )                           AS excl_ipw_mismatch,
    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND d.EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
        AND d.IMPI != ''
        AND ifNull(ipw.status, '') = 'OK'
    )                           AS excl_healthy
FROM default.dump AS d
LEFT JOIN (
    SELECT
        msisdn,
        multiIf(
            NOT (countIf(source_file = 'SIPW')  > 0
                 AND countIf(source_file = 'KIPW') > 0
                 AND countIf(source_file = 'YIPW')  > 0), 'MISSING',
            maxIf(naptrTxt, source_file = 'SIPW') != maxIf(naptrTxt, source_file = 'KIPW') OR
            maxIf(naptrTxt, source_file = 'SIPW') != maxIf(naptrTxt, source_file = 'YIPW'),
            'PATTERN_MISMATCH',
            'OK'
        ) AS status
    FROM default.ipw_raw
    GROUP BY msisdn
) AS ipw ON d.MSISDN = ipw.msisdn
WHERE d.CDRtime = (SELECT max(process_date) FROM default.ipw_raw)
  AND d.TICK = '215'
SETTINGS max_bytes_before_external_group_by = 15000000000;

SELECT 'VoLTE summary computed. Rows:' AS status;
SELECT process_date, total_volte, cnt_healthy, cnt_not_in_ipw FROM default.ipw_volte_summary FINAL;

SELECT 'IPW sync completed successfully!' AS final_status;
