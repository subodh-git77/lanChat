import json
import secrets
import string
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# ✅ Serve frontend properly (IMPORTANT FIX)
app.mount("/", StaticFiles(directory=".", html=True), name="static")

# ----------------------------
# Room Management
# ----------------------------
rooms = {}

def generate_room_id(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def generate_pin(length: int = 4) -> str:
    return ''.join(secrets.choice(string.digits) for _ in range(length))

async def broadcast(message: str, room_id: str, sender=None):
    if room_id not in rooms:
        return

    dead = []
    for client in list(rooms[room_id]["clients"].keys()):
        if client == sender:
            continue
        try:
            await client.send_text(message)
        except:
            dead.append(client)

    for d in dead:
        rooms[room_id]["clients"].pop(d, None)

    if room_id in rooms and not rooms[room_id]["clients"]:
        del rooms[room_id]

# ----------------------------
# WebSocket Endpoint
# ----------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("🔥 WebSocket CONNECTED")

    current_room = None
    username = None

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                data = json.loads(raw)
            except:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Invalid JSON"
                }))
                continue

            action = data.get("action")

            # --- CREATE ROOM ---
            if action == "create_room":
                room_id = generate_room_id()
                pin = generate_pin()
                rooms[room_id] = {"pin": pin, "clients": {}, "history": []}

                await websocket.send_text(json.dumps({
                    "type": "room_created",
                    "room_id": room_id,
                    "pin": pin
                }))

            # --- LIST ROOMS ---
            elif action == "list_rooms":
                room_list = [
                    {"room_id": rid, "users": len(info["clients"])}
                    for rid, info in rooms.items()
                ]
                await websocket.send_text(json.dumps({
                    "type": "room_list",
                    "rooms": room_list
                }))

            # --- JOIN ROOM ---
            elif action == "join_room":
                pin_in = str(data.get("pin") or "").strip()
                username = (data.get("username") or "Guest").strip() or "Guest"

                room_id = None
                for rid, info in rooms.items():
                    if info["pin"] == pin_in:
                        room_id = rid
                        break

                if room_id:
                    if current_room and websocket in rooms.get(current_room, {}).get("clients", {}):
                        rooms[current_room]["clients"].pop(websocket, None)

                    current_room = room_id
                    rooms[room_id]["clients"][websocket] = username

                    await websocket.send_text(json.dumps({
                        "type": "joined_room",
                        "room_id": room_id,
                        "message": f"✅ Joined room {room_id}"
                    }))

                    for msg in rooms[room_id]["history"]:
                        await websocket.send_text(msg)

                    await broadcast(json.dumps({
                        "type": "status",
                        "message": f"{username} joined the room"
                    }), room_id, sender=websocket)

                else:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "Invalid PIN"
                    }))

            # --- LEAVE ROOM ---
            elif action == "leave_room":
                if current_room and current_room in rooms:
                    user = rooms[current_room]["clients"].pop(websocket, None)

                    await websocket.send_text(json.dumps({
                        "type": "left_room",
                        "message": f"👋 You left room {current_room}"
                    }))

                    if user:
                        await broadcast(json.dumps({
                            "type": "status",
                            "message": f"{user} left the room"
                        }), current_room, sender=websocket)

                    if not rooms[current_room]["clients"]:
                        del rooms[current_room]

                current_room = None

            # --- CHAT ---
            elif action == "chat":
                if not current_room:
                    continue

                msg_text = (data.get("message") or "").strip()
                if not msg_text:
                    continue

                timestamp = datetime.utcnow().isoformat() + "Z"

                payload = json.dumps({
                    "type": "chat",
                    "user": username,
                    "message": msg_text,
                    "time": timestamp
                })

                rooms[current_room]["history"].append(payload)
                if len(rooms[current_room]["history"]) > 50:
                    rooms[current_room]["history"].pop(0)

                await broadcast(payload, current_room, sender=websocket)

            # --- TYPING ---
            elif action == "typing":
                if current_room:
                    await broadcast(json.dumps({
                        "type": "typing",
                        "user": username
                    }), current_room, sender=websocket)

            elif action == "stop_typing":
                if current_room:
                    await broadcast(json.dumps({
                        "type": "stop_typing",
                        "user": username
                    }), current_room, sender=websocket)

    except WebSocketDisconnect:
        print("disconnected")

        if current_room and current_room in rooms:
            user = rooms[current_room]["clients"].pop(websocket, None)

            if user:
                await broadcast(json.dumps({
                    "type": "status",
                    "message": f"{user} left the room"
                }), current_room)

            if not rooms[current_room]["clients"]:
                del rooms[current_room]