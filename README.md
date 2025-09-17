# Valeo invoice + packing rules (golden-locked)

This package contains **locked YAML rules** and a **golden test** set for Valeo:
- `rules/VALEO_INVOICE.yml`
- `rules/VALEO_PACKING.yml`

It also includes a small CLI to parse the provided sample PDF and export XLSX, plus a harness to compare with the expected outputs.

## Install
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
```

## Run (parse to out/)
```bash
python -m valeo.cli --supplier VALEO_INVOICE --input tests/goldens/input/Valeo.pdf --out out/valeo_invoice.xlsx
python -m valeo.cli --supplier VALEO_PACKING --input tests/goldens/input/Valeo.pdf --out out/valeo_packing.xlsx
```

## Golden test
```bash
python -m valeo.cli --golden tests/goldens
```
This will parse the PDF twice (invoice + packing) and compare columns + row counts to `tests/goldens/expected/*.xlsx`.

## Notes
- **Invoice lines** export columns: `Supplier_ID, Qty, Net Price, Tot. Net Value, InvoiceNo`
- **Packing lines** export columns: `Parcel N°, VALEO Material N°, Quantity` (Quantity is numeric). We forward-fill `Parcel N°`.