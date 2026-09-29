"""The counseling brain: what to ask, how to read the answers, how to plan.

The interview is slot-filled rather than left to the model. The model decides how
each question is *phrased* and which slots an answer happens to fill, but the
server decides what still needs asking, so nothing gets skipped when someone
answers three questions in one paragraph.
"""

import json
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# The Windows console defaults to cp1252 and dies on the punctuation the model emits.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

_client = None


def client():
    """Built on first use, so a missing key surfaces as a message in the chat
    rather than killing the server before it can serve the page."""
    global _client
    if _client is None:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set — copy .env.example to .env and add your key.")
        _client = OpenAI(api_key=key, timeout=180)
    return _client

# Reasoning models take an effort knob; the 4o family rejects it.
REASONS = MODEL.startswith(("gpt-5", "o1", "o3", "o4"))

# The intake. `question` is the fallback wording used when the model is
# unavailable; `need` tells the model why we are asking, so its rephrasing
# still pulls for the same information.
SLOTS = [
    {
        "key": "name",
        "label": "Name",
        "question": "Let's start simple — what should I call you?",
        "need": "the person's name, so the plan can address them directly",
    },
    {
        "key": "age",
        "label": "Age",
        "question": "How old are you?",
        "need": "their age, which sets how aggressive or exploratory the six months should be",
    },
    {
        "key": "education",
        "label": "Education",
        "question": (
            "What's your degree or current education? Tell me the subject, and whether "
            "you're still studying or already finished."
        ),
        "need": "degree subject, level, and whether it is finished or in progress",
    },
    {
        "key": "certifications",
        "label": "Certifications",
        "question": (
            "Any certifications, online courses, or training you've already completed? "
            "\"None yet\" is a completely normal answer."
        ),
        "need": "certifications and courses already completed, or that there are none",
    },
    {
        "key": "skills",
        "label": "Current skills",
        "question": (
            "What can you actually do today? Tools, software, languages — anything from "
            "Excel to Python to video editing counts."
        ),
        "need": "concrete current skills and rough confidence level in each",
    },
    {
        "key": "experience",
        "label": "Experience",
        "question": (
            "Any experience so far — a job, internship, freelance work, or even personal "
            "projects you built on your own?"
        ),
        "need": "work, internship, freelance, or project experience",
    },
    {
        "key": "interests",
        "label": "Sports, gym & hobbies",
        "question": (
            "Now the part most career advice ignores: outside study and work, what do you "
            "enjoy? Sports, gym, hobbies — and roughly how often you do them."
        ),
        "need": "sports, gym, and hobbies plus how many days a week they take, so the plan schedules around them instead of pretending they don't exist",
    },
    {
        "key": "goal",
        "label": "Career goal",
        "question": (
            "What kind of work do you want to be doing a few years from now? "
            "\"I'm not sure yet\" is a perfectly good answer — I'll help narrow it."
        ),
        "need": "target role or field, or an admission that they are undecided",
    },
    {
        "key": "hours",
        "label": "Time available",
        "question": (
            "How many hours a week can you realistically give to this, and which days "
            "work best for you?"
        ),
        "need": "realistic weekly study hours and which days are free",
    },
    {
        "key": "constraints",
        "label": "Constraints",
        "question": (
            "Last of the basics: anything that shapes the plan? A current job or classes, "
            "budget for paid courses, your city or remote-only, the equipment you have."
        ),
        "need": "money, location, equipment, and time constraints that limit what can be recommended",
    },
]

SLOT_KEYS = [s["key"] for s in SLOTS]
SLOT_BY_KEY = {s["key"]: s for s in SLOTS}

# One-tap answers the UI offers under each question. They fill the box rather than
# sending, so a chip is a starting point someone can add to, not a whole answer.
CHIPS = {
    "education": ["Still studying", "Just graduated"],
    "certifications": ["None yet"],
    "skills": ["I'm a complete beginner"],
    "experience": ["No experience yet", "Only university projects"],
    "interests": ["Gym", "Cricket", "Football", "Reading", "Gaming", "Music"],
    "goal": ["I'm not sure yet"],
    "hours": ["5 hours a week", "10 hours a week", "20+ hours a week"],
    "constraints": ["Free resources only", "Nothing holding me back"],
}

FOLLOWUPS = 3  # adaptive questions the model gets to ask after the fixed intake

COUNSELOR = """You are a career counselor running a first session with someone.

Your manner: warm, direct, and specific. You sound like an experienced human advisor,
not a form. No emoji, no hype, no "great question!". One or two short sentences of
acknowledgement is plenty — react to what they actually said, not to the fact that
they said something.

Never invent details about the person. If an answer is vague, you may say so briefly,
but still move the conversation forward."""

TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "updates": {
            "type": "object",
            "properties": {
                key: {
                    "type": "string",
                    "description": f"Updated value for {key}, or \"\" if this message said nothing about it.",
                }
                for key in SLOT_KEYS
            },
            "required": SLOT_KEYS,
            "additionalProperties": False,
        },
        "reply": {
            "type": "string",
            "description": "Short acknowledgement, then the next question, as one natural message.",
        },
        "next_key": {
            "type": "string",
            "enum": SLOT_KEYS + [""],
            "description": "The field `reply` ends by asking about, or \"\" if reply asks nothing.",
        },
    },
    "required": ["updates", "reply", "next_key"],
    "additionalProperties": False,
}

FOLLOWUP_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "description": f"Exactly {FOLLOWUPS} follow-up questions, one per string.",
            "items": {"type": "string"},
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

MONTH_SCHEMA = {
    "type": "object",
    "properties": {
        "month": {"type": "integer", "description": "1 through 6."},
        "theme": {"type": "string", "description": "Four to seven words naming this month's job."},
        "focus_skills": {"type": "array", "items": {"type": "string"}},
        "weeks": {
            "type": "array",
            "description": "Exactly 4 weeks.",
            "items": {
                "type": "object",
                "properties": {
                    "week": {"type": "integer"},
                    "goal": {"type": "string", "description": "One sentence: what is true at the end of this week."},
                    "tasks": {
                        "type": "array",
                        "description": "3 to 5 concrete actions, each doable in a sitting.",
                        "items": {"type": "string"},
                    },
                },
                "required": ["week", "goal", "tasks"],
                "additionalProperties": False,
            },
        },
        "project": {"type": "string", "description": "The one thing they build or ship this month."},
        "resources": {
            "type": "array",
            "description": "2 to 4 named courses, books, channels, or docs. Real ones only.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "description": "course, book, channel, docs, community, or practice site"},
                    "cost": {"type": "string", "description": "free, or an approximate price"},
                    "why": {"type": "string"},
                },
                "required": ["name", "kind", "cost", "why"],
                "additionalProperties": False,
            },
        },
        "certification": {
            "type": "string",
            "description": "Certification to study for or sit this month, or \"none this month\".",
        },
        "checkpoint": {
            "type": "string",
            "description": "A pass/fail test of the month: something they can point at and say done.",
        },
    },
    "required": [
        "month",
        "theme",
        "focus_skills",
        "weeks",
        "project",
        "resources",
        "certification",
        "checkpoint",
    ],
    "additionalProperties": False,
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "The plan's title, naming the direction. Under 60 characters."},
        "target_role": {"type": "string", "description": "The specific role this plan aims them at."},
        "verdict": {
            "type": "string",
            "description": "Two or three sentences: where they stand today and why this direction fits them. Honest, not flattering.",
        },
        "strengths": {"type": "array", "description": "3 to 4 real advantages they already have.", "items": {"type": "string"}},
        "gaps": {
            "type": "array",
            "description": "3 to 5 gaps between them and the target role, hardest first.",
            "items": {"type": "string"},
        },
        "weekly_routine": {
            "type": "array",
            "description": "Exactly 7 entries, Monday through Sunday, with their gym, sport, and hobby time kept in place.",
            "items": {
                "type": "object",
                "properties": {
                    "day": {"type": "string"},
                    "blocks": {
                        "type": "array",
                        "description": "Each string is one block, e.g. \"7:00-8:00 gym\" or \"20:00-22:00 Python practice\".",
                        "items": {"type": "string"},
                    },
                },
                "required": ["day", "blocks"],
                "additionalProperties": False,
            },
        },
        "months": {"type": "array", "description": "Exactly 6 months.", "items": MONTH_SCHEMA},
        "portfolio": {
            "type": "array",
            "description": "The 2 to 4 artifacts they will own after six months.",
            "items": {"type": "string"},
        },
        "job_search": {
            "type": "array",
            "description": "Ordered job-hunt steps and when in the six months each starts.",
            "items": {"type": "string"},
        },
        "habits": {"type": "array", "description": "3 to 5 small daily or weekly habits.", "items": {"type": "string"}},
        "risks": {
            "type": "array",
            "description": "2 to 4 ways this plan realistically fails for this person, each with a fix.",
            "items": {
                "type": "object",
                "properties": {"risk": {"type": "string"}, "fix": {"type": "string"}},
                "required": ["risk", "fix"],
                "additionalProperties": False,
            },
        },
        "first_step": {
            "type": "string",
            "description": "The single thing to do in the next 24 hours. Small enough that there is no excuse.",
        },
    },
    "required": [
        "headline",
        "target_role",
        "verdict",
        "strengths",
        "gaps",
        "weekly_routine",
        "months",
        "portfolio",
        "job_search",
        "habits",
        "risks",
        "first_step",
    ],
    "additionalProperties": False,
}


def ask(instructions, prompt, schema, name, effort="low", timeout=None):
    """One structured call. Returns the parsed object."""
    kwargs = {
        "model": MODEL,
        "instructions": instructions,
        "input": prompt,
        "text": {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
    }
    if REASONS:
        kwargs["reasoning"] = {"effort": effort}

    caller = client().with_options(timeout=timeout) if timeout else client()
    return json.loads(caller.responses.create(**kwargs).output_text)


def profile_text(profile):
    known = [f"- {SLOT_BY_KEY[k]['label']}: {profile[k]}" for k in SLOT_KEYS if profile.get(k)]
    extra = [f"- {q}\n  Answer: {a}" for q, a in profile.get("extra", [])]
    if extra:
        known.append("Follow-up answers:\n" + "\n".join(extra))
    return "\n".join(known) if known else "(nothing yet)"


def transcript_text(history, limit=6):
    return "\n".join(f"{h['role']}: {h['text']}" for h in history[-limit:])


def turn(profile, history, answer, remaining, closing=False):
    """Read one answer into the profile and compose the next message.

    `remaining` is the ordered list of slots still unanswered, or empty when the
    fixed intake is done and the server will supply what comes next itself.
    `closing` marks the last answer of the session, after which only the plan is left.
    """
    if remaining:
        menu = "\n".join(f'  {s["key"]}: {s["need"]}\n    plain wording: "{s["question"]}"' for s in remaining)
        instruction = f"""Still missing:
{menu}

Go down that list and pick the first field your `updates` did not just fill. Ask about it,
rephrased in your own voice so it follows from what they just said, and put its key in
`next_key`. Ask that one thing only — do not stack a second question onto it.

If your `updates` filled every field on the list, ask nothing, close warmly, and set
`next_key` to ""."""
    elif closing:
        instruction = (
            "That was the last answer you needed. Acknowledge it in a sentence, then tell them "
            "you have enough and are writing their six-month plan now. Ask nothing. "
            "Set `next_key` to \"\"."
        )
    else:
        instruction = (
            "Ask nothing at all — another question is already queued and will follow yours, so "
            "one here would collide with it. Acknowledge their answer in one or two sentences "
            "and stop, without hinting that a question is coming. Set `next_key` to \"\"."
        )

    prompt = f"""Known so far about them:
{profile_text(profile)}

Recent conversation:
{transcript_text(history)}

You just asked: "{history[-1]['text'] if history else ''}"
They answered: "{answer}"

Two jobs.

1. `updates`: pull every fact their answer contains into the matching fields. A single
   answer often fills several — record all of them, not just the field you asked about.
   Leave a field as "" unless this answer said something about it. If they refuse or skip
   a field you asked about, write "not specified" for that field so it stops being asked.
   When a field already has a value and they add to it, return the merged value.

2. `reply`: {instruction}"""

    return ask(COUNSELOR, prompt, TURN_SCHEMA, "turn", effort="low")


def followups(profile):
    """Three questions the fixed intake could not have known to ask."""
    prompt = f"""This is what the intake collected:
{profile_text(profile)}

Ask exactly {FOLLOWUPS} follow-up questions — the ones a good counselor would ask *this*
person specifically, that a standard form would have missed.

Aim them at whatever is still blocking a concrete six-month plan: a vague goal that needs
narrowing, a mismatch between their stated target and their actual time or money, an
untested assumption about what a job in their field is really like, a strength in their
hobbies they have not thought to apply to work.

One question per string. No preamble, no numbering. Each must be answerable in a sentence
or two. Do not re-ask anything the intake above already answered, and do not ask them to
restate something more precisely — ask about what is genuinely absent."""

    result = ask(COUNSELOR, prompt, FOLLOWUP_SCHEMA, "followups", effort="medium")
    return [q for q in result.get("questions", []) if q.strip()][:FOLLOWUPS]


def plan(profile, history):
    """The six-month guide."""
    prompt = f"""Build the six-month plan for this person.

{profile_text(profile)}

Full conversation:
{transcript_text(history, limit=100)}

Rules for this plan.

Fit the real person. Every month must be doable inside the hours they said they have —
if they said eight hours a week, do not plan twenty. Respect their budget: if money is
tight, free resources only, and say so. Their sports, gym, and hobbies stay in the weekly
routine; a plan that quietly deletes them is a plan they will abandon in three weeks.

Make it specific. Name real courses, real certifications, real practice sites. "Learn
Python" is useless — "finish sections 1-6 of Automate the Boring Stuff and solve 20 easy
problems on Exercism" is a plan. Every week needs actions they could start tonight.

Build a curve. Month 1 is foundations and one small visible win. Months 2-4 add depth and
shipped projects. Month 5 turns the work into proof — portfolio, profile, references.
Month 6 is applying, interviewing, or launching, not more studying.

Be honest in `verdict`, `gaps`, and `risks`. If their target is unrealistic in six months,
name the realistic version and aim the plan at that instead. If they were undecided, pick
the strongest direction their answers point to, commit to it, and say plainly why you
chose it.

Write to them as "you"."""

    return ask(COUNSELOR, prompt, PLAN_SCHEMA, "plan", effort="medium", timeout=600)
