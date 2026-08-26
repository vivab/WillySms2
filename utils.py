import re
from config import SUPERADMIN_IDS
from database import is_admin


def is_superadmin(user_id: int) -> bool:
    return user_id in SUPERADMIN_IDS


def has_admin_access(user_id: int) -> bool:
    return is_superadmin(user_id) or is_admin(user_id)


def parse_price(value: str) -> float:
    return float(value.replace("$", "").replace(",", ".").strip())


def fmt_price(p: float) -> str:
    """Красиво форматирует цену: 4.0 -> '4', 4.50 -> '4.5', 3.25 -> '3.25'."""
    s = f"{p:.2f}".rstrip("0").rstrip(".")
    return s if s else "0"


def parse_hold_time(value: str) -> int:
    """Парсит '10м', '1ч', '45с', '1ч30м' и т.п. в секунды."""
    value = value.lower().strip()
    matches = re.findall(r"(\d+)\s*(ч|м|с)", value)
    if not matches:
        return int(value) if value.isdigit() else 0
    total = 0
    for num, unit in matches:
        num = int(num)
        if unit == "ч":
            total += num * 3600
        elif unit == "м":
            total += num * 60
        else:
            total += num
    return total


def normalize_phone(raw: str):
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if digits.startswith("7") and len(digits) == 11:
        return "+" + digits
    if len(digits) == 10:
        return "+7" + digits
    if raw.strip().startswith("+") and 11 <= len(digits) <= 15:
        return "+" + digits
    return None
