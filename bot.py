import os
import telebot
import time
import random
import string
import logging
import threading
import requests
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from supabase import create_client

# نصب خودکار jdatetime
try:
    import jdatetime
except ImportError:
    print("📦 در حال نصب jdatetime...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "jdatetime"])
    import jdatetime
    print("✅ jdatetime نصب شد!")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log", encoding="utf-8")]
)
log = logging.getLogger("VpnIrBot")

# ============================================================
# تنظیمات اولیه (از متغیرهای محیطی)
# ============================================================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 8356825459))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@Vpn_IRan140")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/Vpn_IRan140")
WEBSITE = os.getenv("WEBSITE", "https://vpnir.netlify.app")
SUPPORT_ID = os.getenv("SUPPORT_ID", "@ad_vpnir")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# اگر کلیدها وجود نداشت، خطا بده
if not TOKEN:
    print("❌ TOKEN تنظیم نشده!")
    exit(1)
if not SUPABASE_KEY:
    print("❌ SUPABASE_KEY تنظیم نشده!")
    exit(1)

print(f"🔑 تلاش برای اتصال به Supabase...")
print(f"📡 URL: {SUPABASE_URL}")

try:
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL تنظیم نشده است")
    db = create_client(SUPABASE_URL, SUPABASE_KEY)
    db.table("app_users").select("telegram_id").limit(1).execute()
    print("✅ اتصال به Supabase برقرار شد!")
except Exception as e:
    print(f"❌ خطا در اتصال به Supabase: {e}")
    raise SystemExit(1)

CARD_NUMBER = os.getenv("CARD_NUMBER", "6280231392863212")
CARD_OWNER = os.getenv("CARD_OWNER", "امیرحسین صراف زاده")
BANK_NAME = os.getenv("BANK_NAME", "بانک مسکن")
SHABA_NUMBER = os.getenv("SHABA_NUMBER", "IR620140040004110181136923")
SHABA_LIMIT = int(os.getenv("SHABA_LIMIT", 15_000_000))

bot = telebot.TeleBot(TOKEN)
user_conversations = {}
_bot_username = None
_checkout_cache = {}
_discount_builder = {}
_wallet_lock = threading.RLock()
_order_lock = threading.RLock()
_receipt_order_cache = {}

# ============================================================
# توابع کمکی تاریخ
# ============================================================
def to_jalali(date_str):
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        jalali = jdatetime.datetime.fromgregorian(datetime=dt)
        return jalali.strftime("%Y/%m/%d %H:%M:%S")
    except:
        return date_str

def now_jalali():
    now = jdatetime.datetime.now()
    return f"{now.strftime('%Y/%m/%d')} → ⏰ {now.strftime('%H:%M:%S')}"

def get_bot_username():
    global _bot_username
    if _bot_username is None:
        _bot_username = bot.get_me().username
    return _bot_username

# ============================================================
# قیمت‌ها و تنظیمات
# ============================================================
FRANCE_PRICE_PER_GB = 6000
FRANCE_MIN_GB = 5
FRANCE_MAX_GB = 200
FRANCE_MIN_DAYS = 1
FRANCE_MAX_DAYS = 365

VPN_PRICE_PER_GB = 15000
VPN_MIN_GB = 15
VPN_MAX_GB = 100

STARS_PRICE = 4000
STARS_MIN = 50
STARS_MAX = 10_000_000

UNLIMITED_PRICE = 399000
UNLIMITED_DOWNTIME_NOTE = "در روز حداکثر ۱۰ تا ۲۰ دقیقه قطعی داره."
REFERRAL_PERCENT = 10

VPN_PLAN_CONFIG = {
    "france": {
        "label": "🇫🇷 سرور فرانسه", "price_per_gb": FRANCE_PRICE_PER_GB,
        "min_gb": FRANCE_MIN_GB, "max_gb": FRANCE_MAX_GB, "fixed_days": None,
        "desc": "زیر قیمت کل بازار، همراه با پشتیبانی\nفقط لوکیشن فرانسه\nمدت دلخواه: چند روز تا یک سال"
    },
    "multi": {
        "label": "🌍 سرور مولتی (۱۸ کشور)", "price_per_gb": VPN_PRICE_PER_GB,
        "min_gb": VPN_MIN_GB, "max_gb": VPN_MAX_GB, "fixed_days": 30,
        "desc": "اتصال از بین ۱۸ کشور مختلف"
    }
}

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def gen_code(length=6):
    return "".join(random.choice(CODE_CHARS) for _ in range(length))

def gen_tracking_code(prefix):
    return f"{prefix}-{gen_code(6)}"

def get_expiry_date(days=30):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M UTC")

def get_payment_info(amount):
    if amount >= SHABA_LIMIT:
        return f"""💳 لطفاً مبلغ رو به شبا زیر واریز کن:
<code>{SHABA_NUMBER}</code>
👤 {CARD_OWNER}
🏦 {BANK_NAME}
⚠️ مبلغ بالای ۱۵ میلیون، فقط از طریق شبا قابل پرداخت است."""
    return f"""💳 لطفاً مبلغ رو به کارت زیر واریز کن:
<code>{CARD_NUMBER}</code>
👤 {CARD_OWNER}
🏦 {BANK_NAME}"""

# ============================================================
# دیتابیس - کاربران
# ============================================================
_user_cache = {}

def get_user(telegram_id, force_refresh=False):
    if not force_refresh and telegram_id in _user_cache:
        return _user_cache[telegram_id]
    try:
        res = db.table("app_users").select("*").eq("telegram_id", telegram_id).execute()
        user = res.data[0] if res.data else None
        if user:
            if "user_level" not in user or not user["user_level"]:
                user["user_level"] = "عادی"
            if "is_active" not in user:
                user["is_active"] = False
            _user_cache[telegram_id] = user
        return user
    except Exception as e:
        log.error(f"خطا در get_user: {e}")
        return None

def get_user_by_referral_code(code):
    try:
        res = db.table("app_users").select("*").eq("referral_code", code).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        log.error(f"خطا در get_user_by_referral_code: {e}")
        return None

def create_or_update_user(telegram_id, username, start_payload=None):
    existing = get_user(telegram_id)
    if existing:
        if username and existing.get("username") != username:
            try:
                db.table("app_users").update({"username": username}).eq("telegram_id", telegram_id).execute()
                existing["username"] = username
            except Exception as e:
                log.error(f"خطا در به‌روزرسانی کاربر: {e}")
        return existing

    referred_by = None
    if start_payload:
        ref_user = get_user_by_referral_code(start_payload.strip().upper())
        if ref_user and ref_user["telegram_id"] != telegram_id:
            referred_by = ref_user["telegram_id"]

    while True:
        code = gen_code(6)
        if not get_user_by_referral_code(code):
            break

    row = {
        "telegram_id": telegram_id,
        "username": username,
        "wallet_balance": 0,
        "referral_code": code,
        "referred_by": referred_by,
        "is_banned": False,
        "is_active": False,
        "user_level": "عادی",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    try:
        db.table("app_users").insert(row).execute()
        _user_cache[telegram_id] = row
        return row
    except Exception as e:
        log.error(f"خطا در ایجاد کاربر: {e}")
        return None

def ensure_user_exists(telegram_id, username=None):
    user = get_user(telegram_id)
    if not user:
        user = create_or_update_user(telegram_id, username)
    return user

def is_banned(telegram_id):
    u = get_user(telegram_id)
    return bool(u and u.get("is_banned"))

def set_banned(telegram_id, banned):
    try:
        db.table("app_users").update({"is_banned": banned}).eq("telegram_id", telegram_id).execute()
        if telegram_id in _user_cache:
            _user_cache[telegram_id]["is_banned"] = banned
    except Exception as e:
        log.error(f"خطا در set_banned: {e}")

def adjust_wallet(telegram_id, delta, reason, ref_order_id=None):
    """Conditional wallet update with per-process locking and idempotency check."""
    telegram_id = int(telegram_id)
    delta = int(delta)
    with _wallet_lock:
        try:
            if ref_order_id is not None:
                existing = (db.table("wallet_transactions").select("id")
                            .eq("telegram_id", telegram_id)
                            .eq("ref_order_id", int(ref_order_id))
                            .eq("reason", reason).limit(1).execute())
                if existing.data:
                    user = get_user(telegram_id, force_refresh=True)
                    return int(user.get("wallet_balance") or 0) if user else None
            for attempt in range(5):
                user = get_user(telegram_id, force_refresh=True)
                if not user:
                    return None
                current = int(user.get("wallet_balance") or 0)
                new_balance = current + delta
                if new_balance < 0:
                    return None
                updated = (db.table("app_users").update({"wallet_balance": new_balance})
                           .eq("telegram_id", telegram_id).eq("wallet_balance", current).execute())
                if not updated.data:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                try:
                    db.table("wallet_transactions").insert({
                        "telegram_id": telegram_id, "amount": delta, "reason": reason,
                        "ref_order_id": ref_order_id
                    }).execute()
                except Exception as ledger_error:
                    rollback = (db.table("app_users").update({"wallet_balance": current})
                                .eq("telegram_id", telegram_id).eq("wallet_balance", new_balance).execute())
                    if not rollback.data:
                        log.critical(f"WALLET INCONSISTENCY user={telegram_id} delta={delta} order={ref_order_id}")
                    raise ledger_error
                _user_cache.pop(telegram_id, None)
                return new_balance
            return None
        except Exception as e:
            log.error(f"خطا در adjust_wallet: {e}")
            return None

# ============================================================
# سفارش‌ها و کد تخفیف
# ============================================================
def create_order(telegram_id, product, base_amount, final_amount, order_type, tracking_prefix,
                 discount_code=None, pay_method="card", gb=None, days=None, plan=None, status="pending"):
    """Create an order using the schema already used by this bot."""
    for _ in range(5):
        tracking_code = gen_tracking_code(tracking_prefix)
        row = {
            "telegram_id": telegram_id,
            "product": product,
            "base_amount": int(base_amount),
            "final_amount": int(final_amount),
            "type": order_type,
            "tracking_code": tracking_code,
            "discount_code": discount_code,
            "pay_method": pay_method,
            "gb": gb,
            "days": days,
            "plan": plan,
            "status": status,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        try:
            res = db.table("orders").insert(row).execute()
            if res.data:
                return res.data[0]
        except Exception as e:
            # Retry only for likely tracking-code collisions.
            log.warning(f"خطا در ساخت سفارش: {e}")
    return None

def get_order(order_id):
    try:
        res = db.table("orders").select("*").eq("id", int(order_id)).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        log.error(f"خطا در get_order: {e}")
        return None

def get_latest_pending_order(telegram_id):
    try:
        res = (db.table("orders").select("*")
               .eq("telegram_id", telegram_id)
               .eq("status", "pending")
               .order("created_at", desc=True).limit(1).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        log.error(f"خطا در get_latest_pending_order: {e}")
        return None

def get_pending_orders(telegram_id, limit=10):
    try:
        res = (db.table("orders").select("*").eq("telegram_id", int(telegram_id))
               .eq("status", "pending").order("created_at", desc=True).limit(limit).execute())
        return res.data or []
    except Exception as e:
        log.error(f"خطا در get_pending_orders: {e}")
        return []

def update_order_status(order_id, status, server_info=None, expected_status=None):
    try:
        payload = {"status": status}
        if server_info is not None:
            payload["server_info"] = server_info
        q = db.table("orders").update(payload).eq("id", int(order_id))
        if expected_status is not None:
            q = q.eq("status", expected_status)
        return bool(q.execute().data)
    except Exception as e:
        log.error(f"خطا در update_order_status: {e}")
        return False

def check_discount_code(code, plan_key=None):
    code = (code or "").strip().upper()
    if not code:
        return None, "❌ کد تخفیف خالی است."
    try:
        res = db.table("discount_codes").select("*").eq("code", code).limit(1).execute()
        if not res.data:
            return None, "❌ کد تخفیف پیدا نشد."
        discount = res.data[0]
        if not discount.get("active", False):
            return None, "❌ این کد تخفیف غیرفعال است."
        if discount.get("expires_at"):
            try:
                expires = datetime.fromisoformat(str(discount["expires_at"]).replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires <= datetime.now(timezone.utc):
                    return None, "❌ اعتبار این کد تخفیف تمام شده است."
            except ValueError:
                return None, "❌ تاریخ اعتبار کد تخفیف نامعتبر است."
        max_uses = discount.get("max_uses")
        used = int(discount.get("used_count") or 0)
        if max_uses is not None and used >= int(max_uses):
            return None, "❌ ظرفیت استفاده از این کد تمام شده است."
        discount_plan = discount.get("plan") or "all"
        if discount_plan != "all" and discount_plan != plan_key:
            return None, "❌ این کد برای این سرویس قابل استفاده نیست."
        percent = int(discount.get("percent") or 0)
        if not 1 <= percent <= 100:
            return None, "❌ درصد تخفیف این کد نامعتبر است."
        return discount, None
    except Exception as e:
        log.error(f"خطا در check_discount_code: {e}")
        return None, "❌ خطا در بررسی کد تخفیف."

def consume_discount_code(code):
    """Increment use count. Idempotency is enforced by callers before confirmation."""
    code = (code or "").strip().upper()
    if not code:
        return False
    try:
        res = db.table("discount_codes").select("*").eq("code", code).limit(1).execute()
        if not res.data:
            return False
        d = res.data[0]
        used = int(d.get("used_count") or 0)
        max_uses = d.get("max_uses")
        if not d.get("active", False) or (max_uses is not None and used >= int(max_uses)):
            return False
        upd = (db.table("discount_codes").update({"used_count": used + 1})
               .eq("code", code).eq("used_count", used).execute())
        return bool(upd.data)
    except Exception as e:
        log.error(f"خطا در consume_discount_code: {e}")
        return False

def process_referral_commission(order):
    """Credit referral commission once per order."""
    try:
        if not order or order.get("type") == "wallet_topup":
            return False
        buyer = get_user(int(order["telegram_id"]))
        if not buyer or not buyer.get("referred_by"):
            return False
        referrer_id = int(buyer["referred_by"])
        # Prevent duplicate commission using the transaction reference.
        existing = (db.table("wallet_transactions").select("id")
                    .eq("telegram_id", referrer_id)
                    .eq("ref_order_id", order["id"]).eq("reason", "referral_commission")
                    .limit(1).execute())
        if existing.data:
            return True
        commission = int(round(int(order["final_amount"]) * REFERRAL_PERCENT / 100))
        if commission <= 0:
            return True
        return adjust_wallet(referrer_id, commission, "referral_commission", ref_order_id=order["id"]) is not None
    except Exception as e:
        log.error(f"خطا در process_referral_commission: {e}")
        return False

# ============================================================
# کیبوردها
# ============================================================
def main_keyboard():
    keyboard = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    keyboard.add("🛒 خرید VPN", "⭐ خرید استارز")
    keyboard.add("👛 کیف پول من", "🎁 رفرال من")
    keyboard.add("📦 سفارش‌های من", "📞 پشتیبانی")
    keyboard.add("👤 حساب من")
    return keyboard

def admin_keyboard():
    keyboard = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    keyboard.add("📋 سفارشات در انتظار", "👥 لیست کاربران")
    keyboard.add("📨 پیام همگانی", "📊 آمار فروش")
    keyboard.add("🚫 بن/آنبن کاربر", "📤 تحویل سرور")
    keyboard.add("⭐ تعیین سطح کاربر", "🏷 مدیریت کد تخفیف")
    keyboard.add("💰 مدیریت موجودی", "🔙 برگشت")
    return keyboard

def discount_management_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("➕ ساخت کد جدید", callback_data="discount_create"),
        InlineKeyboardButton("📋 لیست کدها", callback_data="discount_list"),
        InlineKeyboardButton("📤 ارسال به کاربران", callback_data="discount_broadcast"),
        InlineKeyboardButton("🔙 برگشت", callback_data="back")
    )
    return keyboard

def vpn_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("🇫🇷 سرور فرانسه (۶,۰۰۰ تومان/گیگ)", callback_data="vpn_buy_france"))
    keyboard.add(InlineKeyboardButton("🌍 سرور مولتی ۱۸ کشور (۱۵,۰۰۰ تومان/گیگ)", callback_data="vpn_buy_multi"))
    keyboard.add(InlineKeyboardButton("🚀 سرور نامحدود (۳۹۹,۰۰۰ تومان)", callback_data="vpn_unlimited"))
    keyboard.add(InlineKeyboardButton("🔙 برگشت", callback_data="back"))
    return keyboard

def confirm_keyboard(order_id):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✅ تایید", callback_data=f"confirm_order_{order_id}"),
        InlineKeyboardButton("❌ رد", callback_data=f"reject_order_{order_id}")
    )
    return keyboard

def cancel_payment_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="cancel_payment"))
    return keyboard

def channel_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("📢 عضویت در کانال", url=CHANNEL_LINK))
    keyboard.add(InlineKeyboardButton("✅ عضویت را تایید کردم", callback_data="check_membership"))
    return keyboard

def stars_type_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("👤 خودم", callback_data="stars_self"),
        InlineKeyboardButton("👥 شخص دیگر", callback_data="stars_other")
    )
    keyboard.add(InlineKeyboardButton("🔙 برگشت", callback_data="back"))
    return keyboard

def support_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("📋 سوالات متداول", callback_data="faq"),
        InlineKeyboardButton("🤖 پشتیبانی هوشمند", callback_data="support_ai"),
        InlineKeyboardButton("👤 ارتباط با ادمین", callback_data="support_admin")
    )
    keyboard.add(InlineKeyboardButton("🔙 برگشت", callback_data="back"))
    return keyboard

def ban_unban_keyboard(target_id):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🚫 بن کن", callback_data=f"doban_{target_id}"),
        InlineKeyboardButton("✅ آنبن کن", callback_data=f"unban_{target_id}")
    )
    return keyboard

def discount_prompt_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🏷 دارم", callback_data="discount_yes"),
        InlineKeyboardButton("➡️ ندارم، رد شو", callback_data="discount_no")
    )
    return keyboard

def payment_method_keyboard(can_use_wallet):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("💳 پرداخت کارتی", callback_data="pay_card"))
    if can_use_wallet:
        keyboard.add(InlineKeyboardButton("👛 پرداخت از کیف پول", callback_data="pay_wallet"))
    keyboard.add(InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="cancel_payment"))
    return keyboard

FAQ_TEXT = f"""📋 <b>سوالات متداول</b>
━━━━━━━━━━━━━━

❓ <b>سرور فرانسه و مولتی چه فرقی دارن؟</b>

🇫🇷 <b>فرانسه</b>
• ۶,۰۰۰ تومان/گیگ
• ۵ تا ۲۰۰ گیگ
• مدت دلخواه (چند روز تا ۱ سال)

🌍 <b>مولتی (۱۸ کشور)</b>
• ۱۵,۰۰۰ تومان/گیگ
• ۱۵ تا ۱۰۰ گیگ
• مدت ثابت ۳۰ روز

━━━━━━━━━━━━━━

❓ <b>سرور نامحدود چطوره؟</b>
حجم نامحدود، ۳۰ روزه، ۳۹۹,۰۰۰ تومان.
{UNLIMITED_DOWNTIME_NOTE}

❓ <b>کیف پول چیه؟</b>
حساب رو شارژ می‌کنی و از موجودیش برای خرید استفاده می‌کنی — بدون واریز دستی هر بار.

❓ <b>رفرال چطور کار می‌کنه؟</b>
به‌ازای هر خرید تایید‌شده‌ی زیرمجموعه‌ت، {REFERRAL_PERCENT}٪ به کیف پولت اضافه می‌شه.

❓ <b>روش‌های پرداخت چیه؟</b>
کارت‌به‌کارت، شبا (بالای ۱۵ میلیون تومان)، یا از کیف پول.

❓ <b>پشتیبانی از کجا؟</b>
{SUPPORT_ID}"""

AI_SYSTEM_PROMPT = f"""شما یک پشتیبان صمیمی و دقیق برای ربات VPN IR هستید.

📌 قیمت‌ها (به تومان):
1️⃣ سرور فرانسه: هر گیگ = ۶,۰۰۰ تومان (۵ تا ۲۰۰ گیگ)
2️⃣ سرور مولتی: هر گیگ = ۱۵,۰۰۰ تومان (۱۵ تا ۱۰۰ گیگ)
3️⃣ سرور نامحدود: ۳۹۹,۰۰۰ تومان
4️⃣ استارز: هر عدد = ۴,۰۰۰ تومان

💡 امکانات: کیف پول، رفرال ({REFERRAL_PERCENT}٪)، کد تخفیف

پاسخ‌ها کوتاه، دقیق و صمیمی باشن."""

def ask_ai(user_id, question):
    try:
        history = user_conversations.setdefault(user_id, [])
        history.append({"role": "user", "content": question})
        if len(history) > 10:
            del history[:-10]
        messages = [{"role": "system", "content": AI_SYSTEM_PROMPT}] + history
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json={"model": "openai/gpt-3.5-turbo", "messages": messages},
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"]
        history.append({"role": "assistant", "content": answer})
        return answer
    except requests.exceptions.RequestException as e:
        log.warning(f"خطا در AI: {e}")
        return "❌ خطا در ارتباط با هوش مصنوعی. کمی بعد دوباره امتحان کن."
    except (KeyError, IndexError) as e:
        log.error(f"پاسخ غیرمنتظره از AI: {e}")
        return "❌ خطا در پردازش پاسخ."

def safe_edit(chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup, parse_mode=parse_mode)
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" not in str(e):
            log.warning(f"edit failed: {e}")

_membership_cache = {}
MEMBERSHIP_CACHE_SECONDS = 2

def is_member(user_id, force_refresh=False):
    """Check channel membership with a short cache and useful Telegram errors."""
    now = time.time()

    if not force_refresh:
        cached = _membership_cache.get(user_id)
        if cached and cached[1] > now:
            return cached[0]

    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        status = getattr(member, "status", None)

        # Normal member/admin/owner
        if status in ("member", "administrator", "creator"):
            result = True

        # Telegram can return restricted for a member whose permissions
        # are limited. They are still a channel member if not kicked/left.
        elif status == "restricted":
            result = not bool(getattr(member, "is_member", False)) is False
            # Prefer Telegram's explicit is_member field when available.
            result = bool(getattr(member, "is_member", False))

        else:
            result = False

        _membership_cache[user_id] = (
            result,
            now + MEMBERSHIP_CACHE_SECONDS
        )
        return result

    except telebot.apihelper.ApiTelegramException as e:
        # IMPORTANT: don't silently convert every Telegram error to
        # "user is not a member". Log the real reason.
        log.error(
            "Membership check failed | user_id=%s | channel=%s | "
            "error_code=%s | description=%s",
            user_id,
            CHANNEL_ID,
            getattr(e, "error_code", None),
            getattr(e, "description", str(e)),
        )
        _membership_cache[user_id] = (False, now + MEMBERSHIP_CACHE_SECONDS)
        return False

    except Exception:
        log.exception(
            "Unexpected membership check error | user_id=%s | channel=%s",
            user_id,
            CHANNEL_ID,
        )
        _membership_cache[user_id] = (False, now + MEMBERSHIP_CACHE_SECONDS)
        return False


def welcome_text(first_name):
    return f"""👋 <b>سلام {first_name} عزیز، خوش اومدی!</b>

🇮🇷 <b>ربات VPN IR</b>
━━━━━━━━━━━━━━
🇫🇷 سرور فرانسه (زیر قیمت بازار)
🌍 سرور مولتی (۱۸ کشور)
🚀 سرور نامحدود
⭐ فروش استارز تلگرام
👛 کیف پول، رفرال و کد تخفیف
⚡️ تحویل فوری | پشتیبانی ۲۴/۷
━━━━━━━━━━━━━━

📢 کانال: {CHANNEL_ID}
🌍 سایت: {WEBSITE}

از دکمه‌های پایین شروع کن 👇"""

ADMIN_WELCOME_TEXT = """👋 <b>سلام ادمین عزیز، خوش اومدی!</b>

🇮🇷 <b>پنل مدیریت VPN IR</b>
━━━━━━━━━━━━━━
📋 مدیریت سفارشات
👥 مدیریت کاربران
📨 پیام همگانی
📊 آمار فروش
📤 تحویل سرور
⭐ تعیین سطح کاربر
🏷 مدیریت کد تخفیف
💰 مدیریت موجودی
━━━━━━━━━━━━━━

از دکمه‌های پایین استفاده کن 👇"""

def send_home(chat_id, user_id, first_name):
    if user_id == ADMIN_ID:
        bot.send_message(chat_id, ADMIN_WELCOME_TEXT, reply_markup=admin_keyboard(), parse_mode="HTML")
    else:
        bot.send_message(chat_id, welcome_text(first_name), reply_markup=main_keyboard(), parse_mode="HTML")

ALL_MENU_BUTTON_TEXTS = {
    "🛒 خرید VPN", "⭐ خرید استارز", "👛 کیف پول من", "🎁 رفرال من",
    "📦 سفارش‌های من", "📞 پشتیبانی", "👤 حساب من",
    "📋 سفارشات در انتظار", "👥 لیست کاربران", "📨 پیام همگانی",
    "📊 آمار فروش", "🚫 بن/آنبن کاربر", "📤 تحویل سرور", 
    "⭐ تعیین سطح کاربر", "🏷 مدیریت کد تخفیف", "💰 مدیریت موجودی", "🔙 برگشت"
}

def intercept_flow_restart(message):
    text = (message.text or "").strip()
    if not text:
        return False

    if text.startswith("/"):
        _checkout_cache.pop(message.from_user.id, None)
        cmd = text.split()[0].lower()
        if cmd == "/start":
            start(message)
        elif cmd == "/help":
            help_cmd(message)
        else:
            bot.reply_to(message, "❌ عملیات قبلی لغو شد.")
        return True

    if text in ALL_MENU_BUTTON_TEXTS:
        _checkout_cache.pop(message.from_user.id, None)
        handle_buttons(message)
        return True

    return False

# ============================================================
# هندلرهای اصلی
# ============================================================
@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id
    parts = message.text.split(maxsplit=1)
    ref_payload = parts[1].strip() if len(parts) > 1 else None

    if is_banned(user_id):
        bot.reply_to(message, "🚫 شما بن هستید.")
        return
    if not is_member(user_id):
        bot.reply_to(message, f"⚠️ برای استفاده از ربات باید عضو کانال {CHANNEL_ID} بشی.", reply_markup=channel_keyboard())
        return
    create_or_update_user(user_id, message.from_user.username, start_payload=ref_payload)
    send_home(message.chat.id, user_id, message.from_user.first_name)

@bot.message_handler(commands=["help"])
def help_cmd(message):
    bot.reply_to(message, """🆘 <b>راهنمای ربات VPN IR</b>

/start — شروع مجدد ربات
🛒 خرید VPN — فرانسه / مولتی / نامحدود
⭐ خرید استارز — خرید استارز تلگرام
👛 کیف پول من — مشاهده و شارژ موجودی
🎁 رفرال من — لینک دعوت و پورسانت
📦 سفارش‌های من — پیگیری سفارش‌ها
👤 حساب من — اطلاعات حساب و ورود به پنل سایت
📞 پشتیبانی — سوالات متداول یا تماس با ادمین""", parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "check_membership")
def check_membership(call):
    user_id = call.from_user.id
    _membership_cache.pop(user_id, None)
    if is_member(user_id, force_refresh=True):
        bot.answer_callback_query(call.id, "✅")
        create_or_update_user(user_id, call.from_user.username)
        safe_edit(call.message.chat.id, call.message.message_id, "✅ عضویت شما تایید شد!")
        send_home(call.message.chat.id, user_id, call.from_user.first_name)
    else:
        bot.answer_callback_query(call.id, "❌ هنوز عضو نشدی!", show_alert=True)

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_buttons(message):
    user_id = message.from_user.id

    if is_banned(user_id):
        bot.reply_to(message, "🚫 شما بن هستید.")
        return
    if not is_member(user_id):
        bot.reply_to(message, f"⚠️ اول عضو کانال {CHANNEL_ID} شو.", reply_markup=channel_keyboard())
        return

    user = ensure_user_exists(user_id, message.from_user.username)
    if not user:
        bot.reply_to(message, "❌ خطا در شناسایی کاربر. لطفاً /start رو بزن.")
        return

    is_admin = user_id == ADMIN_ID

    # ====== بخش ادمین ======
    if message.text == "📋 سفارشات در انتظار" and is_admin:
        show_pending_orders(message.chat.id); return
    if message.text == "👥 لیست کاربران" and is_admin:
        show_users_list(message.chat.id); return
    if message.text == "📨 پیام همگانی" and is_admin:
        msg = bot.reply_to(message, "📨 پیام همگانی رو بنویس (یا /cancel برای لغو):")
        bot.register_next_step_handler(msg, broadcast_message); return
    if message.text == "📊 آمار فروش" and is_admin:
        show_stats(message.chat.id); return
    if message.text == "🚫 بن/آنبن کاربر" and is_admin:
        msg = bot.reply_to(message, "✏️ آیدی عددی کاربر رو بفرست:")
        bot.register_next_step_handler(msg, ask_ban_target); return
    if message.text == "📤 تحویل سرور" and is_admin:
        show_pending_deliveries(message.chat.id); return
    if message.text == "⭐ تعیین سطح کاربر" and is_admin:
        msg = bot.reply_to(message, "✏️ آیدی عددی کاربر رو بفرست:")
        bot.register_next_step_handler(msg, ask_user_level_target); return
    if message.text == "🏷 مدیریت کد تخفیف" and is_admin:
        show_discount_menu(message.chat.id); return
    if message.text == "💰 مدیریت موجودی" and is_admin:
        msg = bot.reply_to(message, "✏️ آیدی عددی کاربر رو بفرست:")
        bot.register_next_step_handler(msg, ask_wallet_manage_user); return
    if message.text == "🔙 برگشت" and is_admin:
        bot.reply_to(message, "🔙 برگشتی.", reply_markup=main_keyboard()); return

    # ====== بخش کاربری ======
    if message.text == "🛒 خرید VPN":
        bot.reply_to(message, VPN_MENU_TEXT, reply_markup=vpn_keyboard(), parse_mode="HTML")
    elif message.text == "⭐ خرید استارز":
        msg = bot.reply_to(message, f"""⭐ <b>خرید استارز</b>

تعداد استارز مورد نظرت رو بنویس (فقط عدد).

• هر عدد = {STARS_PRICE:,} تومان
• حداقل: {STARS_MIN} عدد
• حداکثر: {STARS_MAX:,} عدد

مثال: 100""", parse_mode="HTML")
        bot.register_next_step_handler(msg, get_stars_count)
    elif message.text == "👛 کیف پول من":
        show_wallet(message.chat.id, user_id)
    elif message.text == "🎁 رفرال من":
        show_referral(message.chat.id, user_id)
    elif message.text == "📦 سفارش‌های من":
        show_my_orders(message.chat.id, user_id)
    elif message.text == "📞 پشتیبانی":
        bot.reply_to(message, "📌 لطفاً یکی از گزینه‌های زیر رو انتخاب کن:", reply_markup=support_keyboard())
    elif message.text == "👤 حساب من":
        show_my_account(message.chat.id, user_id)

# ============================================================
# بخش VPN
# ============================================================
VPN_MENU_TEXT = f"""🌟 <b>یکی از گزینه‌های زیر رو انتخاب کن</b>
━━━━━━━━━━━━━━

🇫🇷 <b>سرور فرانسه</b>
• ۶,۰۰۰ تومان/گیگ
• {FRANCE_MIN_GB} تا {FRANCE_MAX_GB} گیگ
• مدت دلخواه: چند روز تا ۱ سال

🌍 <b>سرور مولتی (۱۸ کشور)</b>
• ۱۵,۰۰۰ تومان/گیگ
• {VPN_MIN_GB} تا {VPN_MAX_GB} گیگ
• مدت ثابت: ۳۰ روز

🚀 <b>نامحدود</b>
• ۳۹۹,۰۰۰ تومان — بدون محدودیت حجم
• {UNLIMITED_DOWNTIME_NOTE}
• مدت: ۳۰ روز
━━━━━━━━━━━━━━
💎 بدون محدودیت کاربری روی همه‌ی پلن‌ها"""

@bot.callback_query_handler(func=lambda call: call.data in ("vpn_buy_france", "vpn_buy_multi"))
def buy_vpn(call):
    bot.answer_callback_query(call.id, "📝")
    plan_key = "france" if call.data == "vpn_buy_france" else "multi"
    plan = VPN_PLAN_CONFIG[plan_key]
    duration_line = "خودت انتخاب می‌کنی (چند روز تا ۱ سال)" if plan['fixed_days'] is None else f"{plan['fixed_days']} روز"
    desc_lines = "\n".join(f"• {line}" for line in plan['desc'].split("\n"))
    text = f"""{plan['label']}
━━━━━━━━━━━━━━
{desc_lines}
• قیمت: {plan['price_per_gb']:,} تومان/گیگ
• حجم: {plan['min_gb']} تا {plan['max_gb']} گیگ
• مدت: {duration_line}
━━━━━━━━━━━━━━

✏️ حجم مورد نظرت رو به گیگ بنویس (فقط عدد):"""
    safe_edit(call.message.chat.id, call.message.message_id, text)
    bot.register_next_step_handler(call.message, get_vpn_volume, plan_key)

def get_vpn_volume(message, plan_key):
    plan = VPN_PLAN_CONFIG[plan_key]
    if not message or not message.text:
        bot.reply_to(message, "❌ لغو شد.")
        return
    if intercept_flow_restart(message):
        return
    text = message.text.strip()
    if not text.isdigit():
        msg = bot.reply_to(message, f"❌ فقط عدد بفرست، بین {plan['min_gb']} تا {plan['max_gb']}:")
        bot.register_next_step_handler(msg, get_vpn_volume, plan_key)
        return
    gb = int(text)
    if gb < plan['min_gb'] or gb > plan['max_gb']:
        msg = bot.reply_to(message, f"❌ بین {plan['min_gb']} تا {plan['max_gb']}:")
        bot.register_next_step_handler(msg, get_vpn_volume, plan_key)
        return

    if plan['fixed_days'] is not None:
        start_checkout(message.chat.id, message.from_user.id, plan_key, gb, plan['fixed_days'])
        return

    msg = bot.reply_to(message, f"✏️ مدت سرویس رو به روز بنویس (بین {FRANCE_MIN_DAYS} تا {FRANCE_MAX_DAYS} روز):")
    bot.register_next_step_handler(msg, get_vpn_duration, plan_key, gb)

def get_vpn_duration(message, plan_key, gb):
    if not message or not message.text:
        bot.reply_to(message, "❌ لغو شد.")
        return
    if intercept_flow_restart(message):
        return
    text = message.text.strip()
    if not text.isdigit():
        msg = bot.reply_to(message, f"❌ فقط عدد بفرست، بین {FRANCE_MIN_DAYS} تا {FRANCE_MAX_DAYS} روز:")
        bot.register_next_step_handler(msg, get_vpn_duration, plan_key, gb)
        return
    days = int(text)
    if days < FRANCE_MIN_DAYS or days > FRANCE_MAX_DAYS:
        msg = bot.reply_to(message, f"❌ بین {FRANCE_MIN_DAYS} تا {FRANCE_MAX_DAYS} روز:")
        bot.register_next_step_handler(msg, get_vpn_duration, plan_key, gb)
        return
    start_checkout(message.chat.id, message.from_user.id, plan_key, gb, days)

# ============================================================
# فرآیند تسویه‌حساب
# ============================================================
def start_checkout(chat_id, user_id, plan_key, gb, days):
    plan = VPN_PLAN_CONFIG[plan_key]
    base_amount = gb * plan['price_per_gb']
    product_name = f"{plan['label']} {gb} گیگ / {days} روز"
    _checkout_cache[user_id] = {
        "kind": "vpn", "plan_key": plan_key, "gb": gb, "days": days,
        "product": product_name, "base_amount": base_amount, "tracking_prefix": "VPN"
    }
    bot.send_message(chat_id, f"""📦 <b>{product_name}</b>
💰 مبلغ پایه: {base_amount:,} تومان

🏷 کد تخفیف داری؟""", reply_markup=discount_prompt_keyboard(), parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data in ("discount_yes", "discount_no"))
def handle_discount_choice(call):
    user_id = call.from_user.id
    cart = _checkout_cache.get(user_id)
    if not cart:
        bot.answer_callback_query(call.id, "❌ سبد خریدی پیدا نشد.")
        return
    if call.data == "discount_no":
        bot.answer_callback_query(call.id)
        show_payment_options(call.message.chat.id, user_id)
    else:
        bot.answer_callback_query(call.id, "📝")
        msg = bot.send_message(call.message.chat.id, "✏️ کد تخفیف رو بنویس:")
        bot.register_next_step_handler(msg, apply_discount_code)

def apply_discount_code(message):
    user_id = message.from_user.id
    cart = _checkout_cache.get(user_id)
    if not cart:
        bot.reply_to(message, "❌ سبد خریدی پیدا نشد.")
        return
    if (message.text or "").strip().lower() == "/skip":
        show_payment_options(message.chat.id, user_id)
        return
    if intercept_flow_restart(message):
        return
    code = (message.text or "").strip().upper()
    plan_key = cart.get("plan_key")
    discount, error = check_discount_code(code, plan_key)
    if error:
        msg = bot.reply_to(message, f"{error}\n✏️ دوباره بنویس یا /skip برای رد کردن:")
        bot.register_next_step_handler(msg, apply_discount_code)
        return
    cart["discount_code"] = discount["code"]
    cart["discount_percent"] = discount["percent"]
    bot.reply_to(message, f"✅ کد تخفیف {discount['percent']}٪ اعمال شد!")
    show_payment_options(message.chat.id, user_id)

def show_payment_options(chat_id, user_id):
    cart = _checkout_cache.get(user_id)
    if not cart:
        return
    base = cart["base_amount"]
    percent = cart.get("discount_percent", 0)
    final_amount = round(base * (100 - percent) / 100)
    cart["final_amount"] = final_amount

    user = ensure_user_exists(user_id)
    wallet_balance = user["wallet_balance"] if user else 0
    can_use_wallet = wallet_balance >= final_amount

    discount_line = f"🏷 بعد از {percent}٪ تخفیف\n" if percent else ""
    text = f"""📦 <b>{cart['product']}</b>
━━━━━━━━━━━━━━
{discount_line}💰 مبلغ نهایی: {final_amount:,} تومان
👛 موجودی کیف پول: {wallet_balance:,} تومان
━━━━━━━━━━━━━━

روش پرداخت رو انتخاب کن:"""
    bot.send_message(chat_id, text, reply_markup=payment_method_keyboard(can_use_wallet), parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data in ("pay_card", "pay_wallet"))
def handle_payment_method(call):
    user_id = call.from_user.id
    cart = _checkout_cache.get(user_id)
    if not cart:
        bot.answer_callback_query(call.id, "❌ سبد خریدی پیدا نشد.")
        return

    final_amount = cart["final_amount"]

    if call.data == "pay_wallet":
        with _order_lock:
            user = ensure_user_exists(user_id)
            if not user or int(user.get("wallet_balance") or 0) < final_amount:
                bot.answer_callback_query(call.id, "❌ موجودی کافی نیست.", show_alert=True)
                return
            order = create_order(
                user_id, cart["product"], cart["base_amount"], final_amount,
                cart["kind"], cart["tracking_prefix"], discount_code=cart.get("discount_code"),
                pay_method="wallet", gb=cart.get("gb"), days=cart.get("days"), plan=cart.get("plan_key"),
                status="pending"
            )
            if not order:
                bot.answer_callback_query(call.id, "❌ ساخت سفارش ناموفق بود.", show_alert=True)
                return
            new_balance = adjust_wallet(user_id, -final_amount, "order_payment", ref_order_id=order["id"])
            if new_balance is None:
                update_order_status(order["id"], "rejected", expected_status="pending")
                bot.answer_callback_query(call.id, "❌ پرداخت کیف پول انجام نشد؛ دوباره تلاش کن.", show_alert=True)
                return
            if not update_order_status(order["id"], "confirmed", expected_status="pending"):
                # If another retry already confirmed it, the debit is idempotent by ref_order_id.
                latest = get_order(order["id"])
                if not latest or latest.get("status") != "confirmed":
                    log.critical(f"WALLET ORDER STATUS ERROR order={order['id']}")
                    bot.answer_callback_query(call.id, "❌ پرداخت ثبت شد ولی وضعیت سفارش نامشخص است. با پشتیبانی تماس بگیر.", show_alert=True)
                    return
                order = latest
            process_referral_commission(order)
            if cart.get("discount_code") and not consume_discount_code(cart["discount_code"]):
                log.warning(f"مصرف کد تخفیف ناموفق بود: {cart['discount_code']} / order={order['id']}")
            safe_edit(call.message.chat.id, call.message.message_id, f"""✅ <b>پرداخت از کیف پول انجام شد!</b>
━━━━━━━━━━━━━━
📦 {cart['product']}
🔖 کد رهگیری: <code>{order['tracking_code']}</code>
━━━━━━━━━━━━━━
⏳ سرویس به‌زودی توسط ادمین ارسال می‌شه.""")
            notify_admin_new_order(order)
            _checkout_cache.pop(user_id, None)
        bot.answer_callback_query(call.id, "✅ پرداخت انجام شد")
        return

    bot.answer_callback_query(call.id, "💳")
    order = create_order(
        user_id, cart["product"], cart["base_amount"], final_amount,
        cart["kind"], cart["tracking_prefix"], discount_code=cart.get("discount_code"),
        pay_method="card", gb=cart.get("gb"), days=cart.get("days"), plan=cart.get("plan_key"),
        status="pending"
    )
    if order:
        _receipt_order_cache[user_id] = order["id"]
        safe_edit(call.message.chat.id, call.message.message_id, f"""🛒 <b>سفارش شما</b>
━━━━━━━━━━━━━━
📦 {cart['product']}
💰 قیمت: {final_amount:,} تومان
🔖 کد رهگیری: <code>{order['tracking_code']}</code>
━━━━━━━━━━━━━━

{get_payment_info(final_amount)}

📤 بعد از واریز، عکس رسید رو همینجا بفرست.""", reply_markup=cancel_payment_keyboard())
    del _checkout_cache[user_id]

@bot.callback_query_handler(func=lambda call: call.data == "cancel_payment")
def cancel_payment(call):
    bot.answer_callback_query(call.id, "🔙")
    _checkout_cache.pop(call.from_user.id, None)
    safe_edit(call.message.chat.id, call.message.message_id, "🔙 به منوی اصلی برگشتی.")
    bot.send_message(call.message.chat.id, "📋 منوی اصلی:", reply_markup=main_keyboard())

# ============================================================
# سرور نامحدود
# ============================================================
@bot.callback_query_handler(func=lambda call: call.data == "vpn_unlimited")
def buy_unlimited(call):
    bot.answer_callback_query(call.id, "🚀")
    user_id = call.from_user.id
    _checkout_cache[user_id] = {
        "kind": "unlimited", "product": "🚀 سرور نامحدود", "base_amount": UNLIMITED_PRICE,
        "tracking_prefix": "UNL", "plan_key": "unlimited"
    }
    safe_edit(call.message.chat.id, call.message.message_id,
              f"📦 <b>🚀 سرور نامحدود</b>\n💰 مبلغ پایه: {UNLIMITED_PRICE:,} تومان\n\n🏷 کد تخفیف داری؟",
              reply_markup=discount_prompt_keyboard())

# ============================================================
# بخش استارز
# ============================================================
def get_stars_count(message):
    if not message or not message.text:
        bot.reply_to(message, "❌ لغو شد.")
        return
    if intercept_flow_restart(message):
        return
    text = message.text.strip()
    if not text.isdigit():
        msg = bot.reply_to(message, f"❌ عدد بین {STARS_MIN} تا {STARS_MAX}:")
        bot.register_next_step_handler(msg, get_stars_count)
        return
    count = int(text)
    if count < STARS_MIN or count > STARS_MAX:
        msg = bot.reply_to(message, f"❌ بین {STARS_MIN} تا {STARS_MAX}:")
        bot.register_next_step_handler(msg, get_stars_count)
        return
    _checkout_cache.setdefault(message.from_user.id, {})["stars_count"] = count
    bot.reply_to(message, "📌 استارز برای چه کسی؟", reply_markup=stars_type_keyboard())

@bot.callback_query_handler(func=lambda call: call.data in ["stars_self", "stars_other"])
def handle_stars_type(call):
    user_id = call.from_user.id
    cart = _checkout_cache.get(user_id)
    if not cart or "stars_count" not in cart:
        bot.answer_callback_query(call.id, "❌ اول تعداد رو وارد کن!")
        return
    count = cart["stars_count"]
    price = count * STARS_PRICE

    if call.data == "stars_self":
        target = call.from_user.username or str(user_id)
    else:
        bot.answer_callback_query(call.id, "📝")
        safe_edit(call.message.chat.id, call.message.message_id, "✏️ آیدی تلگرام شخص مورد نظر رو بنویس:")
        bot.register_next_step_handler(call.message, get_stars_other, count)
        return

    bot.answer_callback_query(call.id, "✅")
    _checkout_cache[user_id] = {
        "kind": "stars", "product": f"⭐ استارز {count} عددی برای @{target}",
        "base_amount": price, "tracking_prefix": "STAR", "plan_key": "stars"
    }
    safe_edit(call.message.chat.id, call.message.message_id,
              f"📦 <b>استارز {count} عددی</b>\n💰 مبلغ پایه: {price:,} تومان\n\n🏷 کد تخفیف داری؟",
              reply_markup=discount_prompt_keyboard())

def get_stars_other(message, count):
    if not message or not message.text:
        bot.reply_to(message, "❌ لغو شد.")
        return
    if intercept_flow_restart(message):
        return
    username = message.text.strip().lstrip("@")
    if not username or len(username) < 3:
        msg = bot.reply_to(message, "❌ آیدی معتبر نیست:")
        bot.register_next_step_handler(msg, get_stars_other, count)
        return
    price = count * STARS_PRICE
    _checkout_cache[message.from_user.id] = {
        "kind": "stars", "product": f"⭐ استارز {count} عددی برای @{username}",
        "base_amount": price, "tracking_prefix": "STAR", "plan_key": "stars"
    }
    bot.send_message(message.chat.id,
                      f"📦 <b>استارز {count} عددی برای @{username}</b>\n💰 مبلغ پایه: {price:,} تومان\n\n🏷 کد تخفیف داری؟",
                      reply_markup=discount_prompt_keyboard(), parse_mode="HTML")

# ============================================================
# کیف پول
# ============================================================
def show_wallet(chat_id, user_id):
    user = ensure_user_exists(user_id)
    if not user:
        bot.send_message(chat_id, "❌ خطا در دریافت اطلاعات. لطفاً /start رو بزن.")
        return

    balance = user["wallet_balance"]

    last_transaction = None
    try:
        res = db.table("wallet_transactions").select("*").eq("telegram_id", user_id).order("created_at", desc=True).limit(1).execute()
        if res.data:
            last_transaction = res.data[0]
    except Exception as e:
        log.error(f"خطا در دریافت آخرین تراکنش: {e}")

    text = f"""👛 <b>کیف پول من</b>
━━━━━━━━━━━━━━
💰 موجودی: {balance:,} تومان
━━━━━━━━━━━━━━
📆 تاریخ امروز: {now_jalali()}
━━━━━━━━━━━━━━"""

    if last_transaction:
        try:
            jalali_date = to_jalali(last_transaction["created_at"])
            text += f"\n🔄 آخرین تراکنش:\n{jalali_date} — {last_transaction['amount']:,} تومان"
        except:
            pass

    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("➕ شارژ کیف پول", callback_data="wallet_topup"))
    bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "wallet_topup")
def wallet_topup(call):
    bot.answer_callback_query(call.id, "➕")
    msg = bot.send_message(call.message.chat.id, """✏️ <b>چند تومان می‌خوای شارژ کنی؟</b>

• فقط عدد بفرست، به تومان (نه ریال)
• مثال: 50000 یعنی ۵۰ هزار تومان
• حداقل مبلغ: ۱۰,۰۰۰ تومان""", parse_mode="HTML")
    bot.register_next_step_handler(msg, get_topup_amount)

def get_topup_amount(message):
    if intercept_flow_restart(message):
        return
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) < 10000:
        msg = bot.reply_to(message, "❌ فقط عدد و به تومان بفرست (مثلاً 50000). حداقل ۱۰,۰۰۰ تومان:")
        bot.register_next_step_handler(msg, get_topup_amount)
        return
    amount = int(text)
    order = create_order(
        message.from_user.id, "👛 شارژ کیف پول", amount, amount,
        "wallet_topup", "TOPUP", pay_method="card", status="pending"
    )
    if order:
        bot.reply_to(message, f"""🛒 <b>درخواست شارژ ثبت شد</b>
━━━━━━━━━━━━━━
💰 مبلغ: {amount:,} تومان
🔖 کد رهگیری: <code>{order['tracking_code']}</code>
━━━━━━━━━━━━━━

{get_payment_info(amount)}

📤 بعد از واریز، عکس رسید رو همینجا بفرست.""", parse_mode="HTML", reply_markup=cancel_payment_keyboard())

# ============================================================
# رفرال
# ============================================================
def show_referral(chat_id, user_id):
    user = ensure_user_exists(user_id)
    if not user:
        bot.send_message(chat_id, "❌ خطا در دریافت اطلاعات. لطفاً /start رو بزن.")
        return
    ref_code = user["referral_code"]
    ref_link = f"https://t.me/{get_bot_username()}?start={ref_code}"

    try:
        referral_count = len(db.table("app_users").select("telegram_id").eq("referred_by", user_id).execute().data)
    except Exception as e:
        log.error(f"خطا در شمارش زیرمجموعه‌ها: {e}")
        referral_count = 0

    try:
        earned_rows = db.table("wallet_transactions").select("amount").eq("telegram_id", user_id).eq("reason", "referral_commission").execute().data
        total_earned = sum(r["amount"] for r in earned_rows)
    except Exception as e:
        log.error(f"خطا در محاسبه پورسانت: {e}")
        total_earned = 0

    bot.send_message(chat_id, f"""🎁 <b>سیستم رفرال</b>
━━━━━━━━━━━━━━

🔗 لینک دعوت شما:
<code>{ref_link}</code>

👥 تعداد زیرمجموعه‌ها: {referral_count}
💰 مجموع پورسانت دریافتی: {total_earned:,} تومان
━━━━━━━━━━━━━━

📌 به‌ازای هر خرید تایید‌شده‌ی زیرمجموعه‌هات، {REFERRAL_PERCENT}٪ به کیف پولت اضافه می‌شه.""", parse_mode="HTML")

# ============================================================
# پیگیری سفارش‌ها
# ============================================================
def show_my_orders(chat_id, user_id):
    try:
        res = db.table("orders").select("*").eq("telegram_id", user_id).order("created_at", desc=True).limit(15).execute()
        orders = res.data
    except Exception as e:
        log.error(f"خطا در دریافت سفارش‌ها: {e}")
        orders = []

    if not orders:
        bot.send_message(chat_id, "❌ هنوز سفارشی ثبت نکردی.")
        return
    status_labels = {"pending": "⏳ در انتظار", "confirmed": "✅ تایید شده", "delivered": "📬 تحویل داده شده", "rejected": "❌ رد شده"}
    text = "📦 <b>سفارش‌های شما</b>\n━━━━━━━━━━━━━━\n\n"
    for o in orders:
        status = status_labels.get(o["status"], o["status"])
        created = to_jalali(o["created_at"]) if o.get("created_at") else "—"
        text += f"🔖 <code>{o['tracking_code']}</code>\n{o['product']}\n{o['final_amount']:,} تومان · {status}\n📅 {created}\n\n"
    bot.send_message(chat_id, text, parse_mode="HTML")

# ============================================================
# حساب من
# ============================================================
def show_my_account(chat_id, user_id):
    user = ensure_user_exists(user_id)
    if not user:
        bot.send_message(chat_id, "❌ خطا در دریافت اطلاعات. لطفاً /start رو بزن.")
        return

    is_active = user.get("is_active", False)
    status_text = "✅ فعال" if is_active else "🔴 غیرفعال"

    try:
        orders = db.table("orders").select("id, status").eq("telegram_id", user_id).execute().data
        orders_count = len(orders)
        delivered_count = sum(1 for o in orders if o["status"] == "delivered")
    except Exception as e:
        log.error(f"خطا در دریافت آمار سفارش‌ها: {e}")
        orders_count = 0
        delivered_count = 0

    try:
        referral_count = len(db.table("app_users").select("telegram_id").eq("referred_by", user_id).execute().data)
    except Exception as e:
        log.error(f"خطا در شمارش زیرمجموعه‌ها: {e}")
        referral_count = 0

    join_date = "—"
    if user.get("created_at"):
        join_date = to_jalali(user["created_at"])

    username = user.get("username") or "ندارد"
    phone_status = user.get("phone") or "🔴 ارسال نشده است 🔴"
    user_group = user.get("user_level", "عادی")

    text = f"""🤖 اطلاعات حساب کاربری شما :

🪪 شناسه کاربری: <code>{user_id}</code>
👤 نام: {username}
📱 شماره تماس: {phone_status}
⌚️ زمان ثبت‌نام: {join_date}
💰 موجودی: {user['wallet_balance']:,} تومان
🛒 تعداد سرویس‌های خریداری‌شده: {delivered_count} عدد
📑 تعداد فاکتورهای پرداخت‌شده: {orders_count} عدد
🤝 تعداد زیرمجموعه‌های شما: {referral_count} نفر
🔖 گروه کاربری: {user_group}
🔐 وضعیت حساب: {status_text}




📆 {now_jalali()}"""

    if not is_active:
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton("🔑 فعال‌سازی حساب", callback_data=f"activate_account_{user_id}"))
        bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    else:
        bot.send_message(chat_id, text, parse_mode="HTML")

# ============================================================
# فعال‌سازی حساب
# ============================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("activate_account_"))
def activate_account(call):
    user_id = int(call.data.replace("activate_account_", ""))
    
    if call.from_user.id != user_id:
        bot.answer_callback_query(call.id, "❌ این دکمه مال شما نیست!")
        return
    
    bot.answer_callback_query(call.id, "🔑 در حال ارسال کد...")
    
    activation_code = str(random.randint(100000, 999999))
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    
    try:
        try:
            db.table("activation_codes").delete().eq("telegram_id", user_id).execute()
        except:
            pass
        
        db.table("activation_codes").insert({
            "telegram_id": user_id,
            "code": activation_code,
            "expires_at": expires_at,
            "used": False
        }).execute()
        
        bot.send_message(
            user_id,
            f"""🔑 <b>کد فعال‌سازی حساب</b>
━━━━━━━━━━━━━━━━━━━━━

کد فعال‌سازی شما (تا ۱۰ دقیقه معتبر است):

<code>{activation_code}</code>

━━━━━━━━━━━━━━━━━━━━━
📌 کد رو در ربات وارد کن تا حساب شما فعال بشه.

⚠️ این کد فقط ۱۰ دقیقه اعتبار دارد!""",
            parse_mode="HTML"
        )
        
        msg = bot.send_message(
            call.message.chat.id,
            f"✅ کد فعال‌سازی به شما ارسال شد!\n\n"
            f"🔑 کد: <code>{activation_code}</code>\n"
            f"⏳ اعتبار: ۱۰ دقیقه\n\n"
            f"📌 کد رو در ربات وارد کن:",
            parse_mode="HTML"
        )
        
        bot.register_next_step_handler(msg, verify_activation_code, user_id)
        
    except Exception as e:
        log.error(f"خطا در ارسال کد فعال‌سازی: {e}")
        bot.answer_callback_query(call.id, "❌ خطا در ارسال کد!")

def verify_activation_code(message, user_id):
    if message.from_user.id != user_id:
        bot.reply_to(message, "❌ این دستور مال شما نیست!")
        return
    
    code = message.text.strip()
    
    try:
        res = db.table("activation_codes").select("*").eq("telegram_id", user_id).eq("code", code).eq("used", False).execute()
        
        if not res.data:
            bot.reply_to(message, "❌ کد فعال‌سازی نامعتبر یا منقضی شده است!\nلطفاً دوباره درخواست کد جدید بدهید.")
            return
        
        activation = res.data[0]
        expires_at = datetime.fromisoformat(activation["expires_at"].replace("Z", "+00:00"))
        if expires_at < datetime.now(timezone.utc):
            bot.reply_to(message, "❌ کد فعال‌سازی منقضی شده است!\nلطفاً دوباره درخواست کد جدید بدهید.")
            return
        
        db.table("app_users").update({"is_active": True}).eq("telegram_id", user_id).execute()
        db.table("activation_codes").update({"used": True}).eq("id", activation["id"]).execute()
        
        if user_id in _user_cache:
            _user_cache[user_id]["is_active"] = True
        
        bot.reply_to(
            message,
            f"✅ <b>حساب شما با موفقیت فعال شد!</b> 🎉\n\n"
            f"اکنون می‌توانید از تمام امکانات ربات استفاده کنید.",
            parse_mode="HTML"
        )
        
        send_home(message.chat.id, user_id, message.from_user.first_name)
        
    except Exception as e:
        log.error(f"خطا در تایید کد فعال‌سازی: {e}")
        bot.reply_to(message, "❌ خطا در فعال‌سازی حساب! دوباره تلاش کنید.")

# ============================================================
# پشتیبانی
# ============================================================
@bot.callback_query_handler(func=lambda call: call.data == "faq")
def show_faq(call):
    bot.answer_callback_query(call.id, "📋")
    safe_edit(call.message.chat.id, call.message.message_id, FAQ_TEXT)

@bot.callback_query_handler(func=lambda call: call.data == "support_ai")
def support_ai(call):
    bot.answer_callback_query(call.id, "🤖")
    safe_edit(call.message.chat.id, call.message.message_id,
              "🤖 <b>پشتیبانی هوشمند</b>\n\nسوالتو بنویس، هوش مصنوعی جواب می‌ده:")
    bot.register_next_step_handler(call.message, handle_ai_question)

def handle_ai_question(message):
    question = message.text or ""
    if "ad_vpnir" in question.lower() or "آیدی پشتیبانی" in question.lower():
        bot.reply_to(message, f"👤 آیدی پشتیبانی: {SUPPORT_ID}")
        return
    bot.send_chat_action(message.chat.id, "typing")
    answer = ask_ai(message.from_user.id, question)
    bot.reply_to(message, f"🤖 <b>پاسخ:</b>\n\n{answer}", parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "support_admin")
def support_admin(call):
    bot.answer_callback_query(call.id, "👤")
    safe_edit(call.message.chat.id, call.message.message_id, f"""👤 <b>ارتباط با ادمین</b>
━━━━━━━━━━━━━━
📌 آیدی ادمین: {SUPPORT_ID}
⏳ پاسخگویی: حداکثر ۳ ساعت""")

# ============================================================
# دریافت عکس رسید
# ============================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("receipt_order_"))
def select_receipt_order(call):
    user_id = call.from_user.id
    try:
        order_id = int(call.data.replace("receipt_order_", "", 1))
    except ValueError:
        bot.answer_callback_query(call.id, "❌ سفارش نامعتبر است.", show_alert=True)
        return
    order = get_order(order_id)
    if not order or int(order.get("telegram_id")) != user_id or order.get("status") != "pending":
        bot.answer_callback_query(call.id, "❌ این سفارش دیگر قابل پرداخت نیست.", show_alert=True)
        return
    _receipt_order_cache[user_id] = order_id
    bot.answer_callback_query(call.id, "✅ سفارش انتخاب شد")
    bot.send_message(call.message.chat.id, f"📤 حالا عکس رسید سفارش <code>{order['tracking_code']}</code> رو بفرست.", parse_mode="HTML")


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        bot.reply_to(message, "🚫 شما بن هستید.")
        return
    if not is_member(user_id):
        bot.reply_to(message, f"⚠️ اول عضو کانال {CHANNEL_ID} شو.")
        return

    pending_orders = get_pending_orders(user_id, limit=10)
    selected_id = _receipt_order_cache.get(user_id)
    if selected_id:
        selected = next((o for o in pending_orders if int(o["id"]) == int(selected_id)), None)
        if selected:
            pending_orders = [selected]
        else:
            _receipt_order_cache.pop(user_id, None)
    if not pending_orders:
        bot.reply_to(message, "❌ سفارش فعالی نداری.")
        return
    if len(pending_orders) > 1:
        keyboard = InlineKeyboardMarkup(row_width=1)
        for o in pending_orders[:8]:
            keyboard.add(InlineKeyboardButton(
                f"🔖 {o['tracking_code']} | {o['final_amount']:,} تومان",
                callback_data=f"receipt_order_{o['id']}"
            ))
        bot.reply_to(message, "⚠️ چند سفارش پرداخت‌نشده داری. اول سفارش مربوط به این رسید رو انتخاب کن:", reply_markup=keyboard)
        return
    order = pending_orders[0]

    try:
        file_id = message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        filename = f"receipt_{user_id}_{int(time.time())}.jpg"
        with open(filename, "wb") as f:
            f.write(downloaded_file)
        with open(filename, "rb") as f:
            bot.send_photo(ADMIN_ID, f, caption=f"""🔔 <b>رسید جدید</b>
━━━━━━━━━━━━━━
👤 {message.from_user.first_name}
🆔 {user_id}
📦 {order['product']}
💰 {order['final_amount']:,} تومان
🔖 {order['tracking_code']}""", reply_markup=confirm_keyboard(order["id"]), parse_mode="HTML")
        os.remove(filename)
        _receipt_order_cache.pop(user_id, None)
        bot.reply_to(message, "✅ رسید شما دریافت شد! منتظر تایید ادمین باش.")
    except telebot.apihelper.ApiTelegramException as e:
        log.error(f"خطای تلگرام هنگام ارسال رسید: {e}")
        bot.reply_to(message, "❌ خطا در ارسال رسید به ادمین. دوباره امتحان کن.")
    except OSError as e:
        log.error(f"خطای فایل هنگام پردازش رسید: {e}")
        bot.reply_to(message, "❌ خطا در پردازش رسید. دوباره امتحان کن.")

def notify_admin_new_order(order):
    try:
        bot.send_message(ADMIN_ID, f"""🔔 <b>سفارش جدید (پرداخت از کیف پول)</b>
━━━━━━━━━━━━━━
📦 {order['product']}
💰 {order['final_amount']:,} تومان
🔖 {order['tracking_code']}
🆔 {order['telegram_id']}""", parse_mode="HTML")
    except telebot.apihelper.ApiTelegramException:
        pass

# ============================================================
# تایید و رد سفارش
# ============================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_"))
def confirm_order_cb(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!", show_alert=True)
        return

    data = call.data
    try:
        if data.startswith("confirm_order_"):
            order_id = int(data.replace("confirm_order_", "", 1))
            order = get_order(order_id)
        else:
            # Backward compatibility with old receipt buttons.
            user_id = int(data.replace("confirm_", "", 1))
            order = get_latest_pending_order(user_id)
    except (ValueError, TypeError):
        bot.answer_callback_query(call.id, "❌ شناسه سفارش نامعتبر است!", show_alert=True)
        return

    if not order or order.get("status") != "pending":
        bot.answer_callback_query(call.id, "❌ این سفارش دیگر در انتظار تایید نیست.", show_alert=True)
        return

    user_id = int(order["telegram_id"])

    if order.get("type") == "wallet_topup":
        with _order_lock:
            current_status = order.get("status")
            if current_status == "pending":
                if not update_order_status(order["id"], "processing", expected_status="pending"):
                    bot.answer_callback_query(call.id, "❌ سفارش قبلاً پردازش شده است.", show_alert=True)
                    return
            elif current_status != "processing":
                bot.answer_callback_query(call.id, "❌ این سفارش دیگر قابل پردازش نیست.", show_alert=True)
                return

            new_balance = adjust_wallet(user_id, int(order["final_amount"]), "topup", ref_order_id=order["id"])
            if new_balance is None:
                log.error(f"شارژ سفارش در وضعیت processing باقی ماند تا دوباره قابل بررسی باشد: {order['id']}")
                bot.answer_callback_query(call.id, "❌ شارژ انجام نشد؛ سفارش برای بررسی مجدد نگه داشته شد.", show_alert=True)
                return
            if not update_order_status(order["id"], "delivered", expected_status="processing"):
                latest = get_order(order["id"])
                if not latest or latest.get("status") != "delivered":
                    log.critical(f"TOPUP STATUS ERROR order={order['id']}")
                    bot.answer_callback_query(call.id, "❌ شارژ ثبت شد ولی وضعیت سفارش نامشخص است. بررسی لازم است.", show_alert=True)
                    return
        try:
            bot.send_message(user_id, f"✅ کیف پولت شارژ شد!\n💰 {order['final_amount']:,} تومان اضافه شد.")
        except telebot.apihelper.ApiTelegramException:
            pass
        try:
            bot.edit_message_caption(f"✅ شارژ کیف پول انجام شد!\n👤 {user_id}", call.message.chat.id, call.message.message_id)
        except telebot.apihelper.ApiTelegramException:
            pass
        bot.answer_callback_query(call.id, "✅")
        return

    if not update_order_status(order["id"], "confirmed", expected_status="pending"):
        bot.answer_callback_query(call.id, "❌ تایید سفارش ناموفق بود.", show_alert=True)
        return

    # Discount is consumed only after actual payment confirmation.
    if order.get("discount_code"):
        consume_discount_code(order["discount_code"])
    process_referral_commission(order)

    if order.get("type") == "stars":
        try:
            bot.send_message(user_id, f"✅ خرید تایید شد!\n📦 {order['product']}")
        except telebot.apihelper.ApiTelegramException:
            pass
        update_order_status(order["id"], "delivered", expected_status="confirmed")
        try:
            bot.edit_message_caption(f"✅ استارز ارسال شد!\n👤 {user_id}", call.message.chat.id, call.message.message_id)
        except telebot.apihelper.ApiTelegramException:
            pass
        bot.answer_callback_query(call.id, "✅")
        return

    bot.answer_callback_query(call.id, "📤")
    try:
        bot.edit_message_caption(
            f"📝 سرور رو ارسال کن:\n👤 {user_id}\n📦 {order['product']}\n🔖 {order['tracking_code']}",
            call.message.chat.id, call.message.message_id
        )
    except telebot.apihelper.ApiTelegramException:
        pass
    msg = bot.send_message(call.message.chat.id, "📤 لطفاً سرور رو ارسال کن (متن یا عکس):")
    bot.register_next_step_handler(msg, send_server_to_user, order["id"], user_id)

def send_server_to_user(message, order_id, user_id):
    if intercept_flow_restart(message):
        return
    order = get_order(order_id)
    if not order:
        bot.reply_to(message, "❌ سفارشی نیست!")
        return
    if message.content_type not in ("photo", "text"):
        bot.reply_to(message, "❌ فقط متن یا عکس مجازه!")
        msg = bot.send_message(message.chat.id, "📤 لطفاً سرور رو ارسال کن (متن یا عکس):")
        bot.register_next_step_handler(msg, send_server_to_user, order_id, user_id)
        return

    days = order.get("days") or 30
    expiry_date = get_expiry_date(days)

    try:
        if message.content_type == "photo":
            file_id = message.photo[-1].file_id
            file_info = bot.get_file(file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            filename = f"server_{user_id}_{int(time.time())}.jpg"
            with open(filename, "wb") as f:
                f.write(downloaded_file)
            caption_text = message.caption or ""
            with open(filename, "rb") as f:
                bot.send_photo(user_id, f, caption=f"""✅ <b>خرید شما تایید شد!</b>
━━━━━━━━━━━━━━
📦 {order['product']}
🔖 {order['tracking_code']}
📅 خرید: {to_jalali(order['created_at'])}
⏳ انقضا: {expiry_date}
━━━━━━━━━━━━━━
🌐 اطلاعات سرور:
{caption_text}""", parse_mode="HTML")
            os.remove(filename)
            update_order_status(order_id, "delivered", server_info=caption_text, expected_status="confirmed")
        else:
            server_text = message.text.strip()
            if not server_text:
                bot.reply_to(message, "❌ خالی بود، دوباره بفرست:")
                bot.register_next_step_handler(message, send_server_to_user, order_id, user_id)
                return
            bot.send_message(user_id, f"""✅ <b>خرید شما تایید شد!</b>
━━━━━━━━━━━━━━
📦 {order['product']}
🔖 {order['tracking_code']}
📅 خرید: {to_jalali(order['created_at'])}
⏳ انقضا: {expiry_date}
━━━━━━━━━━━━━━
🌐 اطلاعات سرور:
{server_text}""", parse_mode="HTML")
            update_order_status(order_id, "delivered", server_info=server_text, expected_status="confirmed")

        bot.reply_to(message, "✅ سرور ارسال شد!")
    except telebot.apihelper.ApiTelegramException as e:
        log.error(f"خطای تلگرام هنگام ارسال سرور: {e}")
        bot.reply_to(message, "❌ نتونستم به کاربر پیام بدم. سفارش هنوز تاییدشده باقی می‌مونه.")
    except OSError as e:
        log.error(f"خطای فایل هنگام ارسال سرور: {e}")
        bot.reply_to(message, "❌ خطا در پردازش فایل. دوباره امتحان کن.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("reject_"))
def reject_order_cb(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!", show_alert=True)
        return
    try:
        if call.data.startswith("reject_order_"):
            order_id = int(call.data.replace("reject_order_", "", 1))
            order = get_order(order_id)
        else:
            user_id = int(call.data.replace("reject_", "", 1))
            order = get_latest_pending_order(user_id)
    except (ValueError, TypeError):
        bot.answer_callback_query(call.id, "❌ شناسه سفارش نامعتبر است!", show_alert=True)
        return
    if not order or order.get("status") != "pending":
        bot.answer_callback_query(call.id, "❌ این سفارش دیگر در انتظار تایید نیست.", show_alert=True)
        return
    if not update_order_status(order["id"], "rejected", expected_status="pending"):
        bot.answer_callback_query(call.id, "❌ سفارش قبلاً پردازش شده است.", show_alert=True)
        return
    user_id = int(order["telegram_id"])
    try:
        bot.send_message(user_id, "❌ خرید شما رد شد. برای توضیحات با پشتیبانی تماس بگیرید.")
    except telebot.apihelper.ApiTelegramException:
        pass
    try:
        bot.edit_message_caption(f"❌ رد شد!\n👤 {user_id}\n🔖 {order['tracking_code']}", call.message.chat.id, call.message.message_id)
    except telebot.apihelper.ApiTelegramException as e:
        log.warning(f"edit_message_caption failed: {e}")
    bot.answer_callback_query(call.id, "❌")

# ============================================================
# گزینه تحویل سرور
# ============================================================
def show_pending_deliveries(chat_id):
    try:
        res = db.table("orders").select("*").eq("status", "confirmed").order("created_at", desc=True).limit(50).execute()
        orders = res.data
    except Exception as e:
        log.error(f"خطا در دریافت سفارشات تایید شده: {e}")
        orders = []

    if not orders:
        bot.send_message(chat_id, "📤 هیچ سفارش تایید شده‌ای برای تحویل وجود ندارد.")
        return

    text = "📤 <b>سفارشات تایید شده - در انتظار تحویل</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    for o in orders:
        created = to_jalali(o['created_at']) if o.get('created_at') else "—"
        text += f"🔖 <code>{o['tracking_code']}</code>\n"
        text += f"🆔 کاربر: <code>{o['telegram_id']}</code>\n"
        text += f"📦 {o['product']}\n"
        text += f"💰 {o['final_amount']:,} تومان\n"
        text += f"📅 {created}\n"
        text += "━━━━━━━━━━━━━━━━━━━━━\n"

    keyboard = InlineKeyboardMarkup(row_width=1)
    for o in orders[:10]:
        keyboard.add(InlineKeyboardButton(
            f"📤 تحویل {o['tracking_code']}",
            callback_data=f"deliver_{o['id']}_{o['telegram_id']}"
        ))
    keyboard.add(InlineKeyboardButton("🔙 برگشت", callback_data="back"))

    bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("deliver_"))
def deliver_from_pending(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!")
        return

    parts = call.data.split("_")
    order_id = int(parts[1])
    user_id = int(parts[2])

    order = get_order(order_id)
    if not order:
        bot.answer_callback_query(call.id, "❌ سفارش یافت نشد!")
        return

    bot.answer_callback_query(call.id, "📤")
    msg = bot.send_message(call.message.chat.id, f"📤 لطفاً سرور رو برای سفارش {order['tracking_code']} ارسال کن:")
    bot.register_next_step_handler(msg, send_server_to_user, order_id, user_id)

# ============================================================
# پنل ادمین
# ============================================================
def show_pending_orders(chat_id):
    try:
        res = db.table("orders").select("*").eq("status", "pending").order("created_at", desc=True).limit(30).execute()
        orders = res.data
    except Exception as e:
        log.error(f"خطا در دریافت سفارشات در انتظار: {e}")
        orders = []

    if not orders:
        bot.send_message(chat_id, "📋 هیچ سفارشی در انتظار نیست.")
        return
    text = "📋 <b>سفارشات در انتظار</b>\n━━━━━━━━━━━━━━\n\n"
    for o in orders:
        created = to_jalali(o['created_at']) if o.get('created_at') else "—"
        text += f"🆔 <code>{o['telegram_id']}</code>\n{o['product']}\n{o['final_amount']:,} تومان · 🔖 {o['tracking_code']}\n📅 {created}\n\n"
    bot.send_message(chat_id, text, parse_mode="HTML")

def show_users_list(chat_id):
    try:
        res = db.table("app_users").select("*").order("created_at", desc=True).limit(50).execute()
        users = res.data
    except Exception as e:
        log.error(f"خطا در دریافت لیست کاربران: {e}")
        users = []

    banned_count = sum(1 for u in users if u["is_banned"])
    text = f"👥 <b>لیست کاربران</b>\n━━━━━━━━━━━━━━\n\n📊 کل (۵۰ نفر آخر): {len(users)}\n🚫 بن‌شده: {banned_count}\n\n"
    for i, u in enumerate(users[:20], 1):
        status = "🚫" if u["is_banned"] else "✅"
        uname = u.get("username")
        level = u.get("user_level", "عادی")
        is_active = "✅" if u.get("is_active") else "🔴"
        created = to_jalali(u['created_at']) if u.get('created_at') else "—"
        text += f"{i}. {status} <code>{u['telegram_id']}</code>" + (f" (@{uname})" if uname else "") + f" | 👛 {u['wallet_balance']:,} | 🏷 {level} | {is_active}\n📅 {created}\n"
    bot.send_message(chat_id, text, parse_mode="HTML")

def show_stats(chat_id):
    try:
        users_count = len(db.table("app_users").select("telegram_id").execute().data)
        confirmed_orders = db.table("orders").select("final_amount, type").in_("status", ["confirmed", "delivered"]).execute().data
        pending_count = len(db.table("orders").select("id").eq("status", "pending").execute().data)
    except Exception as e:
        log.error(f"خطا در دریافت آمار: {e}")
        users_count = 0
        confirmed_orders = []
        pending_count = 0

    total_sales = sum(o["final_amount"] for o in confirmed_orders)
    vpn_orders = sum(1 for o in confirmed_orders if o["type"] == "vpn")
    stars_orders = sum(1 for o in confirmed_orders if o["type"] == "stars")
    topup_total = sum(o["final_amount"] for o in confirmed_orders if o["type"] == "wallet_topup")

    bot.send_message(chat_id, f"""📊 <b>آمار فروش</b>
━━━━━━━━━━━━━━
👥 کل کاربران: {users_count}
📦 سفارشات تایید‌شده: {len(confirmed_orders)}
   • VPN: {vpn_orders} | ⭐ استارز: {stars_orders}
💰 کل فروش: {total_sales:,} تومان
👛 کل شارژ کیف پول: {topup_total:,} تومان
⏳ در انتظار: {pending_count}
━━━━━━━━━━━━━━
📅 {now_jalali()}""", parse_mode="HTML")

def broadcast_message(message):
    if message.from_user.id != ADMIN_ID:
        return
    if message.text and message.text.strip() == "/cancel":
        bot.reply_to(message, "❌ لغو شد.")
        return
    if intercept_flow_restart(message):
        return
    msg = (message.text or "").strip()
    if not msg:
        bot.reply_to(message, "❌ پیام خالی.")
        return

    try:
        users = db.table("app_users").select("telegram_id, is_banned").execute().data
        targets = [u["telegram_id"] for u in users if not u["is_banned"]]
    except Exception as e:
        log.error(f"خطا در دریافت لیست کاربران برای پیام همگانی: {e}")
        bot.reply_to(message, "❌ خطا در دریافت لیست کاربران.")
        return

    status_msg = bot.reply_to(message, f"📨 در حال ارسال به {len(targets)} کاربر...")
    sent, failed = 0, 0
    for uid in targets:
        try:
            bot.send_message(uid, f"📨 پیام از ادمین:\n\n{msg}")
            sent += 1
        except telebot.apihelper.ApiTelegramException:
            failed += 1
        time.sleep(0.05)
    safe_edit(status_msg.chat.id, status_msg.message_id, f"✅ ارسال تمام شد.\n📬 موفق: {sent}\n❌ ناموفق: {failed}")

def ask_ban_target(message):
    if message.from_user.id != ADMIN_ID:
        return
    if intercept_flow_restart(message):
        return
    target = (message.text or "").strip()
    if not target.isdigit():
        bot.reply_to(message, "❌ آیدی باید عددی باشه.")
        return
    user = get_user(int(target))
    status = "🚫 بن است" if (user and user["is_banned"]) else "✅ بن نیست"
    bot.reply_to(message, f"کاربر {target}\n{status}\n\nچه کاری انجام بشه؟", reply_markup=ban_unban_keyboard(target))

@bot.callback_query_handler(func=lambda call: call.data.startswith("doban_"))
def cb_do_ban(call):
    if call.from_user.id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id, "✅")
    target = int(call.data.replace("doban_", ""))
    set_banned(target, True)
    safe_edit(call.message.chat.id, call.message.message_id, f"✅ کاربر {target} بن شد.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("unban_"))
def cb_do_unban(call):
    if call.from_user.id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id, "✅")
    target = int(call.data.replace("unban_", ""))
    set_banned(target, False)
    safe_edit(call.message.chat.id, call.message.message_id, f"✅ کاربر {target} آنبن شد.")

# ============================================================
# تعیین سطح کاربر
# ============================================================
def ask_user_level_target(message):
    if message.from_user.id != ADMIN_ID:
        return
    if intercept_flow_restart(message):
        return
    
    target = (message.text or "").strip()
    if not target.isdigit():
        bot.reply_to(message, "❌ آیدی باید عددی باشه.")
        return
    
    user = get_user(int(target))
    if not user:
        bot.reply_to(message, "❌ کاربر پیدا نشد!")
        return
    
    current_level = user.get("user_level", "عادی")
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🟢 عادی", callback_data=f"setlevel_{target}_عادی"),
        InlineKeyboardButton("🟡 نقره‌ای", callback_data=f"setlevel_{target}_نقره‌ای"),
        InlineKeyboardButton("🔵 طلایی", callback_data=f"setlevel_{target}_طلایی"),
        InlineKeyboardButton("🟣 ویژه", callback_data=f"setlevel_{target}_ویژه")
    )
    keyboard.add(InlineKeyboardButton("🔙 انصراف", callback_data="back"))
    
    bot.reply_to(
        message, 
        f"👤 کاربر: <code>{target}</code>\n"
        f"📊 سطح فعلی: {current_level}\n\n"
        f"سطح جدید رو انتخاب کن:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("setlevel_"))
def set_user_level(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!")
        return
    
    parts = call.data.split("_")
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "❌ خطا در پردازش!")
        return
    target_id = int(parts[1])
    new_level = parts[2]
    
    try:
        db.table("app_users").update({"user_level": new_level}).eq("telegram_id", target_id).execute()
        
        if target_id in _user_cache:
            _user_cache[target_id]["user_level"] = new_level
        
        bot.answer_callback_query(call.id, f"✅ سطح به {new_level} تغییر کرد!")
        
        safe_edit(
            call.message.chat.id, 
            call.message.message_id, 
            f"✅ سطح کاربر {target_id} به «{new_level}» تغییر کرد."
        )
        
        try:
            bot.send_message(
                target_id,
                f"🔔 سطح کاربری شما توسط ادمین به «{new_level}» تغییر یافت."
            )
        except:
            pass
            
    except Exception as e:
        log.error(f"خطا در تغییر سطح کاربر: {e}")
        bot.answer_callback_query(call.id, "❌ خطا در تغییر سطح!")

# ============================================================
# مدیریت موجودی کاربر (افزایش/کاهش/صفر کردن)
# ============================================================
def ask_wallet_manage_user(message):
    if message.from_user.id != ADMIN_ID:
        return
    if intercept_flow_restart(message):
        return
    
    target = (message.text or "").strip()
    if not target.isdigit():
        bot.reply_to(message, "❌ آیدی باید عددی باشه.")
        return
    
    user = get_user(int(target))
    if not user:
        bot.reply_to(message, "❌ کاربر پیدا نشد!")
        return
    
    _discount_builder[message.from_user.id] = {"manage_user": int(target)}
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("➕ افزایش", callback_data=f"wallet_add_{target}"),
        InlineKeyboardButton("➖ کاهش", callback_data=f"wallet_sub_{target}"),
        InlineKeyboardButton("🔄 صفر کردن", callback_data=f"wallet_zero_{target}")
    )
    keyboard.add(InlineKeyboardButton("🔙 انصراف", callback_data="back"))
    
    bot.reply_to(
        message,
        f"👤 کاربر: <code>{target}</code>\n"
        f"💰 موجودی فعلی: {user['wallet_balance']:,} تومان\n\n"
        f"عملیات مورد نظر رو انتخاب کن:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("wallet_add_"))
def wallet_add_amount(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!")
        return
    
    parts = call.data.split("_")
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "❌ خطا در پردازش!")
        return
    target_id = int(parts[2])
    
    user = get_user(target_id)
    if not user:
        bot.answer_callback_query(call.id, "❌ کاربر پیدا نشد!")
        return
    
    bot.answer_callback_query(call.id, "➕")
    
    _discount_builder[call.from_user.id] = {
        "manage_user": target_id,
        "action": "add"
    }
    
    msg = bot.send_message(
        call.message.chat.id,
        f"👤 کاربر: <code>{target_id}</code>\n"
        f"💰 موجودی فعلی: {user['wallet_balance']:,} تومان\n\n"
        f"✏️ مبلغ افزایش رو به تومان وارد کن:\n"
        f"(مثلاً 50000 برای ۵۰ هزار تومان)",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, process_wallet_add)

@bot.callback_query_handler(func=lambda call: call.data.startswith("wallet_sub_"))
def wallet_sub_amount(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!")
        return
    
    parts = call.data.split("_")
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "❌ خطا در پردازش!")
        return
    target_id = int(parts[2])
    
    user = get_user(target_id)
    if not user:
        bot.answer_callback_query(call.id, "❌ کاربر پیدا نشد!")
        return
    
    bot.answer_callback_query(call.id, "➖")
    
    _discount_builder[call.from_user.id] = {
        "manage_user": target_id,
        "action": "sub"
    }
    
    msg = bot.send_message(
        call.message.chat.id,
        f"👤 کاربر: <code>{target_id}</code>\n"
        f"💰 موجودی فعلی: {user['wallet_balance']:,} تومان\n\n"
        f"✏️ مبلغ کاهش رو به تومان وارد کن:\n"
        f"(مثلاً 30000 برای ۳۰ هزار تومان)",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, process_wallet_sub)

@bot.callback_query_handler(func=lambda call: call.data.startswith("wallet_zero_"))
def wallet_zero_confirm(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!")
        return
    
    parts = call.data.split("_")
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "❌ خطا در پردازش!")
        return
    target_id = int(parts[2])
    
    user = get_user(target_id)
    if not user:
        bot.answer_callback_query(call.id, "❌ کاربر پیدا نشد!")
        return
    
    current_balance = user['wallet_balance']
    
    if current_balance == 0:
        bot.answer_callback_query(call.id, "❌ موجودی در حال حاضر صفر است!")
        safe_edit(
            call.message.chat.id,
            call.message.message_id,
            f"⚠️ موجودی کاربر <code>{target_id}</code> در حال حاضر صفر است.",
            parse_mode="HTML"
        )
        return
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✅ بله، صفر کن", callback_data=f"wallet_zero_confirm_{target_id}"),
        InlineKeyboardButton("❌ انصراف", callback_data="back")
    )
    
    safe_edit(
        call.message.chat.id,
        call.message.message_id,
        f"⚠️ <b>هشدار! صفر کردن موجودی</b>\n\n"
        f"👤 کاربر: <code>{target_id}</code>\n"
        f"💰 موجودی فعلی: {current_balance:,} تومان\n\n"
        f"آیا مطمئنی که می‌خوای موجودی این کاربر رو صفر کنی؟",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("wallet_zero_confirm_"))
def wallet_zero_execute(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!")
        return
    
    parts = call.data.split("_")
    if len(parts) < 4:
        bot.answer_callback_query(call.id, "❌ خطا در پردازش!")
        return
    target_id = int(parts[3])
    
    user = get_user(target_id)
    if not user:
        bot.answer_callback_query(call.id, "❌ کاربر پیدا نشد!")
        return
    
    current_balance = user['wallet_balance']
    
    if current_balance == 0:
        bot.answer_callback_query(call.id, "❌ موجودی در حال حاضر صفر است!")
        return
    
    new_balance = adjust_wallet(target_id, -current_balance, "admin_zero_wallet")
    
    if new_balance is not None:
        bot.answer_callback_query(call.id, "✅ موجودی صفر شد!")
        
        safe_edit(
            call.message.chat.id,
            call.message.message_id,
            f"✅ <b>موجودی با موفقیت صفر شد!</b>\n\n"
            f"👤 کاربر: <code>{target_id}</code>\n"
            f"💰 مبلغ حذف شده: {current_balance:,} تومان\n"
            f"💰 موجودی جدید: 0 تومان",
            parse_mode="HTML"
        )
        
        try:
            bot.send_message(
                target_id,
                f"⚠️ <b>موجودی کیف پول شما صفر شد!</b>\n\n"
                f"مبلغ {current_balance:,} تومان از کیف پول شما حذف شد.\n"
                f"💰 موجودی جدید: 0 تومان",
                parse_mode="HTML"
            )
        except telebot.apihelper.ApiTelegramException:
            pass
    else:
        bot.answer_callback_query(call.id, "❌ خطا!")

def process_wallet_add(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    if intercept_flow_restart(message):
        return
    
    data = _discount_builder.get(message.from_user.id)
    if not data or "manage_user" not in data or data.get("action") != "add":
        bot.reply_to(message, "❌ خطا! دوباره از ابتدا تلاش کن.")
        return
    
    target_id = data["manage_user"]
    
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except:
        msg = bot.reply_to(
            message,
            "❌ لطفاً یک عدد معتبر (بزرگتر از صفر) به تومان وارد کن:"
        )
        bot.register_next_step_handler(msg, process_wallet_add)
        return
    
    new_balance = adjust_wallet(target_id, amount, "admin_add_wallet")
    
    if new_balance is not None:
        del _discount_builder[message.from_user.id]
        
        bot.reply_to(
            message,
            f"✅ <b>افزایش موجودی با موفقیت انجام شد!</b>\n\n"
            f"👤 کاربر: <code>{target_id}</code>\n"
            f"💰 مبلغ افزایش: {amount:,} تومان\n"
            f"💰 موجودی جدید: {new_balance:,} تومان",
            parse_mode="HTML"
        )
        
        try:
            bot.send_message(
                target_id,
                f"💰 <b>موجودی کیف پول شما افزایش یافت!</b>\n\n"
                f"مبلغ {amount:,} تومان به کیف پول شما اضافه شد.\n"
                f"💰 موجودی جدید: {new_balance:,} تومان",
                parse_mode="HTML"
            )
        except telebot.apihelper.ApiTelegramException:
            bot.reply_to(message, "⚠️ ارسال پیام به کاربر ممکن نشد (کاربر ربات رو بلاک کرده).")
    else:
        bot.reply_to(message, "❌ خطا در افزایش موجودی!")

def process_wallet_sub(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    if intercept_flow_restart(message):
        return
    
    data = _discount_builder.get(message.from_user.id)
    if not data or "manage_user" not in data or data.get("action") != "sub":
        bot.reply_to(message, "❌ خطا! دوباره از ابتدا تلاش کن.")
        return
    
    target_id = data["manage_user"]
    user = get_user(target_id)
    current_balance = user["wallet_balance"] if user else 0
    
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
        if amount > current_balance:
            bot.reply_to(
                message,
                f"❌ موجودی کاربر ({current_balance:,} تومان) کمتر از مبلغ مورد نظر است!\n"
                f"لطفاً مبلغ کمتری وارد کن:"
            )
            bot.register_next_step_handler(message, process_wallet_sub)
            return
    except:
        msg = bot.reply_to(
            message,
            f"❌ لطفاً یک عدد معتبر (بزرگتر از صفر و کمتر از {current_balance:,}) به تومان وارد کن:"
        )
        bot.register_next_step_handler(msg, process_wallet_sub)
        return
    
    new_balance = adjust_wallet(target_id, -amount, "admin_sub_wallet")
    
    if new_balance is not None:
        del _discount_builder[message.from_user.id]
        
        bot.reply_to(
            message,
            f"✅ <b>کاهش موجودی با موفقیت انجام شد!</b>\n\n"
            f"👤 کاربر: <code>{target_id}</code>\n"
            f"💰 مبلغ کاهش: {amount:,} تومان\n"
            f"💰 موجودی جدید: {new_balance:,} تومان",
            parse_mode="HTML"
        )
        
        try:
            bot.send_message(
                target_id,
                f"⚠️ <b>موجودی کیف پول شما کاهش یافت!</b>\n\n"
                f"مبلغ {amount:,} تومان از کیف پول شما کسر شد.\n"
                f"💰 موجودی جدید: {new_balance:,} تومان",
                parse_mode="HTML"
            )
        except telebot.apihelper.ApiTelegramException:
            bot.reply_to(message, "⚠️ ارسال پیام به کاربر ممکن نشد (کاربر ربات رو بلاک کرده).")
    else:
        bot.reply_to(message, "❌ خطا در کاهش موجودی!")

# ============================================================
# مدیریت کد تخفیف (بخش کامل)
# ============================================================
def show_discount_menu(chat_id):
    bot.send_message(
        chat_id,
        "🏷 <b>مدیریت کدهای تخفیف</b>\n\n"
        "یکی از گزینه‌های زیر رو انتخاب کن:",
        reply_markup=discount_management_keyboard(),
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data == "discount_create")
def discount_create_start(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!")
        return
    
    bot.answer_callback_query(call.id, "➕")
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🇫🇷 فرانسه", callback_data="discount_plan_france"),
        InlineKeyboardButton("🌍 مولتی", callback_data="discount_plan_multi"),
        InlineKeyboardButton("🚀 نامحدود", callback_data="discount_plan_unlimited"),
        InlineKeyboardButton("⭐ استارز", callback_data="discount_plan_stars"),
        InlineKeyboardButton("🎯 همه موارد", callback_data="discount_plan_all")
    )
    keyboard.add(InlineKeyboardButton("🔙 انصراف", callback_data="back"))
    
    safe_edit(
        call.message.chat.id,
        call.message.message_id,
        "🏷 <b>ساخت کد تخفیف جدید</b>\n\n"
        "مرحله ۱: انتخاب سرویس مورد نظر برای تخفیف:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("discount_plan_"))
def discount_select_plan(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!")
        return
    
    plan = call.data.replace("discount_plan_", "")
    user_id = call.from_user.id
    
    _discount_builder[user_id] = {"plan": plan}
    
    plan_names = {
        "france": "🇫🇷 سرور فرانسه",
        "multi": "🌍 سرور مولتی",
        "unlimited": "🚀 سرور نامحدود",
        "stars": "⭐ استارز",
        "all": "🎯 همه موارد"
    }
    
    bot.answer_callback_query(call.id, f"✅ {plan_names.get(plan, plan)}")
    
    msg = bot.send_message(
        call.message.chat.id,
        f"🏷 <b>ساخت کد تخفیف</b>\n\n"
        f"📌 سرویس انتخاب شده: {plan_names.get(plan, plan)}\n\n"
        f"مرحله ۲: درصد تخفیف رو وارد کن (مثلاً ۲۰ برای ۲۰٪):\n"
        f"(فقط عدد بین ۱ تا ۱۰۰)",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, discount_get_percent, user_id)

def discount_get_percent(message, user_id):
    if message.from_user.id != ADMIN_ID:
        return
    
    if intercept_flow_restart(message):
        return
    
    try:
        percent = int(message.text.strip())
        if percent < 1 or percent > 100:
            raise ValueError
    except:
        msg = bot.reply_to(
            message,
            "❌ لطفاً یک عدد بین ۱ تا ۱۰۰ وارد کن:"
        )
        bot.register_next_step_handler(msg, discount_get_percent, user_id)
        return
    
    _discount_builder[user_id]["percent"] = percent
    
    msg = bot.reply_to(
        message,
        f"🏷 <b>ساخت کد تخفیف</b>\n\n"
        f"✅ درصد تخفیف: {percent}٪\n\n"
        f"مرحله ۳: مدت اعتبار کد رو به روز وارد کن:\n"
        f"(مثلاً ۳۰ برای ۳۰ روز)\n"
        f"(۰ = نامحدود)",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, discount_get_days, user_id)

def discount_get_days(message, user_id):
    if message.from_user.id != ADMIN_ID:
        return
    
    if intercept_flow_restart(message):
        return
    
    try:
        days = int(message.text.strip())
        if days < 0:
            raise ValueError
    except:
        msg = bot.reply_to(
            message,
            "❌ لطفاً یک عدد معتبر وارد کن (۰ برای نامحدود):"
        )
        bot.register_next_step_handler(msg, discount_get_days, user_id)
        return
    
    _discount_builder[user_id]["days"] = days
    
    msg = bot.reply_to(
        message,
        f"🏷 <b>ساخت کد تخفیف</b>\n\n"
        f"✅ درصد تخفیف: {_discount_builder[user_id]['percent']}٪\n"
        f"✅ مدت اعتبار: {days if days > 0 else 'نامحدود'} روز\n\n"
        f"مرحله ۴: حداکثر تعداد استفاده رو وارد کن:\n"
        f"(مثلاً ۱۰۰)\n"
        f"(۰ = نامحدود)",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, discount_get_max_uses, user_id)

def discount_get_max_uses(message, user_id):
    if message.from_user.id != ADMIN_ID:
        return
    
    if intercept_flow_restart(message):
        return
    
    try:
        max_uses = int(message.text.strip())
        if max_uses < 0:
            raise ValueError
    except:
        msg = bot.reply_to(
            message,
            "❌ لطفاً یک عدد معتبر وارد کن (۰ برای نامحدود):"
        )
        bot.register_next_step_handler(msg, discount_get_max_uses, user_id)
        return
    
    _discount_builder[user_id]["max_uses"] = max_uses
    
    data = _discount_builder[user_id]
    plan_names = {
        "france": "🇫🇷 سرور فرانسه",
        "multi": "🌍 سرور مولتی",
        "unlimited": "🚀 سرور نامحدود",
        "stars": "⭐ استارز",
        "all": "🎯 همه موارد"
    }
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✅ تایید و ساخت", callback_data=f"discount_confirm_{user_id}"),
        InlineKeyboardButton("❌ لغو", callback_data="back")
    )
    
    bot.send_message(
        message.chat.id,
        f"🏷 <b>تایید نهایی کد تخفیف</b>\n\n"
        f"📌 سرویس: {plan_names.get(data['plan'], data['plan'])}\n"
        f"🎯 درصد تخفیف: {data['percent']}٪\n"
        f"📅 مدت اعتبار: {data['days'] if data['days'] > 0 else 'نامحدود'} روز\n"
        f"🔢 حداکثر استفاده: {data['max_uses'] if data['max_uses'] > 0 else 'نامحدود'}\n\n"
        f"آیا اطلاعات صحیح است؟",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("discount_confirm_"))
def discount_confirm(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!")
        return
    
    user_id = int(call.data.replace("discount_confirm_", ""))
    data = _discount_builder.get(user_id)
    
    if not data:
        bot.answer_callback_query(call.id, "❌ اطلاعات یافت نشد!")
        return
    
    code = f"VIP{gen_code(8)}"
    
    expires_at = None
    if data['days'] > 0:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=data['days'])).isoformat()
    
    try:
        db.table("discount_codes").insert({
            "code": code,
            "percent": data['percent'],
            "plan": data['plan'],
            "max_uses": data['max_uses'] if data['max_uses'] > 0 else None,
            "expires_at": expires_at,
            "active": True,
            "used_count": 0
        }).execute()
        
        del _discount_builder[user_id]
        
        bot.answer_callback_query(call.id, "✅ کد تخفیف ساخته شد!")
        
        safe_edit(
            call.message.chat.id,
            call.message.message_id,
            f"✅ <b>کد تخفیف با موفقیت ساخته شد!</b>\n\n"
            f"🔖 کد تخفیف: <code>{code}</code>\n"
            f"🎯 درصد: {data['percent']}٪\n"
            f"📌 سرویس: {data['plan']}\n"
            f"📅 اعتبار: {data['days'] if data['days'] > 0 else 'نامحدود'} روز\n"
            f"🔢 حداکثر استفاده: {data['max_uses'] if data['max_uses'] > 0 else 'نامحدود'}\n\n"
            f"💡 کاربران می‌تونن با وارد کردن این کد از تخفیف استفاده کنن.",
            parse_mode="HTML"
        )
        
    except Exception as e:
        log.error(f"خطا در ساخت کد تخفیف: {e}")
        bot.answer_callback_query(call.id, "❌ خطا در ساخت کد!")

@bot.callback_query_handler(func=lambda call: call.data == "discount_list")
def discount_list(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!")
        return
    
    bot.answer_callback_query(call.id)
    
    try:
        res = db.table("discount_codes").select("*").order("created_at", desc=True).limit(30).execute()
        codes = res.data
    except Exception as e:
        log.error(f"خطا در دریافت لیست کدهای تخفیف: {e}")
        codes = []
    
    if not codes:
        safe_edit(
            call.message.chat.id,
            call.message.message_id,
            "📋 هیچ کد تخفیفی وجود ندارد.\n\n"
            "از دکمه «➕ ساخت کد جدید» استفاده کن.",
            reply_markup=discount_management_keyboard()
        )
        return
    
    plan_names = {
        "france": "🇫🇷 فرانسه",
        "multi": "🌍 مولتی",
        "unlimited": "🚀 نامحدود",
        "stars": "⭐ استارز",
        "all": "🎯 همه"
    }
    
    text = "📋 <b>لیست کدهای تخفیف</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, c in enumerate(codes[:15], 1):
        status = "✅ فعال" if c["active"] else "❌ غیرفعال"
        plan = plan_names.get(c.get("plan", "all"), "همه")
        used = c.get("used_count", 0)
        max_uses = c.get("max_uses", "∞") if c.get("max_uses") is not None else "∞"
        expires = "نامحدود"
        if c.get("expires_at"):
            expires = to_jalali(c["expires_at"])[:16]
        
        text += f"{i}. 🔖 <code>{c['code']}</code>\n"
        text += f"   🎯 {c['percent']}٪ | 📌 {plan}\n"
        text += f"   🔢 {used}/{max_uses} | 📅 {expires}\n"
        text += f"   {status}\n"
        text += "━━━━━━━━━━━━━━━━━━━━━\n"
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🔄 بروزرسانی", callback_data="discount_list"),
        InlineKeyboardButton("🔙 برگشت", callback_data="back")
    )
    
    safe_edit(
        call.message.chat.id,
        call.message.message_id,
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data == "discount_broadcast")
def discount_broadcast_start(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!")
        return
    
    bot.answer_callback_query(call.id, "📤")
    
    try:
        res = db.table("discount_codes").select("*").eq("active", True).execute()
        codes = res.data
    except Exception as e:
        log.error(f"خطا در دریافت کدهای فعال: {e}")
        codes = []
    
    if not codes:
        safe_edit(
            call.message.chat.id,
            call.message.message_id,
            "❌ هیچ کد تخفیف فعالی وجود ندارد.\n\n"
            "ابتدا یک کد تخفیف جدید بساز.",
            reply_markup=discount_management_keyboard()
        )
        return
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for c in codes[:10]:
        keyboard.add(
            InlineKeyboardButton(
                f"🔖 {c['code']} ({c['percent']}٪)",
                callback_data=f"broadcast_code_{c['code']}"
            )
        )
    keyboard.add(InlineKeyboardButton("🔙 برگشت", callback_data="back"))
    
    safe_edit(
        call.message.chat.id,
        call.message.message_id,
        "📤 <b>ارسال اطلاع‌رسانی کد تخفیف</b>\n\n"
        "کد تخفیفی که می‌خوای به کاربران اطلاع‌رسانی کنی رو انتخاب کن:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("broadcast_code_"))
def discount_broadcast_send(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!")
        return
    
    code = call.data.replace("broadcast_code_", "")
    
    try:
        res = db.table("discount_codes").select("*").eq("code", code).execute()
        if not res.data:
            bot.answer_callback_query(call.id, "❌ کد یافت نشد!")
            return
        discount = res.data[0]
    except Exception as e:
        log.error(f"خطا در دریافت اطلاعات کد: {e}")
        bot.answer_callback_query(call.id, "❌ خطا!")
        return
    
    plan_names = {
        "france": "🇫🇷 سرور فرانسه",
        "multi": "🌍 سرور مولتی",
        "unlimited": "🚀 سرور نامحدود",
        "stars": "⭐ استارز",
        "all": "🎯 همه سرویس‌ها"
    }
    
    plan_name = plan_names.get(discount.get("plan", "all"), "همه سرویس‌ها")
    
    message_text = f"""🎉 <b>کد تخفیف ویژه</b>

📌 <b>سرویس:</b> {plan_name}
🎯 <b>تخفیف:</b> {discount['percent']}٪
🔖 <b>کد:</b> <code>{discount['code']}</code>

💡 <b>نحوه استفاده:</b>
هنگام خرید، کد تخفیف رو وارد کن تا {discount['percent']}٪ تخفیف بگیری!

📅 <b>مدت اعتبار:</b> {'نامحدود' if not discount.get('expires_at') else to_jalali(discount['expires_at'])[:16]}
🔢 <b>تعداد باقیمانده:</b> {'نامحدود' if discount.get('max_uses') is None else discount['max_uses'] - discount['used_count']}

🚀 همین حالا استفاده کن!"""
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✅ ارسال به همه", callback_data=f"send_broadcast_{code}"),
        InlineKeyboardButton("❌ لغو", callback_data="back")
    )
    
    safe_edit(
        call.message.chat.id,
        call.message.message_id,
        f"📤 <b>پیش‌نمایش پیام</b>\n\n"
        f"{message_text}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ این پیام به <b>همه کاربران</b> ارسال خواهد شد.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("send_broadcast_"))
def discount_broadcast_execute(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ دسترسی غیرمجاز!")
        return
    
    code = call.data.replace("send_broadcast_", "")
    bot.answer_callback_query(call.id, "📤 در حال ارسال...")
    
    try:
        res = db.table("discount_codes").select("*").eq("code", code).execute()
        if not res.data:
            bot.answer_callback_query(call.id, "❌ کد یافت نشد!")
            return
        discount = res.data[0]
    except Exception as e:
        log.error(f"خطا در دریافت اطلاعات کد: {e}")
        bot.answer_callback_query(call.id, "❌ خطا!")
        return
    
    plan_names = {
        "france": "🇫🇷 سرور فرانسه",
        "multi": "🌍 سرور مولتی",
        "unlimited": "🚀 سرور نامحدود",
        "stars": "⭐ استارز",
        "all": "🎯 همه سرویس‌ها"
    }
    
    plan_name = plan_names.get(discount.get("plan", "all"), "همه سرویس‌ها")
    
    message_text = f"""🎉 <b>کد تخفیف ویژه</b>

📌 <b>سرویس:</b> {plan_name}
🎯 <b>تخفیف:</b> {discount['percent']}٪
🔖 <b>کد:</b> <code>{discount['code']}</code>

💡 <b>نحوه استفاده:</b>
هنگام خرید، کد تخفیف رو وارد کن تا {discount['percent']}٪ تخفیف بگیری!

📅 <b>مدت اعتبار:</b> {'نامحدود' if not discount.get('expires_at') else to_jalali(discount['expires_at'])[:16]}
🔢 <b>تعداد باقیمانده:</b> {'نامحدود' if discount.get('max_uses') is None else discount['max_uses'] - discount['used_count']}

🚀 همین حالا استفاده کن!"""
    
    try:
        users = db.table("app_users").select("telegram_id, is_banned").execute().data
        targets = [u["telegram_id"] for u in users if not u["is_banned"]]
    except Exception as e:
        log.error(f"خطا در دریافت لیست کاربران: {e}")
        bot.answer_callback_query(call.id, "❌ خطا در دریافت لیست کاربران!")
        return
    
    sent, failed = 0, 0
    for uid in targets:
        try:
            bot.send_message(uid, message_text, parse_mode="HTML")
            sent += 1
        except telebot.apihelper.ApiTelegramException:
            failed += 1
        time.sleep(0.05)
    
    safe_edit(
        call.message.chat.id,
        call.message.message_id,
        f"✅ <b>اطلاع‌رسانی ارسال شد!</b>\n\n"
        f"📬 موفق: {sent}\n"
        f"❌ ناموفق: {failed}\n"
        f"🔖 کد: <code>{code}</code>",
        parse_mode="HTML"
    )

# ============================================================
# سیستم ارسال کد از سایت (OTP Worker)
# ============================================================
def otp_worker():
    while True:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            res = db.table("site_link_codes").select("*").eq("delivered", False).eq("used", False).execute()
            rows = res.data if res.data else []

            for row in rows:
                if row["expires_at"] < now_iso:
                    continue
                try:
                    bot.send_message(row["telegram_id"], f"""🔐 <b>کد ورود به پنل سایت</b>
━━━━━━━━━━━━━━
کد شما (تا ۱۰ دقیقه معتبره):
<code>{row['code']}</code>
━━━━━━━━━━━━━━
این کد رو توی سایت {WEBSITE} وارد کن.""", parse_mode="HTML")
                    db.table("site_link_codes").update({"delivered": True}).eq("code", row["code"]).execute()
                    log.info(f"کد ورود سایت برای کاربر {row['telegram_id']} ارسال شد")
                except Exception as e:
                    log.error(f"خطا در ارسال کد OTP به {row['telegram_id']}: {e}")
        except Exception as e:
            log.error(f"خطا در OTP Worker: {e}")
        time.sleep(3)

# ============================================================
# دستورات ادمین
# ============================================================
@bot.message_handler(commands=["addcode"])
def addcode_cmd(message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "❌ استفاده: /addcode CODE PERCENT [MAX_USES] [DAYS_VALID]")
        return
    code = args[1].strip().upper()
    try:
        percent = int(args[2])
        if not 1 <= percent <= 100:
            raise ValueError
    except ValueError:
        bot.reply_to(message, "❌ درصد باید عددی بین ۱ تا ۱۰۰ باشه.")
        return
    max_uses = int(args[3]) if len(args) > 3 and args[3].isdigit() else None
    expires_at = None
    if len(args) > 4 and args[4].isdigit():
        expires_at = (datetime.now(timezone.utc) + timedelta(days=int(args[4]))).isoformat()

    try:
        db.table("discount_codes").upsert({
            "code": code, "percent": percent, "max_uses": max_uses,
            "expires_at": expires_at, "active": True, "used_count": 0
        }).execute()
        bot.reply_to(message, f"✅ کد تخفیف {code} ({percent}٪) ساخته شد.")
    except Exception as e:
        log.error(f"خطا در ساخت کد تخفیف: {e}")
        bot.reply_to(message, "❌ خطا در ساخت کد تخفیف.")

@bot.message_handler(commands=["ban"])
def ban_cmd(message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        bot.reply_to(message, "❌ استفاده: /ban [user_id]")
        return
    set_banned(int(args[1]), True)
    bot.reply_to(message, f"✅ {args[1]} بن شد.")

@bot.message_handler(commands=["unban"])
def unban_cmd(message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        bot.reply_to(message, "❌ استفاده: /unban [user_id]")
        return
    set_banned(int(args[1]), False)
    bot.reply_to(message, f"✅ {args[1]} آنبن شد.")

@bot.message_handler(commands=["deliver"])
def deliver_cmd(message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "❌ استفاده: /deliver TRACKING_CODE")
        return

    try:
        res = db.table("orders").select("*").eq("tracking_code", args[1]).execute()
        if not res.data:
            bot.reply_to(message, "❌ کد رهگیری پیدا نشد.")
            return
        order = res.data[0]
        msg = bot.reply_to(message, f"📤 سرور رو برای سفارش {order['tracking_code']} ارسال کن:")
        bot.register_next_step_handler(msg, send_server_to_user, order["id"], order["telegram_id"])
    except Exception as e:
        log.error(f"خطا در deliver: {e}")
        bot.reply_to(message, "❌ خطا در پیدا کردن سفارش.")

# ============================================================
# برگشت عمومی
# ============================================================
@bot.callback_query_handler(func=lambda call: call.data == "back")
def back(call):
    bot.answer_callback_query(call.id, "🔙")
    user_id = call.from_user.id
    safe_edit(call.message.chat.id, call.message.message_id, "🔙 برگشتی.")
    if user_id == ADMIN_ID:
        bot.send_message(call.message.chat.id, "📋 منوی ادمین:", reply_markup=admin_keyboard())
    else:
        bot.send_message(call.message.chat.id, "📋 منوی اصلی:", reply_markup=main_keyboard())

# ============================================================
# اجرا
# ============================================================
if __name__ == "__main__":
    log.info("=" * 50)
    log.info("🤖 ربات VPN IR روشن شد!")
    log.info(f"📢 کانال: {CHANNEL_ID}")
    log.info(f"👤 ادمین: {ADMIN_ID}")
    log.info("=" * 50)

    try:
        bot.delete_webhook()
        log.info("✅ Webhook پاک شد!")
    except Exception as e:
        log.warning(f"خطا در پاک کردن Webhook: {e}")

    otp_thread = threading.Thread(target=otp_worker, daemon=True)
    otp_thread.start()
    log.info("✅ OTP Worker برای ارسال کد از سایت شروع شد.")

    while True:
        try:
            bot.infinity_polling(
                skip_pending=True, 
                timeout=30, 
                long_polling_timeout=30,
                restart_on_change=False
            )
        except Exception as e:
            log.error(f"ربات با خطا متوقف شد، ۵ ثانیه دیگه دوباره امتحان می‌کنیم: {e}")
            time.sleep(5)