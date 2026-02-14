# MCA Prospecting Agent

AI-powered MCA (Merchant Cash Advance) lead prospecting pipeline.
Discovers, enriches, scores, and exports qualified business leads
to GoHighLevel CRM with personalized outreach.

## Architecture

```
main_agent.py                  # Daily orchestrator (6-phase pipeline)
├── Phase 0: GHL inbound sync  # Pull CRM state (contacts, opps, conversations)
├── Phase 1: UCC processing    # Score UCC filings for MCA renewal signals
├── Phase 2: Inbound discovery  # Google CSE + Hunter.io + Clearbit
├── Phase 3: Scoring            # 0-100 score, A-D tier classification
├── Phase 4: GHL export         # Create contacts for A-tier leads
└── Phase 5: Slack summary      # Morning report to sales team
```

## Tools

| Module | Purpose |
|--------|---------|
| `tools/ghl_client.py` | Full GHL API v2 client (contacts, opps, pipelines, conversations, calendars) |
| `tools/inbound_leads.py` | Lead discovery: Google CSE → Hunter.io → Clearbit |
| `tools/bank_statement_analyzer.py` | PDF bank statement analysis via Claude vision + batch API |
| `tools/funder_matrix.py` | Funder criteria matching engine — match leads to best-fit funders |
| `tools/deal_calculator.py` | Offer comparison, stacking analysis, consolidation modeling |
| `tools/conversation_analyzer.py` | GHL conversation analysis for buying signals + urgency scoring |
| `tools/lead_scorer.py` | 0-100 MCA lead scoring with tier classification |
| `tools/email_generator.py` | Personalized outreach (renewal, consolidation, new funding templates) |
| `tools/ucc_processor.py` | UCC filing processing + MCA lender detection |
| `tools/google_maps_enricher.py` | Google Places API business enrichment |
| `tools/slack_notifier.py` | Slack morning summaries and alerts |

## MCP Servers

Configured in `config.py` under `MCP_SERVERS`:
- **GHL** — GoHighLevel CRM (21+ tools, expanding to 250+)
- **Google Ads** — Campaign performance and keyword data
- **Apify** — 3,000+ web scraping actors (Google Maps, Yellow Pages)
- **Playwright** — Browser automation for directory scraping

## Running

```bash
# Daily pipeline (dry run)
python main_agent.py --dry-run

# Specific states
python main_agent.py --states FL,TX --dry-run

# Skip inbound discovery (UCC only)
python main_agent.py --skip-inbound --dry-run

# Skip GHL sync
python main_agent.py --skip-ghl-sync --dry-run

# Live mode (creates real GHL contacts)
python main_agent.py --no-dry-run

# Bank statement analysis
python tools/bank_statement_analyzer.py statement.pdf

# Funder matching test
python tools/funder_matrix.py

# Deal calculator test
python tools/deal_calculator.py
```

## Key Design Decisions

- **DRY_RUN=true by default** — never writes to GHL until explicitly enabled
- **Daily caps on all APIs** — prevents runaway spend
- **Domain dedup** — discovered_domains table prevents re-processing
- **Prompt caching** — system prompts use `cache_control: ephemeral` for 90% savings
- **Batch API** — bank statements can be submitted as overnight batches (50% cost savings)
- **Keyword urgency scoring** — free conversation scoring before expensive Claude calls
- **Funder matrix is code, not config** — edit `tools/funder_matrix.py` FUNDER_MATRIX directly

## Environment Variables

See `.env.example` for all required and optional API keys.
Critical: `ANTHROPIC_API_KEY`, `GHL_API_KEY`, `GHL_LOCATION_ID`.
