import io
import logging

from telegram import InputFile, Update
from telegram.ext import ContextTypes

from banbhai.services.export_service import to_csv, to_json
from banbhai.services.telegram_service import is_admin

logger = logging.getLogger("BanBhai")


async def analytics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update):
        return

    days = 7
    if context.args:
        try:
            days = int(context.args[0])
        except ValueError:
            days = 7

    report = await context.application.bot_data["analytics"].report(
        update.effective_chat.id, days
    )

    lines = [
        "📊 BanBhai Analytics",
        "",
        f"Period: {report['days']} day(s)",
        f"👋 Joins: {report['joins']}",
        f"⚠️ Warnings: {report['warnings']}",
        f"🛡️ Actions: {report['actions']}",
        "",
        "Top warning reasons:",
    ]
    top = report["warning_reasons"].most_common(5)
    lines += [f"• {k}: {v}" for k, v in top] or ["• None"]
    lines.append("")
    lines.append("Top actions:")
    top = report["actions_by_type"].most_common(5)
    lines += [f"• {k}: {v}" for k, v in top] or ["• None"]

    await update.effective_message.reply_text("\n".join(lines))


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update):
        return

    fmt = context.args[0].lower() if context.args else "csv"
    try:
        days = int(context.args[1]) if len(context.args) > 1 else 7
    except ValueError:
        days = 7

    report = await context.application.bot_data["analytics"].report(
        update.effective_chat.id, days
    )

    if fmt == "json":
        content = to_json(report)
        filename = f"banbhai-analytics-{days}d.json"
    else:
        content = to_csv(report)
        filename = f"banbhai-analytics-{days}d.csv"

    await update.effective_message.reply_document(
        document=InputFile(io.BytesIO(content), filename=filename),
        caption=f"📦 BanBhai analytics export ({days} days).",
    )


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin(update):
        return

    store = context.application.bot_data["phase3_store"]
    checks = []

    try:
        await store.get_settings(update.effective_chat.id)
        checks.append("✅ Supabase database")
        await store.log_health(
            "OK", "Manual health check",
            update.effective_chat.id,
        )
    except Exception as error:
        logger.exception("Database health check failed")
        checks.append("❌ Supabase database")
        try:
            await store.log_health(
                "ERROR", str(error),
                update.effective_chat.id,
            )
        except Exception:
            pass

    try:
        me = await context.bot.get_me()
        checks.append(f"✅ Telegram API (@{me.username})")
    except Exception:
        checks.append("❌ Telegram API")

    await update.effective_message.reply_text(
        "🏥 BanBhai Health\n\n" + "\n".join(checks)
    )


async def _admin(update):
    if not update.effective_chat or not update.effective_user:
        return False
    try:
        return await is_admin(update.effective_chat, update.effective_user.id)
    except Exception:
        return False
