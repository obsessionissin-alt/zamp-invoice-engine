"""
Generates a plain-English explanation for a decision already made
by the deterministic decision engine.
NO decision logic here — only narrative generation.

Primary:  Groq Llama 3.3 70B (free)
Fallback: Gemini 2.5 Flash   (free)
Last:     GPT-4o            (paid)
"""

import os

import openai
from dotenv import find_dotenv, load_dotenv
from google import genai

load_dotenv(find_dotenv(usecwd=True))

_GROQ_KEY = os.getenv("GROQ_API_KEY")
_GOOGLE_KEY = os.getenv("GOOGLE_API_KEY")
_OPENAI_KEY = os.getenv("OPENAI_API_KEY")


def _build_prompt(extracted: dict, decision: str, rule_triggered: str, raw_reason: str, po_match: dict | None) -> str:
    vendor = extracted.get("vendor_name", "Unknown vendor")
    invoice_num = extracted.get("invoice_number", "N/A")
    total = extracted.get("total", "N/A")
    po_ref = extracted.get("po_reference", "N/A")
    po_amount = po_match.get("approved_amount") if po_match else None

    context = f"""
Invoice details:
- Vendor: {vendor}
- Invoice number: {invoice_num}
- PO reference: {po_ref}
- Invoice total: Rs. {total}
- PO approved amount: {"Rs. " + str(po_amount) if po_amount else "N/A"}

Decision made by the rule engine: {decision}
Rule triggered: {rule_triggered}
Technical reason: {raw_reason}
""".strip()

    return f"""You are an accounts-payable audit assistant writing a review note for a human approver.

The rule engine has already made the following decision — do NOT second-guess or change it.
Your job is to write a clear, professional 2–4 sentence explanation of WHY this decision was made,
in plain English that a non-technical finance manager can understand.

Be specific: mention the invoice number, vendor, amounts, and the exact rule that triggered.
Do not use bullet points. Write in flowing prose.

{context}

Write the explanation now:"""


def _call_groq(prompt: str) -> str:
    client = openai.OpenAI(
        api_key=_GROQ_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()


def _call_gemini(prompt: str) -> str:
    client = genai.Client(api_key=_GOOGLE_KEY)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return response.text.strip()


def _call_gpt(prompt: str) -> str:
    client = openai.OpenAI(api_key=_OPENAI_KEY)
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()


def generate(
    extracted: dict,
    decision: str,
    rule_triggered: str,
    raw_reason: str,
    po_match: dict | None,
) -> dict:
    """
    Generate a plain-English reasoning paragraph for the given decision.
    Returns {"reasoning": str, "model_used": str}
    """
    prompt = _build_prompt(extracted, decision, rule_triggered, raw_reason, po_match)
    last_error = None

    if _GROQ_KEY:
        try:
            text = _call_groq(prompt)
            return {"reasoning": text, "model_used": "groq llama-3.3-70b"}
        except Exception as e:
            last_error = e
            print(f"[reasoning] Groq error — {type(e).__name__}: {e}")

    if _GOOGLE_KEY:
        try:
            text = _call_gemini(prompt)
            return {"reasoning": text, "model_used": "gemini-2.5-flash (fallback)"}
        except Exception as e:
            last_error = e
            print(f"[reasoning] Gemini error — {type(e).__name__}: {e}")

    if _OPENAI_KEY:
        try:
            text = _call_gpt(prompt)
            return {"reasoning": text, "model_used": "gpt-4o (fallback)"}
        except Exception as e:
            last_error = e
            print(f"[reasoning] GPT-4o error — {type(e).__name__}: {e}")

    return {
        "reasoning": raw_reason,
        "model_used": f"raw rule reason (all LLMs unavailable: {last_error})",
    }
