"""
PC/GT Audit Module
Parses and reconciles PC/GT data from:
1. CSV/Excel audit file (2-PC, 3-PC, GT with status)
2. Huawei ALLME files (SCCPGT entries)
3. Ericsson OSS channel logs (c7gsp, c7gcp, c7spp sections)
"""

import streamlit as st
import pandas as pd
import re
import os
import io
import json
from io import BytesIO
from datetime import datetime

# ═══════════════════════════════════════════
#  PERSISTENT STORAGE
# ═══════════════════════════════════════════
# All data is persisted on disk so it survives refreshes and is shared
# across all users. Upload replaces previous version atomically.
STORAGE_DIR = os.environ.get("PCGT_STORAGE_DIR", "/app/data/pcgt")
os.makedirs(STORAGE_DIR, exist_ok=True)

AUDIT_2PC_PATH     = os.path.join(STORAGE_DIR, "audit_2pc.pkl")
AUDIT_3PC_PATH     = os.path.join(STORAGE_DIR, "audit_3pc.pkl")
AUDIT_GT_PATH      = os.path.join(STORAGE_DIR, "audit_gt.pkl")
AUDIT_META_PATH    = os.path.join(STORAGE_DIR, "audit_meta.json")
HUAWEI_PATH        = os.path.join(STORAGE_DIR, "huawei.pkl")
HUAWEI_META_PATH   = os.path.join(STORAGE_DIR, "huawei_meta.json")
ERICSSON_PATH      = os.path.join(STORAGE_DIR, "ericsson.pkl")
ERICSSON_META_PATH = os.path.join(STORAGE_DIR, "ericsson_meta.json")
RESERVATIONS_PATH  = os.path.join(STORAGE_DIR, "reservations.json")


def _atomic_write(path, writer_fn):
    """Write via a temp file, then rename — atomic so concurrent readers see a consistent file."""
    tmp = path + ".tmp"
    writer_fn(tmp)
    os.replace(tmp, path)


def _save_df(df, path):
    _atomic_write(path, lambda p: df.to_pickle(p))


def _load_df(path):
    if os.path.exists(path):
        try:
            return pd.read_pickle(path)
        except Exception:
            pass
    return pd.DataFrame()


def _save_json(obj, path):
    _atomic_write(path, lambda p: open(p, "w", encoding="utf-8").write(json.dumps(obj, indent=2, default=str)))


def _load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def save_audit_data(df_2pc, df_3pc, df_gt, username, source_filename=""):
    _save_df(df_2pc, AUDIT_2PC_PATH)
    _save_df(df_3pc, AUDIT_3PC_PATH)
    _save_df(df_gt, AUDIT_GT_PATH)
    _save_json({
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "uploaded_by": username,
        "source_filename": source_filename,
        "row_counts": {"2pc": len(df_2pc), "3pc": len(df_3pc), "gt": len(df_gt)},
    }, AUDIT_META_PATH)


def load_audit_data():
    return (
        _load_df(AUDIT_2PC_PATH),
        _load_df(AUDIT_3PC_PATH),
        _load_df(AUDIT_GT_PATH),
        _load_json(AUDIT_META_PATH, {}),
    )


def save_huawei_data(df, username, file_names):
    _save_df(df, HUAWEI_PATH)
    _save_json({
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "uploaded_by": username,
        "files": list(file_names),
        "row_count": len(df),
    }, HUAWEI_META_PATH)


def load_huawei_data():
    return _load_df(HUAWEI_PATH), _load_json(HUAWEI_META_PATH, {})


def save_ericsson_data(df, username, file_names):
    _save_df(df, ERICSSON_PATH)
    _save_json({
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "uploaded_by": username,
        "files": list(file_names),
        "row_count": len(df),
    }, ERICSSON_META_PATH)


def load_ericsson_data():
    return _load_df(ERICSSON_PATH), _load_json(ERICSSON_META_PATH, {})


def load_reservations():
    return _load_json(RESERVATIONS_PATH, [])


def save_reservations(reservations):
    _save_json(reservations, RESERVATIONS_PATH)


def clear_audit_data():
    for p in [AUDIT_2PC_PATH, AUDIT_3PC_PATH, AUDIT_GT_PATH, AUDIT_META_PATH]:
        if os.path.exists(p):
            os.remove(p)


def clear_huawei_data():
    for p in [HUAWEI_PATH, HUAWEI_META_PATH]:
        if os.path.exists(p):
            os.remove(p)


def clear_ericsson_data():
    for p in [ERICSSON_PATH, ERICSSON_META_PATH]:
        if os.path.exists(p):
            os.remove(p)


# ─── Session state keys (kept for backward compat with cached reconciliation) ───
SS_RESERVATIONS = "pcgt_reservations"


# ═══════════════════════════════════════════
#  PARSING FUNCTIONS
# ═══════════════════════════════════════════

def load_audit_excel(file_path_or_buffer):
    """Load the Excel audit file into structured DataFrames for 2-PC, 3-PC, and GT."""
    import openpyxl
    import shutil
    import tempfile

    # If the file has .csv extension but is actually xlsx, copy to temp .xlsx
    if isinstance(file_path_or_buffer, str) and file_path_or_buffer.lower().endswith(".csv"):
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        tmp.close()
        shutil.copy2(file_path_or_buffer, tmp.name)
        wb = openpyxl.load_workbook(tmp.name, data_only=True)
        os.remove(tmp.name)
    else:
        wb = openpyxl.load_workbook(file_path_or_buffer, data_only=True)
    ws = wb[wb.sheetnames[0]]

    rows_2pc = []
    rows_3pc = []
    rows_gt = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        pc2 = str(row[0]).strip() if row[0] else None
        pc2_status = str(row[1]).strip() if row[1] else None
        pc3 = str(row[3]).strip() if row[3] else None
        pc3_status = str(row[4]).strip() if row[4] else None
        gt = str(int(row[6])) if row[6] and row[6] is not None else None
        gt_status = str(row[7]).strip() if row[7] else None
        gt_node = str(row[8]).strip() if row[8] else None
        gt_comment = str(row[9]).strip() if row[9] else None

        if pc2:
            rows_2pc.append({"PC": pc2, "Status/Node Name": pc2_status})
        if pc3:
            rows_3pc.append({"PC": pc3, "Status/Node Name": pc3_status})
        if gt:
            rows_gt.append({
                "GT Number": gt,
                "Status": gt_status,
                "Node Name": gt_node,
                "Comment": gt_comment
            })

    df_2pc = pd.DataFrame(rows_2pc)
    df_3pc = pd.DataFrame(rows_3pc)
    df_gt = pd.DataFrame(rows_gt)
    return df_2pc, df_3pc, df_gt


def parse_huawei_file(content, source_name=""):
    """Parse Huawei ALLME file content. Extract SCCPGT and SCCPGTG entries.
    Only keeps GT numbers that start with '201179' AND are exactly 12 digits long."""
    records = []

    gtg_map = {}
    for m in re.finditer(r'ADD SCCPGTG:GTGNM="([^"]*)".*?OPC="H\'([0-9A-Fa-f]+)"', content):
        name = m.group(1)
        opc_dec = int(m.group(2), 16)
        gtg_map[name] = opc_dec

    for m in re.finditer(
        r'ADD SCCPGT:GTNM="([^"]*)".*?ADDR=K\'([^,]+).*?GTGNM="([^"]*)"',
        content
    ):
        gtnm = m.group(1).strip()
        addr = m.group(2).strip()
        gtgnm = m.group(3).strip()

        if not (addr.isdigit() and len(addr) == 12 and addr.startswith("201179")):
            continue

        spc_match = re.search(r'SPC="H\'([0-9A-Fa-f]+)"', m.group(0))
        spc_dec = int(spc_match.group(1), 16) if spc_match else None
        gtg_pc = gtg_map.get(gtgnm)

        def get_pc_type(name):
            if name and len(name) >= 2 and name[1].upper() in ('B', 'R'):
                return "3"
            return "2"

        pc_type = get_pc_type(gtnm)
        spc_as_pc = f"{pc_type}-{spc_dec}" if spc_dec else ""
        gtg_as_pc = f"{pc_type}-{gtg_pc}" if gtg_pc else ""

        records.append({
            "GT Number": addr,
            "Node Name": gtnm,
            "GTGNM": gtgnm,
            "SPC (Hex)": spc_match.group(0) if spc_match else "",
            "SPC (Dec)": spc_dec,
            "SPC as PC": spc_as_pc,
            "PC Type": f"{pc_type}-PC",
            "Group OPC (Dec)": gtg_pc,
            "Group OPC as PC": gtg_as_pc,
            "Source File": source_name
        })

    return pd.DataFrame(records)


def parse_ericsson_log(content, source_name=""):
    """Parse Ericsson OSS channel log (c7gsp, c7gcp, c7spp sections)."""
    gt_to_gtrc = {}
    gsp_sections = re.findall(r'<c7gsp;(.*?)END', content, re.DOTALL)
    for section in gsp_sections:
        for line in section.strip().splitlines():
            line = line.strip()
            m = re.match(r'\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d{12,})\s+(\d+)', line)
            if m:
                ns = m.group(4)
                gtrc = m.group(5)
                if ns.startswith("201179"):
                    gt_to_gtrc[ns] = gtrc

    gtrc_to_pcs = {}
    gcp_sections = re.findall(r'<c7gcp:gtrc=all;(.*?)END', content, re.DOTALL)
    for section in gcp_sections:
        in_table2 = False
        current_gtrc = None
        for line in section.strip().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("CCITT") or stripped.startswith("OPERATING"):
                continue
            if stripped.startswith("GTRC") and "PSP" in stripped:
                in_table2 = False
                continue
            if stripped.startswith("GTRC") and "PRIO" in stripped:
                in_table2 = True
                continue

            if not in_table2:
                m = re.match(r'\s*(\d+)\s+(\S+)\s+\S+\s+\S+\s*(\S*)\s*(\S*)', stripped)
                if m:
                    gtrc = m.group(1)
                    psp = m.group(2)
                    if gtrc not in gtrc_to_pcs:
                        gtrc_to_pcs[gtrc] = set()
                    if re.match(r'\d+-\d+', psp):
                        gtrc_to_pcs[gtrc].add(psp)
                    parts = stripped.split()
                    for p in parts:
                        if re.match(r'\d+-\d+', p) and p != psp:
                            gtrc_to_pcs[gtrc].add(p)
            else:
                m = re.match(r'\s*(\d+)\s+\d+\s+\d+\s+(\d+-\d+)', stripped)
                if m:
                    current_gtrc = m.group(1)
                    sp = m.group(2)
                    if current_gtrc not in gtrc_to_pcs:
                        gtrc_to_pcs[current_gtrc] = set()
                    gtrc_to_pcs[current_gtrc].add(sp)
                else:
                    m2 = re.match(r'\s*\d+\s+\d+\s+(\d+-\d+)', stripped)
                    if m2 and current_gtrc:
                        gtrc_to_pcs[current_gtrc].add(m2.group(1))

    gtrc_to_pcs = {k: list(v) for k, v in gtrc_to_pcs.items()}

    pc_to_node = {}
    spp_sections = re.findall(r'<c7spp:sp=all;(.*?)END', content, re.DOTALL)
    for section in spp_sections:
        for line in section.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("CCITT") or line.startswith("SP") or line.startswith("OPERATING"):
                continue
            m = re.match(r'([\d\-]+)\s+(?:OWNSP\s+)?(\S+)', line)
            if m:
                sp = m.group(1)
                spid = m.group(2)
                if spid not in ("OWNSP",):
                    pc_to_node[sp] = spid

    records = []
    for gt, gtrc in gt_to_gtrc.items():
        pcs = gtrc_to_pcs.get(gtrc, [])
        if pcs:
            for pc in pcs:
                node = pc_to_node.get(pc, "")
                records.append({
                    "GT Number": gt,
                    "GTRC": gtrc,
                    "PC": pc,
                    "Node Name": node,
                    "Source": source_name
                })
        else:
            records.append({
                "GT Number": gt,
                "GTRC": gtrc,
                "PC": "",
                "Node Name": "",
                "Source": source_name
            })

    return pd.DataFrame(records), gt_to_gtrc, gtrc_to_pcs, pc_to_node


# ═══════════════════════════════════════════
#  RECONCILIATION
# ═══════════════════════════════════════════

def reconcile_data(df_gt_csv, df_huawei, df_ericsson):
    """Compare GT data across all 3 sources and flag discrepancies."""
    results = []

    csv_gts = set(df_gt_csv["GT Number"].dropna().astype(str).tolist()) if not df_gt_csv.empty else set()
    huawei_gts = set(df_huawei["GT Number"].dropna().astype(str).tolist()) if not df_huawei.empty else set()
    ericsson_gts = set(df_ericsson["GT Number"].dropna().astype(str).tolist()) if not df_ericsson.empty else set()

    all_gts = csv_gts | huawei_gts | ericsson_gts

    for gt in sorted(all_gts):
        in_csv = gt in csv_gts
        in_huawei = gt in huawei_gts
        in_ericsson = gt in ericsson_gts

        csv_node = ""
        csv_status = ""
        csv_comment = ""
        huawei_node = ""
        ericsson_node = ""
        ericsson_pc = ""

        if in_csv:
            row = df_gt_csv[df_gt_csv["GT Number"].astype(str) == gt].iloc[0]
            csv_node = row.get("Node Name", "")
            csv_status = row.get("Status", "")
            csv_comment = row.get("Comment", "")
        if in_huawei:
            row = df_huawei[df_huawei["GT Number"].astype(str) == gt].iloc[0]
            huawei_node = row.get("Node Name", "")
        if in_ericsson:
            row = df_ericsson[df_ericsson["GT Number"].astype(str) == gt].iloc[0]
            ericsson_node = row.get("Node Name", "")
            ericsson_pc = row.get("PC", "")

        issues = []
        if not in_csv:
            issues.append("Missing in Audit CSV")
        if not in_huawei:
            issues.append("Missing in Huawei")
        if not in_ericsson:
            issues.append("Missing in Ericsson")

        nodes = [n for n in [csv_node, huawei_node, ericsson_node] if n and n.strip()]
        if len(set(n.strip().upper() for n in nodes)) > 1:
            issues.append("Node name mismatch")

        status_label = "OK" if not issues else "; ".join(issues)

        results.append({
            "GT Number": gt,
            "In CSV": "Yes" if in_csv else "No",
            "In Huawei": "Yes" if in_huawei else "No",
            "In Ericsson": "Yes" if in_ericsson else "No",
            "CSV Node": csv_node,
            "CSV Status": csv_status,
            "CSV Comment": csv_comment,
            "Huawei Node": huawei_node,
            "Ericsson Node": ericsson_node,
            "Ericsson PC": ericsson_pc,
            "Audit Result": status_label
        })

    return pd.DataFrame(results)


# ═══════════════════════════════════════════
#  RESERVATION LOGIC
# ═══════════════════════════════════════════

def get_available_nodes(df_2pc, df_3pc, df_gt):
    """Return nodes available for reservation based on rules."""
    avail_2pc = df_2pc[
        df_2pc["Status/Node Name"].str.strip().str.lower() == "not defined"
    ]["PC"].tolist() if not df_2pc.empty else []

    avail_3pc = df_3pc[
        df_3pc["Status/Node Name"].str.strip().str.lower() == "not defined"
    ]["PC"].tolist() if not df_3pc.empty else []

    avail_gt = []
    if not df_gt.empty:
        for _, row in df_gt.iterrows():
            status = str(row.get("Status", "")).strip().lower()
            node = str(row.get("Node Name", "")).strip().lower()
            comment = str(row.get("Comment", "")).strip().lower()
            if status == "not defined" and ("free to use" in comment or "free to use" in node):
                avail_gt.append(row["GT Number"])

    return avail_2pc, avail_3pc, avail_gt


def export_audit_excel(df_2pc, df_3pc, df_gt, reservations):
    """Build an updated Excel file reflecting any reservations made."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    # Dataframes already reflect reservations (applied at reserve-time on disk);
    # we only need to copy to avoid mutating the caller's objects while styling.
    df_2pc = df_2pc.copy()
    df_3pc = df_3pc.copy()
    df_gt = df_gt.copy()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Final audit"

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="A40000", end_color="A40000", fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin")
    )

    headers = ["2-PC", "Status/Node name", "", "3-PC", "Status/Node name", "",
               "GT", "Status", "Node name", "Comment"]
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        if h:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border

    max_rows = max(len(df_2pc), len(df_3pc), len(df_gt))

    defined_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    not_defined_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    reserved_fill = PatternFill(start_color="BDD7EE", end_color="BDD7EE", fill_type="solid")

    for i in range(max_rows):
        row_idx = i + 2

        if i < len(df_2pc):
            ws.cell(row=row_idx, column=1, value=df_2pc.iloc[i]["PC"]).border = thin_border
            status_val = df_2pc.iloc[i]["Status/Node Name"]
            cell = ws.cell(row=row_idx, column=2, value=status_val)
            cell.border = thin_border
            if status_val and "reserved" in str(status_val).lower():
                cell.fill = reserved_fill
            elif status_val and "not defined" in str(status_val).lower():
                cell.fill = not_defined_fill
            else:
                cell.fill = defined_fill

        if i < len(df_3pc):
            ws.cell(row=row_idx, column=4, value=df_3pc.iloc[i]["PC"]).border = thin_border
            status_val = df_3pc.iloc[i]["Status/Node Name"]
            cell = ws.cell(row=row_idx, column=5, value=status_val)
            cell.border = thin_border
            if status_val and "reserved" in str(status_val).lower():
                cell.fill = reserved_fill
            elif status_val and "not defined" in str(status_val).lower():
                cell.fill = not_defined_fill
            else:
                cell.fill = defined_fill

        if i < len(df_gt):
            gt_row = df_gt.iloc[i]
            ws.cell(row=row_idx, column=7, value=gt_row["GT Number"]).border = thin_border

            status_val = gt_row["Status"]
            cell = ws.cell(row=row_idx, column=8, value=status_val)
            cell.border = thin_border
            if status_val and "reserved" in str(status_val).lower():
                cell.fill = reserved_fill
            elif status_val and "not defined" in str(status_val).lower():
                cell.fill = not_defined_fill
            elif status_val and "defined" in str(status_val).lower():
                cell.fill = defined_fill

            ws.cell(row=row_idx, column=9, value=gt_row["Node Name"]).border = thin_border
            ws.cell(row=row_idx, column=10, value=gt_row["Comment"]).border = thin_border

    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max_len + 3, 40)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ═══════════════════════════════════════════
#  MAIN UI — Role-based, disk-backed, shared across users
# ═══════════════════════════════════════════

def _format_meta(meta):
    """Format metadata as a compact 'uploaded at ... by ...' caption."""
    if not meta:
        return "_Not uploaded yet_"
    who = meta.get("uploaded_by", "—")
    when = meta.get("uploaded_at", "—")
    return f"Uploaded **{when}** by **{who}**"


def render_pcgt_audit_tab(username="admin", role="viewer"):
    """Render the PC/GT Audit tab.
    role:
        'viewer'   → can reserve and view; no upload
        'uploader' → can upload files and reserve
    All data is read from / written to disk so every user sees the same shared state.
    """
    can_upload = (role == "uploader")

    # ── Always load fresh from disk so changes from other users are visible ──
    df_2pc, df_3pc, df_gt, audit_meta = load_audit_data()
    df_huawei, huawei_meta = load_huawei_data()
    df_ericsson, ericsson_meta = load_ericsson_data()
    reservations = load_reservations()

    sub1, sub2 = st.tabs(["📋 Audit & Reserve", "🔍 Cross-Source Reconciliation"])

    # ═══════════════════════════════════════════
    # SUB-TAB 1: Audit & Reserve
    # ═══════════════════════════════════════════
    with sub1:
        st.subheader("PC/GT Audit Data")

        # ── Upload (uploaders only) ──
        if can_upload:
            col_up, col_clr = st.columns([4, 1])
            with col_up:
                uploaded_csv = st.file_uploader(
                    "Upload audit file (.xlsx / .csv) — this will replace the current shared version",
                    type=["xlsx", "csv"],
                    key="pcgt_upload_csv"
                )
            with col_clr:
                st.write("")
                st.write("")
                if audit_meta and st.button("🗑️ Clear", key="pcgt_clear_audit", help="Remove current audit data"):
                    clear_audit_data()
                    st.success("Audit data cleared.")
                    st.rerun()

            if uploaded_csv is not None:
                try:
                    with st.spinner("Parsing audit file..."):
                        new_2pc, new_3pc, new_gt = load_audit_excel(uploaded_csv)
                        save_audit_data(new_2pc, new_3pc, new_gt, username, source_filename=uploaded_csv.name)
                        # Clear any pending uploaded-file state to avoid re-saving on rerun
                        st.success(f"Replaced shared audit data: {len(new_2pc)} 2-PC, {len(new_3pc)} 3-PC, {len(new_gt)} GT entries")
                        st.rerun()
                except Exception as e:
                    st.error(f"Error loading audit file: {e}")

        # ── Upload info banner (everyone sees this) ──
        st.info(
            f"📄 **Audit file:** {_format_meta(audit_meta)}"
            + (f" — `{audit_meta.get('source_filename')}`" if audit_meta.get("source_filename") else "")
        )

        if df_2pc.empty and df_3pc.empty and df_gt.empty:
            if can_upload:
                st.warning("No audit file uploaded yet. Upload one above to begin.")
            else:
                st.warning("No audit file available. Ask a manager to upload one.")
            return

        # ── Metrics ──
        m1, m2, m3 = st.columns(3)
        with m1:
            defined_2pc = len(df_2pc[df_2pc["Status/Node Name"].str.strip().str.lower() != "not defined"]) if not df_2pc.empty else 0
            st.metric("2-PC", f"{len(df_2pc)}", f"{defined_2pc} defined")
        with m2:
            defined_3pc = len(df_3pc[df_3pc["Status/Node Name"].str.strip().str.lower() != "not defined"]) if not df_3pc.empty else 0
            st.metric("3-PC", f"{len(df_3pc)}", f"{defined_3pc} defined")
        with m3:
            defined_gt = len(df_gt[df_gt["Status"].str.strip().str.lower() == "defined"]) if not df_gt.empty else 0
            st.metric("GT", f"{len(df_gt)}", f"{defined_gt} defined")

        st.markdown("---")

        # ── Search & view ──
        sc1, sc2 = st.columns([3, 1])
        with sc1:
            search_term = st.text_input("🔍 Search PC, GT, or node name", key="pcgt_search")
        with sc2:
            view_choice = st.selectbox("View", ["GT", "2-PC", "3-PC"], key="pcgt_view")

        def filter_df(df, term, columns):
            if not term or df.empty:
                return df
            mask = pd.Series([False] * len(df), index=df.index)
            for col in columns:
                if col in df.columns:
                    mask |= df[col].astype(str).str.contains(term, case=False, na=False)
            return df[mask]

        if view_choice == "2-PC":
            display_df = filter_df(df_2pc, search_term, ["PC", "Status/Node Name"])
        elif view_choice == "3-PC":
            display_df = filter_df(df_3pc, search_term, ["PC", "Status/Node Name"])
        else:
            display_df = filter_df(df_gt, search_term, ["GT Number", "Status", "Node Name", "Comment"])

        st.dataframe(display_df, use_container_width=True, height=350)

        st.markdown("---")

        # ── Reserve a node (any role can reserve) ──
        with st.expander("📌 Reserve a Node", expanded=False):
            st.caption(
                "**Rules:** 2-PC/3-PC must be 'Not defined'. "
                "GT must be 'Not defined' AND marked 'free to use'. "
                "Reservations are saved and visible to all users."
            )

            avail_2pc, avail_3pc, avail_gt = get_available_nodes(df_2pc, df_3pc, df_gt)

            r1, r2, r3 = st.columns(3)
            with r1:
                st.metric("Free 2-PC", len(avail_2pc))
            with r2:
                st.metric("Free 3-PC", len(avail_3pc))
            with r3:
                st.metric("Free GT", len(avail_gt))

            COMMENT_OPTIONS = [
                "Huawei Audit",
                "Recent Assignment",
                "Ericsson Audit",
                "Free to use",
            ]

            res_type = st.selectbox("Type", ["2-PC", "3-PC", "GT"], key="pcgt_res_type")
            options = {"2-PC": avail_2pc, "3-PC": avail_3pc, "GT": avail_gt}[res_type]

            if not options:
                st.warning(f"No {res_type} nodes available for reservation.")
            else:
                selected = st.selectbox(f"Available {res_type}", options, key="pcgt_res_node")
                res_name = st.text_input("Node name / purpose", key="pcgt_res_name")
                res_comment = st.selectbox("Comment", COMMENT_OPTIONS, key="pcgt_res_comment")

                if st.button("Reserve", type="primary", key="pcgt_reserve_btn"):
                    if not res_name:
                        st.error("Please enter a node name / purpose.")
                    else:
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        # ── Capture pre-reservation state so we can undo later ──
                        prev_state = {}
                        if res_type == "2-PC":
                            mask = df_2pc["PC"] == selected
                            if mask.any():
                                prev_state["Status/Node Name"] = df_2pc.loc[mask, "Status/Node Name"].iloc[0]
                        elif res_type == "3-PC":
                            mask = df_3pc["PC"] == selected
                            if mask.any():
                                prev_state["Status/Node Name"] = df_3pc.loc[mask, "Status/Node Name"].iloc[0]
                        else:
                            mask = df_gt["GT Number"] == selected
                            if mask.any():
                                row = df_gt.loc[mask].iloc[0]
                                prev_state["Status"] = row["Status"]
                                prev_state["Node Name"] = row["Node Name"]
                                prev_state["Comment"] = row["Comment"]

                        reservations.append({
                            "Type": res_type,
                            "Node": selected,
                            "Assigned Name": res_name,
                            "Comment": res_comment,
                            "Reserved At": ts,
                            "Reserved By": username,
                            "Previous State": prev_state,
                        })
                        save_reservations(reservations)

                        # ── Apply reservation to the shared audit data on disk ──
                        if res_type == "2-PC":
                            df_2pc.loc[df_2pc["PC"] == selected, "Status/Node Name"] = f"Reserved/{res_name}"
                        elif res_type == "3-PC":
                            df_3pc.loc[df_3pc["PC"] == selected, "Status/Node Name"] = f"Reserved/{res_name}"
                        else:
                            mask = df_gt["GT Number"] == selected
                            df_gt.loc[mask, "Status"] = "Reserved"
                            df_gt.loc[mask, "Node Name"] = res_name
                            df_gt.loc[mask, "Comment"] = res_comment

                        _save_df(df_2pc, AUDIT_2PC_PATH)
                        _save_df(df_3pc, AUDIT_3PC_PATH)
                        _save_df(df_gt, AUDIT_GT_PATH)

                        st.success(f"Reserved {res_type} **{selected}** as **{res_name}**")
                        st.rerun()

            # ── Reservation history with per-row Undo ──
            if reservations:
                st.markdown("**All reservations (shared):**")
                for i, res in enumerate(reservations):
                    cols = st.columns([1.2, 1.6, 2, 2, 2, 1.4, 1])
                    cols[0].write(f"**{res['Type']}**")
                    cols[1].write(res["Node"])
                    cols[2].write(res["Assigned Name"])
                    cols[3].write(res["Comment"])
                    cols[4].write(res["Reserved At"])
                    cols[5].write(f"_{res.get('Reserved By', '—')}_")
                    if cols[6].button("↩ Undo", key=f"pcgt_undo_{i}"):
                        # Revert the audit data using the stored previous state
                        prev = res.get("Previous State", {})
                        rtype = res["Type"]
                        node = res["Node"]
                        if rtype == "2-PC":
                            df_2pc.loc[df_2pc["PC"] == node, "Status/Node Name"] = prev.get("Status/Node Name", "Not defined")
                        elif rtype == "3-PC":
                            df_3pc.loc[df_3pc["PC"] == node, "Status/Node Name"] = prev.get("Status/Node Name", "Not defined")
                        else:
                            mask = df_gt["GT Number"] == node
                            df_gt.loc[mask, "Status"] = prev.get("Status", "Not defined")
                            df_gt.loc[mask, "Node Name"] = prev.get("Node Name", "")
                            df_gt.loc[mask, "Comment"] = prev.get("Comment", "")

                        _save_df(df_2pc, AUDIT_2PC_PATH)
                        _save_df(df_3pc, AUDIT_3PC_PATH)
                        _save_df(df_gt, AUDIT_GT_PATH)

                        # Remove the reservation from the list
                        reservations.pop(i)
                        save_reservations(reservations)
                        st.success(f"Undone: {rtype} {node} restored.")
                        st.rerun()

        # ── Export Updated Excel (available to everyone) ──
        st.markdown("---")
        if st.button("📤 Generate Updated Excel", type="primary", key="pcgt_export_btn"):
            with st.spinner("Generating Excel..."):
                excel_bytes = export_audit_excel(df_2pc, df_3pc, df_gt, reservations)
                st.session_state["pcgt_excel_bytes"] = excel_bytes

        if "pcgt_excel_bytes" in st.session_state:
            st.download_button(
                "📥 Download Updated Audit Excel",
                data=st.session_state["pcgt_excel_bytes"],
                file_name=f"E& PC & GT audit_updated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    # ═══════════════════════════════════════════
    # SUB-TAB 2: Cross-Source Reconciliation
    # ═══════════════════════════════════════════
    with sub2:
        st.subheader("Cross-Source Reconciliation")

        # ── Upload sections (uploaders only) ──
        if can_upload:
            st.caption("Upload Huawei and Ericsson files — these replace the current shared versions.")
            c_hw, c_er = st.columns(2)
            with c_hw:
                uploaded_huawei = st.file_uploader(
                    "Huawei ALLME files",
                    type=["txt"],
                    accept_multiple_files=True,
                    key="pcgt_huawei_upload"
                )
                if huawei_meta and st.button("🗑️ Clear Huawei data", key="pcgt_clear_hw"):
                    clear_huawei_data()
                    st.rerun()
            with c_er:
                uploaded_ericsson = st.file_uploader(
                    "Ericsson OSS channel logs",
                    type=["log", "txt"],
                    accept_multiple_files=True,
                    key="pcgt_ericsson_upload"
                )
                if ericsson_meta and st.button("🗑️ Clear Ericsson data", key="pcgt_clear_er"):
                    clear_ericsson_data()
                    st.rerun()

            # Process Huawei uploads
            if uploaded_huawei:
                frames = []
                names = []
                with st.spinner("Parsing Huawei files..."):
                    for f in uploaded_huawei:
                        content = f.read().decode("utf-8", errors="ignore")
                        df_hw = parse_huawei_file(content, source_name=f.name)
                        if not df_hw.empty:
                            frames.append(df_hw)
                            names.append(f.name)
                if frames:
                    combined = pd.concat(frames, ignore_index=True)
                    save_huawei_data(combined, username, names)
                    st.success(f"Replaced Huawei data: {len(combined)} entries from {len(names)} file(s)")
                    st.rerun()

            # Process Ericsson uploads
            if uploaded_ericsson:
                frames = []
                names = []
                with st.spinner("Parsing Ericsson files..."):
                    for f in uploaded_ericsson:
                        content = f.read().decode("utf-8", errors="ignore")
                        df_er, _, _, _ = parse_ericsson_log(content, source_name=f.name)
                        if not df_er.empty:
                            frames.append(df_er)
                            names.append(f.name)
                if frames:
                    combined = pd.concat(frames, ignore_index=True)
                    save_ericsson_data(combined, username, names)
                    st.success(f"Replaced Ericsson data: {len(combined)} entries from {len(names)} file(s)")
                    st.rerun()

        # ── Upload info (all roles see this) ──
        i1, i2, i3 = st.columns(3)
        with i1:
            st.markdown(f"**📄 Audit CSV**  \n{_format_meta(audit_meta)}")
        with i2:
            st.markdown(f"**🟣 Huawei**  \n{_format_meta(huawei_meta)}")
            if huawei_meta.get("files"):
                st.caption("Files: " + ", ".join(huawei_meta["files"]))
        with i3:
            st.markdown(f"**🔵 Ericsson**  \n{_format_meta(ericsson_meta)}")
            if ericsson_meta.get("files"):
                st.caption("Files: " + ", ".join(ericsson_meta["files"]))

        s1, s2, s3 = st.columns(3)
        with s1:
            st.markdown(f"{'✅' if not df_gt.empty else '❌'} Audit GT rows: **{len(df_gt)}**")
        with s2:
            st.markdown(f"{'✅' if not df_huawei.empty else '❌'} Huawei rows: **{len(df_huawei)}**")
        with s3:
            st.markdown(f"{'✅' if not df_ericsson.empty else '❌'} Ericsson rows: **{len(df_ericsson)}**")

        if df_gt.empty and df_huawei.empty and df_ericsson.empty:
            if can_upload:
                st.info("Upload at least one source to run reconciliation.")
            else:
                st.info("No reconciliation sources uploaded yet. Ask a manager to upload Huawei/Ericsson files.")
            return

        st.markdown("---")
        if st.button("Run Reconciliation", type="primary", key="pcgt_run_recon"):
            with st.spinner("Reconciling..."):
                st.session_state["pcgt_recon"] = reconcile_data(df_gt, df_huawei, df_ericsson)

        df_recon = st.session_state.get("pcgt_recon", pd.DataFrame())
        if not df_recon.empty:
            total = len(df_recon)
            ok = len(df_recon[df_recon["Audit Result"] == "OK"])
            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                st.metric("Total GTs", total)
            with mc2:
                st.metric("Matching", ok)
            with mc3:
                st.metric("Issues", total - ok)

            filter_opt = st.radio(
                "Filter",
                ["All", "Issues Only", "Missing in CSV", "Missing in Huawei", "Missing in Ericsson", "Node Mismatch"],
                horizontal=True,
                key="pcgt_recon_filter"
            )
            display_recon = df_recon
            if filter_opt == "Issues Only":
                display_recon = df_recon[df_recon["Audit Result"] != "OK"]
            elif filter_opt == "Missing in CSV":
                display_recon = df_recon[df_recon["Audit Result"].str.contains("Missing in Audit CSV", na=False)]
            elif filter_opt == "Missing in Huawei":
                display_recon = df_recon[df_recon["Audit Result"].str.contains("Missing in Huawei", na=False)]
            elif filter_opt == "Missing in Ericsson":
                display_recon = df_recon[df_recon["Audit Result"].str.contains("Missing in Ericsson", na=False)]
            elif filter_opt == "Node Mismatch":
                display_recon = df_recon[df_recon["Audit Result"].str.contains("Node name mismatch", na=False)]

            recon_search = st.text_input("🔍 Search results", key="pcgt_recon_search")
            if recon_search:
                mask = pd.Series([False] * len(display_recon), index=display_recon.index)
                for col in display_recon.columns:
                    mask |= display_recon[col].astype(str).str.contains(recon_search, case=False, na=False)
                display_recon = display_recon[mask]

            st.dataframe(display_recon, use_container_width=True, height=400)

            csv_data = display_recon.to_csv(index=False).encode("utf-8")
            st.download_button(
                "📥 Download Reconciliation Report",
                data=csv_data,
                file_name=f"pcgt_reconciliation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )
