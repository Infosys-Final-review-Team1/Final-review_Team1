import sqlite3
import hashlib
from datetime import datetime

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('chatterbox.db', check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        ''')
        # Messages table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        ''')
        self.conn.commit()
    
    def hash_password(self, password):
        return hashlib.sha256(password.encode()).hexdigest()
    
    def register_user(self, username, password):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username, self.hash_password(password))
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def authenticate_user(self, username, password):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT username FROM users WHERE username = ? AND password = ?",
            (username, self.hash_password(password))
        )
        result = cursor.fetchone()
        return result[0] if result else None
    
    def save_message(self, username, message):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO messages (username, message, timestamp) VALUES (?, ?, ?)",
            (username, message, datetime.now().isoformat())
        )
        self.conn.commit()
    
    def get_recent_messages(self, limit=50):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT username, message, timestamp FROM messages ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
        messages = [{"username": row[0], "message": row[1], "timestamp": row[2]} 
                   for row in cursor.fetchall()]
        return messages[::-1]  # Newest first
    
    def get_total_messages(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages")
        return cursor.fetchone()[0]
    
    def get_user_stats(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        return {"total_users": cursor.fetchone()[0]}
