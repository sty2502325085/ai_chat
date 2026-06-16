# AI Chat Assistant

A local FastAPI + DeepSeek API chat assistant for the V1 learning milestone.

## Features

- User registration and login
- Token-based authentication
- SQLite chat history storage
- Per-user chat sessions
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
```

SQLite data is stored in `chat.db`. The database file is ignored by Git.
