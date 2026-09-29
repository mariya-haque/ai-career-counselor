"""HTTP layer: turn a browser conversation into a filled profile and a plan.

The server owns the interview's progress. The model gets to phrase questions and
read answers, but which slot is outstanding is decided here, so a model that
wanders off never costs the plan a field.
"""

import os
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

import accounts
import counselor
import store
from counselor import SLOTS, SLOT_BY_KEY, SLOT_KEYS

app = FastAPI(title="Career counseling bot")

HERE = os.path.dirname(os.path.abspath(__file__))

# With the app on a public link, every account that gets created can spend your
# API credits. Set APP_PASSWORD and it becomes an invite code people must have to
# register. Left empty, anyone who finds the link can sign up.
INVITE = os.environ.get("APP_PASSWORD", "").strip()

COOKIE = "cc_login"

# Endpoints reachable without being signed in: the page itself, and the doors.
OPEN_PATHS = {"/", "/api/auth/state", "/api/auth/signup", "/api/auth/login", "/api/auth/logout"}


def visitor(request):
    return store.user_by_token(request.cookies.get(COOKIE, ""))


def set_login(response, user_id):
    token = accounts.new_token()
    store.start_login(user_id, token)
    response.set_cookie(
        COOKIE, token, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax"
    )


def public(user):
    return {"id": user["id"], "name": user["name"], "email": user["email"]}

OPENING = (
    "Hi {name} — I'm your career counselor. I'll ask you a dozen or so questions about "
    "your background, your time, and what you actually enjoy, and then write you a "
    "six-month plan built around your real week. Answer in as much detail as you like."
)


class Message(BaseModel):
    session_id: str
    text: str


class Session(BaseModel):
    session_id: str


class SignUp(BaseModel):
    name: str = ""
    email: str = ""
    password: str = ""
    invite: str = ""


class SignIn(BaseModel):
    email: str = ""
    password: str = ""


store.init()


@app.middleware("http")
async def gate(request: Request, call_next):
    """Everything that costs money or reveals an answer needs an account.

    The page itself stays open — it holds no data, and it has to load in order to
    show the sign-in form.
    """
    path = request.url.path
    if path in OPEN_PATHS or path.startswith("/static"):
        return await call_next(request)
    if not visitor(request):
        return JSONResponse({"error": "Please sign in."}, status_code=401)
    return await call_next(request)


@app.get("/api/auth/state")
def auth_state(request: Request):
    user = visitor(request)
    return {"invite_required": bool(INVITE), "user": public(user) if user else None}


@app.post("/api/auth/signup")
def signup(body: SignUp, response: Response):
    problem = accounts.signup_problem(body.name, body.email, body.password, body.invite, INVITE)
    if problem:
        return JSONResponse({"error": problem}, status_code=400)

    email = accounts.clean_email(body.email)
    if store.user_by_email(email):
        return JSONResponse({"error": "An account with that email already exists."}, status_code=409)

    digest, salt = accounts.hash_password(body.password)
    user_id = store.create_user(email, body.name.strip(), digest, salt)
    set_login(response, user_id)
    return {"user": {"id": user_id, "name": body.name.strip(), "email": email}}


@app.post("/api/auth/login")
def login(body: SignIn, response: Response):
    user = store.user_by_email(accounts.clean_email(body.email))
    # Same message either way, so this cannot be used to discover who has an account.
    if not user or not accounts.verify_password(body.password, user["digest"], user["salt"]):
        return JSONResponse({"error": "Wrong email or password."}, status_code=401)
    set_login(response, user["id"])
    return {"user": public(user)}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    store.end_login(request.cookies.get(COOKIE, ""))
    response.delete_cookie(COOKIE)
    return {"ok": True}


def unfilled(profile, skip=None):
    return [s for s in SLOTS if not profile.get(s["key"]) and s["key"] != skip]


def progress(session):
    profile = session["profile"]
    done = sum(1 for k in SLOT_KEYS if profile.get(k))
    asked = len(profile.get("extra", []))
    queued = len(session.get("queue", [])) + (1 if session.get("pending") else 0)
    total = len(SLOT_KEYS) + max(asked + queued, counselor.FOLLOWUPS)
    return {"done": done + asked, "total": total}


def state(session, messages, error=None):
    for text in messages:
        session["history"].append({"role": "counselor", "text": text})
    store.save(session)

    payload = {
        "session_id": session["id"],
        "messages": messages,
        "profile": {
            s["key"]: {"label": s["label"], "value": session["profile"].get(s["key"], "")} for s in SLOTS
        },
        "extra": session["profile"].get("extra", []),
        "stage": session["stage"],
        "progress": progress(session),
        "plan": session.get("plan"),
        "chips": counselor.CHIPS.get(session.get("asked") or "", []),
    }
    if error:
        payload["error"] = error
    return payload


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


MISSING = JSONResponse({"error": "That session has expired. Start a new one."}, status_code=404)


def owned(session_id, user):
    """The session, but only if it belongs to whoever is asking.

    A wrong owner gets the same answer as a session that does not exist, so ids
    cannot be probed for other people's conversations.
    """
    session = store.load(session_id)
    if not session or session.get("user_id") != user["id"]:
        return None
    return session


@app.post("/api/start")
def start(request: Request):
    user = visitor(request)
    session = store.new(uuid.uuid4().hex[:12], user["id"])

    # The account already told us their name, so asking again would be daft.
    session["profile"]["name"] = user["name"].strip()
    first = unfilled(session["profile"])[0]
    session["asked"] = first["key"]

    greeting = OPENING.format(name=user["name"].strip().split()[0] or "there")
    return state(session, [greeting, first["question"]])


@app.post("/api/message")
def message(body: Message, request: Request):
    session = owned(body.session_id, visitor(request))
    if not session:
        return MISSING

    answer = body.text.strip()
    if not answer:
        return state(session, [])

    session["history"].append({"role": "you", "text": answer})
    profile = session["profile"]
    remaining = unfilled(profile, skip=session.get("asked")) if session["stage"] == "core" else []
    # Nothing left to ask and nothing queued: this answer ends the session.
    closing = session["stage"] == "followup" and not session.get("queue")

    try:
        result = counselor.turn(profile, session["history"], answer, remaining, closing)
    except Exception as err:
        store.save(session)
        return state(session, [], error=f"{type(err).__name__}: {err}")

    for key, value in (result.get("updates") or {}).items():
        if key in SLOT_KEYS and value and value.strip():
            profile[key] = value.strip()

    messages = [result["reply"].strip()] if result.get("reply", "").strip() else []

    if session["stage"] == "followup" and session.get("pending"):
        profile.setdefault("extra", []).append([session["pending"], answer])
        session["pending"] = None

    if session["stage"] == "core":
        left = unfilled(profile)
        if left:
            # Honour the model's choice only if it really is still open; otherwise
            # ask the next open slot ourselves rather than let one slip past.
            chosen = result.get("next_key")
            if chosen and chosen in {s["key"] for s in left}:
                session["asked"] = chosen
            else:
                session["asked"] = left[0]["key"]
                messages.append(left[0]["question"])
            return state(session, messages)
        session["stage"] = "followup"
        session["asked"] = None
        try:
            session["queue"] = counselor.followups(profile)
        except Exception as err:
            session["queue"] = []
            messages.append(f"(Skipping the follow-ups — {type(err).__name__}.)")

    if session["stage"] == "followup":
        if session["queue"]:
            session["pending"] = session["queue"].pop(0)
            messages.append(session["pending"])
            return state(session, messages)
        session["stage"] = "ready"

    return state(session, messages)


@app.post("/api/plan")
def build_plan(body: Session, request: Request):
    session = owned(body.session_id, visitor(request))
    if not session:
        return MISSING

    try:
        session["plan"] = counselor.plan(session["profile"], session["history"])
    except Exception as err:
        return state(session, [], error=f"{type(err).__name__}: {err}")

    session["stage"] = "done"
    session["pending"] = None
    session["queue"] = []
    return state(session, [])


@app.get("/api/session/{session_id}")
def resume(session_id: str, request: Request):
    session = owned(session_id, visitor(request))
    if not session:
        return MISSING
    payload = state(session, [])
    payload["messages"] = []
    payload["history"] = session["history"]
    return payload


@app.get("/api/sessions")
def sessions(request: Request):
    return {"sessions": store.sessions_for(visitor(request)["id"])}


@app.get("/api/plan/{session_id}.md", response_class=PlainTextResponse)
def download(session_id: str, request: Request):
    session = owned(session_id, visitor(request))
    if not session or not session.get("plan"):
        return PlainTextResponse("No plan for that session yet.", status_code=404)

    name = session["profile"].get("name") or "there"
    return PlainTextResponse(
        markdown(session["plan"], name),
        headers={"Content-Disposition": f'attachment; filename="six-month-plan-{session_id}.md"'},
    )


def markdown(plan, name):
    """The plan as a file worth keeping open next to your work."""
    out = [f"# {plan['headline']}", "", f"**A six-month plan for {name}** — target: {plan['target_role']}", ""]
    out += [plan["verdict"], ""]

    out += ["## Where you're starting", "", "**What you've already got**", ""]
    out += [f"- {s}" for s in plan["strengths"]]
    out += ["", "**What's missing**", ""]
    out += [f"- {g}" for g in plan["gaps"]]

    out += ["", "## Your week", "", "| Day | Blocks |", "| --- | --- |"]
    out += [f"| {d['day']} | {' · '.join(d['blocks'])} |" for d in plan["weekly_routine"]]

    for m in plan["months"]:
        out += ["", f"## Month {m['month']} — {m['theme']}", ""]
        out += [f"**Focus:** {', '.join(m['focus_skills'])}", ""]
        for w in m["weeks"]:
            out += [f"**Week {w['week']} — {w['goal']}**", ""]
            out += [f"- {t}" for t in w["tasks"]]
            out += [""]
        out += [f"**Build this month:** {m['project']}", ""]
        out += [f"**Certification:** {m['certification']}", "", "**Resources**", ""]
        out += [f"- {r['name']} ({r['kind']}, {r['cost']}) — {r['why']}" for r in m["resources"]]
        out += ["", f"**Done when:** {m['checkpoint']}"]

    out += ["", "## What you'll own after six months", ""]
    out += [f"- {p}" for p in plan["portfolio"]]
    out += ["", "## Job search", ""]
    out += [f"{i}. {s}" for i, s in enumerate(plan["job_search"], 1)]
    out += ["", "## Habits to hold", ""]
    out += [f"- {h}" for h in plan["habits"]]
    out += ["", "## Where this could go wrong", ""]
    out += [f"- **{r['risk']}** → {r['fix']}" for r in plan["risks"]]
    out += ["", "## Start here", "", f"> {plan['first_step']}", ""]

    return "\n".join(out)
