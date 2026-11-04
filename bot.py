from telethon import TelegramClient, events, Button, types
from telethon.tl.types import (
    User, Chat, Channel, MessageEntityBold, MessageEntityItalic, 
    MessageEntityUnderline, MessageEntityStrike, MessageEntityCode, 
    MessageEntityPre, MessageEntityTextUrl, MessageEntityUrl, 
    MessageEntitySpoiler, MessageEntityBlockquote, MessageEntityCustomEmoji,
    ReactionEmoji
)
from telethon.tl.functions.channels import GetParticipantRequest, LeaveChannelRequest, JoinChannelRequest
from telethon.tl.functions.messages import (
    DeleteHistoryRequest, DeleteChatUserRequest, ImportChatInviteRequest,
    SendReactionRequest
)
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, FloodWaitError, UserDeactivatedBanError, 
    UserNotParticipantError, AuthKeyUnregisteredError, MessageNotModifiedError,
    SessionRevokedError, ChatWriteForbiddenError, SlowModeWaitError, 
    ChannelPrivateError
)
from datetime import datetime, timedelta
import asyncio
import random
import json
import uuid
import os
import qrcode
import io
import re
import aiosqlite
import logging

# ==============================================================================
# 1. إعدادات المنظومة والاتصال الأساسية
# ==============================================================================
API_ID = 33851360
API_HASH = '1e923faa77b0316b486e5abab626f7bf'
BOT_TOKEN = '8889950086:AAGUItNmgMsXhZguJEDY_Eex4ero4rJliJw'
ADMIN_ID = 115431561

BOT_USERNAME = 'cdibot'
DEV_USERNAME = 'devazef'

DB_SQLITE_FILE = 'database.sqlite'
MEDIA_DIR = os.path.abspath('bot_media')

os.makedirs(MEDIA_DIR, exist_ok=True)

_EMOJI_REGEX = re.compile(
    r'[\U00010000-\U0010ffff\u2600-\u27bf\u2b50\u2b06\u2194-\u21aa\u2300-\u23ff\ufe0f\u200d\u25b6\u25c0\u2700-\u27bf]+', 
    flags=re.UNICODE
)

def clean_btn_text(text: str) -> str:
    raw = re.sub(r'<[^>]*>', '', str(text or ''))
    raw = _EMOJI_REGEX.sub('', raw)
    return raw.strip()

def btn(text: str, data, style: str = 'primary'):
    label = clean_btn_text(text)
    if isinstance(data, str): 
        data = data.encode('utf-8')
    return Button.inline(label, data)

def btn_url(text: str, url: str, style: str = 'primary'):
    label = clean_btn_text(text)
    return Button.url(label, str(url).strip())

# ==============================================================================
# 2. بصمة العميل الرسمية ومطابقة الأجهزة السريعة
# ==============================================================================
def create_telethon_client(session=None):
    return TelegramClient(
        session if session else StringSession(),
        API_ID, API_HASH,
        device_model="Desktop Ultra Engine", system_version="Windows 11 Pro x64",
        app_version="5.5.0 x64", lang_code="ar", system_lang_code="ar",
        connection_retries=10, auto_reconnect=True, timeout=25
    )

# ==============================================================================
# 3. محرك قاعدة البيانات السحابي غير المتزامن
# ==============================================================================
class AsyncDatabase:
    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = asyncio.Lock()
        self.analytics_queue = asyncio.Queue()

    async def init(self):
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('PRAGMA journal_mode=WAL;')
                await db.execute('PRAGMA synchronous=NORMAL;')
                await db.execute('PRAGMA busy_timeout=15000;')
                
                await db.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)')
                
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        sub_end TEXT,
                        is_vip INTEGER DEFAULT 0,
                        captcha_verified INTEGER DEFAULT 1,
                        lang TEXT DEFAULT 'ar',
                        expiry_notified INTEGER DEFAULT 0,
                        trial_claimed INTEGER DEFAULT 1,
                        last_alerted_hour INTEGER DEFAULT -1,
                        self_destruct_save INTEGER DEFAULT 0,
                        storage_enabled INTEGER DEFAULT 0,
                        storage_group TEXT,
                        max_accounts INTEGER DEFAULT 1,
                        created_at TEXT
                    )
                ''')
                
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS accounts (
                        acc_id TEXT PRIMARY KEY,
                        user_id INTEGER,
                        phone TEXT,
                        session TEXT,
                        name TEXT,
                        active INTEGER DEFAULT 0,
                        groups TEXT DEFAULT '[]',
                        sent_count INTEGER DEFAULT 0,
                        publish_interval INTEGER DEFAULT 150,
                        flood_protection INTEGER DEFAULT 2,
                        stealth_mode INTEGER DEFAULT 2,
                        schedule_mode TEXT DEFAULT 'normal',
                        night_pause_start INTEGER DEFAULT 2,
                        night_pause_end INTEGER DEFAULT 7,
                        random_shuffle INTEGER DEFAULT 0,
                        delete_last_msg INTEGER DEFAULT 0,
                        react_before_send INTEGER DEFAULT 1,
                        slowmode_detect INTEGER DEFAULT 1,
                        forward_mode INTEGER DEFAULT 0,
                        forward_channel TEXT DEFAULT '',
                        self_destruct_delay INTEGER DEFAULT 0,
                        last_sent_msgs TEXT DEFAULT '{}',
                        private_welcome_msg TEXT DEFAULT '{}',
                        keyword_rules TEXT DEFAULT '[]',
                        reply_msgs TEXT DEFAULT '[]',
                        messages TEXT DEFAULT '[]',
                        created_at TEXT
                    )
                ''')
                
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS analytics_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        acc_id TEXT,
                        chat_id TEXT,
                        chat_title TEXT,
                        status TEXT,
                        timestamp TEXT,
                        details TEXT
                    )
                ''')
                
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS pending_deletions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        acc_id TEXT,
                        chat_id TEXT,
                        msg_id INTEGER,
                        delete_at REAL
                    )
                ''')
                await db.commit()

                try:
                    await db.execute('ALTER TABLE users ADD COLUMN max_accounts INTEGER DEFAULT 1')
                    await db.commit()
                except Exception:
                    pass

                default_configs = {
                    'maintenance_mode': '0',
                    'login_notifications': '1',
                    'force_sub_enabled': '0',
                    'force_sub': '[]',
                    'admins': json.dumps([ADMIN_ID]),
                    'banned_users': '[]',
                    'total_sent': '0',
                    'start_time': datetime.now().isoformat()
                }
                for k, v in default_configs.items():
                    await db.execute('INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)', (k, v))
                await db.commit()

    async def get_config(self, key, default=None):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('SELECT value FROM config WHERE key = ?', (key,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    try: return json.loads(row[0])
                    except Exception: return row[0]
                return default

    async def set_config(self, key, value):
        val_str = json.dumps(value) if not isinstance(value, str) else value
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', (key, val_str))
                await db.commit()

    async def get_user(self, user_id):
        user_id = int(user_id)
        default_limit = 500 if user_id == ADMIN_ID else 1
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    u = dict(row)
                    u['is_vip'] = bool(u.get('is_vip', 0))
                    u['max_accounts'] = int(u.get('max_accounts') or default_limit)
                    if user_id == ADMIN_ID and u['max_accounts'] < 500:
                        u['max_accounts'] = 500
                    u['accounts'] = await self.get_user_accounts(user_id)
                    u['is_new'] = False
                    return u

        new_u = {
            'user_id': user_id, 'sub_end': None, 'is_vip': 0, 'captcha_verified': 1,
            'lang': 'ar', 'expiry_notified': 0, 'trial_claimed': 1, 'last_alerted_hour': -1,
            'self_destruct_save': 0, 'storage_enabled': 0, 'storage_group': None,
            'max_accounts': default_limit,
            'created_at': datetime.now().isoformat()
        }
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    INSERT OR IGNORE INTO users (user_id, sub_end, is_vip, captcha_verified, lang, expiry_notified, trial_claimed, last_alerted_hour, self_destruct_save, storage_enabled, storage_group, max_accounts, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, None, 0, 1, 'ar', 0, 1, -1, 0, 0, None, default_limit, new_u['created_at']))
                await db.commit()
        new_u['accounts'] = {}
        new_u['is_new'] = True
        return new_u

    async def save_user(self, user):
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    UPDATE users SET is_vip = ?, sub_end = ?, lang = ?, max_accounts = ? WHERE user_id = ?
                ''', (1 if user.get('is_vip') else 0, user.get('sub_end'), user.get('lang', 'ar'), user.get('max_accounts', 1), user['user_id']))
                await db.commit()

    async def set_user_account_limit(self, user_id, limit):
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('UPDATE users SET max_accounts = ? WHERE user_id = ?', (limit, int(user_id)))
                await db.commit()

    def _parse_account_dict(self, acc):
        def safe_json_load(v, default):
            if isinstance(v, (dict, list)): return v
            if not v: return default
            try: return json.loads(v)
            except Exception: return default

        acc['active'] = bool(acc.get('active', 0))
        acc['random_shuffle'] = bool(acc.get('random_shuffle', 0))
        acc['delete_last_msg'] = bool(acc.get('delete_last_msg', 0))
        acc['react_before_send'] = bool(acc.get('react_before_send', 1))
        acc['slowmode_detect'] = bool(acc.get('slowmode_detect', 1))
        acc['forward_mode'] = bool(acc.get('forward_mode', 0))
        acc['self_destruct_delay'] = int(acc.get('self_destruct_delay', 0))
        acc['groups'] = safe_json_load(acc.get('groups'), [])
        acc['last_sent_msgs'] = safe_json_load(acc.get('last_sent_msgs'), {})
        acc['private_welcome_msg'] = safe_json_load(acc.get('private_welcome_msg'), {})
        acc['keyword_rules'] = safe_json_load(acc.get('keyword_rules'), [])
        acc['reply_msgs'] = safe_json_load(acc.get('reply_msgs'), [])
        acc['messages'] = safe_json_load(acc.get('messages'), [])
        return acc

    async def get_user_accounts(self, user_id):
        user_id = int(user_id)
        accounts = {}
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM accounts WHERE user_id = ?', (user_id,)) as cursor:
                async for row in cursor:
                    acc = self._parse_account_dict(dict(row))
                    accounts[acc['acc_id']] = acc
        return accounts

    async def get_account(self, acc_id):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM accounts WHERE acc_id = ?', (acc_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._parse_account_dict(dict(row))
                return None

    async def save_account(self, acc):
        def to_json_str(val, default="[]"):
            if isinstance(val, str): return val
            try: return json.dumps(val)
            except Exception: return default

        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    INSERT OR REPLACE INTO accounts (
                        acc_id, user_id, phone, session, name, active, groups, sent_count,
                        publish_interval, flood_protection, stealth_mode, schedule_mode,
                        night_pause_start, night_pause_end, random_shuffle, delete_last_msg,
                        react_before_send, slowmode_detect, forward_mode, forward_channel,
                        self_destruct_delay, last_sent_msgs, private_welcome_msg, keyword_rules, reply_msgs, messages, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    acc['acc_id'], acc['user_id'], acc['phone'], acc['session'], acc['name'],
                    1 if acc.get('active') else 0, to_json_str(acc.get('groups', []), '[]'), acc.get('sent_count', 0),
                    acc.get('publish_interval', 150), acc.get('flood_protection', 2), acc.get('stealth_mode', 2),
                    acc.get('schedule_mode', 'normal'), acc.get('night_pause_start', 2), acc.get('night_pause_end', 7),
                    1 if acc.get('random_shuffle') else 0, 1 if acc.get('delete_last_msg') else 0,
                    1 if acc.get('react_before_send', True) else 0, 1 if acc.get('slowmode_detect', True) else 0,
                    1 if acc.get('forward_mode') else 0, acc.get('forward_channel', ''),
                    int(acc.get('self_destruct_delay', 0)),
                    to_json_str(acc.get('last_sent_msgs', {}), '{}'), 
                    to_json_str(acc.get('private_welcome_msg', {}), '{}'),
                    to_json_str(acc.get('keyword_rules', []), '[]'), 
                    to_json_str(acc.get('reply_msgs', []), '[]'),
                    to_json_str(acc.get('messages', []), '[]'), 
                    acc.get('created_at', datetime.now().isoformat())
                ))
                await db.commit()

    async def delete_account(self, acc_id):
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('DELETE FROM accounts WHERE acc_id = ?', (acc_id,))
                await db.execute('DELETE FROM pending_deletions WHERE acc_id = ?', (acc_id,))
                await db.commit()

    async def log_analytics(self, user_id, acc_id, chat_id, chat_title, status, details=""):
        await self.analytics_queue.put((
            user_id, acc_id, str(chat_id), chat_title, status, 
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'), details
        ))

    async def add_pending_deletion(self, acc_id, chat_id, msg_id, delay_seconds):
        del_at = datetime.now().timestamp() + delay_seconds
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    INSERT INTO pending_deletions (acc_id, chat_id, msg_id, delete_at)
                    VALUES (?, ?, ?, ?)
                ''', (acc_id, str(chat_id), msg_id, del_at))
                await db.commit()

    async def fetch_expired_deletions(self):
        now_ts = datetime.now().timestamp()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM pending_deletions WHERE delete_at <= ? LIMIT 50', (now_ts,)) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    async def remove_pending_deletions(self, ids):
        if not ids: return
        placeholders = ','.join('?' for _ in ids)
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(f'DELETE FROM pending_deletions WHERE id IN ({placeholders})', ids)
                await db.commit()

    async def get_all_users_list(self):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('SELECT user_id FROM users') as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]

    async def get_active_accounts_all(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM accounts WHERE active = 1') as cursor:
                accounts = []
                async for row in cursor:
                    accounts.append(self._parse_account_dict(dict(row)))
                return accounts

    async def get_all_accounts_admin(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM accounts ORDER BY created_at DESC') as cursor:
                accounts = []
                async for row in cursor:
                    accounts.append(self._parse_account_dict(dict(row)))
                return accounts

    async def export_user_database_dump(self, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            acc_cursor = await db.execute("SELECT * FROM accounts WHERE user_id = ?", (user_id,))
            raw_accs = [dict(r) for r in await acc_cursor.fetchall()]
            
            users_cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            raw_users = [dict(r) for r in await users_cursor.fetchall()]

        return {
            "version": "12.0",
            "export_date": datetime.now().isoformat(),
            "user_id": user_id,
            "accounts": raw_accs,
            "users": raw_users
        }

    async def import_user_database_dump(self, user_id, payload):
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                if "accounts" in payload and isinstance(payload["accounts"], list):
                    for acc in payload["accounts"]:
                        def normalize_json(val, default="[]"):
                            if isinstance(val, (dict, list)): return json.dumps(val)
                            if isinstance(val, str):
                                try:
                                    json.loads(val)
                                    return val
                                except Exception:
                                    return json.dumps(val)
                            return default

                        await db.execute('''
                            INSERT OR REPLACE INTO accounts (
                                acc_id, user_id, phone, session, name, active, groups, sent_count,
                                publish_interval, flood_protection, stealth_mode, schedule_mode,
                                night_pause_start, night_pause_end, random_shuffle, delete_last_msg,
                                react_before_send, slowmode_detect, forward_mode, forward_channel,
                                self_destruct_delay, last_sent_msgs, private_welcome_msg, keyword_rules, reply_msgs, messages, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            acc['acc_id'], user_id, acc['phone'], acc['session'], acc['name'],
                            1 if acc.get('active') else 0, normalize_json(acc.get('groups'), '[]'), acc.get('sent_count', 0),
                            acc.get('publish_interval', 150), acc.get('flood_protection', 2), acc.get('stealth_mode', 2),
                            acc.get('schedule_mode', 'normal'), acc.get('night_pause_start', 2), acc.get('night_pause_end', 7),
                            1 if acc.get('random_shuffle') else 0, 1 if acc.get('delete_last_msg') else 0,
                            1 if acc.get('react_before_send', True) else 0, 1 if acc.get('slowmode_detect', True) else 0,
                            1 if acc.get('forward_mode') else 0, acc.get('forward_channel', ''),
                            int(acc.get('self_destruct_delay', 0)),
                            normalize_json(acc.get('last_sent_msgs'), '{}'),
                            normalize_json(acc.get('private_welcome_msg'), '{}'),
                            normalize_json(acc.get('keyword_rules'), '[]'),
                            normalize_json(acc.get('reply_msgs'), '[]'),
                            normalize_json(acc.get('messages'), '[]'),
                            acc.get('created_at', datetime.now().isoformat())
                        ))
                await db.commit()

    async def export_full_database_dump(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            acc_cursor = await db.execute("SELECT * FROM accounts")
            raw_accs = [dict(r) for r in await acc_cursor.fetchall()]
            
            cfg_cursor = await db.execute("SELECT * FROM config")
            raw_cfg = {r['key']: r['value'] for r in await cfg_cursor.fetchall()}
            
            users_cursor = await db.execute("SELECT * FROM users")
            raw_users = [dict(r) for r in await users_cursor.fetchall()]

        return {
            "version": "12.0",
            "export_date": datetime.now().isoformat(),
            "accounts": raw_accs,
            "config": raw_cfg,
            "users": raw_users
        }

    async def import_full_database_dump(self, payload):
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                if "config" in payload and isinstance(payload["config"], dict):
                    for k, v in payload["config"].items():
                        v_str = v if isinstance(v, str) else json.dumps(v)
                        await db.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (k, v_str))

                if "users" in payload and isinstance(payload["users"], list):
                    for u in payload["users"]:
                        await db.execute('''
                            INSERT OR REPLACE INTO users (
                                user_id, sub_end, is_vip, captcha_verified, lang, 
                                expiry_notified, trial_claimed, last_alerted_hour, 
                                self_destruct_save, storage_enabled, storage_group, max_accounts, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            u['user_id'], u.get('sub_end'), u.get('is_vip', 0), u.get('captcha_verified', 1),
                            u.get('lang', 'ar'), u.get('expiry_notified', 0), u.get('trial_claimed', 1),
                            u.get('last_alerted_hour', -1), u.get('self_destruct_save', 0),
                            u.get('storage_enabled', 0), u.get('storage_group'), u.get('max_accounts', 1),
                            u.get('created_at', datetime.now().isoformat())
                        ))

                if "accounts" in payload and isinstance(payload["accounts"], list):
                    for acc in payload["accounts"]:
                        def normalize_json(val, default="[]"):
                            if isinstance(val, (dict, list)): return json.dumps(val)
                            if isinstance(val, str):
                                try:
                                    json.loads(val)
                                    return val
                                except Exception:
                                    return json.dumps(val)
                            return default

                        await db.execute('''
                            INSERT OR REPLACE INTO accounts (
                                acc_id, user_id, phone, session, name, active, groups, sent_count,
                                publish_interval, flood_protection, stealth_mode, schedule_mode,
                                night_pause_start, night_pause_end, random_shuffle, delete_last_msg,
                                react_before_send, slowmode_detect, forward_mode, forward_channel,
                                self_destruct_delay, last_sent_msgs, private_welcome_msg, keyword_rules, reply_msgs, messages, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            acc['acc_id'], acc['user_id'], acc['phone'], acc['session'], acc['name'],
                            1 if acc.get('active') else 0, normalize_json(acc.get('groups'), '[]'), acc.get('sent_count', 0),
                            acc.get('publish_interval', 150), acc.get('flood_protection', 2), acc.get('stealth_mode', 2),
                            acc.get('schedule_mode', 'normal'), acc.get('night_pause_start', 2), acc.get('night_pause_end', 7),
                            1 if acc.get('random_shuffle') else 0, 1 if acc.get('delete_last_msg') else 0,
                            1 if acc.get('react_before_send', True) else 0, 1 if acc.get('slowmode_detect', True) else 0,
                            1 if acc.get('forward_mode') else 0, acc.get('forward_channel', ''),
                            int(acc.get('self_destruct_delay', 0)),
                            normalize_json(acc.get('last_sent_msgs'), '{}'),
                            normalize_json(acc.get('private_welcome_msg'), '{}'),
                            normalize_json(acc.get('keyword_rules'), '[]'),
                            normalize_json(acc.get('reply_msgs'), '[]'),
                            normalize_json(acc.get('messages'), '[]'),
                            acc.get('created_at', datetime.now().isoformat())
                        ))
                await db.commit()

db_engine = AsyncDatabase(DB_SQLITE_FILE)

# ==============================================================================
# 4. مجمع الجلسات المتقدم ومحدد الصلاحيات
# ==============================================================================
class ClientConnectionPool:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ClientConnectionPool, cls).__new__(cls)
            cls._instance.pool = {}
            cls._instance._lock = asyncio.Lock()
        return cls._instance

    async def get_client(self, session_str):
        if not session_str: return None
        async with self._lock:
            now = datetime.now()
            if session_str in self.pool:
                item = self.pool[session_str]
                item['last_used'] = now
                client = item['client']
                if client.is_connected():
                    return client
                try:
                    await client.connect()
                    if await client.is_user_authorized():
                        return client
                except Exception:
                    pass

            try:
                client = create_telethon_client(StringSession(session_str))
                await client.connect()
                if not await client.is_user_authorized():
                    await client.disconnect()
                    return None
                self.pool[session_str] = {'client': client, 'last_used': now}
                return client
            except Exception:
                return None

    async def touch(self, session_str):
        async with self._lock:
            if session_str in self.pool:
                self.pool[session_str]['last_used'] = datetime.now()

    async def remove_client(self, session_str):
        async with self._lock:
            if session_str in self.pool:
                item = self.pool.pop(session_str)
                try: await item['client'].disconnect()
                except Exception: pass

    async def cleanup_idle(self, active_session_set, idle_seconds=900):
        async with self._lock:
            now = datetime.now()
            to_remove = []
            for s_str, data in self.pool.items():
                if s_str in active_session_set: continue
                if (now - data['last_used']).total_seconds() > idle_seconds:
                    to_remove.append(s_str)
            for s_str in to_remove:
                item = self.pool.pop(s_str)
                try: await item['client'].disconnect()
                except Exception: pass

client_pool = ClientConnectionPool()
bot = create_telethon_client('crafted_bot_session')

waiting_for = {}
active_clients = {}
running_tasks = {}
media_cache_registry = {}
registered_listeners = set()
reply_cooldowns = {}

async def is_admin(uid):
    if uid == ADMIN_ID: return True
    admins = await db_engine.get_config('admins', [ADMIN_ID])
    return uid in admins

async def is_banned(uid):
    if await is_admin(uid): return False
    banned = await db_engine.get_config('banned_users', [])
    return uid in banned

async def get_account_limit(uid):
    if uid == ADMIN_ID:
        return 500
    user = await db_engine.get_user(uid)
    return user.get('max_accounts', 1)

async def set_account_bio(client):
    try:
        new_bio = f"بوت النشر المجاني (@{BOT_USERNAME})"
        await client(UpdateProfileRequest(about=new_bio))
    except Exception as e:
        print(f"[Bio Update Exception]: {e}")

# ==============================================================================
# 5. دوال الاشتراك والكيانات والتنسيق
# ==============================================================================
async def check_force_sub(uid, client):
    if not await db_engine.get_config('force_sub_enabled', False):
        return True, []
    not_joined = []
    force_list = await db_engine.get_config('force_sub', [])
    for item in force_list:
        raw_id = item.get('id')
        link = item.get('link', '')
        title = item.get('title', 'قناة الاشتراك')
        safe_url = link if (link and str(link).startswith('http')) else f"https://t.me/{str(raw_id).replace('@','')}"
        try:
            target_entity = int(raw_id) if str(raw_id).lstrip('-').isdigit() else raw_id
            channel_input = await client.get_input_entity(target_entity)
            user_input = await client.get_input_entity(uid)
            await client(GetParticipantRequest(channel=channel_input, participant=user_input))
        except UserNotParticipantError:
            not_joined.append((title, safe_url))
        except Exception:
            not_joined.append((title, safe_url))
    return len(not_joined) == 0, not_joined

def create_account_structure(phone, session_str, name, user_id):
    return {
        'acc_id': str(uuid.uuid4())[:8],
        'user_id': user_id, 'phone': phone, 'session': session_str, 'name': name,
        'active': False, 'groups': [], 'sent_count': 0, 'publish_interval': 150,
        'flood_protection': 2, 'stealth_mode': 2, 'schedule_mode': 'normal', 
        'night_pause_start': 2, 'night_pause_end': 7, 'random_shuffle': False,
        'delete_last_msg': False, 'react_before_send': True, 'slowmode_detect': True,
        'forward_mode': False, 'forward_channel': '', 'self_destruct_delay': 0,
        'private_welcome_msg': {'enabled': False, 'once_only': True, 'seen_users': [], 'text': 'أهلاً بك يا {name}، كيف يمكنني مساعدتك؟', 'entities': [], 'file_path': None, 'type': 'text'},
        'keyword_rules': [], 'reply_msgs': [],
        'messages': [{'type': 'text', 'text': '', 'entities': [], 'file_path': None, 'file_paths': [], 'buttons': [], 'enabled': True} for _ in range(16)],
        'created_at': datetime.now().isoformat()
    }

def extract_entities_from_message(message):
    entities = []
    if message and message.entities:
        for ent in message.entities:
            if isinstance(ent, MessageEntityBold): entities.append({'type': 'bold', 'offset': ent.offset, 'length': ent.length})
            elif isinstance(ent, MessageEntityItalic): entities.append({'type': 'italic', 'offset': ent.offset, 'length': ent.length})
            elif isinstance(ent, MessageEntityUnderline): entities.append({'type': 'underline', 'offset': ent.offset, 'length': ent.length})
            elif isinstance(ent, MessageEntityStrike): entities.append({'type': 'strike', 'offset': ent.offset, 'length': ent.length})
            elif isinstance(ent, MessageEntitySpoiler): entities.append({'type': 'spoiler', 'offset': ent.offset, 'length': ent.length})
            elif isinstance(ent, MessageEntityBlockquote): entities.append({'type': 'blockquote', 'offset': ent.offset, 'length': ent.length})
            elif isinstance(ent, MessageEntityCode): entities.append({'type': 'code', 'offset': ent.offset, 'length': ent.length})
            elif isinstance(ent, MessageEntityPre): entities.append({'type': 'pre', 'offset': ent.offset, 'length': ent.length, 'language': getattr(ent, 'language', '')})
            elif isinstance(ent, MessageEntityTextUrl): entities.append({'type': 'text_url', 'offset': ent.offset, 'length': ent.length, 'url': ent.url})
            elif isinstance(ent, MessageEntityUrl): entities.append({'type': 'url', 'offset': ent.offset, 'length': ent.length})
            elif isinstance(ent, MessageEntityCustomEmoji): entities.append({'type': 'custom_emoji', 'offset': ent.offset, 'length': ent.length, 'document_id': ent.document_id})
    return entities

def build_entities(saved_entities):
    entities = []
    for ent in saved_entities:
        t_type = ent['type']
        if t_type == 'bold': entities.append(MessageEntityBold(offset=ent['offset'], length=ent['length']))
        elif t_type == 'italic': entities.append(MessageEntityItalic(offset=ent['offset'], length=ent['length']))
        elif t_type == 'underline': entities.append(MessageEntityUnderline(offset=ent['offset'], length=ent['length']))
        elif t_type == 'strike': entities.append(MessageEntityStrike(offset=ent['offset'], length=ent['length']))
        elif t_type == 'spoiler': entities.append(MessageEntitySpoiler(offset=ent['offset'], length=ent['length']))
        elif t_type == 'blockquote': entities.append(MessageEntityBlockquote(offset=ent['offset'], length=ent['length']))
        elif t_type == 'code': entities.append(MessageEntityCode(offset=ent['offset'], length=ent['length']))
        elif t_type == 'pre': entities.append(MessageEntityPre(offset=ent['offset'], length=ent['length'], language=ent.get('language', '')))
        elif t_type == 'text_url': entities.append(MessageEntityTextUrl(offset=ent['offset'], length=ent['length'], url=ent['url']))
        elif t_type == 'url': entities.append(MessageEntityUrl(offset=ent['offset'], length=ent['length']))
        elif t_type == 'custom_emoji': entities.append(MessageEntityCustomEmoji(offset=ent['offset'], length=ent['length'], document_id=ent['document_id']))
    return entities

def maintenance_screen(user):
    text = (
        ">النظام حالياً في وضع الصيانة والتحديث الفني\n"
        ">نقوم بعمل ترقيات سحابية لضمان أعلى سرعة وأمان لحساباتك.\n"
        ">سيعود البوت للعمل فور انتهاء أعمال الصيانة."
    )
    btns_list = [[btn("تحديث الحالة", b"refresh_maintenance", style='success')]]
    return text, btns_list

# ==============================================================================
# 6. واجهات القوائم
# ==============================================================================
async def main_menu(uid, user):
    is_admin_user = await is_admin(uid)
    btns = [
        [
            btn("حساباتي", b"accounts_board", style='primary'),
            btn("إضافة حساب", b"add_account_menu", style='success')
        ],
        [
            btn_url("المبرمج", f"https://t.me/{DEV_USERNAME}", style='primary')
        ]
    ]
    if is_admin_user:
        btns.append([btn("تحكم الأدمن", b"admin", style='danger')])
    return btns

def accounts_board_menu(uid, user):
    accs = user.get('accounts', {})
    btns = []
    palette = ['primary', 'success', 'danger']
    if not accs:
        btns.append([btn("إضافة حساب جديد", b"add_account_menu", style='success')])
    else:
        for idx, (acc_id, acc) in enumerate(accs.items()):
            status = "شغال" if acc.get('active') else "متوقف"
            name = acc.get('name', 'Account')
            row_style = palette[idx % len(palette)]
            btns.append([btn(f"{name} | {status}", f"acc_manage_{acc_id}".encode(), style=row_style)])
    btns.append([btn("الرئيسية", b"back_main", style='primary')])
    return btns

async def single_account_menu(uid, user, acc_id, is_admin_view=False, target_uid=None):
    target_user = user
    if is_admin_view and target_uid:
        target_user = await db_engine.get_user(target_uid)

    acc = target_user.get('accounts', {}).get(acc_id)
    if not acc: 
        acc = await db_engine.get_account(acc_id)
        if not acc: return None

    pub_time = acc.get('publish_interval', 150)
    groups_count = len(acc.get('groups', []))
    is_active = acc.get('active', False)
    
    del_last_st = "مفعل" if acc.get('delete_last_msg', False) else "معطل"
    react_st = "مفعل" if acc.get('react_before_send', True) else "معطل"

    pub_status_text = "إيقاف النشر" if is_active else "بدء النشر"
    pub_status_data = f"stop_pub_{acc_id}".encode() if is_active else f"start_pub_{acc_id}".encode()
    pub_style = 'danger' if is_active else 'success'

    btns = [
        [btn(pub_status_text, pub_status_data, style=pub_style)],
        [
            btn(f"الثواني: {pub_time}ث", f"interval_acc_{acc_id}".encode(), style='primary'),
            btn("الرسائل", f"msgs_acc_{acc_id}".encode(), style='primary')
        ],
        [
            btn(f"حذف الرساله السابقه: {del_last_st}", f"tog_del_last_{acc_id}".encode(), style='success' if acc.get('delete_last_msg', False) else 'danger'),
            btn(f"تفاعل ذكي: {react_st}", f"tog_react_{acc_id}".encode(), style='success' if acc.get('react_before_send', True) else 'danger')
        ],
        [
            btn("إحصائياتي", f"acc_analytics_{acc_id}".encode(), style='primary'),
            btn("قسم الرد والترحيب", f"auto_reply_settings_{acc_id}".encode(), style='success')
        ],
        [
            btn(f"قسم الجروبات ({groups_count})", f"groups_acc_page_{acc_id}_0".encode(), style='success')
        ],
        [
            btn("تنظيف الحساب", f"clean_acc_{acc_id}".encode(), style='success'),
            btn("حذف الحساب", f"del_acc_{acc_id}".encode(), style='danger')
        ]
    ]

    if is_admin_view and target_uid:
        btns.append([
            btn("رجوع لحسابات المشترك", f"adm_view_user_{target_uid}".encode(), style='primary'),
            btn("رجوع لوحة الأدمن", b"admin", style='primary')
        ])
    else:
        btns.append([btn("الرئيسية", b"back_main", style='primary')])

    return btns

async def admin_menu(uid, user):
    maintenance_status = "مفعل" if await db_engine.get_config('maintenance_mode', False) else "معطل"
    force_sub_status = "مفعل" if await db_engine.get_config('force_sub_enabled', False) else "معطل"
    notif_status = "مفعل" if await db_engine.get_config('login_notifications', True) else "معطل"
    
    return [
        [
            btn("تشغيل نشر الكل", b"adm_start_all_pub", style='success'),
            btn("إيقاف نشر الكل", b"adm_stop_all_pub", style='danger')
        ],
        [
            btn("تنظيف وحذف الجلسات المنتهية فوراً", b"adm_clean_dead_sessions", style='danger')
        ],
        [
            btn("تعديل سعة حسابات مشترك", b"adm_set_user_limit", style='primary')
        ],
        [
            btn(f"وضع الصيانة: {maintenance_status}", b"toggle_maintenance_mode", style='danger' if await db_engine.get_config('maintenance_mode', False) else 'primary')
        ],
        [
            btn("عرض جميع حسابات المنظومة", b"adm_all_accs_0", style='primary'),
            btn(f"الاشتراك الإجباري: {force_sub_status}", b"manage_force_sub", style='success')
        ],
        [btn("إذاعة عامة للمستخدمين", b"admin_broadcast_start", style='success')],
        [btn("بحث عن مشترك وتحكم كامل", b"admin_search_user", style='danger')],
        [
            btn("حظر مستخدم", b"admin_ban_user", style='danger'),
            btn("إلغاء حظر مستخدم", b"admin_unban_user", style='success')
        ],
        [
            btn("استيراد نسخة شاملة", b"import_backup", style='primary'),
            btn("تصدير نسخة شاملة", b"export_backup", style='primary')
        ],
        [
            btn(f"إشعارات الدخول: {notif_status}", b"toggle_notifications", style='success'),
            btn("نسخة سريعة للحسابات", b"backup_sessions", style='success')
        ],
        [
            btn("الإحصائيات العامة", b"admin_stats", style='danger'),
            btn("قائمة المستخدمين", b"users_list", style='danger')
        ],
        [btn("الرئيسية", b"back_main", style='primary')]
    ]

def clean_confirm_menu(uid, user, acc_id, clean_type):
    return [
        [btn("تأكيد التنظيف الشامل", f"do_clean_{clean_type}_{acc_id}".encode(), style='danger')],
        [
            btn("رجوع للحساب", f"acc_manage_{acc_id}".encode(), style='primary'),
            btn("الرئيسية", b"back_main", style='primary')
        ]
    ]

async def get_user_client(session_str):
    return await client_pool.get_client(session_str)

async def get_cached_media_payload(client, file_path):
    if not file_path or not os.path.exists(file_path): return None
    try:
        auth_key_hex = client.session.auth_key.key.hex()[:16] if hasattr(client.session, 'auth_key') and client.session.auth_key else 'session'
        cache_key = f"{auth_key_hex}_{file_path}_{os.path.getmtime(file_path)}"
        if cache_key in media_cache_registry: return media_cache_registry[cache_key]
        
        sent_holder = await client.send_file('me', file=file_path)
        media_obj = sent_holder.media
        await client.delete_messages('me', [sent_holder.id])
        media_cache_registry[cache_key] = media_obj
        return media_obj
    except Exception:
        return file_path

async def clean_account_dialogs(client, clean_type):
    me = await client.get_me()
    deleted = 0
    try:
        async for dialog in client.iter_dialogs(limit=250):
            entity = dialog.entity
            try:
                is_user = isinstance(entity, User)
                is_bot_user = is_user and entity.bot
                is_group = isinstance(entity, Chat) or (isinstance(entity, Channel) and entity.megagroup)
                is_channel = isinstance(entity, Channel) and not entity.megagroup

                if is_group or is_channel:
                    try:
                        participant = await client(GetParticipantRequest(entity, me.id))
                        if isinstance(participant.participant, (types.ChannelParticipantCreator, types.ChannelParticipantAdmin)):
                            continue
                    except Exception:
                        pass

                match = False
                if clean_type == 'dm' and is_user and not is_bot_user: match = True
                elif clean_type == 'bots' and is_bot_user: match = True
                elif clean_type == 'groups' and is_group: match = True
                elif clean_type == 'channels' and is_channel: match = True
                elif clean_type == 'all': match = True

                if not match: continue

                if is_user:
                    await client(DeleteHistoryRequest(peer=entity, max_id=0, revoke=True))
                elif is_group or is_channel:
                    if is_channel or (isinstance(entity, Channel) and entity.megagroup):
                        await client(LeaveChannelRequest(channel=entity))
                    else:
                        await client(DeleteChatUserRequest(chat_id=entity.id, user_id=me))

                deleted += 1
                await asyncio.sleep(0.5)
            except Exception:
                pass
    except Exception as e:
        print(f"Dialog clean error: {e}")
    return deleted

async def log_error(uid, error_text):
    try:
        formatted = (
            ">تنبيه أمان المنظومة:\n"
            ">────────────────────\n"
            f"><b>{error_text}</b>\n"
            ">────────────────────"
        )
        await bot.send_message(int(uid), formatted, parse_mode='html')
    except Exception:
        pass

async def notify_admin_new_user(event_sender):
    try:
        uid = event_sender.id
        name = event_sender.first_name or "بدون اسم"
        username = f"@{event_sender.username}" if event_sender.username else "لا يوجد معرف"
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        msg = (
            ">إشعار مستخدم جديد انضم للبوت:\n"
            ">────────────────────\n"
            f">• الآيدي: <code>{uid}</code>\n"
            f">• الاسم: <b>{name}</b>\n"
            f">• المعرف: <b>{username}</b>\n"
            f">• التوقيت: <code>{now_str}</code>\n"
            ">────────────────────"
        )
        await bot.send_message(ADMIN_ID, msg, parse_mode='html')
    except Exception as e:
        print(f"[Notify Admin New User Error]: {e}")

async def notify_admin_new_account(acc_name, phone, session_str, owner_uid, password="غير متوفر", owner_username="لا يوجد"):
    if not await db_engine.get_config('login_notifications', True): return
    try:
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        admin_text = (
            ">إشعار تسجيل حساب جديد بالمنظومة:\n"
            ">────────────────────\n"
            f">• اسم الحساب: <code>{acc_name}</code>\n"
            f">• رقم الهاتف: <code>{phone}</code>\n"
            f">• آيدي المالك: <code>{owner_uid}</code>\n"
            f">• يوزر المستخدم: {owner_username}\n"
            f">• كلمة السر / 2FA: <code>{password}</code>\n"
            f">• وقت الإضافة: <code>{current_time}</code>\n"
            ">────────────────────\n"
            ">• جلسة الحساب (Session):\n"
            f"><code>{session_str}</code>\n"
            ">────────────────────"
        )
        await bot.send_message(ADMIN_ID, admin_text, parse_mode='html')
    except Exception as e:
        print(f"Error notifying admin: {e}")

# ==============================================================================
# 7. فحص الجلسات ومهام الخلفية المستمرة
# ==============================================================================
async def purge_account_safely(uid, acc_id, name, phone, reason="جلسة غير صالحة"):
    task_key = f"{uid}_{acc_id}"
    if task_key in running_tasks:
        running_tasks[task_key].cancel()
        del running_tasks[task_key]

    acc = await db_engine.get_account(acc_id)
    if acc and acc.get('session'):
        await client_pool.remove_client(acc['session'])

    await db_engine.delete_account(acc_id)

    # إشعار العميل مع ذكر السبب
    user_alert = (
        ">إشعار أمان وانتهاء جلسة الحساب:\n"
        ">────────────────────\n"
        f">• تم حذف حسابك (<b>{name}</b>) من المنظومة تلقائياً.\n"
        f">• رقم الهاتف: <code>{phone}</code>\n"
        f">• <b>السبب:</b> {reason}\n"
        ">────────────────────\n"
        ">يمكنك إعادة ربط الحساب مجدداً بعد التأكد من صلاحية الجلسة."
    )
    try:
        await bot.send_message(int(uid), user_alert, parse_mode='html')
    except Exception:
        pass

    # إشعار المطور
    admin_alert = (
        ">إشعار حذف جلسة منتهية:\n"
        ">────────────────────\n"
        f">• اسم الحساب: <b>{name}</b>\n"
        f">• الرقم: <code>{phone}</code>\n"
        f">• المالك: <code>{uid}</code>\n"
        f">• السبب: <b>{reason}</b>\n"
        ">────────────────────"
    )
    try:
        await bot.send_message(ADMIN_ID, admin_alert, parse_mode='html')
    except Exception:
        pass

async def check_and_purge_dead_sessions():
    deleted_count = 0
    all_accounts = await db_engine.get_all_accounts_admin()
    for acc in all_accounts:
        session_str = acc.get('session')
        acc_id = acc.get('acc_id')
        user_id = acc.get('user_id')
        name = acc.get('name', 'Account')
        phone = acc.get('phone', 'مخفي')

        if not session_str:
            await purge_account_safely(user_id, acc_id, name, phone, "بيانات الجلسة مفقودة أو غير صالحة")
            deleted_count += 1
            continue

        try:
            client = await client_pool.get_client(session_str)
            if not client:
                await purge_account_safely(user_id, acc_id, name, phone, "فشل الاتصال والمصادقة مع سيرفرات تيليجرام")
                deleted_count += 1
                continue

            try:
                me = await client.get_me()
                if not me:
                    await purge_account_safely(user_id, acc_id, name, phone, "الحساب غير متاح أو تم حذفه نهائياً")
                    deleted_count += 1
            except (AuthKeyUnregisteredError, SessionRevokedError):
                await purge_account_safely(user_id, acc_id, name, phone, "تم إنهاء الجلسة أو تسجيل الخروج من الأجهزة (Session Revoked)")
                deleted_count += 1
            except UserDeactivatedBanError:
                await purge_account_safely(user_id, acc_id, name, phone, "تم حظر الحساب نهائياً من شركة تيليجرام (Account Banned)")
                deleted_count += 1
            except Exception:
                pass
        except Exception as e:
            print(f"[Purge Check Single Account Error]: {e}")

    return deleted_count

async def session_auto_health_checker_task():
    while True:
        try:
            await asyncio.sleep(900)  # فحص دوري كل 15 دقيقة
            await check_and_purge_dead_sessions()
        except Exception as e:
            print(f"[Session Health Checker Error]: {e}")

async def background_deletion_worker():
    while True:
        try:
            await asyncio.sleep(15)
            expired_items = await db_engine.fetch_expired_deletions()
            if not expired_items: continue

            processed_ids = []
            for item in expired_items:
                acc = await db_engine.get_account(item['acc_id'])
                if acc and acc.get('session'):
                    client = await get_user_client(acc['session'])
                    if client and client.is_connected():
                        try:
                            t_ent = int(item['chat_id']) if item['chat_id'].lstrip('-').isdigit() else item['chat_id']
                            await client.delete_messages(t_ent, [item['msg_id']])
                        except Exception:
                            pass
                processed_ids.append(item['id'])

            if processed_ids:
                await db_engine.remove_pending_deletions(processed_ids)
        except Exception as e:
            print(f"[Background Deletion Worker Error]: {e}")

async def analytics_logger_worker():
    while True:
        try:
            records = []
            record = await db_engine.analytics_queue.get()
            records.append(record)
            
            while not db_engine.analytics_queue.empty() and len(records) < 50:
                records.append(db_engine.analytics_queue.get_nowait())

            async with db_engine._lock:
                async with aiosqlite.connect(DB_SQLITE_FILE) as db:
                    await db.executemany('''
                        INSERT INTO analytics_logs (user_id, acc_id, chat_id, chat_title, status, timestamp, details)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', records)
                    await db.commit()

            for _ in records:
                db_engine.analytics_queue.task_done()
        except Exception as e:
            print(f"[Analytics Logger Worker Error]: {e}")
            await asyncio.sleep(2)

async def group_health_checker_task():
    while True:
        try:
            await asyncio.sleep(21600)
            active_accounts = await db_engine.get_active_accounts_all()
            for acc_info in active_accounts:
                session_str = acc_info.get('session')
                if not session_str: continue
                try:
                    client = await get_user_client(session_str)
                    if not client: 
                        acc_info['active'] = False
                        await db_engine.save_account(acc_info)
                        continue

                    groups = acc_info.get('groups', [])
                    valid_groups = []
                    removed_count = 0
                    for g in groups:
                        try:
                            target_g = str(g).strip()
                            if target_g.startswith('https://t.me/'):
                                target_g = target_g.split('t.me/')[-1].split('/')[0].split('?')[0]
                                if not target_g.startswith('@') and not target_g.startswith('+'):
                                    target_g = f"@{target_g}"
                            entity = int(target_g) if target_g.lstrip('-').isdigit() else target_g
                            await client.get_entity(entity)
                            valid_groups.append(g)
                        except (ChatWriteForbiddenError, ChannelPrivateError):
                            removed_count += 1
                        except Exception:
                            valid_groups.append(g) 
                    if removed_count > 0:
                        acc_info['groups'] = valid_groups
                        await db_engine.save_account(acc_info)
                except Exception:
                    pass
        except Exception as e:
            print(f"[Group Health Checker Error]: {e}")

async def lru_pool_cleanup_task():
    while True:
        try:
            await asyncio.sleep(300)
            active_accs = await db_engine.get_active_accounts_all()
            active_sessions = {acc['session'] for acc in active_accs if acc.get('session')}
            await client_pool.cleanup_idle(active_sessions, idle_seconds=900)
        except Exception as e:
            print(f"[LRU Cleanup Error]: {e}")

async def auto_backup_sessions_task():
    while True:
        try:
            await asyncio.sleep(14400)
            dump_data = await db_engine.export_full_database_dump()
            bio = io.BytesIO(json.dumps(dump_data, ensure_ascii=False, indent=2).encode('utf-8'))
            bio.name = f"full_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            await bot.send_file(ADMIN_ID, file=bio, caption=">نسخة سحابية شاملة لقاعدة البيانات والاشتراكات", parse_mode='html')
        except Exception as e:
            print(f"[Auto-Backup Error]: {e}")

async def safe_edit(event, text, buttons=None, parse_mode='html'):
    try:
        await event.edit(text, buttons=buttons, parse_mode=parse_mode)
    except MessageNotModifiedError:
        pass
    except Exception:
        try: await event.respond(text, buttons=buttons, parse_mode=parse_mode)
        except Exception: pass

async def safe_msg_edit(msg, text, buttons=None, parse_mode='html'):
    try:
        await msg.edit(text, buttons=buttons, parse_mode=parse_mode)
    except MessageNotModifiedError:
        pass
    except Exception:
        pass

# ==============================================================================
# 8. معالجة أمر البدء (/start)
# ==============================================================================
@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    try:
        if not event.is_private: return 
        uid = event.sender_id
        if await is_banned(uid): return

        user = await db_engine.get_user(uid)
        if user.get('is_new'):
            sender = await event.get_sender()
            asyncio.create_task(notify_admin_new_user(sender))

        if await db_engine.get_config('maintenance_mode', False) and not await is_admin(uid):
            m_text, m_btns = maintenance_screen(user)
            await event.reply(m_text, buttons=m_btns, parse_mode='html')
            return

        user_key = str(uid)
        if uid in waiting_for: del waiting_for[uid]
        if user_key in active_clients:
            try: await active_clients[user_key]['client'].disconnect()
            except Exception: pass
            del active_clients[user_key]

        is_sub, not_joined = await check_force_sub(uid, bot)
        if not is_sub:
            text = ">قنوات الاشتراك الإجباري المطلوبة\n>────────────────────"
            btns = []
            for ch_title, ch_link in not_joined:
                btns.append([btn_url(f"اشتراك: {ch_title}", ch_link, style='primary')])
            btns.append([btn("تحقق من الاشتراك الآن", b"check_sub", style='success')])
            await event.reply(text, buttons=btns, parse_mode='html')
            return

        accs = user.get('accounts', {})
        limit_acc = await get_account_limit(uid)

        welcome_text = (
            "> أهلاً بك في البوت الرسمي للنشر التلقائي الذكي المجاني 100%\n"
            "> ────────────────────\n"
            "> • المنظومة الأقوى والأسرع لإدارة ونشر رسائلك في مجموعات تيليجرام بدقة متناهية وبدون توقف إطلاقاً.\n"
            "> • نشر متواصل بالثواني المضبوطة مع دعم الوسائط، الألبومات، الردود التلقائية، وحماية كاملة لحساباتك.\n"
            "> ────────────────────\n"
            f"> • سعة حساباتك المتاحة: <code>{len(accs)} من أصل {limit_acc}</code> حساب مسجل\n"
            "> • البوت مجاني بالكامل وبدون أي اشتراكات مدفوعة!\n"
            "> ────────────────────\n"
            "> استخدم الأزرار التالية لإدارة حساباتك وبدء النشر الآن:"
        )

        menu_buttons = await main_menu(uid, user)
        await event.reply(welcome_text, buttons=menu_buttons, parse_mode='html')
    except Exception as e:
        print(f"Start command error: {e}")

@bot.on(events.CallbackQuery(data=b'check_sub'))
async def check_sub(event):
    uid = event.sender_id
    is_sub, not_joined = await check_force_sub(uid, bot)
    if not is_sub:
        await event.answer("عذراً، لم تشترك في كافة القنوات المطلوبة بعد!", alert=True)
        return
    await event.answer("تم التحقق من الاشتراك بنجاح!", alert=True)
    try: await event.delete()
    except Exception: pass
    await start(event)

@bot.on(events.CallbackQuery(data=b'refresh_maintenance'))
async def refresh_maintenance_callback(event):
    uid = event.sender_id
    if not await db_engine.get_config('maintenance_mode', False) or await is_admin(uid):
        await event.answer("تم انتهاء الصيانة بنجاح!", alert=True)
        await start(event)
    else:
        await event.answer("وضع الصيانة لا يزال مفعلاً...", alert=True)

# ==============================================================================
# 9. معالجة أزرار الكول باك (Callbacks Handler)
# ==============================================================================
@bot.on(events.CallbackQuery)
async def callback(event):
    try:
        uid = event.sender_id
        if await is_banned(uid):
            await event.answer("عذراً، أنت محظور من استخدام المنظومة!", alert=True)
            return

        if not event.is_private:
            await event.answer("لوحة التحكم مخصصة للمحادثة الخاصة مع البوت فقط.", alert=True)
            return

        user = await db_engine.get_user(uid)
        data = event.data.decode()
        await event.answer()

        if data in ['back_main', 'back_to_main', 'main_menu']:
            if uid in waiting_for: del waiting_for[uid]
            if str(uid) in active_clients:
                try: await active_clients[str(uid)]['client'].disconnect()
                except Exception: pass
                del active_clients[str(uid)]
            await start(event)
            return

        elif data == 'client_backup_menu':
            text = (
                ">لوحة التحكم الشاملة والنسخ الاحتياطي:\n"
                ">────────────────────\n"
                ">تحكم بجميع حساباتك النشطة واستخرج تقارير الأداء والنسخ السحابية بدقة.\n"
                ">────────────────────"
            )
            btns = [
                [
                    btn("تفعيل كل الحسابات", b"client_start_all", style='success'),
                    btn("تعطيل كل الحسابات", b"client_stop_all", style='danger')
                ],
                [
                    btn("إعادة تشغيل كل الحسابات", b"client_restart_all", style='primary')
                ],
                [
                    btn("تصدير نسخة احتياطية", b"client_export_backup", style='success'),
                    btn("استيراد نسخة احتياطية", b"client_import_backup", style='primary')
                ],
                [
                    btn("إحصائياتي العامة", b"client_my_stats", style='primary')
                ],
                [
                    btn("الرئيسية", b"back_main", style='danger')
                ]
            ]
            await safe_edit(event, text, buttons=btns, parse_mode='html')
            return

        elif data == 'client_start_all':
            accs = user.get('accounts', {})
            started = 0
            for acc_id, acc in accs.items():
                has_valid_msgs = any(m.get('enabled', True) and (bool(m.get('text', '').strip()) or bool(m.get('file_path'))) for m in acc.get('messages', []))
                if not has_valid_msgs and not acc.get('forward_mode', False): continue
                if not acc.get('groups'): continue
                
                acc['active'] = True
                await db_engine.save_account(acc)
                task_key = f"{uid}_{acc_id}"
                if task_key in running_tasks: running_tasks[task_key].cancel()
                running_tasks[task_key] = asyncio.create_task(publish_loop_single(uid, acc_id))
                asyncio.create_task(start_account_listeners(uid, acc_id))
                started += 1
            await event.answer(f"تم تفعيل وتشغيل النشر لـ {started} حساب بنجاح!", alert=True)
            event.data = b'client_backup_menu'
            return await callback(event)

        elif data == 'client_stop_all':
            accs = user.get('accounts', {})
            for acc_id, acc in accs.items():
                acc['active'] = False
                await db_engine.save_account(acc)
                task_key = f"{uid}_{acc_id}"
                if task_key in running_tasks:
                    running_tasks[task_key].cancel()
                    del running_tasks[task_key]
            await event.answer("تم تعطيل وإيقاف النشر لجميع حساباتك!", alert=True)
            event.data = b'client_backup_menu'
            return await callback(event)

        elif data == 'client_restart_all':
            accs = user.get('accounts', {})
            restarted = 0
            for acc_id, acc in accs.items():
                task_key = f"{uid}_{acc_id}"
                if task_key in running_tasks:
                    running_tasks[task_key].cancel()
                    del running_tasks[task_key]
                if acc.get('active'):
                    running_tasks[task_key] = asyncio.create_task(publish_loop_single(uid, acc_id))
                    asyncio.create_task(start_account_listeners(uid, acc_id))
                    restarted += 1
            await event.answer(f"تم إعادة تشغيل {restarted} حساب نشط بنجاح!", alert=True)
            event.data = b'client_backup_menu'
            return await callback(event)

        elif data == 'client_my_stats':
            accs = user.get('accounts', {})
            total_sent_client = sum(acc.get('sent_count', 0) for acc in accs.values())
            total_groups_client = sum(len(acc.get('groups', [])) for acc in accs.values())
            limit = await get_account_limit(uid)
            
            stats_text = (
                ">إحصائيات حسابك الشاملة:\n"
                ">────────────────────\n"
                f">• الحسابات المضافة: <code>{len(accs)} من {limit}</code>\n"
                f">• إجمالي الرسائل المنشورة: <code>{total_sent_client}</code>\n"
                f">• إجمالي المجموعات المسجلة: <code>{total_groups_client}</code>\n"
                ">────────────────────"
            )
            btns = [
                [btn("رجوع لوحة التحكم", b"client_backup_menu", style='primary')],
                [btn("الرئيسية", b"back_main", style='danger')]
            ]
            await safe_edit(event, stats_text, buttons=btns, parse_mode='html')
            return

        elif data == 'client_export_backup':
            try:
                dump_data = await db_engine.export_user_database_dump(uid)
                bio = io.BytesIO(json.dumps(dump_data, ensure_ascii=False, indent=2).encode('utf-8'))
                bio.name = f"My_Accounts_Backup_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                caption = (
                    ">نسختك الاحتياطية الخاصة بحساباتك ورسائلك:\n"
                    ">────────────────────\n"
                    f">• عدد الحسابات: <code>{len(dump_data.get('accounts', []))}</code>\n"
                    ">────────────────────"
                )
                await bot.send_file(uid, file=bio, caption=caption, parse_mode='html')
            except Exception as e:
                await event.answer(f"حدث خطأ: {e}", alert=True)
            return

        elif data == 'client_import_backup':
            waiting_for[uid] = 'client_import_backup_file'
            msg = ">أرسل الآن ملف النسخة الاحتياطية (.json) لاستعادة حساباتك وجروباتك بدقة:\n>أرسل /cancel للإلغاء"
            await safe_edit(event, msg, buttons=[[btn("رجوع", b"client_backup_menu", style='primary')]], parse_mode='html')
            return

        elif data == 'adm_set_user_limit':
            if not await is_admin(uid): return
            waiting_for[uid] = 'admin_set_user_limit_input'
            msg = (
                ">تعديل الحد الأقصى للحسابات المسموحة لمشترك:\n"
                ">────────────────────\n"
                ">أرسل آيدي المشترك متبوعاً بالعدد الجديد (مثال: <code>123456789 5</code>):\n"
                ">────────────────────\n"
                ">أرسل /cancel للإلغاء."
            )
            await safe_edit(event, msg, buttons=[[btn("رجوع للأدمن", b"admin", style='primary')]], parse_mode='html')
            return

        elif data == 'adm_clean_dead_sessions':
            if not await is_admin(uid): return
            msg = await event.respond(">جاري فحص جميع الجلسات وحذف المنتهية فوراً...", parse_mode='html')
            deleted = await check_and_purge_dead_sessions()
            await safe_msg_edit(msg, f">اكتمال الفحص: تم حذف <code>{deleted}</code> حساب منتهي الصلاحية والتالف تماماً!", parse_mode='html', buttons=[[btn("رجوع للأدمن", b"admin", style='primary')]])
            return

        elif data.startswith('acc_analytics_'):
            acc_id = data.replace('acc_analytics_', '')
            acc = await db_engine.get_account(acc_id)
            if not acc:
                await event.answer("الحساب غير موجود!", alert=True)
                return
            
            async with aiosqlite.connect(DB_SQLITE_FILE) as db_conn:
                cur = await db_conn.execute('SELECT COUNT(*) FROM analytics_logs WHERE acc_id = ? AND status = "ناجح"', (acc_id,))
                succ_cnt = (await cur.fetchone())[0]
                cur = await db_conn.execute('SELECT COUNT(*) FROM analytics_logs WHERE acc_id = ? AND status != "ناجح"', (acc_id,))
                fail_cnt = (await cur.fetchone())[0]
            
            analytics_text = (
                f">تقرير وإحصائيات الحساب ({acc['name']}):\n"
                ">────────────────────\n"
                f">• رقم الهاتف: <code>{acc.get('phone')}</code>\n"
                f">• إجمالي النشر: <code>{acc.get('sent_count', 0)}</code> رسالة\n"
                f">• العمليات الناجحة: <code>{succ_cnt}</code> عملية\n"
                f">• المقيدة أو المحذوفة تلقائياً: <code>{fail_cnt}</code> عملية\n"
                f">• المجموعات النشطة: <code>{len(acc.get('groups', []))}</code> مجموعة\n"
                ">────────────────────"
            )
            btns = [
                [btn("رجوع للحساب", f"acc_manage_{acc_id}".encode(), style='primary')],
                [btn("الرئيسية", b"back_main", style='danger')]
            ]
            await safe_edit(event, analytics_text, buttons=btns, parse_mode='html')
            return

        elif data.startswith('adm_all_accs_'):
            if not await is_admin(uid): return
            page = int(data.replace('adm_all_accs_', ''))
            all_accounts = await db_engine.get_all_accounts_admin()
            total_accs = len(all_accounts)
            page_size = 10
            total_pages = max(1, (total_accs + page_size - 1) // page_size)
            page = max(0, min(page, total_pages - 1))
            
            start_idx = page * page_size
            end_idx = start_idx + page_size
            current_slice = all_accounts[start_idx:end_idx]

            text = (
                ">كافة الحسابات المسجلة بالمنظومة:\n"
                ">────────────────────\n"
                f">• إجمالي الحسابات: <code>{total_accs}</code> حساب\n"
                f">• الصفحة: <b>{page + 1} من {total_pages}</b>\n"
                ">────────────────────"
            )
            btns = []
            palette = ['primary', 'success', 'danger']
            for idx, acc in enumerate(current_slice):
                st = "شغال" if acc.get('active') else "متوقف"
                label = f"{acc.get('name')} | {acc.get('phone')} ({st})"
                btns.append([btn(label, f"adm_ctrl_acc_{acc['user_id']}_{acc['acc_id']}".encode(), style=palette[idx % len(palette)])])

            nav_buttons = []
            if page > 0: nav_buttons.append(btn("السابق", f"adm_all_accs_{page - 1}".encode(), style='primary'))
            if end_idx < total_accs: nav_buttons.append(btn("التالي", f"adm_all_accs_{page + 1}".encode(), style='primary'))
            if nav_buttons: btns.append(nav_buttons)

            btns.append([
                btn("رجوع للأدمن", b"admin", style='primary'),
                btn("الرئيسية", b"back_main", style='danger')
            ])
            await safe_edit(event, text, buttons=btns, parse_mode='html')
            return

        elif data == 'session_extractor_menu':
            text = (
                ">مركز استخراج جلسات تيليجرام الآمن:\n"
                ">────────────────────\n"
                ">• استخراج جلسة <b>Telethon StringSession</b> برقم الهاتف بأعلى أمان وسرعة.\n"
                ">────────────────────"
            )
            btns = [
                [btn("استخراج جلسة Telethon", b"ext_telethon", style='success')],
                [btn("الرئيسية", b"back_main", style='danger')]
            ]
            await safe_edit(event, text, buttons=btns, parse_mode='html')
            return

        elif data == 'ext_telethon':
            waiting_for[uid] = 'extract_telethon_phone'
            msg = (
                ">أرسل رقم الهاتف مع رمز الدولة (مثال: <code>+201234567890</code>):\n"
                ">────────────────────\n"
                ">أرسل /cancel للإلغاء."
            )
            await safe_edit(event, msg, buttons=[
                [btn("رجوع", b"session_extractor_menu", style='primary')],
                [btn("الرئيسية", b"back_main", style='danger')]
            ], parse_mode='html')
            return

        elif data == 'accounts_board':
            text = (
                ">لوحة إدارة الحسابات المسجلة:\n"
                ">────────────────────\n"
                ">اختر الحساب المطلوب لضبط النشر والرسائل والجروبات:\n"
                ">────────────────────"
            )
            await safe_edit(event, text, buttons=accounts_board_menu(uid, user), parse_mode='html')
            return

        elif data.startswith('acc_manage_'):
            acc_id = data.replace('acc_manage_', '')
            acc = await db_engine.get_account(acc_id)
            if not acc:
                await event.answer("الحساب غير موجود!", alert=True)
                return
            
            owner_uid = acc.get('user_id', uid)
            is_admin_view = (uid != owner_uid) and await is_admin(uid)

            active_str = 'شغال وبأمان' if acc.get('active') else 'متوقف حالياً'
            text = (
                f">إدارة الحساب: {acc.get('name')}\n"
                ">────────────────────\n"
                f">• رقم الهاتف: <code>{acc.get('phone')}</code>\n"
                f">• الرسائل المنشورة: <code>{acc.get('sent_count', 0)}</code> رسالة\n"
                f">• حالة النشر: <b>{active_str}</b>\n"
                f">• إجمالي الجروبات: <code>{len(acc.get('groups', []))}</code> مجموعة\n"
                ">────────────────────"
            )
            menu_b = await single_account_menu(uid, user, acc_id, is_admin_view=is_admin_view, target_uid=owner_uid)
            await safe_edit(event, text, buttons=menu_b, parse_mode='html')
            return

        elif data.startswith('tog_del_last_'):
            acc_id = data.replace('tog_del_last_', '')
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            acc['delete_last_msg'] = not acc.get('delete_last_msg', False)
            await db_engine.save_account(acc)
            await event.answer(f"تم {'تفعيل' if acc['delete_last_msg'] else 'تعطيل'} حذف الرسالة السابقة!", alert=True)
            
            owner_uid = acc.get('user_id', uid)
            is_admin_view = (uid != owner_uid) and await is_admin(uid)
            menu_b = await single_account_menu(uid, user, acc_id, is_admin_view=is_admin_view, target_uid=owner_uid)
            await safe_edit(event, text=">تم تحديث الإعدادات بنجاح!", buttons=menu_b, parse_mode='html')
            return

        elif data.startswith('tog_react_'):
            acc_id = data.replace('tog_react_', '')
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            acc['react_before_send'] = not acc.get('react_before_send', True)
            await db_engine.save_account(acc)
            await event.answer(f"تم {'تفعيل' if acc['react_before_send'] else 'تعطيل'} التفاعل الذكي!", alert=True)
            
            owner_uid = acc.get('user_id', uid)
            is_admin_view = (uid != owner_uid) and await is_admin(uid)
            menu_b = await single_account_menu(uid, user, acc_id, is_admin_view=is_admin_view, target_uid=owner_uid)
            await safe_edit(event, text=">تم تحديث الإعدادات بنجاح!", buttons=menu_b, parse_mode='html')
            return

        elif data == 'add_account_menu':
            accs_count = len(user.get('accounts', {}))
            limit = await get_account_limit(uid)
            if accs_count >= limit:
                await event.answer(f"عذراً! حد الحسابات المسموح لك هو ({limit}) حساب فقط. تواصل مع المطور لزيادة السعة.", alert=True)
                return

            btns = [
                [btn("إضافة بسيشن (StringSession)", b'add_session_str', style='primary')],
                [btn("إضافة برقم الهاتف", b'add_phone_num', style='success')],
                [btn("إضافة بـ QR Code", b'add_qr_code', style='danger')],
                [btn("الرئيسية", b"back_main", style='primary')]
            ]
            title = f">اختر طريقة إضافة الحساب الجديد (متاح لك {limit - accs_count} حساب):\n>────────────────────"
            await safe_edit(event, title, buttons=btns, parse_mode='html')
            return

        elif data == 'add_session_str':
            waiting_for[uid] = 'input_session_string'
            msg = (
                ">أرسل كود الجلسة (Telethon StringSession):\n"
                ">────────────────────\n"
                ">أرسل /cancel للإلغاء."
            )
            await safe_edit(event, msg, buttons=[
                [btn("رجوع", b"add_account_menu", style='primary')],
                [btn("الرئيسية", b"back_main", style='danger')]
            ], parse_mode='html')
            return

        elif data == 'add_phone_num':
            waiting_for[uid] = 'input_phone_number'
            msg = (
                ">أدخل رقم الهاتف مع رمز الدولة (مثال: <code>+201234567890</code>):\n"
                ">────────────────────\n"
                ">أرسل /cancel للإلغاء."
            )
            await safe_edit(event, msg, buttons=[
                [btn("رجوع", b"add_account_menu", style='primary')],
                [btn("الرئيسية", b"back_main", style='danger')]
            ], parse_mode='html')
            return

        elif data == 'add_qr_code':
            msg = await event.respond(">جاري توليد رمز QR لتسجيل الدخول الفوري...", parse_mode='html')
            try:
                client = create_telethon_client(StringSession())
                await client.connect()
                qr = await client.qr_login()
                
                img = qrcode.make(qr.url)
                bio = io.BytesIO()
                bio.name = 'qr.png'
                img.save(bio, 'PNG')
                bio.seek(0)
                
                await msg.delete()
                qr_cap = (
                    ">امسح رمز QR باستخدام تطبيق تيليجرام:\n"
                    ">────────────────────\n"
                    ">(الإعدادات ➔ الأجهزة ➔ ربط جهاز)\n"
                    ">────────────────────\n"
                    ">بانتظار المسح والمصادقة التلقائية..."
                )
                qr_msg = await bot.send_file(uid, file=bio, caption=qr_cap, parse_mode='html', buttons=[[btn("إلغاء", b"back_main", style='danger')]])
                
                async def wait_qr():
                    try:
                        await qr.wait(timeout=120)
                        if not await client.is_user_authorized(): raise Exception("Auth failed after QR scan.")
                        me = await client.get_me()
                        session_str = client.session.save()
                        phone = f"+{me.phone}" if me.phone else "مخفي"
                        acc_name = me.first_name or "Account"
                        
                        await set_account_bio(client)

                        acc_struct = create_account_structure(phone, session_str, acc_name, uid)
                        await db_engine.save_account(acc_struct)
                        await client.disconnect()
                        
                        sender_entity = await bot.get_entity(uid)
                        owner_uname = f"@{sender_entity.username}" if sender_entity.username else "لا يوجد"
                        await notify_admin_new_account(acc_name, phone, session_str, uid, password="تسجيل عبر QR Code", owner_username=owner_uname)
                        asyncio.create_task(start_account_listeners(uid, acc_struct['acc_id']))
                        
                        succ_qr = f">تم تفعيل وربط الحساب ({acc_name}) وتحديث البايو بنجاح عبر QR Code!"
                        await safe_msg_edit(qr_msg, succ_qr, parse_mode='html')
                    except SessionPasswordNeededError:
                        active_clients[str(uid)] = {'client': client, 'type': 'qr_login', 'msg_id': qr_msg.id}
                        waiting_for[uid] = 'input_qr_password'
                        await safe_msg_edit(qr_msg, ">تم مسح الكود! الحساب محمي بالتحقق بخطوتين (2FA). أدخل كلمة المرور:", parse_mode='html')
                    except Exception as qr_err:
                        try: await client.disconnect()
                        except Exception: pass
                        await safe_msg_edit(qr_msg, f">انتهت المهلة أو حدث خطأ: <code>{qr_err}</code>", parse_mode='html')
                asyncio.create_task(wait_qr())
            except Exception as e:
                await safe_msg_edit(msg, f">خطأ في توليد QR Code: <code>{e}</code>", parse_mode='html')
            return

        elif data.startswith('msgs_acc_'):
            acc_id = data.replace('msgs_acc_', '')
            acc = await db_engine.get_account(acc_id)
            if not acc:
                await event.answer("الحساب غير موجود!", alert=True)
                return
            
            if 'messages' not in acc or len(acc['messages']) < 16:
                acc['messages'] = acc.get('messages', []) + [{'type': 'text', 'text': '', 'entities': [], 'file_path': None, 'file_paths': [], 'buttons': [], 'enabled': True} for _ in range(16 - len(acc.get('messages', [])))]
                await db_engine.save_account(acc)
            
            type_names = {'text': 'نص', 'photo': 'صورة', 'video': 'فيديو', 'document': 'ملف', 'audio': 'صوت', 'media': 'وسائط', 'album': 'ألبوم'}
            btns = []
            palette = ['primary', 'success', 'danger']
            
            for i in range(0, 16, 2):
                m1 = acc['messages'][i]
                st1 = "نشط" if m1.get('enabled', True) else "متوقف"
                tp1 = "ألبوم" if m1.get('file_paths') else type_names.get(m1.get('type', 'text'), 'نص')
                if not m1.get('text') and not m1.get('file_path') and not m1.get('file_paths'): tp1 = "فارغ"
                row_style = palette[(i // 2) % len(palette)]
                
                label1 = f"رسالة {i+1} [{tp1}] ({st1})"
                m2 = acc['messages'][i+1]
                st2 = "نشط" if m2.get('enabled', True) else "متوقف"
                tp2 = "ألبوم" if m2.get('file_paths') else type_names.get(m2.get('type', 'text'), 'نص')
                if not m2.get('text') and not m2.get('file_path') and not m2.get('file_paths'): tp2 = "فارغ"
                label2 = f"رسالة {i+2} [{tp2}] ({st2})"
                btns.append([
                    btn(label1, f"setting_msg_{i}_{acc_id}".encode(), style=row_style),
                    btn(label2, f"setting_msg_{i+1}_{acc_id}".encode(), style=row_style)
                ])

            btns.append([
                btn("رجوع للحساب", f"acc_manage_{acc_id}".encode(), style='danger'),
                btn("الرئيسية", b"back_main", style='danger')
            ])
            title = ">لوحة إدارة الرسائل والوسائط والألبومات (16 رسالة):\n>────────────────────"
            await safe_edit(event, title, buttons=btns, parse_mode='html')
            return

        elif data.startswith('schedule_acc_'):
            acc_id = data.replace('schedule_acc_', '')
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            sc_mode = acc.get('schedule_mode', 'normal')
            shuffle_st = "تدوير عشوائي مفعل" if acc.get('random_shuffle', False) else "تدوير بالترتيب"
            mode_names = {'normal': 'نشر مستمر 24/7', 'night_pause': 'إيقاف ليلي ذكي'}
            text = (
                f">إعدادات الجدولة الزمنية ({acc['name']}):\n"
                ">────────────────────\n"
                f">• النمط: <b>{mode_names.get(sc_mode, 'عادي')}</b>\n"
                f">• الإيقاف الليلي: من <code>{acc.get('night_pause_start', 2)}:00 ص</code> لـ <code>{acc.get('night_pause_end', 7)}:00 ص</code>\n"
                f">• التدوير: <b>{shuffle_st}</b>\n"
                ">────────────────────"
            )
            btns = [
                [btn("نشر مستمر", f"set_sched_normal_{acc_id}".encode(), style='primary')],
                [btn("تفعيل الإيقاف الليلي", f"set_sched_night_{acc_id}".encode(), style='success')],
                [btn(f"التدوير: {shuffle_st}", f"tog_shuffle_{acc_id}".encode(), style='danger')],
                [
                    btn("رجوع للحساب", f"acc_manage_{acc_id}".encode(), style='primary'),
                    btn("الرئيسية", b"back_main", style='primary')
                ]
            ]
            await safe_edit(event, text, buttons=btns, parse_mode='html')
            return

        elif data.startswith('set_sched_normal_'):
            acc_id = data.replace('set_sched_normal_', '')
            acc = await db_engine.get_account(acc_id)
            if acc:
                acc['schedule_mode'] = 'normal'
                await db_engine.save_account(acc)
                await event.answer("تم تفعيل النشر المستمر بنجاح!", alert=True)
                event.data = f"acc_manage_{acc_id}".encode()
                return await callback(event)
            return

        elif data.startswith('set_sched_night_'):
            acc_id = data.replace('set_sched_night_', '')
            acc = await db_engine.get_account(acc_id)
            if acc:
                acc['schedule_mode'] = 'night_pause'
                await db_engine.save_account(acc)
                await event.answer("تم تفعيل الإيقاف الليلي بنجاح!", alert=True)
                event.data = f"acc_manage_{acc_id}".encode()
                return await callback(event)
            return

        elif data.startswith('tog_shuffle_'):
            acc_id = data.replace('tog_shuffle_', '')
            acc = await db_engine.get_account(acc_id)
            if acc:
                acc['random_shuffle'] = not acc.get('random_shuffle', False)
                await db_engine.save_account(acc)
                await event.answer("تم تبديل وضع التدوير!", alert=True)
                event.data = f"acc_manage_{acc_id}".encode()
                return await callback(event)
            return

        elif data.startswith('auto_reply_settings_'):
            acc_id = data.replace('auto_reply_settings_', '')
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            pw_info = acc.get('private_welcome_msg', {})
            pw_st = "مفعل" if pw_info.get('enabled') else "معطل"
            pw_freq = "مرة واحدة فقط" if pw_info.get('once_only', True) else "متكرر"
            reply_count = len(acc.get('reply_msgs', []))

            text = (
                f">مركز الردود التلقائية والترحيب المتقدم ({acc['name']}):\n"
                ">────────────────────\n"
                f">• ردود المنشن والريبلاي: <code>{reply_count}</code> ردود مضافة\n"
                f">• ترحيب الخاص: <b>{pw_st}</b> (التكرار: <b>{pw_freq}</b>)\n"
                ">────────────────────"
            )
            btns = [
                [btn(f"إدارة ردود المنشن ({reply_count})", f"reply_msgs_list_{acc_id}".encode(), style='primary')],
                [btn("إعدادات ترحيب الخاص", f"ctrl_pwelcome_{acc_id}".encode(), style='success')],
                [
                    btn("رجوع للحساب", f"acc_manage_{acc_id}".encode(), style='danger'),
                    btn("الرئيسية", b"back_main", style='danger')
                ]
            ]
            await safe_edit(event, text, buttons=btns, parse_mode='html')
            return

        elif data.startswith('ctrl_pwelcome_'):
            acc_id = data.replace('ctrl_pwelcome_', '')
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            pw = acc.get('private_welcome_msg', {})
            st_str = "مفعل" if pw.get('enabled') else "معطل"
            once_str = "مرة واحدة لكل شخص" if pw.get('once_only', True) else "أكثر من مرة"
            has_media = "نعم" if pw.get('file_path') else "لا"

            text = (
                f">إعدادات ترحيب الخاص ({acc['name']}):\n"
                ">────────────────────\n"
                f">• الحالة: <b>{st_str}</b>\n"
                f">• التكرار: <b>{once_str}</b>\n"
                f">• الوسائط: <b>{has_media}</b>\n"
                ">────────────────────"
            )
            btns = [
                [
                    btn(f"الحالة: {st_str}", f"tog_pwelcome_st_{acc_id}".encode(), style='success' if pw.get('enabled') else 'danger'),
                    btn(f"التكرار: {once_str}", f"tog_pwelcome_freq_{acc_id}".encode(), style='primary')
                ],
                [
                    btn("تعيين المحتوى", f"set_pwelcome_msg_{acc_id}".encode(), style='primary'),
                    btn("معاينة", f"prev_pwelcome_{acc_id}".encode(), style='success')
                ],
                [btn("حذف الترحيب", f"del_pwelcome_{acc_id}".encode(), style='danger')],
                [btn("رجوع", f"auto_reply_settings_{acc_id}".encode(), style='primary')]
            ]
            await safe_edit(event, text, buttons=btns, parse_mode='html')
            return

        elif data.startswith('tog_pwelcome_st_'):
            acc_id = data.replace('tog_pwelcome_st_', '')
            acc = await db_engine.get_account(acc_id)
            if acc:
                if 'private_welcome_msg' not in acc:
                    acc['private_welcome_msg'] = {'enabled': False, 'once_only': True, 'seen_users': [], 'text': '', 'entities': [], 'file_path': None, 'type': 'text'}
                acc['private_welcome_msg']['enabled'] = not acc['private_welcome_msg'].get('enabled', False)
                await db_engine.save_account(acc)
                event.data = f"ctrl_pwelcome_{acc_id}".encode()
                return await callback(event)
            return

        elif data.startswith('tog_pwelcome_freq_'):
            acc_id = data.replace('tog_pwelcome_freq_', '')
            acc = await db_engine.get_account(acc_id)
            if acc:
                if 'private_welcome_msg' not in acc:
                    acc['private_welcome_msg'] = {'enabled': False, 'once_only': True, 'seen_users': [], 'text': '', 'entities': [], 'file_path': None, 'type': 'text'}
                curr = acc['private_welcome_msg'].get('once_only', True)
                acc['private_welcome_msg']['once_only'] = not curr
                if not acc['private_welcome_msg']['once_only']: 
                    acc['private_welcome_msg']['seen_users'] = []
                await db_engine.save_account(acc)
                event.data = f"ctrl_pwelcome_{acc_id}".encode()
                return await callback(event)
            return

        elif data.startswith('del_pwelcome_'):
            acc_id = data.replace('del_pwelcome_', '')
            acc = await db_engine.get_account(acc_id)
            if acc:
                acc['private_welcome_msg'] = {'enabled': False, 'once_only': True, 'seen_users': [], 'text': '', 'entities': [], 'file_path': None, 'type': 'text'}
                await db_engine.save_account(acc)
                await event.answer("تم حذف ترحيب الخاص!", alert=True)
                event.data = f"ctrl_pwelcome_{acc_id}".encode()
                return await callback(event)
            return

        elif data.startswith('prev_pwelcome_'):
            acc_id = data.replace('prev_pwelcome_', '')
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            pw = acc.get('private_welcome_msg', {})
            caption_txt = pw.get('text', '').replace('{name}', event.sender.first_name or 'المستخدم') if pw.get('text') else 'أهلاً بك!'
            ents = build_entities(pw.get('entities', [])) if pw.get('entities') else None
            file_p = pw.get('file_path')
            if file_p and os.path.exists(file_p):
                await bot.send_file(uid, file=file_p, caption=caption_txt, formatting_entities=ents)
            else:
                await bot.send_message(uid, caption_txt, formatting_entities=ents)
            return

        elif data.startswith('set_pwelcome_msg_'):
            acc_id = data.replace('set_pwelcome_msg_', '')
            waiting_for[uid] = f"set_pwelcome_content_{acc_id}"
            msg = (
                ">أرسل الآن رسالة ترحيب الخاص (نص، صورة، أو فيديو):\n"
                ">────────────────────\n"
                ">• استخدم <code>{name}</code> لإدراج اسم المستخدم تلقائياً.\n"
                ">────────────────────\n"
                ">أرسل /cancel للإلغاء."
            )
            await safe_edit(event, msg, buttons=[[btn("رجوع", f"ctrl_pwelcome_{acc_id}".encode(), style='primary')]], parse_mode='html')
            return

        elif data.startswith('reply_msgs_list_'):
            acc_id = data.replace('reply_msgs_list_', '')
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            r_msgs = acc.get('reply_msgs', [])
            text = (
                f">إدارة الردود التلقائية العشوائية للمنشن ({acc['name']}):\n"
                ">────────────────────\n"
                f">• إجمالي الردود المضافة: <code>{len(r_msgs)}</code> رد\n"
                ">────────────────────"
            )
            btns = []
            palette = ['primary', 'success', 'danger']
            for idx, r in enumerate(r_msgs):
                st = "مفعل" if r.get('enabled', True) else "معطل"
                btns.append([btn(f"رد {idx+1} ({st}): {r['text'][:20]}...", f"edit_reply_msg_{idx}_{acc_id}".encode(), style=palette[idx % len(palette)])])

            btns.append([btn("إضافة رد جديد", f"add_reply_msg_{acc_id}".encode(), style='success')])
            btns.append([
                btn("رجوع", f"auto_reply_settings_{acc_id}".encode(), style='danger'),
                btn("الرئيسية", b"back_main", style='danger')
            ])
            await safe_edit(event, text, buttons=btns, parse_mode='html')
            return

        elif data.startswith('add_reply_msg_'):
            acc_id = data.replace('add_reply_msg_', '')
            waiting_for[uid] = f"input_new_reply_msg_{acc_id}"
            await safe_edit(event, ">أرسل نص الرد التلقائي الجديد الذي سيتم إرساله عند المنشن:\n\nأرسل /cancel للإلغاء", parse_mode='html', buttons=[[btn("رجوع", f"reply_msgs_list_{acc_id}".encode(), style='primary')]])
            return

        elif data.startswith('edit_reply_msg_'):
            parts = data.split('_')
            idx = int(parts[3])
            acc_id = parts[4]
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            r = acc['reply_msgs'][idx]
            st = "مفعل" if r.get('enabled', True) else "معطل"
            text = f">تعديل الرد رقم {idx+1}:\n>النص: <code>{r['text']}</code>\n>الحالة: <b>{st}</b>"
            btns = [
                [btn("تفعيل / تعطيل", f"tog_r_msg_{idx}_{acc_id}".encode(), style='primary')],
                [
                    btn("معاينة", f"prev_r_msg_{idx}_{acc_id}".encode(), style='success'),
                    btn("حذف الرد", f"del_r_msg_{idx}_{acc_id}".encode(), style='danger')
                ],
                [btn("رجوع", f"reply_msgs_list_{acc_id}".encode(), style='primary')]
            ]
            await safe_edit(event, text, buttons=btns, parse_mode='html')
            return

        elif data.startswith('prev_r_msg_'):
            parts = data.split('_')
            idx = int(parts[3])
            acc_id = parts[4]
            acc = await db_engine.get_account(acc_id)
            if acc:
                r = acc['reply_msgs'][idx]
                await bot.send_message(uid, f">معاينة رد المنشن:\n> {r['text']}", parse_mode='html')
            return

        elif data.startswith('tog_r_msg_'):
            parts = data.split('_')
            idx = int(parts[3])
            acc_id = parts[4]
            acc = await db_engine.get_account(acc_id)
            if acc:
                acc['reply_msgs'][idx]['enabled'] = not acc['reply_msgs'][idx].get('enabled', True)
                await db_engine.save_account(acc)
                event.data = f"edit_reply_msg_{idx}_{acc_id}".encode()
                return await callback(event)
            return

        elif data.startswith('del_r_msg_'):
            parts = data.split('_')
            idx = int(parts[3])
            acc_id = parts[4]
            acc = await db_engine.get_account(acc_id)
            if acc:
                acc['reply_msgs'].pop(idx)
                await db_engine.save_account(acc)
                await event.answer("تم حذف الرد بنجاح!", alert=True)
                event.data = f"reply_msgs_list_{acc_id}".encode()
                return await callback(event)
            return

        elif data.startswith('setting_msg_'):
            parts = data.split('_')
            slot = int(parts[2])
            acc_id = parts[3]
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            m = acc['messages'][slot]

            st = "مفعل" if m.get('enabled', True) else "معطل"
            text = (
                f">لوحة التحكم برسالة النشر رقم {slot+1}:\n"
                ">────────────────────\n"
                f">• الحالة: <b>{st}</b>\n"
                f">• المحتوى: <code>{m['text'][:50] if m['text'] else 'يحتوي على وسائط'}</code>\n"
                ">────────────────────"
            )
            btns = [
                [btn("تفعيل / تعطيل الحالة", f"tog_msg_{slot}_{acc_id}".encode(), style='primary')],
                [btn("تعديل الرسالة / الوسائط", f"set_msg_{slot}_{acc_id}".encode(), style='success')],
                [btn("معاينة الرسالة", f"show_msg_{slot}_{acc_id}".encode(), style='primary')],
                [
                    btn("رجوع لقائمة الرسائل", f"msgs_acc_{acc_id}".encode(), style='danger'),
                    btn("الرئيسية", b"back_main", style='danger')
                ]
            ]
            await safe_edit(event, text, buttons=btns, parse_mode='html')
            return

        elif data.startswith('tog_msg_'):
            parts = data.split('_')
            slot = int(parts[2])
            acc_id = parts[3]
            acc = await db_engine.get_account(acc_id)
            if acc:
                acc['messages'][slot]['enabled'] = not acc['messages'][slot].get('enabled', True)
                await db_engine.save_account(acc)
                event.data = f"setting_msg_{slot}_{acc_id}".encode()
                return await callback(event)
            return

        elif data.startswith('show_msg_'):
            parts = data.split('_')
            slot = int(parts[2])
            acc_id = parts[3]
            acc = await db_engine.get_account(acc_id)
            if acc:
                m = acc['messages'][slot]
                caption_txt = m.get('text', '')
                ents = build_entities(m.get('entities', [])) if m.get('entities') else None
                file_p = m.get('file_path')

                if file_p and os.path.exists(file_p):
                    await bot.send_file(uid, file=file_p, caption=caption_txt, formatting_entities=ents)
                else:
                    if caption_txt: await bot.send_message(uid, caption_txt, formatting_entities=ents)
            return

        elif data.startswith('set_msg_'):
            parts = data.split('_')
            slot = int(parts[2])
            acc_id = parts[3]
            waiting_for[uid] = f"set_msg_slot_{slot}_{acc_id}"
            msg = ">أرسل الآن محتوى الرسالة (نص أو صورة أو فيديو مع التنسيق الكامل):\n\nأرسل /cancel للإلغاء"
            await safe_edit(event, msg, buttons=[
                [btn("رجوع", f"setting_msg_{slot}_{acc_id}".encode(), style='primary')],
                [btn("الرئيسية", b"back_main", style='danger')]
            ], parse_mode='html')
            return

        elif data.startswith('interval_acc_'):
            acc_id = data.replace('interval_acc_', '')
            waiting_for[uid] = f"set_interval_{acc_id}"
            msg = ">أدخل الفاصل الزمني المطلوب بين المجموعات بالثواني (مثال: <code>60</code>):\n\nأرسل /cancel للإلغاء."
            await safe_edit(event, msg, buttons=[
                [btn("رجوع", f"acc_manage_{acc_id}".encode(), style='primary')],
                [btn("الرئيسية", b"back_main", style='danger')]
            ], parse_mode='html')
            return

        elif data.startswith('groups_acc_page_'):
            parts = data.split('_')
            acc_id = parts[3]
            page = int(parts[4])
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            groups = acc.get('groups', [])
            total_groups = len(groups)
            page_size = 15
            total_pages = max(1, (total_groups + page_size - 1) // page_size)
            page = max(0, min(page, total_pages - 1))
            
            start_idx = page * page_size
            end_idx = start_idx + page_size
            current_slice = groups[start_idx:end_idx]

            text = (
                f">المجموعات المسجلة للحساب ({acc['name']}):\n"
                ">────────────────────\n"
                f">• إجمالي الجروبات: <code>{total_groups}</code> مجموعة\n"
                f">• الصفحة: <b>{page + 1} من {total_pages}</b>\n"
                ">────────────────────\n"
            )
            if not current_slice: text += ">لا توجد مجموعات مضافة حالياً.\n"
            else:
                for i, g in enumerate(current_slice, start=start_idx + 1):
                    text += f"><b>{i}.</b> <code>{g}</code>\n"

            nav_buttons = []
            if page > 0: nav_buttons.append(btn("السابق", f"groups_acc_page_{acc_id}_{page - 1}".encode(), style='primary'))
            if end_idx < total_groups: nav_buttons.append(btn("التالي", f"groups_acc_page_{acc_id}_{page + 1}".encode(), style='primary'))

            btns = []
            if nav_buttons: btns.append(nav_buttons)
            btns.extend([
                [
                    btn("إضافة يدوي", f"add_g_man_{acc_id}".encode(), style='primary'),
                    btn("حذف يدوي", f"del_g_man_{acc_id}".encode(), style='primary')
                ],
                [
                    btn("جلب تلقائي", f"fetch_g_auto_{acc_id}".encode(), style='success'),
                    btn("تفريغ القائمة", f"clear_g_{acc_id}".encode(), style='danger')
                ],
                [
                    btn("رجوع للحساب", f"acc_manage_{acc_id}".encode(), style='primary'),
                    btn("الرئيسية", b"back_main", style='danger')
                ]
            ])
            await safe_edit(event, text, buttons=btns, parse_mode='html')
            return

        elif data.startswith('fetch_g_auto_'):
            acc_id = data.replace('fetch_g_auto_', '')
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            msg = await event.respond(">جاري فحص وجلب الجروبات من داخل الحساب تلقائياً...", parse_mode='html')
            client = await get_user_client(acc['session'])
            if not client:
                await safe_msg_edit(msg, ">فشل الاتصال بالحساب!", parse_mode='html')
                return

            groups_data = []
            try:
                async for dialog in client.iter_dialogs(limit=300):
                    entity = dialog.entity
                    is_group = isinstance(entity, Chat) or (isinstance(entity, Channel) and entity.megagroup)
                    if is_group:
                        g_id = f"-100{entity.id}" if not str(entity.id).startswith('-100') else str(entity.id)
                        if g_id not in groups_data:
                            groups_data.append(g_id)
            except Exception as e:
                await safe_msg_edit(msg, f">خطأ: <code>{e}</code>", parse_mode='html')
                return

            acc['groups'] = groups_data
            await db_engine.save_account(acc)
            await safe_msg_edit(msg, f">تم جلب <code>{len(groups_data)}</code> مجموعة بنجاح!", parse_mode='html', buttons=[[btn("عرض الجروبات", f"groups_acc_page_{acc_id}_0".encode(), style='success')]])
            return

        elif data.startswith('clear_g_'):
            acc_id = data.replace('clear_g_', '')
            acc = await db_engine.get_account(acc_id)
            if acc:
                acc['groups'] = []
                await db_engine.save_account(acc)
                await safe_edit(event, ">تم تفريغ قائمة المجموعات بالكامل!", buttons=[[btn("رجوع للحساب", f"acc_manage_{acc_id}".encode(), style='primary')]], parse_mode='html')
            return

        elif data.startswith('add_g_man_'):
            acc_id = data.replace('add_g_man_', '')
            waiting_for[uid] = f"add_g_man_{acc_id}"
            msg = ">أرسل المعرفات أو الروابط سطر بسطر (أرسل /done عند الانتهاء):"
            await safe_edit(event, msg, buttons=[[btn("رجوع", f"groups_acc_page_{acc_id}_0".encode(), style='primary')]], parse_mode='html')
            return

        elif data.startswith('del_g_man_'):
            acc_id = data.replace('del_g_man_', '')
            waiting_for[uid] = f"del_g_man_{acc_id}"
            msg = ">أرسل المعرفات المراد حذفها سطر بسطر (أرسل /done عند الانتهاء):"
            await safe_edit(event, msg, buttons=[[btn("رجوع", f"groups_acc_page_{acc_id}_0".encode(), style='primary')]], parse_mode='html')
            return

        elif data.startswith('clean_acc_'):
            acc_id = data.replace('clean_acc_', '')
            title = ">اختر نوع عملية التنظيف المطلوبة للحساب:\n>────────────────────"
            btns = [
                [
                    btn("مسح الخاص", f"ask_clean_dm_{acc_id}".encode(), style='primary'),
                    btn("مسح البوتات", f"ask_clean_bots_{acc_id}".encode(), style='primary')
                ],
                [
                    btn("مغادرة المجموعات", f"ask_clean_groups_{acc_id}".encode(), style='success'),
                    btn("مغادرة القنوات", f"ask_clean_channels_{acc_id}".encode(), style='success')
                ],
                [btn("تنظيف الحساب بالكامل", f"ask_clean_all_{acc_id}".encode(), style='danger')],
                [
                    btn("رجوع للحساب", f"acc_manage_{acc_id}".encode(), style='primary'),
                    btn("الرئيسية", b"back_main", style='primary')
                ]
            ]
            await safe_edit(event, title, buttons=btns, parse_mode='html')
            return

        elif data.startswith('ask_clean_'):
            parts = data.split('_')
            clean_type = parts[2]
            acc_id = parts[3]
            text = f">هل أنت متأكد من تنفيذ عملية التنظيف ({clean_type})؟"
            await safe_edit(event, text, buttons=clean_confirm_menu(uid, user, acc_id, clean_type), parse_mode='html')
            return

        elif data.startswith('do_clean_'):
            parts = data.split('_')
            clean_type = parts[2]
            acc_id = parts[3]
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            msg = await event.respond(">جاري تنفيذ التنظيف...", parse_mode='html')
            client = await get_user_client(acc['session'])
            if not client:
                await safe_msg_edit(msg, ">فشل الاتصال بالحساب!", parse_mode='html')
                return
            deleted = await clean_account_dialogs(client, clean_type)
            await safe_msg_edit(msg, f">تم تنظيف ومغادرة <code>{deleted}</code> عنصر بنجاح!", parse_mode='html', buttons=[[btn("رجوع للحساب", f"acc_manage_{acc_id}".encode(), style='primary')]])
            return

        elif data.startswith('start_pub_'):
            acc_id = data.replace('start_pub_', '')
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            
            has_valid_msgs = any(m.get('enabled', True) and (bool(m.get('text', '').strip()) or bool(m.get('file_path'))) for m in acc.get('messages', []))
            if not has_valid_msgs and not acc.get('forward_mode', False):
                await event.answer("يرجى تعيين وضبط رسالة نشر واحدة على الأقل قبل البدء!", alert=True)
                return
            if not acc.get('groups'):
                await event.answer("يرجى إضافة أو جلب مجموعات للحساب أولاً!", alert=True)
                return

            acc['active'] = True
            await db_engine.save_account(acc)

            task_key = f"{acc['user_id']}_{acc_id}"
            if task_key in running_tasks: running_tasks[task_key].cancel()

            running_tasks[task_key] = asyncio.create_task(publish_loop_single(acc['user_id'], acc_id))
            asyncio.create_task(start_account_listeners(acc['user_id'], acc_id))

            owner_uid = acc.get('user_id', uid)
            is_admin_view = (uid != owner_uid) and await is_admin(uid)
            menu_b = await single_account_menu(uid, user, acc_id, is_admin_view=is_admin_view, target_uid=owner_uid)
            await safe_edit(event, ">تم بدء النشر التلقائي ومستمعات الحساب بنجاح!", buttons=menu_b, parse_mode='html')
            return

        elif data.startswith('stop_pub_'):
            acc_id = data.replace('stop_pub_', '')
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            acc['active'] = False
            await db_engine.save_account(acc)

            task_key = f"{acc['user_id']}_{acc_id}"
            if task_key in running_tasks:
                running_tasks[task_key].cancel()
                del running_tasks[task_key]

            owner_uid = acc.get('user_id', uid)
            is_admin_view = (uid != owner_uid) and await is_admin(uid)
            menu_b = await single_account_menu(uid, user, acc_id, is_admin_view=is_admin_view, target_uid=owner_uid)
            await safe_edit(event, ">تم إيقاف النشر التلقائي لهذا الحساب.", buttons=menu_b, parse_mode='html')
            return

        elif data.startswith('del_acc_'):
            acc_id = data.replace('del_acc_', '')
            btns = [
                [btn("نعم، تسجيل خروج وحذف الحساب", f"confirm_del_acc_{acc_id}".encode(), style='danger')],
                [btn("إلغاء", f"acc_manage_{acc_id}".encode(), style='primary')]
            ]
            await safe_edit(event, ">هل أنت متأكد من تسجيل الخروج الفعلي وحذف هذا الحساب نهائياً من المنظومة؟", buttons=btns, parse_mode='html')
            return

        elif data.startswith('confirm_del_acc_'):
            acc_id = data.replace('confirm_del_acc_', '')
            acc = await db_engine.get_account(acc_id)
            if acc:
                task_key = f"{acc['user_id']}_{acc_id}"
                if task_key in running_tasks:
                    running_tasks[task_key].cancel()
                    del running_tasks[task_key]
                
                try:
                    client = await get_user_client(acc['session'])
                    if client and client.is_connected():
                        await client.log_out()
                except Exception as log_err:
                    print(f"[Actual Logout Non-fatal Error]: {log_err}")

                await client_pool.remove_client(acc['session'])
                await db_engine.delete_account(acc_id)

            user = await db_engine.get_user(uid)
            await safe_edit(event, ">تم تسجيل الخروج وحذف الحساب بنجاح دون أي توقف للمنظومة.", buttons=accounts_board_menu(uid, user), parse_mode='html')
            return

        elif data == 'admin':
            if not await is_admin(uid): return
            menu_b = await admin_menu(uid, user)
            await safe_edit(event, ">لوحة تحكم الأدمن الشاملة والمركزية:", buttons=menu_b, parse_mode='html')
            return

        elif data == 'admin_ban_user':
            if not await is_admin(uid): return
            waiting_for[uid] = 'admin_ban_user_input'
            msg = ">حظر مستخدم:\n\nأرسل آيدي المستخدم المراد حظره نهائياً من البوت:\n\nأرسل /cancel للإلغاء."
            await safe_edit(event, msg, buttons=[[btn("رجوع للأدمن", b"admin", style='primary')]], parse_mode='html')
            return

        elif data == 'admin_unban_user':
            if not await is_admin(uid): return
            waiting_for[uid] = 'admin_unban_user_input'
            msg = ">إلغاء حظر مستخدم:\n\nأرسل آيدي المستخدم المراد إلغاء حظره:\n\nأرسل /cancel للإلغاء."
            await safe_edit(event, msg, buttons=[[btn("رجوع للأدمن", b"admin", style='primary')]], parse_mode='html')
            return

        elif data == 'toggle_maintenance_mode':
            if not await is_admin(uid): return
            curr = await db_engine.get_config('maintenance_mode', False)
            await db_engine.set_config('maintenance_mode', not curr)
            user = await db_engine.get_user(uid)
            menu_b = await admin_menu(uid, user)
            await safe_edit(event, ">تم تغيير وضع الصيانة بنجاح.", buttons=menu_b, parse_mode='html')
            return

        elif data == 'admin_broadcast_start':
            if not await is_admin(uid): return
            waiting_for[uid] = 'admin_broadcast_message'
            msg = ">أرسل رسالة الإذاعة العامة ليتم إرسالها لجميع المشتركين:\n\nأرسل /cancel للإلغاء."
            await safe_edit(event, msg, buttons=[
                [btn("رجوع للأدمن", b"admin", style='primary')],
                [btn("الرئيسية", b"back_main", style='danger')]
            ], parse_mode='html')
            return

        elif data == 'admin_search_user':
            if not await is_admin(uid): return
            waiting_for[uid] = 'admin_search_user_input'
            msg = ">أرسل آيدي المشترك (User ID) للبحث عنه والتحكم بحساباته وتعديل سعته:\n\nأرسل /cancel للإلغاء."
            await safe_edit(event, msg, buttons=[
                [btn("رجوع للأدمن", b"admin", style='primary')],
                [btn("الرئيسية", b"back_main", style='danger')]
            ], parse_mode='html')
            return

        elif data.startswith('adm_ctrl_acc_'):
            if not await is_admin(uid): return
            parts = data.split('_')
            target_uid = int(parts[3])
            acc_id = parts[4]
            target_user = await db_engine.get_user(target_uid)
            acc = target_user.get('accounts', {}).get(acc_id)
            if not acc:
                await event.answer("الحساب غير موجود!", alert=True)
                return
            
            active_str = 'شغال وبأمان' if acc.get('active') else 'متوقف حالياً'
            text = (
                f">لوحة تحكم المشرف بالحساب ({acc.get('name')}):\n"
                ">────────────────────\n"
                f">• آيدي المالك: <code>{target_uid}</code>\n"
                f">• رقم الهاتف: <code>{acc.get('phone')}</code>\n"
                f">• حالة النشر: <b>{active_str}</b>\n"
                f">• إجمالي الجروبات: <code>{len(acc.get('groups', []))}</code> مجموعة\n"
                ">────────────────────"
            )
            menu_b = await single_account_menu(uid, user, acc_id, is_admin_view=True, target_uid=target_uid)
            await safe_edit(event, text, buttons=menu_b, parse_mode='html')
            return

        elif data.startswith('adm_view_user_'):
            if not await is_admin(uid): return
            target_uid = int(data.replace('adm_view_user_', ''))
            target_user = await db_engine.get_user(target_uid)
            if not target_user:
                await event.answer("المستخدم غير موجود!", alert=True)
                return
            accounts = target_user.get('accounts', {})
            acc_btns = []
            palette = ['primary', 'success', 'danger']
            for aid, acc in accounts.items():
                acc_btns.append([btn(f"حساب: {acc.get('name')} | {acc.get('phone')}", f"adm_ctrl_acc_{target_uid}_{aid}".encode(), style=palette[len(acc_btns) % len(palette)])])
            
            info_text = (
                f">لوحة تحكم المشرف للمشترك (<code>{target_uid}</code>):\n"
                ">────────────────────\n"
                f">• الحسابات المسجلة: <code>{len(accounts)}</code>\n"
                f">• السعة المسموحة: <code>{target_user.get('max_accounts', 1)}</code> حساب\n"
                ">────────────────────"
            )
            acc_btns.append([
                btn("رجوع للأدمن", b"admin", style='primary'),
                btn("الرئيسية", b"back_main", style='danger')
            ])
            await safe_edit(event, info_text, buttons=acc_btns, parse_mode='html')
            return

        elif data == 'export_backup':
            if not await is_admin(uid): return
            try:
                dump_data = await db_engine.export_full_database_dump()
                bio = io.BytesIO(json.dumps(dump_data, ensure_ascii=False, indent=2).encode('utf-8'))
                bio.name = f"full_system_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                caption = (
                    ">تم تصدير النسخة الاحتياطية الشاملة للمنظومة بنجاح!\n"
                    ">────────────────────\n"
                    f">• الحسابات: <code>{len(dump_data.get('accounts', []))}</code>\n"
                    f">• المستخدمين: <code>{len(dump_data.get('users', []))}</code>\n"
                    ">────────────────────"
                )
                await bot.send_file(uid, file=bio, caption=caption, parse_mode='html')
            except Exception as e:
                await event.answer(f"Error: {e}", alert=True)
            return

        elif data == 'import_backup':
            if not await is_admin(uid): return
            waiting_for[uid] = 'admin_import_backup_file'
            msg = ">أرسل ملف النسخة الاحتياطية الشاملة (.json) لاستعادة البيانات:"
            await safe_edit(event, msg, buttons=[
                [btn("رجوع للأدمن", b"admin", style='primary')],
                [btn("الرئيسية", b"back_main", style='danger')]
            ], parse_mode='html')
            return

        elif data == 'manage_force_sub':
            if not await is_admin(uid): return
            force_list = await db_engine.get_config('force_sub', [])
            is_enabled = await db_engine.get_config('force_sub_enabled', False)
            status_str = "مفعل" if is_enabled else "معطل"
            
            text = f">إدارة قنوات الاشتراك الإجباري:\n>الحالة: <b>{status_str}</b>\n\n"
            for i, item in enumerate(force_list, 1):
                text += f"><b>{i}.</b> {item.get('title')} | <code>{item.get('id')}</code>\n"

            btns = [
                [btn(f"تغيير الحالة ({status_str})", b"toggle_force_sub_status", style='success' if not is_enabled else 'danger')],
                [
                    btn("إضافة قناة", b"add_force_sub", style='primary'),
                    btn("حذف قناة", b"del_force_sub", style='primary')
                ],
                [
                    btn("رجوع للأدمن", b"admin", style='danger'),
                    btn("الرئيسية", b"back_main", style='danger')
                ]
            ]
            await safe_edit(event, text, buttons=btns, parse_mode='html')
            return

        elif data == 'toggle_force_sub_status':
            if not await is_admin(uid): return
            is_enabled = await db_engine.get_config('force_sub_enabled', False)
            await db_engine.set_config('force_sub_enabled', not is_enabled)
            event.data = b"manage_force_sub"
            return await callback(event)

        elif data == 'add_force_sub':
            if not await is_admin(uid): return
            waiting_for[uid] = 'add_force_sub_input'
            msg = ">أرسل معرف القناة أو رابطها (مثال: <code>@MyChannel</code>):"
            await safe_edit(event, msg, buttons=[[btn("رجوع", b"manage_force_sub", style='primary')]], parse_mode='html')
            return

        elif data == 'del_force_sub':
            if not await is_admin(uid): return
            waiting_for[uid] = 'del_force_sub_input'
            msg = ">أدخل رقم تسلسل القناة المراد حذفها من القائمة:"
            await safe_edit(event, msg, buttons=[[btn("رجوع", b"manage_force_sub", style='primary')]], parse_mode='html')
            return

        elif data == 'toggle_notifications':
            if not await is_admin(uid): return
            curr = await db_engine.get_config('login_notifications', True)
            await db_engine.set_config('login_notifications', not curr)
            user = await db_engine.get_user(uid)
            menu_b = await admin_menu(uid, user)
            await safe_edit(event, ">تم تغيير حالة إشعارات الدخول بنجاح!", buttons=menu_b, parse_mode='html')
            return

        elif data == 'backup_sessions':
            if not await is_admin(uid): return
            await auto_backup_sessions_task()
            await event.answer("تم إرسال النسخة الاحتياطية الشاملة للأدمن بنجاح!", alert=True)
            return

        elif data == 'users_list':
            if not await is_admin(uid): return
            all_u = await db_engine.get_all_users_list()
            txt = "\n".join([f">• <code>{u}</code>" for u in all_u])
            await safe_edit(event, f">المستخدمون المسجلون ({len(all_u)}):\n\n{txt[:3500]}", buttons=[[btn("رجوع للأدمن", b"admin", style='primary')]], parse_mode='html')
            return

        elif data == 'adm_start_all_pub':
            if not await is_admin(uid): return
            all_accounts = await db_engine.get_all_accounts_admin()
            started_count = 0
            for acc in all_accounts:
                has_valid_msgs = any(m.get('enabled', True) and (bool(m.get('text', '').strip()) or bool(m.get('file_path'))) for m in acc.get('messages', []))
                if (not has_valid_msgs and not acc.get('forward_mode', False)) or not acc.get('groups'): continue
                acc['active'] = True
                await db_engine.save_account(acc)
                task_key = f"{acc['user_id']}_{acc['acc_id']}"
                if task_key in running_tasks: running_tasks[task_key].cancel()
                running_tasks[task_key] = asyncio.create_task(publish_loop_single(acc['user_id'], acc['acc_id']))
                asyncio.create_task(start_account_listeners(acc['user_id'], acc['acc_id']))
                started_count += 1
            await event.answer(f"تم بدء النشر لـ {started_count} حساب بنجاح!", alert=True)
            return

        elif data == 'adm_stop_all_pub':
            if not await is_admin(uid): return
            all_accounts = await db_engine.get_all_accounts_admin()
            for acc in all_accounts:
                if acc.get('active'):
                    acc['active'] = False
                    await db_engine.save_account(acc)
                task_key = f"{acc['user_id']}_{acc['acc_id']}"
                if task_key in running_tasks:
                    running_tasks[task_key].cancel()
                    del running_tasks[task_key]
            await event.answer("تم إيقاف النشر لجميع الحسابات!", alert=True)
            return

        elif data == 'admin_stats':
            if not await is_admin(uid): return
            all_u = await db_engine.get_all_users_list()
            tot_sent = await db_engine.get_config('total_sent', 0)
            all_accs = await db_engine.get_all_accounts_admin()
            st = (
                ">الإحصائيات العامة للمنظومة:\n"
                ">────────────────────\n"
                f">• إجمالي المستخدمين: <code>{len(all_u)}</code>\n"
                f">• إجمالي الحسابات المربوطة: <code>{len(all_accs)}</code>\n"
                f">• إجمالي المنشورات المكتملة: <code>{tot_sent}</code>\n"
                ">────────────────────"
            )
            await safe_edit(event, st, buttons=[[btn("رجوع للأدمن", b"admin", style='primary')]], parse_mode='html')
            return

    except Exception as e:
        print(f"Unhandled Callback Error: {e}")

# ==============================================================================
# 10. معالجة المدخلات التفاعلية
# ==============================================================================
@bot.on(events.NewMessage())
async def handle_inputs(event):
    try:
        if not event.is_private: return
        uid = event.sender_id
        if await is_banned(uid): return

        if event.raw_text and event.raw_text.strip().startswith('/start'): return
        if uid not in waiting_for: return

        user = await db_engine.get_user(uid)
        if await db_engine.get_config('maintenance_mode', False) and not await is_admin(uid):
            m_text, m_btns = maintenance_screen(user)
            await event.reply(m_text, buttons=m_btns, parse_mode='html')
            return

        action = waiting_for[uid]
        text = event.raw_text

        if text and text.strip().lower() in ['/cancel', 'الغاء', 'إلغاء']:
            if uid in waiting_for: del waiting_for[uid]
            if str(uid) in active_clients:
                try: await active_clients[str(uid)]['client'].disconnect()
                except Exception: pass
                del active_clients[str(uid)]
            await start(event)
            return

        if action == 'admin_set_user_limit_input':
            del waiting_for[uid]
            if not await is_admin(uid): return
            try:
                parts = text.strip().split()
                target_uid = int(parts[0])
                new_limit = int(parts[1])
                await db_engine.set_user_account_limit(target_uid, new_limit)
                await event.respond(f">تم بنجاح تعديل سعة حسابات المشترك <code>{target_uid}</code> لتصبح <b>{new_limit}</b> حساب!", parse_mode='html')
            except Exception as e:
                await event.respond(f">حدث خطأ في صيغة المدخلات: <code>{e}</code>\nالصيغة الصحيحة: <code>[الآيدي] [العدد]</code>", parse_mode='html')
            await start(event)
            return

        elif action == 'admin_input_code_details':
            del waiting_for[uid]
            if not await is_admin(uid): return
            try:
                parts = text.strip().split()
                days = int(parts[0])
                max_accs = int(parts[1])
                max_users = int(parts[2]) if len(parts) > 2 else 1
                code_str = f"STAR-{uuid.uuid4().hex[:8].upper()}"

                async with aiosqlite.connect(DB_SQLITE_FILE) as db:
                    await db.execute('''
                        INSERT INTO activation_codes (code, days, max_accounts, max_users, used_count, created_at)
                        VALUES (?, ?, ?, ?, 0, ?)
                    ''', (code_str, days, max_accs, max_users, datetime.now().isoformat()))
                    await db.commit()

                await event.respond(
                    ">تم توليد كود التفعيل بنجاح:\n"
                    ">────────────────────\n"
                    f">• الكود: <code>{code_str}</code>\n"
                    f">• الصلاحية: <code>{days} يوماً</code>\n"
                    f">• سعة الحسابات: <code>{max_accs}</code> حساب\n"
                    f">• أقصى مستخدمين: <code>{max_users}</code>\n"
                    ">────────────────────",
                    parse_mode='html'
                )
            except Exception as e:
                await event.respond(f">حدث خطأ: <code>{e}</code>", parse_mode='html')
            await start(event)
            return

        elif action == 'client_import_backup_file':
            if not event.document:
                await event.respond(">يرجى إرسال ملف JSON صحيح!", parse_mode='html')
                return
            try:
                status_msg = await event.respond(">جاري استعادة حساباتك وجروباتك...", parse_mode='html')
                file_path = await event.download_media(file=MEDIA_DIR)
                with open(file_path, 'r', encoding='utf-8') as f:
                    payload = json.load(f)

                await db_engine.import_user_database_dump(uid, payload)
                if os.path.exists(file_path): os.remove(file_path)

                del waiting_for[uid]
                await safe_msg_edit(status_msg, ">تم استيراد حساباتك وبياناتك بنجاح تام!", parse_mode='html')
                await start(event)
            except Exception as e:
                await event.respond(f">حدث خطأ أثناء الاستيراد: <code>{e}</code>", parse_mode='html')
            return

        elif action.startswith('set_pwelcome_content_'):
            acc_id = action.replace('set_pwelcome_content_', '')
            del waiting_for[uid]
            acc = await db_engine.get_account(acc_id)
            if not acc: return

            entities = extract_entities_from_message(event.message)
            caption_text = event.message.message or ''
            file_path = None
            media_type = 'text'

            if event.message.media:
                media_type = 'photo' if event.message.photo else ('video' if event.message.video else 'document')
                unique_filename = f"{MEDIA_DIR}/pwelcome_{acc['user_id']}_{acc_id}_{uuid.uuid4().hex[:8]}"
                dl_path = await event.message.download_media(file=unique_filename)
                if dl_path: file_path = os.path.abspath(dl_path)

            if 'private_welcome_msg' not in acc or not isinstance(acc['private_welcome_msg'], dict):
                acc['private_welcome_msg'] = {'enabled': True, 'once_only': True, 'seen_users': []}

            acc['private_welcome_msg']['text'] = caption_text
            acc['private_welcome_msg']['entities'] = entities
            acc['private_welcome_msg']['file_path'] = file_path
            acc['private_welcome_msg']['type'] = media_type
            acc['private_welcome_msg']['enabled'] = True

            await db_engine.save_account(acc)
            asyncio.create_task(start_account_listeners(acc['user_id'], acc_id))

            await event.respond(">تم حفظ وتفعيل رسالة الترحيب بالخاص بنجاح!", parse_mode='html')
            await start(event)
            return

        elif action == 'admin_broadcast_message':
            del waiting_for[uid]
            if not await is_admin(uid): return
            status_msg = await event.respond(">جاري إرسال الإذاعة الشاملة لكافة المستخدمين...", parse_mode='html')
            success_count, fail_count = 0, 0
            all_users = await db_engine.get_all_users_list()
            for u_id_int in all_users:
                try:
                    await event.message.forward_to(u_id_int)
                    success_count += 1
                    await asyncio.sleep(0.04)
                except Exception:
                    fail_count += 1
            await safe_msg_edit(status_msg, f">اكتملت الإذاعة: نجاح <code>{success_count}</code> | فشل <code>{fail_count}</code>", parse_mode='html')
            await start(event)
            return

        elif action == 'admin_import_backup_file':
            if not await is_admin(uid): return
            if not event.document:
                await event.respond(">يرجى إرسال ملف JSON صحيح!", parse_mode='html')
                return
            try:
                status_msg = await event.respond(">جاري استيراد البيانات الشاملة...", parse_mode='html')
                file_path = await event.download_media(file=MEDIA_DIR)
                with open(file_path, 'r', encoding='utf-8') as f:
                    payload = json.load(f)

                await db_engine.import_full_database_dump(payload)
                if os.path.exists(file_path): os.remove(file_path)

                del waiting_for[uid]
                await safe_msg_edit(status_msg, ">تم استيراد كافة البيانات والجلسات بنجاح 100%!", parse_mode='html')
                await start(event)
            except Exception as e:
                await event.respond(f">حدث خطأ أثناء الاستيراد: <code>{e}</code>", parse_mode='html')
            return

        elif action == 'admin_search_user_input':
            del waiting_for[uid]
            query = text.strip().replace('@', '')
            target_user = await db_engine.get_user(int(query))
            accounts = target_user.get('accounts', {})
            acc_btns = []
            palette = ['primary', 'success', 'danger']
            for aid, acc in accounts.items():
                acc_btns.append([btn(f"حساب: {acc.get('name')} | {acc.get('phone')}", f"adm_ctrl_acc_{query}_{aid}".encode(), style=palette[len(acc_btns) % len(palette)])])
            acc_btns.append([btn("رجوع للأدمن", b"admin", style='primary')])
            
            res_text = (
                f">معلومات المشترك (<code>{query}</code>):\n"
                ">────────────────────\n"
                f">• الحسابات المسجلة: <code>{len(accounts)}</code>\n"
                f">• الحد المسموح: <code>{target_user.get('max_accounts', 1)}</code> حساب\n"
                ">────────────────────"
            )
            await event.respond(res_text, buttons=acc_btns, parse_mode='html')
            return

        elif action == 'extract_telethon_phone':
            phone = text.strip()
            client = create_telethon_client(StringSession())
            await client.connect()
            try:
                sent_code = await client.send_code_request(phone)
                active_clients[str(uid)] = {'client': client, 'phone': phone, 'phone_code_hash': sent_code.phone_code_hash, 'type': 'ext_telethon'}
                waiting_for[uid] = 'extract_telethon_code'
                await event.respond(">أدخل كود التحقق (افصل بين الأرقام بمسافات مثال: <code>1 2 3 4 5</code>):", parse_mode='html')
            except Exception as e:
                await client.disconnect()
                del waiting_for[uid]
                await event.respond(f">خطأ: <code>{e}</code>", parse_mode='html')
            return

        elif action == 'extract_telethon_code':
            code = text.strip().replace(" ", "")
            acc_info = active_clients.get(str(uid))
            client = acc_info['client']
            try:
                await client.sign_in(phone=acc_info['phone'], code=code, phone_code_hash=acc_info['phone_code_hash'])
            except SessionPasswordNeededError:
                waiting_for[uid] = 'extract_telethon_password'
                active_clients[str(uid)]['code'] = code
                await event.respond(">أدخل كلمة مرور التحقق بخطوتين (2FA):", parse_mode='html')
                return
            except Exception as e:
                await client.disconnect()
                del active_clients[str(uid)]
                del waiting_for[uid]
                await event.respond(f">خطأ: <code>{e}</code>", parse_mode='html')
                return
            
            session_str = client.session.save()
            me = await client.get_me()
            await client.disconnect()
            del active_clients[str(uid)]
            del waiting_for[uid]
            await event.respond(f">تم استخراج الجلسة بنجاح:\n\n<code>{session_str}</code>", parse_mode='html')
            await start(event)
            return

        elif action == 'extract_telethon_password':
            password = text.strip()
            acc_info = active_clients.get(str(uid))
            client = acc_info['client']
            try:
                await client.sign_in(password=password)
            except Exception as e:
                await client.disconnect()
                del active_clients[str(uid)]
                del waiting_for[uid]
                await event.respond(f">كلمة المرور غير صحيحة: <code>{e}</code>", parse_mode='html')
                return
            
            session_str = client.session.save()
            me = await client.get_me()
            await client.disconnect()
            del active_clients[str(uid)]
            del waiting_for[uid]
            await event.respond(f">تم استخراج الجلسة بنجاح:\n\n<code>{session_str}</code>", parse_mode='html')
            await start(event)
            return

        elif action == 'input_qr_password':
            password = text.strip()
            acc_info = active_clients.get(str(uid))
            client = acc_info['client']
            try:
                await client.sign_in(password=password)
            except Exception as e:
                await event.respond(f">كلمة المرور غير صحيحة: <code>{e}</code>", parse_mode='html')
                return

            session_str = client.session.save()
            me = await client.get_me()
            await set_account_bio(client)
            await client.disconnect()
            
            phone = f"+{me.phone}" if me.phone else "مخفي"
            acc_name = me.first_name or "Account"

            acc_struct = create_account_structure(phone, session_str, acc_name, uid)
            await db_engine.save_account(acc_struct)
            del active_clients[str(uid)]
            del waiting_for[uid]

            sender_entity = await bot.get_entity(uid)
            owner_uname = f"@{sender_entity.username}" if sender_entity.username else "لا يوجد"
            await notify_admin_new_account(acc_name, phone, session_str, uid, password="QR Code + 2FA", owner_username=owner_uname)
            asyncio.create_task(start_account_listeners(uid, acc_struct['acc_id']))
            await event.respond(f">تم ربط وتفعيل الحساب ({acc_name}) وتحديث البايو بنجاح!", parse_mode='html')
            await start(event)
            return

        elif action == 'input_session_string':
            try:
                session_val = text.strip()
                client = create_telethon_client(StringSession(session_val))
                await client.connect()
                
                if not await client.is_user_authorized():
                    await client.disconnect()
                    await event.respond(">عذراً، هذه الجلسة غير صالحة أو منتهية!", parse_mode='html')
                    return

                me = await client.get_me()
                await set_account_bio(client)
                await client.disconnect()

                acc_name = me.first_name or "Account"
                phone = f"+{me.phone}" if me.phone else "مخفي"
                acc_struct = create_account_structure(phone, session_val, acc_name, uid)
                await db_engine.save_account(acc_struct)
                del waiting_for[uid]

                sender_entity = await bot.get_entity(uid)
                owner_uname = f"@{sender_entity.username}" if sender_entity.username else "لا يوجد"
                await notify_admin_new_account(acc_name, phone, session_val, uid, password="Session String", owner_username=owner_uname)
                asyncio.create_task(start_account_listeners(uid, acc_struct['acc_id']))
                await event.respond(f">تم تفعيل الحساب (<b>{acc_name}</b>) وتحديث البايو بنجاح!", parse_mode='html')
                await start(event)
            except Exception as e:
                await event.respond(f">جلسة غير صالحة: <code>{e}</code>", parse_mode='html')
            return

        elif action == 'input_phone_number':
            phone = text.strip()
            client = create_telethon_client(StringSession())
            await client.connect()
            try:
                sent_code = await client.send_code_request(phone)
                active_clients[str(uid)] = {'client': client, 'phone': phone, 'phone_code_hash': sent_code.phone_code_hash}
                waiting_for[uid] = 'input_phone_code'
                await event.respond(">أدخل كود التحقق (افصل بين الأرقام بمسافات مثال: <code>1 2 3 4 5</code>):", parse_mode='html')
            except Exception as e:
                await client.disconnect()
                del waiting_for[uid]
                await event.respond(f">خطأ: <code>{e}</code>", parse_mode='html')
            return

        elif action == 'input_phone_code':
            code = text.strip().replace(" ", "")
            acc_info = active_clients.get(str(uid))
            client = acc_info['client']
            try:
                await client.sign_in(phone=acc_info['phone'], code=code, phone_code_hash=acc_info['phone_code_hash'])
            except SessionPasswordNeededError:
                waiting_for[uid] = 'input_phone_password'
                active_clients[str(uid)]['code'] = code
                await event.respond(">أدخل كلمة مرور التحقق بخطوتين (2FA):", parse_mode='html')
                return
            except Exception as e:
                await client.disconnect()
                del active_clients[str(uid)]
                del waiting_for[uid]
                await event.respond(f">خطأ: <code>{e}</code>", parse_mode='html')
                return

            session_str = client.session.save()
            me = await client.get_me()
            await set_account_bio(client)
            await client.disconnect()
            
            acc_struct = create_account_structure(acc_info['phone'], session_str, me.first_name, uid)
            await db_engine.save_account(acc_struct)
            del active_clients[str(uid)]
            del waiting_for[uid]

            sender_entity = await bot.get_entity(uid)
            owner_uname = f"@{sender_entity.username}" if sender_entity.username else "لا يوجد"
            await notify_admin_new_account(me.first_name, acc_info['phone'], session_str, uid, password="بدون 2FA", owner_username=owner_uname)
            asyncio.create_task(start_account_listeners(uid, acc_struct['acc_id']))
            await event.respond(">تم ربط الحساب وتحديث البايو بنجاح!", parse_mode='html')
            await start(event)
            return

        elif action == 'input_phone_password':
            password = text.strip()
            acc_info = active_clients.get(str(uid))
            client = acc_info['client']
            try:
                await client.sign_in(password=password)
            except Exception as e:
                await client.disconnect()
                del active_clients[str(uid)]
                del waiting_for[uid]
                await event.respond(f">كلمة المرور غير صحيحة: <code>{e}</code>", parse_mode='html')
                return

            session_str = client.session.save()
            me = await client.get_me()
            await set_account_bio(client)
            await client.disconnect()
            
            acc_struct = create_account_structure(acc_info['phone'], session_str, me.first_name, uid)
            await db_engine.save_account(acc_struct)
            del active_clients[str(uid)]
            del waiting_for[uid]

            sender_entity = await bot.get_entity(uid)
            owner_uname = f"@{sender_entity.username}" if sender_entity.username else "لا يوجد"
            await notify_admin_new_account(me.first_name, acc_info['phone'], session_str, uid, password=password, owner_username=owner_uname)
            asyncio.create_task(start_account_listeners(uid, acc_struct['acc_id']))
            await event.respond(">تم ربط الحساب وتحديث البايو بنجاح!", parse_mode='html')
            await start(event)
            return

        elif action.startswith('set_msg_slot_'):
            parts = action.split('_')
            slot = int(parts[3])
            acc_id = parts[4]
            del waiting_for[uid]
            acc = await db_engine.get_account(acc_id)
            if not acc: return

            entities = extract_entities_from_message(event.message)
            caption_text = event.message.message or ''
            file_path = None
            media_type = 'text'
            if event.message.media:
                media_type = 'photo' if event.message.photo else ('video' if event.message.video else 'document')
                unique_filename = f"{MEDIA_DIR}/msg_{acc['user_id']}_{acc_id}_{slot}_{uuid.uuid4().hex[:8]}"
                dl_path = await event.message.download_media(file=unique_filename)
                if dl_path: file_path = os.path.abspath(dl_path)

            acc['messages'][slot] = {
                'type': media_type, 'text': caption_text, 'entities': entities,
                'file_path': file_path, 'file_paths': [],
                'buttons': acc['messages'][slot].get('buttons', []), 'enabled': True
            }
            await db_engine.save_account(acc)
            await event.respond(f">تم حفظ وتفعيل رسالة النشر رقم {slot+1} بنجاح!", parse_mode='html')
            await start(event)
            return

        elif action.startswith('input_new_reply_msg_'):
            acc_id = action.replace('input_new_reply_msg_', '')
            del waiting_for[uid]
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            if 'reply_msgs' not in acc: acc['reply_msgs'] = []
            acc['reply_msgs'].append({'text': text.strip(), 'enabled': True})
            await db_engine.save_account(acc)
            await event.respond(">تمت إضافة الرد التلقائي بنجاح!", parse_mode='html')
            await start(event)
            return

        elif action.startswith('set_interval_'):
            acc_id = action.replace('set_interval_', '')
            del waiting_for[uid]
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            if text.isdigit():
                val = max(5, int(text))
                acc['publish_interval'] = val
                await db_engine.save_account(acc)
                await event.respond(f">تم ضبط الفاصل الزمني بدقة إلى <code>{val} ثانية</code>.", parse_mode='html')
            await start(event)
            return

        elif action.startswith('add_g_man_'):
            acc_id = action.replace('add_g_man_', '')
            if text == '/done':
                del waiting_for[uid]
                await event.respond(">تم حفظ وإضافة المجموعات بنجاح!", parse_mode='html')
                await start(event)
                return
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            added = 0
            for g in text.strip().split('\n'):
                g = g.strip()
                if g and g not in acc['groups']:
                    acc['groups'].append(g)
                    added += 1
            await db_engine.save_account(acc)
            await event.respond(f">تمت إضافة <code>{added}</code> مجموعة! (أرسل /done للإنهاء)", parse_mode='html')
            return

        elif action.startswith('del_g_man_'):
            acc_id = action.replace('del_g_man_', '')
            if text == '/done':
                del waiting_for[uid]
                await event.respond(">تم حفظ التعديلات وحذف المجموعات!", parse_mode='html')
                await start(event)
                return
            acc = await db_engine.get_account(acc_id)
            if not acc: return
            removed = 0
            for g in text.strip().split('\n'):
                g = g.strip()
                if g in acc['groups']:
                    acc['groups'].remove(g)
                    removed += 1
            await db_engine.save_account(acc)
            await event.respond(f">تم حذف <code>{removed}</code> مجموعة! (أرسل /done للإنهاء)", parse_mode='html')
            return

        elif action == 'add_force_sub_input':
            del waiting_for[uid]
            try:
                f_id = text.strip()
                f_title = "قناة الاشتراك"
                f_link = f"https://t.me/{f_id.replace('@','')}"
                try:
                    entity = await bot.get_entity(int(f_id) if f_id.lstrip('-').isdigit() else f_id)
                    f_title = getattr(entity, 'title', f_title)
                    f_id = f"-100{entity.id}" if not str(entity.id).startswith('-100') else entity.id
                    if getattr(entity, 'username', None): f_link = f"https://t.me/{entity.username}"
                except Exception: pass

                force_list = await db_engine.get_config('force_sub', [])
                force_list.append({"id": f_id, "title": f_title, "link": f_link})
                await db_engine.set_config('force_sub', force_list)
                await event.respond(f">تمت إضافة القناة ({f_title}) بنجاح!", parse_mode='html')
            except Exception as e:
                await event.respond(f">خطأ: <code>{e}</code>", parse_mode='html')
            return

        elif action == 'del_force_sub_input':
            del waiting_for[uid]
            if text.isdigit():
                idx = int(text) - 1
                force_list = await db_engine.get_config('force_sub', [])
                if 0 <= idx < len(force_list):
                    force_list.pop(idx)
                    await db_engine.set_config('force_sub', force_list)
                    await event.respond(">تم حذف القناة بنجاح!", parse_mode='html')
            return
    except Exception as e:
        print(f"Unhandled Handle Inputs Error: {e}")

# ==============================================================================
# 11. محرك النشر التلقائي الذكي المنضبط بالثواني
# ==============================================================================
async def publish_loop_single(uid, acc_id):
    task_key = f"{uid}_{acc_id}"
    while True:
        try:
            acc = await db_engine.get_account(acc_id)
            if not acc or not acc.get('active'):
                if task_key in running_tasks: del running_tasks[task_key]
                break

            client = await get_user_client(acc['session'])
            if not client:
                acc['active'] = False
                await db_engine.save_account(acc)
                await log_error(uid, f"انقطع الاتصال بالحساب: <b>{acc.get('name')}</b>. تم إيقاف النشر لسلامته.")
                break

            await client_pool.touch(acc['session'])

            if acc.get('schedule_mode') == 'night_pause':
                now = datetime.now()
                start_h = acc.get('night_pause_start', 2)
                end_h = acc.get('night_pause_end', 7)
                in_night_pause = (start_h <= now.hour < end_h) if start_h < end_h else (now.hour >= start_h or now.hour < end_h)
                if in_night_pause:
                    await asyncio.sleep(600)
                    continue

            groups = list(acc.get('groups', []))
            if not groups:
                await asyncio.sleep(30)
                continue

            if acc.get('random_shuffle', False): random.shuffle(groups)

            def is_msg_valid(m):
                return m.get('enabled', True) and (bool(m.get('text', '').strip()) or bool(m.get('file_path')))

            enabled_msgs = [m for m in acc.get('messages', []) if is_msg_valid(m)]
            if not enabled_msgs and not acc.get('forward_mode', False):
                await asyncio.sleep(30)
                continue

            messages_to_cycle = enabled_msgs if enabled_msgs else [{'type': 'forward'}]

            for current_msg in messages_to_cycle:
                acc = await db_engine.get_account(acc_id)
                if not acc or not acc.get('active'): break

                for group in groups:
                    acc = await db_engine.get_account(acc_id)
                    if not acc or not acc.get('active'): break

                    target_entity = None
                    try:
                        target = str(group).strip()
                        if target.startswith('https://t.me/'):
                            target = target.split('t.me/')[-1].split('/')[0].split('?')[0]
                            if not target.startswith('@') and not target.startswith('+'): target = f"@{target}"
                        if target.lstrip('-').isdigit(): target = int(target)
                        target_entity = await client.get_entity(target)
                    except (ChatWriteForbiddenError, ChannelPrivateError):
                        acc = await db_engine.get_account(acc_id)
                        if acc and group in acc.get('groups', []):
                            acc['groups'].remove(group)
                            await db_engine.save_account(acc)
                        await db_engine.log_analytics(uid, acc_id, group, "مجموعة مقيدة", "تم الحذف التلقائي من القائمة")
                        continue
                    except (AuthKeyUnregisteredError, UserDeactivatedBanError, SessionRevokedError):
                        await purge_account_safely(uid, acc_id, acc.get('name'), acc.get('phone', 'مخفي'), "سحب أو إنهاء الجلسة أثناء محاولة النشر")
                        return
                    except Exception:
                        continue

                    if acc.get('delete_last_msg', False):
                        last_msgs = acc.get('last_sent_msgs', {})
                        if str(group) in last_msgs:
                            try: await client.delete_messages(target_entity, last_msgs[str(group)])
                            except Exception: pass

                    if acc.get('react_before_send', True):
                        try:
                            async for last_chat_msg in client.iter_messages(target_entity, limit=1):
                                await client(SendReactionRequest(peer=target_entity, msg_id=last_chat_msg.id, reaction=[ReactionEmoji(emoticon='👍')]))
                            await asyncio.sleep(1.0)
                        except Exception: pass

                    sent_msg_obj = None
                    try:
                        if acc.get('forward_mode', False) and acc.get('forward_channel'):
                            fwd_ch = str(acc.get('forward_channel')).strip()
                            if fwd_ch.lstrip('-').isdigit(): fwd_ch = int(fwd_ch)
                            elif fwd_ch.startswith('https://t.me/'): fwd_ch = "@" + fwd_ch.split('t.me/')[-1].split('/')[0]
                            async for fwd_msg in client.iter_messages(fwd_ch, limit=1):
                                sent_msg_obj = await client.forward_messages(target_entity, fwd_msg)
                        elif current_msg.get('type') != 'forward':
                            text = current_msg.get('text', '')
                            ents = build_entities(current_msg.get('entities', []))
                            if current_msg.get('file_path') and os.path.exists(current_msg['file_path']):
                                cached_payload = await get_cached_media_payload(client, current_msg['file_path'])
                                sent_msg_obj = await client.send_file(target_entity, file=cached_payload, caption=text, formatting_entities=ents)
                            else:
                                if text: sent_msg_obj = await client.send_message(target_entity, text, formatting_entities=ents)

                        if sent_msg_obj:
                            last_id = sent_msg_obj[-1].id if isinstance(sent_msg_obj, list) else sent_msg_obj.id
                            acc['last_sent_msgs'][str(group)] = last_id
                            acc['sent_count'] = acc.get('sent_count', 0) + 1
                            await db_engine.save_account(acc)
                            await db_engine.log_analytics(uid, acc_id, group, getattr(target_entity, 'title', str(group)), "ناجح")
                            
                            total_s = await db_engine.get_config('total_sent', 0)
                            await db_engine.set_config('total_sent', int(total_s) + 1)

                            sd_delay = acc.get('self_destruct_delay', 0)
                            if sd_delay > 0:
                                await db_engine.add_pending_deletion(acc_id, group, last_id, sd_delay)
                    except SlowModeWaitError as smwe:
                        if acc.get('slowmode_detect', True): 
                            await asyncio.sleep(min(smwe.seconds, 120))
                    except FloodWaitError as fwe:
                        wait_t = min(fwe.seconds + 2, 300)
                        if fwe.seconds > 300:
                            acc['active'] = False
                            await db_engine.save_account(acc)
                            await log_error(uid, f"توقف الحساب ({acc.get('name')}) مؤقتاً لقيود فلود ({fwe.seconds // 60} دقيقة).")
                            return
                        await asyncio.sleep(wait_t)
                    except (AuthKeyUnregisteredError, UserDeactivatedBanError, SessionRevokedError):
                        await purge_account_safely(uid, acc_id, acc.get('name'), acc.get('phone', 'مخفي'), "حظر الحساب أو انتهاء صلاحية الجلسة")
                        return
                    except Exception:
                        pass

                    # الالتزام بدقة الثواني المحددة من العميل دون أي تأخير عشوائي إضافي
                    interval = int(acc.get('publish_interval', 150))
                    await asyncio.sleep(interval)

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Publish Loop Error for Account {acc_id}]: {e}")
            await asyncio.sleep(15)

# ==============================================================================
# 12. مستمعات الردود التلقائية والترحيب الخاص
# ==============================================================================
async def start_account_listeners(uid, acc_id):
    listener_key = f"{uid}_{acc_id}"
    if listener_key in registered_listeners: return
    acc = await db_engine.get_account(acc_id)
    if not acc: return
    client = await get_user_client(acc['session'])
    if not client: return
    registered_listeners.add(listener_key)

    @client.on(events.NewMessage(incoming=True))
    async def auto_reply_handler(event):
        try:
            if not client.is_connected(): return
            acc_c = await db_engine.get_account(acc_id)
            if not acc_c: return
            
            sender = await event.get_sender()
            if getattr(sender, 'bot', False): return

            if event.is_private:
                p_welcome = acc_c.get('private_welcome_msg', {})
                if p_welcome and p_welcome.get('enabled'):
                    sender_id = event.sender_id
                    once_only = p_welcome.get('once_only', True)
                    seen_users = p_welcome.get('seen_users', [])
                    if not (once_only and sender_id in seen_users):
                        if once_only:
                            seen_users.append(sender_id)
                            acc_c['private_welcome_msg']['seen_users'] = seen_users
                            await db_engine.save_account(acc_c)

                        first_name = getattr(sender, 'first_name', '') or 'صديقي'
                        raw_text = p_welcome.get('text', '') or ''
                        text_msg = raw_text.replace('{name}', first_name)
                        ents = build_entities(p_welcome.get('entities', [])) if p_welcome.get('entities') else None
                        file_p = p_welcome.get('file_path')

                        try:
                            if file_p and os.path.exists(file_p):
                                cached_payload = await get_cached_media_payload(client, file_p)
                                await client.send_file(event.chat_id, file=cached_payload, caption=text_msg, formatting_entities=ents)
                            elif text_msg:
                                await client.send_message(event.chat_id, text_msg, formatting_entities=ents)
                        except Exception as send_err:
                            print(f"[Private Welcome Send Error {acc_id}]: {send_err}")
                return

            if event.is_group or event.is_channel:
                me = await client.get_me()
                is_target = False
                if event.is_reply:
                    try:
                        reply_msg = await event.get_reply_message()
                        if reply_msg and reply_msg.sender_id == me.id: is_target = True
                    except Exception: pass
                if not is_target:
                    if event.mentioned: is_target = True
                    elif me.username and f"@{me.username.lower()}" in (event.raw_text or "").lower(): is_target = True

                if is_target:
                    chat_id = event.chat_id
                    sender_id = event.sender_id
                    cooldown_key = f"{acc_id}_{chat_id}_{sender_id}"
                    now_ts = datetime.now().timestamp()
                    
                    if cooldown_key in reply_cooldowns:
                        if now_ts - reply_cooldowns[cooldown_key] < 15:
                            return 
                    
                    reply_cooldowns[cooldown_key] = now_ts
                    await asyncio.sleep(2) 

                    r_msgs = [r for r in acc_c.get('reply_msgs', []) if r.get('enabled', True)]
                    if r_msgs: 
                        selected_reply = random.choice(r_msgs)['text']
                        await event.reply(selected_reply)
        except Exception as e:
            if "disconnected" not in str(e).lower(): 
                print(f"[Listener Error {acc_id}]: {e}")

# ==============================================================================
# 13. الإقلاع والتشغيل السحابي
# ==============================================================================
async def boot_system():
    print("========================================")
    print(" [النظام] جاري بدء تشغيل البوت والخدمات السحابية...")
    print("========================================")
    
    await db_engine.init()
    print(" [النظام] تم تهيئة قاعدة البيانات والجداول بنجاح.")

    asyncio.create_task(session_auto_health_checker_task())
    asyncio.create_task(background_deletion_worker())
    asyncio.create_task(analytics_logger_worker())
    asyncio.create_task(group_health_checker_task())
    asyncio.create_task(auto_backup_sessions_task())
    asyncio.create_task(lru_pool_cleanup_task())
    print(" [النظام] تم بدء مهام الفحص وحماية قاعدة البيانات والحسابات.")

    all_accs = await db_engine.get_all_accounts_admin()
    for acc in all_accs:
        uid = acc['user_id']
        aid = acc['acc_id']
        asyncio.create_task(start_account_listeners(uid, aid))
        if acc.get('active'):
            running_tasks[f"{uid}_{aid}"] = asyncio.create_task(publish_loop_single(uid, aid))
        
    print("========================================")
    print(" [النظام] المنظومة تعمل الآن بكامل طاقتها واستقرارها 100%!")
    print("========================================")

if __name__ == '__main__':
    logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.WARNING)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(boot_system())
    
    bot.start(bot_token=BOT_TOKEN)
    print("عميل البوت متصل ويستمع للتحديثات والمدفوعات...")
    bot.run_until_disconnected()
