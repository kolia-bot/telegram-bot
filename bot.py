import asyncio
import json
import os
import random
import time
from datetime import datetime, timezone, timedelta

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)

# =========================================================
# НАСТРОЙКИ
# =========================================================

TOKEN = ""
PROVIDER_TOKEN = "" # Токен платежного провайдера для Telegram Stars (оставьте пустым или укажите от BotFather)

START_BALANCE = 1000

BET_WAIT_SECONDS = 10
SPIN_SECONDS = 3
MAX_BETS_PER_GAME = 150

DUEL_MULTIPLIER = 2.0
TREASURY_COST = 100_000
INVITE_REWARD = 1_500

BALANCES_FILE = "balances.json"
LOG_FILE = "roulette_log.json"
TREASURIES_FILE = "treasuries.json"
STATS_FILE = "stats.json"
BONUS_FILE = "bonus_times.json"
OWNER_ID = 5984555148  # замени на свой Telegram ID
PROMOS_FILE = "promos.json"
PROMO_USES_FILE = "promo_uses.json"

MSK_TZ = timezone(timedelta(hours=3))

# =========================================================
# РУЛЕТКА
# =========================================================

RED = {
    1, 3, 5, 7, 9,
    12, 14, 16, 18,
    19, 21, 23, 25, 27,
    30, 32, 34, 36
}

BLACK = set(range(1, 37)) - RED

# =========================================================
# ФОРМАТИРОВАНИЕ ЧИСЕЛ
# =========================================================

def fmt(num) -> str:
    """Форматирует число с разделителями тысяч (100 000)."""
    try:
        val = int(round(float(num)))
        return f"{val:,}".replace(",", " ")
    except (ValueError, TypeError):
        return str(num)

# =========================================================
# РАБОТА С ФАЙЛАМИ JSON
# =========================================================

def load_json(filename, default):
    if not os.path.exists(filename):
        return default
    try:
        with open(filename, "r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, type(default)):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return default


def save_json(filename, data):
    temp_file = filename + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_file, filename)


balances = load_json(BALANCES_FILE, {})

for k, v in list(balances.items()):
    try:
        balances[k] = int(round(float(v)))
    except Exception:
        balances[k] = START_BALANCE

roulette_log = load_json(LOG_FILE, {})
treasuries = load_json(TREASURIES_FILE, {})
user_stats = load_json(STATS_FILE, {})
bonus_times = load_json(BONUS_FILE, {})
promos = load_json(PROMOS_FILE, {})
promo_uses = load_json(PROMO_USES_FILE, {})

def save_promos():
    save_json(PROMOS_FILE, promos)

def save_promo_uses():
    save_json(PROMO_USES_FILE, promo_uses)


async def promo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    parts = update.message.text.strip().split()

    # Создание: Создать промокод #promo 10000 20
    if len(parts) == 5 and parts[0].lower() == "создать" and parts[1].lower() == "промокод":
        if update.effective_user.id != OWNER_ID:
            await update.message.reply_text("❌ Создавать промокоды может только владелец.")
            return

        code = parts[2].lstrip("#").lower()

        try:
            reward = int(parts[3])
            limit = int(parts[4])
        except ValueError:
            await update.message.reply_text("Формат: Создать промокод #promo 10000 20")
            return

        if not code or reward <= 0 or limit <= 0:
            await update.message.reply_text("❌ Сумма и количество должны быть больше нуля.")
            return

        if code in promos:
            await update.message.reply_text("❌ Такой промокод уже существует.")
            return

        promos[code] = {"reward": reward, "limit": limit, "used": 0}
        promo_uses[code] = []
        save_promos()
        save_promo_uses()

        await update.message.reply_text(
            f"✅ Промокод #{code} создан: +{fmt(reward)} TON, активаций: {limit}."
        )
        return



    # Активация: #promo
    if len(parts) == 1 and parts[0].startswith("#"):
        code = parts[0].lstrip("#").lower()
        user_id = get_user(update.effective_user)

        promo = promos.get(code)
        if not promo:
            await update.message.reply_text("❌ Промокод не найден.")
            return

        used_by = promo_uses.setdefault(code, [])
        if user_id in used_by:
            await update.message.reply_text("❌ Ты уже использовал этот промокод.")
            return

        if promo["used"] >= promo["limit"]:
            await update.message.reply_text("❌ Лимит активаций исчерпан.")
            return

        balances[user_id] += promo["reward"]
        promo["used"] += 1
        used_by.append(user_id)

        save_balances()
        save_promos()
        save_promo_uses()

        await update.message.reply_text(
            f"🎉 Промокод активирован! Начислено: +{fmt(promo['reward'])} TON 💎"
        )


def save_balances():
    save_json(BALANCES_FILE, balances)


def save_log():
    save_json(LOG_FILE, roulette_log)


def save_treasuries():
    save_json(TREASURIES_FILE, treasuries)


def save_stats():
    save_json(STATS_FILE, user_stats)


def save_bonus_times():
    save_json(BONUS_FILE, bonus_times)

# =========================================================
# ПОЛЬЗОВАТЕЛИ И СТАТИСТИКА
# =========================================================

def get_user_display_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    name = (user.first_name or "").strip()
    return name if name else f"Игрок {user.id}"


def get_user(user):
    user_id = str(user.id)
    if user_id not in balances:
        balances[user_id] = START_BALANCE
        save_balances()

    disp_name = get_user_display_name(user)
    if user_id not in user_stats:
        user_stats[user_id] = {"name": disp_name, "win": 0}
        save_stats()
    else:
        if user_stats[user_id].get("name") != disp_name:
            user_stats[user_id]["name"] = disp_name
            save_stats()

    return user_id


def record_game_win(user_id: str, win_amount: int):
    if win_amount <= 0:
        return
    if user_id not in user_stats:
        user_stats[user_id] = {"name": f"Игрок {user_id}", "win": 0}
    user_stats[user_id]["win"] = int(user_stats[user_id].get("win", 0)) + int(round(win_amount))
    save_stats()

# =========================================================
# СОСТОЯНИЕ ИГР
# =========================================================

pending_bets = {}
roulette_ready_time = {}
roulette_running = {}
active_mines_games = {}
last_user_bets = {}

pending_duels = {}
active_duels = {}
duel_id_counter = 1

# Активные игры "Клад": user_id -> {"bet": int, "prizes": [0, 1, 2]}
active_treasure_games = {}

# Ожидание ввода своего количества звёзд: user_id -> True
waiting_for_custom_stars = {}

# =========================================================
# ФОНОВЫЙ СБРОС ТОПОВ В 00:00 ПО МСК
# =========================================================

async def daily_reset_task():
    while True:
        try:
            now_msk = datetime.now(MSK_TZ)
            next_midnight = (now_msk + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            wait_seconds = (next_midnight - now_msk).total_seconds()
            await asyncio.sleep(max(1.0, wait_seconds))

            for uid in user_stats:
                user_stats[uid]["win"] = 0
            save_stats()
            print(f"[{datetime.now(MSK_TZ).isoformat()}] Топ выигрышей сброшен.")
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Ошибка в сбросе топов: {e}")
            await asyncio.sleep(60)

# =========================================================
# ЛОГИКА МИН
# =========================================================

def get_mines_multiplier(opened_count, mines_count=3, total_cells=25):
    if opened_count == 0:
        return 1.0
    safe_cells = total_cells - mines_count
    mult = 1.0
    for i in range(opened_count):
        mult *= (total_cells - i) / (safe_cells - i)
    result = round(mult * 0.80, 2)
    return max(1.02, result)


def build_mines_keyboard(game_data, game_over=False, won=False):
    opened = game_data["opened"]
    mines = game_data["mines"]
    keyboard = []

    for row in range(5):
        row_buttons = []
        for col in range(5):
            idx = row * 5 + col
            if idx in opened:
                text = "💎"
            elif game_over and idx in mines:
                text = "💣"
            else:
                text = " "

            cb_data = f"mine_click_{idx}" if not game_over else "mine_ignore"
            row_buttons.append(InlineKeyboardButton(text, callback_data=cb_data))
        keyboard.append(row_buttons)

    if not game_over:
        opened_count = len(opened)
        mult = get_mines_multiplier(opened_count, game_data["mines_count"])
        win_amount = int(round(game_data["bet"] * mult))

        if opened_count > 0:
            cashout_text = f"💰 Забрать {fmt(win_amount)} TON (x{mult})"
            keyboard.append([InlineKeyboardButton(cashout_text, callback_data="mine_cashout")])
        else:
            keyboard.append([InlineKeyboardButton("🎯 Откройте ячейку", callback_data="mine_ignore")])

    return InlineKeyboardMarkup(keyboard)

# =========================================================
# РАЗБОР СТАВКИ РУЛЕТКИ
# =========================================================

def parse_bet(bet):
    bet = bet.lower()

    if bet in {"к", "красное"}:
        return RED, 2.0, "красное"

    if bet in {"ч", "черное", "чёрное"}:
        return BLACK, 2.0, "чёрное"

    if bet in {"чет", "чёт"}:
        numbers = {n for n in range(1, 37) if n % 2 == 0}
        return numbers, 2.0, "чёт"

    if bet == "нечет":
        numbers = {n for n in range(1, 37) if n % 2 != 0}
        return numbers, 2.0, "нечет"

    if "-" in bet:
        parts = bet.split("-", 1)
        if len(parts) != 2:
            return None
        try:
            start_num = int(parts[0])
            end_num = int(parts[1])
        except ValueError:
            return None

        if not (0 <= start_num <= end_num <= 36):
            return None

        numbers = set(range(start_num, end_num + 1))
        coefficient = 36 / len(numbers)
        return (numbers, coefficient, f"{start_num}-{end_num}")

    try:
        number = int(bet)
    except ValueError:
        return None

    if not 0 <= number <= 36:
        return None

    return ({number}, 36.0, f"{number}")

# =========================================================
# START И МЕНЮ КНОПКИ В ЛС
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = get_user(update.effective_user)
    chat_type = update.effective_chat.type

    reply_markup = None
    if chat_type == "private":
        keyboard = [
            [KeyboardButton("🎁 Забрать бонус"), KeyboardButton("🎮 Игры")],
            [KeyboardButton("📜 Команды"), KeyboardButton("⭐️ Донат")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "🎰 Виртуальное казино\n\n"
        f"💰 Баланс: {fmt(balances[user_id])} TON 💎\n\n"
        "Используй кнопки внизу или команды:\n"
        "• <b>игры</b> — список доступных игр\n"
        "• <b>команды</b> — справка по командам бота\n"
        "• <b>донат</b> — пополнить баланс за Telegram Stars ⭐️",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

# =========================================================
# СПРАВКИ: КОМАНДЫ И ИГРЫ
# =========================================================

async def show_commands_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    text = (
        "📜 <b>СПРАВКА ПО КОМАНДАМ БОТА:</b>\n\n"
        "🎡 <b>Рулетка:</b>\n"
        "• <code>[сумма] [ставка]</code> — сделать ставку (например: <code>100 к</code> или <code>50 0-12</code>)\n"
        "• <b>го</b> — запустить рулетку (после ставки)\n"
        "• <b>отмена</b> — отменить свои ставки в текущем раунде\n"
        "• <b>ставки</b> — посмотреть список твоих сделанных ставок\n"
        "• <b>лог</b> — последние результаты рулетки\n\n"
        "📦 <b>Клад:</b>\n"
        "• <code>клад [сумма]</code> — сыграть в сундуки (0х, 1х, 2х)\n\n"
        "💣 <b>Мины:</b>\n"
        "• <code>мины [сумма]</code> — начать игру с 3 минами\n"
        "• <code>мины [сумма] [кол-во]</code> — игра с кастомным числом мин (до 24)\n\n"
        "🤠 <b>Дуэли:</b>\n"
        "• <code>дуэль [сумма]</code> (ответом на сообщение оппонента) — вызвать игрока на дуэль (x2)\n\n"
        "🏛 <b>Казна чата:</b>\n"
        "• <b>купить казну</b> — покупка казны владельцем чата (100 000 TON)\n"
        "• <code>пополнить казну [сумма]</code> — пополнение казны\n"
        "• <b>казна</b> — статус и баланс казны (+1 500 за приглашение)\n\n"
        "👤 <b>Прочее:</b>\n"
        "• <b>б</b> или <b>баланс</b> — узнать свой баланс\n"
        "• <b>топ</b> — таблица лидеров по балансу и выигрышам\n"
        "• <code>дать [сумма]</code> (ответом) — передать валюту игроку\n"
        "• <b>донат</b> — пополнение за звезды ⭐️"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def show_games_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    text = (
        "🎮 <b>ДОСТУПНЫЕ ИГРЫ В КАЗИНО:</b>\n\n"
        "1️⃣ <b>Рулетка</b>\n"
        "Классическая европейская рулетка (0-36). Ставки на цвета (к/ч), чёт/нечет, числа и диапазоны.\n"
        "📌 <i>Команда:</i> <code>100 к</code> (или пакетные ставки: <code>100 к чёт 0-12</code>), затем <b>го</b>\n\n"
        "2️⃣ <b>Клад 📦</b>\n"
        "Испытай удачу в 3 сундуках! В одном пусто, в другом возврат (x1), в третьем куш (x2).\n"
        "📌 <i>Команда:</i> <code>клад 500</code>\n\n"
        "3️⃣ <b>Мины 💣</b>\n"
        "Открывай безопасные кристаллы на поле 5х5 и забирай растущий коэффициент, избегая мин.\n"
        "📌 <i>Команда:</i> <code>мины 200 3</code>\n\n"
        "4️⃣ <b>Дуэли 🤠</b>\n"
        "Постреляйся с реальным игроком один на один с коэффициентом x2!\n"
        "📌 <i>Команда:</i> <code>дуэль 1000</code> (ответом на сообщение оппонента)"
    )
    await update.message.reply_text(text, parse_mode="HTML")

# =========================================================
# ИГРА «КЛАД» (СУНДУКИ)
# =========================================================

async def handle_treasure_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = get_user(update.effective_user)
    parts = update.message.text.split()

    if len(parts) < 2:
        await update.message.reply_text(
            "❌ Укажи ставку для игры в Клад:\nПример: <code>клад 100</code>",
            parse_mode="HTML"
        )
        return

    try:
        bet = int(parts[1].replace(" ", ""))
    except ValueError:
        await update.message.reply_text("❌ Ставка должна быть целым числом.")
        return

    if bet <= 0:
        await update.message.reply_text("❌ Ставка должна быть больше 0.")
        return

    current_bal = int(round(balances[user_id]))
    balances[user_id] = current_bal

    if current_bal < bet:
        await update.message.reply_text(
            f"❌ Недостаточно TON для игры!\nТвой баланс: <b>{fmt(current_bal)} TON 💎</b>",
            parse_mode="HTML"
        )
        return

    # Списываем ставку
    balances[user_id] -= bet
    save_balances()

    # Генерируем результаты для 3 сундуков:
    # 0 - пусто (сгорает), 1 - возврат (x1), 2 - куш (x2)
    prizes = [0, 1, 2]
    random.shuffle(prizes)

    active_treasure_games[user_id] = {
        "bet": bet,
        "prizes": prizes,
        "opened": False
    }

    keyboard = [
        [
            InlineKeyboardButton("📦 Сундук 1", callback_data=f"treasure_{user_id}_0"),
            InlineKeyboardButton("📦 Сундук 2", callback_data=f"treasure_{user_id}_1"),
            InlineKeyboardButton("📦 Сундук 3", callback_data=f"treasure_{user_id}_2"),
        ]
    ]

    await update.message.reply_text(
        f"📦 <b>ИГРА В КЛАД</b>\n\n"
        f"💰 Ставка: <b>{fmt(bet)} TON 💎</b>\n"
        f"Перед тобой 3 сундука. В одном пусто, во втором возврат (x1), в третьем куш (x2)!\n\n"
        f"👇 <i>Выбери свой сундук:</i>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def treasure_callback_handler(query, user_id: str, box_idx: int):
    q_user_id = str(query.from_user.id)
    if q_user_id != user_id:
        await query.answer("Это не твоя игра в клад!", show_alert=True)
        return

    game = active_treasure_games.get(user_id)
    if not game or game["opened"]:
        await query.answer("Эта игра уже завершена!", show_alert=True)
        return

    game["opened"] = True
    bet = game["bet"]
    prizes = game["prizes"]

    chosen_prize = prizes[box_idx]

    # Раскрываем все сундуки для красоты
    icons = {0: "❌ (пусто)", 1: "🔙 (возврат x1)", 2: "💎 (куш x2)"}
    chest_results = [icons[p] for p in prizes]

    result_text = (
        f"📦 <b>ИТОГИ ИГРЫ В КЛАД</b>\n\n"
        f"Сундуки скрывали:\n"
        f"1️⃣ {chest_results[0]}\n"
        f"2️⃣ {chest_results[1]}\n"
        f"3️⃣ {chest_results[2]}\n\n"
        f"🎯 Ты выбрал сундук №{box_idx + 1}!\n"
    )

    if chosen_prize == 0:
        # Пусто
        record_game_win(user_id, 0)
        result_text += f"💥 <b>Увы, здесь пусто!</b> Ставка сгорела.\n💸 Потеряно: <b>{fmt(bet)} TON</b>"
    elif chosen_prize == 1:
        # Возврат x1
        win_amount = bet
        balances[user_id] = int(round(balances[user_id])) + win_amount
        save_balances()
        record_game_win(user_id, win_amount)
        result_text += f"🔙 <b>Возврат ставки (x1)!</b>\n💳 На баланс возвращено: <b>{fmt(win_amount)} TON</b>"
    else:
        # Куш x2
        win_amount = bet * 2
        balances[user_id] = int(round(balances[user_id])) + win_amount
        save_balances()
        record_game_win(user_id, win_amount)
        result_text += f"🎉 <b>КУШ! МЕГА-ПОБЕДА (x2)!</b>\n💰 Выигрыш: <b>+{fmt(win_amount)} TON 💎</b>"

    result_text += f"\n\n💰 Твой баланс: <b>{fmt(balances[user_id])} TON 💎</b>"

    active_treasure_games.pop(user_id, None)

    try:
        await query.edit_message_text(result_text, parse_mode="HTML")
    except Exception:
        await query.message.reply_text(result_text, parse_mode="HTML")
    await query.answer("Сундук открыт!")

# =========================================================
# БАЛАНС
# =========================================================

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    user_id = get_user(user)
    name = f"@{user.username}" if user.username else (user.first_name or "Игрок")

    await update.message.reply_text(
        f"{name}\n💰 Твой баланс: <b>{fmt(balances[user_id])} TON 💎</b>",
        parse_mode="HTML"
    )

# =========================================================
# СПИСОК СТАВОК ИГРОКА (КОМАНДА "СТАВКИ")
# =========================================================

async def show_my_bets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.effective_chat.id
    user_id = get_user(update.effective_user)

    bets = pending_bets.get(chat_id, [])
    my_bets = [b for b in bets if b["user_id"] == user_id]

    if not my_bets:
        await update.message.reply_text("❌ У тебя нет активных ставок в этом раунде рулетки.")
        return

    total_spent = sum(b["amount"] for b in my_bets)
    lines = ["📋 <b>Твои активные ставки:</b>\n"]

    for b in my_bets:
        lines.append(f"• <b>{fmt(b['amount'])} TON</b> на <b>{b['bet_name']}</b> (x{b['coefficient']:.2f})")

    lines.append("———————————————")
    lines.append(f"💰 Всего списано за раунд: <b>{fmt(total_spent)} TON 💎</b>")
    lines.append(f"🎯 Всего сделано ставок: <b>{len(my_bets)} из {MAX_BETS_PER_GAME}</b>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

# =========================================================
# ТОП ПО БАЛАНСУ И ВЫИГРЫШАМ
# =========================================================

def build_top_menu():
    kb = [
        [
            InlineKeyboardButton("💰 Топ балансов", callback_data="top_balances"),
            InlineKeyboardButton("🏆 Топ выигрышей", callback_data="top_wins")
        ]
    ]
    return InlineKeyboardMarkup(kb)


async def show_top_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    await update.message.reply_text(
        "📊 <b>Выбери, какой топ хочешь посмотреть:</b>\n"
        "<i>(Списки выигрышей обновляются ежедневно в 00:00 по МСК)</i>",
        reply_markup=build_top_menu(),
        parse_mode="HTML"
    )


def render_top_balances() -> str:
    sorted_users = sorted(
        balances.items(),
        key=lambda item: float(item[1]),
        reverse=True
    )[:20]

    lines = ["👑 <b>ТОП-20 ПО БАЛАНСУ:</b>\n"]
    if not sorted_users:
        lines.append("Список пока пуст.")
    else:
        for idx, (uid, bal) in enumerate(sorted_users, start=1):
            name = user_stats.get(uid, {}).get("name") or f"Игрок {uid}"
            lines.append(f"<b>{idx}.</b> {name} — <code>{fmt(bal)}</code> TON 💎")

    lines.append("\n<i>Обновляется в реальном времени</i>")
    return "\n".join(lines)


def render_top_wins() -> str:
    sorted_users = sorted(
        user_stats.items(),
        key=lambda item: int(item[1].get("win", 0)),
        reverse=True
    )[:20]

    lines = ["🏆 <b>ТОП-20 ПО ВЫИГРЫШАМ ЗА ДЕНЬ (ГРЯЗНЫЙ ВЫИГРЫШ):</b>\n"]
    valid_entries = [u for u in sorted_users if u[1].get("win", 0) > 0]

    if not valid_entries:
        lines.append("Сегодня выигрышей ещё не зафиксировано.")
    else:
        for idx, (uid, data) in enumerate(valid_entries, start=1):
            name = data.get("name") or f"Игрок {uid}"
            win = data.get("win", 0)
            lines.append(f"<b>{idx}.</b> {name} — <b>+{fmt(win)}</b> TON 💎")

    lines.append("\n<i>⏰ Сброс статистики каждый день в 00:00 по МСК</i>")
    return "\n".join(lines)

# =========================================================
# ДУЭЛИ
# =========================================================

async def handle_duel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global duel_id_counter
    if not update.message:
        return

    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text("❌ Дуэли проводятся только в группе!")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "❌ Используй команду ответом на сообщение игрока:\n<code>дуэль 500</code>",
            parse_mode="HTML"
        )
        return

    parts = update.message.text.split()
    if len(parts) < 2:
        await update.message.reply_text(
            "❌ Укажи ставку: <code>дуэль 500</code>",
            parse_mode="HTML"
        )
        return

    try:
        amount = int(parts[1].replace(" ", ""))
    except ValueError:
        await update.message.reply_text("❌ Ставка должна быть целым числом.")
        return

    if amount <= 0:
        await update.message.reply_text("❌ Ставка должна быть больше 0.")
        return

    challenger = update.effective_user
    opponent = update.message.reply_to_message.from_user

    if opponent.is_bot:
        await update.message.reply_text("❌ Нельзя вызывать ботов на дуэль!")
        return

    if challenger.id == opponent.id:
        await update.message.reply_text("❌ Нельзя стреляться с самим собой!")
        return

    c_id = get_user(challenger)
    o_id = get_user(opponent)

    c_bal = int(round(balances[c_id]))
    o_bal = int(round(balances[o_id]))

    if c_bal < amount:
        await update.message.reply_text(
            f"❌ У вас недостаточно TON для этой дуэли!\nВаш баланс: <b>{fmt(c_bal)} TON 💎</b>",
            parse_mode="HTML"
        )
        return

    if o_bal < amount:
        o_name = get_user_display_name(opponent)
        await update.message.reply_text(
            f"❌ У {o_name} недостаточно TON для дуэли!\nЕго баланс: <b>{fmt(o_bal)} TON 💎</b>",
            parse_mode="HTML"
        )
        return

    duel_id = duel_id_counter
    duel_id_counter += 1

    win_amount = int(round(amount * DUEL_MULTIPLIER))
    c_name = get_user_display_name(challenger)
    o_name = get_user_display_name(opponent)

    pending_duels[duel_id] = {
        "chat_id": chat.id,
        "challenger_id": c_id,
        "challenger_name": c_name,
        "opponent_id": o_id,
        "opponent_name": o_name,
        "amount": amount,
        "win_amount": win_amount,
        "created_at": time.time()
    }

    keyboard = [
        [
            InlineKeyboardButton("🎯 Принять вызов", callback_data=f"duel_accept_{duel_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"duel_decline_{duel_id}")
        ]
    ]

    await update.message.reply_text(
        f"⚔️ <b>ВЫЗОВ НА ДУЭЛЬ!</b>\n\n"
        f"🤠 <b>{c_name}</b> бросает вызов <b>{o_name}</b>!\n"
        f"💰 Ставка каждого: <b>{fmt(amount)} TON 💎</b>\n"
        f"🏆 Победитель забирает банк: <b>{fmt(win_amount)} TON 💎</b> (x{int(DUEL_MULTIPLIER)})\n\n"
        f"{o_name}, ты принимаешь дуэль?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def duel_callback_handler(query, data: str):
    user_id = str(query.from_user.id)

    if data.startswith("duel_decline_"):
        duel_id = int(data.split("_")[2])
        duel = pending_duels.get(duel_id)
        if not duel:
            await query.answer("Дуэль уже недействительна.", show_alert=True)
            return

        if user_id != duel["opponent_id"] and user_id != duel["challenger_id"]:
            await query.answer("Это не твой вызов на дуэль!", show_alert=True)
            return

        pending_duels.pop(duel_id, None)

        if user_id == duel["opponent_id"]:
            reason_text = f"❌ <b>{duel['opponent_name']} отклонил вызов на дуэль!</b>\nДеньги ни у кого не списаны."
        else:
            reason_text = f"❌ <b>{duel['challenger_name']} отозвал свой вызов на дуэль.</b>"

        await query.edit_message_text(reason_text, parse_mode="HTML")
        await query.answer("Вызов отклонён.")
        return

    if data.startswith("duel_accept_"):
        duel_id = int(data.split("_")[2])
        duel = pending_duels.get(duel_id)
        if not duel:
            await query.answer("Дуэль не найдена или уже завершена.", show_alert=True)
            return

        if user_id != duel["opponent_id"]:
            await query.answer("Принять дуэль может только тот, кому бросили вызов!", show_alert=True)
            return

        c_id = duel["challenger_id"]
        o_id = duel["opponent_id"]
        amount = duel["amount"]

        if int(round(balances.get(c_id, 0))) < amount:
            pending_duels.pop(duel_id, None)
            await query.edit_message_text(
                f"❌ Дуэль отменена: у {duel['challenger_name']} не хватает средств!",
                parse_mode="HTML"
            )
            return

        if int(round(balances.get(o_id, 0))) < amount:
            pending_duels.pop(duel_id, None)
            await query.edit_message_text(
                f"❌ У тебя недостаточно средств для дуэли!",
                parse_mode="HTML"
            )
            return

        balances[c_id] -= amount
        balances[o_id] -= amount
        save_balances()
        pending_duels.pop(duel_id, None)

        turn_id = random.choice([c_id, o_id])
        turn_name = duel["challenger_name"] if turn_id == c_id else duel["opponent_name"]

        active_duels[duel_id] = {
            "challenger_id": c_id,
            "challenger_name": duel["challenger_name"],
            "opponent_id": o_id,
            "opponent_name": duel["opponent_name"],
            "amount": amount,
            "win_amount": duel["win_amount"],
            "turn_id": turn_id,
            "last_action_text": "Дуэлянты зарядили револьверы и встали к барьеру."
        }

        keyboard = [
            [
                InlineKeyboardButton("🔫 Выстрел", callback_data=f"duel_shoot_{duel_id}"),
                InlineKeyboardButton("🏳 Отказаться от дуэли", callback_data=f"duel_surrender_{duel_id}")
            ]
        ]

        await query.edit_message_text(
            f"⚔️ <b>ДУЭЛЬ ПРИНЯТА И НАЧАЛАСЬ!</b>\n\n"
            f"🤠 {duel['challenger_name']} ⚡️ {duel['opponent_name']}\n"
            f"💰 Ставка каждого: <b>{fmt(amount)} TON 💎</b>\n"
            f"🏆 Победитель заберёт: <b>{fmt(duel['win_amount'])} TON 💎</b> (x{int(DUEL_MULTIPLIER)})\n\n"
            f"📝 <i>Дуэлянты встали на позиции и взвели курки.</i>\n\n"
            f"👉 Право первого выстрела за: <b>{turn_name}</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        await query.answer("Вы приняли дуэль!")
        return

    if data.startswith("duel_shoot_"):
        duel_id = int(data.split("_")[2])
        duel = active_duels.get(duel_id)
        if not duel:
            await query.answer("Дуэль завершена или не найдена.", show_alert=True)
            return

        c_id = duel["challenger_id"]
        o_id = duel["opponent_id"]

        if user_id != c_id and user_id != o_id:
            await query.answer("Ты не участвуешь в этой дуэли!", show_alert=True)
            return

        if user_id != duel["turn_id"]:
            await query.answer("Сейчас не твоя очередь стрелять!", show_alert=True)
            return

        shooter_name = duel["challenger_name"] if user_id == c_id else duel["opponent_name"]
        target_id = o_id if user_id == c_id else c_id
        target_name = duel["opponent_name"] if user_id == c_id else duel["challenger_name"]

        roll = random.random()

        if roll < 0.40:
            win_amount = duel["win_amount"]
            balances[user_id] = int(round(balances[user_id])) + win_amount
            save_balances()

            record_game_win(user_id, win_amount)
            active_duels.pop(duel_id, None)

            await query.edit_message_text(
                f"⚔️ <b>ДУЭЛЬ ОКОНЧЕНА!</b>\n\n"
                f"🤠 <b>{shooter_name}</b> нажимает на спуск...\n"
                f"💥 <b>БАХ! ТОЧНОЕ ПОПАДАНИЕ!</b> Игрок <b>{target_name}</b> сражён наповал!\n\n"
                f"🏆 Победитель: <b>{shooter_name}</b>\n"
                f"🎉 Награда: <b>+{fmt(win_amount)} TON 💎</b> (x{int(DUEL_MULTIPLIER)})\n"
                f"💰 Баланс победителя: <b>{fmt(balances[user_id])} TON 💎</b>",
                parse_mode="HTML"
            )
            await query.answer("Точный выстрел! 💥")
            return

        elif roll < 0.85:
            action_text = f"💨 <b>{shooter_name}</b> выстрелил, но <b>ПРОМАХНУЛСЯ</b>! Пуля ушла в молоко."
            duel["turn_id"] = target_id
            duel["last_action_text"] = action_text
        else:
            action_text = f"🔧 <b>{shooter_name}</b> нажимает на курок... <b>ПИСТОЛЕТ ЗАКЛИНИЛ</b>! Осечка!"
            duel["turn_id"] = target_id
            duel["last_action_text"] = action_text

        next_shooter_name = target_name

        keyboard = [
            [
                InlineKeyboardButton("🔫 Выстрел", callback_data=f"duel_shoot_{duel_id}"),
                InlineKeyboardButton("🏳 Отказаться от дуэли", callback_data=f"duel_surrender_{duel_id}")
            ]
        ]

        await query.edit_message_text(
            f"⚔️ <b>ИДЕТ ДУЭЛЬ!</b>\n\n"
            f"🤠 {duel['challenger_name']} ⚡️ {duel['opponent_name']}\n"
            f"💰 Ставка каждого: <b>{fmt(duel['amount'])} TON 💎</b>\n"
            f"🏆 Банк на кону: <b>{fmt(duel['win_amount'])} TON 💎</b>\n\n"
            f"📝 {action_text}\n\n"
            f"👉 Очередь стрелять переходит к: <b>{next_shooter_name}</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        await query.answer("Осечка/Промах! Переход хода.")
        return

    if data.startswith("duel_surrender_"):
        duel_id = int(data.split("_")[2])
        duel = active_duels.get(duel_id)
        if not duel:
            await query.answer("Дуэль завершена или не найдена.", show_alert=True)
            return

        c_id = duel["challenger_id"]
        o_id = duel["opponent_id"]

        if user_id != c_id and user_id != o_id:
            await query.answer("Ты не участник этой дуэли!", show_alert=True)
            return

        surrendered_name = duel["challenger_name"] if user_id == c_id else duel["opponent_name"]
        winner_id = o_id if user_id == c_id else c_id
        winner_name = duel["opponent_name"] if user_id == c_id else duel["challenger_name"]
        win_amount = duel["win_amount"]

        balances[winner_id] = int(round(balances[winner_id])) + win_amount
        save_balances()
        record_game_win(winner_id, win_amount)
        active_duels.pop(duel_id, None)

        await query.edit_message_text(
            f"🏳 <b>ДУЭЛЬ ЗАВЕРШЕНА СДАЧЕЙ!</b>\n\n"
            f"🤠 Игрок <b>{surrendered_name}</b> испугался и отказался от продолжения дуэли!\n"
            f"💸 Его ставка <b>{fmt(duel['amount'])} TON</b> безвозвратно потеряна.\n\n"
            f"🏆 Победитель: <b>{winner_name}</b>\n"
            f"🎉 Получает весь банк: <b>+{fmt(win_amount)} TON 💎</b> (x{int(DUEL_MULTIPLIER)})\n"
            f"💰 Новый баланс победителя: <b>{fmt(balances[winner_id])} TON 💎</b>",
            parse_mode="HTML"
        )
        await query.answer("Вы отказались от дуэли 🏳")
        return

# =========================================================
# ДОНАТ ЗА TELEGRAM STARS ⭐️ (АВТОМАТИЧЕСКАЯ ВЫДАЧА)
# Курс: 1 звезда ⭐️ = 3 000 TON
# =========================================================

STARS_EXCHANGE_RATE = 3_000  # 1 звезда = 3 000 TON (или измени по своему усмотрению)

async def show_donate_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    keyboard = [
        [
            InlineKeyboardButton("50 ⭐️ (150 тыс. TON)", callback_data="donate_stars_50"),
            InlineKeyboardButton("100 ⭐️ (300 тыс. TON)", callback_data="donate_stars_100")
        ],
        [
            InlineKeyboardButton("250 ⭐️", callback_data="donate_stars_250"),
            InlineKeyboardButton("500 ⭐️", callback_data="donate_stars_500")
        ],
        [
            InlineKeyboardButton("1 000 ⭐️", callback_data="donate_stars_1000"),
            InlineKeyboardButton("2 500 ⭐️", callback_data="donate_stars_2500")
        ],
        [
            InlineKeyboardButton("✍️ Ввести своё количество звёзд", callback_data="donate_custom")
        ]
    ]

    await update.message.reply_text(
        "⭐️ <b>ПОПОЛНЕНИЕ БАЛАНСА ЗА TELEGRAM STARS</b>\n\n"
        f"Курс обмена: <b>1 ⭐️ = {fmt(STARS_EXCHANGE_RATE)} TON 💎</b>\n\n"
        "Выбери пакет звёзд ниже или введи своё количество (от 5 до 10 000):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def donate_callback_handler(query, data: str, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(query.from_user.id)

    if data == "donate_custom":
        waiting_for_custom_stars[user_id] = True
        await query.edit_message_text(
            "✍️ <b>ВВЕДИ СВОЁ КОЛИЧЕСТВО ЗВЁЗД:</b>\n\n"
            "Отправь в чат число от 5 до 10 000 (например: <code>300</code>)",
            parse_mode="HTML"
        )
        await query.answer()
        return

    if data.startswith("donate_stars_"):
        stars_count = int(data.split("_")[2])
        await create_stars_invoice(query.message, context, stars_count)
        await query.answer()


async def create_stars_invoice(message, context: ContextTypes.DEFAULT_TYPE, stars_count: int):
    if not (5 <= stars_count <= 10000):
        await message.reply_text("❌ Количество звёзд должно быть от 5 до 10 000!")
        return

    ton_amount = stars_count * STARS_EXCHANGE_RATE
    title = f"Пополнение баланса на {fmt(ton_amount)} TON"
    description = f"Покупка игровой валюты за {stars_count} Telegram Stars ⭐️"
    payload = f"donate_{message.chat.id}_{stars_count}_{int(time.time())}"
    currency = "XTR"  # Валюта Telegram Stars
    prices = [LabeledPrice(f"{stars_count} ⭐️ Stars", stars_count)]

    try:
        await context.bot.send_invoice(
            chat_id=message.chat.id,
            title=title,
            description=description,
            payload=payload,
            provider_token=PROVIDER_TOKEN,
            currency=currency,
            prices=prices,
            start_parameter="donate-topup"
        )
    except Exception as e:
        await message.reply_text(
            f"⚠️ Ошибка создания счёта звёзд: {e}\n\n"
            f"<i>(Убедитесь, что бот настроен как платежный магазин в BotFather или используется тестовый режим)</i>",
            parse_mode="HTML"
        )


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    # Подтверждаем платеж перед списанием
    await query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    stars_paid = payment.total_amount
    user_id = str(update.effective_user.id)

    # Автоматическая выдача валюты
    ton_reward = stars_paid * STARS_EXCHANGE_RATE
    balances[user_id] = int(round(balances.get(user_id, START_BALANCE))) + ton_reward
    save_balances()

    await update.message.reply_text(
        f"✅ <b>ОПЛАТА УСПЕШНО ПРОШЛА!</b>\n\n"
        f"⭐️ Списано звёзд: <b>{stars_paid} ⭐️</b>\n"
        f"💳 Зачислено на баланс: <b>+{fmt(ton_reward)} TON 💎</b>\n"
        f"💰 Твой новый баланс: <b>{fmt(balances[user_id])} TON 💎</b>",
        parse_mode="HTML"
    )

# =========================================================
# КАЗНА ЧАТА
# =========================================================

async def buy_treasury(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat = update.effective_chat
    user = update.effective_user
    chat_id = chat.id
    chat_key = str(chat_id)

    if chat.type in ("private",):
        await update.message.reply_text("❌ Казну можно создать только в группе или супергруппе!")
        return

    user_id = get_user(user)

    try:
        member = await chat.get_member(user.id)
        if member.status != "creator":
            await update.message.reply_text("❌ Только владелец (создатель) чата может купить казну!")
            return
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось проверить права владельца: {e}")
        return

    if chat_key in treasuries:
        await update.message.reply_text(
            f"🏛 В этом чате казна уже создана!\n"
            f"💰 Баланс казны: <b>{fmt(treasuries[chat_key]['balance'])} TON 💎</b>",
            parse_mode="HTML"
        )
        return

    if int(round(balances[user_id])) < TREASURY_COST:
        await update.message.reply_text(
            f"❌ Недостаточно средств для покупки казны!\n"
            f"Стоимость: <b>{fmt(TREASURY_COST)} TON 💎</b>\n"
            f"Твой баланс: <b>{fmt(balances[user_id])} TON 💎</b>",
            parse_mode="HTML"
        )
        return

    balances[user_id] -= TREASURY_COST
    save_balances()

    treasuries[chat_key] = {
        "balance": 0,
        "owner_id": user_id,
        "created_at": datetime.now(MSK_TZ).isoformat()
    }
    save_treasuries()

    await update.message.reply_text(
        f"🏛 <b>Казна чата успешно приобретена!</b>\n\n"
        f"👑 Владелец: {get_user_display_name(user)}\n"
        f"💸 Списано: <code>{fmt(TREASURY_COST)}</code> TON 💎\n"
        f"💰 Баланс казны: <code>0</code> TON 💎\n\n"
        f"Теперь любой игрок может пополнить её командой: <code>Пополнить казну сумма</code>\n"
        f"Каждый, кто пригласит нового участника, получит <b>{fmt(INVITE_REWARD)} TON</b> из казны!",
        parse_mode="HTML"
    )


async def deposit_treasury(update: Update, amount: int):
    if not update.message:
        return

    chat_key = str(update.effective_chat.id)
    if chat_key not in treasuries:
        await update.message.reply_text(
            "🏛 В этом чате ещё нет казны!\n"
            f"Владелец чата может купить её за <b>{fmt(TREASURY_COST)} TON</b> командой: <code>Купить казну</code>",
            parse_mode="HTML"
        )
        return

    if amount <= 0:
        await update.message.reply_text("❌ Сумма пополнения должна быть больше 0.")
        return

    user = update.effective_user
    user_id = get_user(user)

    if int(round(balances[user_id])) < amount:
        await update.message.reply_text(
            f"❌ Недостаточно средств!\n"
            f"Нужно: {fmt(amount)} TON 💎\n"
            f"Твой баланс: {fmt(balances[user_id])} TON 💎"
        )
        return

    balances[user_id] -= amount
    treasuries[chat_key]["balance"] += amount

    save_balances()
    save_treasuries()

    name = get_user_display_name(user)
    await update.message.reply_text(
        f"🏛 <b>Казна пополнена!</b>\n\n"
        f"👤 Благодетель: {name}\n"
        f"➕ Внесено: <b>{fmt(amount)} TON 💎</b>\n"
        f"💰 Текущий баланс казны: <b>{fmt(treasuries[chat_key]['balance'])} TON 💎</b>",
        parse_mode="HTML"
    )


async def show_treasury_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text("🏛 Казна работает только в группах!")
        return

    chat_key = str(chat.id)
    if chat_key not in treasuries:
        await update.message.reply_text(
            "🏛 В этом чате казна ещё не куплена.\n\n"
            f"Владелец может купить её за <b>{fmt(TREASURY_COST)} TON 💎</b>\n"
            f"Команда: <code>Купить казну</code>",
            parse_mode="HTML"
        )
        return

    bal = treasuries[chat_key]["balance"]
    await update.message.reply_text(
        f"🏛 <b>КАЗНА ЧАТА</b>\n\n"
        f"💰 Баланс казны: <b>{fmt(bal)} TON 💎</b>\n"
        f"🎁 Награда за 1 приглашённого участника: <b>{fmt(INVITE_REWARD)} TON 💎</b>\n\n"
        f"<i>Пополнить казну:</i> <code>Пополнить казну [сумма]</code>\n"
        f"<i>(Снимать средства с казны нельзя — они идут только на выплаты пригласившим)</i>",
        parse_mode="HTML"
    )


async def chat_member_joined_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.new_chat_members:
        return

    chat = update.effective_chat
    chat_key = str(chat.id)

    if chat_key not in treasuries:
        return

    inviter = update.message.from_user
    if not inviter or inviter.is_bot:
        return

    inviter_id = get_user(inviter)
    new_users = [u for u in update.message.new_chat_members if not u.is_bot and u.id != inviter.id]

    if not new_users:
        return

    total_reward = len(new_users) * INVITE_REWARD
    current_treasury = treasuries[chat_key]["balance"]

    if current_treasury <= 0:
        await update.message.reply_text(
            f"🏛 В казне чата закончились средства для награды за приглашение!",
            parse_mode="HTML"
        )
        return

    paid = min(total_reward, current_treasury)
    treasuries[chat_key]["balance"] -= paid
    balances[inviter_id] = balances.get(inviter_id, 0) + paid

    save_balances()
    save_treasuries()

    name = get_user_display_name(inviter)
    await update.message.reply_text(
        f"🎉 <b>Награда за приглашение!</b>\n\n"
        f"👤 {name} пригласил(а) участников: {len(new_users)}\n"
        f"💰 Выплачено из казны: <b>+{fmt(paid)} TON 💎</b>\n"
        f"🏛 Остаток в казне: <b>{fmt(treasuries[chat_key]['balance'])} TON 💎</b>",
        parse_mode="HTML"
    )

# =========================================================
# СТАВКИ И РУЛЕТКА
# =========================================================

async def roulette_batch_bets(update: Update, amount: int, valid_bets: list):
    if not update.message:
        return

    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = get_user(user)

    if roulette_running.get(chat_id, False):
        await update.message.reply_text("🎰 Рулетка уже крутится.\nДождись результата.")
        return

    if amount <= 0:
        await update.message.reply_text("❌ Ставка должна быть больше 0.")
        return

    bets = pending_bets.setdefault(chat_id, [])

    existing_user_bets = sum(1 for b in bets if b["user_id"] == user_id)
    if existing_user_bets + len(valid_bets) > MAX_BETS_PER_GAME:
        await update.message.reply_text(
            f"❌ Ограничение: не более {MAX_BETS_PER_GAME} ставок на игру от одного игрока!\n"
            f"У вас уже сделано: {existing_user_bets} шт."
        )
        return

    total_amount = amount * len(valid_bets)

    current_user_bal = int(round(balances[user_id]))
    balances[user_id] = current_user_bal

    if total_amount > current_user_bal:
        await update.message.reply_text(
            f"❌ Недостаточно TON для этой ставки.\n"
            f"Требуется: {fmt(total_amount)} TON\n"
            f"Ваш баланс: {fmt(current_user_bal)} TON 💎"
        )
        return

    if not bets:
        roulette_ready_time[chat_id] = asyncio.get_running_loop().time() + BET_WAIT_SECONDS

    balances[user_id] -= total_amount
    save_balances()

    raw_user_name = user.first_name or f"Игрок {user.id}"
    username_tag = f"@{user.username}" if user.username else raw_user_name

    placed_lines = []
    for bet_str, (numbers, coefficient, bet_name) in valid_bets:
        bets.append({
            "user_id": user_id,
            "username": username_tag,
            "raw_name": raw_user_name,
            "amount": amount,
            "bet_name": bet_name,
            "numbers": list(numbers),
            "coefficient": coefficient,
            "raw_bet": bet_str
        })
        placed_lines.append(f"{raw_user_name} — {fmt(amount)} на {bet_name}")

    placed_text = "\n".join(placed_lines[:15])
    if len(placed_lines) > 15:
        placed_text += f"\n... и ещё {len(placed_lines) - 15} ставок"

    await update.message.reply_text(
        f"✅ <b>Ставки приняты!</b>\n\n"
        f"{placed_text}\n\n"
        f"💰 Списано: {fmt(total_amount)} TON\n"
        f"⏳ Запуск через {BET_WAIT_SECONDS} сек. (или пишите «го»)",
        parse_mode="HTML"
    )


async def roulette_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.effective_chat.id
    user_id = get_user(update.effective_user)

    if roulette_running.get(chat_id, False):
        await update.message.reply_text("❌ Рулетка уже запущена, отмена невозможна!")
        return

    bets = pending_bets.get(chat_id, [])
    if not bets:
        await update.message.reply_text("❌ У вас нет активных ставок в этом чате.")
        return

    user_bets = [b for b in bets if b["user_id"] == user_id]
    if not user_bets:
        await update.message.reply_text("❌ У вас нет активных ставок в этом чате.")
        return

    refund_sum = sum(b["amount"] for b in user_bets)
    balances[user_id] += refund_sum
    save_balances()

    pending_bets[chat_id] = [b for b in bets if b["user_id"] != user_id]

    if not pending_bets[chat_id]:
        pending_bets.pop(chat_id, None)
        roulette_ready_time.pop(chat_id, None)

    username = get_user_display_name(update.effective_user)
    await update.message.reply_text(
        f"❌ Ставки {username} отменены!\n"
        f"💳 На баланс возвращено: {fmt(refund_sum)} TON 💎\n"
        f"💰 Текущий баланс: {fmt(balances[user_id])} TON 💎"
    )


async def roulette_go(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.effective_chat.id
    user_id = get_user(update.effective_user)

    if roulette_running.get(chat_id, False):
        await update.message.reply_text("🎰 Рулетка уже крутится.")
        return

    bets = pending_bets.get(chat_id, [])
    if not bets:
        await update.message.reply_text("❌ В этом чате нет ставок.")
        return

    has_own_bet = any(b["user_id"] == user_id for b in bets)
    if not has_own_bet:
        await update.message.reply_text("❌ Вы не можете запустить рулетку, пока не сделали свою ставку!")
        return

    ready_time = roulette_ready_time.get(chat_id)
    if ready_time is None:
        await update.message.reply_text("❌ Рулетка ещё не готова.")
        return

    remaining = ready_time - asyncio.get_running_loop().time()
    if remaining > 0:
        await update.message.reply_text(
            f"⏳ Ещё рано!\nДо запуска рулетки осталось {int(remaining) + 1} сек."
        )
        return

    roulette_running[chat_id] = True
    bets = list(pending_bets[chat_id])

    user_completed_bets = {}
    for bet_data in bets:
        uid = bet_data["user_id"]
        user_completed_bets.setdefault(uid, []).append({
            "amount": bet_data["amount"],
            "raw_bet": bet_data["raw_bet"]
        })
    for uid, u_bets in user_completed_bets.items():
        last_user_bets[f"{chat_id}_{uid}"] = u_bets

    animation_msg = None

    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        video_filename = None
        for cand in ["red-1.mp4", "roulette.mp4", "roulette.gif"]:
            full_path = os.path.join(base_dir, cand)
            if os.path.exists(full_path):
                video_filename = full_path
                break

        if video_filename:
            try:
                with open(video_filename, "rb") as anim_file:
                    if video_filename.endswith(".gif"):
                        animation_msg = await context.bot.send_animation(
                            chat_id=chat_id,
                            animation=anim_file,
                            caption="🎡 <b>Рулетка крутится...</b>",
                            parse_mode="HTML"
                        )
                    else:
                        animation_msg = await context.bot.send_video(
                            chat_id=chat_id,
                            video=anim_file,
                            caption="🎡 <b>Рулетка крутится...</b>",
                            parse_mode="HTML"
                        )
            except Exception as e:
                print(f"Ошибка отправки видео: {e}")
                animation_msg = await update.message.reply_text("🎡 <b>Рулетка крутится...</b>", parse_mode="HTML")
        else:
            animation_msg = await update.message.reply_text("🎡 <b>Рулетка крутится...</b>", parse_mode="HTML")

        await asyncio.sleep(SPIN_SECONDS)

        if animation_msg:
            try:
                await animation_msg.delete()
            except Exception:
                pass

        result = random.randint(0, 36)

        if result == 0:
            color = "🟢"
            color_name = "Зелёное"
        elif result in RED:
            color = "🔴"
            color_name = "Красное"
        else:
            color = "⚫️"
            color_name = "Чёрное"

        chat_key = str(chat_id)
        if chat_key not in roulette_log:
            roulette_log[chat_key] = []

        roulette_log[chat_key].append({
            "number": result,
            "color": color_name,
            "timestamp": datetime.now(MSK_TZ).isoformat(timespec="seconds")
        })
        roulette_log[chat_key] = roulette_log[chat_key][-1000:]
        save_log()

        lines = [
            "🎡 <b>Рулетка</b>",
            "———————————————",
            f"🎉 Результат: <b>{result} {color}</b>"
        ]

        for bet_data in bets:
            p_name = bet_data.get("raw_name") or bet_data["username"]
            lines.append(f"{p_name} — {fmt(bet_data['amount'])} на {bet_data['bet_name']}")

        lines.append("———————————————")

        winning_bets_lines = []

        for bet_data in bets:
            b_uid = bet_data["user_id"]
            amount = bet_data["amount"]

            if result in bet_data["numbers"]:
                win = int(round(amount * bet_data["coefficient"]))
                balances[b_uid] = int(round(balances[b_uid])) + win
                p_name = bet_data.get("raw_name") or bet_data["username"]
                winning_bets_lines.append(
                    f"🎉 {p_name} — выиграла ставка <b>{bet_data['bet_name']}</b> (+{fmt(win)} TON)"
                )
                record_game_win(b_uid, win)

        save_balances()

        if winning_bets_lines:
            lines.extend(winning_bets_lines)
        else:
            lines.append("❌ <b>Никто не выиграл!</b>")

        keyboard = [
            [
                InlineKeyboardButton("🔄 Повторить ставку", callback_data="roulette_repeat"),
                InlineKeyboardButton("🔁 Удвоить ставку (x2)", callback_data="roulette_double")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        full_text = "\n".join(lines)
        if len(full_text) > 4000:
            chunks = [full_text[i:i + 3800] for i in range(0, len(full_text), 3800)]
            for chunk in chunks[:-1]:
                await update.message.reply_text(chunk, parse_mode="HTML")
            await update.message.reply_text(chunks[-1], reply_markup=reply_markup, parse_mode="HTML")
        else:
            await update.message.reply_text(full_text, reply_markup=reply_markup, parse_mode="HTML")

    finally:
        pending_bets.pop(chat_id, None)
        roulette_ready_time.pop(chat_id, None)
        roulette_running.pop(chat_id, None)


async def roulette_callback(query, user_id, chat_id, double=False):
    key = f"{chat_id}_{user_id}"
    saved_bets = last_user_bets.get(key)

    if not saved_bets:
        await query.answer("⚠️ Вы не делали ставок в прошлом раунде!", show_alert=True)
        return

    if roulette_running.get(chat_id, False):
        await query.answer("🎰 Рулетка уже крутится! Дождитесь следующего раунда.", show_alert=True)
        return

    bets = pending_bets.setdefault(chat_id, [])

    existing_user_bets = sum(1 for b in bets if b["user_id"] == user_id)
    if existing_user_bets + len(saved_bets) > MAX_BETS_PER_GAME:
        await query.answer(f"❌ Превышен лимит {MAX_BETS_PER_GAME} ставок за раунд!", show_alert=True)
        return

    multiplier = 2 if double else 1
    total_needed = sum(b["amount"] * multiplier for b in saved_bets)

    user_cur_bal = int(round(balances[user_id]))
    balances[user_id] = user_cur_bal

    if user_cur_bal < total_needed:
        await query.answer(f"❌ Недостаточно TON! Требуется: {fmt(total_needed)} TON", show_alert=True)
        return

    if not bets:
        roulette_ready_time[chat_id] = asyncio.get_running_loop().time() + BET_WAIT_SECONDS

    balances[user_id] -= total_needed
    save_balances()

    user = query.from_user
    raw_user_name = user.first_name or f"Игрок {user.id}"
    username_tag = f"@{user.username}" if user.username else raw_user_name

    placed_lines = []
    for s_bet in saved_bets:
        bet_str = s_bet["raw_bet"]
        amount = s_bet["amount"] * multiplier
        parsed = parse_bet(bet_str)
        if parsed:
            numbers, coefficient, bet_name = parsed
            bets.append({
                "user_id": user_id,
                "username": username_tag,
                "raw_name": raw_user_name,
                "amount": amount,
                "bet_name": bet_name,
                "numbers": list(numbers),
                "coefficient": coefficient,
                "raw_bet": bet_str
            })
            placed_lines.append(f"{raw_user_name} — {fmt(amount)} на {bet_name}")

    action_word = "удвоил" if double else "повторил"
    placed_text = "\n".join(placed_lines[:15])
    if len(placed_lines) > 15:
        placed_text += f"\n... и ещё {len(placed_lines) - 15} ставок"

    await query.message.reply_text(
        f"✅ <b>{raw_user_name} {action_word} ставки!</b>\n\n"
        f"{placed_text}\n\n"
        f"💰 Списано: {fmt(total_needed)} TON\n"
        f"⏳ Запуск через {BET_WAIT_SECONDS} секунд.",
        parse_mode="HTML"
    )
    await query.answer("Ставки продублированы!")

# =========================================================
# ОБРАБОТЧИК КЛИКОВ В МИНАХ
# =========================================================

async def mines_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = query.data
    user_id = str(query.from_user.id)

    if data == "mine_ignore":
        await query.answer()
        return

    if user_id not in active_mines_games:
        await query.answer("⚠️ Это не ваша игра или она уже завершена!", show_alert=True)
        return

    game_data = active_mines_games[user_id]

    if data == "mine_cashout":
        opened_count = len(game_data["opened"])
        if opened_count == 0:
            await query.answer("Откройте ячейку!", show_alert=True)
            return

        mult = get_mines_multiplier(opened_count, game_data["mines_count"])
        win_amount = int(round(game_data["bet"] * mult))

        balances[user_id] = int(round(balances[user_id])) + win_amount
        save_balances()

        record_game_win(user_id, win_amount)

        reply_markup = build_mines_keyboard(game_data, game_over=True, won=True)

        await query.edit_message_text(
            f"🎉 <b>ПОБЕДА!</b>\n\n"
            f"💳 Вы забрали: <b>{fmt(win_amount)} TON 💎</b> (x{mult})\n"
            f"💰 Ваш баланс: <b>{fmt(balances[user_id])} TON 💎</b>",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

        del active_mines_games[user_id]
        await query.answer("Выигрыш зачислен!")
        return

    if data.startswith("mine_click_"):
        idx = int(data.split("_")[2])

        if idx in game_data["opened"]:
            await query.answer("Ячейка уже открыта!")
            return

        if idx in game_data["mines"]:
            reply_markup = build_mines_keyboard(game_data, game_over=True, won=False)

            await query.edit_message_text(
                f"💥 <b>БУМ! ВЫ ВЗОРВАЛИСЬ НА МИНЕ!</b>\n\n"
                f"💸 Потеряно: <b>{fmt(game_data['bet'])} TON</b>\n"
                f"💰 Ваш баланс: <b>{fmt(balances[user_id])} TON 💎</b>",
                reply_markup=reply_markup,
                parse_mode="HTML"
            )

            del active_mines_games[user_id]
            await query.answer("Бум! Проигрыш 💥")
            return

        game_data["opened"].add(idx)
        opened_count = len(game_data["opened"])
        safe_total = 25 - game_data["mines_count"]

        if opened_count == safe_total:
            mult = get_mines_multiplier(opened_count, game_data["mines_count"])
            win_amount = int(round(game_data["bet"] * mult))

            balances[user_id] = int(round(balances[user_id])) + win_amount
            save_balances()

            record_game_win(user_id, win_amount)

            reply_markup = build_mines_keyboard(game_data, game_over=True, won=True)

            await query.edit_message_text(
                f"🏆 <b>МЕГА-ПОБЕДА!</b> Вы открыли все безопасные ячейки!\n\n"
                f"🎉 Выигрыш: <b>{fmt(win_amount)} TON 💎</b> (x{mult})\n"
                f"💰 Ваш баланс: <b>{fmt(balances[user_id])} TON 💎</b>",
                reply_markup=reply_markup,
                parse_mode="HTML"
            )

            del active_mines_games[user_id]
            await query.answer("Победа! 🎉")
            return

        mult = get_mines_multiplier(opened_count, game_data["mines_count"])
        win_amount = int(round(game_data["bet"] * mult))

        reply_markup = build_mines_keyboard(game_data)

        await query.edit_message_text(
            f"💣 <b>ИГРА В МИНЫ</b>\n\n"
            f"💰 Ставка: <b>{fmt(game_data['bet'])} TON 💎</b> | Мин: {game_data['mines_count']}\n"
            f"💎 Открыто: {opened_count}/{safe_total}\n"
            f"📈 Текущий выигрыш: <b>{fmt(win_amount)} TON 💎</b> (x{mult})\n\n"
            f"Выберите следующую ячейку или заберите банк!",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        await query.answer("Безопасно! 💎")

# =========================================================
# ГЛАВНЫЙ РОУТЕР ДЛЯ КЛИКОВ (CALLBACK QUERY)
# =========================================================

async def general_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = query.data
    user_id = str(query.from_user.id)
    chat_id = query.message.chat.id

    if data.startswith("treasure_"):
        parts = data.split("_")
        u_id = parts[1]
        box_idx = int(parts[2])
        await treasure_callback_handler(query, u_id, box_idx)
        return

    if data.startswith("donate_"):
        await donate_callback_handler(query, data, context)
        return

    if data.startswith("duel_"):
        await duel_callback_handler(query, data)
        return

    if data == "roulette_repeat":
        await roulette_callback(query, user_id, chat_id, double=False)
        return
    elif data == "roulette_double":
        await roulette_callback(query, user_id, chat_id, double=True)
        return

    if data == "top_balances":
        text = render_top_balances()
        kb = [[InlineKeyboardButton("🏆 Показать топ выигрышей", callback_data="top_wins")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        await query.answer()
        return

    if data == "top_wins":
        text = render_top_wins()
        kb = [[InlineKeyboardButton("💰 Показать топ балансов", callback_data="top_balances")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        await query.answer()
        return

    await mines_callback(update, context)

# =========================================================
# ЛОГ
# =========================================================

async def show_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = str(update.effective_chat.id)
    history = roulette_log.get(chat_id, [])

    if not history:
        await update.message.reply_text("📜 Лог пока пуст.")
        return

    last_results = history[-10:]
    lines = []

    for item in reversed(last_results):
        color_name = item.get("color", "")
        if "Зелёное" in color_name:
            icon = "🟢"
        elif "Красное" in color_name:
            icon = "🔴"
        else:
            icon = "⚫️"
        lines.append(f"{icon} {item['number']}")

    await update.message.reply_text(
        "📜 Последние результаты:\n\n" + "\n".join(lines)
    )

# =========================================================
# ПЕРЕДАЧА МОНЕТ
# =========================================================

async def transfer_coins(update: Update, amount: int):
    if not update.message:
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Используй «дать 100» ответом на сообщение пользователя.")
        return

    if amount <= 0:
        await update.message.reply_text("❌ Сумма должна быть больше 0.")
        return

    sender = update.effective_user
    recipient = update.message.reply_to_message.from_user

    if recipient.is_bot:
        await update.message.reply_text("❌ Нельзя переводить TON ботам.")
        return

    sender_id = get_user(sender)
    recipient_id = get_user(recipient)

    if sender_id == recipient_id:
        await update.message.reply_text("❌ Нельзя передать TON самому себе.")
        return

    if int(round(balances[sender_id])) < amount:
        await update.message.reply_text(
            f"❌ Недостаточно TON.\nТвой баланс: {fmt(balances[sender_id])} TON"
        )
        return

    balances[sender_id] -= amount
    balances[recipient_id] += amount
    save_balances()

    await update.message.reply_text(
        f"💸 Передано: <b>{fmt(amount)} TON 💎</b>\n"
        f"👤 Получатель: {get_user_display_name(recipient)}",
        parse_mode="HTML"
    )


async def give_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if len(context.args) != 1:
        await update.message.reply_text("❌ Пример:\n/дать 100")
        return

    try:
        amount = int(context.args[0].replace(" ", ""))
    except ValueError:
        await update.message.reply_text("❌ Сумма должна быть целым числом.")
        return

    await transfer_coins(update, amount)

# =========================================================
# ОБРАБОТКА ТЕКСТА
# =========================================================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    lower = text.lower()
    user_id = str(update.effective_user.id)

    # Проверка на ввод кастомного количества звёзд для доната
    if waiting_for_custom_stars.get(user_id):
        waiting_for_custom_stars.pop(user_id, None)
        try:
            stars_count = int(text.replace(" ", ""))
            await create_stars_invoice(update.message, context, stars_count)
            return
        except ValueError:
            await update.message.reply_text("❌ Пожалуйста, введите корректное число звёзд (например: 300).")
            return

    # Кнопки клавиатуры в ЛС
    if text == "🎁 Забрать бонус":
        await bonus_command(update, context)
        return
    elif text == "🎮 Игры":
        await show_games_help(update, context)
        return
    elif text == "📜 Команды":
        await show_commands_help(update, context)
        return
    elif text == "⭐️ Донат":
        await show_donate_menu(update, context)
        return

    # Текстовые команды
    if lower in {"баланс", "б"}:
        await show_balance(update, context)
        return

    if lower in {"ставки", "мои ставки", "/ставки"}:
        await show_my_bets(update, context)
        return

    if lower in {"команды", "команда", "/команды"}:
        await show_commands_help(update, context)
        return

    if lower in {"игры", "игра", "/игры"}:
        await show_games_help(update, context)
        return

    if lower in {"донат", "donate", "/донат"}:
        await show_donate_menu(update, context)
        return

    if lower.startswith("клад"):
        await handle_treasure_command(update, context)
        return

    if lower.startswith("дуэль") or lower.startswith("/дуэль"):
        await handle_duel_command(update, context)
        return

    if lower in {"топ", "/топ", "top", "/top"}:
        await show_top_menu(update, context)
        return

    if lower in {"лог", "лог рулетки"}:
        await show_log(update, context)
        return

    if lower == "го":
        await roulette_go(update, context)
        return

    if lower == "отмена":
        await roulette_cancel(update, context)
        return

    if lower == "купить казну":
        await buy_treasury(update, context)
        return

    if lower in {"казна", "/казна"}:
        await show_treasury_status(update, context)
        return

    if lower.startswith("пополнить казну"):
        parts = text.split()
        if len(parts) >= 3:
            raw_sum = parts[2].replace(" ", "")
            try:
                amount = int(raw_sum)
                await deposit_treasury(update, amount)
                return
            except ValueError:
                await update.message.reply_text("❌ Пример использования: <code>Пополнить казну 5000</code>", parse_mode="HTML")
                return
        else:
            await update.message.reply_text("❌ Укажите сумму: <code>Пополнить казну 5000</code>", parse_mode="HTML")
            return

    # МИНЫ
    if lower.startswith("мины"):
        parts = lower.split()
        if len(parts) < 2:
            await update.message.reply_text("❌ Пример: `мины 100` или `мины 100 5`", parse_mode="Markdown")
            return

        try:
            bet = int(parts[1].replace(" ", ""))
        except ValueError:
            await update.message.reply_text("❌ Ставка должна быть числом.")
            return

        mines_count = 3
        if len(parts) >= 3:
            try:
                mines_count = int(parts[2])
                if not (1 <= mines_count <= 24):
                    await update.message.reply_text("❌ Количество мин должно быть от 1 до 24.")
                    return
            except ValueError:
                pass

        user_id_obj = get_user(update.effective_user)

        if user_id_obj in active_mines_games:
            await update.message.reply_text("⚠️ У вас уже есть активная игра в Мины!")
            return

        if bet <= 0:
            await update.message.reply_text("❌ Ставка должна быть больше 0.")
            return

        if int(round(balances[user_id_obj])) < bet:
            await update.message.reply_text(
                f"❌ Недостаточно монет на балансе.\nТвой баланс: {fmt(balances[user_id_obj])} TON"
            )
            return

        balances[user_id_obj] -= bet
        save_balances()

        mine_positions = set(random.sample(range(25), mines_count))

        game_data = {
            "bet": bet,
            "mines_count": mines_count,
            "mines": mine_positions,
            "opened": set(),
            "chat_id": update.effective_chat.id,
            "user_id": user_id_obj
        }

        active_mines_games[user_id_obj] = game_data
        reply_markup = build_mines_keyboard(game_data)

        await update.message.reply_text(
            f"💣 <b>ИГРА В МИНЫ</b>\n\n"
            f"💰 Ставка: <b>{fmt(bet)} TON 💎</b> | Мин: {game_data['mines_count']}\n"
            f"💎 Выберите ячейку:",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        return

    # Передача монет
    parts = lower.split()
    if len(parts) == 2 and parts[0] == "дать":
        try:
            amount = int(parts[1].replace(" ", ""))
            await transfer_coins(update, amount)
            return
        except ValueError:
            await update.message.reply_text("❌ Сумма должна быть числом.")
            return

    # Ставки рулетки
    if len(parts) >= 2:
        try:
            amount = int(parts[0].replace(" ", ""))
            valid_bets = []
            for bet_str in parts[1:]:
                parsed = parse_bet(bet_str)
                if parsed is not None:
                    valid_bets.append((bet_str, parsed))

            if valid_bets:
                await roulette_batch_bets(update, amount, valid_bets)
                return
        except ValueError:
            pass

# =========================================================
# БОНУС
# =========================================================

async def bonus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if update.effective_chat.type != "private":
        await update.message.reply_text(
            "🎁 Бонус можно забрать только в личных сообщениях с ботом!\n"
            "Напиши мне в ЛС: /start"
        )
        return

    user_id = get_user(update.effective_user)

    now = time.time()
    last_bonus = bonus_times.get(user_id, 0)

    cooldown = 24 * 60 * 60
    remaining = cooldown - (now - last_bonus)

    if remaining > 0:
        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)
        await update.message.reply_text(
            f"⏳ Бонус уже получен.\n"
            f"Следующий бонус через: {hours} ч. {minutes} мин."
        )
        return

    bonus = 2000
    balances[user_id] += bonus
    bonus_times[user_id] = now
    save_balances()
    save_bonus_times()

    await update.message.reply_text(
        f"🎁 Бонус начислен: <b>+{fmt(bonus)} TON 💎</b>\n"
        f"💰 Твой баланс: <b>{fmt(balances[user_id])} TON 💎</b>",
        parse_mode="HTML"
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"❌ Ошибка в обработчике: {context.error!r}")


async def post_init(application: Application):
    asyncio.create_task(daily_reset_task())


def main():
    if TOKEN == "TOKEN":
        print("❌ Сначала вставь токен бота в переменную TOKEN.")
        return

   
    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, promo_handler),
    group=1
)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(general_callback_handler))

    # Обработчики Telegram Stars (Донат)
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_error_handler(error_handler)

    print("✅ Бот запущен со всеми новыми играми и донатом за Stars...")
    application.run_polling()


if __name__ == "__main__":
    main()






