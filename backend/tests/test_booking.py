import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import crud
from models import Base, Service


class BookingConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.database_file = Path(tempfile.gettempdir()) / f"atelier-test-{os.getpid()}.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.database_file.as_posix()}")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        crud.engine = self.engine
        crud.SessionLocal = self.session_factory
        crud.MASTER_ID = 987654321
        crud._sqlite_lock = asyncio.Lock()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.session_factory() as session:
            session.add_all(
                Service(category=category, subcategory=subcategory, name=name, duration_minutes=minutes)
                for category, subcategory, name, minutes in crud.SERVICE_SEED
            )
            from models import MasterSettings

            session.add(MasterSettings())
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self.database_file.unlink(missing_ok=True)

    async def test_parallel_booking_and_duration_availability(self) -> None:
        async with self.session_factory() as session:
            services = list((await session.scalars(select(Service).order_by(Service.id))).all())
        day = datetime.now(ZoneInfo("Europe/Kyiv")).date() + timedelta(days=3)
        while day.weekday() == 6:
            day += timedelta(days=1)
        same_slot = datetime.combine(day, datetime.min.time()).replace(hour=10)

        results = await asyncio.gather(
            crud.create_booking({"id": 501, "first_name": "Клієнтка"}, services[0].id, same_slot),
            crud.create_booking({"id": 502, "first_name": "Клієнтка"}, services[0].id, same_slot),
            return_exceptions=True,
        )
        self.assertEqual(sum(not isinstance(result, Exception) for result in results), 1)
        conflict = next(result for result in results if isinstance(result, Exception))
        self.assertIsInstance(conflict, HTTPException)
        self.assertEqual(conflict.status_code, 409)

        await crud.create_booking(
            {"id": 503, "first_name": "Клієнтка"},
            services[1].id,
            same_slot.replace(hour=14),
        )
        async with self.session_factory() as session:
            slots = await crud.available_slots(session, services[0].id, day)
        self.assertNotIn("14:00", slots)
        self.assertNotIn("15:30", slots)
        self.assertIn("16:00", slots)

        another_day = day + timedelta(days=1)
        while another_day.weekday() == 6:
            another_day += timedelta(days=1)
        for hour in range(10, 15):
            await crud.create_booking(
                {"id": 504, "first_name": "Клієнтка"},
                services[0].id,
                datetime.combine(another_day, datetime.min.time()).replace(hour=hour),
            )
        with self.assertRaises(HTTPException) as error:
            await crud.create_booking(
                {"id": 504, "first_name": "Клієнтка"},
                services[0].id,
                datetime.combine(another_day, datetime.min.time()).replace(hour=15),
            )
        self.assertEqual(error.exception.status_code, 429)


class ApiRateLimitTests(unittest.TestCase):
    def test_limit_rejects_eleventh_request_in_one_minute(self) -> None:
        import main

        user_id = 987654321
        main.request_times.pop(user_id, None)
        for _ in range(10):
            main._limit(user_id)
        with self.assertRaises(HTTPException) as error:
            main._limit(user_id)
        self.assertEqual(error.exception.status_code, 429)
        main.request_times.pop(user_id, None)


if __name__ == "__main__":
    unittest.main()
