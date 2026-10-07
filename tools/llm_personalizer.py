"""
Claude-powered email personalization.

Takes the template draft from email_generator plus the lead's facts and
asks Claude to rewrite it so it reads like a person wrote it for this
specific business. The template stays the source of truth for facts:

  - Claude may only use facts in the lead JSON and the draft.
  - Any dollar figure in the rewrite must already appear in the draft;
    otherwise the rewrite is rejected and the template is used.
  - Greeting, signature and compliance footer are added by the caller,
    so Claude never touches opt-out language.

Enabled with EMAIL_LLM_PERSONALIZE=true. Token spend is metered against
DAILY_CLAUDE_TOKENS.
"""
import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from tools import api_budget

logger = logging.getLogger(__name__)

try:
    import anthropic
except ImportError:  # pragma: no cover - optional dependency at runtime
    anthropic = None

# Models that accept server-side refusal fallbacks.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
# Skip the call when less than this many tokens are left in today's budget.
_MIN_TOKENS_REMAINING = 5_000

SYSTEM_PROMPT = """You rewrite first-touch outreach emails for a small-business funding broker.

You get a template draft and a JSON object of facts about one business. Rewrite the draft so it reads like a short, plain note a real person wrote to this specific owner.

Rules:
- Use only facts present in the JSON or the draft. Do not invent details about the business, its owner, its lender, or its finances.
- Do not introduce any dollar amount, percentage, or timeframe that is not already in the draft. You may drop numbers.
- Keep estimates conditional ("could", "if", "around"); never promise approval, rates, or savings.
- Do not criticize or make comparative claims about any named lender.
- Never use the phrase "merchant cash advance".
- No greeting line, no sign-off, no signature, no unsubscribe text — those are added separately.
- Body: plain text, 60-140 words, 2-4 short paragraphs, ending with one low-pressure question.
- Subject: under 60 characters, no clickbait, no ALL CAPS, no emoji.
- SMS: under 240 characters, conversational, identifies the sender company, no opt-out text.
"""

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "sms_variant": {"type": "string"},
    },
    "required": ["subject", "body", "sms_variant"],
    "additionalProperties": False,
}

# Facts Claude may draw on. Internal talking points and score internals
# are deliberately excluded.
_FACT_FIELDS = [
    "business_name", "first_name", "contact_position", "industry", "city",
    "state", "business_focus", "funder_display_name", "position_count",
    "ucc_filing_age_months", "google_review_count", "google_rating",
    "employee_count", "years_in_business", "snippet", "description",
]

_DOLLAR_RE = re.compile(r"\$\s?[\d,]+(?:\.\d+)?\s?[KkMm]?")

_client = None


def _get_client():
    global _client
    if _client is None and anthropic is not None and config.ANTHROPIC_API_KEY:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _lead_facts(lead: dict) -> str:
    facts = {
        k: lead[k] for k in _FACT_FIELDS
        if lead.get(k) not in (None, "", [], {})
    }
    facts["sender_company"] = config.SENDER_COMPANY
    return json.dumps(facts, sort_keys=True, default=str)


def _dollar_figures(text: str) -> set[str]:
    return {re.sub(r"[\s,]", "", m).upper() for m in _DOLLAR_RE.findall(text or "")}


def _validate(draft: dict, rewrite: dict) -> Optional[str]:
    """Return a reason string if the rewrite must be rejected."""
    allowed = _dollar_figures(draft.get("body", "") + " " + draft.get("sms_variant", ""))
    produced = _dollar_figures(rewrite["body"] + " " + rewrite["sms_variant"])
    if produced - allowed:
        return f"introduced figures {sorted(produced - allowed)}"
    if "merchant cash advance" in (rewrite["body"] + rewrite["subject"]).lower():
        return "used banned phrase"
    if not rewrite["body"].strip() or not rewrite["subject"].strip():
        return "empty output"
    if len(rewrite["sms_variant"]) > 300:
        return "SMS too long"
    return None


def personalize_with_claude(lead: dict, draft: dict) -> Optional[dict]:
    """
    Rewrite a template draft with Claude. Returns {"subject", "body",
    "sms_variant"} or None (caller keeps the template).
    """
    if not config.EMAIL_LLM_PERSONALIZE:
        return None
    client = _get_client()
    if client is None:
        logger.info("Claude personalization skipped: SDK or ANTHROPIC_API_KEY missing")
        return None
    if api_budget.remaining("claude_tokens") < _MIN_TOKENS_REMAINING:
        logger.warning("Claude personalization skipped: daily token budget exhausted")
        return None

    user_content = (
        f"Business facts (JSON):\n{_lead_facts(lead)}\n\n"
        f"Template draft:\nSubject: {draft['subject']}\n\n{draft['body']}\n\n"
        f"SMS: {draft['sms_variant']}"
    )

    kwargs = dict(
        model=config.CLAUDE_MODEL,
        max_tokens=16000,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_content}],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA},
        },
    )

    try:
        if config.CLAUDE_MODEL in _FALLBACK_MODELS:
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **kwargs,
            )
        else:
            response = client.messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        logger.error(f"Claude personalization failed ({e.status_code}): {e.message}")
        return None
    except anthropic.APIConnectionError as e:
        logger.error(f"Claude personalization connection error: {e}")
        return None

    usage = response.usage
    api_budget.record(
        "claude_tokens",
        (usage.input_tokens or 0) + (usage.output_tokens or 0)
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0),
    )

    if response.stop_reason in ("refusal", "max_tokens"):
        logger.warning(
            f"Claude personalization stopped ({response.stop_reason}) "
            f"for {lead.get('business_name')}"
        )
        return None

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        rewrite = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Claude personalization returned invalid JSON")
        return None

    reason = _validate(draft, rewrite)
    if reason:
        logger.warning(
            f"Claude rewrite rejected for {lead.get('business_name')}: {reason}"
        )
        return None

    return {
        "subject": rewrite["subject"].strip(),
        "body": rewrite["body"].strip(),
        "sms_variant": rewrite["sms_variant"].strip(),
    }
