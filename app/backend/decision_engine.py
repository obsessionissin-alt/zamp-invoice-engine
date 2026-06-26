"""
Deterministic rule-based decision engine.
NO LLM calls here — pure Python logic only.
Decision is one of: APPROVE, FLAG, ESCALATE
"""

import json
import os
from typing import Any

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data")


def _load_po_dataset() -> list[dict]:
    with open(os.path.join(DATA_DIR, "po_dataset.json"), "r") as f:
        return json.load(f)


def _load_vendor_history() -> dict:
    with open(os.path.join(DATA_DIR, "vendor_history.json"), "r") as f:
        return json.load(f)


def _normalize(s: Any) -> str:
    """Lowercase + strip for loose string matching."""
    return str(s or "").strip().lower()


def _line_items_sum_to_total(extracted: dict) -> tuple[bool, str]:
    """
    Returns (ok, reason).
    ok=True  → line items sum within Rs.1 of stated total (rounding tolerance).
    ok=False → mathematical inconsistency detected.
    """
    line_items = extracted.get("line_items") or []
    stated_total = extracted.get("total")

    if not line_items or stated_total is None:
        # Cannot verify — treat as consistent (extraction confidence handles missing fields)
        return True, ""

    try:
        calculated = sum(float(item.get("amount", 0)) for item in line_items)
        stated = float(stated_total)
        if abs(calculated - stated) > 1.0:
            return False, (
                f"Line items sum to Rs. {calculated:,.2f} but invoice states "
                f"Rs. {stated:,.2f} — internal inconsistency of Rs. {abs(calculated - stated):,.2f}."
            )
    except (TypeError, ValueError):
        return True, ""

    return True, ""


def run(extracted: dict) -> dict:
    """
    Run the 3-step decision engine against extracted invoice data.

    Returns:
        {
            "decision": "APPROVE" | "FLAG" | "ESCALATE",
            "rule_triggered": str,   # short machine-readable label
            "raw_reason": str,       # plain-English reason for reasoning.py to expand
            "po_match": dict | None, # matched PO record if found
        }
    """
    po_dataset = _load_po_dataset()
    vendor_history = _load_vendor_history()

    vendor_name = (extracted.get("vendor_name") or "").strip()
    invoice_number = (extracted.get("invoice_number") or "").strip()
    po_reference = (extracted.get("po_reference") or "").strip()
    bank_account = (extracted.get("bank_account") or "").strip()
    confidence = (extracted.get("confidence") or "HIGH").upper()

    try:
        invoice_total = float(extracted.get("total") or 0)
    except (TypeError, ValueError):
        invoice_total = 0.0

    # ------------------------------------------------------------------
    # STEP 1 — Anomaly checks (ESCALATE, overrides everything)
    # ------------------------------------------------------------------

    # 1a. Bank account change
    vendor_record = vendor_history.get(vendor_name)
    if vendor_record and bank_account:
        known_account = vendor_record.get("last_known_bank_account", "")
        if known_account and _normalize(bank_account) != _normalize(known_account):
            return {
                "decision": "ESCALATE",
                "rule_triggered": "bank_account_changed",
                "raw_reason": (
                    f"Vendor '{vendor_name}' has a new bank account on this invoice "
                    f"({bank_account}) that does not match the last known account "
                    f"({known_account}) in vendor history. This is a fraud risk signal."
                ),
                "po_match": None,
            }

    # 1b. Duplicate invoice number
    if vendor_record and invoice_number:
        processed = vendor_record.get("processed_invoices", [])
        if invoice_number in processed:
            return {
                "decision": "ESCALATE",
                "rule_triggered": "duplicate_invoice",
                "raw_reason": (
                    f"Invoice number '{invoice_number}' already exists in "
                    f"'{vendor_name}' processed invoices list — likely a duplicate submission."
                ),
                "po_match": None,
            }

    # 1c. Line items don't sum to total
    items_ok, items_reason = _line_items_sum_to_total(extracted)
    if not items_ok:
        return {
            "decision": "ESCALATE",
            "rule_triggered": "line_items_mismatch",
            "raw_reason": items_reason,
            "po_match": None,
        }

    # ------------------------------------------------------------------
    # STEP 2 — Extraction confidence check (FLAG)
    #
    # The engine independently verifies critical fields rather than
    # trusting the LLM's self-reported confidence. If invoice_number or
    # total are missing, confidence is forced LOW regardless of what the
    # LLM reported — because these are facts the engine can check itself.
    # ------------------------------------------------------------------
    missing_fields = []
    if not invoice_number:
        missing_fields.append("invoice_number")
    if not extracted.get("total"):
        missing_fields.append("total")

    # Override LLM confidence if critical fields are actually missing
    if missing_fields:
        confidence = "LOW"

    if confidence == "LOW":
        reason = (
            f"Extraction confidence is LOW. "
            + (
                f"Critical fields could not be reliably read: {', '.join(missing_fields)}. "
                if missing_fields
                else "The source document appears to be low quality or damaged. "
            )
            + "Cannot auto-approve without a reliable invoice number and total — "
            "without an invoice number, duplicate detection is impossible."
        )
        return {
            "decision": "FLAG",
            "rule_triggered": "low_confidence",
            "raw_reason": reason,
            "po_match": None,
        }

    # ------------------------------------------------------------------
    # STEP 3 — PO match check
    # ------------------------------------------------------------------

    # Find matching PO by po_reference
    po_match = None
    for po in po_dataset:
        if _normalize(po["po_number"]) == _normalize(po_reference):
            po_match = po
            break

    # 3a. No PO found
    if po_match is None:
        return {
            "decision": "FLAG",
            "rule_triggered": "no_po_found",
            "raw_reason": (
                f"No Purchase Order found matching reference '{po_reference}'. "
                "A human reviewer needs to locate or create the corresponding PO before this invoice can be approved."
            ),
            "po_match": None,
        }

    po_vendor = po_match.get("vendor", "")
    po_amount = float(po_match.get("approved_amount", 0))

    # 3b. Vendor name mismatch (ESCALATE — risk signal, not just a typo)
    if _normalize(vendor_name) != _normalize(po_vendor):
        return {
            "decision": "ESCALATE",
            "rule_triggered": "vendor_mismatch",
            "raw_reason": (
                f"PO {po_reference} is issued to '{po_vendor}' but this invoice is from "
                f"'{vendor_name}'. A vendor mismatch despite a matching PO reference "
                "is a risk signal that requires human escalation — not just a data entry error."
            ),
            "po_match": po_match,
        }

    # 3c. Tolerance: smaller of 2% of approved_amount OR Rs. 500
    tolerance = min(0.02 * po_amount, 500.0)
    variance = invoice_total - po_amount

    # 3d. Within tolerance → APPROVE
    if abs(variance) <= tolerance:
        return {
            "decision": "APPROVE",
            "rule_triggered": "po_match_within_tolerance",
            "raw_reason": (
                f"Invoice total Rs. {invoice_total:,.2f} matches PO {po_reference} "
                f"approved amount Rs. {po_amount:,.2f} within the allowed tolerance of "
                f"Rs. {tolerance:,.2f}."
            ),
            "po_match": po_match,
        }

    # 3e. Invoice exceeds PO + tolerance → FLAG
    if variance > tolerance:
        return {
            "decision": "FLAG",
            "rule_triggered": "exceeds_po_tolerance",
            "raw_reason": (
                f"Invoice total Rs. {invoice_total:,.2f} exceeds PO {po_reference} "
                f"approved amount Rs. {po_amount:,.2f} by Rs. {variance:,.2f} "
                f"(tolerance is Rs. {tolerance:,.2f}). "
                "This may be due to additional freight, tax, or line items not on the original PO."
            ),
            "po_match": po_match,
        }

    # 3f. Invoice meaningfully less than PO → FLAG (partial/split billing)
    fraction = invoice_total / po_amount if po_amount > 0 else 0
    return {
        "decision": "FLAG",
        "rule_triggered": "partial_billing",
        "raw_reason": (
            f"Invoice total Rs. {invoice_total:,.2f} is significantly less than PO {po_reference} "
            f"approved amount Rs. {po_amount:,.2f} "
            f"({fraction:.0%} of the PO total). "
            "This appears to be a partial or instalment billing against a larger PO. "
            "Auto-approval is withheld on the first instance — please confirm the instalment "
            "arrangement with the vendor before processing."
        ),
        "po_match": po_match,
    }
