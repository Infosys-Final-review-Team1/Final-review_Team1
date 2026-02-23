from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles 
from fastapi import APIRouter, HTTPException 
import sqlite3
import bcrypt 
import re
import time
import csv
import io
from datetime import datetime
import asyncio
from pydantic import BaseModel
from typing import Dict, List, Set
import math
import json

app = FastAPI(title="Chatterbox Enterprise - FULL ADMIN PANEL + DM SUPPORT")
router = APIRouter()
ADMIN_PASSWORD = "SuperEnterprise2026!"


BAD_WORDS = {
    'fuck', 'shit', 'bitch', 'cunt', 'dick', 'piss', 'asshole', 'bastard', 
    'slut', 'whore', 'cock', 'tits', 'prick', 'twat', 'Kill'
}

def contains_bad_word(message: str) -> tuple[bool, str]:
    message_lower = message.lower()
    words = re.findall(r'\b\w+\b', message_lower)
    for word in words:
        if word in BAD_WORDS:
            return True, word
    return False, ""

def safe_float(value):
    """Convert inf to large number for JSON"""
    if math.isinf(value):
        return 9999999999
    return value

def safe_timestamp(timestamp):
    """Safe timestamp conversion - handles 9999999999"""
    if timestamp == 9999999999 or timestamp > 253402300799:  # Unix max
        return "PERMANENT"
    try:
        return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
    except (OverflowError, ValueError, OSError):
        return "PERMANENT"

def create_csv_response(data, headers, filename):
    """Generic CSV download helper"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    
    for row in data:
        writer.writerow(row)
    
    return Response(
        content=output.getvalue(),
        media_type='text/csv',
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache"
        }
    )

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('chatterbox.db', check_same_thread=False)
        self.migrate_database()
        self.create_tables()

    def unban_user(self, username):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM user_bans WHERE username = ?", (username,))
        cursor.execute("DELETE FROM user_violations WHERE username = ?", (username,))
        self.conn.commit()
        return True
    
    def migrate_database(self):
        """AUTO-MIGRATE: Add missing columns"""
        try:
            cursor = self.conn.cursor()
            
            # Migrate user_bans
            cursor.execute("PRAGMA table_info(user_bans)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'ban_type' not in columns:
                cursor.execute("ALTER TABLE user_bans ADD COLUMN ban_type TEXT DEFAULT 'temp'")
                print("DB MIGRATED: Added ban_type to user_bans")
            
            # Migrate ban_details
            cursor.execute("PRAGMA table_info(ban_details)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'ban_type' not in columns:
                cursor.execute("ALTER TABLE ban_details ADD COLUMN ban_type TEXT DEFAULT 'temp'")
                print("DB MIGRATED: Added ban_type to ban_details")
            
            # Add DM tables
            cursor.execute("CREATE TABLE IF NOT EXISTS dm_messages ("
                           "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                           "from_user TEXT NOT NULL, "
                           "to_user TEXT NOT NULL, "
                           "message TEXT NOT NULL, "
                           "timestamp TEXT NOT NULL, "
                           "UNIQUE(from_user, to_user, timestamp))"
                          )
            
            self.conn.commit()
        except Exception as e:
            print(f"Migration warning: {e}")
    
    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_plain TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_violations (
                username TEXT PRIMARY KEY,
                violation_count INTEGER DEFAULT 0,
                last_violation REAL DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_bans (
                username TEXT PRIMARY KEY,
                ban_until REAL DEFAULT 0,
                ban_type TEXT DEFAULT 'temp'
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ban_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                bad_word TEXT NOT NULL,
                violation_count INTEGER NOT NULL,
                ban_reason TEXT NOT NULL,
                banned_at TEXT NOT NULL,
                ban_until REAL NOT NULL,
                ban_duration_seconds INTEGER NOT NULL,
                ban_type TEXT DEFAULT 'temp'
            )
        ''')
        self.conn.commit()

    def hash_password(self, password):
        """BCRYPT - More secure than SHA256"""
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """BCRYPT VERIFY"""
        return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())
    
    def register_user(self, username, full_name, password):
        try:
            cursor = self.conn.cursor()
            hashed_pw = self.hash_password(password)  
            cursor.execute(
                "INSERT INTO users (username, full_name, password_hash, password_plain) VALUES (?, ?, ?, ?)",
                (username, full_name, hashed_pw, password)
            )
            self.conn.commit()
            print(f"NEW USER: {username}")
            return True
        except sqlite3.IntegrityError:
            return False
    
    def authenticate_user(self, username, password):
        cursor = self.conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
        result = cursor.fetchone()
        if not result:
            return None
        
        stored_hash = result[0]
        
        try:
            if self.verify_password(password, stored_hash):
                pass
        except ValueError:
            new_hash = self.hash_password(password)
            cursor.execute("UPDATE users SET password_hash = ? WHERE username = ?", (new_hash, username))
            self.conn.commit()
            print(f"AUTO-MIGRATED {username}: SHA256 → BCRYPT")
            return username
        
        if self.verify_password(password, stored_hash):
            return username
        return None
    
    def save_global_message(self, username, message):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO messages (username, message, timestamp) VALUES (?, ?, ?)",
            (username, message, datetime.now().isoformat())
        )
        self.conn.commit()
    
    def save_dm_message(self, from_user, to_user, message):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO dm_messages (from_user, to_user, message, timestamp) VALUES (?, ?, ?, ?)",
            (from_user, to_user, message, datetime.now().isoformat())
        )
        self.conn.commit()
    
    def get_recent_global_messages(self, limit=50):
        cursor = self.conn.cursor()
        cursor.execute("SELECT username, message, timestamp FROM messages ORDER BY timestamp DESC LIMIT ?", (limit,))
        messages = [{"username": row[0], "message": row[1], "timestamp": row[2], "isDM": False} 
                   for row in reversed(cursor.fetchall())]
        return messages
    
    def get_recent_dm_messages(self, username, target_user, limit=50):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT from_user, message, timestamp FROM dm_messages 
            WHERE (from_user = ? AND to_user = ?) OR (from_user = ? AND to_user = ?)
            ORDER BY timestamp DESC LIMIT ?
        """, (username, target_user, target_user, username, limit))
        messages = [{"username": row[0], "message": row[1], "timestamp": row[2], "isDM": True} 
                   for row in reversed(cursor.fetchall())]
        return messages
    
    def get_total_messages(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages")
        return cursor.fetchone()[0]
    
    def get_registered_users_count(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]
    
    def get_all_users(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT username, full_name FROM users ORDER BY username")
        return [{"username": row[0], "full_name": row[1]} for row in cursor.fetchall()]
    
    def get_all_users_csv(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT username, full_name, password_plain FROM users ORDER BY username")
        return cursor.fetchall()
    
    def get_all_messages_csv(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT username, message, timestamp FROM messages ORDER BY timestamp DESC")
        return cursor.fetchall()
    
    def get_ban_report(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT username, bad_word, violation_count, ban_reason, banned_at, ban_until, ban_duration_seconds, ban_type 
            FROM ban_details ORDER BY banned_at DESC
        """)
        return cursor.fetchall()
    
    def get_audit_report(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT 'BAN' as action, username, banned_at as timestamp, ban_reason as details 
            FROM ban_details 
            UNION ALL
            SELECT 'MESSAGE' as action, username, timestamp, message as details 
            FROM messages 
            ORDER BY timestamp DESC
        """)
        return cursor.fetchall()
    
    def get_violation_count(self, username):
        cursor = self.conn.cursor()
        cursor.execute("SELECT violation_count FROM user_violations WHERE username = ?", (username,))
        result = cursor.fetchone()
        return result[0] if result else 0
    
    def update_violation(self, username, bad_word):
        cursor = self.conn.cursor()
        cursor.execute("SELECT violation_count FROM user_violations WHERE username = ?", (username,))
        result = cursor.fetchone()
        count = result[0] if result else 0
        new_count = count + 1
        
        cursor.execute("INSERT OR REPLACE INTO user_violations (username, violation_count, last_violation) VALUES (?, ?, ?)",
                      (username, new_count, time.time()))
        self.conn.commit()
        return new_count
    
    def ban_user(self, username, bad_word, duration_seconds, ban_type="temp"):
        ban_until = time.time() + duration_seconds if duration_seconds != 9999999999 else 9999999999
        violation_count = self.get_violation_count(username)
        
        cursor = self.conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO user_bans (username, ban_until, ban_type) VALUES (?, ?, ?)", 
                      (username, ban_until, ban_type))
        cursor.execute("""
            INSERT INTO ban_details (username, bad_word, violation_count, ban_reason, banned_at, ban_until, ban_duration_seconds, ban_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (username, bad_word, violation_count, f"{violation_count}th strike", 
              datetime.now().isoformat(), ban_until, duration_seconds, ban_type))
        self.conn.commit()
        return ban_until
    
    def get_banned_users(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT username, ban_until, ban_type FROM user_bans WHERE ban_until > ?", (time.time(),))
        results = []
        for row in cursor.fetchall():
            safe_ban_until = safe_float(row[1])
            results.append({
                "username": row[0], 
                "ban_until": safe_ban_until, 
                "ban_type": row[2],
                "time_left": max(0, int(safe_ban_until - time.time()))
            })
        return results
    
    def is_user_banned(self, username):
        cursor = self.conn.cursor()
        cursor.execute("SELECT ban_until, ban_type FROM user_bans WHERE username = ?", (username,))
        result = cursor.fetchone()
        if result and result[0] > time.time():
            ban_type = result[1] if result[1] else 'temp'
            return True, safe_float(result[0]), ban_type
        return False, 0, "none"

db = Database()

class UserRegister(BaseModel):
    username: str
    full_name: str
    password: str


class UserLogin(BaseModel):
    username: str
    password: str
 
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.user_online_status: Set[str] = set()
        self.typing_status: Dict[str, float] = {}
        self.unban_notifications: Set[str] = set() 
    
    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        is_banned, ban_until, ban_type = db.is_user_banned(username)
        if is_banned:
            if ban_type == "permanent":
                await websocket.send_json({
                    "type": "banned",
                    "message": "PERMANENT BAN",
                    "ban_type": "permanent",
                    "ban_until": ban_until
                    })
            else:
                ban_time_left = max(0, int(ban_until - time.time()))
                await websocket.send_json({
                    "type": "banned",
                    "message": f"🚫 TEMP BAN: {ban_time_left}s remaining",
                    "ban_type": "temp",
                    "ban_until": ban_until
                    })
                
            await asyncio.sleep(3)
            await websocket.close(code=1008)
            print(f"{username} BANNED ({ban_type})")
            return False
        
        self.active_connections[username] = websocket
        self.user_online_status.add(username)

        if username in self.unban_notifications:
            await websocket.send_json({
                "type": "system",
                "message": "✅ Admin has unbanned your account."
                })
            self.unban_notifications.discard(username)
            
        print(f"{username} connected ({len(self.active_connections)} online)")
        
        history = db.get_recent_global_messages(50)
        await websocket.send_json({"type": "history", "messages": history, "target": "global"})
        
        await self.broadcast_user_list()
        await self.broadcast_json({"type": "system", "message": f"{username} joined ({len(self.active_connections)} online)"})
        return True
    
    def disconnect(self, websocket: WebSocket):
        for username, connection in list(self.active_connections.items()):
            if connection == websocket:
                self.active_connections.pop(username, None)
                self.user_online_status.discard(username)
                self.typing_status.pop(username, None)
                asyncio.create_task(self.broadcast_user_list())
                asyncio.create_task(self.broadcast_json({"type": "system", "message": f"{username} left"}))
                break
            

    async def broadcast_json(self, message: dict):
        disconnected = []
        for username, connection in list(self.active_connections.items()):
            try:
                await connection.send_json(message)
            except:
                disconnected.append(username)
        for username in disconnected:
            self.active_connections.pop(username, None)
    
    async def broadcast_user_list(self):
        """Broadcast current online users to all clients"""
        await self.broadcast_json({
            "type": "user_list",
            "users": list(self.user_online_status)
        })
    
    async def send_to_user(self, username: str, message: dict):
        if username in self.active_connections:
            try:
                await self.active_connections[username].send_json(message)
            except:
                pass
    
    async def send_to_target(self, sender: str, target: str, message: dict):
        """Send message to specific target user or broadcast if 'global'"""
        if target == "global":
            await self.broadcast_json(message)
        else:
            await self.send_to_user(target, message)
            # Also send back to sender (for DM confirmation)
            await self.send_to_user(sender, message)
    
    async def handle_message(self, username: str, data: dict):
        message_type = data.get('type')
        
        if message_type == 'typing':
            self.typing_status[username] = time.time()
            target = data.get('target', 'global')
            typing_msg = {
                "type": "typing",
                "username": username,
                "isTyping": True,
                "target": target
            }
            await self.send_to_target(username, target, typing_msg)
            asyncio.create_task(self.clear_typing(username, target))
            return

        content = data.get('content', '').strip()
        target = data.get('target', 'global')
        
        if not content:
            return False
        
        is_banned, _, _ = db.is_user_banned(username)
        if is_banned:
            return False
        
        has_bad_word, bad_word = contains_bad_word(content)
        if has_bad_word:
            violation_count = db.update_violation(username, bad_word)
            if violation_count == 1:
                await self.send_to_user(username, {
                    "type": "warning",
                    "message": f"1st Strike: '{bad_word}' (1/5) → Next = 5min BAN!"
                    })
            elif violation_count == 2:
                ban_until = db.ban_user(username, bad_word, 5 * 60, "temp")
                await self.send_to_user(username, {
                "type": "banned",
                "message": "2nd Strike: 5 MIN BAN (2/5)",
                "ban_until": ban_until,
                "ban_type": "temp"
                })
            elif violation_count == 3:
                await self.send_to_user(username, {
                "type": "warning",
                "message": f"3rd Strike WARNING (3/5) → Next = 24HR BAN!"
                })
                
            elif violation_count == 4:
                await self.send_to_user(username, {
                "type": "warning",
                "message": f"4th Strike WARNING (4/5) → PERMANENT BAN NEXT!"
                })
            else:
                ban_until = db.ban_user(username, bad_word, 9999999999, "permanent")
                await self.send_to_user(username, {
                "type": "banned",
                "message": "5th Strike: PERMANENT BAN (5/5)",
                "ban_until": ban_until,
                "ban_type": "permanent"
                })


            return False
    

        timestamp = datetime.now().isoformat()
        if target == "global":
            db.save_global_message(username, content)
        else:
            db.save_dm_message(username, target, content)
        
        msg_data = {
            "type": "message",
            "username": username,
            "message": content,
            "timestamp": timestamp,
            "target": target,
            "isDM": target != "global"
        }
        await self.send_to_target(username, target, msg_data)
        return True
    
    async def clear_typing(self, username: str, target: str):
        await asyncio.sleep(3)
        if username in self.typing_status:
            typing_msg = {
                "type": "typing",
                "username": username,
                "isTyping": False,
                "target": target
            }
            await self.send_to_target(username, target, typing_msg)

manager = ConnectionManager()

def check_admin_auth(request: Request, key: str = None) -> bool:
    admin_key = key or request.cookies.get("admin_auth")
    return admin_key == ADMIN_PASSWORD

@app.get("/enterprise/login", response_class=HTMLResponse)
async def admin_login_page():
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Admin Login</title>
    <style>
        body { font-family: Arial; max-width: 400px; margin: 100px auto; padding: 20px; }
        input { width: 100%; padding: 10px; margin: 10px 0; box-sizing: border-box; }
        button { width: 100%; padding: 10px; background: #007bff; color: white; border: none; cursor: pointer; }
    </style>
    </head>
    <body>
        <h2>Admin Login</h2>
        <form action="/enterprise/auth" method="post">
            <input type="password" name="password" placeholder="Enter admin password" required>
            <button type="submit">Login → Admin Panel</button>
        </form>
    </body>
    </html>
    """

@app.post("/enterprise/auth")
async def admin_auth(password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        response = RedirectResponse(url=f"/enterprise?key={ADMIN_PASSWORD}", status_code=303)
        response.set_cookie(key="admin_auth", value=ADMIN_PASSWORD, httponly=True)
        return response
    raise HTTPException(401, "Wrong Password!")


@app.get("/enterprise")
async def enterprise_page(request: Request, key: str = None):
    if not check_admin_auth(request, key):
        return RedirectResponse(url="/enterprise/login", status_code=303)
    print("ADMIN PANEL ACCESS!")
    return FileResponse("web/admin.html")

@app.get("/enterprise/unban/{username}")
async def enterprise_unban(username: str, request: Request, key: str = None):
    if not check_admin_auth(request, key):
        raise HTTPException(status_code=401, detail="Unauthorized")

    is_banned, _, _ = db.is_user_banned(username)
    if not is_banned:
        raise HTTPException(status_code=404, detail="User is not banned")

    db.unban_user(username)
    manager.unban_notifications.add(username)

    if username in manager.active_connections:
        websocket = manager.active_connections[username]
        await websocket.send_json({
            "type": "system",
            "message": "✅ Admin has unbanned your account."
        })
        manager.unban_notifications.discard(username)

    return {"message": f"{username} successfully unbanned"}

@app.get("/enterprise/stats")
async def stats(request: Request, key: str = None):
    if not check_admin_auth(request, key):
        raise HTTPException(401, "Unauthorized")
    
    return {
        "online_count": len(manager.active_connections),
        "online_users": list(manager.user_online_status),
        "registered_users": db.get_registered_users_count(),
        "all_users": db.get_all_users(),
        "total_messages": db.get_total_messages(),
        "banned_users": db.get_banned_users()
    }

@app.get("/enterprise/download/users")
async def download_users(request: Request, key: str = None):
    if not check_admin_auth(request, key):
        raise HTTPException(401, "Unauthorized")
    
    users_data = db.get_all_users_csv()
    filename = f"users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return create_csv_response(
        users_data, 
        ['Username', 'Full Name', 'Password'],
        filename
    )

@app.get("/enterprise/download/messages")
async def download_messages(request: Request, key: str = None):
    if not check_admin_auth(request, key):
        raise HTTPException(401, "Unauthorized")
    
    messages_data = db.get_all_messages_csv()
    filename = f"messages_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return create_csv_response(
        messages_data, 
        ['Username', 'Message', 'Timestamp'],
        filename
    )

@app.get("/enterprise/download/ban-report")
async def download_ban_report(request: Request, key: str = None):
    if not check_admin_auth(request, key):
        raise HTTPException(401, "Unauthorized")
    
    ban_data = db.get_ban_report()
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Username', 'Bad Word', 'Strike Count', 'Reason', 'Banned At', 'Ban Until', 'Duration (sec)', 'Ban Type'])
    
    for row in ban_data:
        ban_until_str = safe_timestamp(row[5])
        duration_str = 'PERMANENT' if row[6] == 9999999999 else f"{row[6]}s"
        
        writer.writerow([
            row[0], row[1], row[2], row[3], row[4],
            ban_until_str,
            duration_str,
            row[7]
        ])
    
    filename = f"ban_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=output.getvalue(),
        media_type='text/csv',
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/enterprise/download/audit")
async def download_audit(request: Request, key: str = None):
    if not check_admin_auth(request, key):
        raise HTTPException(401, "Unauthorized")
    
    audit_data = db.get_audit_report()
    filename = f"audit_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return create_csv_response(
        audit_data, 
        ['Action', 'Username', 'Timestamp', 'Details'],
        filename
    )

@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)

@app.get("/online")  
async def online():
    return {
        "count": len(manager.user_online_status), 
        "users": list(manager.user_online_status)
    }

@app.get("/", response_class=FileResponse)
async def root():
    return FileResponse("web/index.html")

@app.get("/chat", response_class=FileResponse)
async def chat():
    return FileResponse("web/chat.html")

@app.get("/chat.html", response_class=FileResponse)  
async def chat_html():
    return FileResponse("web/chat.html")

@app.get("/register.html", response_class=FileResponse)
async def register_page():
    return FileResponse("web/register.html")

@app.post("/register")
async def register(user: UserRegister):
    if db.register_user(user.username, user.full_name, user.password):
        return {"message": "Registered!", "redirect": "/chat.html?username=" + user.username}
    raise HTTPException(400, "Username exists")

@app.post("/login")
async def login(user: UserLogin):
    username = db.authenticate_user(user.username, user.password)
    if username:
        return {"message": "Logged in!", "redirect": f"/chat.html?username={username}"}
    raise HTTPException(401, "Invalid credentials")

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    connected = await manager.connect(websocket, username)
    if not connected:
        return
    
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message_data = json.loads(data)
            except json.JSONDecodeError:
                message_data = {"type": "message", "content": data, "target": "global"}
            
            await manager.handle_message(username, message_data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket ERROR {username}: {e}")
        manager.disconnect(websocket)

app.mount("/web", StaticFiles(directory="web"), name="web")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)  
