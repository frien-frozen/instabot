"""Run Telegram bot alongside FastAPI."""

from __future__ import annotations

import asyncio
import logging
import re

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import Settings
from app.database import get_session_factory
from app.repositories.task_repository import TaskRepository
from app.telegram import admin
from app.utils.logging import get_logger, log_event

logger = get_logger(__name__)

_ID_CMD = re.compile(r"^/(enable|disable|toggle|delete)\s+(\d+)\s*$", re.I)


class TelegramBotRunner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._app: Application | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self._settings.telegram_enabled:
            log_event(logger, logging.INFO, "telegram_bot_disabled")
            return

        # Make sure core Instagram tasks exist before the admin opens the menu.
        factory = get_session_factory(self._settings)
        async with factory() as session:
            actions = await TaskRepository(session).ensure_defaults()
            await session.commit()
        if actions:
            log_event(logger, logging.INFO, "telegram_startup_tasks_repaired", actions=actions)

        self._app = (
            Application.builder()
            .token(self._settings.telegram_bot_token.strip())
            .build()
        )
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("menu", self._cmd_start))
        self._app.add_handler(CommandHandler("repair", self._cmd_repair))
        self._app.add_handler(CommandHandler("enable", self._cmd_enable))
        self._app.add_handler(CommandHandler("disable", self._cmd_disable))
        self._app.add_handler(CommandHandler("toggle", self._cmd_toggle))
        self._app.add_handler(CommandHandler("delete", self._cmd_delete))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        log_event(logger, logging.INFO, "telegram_bot_started")

    async def stop(self) -> None:
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
        log_event(logger, logging.INFO, "telegram_bot_stopped")

    async def _require_admin(self, update: Update) -> bool:
        if not update.effective_user or not update.message:
            return False
        if not admin.is_admin(self._settings, update.effective_user.id):
            await update.message.reply_text("Unauthorized.")
            return False
        return True

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_admin(update):
            return
        admin.clear_wizard(update.effective_chat.id)
        text = await admin.build_main_menu_text(self._settings)
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(admin.MAIN_KEYBOARD, resize_keyboard=True),
        )

    async def _cmd_repair(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_admin(update):
            return
        await update.message.reply_text(
            await admin.repair_tasks(self._settings),
            parse_mode="Markdown",
        )

    async def _cmd_enable(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._cmd_set_enabled(update, context, enabled=True)

    async def _cmd_disable(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._cmd_set_enabled(update, context, enabled=False)

    async def _cmd_set_enabled(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        enabled: bool,
    ) -> None:
        if not await self._require_admin(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: /enable <task_id> or /disable <task_id>")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Task id must be a number.")
            return
        await update.message.reply_text(
            await admin.set_task_enabled(self._settings, task_id, enabled),
            parse_mode="Markdown",
        )

    async def _cmd_toggle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_admin(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: /toggle <task_id>")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Task id must be a number.")
            return
        await update.message.reply_text(
            await admin.toggle_task(self._settings, task_id),
            parse_mode="Markdown",
        )

    async def _cmd_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_admin(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: /delete <task_id>")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Task id must be a number.")
            return
        factory = get_session_factory(self._settings)
        async with factory() as session:
            ok = await TaskRepository(session).delete(task_id)
            await session.commit()
        await update.message.reply_text("Deleted." if ok else "Task not found.")

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message or not update.message.text:
            return
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        if not admin.is_admin(self._settings, user_id):
            await update.message.reply_text("Unauthorized.")
            return

        text = update.message.text.strip()
        wizard = admin.get_wizard(chat_id)

        if wizard:
            await self._handle_wizard_step(update, wizard, text)
            return

        if text == "📋 Tasks":
            await update.message.reply_text(
                await admin.list_tasks_text(self._settings),
                parse_mode="Markdown",
            )
            return
        if text in ("🔧 Repair Tasks", "Repair Tasks"):
            await update.message.reply_text(
                await admin.repair_tasks(self._settings),
                parse_mode="Markdown",
            )
            return
        if text == "📊 Statistics":
            await update.message.reply_text(
                await admin.stats_text(self._settings),
                parse_mode="Markdown",
            )
            return
        if text == "📝 Logs":
            await update.message.reply_text("Check server logs for structured event entries.")
            return
        if text == "➕ Create Task":
            admin.set_wizard(chat_id, {"step": "reel_url"})
            await update.message.reply_text("Send the Reel or Post URL:")
            return

        match = _ID_CMD.match(text)
        if match:
            action, raw_id = match.group(1).lower(), int(match.group(2))
            if action == "delete":
                factory = get_session_factory(self._settings)
                async with factory() as session:
                    ok = await TaskRepository(session).delete(raw_id)
                    await session.commit()
                await update.message.reply_text("Deleted." if ok else "Task not found.")
            elif action == "toggle":
                await update.message.reply_text(
                    await admin.toggle_task(self._settings, raw_id),
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    await admin.set_task_enabled(
                        self._settings,
                        raw_id,
                        enabled=(action == "enable"),
                    ),
                    parse_mode="Markdown",
                )
            return

        await update.message.reply_text("Use the menu buttons or /start")

    async def _handle_wizard_step(self, update: Update, wizard: dict, text: str) -> None:
        chat_id = update.effective_chat.id
        step = wizard.get("step")

        if step == "reel_url":
            wizard["reel_url"] = text
            wizard["step"] = "public_reply_mode"
            await update.message.reply_text("Public reply: send *ai* or *fixed*", parse_mode="Markdown")
            return

        if step == "public_reply_mode":
            mode = text.lower()
            wizard["public_reply_mode"] = "fixed" if mode == "fixed" else "ai"
            if wizard["public_reply_mode"] == "fixed":
                wizard["step"] = "public_reply_fixed"
                await update.message.reply_text("Send the fixed public reply text:")
            else:
                wizard["step"] = "dm_mode"
                await update.message.reply_text("DM: send *ai* or *fixed*", parse_mode="Markdown")
            admin.set_wizard(chat_id, wizard)
            return

        if step == "public_reply_fixed":
            wizard["public_reply_fixed"] = text
            wizard["step"] = "dm_mode"
            admin.set_wizard(chat_id, wizard)
            await update.message.reply_text("DM: send *ai* or *fixed*", parse_mode="Markdown")
            return

        if step == "dm_mode":
            wizard["dm_mode"] = "fixed" if text.lower() == "fixed" else "ai"
            if wizard["dm_mode"] == "fixed":
                wizard["step"] = "dm_fixed"
                await update.message.reply_text("Send the fixed DM text:")
            else:
                wizard["step"] = "require_follow"
                await update.message.reply_text("Require follow? *yes* / *no*", parse_mode="Markdown")
            admin.set_wizard(chat_id, wizard)
            return

        if step == "dm_fixed":
            wizard["dm_fixed"] = text
            wizard["step"] = "require_follow"
            admin.set_wizard(chat_id, wizard)
            await update.message.reply_text("Require follow? *yes* / *no*", parse_mode="Markdown")
            return

        if step == "require_follow":
            wizard["require_follow"] = text.lower() in ("yes", "y", "true", "1")
            wizard["step"] = "require_like"
            admin.set_wizard(chat_id, wizard)
            await update.message.reply_text("Require like? *yes* / *no*", parse_mode="Markdown")
            return

        if step == "require_like":
            wizard["require_like"] = text.lower() in ("yes", "y", "true", "1")
            admin.set_wizard(chat_id, wizard)
            try:
                msg = await admin.create_reel_task(self._settings, chat_id, wizard)
                await update.message.reply_text(msg, parse_mode="Markdown")
            except Exception as exc:
                admin.clear_wizard(chat_id)
                await update.message.reply_text(f"Failed: {exc}")
