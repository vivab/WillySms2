from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters
)
from config import BOT_TOKEN
from database import init_db, in_hold_requests, seconds_remaining, finalize_payment, get_users_with_queued
from handlers import *


async def reschedule_holds(app):
    """При старте бота подхватывает все активные (не на паузе) холды после перезапуска,
    а также заново запускает таймеры проверки активности для пользователей с номерами в очереди."""
    for r in in_hold_requests():
        remaining = seconds_remaining(r["hold_until"])
        if remaining <= 0:
            result = finalize_payment(r["id"])
            if result:
                try:
                    await app.bot.send_message(
                        result["user_id"],
                        f"💚✅ Отличная работа! Холд по номеру «{result['phone']}» завершён — начислено ${result['price']:.2f}."
                    )
                except Exception:
                    pass
        else:
            app.job_queue.run_once(hold_finished_job, when=remaining, data={"request_id": r["id"]}, name=f"hold_{r['id']}")

    for uid in get_users_with_queued():
        job_name = f"activity_{uid}"
        if not app.job_queue.get_jobs_by_name(job_name):
            app.job_queue.run_repeating(activity_check_job, interval=720, first=720, data={"user_id": uid}, name=job_name)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set in environment")

    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(reschedule_holds).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(pick_service, pattern=r"^svc:\d+$")],
        states={
            WAITING_PHONES: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phones)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(back_main, pattern=r"^back_main$"),
        ],
        allow_reentry=True,
    )
    app.add_handler(conv)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("del", del_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("clearqueue", clearqueue_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("deladmin", deladmin_cmd))
    app.add_handler(CommandHandler("payuser", payuser_cmd))
    app.add_handler(CommandHandler("topup", topup_cmd))
    app.add_handler(CommandHandler(["startwork", "StartWork"], startwork_cmd))
    app.add_handler(CommandHandler(["stopwork", "StopWork"], stopwork_cmd))

    app.add_handler(CallbackQueryHandler(back_main, pattern=r"^back_main$"))
    app.add_handler(CallbackQueryHandler(services, pattern=r"^services$"))
    app.add_handler(CallbackQueryHandler(my_numbers, pattern=r"^mynumbers$"))
    app.add_handler(CallbackQueryHandler(my_stats, pattern=r"^mystats$"))
    app.add_handler(CallbackQueryHandler(my_requests, pattern=r"^myrequests$"))
    app.add_handler(CallbackQueryHandler(user_im_here, pattern=r"^imhere:\d+$"))

    app.add_handler(CallbackQueryHandler(admin_panel, pattern=r"^admin$"))
    app.add_handler(CallbackQueryHandler(adm_take_menu, pattern=r"^adm_take_menu$"))
    app.add_handler(CallbackQueryHandler(adm_show_queue, pattern=r"^adm_svc:\d+$"))
    app.add_handler(CallbackQueryHandler(take_request_cb, pattern=r"^take:\d+$"))
    app.add_handler(CallbackQueryHandler(prompt_send_code, pattern=r"^sendcode:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_resend_prompt, pattern=r"^resend:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_cancel_request, pattern=r"^admincancel:\d+$"))
    app.add_handler(CallbackQueryHandler(user_entered_code, pattern=r"^entered:\d+$"))
    app.add_handler(CallbackQueryHandler(user_retry_code, pattern=r"^retrycode:\d+$"))
    app.add_handler(CallbackQueryHandler(user_skip, pattern=r"^skip:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_confirm_or_decline, pattern=r"^(confirm|declinenum):\d+$"))
    app.add_handler(CallbackQueryHandler(admin_pause_resume, pattern=r"^(pause|resume):\d+$"))
    app.add_handler(CallbackQueryHandler(admin_slot, pattern=r"^slot:\d+$"))
    app.add_handler(CallbackQueryHandler(reviews, pattern=r"^reviews$"))
    app.add_handler(CallbackQueryHandler(review_cb, pattern=r"^(approve|reject):\d+$"))

    app.add_handler(CallbackQueryHandler(owner_panel, pattern=r"^owner$"))
    app.add_handler(CallbackQueryHandler(owner_stats, pattern=r"^owner_stats$"))
    app.add_handler(CallbackQueryHandler(owner_services, pattern=r"^owner_services$"))
    app.add_handler(CallbackQueryHandler(owner_payouts, pattern=r"^owner_payouts$"))
    app.add_handler(CallbackQueryHandler(owner_payall, pattern=r"^owner_payall$"))

    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, admin_send_code))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))

    print("Willy SMS 24/7 (v3) bot started")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
