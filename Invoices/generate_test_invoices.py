"""
Invoice PDF Generator — Zamp ASA Case Study (PS-1)
Generates realistic-looking invoice PDFs from structured data.
Used to create our 5 test invoices (happy path + 4 edge cases).
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_LEFT

import os

OUTPUT_DIR = "invoices"
os.makedirs(OUTPUT_DIR, exist_ok=True)

styles = getSampleStyleSheet()

title_style = ParagraphStyle(
    "TitleStyle", parent=styles["Heading1"], fontSize=22,
    textColor=colors.HexColor("#1a1a1a"), spaceAfter=2
)
label_style = ParagraphStyle(
    "LabelStyle", parent=styles["Normal"], fontSize=9,
    textColor=colors.HexColor("#666666")
)
value_style = ParagraphStyle(
    "ValueStyle", parent=styles["Normal"], fontSize=10,
    textColor=colors.HexColor("#1a1a1a")
)
right_value_style = ParagraphStyle(
    "RightValueStyle", parent=value_style, alignment=TA_RIGHT
)


def build_invoice_pdf(data: dict, output_path: str):
    """
    data dict expected keys:
      vendor_name, vendor_address, invoice_number (str or None),
      invoice_date, po_reference, bank_account, ifsc,
      line_items: list of {description, qty, unit_price, amount}
      subtotal, tax, total, notes (optional)
    """
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=20 * mm, bottomMargin=20 * mm,
        leftMargin=20 * mm, rightMargin=20 * mm
    )
    elements = []

    # Header
    elements.append(Paragraph(data["vendor_name"], title_style))
    elements.append(Paragraph(data.get("vendor_address", ""), label_style))
    elements.append(Spacer(1, 14))

    # Invoice meta block
    invoice_number_display = data["invoice_number"] if data["invoice_number"] else "—"
    meta_table_data = [
        [Paragraph("INVOICE #", label_style), Paragraph(invoice_number_display, value_style)],
        [Paragraph("DATE", label_style), Paragraph(data["invoice_date"], value_style)],
        [Paragraph("PO REFERENCE", label_style), Paragraph(data.get("po_reference", "—"), value_style)],
    ]
    meta_table = Table(meta_table_data, colWidths=[100, 300])
    meta_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 18))

    # Line items table
    table_data = [["Description", "Qty", "Unit Price", "Amount"]]
    for item in data["line_items"]:
        table_data.append([
            item["description"],
            str(item.get("qty", "")),
            f"Rs. {item.get('unit_price', 0):,.2f}" if item.get("unit_price") is not None else "—",
            f"Rs. {item.get('amount', 0):,.2f}",
        ])

    items_table = Table(table_data, colWidths=[230, 50, 90, 90])
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a1a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 12))

    # Totals block
    totals_data = []
    if data.get("subtotal") is not None:
        totals_data.append(["Subtotal", f"Rs. {data['subtotal']:,.2f}"])
    if data.get("tax") is not None and data["tax"] > 0:
        totals_data.append(["Tax", f"Rs. {data['tax']:,.2f}"])
    totals_data.append(["TOTAL", f"Rs. {data['total']:,.2f}"])

    totals_table = Table(totals_data, colWidths=[370, 90])
    totals_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, -1), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.HexColor("#1a1a1a")),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 24))

    # Bank details footer
    bank_block = f"""
    <b>Payment Details</b><br/>
    Bank Account: {data.get('bank_account', '—')}<br/>
    IFSC: {data.get('ifsc', '—')}
    """
    elements.append(Paragraph(bank_block, label_style))

    if data.get("notes"):
        elements.append(Spacer(1, 10))
        elements.append(Paragraph(f"<i>{data['notes']}</i>", label_style))

    doc.build(elements)
    print(f"Generated: {output_path}")


# ──────────────────────────────────────────────
# THE 5 TEST INVOICES
# ──────────────────────────────────────────────

invoice_1_happy_path = {
    "vendor_name": "Apex Office Supplies",
    "vendor_address": "14 Industrial Estate Road, Okhla Phase II, New Delhi - 110020",
    "invoice_number": "INV-2305",
    "invoice_date": "10 June 2026",
    "po_reference": "PO-1001",
    "bank_account": "XXXX-XXXX-4521",
    "ifsc": "HDFC0001234",
    "line_items": [
        {"description": "A4 Paper Reams (500 sheets)", "qty": 50, "unit_price": 250, "amount": 12500},
        {"description": "Toner Cartridges - HP Compatible", "qty": 20, "unit_price": 300, "amount": 6000},
    ],
    "subtotal": 18500,
    "tax": 0,
    "total": 18500,
}

invoice_2_near_miss = {
    "vendor_name": "Brightway Electricals",
    "vendor_address": "Plot 7, Sector 18, Udyog Vihar, Gurugram - 122015",
    "invoice_number": "INV-7702",
    "invoice_date": "11 June 2026",
    "po_reference": "PO-1003",
    "bank_account": "XXXX-XXXX-3344",
    "ifsc": "SBIN0009988",
    "line_items": [
        {"description": "LED Lighting Fixtures - 18W Panel", "qty": 60, "unit_price": 1250, "amount": 75000},
        {"description": "Freight & Handling Charges", "qty": 1, "unit_price": 1700, "amount": 1700},
    ],
    "subtotal": 76700,
    "tax": 0,
    "total": 76700,
    "notes": "PO Approved Amount: Rs. 76,200 — Note: this invoice includes a freight line not itemised on PO-1003.",
}

invoice_3_split_po = {
    "vendor_name": "Meridian IT Solutions",
    "vendor_address": "4th Floor, Cyber Towers, HITEC City, Hyderabad - 500081",
    "invoice_number": "INV-9001",
    "invoice_date": "12 June 2026",
    "po_reference": "PO-1005",
    "bank_account": "XXXX-XXXX-1122",
    "ifsc": "AXIS0004433",
    "line_items": [
        {"description": "Annual Software License — Installment 1 of 4 (Q1, 3 months)", "qty": 1, "unit_price": 31250, "amount": 31250},
    ],
    "subtotal": 31250,
    "tax": 0,
    "total": 31250,
    "notes": "Partial billing against annual license PO-1005 (Total approved: Rs. 1,25,000). First installment of 4.",
}

invoice_4_scanned_lowqual = {
    "vendor_name": "Coastal Packaging Co.",
    "vendor_address": "Warehouse 12, Port Road, Visakhapatnam - 530001",
    "invoice_number": None,  # deliberately missing — this is the point of this edge case
    "invoice_date": "13 June 2026",
    "po_reference": "PO-1007",
    "bank_account": "XXXX-XXXX-6655",
    "ifsc": "KOTAK0007722",
    "line_items": [
        {"description": "Custom Packaging Boxes — Corrugated, Size B", "qty": 10000, "unit_price": 5.40, "amount": 54000},
    ],
    "subtotal": 54000,
    "tax": 0,
    "total": 54000,
}

invoice_5_bank_change = {
    "vendor_name": "Apex Office Supplies",
    "vendor_address": "14 Industrial Estate Road, Okhla Phase II, New Delhi - 110020",
    "invoice_number": "INV-2401",
    "invoice_date": "14 June 2026",
    "po_reference": "PO-1004",
    "bank_account": "XXXX-XXXX-9988",  # CHANGED from on-file XXXX-XXXX-4521
    "ifsc": "ICIC0002211",  # also changed
    "line_items": [
        {"description": "Ergonomic Office Chairs", "qty": 15, "unit_price": 600, "amount": 9000},
    ],
    "subtotal": 9000,
    "tax": 0,
    "total": 9000,
}


if __name__ == "__main__":
    build_invoice_pdf(invoice_1_happy_path, f"{OUTPUT_DIR}/invoice_1_happy_path.pdf")
    build_invoice_pdf(invoice_2_near_miss, f"{OUTPUT_DIR}/invoice_2_near_miss.pdf")
    build_invoice_pdf(invoice_3_split_po, f"{OUTPUT_DIR}/invoice_3_split_po.pdf")
    build_invoice_pdf(invoice_4_scanned_lowqual, f"{OUTPUT_DIR}/invoice_4_scanned_clean_version.pdf")
    build_invoice_pdf(invoice_5_bank_change, f"{OUTPUT_DIR}/invoice_5_bank_change.pdf")
    print("\nAll clean invoices generated. Invoice 4 still needs degradation step (next script).")
