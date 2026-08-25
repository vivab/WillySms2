import httpx
from config import CRYPTO_PAY_TOKEN, CRYPTO_ASSET, CRYPTO_PAY_API_URL


class CryptoPayError(Exception):
    pass


async def transfer_crypto(user_id: int, amount: float, spend_id: str) -> dict:
    if not CRYPTO_PAY_TOKEN:
        raise CryptoPayError("CRYPTO_PAY_TOKEN не задан в переменных окружения")

    payload = {
        "user_id": user_id,
        "asset": CRYPTO_ASSET,
        "amount": f"{amount:.2f}",
        "spend_id": spend_id,
    }
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{CRYPTO_PAY_API_URL}/transfer", json=payload, headers=headers)
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise CryptoPayError(f"Сетевая ошибка Crypto Pay: {e}")

    if not data.get("ok"):
        error = data.get("error", {}) or {}
        raise CryptoPayError(f"{error.get('code', '?')}: {error.get('name', 'unknown error')}")

    return data["result"]


async def create_invoice(amount: float, description: str = "Пополнение баланса приложения") -> dict:
    if not CRYPTO_PAY_TOKEN:
        raise CryptoPayError("CRYPTO_PAY_TOKEN не задан в переменных окружения")

    payload = {
        "asset": CRYPTO_ASSET,
        "amount": f"{amount:.2f}",
        "description": description[:1024],
    }
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{CRYPTO_PAY_API_URL}/createInvoice", json=payload, headers=headers)
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise CryptoPayError(f"Сетевая ошибка Crypto Pay: {e}")

    if not data.get("ok"):
        error = data.get("error", {}) or {}
        raise CryptoPayError(f"{error.get('code', '?')}: {error.get('name', 'unknown error')}")

    return data["result"]
