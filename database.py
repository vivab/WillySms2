import sqlite3
from datetime import datetime, timedelta
from config import DB_NAME


def connect():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def now_str():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def seconds_remaining(hold_until_str):
    if not hold_until_str:
        return 0
    target = datetime.strptime(hold_until_str, "%Y-%m-%d %H:%M:%S")
    return max(0, int((target - datetime.utcnow()).total_seconds()))


def fmt_hold(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}с"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}м" + (f" {sec}с" if sec else "")
    hours, minutes = divmod(minutes, 60)
    return f"{hours}ч" + (f" {minutes}м" if minutes else "")


def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            price REAL NOT NULL,
            hold_seconds INTEGER NOT NULL DEFAULT 0,
            mode TEXT NOT NULL DEFAULT 'user_gives_code',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS admins (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            service_id INTEGER NOT NULL,
            phone TEXT,
            admin_id INTEGER,
            code TEXT,
            status TEXT DEFAULT 'queued',
            paused INTEGER DEFAULT 0,
            remaining_seconds INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            taken_at TEXT,
            code_sent_at TEXT,
            confirmed_at TEXT,
            hold_until TEXT,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS balances (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0,
            total_earned REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_seen TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS activity_checks (
            user_id INTEGER PRIMARY KEY,
            pending INTEGER DEFAULT 0,
            ping_at TEXT
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER,
            action TEXT NOT NULL,
            request_id INTEGER,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA busy_timeout=5000;")


def log_action(actor_id, action, request_id=None, details=""):
    with connect() as c:
        c.execute(
            "INSERT INTO logs(actor_id, action, request_id, details) VALUES (?,?,?,?)",
            (actor_id, action, request_id, details)
        )


# ---------- Пользователи ----------

def touch_user(user_id):
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user_id,))


def all_user_ids():
    with connect() as c:
        return [row["user_id"] for row in c.execute("SELECT user_id FROM users").fetchall()]


# ---------- Проверка активности ----------

def user_has_queued(user_id):
    with connect() as c:
        return c.execute("SELECT 1 FROM requests WHERE user_id=? AND status='queued' LIMIT 1", (user_id,)).fetchone() is not None


def get_users_with_queued():
    with connect() as c:
        return [row["user_id"] for row in c.execute("SELECT DISTINCT user_id FROM requests WHERE status='queued'").fetchall()]


def cancel_all_queued_for_user(user_id):
    with connect() as c:
        cur = c.execute(
            "UPDATE requests SET status='rejected', completed_at=CURRENT_TIMESTAMP WHERE user_id=? AND status='queued'",
            (user_id,)
        )
        return cur.rowcount


def set_activity_pending(user_id, pending: bool):
    with connect() as c:
        c.execute(
            """INSERT INTO activity_checks(user_id, pending, ping_at) VALUES (?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET pending=excluded.pending, ping_at=CURRENT_TIMESTAMP""",
            (user_id, 1 if pending else 0)
        )


def get_activity_pending(user_id):
    with connect() as c:
        row = c.execute("SELECT pending FROM activity_checks WHERE user_id=?", (user_id,)).fetchone()
    return bool(row["pending"]) if row else False


def user_queued_requests(user_id):
    with connect() as c:
        return c.execute("""
            SELECT r.*, s.name AS service_name
            FROM requests r JOIN services s ON s.id = r.service_id
            WHERE r.user_id=? AND r.status='queued'
            ORDER BY r.id
        """, (user_id,)).fetchall()


# ---------- Сервисы ----------

def add_service(name, price, hold_seconds, mode):
    with connect() as c:
        cur = c.execute(
            "INSERT INTO services(name, price, hold_seconds, mode) VALUES (?,?,?,?)",
            (name, price, hold_seconds, mode)
        )
        return cur.lastrowid


def list_services():
    with connect() as c:
        return c.execute("SELECT * FROM services WHERE is_active=1 ORDER BY id").fetchall()


def delete_service(service_id):
    with connect() as c:
        return c.execute(
            "UPDATE services SET is_active=0 WHERE id=? AND is_active=1", (service_id,)
        ).rowcount > 0


def get_service(service_id):
    with connect() as c:
        return c.execute("SELECT * FROM services WHERE id=? AND is_active=1", (service_id,)).fetchone()


# ---------- Админы ----------

def add_admin(tg_id, username=""):
    with connect() as c:
        c.execute(
            """INSERT INTO admins(tg_id, username, is_active) VALUES (?,?,1)
               ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, is_active=1""",
            (tg_id, username)
        )


def remove_admin(tg_id):
    with connect() as c:
        c.execute("UPDATE admins SET is_active=0 WHERE tg_id=?", (tg_id,))


def is_admin(tg_id):
    with connect() as c:
        return c.execute("SELECT 1 FROM admins WHERE tg_id=? AND is_active=1", (tg_id,)).fetchone() is not None


def list_admins():
    with connect() as c:
        return c.execute("SELECT tg_id, username FROM admins WHERE is_active=1").fetchall()


# ---------- Заявки ----------

def create_requests(user_id, service_id, phones):
    ids = []
    with connect() as c:
        for phone in phones:
            cur = c.execute(
                "INSERT INTO requests(user_id, service_id, phone, status) VALUES (?,?,?,'queued')",
                (user_id, service_id, phone)
            )
            ids.append(cur.lastrowid)
    return ids


def get_request(request_id):
    with connect() as c:
        return c.execute("""
            SELECT r.*, s.name AS service_name, s.price AS service_price,
                   s.mode AS service_mode, s.hold_seconds AS service_hold
            FROM requests r JOIN services s ON s.id = r.service_id
            WHERE r.id = ?
        """, (request_id,)).fetchone()


def user_active_requests(user_id):
    with connect() as c:
        return c.execute("""
            SELECT r.*, s.name AS service_name, s.price AS service_price,
                   s.mode AS service_mode, s.hold_seconds AS service_hold
            FROM requests r JOIN services s ON s.id = r.service_id
            WHERE r.user_id = ? AND r.status NOT IN ('paid','rejected','banned_no_pay')
            ORDER BY r.id DESC
        """, (user_id,)).fetchall()


def queued_for_service(service_id):
    with connect() as c:
        return c.execute("""
            SELECT r.*, s.name AS service_name, s.price AS service_price,
                   s.mode AS service_mode, s.hold_seconds AS service_hold
            FROM requests r JOIN services s ON s.id = r.service_id
            WHERE r.service_id = ? AND r.status = 'queued'
            ORDER BY r.id
        """, (service_id,)).fetchall()


def queue_position(request_id):
    r = get_request(request_id)
    if not r or r["status"] != "queued":
        return None
    with connect() as c:
        pos = c.execute(
            "SELECT COUNT(*) FROM requests WHERE service_id=? AND status='queued' AND id<=?",
            (r["service_id"], request_id)
        ).fetchone()[0]
    return pos


def take_request(request_id, admin_id):
    with connect() as c:
        cur = c.execute("""
            UPDATE requests SET status='taken', admin_id=?, taken_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='queued'
        """, (admin_id, request_id))
        ok = cur.rowcount == 1
    if ok:
        log_action(admin_id, "take_request", request_id)
    return ok


def mark_code_sent(request_id):
    with connect() as c:
        c.execute("UPDATE requests SET status='code_sent', code_sent_at=CURRENT_TIMESTAMP WHERE id=?", (request_id,))


def mark_user_confirmed(request_id):
    with connect() as c:
        c.execute(
            "UPDATE requests SET status='user_confirmed', confirmed_at=CURRENT_TIMESTAMP WHERE id=? AND status='code_sent'",
            (request_id,)
        )


def reopen_for_resend(request_id):
    with connect() as c:
        c.execute("UPDATE requests SET status='taken' WHERE id=?", (request_id,))


def admin_approve_hold(request_id, admin_id):
    r = get_request(request_id)
    if not r or r["status"] != "user_confirmed":
        return None
    hold_until = (datetime.utcnow() + timedelta(seconds=r["service_hold"])).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as c:
        c.execute("""
            UPDATE requests SET status='in_hold', hold_until=?
            WHERE id=? AND status='user_confirmed'
        """, (hold_until, request_id))
    log_action(admin_id, "approve_hold", request_id)
    return {"hold_until": hold_until, "hold_seconds": r["service_hold"], "phone": r["phone"], "user_id": r["user_id"]}


def pause_hold(request_id, admin_id):
    r = get_request(request_id)
    if not r or r["status"] != "in_hold" or r["paused"]:
        return None
    remaining = seconds_remaining(r["hold_until"])
    with connect() as c:
        c.execute("UPDATE requests SET paused=1, remaining_seconds=? WHERE id=?", (remaining, request_id))
    log_action(admin_id, "pause_hold", request_id)
    return remaining


def resume_hold(request_id, admin_id):
    r = get_request(request_id)
    if not r or r["status"] != "in_hold" or not r["paused"]:
        return None
    remaining = r["remaining_seconds"] or 0
    hold_until = (datetime.utcnow() + timedelta(seconds=remaining)).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as c:
        c.execute("UPDATE requests SET paused=0, hold_until=?, remaining_seconds=NULL WHERE id=?", (hold_until, request_id))
    log_action(admin_id, "resume_hold", request_id)
    return remaining


def finalize_payment(request_id):
    with connect() as c:
        row = c.execute("SELECT * FROM requests WHERE id=? AND status='in_hold'", (request_id,)).fetchone()
        if not row:
            return None
        cur = c.execute(
            "UPDATE requests SET status='paid', completed_at=CURRENT_TIMESTAMP WHERE id=? AND status='in_hold'",
            (request_id,)
        )
        if cur.rowcount != 1:
            return None
        price = c.execute("SELECT price FROM services WHERE id=?", (row["service_id"],)).fetchone()["price"]
        c.execute("""
            INSERT INTO balances(user_id, balance, total_earned) VALUES (?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                balance = balance + excluded.balance,
                total_earned = total_earned + excluded.total_earned
        """, (row["user_id"], price, price))
        result = {"user_id": row["user_id"], "phone": row["phone"], "price": price}
    log_action(0, "auto_pay", request_id, f"price={price}")
    return result


def mark_banned(request_id, admin_id):
    """Слёт до завершения холда — без оплаты. Если холд уже завершён — возвращает None (поздно)."""
    with connect() as c:
        cur = c.execute(
            "UPDATE requests SET status='banned_no_pay', completed_at=CURRENT_TIMESTAMP WHERE id=? AND status='in_hold'",
            (request_id,)
        )
        ok = cur.rowcount == 1
    if not ok:
        return None
    row = get_request(request_id)
    log_action(admin_id, "mark_banned", request_id)
    return {"phone": row["phone"], "user_id": row["user_id"]}


def cancel_request(request_id, actor_id, by_user=False):
    with connect() as c:
        cur = c.execute("""
            UPDATE requests SET status='rejected', completed_at=CURRENT_TIMESTAMP
            WHERE id=? AND status IN ('taken','code_sent','user_confirmed','pending_review')
        """, (request_id,))
        ok = cur.rowcount == 1
    if ok:
        log_action(actor_id, "user_cancel" if by_user else "admin_cancel", request_id)
    return ok


def in_hold_requests():
    with connect() as c:
        return c.execute("SELECT * FROM requests WHERE status='in_hold' AND paused=0").fetchall()


def clear_queue(service_id=None):
    with connect() as c:
        if service_id:
            cur = c.execute(
                "UPDATE requests SET status='rejected', completed_at=CURRENT_TIMESTAMP WHERE status='queued' AND service_id=?",
                (service_id,)
            )
        else:
            cur = c.execute(
                "UPDATE requests SET status='rejected', completed_at=CURRENT_TIMESTAMP WHERE status='queued'"
            )
        return cur.rowcount


# ---------- Режим 'нам дают код' ----------

def submit_code(request_id, user_id, code):
    with connect() as c:
        cur = c.execute("""
            UPDATE requests SET code=?, status='pending_review'
            WHERE id=? AND user_id=? AND status='taken'
        """, (code, request_id, user_id))
        return cur.rowcount == 1


def get_pending_reviews():
    with connect() as c:
        return c.execute("""
            SELECT r.*, s.name AS service_name, s.price AS service_price
            FROM requests r JOIN services s ON s.id = r.service_id
            WHERE r.status = 'pending_review'
            ORDER BY r.id
        """).fetchall()


def review_request(request_id, admin_id, approve):
    with connect() as c:
        row = c.execute("""
            SELECT r.*, s.price FROM requests r JOIN services s ON s.id = r.service_id WHERE r.id = ?
        """, (request_id,)).fetchone()
        if not row or row["status"] != "pending_review":
            return None
        new_status = "paid" if approve else "rejected"
        c.execute("UPDATE requests SET status=?, completed_at=CURRENT_TIMESTAMP WHERE id=?", (new_status, request_id))
        if approve:
            c.execute("""
                INSERT INTO balances(user_id, balance, total_earned) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    balance = balance + excluded.balance,
                    total_earned = total_earned + excluded.total_earned
            """, (row["user_id"], row["price"], row["price"]))
        result = dict(row)
    log_action(admin_id, new_status, request_id, row["code"] or "")
    return result


# ---------- Баланс / статистика пользователя ----------

def get_balance(user_id):
    with connect() as c:
        row = c.execute("SELECT balance, total_earned FROM balances WHERE user_id=?", (user_id,)).fetchone()
        return (row["balance"], row["total_earned"]) if row else (0.0, 0.0)


def today_stats(user_id):
    with connect() as c:
        success_today = c.execute(
            "SELECT COUNT(*) FROM requests WHERE user_id=? AND status='paid' AND date(completed_at)=date('now')",
            (user_id,)
        ).fetchone()[0]
        queued_today = c.execute(
            "SELECT COUNT(*) FROM requests WHERE user_id=? AND date(created_at)=date('now')",
            (user_id,)
        ).fetchone()[0]
    return success_today, queued_today


def favorite_service(user_id):
    with connect() as c:
        row = c.execute("""
            SELECT s.name, COUNT(*) as cnt
            FROM requests r JOIN services s ON s.id = r.service_id
            WHERE r.user_id=? AND r.status='paid'
            GROUP BY r.service_id ORDER BY cnt DESC LIMIT 1
        """, (user_id,)).fetchone()
    return row["name"] if row else None


# ---------- Выплаты (владелец) ----------

def get_users_with_balance(min_amount=0.01):
    with connect() as c:
        return c.execute("SELECT user_id, balance FROM balances WHERE balance >= ? ORDER BY balance DESC", (min_amount,)).fetchall()


def debit_full_balance(user_id):
    with connect() as c:
        c.execute("UPDATE balances SET balance=0 WHERE user_id=?", (user_id,))


def debit_amount(user_id, amount):
    with connect() as c:
        cur = c.execute("UPDATE balances SET balance=balance-? WHERE user_id=? AND balance>=?", (amount, user_id, amount))
        return cur.rowcount == 1


# ---------- Общая статистика ----------

def get_stats():
    with connect() as c:
        def cnt(status):
            return c.execute("SELECT COUNT(*) FROM requests WHERE status=?", (status,)).fetchone()[0]

        total_requests = c.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        queued = cnt("queued")
        in_progress = sum(cnt(s) for s in ("taken", "code_sent", "user_confirmed", "pending_review"))
        in_hold = cnt("in_hold")
        paid = cnt("paid")
        rejected = cnt("rejected")
        banned_no_pay = cnt("banned_no_pay")
        total_earned = c.execute("SELECT COALESCE(SUM(total_earned), 0) FROM balances").fetchone()[0]
        total_balance = c.execute("SELECT COALESCE(SUM(balance), 0) FROM balances").fetchone()[0]
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        services = c.execute("SELECT COUNT(*) FROM services WHERE is_active=1").fetchone()[0]
        admins = c.execute("SELECT COUNT(*) FROM admins WHERE is_active=1").fetchone()[0]

        return {
            "total_requests": total_requests, "queued": queued, "in_progress": in_progress,
            "in_hold": in_hold, "paid": paid, "rejected": rejected, "banned_no_pay": banned_no_pay,
            "total_earned": total_earned, "total_balance": total_balance,
            "users": users, "services": services, "admins": admins,
        }
