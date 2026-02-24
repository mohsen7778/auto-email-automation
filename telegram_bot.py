"""
telegram_bot.py - Telegram bot with all commands and conversation flows.
Uses python-telegram-bot v20+ (asyncio-native).
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import random
import re
from datetime import datetime, timezone
from typing import Optional

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import db
from config import (
    ADMIN_CHAT_IDS,
    DAILY_SEND_LIMIT,
    SEND_DELAY_MAX,
    SEND_DELAY_MIN,
    TELEGRAM_BOT_TOKEN,
)
from email_service import send_email
from models import ConvState, validate_email

log = logging.getLogger(__name__)

# ── Conversation state integers ───────────────────────────────────────────────
(
    S_ADD_PAIRS,
    S_ADD_TAG,
    S_TMPL_TAG,
    S_TMPL_SUBJECT,
    S_TMPL_BODY,
    S_AISEND_LEADS,
) = range(6)

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9.\-]+")


# ── Admin guard ───────────────────────────────────────────────────────────────
def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if ADMIN_CHAT_IDS and uid not in ADMIN_CHAT_IDS:
            await update.message.reply_text("⛔ Unauthorised.")
            return ConversationHandler.END
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper


async def reply(update: Update, text: str, **kwargs) -> None:
    """Safe reply that handles both Message and CallbackQuery."""
    if update.message:
        await update.message.reply_text(text, **kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, **kwargs)


# ═════════════════════════════════════════════════════════════════════════════
# /start
# ═════════════════════════════════════════════════════════════════════════════
@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("📥 Inbox", callback_data="inbox")],
        [InlineKeyboardButton("📊 Stats", callback_data="stats")],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🤖 *Cold Email Bot*\n\n"
        "Commands:\n"
        "/add — Add leads\n"
        "/remove — Remove leads\n"
        "/addtemplate — Add/update template\n"
        "/removetemplate — Remove template\n"
        "/listtemplates — List templates\n"
        "/send — Send emails by niche tag\n"
        "/retry — Retry failed emails\n"
        "/stats — Show statistics\n"
        "/export — Export leads to CSV\n"
        "/blacklist — Manage blacklist\n"
        "/markreplied — Manually mark replied\n"
        "/cancel — Cancel current operation",
        parse_mode="Markdown",
        reply_markup=markup,
    )


# ═════════════════════════════════════════════════════════════════════════════
# /add  (Conversation: pairs → tag)
# ═════════════════════════════════════════════════════════════════════════════
@admin_only
async def cmd_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "📋 Send leads in the format:\n"
        "`NAME:email, NAME:email, ...`\n\n"
        "Example:\n`John Doe:john@clinic.com, Jane Smith:jane@gym.com`",
        parse_mode="Markdown",
    )
    return S_ADD_PAIRS


async def add_receive_pairs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    pairs = []
    errors = []

    # Parse "NAME:email" entries separated by commas
    entries = [e.strip() for e in text.split(",") if e.strip()]
    for entry in entries:
        if ":" not in entry:
            errors.append(f"• `{entry}` — missing colon")
            continue
        parts = entry.split(":", 1)
        name = parts[0].strip()
        email = parts[1].strip().lower()
        if not name:
            errors.append(f"• `{entry}` — missing name")
            continue
        if not EMAIL_RE.fullmatch(email):
            errors.append(f"• `{entry}` — invalid email")
            continue
        pairs.append((name, email))

    if not pairs:
        await update.message.reply_text(
            "❌ No valid entries found. Please try again.\n" + "\n".join(errors),
            parse_mode="Markdown",
        )
        return S_ADD_PAIRS

    ctx.user_data["pending_leads"] = pairs
    msg = f"✅ Parsed *{len(pairs)}* lead(s)."
    if errors:
        msg += "\n\n⚠️ Skipped:\n" + "\n".join(errors)
    await update.message.reply_text(msg + "\n\nNow enter the *niche tag*:", parse_mode="Markdown")
    return S_ADD_TAG


async def add_receive_tag(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    tag = update.message.text.strip().lower()
    pairs: list[tuple[str, str]] = ctx.user_data.pop("pending_leads", [])

    added, skipped, blacklisted = 0, 0, 0
    for name, email in pairs:
        try:
            result = await db.insert_lead(name, email, tag)
            if result["is_new"]:
                added += 1
            else:
                skipped += 1
        except ValueError as e:
            if "blacklisted" in str(e):
                blacklisted += 1
            else:
                skipped += 1
        except Exception as exc:
            log.error("Insert lead error: %s", exc)
            skipped += 1

    await update.message.reply_text(
        f"✅ Done! Tag: `{tag}`\n"
        f"• Added: {added}\n"
        f"• Duplicates skipped: {skipped}\n"
        f"• Blacklisted skipped: {blacklisted}",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ═════════════════════════════════════════════════════════════════════════════
# /remove
# ═════════════════════════════════════════════════════════════════════════════
@admin_only
async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = " ".join(ctx.args) if ctx.args else ""
    if not args:
        await update.message.reply_text(
            "Usage: `/remove email1@x.com, email2@x.com`", parse_mode="Markdown"
        )
        return
    emails = [e.strip() for e in args.split(",") if e.strip()]
    try:
        count = await db.remove_leads(emails)
        await update.message.reply_text(f"🗑️ Removed *{count}* lead(s).", parse_mode="Markdown")
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")


# ═════════════════════════════════════════════════════════════════════════════
# /addtemplate  (Conversation: tag → subject → body)
# ═════════════════════════════════════════════════════════════════════════════
@admin_only
async def cmd_addtemplate_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "📝 Enter the *niche tag* for this template:", parse_mode="Markdown"
    )
    return S_TMPL_TAG


async def tmpl_receive_tag(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["tmpl_tag"] = update.message.text.strip().lower()
    await update.message.reply_text("✏️ Enter the *subject line*:", parse_mode="Markdown")
    return S_TMPL_SUBJECT


async def tmpl_receive_subject(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["tmpl_subject"] = update.message.text.strip()
    await update.message.reply_text(
        "📄 Enter the *email body*.\n\nUse `{NAME}` as a placeholder for the lead's name.\n\n"
        "Example:\n`Hello {NAME},\nWe'd love to help your clinic...`",
        parse_mode="Markdown",
    )
    return S_TMPL_BODY


async def tmpl_receive_body(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    tag     = ctx.user_data.pop("tmpl_tag", "")
    subject = ctx.user_data.pop("tmpl_subject", "")
    body    = update.message.text.strip()

    try:
        await db.upsert_template(tag, subject, body)
        await update.message.reply_text(
            f"✅ Template saved for tag `{tag}`!\n\n"
            f"*Subject:* {subject}\n"
            f"*Body preview:* {body[:120]}{'...' if len(body)>120 else ''}",
            parse_mode="Markdown",
        )
    except Exception as exc:
        log.error("Save template error: %s", exc)
        await update.message.reply_text(f"❌ Failed to save template: {exc}")
    return ConversationHandler.END


# ═════════════════════════════════════════════════════════════════════════════
# /removetemplate  /listtemplates
# ═════════════════════════════════════════════════════════════════════════════
@admin_only
async def cmd_removetemplate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tag = " ".join(ctx.args).strip().lower() if ctx.args else ""
    if not tag:
        await update.message.reply_text(
            "Usage: `/removetemplate dental clinic`", parse_mode="Markdown"
        )
        return
    deleted = await db.remove_template(tag)
    if deleted:
        await update.message.reply_text(f"🗑️ Template `{tag}` removed.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ No template found for `{tag}`.", parse_mode="Markdown")


@admin_only
async def cmd_listtemplates(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    templates = await db.list_templates()
    if not templates:
        await update.message.reply_text("📭 No templates yet.")
        return
    lines = [f"• `{t['niche_tag']}` — _{t['subject']}_" for t in templates]
    await update.message.reply_text(
        "📋 *Templates:*\n" + "\n".join(lines), parse_mode="Markdown"
    )


# ═════════════════════════════════════════════════════════════════════════════
# /send  (core sending loop)
# ═════════════════════════════════════════════════════════════════════════════
@admin_only
async def cmd_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tag = " ".join(ctx.args).strip().lower() if ctx.args else ""
    if not tag:
        await update.message.reply_text(
            "Usage: `/send dental clinic`", parse_mode="Markdown"
        )
        return

    # Check daily limit
    daily_count = await db.get_daily_sent_count()
    if daily_count >= DAILY_SEND_LIMIT:
        await update.message.reply_text(
            f"⛔ Daily limit of *{DAILY_SEND_LIMIT}* emails reached. Try again tomorrow.",
            parse_mode="Markdown",
        )
        return

    # Fetch template
    template = await db.get_template(tag)
    if not template:
        await update.message.reply_text(
            f"❌ No template found for `{tag}`. Add one with /addtemplate.",
            parse_mode="Markdown",
        )
        return

    # Fetch unsent leads
    leads = await db.get_unsent_leads(tag)
    if not leads:
        await update.message.reply_text(
            f"📭 No unsent leads for tag `{tag}`.", parse_mode="Markdown"
        )
        return

    # Respect daily limit
    remaining_quota = DAILY_SEND_LIMIT - daily_count
    leads = leads[:remaining_quota]

    status_msg = await update.message.reply_text(
        f"📤 Sending *{len(leads)}* email(s) for `{tag}`...", parse_mode="Markdown"
    )

    sent_ok = 0
    failed  = 0
    for i, lead in enumerate(leads, 1):
        name  = lead["name"]
        email = lead["email"]
        subject = template["subject"]
        body    = template["body"].replace("{NAME}", name)

        success, msg_txt = await send_email(email, name, subject, body)
        if success:
            await db.mark_lead_sent(email, tag)
            sent_ok += 1
        else:
            await db.mark_lead_failed(email)
            failed += 1
            log.warning("Failed to send to %s: %s", email, msg_txt)

        # Progress update every 5 emails or on last
        if i % 5 == 0 or i == len(leads):
            try:
                await status_msg.edit_text(
                    f"📤 Progress: {i}/{len(leads)} | ✅ {sent_ok} | ❌ {failed}",
                )
            except Exception:
                pass

        if i < len(leads):
            delay = random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX)
            await asyncio.sleep(delay)

    await update.message.reply_text(
        f"✅ Done sending for `{tag}`!\n• Sent: {sent_ok}\n• Failed: {failed}",
        parse_mode="Markdown",
    )


# ═════════════════════════════════════════════════════════════════════════════
# /retry
# ═════════════════════════════════════════════════════════════════════════════
@admin_only
async def cmd_retry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tag = " ".join(ctx.args).strip().lower() if ctx.args else ""
    if not tag:
        await update.message.reply_text(
            "Usage: `/retry dental clinic`", parse_mode="Markdown"
        )
        return

    template = await db.get_template(tag)
    if not template:
        await update.message.reply_text(f"❌ No template for `{tag}`.", parse_mode="Markdown")
        return

    leads = await db.get_retry_leads(tag)
    if not leads:
        await update.message.reply_text(f"📭 No failed leads to retry for `{tag}`.", parse_mode="Markdown")
        return

    # Reset failed flag before retry
    for lead in leads:
        await db.get_db().leads.update_one(
            {"email": lead["email"]}, {"$set": {"failed": False}}
        )

    await update.message.reply_text(
        f"🔁 Retrying *{len(leads)}* failed email(s)...", parse_mode="Markdown"
    )

    sent_ok = 0
    for lead in leads:
        body = template["body"].replace("{NAME}", lead["name"])
        success, _ = await send_email(lead["email"], lead["name"], template["subject"], body)
        if success:
            await db.mark_lead_sent(lead["email"], tag)
            sent_ok += 1
        else:
            await db.mark_lead_failed(lead["email"])
        await asyncio.sleep(random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX))

    await update.message.reply_text(
        f"🔁 Retry complete. Sent: {sent_ok}/{len(leads)}", parse_mode="Markdown"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 📥 Inbox button / /inbox
# ═════════════════════════════════════════════════════════════════════════════
@admin_only
async def cmd_inbox(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _check_inbox(update)


async def callback_inbox(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await _check_inbox(update)


async def _check_inbox(update: Update) -> None:
    from email_service import check_brevo_inbox
    await reply(update, "📥 Checking inbox...")
    events = await check_brevo_inbox()
    if not events:
        await reply(
            update,
            "📭 No new replies detected via Brevo events.\n\n"
            "💡 *Tip:* For real-time reply detection, configure an *inbound email* "
            "domain in Brevo → set the webhook URL to:\n"
            "`https://your-domain.com/webhook/reply`",
            parse_mode="Markdown",
        )
        return

    for event in events[:10]:
        email = event.get("email", "unknown")
        lead  = await db.mark_lead_replied(email)
        name  = lead["name"] if lead else "Unknown"
        await reply(
            update,
            f"💬 *Reply from:* {name} — `{email}`\n"
            f"Event: `{event.get('event', '?')}`\n"
            f"Time: {event.get('date', '?')}",
            parse_mode="Markdown",
        )


# ═════════════════════════════════════════════════════════════════════════════
# Brevo webhook endpoint (called from main.py FastAPI)
# ═════════════════════════════════════════════════════════════════════════════
async def process_inbound_reply(
    application: Application,
    sender_email: str,
    sender_name: str,
    subject: str,
    text_preview: str,
) -> None:
    """Called by the webhook handler when Brevo sends an inbound email event."""
    lead = await db.mark_lead_replied(sender_email)
    name = lead["name"] if lead else sender_name or sender_email

    msg = (
        f"📬 *Reply received!*\n"
        f"👤 *From:* {name} — `{sender_email}`\n"
        f"📌 *Subject:* {subject}\n\n"
        f"📝 *Preview:*\n{text_preview[:400]}"
    )
    for chat_id in ADMIN_CHAT_IDS:
        try:
            await application.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode="Markdown",
            )
        except Exception as exc:
            log.error("Failed to notify admin %s: %s", chat_id, exc)


# ═════════════════════════════════════════════════════════════════════════════
# /stats
# ═════════════════════════════════════════════════════════════════════════════
@admin_only
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    s = await db.get_stats()
    await update.message.reply_text(
        "📊 *Statistics*\n\n"
        f"👥 Total leads:   {s['total']}\n"
        f"📤 Sent:          {s['sent']}\n"
        f"💬 Replied:       {s['replied']}\n"
        f"⏳ Remaining:     {s['remaining']}\n"
        f"❌ Failed:        {s['failed']}\n"
        f"📈 Reply rate:    {s['reply_rate']}%",
        parse_mode="Markdown",
    )


async def callback_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    s = await db.get_stats()
    await update.callback_query.message.reply_text(
        "📊 *Statistics*\n\n"
        f"👥 Total leads:   {s['total']}\n"
        f"📤 Sent:          {s['sent']}\n"
        f"💬 Replied:       {s['replied']}\n"
        f"⏳ Remaining:     {s['remaining']}\n"
        f"❌ Failed:        {s['failed']}\n"
        f"📈 Reply rate:    {s['reply_rate']}%",
        parse_mode="Markdown",
    )


# ═════════════════════════════════════════════════════════════════════════════
# /export
# ═════════════════════════════════════════════════════════════════════════════
@admin_only
async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tag = " ".join(ctx.args).strip().lower() if ctx.args else None
    leads = await db.get_leads_for_export(tag)
    if not leads:
        await update.message.reply_text("📭 No leads to export.")
        return

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["name", "email", "niche_tag", "used", "replied",
                    "template_used", "sent_at", "failed", "fail_count", "created_at"],
        extrasaction="ignore",
    )
    writer.writeheader()
    for lead in leads:
        # Serialise datetime fields
        for k in ("sent_at", "created_at"):
            if isinstance(lead.get(k), datetime):
                lead[k] = lead[k].isoformat()
        writer.writerow(lead)

    buf.seek(0)
    filename = f"leads{'_' + tag if tag else ''}.csv"
    await update.message.reply_document(
        document=buf.getvalue().encode("utf-8"),
        filename=filename,
        caption=f"📊 Exported {len(leads)} lead(s)." + (f" Tag: `{tag}`" if tag else ""),
        parse_mode="Markdown",
    )


# ═════════════════════════════════════════════════════════════════════════════
# /blacklist
# ═════════════════════════════════════════════════════════════════════════════
@admin_only
async def cmd_blacklist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    sub = ctx.args[0].lower() if ctx.args else ""
    rest = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else ""

    if sub == "add":
        emails = [e.strip() for e in rest.split(",") if e.strip()]
        added = 0
        for e in emails:
            try:
                if await db.add_to_blacklist(e):
                    added += 1
            except ValueError as ex:
                await update.message.reply_text(f"❌ {ex}")
        await update.message.reply_text(f"🚫 Added *{added}* email(s) to blacklist.", parse_mode="Markdown")

    elif sub == "remove":
        emails = [e.strip() for e in rest.split(",") if e.strip()]
        removed = 0
        for e in emails:
            try:
                if await db.remove_from_blacklist(e):
                    removed += 1
            except ValueError as ex:
                await update.message.reply_text(f"❌ {ex}")
        await update.message.reply_text(f"✅ Removed *{removed}* email(s) from blacklist.", parse_mode="Markdown")

    elif sub == "list":
        bl = await db.list_blacklist()
        if not bl:
            await update.message.reply_text("✅ Blacklist is empty.")
        else:
            text = "\n".join(f"• `{e}`" for e in bl)
            await update.message.reply_text(f"🚫 *Blacklist ({len(bl)}):*\n{text}", parse_mode="Markdown")

    else:
        await update.message.reply_text(
            "Usage:\n"
            "`/blacklist add email@x.com`\n"
            "`/blacklist remove email@x.com`\n"
            "`/blacklist list`",
            parse_mode="Markdown",
        )


# ═════════════════════════════════════════════════════════════════════════════
# /markreplied
# ═════════════════════════════════════════════════════════════════════════════
@admin_only
async def cmd_markreplied(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    email = " ".join(ctx.args).strip().lower() if ctx.args else ""
    if not email:
        await update.message.reply_text(
            "Usage: `/markreplied email@example.com`", parse_mode="Markdown"
        )
        return
    try:
        ok = await db.manual_mark_replied(email)
        if ok:
            await update.message.reply_text(f"✅ Marked `{email}` as replied.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ Lead `{email}` not found.", parse_mode="Markdown")
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")


# ═════════════════════════════════════════════════════════════════════════════
# /aisend  — AI writes personalized email per lead's website
# ═════════════════════════════════════════════════════════════════════════════
@admin_only
async def cmd_aisend_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "🤖 *AI Email Mode*\n\n"
        "Send leads in the format:\n"
        "`NAME:email:website`\n\n"
        "Separate multiple leads with commas. Example:\n"
        "`ABC Clinic:abc@gmail.com:abcclinic.com, XYZ Gym:xyz@gmail.com:xyzgym.com`\n\n"
        "AI will visit each website, analyze it, and write a unique personalized email.",
        parse_mode="Markdown",
    )
    return S_AISEND_LEADS


async def aisend_receive_leads(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    from ai_writer import generate_email
    from config import PORTFOLIO_URL
    from email_service import send_email

    text = update.message.text.strip()
    entries = [e.strip() for e in text.split(",") if e.strip()]

    parsed = []
    errors = []

    for entry in entries:
        parts = [p.strip() for p in entry.split(":")]
        if len(parts) < 3:
            errors.append(f"• `{entry}` — use NAME:email:website format")
            continue
        name    = parts[0]
        email   = parts[1].lower()
        website = parts[2]
        if not EMAIL_RE.fullmatch(email):
            errors.append(f"• `{entry}` — invalid email")
            continue
        parsed.append((name, email, website))

    if not parsed:
        await update.message.reply_text(
            "❌ No valid entries.\n" + "\n".join(errors),
            parse_mode="Markdown",
        )
        return S_AISEND_LEADS

    msg = f"✅ Got *{len(parsed)}* lead(s). AI is now writing personalized emails..."
    if errors:
        msg += "\n\n⚠️ Skipped:\n" + "\n".join(errors)
    status = await update.message.reply_text(msg, parse_mode="Markdown")

    sent_ok = 0
    failed  = 0

    for i, (name, email, website) in enumerate(parsed, 1):
        try:
            await status.edit_text(
                f"✍️ Writing email {i}/{len(parsed)} for *{name}*...",
                parse_mode="Markdown",
            )
        except Exception:
            pass

        # AI generates the email
        subject, body = await generate_email(name, email, website, PORTFOLIO_URL)

        # Send via Brevo
        success, err = await send_email(email, name, subject, body)

        if success:
            sent_ok += 1
            # Save to MongoDB as used lead
            try:
                await db.insert_lead(name, email, "ai_outreach")
                await db.mark_lead_sent(email, "ai_outreach")
            except Exception:
                pass  # Already exists is fine
        else:
            failed += 1
            log.warning("AI send failed for %s: %s", email, err)

        if i < len(parsed):
            delay = random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX)
            await asyncio.sleep(delay)

    await update.message.reply_text(
        f"🤖 AI sending complete!\n"
        f"✅ Sent: {sent_ok}\n"
        f"❌ Failed: {failed}",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ═════════════════════════════════════════════════════════════════════════════
# /cancel  (end any conversation)
# ═════════════════════════════════════════════════════════════════════════════
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text("❎ Operation cancelled.")
    return ConversationHandler.END


# ═════════════════════════════════════════════════════════════════════════════
# Application factory
# ═════════════════════════════════════════════════════════════════════════════
def build_application() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # ── Conversation: /add ────────────────────────────────────────────────────
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", cmd_add_start)],
        states={
            S_ADD_PAIRS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_pairs)],
            S_ADD_TAG:   [MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_tag)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    # ── Conversation: /addtemplate ────────────────────────────────────────────
    tmpl_conv = ConversationHandler(
        entry_points=[CommandHandler("addtemplate", cmd_addtemplate_start)],
        states={
            S_TMPL_TAG:     [MessageHandler(filters.TEXT & ~filters.COMMAND, tmpl_receive_tag)],
            S_TMPL_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, tmpl_receive_subject)],
            S_TMPL_BODY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, tmpl_receive_body)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    # ── Conversation: /aisend ─────────────────────────────────────────────────
    aisend_conv = ConversationHandler(
        entry_points=[CommandHandler("aisend", cmd_aisend_start)],
        states={
            S_AISEND_LEADS: [MessageHandler(filters.TEXT & ~filters.COMMAND, aisend_receive_leads)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    # ── Register handlers ─────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",          cmd_start))
    app.add_handler(CommandHandler("cancel",         cmd_cancel))
    app.add_handler(add_conv)
    app.add_handler(tmpl_conv)
    app.add_handler(aisend_conv)
    app.add_handler(CommandHandler("remove",         cmd_remove))
    app.add_handler(CommandHandler("removetemplate", cmd_removetemplate))
    app.add_handler(CommandHandler("listtemplates",  cmd_listtemplates))
    app.add_handler(CommandHandler("send",           cmd_send))
    app.add_handler(CommandHandler("retry",          cmd_retry))
    app.add_handler(CommandHandler("stats",          cmd_stats))
    app.add_handler(CommandHandler("inbox",          cmd_inbox))
    app.add_handler(CommandHandler("export",         cmd_export))
    app.add_handler(CommandHandler("blacklist",      cmd_blacklist))
    app.add_handler(CommandHandler("markreplied",    cmd_markreplied))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(callback_inbox, pattern="^inbox$"))
    app.add_handler(CallbackQueryHandler(callback_stats, pattern="^stats$"))

    return app


async def set_commands(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start",          "Show menu"),
        BotCommand("add",            "Add leads (NAME:email, ...)"),
        BotCommand("remove",         "Remove leads"),
        BotCommand("addtemplate",    "Add/update email template"),
        BotCommand("removetemplate", "Remove template by niche tag"),
        BotCommand("listtemplates",  "List all templates"),
        BotCommand("aisend",         "AI writes personalized email per website"),
        BotCommand("retry",          "Retry failed emails"),
        BotCommand("stats",          "View statistics"),
        BotCommand("inbox",          "Check inbox for replies"),
        BotCommand("export",         "Export leads to CSV"),
        BotCommand("blacklist",      "Manage email blacklist"),
        BotCommand("markreplied",    "Manually mark email as replied"),
        BotCommand("cancel",         "Cancel current operation"),
    ])
