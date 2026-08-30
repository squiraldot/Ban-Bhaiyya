from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from ghostea.config import (
    BLOCKED_DOMAINS_FILE,
    BLOCKED_LINK_ACTION,
    DATA_FILE,
    FILTERS_FILE,
    SPAM_MESSAGE_LIMIT,
    SPAM_MUTE_MINUTES,
    SPAM_PATTERNS_FILE,
    SPAM_WINDOW_SECONDS,
    SUPABASE_KEY,
    SUPABASE_URL,
    TOKEN,
)
from ghostea.filters.line_loader import LineList
from ghostea.filters.loader import AbuseFilter
from ghostea.handlers.common import (
    history_command,
    settings_command,
    start_command,
    warnings_command,
)
from ghostea.handlers.messages import check_message
from ghostea.handlers.phase4 import (
    handle_new_members,
    raidmode_command,
    setraid_command,
    stats_command,
    welcome_command,
)
from ghostea.handlers.moderation import (
    ban_command, mute_command, reloadfilters_command,
    resetwarnings_command, unban_command, unmute_command,
    unwarn_command, warn_command
)
from ghostea.handlers.settings import (
    addfilter_command,
    delfilter_command,
    filters_command,
    logs_command,
    setflood_command,
    setfloodmute_command,
    setlinkaction_command,
    setmute1_command,
    setmute2_command,
    setwarnlimit_command,
    toggle_command,
)
from ghostea.handlers.phase5 import (
    reputation_command,
    setmaxmsg_command,
    setverification_command,
    verification_command,
    verify_callback,
)
from ghostea.handlers.phase6 import (
    analytics_command,
    export_command,
    health_command,
)
from ghostea.logging_config import setup_logging
from ghostea.services.phase3_moderation import Phase3ModerationService
from ghostea.services.analytics_service import AnalyticsService
from ghostea.services.protection_service import ProtectionService
from ghostea.storage.database import SupabaseREST
from ghostea.storage.phase3_store import Phase3Store


def create_application():
    logger = setup_logging()

    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN is not configured.")

    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "Phase 3 requires SUPABASE_URL and SUPABASE_KEY. "
            "Configure them in Termux/server environment variables."
        )

    abuse_filter = AbuseFilter(FILTERS_FILE)
    spam_patterns = LineList(SPAM_PATTERNS_FILE)
    blocked_domains = LineList(BLOCKED_DOMAINS_FILE)

    db = SupabaseREST(SUPABASE_URL, SUPABASE_KEY)
    store = Phase3Store(db)

    protection = ProtectionService(
        spam_patterns,
        blocked_domains,
        SPAM_WINDOW_SECONDS,
        SPAM_MESSAGE_LIMIT,
    )
    moderation = Phase3ModerationService(store)
    analytics = AnalyticsService(store)

    from ghostea.services.verification_service import VerificationService
    verification = VerificationService(store)

    app = Application.builder().token(TOKEN).build()

    app.bot_data.update({
        "abuse_filter": abuse_filter,
        "spam_patterns": spam_patterns,
        "blocked_domains": blocked_domains,
        "protection": protection,
        "phase3_store": store,
        "phase3_moderation": moderation,
        "analytics": analytics,
        "verification": verification,
        # Compatibility aliases for Phase-1 commands.
        "moderation": moderation,
        "block_link_action": BLOCKED_LINK_ACTION,
        "spam_mute_minutes": SPAM_MUTE_MINUTES,
    })

    # Core
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("warnings", warnings_command))
    app.add_handler(CommandHandler("history", history_command))

    # Phase 1 admin moderation
    app.add_handler(CommandHandler("warn", warn_command))
    app.add_handler(CommandHandler("unwarn", unwarn_command))
    app.add_handler(CommandHandler("resetwarnings", resetwarnings_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("mute", mute_command))
    app.add_handler(CommandHandler("unmute", unmute_command))
    app.add_handler(CommandHandler("reloadfilters", reloadfilters_command))

    # Phase 3 group settings
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("setwarnlimit", setwarnlimit_command))
    app.add_handler(CommandHandler("setmute1", setmute1_command))
    app.add_handler(CommandHandler("setmute2", setmute2_command))
    app.add_handler(CommandHandler("setflood", setflood_command))
    app.add_handler(CommandHandler("setfloodmute", setfloodmute_command))
    app.add_handler(CommandHandler("setlinkaction", setlinkaction_command))
    app.add_handler(CommandHandler("toggle", toggle_command))

    # Phase 3 custom filters + logs
    app.add_handler(CommandHandler("addfilter", addfilter_command))
    app.add_handler(CommandHandler("delfilter", delfilter_command))
    app.add_handler(CommandHandler("filters", filters_command))
    app.add_handler(CommandHandler("logs", logs_command))

    # Phase 6
    app.add_handler(CommandHandler("analytics", analytics_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("health", health_command))

    # Phase 5
    app.add_handler(CommandHandler("verification", verification_command))
    app.add_handler(CommandHandler("setverification", setverification_command))
    app.add_handler(CommandHandler("setmaxmsg", setmaxmsg_command))
    app.add_handler(CommandHandler("reputation", reputation_command))
    app.add_handler(CallbackQueryHandler(verify_callback, pattern=r"^verify:"))

    # Phase 4
    app.add_handler(CommandHandler("raidmode", raidmode_command))
    app.add_handler(CommandHandler("setraid", setraid_command))
    app.add_handler(CommandHandler("welcome", welcome_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members)
    )

    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, check_message)
    )

    async def post_init(application):
        from ghostea.storage.migration import migrate_json_if_needed
        try:
            count = await migrate_json_if_needed(store, DATA_FILE)
            if count:
                logger.info("Migrated %s warning records from warnings.json", count)
        except Exception as error:
            logger.exception("Warning migration failed: %s", error)

    async def error_handler(update, context):
        logger.exception("Unhandled bot error: %s", context.error)

    app.add_error_handler(error_handler)
    app.post_init = post_init

    return app
