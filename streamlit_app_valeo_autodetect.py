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

def read_pdf_text(file):
    # read ALL pages (concatenated)
    with pdfplumber.open(io.BytesIO(file.read())) as pdf:
        page_texts = [(p.extract_text() or "") for p in pdf.pages]
    return "\n".join(page_texts)

# ---------- Valeo INVOICE (locked logic) ----------
def parse_valeo_invoice_text(text:str) -> pd.DataFrame:
    rows = []
    current_inv = None
    inv_re = re.compile(r"\b(695\d{6})\b")  # Valeo invoice number

    for raw_line in (l for l in text.splitlines() if l.strip()):
        # capture invoice number when seen
        m_inv = inv_re.search(raw_line)
        if m_inv:
            current_inv = m_inv.group(1)

        low = raw_line.lower()
        # skip non-line blocks
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

        # find "... Qty(int) Orig(AA) Customs(6-8d) ..." scanning leftwards
        j = None
        for k in range(len(tok)-3, 1, -1):
            if (re.fullmatch(r"[A-Z]{2}", tok[k]) and
                k+1 < len(tok) and re.fullmatch(r"\d{6,8}", tok[k+1]) and
                re.fullmatch(r"\d+", tok[k-1])):
                j = k
                break
        if j is None:
            continue

        # supplier_id = first numeric token BEFORE Qty (robust; handles stray tokens)
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

        # IMPORTANT: do NOT drop_duplicates — keep repeat lines (same supplier_id, different prices)
        rows.append([supplier_token, qty, net_price, tot_net, current_inv])

    df = pd.DataFrame(rows, columns=["Supplier_ID","Qty","Net Price","Tot. Net Value","InvoiceNo"])
    return df

# ---------- Valeo PACKING (locked logic) ----------
def parse_valeo_packing_text(text:str) -> pd.DataFrame:
    # proximity to PALLET lines; Quantity numeric; works if items appear above/below header on a page
    item_pat   = re.compile(r"(?P<valeo>\d{3,})\s+(?P<qty>\d+)(?:\s+\d+)?(?:\s+[A-Z0-9\-\/]+)?\s*$")
    parcel_pat = re.compile(r"^\s*(?P<parcel>\d{6,})\s+PALLET\b")

    lines = [l for l in text.splitlines() if l.strip()]
    parcels = [(i, parcel_pat.match(ln).group("parcel")) for i, ln in enumerate(lines) if parcel_pat.match(ln)]
    if not parcels:
        return pd.DataFrame(columns=["Parcel N°","VALEO Material N°","Quantity"])

    rows = []
    for idx, ln in enumerate(lines):
        m = item_pat.search(ln)
        if not m:
            continue
        nearest_parcel = min(parcels, key=lambda t: abs(t[0] - idx))[1]
        rows.append([nearest_parcel, m.group("valeo"), int(m.group("qty"))])

    df = pd.DataFrame(rows, columns=["Parcel N°","VALEO Material N°","Quantity"]).reset_index(drop=True)
    return df

def autodetect(text:str):
    inv_df = parse_valeo_invoice_text(text)
    pack_df = parse_valeo_packing_text(text)
    return inv_df, pack_df

# ---------- UI ----------
if uploads:
    for up in uploads:
        st.markdown("---")
        st.subheader(f"File: {up.name}")
        text = read_pdf_text(up)

        inv_df, pack_df = autodetect(text)
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
            st.write(f"**Packing lines detected:** {len(pack_df)} rows")
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
