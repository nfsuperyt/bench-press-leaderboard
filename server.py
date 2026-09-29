from flask import Flask, request
import sqlite3
import os
import re
import time

app = Flask(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

MAX_ENTRIES = 100

# Always use a database file beside server.py.
# This prevents Render from accidentally opening a different
# database depending on the current working directory.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "leaderboard.db")


# ============================================================
# DATABASE
# ============================================================

def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def init_database():
    """
    Create the database and players table if they do not exist.
    """

    connection = get_db()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS players (
            device_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        )
    """)

    connection.commit()
    connection.close()


# IMPORTANT:
# Initialize the database when Flask starts.
#
# This fixes:
# sqlite3.OperationalError: no such table: players
init_database()


# ============================================================
# VALIDATION
# ============================================================

def clean_username(name):
    if not isinstance(name, str):
        return "Player"

    name = name.strip()

    if not name:
        return "Player"

    name = name[:20]

    # Remove control characters.
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)

    if not name:
        return "Player"

    return name


def clean_device_id(device_id):
    if not isinstance(device_id, str):
        return None

    device_id = device_id.strip()

    if len(device_id) < 10:
        return None

    if len(device_id) > 100:
        return None

    return device_id


def clean_score(score):
    try:
        score = int(score)
    except (ValueError, TypeError):
        return None

    if score < 0:
        return None

    # Prevent accidentally enormous/corrupt values.
    score = min(score, 2_000_000_000)

    return score


# ============================================================
# LEADERBOARD
# ============================================================

def get_leaderboard():
    # Make absolutely sure the table exists.
    init_database()

    connection = get_db()

    rows = connection.execute("""
        SELECT device_id, username, score, updated_at
        FROM players
        WHERE score >= 0
        ORDER BY score DESC, updated_at ASC
        LIMIT ?
    """, (MAX_ENTRIES,)).fetchall()

    connection.close()

    leaderboard = []

    for index, row in enumerate(rows, start=1):
        leaderboard.append({
            "rank": index,
            "username": row["username"],
            "score": row["score"]
        })

    return leaderboard


def get_player_rank(device_id):
    # Make absolutely sure the table exists.
    init_database()

    connection = get_db()

    row = connection.execute("""
        SELECT score
        FROM players
        WHERE device_id = ?
    """, (device_id,)).fetchone()

    if row is None:
        connection.close()
        return None

    rank_row = connection.execute("""
        SELECT COUNT(*) + 1 AS rank
        FROM players
        WHERE score > ?
    """, (row["score"],)).fetchone()

    rank = rank_row["rank"]

    connection.close()

    return rank


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/", methods=["GET"])
def index():
    return {
        "status": "online",
        "game": "Bench Press Challenge",
        "leaderboard": "online"
    }


@app.route("/health", methods=["GET"])
def health():
    return {
        "status": "ok"
    }


# ============================================================
# GET LEADERBOARD
# ============================================================

@app.route("/leaderboard", methods=["GET"])
def leaderboard_route():

    leaderboard = get_leaderboard()

    return {
        "success": True,
        "leaderboard": leaderboard
    }


# ============================================================
# SUBMIT / UPDATE PLAYER
# ============================================================

@app.route("/score", methods=["POST"])
def submit_score():

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return {
            "success": False,
            "error": "Invalid JSON."
        }, 400

    device_id = clean_device_id(
        data.get("device_id")
    )

    username = clean_username(
        data.get("username")
    )

    score = clean_score(
        data.get("score")
    )

    if device_id is None:
        return {
            "success": False,
            "error": "Invalid device ID."
        }, 400

    if score is None:
        return {
            "success": False,
            "error": "Invalid score."
        }, 400

    now = time.time()

    connection = get_db()

    existing = connection.execute("""
        SELECT score
        FROM players
        WHERE device_id = ?
    """, (device_id,)).fetchone()

    if existing is None:

        # First time this device has appeared.
        connection.execute("""
            INSERT INTO players (
                device_id,
                username,
                score,
                updated_at
            )
            VALUES (?, ?, ?, ?)
        """, (
            device_id,
            username,
            score,
            now
        ))

        stored_score = score

    else:

        old_score = int(existing["score"])

        # Keep the highest score for this device.
        stored_score = max(
            old_score,
            score
        )

        connection.execute("""
            UPDATE players
            SET username = ?,
                score = ?,
                updated_at = ?
            WHERE device_id = ?
        """, (
            username,
            stored_score,
            now,
            device_id
        ))

    connection.commit()
    connection.close()

    rank = get_player_rank(device_id)

    return {
        "success": True,
        "username": username,
        "score": stored_score,
        "rank": rank
    }


# ============================================================
# REMOVE A DEVICE
# ============================================================

@app.route("/player/<device_id>", methods=["DELETE"])
def delete_player(device_id):

    device_id = clean_device_id(device_id)

    if device_id is None:
        return {
            "success": False,
            "error": "Invalid device ID."
        }, 400

    connection = get_db()

    connection.execute("""
        DELETE FROM players
        WHERE device_id = ?
    """, (device_id,))

    connection.commit()
    connection.close()

    return {
        "success": True
    }


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    # Initialize once more before starting the local server.
    # This is harmless because IF NOT EXISTS is used.
    init_database()

    port = int(os.environ.get("PORT", 5000))

    print()
    print("==========================================")
    print(" BENCH PRESS CHALLENGE LEADERBOARD SERVER")
    print("==========================================")
    print()
    print("Database:")
    print(DATABASE)
    print()
    print("Server running on port:")
    print(port)
    print()

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
