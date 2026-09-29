# Career counseling bot

A chat counselor that interviews you about your background, your time, and what you
actually enjoy, then writes a six-month plan built around your real week — gym, sport and
hobbies included, not deleted.

Built with FastAPI, the OpenAI Responses API (structured outputs), SQLite (stdlib `sqlite3`) and a single-page HTML frontend.

## Prerequisites

- Python 3.10+ (verified on 3.14)
- An OpenAI API key: https://platform.openai.com/api-keys
- Optional, for sharing a public link: [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)

## Run it

First time only (Windows):

```cmd
python -m venv venv
venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Then put your OpenAI key in `.env`, and start it:

```cmd
venv\Scripts\activate
python -m uvicorn web:app --reload
```

On macOS / Linux:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn web:app --reload
```

Open http://127.0.0.1:8000. `Ctrl+C` stops it, `deactivate` leaves the venv.

The SQLite database `career.db` (plus its `-shm`/`-wal` files) is created next to `store.py` on first run and is git-ignored.

## Accounts

Everyone signs up with a name, email, and password. Their sessions and plans belong to
that account: `/api/session/…`, `/api/plan`, and the Markdown export all check ownership,
and someone else's session id returns the same 404 as one that never existed, so ids
cannot be probed. A wrong password and an unknown email give the same message, so the
login form cannot be used to discover who has an account.

Passwords are hashed with stdlib scrypt (n=2¹⁴, r=8) — about 150ms and 16MB per attempt.
Sessions are server-side tokens in the `logins` table, so signing out revokes that one
device rather than every login. Nothing here needs a dependency beyond the five in
`requirements.txt`.

Signing up also skips the first interview question, since the account already knows their
name.

## Share it

`APP_PASSWORD` in `.env` is the **invite code** people need in order to register. Set it
before exposing the app anywhere public — every account that gets created can spend your
OpenAI credits, roughly $0.05–0.15 for a full plan. Empty means anyone who finds the link
can sign up, which is only safe on localhost.

To hand someone a public link, start the app, then in a **second** window from this folder:

```cmd
cloudflared.exe tunnel --url http://127.0.0.1:8000 --protocol http2 --no-autoupdate
```

`cloudflared.exe` is not in the repository (it is a large binary and git-ignored). [Download it from Cloudflare](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) and put it in this folder or on your `PATH` (on macOS/Linux the command is `cloudflared`).

It prints a `https://….trycloudflare.com` address. That link works from anywhere for as
long as both windows stay open — it is not permanent, and you get a different address
every time you start it. Closing the window takes the link down, which is the simplest way
to stop sharing. Changing `APP_PASSWORD` only affects new sign-ups: existing accounts and their logins keep working.

For a permanent address instead, deploy to a host and set `OPENAI_API_KEY` and
`APP_PASSWORD` as environment variables there.

## The interview

Ten fixed questions — name, age, education, certifications, current skills, experience,
sports/gym/hobbies, career goal, weekly hours, constraints — then three follow-ups the
model writes after seeing your answers, aimed at whatever is still blocking a concrete
plan.

Coverage is the server's job, not the model's. `web.py` tracks which slots are still empty
and will not leave the intake stage until every one is filled, so a model that wanders off
never costs the plan a field. The model only decides how each question is *phrased* and
which slots an answer happens to fill — answer three questions in one paragraph and all
three get recorded, which is why the progress counter sometimes jumps by two.

You can stop early: **Build my plan now** generates from whatever has been answered so far.

## The plan

One structured call returns a verdict on where you stand, your strengths and gaps, a
day-by-day weekly routine, six months of four weeks each (goal, tasks, project,
certification, free/paid resources, and a pass-fail checkpoint), a portfolio list, a job
search sequence, habits, honest failure modes, and one thing to do tonight.

It is told to fit your real constraints: your stated hours cap the workload, a tight
budget means free resources only, and your training and hobby time stays in the routine.
Download it as Markdown from the header.

## Layout

| File | What it holds |
| --- | --- |
| [counselor.py](counselor.py) | Questions, JSON schemas, and the three OpenAI calls |
| [web.py](web.py) | Auth endpoints, interview state machine, HTTP API, Markdown export |
| [accounts.py](accounts.py) | Password hashing, tokens, signup validation |
| [store.py](store.py) | SQLite for users, logins, and sessions (`career.db`, created on first run) |
| [static/index.html](static/index.html) | Sign-in screen, chat UI, profile rail, plan view |

Sessions persist against the account. The browser remembers which one was open per user,
so a refresh picks the conversation back up where it stopped, and **Your plans** in the
account menu reopens any earlier one.

## API

Everything except the page and the `/api/auth/*` endpoints needs the `cc_login` cookie set at sign-in.

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/` | The app |
| GET | `/api/auth/state` | Current user, and whether an invite code is required |
| POST | `/api/auth/signup` | `{name, email, password, invite}` |
| POST | `/api/auth/login` | `{email, password}` |
| POST | `/api/auth/logout` | Revokes this device's login |
| POST | `/api/start` | Starts a new interview session |
| POST | `/api/message` | `{session_id, text}`: answer the current question |
| POST | `/api/plan` | `{session_id}`: build the plan now |
| GET | `/api/sessions` | Your earlier sessions |
| GET | `/api/session/{id}` | Resume a session |
| GET | `/api/plan/{id}.md` | Download the plan as Markdown |

## Config

Copy `.env.example` to `.env` and fill it in.

| Variable | Default | |
| --- | --- | --- |
| `OPENAI_API_KEY` | — | required. Get one at https://platform.openai.com/api-keys |
| `OPENAI_MODEL` | `gpt-5-mini` | any Responses-API model that supports structured outputs |
| `APP_PASSWORD` | empty | invite code required to sign up; empty means open registration |
