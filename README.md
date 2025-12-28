# Telecom Pipeline - Production Deployment

A comprehensive telecom subscriber analytics platform built with ClickHouse, Apache Spark, and Streamlit. This system processes LDIF data files and provides real-time dashboards for subscriber network analysis and MNP (Mobile Number Portability) tracking.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   LDIF Files    │────>│  Spark Processor │────>│    Parquet      │
│   (wldif/)      │     │                  │     │   (Data Lake)   │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                                          v
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│    Streamlit    │<────│    ClickHouse    │<────│   SQL Views     │
│    Dashboard    │     │    Database      │     │                 │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

## Components

### 1. ClickHouse Database
- High-performance columnar database
- Stores subscriber profiles and MNP data
- Queries Parquet files directly (Lakehouse architecture)

### 2. Spark Processor
- Converts LDIF files to Parquet format
- Processes MNP data and inserts to ClickHouse
- Optimized for large file processing with chunking

### 3. Streamlit Dashboard
- **Subscriber Overview**: Total subscribers, network type distribution (2G/3G, 4G, 5G, VoLTE)
- **Data SIMs Analysis**: Breakdown by network generation with EpsProfileId tracking
- **User Lookup**: Search individual subscriber profiles by MSISDN
- **MNP Analysis**: Ported In/Out statistics per operator (Vodafone, Orange, WE)
- **MNP Trends**: Historical trends with daily change tracking

## Quick Start

### Prerequisites
- Docker & Docker Compose
- At least 16GB RAM recommended

### Deployment

1. **Load Docker Images** (if pre-built):
   ```bash
   ./scripts/01-load-images.sh
   ```

2. **Start Services**:
   ```bash
   docker-compose -f docker-compose-production.yml up -d
   ```

3. **Process Data** (optional):
   ```bash
   docker-compose -f docker-compose-production.yml --profile processor up telecom_prod_spark_processor
   ```

4. **Access Dashboard**:
   - URL: http://localhost:30014
   - Default credentials: admin / admin123

## Ports

| Service    | Port  | Description          |
|------------|-------|----------------------|
| ClickHouse | 30012 | HTTP Interface       |
| ClickHouse | 30013 | Native Protocol      |
| Streamlit  | 30014 | Dashboard            |

## Directory Structure

```
production-deployment/
├── clickhouse/           # ClickHouse configuration and SQL scripts
├── spark-processor/      # Spark LDIF processor
├── streamlit-app/        # Dashboard application
├── scripts/              # Deployment and automation scripts
├── data/                 # Parquet data lake (gitignored)
├── dump/                 # Database dumps (gitignored)
├── wldif/                # LDIF source files (gitignored)
└── docker-compose-production.yml
```

## Features

### Network Type Distribution
- **5G**: Subscribers with 5G-capable EpsProfileId
- **4G VoLTE**: TICK = 215
- **4G**: CS subscribers with valid EpsProfileId
- **2G/3G**: CS subscribers without EpsProfileId

### Data SIMs Categories
- **Total Data SIMs**: No TICK + (PDPCP or EpsIndDefContextId)
- **2G/3G Data SIMs**: No EpsProfileId
- **4G Data SIMs**: Calculated as Total - 2G/3G - 5G
- **5G Data SIMs**: Specific 5G EpsProfileId values

### MNP Tracking
- Tracks ported-in and ported-out subscribers
- Supports Vodafone (2010), Orange (2012), WE (2015), Etisalat (2011)
- Daily change trends with historical analysis

## Scripts

| Script | Description |
|--------|-------------|
| `00-build-and-save-images.sh` | Build and save Docker images |
| `01-load-images.sh` | Load pre-built Docker images |
| `02-deploy-production.sh` | Deploy production environment |
| `03-process-data.sh` | Run data processing pipeline |
| `status.sh` | Check service status |
| `stop-production.sh` | Stop all services |

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CDR_DATE` | (empty) | Date for data processing (YYYYMMDD) |
| `CLICKHOUSE_HOST` | telecom_prod_clickhouse | ClickHouse host |
| `CLICKHOUSE_PORT` | 8123 | ClickHouse HTTP port |

---

## Credits

**Developed by:** Haneen | Intern, CS Core Operations

**Supervised by:** Ahmed Mohamed Gamal | Voice Core Operations Manager

**Organization:** e& CS Core Operations

---

*Dashboard Version: 1.0 (CS Core Operations)*
