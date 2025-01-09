import os
import random
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from typing import List
import threading
import subprocess
from test import songs_db  # Убедитесь, что `songs_db` содержит данные в правильном формате

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static folder
app.mount("/static", StaticFiles(directory="static"), name="static")

# Shared state
game_state = {
    "song": None,  # Will be set to the random song
    "winner": None,
    "players": {},  # Stores player names and their WebSocket connections
    "song_playing": False,
    "song_process": None,  # Process to control song playback
}

# WebSocket manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_to(self, websocket: WebSocket, data: dict):
        await websocket.send_json(data)

    async def broadcast(self, data: dict):
        for connection in self.active_connections:
            await connection.send_json(data)

manager = ConnectionManager()

def select_random_song():
    """Select a random song from the database and update game_state."""
    song_index = random.randint(0, len(songs_db) - 1)
    selected_song = songs_db[song_index]
    game_state["song"] = selected_song  # Update global game_state
    return selected_song

def play_song(file_path):
    """Plays the selected song on the server."""
    try:
        print(f"Playing song: {file_path}")
        process = subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", file_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        game_state["song_process"] = process
        process.wait()  # Wait for the process to complete
        print("Song playback finished.")
        game_state["song_process"] = None
    except Exception as e:
        print(f"Error playing song: {e}")

def stop_song():
    """Stops the song playback if a process is running."""
    if game_state["song_process"]:
        game_state["song_process"].terminate()
        game_state["song_process"] = None
        print("Song playback stopped.")

@app.get("/")
async def get():
    # Serve the HTML frontend
    with open("index.html") as f:
        html_content = f.read()
    return HTMLResponse(html_content)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    player_id = id(websocket)

    try:
        # Receive and register player name
        await manager.send_to(websocket, {"type": "prompt", "message": "Enter your name:"})
        player_name = await websocket.receive_text()
        game_state["players"][player_id] = player_name

        # Notify all players about new player
        await manager.broadcast({
            "type": "update",
            "players": list(game_state["players"].values()),
            "winner": game_state["winner"],
            "guess_state": f"{player_name} joined the game."
        })

        # Start playing a random song when two players have joined
        if len(game_state["players"]) == 2 and not game_state["song_playing"]:
            game_state["song_playing"] = True
            selected_song = select_random_song()
            threading.Thread(target=play_song, args=(selected_song["link"],)).start()

        while True:
            guess = await websocket.receive_text()

            if game_state["winner"]:
                await manager.send_to(websocket, {
                    "type": "update",
                    "guess_state": f"The game is over. {game_state['winner']} already won!"
                })
                continue

            if guess.lower() == game_state["song"]["name"].lower():
                game_state["winner"] = player_name
                stop_song()  # Stop the song when there is a winner
                await manager.broadcast({
                    "type": "update",
                    "players": list(game_state["players"].values()),
                    "winner": game_state["winner"],
                    "guess_state": f"{player_name} guessed correctly! The song was: {game_state['song']['name']}"
                })
                break
            else:
                await manager.send_to(websocket, {
                    "type": "update",
                    "players": list(game_state["players"].values()),
                    "winner": game_state["winner"],
                    "guess_state": "Wrong guess. Try again!"
                })
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        if player_id in game_state["players"]:
            del game_state["players"][player_id]
            await manager.broadcast({
                "type": "update",
                "players": list(game_state["players"].values()),
                "winner": game_state["winner"],
                "guess_state": f"{player_name} left the game."
            })

        # Stop the song if there are fewer than 2 players
        if len(game_state["players"]) < 2 and game_state["song_playing"]:
            stop_song()
            game_state["song_playing"] = False

