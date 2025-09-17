import streamlit as st
import pdfplumber, re, pandas as pd, io

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

def read_pdf_pages(file):
    """Return list[str]: text of each page, preserving page order."""
    with pdfplumber.open(io.BytesIO(file.read())) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]

# ---------- Valeo INVOICE ----------
def parse_valeo_invoice_text(full_text:str) -> pd.DataFrame:
    rows = []
    current_inv = None
    inv_re = re.compile(r"\b(695\d{6})\b")  # Valeo invoice number

    for raw_line in (l for l in full_text.splitlines() if l.strip()):
        # capture invoice number if present
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

        # last two tokens must be numeric (Net Price, Tot. Net)
        if not (re.fullmatch(r"[\d\.,]+", tok[-1]) and re.fullmatch(r"[\d\.,]+", tok[-2])):
            continue

        # detect "... Qty(int) Orig(AA) Customs(6-8d) ..."
        j = None
        for k in range(len(tok)-3, 1, -1):
            if (re.fullmatch(r"[A-Z]{2}", tok[k]) and
                k+1 < len(tok) and re.fullmatch(r"\d{6,8}", tok[k+1]) and
                re.fullmatch(r"\d+", tok[k-1])):
                j = k
                break
        if j is None:
            continue

        # supplier_id = first numeric token BEFORE Qty
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

# ---------- Valeo PACKING (sequential, order-preserving) ----------
def parse_valeo_packing_pages(page_texts:list[str]) -> pd.DataFrame:
    """
    - Walk pages top→bottom preserving order.
    - Maintain `current_parcel` when encountering '<7+ digits> PALLET' header.
    - Item match is robust; VALEO code is 4–8 digits (filters '011', keeps real 4-digit codes).
    - Keep duplicates (same item on multiple pallets).
    """
    parcel_pat = re.compile(r"^\s*(?P<parcel>\d{6,})\s+PALLET\b")
    # primary item pattern: line starts with code + qty
    item_pat_a = re.compile(r"^\s*(?P<valeo>\d{4,8})\s+(?P<qty>\d+)\b")
    # fallback: code appears earlier, qty later before EOL (handles odd spacing)
    item_pat_b = re.compile(r"\b(?P<valeo>\d{4,8})\b.*?\s(?P<qty>\d{1,4})\b(?:\s+\d+)?(?:\s+[A-Z0-9\-\/]+)?\s*$")

    rows = []
    current_parcel = None

    for page in page_texts:
        lines = [l for l in page.splitlines() if l.strip()]
        for ln in lines:
            # PALLET header updates current parcel
            mp = parcel_pat.match(ln)
            if mp:
                current_parcel = mp.group("parcel")
                continue

            # try item patterns (only if we already know which pallet we're on)
            if current_parcel is None:
                # we haven't seen a PALLET yet -> these lines belong to previous page's pallet continuation
                # so we skip until we see the first PALLET (prevents misassignment)
                continue

            ma = item_pat_a.search(ln)
            mb = item_pat_b.search(ln) if not ma else None
            m  = ma or mb
            if not m:
                continue

            valeo = m.group("valeo")
            qty   = int(m.group("qty"))
            rows.append([current_parcel, valeo, qty])

    return pd.DataFrame(rows, columns=["Parcel N°","VALEO Material N°","Quantity"]).reset_index(drop=True)

# ---------- autodetect ----------
def autodetect(page_texts:list[str]):
    full_text = "\n".join(page_texts)
    inv_df  = parse_valeo_invoice_text(full_text)
    pack_df = parse_valeo_packing_pages(page_texts)
    return inv_df, pack_df

# ---------- UI ----------
if uploads:
    for up in uploads:
        st.markdown("---")
        st.subheader(f"File: {up.name}")
        pages = read_pdf_pages(up)

        inv_df, pack_df = autodetect(pages)
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
            st.write(f"**Packing lines detected (order preserved):** {len(pack_df)} rows")
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
