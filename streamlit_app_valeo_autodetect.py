def parse_valeo_invoice_text(text:str):
    import re, pandas as pd
    def eu_to_float(s: str):
        s = str(s).strip().replace(".", "").replace(",", ".")
        try: return float(s)
        except: return None

    rows = []
    current_inv = None
    inv_re = re.compile(r"\b(695\d{6})\b")

    for raw_line in (l for l in text.splitlines() if l.strip()):
        # capture invoice number when seen
        m_inv = inv_re.search(raw_line)
        if m_inv:
            current_inv = m_inv.group(1)

        low = raw_line.lower()
        if low.startswith(("your order:", "delivery note:", "goods value",
                           "vat rate", "transport cost", "currency",
                           "total gross value", "net price without vat")):
            continue

        tok = raw_line.split()
        if len(tok) < 7:
            continue

        # last two are Net Price, Tot. Net (numeric)
        if not (re.fullmatch(r"[\d\.,]+", tok[-1]) and re.fullmatch(r"[\d\.,]+", tok[-2])):
            continue

        # find "... Qty(int) Orig(AA) Customs(6-8d) ..." scanning leftwards
        j = None
        for k in range(len(tok)-3, 1, -1):
            if (re.fullmatch(r"[A-Z]{2}", tok[k])
                and k+1 < len(tok) and re.fullmatch(r"\d{6,8}", tok[k+1])
                and re.fullmatch(r"\d+", tok[k-1])):
                j = k
                break
        if j is None:
            continue

        # supplier_id = first numeric token BEFORE Qty (robust)
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

        # DO NOT deduplicate — keep all occurrences
        rows.append([supplier_token, qty, net_price, tot_net, current_inv])

    df = pd.DataFrame(rows, columns=["Supplier_ID","Qty","Net Price","Tot. Net Value","InvoiceNo"])
    # <-- IMPORTANT: no df.drop_duplicates() here
    return df
