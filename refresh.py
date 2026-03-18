#!/usr/bin/env python3
"""
Mohan Ops Dev Team Dashboard — Cloud Refresher
================================================
Runs every 30 minutes via GitHub Actions.
Reads Slack channels, parses task commands, updates tasks.json,
and regenerates the HTML dashboard (index.html + dev-dashboard.html).

Channels monitored:
  #all-mohan-ops  C0A4HBBLJPP  — command channel (done/todo/in progress)
  #value-engines  C0AC2CMB00Z  — scanned for organic task mentions
  DM Randy↔Nahom D0AGJS63YLV  — dev assignments
  DM Randy↔Ben   D0AGN99ED3L  — director requests

Team:
  Nahom  U0A5F3TF2RW  #3b82f6
  Kaleab U0ABH9K0J59  #a855f7
  Randy  U0AGJS6191T  #22c55e
  Ben    U0AE7DZJY0H  #f59e0b
"""

import os, json, re, sys, uuid, html as html_lib
from datetime import datetime, timezone, timedelta
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# ─── Configuration ────────────────────────────────────────────────────────────

SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN")

CHANNELS = {
    "all-mohan-ops": "C0A4HBBLJPP",
    "value-engines":  "C0AC2CMB00Z",
    "dm-randy-nahom": "D0AGJS63YLV",
    "dm-randy-ben":   "D0AGN99ED3L",
}

TEAM = {
    "U0A5F3TF2RW": {"name": "Nahom",  "key": "nahom",  "initial": "N"},
    "U0ABH9K0J59": {"name": "Kaleab", "key": "kaleab", "initial": "K"},
    "U0AGJS6191T": {"name": "Randy",  "key": "randy",  "initial": "R"},
    "U0AE7DZJY0H": {"name": "Ben",    "key": "ben",    "initial": "B"},
}

# Map name mentions → assignee key (both @name and <@SLACK_ID> formats)
MENTION_MAP = {
    "@nahom":  "nahom",  "@kaleab": "kaleab",
    "@randy":  "randy",  "@ben":    "ben",
    "<@u0a5f3tf2rw>": "nahom",  "<@u0abh9k0j59>": "kaleab",
    "<@u0agjs6191t>": "randy",  "<@u0ae7dzjy0h>": "ben",
}

TASKS_FILE    = "tasks.json"
TEMPLATE_FILE = "template.html"
OUTPUT_FILES  = ["index.html", "dev-dashboard.html"]

PROJECT_LABELS = {
    "ve-app":   "VE App",   "academy": "Academy",
    "design":   "Design",   "deck":    "Deck",
    "qc":       "QC",       "content": "Content",
    "access":   "Access",   "general": "General",
}

# ─── Command regex patterns ────────────────────────────────────────────────────

DONE_RE = re.compile(
    r"^("
    r"done[\s:,\-/!]+"
    r"|done with\s+"
    r"|finished[\s:,\-/!]*"
    r"|just finished\s+"
    r"|i'?ve?\s+finished\s+"
    r"|i'?m\s+done[\s:,\-/!]*"
    r"|all\s+done[\s:,\-/!]*"
    r"|completed[\s:,\-/!]*"
    r"|complete[\s:,\-/!]+"
    r"|just\s+completed\s+"
    r"|wrapped\s+up[\s:,\-/!]*"
    r"|knocked\s+out[\s:,\-/!]*"
    r"|knocked\s+off[\s:,\-/!]*"
    r"|took\s+care\s+of\s+"
    r"|taken\s+care\s+of\s+"
    r"|closed[\s:,\-/!]*"
    r"|closed\s+out[\s:,\-/!]*"
    r"|shipped[\s:,\-/!]*"
    r"|delivered[\s:,\-/!]*"
    r"|checked\s+off\s+"
    r"|crossed\s+off\s+"
    r"|it'?s\s+(all\s+)?done[\s:,\-/!]*"
    r"|that'?s\s+done[\s:,\-/!]*"
    r"|it'?s\s+complete[\s:,\-/!]*"
    r"|it'?s\s+finished[\s:,\-/!]*"
    r"|✅\s*"
    r")", re.IGNORECASE,
)

INPROGRESS_RE = re.compile(
    r"^("
    r"working[\s:,\-/!]+"
    r"|working\s+on\s+"
    r"|in\s+progress[\s:,\-/!]*"
    r"|started[\s:,\-/!]*"
    r"|starting[\s:,\-/!]*"
    r"|just\s+started\s+"
    r"|i'?m\s+starting\s+"
    r"|picking\s+up[\s:,\-/!]*"
    r"|picked\s+up[\s:,\-/!]*"
    r"|on\s+it[\s:,\-/!]*"
    r"|i'?m\s+on\s+(it|this)[\s:,\-/!]*"
    r"|kicking\s+off[\s:,\-/!]*"
    r"|kicked\s+off[\s:,\-/!]*"
    r"|jumping\s+on[\s:,\-/!]*"
    r"|jumped\s+on[\s:,\-/!]*"
    r"|handling[\s:,\-/!]*"
    r"|i'?ll\s+handle\s+"
    r"|i'?ll\s+take\s+(care\s+of\s+)?(this|that|it)?"
    r"|taking\s+(this|that|it|on)[\s:,\-/!]*"
    r"|looking\s+into[\s:,\-/!]*"
    r"|taking\s+a\s+look\s+(at\s+)?"
    r"|diving\s+(into|in)[\s:,\-/!]*"
    r"|getting\s+started\s+(on\s+)?"
    r"|beginning[\s:,\-/!]*"
    r"|🔄\s*"
    r")", re.IGNORECASE,
)

TODO_RE = re.compile(
    r"^("
    r"todo[\s:,\-/!]+"
    r"|to[\s\-]do[\s:,\-/!]+"
    r"|new\s+task[\s:,\-/!]+"
    r"|task[\s:,\-/!]+"
    r"|add[\s:,\-/!]+"
    r"|urgent[\s:,\-/!]+"
    r"|📌\s*"
    r"|action\s+item[\s:,\-/!]+"
    r"|assign[\s:,\-/!]+"
    r"|needs[\s:,\-/!]+"
    r"|we\s+need[\s:,\-/!]+"
    r"|need\s+to[\s:,\-/!]+"
    r"|i\s+need[\s:,\-/!]+"
    r"|we\s+should[\s:,\-/!]+"
    r"|please[\s:,\-/!]+"
    r"|could\s+(you|someone|nahom|kaleab|randy|ben)\s+"
    r"|can\s+(you|someone|nahom|kaleab|randy|ben)\s+"
    r"|don'?t\s+forget[\s:,\-/!]+"
    r"|reminder[\s:,\-/!]+"
    r"|make\s+sure[\s:,\-/!]+"
    r"|follow\s+up[\s:,\-/!]+"
    r"|request[\s:,\-/!]+"
    r")", re.IGNORECASE,
)

REMOVE_RE = re.compile(
    r"^("
    r"remove[\s:,\-/!]+"
    r"|cancel[\s:,\-/!]+"
    r"|delete[\s:,\-/!]+"
    r"|drop[\s:,\-/!]+"
    r"|nevermind[\s:,\-/!]*"
    r"|never\s+mind[\s:,\-/!]*"
    r"|scratch\s+that[\s:,\-/!]*"
    r"|disregard[\s:,\-/!]+"
    r"|no\s+longer\s+needed[\s:,\-/!]*"
    r")", re.IGNORECASE,
)

PRIORITY_RE = re.compile(
    r"^(priority[\s:,\-/!]+|make\s+(this\s+)?urgent[\s:,\-/!]*)",
    re.IGNORECASE,
)

# ─── Slack helpers ─────────────────────────────────────────────────────────────

def fetch_messages(client, channel_id, oldest_ts):
    """Fetch messages from a Slack channel since oldest_ts (Unix timestamp)."""
    try:
        resp = client.conversations_history(
            channel=channel_id,
            oldest=str(oldest_ts),
            limit=200,
        )
        msgs = resp.get("messages", [])
        msgs.reverse()  # Chronological order
        return msgs
    except SlackApiError as e:
        print(f"  ⚠️  Slack API error on {channel_id}: {e.response.get('error', e)}")
        return []


def slack_url(channel_id, ts):
    return f"https://mohanops.slack.com/archives/{channel_id}/p{ts.replace('.', '')}"


# ─── Text helpers ──────────────────────────────────────────────────────────────

def strip_command(text, pattern):
    """Remove the leading command keyword from a message."""
    m = pattern.match(text.strip())
    return text[m.end():].strip() if m else text.strip()


def find_assignee(text):
    """Return assignee key from @mention in text, or None."""
    lower = text.lower()
    for mention, key in MENTION_MAP.items():
        if mention in lower:
            return key
    return None


def sender_key(user_id):
    """Map a Slack user ID to our assignee key, defaulting to 'randy'."""
    return TEAM.get(user_id, {}).get("key", "randy")


def clean_title(text):
    """Strip @mentions, urgency words, and extra whitespace from task title."""
    text = re.sub(r"<@[A-Za-z0-9]+>", "", text)
    text = re.sub(r"@\w+", "", text)
    text = re.sub(r"\b(urgent|asap|high|low|medium|critical)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[0].upper() + text[1:] if text else text


def infer_priority(text):
    """Guess priority from message text."""
    lower = text.lower()
    if any(w in lower for w in ["urgent", "asap", "critical", "immediately"]):
        return "high"
    if "high" in lower:
        return "high"
    if "low" in lower:
        return "low"
    return "medium"


def infer_project(text):
    """Guess project tag from task title."""
    lower = text.lower()
    if any(w in lower for w in ["ve app", "value engine", "bot", "coach", "instance",
                                  "password reset", "login link", "satellite", "hub"]):
        return "ve-app"
    if any(w in lower for w in ["academy", "kboss", "library", "scorm", "course", "module", "s.c.o.r.e"]):
        return "academy"
    if any(w in lower for w in ["deck", "pitch", "slide", "presentation"]):
        return "deck"
    if any(w in lower for w in ["design", "brand", "workbook", "agenda", "sprint",
                                  "logo", "canva", "redesign", "branding"]):
        return "design"
    if any(w in lower for w in ["qc", "grammar", "spelling", "review", "typo", "proof"]):
        return "qc"
    return "general"


def fmt_date(ts):
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    return dt.strftime("%b %-d")


# ─── Task matching ─────────────────────────────────────────────────────────────

def tokenize(text):
    """Extract meaningful words (3+ chars) for fuzzy matching."""
    return set(re.findall(r"\b[a-z]{3,}\b", text.lower()))


def best_match(tasks, query, assignee=None):
    """
    Find the task whose title best matches the query string.
    Uses Jaccard similarity with a small bonus if assignee matches.
    Returns None if no task scores above the threshold.
    """
    qw = tokenize(query)
    if not qw:
        return None

    best_score = 0.20  # Minimum similarity threshold
    best_task = None

    for task in tasks:
        tw = tokenize(task["title"])
        if not tw:
            continue
        score = len(qw & tw) / len(qw | tw)
        if assignee and task.get("assignee") == assignee:
            score += 0.05
        if score > best_score:
            best_score = score
            best_task = task

    return best_task


# ─── Message processing ────────────────────────────────────────────────────────

def process_messages(tasks, messages, channel_id, seen_ids):
    """
    Process new Slack messages and mutate tasks list accordingly.
    Returns True if any tasks were changed.
    """
    changed = False

    for msg in messages:
        ts = msg.get("ts", "")
        msg_id = f"{channel_id}:{ts}"

        # Skip already-processed messages and bot/system messages
        if msg_id in seen_ids:
            continue
        text = msg.get("text", "").strip()
        user_id = msg.get("user", "")
        if not text or not user_id or msg.get("subtype"):
            continue

        seen_ids.add(msg_id)

        # ── Mark Done ─────────────────────────────────────────────────────────
        if DONE_RE.match(text):
            query    = strip_command(text, DONE_RE)
            assignee = find_assignee(text) or sender_key(user_id)
            task     = best_match(tasks, query, assignee)
            if task:
                task["status"] = "done"
                task["date"]   = f"Completed {fmt_date(ts)}"
                changed        = True
                print(f"  ✅ Marked done: {task['title'][:60]}")
            else:
                print(f"  ⚠️  No match found for 'done: {query[:40]}'")

        # ── Move to In Progress ────────────────────────────────────────────────
        elif INPROGRESS_RE.match(text):
            query    = strip_command(text, INPROGRESS_RE)
            assignee = find_assignee(text) or sender_key(user_id)
            task     = best_match(tasks, query, assignee)
            if task:
                task["status"] = "inprogress"
                changed        = True
                print(f"  🔄 In progress: {task['title'][:60]}")
            else:
                print(f"  ⚠️  No match found for 'working: {query[:40]}'")

        # ── Add New Task ───────────────────────────────────────────────────────
        elif TODO_RE.match(text):
            query    = strip_command(text, TODO_RE)
            assignee = find_assignee(text) or sender_key(user_id)
            priority = infer_priority(text)
            title    = clean_title(query)
            if title:
                # Only add if a similar task doesn't already exist
                existing = best_match(tasks, title, assignee)
                if not existing:
                    new_task = {
                        "id":        str(uuid.uuid4())[:8],
                        "title":     title,
                        "status":    "todo",
                        "assignee":  assignee,
                        "project":   infer_project(title),
                        "priority":  priority,
                        "date":      fmt_date(ts),
                        "slack_url": slack_url(channel_id, ts),
                    }
                    tasks.append(new_task)
                    changed = True
                    print(f"  📌 New task: {title[:60]}")
                else:
                    print(f"  ⏭️  Similar task already exists, skipping: {title[:40]}")

        # ── Remove Task ────────────────────────────────────────────────────────
        elif REMOVE_RE.match(text):
            query    = strip_command(text, REMOVE_RE)
            assignee = find_assignee(text)
            task     = best_match(tasks, query, assignee)
            if task:
                tasks.remove(task)
                changed = True
                print(f"  🗑️  Removed: {task['title'][:60]}")

        # ── Update Priority ────────────────────────────────────────────────────
        elif PRIORITY_RE.match(text):
            query    = strip_command(text, PRIORITY_RE)
            assignee = find_assignee(text)
            task     = best_match(tasks, query, assignee)
            if task:
                new_priority = infer_priority(text + " " + query)
                task["priority"] = new_priority
                changed = True
                print(f"  🔺 Priority → {new_priority}: {task['title'][:50]}")

    return changed


# ─── HTML rendering ────────────────────────────────────────────────────────────

def render_card(task):
    """Render a single task card as an HTML string."""
    assignee = task.get("assignee", "randy")
    project  = task.get("project",  "general")
    priority = task.get("priority", "medium")
    title    = html_lib.escape(task.get("title", ""))
    date     = html_lib.escape(task.get("date",  ""))
    link     = task.get("slack_url", "")
    status   = task.get("status",    "todo")

    # Look up team member info
    member  = next((v for v in TEAM.values() if v["key"] == assignee),
                   list(TEAM.values())[2])  # default to Randy
    name    = member["name"]
    initial = member["initial"]

    # Priority badge
    if status == "done":
        badge = '<span class="priority-badge priority-low">Done</span>'
    elif priority == "high":
        badge = '<span class="priority-badge priority-high">High</span>'
    elif priority == "medium":
        badge = '<span class="priority-badge priority-medium">Medium</span>'
    else:
        badge = '<span class="priority-badge priority-low">Low</span>'

    title_cls   = "task-title done-title" if status == "done" else "task-title"
    proj_label  = PROJECT_LABELS.get(project, project.title())
    slack_anchor = (
        f'<a class="task-link" href="{link}" target="_blank">↗ Slack</a>'
        if link else ""
    )

    return f"""\
      <div class="task-card" data-assignee="{assignee}" data-project="{project}">
        <div class="task-header">
          <div class="{title_cls}">{title}</div>
          {badge}
        </div>
        <div class="task-meta">
          <span class="assignee-chip chip-{assignee}"><span class="av">{initial}</span>{name}</span>
          <span class="project-tag">{proj_label}</span>
          <span class="task-date">{date}</span>
          {slack_anchor}
        </div>
      </div>"""


def generate_html(tasks, now):
    """Fill the template with rendered task cards and return complete HTML."""
    with open(TEMPLATE_FILE) as f:
        template = f.read()

    todo_tasks     = [t for t in tasks if t["status"] == "todo"]
    inprog_tasks   = [t for t in tasks if t["status"] == "inprogress"]
    done_tasks     = [t for t in tasks if t["status"] == "done"]

    todo_html   = "\n".join(render_card(t) for t in todo_tasks)
    inprog_html = "\n".join(render_card(t) for t in inprog_tasks)
    done_html   = "\n".join(render_card(t) for t in done_tasks)

    now_str = now.strftime("%a %b %-d, %Y %H:%M UTC")

    html = template
    html = html.replace("{{TODO_CARDS}}",       todo_html)
    html = html.replace("{{INPROGRESS_CARDS}}", inprog_html)
    html = html.replace("{{DONE_CARDS}}",       done_html)
    html = html.replace("{{LAST_UPDATED}}",     now_str)

    return html, len(todo_tasks), len(inprog_tasks), len(done_tasks)


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(tz=timezone.utc)
    print(f"\n{'='*60}")
    print(f"  Mohan Ops Dashboard Refresh — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    if not SLACK_TOKEN:
        sys.exit("❌ SLACK_BOT_TOKEN environment variable is not set.\n"
                 "   Add it as a GitHub Actions secret named SLACK_BOT_TOKEN.")

    # ── Load state ──────────────────────────────────────────────────────────
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE) as f:
            state = json.load(f)
        print(f"📂 Loaded {len(state.get('tasks', []))} tasks from {TASKS_FILE}")
    else:
        print(f"⚠️  {TASKS_FILE} not found — starting fresh")
        state = {
            "tasks":          [],
            "last_updated":   "2026-01-01T00:00:00+00:00",
            "processed_ids":  [],
        }

    tasks    = state.get("tasks", [])
    seen_ids = set(state.get("processed_ids", []))
    last_str = state.get("last_updated", "2026-01-01T00:00:00+00:00")

    # Look back from last_updated minus a 10-minute buffer to catch stragglers
    try:
        last_dt = datetime.fromisoformat(last_str)
    except ValueError:
        last_dt = now - timedelta(hours=24)

    oldest_ts = (last_dt - timedelta(minutes=10)).timestamp()
    print(f"🕐 Scanning messages since: {last_dt.strftime('%Y-%m-%d %H:%M UTC')}\n")

    # ── Fetch & process Slack messages ──────────────────────────────────────
    client = WebClient(token=SLACK_TOKEN)
    for ch_name, ch_id in CHANNELS.items():
        print(f"📡 #{ch_name} ({ch_id})")
        messages = fetch_messages(client, ch_id, oldest_ts)
        print(f"   {len(messages)} message(s) to check")
        process_messages(tasks, messages, ch_id, seen_ids)
        print()

    # ── Persist updated state ───────────────────────────────────────────────
    state["tasks"]         = tasks
    state["last_updated"]  = now.isoformat()
    state["processed_ids"] = list(seen_ids)[-2000:]  # Cap to prevent bloat

    with open(TASKS_FILE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"💾 Saved {TASKS_FILE} ({len(tasks)} tasks total)\n")

    # ── Generate HTML ───────────────────────────────────────────────────────
    html, n_todo, n_inprog, n_done = generate_html(tasks, now)

    for out_file in OUTPUT_FILES:
        with open(out_file, "w") as f:
            f.write(html)
        print(f"🎨 Written: {out_file}")

    print(f"\n✅ Refresh complete — "
          f"{n_todo} todo · {n_inprog} in progress · {n_done} done\n")


if __name__ == "__main__":
    main()
