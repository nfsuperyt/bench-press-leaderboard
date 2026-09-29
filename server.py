from flask import Flask, request
import sqlite3
import os
import re
import time

app = Flask(__name__)

DATABASE = "leaderboard.db"
MAX_ENTRIES = 100


# ============================================================
# DATABASE
# ============================================================

def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def init_database():
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
    connection = get_db()

    rows = connection.execute("""
        SELECT device_id, username, score
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

@app.route("/")
def index():
    return {
        "status": "online",
        "game": "Bench Press Challenge",
        "leaderboard": "online"
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

        # IMPORTANT:
        #
        # The device is the identity.
        #
        # Therefore:
        #
        # j -> Mike
        #
        # does NOT create another leaderboard position.
        #
        # It simply renames the existing device entry.
        #
        # The highest score is retained.

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
# START
# ============================================================

if __name__ == "__main__":

    init_database()

    print()
    print("==========================================")
    print(" BENCH PRESS CHALLENGE LEADERBOARD SERVER")
    print("==========================================")
    print()
    print("Server running on:")
    print("http://0.0.0.0:5000")
    print()
    print("Keep this server running while players")
    print("are playing.")
    print()

    init_database()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )

