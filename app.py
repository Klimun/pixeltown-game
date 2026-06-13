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


def cleanup_empty_rooms():
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
    with rooms_lock:
        rooms[room_id] = {
            "name": room_name,
            "owner_id": None,  # ilk katılan oyuncu owner olur
            "players": {},
            "world_blocks": {},
            "ws_clients": {},
            "created_at": time.time(),
            "emptied_at": time.time(),
        }

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
                            "x": 5, "y": 5,
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

                elif t == "move":
                    p = room["players"].get(ws_id)
                    if p:
                        p["x"] = data.get("x", p["x"])
                        p["y"] = data.get("y", p["y"])
                        broadcast(room_id, "player_moved", {
                            "id": p["id"], "x": p["x"], "y": p["y"],
                        }, exclude_id=ws_id)

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
