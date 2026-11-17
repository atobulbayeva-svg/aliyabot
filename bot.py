"""Telegram-бот: на любое сообщение отвечает погодой в Астане и курсом биткоина.

Погода берётся из Open-Meteo (https://open-meteo.com), курс — из CoinGecko.
Ключи API не нужны.
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
ASTANA_TZ = timezone(timedelta(hours=5))  # UTC+5, перехода на летнее время нет
BTC_URL ="https://api.coingecko.com/api/v3/simple/price"
ASTANA = {"latitude": 51.1694, "longitude": 71.4491}

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


def get_weather_text() -> str:
    resp = requests.get(
        WEATHER_URL,
        params={
            **ASTANA,
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
        "Погода в Астане сейчас\n"
        f"{description}\n"
        f"🌡 Температура: {c['temperature_2m']:+.0f}°C "
        f"(ощущается как {c['apparent_temperature']:+.0f}°C)\n"
        f"💧 Влажность: {c['relative_humidity_2m']}%\n"
        f"💨 Ветер: {c['wind_speed_10m']:.1f} м/с, {wind_direction(c['wind_direction_10m'])}\n"
        f"🧭 Давление: {pressure_mm:.0f} мм рт. ст."
    )


def get_bitcoin_text() -> str:
    resp = requests.get(
        BTC_URL,
        params={"ids": "bitcoin", "vs_currencies": "usd,kzt", "include_24hr_change": "true"},
        timeout=10,
    )
    resp.raise_for_status()
    b = resp.json()["bitcoin"]
    change = b["usd_24h_change"]
    arrow = "📈" if change >= 0 else "📉"
    return (
        "Курс биткоина\n"
        f"₿ {b['usd']:,.0f} $ ({b['kzt']:,.0f} ₸)\n".replace(",", " ")
        + f"{arrow} За 24 часа: {change:+.1f}%"
    )


def get_time_text() -> str:
    now = datetime.now(ASTANA_TZ)
    return f"🕐 Время в Астане: {now:%H:%M}, {now:%d.%m.%Y}"


def get_reply_text() -> str:
    parts = [get_time_text()]
    for fetch, error in ((get_weather_text, "погоду"), (get_bitcoin_text, "курс биткоина")):
        try:
            parts.append(fetch())
        except (requests.RequestException, KeyError, ValueError) as e:
            print(f"Ошибка получения: {error}:", e)
            parts.append(f"Не удалось получить {error}, попробуйте чуть позже.")
    return "\n\n".join(parts)


def send_message(chat_id: int, text: str) -> None:
    requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)


def handle_update(update: dict) -> None:
    message = update.get("message")
    if not message:
        return
    chat_id = message["chat"]["id"]
    send_message(chat_id, get_reply_text())


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
