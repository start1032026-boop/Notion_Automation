# Notion → Groq → Email Notifier

Automatically watches your Notion workspace and sends AI-written email notifications to your team whenever something changes — no manual checking required.

Built with **Python**, **Notion API**, **Groq AI**, and **Gmail SMTP**.

---

## What It Detects

| Event | Description |
|---|---|
| ✏️ Page updated | Any text, checkbox, or content change on a watched page |
| ✅ Task completed | A to-do checkbox gets ticked |
| ➕ Task added | A new line or task is added to the page |
| 🗑️ Task removed | A line is deleted from the page |
| 💬 Comment added | A new comment is posted on the page |
| 🆕 New database page | A new entry appears in a watched database |

---

## How It Works

```
Notion Page Changes
       ↓  (every 60 seconds)
  Python script detects diff
       ↓
  Groq AI writes a friendly email summary
       ↓
  Gmail sends it to your shared inbox
```

The script saves a snapshot of your page in `notion_state.json`. Every 60 seconds it compares the current state to the snapshot — if anything changed, it calls Groq to write a human-readable email and sends it instantly.

---

## Project Structure

```
notion_email_notifier/
├── notion_watcher.py     # Main script
├── .env                  # Your secrets (never commit this!)
├── .env.example          # Template for .env
├── requirements.txt      # Python dependencies
└── notion_state.json     # Auto-generated: tracks last-seen state
```

---

## Prerequisites

- Python 3.10+
- A Notion account with a page or database to watch
- A Groq account (free at [console.groq.com](https://console.groq.com))
- A Gmail account

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create a Notion Internal Integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. In the left sidebar under **"Build"**, click **"Internal integrations"**
3. Click **"New integration"**
4. Give it a name (e.g. `Email Notifier`), select your workspace
5. Click **Save** — you'll see the **Internal Integration Secret** (starts with `ntn_...`)
6. Copy it → this is your `NOTION_TOKEN`

### 3. Connect the integration to your Notion page

For **each page or database** you want to watch:
1. Open the page in Notion
2. Click `···` (three dots, top right)
3. Go to **Connections** → find your integration → click **Connect**

> ⚠️ Without this step the script will get a 404 error — Notion hides pages from integrations by default.

### 4. Get your page or database ID

**For a regular page** (like a task list doc):
```
https://www.notion.so/Your-Page-Title-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
                                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                       copy just this 32-character ID
```

**For a database:**
```
https://www.notion.so/workspace/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?v=...
                                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                 copy just this 32-character ID
```

### 5. Get a free Groq API key

1. Sign up at [console.groq.com](https://console.groq.com)
2. Go to **API Keys** → **Create API Key**
3. Copy the key (starts with `gsk_...`)

### 6. Configure your .env file

Create a `.env` file in the project folder:

```dotenv
NOTION_TOKEN=ntn_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_PAGE_IDS=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_DATABASE_IDS=

GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

EMAIL_SENDER=yourteam@gmail.com
EMAIL_PASSWORD=your_gmail_app_password
EMAIL_RECIPIENT=yourteam@gmail.com

POLL_INTERVAL_SECS=60
```

> **Tip:** If you and your teammate share one inbox, set `EMAIL_SENDER` and `EMAIL_RECIPIENT` to the same address.

> **Multiple pages/databases:** separate IDs with commas:
> `NOTION_PAGE_IDS=id1,id2,id3`

### 7. Set up Gmail

**Option A — Gmail App Password (recommended):**
1. Enable 2-Step Verification: [myaccount.google.com/security](https://myaccount.google.com/security)
2. Generate App Password: [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Name it `Notion Notifier` → copy the 16-character password (no spaces)
4. Paste into `.env` as `EMAIL_PASSWORD`

**Option B — Regular Gmail password:**
- Works if your account doesn't have 2FA enabled
- Use your normal Gmail password as `EMAIL_PASSWORD`

### 8. Run it

```bash
python notion_watcher.py
```

The first run will silently snapshot your page. From the second poll onwards, any change triggers an email.

---

## Run Automatically on Windows (Always On)

So you don't need to keep a terminal open, use **Windows Task Scheduler**:

1. Press `Windows + R` → type `taskschd.msc` → Enter
2. Click **"Create Basic Task"**
3. Fill in:
   - **Name:** `Notion Email Notifier`
   - **Trigger:** When the computer starts
   - **Action:** Start a program
   - **Program:** path to your Python (find it by running `where python`)
   - **Arguments:** `notion_watcher.py`
   - **Start in:** `D:\path\to\your\notion_email_notifier`
4. Click **Finish**
5. Right-click the task → **Properties** → **General** → check **"Run whether user is logged on or not"**

Verify it's running after a restart:
```bash
tasklist | findstr python
```

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `No databases or pages configured` | `.env` not loading | Add `from dotenv import load_dotenv` and `load_dotenv()` at top of script |
| `404 Not Found` for page | Integration not connected to page | Open Notion page → `···` → Connections → connect your integration |
| `400 Bad Request` for page | Full URL pasted instead of ID | Use only the 32-character ID from the URL, not the full link |
| `535 Username and Password not accepted` | Wrong Gmail password | Use a Gmail App Password, not your real password |
| `401 Unauthorized` from Groq | Invalid API key | Check `GROQ_API_KEY` at [console.groq.com](https://console.groq.com) |
| `Failed to resolve api.notion.com` | No internet connection | Check your network and try again |
| No emails after changes | State file is stale | Delete `notion_state.json` and restart the script |

---

## Example Email

**Subject:** `[Updated] A T: Mutual Task allocation page`

**Body:**
> Tarundeep has updated the Mutual Task allocation page. Three tasks were marked as completed and a new note was added. Check the latest state here: [Open in Notion →]

---

## Dependencies

| Package | Purpose |
|---|---|
| `groq` | AI email generation via Groq API |
| `requests` | Notion API calls |
| `python-dotenv` | Load `.env` configuration |

---

## License

MIT — free to use and modify.