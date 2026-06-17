# AI Chat Assistant

A local FastAPI + DeepSeek API chat assistant for the V1 learning milestone.

## Features

- User registration and login
- Token-based authentication
- SQLite chat history storage
- Per-user chat sessions
- Daily message limit for regular users
- DeepSeek-powered replies

## Run locally

1. Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Start the app:

```powershell
uvicorn main:app --reload
```

4. Open:

```text
http://127.0.0.1:8000
```

## Configuration

Local secrets are read from `.env`. The file is ignored by Git.

```text
DEEPSEEK_API_KEY=your_deepseek_api_key_here
DEEPSEEK_MODEL=deepseek-chat
SECRET_KEY=replace_with_a_long_random_secret
DAILY_MESSAGE_LIMIT=50
ADMIN_USERS=sty2502325085,admin
DATABASE_URL=
```

Local SQLite data is stored in `chat.db`. The database file is ignored by Git.

If `DATABASE_URL` is set, the app uses PostgreSQL instead of SQLite. This is recommended for online deployment.

The user `甘水清` is configured as an unlimited account in the local learning version.

Open the admin dashboard at `http://127.0.0.1:8000/admin`. Only usernames listed in `ADMIN_USERS` can access admin API data.

## Deploy to Render

This project can be deployed as a Render Web Service.

1. Push the project to GitHub.
2. In Render, create a new Web Service from the GitHub repository.
3. Use these commands if Render asks for them:

```text
Build Command: pip install -r requirements.txt
Start Command: uvicorn main:app --host 0.0.0.0 --port $PORT
```

4. Add environment variables in Render:

```text
DEEPSEEK_API_KEY=your_deepseek_api_key_here
DEEPSEEK_MODEL=deepseek-chat
SECRET_KEY=replace_with_a_long_random_secret
DAILY_MESSAGE_LIMIT=50
ADMIN_USERS=sty2502325085,admin
DATABASE_URL=your_render_postgres_external_database_url
```

Do not commit `.env` or `chat.db` to GitHub.

For online use, create a Render PostgreSQL database and copy its External Database URL into the Web Service environment variable named `DATABASE_URL`.

The local SQLite database and the online PostgreSQL database are separate. Existing local users and chat records are not automatically copied online.
