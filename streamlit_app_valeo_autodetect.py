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

def read_pdf_text(file):
    with pdfplumber.open(io.BytesIO(file.read())) as pdf:
        return "\n".join([(p.extract_text() or "") for p in pdf.pages])

# ---------- Valeo INVOICE ----------
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

# ---------- Valeo PACKING (using extract_words) ----------
def parse_valeo_packing_pdf(pdf) -> pd.DataFrame:
    rows = []
    current_parcel = None
    parcel_pat = re.compile(r"^\s*(?P<parcel>\d{6,})$")
    item_code_pat = re.compile(r"^\d{4,8}$")

    for page in pdf.pages:
        page_text = page.extract_text() or ""
        if "PACKING LIST" not in page_text.upper():
            continue

        words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
        # group words by line (same top within tolerance)
        lines = defaultdict(list)
        for w in words:
            lines[round(w["top"], 1)].append(w)

        for _, ws in sorted(lines.items(), key=lambda kv: kv[0]):
            texts = [w["text"] for w in sorted(ws, key=lambda x: x["x0"])]

            # detect parcel header
            if len(texts) >= 2 and texts[1].upper().startswith("PALLET") and texts[0].isdigit():
                current_parcel = texts[0]
                continue

            if not current_parcel:
                continue

            # find supplier_id and quantity in this line
            supplier_ids = [t for t in texts if item_code_pat.match(t)]
            qtys = [t for t in texts if t.isdigit()]
            if supplier_ids and qtys:
                supplier = supplier_ids[0]
                qty = int(qtys[-1])  # take rightmost number as quantity
                rows.append([current_parcel, supplier, qty])

    return pd.DataFrame(rows, columns=["Parcel N°","VALEO Material N°","Quantity"]).reset_index(drop=True)

# ---------- autodetect ----------
def autodetect(file):
    pdf = read_pdf(file)
    text = "\n".join([(p.extract_text() or "") for p in pdf.pages])

    inv_df  = parse_valeo_invoice_text(text)
    pack_df = parse_valeo_packing_pdf(pdf)

    pdf.close()
    return inv_df, pack_df

# ---------- UI ----------
if uploads:
    for up in uploads:
        st.markdown("---")
        st.subheader(f"File: {up.name}")
        inv_df, pack_df = autodetect(up)

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
            st.write(f"**Packing lines detected:** {len(pack_df)} rows (Σ Quantity={pack_df['Quantity'].sum()})")
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
