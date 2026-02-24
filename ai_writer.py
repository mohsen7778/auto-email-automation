"""
ai_writer.py - AI-powered human-like cold email writer
Uses GitHub Models (GPT-4o-mini) to analyze target websites
and write personalized, human-sounding cold emails.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from openai import AsyncOpenAI

from config import GH_MODELS_TOKEN, SENDER_NAME

log = logging.getLogger(__name__)

client = AsyncOpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=GH_MODELS_TOKEN,
)


async def fetch_website_text(url: str) -> str:
    """Fetch plain text content from a website for analysis."""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
            resp = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
            html = resp.text
            # Strip HTML tags to get readable text
            clean = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL)
            clean = re.sub(r"<script[^>]*>.*?</script>", " ", clean, flags=re.DOTALL)
            clean = re.sub(r"<[^>]+>", " ", clean)
            clean = re.sub(r"\s+", " ", clean).strip()
            return clean[:3000]  # Keep it under token limit
    except Exception as e:
        log.warning("Could not fetch website %s: %s", url, e)
        return ""


async def generate_email(
    name: str,
    email: str,
    website: str,
    portfolio_url: str = "https://astruxmarketing.pages.dev",
) -> tuple[str, str]:
    """
    Generate a personalized human-like cold email.
    Returns (subject, body).
    """
    website_content = await fetch_website_text(website)

    website_context = (
        f"Here is the text content scraped from their website ({website}):\n{website_content}"
        if website_content
        else f"Their website is: {website} (could not be scraped, use the URL to infer what kind of business it is)"
    )

    prompt = f"""
You are a skilled human copywriter writing a cold outreach email on behalf of a digital marketing agency called Astrux Marketing.

Target:
- Business name: {name}
- Website: {website}

{website_context}

Your job:
1. Carefully analyze the website content above and identify 2 to 3 SPECIFIC, REAL weaknesses you notice. These could be things like: outdated design, no clear call to action, slow loading feel, missing trust signals, no testimonials visible, poor mobile layout hints, weak headline, confusing navigation, no blog or content, missing contact info etc. Be specific to what you actually see in their content, not generic.

2. Write a cold email from {SENDER_NAME} to this business. The email must:
- Open with their business name naturally, like you genuinely looked at their site
- Mention you went through their website and noticed specific things (reference the actual weaknesses you found)
- Sound like it was written by a real human, NOT a robot or marketer
- Be warm, confident and conversational, not salesy
- Provide real value by explaining what those weak points are costing them
- Tell them you can fix it and show them what that looks like
- Ask them to visit {portfolio_url} to see your work
- End by telling them they can reply to this email OR find contact options on the portfolio site
- Be between 120 and 180 words total, no more
- NO bullet points, NO numbered lists, NO headers
- NO dashes, hyphens, em dashes or any kind of dash character anywhere
- NO words like "delve", "leverage", "game changer", "cutting edge", "seamless", "tailored"
- Write like a real person texting a friendly but professional message
- Do NOT start with "I hope this email finds you well" or any generic opener
- Start with something like "Hey [Name]," or "Hi [Name],"

3. Also write a subject line that feels personal and curiosity-driven, NOT like a marketing email. Max 8 words. No dashes.

Respond in this exact JSON format with no extra text:
{{
  "subject": "your subject line here",
  "body": "your full email body here"
}}
"""

    try:
        response = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a JSON-only API. Return only valid JSON, no markdown, no extra text."},
                {"role": "user", "content": prompt},
            ],
            model="gpt-4o-mini",
            temperature=0.85,
            max_tokens=600,
        )

        raw = response.choices[0].message.content.strip()

        # Clean any markdown code fences
        raw = re.sub(r"```json|```", "", raw).strip()

        import json
        data = json.loads(raw)

        subject = data.get("subject", f"Quick thought about {name}'s website")
        body = data.get("body", "")

        if not body:
            raise ValueError("Empty body from AI")

        return subject, body

    except Exception as e:
        log.error("AI email generation failed for %s: %s", name, e)
        # Fallback to a decent generic template
        subject = f"Noticed something on {name}'s website"
        body = (
            f"Hi {name},\n\n"
            f"I was looking through your website at {website} and a few things caught my attention "
            f"that I think are holding you back from getting more clients online.\n\n"
            f"Nothing major to fix but the kind of small changes that can make a real difference "
            f"in how people see you when they land on your site.\n\n"
            f"I put together some of our recent work at {portfolio_url} if you want to see what "
            f"we have done for businesses like yours.\n\n"
            f"Feel free to reply here or reach out through the contact options on the site.\n\n"
            f"Best,\n{SENDER_NAME}"
        )
        return subject, body
