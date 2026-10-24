"""
Company lookup for leads: headquarters city and sector, via Claude.

Most portfolio pages list only company names (Index Ventures publishes no
location at all), so without this every lead falls back to its scraper's
default_location — "Europe" — which is a placeholder, not data. We ask
Claude what it knows about each company instead.

The model is told to answer "unknown" rather than guess. For tiny seed-stage
companies it often won't know, and a blank location on the card is more
honest than a plausible-looking wrong city.
"""

from __future__ import annotations

import logging

from anthropic import Anthropic
from pydantic import BaseModel

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You identify where startups are headquartered and what sector they are in.

Only answer from what you actually know about the specific company. Several
unrelated companies often share a name; the VC that backs it, its website,
and its listed location and sectors tell you which one is meant. If you are
not confident, answer "unknown" for headquarters and return an empty sector
list — unless sectors are listed, which you can still translate. A wrong
answer is worse than "unknown".

- headquarters: "City, Country" with the country written in full, e.g.
  "London, United Kingdom", "San Francisco, United States". If the company
  is remote-first or you only know the country, give just the country.
- sectors: one to three short, conventional startup-sector labels naming
  the domain, e.g. fintech, health-tech, AI/ML, developer tools, security,
  climate, ecommerce, adtech, crypto. Never use generic business-model labels
  such as "B2B SaaS", "SaaS", "software" or "enterprise" — they apply to most
  startups and tell the reader nothing. The listed sectors are the VC's own
  wording; translate them into these conventional labels rather than
  copying them.
"""


class CompanyFacts(BaseModel):
    headquarters: str
    sectors: list[str]


class CompanyLookup:
    def __init__(self, api_key: str, model: str):
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def lookup(
        self, name: str, vc: str, url: str, listed_location: str = "", listed_sectors: str = ""
    ) -> CompanyFacts:
        """Return what Claude knows; headquarters is "" when unknown."""
        resp = self.client.messages.parse(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            # A recall question, not a reasoning one — low effort keeps it cheap.
            output_config={"effort": "low"},
            output_format=CompanyFacts,
            messages=[{
                "role": "user",
                "content": (
                    f"Company: {name}\nBacked by: {vc}\nWebsite: {url or 'not known'}"
                    f"\nListed location: {listed_location or 'not known'}"
                    f"\nListed sectors: {listed_sectors or 'not known'}"
                ),
            }],
        )
        facts = resp.parsed_output
        if resp.stop_reason == "refusal" or facts is None:
            return CompanyFacts(headquarters="", sectors=[])
        if facts.headquarters.strip().lower() == "unknown":
            facts.headquarters = ""
        return facts
