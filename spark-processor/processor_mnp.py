#!/usr/bin/env python3
"""
MNP LDIF Processor - Mobile Number Portability Data
Parses MNP LDIF files and stores aggregated data in ClickHouse

MNP LDIF Structure:
    dn: MSISDN=201000000007,dc=msisdn,ou=NPSD,serv=CSPS,ou=servCommonData,dc=eti
    NPREFIX:: QPE=     (Base64 encoded - indicates ported to Orange)
    SUBSTYPE: 3
    MSISDN: 201000000007

NPREFIX Values (after Base64 decode):
    QPM = Ported IN to Etisalat (prefix from MSISDN indicates source operator)
    QPI = Ported to Vodafone (from Etisalat)
    QPE = Ported to Orange (from Etisalat)
    QPQ = Ported to WE (from Etisalat)

Operator Prefixes:
    2010 = Vodafone
    2011 = Etisalat
    2012 = Orange
    2015 = WE
"""

import os
import gc
import gzip
import base64
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Generator
from collections import defaultdict

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
LDIF_DIR = os.getenv('LDIF_DIR', '/wldif')
CDR_DATE = os.getenv('CDR_DATE', '') or datetime.now().strftime('%Y-%m-%d')

# ClickHouse connection settings
CLICKHOUSE_HOST = os.getenv('CLICKHOUSE_HOST', 'telecom_clickhouse')
CLICKHOUSE_PORT = int(os.getenv('CLICKHOUSE_PORT', '8123'))


def decode_base64_value(value: str) -> str:
    """
    Handle LDIF Base64 encoded values.
    In LDIF, values after '::' are Base64 encoded.

    For MNP files, the values are like 'QPM=', 'QPI=', 'QPE=', 'QPQ='
    which are short Base64-encoded strings that decode to single characters.
    The dashboard expects the values WITHOUT the '=' padding, so we just strip it.
    """
    try:
        # Remove any whitespace and trailing '=' (Base64 padding)
        value = value.strip().rstrip('=')
        return value
    except Exception as e:
        logger.warning(f"Failed to process value '{value}': {e}")
        return value


def extract_msisdn_prefix(msisdn: str) -> str:
    """
    Extract the first 4 digits of MSISDN as operator prefix.
    e.g., 201012345678 -> 2010 (Vodafone)
    """
    if msisdn and len(msisdn) >= 4:
        return msisdn[:4]
    return None


def stream_mnp_entries(file_path: str) -> Generator[Dict[str, Any], None, None]:
    """
    Stream MNP LDIF entries from file.
    Yields one entry at a time with parsed fields.
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
                line = line.rstrip('\n\r')

                if not line:
                    # Empty line = end of entry
                    if current_entry and 'MSISDN' in current_entry:
                        yield current_entry
                        entry_count += 1
                        if entry_count % 100000 == 0:
                            logger.info(f"  Streamed {entry_count:,} MNP entries...")
                    current_entry = {}
                    continue

                if line.startswith('#'):
                    continue

                # Parse attribute: value pairs
                if '::' in line:
                    # Base64 encoded value (NPREFIX:: QPE=)
                    parts = line.split('::', 1)
                    attr = parts[0].strip()
                    value = decode_base64_value(parts[1].strip()) if len(parts) > 1 else ''
                    current_entry[attr] = value
                elif ':' in line:
                    # Regular value
                    parts = line.split(':', 1)
                    attr = parts[0].strip()
                    value = parts[1].strip() if len(parts) > 1 else ''
                    current_entry[attr] = value

            # Last entry
            if current_entry and 'MSISDN' in current_entry:
                yield current_entry
                entry_count += 1

        logger.info(f"  Total MNP entries streamed: {entry_count:,}")

    except Exception as e:
        logger.error(f"Error streaming MNP file {file_path}: {e}")
        raise


def process_mnp_file(file_path: str, cdr_date: str) -> tuple:
    """
    Process MNP LDIF file and return both aggregated counts and detailed records.
    Returns: (Dict[(prefix, value), count], List[detailed_records])
    """
    file_name = os.path.basename(file_path)
    logger.info(f"Processing MNP file: {file_name}")

    # Aggregate counts by (prefix, nprefix_value)
    counts = defaultdict(int)
    # Detailed records for MNP_details table
    detailed_records = []

    for entry in stream_mnp_entries(file_path):
        msisdn = entry.get('MSISDN', '')
        nprefix = entry.get('NPREFIX', '')
        substype = entry.get('SUBSTYPE', '')

        if not msisdn or not nprefix:
            continue

        # Extract operator prefix from MSISDN
        prefix = extract_msisdn_prefix(msisdn)
        if not prefix:
            continue

        # Count this entry (for aggregated table)
        counts[(prefix, nprefix)] += 1

        # Store detailed record
        detailed_records.append({
            'Date': cdr_date,
            'MSISDN': msisdn,
            'Prefix': prefix,
            'NPREFIX': nprefix,
            'SUBSTYPE': substype,
            'source_file': file_name
        })

    logger.info(f"  Aggregated into {len(counts)} groups, {len(detailed_records):,} detailed records")
    return dict(counts), detailed_records


def insert_to_clickhouse(aggregated_data: Dict[tuple, int], detailed_records: list, cdr_date: str):
    """
    Insert MNP data into ClickHouse (both aggregated and detailed).
    Uses DELETE + INSERT pattern to prevent duplicates when reprocessing the same day's file.
    """
    try:
        import clickhouse_connect
        from datetime import datetime as dt

        # Convert date string to date object
        if isinstance(cdr_date, str):
            date_obj = dt.strptime(cdr_date, '%Y-%m-%d').date()
        else:
            date_obj = cdr_date

        client = clickhouse_connect.get_client(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            username='default',
            password=''
        )

        # Delete existing data for this date before inserting (prevents duplicates)
        logger.info(f"  Deleting existing MNP data for date: {cdr_date}")
        client.command(f"ALTER TABLE default.MNP DELETE WHERE Date = '{cdr_date}'")
        client.command(f"ALTER TABLE default.MNP_details DELETE WHERE Date = '{cdr_date}'")
        logger.info(f"  Deleted existing data for {cdr_date}")

        # Insert aggregated data into MNP table
        agg_rows = []
        for (prefix, value), count in aggregated_data.items():
            agg_rows.append([date_obj, prefix, value, count])

        if agg_rows:
            client.insert(
                'default.MNP',
                agg_rows,
                column_names=['Date', 'Prefix', 'value', 'Count']
            )
            logger.info(f"  Inserted {len(agg_rows)} rows into MNP table (aggregated)")

        # Insert detailed records into MNP_details table
        if detailed_records:
            detail_rows = []
            for record in detailed_records:
                detail_rows.append([
                    date_obj,  # Use the date object, not string
                    record['MSISDN'],
                    record['Prefix'],
                    record['NPREFIX'],
                    record['SUBSTYPE'],
                    record['source_file']
                ])

            # Insert in batches of 10000 to avoid memory issues
            batch_size = 10000
            for i in range(0, len(detail_rows), batch_size):
                batch = detail_rows[i:i + batch_size]
                client.insert(
                    'default.MNP_details',
                    batch,
                    column_names=['Date', 'MSISDN', 'Prefix', 'NPREFIX', 'SUBSTYPE', 'source_file']
                )
            logger.info(f"  Inserted {len(detail_rows):,} rows into MNP_details table")

        client.close()

    except Exception as e:
        logger.error(f"Error inserting to ClickHouse: {e}")
        raise


def find_mnp_files(ldif_dir: str) -> list:
    """
    Find all MNP LDIF files in the directory.
    MNP files are named like: MNP_31_202511300627.ldif or MNP_*.ldif.gz
    """
    mnp_files = []
    ldif_path = Path(ldif_dir)

    for pattern in ['MNP_*.ldif', 'MNP_*.ldif.gz']:
        mnp_files.extend(ldif_path.glob(pattern))

    return sorted(mnp_files)


def extract_date_from_filename(filename: str) -> str:
    """
    Extract date from MNP filename.
    e.g., MNP_31_202511300627.ldif -> 2025-11-30
    """
    # Try to find date pattern YYYYMMDD in filename
    match = re.search(r'(\d{4})(\d{2})(\d{2})', filename)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"
    return None


def main():
    """Main entry point for MNP processing"""
    logger.info("=" * 60)
    logger.info("MNP LDIF Processor - Mobile Number Portability")
    logger.info("=" * 60)
    logger.info(f"LDIF Directory: {LDIF_DIR}")
    logger.info(f"Default CDR Date: {CDR_DATE}")
    logger.info(f"ClickHouse: {CLICKHOUSE_HOST}:{CLICKHOUSE_PORT}")
    logger.info("=" * 60)

    # Find MNP files
    mnp_files = find_mnp_files(LDIF_DIR)

    if not mnp_files:
        logger.warning(f"No MNP files found in {LDIF_DIR}")
        logger.info("Looking for files matching: MNP_*.ldif or MNP_*.ldif.gz")
        return

    logger.info(f"Found {len(mnp_files)} MNP files to process")

    # Process each MNP file
    total_records = 0

    for idx, mnp_file in enumerate(mnp_files, 1):
        file_name = mnp_file.name
        file_size = mnp_file.stat().st_size / 1024  # KB

        # Extract date from filename or use default
        file_date = extract_date_from_filename(file_name) or CDR_DATE

        logger.info("-" * 60)
        logger.info(f"FILE {idx}/{len(mnp_files)}: {file_name}")
        logger.info(f"Size: {file_size:.2f} KB")
        logger.info(f"Date: {file_date}")
        logger.info("-" * 60)

        file_start = datetime.now()

        try:
            # Process and aggregate
            aggregated_data, detailed_records = process_mnp_file(str(mnp_file), file_date)

            # Insert to ClickHouse
            insert_to_clickhouse(aggregated_data, detailed_records, file_date)

            file_records = len(detailed_records)
            total_records += file_records

            file_duration = (datetime.now() - file_start).total_seconds()
            logger.info(f"  Completed in {file_duration:.1f}s ({file_records:,} MNP records)")

        except Exception as e:
            logger.error(f"Error processing {file_name}: {e}")
            import traceback
            traceback.print_exc()

        gc.collect()

    # Final statistics
    logger.info("=" * 60)
    logger.info("MNP PROCESSING COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Total files processed: {len(mnp_files)}")
    logger.info(f"Total MNP records: {total_records:,}")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
