from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import SessionLocal, engine
from models import Base, Booking, MasterSettings, Notification, Service, User

KYIV = ZoneInfo("Europe/Kyiv")
MASTER_ID = int(os.getenv("MASTER_TELEGRAM_ID", "0"))
SERVICE_SEED = [
    ("Манікюр", "Гігієнічний манікюр", "Гігієнічний манікюр (без покриття)", 60),
    ("Манікюр", "Покриття", "Манікюр з покриттям гель-лак (однотонний)", 120),
    ("Манікюр", "Дизайн", "Манікюр з покриттям + дизайн (френч, слайдери, розпис)", 150),
    ("Манікюр", "Укріплення", "Укріплення гелем + покриття", 150),
    ("Манікюр", "Нарощування", "Нарощування нігтів (1-2 довжина) + покриття", 180),
    ("Манікюр", "Корекція", "Корекція нарощування + покриття", 180),
    ("Педикюр", "Гігієнічний догляд", "Гігієнічний педикюр (без покриття)", 60),
    ("Педикюр", "Покриття", "Педикюр з покриттям гель-лак", 90),
    ("Педикюр", "Комплекс", "Комплекс «Педикюр + покриття»", 120),
    ("Педикюр", "Апаратний догляд", "Апаратний педикюр з повною обробкою стопи", 90),
    ("Комплекс", "Повний комплекс", "Манікюр + педикюр (повний комплекс)", 240),
]
_sqlite_lock = asyncio.Lock()


async def initialize_database() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        if not (await session.scalars(select(Service.id).limit(1))).first():
            session.add_all(
                Service(category=category, subcategory=subcategory, name=name, duration_minutes=minutes)
                for category, subcategory, name, minutes in SERVICE_SEED
            )
        if not (await session.scalars(select(MasterSettings.id).limit(1))).first():
            session.add(MasterSettings())
        await session.commit()


async def upsert_user(session: AsyncSession, data: dict) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == int(data["id"])))
    if user is None:
        user = User(telegram_id=int(data["id"]), first_name=data.get("first_name") or "Гостя",
                    username=data.get("username"))
        session.add(user)
    else:
        user.first_name = data.get("first_name") or user.first_name
        user.username = data.get("username")
    await session.flush()
    return user


def _local_datetime(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(KYIV).replace(tzinfo=None)
    return value


async def get_service(session: AsyncSession, service_id: int) -> Service:
    service = await session.scalar(select(Service).where(Service.id == service_id, Service.active.is_(True)))
    if service is None:
        raise HTTPException(status_code=404, detail="Послугу не знайдено")
    return service


async def available_slots(session: AsyncSession, service_id: int, day: date) -> list[str]:
    service = await get_service(session, service_id)
    settings = await session.scalar(select(MasterSettings).limit(1))
    if settings is None or day < datetime.now(KYIV).date() or day.weekday() not in settings.working_days:
        return []
    start_hour, start_min = map(int, settings.working_hours_start.split(":"))
    end_hour, end_min = map(int, settings.working_hours_end.split(":"))
    opening = datetime.combine(day, time(start_hour, start_min))
    closing = datetime.combine(day, time(end_hour, end_min))
    if day == datetime.now(KYIV).date():
        now = datetime.now(KYIV).replace(tzinfo=None)
        next_slot = now.replace(second=0, microsecond=0)
        next_slot += timedelta(minutes=30 - next_slot.minute % 30)
        opening = max(opening, next_slot)
    rows = (await session.scalars(select(Booking).where(
        Booking.status.in_(("pending", "confirmed")),
        Booking.start_time < closing,
        Booking.end_time > datetime.combine(day, time.min),
    ))).all()
    occupied = [(row.start_time, row.end_time) for row in rows]
    duration = timedelta(minutes=service.duration_minutes)
    slots: list[str] = []
    slot = opening
    while slot + duration <= closing:
        if all(slot + duration <= busy_start or slot >= busy_end for busy_start, busy_end in occupied):
            slots.append(slot.strftime("%H:%M"))
        slot += timedelta(minutes=30)
    return slots


async def _book(session: AsyncSession, user_data: dict, service_id: int, requested: datetime) -> Booking:
    local_start = _local_datetime(requested)
    now = datetime.now(KYIV).replace(tzinfo=None)
    if local_start <= now:
        raise HTTPException(status_code=400, detail="Не можна обрати час у минулому")
    if local_start.minute not in (0, 30) or local_start.second or local_start.microsecond:
        raise HTTPException(status_code=400, detail="Оберіть час із доступних слотів")
    service = await get_service(session, service_id)
    slots = await available_slots(session, service_id, local_start.date())
    if local_start.strftime("%H:%M") not in slots:
        raise HTTPException(status_code=409, detail="Цей час уже недоступний. Оберіть інший слот")
    user = await upsert_user(session, user_data)
    from sqlalchemy import func
    local_day_start = datetime.combine(now.date(), time.min).replace(tzinfo=KYIV)
    utc_day_start = local_day_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    utc_day_end = (local_day_start + timedelta(days=1)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    count_today = await session.scalar(select(func.count(Booking.id)).where(
        Booking.user_id == user.id,
        Booking.created_at >= utc_day_start,
        Booking.created_at < utc_day_end,
    ))
    if count_today >= 5:
        raise HTTPException(status_code=429, detail="Доступно не більше 5 записів на день")
    end = local_start + timedelta(minutes=service.duration_minutes)
    status_value = "pending" if local_start - now < timedelta(hours=24) else "confirmed"
    booking = Booking(user_id=user.id, service_id=service.id, master_id=MASTER_ID,
                      start_time=local_start, end_time=end, status=status_value)
    session.add(booking)
    await session.flush()
    return booking


async def create_booking(user_data: dict, service_id: int, start_time: datetime) -> Booking:
    is_sqlite = engine.url.get_backend_name() == "sqlite"
    if is_sqlite:
        async with _sqlite_lock:
            async with engine.connect() as connection:
                await connection.exec_driver_sql("BEGIN IMMEDIATE")
                session = AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="control_fully",
                )
                try:
                    booking = await _book(session, user_data, service_id, start_time)
                    await session.commit()
                    return booking
                except Exception:
                    await session.rollback()
                    raise
                finally:
                    await session.close()
    async with SessionLocal() as session:
        async with session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": MASTER_ID})
            booking = await _book(session, user_data, service_id, start_time)
            session.expunge(booking)
            return booking


async def list_bookings(session: AsyncSession, telegram_id: int) -> list[Booking]:
    return list((await session.scalars(
        select(Booking).join(User).options(selectinload(Booking.service))
        .where(User.telegram_id == telegram_id)
        .order_by(Booking.start_time.desc())
    )).all())


async def get_booking(session: AsyncSession, booking_id: int) -> Booking:
    booking = await session.scalar(
        select(Booking).options(selectinload(Booking.service), selectinload(Booking.user))
        .where(Booking.id == booking_id)
    )
    if booking is None:
        raise HTTPException(status_code=404, detail="Запис не знайдено")
    return booking


async def cancel_booking(session: AsyncSession, booking_id: int, telegram_id: int) -> Booking:
    booking = await get_booking(session, booking_id)
    if booking.user.telegram_id != telegram_id:
        raise HTTPException(status_code=404, detail="Запис не знайдено")
    if booking.status not in ("pending", "confirmed") or booking.start_time <= datetime.now(KYIV).replace(tzinfo=None):
        raise HTTPException(status_code=409, detail="Цей запис уже неможливо скасувати")
    booking.status = "cancelled"
    await session.commit()
    return booking


async def reschedule_booking(session: AsyncSession, booking_id: int, telegram_id: int,
                             new_start: datetime) -> Booking:
    booking = await get_booking(session, booking_id)
    if booking.user.telegram_id != telegram_id or booking.status not in ("pending", "confirmed"):
        raise HTTPException(status_code=404, detail="Запис не знайдено")
    previous_start = booking.start_time
    booking.status = "cancelled"
    await session.flush()
    try:
        replacement = await _book(
            session,
            {"id": telegram_id, "first_name": booking.user.first_name, "username": booking.user.username},
            booking.service_id,
            new_start,
        )
    except Exception:
        booking.status = "confirmed" if booking.confirmed_at else "pending"
        booking.start_time = previous_start
        booking.end_time = previous_start + timedelta(minutes=booking.service.duration_minutes)
        raise
    await session.commit()
    return replacement


async def mark_reminder(session: AsyncSession, booking_id: int, kind: str) -> bool:
    existing = await session.scalar(select(Notification).where(
        Notification.booking_id == booking_id, Notification.type == kind))
    if existing is not None:
        return False
    session.add(Notification(booking_id=booking_id, type=kind, sent_at=datetime.utcnow()))
    await session.commit()
    return True
