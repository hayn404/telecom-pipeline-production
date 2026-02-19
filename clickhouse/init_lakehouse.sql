-- ClickHouse Lakehouse Configuration - PRODUCTION OPTIMIZED
-- Spark writes to Parquet → ClickHouse reads and materializes for fast queries
--
-- ARCHITECTURE:
-- 1. dump_source: VIEW reading from Parquet files (real-time ingestion)
-- 2. dump_materialized: MergeTree table with indexes (fast queries)
-- 3. dump: VIEW pointing to materialized table (backward compatibility)
-- 4. Materialized view auto-populates dump_materialized from dump_source

-- ============================================================================
-- STEP 1: SOURCE VIEW - Reads from Parquet files dynamically
-- ============================================================================
-- This VIEW reads the latest Parquet files (including newly written chunks)
-- Excludes Spark's _temporary directory to avoid reading incomplete files
CREATE OR REPLACE VIEW default.dump_source AS
SELECT
    CAST(mscid AS Nullable(String)) AS mscid,
    CAST(CDRtime AS Nullable(Date)) AS CDRtime,
    CAST(MSISDN AS Nullable(String)) AS MSISDN,
    CAST(IMSI AS Nullable(String)) AS IMSI,
    CAST(CSP AS Nullable(String)) AS CSP,
    CAST(CSLOC AS Nullable(String)) AS CSLOC,
    CAST(VLRADD AS Nullable(String)) AS VLRADD,
    CAST(PDPCP AS Nullable(String)) AS PDPCP,
    CAST(TICK AS Nullable(String)) AS TICK,
    CAST(OBO AS Nullable(String)) AS OBO,
    CAST(OBI AS Nullable(String)) AS OBI,
    CAST(OBR AS Nullable(String)) AS OBR,
    CAST(TS11 AS Nullable(String)) AS TS11,
    CAST(TS21 AS Nullable(String)) AS TS21,
    CAST(TS22 AS Nullable(String)) AS TS22,
    CAST(PRBT AS Nullable(String)) AS PRBT,
    CAST(NAM AS Nullable(String)) AS NAM,
    CAST(DCF AS Nullable(String)) AS DCF,
    CAST(CAW AS Nullable(String)) AS CAW,
    CAST(HOLD AS Nullable(String)) AS HOLD,
    CAST(CFB AS Nullable(String)) AS CFB,
    CAST(CFNRC AS Nullable(String)) AS CFNRC,
    CAST(CFNRY AS Nullable(String)) AS CFNRY,
    CAST(CFU AS Nullable(String)) AS CFU,
    CAST(CLIR AS Nullable(String)) AS CLIR,
    CAST(SOCLIR AS Nullable(String)) AS SOCLIR,
    CAST(SOCLIP AS Nullable(String)) AS SOCLIP,
    CAST(CLIP AS Nullable(String)) AS CLIP,
    CAST(CAT AS Nullable(String)) AS CAT,
    CAST(EpsImeiSv AS Nullable(String)) AS EpsImeiSv,
    CAST(EpsLastUpdateLocationDate AS Nullable(String)) AS EpsLastUpdateLocationDate,
    CAST(EpsLastActivityDate AS Nullable(String)) AS EpsLastActivityDate,
    CAST(EpsAccessRestriction AS Nullable(String)) AS EpsAccessRestriction,
    CAST(EpsStnSr AS Nullable(String)) AS EpsStnSr,
    CAST(EpsAutomaticProvisioned AS Nullable(String)) AS EpsAutomaticProvisioned,
    CAST(EpsRoamAllow AS Nullable(String)) AS EpsRoamAllow,
    CAST(EpsRoamRestrict AS Nullable(String)) AS EpsRoamRestrict,
    CAST(EpsRoamingServiceAreaId AS Nullable(String)) AS EpsRoamingServiceAreaId,
    CAST(ImsLastActivityDate AS Nullable(String)) AS ImsLastActivityDate,
    CAST(ImsRoamAllow AS Nullable(String)) AS ImsRoamAllow,
    CAST(ImsBarrInd AS Nullable(String)) AS ImsBarrInd,
    CAST(COLP AS Nullable(String)) AS COLP,
    CAST(SOCOLP AS Nullable(String)) AS SOCOLP,
    CAST(EpsIndDefContextId AS Nullable(String)) AS EpsIndDefContextId,
    CAST(EpsProfileId AS Nullable(String)) AS EpsProfileId,
    CAST(EpsUserIpV4Address AS Nullable(String)) AS EpsUserIpV4Address,
    CAST(CFUT10FNUM AS Nullable(String)) AS CFUT10FNUM,
    CAST(CFBTS10FNUM AS Nullable(String)) AS CFBTS10FNUM,
    CAST(CFNRCTS10FNUM AS Nullable(String)) AS CFNRCTS10FNUM,
    CAST(CFNRYTS10FNUM AS Nullable(String)) AS CFNRYTS10FNUM,
    CAST(DCFTS10FNUM AS Nullable(String)) AS DCFTS10FNUM,
    CAST(source_file AS Nullable(String)) AS source_file,
    CAST(processing_time AS Nullable(DateTime)) AS processing_time
FROM file('/var/lib/clickhouse/user_files/parquet/telecom_data/**/*.parquet', 'Parquet')
WHERE _path NOT LIKE '%_temporary%'
SETTINGS input_format_parquet_import_nested = 1;

-- ============================================================================
-- STEP 2: MATERIALIZED TABLE - Fast indexed table for queries
-- ============================================================================
-- MergeTree engine with optimized data types and indexes
-- ORDER BY (CDRtime, MSISDN, IMSI) creates a primary index for fast lookups
-- Partitioned by month for efficient data management
CREATE TABLE IF NOT EXISTS default.dump_materialized
(
    mscid String DEFAULT '',
    CDRtime Date,
    MSISDN String DEFAULT '',
    IMSI String DEFAULT '',
    CSP String DEFAULT '',
    CSLOC String DEFAULT '',
    VLRADD String DEFAULT '',
    PDPCP String DEFAULT '',
    TICK String DEFAULT '',
    OBO String DEFAULT '',
    OBI String DEFAULT '',
    OBR String DEFAULT '',
    TS11 String DEFAULT '',
    TS21 String DEFAULT '',
    TS22 String DEFAULT '',
    PRBT String DEFAULT '',
    NAM String DEFAULT '',
    DCF String DEFAULT '',
    CAW String DEFAULT '',
    HOLD String DEFAULT '',
    CFB String DEFAULT '',
    CFNRC String DEFAULT '',
    CFNRY String DEFAULT '',
    CFU String DEFAULT '',
    CLIR String DEFAULT '',
    SOCLIR String DEFAULT '',
    SOCLIP String DEFAULT '',
    CLIP String DEFAULT '',
    CAT String DEFAULT '',
    EpsImeiSv String DEFAULT '',
    EpsLastUpdateLocationDate String DEFAULT '',
    EpsLastActivityDate String DEFAULT '',
    EpsAccessRestriction String DEFAULT '',
    EpsStnSr String DEFAULT '',
    EpsAutomaticProvisioned String DEFAULT '',
    EpsRoamAllow String DEFAULT '',
    EpsRoamRestrict String DEFAULT '',
    EpsRoamingServiceAreaId String DEFAULT '',
    ImsLastActivityDate String DEFAULT '',
    ImsRoamAllow String DEFAULT '',
    ImsBarrInd String DEFAULT '',
    COLP String DEFAULT '',
    SOCOLP String DEFAULT '',
    EpsIndDefContextId String DEFAULT '',
    EpsProfileId String DEFAULT '',
    EpsUserIpV4Address String DEFAULT '',
    CFUT10FNUM String DEFAULT '',
    CFBTS10FNUM String DEFAULT '',
    CFNRCTS10FNUM String DEFAULT '',
    CFNRYTS10FNUM String DEFAULT '',
    DCFTS10FNUM String DEFAULT '',
    source_file String DEFAULT '',
    processing_time DateTime DEFAULT now()
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(CDRtime)
ORDER BY (CDRtime, MSISDN, IMSI)
SETTINGS index_granularity = 8192;

-- ============================================================================
-- STEP 3: ADD BLOOM FILTER INDEXES - Super fast MSISDN/IMSI lookups
-- ============================================================================
-- Bloom filter indexes dramatically speed up equality searches
-- These are the exact optimization your Data Engineer Supervisor recommended!
ALTER TABLE default.dump_materialized
ADD INDEX IF NOT EXISTS idx_msisdn MSISDN TYPE bloom_filter GRANULARITY 1;

ALTER TABLE default.dump_materialized
ADD INDEX IF NOT EXISTS idx_imsi IMSI TYPE bloom_filter GRANULARITY 1;

ALTER TABLE default.dump_materialized
ADD INDEX IF NOT EXISTS idx_tick TICK TYPE bloom_filter GRANULARITY 1;

ALTER TABLE default.dump_materialized
ADD INDEX IF NOT EXISTS idx_pdpcp PDPCP TYPE bloom_filter GRANULARITY 1;

ALTER TABLE default.dump_materialized
ADD INDEX IF NOT EXISTS idx_eps_access EpsAccessRestriction TYPE bloom_filter GRANULARITY 1;

-- ============================================================================
-- STEP 4: MAIN VIEW - Points to materialized table (backward compatibility)
-- ============================================================================
-- Applications query "default.dump" which now reads from the fast indexed table
CREATE OR REPLACE VIEW default.dump AS
SELECT * FROM default.dump_materialized;

-- ============================================================================
-- STEP 5: MATERIALIZED VIEW - Auto-populate from Parquet files
-- ============================================================================
-- This is commented out because it would auto-insert on every read from dump_source
-- Instead, use the migration script to populate the table initially
-- Then set up a scheduled job to sync new data periodically
/*
CREATE MATERIALIZED VIEW IF NOT EXISTS default.dump_mv TO default.dump_materialized AS
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
    coalesce(CFUT10FNUM, '') AS CFUT10FNUM,
    coalesce(CFBTS10FNUM, '') AS CFBTS10FNUM,
    coalesce(CFNRCTS10FNUM, '') AS CFNRCTS10FNUM,
    coalesce(CFNRYTS10FNUM, '') AS CFNRYTS10FNUM,
    coalesce(DCFTS10FNUM, '') AS DCFTS10FNUM,
    coalesce(source_file, '') AS source_file,
    coalesce(processing_time, now()) AS processing_time
FROM default.dump_source;
*/

-- ============================================================================
-- MNP (Mobile Number Portability) Tables
-- ============================================================================

-- MNP Aggregated table (counts by date, prefix, value)
-- Used for dashboard statistics and trends
CREATE TABLE IF NOT EXISTS default.MNP
(
    Date Date,
    Prefix String,
    value String,
    Count UInt32
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(Date)
ORDER BY (Date, Prefix, value)
SETTINGS index_granularity = 8192;

-- Add indexes for fast MNP queries
ALTER TABLE default.MNP
ADD INDEX IF NOT EXISTS idx_mnp_prefix Prefix TYPE bloom_filter GRANULARITY 1;

ALTER TABLE default.MNP
ADD INDEX IF NOT EXISTS idx_mnp_value value TYPE bloom_filter GRANULARITY 1;

-- MNP Detailed table (individual MSISDN records for detailed analysis)
-- Stores each ported number with its details
CREATE TABLE IF NOT EXISTS default.MNP_details
(
    Date Date,
    MSISDN String,
    Prefix String,
    NPREFIX String,
    SUBSTYPE String DEFAULT '',
    source_file String DEFAULT '',
    processing_time DateTime DEFAULT now()
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(Date)
ORDER BY (Date, Prefix, MSISDN)
SETTINGS index_granularity = 8192;

-- Indexes for MNP details
ALTER TABLE default.MNP_details
ADD INDEX IF NOT EXISTS idx_mnp_detail_msisdn MSISDN TYPE bloom_filter GRANULARITY 1;

ALTER TABLE default.MNP_details
ADD INDEX IF NOT EXISTS idx_mnp_detail_prefix Prefix TYPE bloom_filter GRANULARITY 1;

ALTER TABLE default.MNP_details
ADD INDEX IF NOT EXISTS idx_mnp_detail_nprefix NPREFIX TYPE bloom_filter GRANULARITY 1;

-- ============================================================================
-- MNP Helper Views
-- ============================================================================

-- View: MNP Summary by Operator (for easy dashboard queries)
CREATE OR REPLACE VIEW default.MNP_operator_summary AS
SELECT
    Date,
    CASE Prefix
        WHEN '2010' THEN 'Vodafone'
        WHEN '2011' THEN 'Etisalat'
        WHEN '2012' THEN 'Orange'
        WHEN '2015' THEN 'WE'
        ELSE 'Other'
    END AS Operator,
    Prefix,
    value,
    CASE value
        WHEN 'QPM' THEN 'Ported In'
        WHEN 'QPI' THEN 'Ported to Vodafone'
        WHEN 'QPE' THEN 'Ported to Orange'
        WHEN 'QPQ' THEN 'Ported to WE'
        ELSE value
    END AS PortingType,
    Count
FROM default.MNP
ORDER BY Date DESC, Prefix, value;

-- Processing statistics table
CREATE TABLE IF NOT EXISTS default.processing_stats
(
    batch_id Int64,
    records_processed UInt32,
    source_file String,
    processing_time DateTime DEFAULT now()
)
ENGINE = MergeTree()
ORDER BY processing_time
SETTINGS index_granularity = 8192;
