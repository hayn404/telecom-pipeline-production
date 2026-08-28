#!/usr/bin/env python3
"""
IPW Audit Processor - Tab-Separated Files to Parquet

Processes 3 IMS node IPW files (SIPW, KIPW, YIPW) into a unified Parquet
data lake for ClickHouse reconciliation queries.

File format (tab-separated, with header):
    enumDn          naptrTxt
    201117539668    !^.*$!sip:+201117539668@ims.mnc003.mcc602.3gppnetwork.org!

Architecture:
    IPW .txt Files → Python (chunked) → Parquet (Data Lake) ← ClickHouse ← Streamlit

Optimized for large files (~1 GB each, ~13M rows each):
    - Chunked reading via pandas (500K rows/chunk) — no full file in RAM
    - PyArrow Parquet writing with Snappy compression
    - Garbage collection between chunks
"""

import gc
import glob
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (via environment variables, with sensible defaults)
# ---------------------------------------------------------------------------
IPW_DIR      = os.getenv('IPW_DIR',      '/ipw_audit')
PARQUET_DIR  = os.getenv('PARQUET_DIR',  '/data/parquet')
PROCESS_DATE = os.getenv('PROCESS_DATE', '') or datetime.now().strftime('%Y-%m-%d')
CHUNK_SIZE   = int(os.getenv('CHUNK_SIZE', '500000'))  # rows per chunk

# Map: source label → glob pattern to find the file
FILE_PATTERNS = {
    'SIPW':  '*SIPW.txt',
    'KIPW': '*KIPW.txt',
    'YIPW':  '*YIPW.txt',
}

# ---------------------------------------------------------------------------
# PyArrow schema — must match ClickHouse ipw_raw table columns
# ---------------------------------------------------------------------------
SCHEMA = pa.schema([
    pa.field('process_date', pa.string()),
    pa.field('msisdn',       pa.string()),
    pa.field('naptrTxt',     pa.string()),
    pa.field('source_file',  pa.string()),
])


def find_file(directory: str, pattern: str) -> Optional[str]:
    """Return the first file matching *pattern* inside *directory*, or None."""
    matches = glob.glob(os.path.join(directory, pattern))
    if not matches:
        return None
    if len(matches) > 1:
        logger.warning(f"Multiple files match '{pattern}' — using: {matches[0]}")
    return matches[0]


def process_file_chunked(
    file_path: str,
    source_label: str,
    process_date: str,
    writer: pq.ParquetWriter,
) -> int:
    """
    Stream a single IPW tab-separated file in chunks of CHUNK_SIZE rows.
    Writes each processed chunk to the shared Parquet writer.

    Returns the total number of rows written for this file.
    """
    file_size_gb = os.path.getsize(file_path) / (1024 ** 3)
    logger.info(f"  File : {os.path.basename(file_path)}  ({file_size_gb:.2f} GB)")
    logger.info(f"  Label: {source_label} | Chunk size: {CHUNK_SIZE:,} rows")

    total_rows = 0
    chunk_num  = 0

    for chunk in pd.read_csv(
        file_path,
        sep='\t',
        header=0,
        names=['msisdn', 'naptrTxt'],  # file columns: enumDn → msisdn
        dtype=str,
        chunksize=CHUNK_SIZE,
        encoding='utf-8',
        on_bad_lines='skip',           # skip malformed rows gracefully
        engine='c',                    # fast C parser
    ):
        chunk_num += 1

        # Drop rows where the header line was repeated mid-file (some exports do this)
        chunk = chunk[chunk['msisdn'] != 'enumDn']
        chunk = chunk.dropna(subset=['msisdn', 'naptrTxt'])

        if chunk.empty:
            logger.info(f"    Chunk {chunk_num}: empty after filtering — skipping")
            continue

        # Add metadata columns
        chunk['process_date'] = process_date
        chunk['source_file']  = source_label

        # Reorder to match SCHEMA
        chunk = chunk[['process_date', 'msisdn', 'naptrTxt', 'source_file']]

        table = pa.Table.from_pandas(chunk, schema=SCHEMA, preserve_index=False)
        writer.write_table(table)

        total_rows += len(chunk)
        logger.info(f"    Chunk {chunk_num}: {len(chunk):,} rows  (running total: {total_rows:,})")

        # Explicit cleanup to keep memory footprint low
        del table
        del chunk
        gc.collect()

    return total_rows


def main() -> None:
    logger.info("=" * 60)
    logger.info("IPW Audit Processor")
    logger.info("=" * 60)
    logger.info(f"IPW Directory  : {IPW_DIR}")
    logger.info(f"Parquet Output : {PARQUET_DIR}")
    logger.info(f"Process Date   : {PROCESS_DATE}")
    logger.info(f"Chunk Size     : {CHUNK_SIZE:,} rows")
    logger.info("=" * 60)

    # Output path mirrors the existing telecom_data partition layout
    output_dir  = Path(PARQUET_DIR) / "ipw_data" / f"process_date={PROCESS_DATE}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "part-00000.snappy.parquet"

    start_time     = datetime.now()
    grand_total    = 0
    files_processed = 0
    files_missing  = []

    with pq.ParquetWriter(str(output_file), SCHEMA, compression='snappy') as writer:
        for source_label, pattern in FILE_PATTERNS.items():
            file_path = find_file(IPW_DIR, pattern)

            if not file_path:
                logger.warning(f"No file found for {source_label} (pattern: {pattern}) — skipping")
                files_missing.append(source_label)
                continue

            logger.info("=" * 60)
            logger.info(f"SOURCE: {source_label}")
            logger.info("=" * 60)

            file_start = datetime.now()
            try:
                rows = process_file_chunked(file_path, source_label, PROCESS_DATE, writer)
                grand_total    += rows
                files_processed += 1

                duration = (datetime.now() - file_start).total_seconds()
                logger.info(f"✓ {source_label}: {rows:,} rows in {duration:.1f}s")

            except Exception as exc:
                logger.error(f"✗ Error processing {source_label}: {exc}")
                raise

            gc.collect()

    total_duration = (datetime.now() - start_time).total_seconds()

    logger.info("=" * 60)
    logger.info("PROCESSING COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Files processed : {files_processed}/3")
    if files_missing:
        logger.warning(f"Files missing   : {', '.join(files_missing)}")
    logger.info(f"Total rows      : {grand_total:,}")
    logger.info(f"Output file     : {output_file}")
    logger.info(f"Duration        : {int(total_duration // 60)}m {int(total_duration % 60)}s")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
