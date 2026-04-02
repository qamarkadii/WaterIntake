from __future__ import annotations

import html
import sqlite3
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "water_tracker.db"
STATIC_DIR = BASE_DIR / "static"
DEFAULT_GOAL_ML = 2000
QUICK_ADD_OPTIONS = (150, 250, 350, 500)


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS intake_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_date TEXT NOT NULL,
                amount_ml INTEGER NOT NULL CHECK (amount_ml > 0),
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_goals (
                entry_date TEXT PRIMARY KEY,
                goal_ml INTEGER NOT NULL CHECK (goal_ml > 0)
            )
            """
        )


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def get_or_create_goal(connection: sqlite3.Connection, entry_date: str) -> int:
    row = connection.execute(
        "SELECT goal_ml FROM daily_goals WHERE entry_date = ?",
        (entry_date,),
    ).fetchone()
    if row:
        return int(row["goal_ml"])

    connection.execute(
        "INSERT INTO daily_goals (entry_date, goal_ml) VALUES (?, ?)",
        (entry_date, DEFAULT_GOAL_ML),
    )
    connection.commit()
    return DEFAULT_GOAL_ML


def get_dashboard_data() -> dict[str, object]:
    today = date.today().isoformat()
    with get_connection() as connection:
        goal_ml = get_or_create_goal(connection, today)
        total_ml = int(
            connection.execute(
                "SELECT COALESCE(SUM(amount_ml), 0) AS total FROM intake_entries WHERE entry_date = ?",
                (today,),
            ).fetchone()["total"]
        )
        entries = connection.execute(
            """
            SELECT amount_ml, created_at
            FROM intake_entries
            WHERE entry_date = ?
            ORDER BY id DESC
            """,
            (today,),
        ).fetchall()
        history_rows = connection.execute(
            """
            SELECT g.entry_date,
                   g.goal_ml,
                   COALESCE(SUM(i.amount_ml), 0) AS total_ml
            FROM daily_goals AS g
            LEFT JOIN intake_entries AS i
                ON i.entry_date = g.entry_date
            GROUP BY g.entry_date, g.goal_ml
            ORDER BY g.entry_date DESC
            LIMIT 7
            """
        ).fetchall()

    percent = min(100, round((total_ml / goal_ml) * 100)) if goal_ml else 0
    remaining_ml = max(goal_ml - total_ml, 0)

    formatted_entries = [
        {
            "amount_ml": int(row["amount_ml"]),
            "time": datetime.fromisoformat(row["created_at"]).strftime("%I:%M %p"),
        }
        for row in entries
    ]
    history = [
        {
            "entry_date": row["entry_date"],
            "goal_ml": int(row["goal_ml"]),
            "total_ml": int(row["total_ml"]),
            "percent": min(100, round((int(row["total_ml"]) / int(row["goal_ml"])) * 100))
            if int(row["goal_ml"])
            else 0,
        }
        for row in history_rows
    ]

    return {
        "today": today,
        "goal_ml": goal_ml,
        "total_ml": total_ml,
        "remaining_ml": remaining_ml,
        "percent": percent,
        "entries": formatted_entries,
        "history": history,
    }


def add_water(amount_ml: int) -> None:
    today = date.today().isoformat()
    timestamp = datetime.now().isoformat(timespec="seconds")
    with get_connection() as connection:
        get_or_create_goal(connection, today)
        connection.execute(
            "INSERT INTO intake_entries (entry_date, amount_ml, created_at) VALUES (?, ?, ?)",
            (today, amount_ml, timestamp),
        )
        connection.commit()


def update_goal(goal_ml: int) -> None:
    today = date.today().isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO daily_goals (entry_date, goal_ml)
            VALUES (?, ?)
            ON CONFLICT(entry_date) DO UPDATE SET goal_ml = excluded.goal_ml
            """,
            (today, goal_ml),
        )
        connection.commit()


def reset_today() -> None:
    today = date.today().isoformat()
    with get_connection() as connection:
        connection.execute("DELETE FROM intake_entries WHERE entry_date = ?", (today,))
        connection.commit()


def render_page(data: dict[str, object]) -> str:
    entries_html = "".join(
        f"""
        <li class="entry-item">
            <span>{entry["amount_ml"]} ml</span>
            <span>{html.escape(entry["time"])}</span>
        </li>
        """
        for entry in data["entries"]
    )
    if not entries_html:
        entries_html = """
        <li class="entry-item entry-empty">
            <span>No water logged yet</span>
            <span>Start with one sip</span>
        </li>
        """

    history_html = "".join(
        f"""
        <li class="history-item">
            <div>
                <strong>{html.escape(row["entry_date"])}</strong>
                <span>{row["total_ml"]} / {row["goal_ml"]} ml</span>
            </div>
            <div>{row["percent"]}%</div>
        </li>
        """
        for row in data["history"]
    )

    if not history_html:
        history_html = """
        <li class="history-item history-empty">
            <div>
                <strong>No history yet</strong>
                <span>Your last 7 days will appear here.</span>
            </div>
            <div>--</div>
        </li>
        """

    quick_add_buttons = "".join(
        f"""
        <form method="post" action="/add">
            <input type="hidden" name="amount_ml" value="{amount}">
            <button class="quick-button" type="submit">+{amount} ml</button>
        </form>
        """
        for amount in QUICK_ADD_OPTIONS
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mizu Flow</title>
    <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
    <div class="backdrop"></div>
    <main class="page-shell">
        <section class="hero-card">
            <div class="hero-copy">
                <p class="eyebrow">Suibun Hokyu | Water Ritual</p>
                <h1>Mizu Flow</h1>
                <p class="subtitle">A blue floral water tracker with a calm Japanese-inspired atmosphere.</p>
            </div>
            <div class="hero-stats">
                <p class="stat-kicker">Today</p>
                <div class="stat-value">{data["total_ml"]} <span>ml</span></div>
                <p class="stat-note">{data["remaining_ml"]} ml left to reach {data["goal_ml"]} ml</p>
                <div class="progress-ring">
                    <div class="progress-ring__inner">
                        <strong>{data["percent"]}%</strong>
                        <span>of goal</span>
                    </div>
                </div>
            </div>
        </section>

        <section class="grid">
            <article class="panel panel-primary">
                <div class="panel-header">
                    <div>
                        <p class="eyebrow">Kyo no Kiroku</p>
                        <h2>Track Your Intake</h2>
                    </div>
                    <p>{html.escape(str(data["today"]))}</p>
                </div>

                <div class="meter">
                    <div class="meter-fill" style="width: {data["percent"]}%"></div>
                </div>

                <div class="quick-grid">
                    {quick_add_buttons}
                </div>

                <form class="custom-form" method="post" action="/add">
                    <label for="amount_ml">Custom amount</label>
                    <div class="input-row">
                        <input id="amount_ml" name="amount_ml" type="number" min="1" step="10" placeholder="Enter ml" required>
                        <button type="submit">Add Water</button>
                    </div>
                </form>
            </article>

            <article class="panel">
                <div class="panel-header">
                    <div>
                        <p class="eyebrow">Mokuhyo</p>
                        <h2>Daily Goal</h2>
                    </div>
                </div>

                <form class="stack-form" method="post" action="/goal">
                    <label for="goal_ml">Goal in milliliters</label>
                    <input id="goal_ml" name="goal_ml" type="number" min="250" step="50" value="{data["goal_ml"]}" required>
                    <button type="submit">Update Goal</button>
                </form>

                <form class="reset-form" method="post" action="/reset">
                    <button class="ghost-button" type="submit">Reset Today</button>
                </form>
            </article>

            <article class="panel">
                <div class="panel-header">
                    <div>
                        <p class="eyebrow">Shizuku</p>
                        <h2>Recent Sips</h2>
                    </div>
                </div>
                <ul class="entry-list">
                    {entries_html}
                </ul>
            </article>

            <article class="panel">
                <div class="panel-header">
                    <div>
                        <p class="eyebrow">Rireki</p>
                        <h2>7-Day History</h2>
                    </div>
                </div>
                <ul class="history-list">
                    {history_html}
                </ul>
            </article>
        </section>
    </main>
</body>
</html>
"""


class WaterTrackerHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.respond_html(render_page(get_dashboard_data()))
            return

        if parsed.path == "/static/styles.css":
            stylesheet = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
            self.respond(stylesheet.encode("utf-8"), "text/css; charset=utf-8")
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Page not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8")
        form = parse_qs(raw_body)

        try:
            if parsed.path == "/add":
                amount_ml = int(form.get("amount_ml", ["0"])[0])
                if amount_ml <= 0:
                    raise ValueError
                add_water(amount_ml)
                self.redirect("/")
                return

            if parsed.path == "/goal":
                goal_ml = int(form.get("goal_ml", ["0"])[0])
                if goal_ml <= 0:
                    raise ValueError
                update_goal(goal_ml)
                self.redirect("/")
                return

            if parsed.path == "/reset":
                reset_today()
                self.redirect("/")
                return

        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid form value")
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Page not found")

    def log_message(self, format: str, *args: object) -> None:
        return

    def respond_html(self, body: str) -> None:
        self.respond(body.encode("utf-8"), "text/html; charset=utf-8")

    def respond(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()


def main() -> None:
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", 8000), WaterTrackerHandler)
    print("Mizu Flow is running at http://127.0.0.1:8000")
    server.serve_forever()


if __name__ == "__main__":
    main()
