import os
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from ghostea.config import (
    BLOCKED_DOMAINS_FILE,
    DATA_FILE,
    FILTERS_FILE,
    SPAM_PATTERNS_FILE,
    SUPABASE_KEY,
    SUPABASE_URL,
    TOKEN,
    DEFAULT_SPAM_MESSAGE_LIMIT,
    DEFAULT_SPAM_WINDOW_SECONDS,
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
from ghostea.handlers.moderation import (
    ban_command, mute_command, reloadfilters_command,
    resetwarnings_command, unban_command, unmute_command,
    unwarn_command, warn_command,
)
from ghostea.handlers.settings import (
    addfilter_command, delfilter_command, filters_command,
    logs_command, setflood_command, setfloodmute_command,
    setlinkaction_command, setmute1_command, setmute2_command,
    setwarnlimit_command, toggle_command,
)
from ghostea.handlers.phase4 import (
    handle_new_members, raidmode_command, setraid_command,
    stats_command, welcome_command,
)
from ghostea.handlers.phase5 import (
    reputation_command, setmaxmsg_command,
    setverification_command, verification_command, verify_callback,
)
from ghostea.handlers.phase6 import analytics_command, export_command, health_command
from ghostea.logging_config import setup_logging
from ghostea.services.analytics_service import AnalyticsService
from ghostea.services.moderation_engine import ModerationEngine
from ghostea.services.phase3_moderation import Phase3ModerationService
from ghostea.services.protection_service import ProtectionService
from ghostea.services.verification_service import VerificationService
from ghostea.services.security_service import SecurityService
from ghostea.services.user_management_service import UserManagementService
from ghostea.storage.database import SupabaseREST
from ghostea.storage.phase3_store import Phase3Store
from ghostea.web_server import start_web_server


def create_application():
    logger = setup_logging()

    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured.")
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY are required."
        )
    if not os.getenv("DASHBOARD_API_KEY", "").strip():
        raise RuntimeError("DASHBOARD_API_KEY is required.")
    if not os.getenv("DASHBOARD_ORIGIN", "").strip():
        raise RuntimeError("DASHBOARD_ORIGIN is required.")

    abuse_filter = AbuseFilter(FILTERS_FILE)
    spam_patterns = LineList(SPAM_PATTERNS_FILE)
    blocked_domains = LineList(BLOCKED_DOMAINS_FILE)

    db = SupabaseREST(SUPABASE_URL, SUPABASE_KEY)
    store = Phase3Store(db)
    protection = ProtectionService(
        spam_patterns,
        blocked_domains,
        DEFAULT_SPAM_WINDOW_SECONDS,
        DEFAULT_SPAM_MESSAGE_LIMIT,
    )
    moderation = Phase3ModerationService(store)
    moderation_engine = ModerationEngine(abuse_filter, protection)
    analytics = AnalyticsService(store)
    verification = VerificationService(store)
    security = SecurityService(store, None)  # bot is attached after Application creation

    async def post_init(application):
        try:
            from ghostea.storage.migration import migrate_json_if_needed
            count = await migrate_json_if_needed(store, DATA_FILE)
            if count:
                logger.info("Migrated %s warning records.", count)
        except Exception:
            logger.exception("Warning migration failed")

        # Persistent security recovery: raid locks and verification expiry.
        try:
            security.bot = application.bot
            await security.start()
            application.bot_data["security"] = security
            logger.info("Ghostea security recovery started.")
        except Exception:
            logger.exception("Security recovery failed")
            raise

        # Render health/API server.
        try:
            server = start_web_server(store, analytics, application.bot)
            application.bot_data["web_server"] = server
            logger.info("Ghostea web server started.")
        except Exception:
            logger.exception("Web server failed to start")
            raise

    async def post_shutdown(application):
        try:
            await security.stop()
        except Exception:
            logger.exception("Security service shutdown failed")

        server = application.bot_data.get("web_server")
        if server:
            server.shutdown()
            server.server_close()

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    user_management = UserManagementService(store, application.bot)

    application.bot_data.update({
        "abuse_filter": abuse_filter,
        "spam_patterns": spam_patterns,
        "blocked_domains": blocked_domains,
        "protection": protection,
        "phase3_store": store,
        "phase3_moderation": moderation,
        "analytics": analytics,
        "user_management": user_management,
        "verification": verification,
        "security": security,
        "moderation": moderation,
        "moderation_engine": moderation_engine,
    })

    # Core
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("warnings", warnings_command))
    application.add_handler(CommandHandler("history", history_command))

    # Moderation
    for command, callback in {
        "warn": warn_command, "unwarn": unwarn_command,
        "resetwarnings": resetwarnings_command, "ban": ban_command,
        "unban": unban_command, "mute": mute_command,
        "unmute": unmute_command, "reloadfilters": reloadfilters_command,
    }.items():
        application.add_handler(CommandHandler(command, callback))

    # Settings
    for command, callback in {
        "settings": settings_command, "setwarnlimit": setwarnlimit_command,
        "setmute1": setmute1_command, "setmute2": setmute2_command,
        "setflood": setflood_command, "setfloodmute": setfloodmute_command,
        "setlinkaction": setlinkaction_command, "toggle": toggle_command,
        "addfilter": addfilter_command, "delfilter": delfilter_command,
        "filters": filters_command, "logs": logs_command,
    }.items():
        application.add_handler(CommandHandler(command, callback))

    # Phase 4
    for command, callback in {
        "raidmode": raidmode_command, "setraid": setraid_command,
        "welcome": welcome_command, "stats": stats_command,
    }.items():
        application.add_handler(CommandHandler(command, callback))

    # Phase 5
    for command, callback in {
        "verification": verification_command,
        "setverification": setverification_command,
        "setmaxmsg": setmaxmsg_command,
        "reputation": reputation_command,
    }.items():
        application.add_handler(CommandHandler(command, callback))
    application.add_handler(
        CallbackQueryHandler(verify_callback, pattern=r"^verify:")
    )

    # Phase 6
    for command, callback in {
        "analytics": analytics_command,
        "export": export_command,
        "health": health_command,
    }.items():
        application.add_handler(CommandHandler(command, callback))

    # Join events must be registered before the generic message handler.
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members)
    )
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, check_message)
    )

    async def error_handler(update, context):
        logger.error("Unhandled bot error: %s", context.error, exc_info=True)

    application.add_error_handler(error_handler)
    return application


def run():
    application = create_application()
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )
