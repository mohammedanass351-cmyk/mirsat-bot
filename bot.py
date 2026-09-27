# -*- coding: utf-8 -*-
"""
بوت إدارة قناة "مِرْسَاة التربية الإسلامية"
==============================================
يقوم هذا البوت بـ:
  1. استقبال الملفات من الأعضاء (في مجموعة الاستقبال أو في الخاص).
  2. التحقق من وجود الوسوم الإلزامية في التعليق (المستوى + القسم).
  3. إعادة تسمية الملف تلقائيًا وفق نظام التسمية الموحد.
  4. تحويل الملف لقائمة انتظار مراجعة المشرفين (لا يُنشر تلقائيًا بدون موافقة، حفاظًا على الجودة).
  5. عند موافقة المشرف: نشر الملف في القناة الصحيحة مع الوسوم والتنسيق.
  6. دعم أمر /فهرس لعرض الفهرس الرئيسي، و/بحث لاسترجاع الملفات حسب الوسم.

قبل التشغيل: عدّل قسم "الإعدادات" أسفله بمعلوماتك الخاصة.
"""

import logging
import re
from dataclasses import dataclass, field

from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ====================== الإعدادات (عدّل هذا القسم فقط) ======================

BOT_TOKEN =  "8935587557:AAHcEFpzctI0pB4nQkkXqGjlKw6WetX4Ty0" 
ADMIN_IDS = [6796254856]        # أرقام تيليجرام (user_id) للمشرفين، رقم لكل مشرف
CHANNEL_ID = -1004486675278               # معرّف القناة الرقمي (يبدأ بـ -100)
INTAKE_CHAT_ID = -1004349399598           # معرّف مجموعة استقبال الملفات (يمكن أن تكون مجموعة النقاش نفسها)

# وسوم المستوى المقبولة
LEVEL_TAGS = ["1AM", "2AM", "3AM", "4AM"]

# وسوم الأقسام المقبولة وربطها بعنوان القسم الظاهر للمستخدم
SECTION_TAGS = {
    "مذكرات": "📚 المذكرات حسب السنوات الدراسية",
    "وثائق_الأستاذ": "👨‍🏫 وثائق الأستاذ الإدارية",
    "وثائق_رسمية": "📑 الوثائق الرسمية والتربوية",
    "تقويم_تشخيصي": "📝 التقويم التشخيصي",
    "فروض": "📋 الفروض",
    "اختبارات": "📋 الاختبارات",
    "تصحيح": "✅ تصحيح الفروض والاختبارات",
    "تخطيط": "📅 التخطيط والتنظيم السنوي",
    "تكوين": "🎓 تكوين الأستاذ الجديد",
    "أفكار_وخبرات": "💡 بنك الأفكار والخبرات",
    "وسائل_تعليمية": "🎨 الوسائل التعليمية والأنشطة",
}

SCHOOL_YEAR_TAG = "2026_2027"

INDEX_MESSAGE = (
    "🔎 *الفهرس الرئيسي*\n"
    "📚 المذكرات حسب السنوات الدراسية\n"
    "👨‍🏫 وثائق الأستاذ الإدارية\n"
    "📑 الوثائق الرسمية والتربوية\n"
    "📝 التقويم التشخيصي\n"
    "📋 الفروض والاختبارات\n"
    "✅ تصحيح الفروض والاختبارات\n"
    "📅 التخطيط والتنظيم السنوي\n"
    "🎓 تكوين الأستاذ الجديد\n"
    "💡 بنك الأفكار والخبرات\n"
    "🎨 الوسائل التعليمية والأنشطة\n\n"
    "استخدم الأمر /بحث متبوعًا بالوسم، مثال:\n"
    "`/بحث #4AM #مذكرات`"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# قائمة انتظار مؤقتة (في الذاكرة) للملفات بانتظار موافقة المشرف
# في نسخة إنتاجية حقيقية يُستحسن استبدالها بقاعدة بيانات (sqlite) حتى لا تُفقد عند إعادة التشغيل
@dataclass
class PendingFile:
    file_id: str
    file_type: str          # "document" | "photo" | "video"
    original_name: str
    caption: str
    level: str
    sections: list
    sender_id: int
    sender_name: str


PENDING: dict[int, PendingFile] = {}
_next_id = 1


def _new_id() -> int:
    global _next_id
    i = _next_id
    _next_id += 1
    return i


# ====================== أدوات مساعدة ======================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def extract_tags(text: str) -> list:
    """يستخرج كل الوسوم (#كلمة) من نص التعليق."""
    if not text:
        return []
    # تحويل الأرقام العربية الشرقية إلى أرقام إنجليزية (بعض لوحات المفاتيح تكتبها تلقائيًا)
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    english_digits = "0123456789"
    text = text.translate(str.maketrans(arabic_digits, english_digits))
    # إزالة حروف الاتجاه المخفية (LRM, RLM, ALM, ZWJ, ZWNJ) التي تضيفها بعض لوحات المفاتيح
    # عند المزج بين نص عربي وإنجليزي في نفس السطر
    hidden_chars = "\u200b\u200c\u200d\u200e\u200f\u061c"
    text = "".join(ch for ch in text if ch not in hidden_chars)
    return re.findall(r"#(\S+)", text)


def validate_tags(tags: list):
    """
    يتحقق من وجود وسم مستوى واحد على الأقل ووسم قسم واحد على الأقل.
    يعيد (المستوى, [الأقسام]) أو يرفع ValueError مع رسالة توضيحية بالعربية.
    """
    level = next((t for t in tags if t in LEVEL_TAGS), None)
    sections = [t for t in tags if t in SECTION_TAGS]

    if not level:
        raise ValueError(
            "⚠️ لم أجد وسم المستوى. أضف واحدًا من: "
            + " ".join(f"#{t}" for t in LEVEL_TAGS)
        )
    if not sections:
        raise ValueError(
            "⚠️ لم أجد وسم القسم. أضف واحدًا من:\n"
            + " ".join(f"#{t}" for t in SECTION_TAGS)
        )
    return level, sections


def build_filename(level: str, sections: list, title_hint: str, source: str) -> str:
    """
    يبني اسم الملف وفق الصيغة الموحدة:
    [المستوى]_[القسم]_[العنوان]_[المصدر]
    """
    section_part = "_".join(sections)
    title_clean = re.sub(r"[^\w\u0600-\u06FF]+", "_", title_hint).strip("_") or "ملف"
    return f"{level}_{section_part}_{title_clean}_{source}"


def build_channel_caption(level: str, sections: list, sender_name: str, source: str, user_caption: str) -> str:
    section_titles = " / ".join(SECTION_TAGS[s] for s in sections)
    tags_line = " ".join(
        [f"#{level}"] + [f"#{s}" for s in sections] + [f"#{source}", f"#{SCHOOL_YEAR_TAG}"]
    )
    clean_caption = re.sub(r"#\S+", "", user_caption or "").strip()
    parts = [f"*{section_titles}* — {level}"]
    if clean_caption:
        parts.append(clean_caption)
    parts.append(f"المصدر: {'وثيقة رسمية 🟢' if source == 'رسمي' else 'إعداد أساتذة 🔵'}")
    parts.append(f"رفعه: {sender_name}")
    parts.append(tags_line)
    return "\n\n".join(parts)


# ====================== أوامر البوت ======================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلًا بك 👋\n"
        "أرسل لي أي ملف (مذكرة، وثيقة، نموذج...) مع تعليق يحتوي وسم المستوى ووسم القسم، "
        "وسأتحقق منه وأرسله لمراجعة المشرفين قبل نشره في القناة.\n\n"
        "مثال على التعليق المطلوب:\n"
        "`مذكرة الإيمان باليوم الآخر #4AM #مذكرات #أستاذ`\n\n"
        "اكتب /فهرس لعرض فهرس الأقسام، و/مساعدة لشرح نظام الوسوم كاملًا.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tags_list = "\n".join(f"#{t}" for t in LEVEL_TAGS)
    sections_list = "\n".join(f"#{t} → {v}" for t, v in SECTION_TAGS.items())
    await update.message.reply_text(
        "📖 *نظام الوسوم*\n\n"
        f"*وسوم المستوى (اختر واحدًا):*\n{tags_list}\n\n"
        f"*وسوم القسم (اختر واحدًا أو أكثر):*\n{sections_list}\n\n"
        "*وسم المصدر (اختياري، أضفه في آخر التعليق):*\n#رسمي أو #أستاذ\n\n"
        "كل ملف بلا وسم مستوى ووسم قسم صحيحين سيُرفض تلقائيًا مع توضيح السبب.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_index(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(INDEX_MESSAGE, parse_mode=ParseMode.MARKDOWN)


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /بحث #4AM #مذكرات
    بحث بسيط تجريبي: في نسخة حقيقية يُستحسن ربطه بقاعدة بيانات لكل ما نُشر في القناة.
    هنا نكتفي بشرح الاستخدام لأن الفهرسة الفعلية للمنشورات القديمة تتطلب أرشفة مسبقة.
    """
    tags = extract_tags(update.message.text or "")
    if not tags:
        await update.message.reply_text("اكتب الوسوم بعد الأمر، مثال:\n/بحث #4AM #مذكرات")
        return
    await update.message.reply_text(
        "🔎 استخدم بحث تيليجرام الداخلي داخل القناة بكتابة نفس الوسوم "
        "(تيليجرام يفهرس الوسوم تلقائيًا لكل قناة).\n"
        f"الوسوم التي بحثت عنها: {' '.join('#'+t for t in tags)}"
    )


# ====================== استقبال الملفات ======================

async def handle_incoming_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.chat_id != INTAKE_CHAT_ID and message.chat.type != "private":
        return  # نتجاهل أي رسالة من مجموعات/قنوات أخرى غير مصرح بها

    caption = message.caption or ""
    tags = extract_tags(caption)

    try:
        level, sections = validate_tags(tags)
    except ValueError as e:
        await message.reply_text(str(e))
        return

    source = "رسمي" if "رسمي" in tags else "أستاذ"

    if message.document:
        file_id = message.document.file_id
        file_type = "document"
        original_name = message.document.file_name or "ملف"
    elif message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
        original_name = "صورة"
    elif message.video:
        file_id = message.video.file_id
        file_type = "video"
        original_name = message.video.file_name or "فيديو"
    else:
        await message.reply_text("⚠️ الرجاء إرسال ملف (مستند/صورة/فيديو) وليس نصًا فقط.")
        return

    title_hint = re.sub(r"#\S+", "", caption).strip() or original_name
    sender_name = message.from_user.full_name if message.from_user else "غير معروف"
    sender_id = message.from_user.id if message.from_user else 0

    pf = PendingFile(
        file_id=file_id,
        file_type=file_type,
        original_name=original_name,
        caption=caption,
        level=level,
        sections=sections,
        sender_id=sender_id,
        sender_name=sender_name,
    )
    pid = _new_id()
    PENDING[pid] = pf

    await message.reply_text(
        "✅ تم استلام الملف وهو الآن قيد مراجعة المشرفين.\n"
        f"المستوى: #{level} | القسم: {' '.join('#'+s for s in sections)}\n"
        "سيتم إعلامك عند النشر."
    )

    # إرسال نسخة للمشرفين مع أزرار موافقة/رفض
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ نشر في القناة", callback_data=f"approve:{pid}"),
                InlineKeyboardButton("❌ رفض", callback_data=f"reject:{pid}"),
            ]
        ]
    )
    admin_note = (
        f"📥 ملف جديد بانتظار المراجعة (#{pid})\n"
        f"من: {sender_name}\n"
        f"المستوى: #{level} | الأقسام: {' '.join('#'+s for s in sections)} | المصدر: {source}\n"
        f"التعليق الأصلي: {caption or '—'}"
    )
    for admin_id in ADMIN_IDS:
        try:
            if file_type == "document":
                await context.bot.send_document(
                    admin_id, file_id, caption=admin_note, reply_markup=keyboard
                )
            elif file_type == "photo":
                await context.bot.send_photo(
                    admin_id, file_id, caption=admin_note, reply_markup=keyboard
                )
            else:
                await context.bot.send_video(
                    admin_id, file_id, caption=admin_note, reply_markup=keyboard
                )
        except Exception as e:
            logger.warning("تعذر إرسال إشعار للمشرف %s: %s", admin_id, e)


# ====================== موافقة/رفض المشرف ======================

async def handle_admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.answer("هذا الإجراء للمشرفين فقط.", show_alert=True)
        return

    action, pid_str = query.data.split(":")
    pid = int(pid_str)
    pf = PENDING.get(pid)

    if pf is None:
        await query.edit_message_caption(caption="⚠️ هذا الملف لم يعد موجودًا في قائمة الانتظار.")
        return

    if action == "reject":
        del PENDING[pid]
        await query.edit_message_caption(caption=(query.message.caption or "") + "\n\n❌ تم الرفض.")
        try:
            await context.bot.send_message(pf.sender_id, "⚠️ للأسف لم يُقبل ملفك للنشر. تواصل مع المشرفين للتفاصيل.")
        except Exception:
            pass
        return

    # action == "approve"
    source = "رسمي" if "رسمي" in extract_tags(pf.caption) else "أستاذ"
    new_filename = build_filename(pf.level, pf.sections, pf.original_name, source)
    channel_caption = build_channel_caption(pf.level, pf.sections, pf.sender_name, source, pf.caption)

    try:
        if pf.file_type == "document":
            await context.bot.send_document(
                CHANNEL_ID,
                pf.file_id,
                filename=new_filename + _guess_extension(pf.original_name),
                caption=channel_caption,
                parse_mode=ParseMode.MARKDOWN,
            )
        elif pf.file_type == "photo":
            await context.bot.send_photo(CHANNEL_ID, pf.file_id, caption=channel_caption, parse_mode=ParseMode.MARKDOWN)
        else:
            await context.bot.send_video(CHANNEL_ID, pf.file_id, caption=channel_caption, parse_mode=ParseMode.MARKDOWN)

        await query.edit_message_caption(caption=(query.message.caption or "") + "\n\n✅ تم النشر في القناة.")
        try:
            await context.bot.send_message(pf.sender_id, "✅ تم نشر ملفك في القناة، شكرًا لمساهمتك!")
        except Exception:
            pass
    except Exception as e:
        logger.error("فشل النشر في القناة: %s", e)
        await query.edit_message_caption(caption=(query.message.caption or "") + f"\n\n⚠️ فشل النشر: {e}")
    finally:
        PENDING.pop(pid, None)


def _guess_extension(filename: str) -> str:
    if "." in filename:
        return "." + filename.rsplit(".", 1)[-1]
    return ""


# ====================== نقطة التشغيل ======================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("index", cmd_index))
    app.add_handler(CommandHandler("search", cmd_search))

    app.add_handler(
        MessageHandler(filters.Document.ALL | filters.PHOTO | filters.VIDEO, handle_incoming_file)
    )
    app.add_handler(CallbackQueryHandler(handle_admin_decision))

    logger.info("البوت يعمل الآن...")
    app.run_polling()


if __name__ == "__main__":
    main()
