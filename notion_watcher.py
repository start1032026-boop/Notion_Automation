"""
Notion → Claude → Email Notifier
Watches your Notion workspace for any changes and sends AI-generated
email summaries to your teammate via Gmail SMTP.

Triggers:
  - Any page update (regular pages AND database pages)
  - New page created
  - Specific database item updated
  - Comment added
  - Checkbox ticked / task completed (on regular pages)
"""

import os
import json
import time
import smtplib
import logging
import hashlib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import requests
from dotenv import load_dotenv
load_dotenv()
from groq import Groq

# ─────────────────────────────────────────────
# CONFIGURATION  (or use .env / environment vars)
# ─────────────────────────────────────────────
NOTION_TOKEN        = os.getenv("NOTION_TOKEN", "your_notion_integration_token")
NOTION_DATABASE_IDS = [x for x in os.getenv("NOTION_DATABASE_IDS", "").split(",") if x.strip()]
NOTION_PAGE_IDS     = [x for x in os.getenv("NOTION_PAGE_IDS", "").split(",") if x.strip()]  # regular pages (non-database)

GROQ_API_KEY        = os.getenv("GROQ_API_KEY", "your_groq_api_key")

# Gmail SMTP (use an App Password, not your real password)
SMTP_HOST           = "smtp.gmail.com"
SMTP_PORT           = 587
EMAIL_SENDER        = os.getenv("EMAIL_SENDER", "you@gmail.com")
EMAIL_PASSWORD      = os.getenv("EMAIL_PASSWORD", "your_gmail_app_password")
EMAIL_RECIPIENT     = os.getenv("EMAIL_RECIPIENT", "teammate@example.com")

POLL_INTERVAL_SECS  = int(os.getenv("POLL_INTERVAL_SECS", "60"))   # how often to check
STATE_FILE          = "notion_state.json"                           # local cache of last-seen state

# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

groq_client = Groq(api_key=GROQ_API_KEY)


# ─────────────────────────────────────────────
# STATE HELPERS
# ─────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fingerprint(obj: dict) -> str:
    """Stable hash of a dict so we can detect changes."""
    return hashlib.md5(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


# ─────────────────────────────────────────────
# NOTION API HELPERS
# ─────────────────────────────────────────────

def query_database(db_id: str) -> list[dict]:
    """Return all pages in a Notion database."""
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    pages, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        resp = requests.post(url, headers=notion_headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        pages.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return pages


def get_page_comments(page_id: str) -> list[dict]:
    """Fetch comments on a Notion page."""
    url = f"https://api.notion.com/v1/comments?block_id={page_id}"
    resp = requests.get(url, headers=notion_headers)
    if resp.status_code == 200:
        return resp.json().get("results", [])
    return []


def get_page_metadata(page_id: str) -> dict:
    """Fetch metadata for a regular Notion page."""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    resp = requests.get(url, headers=notion_headers)
    resp.raise_for_status()
    return resp.json()


def get_page_blocks(page_id: str) -> list[dict]:
    """Fetch all block children of a page (its content)."""
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    blocks, cursor = [], None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        resp = requests.get(url, headers=notion_headers, params=params)
        if resp.status_code != 200:
            break
        data = resp.json()
        blocks.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return blocks


def extract_block_text(block: dict) -> str:
    """Pull plain text and checkbox state from a block."""
    btype = block.get("type", "")
    bdata = block.get(btype, {})
    rich  = bdata.get("rich_text", [])
    text  = "".join(r.get("plain_text", "") for r in rich)
    if btype == "to_do":
        checked = bdata.get("checked", False)
        return f"[{'x' if checked else ' '}] {text}"
    return text


def summarise_blocks(blocks: list[dict]) -> str:
    """Return a readable text summary of all page blocks."""
    lines = []
    for b in blocks:
        line = extract_block_text(b)
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


def diff_blocks(old_blocks: list[dict], new_blocks: list[dict]) -> list[str]:
    """Return human-readable list of what changed between two block snapshots."""
    old_map = {b["id"]: extract_block_text(b) for b in old_blocks}
    new_map = {b["id"]: extract_block_text(b) for b in new_blocks}
    changes = []
    for bid, new_text in new_map.items():
        if bid not in old_map:
            changes.append(f"➕ Added: {new_text}")
        elif old_map[bid] != new_text:
            changes.append(f"✏️  Changed: '{old_map[bid]}' → '{new_text}'")
    for bid in old_map:
        if bid not in new_map:
            changes.append(f"🗑️  Removed: {old_map[bid]}")
    return changes


def extract_page_summary(page: dict) -> dict:
    """Pull the most useful fields from a raw Notion page object."""
    props = page.get("properties", {})

    # Try to find a title property
    title = "Untitled"
    for prop in props.values():
        if prop.get("type") == "title":
            rich = prop["title"]
            if rich:
                title = "".join(t.get("plain_text", "") for t in rich)
            break

    return {
        "id":           page["id"],
        "title":        title,
        "url":          page.get("url", ""),
        "created_time": page.get("created_time", ""),
        "edited_time":  page.get("last_edited_time", ""),
        "properties":   {
            k: _flatten_prop(v) for k, v in props.items()
        },
    }


def _flatten_prop(prop: dict) -> str:
    """Convert a Notion property value to a readable string."""
    t = prop.get("type", "")
    val = prop.get(t)
    if val is None:
        return ""
    if t == "title" or t == "rich_text":
        return "".join(r.get("plain_text", "") for r in val)
    if t == "select":
        return val.get("name", "") if val else ""
    if t == "multi_select":
        return ", ".join(o.get("name", "") for o in val)
    if t == "status":
        return val.get("name", "") if val else ""
    if t == "checkbox":
        return str(val)
    if t == "date":
        return val.get("start", "") if val else ""
    if t == "people":
        return ", ".join(p.get("name", p.get("id", "")) for p in val)
    if t == "number":
        return str(val)
    if t == "url":
        return val or ""
    if t == "email":
        return val or ""
    return str(val)[:120]


# ─────────────────────────────────────────────
# CLAUDE SUMMARY GENERATION
# ─────────────────────────────────────────────

def generate_email_with_claude(
    event_type: str,
    page: dict,
    old_page: Optional[dict] = None,
    comments: Optional[list] = None,
    block_changes: Optional[list[str]] = None,
) -> tuple[str, str]:
    """
    Ask Claude to write a helpful email notification.
    Returns (subject, html_body).
    """
    comment_text = ""
    if comments:
        comment_text = "\n\nNew comments:\n" + "\n".join(
            f"- {c.get('created_by', {}).get('name', 'Someone')}: "
            + "".join(r.get("plain_text", "") for r in c.get("rich_text", []))
            for c in comments
        )

    change_description = ""
    if old_page and event_type == "updated" and "properties" in page:
        diffs = []
        for key in page["properties"]:
            old_val = old_page.get("properties", {}).get(key, "")
            new_val = page["properties"].get(key, "")
            if old_val != new_val:
                diffs.append(f"  • {key}: '{old_val}' → '{new_val}'")
        if diffs:
            change_description = "\n\nChanged fields:\n" + "\n".join(diffs)

    block_change_text = ""
    if block_changes:
        block_change_text = "\n\nContent changes detected:\n" + "\n".join(
            f"  {c}" for c in block_changes[:20]  # cap at 20 to avoid huge prompts
        )

    prompt = f"""You are a helpful assistant that writes concise, friendly team update emails.

A Notion workspace event just occurred:

Event type: {event_type.upper()}
Page title: {page['title']}
Page URL: {page['url']}
Last edited: {page['edited_time']}
{change_description}
{block_change_text}
{comment_text}

Write a professional but friendly email notification to a teammate.
- Subject line: short and descriptive (prefix with the event type in brackets, e.g. [Updated], [Task Done], [Comment])
- If tasks were ticked/completed, highlight that specifically
- Body: 2–4 sentences explaining what happened and why it matters
- End with the direct Notion link
- Format the body as clean HTML (use <p>, <b>, <a> tags only)

Respond ONLY as valid JSON with keys: "subject" and "body". No markdown, no backticks."""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    result = json.loads(raw)
    return result["subject"], result["body"]


# ─────────────────────────────────────────────
# EMAIL SENDING
# ─────────────────────────────────────────────

def send_email(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECIPIENT
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())

    log.info(f"📧 Email sent: {subject}")


# ─────────────────────────────────────────────
# MAIN POLLING LOOP
# ─────────────────────────────────────────────

def check_database(db_id: str, state: dict) -> dict:
    """Compare current DB state to last-known state, fire notifications for changes."""
    db_key = f"db:{db_id}"
    known_pages: dict = state.get(db_key, {})
    current_pages_raw = query_database(db_id)

    new_known = {}

    for raw_page in current_pages_raw:
        page     = extract_page_summary(raw_page)
        page_id  = page["id"]
        fp       = fingerprint(raw_page["properties"])
        new_known[page_id] = fp

        # ── NEW PAGE ──────────────────────────────────────
        if page_id not in known_pages:
            log.info(f"🆕 New page: {page['title']}")
            try:
                subject, body = generate_email_with_claude("created", page)
                send_email(subject, body)
            except Exception as e:
                log.error(f"Failed to send new-page email: {e}")

        # ── UPDATED PAGE ──────────────────────────────────
        elif known_pages[page_id] != fp:
            log.info(f"✏️  Updated page: {page['title']}")
            comments = get_page_comments(page_id)
            comment_fp_key = f"comment_count:{page_id}"
            old_comment_count = state.get(comment_fp_key, 0)
            new_comments = comments[old_comment_count:]
            state[comment_fp_key] = len(comments)
            try:
                subject, body = generate_email_with_claude(
                    "updated", page, comments=new_comments if new_comments else None
                )
                send_email(subject, body)
            except Exception as e:
                log.error(f"Failed to send update email: {e}")

        # ── COMMENT-ONLY CHANGE ───────────────────────────
        else:
            comments = get_page_comments(page_id)
            comment_fp_key = f"comment_count:{page_id}"
            old_count = state.get(comment_fp_key, 0)
            if len(comments) > old_count:
                new_comments = comments[old_count:]
                state[comment_fp_key] = len(comments)
                log.info(f"💬 New comment on: {page['title']}")
                try:
                    subject, body = generate_email_with_claude(
                        "comment", page, comments=new_comments
                    )
                    send_email(subject, body)
                except Exception as e:
                    log.error(f"Failed to send comment email: {e}")

    state[db_key] = new_known
    return state


def check_page(page_id: str, state: dict) -> dict:
    """
    Watch a regular (non-database) Notion page for:
      - last_edited_time change  → fetch blocks, diff them, send email
      - new comments
    """
    page_id = page_id.strip()
    meta_key    = f"page_meta:{page_id}"
    blocks_key  = f"page_blocks:{page_id}"
    comment_key = f"comment_count:{page_id}"

    try:
        meta = get_page_metadata(page_id)
    except Exception as e:
        log.error(f"Could not fetch page {page_id}: {e}")
        return state

    title       = meta.get("properties", {})
    # Regular pages store title under "title" property
    page_title  = "Untitled"
    for prop in title.values():
        if prop.get("type") == "title":
            page_title = "".join(t.get("plain_text", "") for t in prop["title"])
            break

    page_summary = {
        "id":          page_id,
        "title":       page_title,
        "url":         meta.get("url", f"https://notion.so/{page_id.replace('-','')}"),
        "edited_time": meta.get("last_edited_time", ""),
        "properties":  {},
    }

    old_edited  = state.get(meta_key, "")
    new_edited  = meta.get("last_edited_time", "")
    old_blocks_raw = state.get(blocks_key, [])

    # ── PAGE CONTENT CHANGED ─────────────────────────
    if new_edited != old_edited and old_edited != "":
        log.info(f"✏️  Regular page updated: {page_title}")
        new_blocks = get_page_blocks(page_id)
        changes    = diff_blocks(old_blocks_raw, new_blocks)

        # Check comments too
        comments      = get_page_comments(page_id)
        old_count     = state.get(comment_key, 0)
        new_comments  = comments[old_count:] if len(comments) > old_count else []
        state[comment_key] = len(comments)

        try:
            subject, body = generate_email_with_claude(
                "updated", page_summary,
                block_changes=changes if changes else None,
                comments=new_comments if new_comments else None,
            )
            send_email(subject, body)
        except Exception as e:
            log.error(f"Failed to send page-update email: {e}")

        state[blocks_key] = new_blocks

    # ── FIRST SEEN — just snapshot, no email ─────────
    elif old_edited == "":
        log.info(f"📸 Snapshotting page for first time: {page_title}")
        state[blocks_key] = get_page_blocks(page_id)
        comments = get_page_comments(page_id)
        state[comment_key] = len(comments)

    # ── NO CONTENT CHANGE — check comments only ───────
    else:
        comments  = get_page_comments(page_id)
        old_count = state.get(comment_key, 0)
        if len(comments) > old_count:
            new_comments = comments[old_count:]
            state[comment_key] = len(comments)
            log.info(f"💬 New comment on page: {page_title}")
            try:
                subject, body = generate_email_with_claude(
                    "comment", page_summary, comments=new_comments
                )
                send_email(subject, body)
            except Exception as e:
                log.error(f"Failed to send comment email: {e}")

    state[meta_key] = new_edited
    return state


def run():
    log.info("🚀 Notion watcher started. Polling every %ds ...", POLL_INTERVAL_SECS)
    if not NOTION_DATABASE_IDS and not NOTION_PAGE_IDS:
        log.warning("⚠️  No databases or pages configured! Set NOTION_DATABASE_IDS or NOTION_PAGE_IDS in .env")
    while True:
        state = load_state()
        try:
            for db_id in NOTION_DATABASE_IDS:
                state = check_database(db_id.strip(), state)
            for page_id in NOTION_PAGE_IDS:
                state = check_page(page_id.strip(), state)
        except Exception as e:
            log.error(f"Error during poll cycle: {e}")
        save_state(state)
        time.sleep(POLL_INTERVAL_SECS)


if __name__ == "__main__":
    run()