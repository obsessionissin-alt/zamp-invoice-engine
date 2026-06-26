"""
PDF extraction — 3-tier strategy, most-free first:

Tier 1 (FREE — Groq):
  PyMuPDF extracts raw text from the PDF.
  If text is found (digital/searchable PDF) → send text to Groq Llama 3.3 70B for JSON structuring.
  Groq's free tier is generous: 30 RPM, 14,400 requests/day.

Tier 2 (FREE — Gemini vision):
  PDF has no extractable text (scanned/image PDF like invoice_4).
  Convert to PNG via PyMuPDF → send image to Gemini 2.5 Flash for vision extraction.

Tier 3 (Paid fallback — only if both above fail):
  GPT-4o with the same PNG image.

This means invoices 1, 2, 3, 5 (digital PDFs) run on Groq — no vision quota at all.
Only invoice 4 (blurry scan) uses Gemini vision.
"""

import base64
import json
import os
import re

import fitz  # PyMuPDF
import openai
from dotenv import find_dotenv, load_dotenv
from google import genai
from google.genai import types

load_dotenv(find_dotenv(usecwd=True))

_GROQ_KEY = os.getenv("GROQ_API_KEY")
_GOOGLE_KEY = os.getenv("GOOGLE_API_KEY")
_OPENAI_KEY = os.getenv("OPENAI_API_KEY")

# ── Prompts ────────────────────────────────────────────────────────────────

# ── Best-practice structured extraction prompt ─────────────────────────────
#
# Design principles applied here:
#   1. Role + task framing  — model knows exactly WHO it is and WHAT it must do
#   2. Exhaustive synonyms  — every field lists all real-world label variants
#   3. Output contract      — exact JSON schema with type annotations
#   4. Explicit confidence  — binary HIGH/LOW with precise, testable criteria
#   5. Normalisation rules  — strip currency symbols, handle partial masking
#   6. Few-shot example     — one complete input→output pair anchors the format
#   7. Failure instruction  — tells the model to return null, never hallucinate

EXTRACTION_SYSTEM = """You are a precise accounts-payable data extraction engine.
Your ONLY job is to read an invoice document and return a single valid JSON object.
You never invent, guess, or hallucinate field values.
If a field is absent or unreadable, you return null for that field — never a placeholder."""

EXTRACTION_INSTRUCTIONS = """
## Your task
Extract the following fields from the invoice and return them as a JSON object.
Return RAW JSON ONLY — no markdown fences, no explanation, no extra keys.

## Output schema
{
  "invoice_number" : string | null,
  "vendor_name"    : string | null,
  "po_reference"   : string | null,
  "invoice_date"   : string | null,
  "bank_account"   : string | null,
  "line_items"     : [ { "description": string, "amount": number } ],
  "total"          : number | null,
  "confidence"     : "HIGH" | "LOW"
}

## Field extraction rules

### invoice_number
The unique identifier for THIS document.
Accept any of these labels: Invoice #, Invoice No., Invoice ID, Order ID, Order #,
Bill No, Bill Number, Receipt #, Reference #, Document No, Voucher No, Ref No.
→ Return the VALUE next to that label as a string. Strip leading/trailing whitespace.

### vendor_name
The company or person ISSUING the invoice (the seller/supplier).
Look for: Vendor, Supplier, From, Billed By, Company Name at the top or header.
Do NOT return the buyer/client name.

### po_reference
The Purchase Order number this invoice is billed against.
Accept: PO #, PO Number, PO Ref, Purchase Order, Order Reference, Against PO.
→ Return exactly as printed (e.g. "PO-1001"). Return null if absent.

### invoice_date
The date the invoice was issued.
Accept: Invoice Date, Date, Issued On, Bill Date, Date of Invoice.
→ Return as a string in the format found on the document.

### bank_account
The supplier's bank account number for payment.
Accept: Account No, Bank Account, A/C No, Account Number, Pay To Account.
→ Return the masked or full number exactly as printed (e.g. "XXXX-XXXX-4521").
→ Return null if no bank details are present.

### line_items
Every individual charge listed on the invoice.
Each item needs:
  - description: the name/label of the item or service (string)
  - amount: the monetary value as a plain number — strip Rs., $, £, ₹, commas (number)
→ Return an empty array [] if no line items are listed.

### total
The final payable amount on the invoice.
Accept: Total, Total Amount, Grand Total, Amount Due, Invoice Total, Net Payable.
→ Return as a plain number — strip currency symbols and commas (e.g. 18500, not "Rs. 18,500").

### confidence
Rate your own extraction quality:
→ "LOW" if ANY of these are true:
   - The document is blurry, rotated, low-resolution, or partially illegible
   - invoice_number could not be found or read clearly
   - total could not be found or read clearly
   - More than 2 fields are null due to poor document quality (not because they genuinely don't exist)
→ "HIGH" if the document is clear and both invoice_number and total were extracted reliably.

## Example

Input text:
  TAX INVOICE
  Apex Office Supplies | HDFC Bank | A/C: XXXX-XXXX-4521
  Invoice No: INV-2305   Date: 15-Mar-2024   PO Ref: PO-1001
  Item                          Qty   Rate    Amount
  A4 Paper Reams (50)            50   200     10000
  Toner Cartridges (20)          20   425      8500
  Total:  Rs. 18,500

Output:
{
  "invoice_number": "INV-2305",
  "vendor_name": "Apex Office Supplies",
  "po_reference": "PO-1001",
  "invoice_date": "15-Mar-2024",
  "bank_account": "XXXX-XXXX-4521",
  "line_items": [
    {"description": "A4 Paper Reams (50)", "amount": 10000},
    {"description": "Toner Cartridges (20)", "amount": 8500}
  ],
  "total": 18500,
  "confidence": "HIGH"
}
"""

TEXT_EXTRACTION_PROMPT = EXTRACTION_INSTRUCTIONS + """
## Invoice text to extract from
---
{text}
---"""

VISION_EXTRACTION_PROMPT = EXTRACTION_INSTRUCTIONS + """
## Invoice
The invoice is provided as an image above. Extract all fields from it."""


# ── PyMuPDF helpers ────────────────────────────────────────────────────────

def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract plain text from all pages of a PDF. Returns empty string if none."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_text = [page.get_text("text") for page in doc]
    doc.close()
    return "\n".join(pages_text).strip()


def _pdf_to_png_bytes(pdf_bytes: bytes) -> bytes:
    """Convert first page of PDF to high-res PNG bytes using PyMuPDF."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    pix = page.get_pixmap(dpi=200)
    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes


def _parse_json_response(text: str) -> dict:
    """Extract JSON from LLM response, stripping any markdown fences."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text.strip())


# ── Tier 1: Groq text-only (digital PDFs) ─────────────────────────────────

def _extract_text_with_groq(pdf_text: str) -> dict:
    """Send extracted PDF text to Groq Llama 3.3 for JSON structuring. Free tier."""
    client = openai.OpenAI(
        api_key=_GROQ_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    prompt = TEXT_EXTRACTION_PROMPT.replace("{text}", pdf_text)

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=1024,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    )
    result = _parse_json_response(response.choices[0].message.content)
    result["model_used"] = "groq llama-3.3-70b (text)"
    return result


# ── Tier 2: Gemini vision (scanned/image PDFs) ────────────────────────────

def _extract_image_with_gemini(png_bytes: bytes) -> dict:
    """Send PNG image to Gemini 2.5 Flash for vision extraction."""
    client = genai.Client(api_key=_GOOGLE_KEY)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
            EXTRACTION_SYSTEM + "\n\n" + VISION_EXTRACTION_PROMPT,
        ],
    )
    result = _parse_json_response(response.text)
    result["model_used"] = "gemini-2.5-flash (vision)"
    return result


# ── Tier 3: GPT-4o fallback (image) ───────────────────────────────────────

def _extract_with_gpt(png_bytes: bytes) -> dict:
    """Send PNG image to GPT-4o. Last-resort paid fallback."""
    client = openai.OpenAI(api_key=_OPENAI_KEY)
    png_b64 = base64.b64encode(png_bytes).decode("utf-8")

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=1024,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{png_b64}",
                            "detail": "high",
                        },
                    },
                    {"type": "text", "text": VISION_EXTRACTION_PROMPT},
                ],
            },
        ],
    )
    result = _parse_json_response(response.choices[0].message.content)
    result["model_used"] = "gpt-4o (fallback)"
    return result


# ── Main entry point ───────────────────────────────────────────────────────

def extract(pdf_bytes: bytes) -> dict:
    """
    Extract structured invoice data from a PDF.

    Strategy:
      1. PyMuPDF text → Groq Llama 3.3 70B  (free, no vision quota)
      2. PDF has no text → Gemini vision    (free vision tier)
      3. Both fail → GPT-4o                 (paid fallback)
    """
    pdf_text = _extract_pdf_text(pdf_bytes)
    png_bytes = None  # lazy — only convert if needed
    last_error = None

    # ── Tier 1: digital PDF → Groq text ──
    if pdf_text and len(pdf_text) > 50 and _GROQ_KEY:
        print(f"[extraction] Digital PDF detected ({len(pdf_text)} chars). Using Groq text path.")
        try:
            return _extract_text_with_groq(pdf_text)
        except Exception as e:
            last_error = e
            print(f"[extraction] Groq error — {type(e).__name__}: {e}")
            # fall through to vision path

    # ── Tier 2: Gemini vision ──
    if _GOOGLE_KEY:
        print("[extraction] Using Gemini vision path.")
        png_bytes = _pdf_to_png_bytes(pdf_bytes)
        try:
            return _extract_image_with_gemini(png_bytes)
        except Exception as e:
            last_error = e
            print(f"[extraction] Gemini vision error — {type(e).__name__}: {e}")

    # ── Tier 3: GPT-4o fallback ──
    if _OPENAI_KEY:
        print("[extraction] Falling back to GPT-4o...")
        if png_bytes is None:
            png_bytes = _pdf_to_png_bytes(pdf_bytes)
        return _extract_with_gpt(png_bytes)

    raise RuntimeError(
        f"All extraction tiers failed or no API key configured. Last error: {last_error}"
    )
