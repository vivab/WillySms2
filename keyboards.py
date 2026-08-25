from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu(admin=False, superadmin=False):
    rows = [
        [InlineKeyboardButton("📋 Сервисы", callback_data="services")],
        [InlineKeyboardButton("📍 Мои номера", callback_data="mynumbers")],
        [InlineKeyboardButton("📊 Моя статистика", callback_data="mystats")],
        [InlineKeyboardButton("📥 Мои заявки", callback_data="myrequests")],
    ]
    if admin or superadmin:
        rows.append([InlineKeyboardButton("🛠 Админка", callback_data="admin")])
    if superadmin:
        rows.append([InlineKeyboardButton("👑 Владелец", callback_data="owner")])
    return InlineKeyboardMarkup(rows)


def services_pick_keyboard(services):
    rows = [[InlineKeyboardButton(f"🟢 {s['name']} — ${s['price']:.2f}", callback_data=f"svc:{s['id']}")] for s in services]
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def back_main_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]])


def home_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 В главное меню", callback_data="back_main")]])


def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔱 Взять номер", callback_data="adm_take_menu")],
        [InlineKeyboardButton("📨 Коды на проверке", callback_data="reviews")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_main")],
    ])


def adm_services_keyboard(services):
    rows = [[InlineKeyboardButton(f"🟢 {s['name']}", callback_data=f"adm_svc:{s['id']}")] for s in services]
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


def take_keyboard(request_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔱 Взять номер", callback_data=f"take:{request_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="adm_take_menu")],
    ])


def send_code_keyboard(request_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Отправить код📨", callback_data=f"sendcode:{request_id}")],
        [InlineKeyboardButton("Отменить номер❌", callback_data=f"admincancel:{request_id}")],
    ])


def admin_waiting_keyboard(request_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Скинуть повтор📨", callback_data=f"resend:{request_id}")],
        [InlineKeyboardButton("Отменить номер❌", callback_data=f"admincancel:{request_id}")],
    ])


def admin_cancel_only_keyboard(request_id):
    return InlineKeyboardMarkup([[InlineKeyboardButton("Отменить номер❌", callback_data=f"admincancel:{request_id}")]])


def user_code_keyboard(request_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Ввел код✅", callback_data=f"entered:{request_id}")],
        [InlineKeyboardButton("Запросить повтор📨", callback_data=f"retrycode:{request_id}")],
        [InlineKeyboardButton("Скип❌", callback_data=f"skip:{request_id}")],
    ])


def admin_confirm_keyboard(request_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Подтвердить✅", callback_data=f"confirm:{request_id}"),
        InlineKeyboardButton("Отклонить❌", callback_data=f"declinenum:{request_id}")
    ]])


def admin_hold_keyboard(request_id, paused=False):
    stop_btn = (
        InlineKeyboardButton("Начать✅", callback_data=f"resume:{request_id}")
        if paused else
        InlineKeyboardButton("Стоп⛔️", callback_data=f"pause:{request_id}")
    )
    return InlineKeyboardMarkup([
        [stop_btn],
        [InlineKeyboardButton("Слёт📛", callback_data=f"slot:{request_id}")],
    ])


def review_keyboard(request_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve:{request_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{request_id}")
    ]])


def owner_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Общая статистика", callback_data="owner_stats")],
        [InlineKeyboardButton("➕ Добавить сервис", callback_data="owner_services")],
        [InlineKeyboardButton("💸 Выплаты", callback_data="owner_payouts")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_main")],
    ])


def owner_payouts_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Выплатить всем", callback_data="owner_payall")],
        [InlineKeyboardButton("🔙 Назад", callback_data="owner")],
    ])
