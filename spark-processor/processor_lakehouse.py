#!/usr/bin/env python3
"""
Telecom LDIF Processor - Lakehouse Architecture (Optimized for Large Files)
Spark processes LDIF → Saves to Parquet → ClickHouse queries Parquet

Architecture:
    LDIF Files → Spark (Transform) → Parquet (Data Lake) ← ClickHouse ← Streamlit

Optimizations for large files (7+ GB compressed):
    - Chunked processing to limit memory usage
    - Streaming LDIF parsing
    - Incremental Parquet writes
    - Garbage collection between chunks
"""

import os
import gc
import gzip
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Generator, Iterator

from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.functions import col, lit, current_timestamp, year, month, dayofmonth
from pyspark.sql.types import StructType, StructField, StringType, DateType
import pandas as pd

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
LDIF_DIR = os.getenv('LDIF_DIR', '/wldif')
PARQUET_DIR = os.getenv('PARQUET_DIR', '/data/parquet')
CDR_DATE = os.getenv('CDR_DATE', '') or datetime.now().strftime('%Y-%m-%d')

# Chunk size for processing (number of LDIF entries per chunk)
# Smaller = less memory, slower; Larger = more memory, faster
CHUNK_SIZE = int(os.getenv('CHUNK_SIZE', '500000'))  # 500K entries per chunk

# Attributes to extract
SELECTED_ATTRS = [
    'MSISDN', 'IMSI', 'CSP', 'CSLOC', 'VLRADD', 'PDPCP', 'TICK', 'OBO', 'OBI', 'OBR',
    'TS11', 'TS21', 'TS22', 'PRBT', 'NAM', 'DCF', 'CAW', 'HOLD', 'CFB', 'CFNRC',
    'CFNRY', 'CFU', 'CLIR', 'SOCLIR', 'SOCLIP', 'CLIP', 'CAT', 'EpsImeiSv',
    'EpsLastUpdateLocationDate', 'EpsLastActivityDate', 'EpsAccessRestriction',
    'EpsStnSr', 'EpsAutomaticProvisioned', 'EpsRoamAllow', 'EpsRoamRestrict',
    'EpsRoamingServiceAreaId', 'ImsLastActivityDate', 'ImsRoamAllow', 'ImsBarrInd',
    'COLP', 'SOCOLP',
    # New parameters for VoLTE APN analysis
    'EpsIndDefContextId',  # VoLTE APN - if missing on VoLTE profile, indicates problem
    'EpsProfileId',        # Profile ID for subscriber
    'EpsUserIpV4Address'   # Format: 2002200145$10.89.73.130 (last 3 digits before $ = APN, after $ = IP)
]


def create_spark_session() -> SparkSession:
    """Create optimized Spark session for lakehouse processing"""
    import multiprocessing

    # Respect MAX_CORES environment variable to limit resource usage on production servers
    max_cores_env = os.getenv('MAX_CORES')
    if max_cores_env:
        num_cores = int(max_cores_env)
        logger.info(f"Resource limit applied: Using {num_cores} cores (MAX_CORES={max_cores_env})")
    else:
        num_cores = multiprocessing.cpu_count()
        logger.info(f"No resource limit: Using all {num_cores} available cores")

    spark = SparkSession.builder \
        .appName("TelecomLDIF_Lakehouse") \
        .config("spark.sql.shuffle.partitions", str(min(num_cores * 2, 200))) \
        .config("spark.default.parallelism", str(min(num_cores * 2, 200))) \
        .config("spark.executor.memory", os.getenv('SPARK_EXECUTOR_MEMORY', '4g')) \
        .config("spark.driver.memory", os.getenv('SPARK_DRIVER_MEMORY', '4g')) \
        .config("spark.driver.maxResultSize", "2g") \
        .config("spark.sql.adaptive.enabled", "true") \
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true") \
        .config("spark.serializer", "org.apache.spark.serializer.KryoSerializer") \
        .config("spark.sql.parquet.compression.codec", "snappy") \
        .config("spark.sql.parquet.mergeSchema", "false") \
        .config("spark.sql.parquet.filterPushdown", "true") \
        .config("spark.sql.parquet.int96AsTimestamp", "true") \
        .config("spark.memory.fraction", "0.6") \
        .config("spark.memory.storageFraction", "0.3") \
        .getOrCreate()

    spark.sparkContext.setLogLevel("WARN")
    logger.info(f"Spark session created - Cores: {num_cores}, Chunk size: {CHUNK_SIZE:,}")
    return spark


def create_schema() -> StructType:
    """Define schema for the telecom data"""
    fields = [
        StructField("mscid", StringType(), True),
        StructField("CDRtime", DateType(), True),
        StructField("source_file", StringType(), True)
    ]

    # Add all selected attributes
    for attr in SELECTED_ATTRS:
        fields.append(StructField(attr, StringType(), True))

    return StructType(fields)


def extract_mscid(dn: str) -> str:
    """Extract mscId from DN string"""
    if not dn:
        return None
    match = re.search(r'mscid=([^,]+)', dn, re.IGNORECASE)
    return match.group(1) if match else None


def stream_ldif_entries(file_path: str) -> Generator[Dict[str, Any], None, None]:
    """
    Stream LDIF entries from file without loading entire file into memory.
    Yields one entry at a time.
    """
    try:
        # Handle compressed files
        if file_path.endswith('.gz'):
            file_handle = gzip.open(file_path, 'rt', encoding='utf-8', errors='ignore')
        else:
            file_handle = open(file_path, 'r', encoding='utf-8', errors='ignore')

        current_entry = {}
        entry_count = 0

        with file_handle as f:
            for line in f:
                line = line.strip()

                if not line:
                    if current_entry and 'dn' in current_entry:
                        yield current_entry
                        entry_count += 1
                        if entry_count % 1000000 == 0:
                            logger.info(f"  Streamed {entry_count:,} entries...")
                    current_entry = {}
                    continue

                if line.startswith('#'):
                    continue

                if ':' in line:
                    parts = line.split(':', 1)
                    attr = parts[0].strip()
                    value = parts[1].strip() if len(parts) > 1 else ''

                    if attr not in current_entry:
                        current_entry[attr] = value

            # Last entry
            if current_entry and 'dn' in current_entry:
                yield current_entry

        logger.info(f"  Total entries streamed: {entry_count:,}")

    except Exception as e:
        logger.error(f"Error streaming {file_path}: {e}")
        raise


def process_entries_chunk(entries: List[Dict], source_file: str, cdr_date: str) -> List[Dict]:
    """
    Process a chunk of LDIF entries: group by mscId and merge attributes.
    Returns list of processed records ready for DataFrame.
    """
    # Group entries by mscId
    entries_by_mscid = {}

    for entry in entries:
        mscid = extract_mscid(entry.get('dn', ''))
        if not mscid:
            continue

        if mscid not in entries_by_mscid:
            entries_by_mscid[mscid] = []
        entries_by_mscid[mscid].append(entry)

    # Merge attributes from all entries with same mscId
    processed_records = []

    for mscid, mscid_entries in entries_by_mscid.items():
        # Create merged record for this subscriber
        record = {
            'mscid': mscid,
            'CDRtime': cdr_date,
            'source_file': source_file
        }

        # Merge all attributes from all entries for this mscId
        for entry in mscid_entries:
            for attr in SELECTED_ATTRS:
                # Only set if not already set (first occurrence wins)
                if attr not in record or record[attr] is None:
                    value = entry.get(attr, None)
                    if isinstance(value, list):
                        value = value[0] if value else None
                    # Convert to string if value exists, otherwise None
                    record[attr] = str(value) if value else None

        # Only include records with valid MSISDN
        if record.get('MSISDN') is not None:
            processed_records.append(record)

    return processed_records


def chunk_iterator(iterator: Iterator, chunk_size: int) -> Generator[List, None, None]:
    """Split an iterator into chunks of specified size"""
    chunk = []
    for item in iterator:
        chunk.append(item)
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def process_ldif_file_chunked(spark: SparkSession, file_path: str, parquet_dir: str,
                               cdr_date: str, schema: StructType) -> int:
    """
    Process a single LDIF file in chunks to manage memory.
    Returns total number of records processed.
    """
    file_name = os.path.basename(file_path)
    output_path = f"{parquet_dir}/telecom_data"
    total_records = 0
    chunk_num = 0

    logger.info(f"Processing {file_name} in chunks of {CHUNK_SIZE:,} entries...")

    # Stream entries and process in chunks
    entry_stream = stream_ldif_entries(file_path)

    for entries_chunk in chunk_iterator(entry_stream, CHUNK_SIZE):
        chunk_num += 1
        logger.info(f"  Processing chunk {chunk_num} ({len(entries_chunk):,} entries)...")

        # Process this chunk
        processed_records = process_entries_chunk(entries_chunk, file_name, cdr_date)

        if not processed_records:
            logger.info(f"  Chunk {chunk_num}: No valid records")
            continue

        # Convert to DataFrame
        try:
            pandas_df = pd.DataFrame(processed_records)
            pandas_df['CDRtime'] = pd.to_datetime(pandas_df['CDRtime']).dt.date

            df = spark.createDataFrame(pandas_df, schema=schema)

            # Add partitioning columns
            df = df.withColumn("processing_time", current_timestamp())
            df = df.withColumn("year", year(col("CDRtime")))
            df = df.withColumn("month", month(col("CDRtime")))
            df = df.withColumn("day", dayofmonth(col("CDRtime")))

            chunk_count = len(processed_records)
            total_records += chunk_count

            # Write chunk to Parquet (append mode)
            df.write \
                .mode("append") \
                .partitionBy("year", "month", "day") \
                .parquet(output_path)

            logger.info(f"  Chunk {chunk_num}: Wrote {chunk_count:,} records (Total: {total_records:,})")

            # Clean up to free memory
            del df
            del pandas_df
            del processed_records
            del entries_chunk

        except Exception as e:
            logger.error(f"  Error processing chunk {chunk_num}: {e}")
            raise

        # Force garbage collection after each chunk
        gc.collect()

    return total_records


def is_mnp_file(file_path: str) -> bool:
    """Check if the file is an MNP file based on filename pattern."""
    file_name = os.path.basename(file_path).upper()
    return file_name.startswith('MNP_') or file_name.startswith('MNP.')


def extract_date_from_filename(filename: str) -> str:
    """
    Extract date from filename.
    e.g., UDC_Details_31_202511300627.ldif.gz -> 2025-11-30
    e.g., MNP_31_202511300627.ldif -> 2025-11-30
    """
    # Try to find date pattern YYYYMMDD in filename
    match = re.search(r'(\d{4})(\d{2})(\d{2})', filename)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"
    return None


def delete_parquet_for_date(parquet_dir: str, cdr_date: str):
    """
    Delete existing Parquet data for a specific date.
    UDC files are replaced daily, so we need to remove old data before inserting new.
    """
    import shutil

    # Parse date
    date_parts = cdr_date.split('-')
    if len(date_parts) != 3:
        logger.warning(f"Invalid date format: {cdr_date}, skipping deletion")
        return

    year, month, day = date_parts
    partition_path = Path(parquet_dir) / "telecom_data" / f"year={year}" / f"month={int(month)}" / f"day={int(day)}"

    if partition_path.exists():
        logger.info(f"Deleting existing Parquet data for {cdr_date}: {partition_path}")
        try:
            shutil.rmtree(partition_path)
            logger.info(f"  Deleted: {partition_path}")
        except Exception as e:
            logger.error(f"  Failed to delete {partition_path}: {e}")
    else:
        logger.info(f"No existing Parquet data for {cdr_date}")


def process_ldif_files_to_parquet(spark: SparkSession, ldif_dir: str, parquet_dir: str, cdr_date: str):
    """
    Process all LDIF files and save to partitioned Parquet files.
    Uses chunked processing for memory efficiency.
    Separates UDC_Details files from MNP files.
    """
    # Find all LDIF files
    all_files = []
    for ext in ['*.ldif', '*.ldif.gz']:
        all_files.extend(Path(ldif_dir).glob(ext))

    if not all_files:
        logger.warning(f"No LDIF files found in {ldif_dir}")
        return

    # Separate UDC files from MNP files
    ldif_files = [f for f in all_files if not is_mnp_file(str(f))]
    mnp_files = [f for f in all_files if is_mnp_file(str(f))]

    logger.info(f"Found {len(all_files)} total LDIF files:")
    logger.info(f"  - {len(ldif_files)} UDC_Details files (for Parquet processing)")
    logger.info(f"  - {len(mnp_files)} MNP files (will be processed separately)")

    if not ldif_files:
        logger.warning(f"No UDC_Details LDIF files found in {ldif_dir}")
        if mnp_files:
            logger.info("MNP files found - run processor_mnp.py to process them")
        return

    logger.info(f"Processing {len(ldif_files)} UDC_Details LDIF files")

    # Create schema once
    schema = create_schema()

    # Group files by date and delete existing data before processing
    dates_to_delete = set()
    file_dates = {}
    for ldif_file in ldif_files:
        file_date = extract_date_from_filename(ldif_file.name) or cdr_date
        file_dates[str(ldif_file)] = file_date
        dates_to_delete.add(file_date)

    # Delete existing Parquet data for all dates we'll be processing
    for date_to_delete in dates_to_delete:
        delete_parquet_for_date(parquet_dir, date_to_delete)

    # Process each file
    grand_total = 0

    for idx, ldif_file in enumerate(ldif_files, 1):
        file_name = ldif_file.name
        file_size = ldif_file.stat().st_size / (1024 * 1024 * 1024)  # GB
        file_date = file_dates[str(ldif_file)]

        logger.info("=" * 60)
        logger.info(f"FILE {idx}/{len(ldif_files)}: {file_name}")
        logger.info(f"Size: {file_size:.2f} GB")
        logger.info(f"Date: {file_date}")
        logger.info("=" * 60)

        file_start = datetime.now()

        try:
            file_records = process_ldif_file_chunked(
                spark, str(ldif_file), parquet_dir, file_date, schema
            )
            grand_total += file_records

            file_duration = (datetime.now() - file_start).total_seconds()
            logger.info(f"✓ {file_name}: {file_records:,} records in {file_duration:.1f}s")

        except Exception as e:
            logger.error(f"✗ Error processing {file_name}: {e}")
            raise

        # Force garbage collection between files
        gc.collect()

    # Final statistics
    logger.info("=" * 60)
    logger.info("PROCESSING COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Total files processed: {len(ldif_files)}")
    logger.info(f"Total records: {grand_total:,}")
    logger.info(f"Output path: {parquet_dir}/telecom_data")
    logger.info("=" * 60)


def process_mnp_files(ldif_dir: str, cdr_date: str):
    """
    Process MNP files and insert directly into ClickHouse.
    This is called after UDC processing.
    """
    from processor_mnp import find_mnp_files, process_mnp_file, insert_to_clickhouse, extract_date_from_filename

    mnp_files = find_mnp_files(ldif_dir)
    if not mnp_files:
        logger.info("No MNP files found to process")
        return

    logger.info("=" * 60)
    logger.info("Processing MNP Files")
    logger.info("=" * 60)
    logger.info(f"Found {len(mnp_files)} MNP files")

    total_records = 0

    for idx, mnp_file in enumerate(mnp_files, 1):
        file_name = mnp_file.name
        file_date = extract_date_from_filename(file_name) or cdr_date

        logger.info(f"  MNP File {idx}/{len(mnp_files)}: {file_name} (Date: {file_date})")

        try:
            aggregated_data, detailed_records = process_mnp_file(str(mnp_file), file_date)
            insert_to_clickhouse(aggregated_data, detailed_records, file_date)
            total_records += len(detailed_records)
            logger.info(f"    ✓ Processed {len(detailed_records):,} MNP records")
        except Exception as e:
            logger.error(f"    ✗ Error processing {file_name}: {e}")

        gc.collect()

    logger.info(f"MNP Processing complete: {total_records:,} total records")


def main():
    """Main entry point"""
    logger.info("=" * 60)
    logger.info("Telecom LDIF Processor - Lakehouse Architecture")
    logger.info("Optimized for Large Files (Chunked Processing)")
    logger.info("Supports: UDC_Details + MNP files")
    logger.info("=" * 60)
    logger.info(f"LDIF Directory: {LDIF_DIR}")
    logger.info(f"Parquet Output: {PARQUET_DIR}")
    logger.info(f"CDR Date: {CDR_DATE}")
    logger.info(f"Chunk Size: {CHUNK_SIZE:,} entries")
    logger.info("=" * 60)

    # Create Parquet directory
    os.makedirs(PARQUET_DIR, exist_ok=True)

    # Track overall start time
    start_time = datetime.now()

    # Create Spark session
    spark = create_spark_session()

    try:
        # Process UDC files and save to Parquet
        process_ldif_files_to_parquet(spark, LDIF_DIR, PARQUET_DIR, CDR_DATE)

        udc_duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"UDC Processing time: {int(udc_duration // 60)}m {int(udc_duration % 60)}s")

    except Exception as e:
        logger.error(f"Error during UDC processing: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        spark.stop()
        logger.info("Spark session stopped")

    # Process MNP files (separate from Spark, uses ClickHouse directly)
    try:
        mnp_start = datetime.now()
        process_mnp_files(LDIF_DIR, CDR_DATE)
        mnp_duration = (datetime.now() - mnp_start).total_seconds()
        logger.info(f"MNP Processing time: {int(mnp_duration // 60)}m {int(mnp_duration % 60)}s")
    except Exception as e:
        logger.error(f"Error during MNP processing: {e}")
        import traceback
        traceback.print_exc()

    total_duration = (datetime.now() - start_time).total_seconds()
    logger.info("=" * 60)
    logger.info(f"Total processing time: {int(total_duration // 60)}m {int(total_duration % 60)}s")
    logger.info("✓ All processing complete!")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
