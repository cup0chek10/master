# Atelier — запис до майстра манікюру

Telegram Mini App для запису до майстра: бот і FastAPI працюють з однією SQLite або PostgreSQL базою, а React-застосунок викликає захищений API з перевіреним Telegram `initData`.

## Структура

```text
master/
├── backend/
│   ├── bot/
│   │   ├── __init__.py
│   │   └── handlers.py
│   ├── crud.py
│   ├── database.py
│   ├── main.py
│   ├── models.py
│   ├── requirements.txt
│   ├── schemas.py
│   └── tests/
│       └── test_booking.py
├── frontend/
│   ├── .env.example
│   ├── src/App.tsx
│   ├── src/api.ts
│   ├── src/main.tsx
│   ├── src/styles.css
│   ├── package.json
│   ├── tailwind.config.js
│   ├── vite.config.ts
│   └── package-lock.json
├── .gitignore
├── .env.example
└── render.yaml
```

## Локальний запуск

Потрібні Python 3.11+, Node.js 20.19+ (або 22.12+) та npm.

1. Скопіюйте `.env.example` у `.env`, заповніть `BOT_TOKEN`, `MASTER_TELEGRAM_ID` та `MINIAPP_URL`. Для локальної розробки `MINIAPP_URL` має бути HTTPS-адресою, доступною Telegram (наприклад, через тунель). Скопіюйте `frontend/.env.example` у `frontend/.env` та задайте `VITE_MASTER_TELEGRAM_ID` для показу майстру посилання на панель.
2. У першому терміналі:

   ```powershell
   cd backend
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```

3. У другому терміналі:

   ```powershell
   cd frontend
   npm install
   npm run dev -- --host 0.0.0.0
   ```

4. Налаштуйте BotFather: додайте HTTPS URL Mini App та команду меню. Для тесту API без Telegram доступний лише режим `DEV_AUTH=true`; він призначений виключно для локального запуску.

SQLite-файл `backend/atelier.db` створюється автоматично. Для PostgreSQL задайте `DATABASE_URL=postgresql+asyncpg://...`.

Запустити перевірки backend можна з папки `backend`: `py -3.11 -m unittest discover -s tests -v`.

## Render

Створіть Web Service з кореневою директорією `backend`, командою запуску `uvicorn main:app --host 0.0.0.0 --port $PORT` і Python 3.11. Додайте PostgreSQL та змінні `BOT_TOKEN`, `DATABASE_URL`, `MINIAPP_URL`, `MASTER_TELEGRAM_ID`. Не вмикайте `DEV_AUTH` у продакшені. Frontend можна розгорнути як Static Site з директорією `frontend`, build-командою `npm ci && npm run build` і publish-директорією `dist`; встановіть `VITE_API_URL` на URL backend. Обидва URL для Telegram мають використовувати HTTPS.

Backend запускає polling бота та планувальник нагадувань у тому самому процесі. На Render не запускайте додаткову копію polling-процесу з тим самим токеном.

## Перевірка

- [ ] `/start` показує українське привітання та кнопку «Відкрити додаток».
- [ ] У Mini App проходить онбординг; каталог відкривається за категоріями.
- [ ] Для кожної послуги слоти закінчуються до 20:00 з урахуванням тривалості.
- [ ] Два одночасні запити на один слот: створюється лише один запис.
- [ ] Прострочений слот відхиляється; з одного облікового запису дозволено не більше 5 записів за добу.
- [ ] Понад 10 API-запитів за хвилину від одного користувача обмежуються.
- [ ] Запис менш ніж за 24 години має статус `pending`; майстер може підтвердити чи відхилити його в боті.
- [ ] Скасування та перенесення відображаються в API та Mini App.
- [ ] Планувальник надсилає нагадування за 24 і 1 годину (перевірте на тестовому записі).
- [ ] Після повернення до Mini App список записів оновлюється.

Нагадування надсилаються лише користувачам, які запускали бота та не заблокували його. Робочий час і дати зберігаються як локальний час `Europe/Kyiv`.
