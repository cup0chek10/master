from __future__ import annotations

import html
import os
import time
from datetime import datetime

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..crud import get_booking, list_bookings

router = Router(name="atelier")


class SupportStates(StatesGroup):
    waiting_for_question = State()


class MessageThrottleMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self.last_message: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        sender = getattr(event, "from_user", None)
        if sender is not None:
            now = time.monotonic()
            previous = self.last_message.get(sender.id, 0)
            self.last_message[sender.id] = now
            if now - previous < 1:
                return None
        return await handler(event, data)


def _app_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Відкрити додаток", web_app=WebAppInfo(url=os.getenv("MINIAPP_URL", "https://example.com")))
    ]])


def _home_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Записатися"), KeyboardButton(text="Мої записи")],
                  [KeyboardButton(text="Допомога")]],
        resize_keyboard=True,
    )


def _master_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Підтвердити", callback_data=f"booking:confirm:{booking_id}"),
        InlineKeyboardButton(text="Відхилити", callback_data=f"booking:reject:{booking_id}"),
    ]])


def _help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Написати майстру", callback_data="help:contact")
    ]])


def register_handlers(session_factory: async_sessionmaker[AsyncSession]) -> Router:
    router.message.middleware(MessageThrottleMiddleware())

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        name = html.escape(message.from_user.first_name if message.from_user else "Вітаємо")
        await message.answer(
            f"Вітаємо, {name}! 🌿\nЗапишіться на догляд за нігтями у зручний час.",
            reply_markup=_home_keyboard(),
        )
        await message.answer("Ваш запис — кілька простих кроків:", reply_markup=_app_keyboard())

    @router.message(Command("mybookings"))
    @router.message(F.text == "Мої записи")
    async def my_bookings(message: Message) -> None:
        if not message.from_user:
            return
        async with session_factory() as session:
            rows = await list_bookings(session, message.from_user.id)
        if not rows:
            await message.answer("У вас ще немає записів. Запишіться на перший візит! ✨", reply_markup=_app_keyboard())
            return
        lines = ["<b>Ваші записи</b>"]
        for booking in rows[:10]:
            when = booking.start_time.strftime("%d.%m.%Y о %H:%M")
            state = {
                "pending": "Очікує підтвердження",
                "confirmed": "Підтверджено",
                "cancelled": "Скасовано",
                "completed": "Завершено",
            }.get(booking.status, "Статус невідомий")
            lines.append(
                f"\n• {html.escape(booking.service.name)}\n  {when} · "
                f"{state}"
            )
        await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=_app_keyboard())

    @router.message(Command("help"))
    @router.message(F.text == "Допомога")
    async def help_command(message: Message) -> None:
        await message.answer(
            "Щоб записатися, натисніть «Відкрити додаток» і оберіть послугу та час.\n"
            "Команди: /mybookings — ваші записи, /cancel — скасування дії.",
            reply_markup=_help_keyboard(),
        )

    @router.callback_query(F.data == "help:contact")
    async def start_support_request(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(SupportStates.waiting_for_question)
        await callback.answer()
        if callback.message:
            await callback.message.answer("Напишіть ваше запитання — ми передамо його майстру. /cancel скасує дію.")

    @router.message(Command("cancel"))
    async def cancel_command(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Поточну дію скасовано. Ви можете почати запис у будь-який час.", reply_markup=_home_keyboard())

    @router.message(F.text == "Записатися")
    async def booking_command(message: Message) -> None:
        await message.answer("Оберіть послугу та зручний час у додатку:", reply_markup=_app_keyboard())

    @router.message(SupportStates.waiting_for_question)
    async def forward_support_request(message: Message, state: FSMContext, bot: Bot) -> None:
        master_id = int(os.getenv("MASTER_TELEGRAM_ID", "0"))
        await state.clear()
        if not master_id or not message.from_user:
            await message.answer("Зв’язок із майстром тимчасово недоступний. Спробуйте пізніше.")
            return
        question = html.escape(message.text or message.caption or "Надіслано вкладення")
        name = html.escape(message.from_user.full_name)
        username = f" (@{html.escape(message.from_user.username)})" if message.from_user.username else ""
        await bot.send_message(master_id, f"💌 <b>Запитання від клієнта</b>\n{name}{username}\n{question}", parse_mode="HTML")
        await message.answer("Дякуємо! Ваше повідомлення передано майстру 💜")

    @router.callback_query(F.data.startswith("booking:"))
    async def master_decision(callback: CallbackQuery, bot: Bot) -> None:
        master_id = int(os.getenv("MASTER_TELEGRAM_ID", "0"))
        if callback.from_user.id != master_id:
            await callback.answer("Недостатньо прав", show_alert=True)
            return
        try:
            _, decision, booking_id_text = (callback.data or "").split(":")
            booking_id = int(booking_id_text)
        except ValueError:
            await callback.answer("Некоректна дія", show_alert=True)
            return
        if decision not in ("confirm", "reject"):
            await callback.answer("Некоректна дія", show_alert=True)
            return
        async with session_factory() as session:
            booking = await get_booking(session, booking_id)
            if booking.status != "pending":
                await callback.answer("Цей запис уже опрацьовано", show_alert=True)
                return
            booking.status = "confirmed" if decision == "confirm" else "cancelled"
            booking.confirmed_at = datetime.utcnow() if decision == "confirm" else None
            await session.commit()
            client_id = booking.user.telegram_id
            service_name = html.escape(booking.service.name)
            when = booking.start_time.strftime("%d.%m.%Y о %H:%M")
        if decision == "confirm":
            await bot.send_message(client_id, f"✅ <b>Запис підтверджено</b>\n{service_name}\n{when}", parse_mode="HTML")
            await callback.answer("Запис підтверджено")
        else:
            await bot.send_message(client_id, f"На жаль, майстер не може прийняти запис на {when}. Оберіть інший час у додатку.")
            await callback.answer("Запис відхилено")
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)

    return router


def master_booking_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    return _master_keyboard(booking_id)
