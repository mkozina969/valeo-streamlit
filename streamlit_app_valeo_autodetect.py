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

# ---------- Valeo INVOICE ----------
def parse_valeo_invoice_text(full_text:str) -> pd.DataFrame:
    rows = []
    current_inv = None
    inv_re = re.compile(r"\b(695\d{6})\b")  # Valeo invoice number

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

# ---------- Valeo PACKING (column-aligned + order-preserving) ----------
def parse_valeo_packing_pdf(pdf) -> pd.DataFrame:
    """
    Only process pages that contain 'PACKING LIST'.
    Detect the header columns (x position of 'VALEO Material N°' and 'Quantity'),
    then, line by line, take values only from those column windows.
    Keep duplicates and preserve order. Forward-fill Parcel N° between '... PALLET' headers.
    """
    rows = []
    current_parcel = None
    PALLET_RE = re.compile(r"\bPALLET\b", re.IGNORECASE)
    DIGITS_6PLUS = re.compile(r"^\d{6,}$")
    SUPPLIER_RE = re.compile(r"^\d{4,8}$")   # supplier_id 4–8 digits
    INT_RE = re.compile(r"^\d+$")

    for page in pdf.pages:
        page_text = page.extract_text() or ""
        if "PACKING LIST" not in page_text.upper():
            continue

        words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
        if not words:
            continue

        # 1) Find header line and column x-positions
        header_candidates = [w for w in words if w["text"].strip().lower() in {"quantity", "qty", "valeo", "parcel", "parcel n°", "parcel no", "valeo material", "valeo material n°"}]
        if not header_candidates:
            # fallback: look for the first occurrence of 'Quantity'
            header_candidates = [w for w in words if w["text"].strip().lower() == "quantity"]

        # choose y of header as the smallest 'top' among candidates containing 'quantity'
        qty_headers = [w for w in words if w["text"].strip().lower() == "quantity"]
        if qty_headers:
            header_y = min(qh["top"] for qh in qty_headers)
            qty_x = sorted(qty_headers, key=lambda w: w["x0"])[0]["x0"]
        else:
            # if somehow 'Quantity' word not recognized, approximate with the rightmost numeric column x
            header_y = min(w["top"] for w in words)
            qty_x = max(w["x0"] for w in words) - 100  # rough fallback

        # find x for 'VALEO Material'
        valeo_headers = [w for w in words if "valeo" in w["text"].strip().lower()]
        if valeo_headers:
            valeo_x = sorted(valeo_headers, key=lambda w: w["x0"])[0]["x0"]
        else:
            # heuristic fallback: use mid-left of the page
            valeo_x = page.width * 0.55 if hasattr(page, "width") else qty_x - 200

        # define column windows (tunable widths)
        QTY_WIN = (qty_x - 15, qty_x + 120)
        VAL_WIN = (valeo_x - 30, valeo_x + 180)

        # 2) Group words into visual lines (same y within tolerance)
        TOL = 3.0
        line_map = defaultdict(list)
        for w in words:
            line_map[round(w["top"], 1)].append(w)

        # 3) Iterate lines in order
        for _, ws in sorted(line_map.items(), key=lambda kv: kv[0]):
            ws_sorted = sorted(ws, key=lambda x: x["x0"])
            texts = [w["text"] for w in ws_sorted]

            # 3a) Detect new PALLET header: any line that has a big number + the word PALLET
            if any(PALLET_RE.search(t) for t in texts):
                # take the first 6+ digit token on that line as the parcel id
                parcel_tokens = [t for t in texts if DIGITS_6PLUS.match(t)]
                if parcel_tokens:
                    current_parcel = parcel_tokens[0]
                    continue

            if not current_parcel:
                continue

            # 3b) Find supplier_id strictly in the VALEO column window
            supplier_tokens = [w["text"] for w in ws_sorted
                               if VAL_WIN[0] <= w["x0"] <= VAL_WIN[1] and SUPPLIER_RE.match(w["text"])]
            if not supplier_tokens:
                continue
            supplier_id = supplier_tokens[0]

            # 3c) Find quantity strictly in the Quantity column window
            qty_tokens = [w["text"] for w in ws_sorted
                          if QTY_WIN[0] <= w["x0"] <= QTY_WIN[1] and INT_RE.match(w["text"])]
            if not qty_tokens:
                continue
            quantity = int(qty_tokens[-1])  # rightmost numeric in the qty column

            rows.append([current_parcel, supplier_id, quantity])

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
