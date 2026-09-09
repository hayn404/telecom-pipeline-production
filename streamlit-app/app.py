import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import clickhouse_connect
import os
import io
import json
import hashlib
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')
from pcgt_audit import render_pcgt_audit_tab
from cnacld_tab import render_cnacld_tab

# Page config - MUST be first Streamlit command
st.set_page_config(page_title="e& CS Core Operations", page_icon="📡", layout="wide")

# Custom CSS to hide delta arrows in st.metric while keeping the percentage text
st.markdown("""
    <style>
    [data-testid="stMetricDelta"] svg {
        display: none;
    }
    </style>
""", unsafe_allow_html=True)

# ─── Users & Roles ───
# viewer   → can view data and reserve, but cannot upload files
# uploader → all viewer privileges PLUS can upload audit/Huawei/Ericsson files
USERS = {
    "admin":   {"password": "admin",      "role": "viewer"},
    "manager": {"password": "manager123", "role": "uploader"},
}

# LOGIN CHECK
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
if 'user_role' not in st.session_state:
    st.session_state.user_role = None
if 'username' not in st.session_state:
    st.session_state.username = None

def login():
    st.markdown("<h1 style='color: #A40000;'>📡 e& CS Core Operations</h1>", unsafe_allow_html=True)
    st.markdown("<h3> User Profile Management</h3>", unsafe_allow_html=True)
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        user = USERS.get(username)
        if user and user["password"] == password:
            st.session_state.authenticated = True
            st.session_state.username = username
            st.session_state.user_role = user["role"]
            st.rerun()
        else:
            st.error("Wrong username or password")

if not st.session_state.authenticated:
    login()
    st.stop()

# Title
st.title("📡 e& CS Core Operations Dashboard")

# Connect to ClickHouse - pooled connection via st.cache_resource
@st.cache_resource
def get_clickhouse_pool():
    """Create a reusable ClickHouse client (connection pool)"""
    try:
        client = clickhouse_connect.get_client(
            host=os.getenv('CLICKHOUSE_HOST', 'telecom_clickhouse'),
            port=int(os.getenv('CLICKHOUSE_PORT', '8123')),
            username='default',
            password='',
            compress=True,
            query_retries=2,
            connect_timeout=10,
            send_receive_timeout=30,
        )
        return client
    except Exception as e:
        st.error(f"Failed to connect to ClickHouse: {e}")
        return None

def get_clickhouse_client():
    return get_clickhouse_pool()

def run_query(query):
    """Execute a query using the pooled connection"""
    client = get_clickhouse_pool()
    if client is None:
        raise Exception("Cannot connect to database")
    result = client.query(query)
    return result

# Cached query function - caches results for 5 minutes to speed up dashboard
@st.cache_data(ttl=300, show_spinner=False)
def run_cached_query(query):
    """Execute a query with caching (5 min TTL) for better performance"""
    client = get_clickhouse_pool()
    if client is None:
        raise Exception("Cannot connect to database")
    result = client.query(query)
    # Convert to tuple of tuples for caching (lists aren't hashable)
    return tuple(tuple(row) for row in result.result_rows), result.column_names

def get_cached_result(query):
    """Wrapper to get cached query results"""
    rows, columns = run_cached_query(query)
    return list(rows), columns

# User lookup cached query - caches results for 1 hour (user data doesn't change frequently)
@st.cache_data(ttl=3600, show_spinner=False)
def run_user_lookup_cached(query):
    """Execute a user lookup query with caching (1 hour TTL) for fast repeated lookups"""
    client = get_clickhouse_pool()
    if client is None:
        raise Exception("Cannot connect to database")
    result = client.query(query)
    # Convert to tuple of tuples for caching (lists aren't hashable)
    return tuple(tuple(row) for row in result.result_rows), result.column_names

def get_user_lookup_result(query):
    """Wrapper to get cached user lookup query results"""
    rows, columns = run_user_lookup_cached(query)
    return list(rows), columns

# ============================================================================
# SERVER-SIDE FILE CACHE - Pre-computed results persist across restarts
# Data is processed daily, so results for a given date NEVER change.
# Cache files are stored on disk and loaded instantly on login.
# ============================================================================
CACHE_DIR = Path(os.getenv('CACHE_DIR', '/app/cache'))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _cache_path(date_str, cache_key):
    """Get the file path for a specific cache entry"""
    return CACHE_DIR / f"{date_str}" / f"{cache_key}.json"

def get_server_cache(date_str, cache_key):
    """Load pre-computed results from disk. Returns None if not cached."""
    path = _cache_path(date_str, cache_key)
    if path.exists():
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
    return None

def set_server_cache(date_str, cache_key, data):
    """Save pre-computed results to disk."""
    path = _cache_path(date_str, cache_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f)

def run_cached_query_with_disk(query, date_str, cache_key):
    """Query with server-side disk cache. Checks disk first, then DB.
    Cache entries include a hash of the query text so that changes to SQL
    automatically invalidate stale results."""
    query_hash = hashlib.md5(query.encode()).hexdigest()
    cached = get_server_cache(date_str, cache_key)
    if cached is not None and cached.get('query_hash') == query_hash:
        rows = tuple(tuple(row) for row in cached['rows'])
        columns = tuple(cached['columns'])
        return rows, columns
    # Not on disk, or query changed — query DB and save
    rows, columns = run_cached_query(query)
    # Serialize: convert date/datetime objects to strings for JSON
    serializable_rows = []
    for row in rows:
        serializable_row = []
        for val in row:
            if isinstance(val, (date, datetime)):
                serializable_row.append(val.isoformat())
            else:
                serializable_row.append(val)
        serializable_rows.append(serializable_row)
    set_server_cache(date_str, cache_key, {
        'rows': serializable_rows,
        'columns': list(columns),
        'query_hash': query_hash,
    })
    return rows, columns

def clear_date_cache(date_str):
    """Clear all cached files for a specific date"""
    cache_dir = CACHE_DIR / f"{date_str}"
    if cache_dir.exists():
        for f in cache_dir.iterdir():
            f.unlink()

# Test connection (quick check)
try:
    test_client = get_clickhouse_pool()
    if test_client is None:
        st.error("Cannot connect to database. Please check ClickHouse service.")
        st.stop()
except Exception as e:
    st.error(f"Database connection failed: {e}")
    st.stop()

# Sidebar - User info + logout
_role_icon = "👑" if st.session_state.user_role == "uploader" else "👤"
st.sidebar.markdown(
    f"{_role_icon} **{st.session_state.username}** "
    f"(_{st.session_state.user_role}_)"
)
if st.sidebar.button("🚪 Logout", key="sidebar_logout"):
    st.session_state.authenticated = False
    st.session_state.username = None
    st.session_state.user_role = None
    st.rerun()

# Sidebar - Date Selection
st.sidebar.title("⚙️ Settings")

# Get all available UDC dates with counts in a single query
try:
    rows, _ = run_cached_query_with_disk(
        "SELECT CDRtime, count() AS cnt FROM default.dump GROUP BY CDRtime ORDER BY CDRtime DESC",
        "global", "available_dates"
    )
    available_udc_dates = []
    date_counts = {}
    for row in rows:
        if row[0]:
            d = row[0]
            if isinstance(d, str):
                d = datetime.strptime(d, '%Y-%m-%d').date()
            available_udc_dates.append(d)
            date_counts[d] = row[1]

    if available_udc_dates:
        default_date = available_udc_dates[0]  # Latest available date
    else:
        default_date = date.today()
        available_udc_dates = [default_date]
except:
    default_date = date.today()
    available_udc_dates = [default_date]
    date_counts = {}

# Show info about available data dates
st.sidebar.info(f"📅 UDC data available for {len(available_udc_dates)} date(s)")

# Use selectbox to only allow selection of available dates
if len(available_udc_dates) > 1:
    cdr_date = st.sidebar.selectbox(
        "Select UDC Data Date",
        options=available_udc_dates,
        index=0,
        format_func=lambda x: x.strftime('%Y-%m-%d')
    )
else:
    cdr_date = st.sidebar.date_input(
        "Select Date",
        value=default_date
    )

cdr_date_str = cdr_date.strftime('%Y-%m-%d')
cdr_date_str_dash = cdr_date.strftime('%Y-%m-%d')

# Add refresh button in sidebar
if st.sidebar.button("🔄 Refresh Data"):
    # Clear server-side disk cache for current date + global cache
    clear_date_cache(cdr_date_str)
    clear_date_cache("global")
    st.cache_data.clear()
    st.cache_resource.clear()
    st.rerun()

# Verify data exists for selected date (use pre-fetched counts)
date_count = date_counts.get(cdr_date, 0)
if date_count == 0:
    st.warning(f"⚠️ No data available for {cdr_date_str}. Please select a different date from the sidebar.")
    st.stop()
else:
    st.sidebar.success(f"✓ {date_count:,} records for {cdr_date_str}")

# Create tabs
# ============================================================================
# EXCEL EXPORT HELPERS
# ============================================================================
def _write_styled_sheet(ws, df, title=None):
    """Write a DataFrame to an openpyxl worksheet with e& brand styling."""
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    header_fill = PatternFill(start_color="A40000", end_color="A40000", fill_type="solid")
    alt_fill    = PatternFill(start_color="FFF5F5", end_color="FFF5F5", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    data_font   = Font(size=10)
    thin_side   = Side(style="thin", color="DDDDDD")
    cell_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
    center      = Alignment(horizontal="center", vertical="center")

    if title:
        ws.append([title])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=13, color="A40000")
        ws.append([])

    header_row = ws.max_row + 1
    for ci, col_name in enumerate(df.columns, 1):
        c = ws.cell(row=header_row, column=ci, value=str(col_name))
        c.fill = header_fill
        c.font = header_font
        c.alignment = center
        c.border = cell_border

    for ri, row_vals in enumerate(df.itertuples(index=False), header_row + 1):
        fill = alt_fill if ri % 2 == 0 else PatternFill()
        for ci, val in enumerate(row_vals, 1):
            c = ws.cell(row=ri, column=ci, value=val)
            c.fill = fill
            c.font = data_font
            c.alignment = center
            c.border = cell_border

    for ci, col_name in enumerate(df.columns, 1):
        col_vals = df.iloc[:, ci - 1].astype(str)
        max_len = max(len(str(col_name)), col_vals.str.len().max() if len(df) > 0 else 0)
        ws.column_dimensions[get_column_letter(ci)].width = min(int(max_len) + 4, 45)


def create_stats_excel_report(date_str, total_subs, volte_subs, vowifi_subs, fiveg_subs,
                               roaming_subs, local_subs, fvno_subs,
                               data_sims_total, data_2g3g, data_4g, data_5g, corporate_data,
                               df_tick, df_network, df_vowifi, features_data,
                               df_country, df_operator_display):
    """Generate a formatted multi-sheet Excel report for the Statistics tab."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    safe_pct = lambda num, den: f"{num/den*100:.1f}%" if den > 0 else "N/A"

    # Sheet 1: KPI Summary
    ws = wb.create_sheet("KPI Summary")
    kpi_rows = [
        ("Voice",     "Total Subscribers",      total_subs,      "100.0%"),
        ("Voice",     "VoLTE Subscribers",       volte_subs,      safe_pct(volte_subs, total_subs)),
        ("Voice",     "VoWiFi Subscribers",      vowifi_subs,     safe_pct(vowifi_subs, total_subs)),
        ("Voice",     "5G Subscribers",          fiveg_subs,      safe_pct(fiveg_subs, total_subs)),
        ("Roaming",   "Roaming Subscribers",     roaming_subs,    safe_pct(roaming_subs, total_subs)),
        ("Roaming",   "Egypt/Local Subscribers", local_subs,      safe_pct(local_subs, total_subs)),
        ("Roaming",   "Fixed FVNO Subscribers",  fvno_subs,       safe_pct(fvno_subs, total_subs)),
        ("Data SIMs", "Total Data SIMs",         data_sims_total, safe_pct(data_sims_total, total_subs)),
        ("Data SIMs", "2G/3G Data SIMs",         data_2g3g,       safe_pct(data_2g3g, data_sims_total)),
        ("Data SIMs", "4G Data SIMs",            data_4g,         safe_pct(data_4g, data_sims_total)),
        ("Data SIMs", "5G Data SIMs",            data_5g,         safe_pct(data_5g, data_sims_total)),
        ("Data SIMs", "Corporate Data",          corporate_data,  safe_pct(corporate_data, total_subs)),
    ]
    kpi_df = pd.DataFrame(kpi_rows, columns=["Category", "Metric", "Subscribers", "% of Total"])
    _write_styled_sheet(ws, kpi_df, f"e& CS Core Operations — KPI Summary ({date_str})")

    # Sheet 2: CS Service Distribution
    if not df_tick.empty:
        ws = wb.create_sheet("CS Service Distribution")
        export_df = df_tick[['Service Type', 'Subscribers', 'Percentage']].copy()
        export_df['Percentage'] = export_df['Percentage'].apply(lambda x: f"{x:.1f}%")
        _write_styled_sheet(ws, export_df, "CS Service Type Distribution")

    # Sheet 3: Network Distribution
    if not df_network.empty:
        ws = wb.create_sheet("Network Distribution")
        net_df = df_network.copy()
        total_net = net_df['Subscribers'].sum()
        net_df['% of Total'] = net_df['Subscribers'].apply(lambda x: safe_pct(x, total_net))
        _write_styled_sheet(ws, net_df, "Network Technology Distribution")

    # Sheet 4: VoWiFi Status
    if not df_vowifi.empty:
        ws = wb.create_sheet("VoWiFi Status")
        wifi_df = df_vowifi.copy()
        total_wifi = wifi_df['Subscribers'].sum()
        wifi_df['% of Total'] = wifi_df['Subscribers'].apply(lambda x: safe_pct(x, total_wifi))
        _write_styled_sheet(ws, wifi_df, "VoWiFi Access Distribution")

    # Sheet 5: Call Features
    if not features_data.empty:
        ws = wb.create_sheet("Call Features")
        feat_df = features_data[['Feature', 'Count', 'Percentage']].copy()
        feat_df = feat_df.rename(columns={'Count': 'Subscribers'})
        feat_df['Percentage'] = feat_df['Percentage'].apply(lambda x: f"{x:.1f}%")
        _write_styled_sheet(ws, feat_df, "Call Features Adoption")

    # Sheet 6: Roaming by Country
    if not df_country.empty:
        ws = wb.create_sheet("Roaming by Country")
        country_df = df_country.copy()
        total_roam = country_df['Subscribers'].sum()
        country_df['% of Roaming'] = country_df['Subscribers'].apply(lambda x: safe_pct(x, total_roam))
        _write_styled_sheet(ws, country_df, "Top Roaming Countries")

    # Sheet 7: Roaming by Operator
    if not df_operator_display.empty:
        ws = wb.create_sheet("Roaming by Operator")
        _write_styled_sheet(ws, df_operator_display, "Top Roaming Operators")

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs(["📊 Statistics", "🔍 User Lookup", "🔎 Advanced Search & Filter", "📱 MNP Analysis", "🗂️ Reconciliation", "📈 Historical Trends", "🔒 PC/GT Audit", "📡 CNACLD Extractor"])

# ==================== TAB 1: STATISTICS ====================
with tab1:
    st.header("📊 Subscriber Statistics")

    # ==================== VOICE STATISTICS SECTION ====================
    st.subheader("📞 Voice Statistics")

    # COMBINED QUERY: All voice metrics in a single scan (disk-cached per date)
    voice_metrics_query = f"""
        SELECT
            uniqExact(MSISDN) AS total_subs,
            uniqExactIf(MSISDN, TICK = '215') AS volte_subs,
            uniqExactIf(MSISDN, EpsAccessRestriction = '0') AS vowifi_subs,
            uniqExactIf(MSISDN, length(EpsProfileId) >= 3 AND (EpsProfileId LIKE '3%' OR EpsProfileId LIKE '5%') AND EpsProfileId = PDPCP) AS fiveg_subs,
            uniqExactIf(MSISDN, VLRADD != '' AND VLRADD IS NOT NULL AND NOT startsWith(VLRADD, '1920117900')) AS roaming_subs,
            uniqExactIf(MSISDN, VLRADD != '' AND VLRADD IS NOT NULL AND startsWith(VLRADD, '1920117900')) AS local_subs,
            uniqExactIf(MSISDN, (VLRADD IS NULL OR VLRADD = '') AND (TICK IS NULL OR TICK = '') AND (PDPCP IS NULL OR PDPCP = '') AND length(MSISDN) >= 9 AND length(MSISDN) <= 11 AND MSISDN LIKE '%611%') AS fvno_subs
        FROM default.dump
        WHERE CDRtime = '{cdr_date_str_dash}'
    """
    rows, _ = run_cached_query_with_disk(voice_metrics_query, cdr_date_str, "voice_metrics")
    total_subs, volte_subs, vowifi_subs, fiveg_subs, roaming_subs, local_subs, fvno_subs = rows[0]

    # Main metrics
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Subscribers", f"{total_subs:,}")

    with col2:
        volte_pct = (volte_subs / total_subs * 100) if total_subs > 0 else 0
        st.metric("VoLTE Subscribers", f"{volte_subs:,}", f"{volte_pct:.1f}%", delta_color="normal")

    with col3:
        vowifi_pct = (vowifi_subs / total_subs * 100) if total_subs > 0 else 0
        st.metric("VoWiFi Subscribers", f"{vowifi_subs:,}", f"{vowifi_pct:.1f}%", delta_color="normal")

    with col4:
        fiveg_pct = (fiveg_subs / total_subs * 100) if total_subs > 0 else 0
        st.metric("5G Subscribers", f"{fiveg_subs:,}", f"{fiveg_pct:.1f}%", delta_color="normal")

    st.markdown("---")

    # Charts row 1
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("CS Service Type Distribution")
        # CS Only - 4 Categories: TICK 201 (CS RPT), TICK 205 (CS Call Screening),
        # TICK 203 (CS RPT + Call Screening), TICK 190 (Normal CS - 2G/3G without EpsProfileId)
        # Excludes VoLTE (TICK 215)
        query = f"""
            SELECT
                CASE
                    WHEN TICK = '201' THEN 'CS RPT'
                    WHEN TICK = '205' THEN 'CS Call Screening'
                    WHEN TICK = '203' THEN 'CS RPT + Call Screening'
                    WHEN TICK = '190' AND (EpsProfileId = '' OR EpsProfileId IS NULL) THEN 'Normal CS Subscriber'
                    WHEN (PDPCP = '' OR PDPCP IS NULL) AND TICK = '242' THEN 'PreActive Dial'
                    ELSE NULL
                END as service_type,
                uniqExact(MSISDN) as subscribers
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
              AND TICK IS NOT NULL AND TICK != ''
              AND TICK != '215'
            GROUP BY service_type
            HAVING service_type IS NOT NULL
            ORDER BY subscribers DESC
        """
        rows, cols = run_cached_query_with_disk(query, cdr_date_str, "cs_service_distribution")
        df_tick = pd.DataFrame(rows, columns=['Service Type', 'Subscribers'])

        # Calculate total CS subscribers and percentages
        total_cs_subs = df_tick['Subscribers'].sum()
        df_tick['Percentage'] = (df_tick['Subscribers'] / total_cs_subs * 100).round(1)
        df_tick['Label'] = df_tick.apply(lambda x: f"{x['Service Type']}<br>{x['Subscribers']:,} ({x['Percentage']:.1f}%)", axis=1)

        # Color mapping for service types
        tick_colors = {
            'CS RPT': '#3498db',
            'CS Call Screening': '#2ecc71',
            'CS RPT + Call Screening': '#9b59b6',
            'Normal CS Subscriber': '#e74c3c',
            'PreActive Dial': "#f36c12"
        }

        fig = px.pie(df_tick, values='Subscribers', names='Service Type',
                     title=f"CS Service Type Distribution)",
                     color='Service Type',
                     color_discrete_map=tick_colors)
        fig.update_traces(textposition='inside', textinfo='percent+label')
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Network Type Distribution")
        # 5 Categories: 2G/3G (includes all CS types), 4G, 5G, PreActive Dial
        query = f"""
            SELECT
                CASE
                    WHEN length(EpsProfileId) >= 3 AND (EpsProfileId LIKE '3%' OR EpsProfileId LIKE '5%') AND EpsProfileId = PDPCP THEN '5G'
                    WHEN TICK = '215' THEN '4G VoLTE'
                    WHEN TICK IN ('190', '201', '203', '205') AND (EpsProfileId = '' OR EpsProfileId IS NULL) THEN '2G/3G'
                    WHEN TICK IN ('190', '201', '203', '205') AND (EpsProfileId != '' AND EpsProfileId IS NOT NULL) THEN '4G'
                    ELSE NULL
                END as network_type,
                uniqExact(MSISDN) as subscribers
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
            GROUP BY network_type
            ORDER BY subscribers DESC
        """
        rows, cols = run_cached_query_with_disk(query, cdr_date_str, "network_distribution")
        df_network = pd.DataFrame(rows, columns=['Network Type', 'Subscribers'])
        # Remove NULL values from the chart
        df_network = df_network[df_network['Network Type'].notna()]

        # Color mapping for network types
        network_colors = {
            '2G/3G': '#e74c3c',
            '4G': '#f39c12',
            '4G VoLTE': '#3498db',
            '5G': '#2ecc71',
        }

        fig = px.pie(df_network, values='Subscribers', names='Network Type',
                     title="Network Technology Distribution",
                     color='Network Type',
                     color_discrete_map=network_colors)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # Charts row 2
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("VoWiFi Access Status")
        # Categories: 4G+WiFi, 5G+WiFi, Data Only, All Data Barred
        query = f"""
            SELECT
                CASE
                    WHEN EpsAccessRestriction = '0' AND PDPCP != '' AND PDPCP IN ('401', '467', '412', '405', '419', '407', '426', '420') THEN '4G + WiFi'
                    WHEN EpsAccessRestriction = '0' AND PDPCP != '' AND PDPCP IN ('501', '567', '512', '505', '519', '507', '526', '520') THEN '5G + WiFi'
                    WHEN EpsAccessRestriction = '32' THEN 'Data Only (No VoWiFi)'
                    WHEN EpsAccessRestriction = '63' THEN 'All Data Barred'
                    ELSE NULL
                END as status,
                uniqExact(MSISDN) as subscribers
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
              AND EpsAccessRestriction IS NOT NULL
              AND EpsAccessRestriction != ''
            GROUP BY status
            HAVING status IS NOT NULL
            ORDER BY subscribers DESC
        """
        rows, cols = run_cached_query_with_disk(query, cdr_date_str, "vowifi_distribution")
        df_vowifi = pd.DataFrame(rows, columns=['VoWiFi Status', 'Subscribers'])

        # Create color mapping
        color_map = {
            '4G + WiFi': '#3498db',           # Blue
            '5G + WiFi': '#2ecc71',           # Green
            'Data Only (No VoWiFi)': '#f39c12',  # Orange
            'All Data Barred': '#e74c3c'      # Red
        }

        fig = px.bar(df_vowifi, x='VoWiFi Status', y='Subscribers',
                     title="VoWiFi Access Distribution",
                     color='VoWiFi Status',
                     color_discrete_map=color_map,
                     text='Subscribers')
        fig.update_traces(texttemplate='%{text:,}', textposition='outside')
        fig.update_layout(showlegend=False, yaxis_title="Number of Subscribers")
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Call Features Adoption")
        query = f"""
            SELECT
                uniqExactIf(MSISDN, CFU = '1') as cfu_enabled,
                uniqExactIf(MSISDN, CFB = '1') as cfb_enabled,
                uniqExactIf(MSISDN, HOLD = '1') as hold_enabled,
                uniqExactIf(MSISDN, CAW = '1') as caw_enabled,
                uniqExactIf(MSISDN, DCF = '1') as mcn_enabled,
                uniqExactIf(MSISDN, CLIR = '1') as clir_enabled,
                uniqExactIf(MSISDN, COLP = '1') as colp_enabled,
                uniqExact(MSISDN) as total
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
        """
        rows, _ = run_cached_query_with_disk(query, cdr_date_str, "call_features")
        cfu, cfb, hold, caw, mcn, clir, colp, total = rows[0]

        features_data = pd.DataFrame({
            'Feature': ['CFU', 'CFB', 'HOLD', 'CAW', 'MCN', 'CLIR', 'COLP'],
            'Count': [cfu, cfb, hold, caw, mcn, clir, colp],
            'Percentage': [
                (cfu/total*100) if total > 0 else 0,
                (cfb/total*100) if total > 0 else 0,
                (hold/total*100) if total > 0 else 0,
                (caw/total*100) if total > 0 else 0,
                (mcn/total*100) if total > 0 else 0,
                (clir/total*100) if total > 0 else 0,
                (colp/total*100) if total > 0 else 0
            ]
        })

        fig = px.bar(features_data, x='Feature', y='Count',
                     title="Call Features Adoption",
                     color='Percentage',
                     color_continuous_scale='Oranges',
                     text=features_data.apply(lambda row: f"{row['Count']:,} ({row['Percentage']:.1f}%)", axis=1))
        fig.update_traces(textposition='outside')
        fig.update_layout(showlegend=False, yaxis_title="Number of Subscribers")
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # Roaming statistics - 3 Categories: Roaming, Egypt/Local, Fixed FVNO
    st.subheader("📡 Roaming Statistics")
    st.markdown("*Based on VLRADD: prefix `1920117900` = Egypt (Local), other = Roaming, Fixed FVNO = no VLR/TICK/PDPCP with 611 in MSISDN*")
    col1, col2, col3 = st.columns(3)

    with col1:
        roaming_pct = (roaming_subs / total_subs * 100) if total_subs > 0 else 0
        st.metric("Roaming Subscribers", f"{roaming_subs:,}", f"{roaming_pct:.1f}%", delta_color="normal")

    with col2:
        local_pct = (local_subs / total_subs * 100) if total_subs > 0 else 0
        st.metric("Egypt/Local", f"{local_subs:,}", f"{local_pct:.1f}%", delta_color="normal")

    with col3:
        fvno_pct = (fvno_subs / total_subs * 100) if total_subs > 0 else 0
        st.metric("Fixed FVNO", f"{fvno_subs:,}", f"{fvno_pct:.1f}%", delta_color="normal")

    # Roaming subscribers by country (top 10)
    df_country = pd.DataFrame()
    roaming_country_query = f"""
        SELECT
            CASE
                WHEN startsWith(VLRADD, '19966') THEN 'Saudi Arabia'
                WHEN startsWith(VLRADD, '19971') THEN 'UAE'
                WHEN startsWith(VLRADD, '19218') THEN 'Libya'
                WHEN startsWith(VLRADD, '19249') THEN 'Sudan'
                WHEN startsWith(VLRADD, '19965') THEN 'Kuwait'
                WHEN startsWith(VLRADD, '19962') THEN 'Jordan'
                WHEN startsWith(VLRADD, '19963') THEN 'Syria'
                WHEN startsWith(VLRADD, '19964') THEN 'Iraq'
                WHEN startsWith(VLRADD, '19968') THEN 'Oman'
                WHEN startsWith(VLRADD, '19974') THEN 'Qatar'
                WHEN startsWith(VLRADD, '19973') THEN 'Bahrain'
                WHEN startsWith(VLRADD, '19967') THEN 'Yemen'
                WHEN startsWith(VLRADD, '19961') THEN 'Lebanon'
                WHEN startsWith(VLRADD, '19972') THEN 'Palestine'
                WHEN startsWith(VLRADD, '19212') THEN 'Morocco'
                WHEN startsWith(VLRADD, '19213') THEN 'Algeria'
                WHEN startsWith(VLRADD, '19216') THEN 'Tunisia'
                WHEN startsWith(VLRADD, '19251') THEN 'Ethiopia'
                WHEN startsWith(VLRADD, '1944')  THEN 'UK'
                WHEN startsWith(VLRADD, '1933')  THEN 'France'
                WHEN startsWith(VLRADD, '1949')  THEN 'Germany'
                WHEN startsWith(VLRADD, '1939')  THEN 'Italy'
                WHEN startsWith(VLRADD, '1934')  THEN 'Spain'
                WHEN startsWith(VLRADD, '1931')  THEN 'Netherlands'
                ELSE 'Other'
            END AS country,
            uniq(MSISDN) AS subscriber_count
        FROM default.dump
        WHERE CDRtime = '{cdr_date_str_dash}'
          AND VLRADD != ''
          AND VLRADD IS NOT NULL
          AND NOT startsWith(VLRADD, '1920117900')
        GROUP BY country
        HAVING country != 'Other'
        ORDER BY subscriber_count DESC
        LIMIT 10
    """
    try:
        country_rows, _ = run_cached_query_with_disk(roaming_country_query, cdr_date_str, "roaming_country_distribution")
        if country_rows:
            df_country = pd.DataFrame(country_rows, columns=['Country', 'Subscribers'])

            col_chart1, col_chart2 = st.columns([3, 2])

            with col_chart1:
                fig_bar = go.Figure(go.Bar(
                    x=df_country['Subscribers'],
                    y=df_country['Country'],
                    orientation='h',
                    marker_color='#3498db',
                    text=df_country['Subscribers'].apply(lambda v: f"{v:,}"),
                    textposition='outside'
                ))
                fig_bar.update_layout(
                    title="Top 10 Roaming Countries (Subscriber Count)",
                    xaxis_title="Subscribers",
                    yaxis=dict(autorange='reversed'),
                    height=400,
                    plot_bgcolor='white',
                    xaxis=dict(showgrid=True, gridcolor='lightgrey'),
                )
                st.plotly_chart(fig_bar, use_container_width=True)

            with col_chart2:
                fig_pie = px.pie(
                    df_country,
                    names='Country',
                    values='Subscribers',
                    title="Roaming Share by Country"
                )
                fig_pie.update_traces(textposition='inside', textinfo='percent+label')
                fig_pie.update_layout(height=400, showlegend=False)
                st.plotly_chart(fig_pie, use_container_width=True)

            st.dataframe(df_country, use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"Error loading roaming country data: {e}")

    st.markdown("---")

    # ==================== TOP 10 ROAMING OPERATORS SECTION ====================
    st.subheader("📡 Top 10 Roaming Operators")

    df_operator_display = pd.DataFrame()
    roaming_operator_query = f"""
        SELECT
            CASE
                -- Saudi Arabia (966)
                WHEN startsWith(VLRADD, '1996650') THEN 'Saudi Arabia - STC'
                WHEN startsWith(VLRADD, '1996656') THEN 'Saudi Arabia - Mobily'
                WHEN startsWith(VLRADD, '1996659') THEN 'Saudi Arabia - Zain'
                -- UAE (971)
                WHEN startsWith(VLRADD, '1997150') THEN 'UAE - Etisalat'
                WHEN startsWith(VLRADD, '1997155') THEN 'UAE - du'
                -- Kuwait (965)
                WHEN startsWith(VLRADD, '19965500') THEN 'Kuwait - STC'
                WHEN startsWith(VLRADD, '1996596')  THEN 'Kuwait - Zain'
                WHEN startsWith(VLRADD, '199656')   THEN 'Kuwait - Ooredoo'
                -- Qatar (974)
                WHEN startsWith(VLRADD, '1997477') THEN 'Qatar - Ooredoo'
                WHEN startsWith(VLRADD, '1997455') THEN 'Qatar - Vodafone'
                -- Jordan (962)
                WHEN startsWith(VLRADD, '1996279') THEN 'Jordan - Orange'
                WHEN startsWith(VLRADD, '1996277') THEN 'Jordan - Umniah'
                WHEN startsWith(VLRADD, '1996278') THEN 'Jordan - Zain'
                -- Bahrain (973)
                WHEN startsWith(VLRADD, '1997333') THEN 'Bahrain - STC'
                WHEN startsWith(VLRADD, '1997336') THEN 'Bahrain - Zain'
                WHEN startsWith(VLRADD, '1997339') THEN 'Bahrain - Batelco'
                -- Iraq (964)
                WHEN startsWith(VLRADD, '199647701') THEN 'Iraq - Asiacell'
                WHEN startsWith(VLRADD, '199647802') THEN 'Iraq - Korek'
                WHEN startsWith(VLRADD, '1996475')   THEN 'Iraq - Zain'
                -- Syria (963)
                WHEN startsWith(VLRADD, '1996394') THEN 'Syria - Syriatel'
                WHEN startsWith(VLRADD, '1996393') THEN 'Syria - MTN'
                -- Yemen (967)
                WHEN startsWith(VLRADD, '1996771') THEN 'Yemen - Sabafon'
                WHEN startsWith(VLRADD, '1996770') THEN 'Yemen - MTN'
                -- Oman (968)
                WHEN startsWith(VLRADD, '1996895') THEN 'Oman - Ooredoo'
                WHEN startsWith(VLRADD, '1996892') THEN 'Oman - Omantel'
                -- Libya (218)
                WHEN startsWith(VLRADD, '1921891') THEN 'Libya - Almadar'
                WHEN startsWith(VLRADD, '1921892') THEN 'Libya - Libyana'
                -- Lebanon (961)
                WHEN startsWith(VLRADD, '1996134') THEN 'Lebanon - Alfa'
                WHEN startsWith(VLRADD, '1996139') THEN 'Lebanon - Touch'
                -- Palestine (972)
                WHEN startsWith(VLRADD, '1997259') THEN 'Palestine - Jawwal'
                WHEN startsWith(VLRADD, '1997256') THEN 'Palestine - Ooredoo'
                -- Sudan (249)
                WHEN startsWith(VLRADD, '1924991') THEN 'Sudan - Zain'
                WHEN startsWith(VLRADD, '1924912') THEN 'Sudan - Sudani'
                WHEN startsWith(VLRADD, '1924995') THEN 'Sudan - Vivacell'
                -- Morocco (212)
                WHEN startsWith(VLRADD, '192126639') THEN 'Morocco - Orange'
                WHEN startsWith(VLRADD, '19212661')  THEN 'Morocco - Maroc Telecom'
                WHEN startsWith(VLRADD, '19212640')  THEN 'Morocco - inwi'
                -- Algeria (213)
                WHEN startsWith(VLRADD, '19213661') THEN 'Algeria - Mobilis'
                WHEN startsWith(VLRADD, '19213770') THEN 'Algeria - Djezzy'
                WHEN startsWith(VLRADD, '1921350')  THEN 'Algeria - Ooredoo'
                -- Tunisia (216)
                WHEN startsWith(VLRADD, '192165')  THEN 'Tunisia - Tunisie Telecom'
                WHEN startsWith(VLRADD, '1921622') THEN 'Tunisia - Ooredoo'
                WHEN startsWith(VLRADD, '1921698') THEN 'Tunisia - Orange'
                -- Ethiopia (251)
                WHEN startsWith(VLRADD, '1925191') THEN 'Ethiopia - Ethio Telecom'
                -- Turkey (90)
                WHEN startsWith(VLRADD, '1990505') THEN 'Turkey - Turk Telekom'
                WHEN startsWith(VLRADD, '1990559') THEN 'Turkey - Turkcell'
                WHEN startsWith(VLRADD, '1990532') THEN 'Turkey - Vodafone'
                -- UK (44)
                WHEN startsWith(VLRADD, '19447781')  THEN 'UK - O2'
                WHEN startsWith(VLRADD, '19447782')  THEN 'UK - EE'
                WHEN startsWith(VLRADD, '19447802')  THEN 'UK - EE'
                WHEN startsWith(VLRADD, '1944973')   THEN 'UK - Vodafone'
                WHEN startsWith(VLRADD, '19447953')  THEN 'UK - O2'
                WHEN startsWith(VLRADD, '1944385')   THEN 'UK - Vodafone'
                WHEN startsWith(VLRADD, '194478297') THEN 'UK - Vodafone'
                WHEN startsWith(VLRADD, '19447624')  THEN 'UK - Vodafone'
                WHEN startsWith(VLRADD, '19447797')  THEN 'UK - Sure'
                -- France (33)
                WHEN startsWith(VLRADD, '1933660') THEN 'France - Bouygues'
                WHEN startsWith(VLRADD, '1933689') THEN 'France - Orange'
                WHEN startsWith(VLRADD, '1933609') THEN 'France - SFR'
                WHEN startsWith(VLRADD, '1933695') THEN 'France - Free'
                -- Germany (49)
                WHEN startsWith(VLRADD, '1949176') THEN 'Germany - O2'
                WHEN startsWith(VLRADD, '1949177') THEN 'Germany - Telekom'
                WHEN startsWith(VLRADD, '1949171') THEN 'Germany - Vodafone'
                WHEN startsWith(VLRADD, '1949172') THEN 'Germany - O2'
                -- Italy (39)
                WHEN startsWith(VLRADD, '1939391')  THEN 'Italy - Wind Tre'
                WHEN startsWith(VLRADD, '1939339')  THEN 'Italy - Wind Tre'
                WHEN startsWith(VLRADD, '1939320')  THEN 'Italy - Wind Tre'
                WHEN startsWith(VLRADD, '1939335')  THEN 'Italy - TIM'
                WHEN startsWith(VLRADD, '1939349')  THEN 'Italy - TIM'
                WHEN startsWith(VLRADD, '19393519') THEN 'Italy - TIM'
                -- Spain (34)
                WHEN startsWith(VLRADD, '19346404') THEN 'Spain - Airtel'
                WHEN startsWith(VLRADD, '1934607')  THEN 'Spain - Movistar'
                WHEN startsWith(VLRADD, '1934609')  THEN 'Spain - Orange'
                WHEN startsWith(VLRADD, '1934622')  THEN 'Spain - Yoigo'
                -- Pakistan (92)
                WHEN startsWith(VLRADD, '1992345') THEN 'Pakistan - Jazz'
                WHEN startsWith(VLRADD, '1992333') THEN 'Pakistan - Jazz'
                WHEN startsWith(VLRADD, '1992321') THEN 'Pakistan - Jazz'
                WHEN startsWith(VLRADD, '199231')  THEN 'Pakistan - Jazz'
                WHEN startsWith(VLRADD, '1992300') THEN 'Pakistan - Zong'
                -- India (91)
                WHEN startsWith(VLRADD, '199197')  THEN 'India - Aircell'
                WHEN startsWith(VLRADD, '199198')  THEN 'India - Airtel'
                WHEN startsWith(VLRADD, '1991981') THEN 'India - Airtel'
                WHEN startsWith(VLRADD, '199195')  THEN 'India - Tata'
                WHEN startsWith(VLRADD, '199196')  THEN 'India - Vodafone Idea'
                -- Netherlands (31)
                WHEN startsWith(VLRADD, '193165') THEN 'Netherlands - KPN'
                WHEN startsWith(VLRADD, '193162') THEN 'Netherlands - Vodafone'
                WHEN startsWith(VLRADD, '193154') THEN 'Netherlands - T-Mobile'
                WHEN startsWith(VLRADD, '193163') THEN 'Netherlands - T-Mobile'
                ELSE ''
            END AS operator_label,
            uniq(MSISDN) AS subscriber_count
        FROM default.dump
        WHERE CDRtime = '{cdr_date_str_dash}'
          AND VLRADD != ''
          AND VLRADD IS NOT NULL
          AND NOT startsWith(VLRADD, '1920117900')
        GROUP BY operator_label
        HAVING operator_label != ''
        ORDER BY subscriber_count DESC
        LIMIT 10
    """
    try:
        operator_rows, _ = run_cached_query_with_disk(roaming_operator_query, cdr_date_str, "roaming_operator_distribution")
        if operator_rows:
            df_operator = pd.DataFrame(operator_rows, columns=['Operator', 'Subscribers'])

            col_chart1, col_chart2 = st.columns([3, 2])

            with col_chart1:
                fig_bar = go.Figure(go.Bar(
                    x=df_operator['Subscribers'],
                    y=df_operator['Operator'],
                    orientation='h',
                    marker_color='#e74c3c',
                    text=df_operator['Subscribers'].apply(lambda v: f"{v:,}"),
                    textposition='outside'
                ))
                fig_bar.update_layout(
                    title="Top 10 Roaming Operators (Subscriber Count)",
                    xaxis_title="Subscribers",
                    yaxis=dict(autorange='reversed'),
                    height=400,
                    plot_bgcolor='white',
                    xaxis=dict(showgrid=True, gridcolor='lightgrey'),
                )
                st.plotly_chart(fig_bar, use_container_width=True)

            with col_chart2:
                fig_pie = px.pie(
                    df_operator,
                    names='Operator',
                    values='Subscribers',
                    title="Roaming Share by Operator"
                )
                fig_pie.update_traces(textposition='inside', textinfo='percent+label')
                fig_pie.update_layout(height=400, showlegend=False)
                st.plotly_chart(fig_pie, use_container_width=True)

            # Split "Country - Operator" into separate columns for cleaner display
            df_operator_display = df_operator.copy()
            split_result = df_operator_display['Operator'].str.split(' - ', n=1, expand=True)
            df_operator_display.insert(0, 'Country', split_result[0])
            df_operator_display['Operator'] = split_result[1] if 1 in split_result.columns else split_result[0]
            total_roaming = df_operator_display['Subscribers'].sum()
            df_operator_display['% of Shown'] = (
                df_operator_display['Subscribers'] / total_roaming * 100
            ).map(lambda v: f"{v:.1f}%")
            st.dataframe(
                df_operator_display[['Country', 'Operator', 'Subscribers', '% of Shown']],
                use_container_width=True,
                hide_index=True
            )
    except Exception as e:
        st.error(f"Error loading roaming operator data: {e}")

    st.markdown("---")

    st.subheader("SCHAR Distribution")

    schar_query = f"""
        SELECT
            CASE SCHAR
                WHEN '2' THEN 'Data SIM'
                WHEN '0' THEN 'Mobile Internet'
                WHEN '5' THEN 'Kidzo1'
                WHEN '6' THEN 'Kidzo2'
                ELSE 'Other'
            END AS SCHARType,
            uniqExact(MSISDN) AS Subscribers
        FROM default.dump
        WHERE CDRtime = '{cdr_date_str_dash}'
        AND SCHAR IS NOT NULL AND SCHAR != ''
        GROUP BY SCHARType
        ORDER BY Subscribers DESC
    """
    rows, _ = run_cached_query_with_disk(schar_query, cdr_date_str, "schar_distribution")
    df_schar = pd.DataFrame(rows, columns=['Type', 'Subscribers'])

    fig = px.pie(df_schar, values='Subscribers', names='Type',
                title="Subscribers by SCHAR Type")
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # ==================== DATA SIMs STATISTICS SECTION ====================
    st.subheader("📶 Data SIMs Statistics")
    st.markdown("*Data SIMs: Subscribers with PDPCP or EpsUserIpV4Address (APN) but without TICK (no voice service)*")

    # COMBINED QUERY: All Data SIMs metrics in a single scan
    eps_5g_list = ['5115', '503', '554', '524', '585', '536', '530', '535', '511', '533', '537', '531', '540', '578', '579', '580', '592', '593', '594', '595', '557', '5111', '5113']
    eps_5g_str = "', '".join(eps_5g_list)
    data_sims_query = f"""
        SELECT
            uniqExactIf(MSISDN,
                (TICK IS NULL OR TICK = '')
                AND ((PDPCP IS NOT NULL AND PDPCP != '') OR (EpsIndDefContextId IS NOT NULL AND EpsIndDefContextId != ''))
            ) AS data_sims_total,
            uniqExactIf(MSISDN,
                (TICK IS NULL OR TICK = '')
                AND ((PDPCP IS NOT NULL AND PDPCP != '') OR (EpsIndDefContextId IS NOT NULL AND EpsIndDefContextId != ''))
                AND (EpsProfileId IS NULL OR EpsProfileId = '')
            ) AS data_2g3g,
            uniqExactIf(MSISDN,
                (TICK IS NULL OR TICK = '')
                AND ((PDPCP IS NOT NULL AND PDPCP != '') OR (EpsIndDefContextId IS NOT NULL AND EpsIndDefContextId != ''))
                AND EpsProfileId IN ('{eps_5g_str}')
            ) AS data_5g,
            uniqExactIf(MSISDN,
                EpsIndDefContextId IS NOT NULL AND EpsIndDefContextId != ''
                AND NOT endsWith(EpsIndDefContextId, '37') AND NOT endsWith(EpsIndDefContextId, '00')
            ) AS corporate_data
        FROM default.dump
        WHERE CDRtime = '{cdr_date_str_dash}'
    """
    rows, _ = run_cached_query_with_disk(data_sims_query, cdr_date_str, "data_sims_metrics")
    data_sims_total, data_2g3g, data_5g, corporate_data = rows[0]
    data_4g = max(0, data_sims_total - data_2g3g - data_5g)

    # Data SIMs metrics
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        data_pct = (data_sims_total / total_subs * 100) if total_subs > 0 else 0
        st.metric("Total Data SIMs", f"{data_sims_total:,}", f"{data_pct:.1f}%", delta_color="normal")

    with col2:
        data_2g3g_pct = (data_2g3g / data_sims_total * 100) if data_sims_total > 0 else 0
        st.metric("2G/3G Data SIMs", f"{data_2g3g:,}", f"{data_2g3g_pct:.1f}%", delta_color="normal")

    with col3:
        data_5g_pct = (data_5g / data_sims_total * 100) if data_sims_total > 0 else 0
        st.metric("5G Data SIMs", f"{data_5g:,}", f"{data_5g_pct:.1f}%", delta_color="normal")

    with col4:
        data_4g_pct = (data_4g / data_sims_total * 100) if data_sims_total > 0 else 0
        st.metric("4G Data SIMs", f"{data_4g:,}", f"{data_4g_pct:.1f}%", delta_color="normal")

    with col5:
        corporate_pct = (corporate_data / total_subs * 100) if total_subs > 0 else 0
        st.metric("Corporate Data Dial", f"{corporate_data:,}", f"{corporate_pct:.1f}%", delta_color="normal")

    st.markdown("---")

    # Data SIMs Charts
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Data SIMs Distribution")
        # Pie chart for Data SIMs types (4G = Total - 2G/3G - 5G, so no "Other" needed)
        data_sims_data = pd.DataFrame({
            'Type': ['2G/3G Data SIMs', '4G Data SIMs', '5G Data SIMs'],
            'Count': [data_2g3g, data_4g, data_5g]
        })
        data_sims_data = data_sims_data[data_sims_data['Count'] > 0]

        data_colors = {
            '2G/3G Data SIMs': '#e74c3c',
            '4G Data SIMs': '#f39c12',
            '5G Data SIMs': '#2ecc71'
        }

        fig = px.pie(data_sims_data, values='Count', names='Type',
                     title="Data SIMs by Network Type",
                     color='Type',
                     color_discrete_map=data_colors)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Top EpsProfileId for Data SIMs")
        # Bar chart for top EpsProfileId values
        query = f"""
            SELECT
                CASE
                    WHEN EpsProfileId IS NULL OR EpsProfileId = '' THEN '2G/3G'
                    ELSE EpsProfileId
                END as EpsProfileId,
                uniqExact(MSISDN) as subscribers
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
              AND (TICK IS NULL OR TICK = '')
              AND (
                (PDPCP IS NOT NULL AND PDPCP != '')
                OR (EpsIndDefContextId IS NOT NULL AND EpsIndDefContextId != '')
              )
            GROUP BY EpsProfileId
            ORDER BY subscribers DESC
            LIMIT 10
        """
        rows, _ = run_cached_query_with_disk(query, cdr_date_str, "data_sims_eps_profile")
        df_eps = pd.DataFrame(rows, columns=['EpsProfileId', 'Subscribers'])
        # Convert EpsProfileId to string to treat as categorical
        df_eps['EpsProfileId'] = df_eps['EpsProfileId'].astype(str)
        # Sort by Subscribers descending for display
        df_eps = df_eps.sort_values('Subscribers', ascending=False)

        fig = px.bar(df_eps, x='EpsProfileId', y='Subscribers',
                     title="Top 10 EpsProfileId for Data SIMs",
                     color='Subscribers',
                     color_continuous_scale='Blues',
                     text='Subscribers')
        fig.update_traces(texttemplate='%{text:,}', textposition='outside')
        fig.update_layout(showlegend=False, xaxis_title="EpsProfileId", yaxis_title="Subscribers",
                         xaxis={'type': 'category', 'categoryorder': 'total descending'})
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    st.subheader("📥 Export Statistics Report")
    st.markdown("Download all statistics as a formatted Excel workbook with separate sheets for each section.")
    excel_bytes = create_stats_excel_report(
        date_str=cdr_date_str,
        total_subs=total_subs, volte_subs=volte_subs, vowifi_subs=vowifi_subs, fiveg_subs=fiveg_subs,
        roaming_subs=roaming_subs, local_subs=local_subs, fvno_subs=fvno_subs,
        data_sims_total=data_sims_total, data_2g3g=data_2g3g, data_4g=data_4g,
        data_5g=data_5g, corporate_data=corporate_data,
        df_tick=df_tick, df_network=df_network, df_vowifi=df_vowifi,
        features_data=features_data, df_country=df_country, df_operator_display=df_operator_display,
    )
    st.download_button(
        label="📊 Download Statistics Report (Excel)",
        data=excel_bytes,
        file_name=f"eand_core_stats_{cdr_date_str}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# ==================== TAB 2: USER LOOKUP ====================
with tab2:
    st.header("🔍 User Profile Lookup")
    st.markdown("Search for a specific subscriber by MSISDN or IMSI")

    # Search options
    search_type = st.radio("Search by:", ["MSISDN", "IMSI"], horizontal=True)

    if search_type == "MSISDN":
        search_value = st.text_input("Enter MSISDN (e.g., 201000089328):", max_chars=15)
        search_field = "MSISDN"
    else:
        search_value = st.text_input("Enter IMSI (e.g., 602020000000001):", max_chars=15)
        search_field = "IMSI"

    if st.button("Search", type="primary"):
        if not search_value:
            st.warning("Please enter a value to search")
        else:
            try:
                # Search for user (using cached query for faster performance)
                query = f"""
                    SELECT
                        MSISDN,
                        IMSI,
                        TICK,
                        PDPCP,
                        EpsProfileId,
                        EpsIndDefContextId,
                        EpsStnSr,
                        EpsUserIpV4Address,
                        CFU,
                        CFB,
                        HOLD,
                        CAW,
                        TS11,
                        TS21,
                        TS22,
                        DCF,
                        EpsAccessRestriction,
                        CDRtime,
                        CFUT10FNUM,
                        CFBTS10FNUM,
                        CFNRCTS10FNUM,
                        CFNRYTS10FNUM,
                        DCFTS10FNUM
                    FROM default.dump
                    WHERE {search_field} = '{search_value}' AND CDRtime = '{cdr_date_str_dash}'
                    LIMIT 1
                """
                result_rows, column_names = run_user_lookup_cached(query)

                if not result_rows:
                    st.error(f"No records found for {search_field} = {search_value} on {cdr_date_str}")
                else:
                    st.success(f"Found profile for {search_field} = {search_value}")

                    # Convert to DataFrame
                    df_user = pd.DataFrame(result_rows, columns=[
                        'MSISDN', 'IMSI', 'TICK', 'PDPCP', 'EpsProfileId', 'EpsIndDefContextId',
                        'EpsStnSr', 'EpsUserIpV4Address', 'CFU', 'CFB', 'HOLD', 'CAW',
                        'TS11', 'TS21', 'TS22', 'DCF', 'EpsAccessRestriction', 'CDRtime',
                        'CFUT10FNUM', 'CFBTS10FNUM', 'CFNRCTS10FNUM', 'CFNRYTS10FNUM', 'DCFTS10FNUM'
                    ])

                    # Display main profile info
                    st.subheader("📋 Subscriber Profile")

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.markdown(f"**MSISDN:** {df_user['MSISDN'].iloc[0]}")
                        st.markdown(f"**IMSI:** {df_user['IMSI'].iloc[0]}")
                        st.markdown(f"**EpsProfileId:** {df_user['EpsProfileId'].iloc[0] or 'N/A'}")
                    with col2:
                        # Decode TICK
                        tick_val = df_user['TICK'].iloc[0]
                        tick_desc = {
                            '215': 'VoLTE',
                            '201': 'RPT (Call Tone)',
                            '203': 'RPT + Call Screening',
                            '205': 'Call Screening',
                            'N': 'Not Configured'
                        }.get(tick_val, f'TICK {tick_val}')
                        st.markdown(f"**Service Type (TICK):** {tick_desc}")

                        # Decode PDPCP
                        pdpcp_val = str(df_user['PDPCP'].iloc[0] or '')
                        if pdpcp_val.startswith('5'):
                            network = '5G'
                        elif pdpcp_val.startswith('4'):
                            network = '4G+'
                        elif pdpcp_val.startswith('3'):
                            network = '4G'
                        elif pdpcp_val.startswith('1'):
                            network = '3G/2G'
                        else:
                            network = 'Other'
                        st.markdown(f"**Network Type (PDPCP):** {network} ({pdpcp_val})")

                    with col3:
                        eps_val = str(df_user['EpsAccessRestriction'].iloc[0] or '')
                        if eps_val == '0':
                            vowifi_status = "✅ VoWiFi Enabled"
                        elif eps_val == '32':
                            vowifi_status = "⚠️ Data Only (No VoWiFi)"
                        elif eps_val == '128':
                            vowifi_status = "❌ All Data Barred"
                        else:
                            vowifi_status = f"⚠️ Unknown ({eps_val})"
                        st.markdown(f"**VoWiFi Status:** {vowifi_status}")
                        st.markdown(f"**EpsAccessRestriction:** {eps_val}")
                        st.markdown(f"**Data Date:** {df_user['CDRtime'].iloc[0]}")

                    st.markdown("---")

                    # EPS Parameters Section
                    st.subheader("📡 EPS Parameters")
                    col1, col2 = st.columns(2)

                    with col1:
                        # VoLTE APN (EpsIndDefContextId)
                        apn_val = df_user['EpsIndDefContextId'].iloc[0]
                        if tick_val == '215' and (not apn_val or apn_val == ''):
                            st.markdown(f"**EpsIndDefContextId (VoLTE APN):** ⚠️ **MISSING** - Problem Profile!")
                        else:
                            st.markdown(f"**EpsIndDefContextId (VoLTE APN):** {apn_val or 'N/A'}")

                        # EpsStnSr
                        stnsr_val = df_user['EpsStnSr'].iloc[0]
                        st.markdown(f"**EpsStnSr:** {stnsr_val or 'N/A'}")

                    with col2:
                        # EpsUserIpV4Address - Parse APN and IP
                        ip_addr_val = df_user['EpsUserIpV4Address'].iloc[0]
                        st.markdown(f"**EpsUserIpV4Address:** {ip_addr_val or 'N/A'}")

                        if ip_addr_val and '$' in str(ip_addr_val):
                            parts = str(ip_addr_val).split('$')
                            apn_code = parts[0][-3:] if len(parts[0]) >= 3 else parts[0]
                            ip_part = parts[1] if len(parts) > 1 else ''
                            st.markdown(f"  - **APN Code:** {apn_code}")
                            st.markdown(f"  - **IP Address:** {ip_part}")

                    st.markdown("---")

                    # Call features status
                    st.subheader("📞 Call Features")
                    col1, col2, col3, col4 = st.columns(4)

                    # Aggregate call features (take max across all profiles)
                    cfu_status = "✅ Enabled" if df_user['CFU'].max() == '1' else "❌ Disabled"
                    cfb_status = "✅ Enabled" if df_user['CFB'].max() == '1' else "❌ Disabled"
                    hold_status = "✅ Enabled" if df_user['HOLD'].max() == '1' else "❌ Disabled"
                    caw_status = "✅ Enabled" if df_user['CAW'].max() == '1' else "❌ Disabled"

                    with col1:
                        st.markdown(f"**CFU:** {cfu_status}")
                    with col2:
                        st.markdown(f"**CFB:** {cfb_status}")
                    with col3:
                        st.markdown(f"**HOLD:** {hold_status}")
                    with col4:
                        st.markdown(f"**CAW:** {caw_status}")

                    st.markdown("---")

                    # Call Forwarding Destinations
                    st.subheader("📲 Call Forwarding Destinations")
                    fwd_entries = [
                        ("CFU (Unconditional)",   'CFUT10FNUM'),
                        ("CFB (Busy)",            'CFBTS10FNUM'),
                        ("CFNRC (No Reply)",      'CFNRCTS10FNUM'),
                        ("CFNRY (No Reply 2)",    'CFNRYTS10FNUM'),
                        ("DCF (Default)",         'DCFTS10FNUM'),
                    ]
                    any_fwd = False
                    fwd_cols = st.columns(len(fwd_entries))
                    for col_widget, (label, attr) in zip(fwd_cols, fwd_entries):
                        fwd_num = df_user[attr].iloc[0]
                        if fwd_num and str(fwd_num).strip() not in ('', 'None', 'nan'):
                            col_widget.markdown(f"**{label}**")
                            col_widget.markdown(f"`{fwd_num}`")
                            any_fwd = True
                        else:
                            col_widget.markdown(f"**{label}**")
                            col_widget.markdown("—")
                    if not any_fwd:
                        st.info("No call forwarding destinations configured for this subscriber.")

                    st.markdown("---")

                    # Supplementary services
                    st.subheader("🔧 Supplementary Services")
                    col1, col2, col3 = st.columns(3)

                    with col1:
                        ts11_val = df_user['TS11'].max()
                        ts11_status = "✅ Active" if ts11_val == '1' else "❌ Inactive"
                        st.markdown(f"**TS11:** {ts11_status}")
                    with col2:
                        ts21_val = df_user['TS21'].max()
                        ts21_status = "✅ Active" if ts21_val == '1' else "❌ Inactive"
                        st.markdown(f"**TS21:** {ts21_status}")
                    with col3:
                        ts22_val = df_user['TS22'].max()
                        ts22_status = "✅ Active" if ts22_val == '1' else "❌ Inactive"
                        st.markdown(f"**TS22:** {ts22_status}")

                    st.markdown("---")

                    # Detailed profile table
                    st.subheader("📊 Detailed Profile Records")
                    st.markdown(f"**Total profiles for this subscriber:** {len(df_user)}")
                    st.dataframe(df_user, use_container_width=True, height=400)

                    # Export option
                    csv = df_user.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Profile Data (CSV)",
                        data=csv,
                        file_name=f"profile_{search_value}_{cdr_date_str}.csv",
                        mime="text/csv"
                    )

            except Exception as e:
                st.error(f"Error searching for user: {e}")

    st.markdown("---")

    # Bulk search option
    st.subheader("📁 Bulk User Lookup")
    st.markdown("Upload a file with multiple MSISDNs (one per line) to get all profiles")

    uploaded_file = st.file_uploader("Upload text file with MSISDNs", type=['txt', 'csv'])

    if uploaded_file is not None:
        try:
            # Read MSISDNs from file
            content = uploaded_file.read().decode('utf-8')
            msisdns = [line.strip() for line in content.split('\n') if line.strip()]

            st.info(f"Found {len(msisdns)} MSISDNs in file")

            if st.button("Search All", type="primary"):
                with st.spinner(f"Searching for {len(msisdns)} subscribers..."):
                    # Build query for all MSISDNs (using cached query for faster performance)
                    msisdn_list = "','".join(msisdns)
                    query = f"""
                        SELECT
                            MSISDN,
                            IMSI,
                            TICK,
                            PDPCP,
                            CFU,
                            CFB,
                            HOLD,
                            CAW,
                            TS11,
                            TS21,
                            TS22,
                            EpsAccessRestriction,
                            COUNT(*) as record_count
                        FROM default.dump
                        WHERE MSISDN IN ('{msisdn_list}') AND CDRtime = '{cdr_date_str_dash}'
                        GROUP BY MSISDN, IMSI, TICK, PDPCP, CFU, CFB, HOLD, CAW, TS11, TS21, TS22, EpsAccessRestriction
                    """
                    result_rows, column_names = run_user_lookup_cached(query)

                    if not result_rows:
                        st.error("No records found for any of the MSISDNs")
                    else:
                        df_bulk = pd.DataFrame(result_rows, columns=[
                            'MSISDN', 'IMSI', 'TICK', 'PDPCP', 'CFU', 'CFB', 'HOLD', 'CAW',
                            'TS11', 'TS21', 'TS22', 'EpsAccessRestriction', 'Record Count'
                        ])

                        st.success(f"Found {len(df_bulk)} profile records for {df_bulk['MSISDN'].nunique()} subscribers")
                        st.dataframe(df_bulk, use_container_width=True, height=400)

                        # Export
                        csv = df_bulk.to_csv(index=False)
                        st.download_button(
                            label="📥 Download All Profiles (CSV)",
                            data=csv,
                            file_name=f"bulk_profiles_{cdr_date_str}.csv",
                            mime="text/csv"
                        )
        except Exception as e:
            st.error(f"Error processing file: {e}")

# ==================== TAB 3: ADVANCED SEARCH & FILTER ====================
with tab3:
    st.header("🔎 Advanced Search & Filter")
    st.markdown("Build custom queries with multiple filters to find specific subscribers")

    st.subheader("🔧 Filter Criteria")

    col1, col2 = st.columns(2)

    with col1:
        # Service Type Filter (TICK)
        st.markdown("**Service Type (TICK)**")
        tick_filter = st.multiselect(
            "Select service types:",
            options=['VoLTE (215)', 'RPT Call Tone (201)', 'RPT + Screening (203)', 'Call Screening (205)', 'Any'],
            default=['Any'],
            key="adv_tick_filter"
        )

        # Network Type Filter (PDPCP)
        st.markdown("**Network Type (PDPCP)**")
        network_filter = st.multiselect(
            "Select network types:",
            options=['5G', '4G+', '4G', '3G/2G', 'Any'],
            default=['Any'],
            key="adv_network_filter"
        )

        # VoWiFi Filter
        vowifi_filter = st.selectbox(
            "VoWiFi Status:",
            options=['Any', 'Enabled', 'Disabled']
        )

    with col2:
        # Call Features Filters
        st.markdown("**Call Features**")
        cfu_filter = st.selectbox("CFU (Call Forward Unconditional):", options=['Any', 'Enabled', 'Disabled'])
        cfb_filter = st.selectbox("CFB (Call Forward Busy):", options=['Any', 'Enabled', 'Disabled'])
        hold_filter = st.selectbox("HOLD (Call Hold):", options=['Any', 'Enabled', 'Disabled'])
        caw_filter = st.selectbox("CAW (Call Waiting):", options=['Any', 'Enabled', 'Disabled'])
        clir_filter = st.selectbox("CLIR (Calling Line ID Restriction):", options=['Any', 'Enabled', 'Disabled'])
        colp_filter = st.selectbox("COLP (Connected Line ID Presentation):", options=['Any', 'Enabled', 'Disabled'])

    # IMSI Prefix Filter
    st.markdown("**IMSI Prefix**")
    imsi_prefix = st.text_input("IMSI starts with (e.g., 60202):", max_chars=10)

    st.markdown("---")
    st.subheader("📡 Advanced EPS Parameters")

    col1, col2 = st.columns(2)

    with col1:
        # VoLTE APN Analysis (EpsIndDefContextId)
        st.markdown("**VoLTE APN Analysis (EpsIndDefContextId)**")
        volte_apn_filter = st.selectbox(
            "VoLTE APN Status:",
            options=['Any', 'Has APN (Normal)', 'Missing APN (Problem Profiles)'],
            key="volte_apn_filter",
            help="EpsIndDefContextId is the VoLTE APN. VoLTE profiles without this parameter have configuration issues."
        )

        # EpsStnSr Filter
        st.markdown("**EpsStnSr (STN-SR Number)**")
        epsstnsr_filter = st.selectbox(
            "EpsStnSr Status:",
            options=['Any', 'Has EpsStnSr 201100000000', 'Has Any EpsStnSr', 'No EpsStnSr'],
            key="epsstnsr_filter",
            help="Filter profiles by STN-SR number configuration"
        )

    with col2:
        # EpsUserIpV4Address Analysis
        st.markdown("**EpsUserIpV4Address (APN & IP)**")
        eps_ip_filter = st.selectbox(
            "IP Address Status:",
            options=['Any', 'Has IP Address', 'No IP Address'],
            key="eps_ip_filter",
            help="Format: 2002200145$10.89.73.130 - Last 3 digits before $ = APN, after $ = IP"
        )

        # APN Filter from EpsUserIpV4Address
        apn_filter_input = st.text_input(
            "Filter by APN Code (last 3 digits, e.g., 145):",
            max_chars=3,
            key="apn_code_filter",
            help="Enter APN code to filter (extracted from EpsUserIpV4Address)"
        )

    st.markdown("---")

    # Build query based on filters
    if st.button("🔍 Apply Filters & Search", type="primary"):
        try:
            # Build WHERE clause
            where_clauses = [f"CDRtime = '{cdr_date_str_dash}'"]

            # TICK filter (Service Type)
            if 'Any' not in tick_filter and len(tick_filter) > 0:
                # Extract TICK values from options like 'VoLTE (215)'
                tick_map = {
                    'VoLTE (215)': '215',
                    'RPT Call Tone (201)': '201',
                    'RPT + Screening (203)': '203',
                    'Call Screening (205)': '205'
                }
                tick_vals = [tick_map.get(t, '') for t in tick_filter if t in tick_map]
                if tick_vals:
                    tick_condition = "','".join(tick_vals)
                    where_clauses.append(f"TICK IN ('{tick_condition}')")

            # Network filter (PDPCP)
            if 'Any' not in network_filter and len(network_filter) > 0:
                network_conditions = []
                if '5G' in network_filter:
                    network_conditions.append("PDPCP LIKE '5%'")
                if '4G+' in network_filter:
                    network_conditions.append("PDPCP LIKE '4%'")
                if '4G' in network_filter:
                    network_conditions.append("PDPCP LIKE '3%'")
                if '3G/2G' in network_filter:
                    network_conditions.append("PDPCP LIKE '1%'")
                if network_conditions:
                    where_clauses.append(f"({' OR '.join(network_conditions)})")

            # VoWiFi filter
            if vowifi_filter == 'Enabled':
                where_clauses.append("EpsAccessRestriction = '0'")
            elif vowifi_filter == 'Disabled':
                where_clauses.append("EpsAccessRestriction != '0'")

            # Call features filters
            if cfu_filter == 'Enabled':
                where_clauses.append("CFU = '1'")
            elif cfu_filter == 'Disabled':
                where_clauses.append("CFU = '0'")

            if cfb_filter == 'Enabled':
                where_clauses.append("CFB = '1'")
            elif cfb_filter == 'Disabled':
                where_clauses.append("CFB = '0'")

            if hold_filter == 'Enabled':
                where_clauses.append("HOLD = '1'")
            elif hold_filter == 'Disabled':
                where_clauses.append("HOLD = '0'")

            if caw_filter == 'Enabled':
                where_clauses.append("CAW = '1'")
            elif caw_filter == 'Disabled':
                where_clauses.append("CAW = '0'")

            if clir_filter == 'Enabled':
                where_clauses.append("CLIR = '1'")
            elif clir_filter == 'Disabled':
                where_clauses.append("CLIR = '0'")

            if colp_filter == 'Enabled':
                where_clauses.append("COLP = '1'")
            elif colp_filter == 'Disabled':
                where_clauses.append("COLP = '0'")

            # IMSI prefix filter
            if imsi_prefix:
                where_clauses.append(f"IMSI LIKE '{imsi_prefix}%'")

            # VoLTE APN filter (EpsIndDefContextId)
            if volte_apn_filter == 'Has APN (Normal)':
                where_clauses.append("EpsIndDefContextId != '' AND EpsIndDefContextId IS NOT NULL")
            elif volte_apn_filter == 'Missing APN (Problem Profiles)':
                # VoLTE profiles (TICK=215) without EpsIndDefContextId
                where_clauses.append("TICK = '215' AND (EpsIndDefContextId = '' OR EpsIndDefContextId IS NULL)")

            # EpsStnSr filter
            if epsstnsr_filter == 'Has EpsStnSr 201100000000':
                where_clauses.append("EpsStnSr LIKE '201100000000%'")
            elif epsstnsr_filter == 'Has Any EpsStnSr':
                where_clauses.append("EpsStnSr != '' AND EpsStnSr IS NOT NULL")
            elif epsstnsr_filter == 'No EpsStnSr':
                where_clauses.append("(EpsStnSr = '' OR EpsStnSr IS NULL)")

            # EpsUserIpV4Address filter
            if eps_ip_filter == 'Has IP Address':
                where_clauses.append("EpsUserIpV4Address != '' AND EpsUserIpV4Address IS NOT NULL")
            elif eps_ip_filter == 'No IP Address':
                where_clauses.append("(EpsUserIpV4Address = '' OR EpsUserIpV4Address IS NULL)")

            # APN Code filter (from EpsUserIpV4Address - last 3 digits before $)
            if apn_filter_input:
                where_clauses.append(f"EpsUserIpV4Address LIKE '%{apn_filter_input}$%'")

            # Build full query
            where_clause = " AND ".join(where_clauses)

            query = f"""
                SELECT
                    MSISDN,
                    IMSI,
                    TICK,
                    PDPCP,
                    EpsProfileId,
                    EpsIndDefContextId,
                    EpsStnSr,
                    EpsUserIpV4Address,
                    EpsAccessRestriction,
                    CFU,
                    CFB,
                    HOLD,
                    CAW
                FROM default.dump
                WHERE {where_clause}
                ORDER BY MSISDN
                LIMIT 1000
            """

            with st.spinner("Searching..."):
                result_rows, column_names = run_user_lookup_cached(query)

            if not result_rows:
                st.warning("No subscribers found matching the filter criteria")
            else:
                df_filtered = pd.DataFrame(result_rows, columns=[
                    'MSISDN', 'IMSI', 'TICK', 'PDPCP', 'EpsProfileId', 'EpsIndDefContextId',
                    'EpsStnSr', 'EpsUserIpV4Address', 'EpsAccessRestriction', 'CFU', 'CFB', 'HOLD', 'CAW'
                ])

                # Parse APN and IP from EpsUserIpV4Address
                def parse_apn_ip(value):
                    if not value or '$' not in str(value):
                        return '', ''
                    try:
                        parts = str(value).split('$')
                        apn_part = parts[0][-3:] if len(parts[0]) >= 3 else parts[0]  # Last 3 digits
                        ip_part = parts[1] if len(parts) > 1 else ''
                        return apn_part, ip_part
                    except:
                        return '', ''

                df_filtered['APN_Code'] = df_filtered['EpsUserIpV4Address'].apply(lambda x: parse_apn_ip(x)[0])
                df_filtered['IP_Address'] = df_filtered['EpsUserIpV4Address'].apply(lambda x: parse_apn_ip(x)[1])

                st.success(f"Found {len(df_filtered)} subscribers (showing up to 1000)")

                # Summary metrics
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total Subscribers", f"{len(df_filtered):,}")
                with col2:
                    volte_count = len(df_filtered[df_filtered['TICK'] == '215'])
                    st.metric("VoLTE Users", f"{volte_count:,}")
                with col3:
                    # VoLTE without APN (problem profiles)
                    volte_no_apn = len(df_filtered[(df_filtered['TICK'] == '215') &
                                                   ((df_filtered['EpsIndDefContextId'] == '') |
                                                    (df_filtered['EpsIndDefContextId'].isna()))])
                    st.metric("VoLTE Missing APN", f"{volte_no_apn:,}",
                             delta="Problem!" if volte_no_apn > 0 else None,
                             delta_color="inverse")
                with col4:
                    has_stnsr = len(df_filtered[(df_filtered['EpsStnSr'] != '') &
                                                (df_filtered['EpsStnSr'].notna())])
                    st.metric("Has EpsStnSr", f"{has_stnsr:,}")

                # Second row of metrics
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    has_ip = len(df_filtered[(df_filtered['EpsUserIpV4Address'] != '') &
                                             (df_filtered['EpsUserIpV4Address'].notna())])
                    st.metric("Has IP Address", f"{has_ip:,}")
                with col2:
                    vowifi_count = len(df_filtered[df_filtered['EpsAccessRestriction'] == '0'])
                    st.metric("VoWiFi Users", f"{vowifi_count:,}")
                with col3:
                    # Count unique APN codes
                    unique_apns = df_filtered[df_filtered['APN_Code'] != '']['APN_Code'].nunique()
                    st.metric("Unique APN Codes", f"{unique_apns:,}")
                with col4:
                    # Count with EpsStnSr starting with 201100000000
                    stnsr_special = len(df_filtered[df_filtered['EpsStnSr'].str.startswith('201100000000', na=False)])
                    st.metric("EpsStnSr 201100000000", f"{stnsr_special:,}")

                st.markdown("---")

                # Reorder columns for display
                display_cols = ['MSISDN', 'IMSI', 'EpsProfileId', 'TICK', 'PDPCP',
                               'EpsIndDefContextId', 'EpsStnSr', 'APN_Code', 'IP_Address',
                               'EpsAccessRestriction', 'CFU', 'CFB', 'HOLD', 'CAW']
                df_display = df_filtered[[c for c in display_cols if c in df_filtered.columns]]

                # Display results table
                st.dataframe(df_display, use_container_width=True, height=500)

                # Export filtered results
                csv = df_filtered.to_csv(index=False)
                st.download_button(
                    label=f"📥 Download Filtered Results ({len(df_filtered)} subscribers)",
                    data=csv,
                    file_name=f"filtered_subscribers_{cdr_date_str}.csv",
                    mime="text/csv"
                )

        except Exception as e:
            st.error(f"Error executing filter query: {e}")
            st.code(query)

# ==================== TAB 4: MNP ANALYSIS ====================
with tab4:
    st.header("📱 MNP (Mobile Number Portability) Analysis")
    st.markdown("Analyze ported-in and ported-out subscriber trends across operators")

    # Operator mapping
    OPERATORS = {
        'Etisalat': {'prefix': '2011', 'color': '#A40000'},
        'Vodafone': {'prefix': '2010', 'color': '#E60000'},
        'Orange': {'prefix': '2012', 'color': '#FF6600'},
        'WE': {'prefix': '2015', 'color': '#7B2D8E'}
    }

    # Value codes for porting
    # QPM = Ported In (to the operator identified by Prefix)
    # QPI = Ported Out to Vodafone (from Etisalat, prefix 2011)
    # QPE = Ported Out to Orange (from Etisalat, prefix 2011)
    # QPQ = Ported Out to WE (from Etisalat, prefix 2011)

    # Get the latest available MNP date from the database
    try:
        mnp_dates_rows, _ = run_cached_query("SELECT DISTINCT Date FROM default.MNP ORDER BY Date DESC")
        available_mnp_dates = []
        for row in mnp_dates_rows:
            if row[0]:
                d = row[0]
                if isinstance(d, str):
                    d = datetime.strptime(d, '%Y-%m-%d').date()
                available_mnp_dates.append(d)

        if available_mnp_dates:
            default_mnp_date = available_mnp_dates[0]
        else:
            default_mnp_date = cdr_date
            available_mnp_dates = []
    except:
        default_mnp_date = cdr_date
        available_mnp_dates = []

    # Section 0: MNP User Lookup
    st.subheader("🔍 MNP User Lookup")
    st.markdown("Search for a subscriber to see their details and porting status")

    mnp_search_msisdn = st.text_input(
        "Enter MSISDN to lookup (e.g., 201012345678):",
        max_chars=15,
        key="mnp_lookup_msisdn"
    )

    if st.button("🔍 Search", type="primary", key="mnp_lookup_btn"):
        if not mnp_search_msisdn:
            st.warning("Please enter an MSISDN to search")
        else:
            try:
                # Determine original operator from MSISDN prefix
                def get_original_operator(msisdn):
                    msisdn_str = str(msisdn)
                    if msisdn_str.startswith('2011'):
                        return 'Etisalat'
                    elif msisdn_str.startswith('2010'):
                        return 'Vodafone'
                    elif msisdn_str.startswith('2012'):
                        return 'Orange'
                    elif msisdn_str.startswith('2015'):
                        return 'WE'
                    else:
                        return 'Unknown'

                original_operator = get_original_operator(mnp_search_msisdn)

                # ============ Part 1: Check Porting Status from MNP_details ============
                st.markdown("### 📱 Porting Status")

                # Query MNP_details to check if number was ported
                mnp_query = f"""
                    SELECT
                        MSISDN,
                        Prefix,
                        NPREFIX,
                        Date,
                        CASE NPREFIX
                            WHEN 'QPM' THEN 'Ported IN to Etisalat'
                            WHEN 'QPI' THEN 'Ported OUT to Vodafone'
                            WHEN 'QPE' THEN 'Ported OUT to Orange'
                            WHEN 'QPQ' THEN 'Ported OUT to WE'
                            ELSE NPREFIX
                        END as porting_status
                    FROM default.MNP_details
                    WHERE MSISDN = '{mnp_search_msisdn}'
                    ORDER BY Date DESC
                    LIMIT 10
                """

                with st.spinner("Checking porting status..."):
                    mnp_rows, _ = run_cached_query(mnp_query)

                col1, col2 = st.columns(2)

                with col1:
                    st.metric("Original Operator (by prefix)", original_operator)

                with col2:
                    if mnp_rows:
                        # Number has been ported
                        latest_porting = mnp_rows[0]
                        porting_status = latest_porting[4]  # porting_status column
                        porting_date = latest_porting[3]    # Date column
                        st.metric("Current Status", f"⚠️ {porting_status}")
                        st.caption(f"Last porting date: {porting_date}")
                    else:
                        st.metric("Current Status", f"✅ Not Ported (still with {original_operator})")

                # Show porting history if exists
                if mnp_rows:
                    st.markdown("**Porting History:**")
                    df_porting = pd.DataFrame(mnp_rows, columns=['MSISDN', 'Prefix', 'NPREFIX', 'Date', 'Porting Status'])
                    st.dataframe(df_porting[['Date', 'Porting Status', 'NPREFIX']], use_container_width=True)

                st.markdown("---")

                # ============ Part 2: Get Subscriber Details from UDC ============
                st.markdown("### 📋 Subscriber Details (from UDC)")

                udc_query = f"""
                    SELECT
                        MSISDN,
                        IMSI,
                        TICK,
                        PDPCP,
                        CFU,
                        CFB,
                        HOLD,
                        CAW,
                        EpsAccessRestriction,
                        CDRtime
                    FROM default.dump
                    WHERE MSISDN = '{mnp_search_msisdn}'
                    ORDER BY CDRtime DESC
                    LIMIT 1
                """

                with st.spinner("Fetching subscriber details..."):
                    udc_rows, _ = run_cached_query(udc_query)

                if not udc_rows:
                    st.info("No subscriber details found in UDC data for this MSISDN")
                else:
                    df_udc = pd.DataFrame(udc_rows, columns=[
                        'MSISDN', 'IMSI', 'TICK', 'PDPCP', 'CFU', 'CFB', 'HOLD', 'CAW',
                        'EpsAccessRestriction', 'Data Date'
                    ])

                    # Display key info in columns
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("IMSI", df_udc['IMSI'].iloc[0] or "N/A")
                    with col2:
                        st.metric("TICK", df_udc['TICK'].iloc[0] or "N/A")
                    with col3:
                        st.metric("PDPCP", df_udc['PDPCP'].iloc[0] or "N/A")

                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("CFU", df_udc['CFU'].iloc[0] or "N/A")
                    with col2:
                        st.metric("CFB", df_udc['CFB'].iloc[0] or "N/A")
                    with col3:
                        st.metric("HOLD", df_udc['HOLD'].iloc[0] or "N/A")
                    with col4:
                        st.metric("CAW", df_udc['CAW'].iloc[0] or "N/A")

                    st.metric("EPS Access Restriction", df_udc['EpsAccessRestriction'].iloc[0] or "N/A")
                    st.caption(f"Data from: {df_udc['Data Date'].iloc[0]}")

                    # Export combined data
                    export_data = {
                        'MSISDN': mnp_search_msisdn,
                        'Original Operator': original_operator,
                        'Ported': 'Yes' if mnp_rows else 'No',
                        'Current Status': mnp_rows[0][4] if mnp_rows else f'With {original_operator}',
                        'IMSI': df_udc['IMSI'].iloc[0],
                        'TICK': df_udc['TICK'].iloc[0],
                        'PDPCP': df_udc['PDPCP'].iloc[0]
                    }
                    csv = pd.DataFrame([export_data]).to_csv(index=False)
                    st.download_button(
                        label="📥 Download Subscriber Info",
                        data=csv,
                        file_name=f"subscriber_{mnp_search_msisdn}.csv",
                        mime="text/csv",
                        key="mnp_lookup_download"
                    )

            except Exception as e:
                st.error(f"Error searching: {e}")

    st.markdown("---")

    # Section: Random Ported Numbers Lookup
    st.subheader("🎲 Random Ported Numbers Lookup")
    st.markdown("Get random sample of ported numbers for any operator")

    col1, col2, col3 = st.columns(3)

    with col1:
        selected_operator = st.selectbox(
            "Select Operator:",
            options=['Etisalat', 'Vodafone', 'Orange', 'WE'],
            key="ported_operator"
        )

    with col2:
        porting_direction = st.selectbox(
            "Porting Direction:",
            options=['Ported Out', 'Ported In'],
            key="porting_direction"
        )

    with col3:
        sample_size = st.number_input(
            "Number of samples:",
            min_value=5,
            max_value=100,
            value=20,
            key="sample_size"
        )

    # Operator prefix mapping
    operator_prefixes = {
        'Etisalat': '2011',
        'Vodafone': '2010',
        'Orange': '2012',
        'WE': '2015'
    }

    # NPREFIX codes for porting OUT from Etisalat
    # QPI = to Vodafone, QPE = to Orange, QPQ = to WE
    porting_codes = {
        'Vodafone': 'QPI',
        'Orange': 'QPE',
        'WE': 'QPQ'
    }

    if st.button("🔍 Get Random Ported Numbers", type="primary", key="random_ported_btn"):
        try:
            prefix = operator_prefixes[selected_operator]

            if porting_direction == 'Ported Out':
                # Numbers FROM this operator that went to others
                if selected_operator == 'Etisalat':
                    # Etisalat numbers (2011*) ported out to Vodafone, Orange, or WE
                    query = f"""
                        SELECT
                            MSISDN,
                            CASE NPREFIX
                                WHEN 'QPI' THEN 'Vodafone'
                                WHEN 'QPE' THEN 'Orange'
                                WHEN 'QPQ' THEN 'WE'
                                ELSE 'Unknown'
                            END AS ported_to,
                            Date
                        FROM default.MNP_details
                        WHERE Prefix = '2011'
                          AND NPREFIX IN ('QPI', 'QPE', 'QPQ')
                        ORDER BY rand()
                        LIMIT {sample_size}
                    """
                else:
                    # Other operators (Vodafone/Orange/WE) numbers that ported OUT to Etisalat
                    # Prefix = operator's own prefix, NPREFIX = QPM (ported to Etisalat)
                    query = f"""
                        SELECT
                            MSISDN,
                            'Etisalat' AS ported_to,
                            Date
                        FROM default.MNP_details
                        WHERE Prefix = '{prefix}'
                          AND NPREFIX = 'QPM'
                        ORDER BY rand()
                        LIMIT {sample_size}
                    """
            else:
                # Ported In - numbers coming TO this operator from others
                if selected_operator == 'Etisalat':
                    # Numbers from other operators (2010/2012/2015) that came TO Etisalat
                    # These are QPM records where Prefix is the original operator
                    query = f"""
                        SELECT
                            MSISDN,
                            CASE Prefix
                                WHEN '2010' THEN 'Vodafone'
                                WHEN '2012' THEN 'Orange'
                                WHEN '2015' THEN 'WE'
                                ELSE 'Unknown'
                            END AS ported_from,
                            Date
                        FROM default.MNP_details
                        WHERE NPREFIX = 'QPM'
                          AND Prefix != '2011'
                        ORDER BY rand()
                        LIMIT {sample_size}
                    """
                else:
                    # Numbers from Etisalat (2011*) that came TO this operator
                    # These are QPI/QPE/QPQ records with Prefix = '2011'
                    nprefix_code = porting_codes.get(selected_operator, '')
                    query = f"""
                        SELECT
                            MSISDN,
                            'Etisalat' AS ported_from,
                            Date
                        FROM default.MNP_details
                        WHERE Prefix = '2011'
                          AND NPREFIX = '{nprefix_code}'
                        ORDER BY rand()
                        LIMIT {sample_size}
                    """

            with st.spinner("Fetching random ported numbers..."):
                result_rows, _ = run_cached_query(query)

            if not result_rows:
                st.warning(f"No ported numbers found for {selected_operator} ({porting_direction})")
            else:
                if porting_direction == 'Ported Out':
                    df_ported = pd.DataFrame(result_rows, columns=['MSISDN', 'Ported To', 'Date'])
                else:
                    df_ported = pd.DataFrame(result_rows, columns=['MSISDN', 'Ported From', 'Date'])

                st.success(f"Found {len(df_ported)} random {porting_direction.lower()} numbers for {selected_operator}")

                # Color the operator column
                st.dataframe(df_ported, use_container_width=True, height=400)

                # Export option
                csv = df_ported.to_csv(index=False)
                st.download_button(
                    label=f"📥 Download {selected_operator} {porting_direction} Numbers",
                    data=csv,
                    file_name=f"{selected_operator.lower()}_{porting_direction.lower().replace(' ', '_')}_samples.csv",
                    mime="text/csv",
                    key="download_ported_samples"
                )

        except Exception as e:
            st.error(f"Error fetching ported numbers: {e}")

    st.markdown("---")

    # Section 1: Daily MNP Overview
    st.subheader("📊 Daily MNP Overview")

    # Show available MNP dates and allow selection
    if available_mnp_dates:
        st.info(f"📅 MNP data available for {len(available_mnp_dates)} date(s)")
        if len(available_mnp_dates) > 1:
            selected_date_mnp = st.selectbox(
                "Select MNP Data Date",
                options=available_mnp_dates,
                index=0,
                format_func=lambda x: x.strftime('%Y-%m-%d'),
                key="mnp_date"
            )
        else:
            selected_date_mnp = available_mnp_dates[0]
            st.write(f"**Selected Date:** {selected_date_mnp}")
    else:
        st.warning("No MNP data available. Please run the processor with MNP files.")
        selected_date_mnp = st.date_input("Select Date for Daily Analysis", value=default_mnp_date, key="mnp_date")

    mnp_date_str = selected_date_mnp.strftime("%Y-%m-%d")

    try:
        # Query MNP data for selected date
        mnp_query = f"""
            SELECT Date, Prefix, value, Count
            FROM default.MNP
            WHERE Date = '{mnp_date_str}'
        """
        mnp_rows, mnp_cols = run_cached_query(mnp_query)

        if not mnp_rows:
            st.warning(f"No MNP data found for {mnp_date_str}")
        else:
            df_mnp = pd.DataFrame(mnp_rows, columns=['Date', 'Prefix', 'Value', 'Count'])
            df_mnp['Prefix'] = df_mnp['Prefix'].astype(str).str.strip()
            df_mnp['Value'] = df_mnp['Value'].astype(str).str.strip()
            df_mnp['Count'] = df_mnp['Count'].astype(int)

            def safe_extract(df, prefix_val, value_val):
                match = df[(df['Prefix'] == prefix_val) & (df['Value'] == value_val)]
                if not match.empty:
                    return int(match.iloc[0]['Count'])
                return 0

            # Extract porting data for each operator
            # Ported IN = subscribers coming TO the operator (QPM for that operator's prefix)
            # Ported OUT = subscribers leaving FROM Etisalat TO other operators

            voda_in = safe_extract(df_mnp, '2010', 'QPM')
            voda_out = safe_extract(df_mnp, '2011', 'QPI')

            orange_in = safe_extract(df_mnp, '2012', 'QPM')
            orange_out = safe_extract(df_mnp, '2011', 'QPE')

            we_in = safe_extract(df_mnp, '2015', 'QPM')
            we_out = safe_extract(df_mnp, '2011', 'QPQ')

            # Etisalat totals
            etisalat_in = voda_in + orange_in + we_in  # What others lost = what Etisalat gained
            etisalat_out = voda_out + orange_out + we_out  # What Etisalat lost to others

            # Display metrics
            st.markdown("#### Etisalat (e&) MNP Summary")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Ported In (Gained)", f"{etisalat_in:,}",
                         delta=f"{etisalat_in - etisalat_out:+,} net",
                         delta_color="normal")
            with col2:
                st.metric("Ported Out (Lost)", f"{etisalat_out:,}")
            with col3:
                net_change = etisalat_in - etisalat_out
                st.metric("Net Change", f"{net_change:+,}",
                         delta="Gain" if net_change > 0 else "Loss",
                         delta_color="normal" if net_change > 0 else "inverse")

            st.markdown("---")

            # Create comparison DataFrame
            data = {
                "Operator": ["Vodafone", "Orange", "WE"],
                "Ported In": [voda_in, orange_in, we_in],
                "Ported Out": [voda_out, orange_out, we_out],
                "Net Change": [voda_in - voda_out, orange_in - orange_out, we_in - we_out]
            }
            df_comparison = pd.DataFrame(data)

            # Display operator comparison
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("#### Operator Comparison (Bar Chart)")
                fig_bar = go.Figure()

                fig_bar.add_trace(go.Bar(
                    y=df_comparison["Operator"],
                    x=df_comparison["Ported Out"],
                    name="Ported Out (Lost to Etisalat)",
                    orientation='h',
                    marker_color='#A40000',
                    text=df_comparison["Ported Out"],
                    textposition='outside'
                ))

                fig_bar.add_trace(go.Bar(
                    y=df_comparison["Operator"],
                    x=df_comparison["Ported In"],
                    name="Ported In (Gained from Etisalat)",
                    orientation='h',
                    marker_color='#FF6600',
                    text=df_comparison["Ported In"],
                    textposition='outside'
                ))

                fig_bar.update_layout(
                    title=f"MNP Distribution - {mnp_date_str}",
                    barmode='group',
                    xaxis_title="Number of Subscribers",
                    yaxis_title="Operator",
                    height=400,
                    plot_bgcolor='white',
                    font=dict(size=12),
                    xaxis=dict(showgrid=True, gridwidth=1, gridcolor='lightgrey'),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                )
                st.plotly_chart(fig_bar, use_container_width=True)

            with col2:
                st.markdown("#### Net Change by Operator")
                # Use operator brand colors
                colors = [OPERATORS.get(op, {}).get('color', '#888888') for op in df_comparison["Operator"]]

                fig_net = go.Figure(go.Bar(
                    x=df_comparison["Operator"],
                    y=df_comparison["Net Change"],
                    marker_color=colors,
                    text=df_comparison["Net Change"].apply(lambda x: f"{x:+,}"),
                    textposition='outside'
                ))

                fig_net.update_layout(
                    title=f"Net MNP Change (Gain/Loss) - {mnp_date_str}",
                    xaxis_title="Operator",
                    yaxis_title="Net Subscribers",
                    height=400,
                    plot_bgcolor='white',
                    yaxis=dict(zeroline=True, zerolinewidth=2, zerolinecolor='black')
                )
                st.plotly_chart(fig_net, use_container_width=True)

            # Data table
            st.markdown("#### MNP Data Table")
            st.dataframe(df_comparison, use_container_width=True)

    except Exception as e:
        st.error(f"Error loading MNP data: {e}")

    st.markdown("---")

    # Section 2: MNP Trends Over Time
    st.subheader("📈 MNP Trends Over Time")

    # Use MNP data dates for the trend range (not UDC dates)
    col1, col2 = st.columns(2)
    with col1:
        trend_start = st.date_input("Start Date", value=default_mnp_date - timedelta(days=7), key="mnp_start")
    with col2:
        trend_end = st.date_input("End Date", value=default_mnp_date, key="mnp_end")

    if st.button("📊 Load Trends", type="primary", key="mnp_trends_btn"):
        if trend_start > trend_end:
            st.error("End Date must be after Start Date")
        else:
            try:
                trend_query = f"""
                    SELECT Date, Prefix, value, Count
                    FROM default.MNP
                    WHERE Date BETWEEN '{trend_start}' AND '{trend_end}'
                    ORDER BY Date
                """
                trend_rows, _ = run_cached_query(trend_query)

                if not trend_rows:
                    st.warning("No MNP trend data found for the selected date range.")
                else:
                    df_trend = pd.DataFrame(trend_rows, columns=['Date', 'Prefix', 'Value', 'Count'])
                    df_trend['Date'] = pd.to_datetime(df_trend['Date'])
                    df_trend['Prefix'] = df_trend['Prefix'].astype(str).str.strip()
                    df_trend['Value'] = df_trend['Value'].astype(str).str.strip()
                    df_trend['Count'] = df_trend['Count'].astype(int)

                    def extract_operator_data(df, prefix, ported_in_value, ported_out_value):
                        ported_in = df[(df['Prefix'] == prefix) & (df['Value'] == ported_in_value)].copy()
                        ported_out = df[(df['Prefix'] == '2011') & (df['Value'] == ported_out_value)].copy()
                        return ported_in, ported_out

                    voda_in_trend, voda_out_trend = extract_operator_data(df_trend, '2010', 'QPM', 'QPI')
                    orange_in_trend, orange_out_trend = extract_operator_data(df_trend, '2012', 'QPM', 'QPE')
                    we_in_trend, we_out_trend = extract_operator_data(df_trend, '2015', 'QPM', 'QPQ')

                    # Chart type selection
                    chart_type = st.radio(
                        "Chart Display Mode:",
                        options=['Daily Change (Delta)', 'Total Count'],
                        horizontal=True,
                        key="mnp_chart_type"
                    )

                    def create_trend_chart(title, in_data, out_data, color_in='green', color_out='red', show_delta=True):
                        fig = go.Figure()

                        if not in_data.empty:
                            in_data = in_data.sort_values('Date').copy()
                            if show_delta:
                                # Calculate daily change (difference from previous day)
                                # First day has no previous data, so set to 0
                                in_data['Delta'] = in_data['Count'].diff().fillna(0)
                                y_values = in_data['Delta']
                                y_name = "Daily Change - Ported In"
                            else:
                                y_values = in_data['Count']
                                y_name = "Ported In (Gained from Etisalat)"

                            fig.add_trace(go.Scatter(
                                x=in_data['Date'],
                                y=y_values,
                                mode='lines+markers+text',
                                name=y_name,
                                line=dict(color=color_in, width=2),
                                marker=dict(size=8),
                                text=[f"{int(v):+,}" if show_delta else f"{int(v):,}" for v in y_values],
                                textposition='top center',
                                textfont=dict(size=10)
                            ))

                        if not out_data.empty:
                            out_data = out_data.sort_values('Date').copy()
                            if show_delta:
                                # Calculate daily change (difference from previous day)
                                # First day has no previous data, so set to 0
                                out_data['Delta'] = out_data['Count'].diff().fillna(0)
                                y_values = out_data['Delta']
                                y_name = "Daily Change - Ported Out"
                            else:
                                y_values = out_data['Count']
                                y_name = "Ported Out (Lost to Etisalat)"

                            fig.add_trace(go.Scatter(
                                x=out_data['Date'],
                                y=y_values,
                                mode='lines+markers+text',
                                name=y_name,
                                line=dict(color=color_out, width=2),
                                marker=dict(size=8),
                                text=[f"{int(v):+,}" if show_delta else f"{int(v):,}" for v in y_values],
                                textposition='bottom center',
                                textfont=dict(size=10)
                            ))

                        y_axis_title = "Daily Change (vs Previous Day)" if show_delta else "Count"
                        chart_title = f"{title} - {'Daily Changes' if show_delta else 'Total Count'}"

                        fig.update_layout(
                            title=chart_title,
                            xaxis_title="Date",
                            yaxis_title=y_axis_title,
                            plot_bgcolor='white',
                            height=400,
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                            xaxis=dict(showgrid=True, gridcolor='lightgrey'),
                            yaxis=dict(showgrid=True, gridcolor='lightgrey', zeroline=True, zerolinewidth=2, zerolinecolor='black')
                        )
                        return fig

                    show_delta = (chart_type == 'Daily Change (Delta)')

                    # Display trend charts
                    st.plotly_chart(create_trend_chart("Vodafone MNP", voda_in_trend, voda_out_trend, show_delta=show_delta), use_container_width=True)

                    col1, col2 = st.columns(2)
                    with col1:
                        st.plotly_chart(create_trend_chart("Orange MNP", orange_in_trend, orange_out_trend, show_delta=show_delta), use_container_width=True)
                    with col2:
                        st.plotly_chart(create_trend_chart("WE MNP", we_in_trend, we_out_trend, show_delta=show_delta), use_container_width=True)

                    # Summary statistics for the period
                    st.markdown("#### Period Summary Statistics")

                    total_voda_in = voda_in_trend['Count'].sum() if not voda_in_trend.empty else 0
                    total_voda_out = voda_out_trend['Count'].sum() if not voda_out_trend.empty else 0
                    total_orange_in = orange_in_trend['Count'].sum() if not orange_in_trend.empty else 0
                    total_orange_out = orange_out_trend['Count'].sum() if not orange_out_trend.empty else 0
                    total_we_in = we_in_trend['Count'].sum() if not we_in_trend.empty else 0
                    total_we_out = we_out_trend['Count'].sum() if not we_out_trend.empty else 0

                    summary_data = {
                        "Operator": ["Vodafone", "Orange", "WE", "**Etisalat (e&)**"],
                        "Total Ported In": [total_voda_in, total_orange_in, total_we_in, total_voda_out + total_orange_out + total_we_out],
                        "Total Ported Out": [total_voda_out, total_orange_out, total_we_out, total_voda_in + total_orange_in + total_we_in],
                        "Net Change": [
                            total_voda_in - total_voda_out,
                            total_orange_in - total_orange_out,
                            total_we_in - total_we_out,
                            (total_voda_out + total_orange_out + total_we_out) - (total_voda_in + total_orange_in + total_we_in)
                        ],
                        "Avg Daily In": [
                            total_voda_in / max(len(voda_in_trend), 1),
                            total_orange_in / max(len(orange_in_trend), 1),
                            total_we_in / max(len(we_in_trend), 1),
                            (total_voda_out + total_orange_out + total_we_out) / max((trend_end - trend_start).days + 1, 1)
                        ],
                        "Avg Daily Out": [
                            total_voda_out / max(len(voda_out_trend), 1),
                            total_orange_out / max(len(orange_out_trend), 1),
                            total_we_out / max(len(we_out_trend), 1),
                            (total_voda_in + total_orange_in + total_we_in) / max((trend_end - trend_start).days + 1, 1)
                        ]
                    }
                    df_summary = pd.DataFrame(summary_data)
                    df_summary['Avg Daily In'] = df_summary['Avg Daily In'].round(0).astype(int)
                    df_summary['Avg Daily Out'] = df_summary['Avg Daily Out'].round(0).astype(int)

                    st.dataframe(df_summary, use_container_width=True)

                    # Export option
                    csv_summary = df_summary.to_csv(index=False)
                    st.download_button(
                        label="📥 Download MNP Summary (CSV)",
                        data=csv_summary,
                        file_name=f"mnp_summary_{trend_start}_{trend_end}.csv",
                        mime="text/csv"
                    )

            except Exception as e:
                st.error(f"Error loading MNP trends: {e}")

# ==================== TAB 5: RECONCILIATION ====================
with tab5:
    st.header("🗂️ VoLTE Health Reconciliation")
    st.markdown(
        "VoLTE subscriber health check. A **healthy VoLTE user** must have: "
        "**TICK = 215**, **EpsProfileId set**, **EpsIndMappingContextId = 15$2008300586 or 15$1008300586**, "
        "**IMPI set**, and be **present in all 3 IPW nodes** with **consistent NAPTR patterns**."
    )

    # ---- Get IPW last processed date ----
    ipw_process_date = "No IPW data"
    try:
        ipw_date_rows, _ = run_cached_query("SELECT max(process_date) FROM default.ipw_raw")
        if ipw_date_rows and ipw_date_rows[0][0]:
            ipw_process_date = str(ipw_date_rows[0][0])
    except Exception:
        pass

    st.info(f"📅 UDC data: **{cdr_date_str}**  |  📡 IPW files processed: **{ipw_process_date}**")

    # ---- Load unified summary via LEFT JOIN of UDC (TICK=215) with IPW ----
    volte_available = False
    total_volte = 0
    cnt_healthy = cnt_no_profile = cnt_wrong_apn = cnt_missing_impi = 0
    cnt_not_in_ipw = cnt_ipw_missing_node = cnt_ipw_mismatch = 0

    try:
        vh_rows, _ = run_cached_query_with_disk(f"""
            

SELECT
    count() AS total_volte,

    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND d.EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
        AND d.IMPI != ''
        AND ipw.status = 'OK'
    ) AS healthy,

    countIf(d.EpsProfileId = '' OR d.EpsProfileId IS NULL) AS no_profile,

    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND (d.EpsIndMappingContextId NOT IN ('15$2008300586', '15$1008300586')
             OR d.EpsIndMappingContextId IS NULL)
    ) AS wrong_apn,

    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND d.EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
        AND (d.IMPI = '' OR d.IMPI IS NULL)
    ) AS missing_impi,

    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND d.EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
        AND d.IMPI != '' AND d.IMPI IS NOT NULL
        AND (ipw.msisdn IS NULL OR ipw.msisdn = '')
    ) AS not_in_ipw,
    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND d.EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
        AND d.IMPI != '' AND d.IMPI IS NOT NULL
        AND ipw.status = 'MISSING'
    ) AS ipw_missing_node,
    countIf(
        d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
        AND d.EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
        AND d.IMPI != '' AND d.IMPI IS NOT NULL
        AND ipw.status = 'PATTERN_MISMATCH'
    ) AS ipw_mismatch

FROM
(
    SELECT *
    FROM default.dump
    WHERE CDRtime = '{cdr_date_str_dash}'
      AND TICK = '215'
) d

LEFT JOIN
(
    SELECT *
    FROM default.ipw_reconciliation
    WHERE process_date = (
        SELECT max(process_date) FROM default.ipw_reconciliation
    )
) ipw

ON d.MSISDN = ipw.msisdn        """, cdr_date_str, "volte_health_summary")
        if vh_rows and vh_rows[0][0] > 0:
            volte_available      = True
            total_volte          = int(vh_rows[0][0])
            cnt_healthy          = int(vh_rows[0][1])
            cnt_no_profile       = int(vh_rows[0][2])
            cnt_wrong_apn        = int(vh_rows[0][3])
            cnt_missing_impi     = int(vh_rows[0][4])
            cnt_not_in_ipw       = int(vh_rows[0][5])
            cnt_ipw_missing_node = int(vh_rows[0][6])
            cnt_ipw_mismatch     = int(vh_rows[0][7])
    except Exception as e:
        st.error(f"Error loading VoLTE health data: {e}")

    if not volte_available:
        st.warning(f"No VoLTE subscribers (TICK=215) found for {cdr_date_str}.")
    else:
        # No EpsProfileId means the user can't be VoLTE-healthy either —
        # count them as unhealthy so Healthy + Unhealthy always equals Total TICK=215
        cnt_with_profile = total_volte - cnt_no_profile
        cnt_unhealthy = total_volte - cnt_healthy

        # ---- Summary metrics ----
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total TICK=215", f"{total_volte:,}",
                       help="All subscribers with TICK=215 in UDC")
        with col2:
            st.metric("With EPS Profile", f"{cnt_with_profile:,}",
                       help="Have EpsProfileId — actual VoLTE users")
        with col3:
            h_pct = (cnt_healthy / total_volte * 100) if total_volte > 0 else 0
            st.metric("Healthy", f"{cnt_healthy:,}", f"{h_pct:.1f}%", delta_color="normal")
        with col4:
            u_pct = (cnt_unhealthy / total_volte * 100) if total_volte > 0 else 0
            st.metric("Unhealthy", f"{cnt_unhealthy:,}", f"{u_pct:.1f}%", delta_color="inverse")

        # Issue breakdown with clear descriptions
        st.markdown("**Issue Breakdown:**")
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric("No EPS Profile", f"{cnt_no_profile:,}",
                       help="TICK=215 but EpsProfileId is empty — counted as unhealthy")
        with col2:
            st.metric("Wrong APN Mapping", f"{cnt_wrong_apn:,}",
                       help="Has EpsProfileId but EpsIndMappingContextId is not 15$2008300586 or 15$1008300586")
        with col3:
            st.metric("No IMS Identity", f"{cnt_missing_impi:,}",
                       help="Has correct profile + APN but IMPI is empty (no IMS Private Identity)")
        with col4:
            st.metric("Not in IPW Files", f"{cnt_not_in_ipw:,}",
                       help="Has TICK=215 in UDC but not found in any of the 3 IPW files")
        with col5:
            st.metric("IPW Node Issues", f"{cnt_ipw_missing_node + cnt_ipw_mismatch:,}",
                       help="Missing from some IPW node(s) or NAPTR pattern mismatch across nodes")

        st.markdown("---")

        # ---- Charts ----
        col_c1, col_c2 = st.columns(2)

        with col_c1:
            issue_data = []
            if cnt_healthy > 0:
                issue_data.append({'Issue': 'Healthy', 'Count': cnt_healthy})
            if cnt_no_profile > 0:
                issue_data.append({'Issue': 'No EPS Profile', 'Count': cnt_no_profile})
            if cnt_wrong_apn > 0:
                issue_data.append({'Issue': 'Wrong APN Mapping', 'Count': cnt_wrong_apn})
            if cnt_missing_impi > 0:
                issue_data.append({'Issue': 'No IMS Identity (IMPI)', 'Count': cnt_missing_impi})
            if cnt_not_in_ipw > 0:
                issue_data.append({'Issue': 'Not in IPW Files', 'Count': cnt_not_in_ipw})
            if cnt_ipw_missing_node > 0:
                issue_data.append({'Issue': 'Missing from IPW Node(s)', 'Count': cnt_ipw_missing_node})
            if cnt_ipw_mismatch > 0:
                issue_data.append({'Issue': 'NAPTR Pattern Mismatch', 'Count': cnt_ipw_mismatch})

            if issue_data:
                df_health = pd.DataFrame(issue_data)
                fig_h = px.pie(
                    df_health, values='Count', names='Issue',
                    color='Issue',
                    color_discrete_map={
                        'Healthy': '#2ecc71',
                        'No EPS Profile': '#95a5a6',
                        'Wrong APN Mapping': '#e74c3c',
                        'No IMS Identity (IMPI)': '#f39c12',
                        'Not in IPW Files': '#e67e22',
                        'Missing from IPW Node(s)': '#3498db',
                        'NAPTR Pattern Mismatch': '#c0392b',
                    }
                )
                fig_h.update_traces(textposition='inside', textinfo='percent+label')
                fig_h.update_layout(showlegend=False, title="VoLTE Health Distribution")
                st.plotly_chart(fig_h, use_container_width=True)

        with col_c2:
            if cnt_wrong_apn > 0:
                try:
                    eps_rows, _ = run_cached_query(f"""
                        SELECT EpsIndMappingContextId, count() AS cnt
                        FROM default.dump
                        WHERE CDRtime = '{cdr_date_str_dash}' AND TICK = '215'
                            AND EpsIndMappingContextId NOT IN ('15$2008300586', '15$1008300586')
                        GROUP BY EpsIndMappingContextId
                        ORDER BY cnt DESC
                        LIMIT 10
                    """)
                    if eps_rows:
                        df_eps = pd.DataFrame(eps_rows, columns=['EpsIndMappingContextId', 'Count'])
                        fig_eps = px.bar(
                            df_eps, x='Count', y='EpsIndMappingContextId', orientation='h',
                            title='Wrong EpsIndMappingContextId Values',
                            text='Count', color_discrete_sequence=['#e74c3c']
                        )
                        fig_eps.update_traces(texttemplate='%{text:,}', textposition='outside')
                        fig_eps.update_layout(yaxis={'categoryorder': 'total ascending'})
                        st.plotly_chart(fig_eps, use_container_width=True)
                except Exception as e:
                    st.error(f"Error loading EpsIndMappingContextId breakdown: {e}")

        st.markdown("---")

        # ---- MSISDN Quick Lookup ----
        st.subheader("MSISDN Quick Lookup")
        lookup_msisdn = st.text_input(
            "Enter MSISDN to check its full VoLTE health:",
            placeholder="e.g. 201117539668",
            key="ipw_lookup"
        )
        if lookup_msisdn:
            lookup_msisdn = lookup_msisdn.strip()

            # UDC profile
            try:
                udc_rows, _ = run_cached_query(f"""
                    SELECT MSISDN, IMSI, TICK, EpsIndMappingContextId, IMPI, EpsProfileId
                    FROM default.dump
                    WHERE MSISDN = '{lookup_msisdn}' AND CDRtime = '{cdr_date_str_dash}'
                    LIMIT 1
                """)
                if udc_rows:
                    r = udc_rows[0]
                    st.markdown("**UDC Profile:**")
                    c1, c2, c3, c4 = st.columns(4)
                    has_profile = bool(r[5])
                    eps_ok = r[3] in ('15$2008300586', '15$1008300586')
                    c1.metric("EpsProfileId", r[5] if r[5] else "EMPTY", "OK" if has_profile else "No Profile", delta_color="normal" if has_profile else "inverse")
                    c2.metric("EpsIndMappingContextId", r[3] if r[3] else "EMPTY", "OK" if eps_ok else "Wrong APN", delta_color="normal" if eps_ok else "inverse")
                    c3.metric("IMPI", r[4][:30] + "..." if r[4] and len(r[4]) > 30 else (r[4] if r[4] else "EMPTY"), "OK" if r[4] else "Missing", delta_color="normal" if r[4] else "inverse")
                    c4.metric("TICK", r[2], "VoLTE" if r[2] == '215' else "Not VoLTE", delta_color="normal" if r[2] == '215' else "inverse")
                else:
                    st.info(f"MSISDN `{lookup_msisdn}` not found in UDC dump for {cdr_date_str}.")
            except Exception as e:
                st.error(f"UDC lookup error: {e}")

            # IPW presence
            try:
                lk_rows, _ = run_cached_query(f"""
                    SELECT
                        msisdn, process_date,
                        if(in_sipw,  'Present', 'Missing') AS SIPW,
                        if(in_kipw, 'Present', 'Missing') AS KIPW,
                        if(in_yipw,  'Present', 'Missing') AS YIPW,
                        status, sipw_pattern, kipw_pattern, yipw_pattern
                    FROM default.ipw_reconciliation
                    WHERE msisdn = '{lookup_msisdn}'
                    LIMIT 1
                """)
                if lk_rows:
                    r = lk_rows[0]
                    status_val = r[5]
                    st.markdown(f"**IPW Status:** `{status_val}`")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("SIPW",  r[2])
                    c2.metric("KIPW", r[3])
                    c3.metric("YIPW",  r[4])
                    if status_val == 'PATTERN_MISMATCH':
                        st.markdown("**NAPTR Patterns across nodes:**")
                        st.write(pd.DataFrame({
                            'Node':    ['SIPW',  'KIPW',  'YIPW'],
                            'Pattern': [r[6],    r[7],     r[8]]
                        }))
                else:
                    st.warning(f"MSISDN `{lookup_msisdn}` **not found in any IPW file**.")
            except Exception as e:
                st.error(f"IPW lookup error: {e}")

        st.markdown("---")

        # ---- Unhealthy VoLTE Subscribers Table ----
        st.subheader("Misconfigured VoLTE Subscribers")
        st.markdown(
            "All TICK=215 subscribers failing **any** health criterion — including users with "
            "no EPS profile — so this list always matches the metrics above."
        )

        if cnt_unhealthy == 0:
            st.success("All VoLTE subscribers are fully healthy.")
        else:
            # Issue type filter
            issue_filter = st.selectbox("Filter by issue type:", [
                "All Issues",
                "No EPS Profile (EpsProfileId is empty)",
                "Wrong APN Mapping (has profile but wrong EpsIndMappingContextId)",
                "No IMS Identity (has profile + correct APN but IMPI empty)",
                "Not in Any IPW File (TICK=215 but absent from IPW)",
                "Missing from Some IPW Node(s)",
                "NAPTR Pattern Mismatch Across IPW Nodes",
            ], key="recon_filter")

            # Build WHERE clause based on filter
            issue_where = ""
            if "No EPS Profile" in issue_filter:
                issue_where = "AND (d.EpsProfileId = '' OR d.EpsProfileId IS NULL)"
            elif "Wrong APN Mapping" in issue_filter:
                issue_where = "AND d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL AND d.EpsIndMappingContextId NOT IN ('15$2008300586', '15$1008300586')"
            elif "No IMS Identity" in issue_filter:
                issue_where = "AND d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL AND d.EpsIndMappingContextId IN ('15$2008300586', '15$1008300586') AND (d.IMPI = '' OR d.IMPI IS NULL)"
            elif "Not in Any IPW" in issue_filter:
                issue_where = "AND (ipw.msisdn IS NULL OR ipw.msisdn = '')"
            elif "Missing from Some IPW" in issue_filter:
                issue_where = "AND ipw.status = 'MISSING'"
            elif "NAPTR Pattern Mismatch" in issue_filter:
                issue_where = "AND ipw.status = 'PATTERN_MISMATCH'"

            # "All Issues" = every subscriber failing the full health check,
            # including No-EPS-Profile users, so the export matches the metrics
            if issue_filter == "All Issues":
                issue_where = """AND NOT (
                    d.EpsProfileId != '' AND d.EpsProfileId IS NOT NULL
                    AND d.EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
                    AND d.IMPI != ''
                    AND ifNull(ipw.status, '') = 'OK'
                )"""

            try:
                misc_rows, _ = run_cached_query(f"""
                    SELECT
                        d.MSISDN,
                        d.IMSI,
                        d.EpsProfileId,
                        d.EpsIndMappingContextId,
                        d.IMPI,
                        multiIf(
                            d.EpsProfileId = '' OR d.EpsProfileId IS NULL, 'No EPS Profile',
                            d.EpsIndMappingContextId NOT IN ('15$2008300586', '15$1008300586'), 'Wrong APN Mapping',
                            d.IMPI = '' OR d.IMPI IS NULL, 'No IMS Identity',
                            '-'
                        ) AS udc_issue,
                        if(ipw.msisdn IS NULL OR ipw.msisdn = '', 'N/A',
                            if(ipw.in_sipw, 'Y', 'N')) AS SIPW,
                        if(ipw.msisdn IS NULL OR ipw.msisdn = '', 'N/A',
                            if(ipw.in_kipw, 'Y', 'N')) AS KIPW,
                        if(ipw.msisdn IS NULL OR ipw.msisdn = '', 'N/A',
                            if(ipw.in_yipw, 'Y', 'N')) AS YIPW,
                        multiIf(
                            ipw.msisdn IS NULL OR ipw.msisdn = '', 'Not in IPW',
                            ipw.status = 'MISSING', 'Missing from Node(s)',
                            ipw.status = 'PATTERN_MISMATCH', 'NAPTR Mismatch',
                            'OK'
                        ) AS ipw_status
                    FROM default.dump AS d
                    LEFT JOIN (
                        SELECT * FROM default.ipw_reconciliation
                        WHERE process_date = (SELECT max(process_date) FROM default.ipw_reconciliation)
                    ) AS ipw ON d.MSISDN = ipw.msisdn
                    WHERE d.CDRtime = '{cdr_date_str_dash}' AND d.TICK = '215'
                        {issue_where}
                    ORDER BY d.MSISDN
                    LIMIT 1000
                """)
                if misc_rows:
                    df_misc = pd.DataFrame(misc_rows, columns=[
                        'MSISDN', 'IMSI', 'EpsProfileId', 'EpsIndMappingContextId', 'IMPI',
                        'UDC Issue', 'SIPW', 'KIPW', 'YIPW', 'IPW Status'
                    ])
                    st.caption(f"Showing first {len(df_misc):,} results")
                    st.dataframe(df_misc, use_container_width=True, height=400)
                else:
                    st.info("No subscribers found for this filter.")

                # Download
                dl_rows, _ = run_cached_query(f"""
                    SELECT
                        d.MSISDN, d.IMSI, d.EpsProfileId, d.EpsIndMappingContextId, d.IMPI,
                        multiIf(
                            d.EpsProfileId = '' OR d.EpsProfileId IS NULL, 'No EPS Profile',
                            d.EpsIndMappingContextId NOT IN ('15$2008300586', '15$1008300586'), 'Wrong APN Mapping',
                            d.IMPI = '' OR d.IMPI IS NULL, 'No IMS Identity',
                            '-'
                        ) AS udc_issue,
                        if(ipw.msisdn IS NULL OR ipw.msisdn = '', 'N/A',
                            if(ipw.in_sipw, 'Y', 'N')) AS SIPW,
                        if(ipw.msisdn IS NULL OR ipw.msisdn = '', 'N/A',
                            if(ipw.in_kipw, 'Y', 'N')) AS KIPW,
                        if(ipw.msisdn IS NULL OR ipw.msisdn = '', 'N/A',
                            if(ipw.in_yipw, 'Y', 'N')) AS YIPW,
                        multiIf(
                            ipw.msisdn IS NULL OR ipw.msisdn = '', 'Not in IPW',
                            ipw.status = 'MISSING', 'Missing from Node(s)',
                            ipw.status = 'PATTERN_MISMATCH', 'NAPTR Mismatch',
                            'OK'
                        ) AS ipw_status
                    FROM default.dump AS d
                    LEFT JOIN (
                        SELECT * FROM default.ipw_reconciliation
                        WHERE process_date = (SELECT max(process_date) FROM default.ipw_reconciliation)
                    ) AS ipw ON d.MSISDN = ipw.msisdn
                    WHERE d.CDRtime = '{cdr_date_str_dash}' AND d.TICK = '215'
                        {issue_where}
                    ORDER BY d.MSISDN
                    LIMIT 100000
                """)
                if dl_rows:
                    df_dl = pd.DataFrame(dl_rows, columns=[
                        'MSISDN', 'IMSI', 'EpsProfileId', 'EpsIndMappingContextId', 'IMPI',
                        'UDC Issue', 'SIPW', 'KIPW', 'YIPW', 'IPW Status'
                    ])
                    st.download_button(
                        label="Download Misconfigured VoLTE Users (CSV)",
                        data=df_dl.to_csv(index=False),
                        file_name=f"volte_unhealthy_{cdr_date_str}.csv",
                        mime='text/csv'
                    )
            except Exception as e:
                st.error(f"Error loading misconfigured subscribers: {e}")

# ==================== TAB 6: HISTORICAL TRENDS ====================
with tab6:
    st.header("📈 Historical Trends")
    st.markdown(
        "Subscriber data trends over the last **7 days** based on UDC dump retention. "
        "The sidebar date selector does **not** affect this tab — all available days are shown."
    )

    # Master query: single ClickHouse scan for all sections
    trends_query = """
        SELECT
            CDRtime,
            uniq(MSISDN) AS total_subs,
            uniqIf(MSISDN, TICK = '215') AS volte_subs,
            uniqIf(MSISDN, EpsAccessRestriction = '0') AS vowifi_subs,
            uniqIf(
                MSISDN,
                length(EpsProfileId) >= 3
                AND (EpsProfileId LIKE '3%' OR EpsProfileId LIKE '5%')
                AND EpsProfileId = PDPCP
            ) AS fiveg_subs,
            uniqIf(
                MSISDN,
                TICK IN ('190', '201', '203', '205')
                AND EpsProfileId != ''
                AND EpsProfileId IS NOT NULL
            ) AS fourg_cs_subs,
            uniqIf(
                MSISDN,
                TICK IN ('190', '201', '203', '205')
                AND (EpsProfileId = '' OR EpsProfileId IS NULL)
            ) AS twog3g_subs,
            uniqIf(
                MSISDN,
                TICK = '215'
                AND EpsProfileId != ''
                AND EpsProfileId IS NOT NULL
                AND EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
                AND IMPI != ''
                AND IMPI IS NOT NULL
            ) AS healthy_volte,
            uniqIf(
                MSISDN,
                TICK = '215'
                AND NOT (
                    EpsProfileId != ''
                    AND EpsProfileId IS NOT NULL
                    AND EpsIndMappingContextId IN ('15$2008300586', '15$1008300586')
                    AND IMPI != ''
                    AND IMPI IS NOT NULL
                )
            ) AS unhealthy_volte
        FROM default.dump
        WHERE CDRtime >= today() - 7
        GROUP BY CDRtime
        ORDER BY CDRtime
        SETTINGS max_memory_usage = 10000000000
    """

    trends_available = False
    df_trends = pd.DataFrame()

    # Disk-cached by today's date — first query of the day hits DB, all subsequent
    # logins/reruns load instantly from disk. Key rolls over at midnight.
    try:
        today_key = date.today().isoformat()
        trend_rows, trend_cols = run_cached_query_with_disk(
            trends_query, today_key, "historical_trends_last7"
        )
        if trend_rows:
            df_trends = pd.DataFrame(trend_rows, columns=[
                'CDRtime', 'total_subs', 'volte_subs', 'vowifi_subs', 'fiveg_subs',
                'fourg_cs_subs', 'twog3g_subs', 'healthy_volte', 'unhealthy_volte'
            ])
            df_trends['CDRtime'] = pd.to_datetime(df_trends['CDRtime'])
            for col in df_trends.columns[1:]:
                df_trends[col] = df_trends[col].astype(int)
            trends_available = True
        else:
            st.warning("No historical data found. The UDC table may only contain data for today.")
    except Exception as e:
        st.error(f"Error loading historical trend data: {e}")

    if trends_available and len(df_trends) > 0:

        def _trend_layout(fig, title, yaxis_title="Subscribers"):
            fig.update_layout(
                title=title,
                xaxis_title="Date",
                yaxis_title=yaxis_title,
                height=400,
                plot_bgcolor='white',
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                xaxis=dict(showgrid=True, gridcolor='lightgrey', tickformat='%Y-%m-%d'),
                yaxis=dict(showgrid=True, gridcolor='lightgrey'),
            )
            return fig

        latest = df_trends.iloc[-1]
        has_previous = len(df_trends) >= 2
        previous = df_trends.iloc[-2] if has_previous else None

        def _delta(col):
            if previous is None:
                return None
            return int(latest[col]) - int(previous[col])

        # ── SECTION 1: Subscriber Volume Trends ──────────────────────────────
        st.subheader("📊 Subscriber Volume Trends")

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            d = _delta('total_subs')
            st.metric("Total Subscribers (Latest)", f"{int(latest['total_subs']):,}",
                      delta=f"{d:+,}" if d is not None else None)
        with col2:
            d = _delta('volte_subs')
            st.metric("VoLTE (Latest)", f"{int(latest['volte_subs']):,}",
                      delta=f"{d:+,}" if d is not None else None)
        with col3:
            d = _delta('vowifi_subs')
            st.metric("VoWiFi (Latest)", f"{int(latest['vowifi_subs']):,}",
                      delta=f"{d:+,}" if d is not None else None)
        with col4:
            d = _delta('fiveg_subs')
            st.metric("5G (Latest)", f"{int(latest['fiveg_subs']):,}",
                      delta=f"{d:+,}" if d is not None else None)

        # Compute day-over-day deltas for line charts
        df_delta = df_trends[['CDRtime', 'total_subs', 'volte_subs', 'vowifi_subs',
                               'fiveg_subs', 'healthy_volte', 'unhealthy_volte']].copy()
        for col in df_delta.columns[1:]:
            df_delta[col] = df_delta[col].diff().fillna(0).astype(int)

        fig_vol = go.Figure()
        fig_vol.add_trace(go.Scatter(
            x=df_delta['CDRtime'], y=df_delta['total_subs'],
            mode='lines+markers+text', name='Total',
            line=dict(color='#2c3e50', width=2), marker=dict(size=7),
            text=df_delta['total_subs'].apply(lambda v: f"{v:+,}"),
            textposition='top center', textfont=dict(size=9)
        ))
        fig_vol.add_trace(go.Scatter(
            x=df_delta['CDRtime'], y=df_delta['volte_subs'],
            mode='lines+markers+text', name='VoLTE',
            line=dict(color='#3498db', width=2), marker=dict(size=7),
            text=df_delta['volte_subs'].apply(lambda v: f"{v:+,}"),
            textposition='top center', textfont=dict(size=9)
        ))
        fig_vol.add_trace(go.Scatter(
            x=df_delta['CDRtime'], y=df_delta['vowifi_subs'],
            mode='lines+markers+text', name='VoWiFi',
            line=dict(color='#2ecc71', width=2), marker=dict(size=7),
            text=df_delta['vowifi_subs'].apply(lambda v: f"{v:+,}"),
            textposition='top center', textfont=dict(size=9)
        ))
        fig_vol.add_trace(go.Scatter(
            x=df_delta['CDRtime'], y=df_delta['fiveg_subs'],
            mode='lines+markers+text', name='5G',
            line=dict(color='#9b59b6', width=2), marker=dict(size=7),
            text=df_delta['fiveg_subs'].apply(lambda v: f"{v:+,}"),
            textposition='top center', textfont=dict(size=9)
        ))
        fig_vol.add_hline(y=0, line_dash='dash', line_color='grey', opacity=0.5)
        _trend_layout(fig_vol, "Daily Change in Subscriber Counts (vs Previous Day)", yaxis_title="Change in Subscribers")
        st.plotly_chart(fig_vol, use_container_width=True)

        st.markdown("---")

        # ── SECTION 2: Network Type Distribution Trends ───────────────────────
        st.subheader("📶 Network Type Distribution Trends")

        display_mode = st.radio(
            "Display mode:",
            options=["Absolute Counts", "Percentage of Total"],
            horizontal=True,
            key="hist_network_mode"
        )

        network_cols = ['fiveg_subs', 'volte_subs', 'fourg_cs_subs', 'twog3g_subs']
        network_labels = ['5G', '4G VoLTE', '4G CS', '2G/3G']
        network_colors = ['#2ecc71', '#3498db', '#f39c12', '#e74c3c']

        if display_mode == "Percentage of Total":
            row_totals = df_trends[network_cols].sum(axis=1).replace(0, 1)
            plot_df = df_trends[network_cols].div(row_totals, axis=0) * 100
            yaxis_label = "% of Subscribers"
        else:
            plot_df = df_trends[network_cols]
            yaxis_label = "Subscribers"

        fig_net = go.Figure()
        for col, label, color in zip(network_cols, network_labels, network_colors):
            fig_net.add_trace(go.Bar(
                x=df_trends['CDRtime'],
                y=plot_df[col],
                name=label,
                marker_color=color
            ))
        fig_net.update_layout(barmode='stack')
        _trend_layout(fig_net, "Network Type Distribution Over Time", yaxis_title=yaxis_label)
        st.plotly_chart(fig_net, use_container_width=True)

        st.markdown("---")

        # ── SECTION 3: VoLTE Health Trends ────────────────────────────────────
        st.subheader("🏥 VoLTE Health Trends")
        st.markdown(
            "_Health criteria (UDC-only): TICK=215, EpsProfileId set, "
            "EpsIndMappingContextId ∈ {15\\$2008300586, 15\\$1008300586}, IMPI set._"
        )

        total_volte_latest = int(latest['volte_subs'])
        healthy_latest = int(latest['healthy_volte'])
        unhealthy_latest = int(latest['unhealthy_volte'])

        col1, col2, col3 = st.columns(3)
        with col1:
            d = _delta('volte_subs')
            st.metric("Total VoLTE (Latest)", f"{total_volte_latest:,}",
                      delta=f"{d:+,}" if d is not None else None)
        with col2:
            d = _delta('healthy_volte')
            st.metric("Healthy (Latest)", f"{healthy_latest:,}",
                      delta=f"{d:+,}" if d is not None else None, delta_color="normal")
        with col3:
            d = _delta('unhealthy_volte')
            st.metric("Unhealthy (Latest)", f"{unhealthy_latest:,}",
                      delta=f"{d:+,}" if d is not None else None, delta_color="inverse")

        fig_health = go.Figure()
        fig_health.add_trace(go.Scatter(
            x=df_delta['CDRtime'], y=df_delta['healthy_volte'],
            mode='lines+markers+text', name='Healthy VoLTE',
            line=dict(color='#2ecc71', width=2), marker=dict(size=7),
            text=df_delta['healthy_volte'].apply(lambda v: f"{v:+,}"),
            textposition='top center', textfont=dict(size=9)
        ))
        fig_health.add_trace(go.Scatter(
            x=df_delta['CDRtime'], y=df_delta['unhealthy_volte'],
            mode='lines+markers+text', name='Unhealthy VoLTE',
            line=dict(color='#e74c3c', width=2), marker=dict(size=7),
            text=df_delta['unhealthy_volte'].apply(lambda v: f"{v:+,}"),
            textposition='top center', textfont=dict(size=9)
        ))
        fig_health.add_hline(y=0, line_dash='dash', line_color='grey', opacity=0.5)
        _trend_layout(fig_health, "Daily Change in VoLTE Health (vs Previous Day)", yaxis_title="Change in Subscribers")
        st.plotly_chart(fig_health, use_container_width=True)

        df_trends['health_pct'] = (
            df_trends['healthy_volte'] / df_trends['volte_subs'].replace(0, 1) * 100
        ).round(2)

        fig_hpct = go.Figure()
        fig_hpct.add_trace(go.Scatter(
            x=df_trends['CDRtime'], y=df_trends['health_pct'],
            mode='lines+markers+text',
            name='Health Rate %',
            line=dict(color='#27ae60', width=2), marker=dict(size=7),
            text=df_trends['health_pct'].apply(lambda v: f"{v:.1f}%"),
            textposition='top center',
            textfont=dict(size=10)
        ))
        fig_hpct.update_layout(yaxis=dict(range=[0, 105]))
        _trend_layout(fig_hpct, "VoLTE Health Rate % Over Time", yaxis_title="Health Rate (%)")
        st.plotly_chart(fig_hpct, use_container_width=True)

        st.markdown("---")

        # ── SECTION 4: Summary Table + CSV Export ─────────────────────────────
        st.subheader("📋 Historical Summary Table")
        st.markdown("All available UDC days with key metrics. Ordered most-recent-first.")

        df_summary = df_trends[[
            'CDRtime', 'total_subs', 'volte_subs', 'vowifi_subs',
            'fiveg_subs', 'healthy_volte', 'unhealthy_volte'
        ]].copy().sort_values('CDRtime', ascending=False)

        df_summary['volte_pct'] = (
            df_summary['volte_subs'] / df_summary['total_subs'].replace(0, 1) * 100
        ).round(2)
        df_summary['vowifi_pct'] = (
            df_summary['vowifi_subs'] / df_summary['total_subs'].replace(0, 1) * 100
        ).round(2)
        df_summary['fiveg_pct'] = (
            df_summary['fiveg_subs'] / df_summary['total_subs'].replace(0, 1) * 100
        ).round(2)
        df_summary['health_rate_pct'] = (
            df_summary['healthy_volte'] / df_summary['volte_subs'].replace(0, 1) * 100
        ).round(2)

        df_summary['CDRtime'] = df_summary['CDRtime'].dt.strftime('%Y-%m-%d')

        df_display = df_summary.rename(columns={
            'CDRtime': 'Date',
            'total_subs': 'Total Subs',
            'volte_subs': 'VoLTE',
            'vowifi_subs': 'VoWiFi',
            'fiveg_subs': '5G',
            'healthy_volte': 'VoLTE Healthy',
            'unhealthy_volte': 'VoLTE Unhealthy',
            'volte_pct': 'VoLTE %',
            'vowifi_pct': 'VoWiFi %',
            'fiveg_pct': '5G %',
            'health_rate_pct': 'Health Rate %'
        })

        st.dataframe(df_display, use_container_width=True)

        csv_export = df_display.to_csv(index=False)
        st.download_button(
            label="📥 Download Historical Summary (CSV)",
            data=csv_export,
            file_name=f"udc_historical_trends_{date.today().strftime('%Y-%m-%d')}.csv",
            mime="text/csv",
            key="hist_trends_csv"
        )

    elif trends_available and len(df_trends) == 0:
        st.info(
            "No historical data found for the last 7 days. "
            "This can happen when the pipeline has only just started or when the database is being refreshed."
        )

# ==================== TAB 7: PC/GT AUDIT ====================
with tab7:
    render_pcgt_audit_tab()

# ==================== TAB 8: CNACLD EXTRACTOR ====================
with tab8:
    render_cnacld_tab()

# Footer
st.markdown("---")
st.markdown("**Developed by:** Haneen Alaa | CS Core Operations Intern")
st.markdown("**Supervised by:** Ahmed Mohamed Gamal | Voice Core Operations Manager")