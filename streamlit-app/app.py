import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import clickhouse_connect
import os
import warnings
warnings.filterwarnings('ignore')

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

# SET USERNAME AND PASSWORD
USERNAME = "admin"
PASSWORD = "admin"

# LOGIN CHECK
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

def login():
    st.markdown("<h1 style='color: #A40000;'>📡 e& CS Core Operations</h1>", unsafe_allow_html=True)
    st.markdown("<h3> User Profile Management</h3>", unsafe_allow_html=True)
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        if username == USERNAME and password == PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Wrong username or password")

if not st.session_state.authenticated:
    login()
    st.stop()

# Title
st.title("📡 e& CS Core Operations Dashboard")

# Connect to ClickHouse - Create new connection for each query to avoid concurrency issues
def get_clickhouse_client():
    try:
        client = clickhouse_connect.get_client(
            host=os.getenv('CLICKHOUSE_HOST', 'telecom_clickhouse'),
            port=int(os.getenv('CLICKHOUSE_PORT', '8123')),
            username='default',
            password=''
        )
        return client
    except Exception as e:
        st.error(f"Failed to connect to ClickHouse: {e}")
        return None

def run_query(query):
    """Execute a query with a fresh connection to avoid concurrency issues"""
    client = get_clickhouse_client()
    if client is None:
        raise Exception("Cannot connect to database")
    try:
        result = client.query(query)
        return result
    finally:
        client.close()

# Cached query function - caches results for 5 minutes to speed up dashboard
@st.cache_data(ttl=300, show_spinner=False)
def run_cached_query(query):
    """Execute a query with caching (5 min TTL) for better performance"""
    client = get_clickhouse_client()
    if client is None:
        raise Exception("Cannot connect to database")
    try:
        result = client.query(query)
        # Convert to tuple of tuples for caching (lists aren't hashable)
        return tuple(tuple(row) for row in result.result_rows), result.column_names
    finally:
        client.close()

def get_cached_result(query):
    """Wrapper to get cached query results"""
    rows, columns = run_cached_query(query)
    return list(rows), columns

# User lookup cached query - caches results for 1 hour (user data doesn't change frequently)
@st.cache_data(ttl=3600, show_spinner=False)
def run_user_lookup_cached(query):
    """Execute a user lookup query with caching (1 hour TTL) for fast repeated lookups"""
    client = get_clickhouse_client()
    if client is None:
        raise Exception("Cannot connect to database")
    try:
        result = client.query(query)
        # Convert to tuple of tuples for caching (lists aren't hashable)
        return tuple(tuple(row) for row in result.result_rows), result.column_names
    finally:
        client.close()

def get_user_lookup_result(query):
    """Wrapper to get cached user lookup query results"""
    rows, columns = run_user_lookup_cached(query)
    return list(rows), columns

# Test connection (quick check)
try:
    test_client = get_clickhouse_client()
    if test_client is None:
        st.error("Cannot connect to database. Please check ClickHouse service.")
        st.stop()
    test_client.close()
except Exception as e:
    st.error(f"Database connection failed: {e}")
    st.stop()

# Sidebar - Date Selection
st.sidebar.title("⚙️ Settings")

# Get all available UDC dates from the database (dates extracted from filenames)
try:
    rows, _ = run_cached_query("SELECT DISTINCT CDRtime FROM default.dump ORDER BY CDRtime DESC")
    available_udc_dates = []
    for row in rows:
        if row[0]:
            d = row[0]
            if isinstance(d, str):
                d = datetime.strptime(d, '%Y-%m-%d').date()
            available_udc_dates.append(d)

    if available_udc_dates:
        default_date = available_udc_dates[0]  # Latest available date
    else:
        default_date = date.today()
        available_udc_dates = [default_date]
except:
    default_date = date.today()
    available_udc_dates = [default_date]

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
    st.cache_data.clear()
    st.rerun()

# Verify data exists for selected date
try:
    rows, _ = run_cached_query(f"SELECT COUNT(*) as count FROM default.dump WHERE CDRtime = '{cdr_date_str_dash}'")
    date_count = rows[0][0]

    if date_count == 0:
        st.warning(f"⚠️ No data available for {cdr_date_str}. Please select a different date from the sidebar.")
        st.stop()
    else:
        st.sidebar.success(f"✓ {date_count:,} records for {cdr_date_str}")
except Exception as e:
    st.error(f"Error checking data: {e}")
    st.stop()

# Create tabs
tab1, tab2, tab3, tab4 = st.tabs(["📊 Statistics", "🔍 User Lookup", "🔎 Advanced Search & Filter", "📱 MNP Analysis"])

# ==================== TAB 1: STATISTICS ====================
with tab1:
    st.header("📊 Subscriber Statistics")

    # ==================== VOICE STATISTICS SECTION ====================
    st.subheader("📞 Voice Statistics")

    # Main metrics
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        query = f"""
            SELECT COUNT(DISTINCT MSISDN) as count
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
        """
        rows, _ = run_cached_query(query)
        total_subs = rows[0][0]
        st.metric("Total Subscribers", f"{total_subs:,}")

    with col2:
        query = f"""
            SELECT COUNT(DISTINCT MSISDN) as count
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}' AND TICK = '215'
        """
        rows, _ = run_cached_query(query)
        volte_subs = rows[0][0]
        volte_pct = (volte_subs / total_subs * 100) if total_subs > 0 else 0
        st.metric("VoLTE Subscribers", f"{volte_subs:,}", f"{volte_pct:.1f}%", delta_color="normal")

    with col3:
        query = f"""
            SELECT COUNT(DISTINCT MSISDN) as count
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}' AND EpsAccessRestriction = '0'
        """
        rows, _ = run_cached_query(query)
        vowifi_subs = rows[0][0]
        vowifi_pct = (vowifi_subs / total_subs * 100) if total_subs > 0 else 0
        st.metric("VoWiFi Subscribers", f"{vowifi_subs:,}", f"{vowifi_pct:.1f}%", delta_color="normal")

    with col4:
        # 5G: EpsProfileId is 3+ digits starting with 3 or 5, AND EpsProfileId = PDPCP
        query = f"""
            SELECT COUNT(DISTINCT MSISDN) as count
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
              AND length(EpsProfileId) >= 3
              AND (EpsProfileId LIKE '3%' OR EpsProfileId LIKE '5%')
              AND EpsProfileId = PDPCP
        """
        rows, _ = run_cached_query(query)
        fiveg_subs = rows[0][0]
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
                COUNT(DISTINCT MSISDN) as subscribers
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
              AND TICK IS NOT NULL AND TICK != ''
              AND TICK != '215'
            GROUP BY service_type
            HAVING service_type IS NOT NULL
            ORDER BY subscribers DESC
        """
        rows, cols = run_cached_query(query)
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
                COUNT(DISTINCT MSISDN) as subscribers
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
            GROUP BY network_type
            ORDER BY subscribers DESC
        """
        rows, cols = run_cached_query(query)
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
                COUNT(DISTINCT MSISDN) as subscribers
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
              AND EpsAccessRestriction IS NOT NULL
              AND EpsAccessRestriction != ''
            GROUP BY status
            HAVING status IS NOT NULL
            ORDER BY subscribers DESC
        """
        rows, cols = run_cached_query(query)
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
                COUNT(DISTINCT CASE WHEN CFU = '1' THEN MSISDN END) as cfu_enabled,
                COUNT(DISTINCT CASE WHEN CFB = '1' THEN MSISDN END) as cfb_enabled,
                COUNT(DISTINCT CASE WHEN HOLD = '1' THEN MSISDN END) as hold_enabled,
                COUNT(DISTINCT CASE WHEN CAW = '1' THEN MSISDN END) as caw_enabled,
                COUNT(DISTINCT CASE WHEN DCF = '1' THEN MSISDN END) as mcn_enabled,
                COUNT(DISTINCT CASE WHEN CLIR = '1' THEN MSISDN END) as clir_enabled,
                COUNT(DISTINCT CASE WHEN COLP = '1' THEN MSISDN END) as colp_enabled,
                COUNT(DISTINCT MSISDN) as total
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
        """
        rows, _ = run_cached_query(query)
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
        query = f"""
            SELECT COUNT(DISTINCT MSISDN) as roaming
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
              AND VLRADD IS NOT NULL
              AND VLRADD != ''
              AND NOT startsWith(VLRADD, '1920117900')
        """
        rows, _ = run_cached_query(query)
        roaming_subs = rows[0][0]
        roaming_pct = (roaming_subs / total_subs * 100) if total_subs > 0 else 0
        st.metric("Roaming Subscribers", f"{roaming_subs:,}", f"{roaming_pct:.1f}%", delta_color="normal")

    with col2:
        query = f"""
            SELECT COUNT(DISTINCT MSISDN) as local
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
              AND VLRADD IS NOT NULL
              AND VLRADD != ''
              AND startsWith(VLRADD, '1920117900')
        """
        rows, _ = run_cached_query(query)
        local_subs = rows[0][0]
        local_pct = (local_subs / total_subs * 100) if total_subs > 0 else 0
        st.metric("Egypt/Local", f"{local_subs:,}", f"{local_pct:.1f}%", delta_color="normal")

    with col3:
        # Fixed FVNO: no VLR, no TICK, no PDPCP, MSISDN length 9-11 digits, contains '611'
        query = f"""
            SELECT COUNT(DISTINCT MSISDN) as fvno
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
              AND (VLRADD IS NULL OR VLRADD = '')
              AND (TICK IS NULL OR TICK = '')
              AND (PDPCP IS NULL OR PDPCP = '')
              AND length(MSISDN) >= 9
              AND length(MSISDN) <= 11
              AND MSISDN LIKE '%611%'
        """
        rows, _ = run_cached_query(query)
        fvno_subs = rows[0][0]
        fvno_pct = (fvno_subs / total_subs * 100) if total_subs > 0 else 0
        st.metric("Fixed FVNO", f"{fvno_subs:,}", f"{fvno_pct:.1f}%", delta_color="normal")

    st.markdown("---")

    # ==================== DATA SIMs STATISTICS SECTION ====================
    st.subheader("📶 Data SIMs Statistics")
    st.markdown("*Data SIMs: Subscribers with PDPCP or EpsUserIpV4Address (APN) but without TICK (no voice service)*")

    # Data SIMs metrics
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        # Total Data SIMs: has PDPCP OR has EpsIndDefContextId, AND no TICK
        query = f"""
            SELECT COUNT(DISTINCT MSISDN) as count
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
              AND (TICK IS NULL OR TICK = '')
              AND (
                (PDPCP IS NOT NULL AND PDPCP != '')
                OR (EpsIndDefContextId IS NOT NULL AND EpsIndDefContextId != '')
              )
        """
        rows, _ = run_cached_query(query)
        data_sims_total = rows[0][0]
        data_pct = (data_sims_total / total_subs * 100) if total_subs > 0 else 0
        st.metric("Total Data SIMs", f"{data_sims_total:,}", f"{data_pct:.1f}%", delta_color="normal")

    with col2:
        # 2G/3G Data SIMs: EpsProfileId is NULL or empty
        query = f"""
            SELECT COUNT(DISTINCT MSISDN) as count
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
              AND (TICK IS NULL OR TICK = '')
              AND (
                (PDPCP IS NOT NULL AND PDPCP != '')
                OR (EpsIndDefContextId IS NOT NULL AND EpsIndDefContextId != '')
              )
              AND (EpsProfileId IS NULL OR EpsProfileId = '')
        """
        rows, _ = run_cached_query(query)
        data_2g3g = rows[0][0]
        data_2g3g_pct = (data_2g3g / data_sims_total * 100) if data_sims_total > 0 else 0
        st.metric("2G/3G Data SIMs", f"{data_2g3g:,}", f"{data_2g3g_pct:.1f}%", delta_color="normal")

    with col3:
        # 5G Data SIMs
        eps_5g_list = ['5115', '503', '554', '524', '585', '536', '530', '535', '511', '533', '537', '531', '540', '578', '579', '580', '592', '593', '594', '595', '557', '5111', '5113']
        eps_5g_str = "', '".join(eps_5g_list)
        query = f"""
            SELECT COUNT(DISTINCT MSISDN) as count
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
              AND (TICK IS NULL OR TICK = '')
              AND (
                (PDPCP IS NOT NULL AND PDPCP != '')
                OR (EpsIndDefContextId IS NOT NULL AND EpsIndDefContextId != '')
              )
              AND EpsProfileId IN ('{eps_5g_str}')
        """
        rows, _ = run_cached_query(query)
        data_5g = rows[0][0]
        data_5g_pct = (data_5g / data_sims_total * 100) if data_sims_total > 0 else 0
        st.metric("5G Data SIMs", f"{data_5g:,}", f"{data_5g_pct:.1f}%", delta_color="normal")

    with col4:
        # 4G Data SIMs = Total - 2G/3G - 5G
        data_4g = max(0, data_sims_total - data_2g3g - data_5g)
        data_4g_pct = (data_4g / data_sims_total * 100) if data_sims_total > 0 else 0
        st.metric("4G Data SIMs", f"{data_4g:,}", f"{data_4g_pct:.1f}%", delta_color="normal")

    with col5:
        # Corporate Data Dial: has EpsIndDefContextId and doesn't end with 37 or 00
        query = f"""
            SELECT COUNT(DISTINCT MSISDN) as count
            FROM default.dump
            WHERE CDRtime = '{cdr_date_str_dash}'
              AND EpsIndDefContextId IS NOT NULL
              AND EpsIndDefContextId != ''
              AND NOT endsWith(EpsIndDefContextId, '37')
              AND NOT endsWith(EpsIndDefContextId, '00')
        """
        rows, _ = run_cached_query(query)
        corporate_data = rows[0][0]
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
                COUNT(DISTINCT MSISDN) as subscribers
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
        rows, _ = run_cached_query(query)
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
                        CDRtime
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
                        'TS11', 'TS21', 'TS22', 'DCF', 'EpsAccessRestriction', 'CDRtime'
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
                    # These appear in Etisalat's QPM records (ported IN to Etisalat)
                    query = f"""
                        SELECT
                            MSISDN,
                            'Etisalat' AS ported_to,
                            Date
                        FROM default.MNP_details
                        WHERE Prefix = '2011'
                          AND NPREFIX = 'QPM'
                          AND MSISDN LIKE '{prefix}%'
                        ORDER BY rand()
                        LIMIT {sample_size}
                    """
            else:
                # Ported In - numbers coming TO this operator from others
                if selected_operator == 'Etisalat':
                    # Numbers from other operators (2010/2012/2015) that came TO Etisalat
                    # These are QPM records with non-2011 MSISDN
                    query = f"""
                        SELECT
                            MSISDN,
                            CASE
                                WHEN MSISDN LIKE '2010%' THEN 'Vodafone'
                                WHEN MSISDN LIKE '2012%' THEN 'Orange'
                                WHEN MSISDN LIKE '2015%' THEN 'WE'
                                ELSE 'Unknown'
                            END AS ported_from,
                            Date
                        FROM default.MNP_details
                        WHERE Prefix = '2011'
                          AND NPREFIX = 'QPM'
                        ORDER BY rand()
                        LIMIT {sample_size}
                    """
                else:
                    # Numbers from Etisalat (2011*) that came TO this operator
                    # These are QPI/QPE/QPQ records (Etisalat numbers ported out)
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

# Footer
st.markdown("---")
st.markdown("**Developed by:** Haneen Alaa | CS Core Operations Intern")
st.markdown("**Supervised by:** Ahmed Mohamed Gamal | Voice Core Operations Manager")
