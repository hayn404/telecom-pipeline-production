-- ============================================================================
-- MIGRATION SCRIPT: Populate dump_materialized from Parquet files
-- ============================================================================
-- This script loads existing Parquet data into the optimized materialized table
-- Run this ONCE after creating the dump_materialized table
--
-- USAGE:
--   docker exec telecom_clickhouse clickhouse-client --multiquery < migrate_to_materialized.sql
--
-- OR run inside ClickHouse client:
--   clickhouse-client --multiquery < migrate_to_materialized.sql
--
-- IMPORTANT: This may take time depending on data size
-- Monitor progress with: SELECT count(*) FROM default.dump_materialized;
-- ============================================================================

-- Step 1: Check current state
SELECT 'Current row count in dump_materialized:' AS status, COUNT(*) AS count FROM default.dump_materialized;
SELECT 'Estimated rows to migrate from Parquet:' AS status, COUNT(*) AS count FROM default.dump_source;

-- Step 2: Truncate existing data (if re-running migration)
-- UNCOMMENT ONLY IF YOU WANT TO START FRESH:
-- TRUNCATE TABLE default.dump_materialized;

-- Step 3: Insert data from Parquet files into materialized table
-- This uses INSERT INTO SELECT for efficient bulk loading
INSERT INTO default.dump_materialized
SELECT
    coalesce(mscid, '') AS mscid,
    CDRtime,
    coalesce(MSISDN, '') AS MSISDN,
    coalesce(IMSI, '') AS IMSI,
    coalesce(CSP, '') AS CSP,
    coalesce(CSLOC, '') AS CSLOC,
    coalesce(VLRADD, '') AS VLRADD,
    coalesce(PDPCP, '') AS PDPCP,
    coalesce(TICK, '') AS TICK,
    coalesce(OBO, '') AS OBO,
    coalesce(OBI, '') AS OBI,
    coalesce(OBR, '') AS OBR,
    coalesce(TS11, '') AS TS11,
    coalesce(TS21, '') AS TS21,
    coalesce(TS22, '') AS TS22,
    coalesce(PRBT, '') AS PRBT,
    coalesce(NAM, '') AS NAM,
    coalesce(DCF, '') AS DCF,
    coalesce(CAW, '') AS CAW,
    coalesce(HOLD, '') AS HOLD,
    coalesce(CFB, '') AS CFB,
    coalesce(CFNRC, '') AS CFNRC,
    coalesce(CFNRY, '') AS CFNRY,
    coalesce(CFU, '') AS CFU,
    coalesce(CLIR, '') AS CLIR,
    coalesce(SOCLIR, '') AS SOCLIR,
    coalesce(SOCLIP, '') AS SOCLIP,
    coalesce(CLIP, '') AS CLIP,
    coalesce(CAT, '') AS CAT,
    coalesce(EpsImeiSv, '') AS EpsImeiSv,
    coalesce(EpsLastUpdateLocationDate, '') AS EpsLastUpdateLocationDate,
    coalesce(EpsLastActivityDate, '') AS EpsLastActivityDate,
    coalesce(EpsAccessRestriction, '') AS EpsAccessRestriction,
    coalesce(EpsStnSr, '') AS EpsStnSr,
    coalesce(EpsAutomaticProvisioned, '') AS EpsAutomaticProvisioned,
    coalesce(EpsRoamAllow, '') AS EpsRoamAllow,
    coalesce(EpsRoamRestrict, '') AS EpsRoamRestrict,
    coalesce(EpsRoamingServiceAreaId, '') AS EpsRoamingServiceAreaId,
    coalesce(ImsLastActivityDate, '') AS ImsLastActivityDate,
    coalesce(ImsRoamAllow, '') AS ImsRoamAllow,
    coalesce(ImsBarrInd, '') AS ImsBarrInd,
    coalesce(COLP, '') AS COLP,
    coalesce(SOCOLP, '') AS SOCOLP,
    coalesce(EpsIndDefContextId, '') AS EpsIndDefContextId,
    coalesce(EpsProfileId, '') AS EpsProfileId,
    coalesce(EpsUserIpV4Address, '') AS EpsUserIpV4Address,
    coalesce(source_file, '') AS source_file,
    coalesce(processing_time, now()) AS processing_time
FROM default.dump_source;

-- Step 4: Optimize the table to merge parts and build indexes
OPTIMIZE TABLE default.dump_materialized FINAL;

-- Step 5: Verify migration
SELECT 'Migration completed! Final row count:' AS status, COUNT(*) AS count FROM default.dump_materialized;
SELECT 'Distinct subscribers:' AS status, COUNT(DISTINCT MSISDN) AS count FROM default.dump_materialized;
SELECT 'Date range:' AS status, MIN(CDRtime) AS min_date, MAX(CDRtime) AS max_date FROM default.dump_materialized;
SELECT 'Partitions created:' AS status, partition, COUNT(*) AS rows
FROM system.parts
WHERE table = 'dump_materialized' AND active = 1
GROUP BY partition
ORDER BY partition;

-- Step 6: Show index statistics
SELECT 'Index status:' AS info;
SELECT name, type, expr
FROM system.data_skipping_indices
WHERE table = 'dump_materialized';

SELECT 'Migration completed successfully!' AS final_status;
