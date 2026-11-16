from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import html
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.utils.web_app import safe_parse_webapp_init_data
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from bot.handlers import master_booking_keyboard, register_handlers
from crud import KYIV, MASTER_ID, available_slots, cancel_booking, create_booking, get_booking, initialize_database, list_bookings, mark_reminder, reschedule_booking, upsert_user
from database import SessionLocal, engine, get_session
from models import Booking, Service
from schemas import BookingCreate, MasterDecision, NotificationPreference, RescheduleRequest, UserData

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("atelier")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DEV_AUTH = os.getenv("DEV_AUTH", "false").lower() == "true"
BOT_POLLING_ENABLED = os.getenv("BOT_POLLING_ENABLED", "true").lower() == "true"
bot: Bot | None = None
dispatcher: Dispatcher | None = None
scheduler: AsyncIOScheduler | None = None
polling_task: asyncio.Task | None = None
request_times: dict[int, deque[float]] = defaultdict(deque)


def _client_origin() -> list[str]:
    configured = os.getenv("MINIAPP_URL", "")
    origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    if configured:
        parsed = urlparse(configured)
        if parsed.scheme and parsed.netloc:
            origins.append(f"{parsed.scheme}://{parsed.netloc}")
    return origins


def _limit(user_id: int) -> None:
    now = time.monotonic()
    requests = request_times[user_id]
    while requests and requests[0] <= now - 60:
        requests.popleft()
    if len(requests) >= 10:
        raise HTTPException(status_code=429, detail="Забагато запитів. Спробуйте за хвилину")
    requests.append(now)


async def authenticated_user(x_telegram_init_data: str | None = Header(default=None)) -> dict:
    if DEV_AUTH and not x_telegram_init_data:
        data = {"id": 100000001, "first_name": "Тестова клієнтка", "username": "demo"}
    else:
        if not BOT_TOKEN or not x_telegram_init_data:
            raise HTTPException(status_code=401, detail="Потрібна авторизація Telegram")
        try:
            parsed = safe_parse_webapp_init_data(BOT_TOKEN, x_telegram_init_data)
            now = datetime.now(ZoneInfo("UTC"))
            if (parsed.auth_date is None or parsed.auth_date < now - timedelta(hours=24)
                    or parsed.auth_date > now + timedelta(seconds=30)):
                raise ValueError("Expired Telegram initData")
            if parsed.user is None:
                raise ValueError("Missing Telegram user")
            data = parsed.user.model_dump()
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(status_code=401, detail="Не вдалося перевірити дані Telegram") from exc
    try:
        user = UserData.model_validate(data).model_dump()
    except ValidationError as exc:
        raise HTTPException(status_code=401, detail="Некоректні дані профілю Telegram") from exc
    _limit(user["id"])
    return user


async def master_user(user: dict = Depends(authenticated_user)) -> dict:
    if not MASTER_ID or user["id"] != MASTER_ID:
        raise HTTPException(status_code=403, detail="Доступ лише для майстра")
    return user


def _booking_json(booking: Booking) -> dict:
    return {
        "id": booking.id,
        "service_id": booking.service_id,
        "service": booking.service.name,
        "category": booking.service.category,
        "duration_minutes": booking.service.duration_minutes,
        "start_time": booking.start_time.replace(tzinfo=KYIV).isoformat(),
        "end_time": booking.end_time.replace(tzinfo=KYIV).isoformat(),
        "status": booking.status,
    }


async def _notify_booking(booking_id: int) -> None:
    if bot is None:
        return
    async with SessionLocal() as session:
        booking = await get_booking(session, booking_id)
        client_id = booking.user.telegram_id
        master_id = MASTER_ID
        name = html.escape(booking.service.name)
        when = booking.start_time.strftime("%d.%m.%Y о %H:%M")
        status_text = "Очікує підтвердження майстра" if booking.status == "pending" else "Підтверджено"
        details = f"<b>Новий запис</b>\n{name}\n{when}\n{status_text}"
        keyboard = master_booking_keyboard(booking.id) if booking.status == "pending" else None
    try:
        await bot.send_message(client_id, f"✅ <b>Запис створено</b>\n{name}\n{when}\n{status_text}", parse_mode="HTML")
    except Exception:
        logger.exception("Could not notify client about booking_id=%s", booking_id)
    if master_id:
        try:
            await bot.send_message(master_id, details, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            logger.exception("Could not notify master about booking_id=%s", booking_id)


async def _reminder_job() -> None:
    if bot is None:
        return
    now = datetime.now(KYIV).replace(tzinfo=None)
    async with SessionLocal() as session:
        rows = (await session.scalars(
            select(Booking).options(selectinload(Booking.service), selectinload(Booking.user))
            .where(Booking.status == "confirmed", Booking.start_time > now,
                   Booking.start_time <= now + timedelta(hours=24))
        )).all()
        for booking in rows:
            remaining = booking.start_time - now
            if remaining <= timedelta(hours=1):
                kind = "reminder_1h"
            elif timedelta(hours=23) < remaining <= timedelta(hours=24):
                kind = "reminder_24h"
            else:
                continue
            if booking.user.notifications_enabled:
                label = "за годину" if kind == "reminder_1h" else "приблизно за добу"
                try:
                    await bot.send_message(
                        booking.user.telegram_id,
                        f"⏰ <b>Нагадуємо про візит {label}</b>\n"
                        f"{html.escape(booking.service.name)}\n"
                        f"{booking.start_time.strftime('%d.%m.%Y о %H:%M')}",
                        parse_mode="HTML",
                    )
                    await mark_reminder(session, booking.id, kind)
                except Exception:
                    logger.exception("Could not send reminder for booking_id=%s", booking.id)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global bot, dispatcher, scheduler, polling_task
    await initialize_database()
    if BOT_TOKEN and BOT_POLLING_ENABLED:
        bot = Bot(BOT_TOKEN)
        dispatcher = Dispatcher()
        dispatcher.include_router(register_handlers(SessionLocal))
        polling_task = asyncio.create_task(dispatcher.start_polling(bot, handle_signals=False))
        scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")
        scheduler.add_job(_reminder_job, "interval", minutes=1, id="booking-reminders", coalesce=True)
        scheduler.start()
    elif BOT_TOKEN and not BOT_POLLING_ENABLED:
        logger.info("Bot polling is disabled by BOT_POLLING_ENABLED")
    elif not DEV_AUTH:
        logger.warning("BOT_TOKEN is not set; API is running without bot polling or reminders")
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    if polling_task:
        polling_task.cancel()
        await asyncio.gather(polling_task, return_exceptions=True)
        polling_task = None
    if bot:
        await bot.session.close()
    await engine.dispose()


app = FastAPI(title="Atelier Booking API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_client_origin(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "X-Telegram-Init-Data"],
)


# ============================================================
#  Health-check (для пинга и отладки)
# ============================================================
@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ============================================================
#  API-эндпоинты (все под /api/...)
# ============================================================
@app.get("/api/services")
async def services(session=Depends(get_session), _: dict = Depends(authenticated_user)) -> list[dict]:
    rows = (await session.scalars(select(Service).where(Service.active.is_(True)).order_by(Service.id))).all()
    grouped: dict[str, dict[str, list[dict]]] = {}
    for service in rows:
        grouped.setdefault(service.category, {}).setdefault(service.subcategory, []).append({
            "id": service.id, "name": service.name, "duration_minutes": service.duration_minutes,
        })
    return [{"name": category, "subcategories": [
        {"name": subcategory, "services": items} for subcategory, items in groups.items()
    ]} for category, groups in grouped.items()]


@app.get("/api/slots")
async def slots(service_id: int, date: str, session=Depends(get_session),
                _: dict = Depends(authenticated_user)) -> dict:
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Дата має бути у форматі YYYY-MM-DD") from exc
    return {"date": date, "slots": await available_slots(session, service_id, day)}


@app.post("/api/bookings")
async def create_booking_route(body: BookingCreate, user: dict = Depends(authenticated_user)) -> dict:
    booking = await create_booking(user, body.service_id, body.start_time)
    if bot is not None:
        await _notify_booking(booking.id)
    async with SessionLocal() as session:
        booking = await get_booking(session, booking.id)
        return _booking_json(booking)


@app.get("/api/bookings/my")
async def my_bookings(user: dict = Depends(authenticated_user), session=Depends(get_session)) -> list[dict]:
    rows = await list_bookings(session, user["id"])
    return [_booking_json(row) for row in rows]


@app.patch("/api/bookings/{booking_id}/cancel")
async def cancel_route(booking_id: int, user: dict = Depends(authenticated_user),
                       session=Depends(get_session)) -> dict:
    booking = await cancel_booking(session, booking_id, user["id"])
    if bot is not None:
        try:
            if MASTER_ID:
                await bot.send_message(MASTER_ID, f"Запис №{booking.id} скасовано клієнтом.")
        except Exception:
            logger.exception("Could not notify master about cancellation booking_id=%s", booking.id)
        try:
            await bot.send_message(user["id"], f"Запис №{booking.id} скасовано. Будемо раді бачити вас знову!")
        except Exception:
            logger.exception("Could not notify client about cancellation booking_id=%s", booking.id)
    return {"id": booking.id, "status": booking.status}


@app.post("/api/bookings/{booking_id}/reschedule")
async def reschedule_route(booking_id: int, body: RescheduleRequest,
                           user: dict = Depends(authenticated_user), session=Depends(get_session)) -> dict:
    booking = await reschedule_booking(session, booking_id, user["id"], body.start_time)
    async with SessionLocal() as read_session:
        booking = await get_booking(read_session, booking.id)
        if bot is not None:
            try:
                await bot.send_message(user["id"], f"🔄 Запис перенесено:\n{html.escape(booking.service.name)}\n"
                                              f"{booking.start_time.strftime('%d.%m.%Y о %H:%M')}")
            except Exception:
                logger.exception("Could not notify client about reschedule booking_id=%s", booking.id)
            if MASTER_ID:
                try:
                    await bot.send_message(MASTER_ID, f"Клієнт переніс запис №{booking.id} на "
                                                  f"{booking.start_time.strftime('%d.%m.%Y о %H:%M')}.")
                except Exception:
                    logger.exception("Could not notify master about reschedule booking_id=%s", booking.id)
        return _booking_json(booking)


@app.patch("/api/profile/notifications")
async def notification_preferences(body: NotificationPreference, user: dict = Depends(authenticated_user),
                                   session=Depends(get_session)) -> dict:
    profile = await upsert_user(session, user)
    profile.notifications_enabled = body.enabled
    await session.commit()
    return {"enabled": profile.notifications_enabled}


@app.get("/api/profile")
async def profile(user: dict = Depends(authenticated_user), session=Depends(get_session)) -> dict:
    profile_user = await upsert_user(session, user)
    await session.commit()
    return {
        "first_name": profile_user.first_name,
        "username": profile_user.username,
        "notifications_enabled": profile_user.notifications_enabled,
    }


@app.get("/api/master/bookings")
async def master_bookings(_: dict = Depends(master_user), session=Depends(get_session)) -> list[dict]:
    rows = (await session.scalars(select(Booking).options(selectinload(Booking.service), selectinload(Booking.user))
                                  .order_by(Booking.start_time.desc()))).all()
    return [{**_booking_json(row), "client_name": row.user.first_name, "telegram_id": row.user.telegram_id}
            for row in rows]


@app.post("/api/master/confirm")
async def master_confirm(body: MasterDecision, master: dict = Depends(master_user),
                         session=Depends(get_session)) -> dict:
    booking = await get_booking(session, body.booking_id)
    if booking.status != "pending":
        raise HTTPException(status_code=409, detail="Цей запис уже опрацьовано")
    booking.status = "confirmed" if body.decision == "confirm" else "cancelled"
    booking.confirmed_at = datetime.utcnow() if body.decision == "confirm" else None
    await session.commit()
    if bot is not None:
        text = "✅ Ваш запис підтверджено майстром." if body.decision == "confirm" else "На жаль, майстер не може прийняти цей запис."
        try:
            await bot.send_message(booking.user.telegram_id, text)
        except Exception:
            logger.exception("Could not notify client about master decision booking_id=%s", booking.id)
    return {"id": booking.id, "status": booking.status}


# ============================================================
#  Раздача Mini App (статика из frontend/dist → backend/static)
#  ВАЖНО: этот блок должен быть ПОСЛЕДНИМ в файле!
# ============================================================
STATIC_DIR = Path(__file__).parent / "static"

if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        if full_path.startswith(("api/", "health", "docs", "openapi", "redoc")):
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(STATIC_DIR / "index.html")
else:
    logger.warning("Frontend build not found at %s — Mini App will not be served", STATIC_DIR)

    @app.get("/")
    async def root_fallback() -> dict:
        return {
            "service": "Atelier Booking API",
            "status": "ok",
            "note": "Frontend not built. Place build output in backend/static/",
        }