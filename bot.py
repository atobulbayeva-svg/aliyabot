"""Telegram-бот: на любое сообщение отвечает сводкой по двум странам.

Казахстан: время в Астане, погода в Астане, курс доллара к тенге (покупка/продажа).
Грузия: время, погода в Батуми, курс доллара к лари, курс биткоина.

Источники (ключи API не нужны):
  погода: Open-Meteo, курс тенге: Halyk Bank, курс лари: Нацбанк Грузии,
  биткоин: CoinGecko.
Обновления получаем через long polling (getUpdates).
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    sys.exit("BOT_TOKEN не найден. Добавьте его в файл .env")

API = f"https://api.telegram.org/bot{TOKEN}"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
BTC_URL = "https://api.coingecko.com/api/v3/simple/price"
HALYK_URL = "https://back.halykbank.kz/common/currency-history"
NBG_URL = "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/"

# Часовые пояса без перехода на летнее время
KZ_TZ = timezone(timedelta(hours=5))   # Астана, UTC+5
GE_TZ = timezone(timedelta(hours=4))   # Грузия, UTC+4

ASTANA = {"latitude": 51.1694, "longitude": 71.4491}
BATUMI = {"latitude": 41.6168, "longitude": 41.6367}

# Расшифровка WMO-кодов погоды
WMO = {
    0: "☀️ Ясно",
    1: "🌤 Преимущественно ясно",
    2: "⛅ Переменная облачность",
    3: "☁️ Пасмурно",
    45: "🌫 Туман",
    48: "🌫 Изморозь",
    51: "🌦 Лёгкая морось",
    53: "🌦 Морось",
    55: "🌦 Сильная морось",
    56: "🌧 Ледяная морось",
    57: "🌧 Сильная ледяная морось",
    61: "🌧 Небольшой дождь",
    63: "🌧 Дождь",
    65: "🌧 Сильный дождь",
    66: "🌧 Ледяной дождь",
    67: "🌧 Сильный ледяной дождь",
    71: "🌨 Небольшой снег",
    73: "🌨 Снег",
    75: "❄️ Сильный снег",
    77: "🌨 Снежные зёрна",
    80: "🌦 Небольшой ливень",
    81: "🌧 Ливень",
    82: "⛈ Сильный ливень",
    85: "🌨 Снегопад",
    86: "❄️ Сильный снегопад",
    95: "⛈ Гроза",
    96: "⛈ Гроза с градом",
    99: "⛈ Сильная гроза с градом",
}

DIRECTIONS = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]


def wind_direction(deg: float) -> str:
    return DIRECTIONS[round(deg / 45) % 8]


def time_text(tz: timezone, place: str) -> str:
    now = datetime.now(tz)
    return f"🕐 Время в {place}: {now:%H:%M}, {now:%d.%m.%Y}"


def weather_text(coords: dict, place: str) -> str:
    resp = requests.get(
        WEATHER_URL,
        params={
            **coords,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "pressure_msl,wind_speed_10m,wind_direction_10m,weather_code",
            "wind_speed_unit": "ms",
            "timezone": "auto",
        },
        timeout=10,
    )
    resp.raise_for_status()
    c = resp.json()["current"]
    description = WMO.get(c["weather_code"], "🌡 Погода")
    pressure_mm = c["pressure_msl"] * 0.75006  # гПа -> мм рт. ст.
    return (
        f"Погода в {place} сейчас\n"
        f"{description}\n"
        f"🌡 Температура: {c['temperature_2m']:+.0f}°C "
        f"(ощущается как {c['apparent_temperature']:+.0f}°C)\n"
        f"💧 Влажность: {c['relative_humidity_2m']}%\n"
        f"💨 Ветер: {c['wind_speed_10m']:.1f} м/с, {wind_direction(c['wind_direction_10m'])}\n"
        f"🧭 Давление: {pressure_mm:.0f} мм рт. ст."
    )


def usd_kzt_text() -> str:
    """Курс доллара в Halyk Bank для физлиц (покупка и продажа банком)."""
    resp = requests.get(HALYK_URL, timeout=10)
    resp.raise_for_status()
    rate = resp.json()["data"]["currencyHistory"][0]["privatePersons"]["USD/KZT"]
    return (
        "💵 Доллар к тенге (Halyk Bank)\n"
        f"Покупка: {rate['buy']:.2f} ₸\n"
        f"Продажа: {rate['sell']:.2f} ₸"
    )


def usd_gel_text() -> str:
    """Официальный курс Нацбанка Грузии."""
    resp = requests.get(NBG_URL, params={"currencies": "USD"}, timeout=10)
    resp.raise_for_status()
    usd = resp.json()[0]["currencies"][0]
    return (
        "💵 Доллар к лари (Нацбанк Грузии)\n"
        f"1 $ = {usd['rate']:.4f} ₾"
    )


def bitcoin_text() -> str:
    resp = requests.get(
        BTC_URL,
        params={"ids": "bitcoin", "vs_currencies": "usd,kzt", "include_24hr_change": "true"},
        timeout=10,
    )
    resp.raise_for_status()
    b = resp.json()["bitcoin"]
    change = b["usd_24h_change"]
    arrow = "📈" if change >= 0 else "📉"
    price = f"₿ {b['usd']:,.0f} $ ({b['kzt']:,.0f} ₸)".replace(",", " ")
    return f"Курс биткоина\n{price}\n{arrow} За 24 часа: {change:+.1f}%"


def safe(fetch, error: str) -> str:
    """Один сбойный источник не должен ломать весь ответ."""
    try:
        return fetch()
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        print(f"Ошибка ({error}):", e)
        return f"Не удалось получить {error}, попробуйте чуть позже."


def get_reply_text() -> str:
    kazakhstan = [
        "🇰🇿 КАЗАХСТАН",
        time_text(KZ_TZ, "Астане"),
        safe(lambda: weather_text(ASTANA, "Астане"), "погоду в Астане"),
        safe(usd_kzt_text, "курс доллара к тенге"),
    ]
    georgia = [
        "🇬🇪 ГРУЗИЯ",
        time_text(GE_TZ, "Грузии"),
        safe(lambda: weather_text(BATUMI, "Батуми"), "погоду в Батуми"),
        safe(usd_gel_text, "курс доллара к лари"),
        safe(bitcoin_text, "курс биткоина"),
    ]
    divider = "\n\n━━━━━━━━━━━━━━\n\n"
    return divider.join(["\n\n".join(kazakhstan), "\n\n".join(georgia)])


def send_message(chat_id: int, text: str) -> None:
    requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)


def handle_update(update: dict) -> None:
    message = update.get("message")
    if not message:
        return
    send_message(message["chat"]["id"], get_reply_text())


def skip_pending_updates() -> int | None:
    """Пропускает сообщения, пришедшие, пока бот был выключен.

    offset=-1 возвращает только самое последнее обновление. Возвращаем offset
    сразу за ним, и всё, что накопилось раньше, считается прочитанным.
    """
    data = requests.get(f"{API}/getUpdates", params={"offset": -1, "timeout": 0}, timeout=10).json()
    if data.get("ok") and data["result"]:
        skipped = data["result"][-1]["update_id"] + 1
        # Подтверждаем последнее обновление, чтобы Telegram не отдал его снова
        requests.get(f"{API}/getUpdates", params={"offset": skipped, "timeout": 0}, timeout=10)
        print("Пропущены сообщения, пришедшие, пока бот был офлайн.")
        return skipped
    return None


def main() -> None:
    me = requests.get(f"{API}/getMe", timeout=10).json()
    if not me.get("ok"):
        sys.exit(f"Токен не принят Telegram: {me.get('description')}")
    print(f"Бот @{me['result']['username']} запущен. Ctrl+C для остановки.")

    offset = skip_pending_updates()
    while True:
        try:
            resp = requests.get(
                f"{API}/getUpdates",
                params={"timeout": 30, "offset": offset, "allowed_updates": '["message"]'},
                timeout=40,
            )
            data = resp.json()
            if not data.get("ok"):
                print("Ошибка getUpdates:", data)
                time.sleep(5)
                continue
            for update in data["result"]:
                offset = update["update_id"] + 1
                handle_update(update)
        except requests.RequestException as e:
            print("Сетевая ошибка:", e)
            time.sleep(5)
        except KeyboardInterrupt:
            print("Остановлен.")
            break


if __name__ == "__main__":
    main()
