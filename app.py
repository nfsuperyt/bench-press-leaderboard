import pygame
import random
import sys
import os
import uuid
import time
import requests
import threading
import queue


# ============================================================
# PYGAME SETUP
# ============================================================

pygame.init()

W, H = 1000, 650

screen = pygame.display.set_mode((W, H))
pygame.display.set_caption("Bench Press Challenge")

clock = pygame.time.Clock()

FONT = pygame.font.SysFont("arial", 28)
BIG = pygame.font.SysFont("arial", 58, bold=True)
SMALL = pygame.font.SysFont("arial", 20)
TINY = pygame.font.SysFont("arial", 17)


# ============================================================
# CONSTANTS
# ============================================================

TOP_Y = 225
CHEST_Y = 405

MIN_WEIGHT = 20
MAX_WEIGHT = 1000

HIGH_WEIGHT_UNLOCK_SCORE = 35000

MAX_LEADERBOARD_ENTRIES = 100

SERVER_URL = "https://bench-press-leaderboard.onrender.com"
SERVER_TIMEOUT = 30

LEADERBOARD_REFRESH_TIME = 10.0
SCORE_UPLOAD_TIME = 10.0

NETWORK_RETRY_DELAY = 5.0


# ============================================================
# CLAP
# ============================================================

CLAP_HALF_DURATION = 0.175
CLAP_DURATION = 0.350
CLAP_MAX_MOVEMENT = 55


# ============================================================
# ACTION REWARDS
#
# THESE ARE THE ONLY SCORE REWARDS.
#
# Normal rep:
#     weight
#
# Throw + catch:
#     weight * 0.5
#
# Throw + clap + catch:
#     weight
#
# Examples at 300 kg:
#
#     Normal rep           = 300
#     Throw + catch        = 150
#     Throw + clap + catch = 300
#
# IMPORTANT:
#
# A throw does NOT receive the normal-rep reward.
# A clap throw does NOT receive the normal throw reward.
# Each successful action receives exactly ONE reward.
# ============================================================

NORMAL_REP_SCORE_MULTIPLIER = 1.0
NORMAL_REP_STAMINA_REWARD = 0

THROW_SCORE_MULTIPLIER = 0.5
THROW_STAMINA_REWARD = 15

CLAP_PRESS_SCORE_MULTIPLIER = 1.0
CLAP_PRESS_STAMINA_REWARD = 30


# ============================================================
# LOCAL PLAYER DATA
# ============================================================

DEVICE_ID_FILE = "bench_press_device_id.txt"
BEST_SCORE_FILE = "bench_press_best_score.txt"


def get_device_id():

    if os.path.exists(DEVICE_ID_FILE):

        try:

            with open(
                DEVICE_ID_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                value = f.read().strip()

                if len(value) >= 10:
                    return value

        except OSError:
            pass

    value = str(uuid.uuid4())

    try:

        with open(
            DEVICE_ID_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(value)

    except OSError:
        pass

    return value


def load_local_best_score():

    if not os.path.exists(BEST_SCORE_FILE):
        return 0

    try:

        with open(
            BEST_SCORE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return max(
                0,
                int(f.read().strip())
            )

    except (
        OSError,
        ValueError,
        TypeError
    ):

        return 0


def save_local_best_score(value):

    value = max(
        0,
        int(value)
    )

    try:

        with open(
            BEST_SCORE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(str(value))

    except OSError:
        pass


DEVICE_ID = get_device_id()
local_best_score = load_local_best_score()


# ============================================================
# USERNAME
# ============================================================

username = ""
username_input = ""


def clean_username(name):

    if not isinstance(name, str):
        return "Player"

    cleaned = ""

    for char in name.strip()[:20]:

        code = ord(char)

        if code >= 32 and code != 127:
            cleaned += char

    cleaned = cleaned.strip()

    return cleaned if cleaned else "Player"


def get_server_username(item):

    if not isinstance(item, dict):
        return "Player"

    possible_names = (
        item.get("username"),
        item.get("name"),
        item.get("player_name")
    )

    for value in possible_names:

        if isinstance(value, str):

            cleaned = clean_username(value)

            if cleaned != "Player":
                return cleaned

    return "Player"


# ============================================================
# NETWORK STATE
# ============================================================

online_leaderboard = []

online_connected = False
online_status_message = "OFFLINE"

online_best_score = 0
online_rank = None

network_queue = queue.Queue()
network_lock = threading.Lock()

network_running = True

leaderboard_request_pending = False
score_request_pending = False

next_leaderboard_retry = 0.0
next_score_retry = 0.0

last_successful_connection = 0.0

last_leaderboard_request = 0.0
last_score_request = 0.0

last_uploaded_score = 0


def server_url(path):

    return SERVER_URL.rstrip("/") + path


# ============================================================
# NETWORK STATUS
# ============================================================

def set_status(connected, message):

    global online_connected
    global online_status_message

    with network_lock:

        online_connected = connected
        online_status_message = message


def mark_connected():

    global online_connected
    global online_status_message
    global last_successful_connection

    with network_lock:

        online_connected = True
        online_status_message = "CONNECTED"
        last_successful_connection = time.time()


def network_failure(task_type, message):

    global next_leaderboard_retry
    global next_score_retry

    set_status(
        False,
        message
    )

    retry = time.time() + NETWORK_RETRY_DELAY

    if task_type == "leaderboard":
        next_leaderboard_retry = retry

    elif task_type == "score":
        next_score_retry = retry


# ============================================================
# NETWORK WORKER
# ============================================================

def network_worker():

    global online_leaderboard
    global online_best_score
    global online_rank

    global leaderboard_request_pending
    global score_request_pending

    global next_leaderboard_retry
    global next_score_retry

    global last_uploaded_score
    global local_best_score

    session = requests.Session()

    session.headers.update({
        "User-Agent": "BenchPressChallenge/6.0"
    })

    while network_running:

        try:

            task = network_queue.get(
                timeout=0.25
            )

        except queue.Empty:

            continue

        task_type = task.get("type")

        try:

            # ====================================================
            # LEADERBOARD REQUEST
            # ====================================================

            if task_type == "leaderboard":

                set_status(
                    False,
                    "CONNECTING..."
                )

                response = session.get(
                    server_url("/leaderboard"),
                    timeout=SERVER_TIMEOUT
                )

                if response.status_code != 200:

                    network_failure(
                        "leaderboard",
                        f"SERVER {response.status_code}"
                    )

                    continue

                try:

                    data = response.json()

                except ValueError:

                    network_failure(
                        "leaderboard",
                        "BAD SERVER DATA"
                    )

                    continue

                if not isinstance(data, dict):

                    network_failure(
                        "leaderboard",
                        "BAD SERVER DATA"
                    )

                    continue

                if not data.get("success", False):

                    network_failure(
                        "leaderboard",
                        "SERVER ERROR"
                    )

                    continue

                received = data.get(
                    "leaderboard",
                    []
                )

                if not isinstance(received, list):
                    received = []

                with network_lock:

                    online_leaderboard = list(
                        received
                    )

                server_player_best = 0

                for item in received:

                    if not isinstance(item, dict):
                        continue

                    item_device = str(
                        item.get(
                            "device_id",
                            ""
                        )
                    ).strip()

                    item_name = get_server_username(
                        item
                    )

                    try:

                        item_score = int(
                            item.get(
                                "score",
                                0
                            )
                        )

                    except (
                        ValueError,
                        TypeError
                    ):

                        continue

                    same_device = (
                        bool(item_device)
                        and item_device == DEVICE_ID
                    )

                    same_name = (
                        bool(username)
                        and item_name.lower()
                        == clean_username(
                            username
                        ).lower()
                    )

                    if same_device or same_name:

                        server_player_best = max(
                            server_player_best,
                            item_score
                        )

                if server_player_best > local_best_score:

                    local_best_score = (
                        server_player_best
                    )

                    save_local_best_score(
                        local_best_score
                    )

                with network_lock:

                    online_best_score = max(
                        online_best_score,
                        server_player_best
                    )

                mark_connected()

                next_leaderboard_retry = 0.0

            # ====================================================
            # SCORE REQUEST
            # ====================================================

            elif task_type == "score":

                player_score = task.get(
                    "score",
                    0
                )

                try:

                    player_score = int(
                        player_score
                    )

                except (
                    ValueError,
                    TypeError
                ):

                    player_score = 0

                player_name = clean_username(
                    task.get(
                        "username",
                        "Player"
                    )
                )

                update_name_only = bool(
                    task.get(
                        "update_name_only",
                        False
                    )
                )

                if player_score <= 0:

                    score_request_pending = False

                    continue

                with network_lock:

                    known_best = max(
                        local_best_score,
                        online_best_score,
                        last_uploaded_score
                    )

                if (
                    player_score <= known_best
                    and not update_name_only
                ):

                    score_request_pending = False

                    continue

                set_status(
                    False,
                    "UPLOADING..."
                )

                response = session.post(
                    server_url("/score"),
                    json={
                        "device_id": DEVICE_ID,
                        "username": player_name,
                        "score": player_score
                    },
                    timeout=SERVER_TIMEOUT
                )

                if response.status_code != 200:

                    network_failure(
                        "score",
                        f"SERVER {response.status_code}"
                    )

                    continue

                try:

                    data = response.json()

                except ValueError:

                    network_failure(
                        "score",
                        "BAD SCORE DATA"
                    )

                    continue

                if not isinstance(data, dict):

                    network_failure(
                        "score",
                        "BAD SCORE DATA"
                    )

                    continue

                if not data.get("success", False):

                    network_failure(
                        "score",
                        "UPLOAD FAILED"
                    )

                    continue

                try:

                    server_score = int(
                        data.get(
                            "score",
                            player_score
                        )
                    )

                except (
                    ValueError,
                    TypeError
                ):

                    server_score = player_score

                with network_lock:

                    online_best_score = max(
                        online_best_score,
                        server_score
                    )

                    online_rank = data.get(
                        "rank"
                    )

                local_best_score = max(
                    local_best_score,
                    player_score,
                    server_score
                )

                save_local_best_score(
                    local_best_score
                )

                last_uploaded_score = max(
                    last_uploaded_score,
                    player_score,
                    server_score
                )

                mark_connected()

                next_score_retry = 0.0

                with network_lock:

                    leaderboard_busy = (
                        leaderboard_request_pending
                    )

                if not leaderboard_busy:

                    try:

                        leaderboard_request_pending = True

                        network_queue.put_nowait({
                            "type": "leaderboard"
                        })

                    except Exception:

                        leaderboard_request_pending = False

            else:

                set_status(
                    False,
                    "NETWORK ERROR"
                )

        except requests.Timeout:

            network_failure(
                task_type,
                "SERVER SLOW"
            )

        except requests.ConnectionError:

            network_failure(
                task_type,
                "OFFLINE"
            )

        except requests.RequestException:

            network_failure(
                task_type,
                "NETWORK ERROR"
            )

        except Exception:

            network_failure(
                task_type,
                "OFFLINE"
            )

        finally:

            if task_type == "leaderboard":
                leaderboard_request_pending = False

            elif task_type == "score":
                score_request_pending = False

            try:

                network_queue.task_done()

            except ValueError:
                pass

    session.close()


network_thread = threading.Thread(
    target=network_worker,
    daemon=True
)

network_thread.start()


# ============================================================
# NETWORK REQUESTS
# ============================================================

def get_online_leaderboard(force=False):

    global leaderboard_request_pending

    now = time.time()

    if (
        not force
        and now < next_leaderboard_retry
    ):
        return

    if leaderboard_request_pending:
        return

    leaderboard_request_pending = True

    try:

        network_queue.put_nowait({
            "type": "leaderboard"
        })

    except Exception:

        leaderboard_request_pending = False


def upload_score(force=False):

    global score_request_pending

    if not username:
        return

    current_score = int(score)

    if current_score <= 0:
        return

    with network_lock:

        known_best = max(
            local_best_score,
            online_best_score,
            last_uploaded_score
        )

    if current_score <= known_best:
        return

    now = time.time()

    if (
        not force
        and now < next_score_retry
    ):
        return

    if score_request_pending:
        return

    score_request_pending = True

    try:

        network_queue.put_nowait({
            "type": "score",
            "device_id": DEVICE_ID,
            "username": clean_username(username),
            "score": current_score,
            "update_name_only": False
        })

    except Exception:

        score_request_pending = False


def upload_username():

    if not username:
        return

    player_name = clean_username(
        username
    )

    try:

        player_score = get_player_best()

    except Exception:

        player_score = local_best_score

    player_score = max(
        1,
        int(player_score)
    )

    try:

        network_queue.put_nowait({
            "type": "score",
            "device_id": DEVICE_ID,
            "username": player_name,
            "score": player_score,
            "update_name_only": True
        })

    except Exception:
        pass


def update_online_data():

    global last_leaderboard_request
    global last_score_request

    now = time.time()

    if (
        now - last_leaderboard_request
        >= LEADERBOARD_REFRESH_TIME
    ):

        get_online_leaderboard()

        last_leaderboard_request = now

    if (
        state == "playing"
        and username
        and score > 0
        and score > local_best_score
        and (
            now - last_score_request
            >= SCORE_UPLOAD_TIME
        )
    ):

        upload_score()

        last_score_request = now


# ============================================================
# LEADERBOARD
# ============================================================

def get_sorted_leaderboard():

    with network_lock:

        received = list(
            online_leaderboard
        )

    players_by_device = {}
    players_without_device = {}

    for item in received:

        if not isinstance(item, dict):
            continue

        name = get_server_username(
            item
        )

        try:

            score_value = int(
                item.get(
                    "score",
                    0
                )
            )

        except (
            ValueError,
            TypeError
        ):

            continue

        if score_value < 0:
            continue

        device_id = str(
            item.get(
                "device_id",
                ""
            )
        ).strip()

        try:

            supplied_rank = int(
                item.get(
                    "rank",
                    999999
                )
            )

        except (
            ValueError,
            TypeError
        ):

            supplied_rank = 999999

        candidate = {
            "name": name,
            "score": score_value,
            "rank": supplied_rank,
            "device_id": device_id
        }

        if device_id:

            existing = players_by_device.get(
                device_id
            )

            if (
                existing is None
                or score_value > existing["score"]
            ):

                players_by_device[
                    device_id
                ] = candidate

            elif (
                score_value == existing["score"]
                and supplied_rank < existing["rank"]
            ):

                players_by_device[
                    device_id
                ] = candidate

        else:

            username_key = name.lower()

            existing = players_without_device.get(
                username_key
            )

            if (
                existing is None
                or score_value > existing["score"]
            ):

                players_without_device[
                    username_key
                ] = candidate

            elif (
                score_value == existing["score"]
                and supplied_rank < existing["rank"]
            ):

                players_without_device[
                    username_key
                ] = candidate

    entries = list(
        players_by_device.values()
    )

    entries.extend(
        players_without_device.values()
    )

    entries.sort(
        key=lambda item: (
            -item["score"],
            item["rank"],
            item["name"].lower()
        )
    )

    for index, item in enumerate(entries):

        item["display_rank"] = index + 1

    return entries[:MAX_LEADERBOARD_ENTRIES]


def get_player_best():

    best = max(
        0,
        int(local_best_score)
    )

    with network_lock:

        best = max(
            best,
            int(online_best_score)
        )

    player_device = DEVICE_ID

    player_name = clean_username(
        username
    ).lower()

    for item in get_sorted_leaderboard():

        same_device = (
            bool(item["device_id"])
            and item["device_id"]
            == player_device
        )

        same_name = (
            item["name"].lower()
            == player_name
        )

        if same_device or same_name:

            best = max(
                best,
                item["score"]
            )

    return best


def get_player_rank():

    entries = get_sorted_leaderboard()

    player_device = DEVICE_ID

    player_name = clean_username(
        username
    ).lower()

    for item in entries:

        same_device = (
            bool(item["device_id"])
            and item["device_id"]
            == player_device
        )

        same_name = (
            item["name"].lower()
            == player_name
        )

        if same_device or same_name:

            return item["display_rank"]

    with network_lock:

        server_rank = online_rank

    if server_rank is not None:

        try:

            return int(server_rank)

        except (
            ValueError,
            TypeError
        ):
            pass

    return None


# ============================================================
# GAME CONSTANTS
# ============================================================

TRICEP_TEAR_TIME = 0.40
PRESS_IDLE_GRACE = 1.0

REBOUND_RISE_TIME = 0.30
REBOUND_DISTANCE = 35

CHEST_PRESS_DELAY = 0.15

THROW_INITIAL_VELOCITY = -750
THROW_GRAVITY = 1800

HAND_Y = TOP_Y

CATCH_TOP_Y = HAND_Y - 50
CATCH_BOTTOM_Y = HAND_Y + 50

STARTING_STAMINA = 100.0

STAMINA_PER_TAP = 1.0
STAMINA_REGEN_PERCENT = 0.01
STAMINA_REGEN_LIMIT = 0.50


# ============================================================
# WEIGHTS
# ============================================================

WEIGHTS_PAGE_1 = [
    20, 40, 60, 80, 100,
    120, 140, 160, 180, 200,
    220, 240, 260, 280, 300,
    320, 340, 360, 380, 400,
    420, 440, 460, 480, 500
]

WEIGHTS_PAGE_2 = [
    520, 540, 560, 580, 600,
    620, 640, 660, 680, 700,
    720, 740, 760, 780, 800,
    820, 840, 860, 880, 900,
    920, 940, 960, 980, 1000
]


# ============================================================
# TAP REQUIREMENTS
# ============================================================

def get_required_taps(weight):

    known = {
        20: 9,
        100: 15,
        200: 25,
        300: 35,
        400: 50,
        500: 75,
        600: 100,
        700: 125,
        800: 150,
        900: 175,
        1000: 200
    }

    if weight in known:
        return known[weight]

    if weight <= 500:

        points = [
            20,
            100,
            200,
            300,
            400,
            500
        ]

        for i in range(
            len(points) - 1
        ):

            low = points[i]
            high = points[i + 1]

            if low < weight < high:

                ratio = (
                    (weight - low)
                    / (high - low)
                )

                return int(
                    round(
                        known[low]
                        + (
                            known[high]
                            - known[low]
                        ) * ratio
                    )
                )

    ratio = (
        (weight - 500)
        / 500
    )

    return int(
        round(
            75
            + 125 * ratio
        )
    )


# ============================================================
# CHALLENGE DIFFICULTY
# ============================================================

def get_weight_difficulty(
    challenge_weight,
    action
):

    if action == "clap_press":

        if challenge_weight <= 100:
            return "medium"

        elif challenge_weight <= 240:
            return "hard"

        elif challenge_weight <= 400:
            return "insane"

        elif challenge_weight <= 540:
            return "hell"

        elif challenge_weight <= 700:
            return "depression"

        elif challenge_weight <= 840:
            return "demonic"

        else:
            return "impossible"

    if action == "throw":

        if challenge_weight <= 100:
            return "easy"

        elif challenge_weight <= 240:
            return "medium"

        elif challenge_weight <= 400:
            return "hard"

        elif challenge_weight <= 540:
            return "insane"

        elif challenge_weight <= 700:
            return "hell"

        elif challenge_weight <= 840:
            return "depression"

        else:
            return "demonic"

    if action == "rep":

        if challenge_weight <= 100:
            return "easy"

        elif challenge_weight <= 240:
            return "medium"

        elif challenge_weight <= 400:
            return "hard"

        elif challenge_weight <= 700:
            return "insane"

        elif challenge_weight <= 840:
            return "demonic"

        else:
            return "demonic"

    return "easy"


# ============================================================
# CHALLENGES
# ============================================================

CHALLENGE_INTERVAL = 4
CHALLENGE_MESSAGE_DURATION = 7.0

challenge = None
challenge_progress = 0
challenge_target = 0
challenge_weight = 0
challenge_difficulty = ""

challenge_message = ""
challenge_message_timer = 0.0


def create_clap_press_challenge():

    global challenge
    global challenge_progress
    global challenge_target
    global challenge_weight
    global challenge_difficulty
    global challenge_message
    global challenge_message_timer

    challenge = "clap_press"

    if score >= HIGH_WEIGHT_UNLOCK_SCORE:

        challenge_weight = random.randrange(
            20,
            1001,
            20
        )

    else:

        challenge_weight = random.randrange(
            20,
            501,
            20
        )

    challenge_difficulty = get_weight_difficulty(
        challenge_weight,
        "clap_press"
    )

    challenge_target = random.randint(
        4,
        8
    )

    challenge_progress = 0

    challenge_message = (
        f"{challenge_difficulty.upper()} "
        f"CLAP PRESS CHALLENGE! "
        f"4-8 CLAP PRESSES"
    )

    challenge_message_timer = (
        CHALLENGE_MESSAGE_DURATION
    )


def create_normal_challenge():

    global challenge
    global challenge_progress
    global challenge_target
    global challenge_weight
    global challenge_difficulty
    global challenge_message
    global challenge_message_timer

    unlocked = (
        score >= HIGH_WEIGHT_UNLOCK_SCORE
    )

    if unlocked:

        difficulty = random.choice([
            "easy",
            "medium",
            "hard",
            "insane",
            "hell",
            "depression",
            "demonic"
        ])

    else:

        difficulty = random.choice([
            "easy",
            "medium",
            "hard",
            "insane"
        ])

    challenge_difficulty = difficulty

    if difficulty == "easy":

        challenge = random.choice([
            "rep",
            "throw"
        ])

        challenge_weight = random.randrange(
            20,
            101,
            20
        )

        challenge_target = random.randint(
            2,
            3
        )

    elif difficulty == "medium":

        challenge = random.choice([
            "rep",
            "throw"
        ])

        challenge_weight = random.randrange(
            120,
            241,
            20
        )

        challenge_target = random.randint(
            3,
            5
        )

    elif difficulty == "hard":

        challenge = random.choice([
            "rep",
            "throw"
        ])

        challenge_weight = random.randrange(
            260,
            401,
            20
        )

        challenge_target = random.randint(
            4,
            6
        )

    elif difficulty == "insane":

        challenge = random.choice([
            "rep",
            "throw"
        ])

        if unlocked:

            challenge_weight = random.randrange(
                420,
                541,
                20
            )

        else:

            challenge_weight = random.randrange(
                420,
                501,
                20
            )

        challenge_target = random.randint(
            5,
            8
        )

    elif difficulty == "hell":

        challenge = random.choice([
            "rep",
            "throw"
        ])

        challenge_weight = random.randrange(
            560,
            701,
            20
        )

        challenge_target = random.randint(
            3,
            5
        )

    elif difficulty == "depression":

        challenge = random.choice([
            "rep",
            "throw"
        ])

        challenge_weight = random.randrange(
            720,
            841,
            20
        )

        challenge_target = random.randint(
            4,
            6
        )

    elif difficulty == "demonic":

        challenge = random.choice([
            "rep",
            "throw"
        ])

        challenge_weight = random.randrange(
            860,
            1001,
            20
        )

        challenge_target = random.randint(
            5,
            8
        )

    challenge_progress = 0

    challenge_message = (
        f"{challenge_difficulty.upper()} CHALLENGE!"
    )

    challenge_message_timer = (
        CHALLENGE_MESSAGE_DURATION
    )


def create_challenge():

    challenge_type = random.choice([
        "normal",
        "normal",
        "clap_press"
    ])

    if challenge_type == "clap_press":

        create_clap_press_challenge()

    else:

        create_normal_challenge()


def get_challenge_reward_text():

    rewards = {
        "easy": "+50% STAMINA",
        "medium": "FULL STAMINA REFILL",
        "hard": "+50 MAX STAMINA + FULL REFILL",
        "insane": "+100 MAX STAMINA + FULL REFILL",
        "hell": "+150 MAX STAMINA + FULL REFILL",
        "depression": "+200 MAX STAMINA + FULL REFILL",
        "demonic": "+250 MAX STAMINA + FULL REFILL",
        "impossible": "+300 MAX STAMINA + FULL REFILL"
    }

    return rewards.get(
        challenge_difficulty,
        ""
    )


def complete_challenge():

    global challenge
    global challenge_progress
    global challenge_message
    global challenge_message_timer
    global stamina
    global max_stamina
    global score

    rewards = {
        "easy": 500,
        "medium": 1000,
        "hard": 1500,
        "insane": 2000,
        "hell": 3000,
        "depression": 4000,
        "demonic": 5000,
        "impossible": 6000
    }

    if challenge_difficulty == "easy":

        stamina = min(
            max_stamina,
            stamina + max_stamina * 0.50
        )

    elif challenge_difficulty == "medium":

        stamina = max_stamina

    elif challenge_difficulty == "hard":

        max_stamina += 50
        stamina = max_stamina

    elif challenge_difficulty == "insane":

        max_stamina += 100
        stamina = max_stamina

    elif challenge_difficulty == "hell":

        max_stamina += 150
        stamina = max_stamina

    elif challenge_difficulty == "depression":

        max_stamina += 200
        stamina = max_stamina

    elif challenge_difficulty == "demonic":

        max_stamina += 250
        stamina = max_stamina

    elif challenge_difficulty == "impossible":

        max_stamina += 300
        stamina = max_stamina

    score += rewards[
        challenge_difficulty
    ]

    stamina = max(
        0,
        min(
            stamina,
            max_stamina
        )
    )

    challenge_message = (
        "CHALLENGE COMPLETE! "
        + get_challenge_reward_text()
        + f" +{rewards[challenge_difficulty]} SCORE"
    )

    challenge_message_timer = (
        CHALLENGE_MESSAGE_DURATION
    )

    challenge = None
    challenge_progress = 0

    upload_score(
        force=True
    )


def update_challenge_display():

    if challenge is None:
        return ""

    if challenge == "rep":
        action = "REP"

    elif challenge == "throw":
        action = "THROW"

    elif challenge == "clap_press":
        action = "CLAP PRESS"

    else:
        action = str(
            challenge
        ).upper()

    return (
        f"{challenge_difficulty.upper()}: "
        f"{action} {challenge_weight} kg "
        f"{challenge_progress}/{challenge_target}"
    )


def register_challenge_rep():

    global challenge_progress

    if challenge != "rep":
        return

    if weight != challenge_weight:
        return

    challenge_progress += 1

    if challenge_progress >= challenge_target:

        complete_challenge()


def register_challenge_throw():

    global challenge_progress

    if challenge != "throw":
        return

    if weight != challenge_weight:
        return

    challenge_progress += 1

    if challenge_progress >= challenge_target:

        complete_challenge()


def register_challenge_clap_press():

    global challenge_progress

    if challenge != "clap_press":
        return

    if weight != challenge_weight:
        return

    challenge_progress += 1

    if challenge_progress >= challenge_target:

        complete_challenge()


# ============================================================
# ACTION REWARD SYSTEM
#
# THIS IS THE IMPORTANT FIX.
#
# Every action has exactly ONE reward function.
# ============================================================

def reward_normal_rep():

    global score
    global stamina

    # NORMAL REP
    #
    # 300 kg -> +300 points
    #
    # No stamina reward.

    score += int(
        weight
        * NORMAL_REP_SCORE_MULTIPLIER
    )

    stamina = min(
        max_stamina,
        stamina + NORMAL_REP_STAMINA_REWARD
    )


def reward_throw_catch():

    global score
    global stamina

    # THROW + CATCH
    #
    # 300 kg -> +150 points
    #
    # This is deliberately ONLY 50% of the weight.
    #
    # DO NOT add weight again.
    # DO NOT call reward_normal_rep().

    score += int(
        weight
        * THROW_SCORE_MULTIPLIER
    )

    stamina = min(
        max_stamina,
        stamina + THROW_STAMINA_REWARD
    )


def reward_clap_press():

    global score
    global stamina

    # THROW + CLAP + CATCH
    #
    # 300 kg -> +300 points
    #
    # This is deliberately ONLY 100% of the weight.
    #
    # DO NOT call reward_throw_catch().
    # DO NOT call reward_normal_rep().

    score += int(
        weight
        * CLAP_PRESS_SCORE_MULTIPLIER
    )

    stamina = min(
        max_stamina,
        stamina + CLAP_PRESS_STAMINA_REWARD
    )


# ============================================================
# GAME STATE
# ============================================================

weight = 20

score = 0
reps = 0

max_stamina = STARTING_STAMINA
stamina = STARTING_STAMINA

bar_y = TOP_Y
bar_v = 0.0

phase = "holding"
state = "username"

tap_count = 0
required_taps = get_required_taps(weight)

press_timer = 0.0
press_idle_timer = 0.0
chest_delay_timer = 0.0

rebound_timer = 0.0
rebound_active = False

failed_press = False
muscle_torn = False
failure_cause = ""

throwing = False

throw_catch_window_active = False
throw_catch_window_timer = 0.0
throw_catch_attempted = False

throw_reached_apex = False
throw_start_y = TOP_Y

throw_clap_completed = False

clapping = False
clap_timer = 0.0

crushing = False
crush_timer = 0.0

message = ""

weight_menu_open = False
weight_page = 1

leaderboard_page = False
leaderboard_scroll = 0

throw_unlocked = True


# ============================================================
# ARM
# ============================================================

BASE_ARM_SIZE = 15


def get_arm_size():

    return BASE_ARM_SIZE + (
        score // 500
    )


# ============================================================
# CLAP
# ============================================================

def start_clap():

    global clapping
    global clap_timer

    if clapping:
        return

    if not throwing:
        return

    if throw_clap_completed:
        return

    clapping = True
    clap_timer = 0.0


def update_clap(dt):

    global clapping
    global clap_timer
    global throw_clap_completed

    if not clapping:
        return

    clap_timer += dt

    if clap_timer >= CLAP_DURATION:

        clap_timer = CLAP_DURATION
        clapping = False

        if throwing:

            throw_clap_completed = True


def get_clap_hand_positions():

    timer = max(
        0.0,
        min(
            clap_timer,
            CLAP_DURATION
        )
    )

    if timer <= CLAP_HALF_DURATION:

        progress = (
            timer
            / CLAP_HALF_DURATION
        )

    else:

        progress = (
            CLAP_DURATION - timer
        ) / CLAP_HALF_DURATION

    progress = max(
        0.0,
        min(
            1.0,
            progress
        )
    )

    left_hand_x = (
        470
        + int(
            CLAP_MAX_MOVEMENT
            * progress
        )
    )

    right_hand_x = (
        630
        - int(
            CLAP_MAX_MOVEMENT
            * progress
        )
    )

    return (
        left_hand_x,
        right_hand_x
    )


# ============================================================
# RESET
# ============================================================

def reset():

    global weight
    global score
    global reps

    global max_stamina
    global stamina

    global bar_y
    global bar_v

    global phase
    global state

    global tap_count
    global required_taps

    global press_timer
    global press_idle_timer
    global chest_delay_timer

    global rebound_timer
    global rebound_active

    global failed_press
    global muscle_torn
    global failure_cause

    global throwing

    global throw_catch_window_active
    global throw_catch_window_timer
    global throw_catch_attempted

    global throw_reached_apex
    global throw_start_y

    global throw_clap_completed

    global clapping
    global clap_timer

    global crushing
    global crush_timer

    global message

    global weight_menu_open
    global weight_page

    global throw_unlocked

    global challenge
    global challenge_progress
    global challenge_target
    global challenge_weight
    global challenge_difficulty
    global challenge_message
    global challenge_message_timer

    global leaderboard_page
    global leaderboard_scroll

    weight = 20

    score = 0
    reps = 0

    max_stamina = STARTING_STAMINA
    stamina = STARTING_STAMINA

    bar_y = TOP_Y
    bar_v = 0.0

    phase = "holding"
    state = "playing"

    tap_count = 0
    required_taps = get_required_taps(weight)

    press_timer = 0.0
    press_idle_timer = 0.0
    chest_delay_timer = 0.0

    rebound_timer = 0.0
    rebound_active = False

    failed_press = False
    muscle_torn = False
    failure_cause = ""

    throwing = False

    throw_catch_window_active = False
    throw_catch_window_timer = 0.0
    throw_catch_attempted = False

    throw_reached_apex = False
    throw_start_y = TOP_Y

    throw_clap_completed = False

    clapping = False
    clap_timer = 0.0

    crushing = False
    crush_timer = 0.0

    message = ""

    weight_menu_open = False
    weight_page = 1

    throw_unlocked = True

    leaderboard_page = False
    leaderboard_scroll = 0

    challenge = None
    challenge_progress = 0
    challenge_target = 0
    challenge_weight = 0
    challenge_difficulty = ""

    challenge_message = ""
    challenge_message_timer = 0.0

    create_challenge()


# ============================================================
# TEXT
# ============================================================

def txt(
    text,
    x,
    y,
    colour=(245, 245, 245),
    font=FONT
):

    surface = font.render(
        str(text),
        True,
        colour
    )

    screen.blit(
        surface,
        (x, y)
    )


# ============================================================
# WEIGHT
# ============================================================

def change_weight(new_weight):

    global weight
    global required_taps
    global weight_menu_open
    global throw_unlocked

    if (
        new_weight >= 520
        and score < HIGH_WEIGHT_UNLOCK_SCORE
    ):
        return

    weight = max(
        MIN_WEIGHT,
        min(
            MAX_WEIGHT,
            new_weight
        )
    )

    required_taps = get_required_taps(
        weight
    )

    throw_unlocked = False
    weight_menu_open = False


# ============================================================
# CRUSH
# ============================================================

def start_crush():

    global state
    global crushing
    global crush_timer
    global throwing
    global bar_v
    global tap_count
    global clapping
    global throw_clap_completed

    state = "crushing"

    crushing = True
    crush_timer = 0.0

    throwing = False
    clapping = False

    throw_clap_completed = False

    bar_v = 0.0
    tap_count = 0


# ============================================================
# DRAW GYM
# ============================================================

def draw_gym():

    screen.fill(
        (25, 30, 38)
    )

    pygame.draw.rect(
        screen,
        (42, 49, 59),
        (0, 475, W, 175)
    )

    pygame.draw.rect(
        screen,
        (80, 85, 94),
        (285, 425, 430, 30),
        border_radius=8
    )

    pygame.draw.line(
        screen,
        (110, 110, 115),
        (340, 455),
        (315, 540),
        9
    )

    pygame.draw.line(
        screen,
        (110, 110, 115),
        (660, 455),
        (685, 540),
        9
    )

    if state == "crushing":
        return

    arm_size = get_arm_size()

    pygame.draw.circle(
        screen,
        (224, 174, 130),
        (650, 390),
        29
    )

    pygame.draw.line(
        screen,
        (45, 130, 220),
        (620, 405),
        (440, 410),
        36
    )

    pygame.draw.line(
        screen,
        (45, 130, 220),
        (455, 410),
        (385, 455),
        25
    )

    pygame.draw.line(
        screen,
        (45, 130, 220),
        (455, 410),
        (400, 365),
        18
    )

    # ========================================================
    # ARM / CLAP ANIMATION
    # ========================================================

    if clapping:

        left_hand_x, right_hand_x = (
            get_clap_hand_positions()
        )

        pygame.draw.line(
            screen,
            (224, 174, 130),
            (545, 398),
            (
                left_hand_x,
                HAND_Y
            ),
            arm_size
        )

        pygame.draw.line(
            screen,
            (224, 174, 130),
            (575, 398),
            (
                right_hand_x,
                HAND_Y
            ),
            arm_size
        )

        if abs(
            clap_timer
            - CLAP_HALF_DURATION
        ) <= 0.025:

            pygame.draw.circle(
                screen,
                (255, 225, 120),
                (
                    550,
                    HAND_Y
                ),
                9,
                2
            )

            pygame.draw.circle(
                screen,
                (255, 245, 180),
                (
                    550,
                    HAND_Y
                ),
                4
            )

    elif throwing:

        left_hand_x = 470
        right_hand_x = 630

        pygame.draw.line(
            screen,
            (224, 174, 130),
            (545, 398),
            (
                left_hand_x,
                HAND_Y
            ),
            arm_size
        )

        pygame.draw.line(
            screen,
            (224, 174, 130),
            (575, 398),
            (
                right_hand_x,
                HAND_Y
            ),
            arm_size
        )

    else:

        pygame.draw.line(
            screen,
            (224, 174, 130),
            (545, 398),
            (
                470,
                int(bar_y)
            ),
            arm_size
        )

        pygame.draw.line(
            screen,
            (224, 174, 130),
            (575, 398),
            (
                630,
                int(bar_y)
            ),
            arm_size
        )

    # ========================================================
    # BAR
    # ========================================================

    pygame.draw.line(
        screen,
        (205, 210, 215),
        (285, int(bar_y)),
        (750, int(bar_y)),
        9
    )

    plate = (
        15
        + int(
            min(weight, 1000)
            / MAX_WEIGHT
            * 45
        )
    )

    for x in (310, 725):

        pygame.draw.rect(
            screen,
            (30, 30, 30),
            (
                x - 14,
                int(bar_y) - plate // 2,
                28,
                plate
            ),
            border_radius=5
        )

    # ========================================================
    # UI
    # ========================================================

    txt(
        f"Weight: {weight} kg",
        30,
        20
    )

    txt(
        f"Reps: {reps}",
        30,
        60
    )

    txt(
        f"Score: {score}",
        30,
        100
    )

    txt(
        f"Arm Size: {get_arm_size()}",
        30,
        140,
        (100, 200, 255),
        SMALL
    )

    txt(
        f"Player: {username}",
        30,
        165,
        (190, 195, 205),
        SMALL
    )

    txt(
        "STAMINA",
        725,
        20,
        font=SMALL
    )

    pygame.draw.rect(
        screen,
        (70, 70, 75),
        (725, 50, 220, 24),
        border_radius=8
    )

    stamina_ratio = (
        stamina / max_stamina
        if max_stamina > 0
        else 0
    )

    if stamina_ratio > 0.5:

        stamina_colour = (
            40,
            200,
            100
        )

    elif stamina_ratio > 0.2:

        stamina_colour = (
            230,
            190,
            40
        )

    else:

        stamina_colour = (
            220,
            55,
            50
        )

    pygame.draw.rect(
        screen,
        stamina_colour,
        (
            725,
            50,
            int(220 * stamina_ratio),
            24
        ),
        border_radius=8
    )

    txt(
        f"{int(stamina)} / {int(max_stamina)}",
        810,
        80,
        (220, 220, 220),
        SMALL
    )

    # ========================================================
    # CHALLENGE DISPLAY
    # ========================================================

    if challenge is not None:

        colours = {
            "easy": (100, 220, 130),
            "medium": (255, 210, 70),
            "hard": (255, 130, 50),
            "insane": (255, 60, 80),
            "hell": (255, 40, 40),
            "depression": (190, 70, 255),
            "demonic": (255, 0, 0),
            "impossible": (255, 0, 255)
        }

        txt(
            update_challenge_display(),
            300,
            140,
            colours.get(
                challenge_difficulty,
                (255, 255, 255)
            ),
            SMALL
        )

    elif challenge_message:

        txt(
            challenge_message,
            300,
            140,
            (100, 240, 130),
            SMALL
        )

    txt(
        "SHIFT = Weight Menu | "
        "S = Lower | W / UP = Press | "
        "SPACE = Throw / Catch | CTRL = Clap | TAB = Leaderboard",
        25,
        580,
        (185, 190, 200),
        SMALL
    )

    with network_lock:

        connected = online_connected
        status = online_status_message

    if connected:

        txt(
            "ONLINE",
            875,
            610,
            (70, 230, 120),
            TINY
        )

    else:

        txt(
            status,
            825,
            610,
            (255, 170, 80),
            TINY
        )


# ============================================================
# WEIGHT MENU
# ============================================================

def draw_weight_menu():

    overlay = pygame.Surface(
        (W, H),
        pygame.SRCALPHA
    )

    overlay.fill(
        (0, 0, 0, 190)
    )

    screen.blit(
        overlay,
        (0, 0)
    )

    title = BIG.render(
        "SELECT WEIGHT",
        True,
        (255, 215, 80)
    )

    screen.blit(
        title,
        (
            W // 2
            - title.get_width() // 2,
            20
        )
    )

    txt(
        f"PAGE {weight_page} / 2",
        450,
        85,
        (200, 205, 215),
        SMALL
    )

    if (
        weight_page == 2
        and score < HIGH_WEIGHT_UNLOCK_SCORE
    ):

        txt(
            "UNLOCKS AT 35000 POINTS",
            330,
            108,
            (255, 80, 80),
            SMALL
        )

    page_weights = (
        WEIGHTS_PAGE_1
        if weight_page == 1
        else WEIGHTS_PAGE_2
    )

    tile_w = 145
    tile_h = 70

    gap_x = 18
    gap_y = 14

    total_w = (
        5 * tile_w
        + 4 * gap_x
    )

    start_x = (
        W - total_w
    ) // 2

    start_y = 125

    mouse_pos = pygame.mouse.get_pos()

    for index, selected_weight in enumerate(
        page_weights
    ):

        row = index // 5
        col = index % 5

        x = (
            start_x
            + col * (
                tile_w + gap_x
            )
        )

        y = (
            start_y
            + row * (
                tile_h + gap_y
            )
        )

        rect = pygame.Rect(
            x,
            y,
            tile_w,
            tile_h
        )

        hovered = rect.collidepoint(
            mouse_pos
        )

        locked = (
            selected_weight >= 520
            and score < HIGH_WEIGHT_UNLOCK_SCORE
        )

        if locked:

            colour = (
                45,
                45,
                50
            )

        elif selected_weight == weight:

            colour = (
                45,
                150,
                90
            )

        elif hovered:

            colour = (
                65,
                105,
                180
            )

        else:

            colour = (
                55,
                60,
                70
            )

        pygame.draw.rect(
            screen,
            colour,
            rect,
            border_radius=8
        )

        pygame.draw.rect(
            screen,
            (180, 185, 195),
            rect,
            2,
            border_radius=8
        )

        label_text = (
            "LOCKED"
            if locked
            else f"{selected_weight} kg"
        )

        label_colour = (
            (130, 130, 140)
            if locked
            else (255, 255, 255)
        )

        label = FONT.render(
            label_text,
            True,
            label_colour
        )

        screen.blit(
            label,
            (
                x + tile_w // 2
                - label.get_width() // 2,
                y + tile_h // 2
                - label.get_height() // 2
            )
        )

    txt(
        "LEFT / A = Page 1 • RIGHT / D = Page 2",
        325,
        520,
        (210, 215, 225),
        SMALL
    )

    txt(
        "Click a weight • SHIFT or ESC to close",
        315,
        550,
        (210, 215, 225),
        SMALL
    )


# ============================================================
# CRUSH SCENE
# ============================================================

def draw_crush_scene(progress):

    bench_top = 425

    bar_top = (
        335
        + int(90 * progress)
    )

    normal_top = 365
    normal_bottom = 424

    normal_height = (
        normal_bottom
        - normal_top
    )

    body_height = max(
        4,
        int(
            normal_height
            * (
                1.0
                - 0.94 * progress
            )
        )
    )

    body_y = (
        bench_top
        - body_height
    )

    spread = int(
        90 * progress
    )

    body_left = (
        385
        - spread // 2
    )

    body_width = (
        300
        + spread
    )

    pygame.draw.ellipse(
        screen,
        (45, 130, 220),
        (
            body_left,
            body_y,
            body_width,
            body_height
        )
    )

    head_width = (
        58
        + int(45 * progress)
    )

    head_height = max(
        5,
        int(
            58
            * (
                1.0
                - 0.94 * progress
            )
        )
    )

    head_x = (
        620
        - int(22 * progress)
    )

    head_y = (
        bench_top
        - head_height
    )

    pygame.draw.ellipse(
        screen,
        (224, 174, 130),
        (
            head_x,
            head_y,
            head_width,
            head_height
        )
    )

    arm_size = get_arm_size()

    arm_height = max(
        4,
        int(
            arm_size
            * (
                1.0
                - 0.80 * progress
            )
        )
    )

    arm_y = (
        bench_top
        - arm_height
    )

    pygame.draw.line(
        screen,
        (224, 174, 130),
        (545, arm_y),
        (
            400 - int(40 * progress),
            arm_y
        ),
        arm_height
    )

    pygame.draw.line(
        screen,
        (224, 174, 130),
        (575, arm_y),
        (
            700 + int(40 * progress),
            arm_y
        ),
        arm_height
    )

    pygame.draw.line(
        screen,
        (205, 210, 215),
        (285, bar_top),
        (750, bar_top),
        14
    )

    plate = (
        15
        + int(
            min(weight, 1000)
            / MAX_WEIGHT
            * 45
        )
    )

    for x in (310, 725):

        pygame.draw.rect(
            screen,
            (30, 30, 30),
            (
                x - 14,
                bar_top - plate // 2,
                28,
                plate
            ),
            border_radius=5
        )

    pygame.draw.rect(
        screen,
        (80, 85, 94),
        (285, 425, 430, 30),
        border_radius=8
    )

    txt(
        "Crushed",
        420,
        155,
        (255, 50, 50),
        BIG
    )


# ============================================================
# USERNAME SCREEN
# ============================================================

def draw_username_screen():

    screen.fill(
        (20, 25, 32)
    )

    title = BIG.render(
        "BENCH PRESS CHALLENGE",
        True,
        (255, 215, 80)
    )

    screen.blit(
        title,
        (
            W // 2
            - title.get_width() // 2,
            90
        )
    )

    txt(
        "ENTER YOUR USERNAME",
        350,
        205,
        (220, 225, 235),
        FONT
    )

    box = pygame.Rect(
        280,
        255,
        440,
        65
    )

    pygame.draw.rect(
        screen,
        (45, 50, 60),
        box,
        border_radius=8
    )

    pygame.draw.rect(
        screen,
        (255, 215, 80),
        box,
        2,
        border_radius=8
    )

    if username_input:

        displayed = username_input
        colour = (255, 255, 255)

    else:

        displayed = "Type username..."
        colour = (120, 125, 135)

    txt(
        displayed,
        300,
        274,
        colour,
        FONT
    )

    txt(
        "Press ENTER to begin",
        360,
        370,
        (100, 225, 140),
        SMALL
    )

    txt(
        "TAB = Online Leaderboard",
        370,
        405,
        (100, 180, 255),
        SMALL
    )

    txt(
        "Your device gets one leaderboard position.",
        315,
        445,
        (175, 180, 190),
        SMALL
    )

    with network_lock:

        connected = online_connected
        status = online_status_message

    if connected:

        txt(
            "SERVER: CONNECTED",
            390,
            490,
            (70, 230, 120),
            SMALL
        )

    else:

        txt(
            "SERVER: " + status,
            380,
            490,
            (255, 170, 80),
            SMALL
        )


# ============================================================
# LEADERBOARD SCREEN
# ============================================================

def draw_leaderboard():

    screen.fill(
        (18, 22, 29)
    )

    title = BIG.render(
        "ONLINE LEADERBOARD",
        True,
        (255, 215, 80)
    )

    screen.blit(
        title,
        (
            W // 2
            - title.get_width() // 2,
            20
        )
    )

    with network_lock:

        connected = online_connected

    if connected:

        txt(
            "● LIVE",
            450,
            82,
            (70, 230, 120),
            SMALL
        )

    else:

        txt(
            "● OFFLINE",
            440,
            82,
            (255, 90, 80),
            SMALL
        )

    best = get_player_best()

    txt(
        f"{clean_username(username)}'s highest score: {best}",
        300,
        110,
        (100, 225, 140),
        FONT
    )

    rank = get_player_rank()

    if rank is not None:

        txt(
            f"Current rank: #{rank}",
            425,
            145,
            (190, 195, 205),
            SMALL
        )

    else:

        txt(
            "Current rank: outside top 100",
            390,
            145,
            (190, 195, 205),
            SMALL
        )

    table_x = 175
    table_y = 175

    table_w = 650
    row_h = 34

    pygame.draw.rect(
        screen,
        (55, 65, 80),
        (
            table_x,
            table_y,
            table_w,
            row_h
        ),
        border_radius=5
    )

    txt(
        "RANK",
        table_x + 30,
        table_y + 6,
        (255, 215, 80),
        SMALL
    )

    txt(
        "USERNAME",
        table_x + 155,
        table_y + 6,
        (255, 215, 80),
        SMALL
    )

    txt(
        "SCORE",
        table_x + 500,
        table_y + 6,
        (255, 215, 80),
        SMALL
    )

    entries = get_sorted_leaderboard()

    visible_rows = 10

    max_scroll = max(
        0,
        len(entries) - visible_rows
    )

    current_scroll = max(
        0,
        min(
            leaderboard_scroll,
            max_scroll
        )
    )

    for row in range(visible_rows):

        index = (
            current_scroll
            + row
        )

        if index >= len(entries):
            break

        item = entries[index]

        rank_value = item["display_rank"]
        name = item["name"]
        score_value = item["score"]

        y = (
            table_y
            + row_h
            + row * row_h
        )

        is_player = (
            bool(item["device_id"])
            and item["device_id"] == DEVICE_ID
        )

        if not is_player:

            is_player = (
                name.lower()
                == clean_username(
                    username
                ).lower()
                and not item["device_id"]
            )

        if is_player:

            colour = (
                50,
                105,
                75
            )

            text_colour = (
                255,
                255,
                255
            )

        else:

            colour = (
                35,
                40,
                50
            )

            text_colour = (
                220,
                225,
                230
            )

        pygame.draw.rect(
            screen,
            colour,
            (
                table_x,
                y,
                table_w,
                row_h - 2
            ),
            border_radius=3
        )

        txt(
            rank_value,
            table_x + 38,
            y + 6,
            text_colour,
            SMALL
        )

        display_name = name

        if len(display_name) > 18:

            display_name = (
                display_name[:17]
                + "…"
            )

        txt(
            display_name,
            table_x + 155,
            y + 6,
            text_colour,
            SMALL
        )

        score_surface = SMALL.render(
            str(score_value),
            True,
            text_colour
        )

        screen.blit(
            score_surface,
            (
                table_x
                + 550
                - score_surface.get_width(),
                y + 6
            )
        )

    if max_scroll > 0:

        txt(
            f"Showing "
            f"{current_scroll + 1}-"
            f"{min(
                current_scroll
                + visible_rows,
                len(entries)
            )}"
            f" of {len(entries)}",
            405,
            535,
            (160, 165, 175),
            TINY
        )

    else:

        txt(
            f"{len(entries)} player(s)",
            450,
            535,
            (160, 165, 175),
            TINY
        )

    txt(
        "UP/DOWN or W/S = Scroll • PAGE UP/DOWN = Fast scroll",
        285,
        565,
        (190, 195, 205),
        SMALL
    )

    txt(
        "TAB / ESC = Return",
        405,
        595,
        (255, 215, 80),
        SMALL
    )


# ============================================================
# INITIALIZE
# ============================================================

pygame.key.start_text_input()

get_online_leaderboard(
    force=True
)


# ============================================================
# MAIN LOOP
# ============================================================

running = True

while running:

    dt = clock.tick(60) / 1000.0

    dt = min(
        dt,
        0.033
    )

    # ========================================================
    # UPDATE CLAP
    # ========================================================

    update_clap(dt)

    # ========================================================
    # CHALLENGE MESSAGE
    # ========================================================

    if challenge_message_timer > 0:

        challenge_message_timer -= dt

        if challenge_message_timer <= 0:

            challenge_message_timer = 0
            challenge_message = ""

    # ========================================================
    # NETWORK
    # ========================================================

    update_online_data()

    # ========================================================
    # EVENTS
    # ========================================================

    for e in pygame.event.get():

        if e.type == pygame.QUIT:

            running = False
            break

        # ====================================================
        # LEADERBOARD
        # ====================================================

        if leaderboard_page:

            if e.type == pygame.MOUSEWHEEL:

                leaderboard_scroll -= e.y

            elif e.type == pygame.KEYDOWN:

                entries = get_sorted_leaderboard()

                max_scroll = max(
                    0,
                    len(entries) - 10
                )

                if e.key in (
                    pygame.K_DOWN,
                    pygame.K_s
                ):

                    leaderboard_scroll += 1

                elif e.key in (
                    pygame.K_UP,
                    pygame.K_w
                ):

                    leaderboard_scroll -= 1

                elif e.key == pygame.K_PAGEDOWN:

                    leaderboard_scroll += 10

                elif e.key == pygame.K_PAGEUP:

                    leaderboard_scroll -= 10

                elif e.key == pygame.K_HOME:

                    leaderboard_scroll = 0

                elif e.key == pygame.K_END:

                    leaderboard_scroll = max_scroll

                elif e.key in (
                    pygame.K_TAB,
                    pygame.K_ESCAPE
                ):

                    leaderboard_page = False

                leaderboard_scroll = max(
                    0,
                    min(
                        leaderboard_scroll,
                        max_scroll
                    )
                )

            continue

        # ====================================================
        # USERNAME
        # ====================================================

        if state == "username":

            if e.type == pygame.TEXTINPUT:

                if len(username_input) < 20:

                    username_input += e.text

                continue

            if e.type == pygame.KEYDOWN:

                if e.key == pygame.K_BACKSPACE:

                    username_input = (
                        username_input[:-1]
                    )

                elif e.key == pygame.K_RETURN:

                    username = clean_username(
                        username_input
                    )

                    upload_username()

                    get_online_leaderboard(
                        force=True
                    )

                    reset()

                elif e.key == pygame.K_TAB:

                    leaderboard_page = True
                    leaderboard_scroll = 0

                    get_online_leaderboard(
                        force=True
                    )

            continue

        # ====================================================
        # MOUSE
        # ====================================================

        if e.type == pygame.MOUSEBUTTONDOWN:

            if (
                e.button == 1
                and weight_menu_open
                and state == "playing"
            ):

                mouse_x, mouse_y = e.pos

                tile_w = 145
                tile_h = 70

                gap_x = 18
                gap_y = 14

                total_w = (
                    5 * tile_w
                    + 4 * gap_x
                )

                start_x = (
                    W - total_w
                ) // 2

                start_y = 125

                page_weights = (
                    WEIGHTS_PAGE_1
                    if weight_page == 1
                    else WEIGHTS_PAGE_2
                )

                clicked_weight = None

                for index, selected_weight in enumerate(
                    page_weights
                ):

                    row = index // 5
                    col = index % 5

                    x = (
                        start_x
                        + col * (
                            tile_w + gap_x
                        )
                    )

                    y = (
                        start_y
                        + row * (
                            tile_h + gap_y
                        )
                    )

                    rect = pygame.Rect(
                        x,
                        y,
                        tile_w,
                        tile_h
                    )

                    if rect.collidepoint(
                        mouse_x,
                        mouse_y
                    ):

                        clicked_weight = (
                            selected_weight
                        )

                        break

                if clicked_weight is not None:

                    change_weight(
                        clicked_weight
                    )

                continue

        # ====================================================
        # KEYBOARD
        # ====================================================

        if e.type != pygame.KEYDOWN:
            continue

        # ====================================================
        # GLOBAL LEADERBOARD
        # ====================================================

        if e.key == pygame.K_TAB:

            leaderboard_page = True
            leaderboard_scroll = 0

            get_online_leaderboard(
                force=True
            )

            continue

        # ====================================================
        # GAME OVER
        # ====================================================

        if state == "gameover":

            if e.key == pygame.K_RETURN:

                leaderboard_page = True
                leaderboard_scroll = 0

                get_online_leaderboard(
                    force=True
                )

            elif e.key == pygame.K_r:

                reset()

            continue

        # ====================================================
        # WEIGHT MENU
        # ====================================================

        if weight_menu_open:

            if e.key in (
                pygame.K_ESCAPE,
                pygame.K_LSHIFT,
                pygame.K_RSHIFT
            ):

                weight_menu_open = False

            elif e.key in (
                pygame.K_LEFT,
                pygame.K_a
            ):

                weight_page = 1

            elif e.key in (
                pygame.K_RIGHT,
                pygame.K_d
            ):

                weight_page = 2

            continue

        # ====================================================
        # ONLY PLAYING
        # ====================================================

        if state != "playing":
            continue

        # ====================================================
        # CTRL = CLAP
        # ====================================================

        if e.key in (
            pygame.K_LCTRL,
            pygame.K_RCTRL
        ):

            start_clap()

            continue

        # ====================================================
        # SHIFT = WEIGHT MENU
        # ====================================================

        if e.key in (
            pygame.K_LSHIFT,
            pygame.K_RSHIFT
        ):

            if (
                phase == "holding"
                and not throwing
                and abs(
                    bar_y - TOP_Y
                ) < 2
            ):

                weight_menu_open = True
                weight_page = 1

            continue

        # ====================================================
        # THROW / CATCH
        #
        # IMPORTANT:
        #
        # Successful throw:
        #
        #     Normal throw:
        #         reward_throw_catch()
        #
        #     Clap throw:
        #         reward_clap_press()
        #
        # NEVER call both.
        #
        # NEVER call reward_normal_rep().
        # ====================================================

        if (
            e.key == pygame.K_SPACE
            and throwing
        ):

            if throw_catch_attempted:
                continue

            throw_catch_attempted = True

            if (
                throw_catch_window_active
                and throw_reached_apex
            ):

                # Successful catch.

                throwing = False

                throw_catch_window_active = False
                throw_catch_window_timer = 0.0

                # ------------------------------------------------
                # CLAP THROW
                #
                # 300 kg -> +300 points
                # ------------------------------------------------

                if throw_clap_completed:

                    reward_clap_press()

                    if (
                        challenge == "clap_press"
                        and weight == challenge_weight
                    ):

                        register_challenge_clap_press()

                # ------------------------------------------------
                # NORMAL THROW
                #
                # 300 kg -> +150 points
                # ------------------------------------------------

                else:

                    reward_throw_catch()

                    if (
                        challenge == "throw"
                        and weight == challenge_weight
                    ):

                        register_challenge_throw()

                throw_unlocked = False

                throw_clap_completed = False

                upload_score(
                    force=True
                )

            continue

        # ====================================================
        # START THROW
        # ====================================================

        if (
            e.key == pygame.K_SPACE
            and phase == "holding"
            and abs(
                bar_y - TOP_Y
            ) < 2
            and not throwing
            and throw_unlocked
        ):

            throwing = True

            throw_reached_apex = False

            throw_catch_window_active = False
            throw_catch_window_timer = 0.0

            throw_catch_attempted = False

            throw_clap_completed = False

            throw_start_y = bar_y

            bar_y = throw_start_y

            bar_v = THROW_INITIAL_VELOCITY

            continue

        # ====================================================
        # NORMAL PRESS
        # ====================================================

        if (
            phase == "pressing"
            and e.key in (
                pygame.K_w,
                pygame.K_UP
            )
            and not failed_press
            and not muscle_torn
        ):

            if (
                chest_delay_timer
                < CHEST_PRESS_DELAY
            ):

                continue

            if stamina <= 0:

                stamina = 0

                failed_press = True

                failure_cause = (
                    "You ran out of stamina."
                )

                continue

            stamina -= STAMINA_PER_TAP

            stamina = max(
                0,
                stamina
            )

            tap_count += 1
            press_idle_timer = 0.0

            progress = min(
                1.0,
                tap_count / required_taps
            )

            bar_y = (
                CHEST_Y
                - (
                    CHEST_Y - TOP_Y
                ) * progress
            )

            # ====================================================
            # COMPLETED NORMAL REP
            #
            # ONLY NORMAL REPS COME THROUGH HERE.
            #
            # 300 kg -> +300 points
            # ====================================================

            if tap_count >= required_taps:

                bar_y = TOP_Y

                if press_timer < TRICEP_TEAR_TIME:

                    failed_press = True
                    muscle_torn = True

                    failure_cause = (
                        "Your tricep tore during the press."
                    )

                else:

                    reps += 1

                    # Exactly ONE normal-rep reward.
                    reward_normal_rep()

                    if (
                        challenge == "rep"
                        and weight == challenge_weight
                    ):

                        register_challenge_rep()

                    if (
                        reps % CHALLENGE_INTERVAL == 0
                        and challenge is None
                    ):

                        create_challenge()

                    phase = "holding"

                    tap_count = 0
                    press_timer = 0
                    press_idle_timer = 0
                    chest_delay_timer = 0

                    failed_press = False
                    muscle_torn = False

                    throw_unlocked = True

                    upload_score(
                        force=True
                    )

    # ========================================================
    # PLAYING UPDATE
    # ========================================================

    if state == "playing":

        if weight_menu_open:

            pass

        # ====================================================
        # THROW PHYSICS
        # ====================================================

        elif throwing:

            bar_v += (
                THROW_GRAVITY
                * dt
            )

            bar_y += (
                bar_v
                * dt
            )

            if bar_v >= 0:

                throw_reached_apex = True

            if (
                throw_reached_apex
                and bar_v > 0
                and CATCH_TOP_Y
                <= bar_y
                <= CATCH_BOTTOM_Y
            ):

                throw_catch_window_active = True
                throw_catch_window_timer = 0.0

            else:

                throw_catch_window_active = False
                throw_catch_window_timer = 0.0

            if bar_y >= CHEST_Y:

                bar_y = CHEST_Y

                failure_cause = (
                    "You failed to catch the thrown bar."
                )

                throw_catch_window_active = False

                throw_clap_completed = False

                start_crush()

        # ====================================================
        # NORMAL BENCH PRESS
        # ====================================================

        else:

            if phase == "holding":

                bar_y = TOP_Y

                keys = pygame.key.get_pressed()

                lowering = (
                    keys[pygame.K_s]
                    or keys[pygame.K_DOWN]
                )

                if lowering:

                    phase = "lowering"

            elif phase == "lowering":

                keys = pygame.key.get_pressed()

                lowering = (
                    keys[pygame.K_s]
                    or keys[pygame.K_DOWN]
                )

                if lowering:

                    lowering_speed = (
                        CHEST_Y - TOP_Y
                    )

                    bar_y += (
                        lowering_speed
                        * dt
                    )

                if bar_y >= CHEST_Y:

                    bar_y = CHEST_Y

                    tap_count = 0
                    press_timer = 0
                    press_idle_timer = 0
                    chest_delay_timer = 0

                    failed_press = False
                    muscle_torn = False

                    rebound_active = True
                    rebound_timer = 0.0

                    phase = "rebounding"

            elif phase == "rebounding":

                rebound_timer += dt

                progress = min(
                    1.0,
                    rebound_timer
                    / REBOUND_RISE_TIME
                )

                smooth_progress = (
                    1.0
                    - (
                        1.0 - progress
                    ) ** 2
                )

                bar_y = (
                    CHEST_Y
                    - REBOUND_DISTANCE
                    * smooth_progress
                )

                if rebound_timer >= REBOUND_RISE_TIME:

                    bar_y = (
                        CHEST_Y
                        - REBOUND_DISTANCE
                    )

                    rebound_active = False
                    chest_delay_timer = 0.0

                    phase = "rebound_delay"

            elif phase == "rebound_delay":

                bar_y = (
                    CHEST_Y
                    - REBOUND_DISTANCE
                )

                chest_delay_timer += dt

                if (
                    chest_delay_timer
                    >= CHEST_PRESS_DELAY
                ):

                    phase = "pressing"

                    tap_count = 0
                    press_timer = 0
                    press_idle_timer = 0

            elif phase == "pressing":

                chest_delay_timer += dt
                press_timer += dt

                if failed_press or muscle_torn:

                    fall_speed = (
                        300
                        + weight * 0.5
                    )

                    bar_y += (
                        fall_speed
                        * dt
                    )

                    if bar_y >= CHEST_Y:

                        bar_y = CHEST_Y

                        start_crush()

                else:

                    press_idle_timer += dt

                    if (
                        press_idle_timer
                        >= PRESS_IDLE_GRACE
                    ):

                        fall_speed = (
                            300
                            + weight * 0.5
                        )

                        bar_y += (
                            fall_speed
                            * dt
                        )

                        if bar_y >= CHEST_Y:

                            bar_y = CHEST_Y

                            failure_cause = (
                                "You stopped pressing for too long."
                            )

                            start_crush()

        # ====================================================
        # STAMINA REGEN
        # ====================================================

        if (
            phase == "holding"
            and not throwing
            and not weight_menu_open
        ):

            regen_limit = (
                max_stamina
                * STAMINA_REGEN_LIMIT
            )

            if stamina < regen_limit:

                stamina += (
                    max_stamina
                    * STAMINA_REGEN_PERCENT
                    * dt
                )

                stamina = min(
                    stamina,
                    regen_limit
                )

        stamina = max(
            0,
            min(
                stamina,
                max_stamina
            )
        )

    # ========================================================
    # CRUSH
    # ========================================================

    if state == "crushing":

        crush_timer += dt

        if crush_timer >= 1.25:

            state = "gameover"

            upload_score(
                force=True
            )

            get_online_leaderboard(
                force=True
            )

    # ========================================================
    # DRAW
    # ========================================================

    if state == "username":

        draw_username_screen()

    elif leaderboard_page:

        draw_leaderboard()

    elif state == "crushing":

        draw_gym()

        progress = min(
            1.0,
            crush_timer / 1.25
        )

        draw_crush_scene(
            progress
        )

    else:

        draw_gym()

    # ========================================================
    # WEIGHT MENU
    # ========================================================

    if (
        weight_menu_open
        and state == "playing"
    ):

        draw_weight_menu()

    # ========================================================
    # GAME OVER
    # ========================================================

    if (
        state == "gameover"
        and not leaderboard_page
    ):

        overlay = pygame.Surface(
            (W, H),
            pygame.SRCALPHA
        )

        overlay.fill(
            (0, 0, 0, 185)
        )

        screen.blit(
            overlay,
            (0, 0)
        )

        title_surface = BIG.render(
            "GAME OVER",
            True,
            (255, 75, 75)
        )

        screen.blit(
            title_surface,
            (
                W // 2
                - title_surface.get_width() // 2,
                60
            )
        )

        score_surface = BIG.render(
            str(score),
            True,
            (255, 255, 255)
        )

        screen.blit(
            score_surface,
            (
                W // 2
                - score_surface.get_width() // 2,
                135
            )
        )

        txt(
            f"{username}'s score",
            425,
            200,
            (190, 195, 205),
            SMALL
        )

        txt(
            f"{reps} reps • {weight} kg",
            390,
            235,
            (220, 220, 220),
            SMALL
        )

        personal_best = get_player_best()

        if score >= personal_best:

            best_message = "NEW PERSONAL BEST!"

        else:

            best_message = (
                f"Highest ever: {personal_best}"
            )

        txt(
            best_message,
            390,
            275,
            (100, 225, 140),
            SMALL
        )

        current_rank = get_player_rank()

        if current_rank is not None:

            txt(
                f"Online rank: #{current_rank}",
                420,
                310,
                (100, 180, 255),
                SMALL
            )

        else:

            txt(
                "Online rank: outside top 100",
                390,
                310,
                (160, 165, 175),
                SMALL
            )

        cause_label = SMALL.render(
            "CAUSE OF FAILURE",
            True,
            (255, 215, 80)
        )

        screen.blit(
            cause_label,
            (
                W // 2
                - cause_label.get_width() // 2,
                350
            )
        )

        words = failure_cause.split()

        lines = []
        current_line = ""

        for word in words:

            test_line = (
                current_line
                + " "
                + word
            ).strip()

            if SMALL.size(test_line)[0] <= 700:

                current_line = test_line

            else:

                if current_line:
                    lines.append(
                        current_line
                    )

                current_line = word

        if current_line:
            lines.append(
                current_line
            )

        for i, line in enumerate(lines):

            surface = SMALL.render(
                line,
                True,
                (245, 245, 245)
            )

            screen.blit(
                surface,
                (
                    W // 2
                    - surface.get_width() // 2,
                    380 + i * 25
                )
            )

        txt(
            "ENTER = Online Leaderboard",
            340,
            485,
            (255, 215, 80),
            FONT
        )

        txt(
            "R = Restart",
            435,
            530,
            (190, 195, 205),
            SMALL
        )

    pygame.display.flip()


# ============================================================
# SHUTDOWN
# ============================================================

if username and score > 0:

    upload_score(
        force=True
    )

    shutdown_start = time.time()

    while (
        score_request_pending
        and (
            time.time()
            - shutdown_start
            < 5.0
        )
    ):

        time.sleep(0.05)

network_running = False

pygame.key.stop_text_input()

pygame.quit()

sys.exit()
