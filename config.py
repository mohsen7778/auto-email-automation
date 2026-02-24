"""
config.py - Centralized configuration from environment variables
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_CHAT_IDS: list[int] = [
    int(x.strip())
    for x in os.getenv("ADMIN_CHAT_IDS", "").split(",")
    if x.strip()
]

# ── MongoDB ───────────────────────────────────────────────────────────────────
MONGO_URI: str = os.environ["MONGO_URI"]
MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "cold_email_bot")

# ── Gmail SMTP ────────────────────────────────────────────────────────────────
GMAIL_APP_PASSWORD: str = os.environ["GMAIL_APP_PASSWORD"]
SENDER_NAME: str = os.getenv("SENDER_NAME", "My Company")
SENDER_EMAIL: str = os.environ["SENDER_EMAIL"]

# ── Email sending behaviour ───────────────────────────────────────────────────
SEND_DELAY_MIN: float = float(os.getenv("SEND_DELAY_MIN", "2"))   # seconds
SEND_DELAY_MAX: float = float(os.getenv("SEND_DELAY_MAX", "5"))
DAILY_SEND_LIMIT: int = int(os.getenv("DAILY_SEND_LIMIT", "100"))
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))

# ── GitHub Models (GPT-4o-mini) ───────────────────────────────────────────────
GH_MODELS_TOKEN: str = os.getenv("GH_MODELS_TOKEN", "")
PORTFOLIO_URL: str = os.getenv("PORTFOLIO_URL", "https://astruxmarketing.pages.dev")

# ── App ───────────────────────────────────────────────────────────────────────
PORT: int = int(os.getenv("PORT", "8000"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
