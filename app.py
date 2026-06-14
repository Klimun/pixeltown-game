"""
PixelTown — Render Sunucusu
Tüm odalar bu tek sunucuda barınır. Host telefon kavramı yok.
Kurulum: pip install flask flask-sock
Çalıştır: python app.py
"""

from flask import Flask, render_template, request, jsonify
from flask_sock import Sock
import json
import time
import math
import uuid
import threading

app = Flask(__name__)
sock = Sock(app)

BLOCK_COLORS = {
    "grass": "#4CAF50",
    "stone": "#9E9E9E",
    "wood":  "#8D6E63",
    "water": "#2196F3",
    "sand":  "#FFF176",
    "brick": "#E53935",
}

# ---------------------------------------------------------
# Fizik Sabitleri (Platform Oyunu)
# ---------------------------------------------------------
GRAVITY = 0.018          # her tikte aşağı ivme
JUMP_VELOCITY = 0.38     # zıplama anlık hızı (yukarı, pozitif)
MOVE_SPEED = 0.20        # yatay hareket hızı (tile/tick) -> 4.0 tile/sn @ 20Hz
RUN_MULTIPLIER = 1.8     # koşma çarpanı (ileride kullanılacak)
MAX_FALL_SPEED = 0.6     # terminal düşüş hızı
TICK_RATE = 1 / 20       # 20 Hz fizik döngüsü
PLAYER_W = 0.8           # oyuncu genişliği (tile)
PLAYER_H = 0.9           # oyuncu yüksekliği (tile)

# Hangi bloklar "katı" (üzerine basılabilir / içinden geçilemez)
SOLID_BLOCKS = {"grass", "stone", "wood", "sand", "brick"}
# water katı değil, içinden geçilebilir

# ---------------------------------------------------------
# Oda Durumu — Bellek İçi
# ---------------------------------------------------------
# rooms = {
#   "room_id": {
#       "name": "...",
#       "owner_id": "...",        # ilk kuran kişi (host yetkisi)
#       "players": {ws_id: {...}},
#       "world_blocks": {...},
#       "ws_clients": {ws_id: ws},
#       "created_at": time
#   }
# }
rooms = {}
rooms_lock = threading.Lock()

ROOM_EMPTY_TIMEOUT = 3600  # 1 saat kimse yoksa oda silinir


# ---------------------------------------------------------
# Yardımcı Fonksiyonlar
# ---------------------------------------------------------

def broadcast(room_id, msg_type, data, exclude_id=None):
    room = rooms.get(room_id)
    if not room:
        return
    payload = json.dumps({"type": msg_type, **data})
    dead = []
    for wid, ws in list(room["ws_clients"].items()):
        if wid == exclude_id:
            continue
        try:
            ws.send(payload)
        except Exception:
            dead.append(wid)
    for wid in dead:
        room["ws_clients"].pop(wid, None)
        room["players"].pop(wid, None)


def send_to(ws, msg_type, data):
    try:
        ws.send(json.dumps({"type": msg_type, **data}))
    except Exception:
        pass


def is_solid(world_blocks, x, y):
    """Verilen tile koordinatında katı blok var mı?"""
    key = f"{int(math.floor(x))},{int(math.floor(y))}"
    block = world_blocks.get(key)
    return block in SOLID_BLOCKS


EPS = 1e-6  # kayan nokta hatalarını tolere etmek için


def check_collision_box(world_blocks, x, y, w, h):
    """Oyuncu kutusu (x,y sol-alt köşe) bir katı blokla çakışıyor mu?"""
    # Kutunun kapladığı tile aralığını tara (floor tabanlı, negatif koordinat güvenli)
    x0 = int(math.floor(x + EPS))
    x1 = int(math.floor(x + w - EPS))
    y0 = int(math.floor(y + EPS))
    y1 = int(math.floor(y + h - EPS))
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            # Gerçek çakışma kontrolü (AABB, epsilon toleranslı)
            if (tx < x + w - EPS and tx + 1 > x + EPS and
                    ty < y + h - EPS and ty + 1 > y + EPS):
                if is_solid(world_blocks, tx, ty):
                    return True
    return False


def physics_step(room):
    """Bir oda için tüm oyuncuların fiziğini bir tik ilerletir."""
    world_blocks = room["world_blocks"]
    moved = False

    for p in room["players"].values():
        inp = p.get("input", {"left": False, "right": False, "jump": False})
        vx = 0.0
        if inp.get("left"):
            vx -= MOVE_SPEED
        if inp.get("right"):
            vx += MOVE_SPEED

        vy = p.get("vy", 0.0)

        # Gravite uygula
        vy -= GRAVITY
        if vy < -MAX_FALL_SPEED:
            vy = -MAX_FALL_SPEED

        # Zıplama (sadece yerdeyken)
        if inp.get("jump") and p.get("on_ground", False):
            vy = JUMP_VELOCITY
            p["on_ground"] = False

        x, y = p["x"], p["y"]

        # --- Yatay hareket + çarpışma ---
        new_x = x + vx
        if vx != 0:
            if check_collision_box(world_blocks, new_x, y, PLAYER_W, PLAYER_H):
                # Çarpışma varsa hareket etme
                new_x = x
            x = new_x

        # --- Dikey hareket + çarpışma ---
        new_y = y + vy
        on_ground = False
        if vy != 0:
            if check_collision_box(world_blocks, x, new_y, PLAYER_W, PLAYER_H):
                if vy > 0:
                    # Yukarı çarptı - tavana kafa attı
                    vy = 0
                else:
                    # Aşağı çarptı - yere indi
                    vy = 0
                    on_ground = True
                new_y = y
            y = new_y
        else:
            # Hız sıfırsa, hâlâ yerde mi kontrol et (bir altındaki blok)
            if check_collision_box(world_blocks, x, y - 0.05, PLAYER_W, PLAYER_H):
                on_ground = True

        # Dünya sınırları (y eksisi = aşağı, düşmeyi sınırla)
        if y < -20:
            y = 1
            vy = 0

        if abs(x - p["x"]) > 0.0001 or abs(y - p["y"]) > 0.0001 or p.get("vy") != vy:
            moved = True

        p["x"] = round(x, 4)
        p["y"] = round(y, 4)
        p["vy"] = round(vy, 4)
        p["on_ground"] = on_ground
        p["facing"] = "right" if vx > 0 else ("left" if vx < 0 else p.get("facing", "right"))

    return moved


def physics_loop(room_id):
    """Bir oda için sürekli çalışan fizik döngüsü (ayrı thread)."""
    next_tick = time.monotonic()
    while True:
        next_tick += TICK_RATE
        sleep_time = next_tick - time.monotonic()
        if sleep_time > 0:
            time.sleep(sleep_time)
        else:
            # Geride kaldıysak zamanlamayı sıfırla (sürekli birikme olmasın)
            next_tick = time.monotonic()

        moved = False
        snapshot = None

        try:
            with rooms_lock:
                room = rooms.get(room_id)
                if not room:
                    return  # oda silindi, döngüyü bitir
                if not room["players"]:
                    continue  # kimse yok, ama döngü canlı kalsın (oda silinmediyse)

                moved = physics_step(room)

                if moved:
                    snapshot = {
                        pid_data["id"]: {
                            "x": pid_data["x"], "y": pid_data["y"],
                            "vy": pid_data["vy"], "on_ground": pid_data["on_ground"],
                            "facing": pid_data["facing"],
                        }
                        for pid_data in room["players"].values()
                    }
        except Exception as e:
            # Bir hata fizik döngüsünü asla tamamen durdurmasın
            print(f"[!] Fizik hatası ({room_id}): {e}")
            continue

        if moved and snapshot:
            try:
                broadcast(room_id, "physics_update", {"players": snapshot})
            except Exception as e:
                print(f"[!] Broadcast hatası ({room_id}): {e}")


    now = time.time()
    with rooms_lock:
        to_delete = []
        for rid, room in rooms.items():
            if not room["players"] and now - room.get("emptied_at", now) > ROOM_EMPTY_TIMEOUT:
                to_delete.append(rid)
        for rid in to_delete:
            del rooms[rid]


# ---------------------------------------------------------
# HTTP Endpoint'ler
# ---------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", block_colors=json.dumps(BLOCK_COLORS))


@app.route("/api/rooms")
def get_rooms():
    cleanup_empty_rooms()
    result = []
    for rid, room in rooms.items():
        result.append({
            "room_id": rid,
            "name": room["name"],
            "current_players": len(room["players"]),
            "max_players": 20,
        })
    return jsonify(result)


@app.route("/api/host", methods=["POST"])
def create_room():
    data = request.json
    room_name = data.get("name", "Yeni Oda")[:30]

    room_id = str(uuid.uuid4())[:8].upper()

    # Başlangıç zemini: y=0 satırında geniş bir platform
    initial_blocks = {}
    for x in range(-50, 200):
        initial_blocks[f"{x},0"] = "grass"
        initial_blocks[f"{x},-1"] = "stone"
        initial_blocks[f"{x},-2"] = "stone"

    with rooms_lock:
        rooms[room_id] = {
            "name": room_name,
            "owner_id": None,  # ilk katılan oyuncu owner olur
            "players": {},
            "world_blocks": initial_blocks,
            "ws_clients": {},
            "created_at": time.time(),
            "emptied_at": time.time(),
        }

    # Fizik döngüsünü başlat (oda silinince otomatik durur)
    t = threading.Thread(target=physics_loop, args=(room_id,), daemon=True)
    t.start()

    return jsonify({"status": "ok", "room_id": room_id, "room_name": room_name})


# ---------------------------------------------------------
# WebSocket — Oyun Mantığı
# ---------------------------------------------------------

@sock.route("/ws/<room_id>")
def websocket(ws, room_id):
    room_id = room_id.upper()
    ws_id = str(uuid.uuid4())[:8]

    with rooms_lock:
        room = rooms.get(room_id)
        if not room:
            send_to(ws, "error", {"message": "Oda bulunamadı"})
            return
        room["ws_clients"][ws_id] = ws

    try:
        while True:
            raw = ws.receive()
            if raw is None:
                break
            try:
                data = json.loads(raw)
                t = data.get("type")

                if t == "join":
                    with rooms_lock:
                        if len(room["players"]) >= 20:
                            send_to(ws, "error", {"message": "Oda dolu!"})
                            break

                        player_id = str(uuid.uuid4())[:8]
                        room["players"][ws_id] = {
                            "id": player_id,
                            "name": data.get("name", "Oyuncu"),
                            "x": 5, "y": 1,
                            "vy": 0.0,
                            "on_ground": False,
                            "facing": "right",
                            "input": {"left": False, "right": False, "jump": False},
                            "skin": data.get("skin", None),
                        }

                        # İlk giren = owner (host yetkisi)
                        if room["owner_id"] is None:
                            room["owner_id"] = player_id

                        is_owner = (room["owner_id"] == player_id)

                    send_to(ws, "welcome", {
                        "your_id": player_id,
                        "world": room["world_blocks"],
                        "players": list(room["players"].values()),
                        "block_colors": BLOCK_COLORS,
                        "is_owner": is_owner,
                        "room_name": room["name"],
                    })

                    p = room["players"][ws_id]
                    broadcast(room_id, "player_joined", {
                        "id": p["id"], "name": p["name"],
                        "x": p["x"], "y": p["y"],
                    }, exclude_id=ws_id)

                elif t == "input":
                    # Client'tan gelen tuş durumu (sol/sağ/zıpla)
                    p = room["players"].get(ws_id)
                    if p:
                        p["input"] = {
                            "left": bool(data.get("left", False)),
                            "right": bool(data.get("right", False)),
                            "jump": bool(data.get("jump", False)),
                        }

                elif t == "place_block":
                    p = room["players"].get(ws_id)
                    if p:
                        key = f"{data['x']},{data['y']}"
                        room["world_blocks"][key] = data.get("block_type", "grass")
                        broadcast(room_id, "block_placed", {
                            "x": data["x"], "y": data["y"],
                            "block_type": room["world_blocks"][key],
                            "placed_by": p["id"],
                        })

                elif t == "remove_block":
                    key = f"{data['x']},{data['y']}"
                    room["world_blocks"].pop(key, None)
                    broadcast(room_id, "block_removed", {
                        "x": data["x"], "y": data["y"],
                    })

                elif t == "chat":
                    p = room["players"].get(ws_id)
                    if p:
                        msg = str(data.get("message", ""))[:200]
                        broadcast(room_id, "chat_message", {
                            "from_id": p["id"],
                            "from_name": p["name"],
                            "message": msg,
                            "timestamp": time.time(),
                        })

                elif t == "transfer_host":
                    # Host yetkisini başka bir oyuncuya devret
                    p = room["players"].get(ws_id)
                    if p and room["owner_id"] == p["id"]:
                        new_owner_id = data.get("new_owner_id")
                        # Hedef oyuncunun varlığını kontrol et
                        target_exists = any(
                            pl["id"] == new_owner_id for pl in room["players"].values()
                        )
                        if target_exists:
                            room["owner_id"] = new_owner_id
                            broadcast(room_id, "host_changed", {
                                "new_owner_id": new_owner_id,
                            })

            except (json.JSONDecodeError, KeyError):
                pass

    except Exception:
        pass
    finally:
        with rooms_lock:
            p = room["players"].pop(ws_id, None)
            room["ws_clients"].pop(ws_id, None)

            if p:
                # Eğer ayrılan kişi owner ise, host'u devret
                if room["owner_id"] == p["id"] and room["players"]:
                    new_owner = next(iter(room["players"].values()))
                    room["owner_id"] = new_owner["id"]
                    broadcast(room_id, "host_changed", {
                        "new_owner_id": new_owner["id"],
                    })

                broadcast(room_id, "player_left", {
                    "id": p["id"], "name": p["name"],
                })

            if not room["players"]:
                room["emptied_at"] = time.time()


# ---------------------------------------------------------
# Başlat
# ---------------------------------------------------------

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    print("=" * 40)
    print("PixelTown başlatılıyor...")
    print(f"Port: {port}")
    print("=" * 40)
    app.run(host="0.0.0.0", port=port, debug=False)
