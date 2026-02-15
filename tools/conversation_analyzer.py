"""
GHL Conversation & Call Transcript Analyzer.

Analyzes GHL conversations (SMS, email, call transcripts) for:
  - Buying signals and urgency indicators
  - Objections raised and how to counter them
  - Lead qualification status from conversation context
  - Next-best-action recommendations
  - Urgency scoring (1-10)

Uses the Anthropic API for analysis. Supports prompt caching
to keep the scoring criteria cheap on repeated calls.
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)

try:
    import anthropic
    _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
except ImportError:
    _client = None
    logger.warning("anthropic SDK not installed — conversation analysis unavailable")


CONVERSATION_ANALYSIS_PROMPT = """You are an expert MCA (Merchant Cash Advance) sales analyst.

Analyze this conversation between a sales rep and a business owner/merchant prospect.
Return ONLY valid JSON with this structure:

{
  "urgency_score": 0,
  "buying_signals": [],
  "objections": [],
  "qualification_status": "",
  "key_facts_learned": {},
  "sentiment": "",
  "next_best_action": "",
  "follow_up_timing": "",
  "suggested_response": "",
  "pipeline_stage_recommendation": "",
  "notes": ""
}

Field definitions:

urgency_score (1-10):
  10 = "I need funding this week"
  8-9 = Actively looking, comparing offers
  6-7 = Interested, asking detailed questions
  4-5 = Mildly interested, early stage
  2-3 = Not interested now but might be later
  1 = Dead lead / Do not contact

buying_signals: List of specific phrases or behaviors indicating interest.
  Examples: asking about rates, amounts, timeline, requirements, expressing urgency

objections: List of concerns raised. For each, include:
  {"objection": "what they said", "type": "price|trust|timing|need|authority", "counter": "suggested response"}

qualification_status: One of:
  "qualified" - meets MCA criteria, ready to process
  "needs_info" - interested but missing key info (revenue, TIB, etc.)
  "nurture" - not ready now but worth following up
  "unqualified" - doesn't meet basic criteria
  "dead" - explicitly not interested / do not contact

key_facts_learned: Any business details mentioned in conversation:
  {"monthly_revenue": "", "years_in_business": "", "industry": "",
   "existing_funding": "", "credit_concerns": "", "funding_need": "",
   "timeline": "", "decision_maker": ""}

sentiment: "positive" | "neutral" | "negative" | "frustrated"

next_best_action: Specific next step the rep should take.

follow_up_timing: When to follow up ("immediately", "tomorrow", "3 days", "1 week", "1 month")

suggested_response: If the conversation is ongoing, draft the next message.

pipeline_stage_recommendation: One of:
  "new_lead", "contacted", "qualifying", "docs_requested",
  "docs_received", "submitted_to_funder", "approved", "funded",
  "dead", "nurture"

Return ONLY the JSON object."""


def analyze_conversation(
    messages: list[dict],
    contact_info: Optional[dict] = None,
) -> dict:
    """
    Analyze a conversation for buying signals, objections, and next actions.

    Args:
        messages: List of message dicts, each with:
            - direction: "inbound" or "outbound"
            - body: message text
            - type: "SMS", "Email", "Call", etc.
            - dateAdded: timestamp (optional)
        contact_info: Optional dict with contact name, business, prior notes.

    Returns:
        dict with analysis results or error info.
    """
    if not _client:
        return {"success": False, "error": "Anthropic SDK not available"}

    if not messages:
        return {"success": False, "error": "No messages to analyze"}

    # Format the conversation for Claude
    conversation_text = _format_conversation(messages)

    # Add contact context if available
    context = ""
    if contact_info:
        context = f"\nContact context: {json.dumps(contact_info)}\n"

    try:
        response = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": CONVERSATION_ANALYSIS_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": f"{context}\nConversation:\n{conversation_text}",
                }
            ],
        )

        raw_text = response.content[0].text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3].strip()

        analysis = json.loads(raw_text)
        analysis["_metadata"] = {
            "analyzed_at": datetime.now().isoformat(),
            "message_count": len(messages),
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        return {"success": True, "analysis": analysis}

    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON parse error: {e}"}
    except Exception as e:
        logger.error(f"Conversation analysis failed: {e}")
        return {"success": False, "error": str(e)}


def analyze_call_transcript(
    transcript: str,
    contact_info: Optional[dict] = None,
) -> dict:
    """
    Analyze a call transcript (from GHL Voice AI or manual notes).
    Same analysis as conversation but formatted for voice calls.
    """
    messages = [{"direction": "mixed", "body": transcript, "type": "Call"}]
    return analyze_conversation(messages, contact_info)


def score_conversation_urgency(messages: list[dict]) -> int:
    """
    Quick urgency scoring without full Claude analysis.
    Uses keyword matching for fast, free scoring.
    Returns 1-10 urgency score.

    This is the fast/cheap fallback when you don't want to
    burn an API call on every conversation.
    """
    text = " ".join(m.get("body", "") for m in messages).lower()

    score = 3  # baseline

    # High urgency signals
    high_urgency = [
        "need funding", "need capital", "asap", "urgent", "this week",
        "how fast", "how quickly", "emergency", "payroll", "can't wait",
        "ready to move", "let's do it", "send me the application",
        "what do you need from me", "how do we start", "i'm interested",
    ]
    for signal in high_urgency:
        if signal in text:
            score = min(score + 2, 10)

    # Medium urgency signals
    medium_urgency = [
        "what are the rates", "how much can i get", "what's the process",
        "requirements", "credit score", "bank statements", "tell me more",
        "how does it work", "what do you offer",
    ]
    for signal in medium_urgency:
        if signal in text:
            score = min(score + 1, 10)

    # Negative signals
    negative = [
        "not interested", "stop", "unsubscribe", "don't contact",
        "no thanks", "too expensive", "already funded", "don't need",
        "wrong number", "remove me",
    ]
    for signal in negative:
        if signal in text:
            score = 1
            break

    return score


def _format_conversation(messages: list[dict]) -> str:
    """Format messages into a readable conversation transcript."""
    lines = []
    for msg in messages:
        direction = msg.get("direction", "unknown")
        body = msg.get("body", "").strip()
        msg_type = msg.get("type", "")
        timestamp = msg.get("dateAdded", "")

        if not body:
            continue

        if direction == "inbound":
            prefix = "MERCHANT"
        elif direction == "outbound":
            prefix = "REP"
        else:
            prefix = "UNKNOWN"

        time_str = f" [{timestamp}]" if timestamp else ""
        type_str = f" ({msg_type})" if msg_type else ""
        lines.append(f"{prefix}{type_str}{time_str}: {body}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# BATCH CONVERSATION ANALYSIS
# ═══════════════════════════════════════════════════════════════

async def analyze_recent_conversations(
    max_conversations: int = 20,
    min_messages: int = 2,
) -> list[dict]:
    """
    Pull recent GHL conversations and analyze them for buying signals.
    Returns a list of analyzed conversations sorted by urgency.

    Requires ghl_client to be configured.
    """
    from tools.ghl_client import list_conversations, get_conversation_messages

    conversations = await list_conversations(limit=max_conversations)
    results = []

    for conv in conversations:
        conv_id = conv.get("id", "")
        contact_id = conv.get("contactId", "")

        messages = await get_conversation_messages(conv_id, limit=30)
        if len(messages) < min_messages:
            continue

        # Quick urgency score first (free)
        urgency = score_conversation_urgency(messages)

        result = {
            "conversation_id": conv_id,
            "contact_id": contact_id,
            "message_count": len(messages),
            "quick_urgency_score": urgency,
            "last_message_date": conv.get("lastMessageDate", ""),
        }

        # Only run full Claude analysis on high-urgency conversations
        if urgency >= 6 and _client:
            contact_info = {
                "contact_id": contact_id,
                "contact_name": conv.get("contactName", ""),
            }
            full_analysis = analyze_conversation(messages, contact_info)
            if full_analysis.get("success"):
                result["full_analysis"] = full_analysis["analysis"]
                result["urgency_score"] = full_analysis["analysis"].get("urgency_score", urgency)

        results.append(result)

    # Sort by urgency (highest first)
    results.sort(
        key=lambda r: r.get("urgency_score", r.get("quick_urgency_score", 0)),
        reverse=True,
    )

    return results


# ── Standalone test ────────────────────────────────────────────
if __name__ == "__main__":
    # Test keyword-based scoring
    test_messages = [
        {"direction": "outbound", "body": "Hi, this is TCG. We help businesses with working capital."},
        {"direction": "inbound", "body": "How much can I get? I need funding pretty quickly for a new truck."},
        {"direction": "outbound", "body": "For trucking businesses we typically do $25K-$150K. What's your monthly revenue?"},
        {"direction": "inbound", "body": "Around $80K/month. What are the rates? How fast can you fund?"},
    ]

    urgency = score_conversation_urgency(test_messages)
    print(f"Quick urgency score: {urgency}/10")

    # Full analysis requires API key
    if _client:
        result = analyze_conversation(
            test_messages,
            contact_info={"business_name": "ABC Trucking", "industry": "trucking"},
        )
        print(json.dumps(result, indent=2))
    else:
        print("(Full analysis requires ANTHROPIC_API_KEY)")
