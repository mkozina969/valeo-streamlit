import streamlit as st
import pdfplumber, re, pandas as pd, io
from collections import defaultdict

st.set_page_config(page_title="Valeo → XLSX (auto-detect)", layout="wide")
st.title("Valeo PDF → XLSX (auto-detect: Invoice & Packing)")

uploads = st.file_uploader("Upload one or more Valeo PDFs", type=["pdf"], accept_multiple_files=True)

# ---------- helpers ----------
def eu_to_float(s: str):
    s = str(s).strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None

def read_pdf(file):
    return pdfplumber.open(io.BytesIO(file.read()))

def read_all_text(pdf):
    return "\n".join([(p.extract_text() or "") for p in pdf.pages])

# ---------- Valeo INVOICE (same as before) ----------
def parse_valeo_invoice_text(full_text:str) -> pd.DataFrame:
    rows = []
    current_inv = None
    inv_re = re.compile(r"\b(695\d{6})\b")

    for raw_line in (l for l in full_text.splitlines() if l.strip()):
        m_inv = inv_re.search(raw_line)
        if m_inv:
            current_inv = m_inv.group(1)

        low = raw_line.lower()
        if low.startswith((
            "your order:", "delivery note:", "goods value",
            "vat rate", "transport cost", "currency",
            "total gross value", "net price without vat"
        )):
            continue

        tok = raw_line.split()
        if len(tok) < 7:
            continue

        if not (re.fullmatch(r"[\d\.,]+", tok[-1]) and re.fullmatch(r"[\d\.,]+", tok[-2])):
            continue

        j = None
        for k in range(len(tok)-3, 1, -1):
            if (re.fullmatch(r"[A-Z]{2}", tok[k]) and
                k+1 < len(tok) and re.fullmatch(r"\d{6,8}", tok[k+1]) and
                re.fullmatch(r"\d+", tok[k-1])):
                j = k
                break
        if j is None:
            continue

        supplier_token = None
        for t in tok[:j-1]:
            if re.fullmatch(r"\d+", t):
                supplier_token = t
                break
        if not supplier_token:
            continue

        qty = int(tok[j-1])
        net_price = eu_to_float(tok[-2])
        tot_net   = eu_to_float(tok[-1])
        rows.append([supplier_token, qty, net_price, tot_net, current_inv])

    return pd.DataFrame(rows, columns=["Supplier_ID","Qty","Net Price","Tot. Net Value","InvoiceNo"])

# ---------- Valeo PACKING (header-locked, column windows, order-preserving) ----------
def parse_valeo_packing_pdf(pdf) -> pd.DataFrame:
    """
    Only process pages with 'PACKING LIST'.
    Detect header line to learn exact x0..x1 windows for 'VALEO Material N°' and 'Quantity'.
    For each visual line:
      - supplier_id: 4–8 digits inside VALEO window
      - qty: integer inside Quantity window AND qty <= 1000
    Walk top->bottom, forward-fill Parcel N° between '<digits> PALLET' headers.
    Keep duplicates. Preserve order.
    """
    rows = []
    current_parcel = None

    # patterns
    PALLET_WORD = re.compile(r"\bPALLET\b", re.IGNORECASE)
    PARCEL_ID   = re.compile(r"^\d{6,}$")
    SUPPLIER_ID = re.compile(r"^\d{4,8}$")
    INT_ONLY    = re.compile(r"^\d+$")
    SKIP_LINE   = re.compile(r"(dimensions|total\s+net\s+weight|total\s+gross\s+weight|type\s+of\s+parcel)",
                             re.IGNORECASE)

    for page in pdf.pages:
        page_text = page.extract_text() or ""
        if "PACKING LIST" not in page_text.upper():
            continue

        words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
        if not words:
            continue

        # 1) Group to visual lines
        line_map = defaultdict(list)
        for w in words:
            line_map[round(w["top"], 1)].append(w)
        lines = [(y, sorted(ws, key=lambda x: x["x0"])) for y, ws in sorted(line_map.items(), key=lambda kv: kv[0])]

        # 2) Find header row (contains both 'VALEO' and 'Quantity' on ~same y)
        header = None
        for y, ws in lines:
            texts_lower = [w["text"].strip().lower() for w in ws]
            if any("quantity" == t for t in texts_lower) and any("valeo" in t for t in texts_lower):
                header = ws
                break
        if not header:
            # if not found, skip page (better to skip than to introduce noise)
            continue

        # column x-windows from header words themselves (tight)
        qty_hdrs   = [w for w in header if w["text"].strip().lower() == "quantity"]
        valeo_hdrs = [w for w in header if "valeo" in w["text"].strip().lower()]

        qty_win  = (min(w["x0"] for w in qty_hdrs) - 2,  max(w["x1"] for w in qty_hdrs) + 2) if qty_hdrs else (page.width*0.75, page.width*0.95)
        valeo_win= (min(w["x0"] for w in valeo_hdrs) - 2, max(w["x1"] for w in valeo_hdrs) + 2) if valeo_hdrs else (page.width*0.45, page.width*0.7)

        # hard cap on qty values to avoid weights/codes being picked
        QTY_MAX = 1000

        # 3) Iterate subsequent lines (after header) in order
        header_y = header[0]["top"]
        for y, ws in lines:
            if y <= header_y:
                continue  # stay below header
            if SKIP_LINE.search(" ".join(w["text"] for w in ws)):
                continue

            texts = [w["text"] for w in ws]

            # detect new PALLET
            if any(PALLET_WORD.search(t) for t in texts):
                parcel_tokens = [w["text"] for w in ws if PARCEL_ID.match(w["text"])]
                if parcel_tokens:
                    current_parcel = parcel_tokens[0]
                continue

            if not current_parcel:
                continue

            # supplier inside VALEO window
            supplier_tokens = [w["text"] for w in ws
                               if valeo_win[0] <= w["x0"] <= valeo_win[1] and SUPPLIER_ID.match(w["text"])]
            if not supplier_tokens:
                continue
            supplier_id = supplier_tokens[0]

            # quantity inside Quantity window (pure int, <= QTY_MAX)
            qty_tokens = [int(w["text"]) for w in ws
                          if qty_win[0] <= w["x0"] <= qty_win[1] and INT_ONLY.match(w["text"])]
            if not qty_tokens:
                continue
            qty = qty_tokens[-1]
            if qty > QTY_MAX:
                continue  # guard against weights/codes

            rows.append([current_parcel, supplier_id, qty])

    return pd.DataFrame(rows, columns=["Parcel N°","VALEO Material N°","Quantity"]).reset_index(drop=True)

# ---------- autodetect ----------
def autodetect(pdf):
    text = read_all_text(pdf)
    inv_df  = parse_valeo_invoice_text(text)
    pack_df = parse_valeo_packing_pdf(pdf)
    return inv_df, pack_df

# ---------- UI ----------
if uploads:
    for up in uploads:
        st.markdown("---")
        st.subheader(f"File: {up.name}")

        pdf = read_pdf(up)
        inv_df, pack_df = autodetect(pdf)
        pdf.close()

        produced_any = False

        if len(inv_df) > 0:
            produced_any = True
            st.write(f"**Invoice lines detected:** {len(inv_df)} rows")
            st.dataframe(inv_df, use_container_width=True, height=320)
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as xw:
                inv_df.to_excel(xw, index=False, sheet_name="InvoiceLines")
            st.download_button(
                "Download Invoice XLSX",
                data=buf.getvalue(),
                file_name=f"{up.name.rsplit('.',1)[0]}_invoice_lines.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        if len(pack_df) > 0:
            produced_any = True
            st.write(f"**Packing lines detected:** {len(pack_df)} rows (Σ Quantity = {pack_df['Quantity'].sum()})")
            st.dataframe(pack_df, use_container_width=True, height=320)
            buf2 = io.BytesIO()
            with pd.ExcelWriter(buf2, engine="openpyxl") as xw:
                pack_df.to_excel(xw, index=False, sheet_name="PackingLines")
            st.download_button(
                "Download Packing XLSX",
                data=buf2.getvalue(),
                file_name=f"{up.name.rsplit('.',1)[0]}_packing_lines.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        if not produced_any:
            st.warning("No invoice or packing lines detected — is this a Valeo PDF?")
else:
    st.info("Upload one or more Valeo PDFs.")
