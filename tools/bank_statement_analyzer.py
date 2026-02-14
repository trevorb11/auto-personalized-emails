"""
Bank Statement PDF Analyzer.

Uses the Anthropic API (Claude's vision/PDF capabilities) to extract
underwriting metrics from merchant bank statements:
  - Average daily balance
  - Monthly deposit totals and consistency
  - NSF/overdraft frequency
  - Existing MCA/lender payment detection (stacking signals)
  - Negative balance days
  - Revenue trend (growing, stable, declining)

Supports single-statement and batch processing via Anthropic Batch API
for 50% cost savings on overnight runs.

Setup: Requires ANTHROPIC_API_KEY in .env
"""
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)

# ── Anthropic client setup ──────────────────────────────────────
try:
    import anthropic
    _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
except ImportError:
    _client = None
    logger.warning("anthropic SDK not installed — bank statement analysis unavailable")


# The system prompt encoding underwriting extraction criteria
BANK_STATEMENT_SYSTEM_PROMPT = """You are an expert MCA (Merchant Cash Advance) underwriter analyzing bank statements.

Extract the following metrics from the bank statement PDF and return ONLY valid JSON:

{
  "bank_name": "string",
  "account_holder": "string",
  "statement_period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "beginning_balance": 0.00,
  "ending_balance": 0.00,
  "total_deposits": 0.00,
  "total_withdrawals": 0.00,
  "deposit_count": 0,
  "average_daily_balance": 0.00,
  "lowest_daily_balance": 0.00,
  "highest_daily_balance": 0.00,
  "negative_balance_days": 0,
  "nsf_overdraft_count": 0,
  "nsf_overdraft_fees": 0.00,
  "detected_mca_payments": [
    {"name": "string", "amount": 0.00, "frequency": "daily|weekly|monthly", "estimated_monthly_total": 0.00}
  ],
  "detected_loan_payments": [
    {"name": "string", "amount": 0.00, "frequency": "string"}
  ],
  "large_deposits": [
    {"date": "YYYY-MM-DD", "amount": 0.00, "description": "string"}
  ],
  "revenue_trend": "growing|stable|declining|insufficient_data",
  "monthly_revenue_estimate": 0.00,
  "daily_average_deposits": 0.00,
  "notes": ["any important observations"]
}

MCA payment detection rules:
- Look for daily ACH debits of consistent amounts ($50-$2000/day) — these are MCA payments
- Common MCA company names: Yellowstone, Credibly, Rapid Finance, Forward Financing, Libertas, Pearl Capital, Kapitus, OnDeck, Fundkite, CloudFund, Mantis, National Funding, Bluevine, Kabbage, CAN Capital
- Weekly ACH debits of consistent amounts may also be MCA payments
- Flag any "merchant cash" or "business advance" descriptions

Revenue trend assessment:
- Compare weekly deposit totals across the statement period
- "growing" = last 2 weeks average > first 2 weeks average by 10%+
- "declining" = last 2 weeks average < first 2 weeks average by 10%+
- "stable" = within 10% variance

Return ONLY the JSON object, no other text."""


def analyze_bank_statement(
    pdf_path: str,
    use_cache: bool = True,
) -> dict:
    """
    Analyze a bank statement PDF using Claude's vision capabilities.

    Args:
        pdf_path: Path to the bank statement PDF file.
        use_cache: Whether to use prompt caching for the system prompt.

    Returns:
        dict with extracted underwriting metrics, or error info.
    """
    if not _client:
        return {"success": False, "error": "Anthropic SDK not available or API key not set"}

    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        return {"success": False, "error": f"File not found: {pdf_path}"}

    if not pdf_file.suffix.lower() == ".pdf":
        return {"success": False, "error": "File must be a PDF"}

    try:
        import base64
        pdf_data = base64.standard_b64encode(pdf_file.read_bytes()).decode("utf-8")

        # Build the message with the PDF
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Analyze this bank statement and extract all underwriting metrics per the instructions.",
                    },
                ],
            }
        ]

        # Use prompt caching on the system prompt to save 90% on repeated calls
        system_config = [
            {
                "type": "text",
                "text": BANK_STATEMENT_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ] if use_cache else BANK_STATEMENT_SYSTEM_PROMPT

        response = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=system_config,
            messages=messages,
        )

        # Parse the response
        raw_text = response.content[0].text.strip()

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3].strip()

        metrics = json.loads(raw_text)

        # Add computed fields
        metrics["_analysis_metadata"] = {
            "analyzed_at": datetime.now().isoformat(),
            "model": CLAUDE_MODEL,
            "source_file": str(pdf_path),
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_read_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
            "cache_creation_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
        }

        # Compute underwriting summary
        metrics["_underwriting_summary"] = _compute_underwriting_summary(metrics)

        return {"success": True, "metrics": metrics}

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse bank statement analysis: {e}")
        return {"success": False, "error": f"JSON parse error: {e}", "raw_response": raw_text}
    except Exception as e:
        logger.error(f"Bank statement analysis failed: {e}")
        return {"success": False, "error": str(e)}


def _compute_underwriting_summary(metrics: dict) -> dict:
    """Compute a quick underwriting summary from extracted metrics."""
    total_deposits = metrics.get("total_deposits", 0)
    nsf_count = metrics.get("nsf_overdraft_count", 0)
    neg_days = metrics.get("negative_balance_days", 0)
    mca_payments = metrics.get("detected_mca_payments", [])
    avg_balance = metrics.get("average_daily_balance", 0)

    # Stacking risk
    num_positions = len(mca_payments)
    total_daily_mca_obligation = sum(
        p.get("estimated_monthly_total", 0) / 22  # ~22 business days
        for p in mca_payments
    )

    # Cash flow health score (simple 0-100)
    health = 100
    if nsf_count > 0:
        health -= min(nsf_count * 10, 40)
    if neg_days > 0:
        health -= min(neg_days * 5, 30)
    if num_positions >= 3:
        health -= 20
    elif num_positions >= 2:
        health -= 10
    if avg_balance < 1000:
        health -= 15
    health = max(health, 0)

    # Sustainable daily payment capacity (rough estimate)
    daily_deposits = metrics.get("daily_average_deposits", total_deposits / 22)
    sustainable_daily = max(0, (daily_deposits * 0.15) - total_daily_mca_obligation)

    return {
        "cash_flow_health_score": health,
        "existing_positions": num_positions,
        "total_daily_mca_obligation": round(total_daily_mca_obligation, 2),
        "sustainable_daily_payment": round(sustainable_daily, 2),
        "estimated_max_new_funding": round(sustainable_daily * 22 * 8, 2),  # ~8mo term
        "stacking_risk": "high" if num_positions >= 3 else "medium" if num_positions >= 2 else "low",
        "nsf_risk": "high" if nsf_count >= 5 else "medium" if nsf_count >= 2 else "low",
        "revenue_trend": metrics.get("revenue_trend", "insufficient_data"),
        "recommendation": _get_recommendation(health, num_positions, nsf_count),
    }


def _get_recommendation(health: int, positions: int, nsf_count: int) -> str:
    """Generate a plain-English underwriting recommendation."""
    if health >= 70 and positions <= 1 and nsf_count <= 1:
        return "Strong candidate. Low risk profile — proceed with standard offers."
    elif health >= 50 and positions <= 2:
        return "Moderate candidate. Consider consolidation offer if stacked. Watch NSF history."
    elif health >= 30:
        return "Marginal candidate. High risk — if pursuing, keep position small and short-term."
    else:
        return "Decline recommended. Cash flow insufficient to support additional obligation."


# ═══════════════════════════════════════════════════════════════
# BATCH PROCESSING (Anthropic Batch API — 50% discount)
# ═══════════════════════════════════════════════════════════════

def create_batch_analysis(pdf_paths: list[str]) -> dict:
    """
    Submit multiple bank statements for batch analysis via Anthropic Batch API.
    Results arrive within 24 hours at 50% cost savings.

    Args:
        pdf_paths: List of paths to bank statement PDFs.

    Returns:
        dict with batch_id for status checking, or error info.
    """
    if not _client:
        return {"success": False, "error": "Anthropic SDK not available"}

    import base64

    requests = []
    for i, pdf_path in enumerate(pdf_paths):
        pdf_file = Path(pdf_path)
        if not pdf_file.exists():
            logger.warning(f"Skipping missing file: {pdf_path}")
            continue

        pdf_data = base64.standard_b64encode(pdf_file.read_bytes()).decode("utf-8")

        requests.append({
            "custom_id": f"stmt-{i}-{pdf_file.stem}",
            "params": {
                "model": CLAUDE_MODEL,
                "max_tokens": 4096,
                "system": [
                    {
                        "type": "text",
                        "text": BANK_STATEMENT_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": pdf_data,
                                },
                            },
                            {
                                "type": "text",
                                "text": "Analyze this bank statement and extract all underwriting metrics.",
                            },
                        ],
                    }
                ],
            },
        })

    if not requests:
        return {"success": False, "error": "No valid PDF files provided"}

    try:
        batch = _client.messages.batches.create(requests=requests)
        logger.info(f"Created batch {batch.id} with {len(requests)} statements")
        return {
            "success": True,
            "batch_id": batch.id,
            "statement_count": len(requests),
            "status": "processing",
        }
    except Exception as e:
        logger.error(f"Batch creation failed: {e}")
        return {"success": False, "error": str(e)}


def check_batch_status(batch_id: str) -> dict:
    """Check the status of a batch analysis job."""
    if not _client:
        return {"success": False, "error": "Anthropic SDK not available"}

    try:
        batch = _client.messages.batches.retrieve(batch_id)
        return {
            "batch_id": batch.id,
            "status": batch.processing_status,
            "created_at": batch.created_at,
            "counts": {
                "succeeded": batch.request_counts.succeeded,
                "errored": batch.request_counts.errored,
                "processing": batch.request_counts.processing,
            },
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_batch_results(batch_id: str) -> list[dict]:
    """Retrieve results from a completed batch analysis."""
    if not _client:
        return []

    try:
        results = []
        for result in _client.messages.batches.results(batch_id):
            custom_id = result.custom_id
            if result.result.type == "succeeded":
                raw_text = result.result.message.content[0].text.strip()
                if raw_text.startswith("```"):
                    raw_text = raw_text.split("\n", 1)[1]
                    if raw_text.endswith("```"):
                        raw_text = raw_text[:-3].strip()
                try:
                    metrics = json.loads(raw_text)
                    metrics["_underwriting_summary"] = _compute_underwriting_summary(metrics)
                    results.append({"custom_id": custom_id, "success": True, "metrics": metrics})
                except json.JSONDecodeError:
                    results.append({"custom_id": custom_id, "success": False, "error": "JSON parse error"})
            else:
                results.append({"custom_id": custom_id, "success": False, "error": str(result.result)})
        return results
    except Exception as e:
        logger.error(f"Batch results retrieval failed: {e}")
        return []


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python bank_statement_analyzer.py <path-to-pdf>")
        print("  Or:  python bank_statement_analyzer.py --batch <pdf1> <pdf2> ...")
        sys.exit(1)

    if sys.argv[1] == "--batch":
        result = create_batch_analysis(sys.argv[2:])
        print(json.dumps(result, indent=2))
    else:
        result = analyze_bank_statement(sys.argv[1])
        print(json.dumps(result, indent=2, default=str))
