"""
Two-stage job matcher.

Stage 1: rule-based pre-filter (free, instant).
   - Drop jobs whose title contains "must_not_have" keywords
   - Drop jobs that don't mention any "must_have" keyword
   - Drop jobs in clearly unsuitable locations
   This kills ~80–90% of noise before any AI call.

Stage 2: Claude scoring (cheap with Haiku, but rate-limit yourself).
   - Send profile + job to Claude
   - Ask for structured JSON: score, verdict, reasoning, flags
   - Cache results so reruns don't re-spend tokens.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from anthropic import Anthropic

from . import geo
from .models import Job, Score

logger = logging.getLogger(__name__)


# --- Stage 1: rule-based pre-filter -------------------------------------------

def passes_prefilter(
    job: Job,
    profile: dict[str, Any],
    job_countries: list[str] | None = None,
) -> tuple[bool, str]:
    """Return (pass, reason). Reason is empty when pass=True.

    `job_countries` is the job's location as country codes (see geo.py);
    it only matters when the profile sets `location.countries`.
    """
    haystack = f"{job.title}\n{job.description}".lower()

    must_not = profile.get("must_not_have_keywords", [])
    for kw in must_not:
        if re.search(rf"\b{re.escape(kw.lower())}\b", haystack):
            return False, f"contains '{kw}'"

    # must-have: substring match (so 'design' catches 'designer', 'redesign', etc).
    # The matcher's job is to be permissive here — Claude does the discrimination later.
    must = profile.get("must_have_keywords", [])
    if must and not any(kw.lower() in haystack for kw in must):
        return False, "no must-have keyword present"

    # Country filter — keep only jobs that can be done in an allowed country.
    # Remote jobs aren't exempt: "Remote US" resolves to US like any other.
    allowed = geo.allowed_countries(profile)
    if allowed is not None:
        if not geo.in_allowed(job_countries, allowed):
            return False, f"location '{job.location}' is outside the allowed countries"
        return True, ""

    # No country filter: drop only when the job is in a hard "no" location.
    # Everything else (home, remote, would_relocate, unknown) falls through
    # to Claude, which weighs it against the tiered preferences in the profile.
    loc = profile.get("location", {})
    job_loc = (job.location or "").lower()
    if job_loc and not job.remote:
        for nope in loc.get("would_not_relocate", []):
            if nope.lower() in job_loc:
                return False, f"location '{job.location}' is in would_not_relocate"

    return True, ""


# --- Stage 2: Claude scoring ---------------------------------------------------

SYSTEM_PROMPT = """You are an expert career advisor reviewing job postings on behalf of the person described in the profile (its headline and summary say who they are).

You will be given:
1. A profile JSON describing the person's background, role preferences, and red/green flags
2. A single job posting

Your task: judge how well this job matches the person's profile.

You MUST respond with ONLY a valid JSON object — no preamble, no markdown fences, no explanation outside the JSON. The JSON shape is:

{
  "score": <integer 0-100>,
  "verdict": "<one of: strong_match | possible_match | weak_match | skip>",
  "reasoning": "<2-3 sentences, plain English, explaining the score>",
  "green_flags": ["<flag1>", "<flag2>", ...],
  "red_flags": ["<flag1>", "<flag2>", ...]
}

Scoring rubric:
- 85-100 (strong_match): strong fit across role family, sector, seniority, and scope with no major gaps. Leadership title is a positive signal but not a requirement — a senior IC role (Principal, Staff, Senior Product Designer) with genuine strategic ownership at a well-aligned company qualifies equally. All four of role fit, sector fit, seniority, and meaningful remit must be clearly present.
- 65-84 (possible_match): solid alignment with one genuine uncertainty — title/level, scope, or sector. Worth investigating but not a clear yes.
- 40-64 (weak_match): some overlap but significant gaps. Adjacent discipline, noticeably too junior, or remit is too narrow.
- 0-39 (skip): clearly off-profile. Wrong discipline entirely, junior level, or explicitly in their avoid sectors.

When evaluating location, treat jobs in 'home' or 'would_relocate' regions as
fully acceptable. Only penalise location for jobs in 'would_not_relocate' or
clearly unspecified-and-non-remote postings.

When recent feedback examples are provided, treat them as the strongest
signal of fit. The person's actual votes outweigh stated profile preferences
when the two conflict. A pattern like "marked Interested at score 55, marked
Not for me at score 70" suggests the matcher is miscalibrated — look at what
those jobs share that the score missed.

Be honest and specific. Do not inflate scores. The person values precision over flattery.

Write the reasoning and flags to the person, in the second person ("your service
design experience", "you would report to..."). Never refer to them as he, she or
they, or by name: the dashboard is read by the person themselves.
"""

USER_PROMPT_TEMPLATE = """Here is the person's profile:

```json
{profile}
```

Here is the job posting:

Company: {company}
Title: {title}
Location: {location}
Source: {source_label}
URL: {url}

Description:
{description}

Respond with the JSON object only.
"""


class ClaudeMatcher:
    def __init__(self, api_key: str, model: str):
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def score(
        self,
        job: Job,
        profile: dict[str, Any],
        feedback_examples: list[dict] | None = None,
    ) -> Score:
        """Send a single job to Claude and parse the verdict."""

        feedback_block = ""
        if feedback_examples:
            lines = []
            for fb in feedback_examples:
                verdict = "INTERESTED" if fb["rating"] == "up" else "NOT FOR ME"
                loc = fb.get("location") or "no location"
                prior = fb.get("prior_score")
                score_part = f", scored {prior}" if prior is not None else ""
                line = f"- [{verdict}] '{fb['title']}' at {fb['company']} ({loc}{score_part})"
                if fb.get("positive"):
                    line += f"\n  + {fb['positive']}"
                if fb.get("negative"):
                    line += f"\n  - {fb['negative']}"
                lines.append(line)
            feedback_block = (
                "\n\nRECENT FEEDBACK FROM THIS PERSON (use as calibration):\n"
                + "\n".join(lines)
                + "\n"
            )

        user_prompt = USER_PROMPT_TEMPLATE.format(
            profile=json.dumps(profile, indent=2),
            company=job.company,
            title=job.title,
            location=job.location or "not specified",
            source_label=job.source_label or job.source,
            url=job.url,
            description=(job.description or "(no description scraped — judge from title/company alone, and lower confidence accordingly)")[:6000],
        ) + feedback_block

        # The system prompt talks about 'home' and 'would_relocate'; a profile
        # using the country filter has neither, so say what the list means.
        allowed = geo.allowed_countries(profile)
        if allowed:
            user_prompt += (
                "\nLocation: the person only sees jobs that can be done in these countries "
                f"(ISO codes): {', '.join(sorted(allowed))}. Treat all of them as fully acceptable.\n"
            )

        resp = self.client.messages.create(
            model=self.model,
            # Haiku 5.5 thinks before answering and those tokens count against
            # max_tokens; a tight cap would truncate the JSON verdict.
            max_tokens=16000,
            # Judgement call on fit, so not "low"; stated explicitly because
            # the default differs between model generations.
            output_config={"effort": "medium"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        ).strip()

        data = _parse_json_response(text)

        return Score(
            job_id=job.id,
            score=int(data.get("score", 0)),
            verdict=str(data.get("verdict", "skip")),
            reasoning=str(data.get("reasoning", "")),
            green_flags=list(data.get("green_flags", [])),
            red_flags=list(data.get("red_flags", [])),
            model=self.model,
        )


def _parse_json_response(text: str) -> dict:
    """Best-effort JSON extraction. Claude usually returns clean JSON,
    but we strip markdown fences just in case."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip ```json ... ``` fences
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Last resort: find the first {...} block
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise
