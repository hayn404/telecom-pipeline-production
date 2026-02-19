# Telecom Pipeline - Handover Guide

<<<<<<< HEAD
## For: New Team Member (Non-Technical Background)

**Developed by:** Haneen | Intern, CS Core Operations
**Supervised by:** Ahmed Mohamed Gamal | Voice Core Operations Manager
=======
- **Developed by:** Haneen Alaa| Data Science Intern, CS Core Operations
- **Supervised by:** Ahmed Mohamed Gamal | Voice Core Operations Manager
>>>>>>> b0781d29c06d1bd53291de84fc9d3e333d821318

---

## What Does This Project Do?

This project is an **automated system** that:
1. **Reads subscriber data** from telecom network files (LDIF format)
2. **Processes and stores** the data in a database
3. **Shows statistics** on a web dashboard

<<<<<<< HEAD
Think of it like: **Excel on steroids** - it handles millions of records automatically and shows charts.

---

## The Big Picture (Simple Explanation)
=======
---

## The Big Picture
>>>>>>> b0781d29c06d1bd53291de84fc9d3e333d821318

```
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│   LDIF Files    │  ───▶   │     Spark       │  ───▶   │   ClickHouse    │  ───▶   │   Dashboard     │
│   (Raw Data)    │         │   (Processor)   │         │   (Database)    │         │   (Web Page)    │
└─────────────────┘         └─────────────────┘         └─────────────────┘         └─────────────────┘
     Input                    Transform                     Store                      Display
```

### In Simple Words:

| Step | What Happens | Like... |
|------|--------------|---------|
| **1. LDIF Files** | Network exports subscriber data daily | A big text file with all customer info |
| **2. Spark** | Reads the file and organizes it | Converting messy data to clean tables |
| **3. ClickHouse** | Stores the clean data | A very fast database |
| **4. Dashboard** | Shows charts and statistics | A website with graphs |

---

## The 4 Main Components

### 1. LDIF Files (Input Data)

**What are they?**
- Text files exported from the telecom network (HLR/HSS)
- Contains subscriber information (phone number, services, network type, etc.)
- Usually named like: `UDC_Details_31_202511300627.ldif.gz`

**Two types of files:**
| File Type | Content | Example |
|-----------|---------|---------|
| **UDC_Details** | All subscriber profiles | Phone numbers, IMSI, services, network type |
| **MNP** | Ported numbers | Numbers that moved between operators |

**Location:** `./wldif/` folder

---

### 2. Spark Processor (Data Transformer)

**What does it do?**
- Reads the big LDIF files
- Extracts important information (MSISDN, IMSI, TICK, etc.)
- Saves to Parquet format (compressed, fast to query)

**Two processors:**

| Processor | File | Purpose |
|-----------|------|---------|
| **processor_lakehouse.py** | UDC_Details files | Converts to Parquet for dashboard |
| **processor_mnp.py** | MNP files | Stores porting data in ClickHouse |

<<<<<<< HEAD
**How it works (Simple):**
=======
**How it works:**
>>>>>>> b0781d29c06d1bd53291de84fc9d3e333d821318

```
UDC_Details_31_202511300627.ldif.gz
            │
            ▼
   ┌─────────────────┐
   │  Spark reads    │
   │  line by line   │
   │  (streaming)    │
   └────────┬────────┘
            │
            ▼
   ┌─────────────────┐
   │  Groups data    │
   │  by subscriber  │
   │  (mscId)        │
   └────────┬────────┘
            │
            ▼
   ┌─────────────────┐
   │  Saves to       │
   │  Parquet file   │
   └─────────────────┘
```

---

### 3. ClickHouse Database (Data Storage)

**What is it?**
- A super-fast database designed for analytics
- Can query millions of records in seconds
- Stores data in columns (not rows) for speed

**Tables in the database:**

| Table | Content | Updated By |
|-------|---------|------------|
| **dump** | All subscribers (reads from Parquet) | Spark (via Parquet files) |
| **MNP** | Porting summary (counts per day) | MNP Processor |
| **MNP_details** | Individual ported numbers | MNP Processor |

**How dashboard queries work:**

```
Dashboard asks: "How many VoLTE subscribers?"
            │
            ▼
   ┌─────────────────┐
   │   ClickHouse    │
   │   reads from    │
   │   Parquet files │
   └────────┬────────┘
            │
            ▼
   Answer: 5,234,567 VoLTE subscribers
```

---

### 4. Streamlit Dashboard (Web Interface)

**What is it?**
- A Python web application
- Shows statistics, charts, and allows searching
<<<<<<< HEAD
- Accessible via browser at `http://localhost:30014`
=======
- Accessible via browser at `http://10.74.192.12:30014`
>>>>>>> b0781d29c06d1bd53291de84fc9d3e333d821318

**Dashboard Tabs:**

| Tab | Purpose |
|-----|---------|
| **Statistics** | Overview of all subscribers (VoLTE, 5G, Data SIMs, etc.) |
| **User Lookup** | Search for a specific subscriber by MSISDN or IMSI |
| **Advanced Search** | Filter subscribers by multiple criteria |
| **MNP Analysis** | Ported numbers between operators |

---

## Key Technical Terms (Glossary)

| Term | Meaning |
|------|---------|
| **MSISDN** | Phone number (e.g., 201001234567) |
| **IMSI** | SIM card ID (unique identifier) |
| **TICK** | Service type code (215=VoLTE, 201=RPT, etc.) |
| **PDPCP** | Network profile (starts with 5=5G, 4=4G+, etc.) |
| **EpsProfileId** | LTE profile identifier |
| **VoLTE** | Voice over LTE (HD calling over 4G) |
| **VoWiFi** | Voice over WiFi (calling over WiFi) |
| **MNP** | Mobile Number Portability (switching operators) |
| **Parquet** | Compressed file format for big data |
| **LDIF** | Text format for directory data |

---

## Network Type Classification

### Voice Subscribers (have TICK):

| Network Type | Condition | Meaning |
|--------------|-----------|---------|
| **5G** | EpsProfileId starts with 3 or 5, 3+ digits, equals PDPCP | 5G capable |
| **4G VoLTE** | TICK = 215 | Uses Voice over LTE |
| **4G** | TICK in (190,201,203,205) AND has EpsProfileId | 4G with data |
| **2G/3G** | TICK in (190,201,203,205) AND no EpsProfileId | Legacy network |

### Data SIMs (no TICK):

| Type | Condition |
|------|-----------|
| **Data SIM** | Has PDPCP or EpsIndDefContextId, but no TICK |
| **2G/3G Data** | Data SIM with no EpsProfileId |
| **4G Data** | Total Data - 2G/3G - 5G |
| **5G Data** | Data SIM with 5G EpsProfileId |

---

## MNP (Porting) Codes

| Code | Meaning |
|------|---------|
| **QPM** | Ported IN to the operator |
| **QPI** | Ported OUT to Vodafone |
| **QPE** | Ported OUT to Orange |
| **QPQ** | Ported OUT to WE |

| Operator | Prefix |
|----------|--------|
| Vodafone | 2010 |
| Etisalat | 2011 |
| Orange | 2012 |
| WE | 2015 |

---

## How to Run the System

### Start Everything:

```bash
cd production-deployment
docker-compose -f docker-compose-production.yml up -d
```

### Process New Data:

```bash
# Put LDIF files in ./wldif/ folder first, then:
docker-compose -f docker-compose-production.yml --profile processor up telecom_prod_spark_processor
```

### Access Dashboard:

<<<<<<< HEAD
Open browser: `http://localhost:30014`
=======
Open browser: `http://10.74.192.12:30014`
>>>>>>> b0781d29c06d1bd53291de84fc9d3e333d821318
- Username: `admin`
- Password: `admin123`

### Stop Everything:

```bash
docker-compose -f docker-compose-production.yml down
```

---

## File Structure

```
production-deployment/
│
├── wldif/                    # PUT LDIF FILES HERE
│   ├── UDC_Details_*.ldif.gz   # Subscriber data
│   └── MNP_*.ldif              # Porting data
│
├── data/parquet/             # Processed data (auto-generated)
│
├── spark-processor/          # Data processing scripts
│   ├── processor_lakehouse.py  # UDC processor
│   └── processor_mnp.py        # MNP processor
│
├── streamlit-app/            # Dashboard
│   └── app.py                  # Main dashboard code
│
├── clickhouse/               # Database config
│   └── init_lakehouse.sql      # Table definitions
│
├── docker-compose-production.yml  # System configuration
└── scripts/                  # Helper scripts
```

---

## Daily Operations

### Normal Daily Process:

1. **Receive LDIF files** from network team
2. **Copy files** to `./wldif/` folder
3. **Run processor** (see command above)
4. **Check dashboard** for updated statistics

### If Something Goes Wrong:

| Problem | Solution |
|---------|----------|
| Dashboard not loading | `docker-compose restart telecom_prod_streamlit_app` |
| Data not showing | Check if processor ran successfully |
| Old data showing | Clear cache: Click "Refresh Data" button |
| Can't connect to database | `docker-compose restart telecom_prod_clickhouse` |

---

## Ports Used

| Service | Port | URL |
|---------|------|-----|
<<<<<<< HEAD
| Dashboard | 30014 | http://localhost:30014 |
=======
| Dashboard | 30014 | http://10.74.192.12:30014 |
>>>>>>> b0781d29c06d1bd53291de84fc9d3e333d821318
| ClickHouse HTTP | 30012 | - |
| ClickHouse Native | 30013 | - |

---

## Flow Diagram (Complete)

```
                           TELECOM PIPELINE FLOW
                           ====================

    ┌──────────────────────────────────────────────────────────────────┐
    │                        INPUT (Daily)                              │
    │  ┌─────────────────┐              ┌─────────────────┐            │
    │  │ UDC_Details     │              │ MNP             │            │
    │  │ (Subscribers)   │              │ (Porting)       │            │
    │  └────────┬────────┘              └────────┬────────┘            │
    └───────────┼────────────────────────────────┼─────────────────────┘
                │                                │
                ▼                                ▼
    ┌───────────────────────┐      ┌───────────────────────┐
    │   Spark Processor     │      │   MNP Processor       │
    │   (processor_         │      │   (processor_         │
    │    lakehouse.py)      │      │    mnp.py)            │
    └───────────┬───────────┘      └───────────┬───────────┘
                │                              │
                ▼                              ▼
    ┌───────────────────────┐      ┌───────────────────────┐
    │   Parquet Files       │      │   ClickHouse          │
    │   (data/parquet/)     │      │   (MNP & MNP_details) │
    └───────────┬───────────┘      └───────────┬───────────┘
                │                              │
                └──────────────┬───────────────┘
                               │
                               ▼
                  ┌───────────────────────┐
                  │     ClickHouse        │
                  │     Database          │
                  │  (Queries Parquet +   │
                  │   MNP tables)         │
                  └───────────┬───────────┘
                              │
                              ▼
                  ┌───────────────────────┐
                  │   Streamlit Dashboard │
<<<<<<< HEAD
                  │   (http://localhost:  │
=======
                  │   (http://10.74.192.12:│
>>>>>>> b0781d29c06d1bd53291de84fc9d3e333d821318
                  │    30014)             │
                  └───────────────────────┘
                              │
                              ▼
                  ┌───────────────────────┐
                  │   USER VIEWS:         │
                  │   - Statistics        │
                  │   - User Lookup       │
                  │   - Advanced Search   │
                  │   - MNP Analysis      │
                  └───────────────────────┘
```

---

## Important: Daily Data Processing Logic

### UDC Files (Subscriber Data):
- **Strategy:** REPLACE daily
- Only processes files matching today's date (CDR_DATE)
- Deletes old Parquet data for that date before inserting
- Old files are deleted after processing (keeps only last 5 days)

### MNP Files (Porting Data):
- **Strategy:** ACCUMULATE (append new data)
- Only processes files matching today's date (CDR_DATE)
- Deletes MNP data for that date, then inserts new data
- Old MNP records kept for 1 year
- Old files deleted after 5 days

### Why This Matters:
- If CDR_DATE is set, only today's files are processed
- This prevents reprocessing old files every day
- MNP data accumulates over time (historical trends)
- UDC data is replaced daily (always shows latest subscriber status)

---

<<<<<<< HEAD
## Contact

For questions about this system:
- **Developer:** Haneen | CS Core Operations Intern
- **Supervisor:** Ahmed Mohamed Gamal | Voice Core Operations Manager
=======
>>>>>>> b0781d29c06d1bd53291de84fc9d3e333d821318

---

*Last Updated: January 2025*
