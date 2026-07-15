-- ============================================================================
-- INCREMENTAL SYNC SCRIPT: Sync new Parquet data to materialized table
-- ============================================================================
-- This script synchronizes new data from Parquet files into the materialized table
-- Run this PERIODICALLY (e.g., every hour or daily) to keep the table updated
--
-- USAGE:
--   docker exec telecom_clickhouse clickhouse-client --multiquery < sync_incremental.sql
--
-- OR via cron job:
--   0 * * * * docker exec telecom_clickhouse clickhouse-client --multiquery < /path/to/sync_incremental.sql
--
-- STRATEGY:
--   - Finds the latest date in dump_materialized
--   - Inserts only new data from dump_source that is newer than latest date
--   - Uses INSERT INTO SELECT with WHERE clause for efficiency
-- ============================================================================

-- Step 1: Check current state
SELECT 'Current state before sync:' AS status;
SELECT 'Rows in materialized table:' AS metric, COUNT(*) AS value FROM default.dump_materialized;
SELECT 'Latest date in materialized:' AS metric, MAX(CDRtime) AS value FROM default.dump_materialized;
SELECT 'Latest date in source (Parquet):' AS metric, MAX(CDRtime) AS value FROM default.dump_source;

-- Step 2: Delete existing data for dates that will be synced
-- This handles daily UDC replacement where same date's data is updated
ALTER TABLE default.dump_materialized DELETE
WHERE CDRtime IN (SELECT DISTINCT CDRtime FROM default.dump_source);

-- Step 3: Sync all data from Parquet (handles both new and replaced dates)
INSERT INTO default.dump_materialized
    (mscid, CDRtime, MSISDN, IMSI, CSP, CSLOC, VLRADD, PDPCP, TICK,
     OBO, OBI, OBR, TS11, TS21, TS22, PRBT, NAM, DCF, CAW, HOLD,
     CFB, CFNRC, CFNRY, CFU, CLIR, SOCLIR, SOCLIP, CLIP, CAT,
     EpsImeiSv, EpsLastUpdateLocationDate, EpsLastActivityDate,
     EpsAccessRestriction, EpsStnSr, EpsAutomaticProvisioned,
     EpsRoamAllow, EpsRoamRestrict, EpsRoamingServiceAreaId,
     ImsLastActivityDate, ImsRoamAllow, ImsBarrInd, COLP, SOCOLP,
     EpsIndDefContextId, EpsIndMappingContextId, EpsProfileId,
     EpsUserIpV4Address, IMPI, source_file, processing_time,
     CFUT10FNUM, CFBTS10FNUM, CFNRCTS10FNUM, CFNRYTS10FNUM, DCFTS10FNUM, SCHAR)
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
    coalesce(EpsIndMappingContextId, '') AS EpsIndMappingContextId,
    coalesce(EpsProfileId, '') AS EpsProfileId,
    coalesce(EpsUserIpV4Address, '') AS EpsUserIpV4Address,
    coalesce(IMPI, '') AS IMPI,
    coalesce(source_file, '') AS source_file,
    coalesce(processing_time, now()) AS processing_time,
    coalesce(CFUT10FNUM, '') AS CFUT10FNUM,
    coalesce(CFBTS10FNUM, '') AS CFBTS10FNUM,
    coalesce(CFNRCTS10FNUM, '') AS CFNRCTS10FNUM,
    coalesce(CFNRYTS10FNUM, '') AS CFNRYTS10FNUM,
    coalesce(DCFTS10FNUM, '') AS DCFTS10FNUM,
    coalesce(SCHAR, '') AS SCHAR
FROM default.dump_source;

-- Step 4: Optimize the table (merge parts and rebuild indexes)
-- Using FINAL to force optimization of all parts
OPTIMIZE TABLE default.dump_materialized FINAL;

-- Step 5: Verify sync results
SELECT 'Sync completed! Updated state:' AS status;
SELECT 'Total rows in materialized:' AS metric, COUNT(*) AS value FROM default.dump_materialized;
SELECT 'Latest date in materialized:' AS metric, MAX(CDRtime) AS value FROM default.dump_materialized;
SELECT 'Distinct subscribers:' AS metric, COUNT(DISTINCT MSISDN) AS value FROM default.dump_materialized;

-- Step 6: Show partition statistics
SELECT 'Active partitions:' AS info;
SELECT partition, COUNT(*) AS rows, formatReadableSize(sum(bytes)) AS size
FROM system.parts
WHERE table = 'dump_materialized' AND active = 1
GROUP BY partition
ORDER BY partition DESC
LIMIT 10;

SELECT 'Incremental sync completed successfully!' AS final_status;
