import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database import *
from keyboards import *
from utils import has_admin_access, is_superadmin, parse_price, parse_hold_time, fmt_price, normalize_phone
from config import SUPERADMIN_IDS
from crypto_pay import transfer_crypto, create_invoice, CryptoPayError

WAITING_PHONES = 1


# ==================== Общее ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    touch_user(uid)
    context.user_data.clear()
    await update.message.reply_text(
        "🟢👋 Добро пожаловать в Willy!\n\nВыберите действие:",
        reply_markup=main_menu(has_admin_access(uid), is_superadmin(uid))
    )


async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    context.user_data.clear()
    await q.message.edit_text(
        "🟢👋 Добро пожаловать в Willy!\n\nВыберите действие:",
        reply_markup=main_menu(has_admin_access(uid), is_superadmin(uid))
    )


# ==================== Выбор сервиса и номеров ====================

async def services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    rows = list_services()
    if not rows:
        await q.message.edit_text("Пока нет доступных сервисов.", reply_markup=back_main_keyboard())
        return
    await q.message.edit_text("📋 Выберите сервис:", reply_markup=services_pick_keyboard(rows))


async def pick_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    sid = int(q.data.split(":")[1])
    service = get_service(sid)
    if not service:
        await q.answer("Сервис недоступен.", show_alert=True)
        return
    context.user_data["chosen_service"] = sid
    await q.message.edit_text(
        f"📱 Сервис: <b>{service['name']}</b>\n\n"
        f"Отправьте от 1 до 10 номеров телефона — каждый с новой строки:\n\n"
        f"+79867345674\n+79001112233\n\n"
        f"Каждый номер станет отдельной заявкой в очереди.",
        reply_markup=back_main_keyboard(),
        parse_mode="HTML"
    )
    return WAITING_PHONES


async def receive_phones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = context.user_data.get("chosen_service")
    service = get_service(sid) if sid else None
    if not service:
        await update.message.reply_text("Сессия устарела. Начните заново /start")
        return ConversationHandler.END

    raw_lines = [x.strip() for x in (update.message.text or "").replace(",", "\n").split("\n") if x.strip()]
    phones, invalid = [], []
    for line in raw_lines[:10]:
        phone = normalize_phone(line)
        (phones if phone else invalid).append(phone or line)

    if not phones:
        await update.message.reply_text("❌ Ни одного корректного номера. Попробуйте ещё раз.\nПример: +79867345674")
        return WAITING_PHONES

    ids = create_requests(update.effective_user.id, sid, phones)
    for rid in ids:
        log_action(update.effective_user.id, "create_request", rid, details=service["name"])

    if len(ids) == 1:
        pos = queue_position(ids[0])
        r = get_request(ids[0])
        text = (
            f"Отлично! Номер «<code>{r['phone']}</code>» добавлен в очередь, ожидайте администратора!\n\n"
            f"📍 Место в очереди: <b>{pos}</b>\n\n"
            f"Хотите добавить ещё?"
        )
    else:
        lines = []
        for rid in ids:
            pos = queue_position(rid)
            r = get_request(rid)
            lines.append(f"📱 <code>{r['phone']}</code> — место в очереди: <b>{pos}</b>")
        text = (
            "Отлично! Номера добавлены в очередь, ожидайте администратора!\n\n" +
            "\n".join(lines) +
            "\n\nХотите добавить ещё?"
        )

    if invalid:
        text += "\n\n⚠️ Пропущены нераспознанные: " + ", ".join(invalid)

    context.user_data.clear()

    job_name = f"activity_{update.effective_user.id}"
    if not context.job_queue.get_jobs_by_name(job_name):
        context.job_queue.run_repeating(
            activity_check_job, interval=720, first=720,
            data={"user_id": update.effective_user.id}, name=job_name
        )

    await update.message.reply_text(text, reply_markup=after_add_keyboard(), parse_mode="HTML")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.", reply_markup=main_menu(
        has_admin_access(update.effective_user.id), is_superadmin(update.effective_user.id)
    ))
    return ConversationHandler.END


# ==================== Личная статистика / номера ====================

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    balance_val, _ = get_balance(uid)
    success_today, queued_today = today_stats(uid)
    fav = favorite_service(uid)
    await q.message.edit_text(
        f"📊 <b>Ваша статистика</b>\n\n"
        f"✅ Успешных номеров сегодня: <b>{success_today}</b>\n"
        f"⏳ Заявок сегодня: <b>{queued_today}</b>\n"
        f"💰 Баланс: <b>${balance_val:.2f}</b>\n"
        f"⭐ Любимый сервис: <b>{fav or '—'}</b>",
        reply_markup=back_main_keyboard(),
        parse_mode="HTML"
    )


async def my_numbers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    rows = user_active_requests(q.from_user.id)
    if not rows:
        await q.message.edit_text("📭 У вас нет активных номеров.", reply_markup=back_main_keyboard())
        return

    status_map = {
        "taken": "📩 Взят, ожидаем код",
        "code_sent": "📨 Код отправлен вам",
        "user_confirmed": "🔎 На проверке у админа",
        "in_hold": "⏳ Холд, ожидание оплаты",
        "pending_review": "🔎 Код на проверке",
    }
    lines = []
    for r in rows:
        if r["status"] == "queued":
            pos = queue_position(r["id"])
            status_text = f"⏳ Место в очереди: <b>{pos}</b>"
        else:
            status_text = status_map.get(r["status"], r["status"])
        lines.append(f"📱 <code>{r['phone']}</code> — {r['service_name']}\n{status_text}\n————")
    await q.message.edit_text(
        "📍 <b>Ваши номера:</b>\n\n" + "\n".join(lines),
        reply_markup=back_main_keyboard(),
        parse_mode="HTML"
    )


# ---------- Проверка активности ----------

async def activity_check_job(context: ContextTypes.DEFAULT_TYPE):
    uid = context.job.data["user_id"]
    if not user_has_queued(uid):
        context.job.schedule_removal()
        return
    set_activity_pending(uid, True)
    try:
        await context.bot.send_message(
            uid,
            "👀 <b>Вы тут?</b>\n\nУ вас есть номера в очереди — подтвердите, что вы на месте!\n"
            "Если не нажмёте кнопку в течение 3 минут, номера будут убраны из очереди.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Я готов✅", callback_data=f"imhere:{uid}")]]),
            parse_mode="HTML"
        )
    except Exception:
        pass
    context.job_queue.run_once(activity_deadline_job, when=180, data={"user_id": uid}, name=f"activity_deadline_{uid}")


async def activity_deadline_job(context: ContextTypes.DEFAULT_TYPE):
    uid = context.job.data["user_id"]
    if not get_activity_pending(uid):
        return
    count = cancel_all_queued_for_user(uid)
    set_activity_pending(uid, False)
    if count:
        try:
            await context.bot.send_message(
                uid,
                "⏰ Вы не подтвердили активность вовремя — ваши номера убраны из очереди.\n"
                "Можете добавить их заново, когда будете готовы 🟢"
            )
        except Exception:
            pass


async def user_im_here(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = int(q.data.split(":")[1])
    if q.from_user.id != uid:
        await q.answer("Недоступно.", show_alert=True)
        return
    set_activity_pending(uid, False)
    await q.answer("👍 Отлично, продолжаем!")
    try:
        await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# ==================== Админка: взятие номера ====================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not has_admin_access(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    await q.message.edit_text("🛠 <b>Админ-панель</b>", reply_markup=admin_menu(), parse_mode="HTML")


async def adm_take_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not has_admin_access(q.from_user.id):
        return
    rows = list_services()
    if not rows:
        await q.message.edit_text("Нет сервисов.", reply_markup=admin_menu())
        return
    await q.message.edit_text("🔱 Выберите сервис:", reply_markup=adm_services_keyboard(rows))


async def adm_show_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not has_admin_access(q.from_user.id):
        return
    sid = int(q.data.split(":")[1])
    service = get_service(sid)
    rows = queued_for_service(sid)
    if not rows:
        await q.message.edit_text(
            f"📭 Очередь «{service['name'] if service else '?'}» пуста.",
            reply_markup=adm_services_keyboard(list_services())
        )
        return
    r = rows[0]
    mode_label = "мы даём код" if r["service_mode"] == "admin_gives_code" else "нам дают код"
    text = (
        f"🟢 <b>{r['service_name']}</b> ({mode_label})\n\n"
        f"Номер: <code>{r['phone']}</code>\n"
        f"В очереди ещё: {len(rows) - 1}"
    )
    await q.message.edit_text(text, reply_markup=take_keyboard(r["id"]), parse_mode="HTML")


async def take_request_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not has_admin_access(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    rid = int(q.data.split(":")[1])
    if not take_request(rid, q.from_user.id):
        await q.answer("❌ Заявка уже взята другим администратором.", show_alert=True)
        return
    await q.answer("✅ Заявка взята!")
    r = get_request(rid)

    if r["service_mode"] == "admin_gives_code":
        await q.message.edit_text(
            f"✅ Вы взяли номер «<code>{r['phone']}</code>» в работу.\n\n"
            f"Отправьте код (фото или текстом) через кнопку ниже 👇",
            reply_markup=send_code_keyboard(rid),
            parse_mode="HTML"
        )
        try:
            await context.bot.send_message(
                r["user_id"],
                f"📨 <b>Ваш номер «{r['phone']}» взят в работу!</b>\n\nОжидайте код от администратора 💚",
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        await q.message.edit_text(
            f"✅ Вы взяли номер «<code>{r['phone']}</code>» в работу.\n\n"
            f"Ожидайте код от пользователя (попросите ответить текстом).",
            reply_markup=admin_cancel_only_keyboard(rid),
            parse_mode="HTML"
        )
        try:
            await context.bot.send_message(
                r["user_id"],
                f"📨 <b>Ваш номер «{r['phone']}» взят в работу!</b>\n\n"
                f"💻 Введите <b>ответом</b> на это сообщение код, который вам придёт.\n"
                f"<i>(от 2 до 10 символов)</i>",
                parse_mode="HTML"
            )
        except Exception:
            pass


# ==================== Режим 'мы даём код' ====================

async def prompt_send_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    rid = int(q.data.split(":")[1])
    if not has_admin_access(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    r = get_request(rid)
    if not r or r["status"] not in ("taken", "code_sent"):
        await q.answer("Недоступно.", show_alert=True)
        return
    context.user_data["awaiting_code_for"] = rid
    await q.answer()
    await q.message.reply_text(
        "✍️ Отлично! Пожалуйста, отправьте код пользователю следующим сообщением (текст или фото).",
        reply_markup=admin_cancel_only_keyboard(rid)
    )


async def admin_send_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not has_admin_access(uid):
        return
    rid = context.user_data.get("awaiting_code_for")
    if not rid:
        return
    r = get_request(rid)
    if not r or r["status"] not in ("taken", "code_sent"):
        context.user_data.pop("awaiting_code_for", None)
        return

    kb = user_code_keyboard(rid)
    caption = f"📨 <b>Код для номера «{r['phone']}»</b>\n\nВведите его в течение 2 минут ⏱\nИли запросите повтор по кнопке!"
    try:
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            await context.bot.send_photo(r["user_id"], file_id, caption=caption, reply_markup=kb, parse_mode="HTML")
        else:
            text = update.message.text or ""
            await context.bot.send_message(r["user_id"], f"{caption}\n\n{text}", reply_markup=kb, parse_mode="HTML")
    except Exception:
        await update.message.reply_text("❌ Не удалось отправить код пользователю.")
        return

    mark_code_sent(rid)
    context.user_data.pop("awaiting_code_for", None)
    await update.message.reply_text(
        f"⏳ Ожидаем подтверждения от номера «{r['phone']}».",
        reply_markup=admin_waiting_keyboard(rid)
    )


async def admin_resend_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    rid = int(q.data.split(":")[1])
    if not has_admin_access(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    r = get_request(rid)
    if not r or r["status"] not in ("code_sent", "user_confirmed"):
        await q.answer("Недоступно.", show_alert=True)
        return
    reopen_for_resend(rid)
    context.user_data["awaiting_code_for"] = rid
    await q.answer()
    await q.message.reply_text(
        "✍️ Пожалуйста, отправьте новый код пользователю следующим сообщением.",
        reply_markup=admin_cancel_only_keyboard(rid)
    )


async def user_entered_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    rid = int(q.data.split(":")[1])
    r = get_request(rid)
    if not r or r["user_id"] != q.from_user.id or r["status"] != "code_sent":
        await q.answer("Недоступно.", show_alert=True)
        return
    mark_user_confirmed(rid)
    await q.answer()
    try:
        await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await q.message.reply_text("🕐 Отлично! Ожидайте подтверждения от администратора.")
    if r["admin_id"]:
        try:
            await context.bot.send_message(
                r["admin_id"],
                f"✅ <b>Код по номеру «{r['phone']}» введён успешно!</b>\n\n"
                f"Проверьте аккаунт и подтвердите или отклоните номер.",
                reply_markup=admin_confirm_keyboard(rid),
                parse_mode="HTML"
            )
        except Exception:
            pass


async def user_retry_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    rid = int(q.data.split(":")[1])
    r = get_request(rid)
    if not r or r["user_id"] != q.from_user.id or r["status"] != "code_sent":
        await q.answer("Недоступно.", show_alert=True)
        return
    await q.answer("Запрос отправлен администратору")
    if r["admin_id"]:
        try:
            await context.bot.send_message(
                r["admin_id"],
                f"📨 <b>Пользователь запросил повтор кода</b> на номер «{r['phone']}».\nПриготовьте код!",
                reply_markup=admin_waiting_keyboard(rid),
                parse_mode="HTML"
            )
        except Exception:
            pass


async def user_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    rid = int(q.data.split(":")[1])
    r = get_request(rid)
    if not r or r["user_id"] != q.from_user.id:
        await q.answer("Недоступно.", show_alert=True)
        return
    if not cancel_request(rid, q.from_user.id, by_user=True):
        await q.answer("Уже обработано.", show_alert=True)
        return
    await q.answer("Отменено")
    try:
        await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await q.message.reply_text("Хорошо, заявка отменена.")
    if r["admin_id"]:
        try:
            await context.bot.send_message(r["admin_id"], f"ℹ️ Пользователь сам отменил номер «{r['phone']}».")
        except Exception:
            pass


async def admin_cancel_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    rid = int(q.data.split(":")[1])
    if not has_admin_access(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    r = get_request(rid)
    if not r or not cancel_request(rid, q.from_user.id, by_user=False):
        await q.answer("Заявка уже обработана.", show_alert=True)
        return
    await q.answer("Отменено")
    await q.message.edit_text(f"❌ Заявка по номеру «{r['phone']}» отменена.")
    try:
        await context.bot.send_message(r["user_id"], f"❌ Ваша заявка по номеру «{r['phone']}» была отменена администратором.")
    except Exception:
        pass


# ==================== Подтверждение кода / холд ====================

async def admin_confirm_or_decline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, rid_str = q.data.split(":")
    rid = int(rid_str)
    if not has_admin_access(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return

    if action == "confirm":
        result = admin_approve_hold(rid, q.from_user.id)
        if not result:
            await q.answer("❌ Заявка уже обработана.", show_alert=True)
            return
        await q.answer("✅ В холде!")

        if result["recurring"]:
            hold_text = "каждый час"
        else:
            hold_text = fmt_hold(result["hold_seconds"])

        await q.message.edit_text(
            f"✅ Вы взяли номер «{result['phone']}» в холд ({hold_text}).\n\n"
            f"📛 Если аккаунт получил ограничение, блок или слетел — жмите «Слёт» и берите следующий номер.\n"
            f"⛔️/✅ Если у вас перерыв или конец смены — жмите «Стоп», а когда вернётесь — «Начать», холд продолжится с того же места.",
            reply_markup=admin_hold_keyboard(rid, paused=False)
        )

        job_name = f"hold_{rid}"
        for j in context.job_queue.get_jobs_by_name(job_name):
            j.schedule_removal()
        if result["recurring"]:
            context.job_queue.run_repeating(hourly_pay_job, interval=3600, first=3600, data={"request_id": rid}, name=job_name)
        else:
            context.job_queue.run_once(hold_finished_job, when=result["hold_seconds"], data={"request_id": rid}, name=job_name)

        try:
            await context.bot.send_message(
                result["user_id"],
                f"🎉 <b>Номер «{result['phone']}» в работе!</b>\n\n"
                f"Мы всегда знали, что на вас можно положиться 💚\n"
                f"Ожидайте изменения статуса номера.",
                reply_markup=home_keyboard(),
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        r = get_request(rid)
        if not r or not cancel_request(rid, q.from_user.id, by_user=False):
            await q.answer("❌ Заявка уже обработана.", show_alert=True)
            return
        await q.answer("❌ Отклонено")
        await q.message.edit_text(f"❌ Номер «{r['phone']}» отклонён.")
        try:
            await context.bot.send_message(
                r["user_id"],
                f"😔 К сожалению, код по номеру «{r['phone']}» не подтверждён администратором. Попробуйте другой номер!"
            )
        except Exception:
            pass


async def admin_pause_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, rid_str = q.data.split(":")
    rid = int(rid_str)
    if not has_admin_access(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return

    r = get_request(rid)
    recurring = bool(r["service_recurring"]) if r else False

    if action == "pause":
        remaining = pause_hold(rid, q.from_user.id)
        if remaining is None:
            await q.answer("Недоступно.", show_alert=True)
            return
        for j in context.job_queue.get_jobs_by_name(f"hold_{rid}"):
            j.schedule_removal()
        await q.answer("⏸ Холд приостановлен")
        try:
            await q.message.edit_reply_markup(reply_markup=admin_hold_keyboard(rid, paused=True))
        except Exception:
            pass
    else:
        remaining = resume_hold(rid, q.from_user.id)
        if remaining is None:
            await q.answer("Недоступно.", show_alert=True)
            return
        if recurring:
            context.job_queue.run_repeating(hourly_pay_job, interval=3600, first=3600, data={"request_id": rid}, name=f"hold_{rid}")
        else:
            context.job_queue.run_once(hold_finished_job, when=remaining, data={"request_id": rid}, name=f"hold_{rid}")
        await q.answer("▶️ Холд возобновлён")
        try:
            await q.message.edit_reply_markup(reply_markup=admin_hold_keyboard(rid, paused=False))
        except Exception:
            pass


async def admin_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    rid = int(q.data.split(":")[1])
    if not has_admin_access(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    r = get_request(rid)
    if not r:
        await q.answer("Заявка не найдена.", show_alert=True)
        return

    if r["status"] == "in_hold":
        if r["service_recurring"]:
            ok = stop_recurring(rid, q.from_user.id)
            if not ok:
                await q.answer("Недоступно.", show_alert=True)
                return
            for j in context.job_queue.get_jobs_by_name(f"hold_{rid}"):
                j.schedule_removal()
            await q.answer("Остановлено")
            try:
                await q.message.edit_text(
                    f"📛 Номер «{r['phone']}» остановлен. Начисления прекращены, всё уже выплаченное остаётся у пользователя."
                )
            except Exception:
                pass
            try:
                await context.bot.send_message(
                    r["user_id"],
                    f"😔 Номер «{r['phone']}» остановлен администратором.\n"
                    f"Начисления прекращены, но всё уже выплаченное остаётся на вашем балансе 💚"
                )
            except Exception:
                pass
        else:
            result = mark_banned(rid, q.from_user.id)
            if not result:
                await q.answer("Недоступно.", show_alert=True)
                return
            for j in context.job_queue.get_jobs_by_name(f"hold_{rid}"):
                j.schedule_removal()
            await q.answer("Отмечено")
            try:
                await q.message.edit_text(f"📛 Номер «{result['phone']}» отмечен как слетевший. Оплата не производится.")
            except Exception:
                pass
            try:
                await context.bot.send_message(
                    result["user_id"],
                    f"😔 Ваш номер «{result['phone']}» слетел до завершения холда.\n"
                    f"К сожалению, оплата в этот раз не производится."
                )
            except Exception:
                pass

    elif r["status"] == "paid" and not r["service_recurring"]:
        ok = mark_late_banned(rid, q.from_user.id)
        if not ok:
            await q.answer("Уже отмечено ранее.", show_alert=True)
            return
        await q.answer("Отмечено для статистики")
        try:
            await q.message.edit_text(
                f"📛 Номер «{r['phone']}» отмечен как слетевший (задним числом).\n"
                f"Оплата уже была произведена и не отменяется. Пользователь не уведомляется."
            )
        except Exception:
            pass

    else:
        await q.answer("Недоступно для этой заявки.", show_alert=True)


async def hold_finished_job(context: ContextTypes.DEFAULT_TYPE):
    rid = context.job.data["request_id"]
    result = finalize_payment(rid)
    if not result:
        return
    try:
        await context.bot.send_message(
            result["user_id"],
            f"💚✅ <b>Отличная работа!</b>\n\n"
            f"Холд по номеру «{result['phone']}» завершён — начислено <b>${result['price']:.2f}</b>.\n"
            f"Спасибо, что вы с нами! 🎉",
            parse_mode="HTML"
        )
    except Exception:
        pass
    if result.get("admin_id"):
        try:
            await context.bot.send_message(
                result["admin_id"],
                f"✅ Холд по номеру «{result['phone']}» завершён, оплата ${result['price']:.2f} произведена.\n\n"
                f"Если позже узнаете, что аккаунт заблокирован или в спаме — всё равно отметьте это "
                f"(для статистики, оплата не отменяется, пользователь уведомлён не будет):",
                reply_markup=late_slot_keyboard(rid),
                parse_mode="HTML"
            )
        except Exception:
            pass


async def hourly_pay_job(context: ContextTypes.DEFAULT_TYPE):
    rid = context.job.data["request_id"]
    r = get_request(rid)
    if not r or r["status"] != "in_hold":
        context.job.schedule_removal()
        return
    price = r["service_price"]
    credit_recurring_payment(rid, price)
    try:
        await context.bot.send_message(
            r["user_id"],
            f"💚 Начислено <b>${price:.2f}</b> за очередной час работы номера «{r['phone']}»!",
            parse_mode="HTML"
        )
    except Exception:
        pass


# ==================== Режим 'нам дают код' ====================

async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not (2 <= len(text) <= 10):
        return
    active = user_active_requests(update.effective_user.id)
    target = None
    for r in active:
        if r["status"] == "taken" and r["service_mode"] == "user_gives_code":
            target = r
            break
    if not target:
        return
    if not submit_code(target["id"], update.effective_user.id, text):
        return
    await update.message.reply_text("✅ <b>Код принят!</b>\n\nОн отправлен администратору на проверку.", parse_mode="HTML")
    if target["admin_id"]:
        try:
            await context.bot.send_message(
                target["admin_id"],
                f"📨 <b>Код по номеру {target['phone']} получен!</b>\n\n"
                f"Код: <code>{text}</code>\nСумма: <b>${target['service_price']:.2f}</b>\n\nПроверьте и подтвердите:",
                reply_markup=review_keyboard(target["id"]),
                parse_mode="HTML"
            )
        except Exception:
            pass


async def reviews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not has_admin_access(q.from_user.id):
        return
    rows = get_pending_reviews()
    if not rows:
        await q.message.edit_text("📭 Кодов на проверке нет.", reply_markup=admin_menu())
        return
    for r in rows:
        await q.message.reply_text(
            f"📨 <b>Номер {r['phone']}</b>\nСервис: {r['service_name']}\nКод: <code>{r['code']}</code>\nСумма: ${r['service_price']:.2f}",
            reply_markup=review_keyboard(r["id"]),
            parse_mode="HTML"
        )


async def review_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not has_admin_access(q.from_user.id):
        return
    action, rid_str = q.data.split(":")
    rid = int(rid_str)
    result = review_request(rid, q.from_user.id, action == "approve")
    if not result:
        await q.answer("❌ Заявка уже обработана.", show_alert=True)
        return
    if action == "approve":
        await q.message.edit_text(f"✅ Номер «{result['phone']}» подтверждён.")
        user_msg = f"✅ <b>Код подтверждён!</b>\n\n💰 Начислено: <b>${result['price']:.2f}</b>\n\nСпасибо за работу! 🎉"
    else:
        await q.message.edit_text(f"❌ Номер «{result['phone']}» отклонён.")
        user_msg = f"❌ <b>Код отклонён.</b> Начисление не произведено."
    try:
        await context.bot.send_message(result["user_id"], user_msg, parse_mode="HTML")
    except Exception:
        pass


# ==================== Владелец ====================

async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_superadmin(q.from_user.id):
        await q.answer("Нет доступа", show_alert=True)
        return
    await q.message.edit_text("👑 <b>Панель владельца</b>", reply_markup=owner_menu(), parse_mode="HTML")


async def owner_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_superadmin(q.from_user.id):
        return
    s = get_stats()
    text = (
        f"📊 <b>Общая статистика</b>\n\n"
        f"👥 Пользователей: <b>{s['users']}</b>\n🏷 Сервисов: <b>{s['services']}</b>\n🛡 Админов: <b>{s['admins']}</b>\n\n"
        f"📥 Всего заявок: <b>{s['total_requests']}</b>\n⏳ В очереди: <b>{s['queued']}</b>\n"
        f"🔄 В процессе: <b>{s['in_progress']}</b>\n⏱ В холде: <b>{s['in_hold']}</b>\n"
        f"✅ Оплачено: <b>{s['paid']}</b>\n❌ Отклонено: <b>{s['rejected']}</b>\n"
        f"🚫 Слёт: <b>{s['banned_no_pay']}</b>\n⏹ Остановлено (часовые): <b>{s['stopped']}</b>\n\n"
        f"💰 Заработано всеми: <b>${s['total_earned']:.2f}</b>\n💳 На балансах сейчас: <b>${s['total_balance']:.2f}</b>"
    )
    await q.message.edit_text(text, reply_markup=owner_menu(), parse_mode="HTML")


async def owner_durations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_superadmin(q.from_user.id):
        return
    rows = held_stats(20)
    if not rows:
        text = "⏱ Пока нет завершённых холдов."
    else:
        lines = [f"📱 {r['phone']} — {r['held_minutes']} мин — {r['service_name']}" for r in rows]
        text = "⏱ <b>Последние номера по времени холда:</b>\n\n" + "\n".join(lines)
    await q.message.edit_text(text, reply_markup=owner_menu(), parse_mode="HTML")


async def owner_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_superadmin(q.from_user.id):
        return
    rows = list_services()
    text = "📋 <b>Сервисы:</b>\n\n"
    if not rows:
        text += "Пусто\n"
    else:
        for r in rows:
            mode_label = "мы даём" if r["mode"] == "admin_gives_code" else "нам дают"
            rec_label = ", почасово" if r["recurring"] else ""
            text += f"#{r['id']} — {r['name']} ({mode_label}{rec_label})\n"
    text += (
        "\n<b>Команды:</b>\n"
        "/add Название Цена/Холд Режим\n"
        "  Холд — число минут: 4/10\n"
        "  Почасовая оплата: 5/каждый час\n"
        "  Режим: us — мы даём код, them — нам дают код\n\n"
        "  Примеры:\n"
        "  /add Пятёрочка 4/10 us\n"
        "  /add ВКонтакте 3/25 us\n"
        "  /add Магнит 6/60 us\n"
        "  /add Авито 5/каждый час us\n\n"
        "/del ID\n/list\n"
        "/clearqueue [ID] — очистить очередь\n"
        "/broadcast Текст — рассылка всем\n"
        "/addadmin ID / /deladmin ID"
    )
    await q.message.edit_text(text, reply_markup=owner_menu(), parse_mode="HTML")


async def owner_payouts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_superadmin(q.from_user.id):
        return
    rows = get_users_with_balance()
    total = sum(r["balance"] for r in rows)
    text = (
        f"💸 <b>Выплаты</b>\n\n"
        f"Пользователей с балансом: <b>{len(rows)}</b>\n"
        f"Суммарно к выплате: <b>${total:.2f}</b>\n\n"
        f"Точечная выплата: /payuser ID [сумма]\n"
        f"(если сумму не указать — выплатим весь баланс)"
    )
    await q.message.edit_text(text, reply_markup=owner_payouts_keyboard(), parse_mode="HTML")


async def owner_payall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_superadmin(q.from_user.id):
        return
    rows = get_users_with_balance()
    if not rows:
        await q.message.reply_text("Пока некому платить — ни у кого нет баланса.")
        return
    await q.message.reply_text(f"⏳ Начинаю выплаты {len(rows)} пользователям...")
    paid, failed, total = 0, 0, 0.0
    for r in rows:
        uid, amount = r["user_id"], r["balance"]
        spend_id = f"payall_{uid}_{int(datetime.utcnow().timestamp())}"
        try:
            await transfer_crypto(user_id=uid, amount=amount, spend_id=spend_id)
            debit_full_balance(uid)
            paid += 1
            total += amount
            try:
                await context.bot.send_message(uid, f"💚 Вам начислена выплата <b>${amount:.2f}</b> в USDT!", parse_mode="HTML")
            except Exception:
                pass
        except CryptoPayError:
            failed += 1
        await asyncio.sleep(0.3)
    await q.message.reply_text(f"✅ Выплачено {paid} пользователям на сумму ${total:.2f}\n❌ Не удалось: {failed}")


# ==================== Команды ====================

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    if len(context.args) < 3:
        await update.message.reply_text(
            "Использование:\n/add Название Цена/Холд Режим\n\n"
            "Холд — число минут: 4/10 значит $4 и холд 10 минут\n"
            "Почасовая оплата: 5/каждый час — $5 будут начисляться каждый час, пока номер не остановят\n\n"
            "Режим: us — мы даём код, them — нам дают код (последним словом в команде)\n\n"
            "Примеры:\n"
            "/add Пятёрочка 4/10 us\n"
            "/add ВКонтакте 3/25 us\n"
            "/add Магнит 6/60 us\n"
            "/add Авито 5/каждый час us"
        )
        return

    mode_raw = context.args[-1].lower()
    if mode_raw not in ("us", "them"):
        await update.message.reply_text("Режим должен быть 'us' или 'them' (последним словом в команде)")
        return
    mode = "admin_gives_code" if mode_raw == "us" else "user_gives_code"

    rest = context.args[:-1]
    slash_idx = None
    for i, tok in enumerate(rest):
        if "/" in tok:
            slash_idx = i
            break

    if slash_idx is None or slash_idx == 0:
        await update.message.reply_text("❌ Не найден формат Цена/Холд (например 4/10). Напишите /add без аргументов для помощи.")
        return

    name = " ".join(rest[:slash_idx])
    price_hold_str = " ".join(rest[slash_idx:])
    if "/" not in price_hold_str:
        await update.message.reply_text("❌ Не найден символ '/' между ценой и холдом.")
        return

    price_part, hold_part = price_hold_str.split("/", 1)
    hold_part = hold_part.strip().lower()

    try:
        price = parse_price(price_part)
    except Exception:
        await update.message.reply_text("❌ Не удалось распознать цену.")
        return

    if not name:
        await update.message.reply_text("❌ Не указано название сервиса.")
        return

    if "час" in hold_part:
        recurring = 1
        hold_seconds = 3600
        display_name = f"{name} {fmt_price(price)}$ каждый час"
    else:
        recurring = 0
        if hold_part.isdigit():
            hold_seconds = int(hold_part) * 60
        else:
            hold_seconds = parse_hold_time(hold_part)
        display_name = f"{name} {fmt_price(price)}/{hold_seconds // 60}"

    try:
        sid = add_service(display_name, price, hold_seconds, mode, recurring)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return

    mode_label = "мы даём код" if mode == "admin_gives_code" else "нам дают код"
    await update.message.reply_text(f"🟢✅ Сервис добавлен: #{sid} «{display_name}» ({mode_label})")


async def del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Использование: /del ID")
        return
    ok = delete_service(int(context.args[0]))
    await update.message.reply_text("✅ Удалён" if ok else "❌ Не найден")


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    rows = list_services()
    if not rows:
        await update.message.reply_text("Пусто")
        return
    lines = []
    for r in rows:
        mode_label = "us" if r["mode"] == "admin_gives_code" else "them"
        rec = " почасово" if r["recurring"] else ""
        lines.append(f"#{r['id']} — {r['name']} ({mode_label}{rec})")
    await update.message.reply_text("\n".join(lines))


async def clearqueue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    sid = None
    if context.args:
        try:
            sid = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Использование: /clearqueue [ID_сервиса]")
            return
    count = clear_queue(sid)
    scope = f"сервиса #{sid}" if sid else "всех сервисов"
    await update.message.reply_text(f"✅ Очередь {scope} очищена. Отменено: {count}")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    text = update.message.text.partition(" ")[2].strip()
    if not text:
        await update.message.reply_text("Использование: /broadcast Текст сообщения")
        return
    ids = all_user_ids()
    sent = 0
    for uid in ids:
        try:
            await context.bot.send_message(uid, text)
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"✅ Рассылка отправлена: {sent}/{len(ids)}")


async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Использование: /addadmin TELEGRAM_ID")
        return
    add_admin(int(context.args[0]))
    await update.message.reply_text("✅ Админ добавлен")


async def deladmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    if not context.args:
        return
    remove_admin(int(context.args[0]))
    await update.message.reply_text("✅ Админ удалён")


async def payuser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Использование: /payuser ID [сумма]")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом.")
        return
    bal, _ = get_balance(uid)
    amount = bal
    if len(context.args) > 1:
        try:
            amount = float(context.args[1].replace(",", "."))
        except ValueError:
            await update.message.reply_text("Некорректная сумма.")
            return
    if amount <= 0 or amount > bal:
        await update.message.reply_text(f"❌ Недостаточно средств у пользователя (баланс ${bal:.2f}).")
        return
    spend_id = f"pay_{uid}_{int(datetime.utcnow().timestamp())}"
    try:
        await transfer_crypto(user_id=uid, amount=amount, spend_id=spend_id)
    except CryptoPayError as e:
        await update.message.reply_text(f"❌ Ошибка перевода: {e}")
        return
    debit_amount(uid, amount)
    await update.message.reply_text(f"✅ Выплачено ${amount:.2f} пользователю {uid}")
    try:
        await context.bot.send_message(uid, f"💚 Вам начислена выплата <b>${amount:.2f}</b> в USDT!", parse_mode="HTML")
    except Exception:
        pass


async def topup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Использование: /topup СУММА")
        return
    try:
        amount = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("Введите число, например /topup 10")
        return
    try:
        invoice = await create_invoice(amount, description="Пополнение баланса приложения Willy")
    except CryptoPayError as e:
        await update.message.reply_text(f"❌ Ошибка создания счёта: {e}")
        return
    pay_url = invoice.get("bot_invoice_url") or invoice.get("pay_url")
    await update.message.reply_text(
        f"💳 Счёт создан на ${amount:.2f}\n👉 {pay_url}",
        disable_web_page_preview=True
    )


async def startwork_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    ids = all_user_ids()
    sent = 0
    for uid in ids:
        try:
            await context.bot.send_message(uid, "Начало работы✅ ждем ваших номеров🟩")
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"✅ Оповещение отправлено: {sent}/{len(ids)}")


async def stopwork_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    ids = all_user_ids()
    sent = 0
    for uid in ids:
        try:
            await context.bot.send_message(uid, "Стоп ворк⛔️ Если ваш номер не слетел он останется на завтрашний день!✅")
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"✅ Оповещение отправлено: {sent}/{len(ids)}")
