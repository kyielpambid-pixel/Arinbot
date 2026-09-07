# ============================================================
# FULL BOT.PY — SigmaBot with Luraph Deobfuscator
# Platform: Linux/Windows | Python 3.10+ | discord.py 2.x
# Architecture: async discord bot, slash commands, aiohttp HTTP
# ============================================================

import os
import re
import sys
import json
import time
import struct
import zlib
import base64
import asyncio
import aiohttp
import discord
import tempfile
import shutil
import subprocess
import traceback
import functools
from contextlib import suppress
from urllib.parse import urlparse
from discord import app_commands

# ============================================================
# CONFIG
# ============================================================

DEOBF_REQUEST_TIMEOUT_SECONDS = 60
DEOBF_STEP_INTERVAL_SECONDS   = 3    # was 7 — faster progress ticks
DEOBF_LARGE_TIMEOUT_SECONDS   = 120
DEOBF_SMALL_TIMEOUT_SECONDS   = 60
DEOBF_MAX_PROCESS_SECONDS     = 300
LARGE_DEOBF_FILE_BYTES        = 512 * 1024  # 512 KB

DEOBF_READ_STAGE    = 0
DEOBF_ANALYZE_STAGE = 1
DEOBF_SCRIPT_STAGE  = 2
DEOBF_REVIEW_STAGE  = 3
DEOBF_ERRORS_STAGE  = 4
DEOBF_CLEAN_STAGE   = 5
DEOBF_REFRESH_STAGE = 6
DEOBF_FINALIZE_STAGE = 7

COMMAND_FAILURE_MESSAGE = "An unexpected error occurred during deobfuscation."
BOT_FOOTER              = "Powered by KHUB & Kyiel"   # ← changed

IS_MOONVEIL_V2022 = None
MOONVEIL_DECODER  = None

_large_deobf_timeouts: dict[int, float] = {}
LARGE_DEOBF_COOLDOWN_SECONDS = 180

# ============================================================
# DISCORD CLIENT
# ============================================================

intents    = discord.Intents.default()
client     = discord.Client(intents=intents)
tree       = app_commands.CommandTree(client)
client.tree = tree

# ============================================================
# LURAPH VERSION PATTERNS
# ============================================================

LUAOBF_ALPHA_MODES = {
    "chaotic_good":  "Chaotic Good",
    "chaotic_evil":  "Chaotic Evil",
    "obfuscate_old": "Obfuscate (Old)",
    "obfuscate_v1":  "Obfuscate V1",
}

LURAPH_VERSION_PATTERNS = {
    "Luraph 10.7": (
        r"\bLuraph\b", r"luraph\.net",
        r"local\s+\w+\s*=\s*\{[^}]*0x[0-9a-fA-F]{4,}[^}]*\}",
        r"string\.char\s*\(\s*(?:\w+\s*\(\s*\w+\s*,\s*\w+\s*\)\s*,?\s*){3,}\)",
        r"local\s+\w+\s*=\s*\w+\s*%\s*256",
    ),
    "Luraph 14.7": (
        r"\bLuraph\b", r"luraph\.net",
        r"local\s+\w+\s*=\s*\w+\s*\(\s*\w+\s*,\s*\w+\s*\+\s*\w+\s*\)",
        r"(?:0x[0-9a-fA-F]{6,})", r"\bbit32\b.*?\bbxor\b",
    ),
    "Luraph 14.8": (
        r"\bLuraph\b", r"luraph\.net",
        r"local\s+\w+\s*=\s*\w+\s*\(\s*\w+\s*,\s*\w+\s*\+\s*1\s*\)",
        r"(?:0x[0-9a-fA-F]{6,})", r"\bbit32\.bxor\b|\bbit\.bxor\b",
    ),
    "Luraph 15.0": (
        r"\bLuraph\b", r"luraph\.net",
        r"local\s+\w+\s*=\s*\w+\s*\(\s*\w+\s*,\s*\w+\s*\+\s*1\s*\)",
        r"local\s+\w+\s*=\s*\w+\s*\*\s*\w+\s*\+\s*\w+",
        r"0x[0-9a-fA-F]{8,}",
        r"local\s+\w+\s*=\s*setmetatable\s*\(",
        # 15.0 specific: buffer.create / buffer.writeu32
        r"\bbuffer\.create\b", r"\bbuffer\.writeu32\b",
    ),
    "Luraph (Generic)": (
        r"\bLuraph\b", r"luraph\.net",
        r"--\s*Obfuscated\s+(?:with|by|using)\s+Luraph",
    ),
}

SUPPORTED_DEOBFUSCATORS = {
    "MoonVeil 2.0.22": {
        "endpoint": None,
        "patterns": (
            r"^\s*return\s*\(\s*\{",
            r":\s*[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\.\.\.\s*\)\s*;?\s*$",
            r"moonveil", r"moonveil\.cc",
        ),
    },
    "Moonsec V3": {
        "endpoint": "https://leakd.up.railway.app/moonsec",
        "patterns": (r"moonsec\s*v3", r"protected\s+with\s+moonsec\s*v3",
                     r"moon_api", r"moon_encrypt"),
    },
    "Prometheus/WeAreDevs": {
        "endpoint": "https://leakd.up.railway.app/prometheus",
        "patterns": (r"wearedevs", r"wad_api", r"w_encrypt",
                     r"v1\.0\.0\s+https://wearedevs\.net/obfuscator"),
    },
    "Hercules": {
        "endpoint": "https://leakd.up.railway.app/hercules",
        "patterns": (r"\bhercules\b", r"obfuscated\s+by\s+hercules",
                     r"protected\s+by\s+hercules",
                     r"github\.com/zeusssz/hercules-obfuscator"),
    },
    "LuaObfuscator Alpha (Chaotic Good)": {
        "endpoint": None,
        "patterns": (r"\bferib\b", r"_welcome\s+to\s+luaobfuscator\.com",
                     r"local\s+\w+\s*=\s*string\.char.*?local\s+function\s+\w+",
                     r'\b\w+\s*\(\s*["\']\\\d+'),
    },
    "LuaObfuscator Alpha (Chaotic Evil)": {
        "endpoint": None,
        "patterns": (
            r"local\s+v0\s*=\s*string\.char\s*;local\s+v1\s*=\s*string\.byte\s*;local\s+v2\s*=\s*string\.sub\s*;local\s+v3\s*=\s*bit32\s+or\s+bit\s*;",
            r"integrity\s+protected", r"_Welcome\s+to\s+LuaObfuscator\.com",
            r'["\'"]LOL![0-9A-Fa-f2-9Q]{20,}["\']', r"local\s+function\s+v23\s*\(",
        ),
    },
    "LuaObfuscator Alpha (Obfuscate V1)": {
        "endpoint": None,
        "patterns": (r"\bLOL!0E3Q", r"\blocal\s+v0\s*=\s*tonumber\b.*?\bLOL!"),
    },
    "LuaObfuscator Alpha (Obfuscate Old)": {
        "endpoint": None,
        "patterns": (r"\bLOL!153Q", r"\blocal\s+v0\s*=\s*tonumber\b.*?\bLOL!"),
    },
}

for _luraph_name, _luraph_patterns in LURAPH_VERSION_PATTERNS.items():
    SUPPORTED_DEOBFUSCATORS[_luraph_name] = {"endpoint": None, "patterns": _luraph_patterns}

DEOBF_PROGRESS_LABELS = (
    "Reading the input...",
    "Analyzing the structure...",
    "Deobfuscating the script...",
    "Reviewing the result...",
    "Checking for errors...",
    "Refreshing the output...",
    "Cleaning the result...",
    "Finalizing...",
)

DEOBF_URL_PATTERN = re.compile(r"https?://[^\s<>'`\"]+", re.IGNORECASE)

# ============================================================
# UTILITY STUBS
# ============================================================

def remove_lua_comments(content: str) -> str:
    content = re.sub(r"--\[\[.*?\]\]", "", content, flags=re.DOTALL)
    content = re.sub(r"--[^\n]*", "", content)
    return content

def rename_obfuscated_lua_identifiers(content: str) -> str:
    return content

def clean_lua_without_ai(content: str) -> str:
    return content

def read_script_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()

def write_script_text(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def is_large_deobf_file(file) -> bool:
    return bool(file and hasattr(file, "size") and file.size >= LARGE_DEOBF_FILE_BYTES)

def large_deobf_remaining(user_id: int) -> int:
    expiry    = _large_deobf_timeouts.get(user_id, 0)
    remaining = expiry - time.monotonic()
    return max(0, int(remaining))

def clear_large_deobf_timeout(user_id: int) -> None:
    _large_deobf_timeouts.pop(user_id, None)

# ============================================================
# PASTEFY UPLOAD
# ============================================================

PASTEFY_API = "https://pastefy.app/api/v2/paste"

async def upload_to_pastefy(content: str, title: str) -> str:
    payload = {"title": title, "content": content, "type": "PASTE", "encrypted": False}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                PASTEFY_API,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json(content_type=None)
                paste_id = data.get("paste", {}).get("id") or data.get("id")
                if paste_id:
                    return f"https://pastefy.app/{paste_id}"
                return "Pastefy upload failed (no ID returned)."
    except Exception as exc:
        return f"Pastefy upload error: {exc}"

# ============================================================
# INPUT LOADING
# ============================================================

async def load_deobfuscation_input(file, url):
    if file:
        data = await file.read()
        fd, path = tempfile.mkstemp(suffix=".lua", prefix="sigmabot-input-")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(data)
        return path, data.decode("utf-8", errors="replace")
    elif url:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                data = await resp.read()
        fd, path = tempfile.mkstemp(suffix=".lua", prefix="sigmabot-input-")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(data)
        return path, data.decode("utf-8", errors="replace")
    raise ValueError("No file or URL provided.")

async def fetch_url_content(url: str) -> bytes:
    """Fetch raw bytes from any URL — used by /convert."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            return await resp.read()

# ============================================================
# LURAPH LOW-LEVEL HELPERS
# ============================================================

def _luraph_read_uint8(data, offset):
    if offset >= len(data): return 0, offset
    return data[offset], offset + 1

def _luraph_read_uint16_le(data, offset):
    if offset + 2 > len(data): return 0, offset
    return struct.unpack_from("<H", data, offset)[0], offset + 2

def _luraph_read_uint32_le(data, offset):
    if offset + 4 > len(data): return 0, offset
    return struct.unpack_from("<I", data, offset)[0], offset + 4

def _luraph_read_double(data, offset):
    if offset + 8 > len(data): return 0.0, offset
    return struct.unpack_from("<d", data, offset)[0], offset + 8

def _luraph_read_string(data, offset, length):
    if offset + length > len(data): return "", offset
    raw = data[offset:offset + length]
    try:    return raw.decode("utf-8",  errors="replace"), offset + length
    except: return raw.decode("latin-1",errors="replace"), offset + length

def _luraph_xor_stream(data: bytes, seed: int) -> bytes:
    state    = seed & 0xFFFFFFFF
    keystream = bytearray(256)
    for i in range(256):
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        keystream[i] = (state >> 16) & 0xFF
    result = bytearray(len(data))
    for i, byte in enumerate(data):
        result[i] = byte ^ keystream[i % 256]
    return bytes(result)

# ─── Luraph 15.0 key extraction ───────────────────────────────────────────────
# 15.0 embeds its key differently from 14.x:
#   byte[0]          = key_length  (u8)
#   byte[1..key_len] = raw XOR key
#   byte[key_len+1]  = payload_length (u32 LE)
#   byte[key_len+5:] = encrypted payload
# The chained XOR feeds each output byte back into the keystream (CBC-ish).

def _luraph_xor_v15_full(blob: bytes) -> bytes:
    """Attempt all plausible key-length prefixes for 15.0 blobs."""
    candidates = []
    for key_len_guess in range(4, min(65, len(blob))):
        if key_len_guess >= len(blob): break
        key     = blob[:key_len_guess]
        payload = blob[key_len_guess:]
        out     = bytearray(len(payload))
        prev    = 0
        for i, b in enumerate(payload):
            k       = key[i % key_len_guess]
            out[i]  = b ^ k ^ (prev & 0xFF)
            prev    = out[i]
        candidates.append(bytes(out))
    return candidates

def _luraph_xor_v15(data: bytes, key: bytes) -> bytes:
    """Original chained-XOR used for non-15.0 blobs."""
    if not key: return data
    result = bytearray(len(data))
    prev   = 0
    for i, byte in enumerate(data):
        k         = key[i % len(key)]
        result[i] = byte ^ k ^ (prev & 0xFF)
        prev      = result[i]
    return bytes(result)

LURAPH_CONST_NIL    = 0
LURAPH_CONST_BOOL   = 1
LURAPH_CONST_NUMBER = 3
LURAPH_CONST_STRING = 4

def _luraph_extract_constants(blob: bytes) -> list:
    constants = []
    offset    = 0
    length    = len(blob)
    while offset < length:
        ctype, offset = _luraph_read_uint8(blob, offset)
        if ctype == LURAPH_CONST_NIL:
            constants.append(None)
        elif ctype == LURAPH_CONST_BOOL:
            val, offset = _luraph_read_uint8(blob, offset)
            constants.append(bool(val))
        elif ctype == LURAPH_CONST_NUMBER:
            val, offset = _luraph_read_double(blob, offset)
            constants.append(val)
        elif ctype == LURAPH_CONST_STRING:
            str_len, offset = _luraph_read_uint32_le(blob, offset)
            if str_len == 0:
                constants.append("")
                continue
            if str_len > 65536 or offset + str_len > length:
                break
            val, offset = _luraph_read_string(blob, offset, str_len)
            constants.append(val)
        else:
            break
    return constants

_LURAPH_HEX_RE  = re.compile(r"""(?:["'])([0-9a-fA-F]{64,})(?:["'])""",       re.IGNORECASE)
_LURAPH_B64_RE  = re.compile(r"""(?:["'])([A-Za-z0-9+/]{64,}={0,2})(?:["'])""")
# 15.0 also uses bare base64 assigned to local variables (U=[[...]])
_LURAPH_LB64_RE = re.compile(r"""=\s*\[=\[([A-Za-z0-9+/\n\r]{32,}={0,2})\]=\]""")

def _luraph_extract_blobs(content: str) -> list:
    blobs = []
    seen  = set()

    def _push(b: bytes):
        if b and b not in seen:
            seen.add(b)
            blobs.append(b)

    for match in _LURAPH_HEX_RE.finditer(content):
        raw = match.group(1)
        if len(raw) % 2 != 0: continue
        try: _push(bytes.fromhex(raw))
        except ValueError: pass

    for match in _LURAPH_B64_RE.finditer(content):
        try: _push(base64.b64decode(match.group(1) + "=="))
        except Exception: pass

    # Long-bracket base64 (Luraph 15.0 style: U=[=[...]=])
    for match in _LURAPH_LB64_RE.finditer(content):
        raw = re.sub(r"[\r\n\s]", "", match.group(1))
        try: _push(base64.b64decode(raw + "=="))
        except Exception: pass

    return blobs

_LURAPH_SEED_RE = re.compile(r"\b(0x[0-9a-fA-F]{4,}|\d{5,})\b")

def _luraph_extract_seeds(content: str) -> list:
    seeds = []
    seen  = set()
    for match in _LURAPH_SEED_RE.finditer(content):
        raw = match.group(1)
        try:
            val = int(raw, 16) if raw.startswith("0x") else int(raw)
            if val not in seen and 0x1000 <= val <= 0xFFFFFFFF:
                seen.add(val)
                seeds.append(val)
        except ValueError:
            pass
    return seeds

def _score_constant_list(consts: list) -> int:
    """Return how many string constants look like real Lua identifiers / keywords."""
    score = 0
    LUA_KW = {"local","function","return","if","then","else","for","while",
               "end","print","require","loadstring","game","workspace",
               "script","wait","spawn","coroutine","table","string","math"}
    for c in consts:
        if not isinstance(c, str) or len(c) < 2: continue
        if c in LUA_KW:          score += 3
        elif re.match(r'^[A-Za-z_]\w{1,40}$', c): score += 1
    return score

def _luraph_try_decrypt_blob(blob: bytes, seed: int, version: str) -> list:
    results    = []
    candidates = []

    if "15.0" in version:
        # Try all key-length prefixes for 15.0 first
        candidates.extend(_luraph_xor_v15_full(blob))
        # Also try seeded LCG stream in case key extraction differs
        candidates.append(_luraph_xor_stream(blob, seed))
        # Try raw zlib on the blob itself
        try: candidates.append(zlib.decompress(blob))
        except Exception: pass
    else:
        candidates.append(_luraph_xor_stream(blob, seed))
        xored = _luraph_xor_stream(blob, seed)
        try:    candidates.append(zlib.decompress(xored))
        except: pass
        try:    candidates.append(zlib.decompress(blob))
        except: pass
        if len(blob) > 32:
            key_len = blob[0]
            if 4 <= key_len <= 64 and key_len < len(blob):
                key     = blob[1:1 + key_len]
                payload = blob[1 + key_len:]
                candidates.append(_luraph_xor_v15(payload, key))

    best_consts = []
    best_score  = -1
    for candidate in candidates:
        consts = _luraph_extract_constants(candidate)
        score  = _score_constant_list(consts)
        if score > best_score:
            best_score  = score
            best_consts = consts

    for c in best_consts:
        if isinstance(c, str) and len(c) >= 2:
            ratio = sum(ch.isprintable() or ch in "\n\r\t" for ch in c) / max(len(c), 1)
            if ratio > 0.85:
                results.append(c)
    return results

def _luraph_recover_strings(content: str, version: str) -> list:
    blobs   = _luraph_extract_blobs(content)
    seeds   = _luraph_extract_seeds(content)
    if 0 not in seeds: seeds.insert(0, 0)
    recovered = []
    seen      = set()
    for blob in blobs:
        for seed in seeds:
            for s in _luraph_try_decrypt_blob(blob, seed, version):
                if s not in seen:
                    seen.add(s)
                    recovered.append(s)
    return recovered

def _lua_literal(value):
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")

def _luraph_reconstruct_source(content: str, strings: list, version: str) -> str:
    lines = [
        f"-- Deobfuscated by SigmaBot | Luraph {version}",
        f"-- Recovered {len(strings)} string constant(s) from VM bytecode",
        "-- Note: control flow cannot be recovered without a full VM emulator.",
        "",
    ]
    if strings:
        lines.append("-- Recovered string constants")
        for i, s in enumerate(strings, 1):
            lines.append(f"local recovered_str_{i} = {_lua_literal(s)}")
        lines.append("")
    api_re   = re.compile(r"\b(game\s*:\s*\w+|loadstring\s*\(|pcall\s*\(|HttpGet\s*\()\s*", re.IGNORECASE)
    api_calls = list(dict.fromkeys(m.group(0).strip() for m in api_re.finditer(content)))
    if api_calls:
        lines.append("-- Detected API patterns")
        for call in api_calls[:24]: lines.append(f"-- {call}")
        lines.append("")
    url_re = re.compile(r"https?://[^\s'\"\\>]{8,}", re.IGNORECASE)
    urls   = list(dict.fromkeys(url_re.findall(content)))
    if urls:
        lines.append("-- Detected loader URLs")
        for url in urls[:8]: lines.append(f"-- {url}")
        lines.append("")
    cleaned = remove_lua_comments(content).strip()
    cleaned = rename_obfuscated_lua_identifiers(cleaned)
    cleaned = re.sub(r'(?:["\'`])[0-9a-fA-F]{64,}(?:["\'`])', '"[LURAPH_BLOB]"', cleaned)
    cleaned = re.sub(r'=\s*\[=\[.{32,}?\]=\]', '= "[LURAPH_BLOB]"', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    lines.append("-- Structural skeleton")
    lines.append(cleaned)
    return "\n".join(lines)

def _luraph_detect_version(content: str):
    scores = {}
    for version, patterns in LURAPH_VERSION_PATTERNS.items():
        score = sum(1 for p in patterns if re.search(p, content, re.IGNORECASE | re.DOTALL))
        if score > 0: scores[version] = score
    if not scores: return None
    specific = {k: v for k, v in scores.items() if k != "Luraph (Generic)"}
    if specific: return max(specific, key=lambda k: specific[k])
    return "Luraph (Generic)"

def _luraph_deobfuscate(content: str, service_name: str):
    version = service_name.replace("Luraph ", "").replace("Luraph", "").strip()
    if not version or version == "(Generic)":
        detected = _luraph_detect_version(content)
        version  = detected.replace("Luraph ", "") if detected else "Generic"
    strings = _luraph_recover_strings(content, version)
    if not strings:
        cleaned = remove_lua_comments(content).strip()
        cleaned = rename_obfuscated_lua_identifiers(cleaned)
        if cleaned and len(cleaned) > 20:
            return (
                f"-- Deobfuscated by SigmaBot | Luraph {version}\n"
                f"-- Warning: no string constants could be decrypted.\n"
                f"-- Structural skeleton follows.\n\n" + cleaned
            )
        return None
    return _luraph_reconstruct_source(content, strings, version)

# ============================================================
# LUAOBFUSCATOR ALPHA HELPERS  (unchanged from original)
# ============================================================

def _decode_lua_escaped_text(value):
    output = []
    index  = 0
    escape_map = {"a":"\a","b":"\b","f":"\f","n":"\n","r":"\r","t":"\t","v":"\v","\\":"\\",'"':'"',"'":"'"}
    while index < len(value):
        ch = value[index]
        if ch != "\\":
            output.append(ch); index += 1; continue
        index += 1
        if index >= len(value): output.append("\\"); break
        esc = value[index]
        if esc.isdigit():
            digits = [esc]; index += 1
            while index < len(value) and len(digits) < 3 and value[index].isdigit():
                digits.append(value[index]); index += 1
            output.append(chr(int("".join(digits), 10) % 256)); continue
        if esc == "x" and index + 2 < len(value):
            hx = value[index+1:index+3]
            if re.fullmatch(r"[0-9a-fA-F]{2}", hx):
                output.append(chr(int(hx, 16))); index += 3; continue
        output.append(escape_map.get(esc, esc)); index += 1
    return "".join(output)

def _decode_luaobfuscator_blob(value):
    if not value.startswith("LOL!"): return b""
    encoded     = value[4:]
    decoded     = bytearray()
    repeat_count = 1
    for index in range(0, len(encoded) - 1, 2):
        pair = encoded[index:index + 2]
        if pair[1] == "Q":
            if pair[0].isdigit(): repeat_count = int(pair[0])
            continue
        try:    decoded.extend(bytes([int(pair, 16)]) * repeat_count)
        except: return b""
        repeat_count = 1
    return bytes(decoded)

def _extract_luaobfuscator_blobs(content):
    blobs   = []
    pattern = re.compile(r"""(["'])(LOL![0-9A-Fa-f2-9Q]+)\1""")
    for match in pattern.finditer(content):
        decoded = _decode_luaobfuscator_blob(match.group(2))
        if decoded: blobs.append(decoded)
    return blobs

def _extract_printable_strings(data):
    strings = []
    for match in re.finditer(rb"[\x20-\x7e]{2,}", data):
        value = match.group(0).decode("latin1", errors="ignore")
        if value not in strings: strings.append(value)
    return strings

def _looks_like_lua_source(value):
    if not value or len(value) < 8: return False
    ratio = sum(c in "\n\r\t" or ord(c) >= 32 for c in value) / len(value)
    if ratio < 0.96: return False
    return bool(
        re.search(r"\b(?:local|function|return|if|then|else|elseif|for|while|repeat|do|end|print|string|table|loadstring|require)\b", value)
        and re.search(r"\b(?:local|function|return|if|for|while|print)\b", value)
        and ("=" in value or "(" in value or "\n" in value)
    )

def _recover_embedded_lua_source(blobs):
    candidates = []
    for blob in blobs:
        candidates.append(blob.decode("utf-8", errors="replace"))
        for nested in re.findall(rb"LOL![A-Za-z0-9]+", blob):
            nested_blob = _decode_luaobfuscator_blob(nested.decode("ascii", errors="ignore"))
            if nested_blob: candidates.append(nested_blob.decode("utf-8", errors="replace"))
    for candidate in candidates:
        candidate = remove_lua_comments(candidate).strip()
        if _looks_like_lua_source(candidate):
            cleaned = "".join(c for c in candidate if c in "\n\r\t" or ord(c) >= 32)
            if _looks_like_lua_source(cleaned):
                return rename_obfuscated_lua_identifiers(cleaned).strip()
    return None

def _recover_lua_from_constants(content, blobs):
    printable = []
    for blob in blobs:
        for value in _extract_printable_strings(blob):
            if value not in printable: printable.append(value)
    ignored = {"string","char","byte","sub","gsub","tonumber","math","ldexp","getfenv","setmetatable","pcall","select","unpack","table","concat","insert","bit32","bit","bxor","print","_ENV"}
    useful  = [v for v in printable if v not in ignored and not v.startswith("LOL!") and len(v) <= 4096 and not re.fullmatch(r"[\W_]+", v)]
    if not useful: return None
    lines = ["-- Recovered LuaObfuscator VM constants."]
    for i, v in enumerate(useful, 1): lines.append(f"local recovered_{i} = {_lua_literal(v)}")
    lines.extend(["","return {", *[f"    recovered_{i}," for i in range(1, len(useful)+1)], "}"])
    return "\n".join(lines)

def _xor_decrypt_v7(encrypted: bytes, key: bytes) -> bytes:
    if not key or not encrypted: return encrypted
    result  = bytearray(len(encrypted))
    key_len = len(key)
    for i in range(len(encrypted)):
        result[i] = encrypted[i] ^ key[(i + 1) % key_len]
    return bytes(result)

def _parse_luaobf_blob_string_constants(blob_data: bytes):
    constants = []
    i = 0; length = len(blob_data)
    while i < length - 5:
        if blob_data[i] == 0x03:
            str_len = blob_data[i + 1]
            if str_len > 0 and (i + 5 + str_len) <= length:
                if blob_data[i+2] == 0 and blob_data[i+3] == 0 and blob_data[i+4] == 0:
                    str_bytes = blob_data[i+5:i+5+str_len]
                    constants.append((i, str_len, str_bytes))
                    i += 5 + str_len; continue
        i += 1
    return constants

def _deobfuscate_chaotic_evil_blob(content: str):
    blobs = _extract_luaobfuscator_blobs(content)
    if not blobs: return None
    for blob_data in blobs:
        constants   = _parse_luaobf_blob_string_constants(blob_data)
        if not constants: continue
        url_results = []; lua_results = []; seen = set()
        for ki, klen, key_bytes in constants:
            if klen < 4 or klen > 128: continue
            for di, dlen, data_bytes in constants:
                if di == ki or dlen <= klen: continue
                decrypted_raw = _xor_decrypt_v7(data_bytes, key_bytes)
                try:    text = decrypted_raw.decode("utf-8")
                except: 
                    try: text = decrypted_raw.decode("latin-1")
                    except: continue
                if not text or text in seen: continue
                ratio = sum(c.isprintable() or c in "\n\r\t" for c in text) / max(len(text), 1)
                if ratio < 0.88: continue
                seen.add(text)
                stripped = text.strip().rstrip("\x00")
                if re.match(r"https?://", stripped):   url_results.append(stripped)
                elif _looks_like_lua_source(stripped): lua_results.append(stripped)
        if lua_results: return max(lua_results, key=len)
        if url_results:
            url = url_results[0]
            return f'-- Payload URL: {url}\n\nloadstring(game:HttpGet("{url}"))() '
    return None

def _luaobfuscator_mode_scores(content):
    scores       = {mode: 0 for mode in LUAOBF_ALPHA_MODES}
    lower_content = content.lower()
    if re.search(r'local\s+\w+\s*=\s*string\.char\s*;?\s*local\s+\w+\s*=\s*string\.byte', content, re.IGNORECASE):
        scores["chaotic_good"] += 2; scores["chaotic_evil"] += 2
    if re.search(r'local\s+\w+\s*=\s*string\.char.*?local\s+\w+\s*=\s*bit32\s+or\s+bit', content, re.IGNORECASE | re.DOTALL):
        scores["chaotic_good"] += 2; scores["chaotic_evil"] += 3
    if re.search(r'local\s+function\s+\w+\s*\(.*?bxor', content, re.IGNORECASE | re.DOTALL):
        scores["chaotic_good"] += 2; scores["chaotic_evil"] += 1
    if "integrity protected" in lower_content: scores["chaotic_evil"] += 4
    if re.search(r'\b\w+\s*\(\s*["\']\\d+', content): scores["chaotic_good"] += 4
    if re.search(r'local\s+v0\s*=\s*tonumber\s*;?\s*local\s+v1\s*=\s*string\.byte', content, re.IGNORECASE):
        scores["obfuscate_old"] += 2; scores["obfuscate_v1"] += 2
    if re.search(r'\b\w+\s*\(\s*["\']LOL!0E3Q', content): scores["obfuscate_v1"] += 5
    if re.search(r'\b\w+\s*\(\s*["\']LOL!153Q', content): scores["obfuscate_old"] += 5
    return scores

def _luaobfuscator_detect_mode(content):
    scores              = _luaobfuscator_mode_scores(content)
    best_mode, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score <= 0: return None
    if best_mode in ("chaotic_good", "chaotic_evil") and "LOL!" in content:
        if scores["chaotic_evil"] > scores["chaotic_good"]: return "chaotic_evil"
    return best_mode

def _luaobfuscator_candidate_matches(content, mode):
    if mode == "chaotic_good":  return bool(re.search(r'\b\w+\s*\(\s*["\']\\d+', content) and not re.search(r'["\']LOL![0-9A-Fa-f2-9Q]+["\']', content))
    if mode == "chaotic_evil":  return bool("integrity protected" in content.lower() or re.search(r'\b\w+\s*\(\s*["\']LOL!', content))
    if mode == "obfuscate_v1":  return bool(re.search(r'\b\w+\s*\(\s*["\']LOL!0E3Q', content) or (re.search(r'\bLOL!', content) and re.search(r'\blocal\s+v0\s*=\s*tonumber\b', content, re.IGNORECASE) and not re.search(r'LOL!153Q', content)))
    if mode == "obfuscate_old": return bool(re.search(r'\b\w+\s*\(\s*["\']LOL!153Q', content) or (re.search(r'\bLOL!', content) and re.search(r'\blocal\s+v0\s*=\s*tonumber\b', content, re.IGNORECASE) and not re.search(r'LOL!0E3Q', content)))
    return False

def _deobfuscate_luaobfuscator_chaotic_good(content):
    patterns = (
        re.compile(r'\b[A-Za-z_][A-Za-z0-9_]*\s*\(\s*"((?:\\.|[^"])*)"' r'\s*,\s*"((?:\\.|[^"])*)"\s*\)'),
        re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(\s*'((?:\\.|[^'])*)'" r"\s*,\s*'((?:\\.|[^'])*)'\s*\)"),
    )
    matches      = [m for p in patterns for m in p.finditer(content)]
    if not matches: return None
    replacements = {}
    for match in matches:
        encrypted = _decode_lua_escaped_text(match.group(1)).encode("latin1", errors="ignore")
        key       = _decode_lua_escaped_text(match.group(2)).encode("latin1", errors="ignore")
        if not encrypted or not key: continue
        decoded = bytes(v ^ key[i % len(key)] for i, v in enumerate(encrypted, 1)).decode("utf-8", errors="replace")
        replacements[match.group(0)] = _lua_literal(decoded)
    if not replacements: return None
    output = content
    for src, rep in replacements.items(): output = output.replace(src, rep)
    output = remove_lua_comments(output).strip()
    output = re.sub(r'\blocal\s+v\d+\s*=\s*string\.(?:char|byte|sub)\s*;?', "", output)
    output = re.sub(r'\blocal\s+v\d+\s*=\s*bit32\s+or\s+bit\s*;?',         "", output)
    output = re.sub(r'\n{3,}', "\n\n", output).strip()
    return output if output else None

def _build_luaobfuscator_vm_output(content, mode, blobs):
    recovered_source    = _recover_embedded_lua_source(blobs)
    if recovered_source: return recovered_source
    recovered_constants = _recover_lua_from_constants(content, blobs)
    if recovered_constants: return recovered_constants
    cleaned = remove_lua_comments(content).strip()
    return rename_obfuscated_lua_identifiers(cleaned).strip()

def _luaobfuscator_deobfuscate(content, mode):
    if not _luaobfuscator_candidate_matches(content, mode): return None
    if mode == "chaotic_good":
        decoded = _deobfuscate_luaobfuscator_chaotic_good(content)
        if decoded: return decoded
    if mode == "chaotic_evil":
        decoded = _deobfuscate_chaotic_evil_blob(content)
        if decoded and decoded.strip(): return decoded
    blobs = _extract_luaobfuscator_blobs(content)
    if blobs: return _build_luaobfuscator_vm_output(content, mode, blobs)
    if mode in ("obfuscate_old", "obfuscate_v1"):
        cleaned = remove_lua_comments(content).strip()
        return rename_obfuscated_lua_identifiers(cleaned).strip()
    return None

async def _try_luaobfuscator_modes(content, preferred_mode=None):
    scores = _luaobfuscator_mode_scores(content)
    modes  = list(LUAOBF_ALPHA_MODES)
    modes.sort(key=lambda c: scores.get(c, 0), reverse=True)
    if preferred_mode in modes:
        modes.remove(preferred_mode); modes.insert(0, preferred_mode)
    for candidate in modes:
        try:
            result = await asyncio.to_thread(_luaobfuscator_deobfuscate, content, candidate)
        except Exception as error:
            print(f"[LuaObfuscator Alpha] {LUAOBF_ALPHA_MODES[candidate]} failed: {error}", file=sys.stderr, flush=True)
            continue
        if isinstance(result, str) and result.strip(): return result
    return None

def _luaobfuscator_service_mode(service_name):
    if service_name.endswith("(Chaotic Good)"):  return "chaotic_good"
    if service_name.endswith("(Chaotic Evil)"):  return "chaotic_evil"
    if service_name.endswith("(Obfuscate V1)"): return "obfuscate_v1"
    if service_name.endswith("(Obfuscate Old)"): return "obfuscate_old"
    return None

# ============================================================
# DETECTION
# ============================================================

def detect_supported_deobfuscator(content: str):
    if IS_MOONVEIL_V2022 is not None:
        try:
            if IS_MOONVEIL_V2022(content): return "MoonVeil 2.0.22"
        except Exception as error:
            print(f"[moonveil] detection failed: {error}", file=sys.stderr, flush=True)

    matches        = []
    luraph_matches = []

    for name, detector in SUPPORTED_DEOBFUSCATORS.items():
        score = sum(1 for pattern in detector["patterns"] if re.search(pattern, content, re.IGNORECASE | re.DOTALL))
        if not score: continue
        if name.startswith("Luraph"): luraph_matches.append((score, name))
        else:                         matches.append((score, name))

    if luraph_matches:
        luraph_matches.sort(key=lambda x: (x[0], x[1] != "Luraph (Generic)"), reverse=True)
        best_luraph_score, best_luraph_name = luraph_matches[0]
        if not matches or best_luraph_score >= matches[0][0]:
            return best_luraph_name

    return max(matches, key=lambda item: item[0])[1] if matches else None

# ============================================================
# API DEOBFUSCATION
# ============================================================

def _extract_deobfuscated_code(response_data):
    if isinstance(response_data, str) and response_data.strip(): return response_data
    if not isinstance(response_data, dict): return None
    keys = ("deobfuscated_code","deobfuscatedCode","deobfuscated","decrypted_code","code","output","source","script","lua")
    for key in keys:
        value = response_data.get(key)
        if isinstance(value, str) and value.strip(): return value
    for key in ("data","payload","response","result"):
        nested = response_data.get(key)
        value  = _extract_deobfuscated_code(nested)
        if value: return value
    return None

async def request_deobfuscation_api(input_path, endpoint, service_name):
    request_timeout = aiohttp.ClientTimeout(total=DEOBF_REQUEST_TIMEOUT_SECONDS, connect=30, sock_read=DEOBF_REQUEST_TIMEOUT_SECONDS)
    form = aiohttp.FormData()
    with open(input_path, "rb") as script_file:
        form.add_field("file", script_file, filename="script.lua", content_type="text/x-lua")
        async with aiohttp.ClientSession(timeout=request_timeout) as session:
            async with session.post(endpoint, data=form) as response:
                status_code    = response.status
                response_text  = await response.text()
                try:    response_data = json.loads(response_text)
                except: response_data = response_text
    if status_code < 200 or status_code >= 300:
        error = (response_data.get("error") if isinstance(response_data, dict) else None) or response_text.strip() or f"HTTP {status_code}"
        raise RuntimeError(f"{service_name} deobfuscation failed: {error}")
    deobfuscated_code = _extract_deobfuscated_code(response_data)
    if not isinstance(deobfuscated_code, str) or not deobfuscated_code.strip():
        error = response_data.get("error") if isinstance(response_data, dict) else None
        raise RuntimeError(f"The {service_name} service returned no deobfuscated code" + (f": {error}" if error else "."))
    return deobfuscated_code

async def try_deobfuscator(input_path: str, content: str, service_name: str) -> str:
    detector = SUPPORTED_DEOBFUSCATORS[service_name]
    if service_name == "MoonVeil 2.0.22":
        if MOONVEIL_DECODER is None: raise RuntimeError("The MoonVeil decoder module is not available.")
        source_name = os.path.basename(input_path) or "script.lua"
        result      = await asyncio.to_thread(lambda: MOONVEIL_DECODER().deobfuscate(content, source_name=source_name))
        output      = result.output
        if result.warnings:
            output = "-- MoonVeil 2.0.22 decoded locally.\n" + "\n".join(f"-- Warning: {w}" for w in result.warnings) + "\n\n" + output
        return output
    if service_name.startswith("Luraph"):
        result = await asyncio.to_thread(_luraph_deobfuscate, content, service_name)
        if isinstance(result, str) and result.strip(): return result
        raise RuntimeError(f"{service_name}: deobfuscation produced no output.")
    if detector["endpoint"]:
        return await request_deobfuscation_api(input_path, detector["endpoint"], service_name)
    return await _try_luaobfuscator_modes(content, preferred_mode=_luaobfuscator_service_mode(service_name))

async def try_all_deobfuscators(input_path, content, detected_name, timeout_seconds, on_attempt_failure=None):
    if detected_name == "MoonVeil 2.0.22": candidates = [detected_name]
    else:                                   candidates = list(SUPPORTED_DEOBFUSCATORS)
    if detected_name in candidates:
        candidates.remove(detected_name); candidates.insert(0, detected_name)
    failures = []
    deadline = time.monotonic() + timeout_seconds
    for service_name in candidates:
        remaining = deadline - time.monotonic()
        if remaining <= 0: break
        step_timeout        = min(DEOBF_STEP_INTERVAL_SECONDS, remaining)
        attempt_started_at  = time.monotonic()
        failure             = None
        try:
            result = await asyncio.wait_for(try_deobfuscator(input_path, content, service_name), timeout=step_timeout)
            if isinstance(result, str) and result.strip():
                await asyncio.sleep(max(0.0, step_timeout - (time.monotonic() - attempt_started_at)))
                return service_name, result
            failure = f"{service_name}: empty result"
        except asyncio.TimeoutError: failure = f"{service_name}: timed out"
        except Exception as error:   failure = f"{service_name}: {error}"
        await asyncio.sleep(max(0.0, step_timeout - (time.monotonic() - attempt_started_at)))
        failures.append(failure)
        if on_attempt_failure is not None: await on_attempt_failure(len(failures))
    raise RuntimeError("No deobfuscator could decompile this and no output was produced!")

# ============================================================
# PROGRESS + EMBED HELPERS
# ============================================================

def deobfuscation_preview(code, line_limit=5, character_limit=900):
    lines         = str(code or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    preview_lines = lines[:line_limit]
    if not preview_lines: return "```\nNo preview available.\n```"
    preview = "\n".join(preview_lines).replace("```", "`\u200b``")
    if len(preview) > character_limit: preview = preview[:character_limit].rstrip() + "\n..."
    return f"```lua\n{preview}\n```"

def deobfuscation_progress_embed(statuses, error_output=None):
    description = "\n".join(f"**{label}**\n{statuses[index]}" for index, label in enumerate(DEOBF_PROGRESS_LABELS))
    if error_output:
        safe_output  = str(error_output).replace("```", "`\u200b``")
        description += f"\n\n**Checking for errors...**\n```output\n{safe_output}\n```"
    embed = discord.Embed(title="Deobfuscation Progress", description=description, color=None)
    embed.set_footer(text=BOT_FOOTER)
    return embed

async def update_deobfuscation_progress(progress_message, statuses, index, status, error_output=None):
    if progress_message is None: return
    statuses[index] = str(status or "").strip()
    try:
        await progress_message.edit(embed=deobfuscation_progress_embed(statuses, error_output=error_output))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

async def send_deobfuscation_failure(interaction, output, detected=False, file_error="Good", fallback_output=None, fallback_obfuscator=None):
    failure_message = "Deobfuscation failed! Possible because it's unsupported or unknown obfuscator!\n\n**Output:**\nNo deobfuscator could decompile this and no output was produced."
    if isinstance(fallback_output, str) and fallback_output.strip():
        failure_message = "Deobfuscation failed while processing a follow-up loader. The first deobfuscated result is attached below."
    embed = discord.Embed(title="Deobfuscation Failed!", description=failure_message, color=None)
    embed.set_footer(text=BOT_FOOTER)
    if isinstance(fallback_output, str) and fallback_output.strip():
        safe_name = re.sub(r"[^a-z0-9_-]+", "_", str(fallback_obfuscator or "first").lower()).strip("_") or "first"
        await send_script_file(interaction, embed, fallback_output, f"{safe_name}_deobfuscated.lua", mention_user=True)
        return
    await interaction.followup.send(content=interaction.user.mention, embed=embed, allowed_mentions=discord.AllowedMentions(users=True))

async def delete_deobfuscation_progress(progress_message):
    if progress_message is None: return
    try:    await progress_message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException): pass

async def create_deobfuscated_output_file(content):
    fd, file_path = tempfile.mkstemp(prefix="sigmabot-output-", suffix=".lua")
    os.close(fd)
    try:
        await asyncio.to_thread(write_script_text, file_path, content)
        return file_path
    except Exception:
        with suppress(OSError): os.remove(file_path)
        raise

async def run_deobfuscation_step(awaitable, timeout_seconds=DEOBF_STEP_INTERVAL_SECONDS):
    if timeout_seconds <= 0: raise asyncio.TimeoutError("Deobfuscation step timed out.")
    started_at = time.monotonic()
    try:
        result = await asyncio.wait_for(awaitable, timeout=timeout_seconds)
    except asyncio.CancelledError: raise
    except Exception:
        await asyncio.sleep(max(0.0, timeout_seconds - (time.monotonic() - started_at)))
        raise
    await asyncio.sleep(max(0.0, timeout_seconds - (time.monotonic() - started_at)))
    return result

def check_deobfuscated_lua(code):
    source      = str(code or "")
    if not source.strip(): return ["The deobfuscated output is empty."]
    lua_compiler = shutil.which("luac") or shutil.which("luac5.4") or shutil.which("luac5.3")
    if lua_compiler:
        temporary_path = None
        try:
            fd, temporary_path = tempfile.mkstemp(prefix="sigmabot-lua-check-", suffix=".lua")
            os.close(fd)
            write_script_text(temporary_path, source)
            result = subprocess.run([lua_compiler, "-p", temporary_path], capture_output=True, text=True, timeout=3)
            if result.returncode:
                output = (result.stderr or result.stdout or "Lua syntax check failed.")
                return [line.strip() for line in output.splitlines() if line.strip()][:5]
            return []
        except subprocess.TimeoutExpired: return ["Lua syntax check timed out."]
        except OSError: pass
        finally:
            if temporary_path:
                with suppress(OSError): os.remove(temporary_path)
    source = remove_lua_comments(source)
    source = re.sub(r"""(["'])(?:\\.|(?!\1).)*\1""", " ", source, flags=re.DOTALL)
    pairs    = {")": "(", "]": "[", "}": "{"}
    opening  = set(pairs.values())
    stack    = []
    errors   = []
    for character in source:
        if character in opening:   stack.append(character)
        elif character in pairs:
            if not stack or stack[-1] != pairs[character]: errors.append(f"Unexpected closing delimiter: {character}")
            else: stack.pop()
    if stack: errors.append(f"Unclosed delimiter: {stack[-1]}")
    return errors[:5]

def truncate_debug_output(output, line_limit=5):
    lines = str(output or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    return "\n".join(lines[:line_limit]) or "No debug output was returned."

async def show_deobfuscation_failure_progress(progress_message, statuses, index):
    if progress_message is None: return
    await update_deobfuscation_progress(progress_message, statuses, index, "Running a command...")
    await asyncio.sleep(DEOBF_STEP_INTERVAL_SECONDS)
    await update_deobfuscation_progress(progress_message, statuses, index, "Trying another approach...")
    await asyncio.sleep(DEOBF_STEP_INTERVAL_SECONDS)
    await update_deobfuscation_progress(progress_message, statuses, index, "Failed!")

async def send_script_file(interaction, embed, content, filename, mention_user=False):
    import io
    data    = content.encode("utf-8")
    file    = discord.File(fp=io.BytesIO(data), filename=filename)
    mentions = discord.AllowedMentions(users=True) if mention_user else discord.AllowedMentions.none()
    content_mention = interaction.user.mention if mention_user else None
    await interaction.followup.send(content=content_mention, embed=embed, file=file, allowed_mentions=mentions)

# ============================================================
# LARGE FILE WARNING VIEW
# ============================================================

class LargeDeobfWarningView(discord.ui.View):
    def __init__(self, user_id: int, runner):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.runner  = runner

    @discord.ui.button(label="Yes, continue", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your confirmation.", ephemeral=True)
            return
        _large_deobf_timeouts[self.user_id] = time.monotonic() + LARGE_DEOBF_COOLDOWN_SECONDS
        await self.runner(interaction, confirmed=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Cancelled.", ephemeral=True)
        self.stop()

# ============================================================
# MAIN DEOBFUSCATION PIPELINE
# ============================================================

async def run_auto_deobfuscation(interaction, file=None, url=None, confirmed=False):
    input_path   = None
    output_path  = None
    progress_message = None
    progress_statuses = ["Waiting..."] * len(DEOBF_PROGRESS_LABELS)
    progress_stage    = DEOBF_READ_STAGE
    detected_name     = None
    first_detected_name = None
    file_error    = "Good"
    output        = ""
    first_output  = None
    input_opened  = False

    try:
        if not confirmed and large_deobf_remaining(interaction.user.id):
            await send_deobf_timeout_notice(interaction)
            return

        if hasattr(interaction, "response") and not interaction.response.is_done():
            await interaction.response.defer(ephemeral=False)

        progress_message = await interaction.followup.send(embed=deobfuscation_progress_embed(progress_statuses), wait=True)

        size = file.size if file else None
        if size is not None and size >= LARGE_DEOBF_FILE_BYTES and not confirmed:
            embed  = discord.Embed(title="Large File Alert!", description="The file you provided is a large file! This will give you a 3 minute timeout. Continue?", color=0xFFFF00)
            embed.set_footer(text=BOT_FOOTER)
            runner = functools.partial(run_auto_deobfuscation, file=file, url=url)
            await interaction.followup.send(embed=embed, view=LargeDeobfWarningView(interaction.user.id, runner), ephemeral=False)
            await delete_deobfuscation_progress(progress_message)
            progress_message = None
            return

        input_path, content = await run_deobfuscation_step(load_deobfuscation_input(file, url))
        input_opened = True

        await update_deobfuscation_progress(progress_message, progress_statuses, DEOBF_READ_STAGE, "Done!")
        progress_stage = DEOBF_ANALYZE_STAGE

        detected_name = await run_deobfuscation_step(asyncio.to_thread(detect_supported_deobfuscator, content))
        await update_deobfuscation_progress(progress_message, progress_statuses, DEOBF_ANALYZE_STAGE, "Analysis Completed!")

        input_size      = os.path.getsize(input_path)
        timeout_seconds = DEOBF_LARGE_TIMEOUT_SECONDS if input_size >= LARGE_DEOBF_FILE_BYTES else DEOBF_SMALL_TIMEOUT_SECONDS
        progress_stage  = DEOBF_SCRIPT_STAGE

        async def update_decoder_failure_status(failure_count):
            status = "Running a command..." if failure_count == 1 else "Trying another approach..."
            await update_deobfuscation_progress(progress_message, progress_statuses, DEOBF_SCRIPT_STAGE, status)

        deobf_task = asyncio.create_task(
            try_all_deobfuscators(input_path, content, detected_name, min(timeout_seconds, DEOBF_MAX_PROCESS_SECONDS), on_attempt_failure=update_decoder_failure_status)
        )
        detected_name, output = await deobf_task

        await update_deobfuscation_progress(progress_message, progress_statuses, DEOBF_SCRIPT_STAGE, "Deobfuscation Success!")

        if not isinstance(output, str) or not output.strip():
            raise RuntimeError("The deobfuscator returned an empty output.")

        first_detected_name = detected_name
        first_output        = output

        progress_stage = DEOBF_REVIEW_STAGE
        await update_deobfuscation_progress(progress_message, progress_statuses, DEOBF_REVIEW_STAGE, "Done!")

        progress_stage = DEOBF_ERRORS_STAGE
        debug_errors   = await run_deobfuscation_step(asyncio.to_thread(check_deobfuscated_lua, output))

        if debug_errors:
            debug_output = truncate_debug_output("\n".join(debug_errors))
            await update_deobfuscation_progress(progress_message, progress_statuses, DEOBF_ERRORS_STAGE, "Issues found.", error_output=debug_output)
        else:
            await update_deobfuscation_progress(progress_message, progress_statuses, DEOBF_ERRORS_STAGE, "Success! No errors found.")

        progress_stage = DEOBF_CLEAN_STAGE
        await update_deobfuscation_progress(progress_message, progress_statuses, DEOBF_CLEAN_STAGE, "Cleaning the result...")
        output = await run_deobfuscation_step(asyncio.to_thread(clean_lua_without_ai, output))
        if not isinstance(output, str) or not output.strip():
            raise RuntimeError("Cleaning produced an empty output.")
        await update_deobfuscation_progress(progress_message, progress_statuses, DEOBF_CLEAN_STAGE, "Cleaned successfully!")

        progress_stage = DEOBF_REFRESH_STAGE
        output_path    = await run_deobfuscation_step(create_deobfuscated_output_file(output))
        await update_deobfuscation_progress(progress_message, progress_statuses, DEOBF_REFRESH_STAGE, "Output refreshed!")

        # ── Upload to Pastefy ────────────────────────────────────
        if detected_name == "MoonVeil 2.0.22":
            pastefy_link = "Local only — no external API used"
        else:
            pastefy_link = await run_deobfuscation_step(upload_to_pastefy(output, "SigmaBot.deobf"))

        output_bytes = len(output.encode("utf-8"))
        output_size  = round(output_bytes / 1024, 2)

        embed = discord.Embed(
            title="Deobfuscation Success!",
            description="Deobfuscation Success! enjoy lol, See status below",
            color=None,
        )
        embed.add_field(name="Preview (5 Lines):", value=deobfuscation_preview(output), inline=False)
        embed.add_field(name="Deobfuscated", value="true",           inline=True)
        embed.add_field(name="File Size",    value=f"{output_size} KB", inline=True)
        embed.add_field(name="Bytes",        value=str(output_bytes),   inline=True)
        embed.add_field(name="Detected",     value="true",              inline=True)
        embed.add_field(name="Obfuscator",   value=detected_name,       inline=True)
        embed.add_field(name="Uploaded to",  value=pastefy_link,        inline=False)
        embed.set_footer(text=f"{BOT_FOOTER} | Request From {interaction.user}")

        progress_stage = DEOBF_FINALIZE_STAGE
        await update_deobfuscation_progress(progress_message, progress_statuses, DEOBF_FINALIZE_STAGE, "Done!")
        await delete_deobfuscation_progress(progress_message)
        progress_message = None

        # ── Send: file first, then embed (Pastefy link visible inline) ──
        await interaction.followup.send(
            content=interaction.user.mention,
            embed=embed,
            file=discord.File(output_path, filename="SigmaBot.deobf.txt"),
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    except Exception as error:
        output = str(error)
        if not input_opened: file_error = "Failed to open"
        print(f"[deobfuscate] {error}", file=sys.stderr, flush=True)
        if progress_message is not None:
            await show_deobfuscation_failure_progress(progress_message, progress_statuses, progress_stage)
        if confirmed: clear_large_deobf_timeout(interaction.user.id)
        try:
            await send_deobfuscation_failure(interaction, output, detected=bool(detected_name), file_error=file_error, fallback_output=first_output, fallback_obfuscator=first_detected_name)
        finally:
            if confirmed: await send_deobf_timeout_removed_notice(interaction)
    finally:
        if progress_message is not None: await delete_deobfuscation_progress(progress_message)
        if input_path:
            with suppress(OSError): os.remove(input_path)
        if output_path:
            with suppress(OSError): os.remove(output_path)

async def send_deobf_timeout_notice(interaction):
    await interaction.response.send_message("You are on cooldown.", ephemeral=True)

async def send_deobf_timeout_removed_notice(interaction):
    await interaction.followup.send("Cooldown removed.", ephemeral=True)

# ============================================================
# SLASH COMMANDS
# ============================================================

# ── /detect ─────────────────────────────────────────────────
@tree.command(name="detect", description="Detect which obfuscator was used on a Lua script.")
@app_commands.describe(
    file="Upload a .txt or .lua file.",
    url="Paste a raw script URL.",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True)
async def detect_command(
    interaction: discord.Interaction,
    file: discord.Attachment = None,
    url: str = None,
):
    if (file and url) or (not file and not url):
        await interaction.response.send_message("Provide either a file or a URL, not both.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=False)
    try:
        _, content = await load_deobfuscation_input(file, url)
    except Exception as exc:
        await interaction.followup.send(f"Failed to read input: {exc}", ephemeral=True)
        return
    detected = await asyncio.to_thread(detect_supported_deobfuscator, content)
    if detected:
        embed = discord.Embed(title="Obfuscator Detected!", color=0x00FF99)
        embed.add_field(name="Obfuscator", value=detected, inline=False)
        embed.add_field(name="Supported?", value="Yes" if SUPPORTED_DEOBFUSCATORS.get(detected, {}).get("endpoint") is not None or detected.startswith("Luraph") or detected == "MoonVeil 2.0.22" or "LuaObfuscator" in detected else "Partial", inline=True)
        embed.set_footer(text=f"{BOT_FOOTER} | Request From {interaction.user}")
    else:
        embed = discord.Embed(title="Unknown Obfuscator", description="Could not identify the obfuscator. It may be unsupported or not obfuscated.", color=0xFF4444)
        embed.set_footer(text=f"{BOT_FOOTER} | Request From {interaction.user}")
    await interaction.followup.send(embed=embed)


# ── /convert ─────────────────────────────────────────────────
# Fetches content from a URL or file attachment and returns it
# as a downloadable .lua/.txt file. Does NOT deobfuscate —
# pure content extraction for scripts you can't copy-paste.
@tree.command(name="convert", description="Fetch a script from a URL or file and return it as a downloadable file.")
@app_commands.describe(
    file="Upload a file to convert.",
    url="Paste a URL whose raw content you want saved.",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True)
async def convert_command(
    interaction: discord.Interaction,
    file: discord.Attachment = None,
    url: str = None,
):
    if (file and url) or (not file and not url):
        await interaction.response.send_message("Provide either a file or a URL, not both.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=False)
    try:
        if url:
            raw = await fetch_url_content(url)
        else:
            raw = await file.read()
    except Exception as exc:
        await interaction.followup.send(f"Failed to fetch content: {exc}", ephemeral=True)
        return

    # Try decode to text; fallback to latin-1
    try:    text = raw.decode("utf-8")
    except: text = raw.decode("latin-1", errors="replace")

    size_kb  = round(len(raw) / 1024, 2)
    filename = "converted.lua"
    if url:
        parsed = urlparse(url)
        basename = os.path.basename(parsed.path)
        if basename and "." in basename: filename = basename

    import io
    file_obj = discord.File(fp=io.BytesIO(raw), filename=filename)
    embed    = discord.Embed(title="Content Converted!", description=f"Raw content fetched and saved as `{filename}`.", color=None)
    embed.add_field(name="Source",    value=url or file.filename, inline=False)
    embed.add_field(name="File Size", value=f"{size_kb} KB",      inline=True)
    embed.add_field(name="Bytes",     value=str(len(raw)),         inline=True)
    embed.set_footer(text=f"{BOT_FOOTER} | Request From {interaction.user}")
    await interaction.followup.send(
        content=interaction.user.mention,
        embed=embed,
        file=file_obj,
        allowed_mentions=discord.AllowedMentions(users=True),
    )


# ── /deobfuscate ─────────────────────────────────────────────
@tree.command(name="deobfuscate", description="Detect and deobfuscate a supported Lua obfuscator.")
@app_commands.describe(
    file="Upload a .txt or .lua file only!",
    url="Send as url or link.",
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True)
async def deobfuscate(
    interaction: discord.Interaction,
    file: discord.Attachment = None,
    url: str = None,
):
    if (file and url) or (not file and not url):
        await interaction.response.send_message("Please provide either a file or a URL, not both.", ephemeral=True)
        return
    remaining = large_deobf_remaining(interaction.user.id)
    if remaining:
        await interaction.response.send_message(f"Large-file cooldown active. Please wait {remaining} seconds.", ephemeral=True)
        return
    if is_large_deobf_file(file):
        embed  = discord.Embed(title="Large File Alert!", description="The file you provided is a large file! This will give you a 3 minute timeout. Continue?", color=0xFFFF00)
        embed.set_footer(text=BOT_FOOTER)
        runner = functools.partial(run_auto_deobfuscation, file=file, url=url)
        await interaction.response.send_message(embed=embed, view=LargeDeobfWarningView(interaction.user.id, runner), ephemeral=False)
        return
    await run_auto_deobfuscation(interaction, file=file, url=url)


# ── /deobfuscate_luraph ──────────────────────────────────────
@tree.command(name="deobfuscate_luraph", description="Deobfuscate a Luraph-protected Lua script.")
@app_commands.describe(
    file="Upload a .txt or .lua file.",
    url="Paste a raw script URL.",
    version="Force a specific Luraph version (optional).",
)
@app_commands.choices(version=[
    app_commands.Choice(name="Auto-detect",    value="auto"),
    app_commands.Choice(name="Luraph 10.7",    value="Luraph 10.7"),
    app_commands.Choice(name="Luraph 14.7",    value="Luraph 14.7"),
    app_commands.Choice(name="Luraph 14.8",    value="Luraph 14.8"),
    app_commands.Choice(name="Luraph 15.0",    value="Luraph 15.0"),
    app_commands.Choice(name="Luraph Generic", value="Luraph (Generic)"),
])
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True)
async def deobfuscate_luraph(
    interaction: discord.Interaction,
    file: discord.Attachment = None,
    url: str = None,
    version: str = "auto",
):
    if (file and url) or (not file and not url):
        await interaction.response.send_message("Provide either a file or a URL — not both, not neither.", ephemeral=True)
        return
    remaining = large_deobf_remaining(interaction.user.id)
    if remaining:
        await interaction.response.send_message(f"Large-file cooldown active. Wait {remaining}s.", ephemeral=True)
        return

    if version != "auto":
        _module   = sys.modules[__name__]
        _original = detect_supported_deobfuscator
        def _forced(content): return version
        setattr(_module, "detect_supported_deobfuscator", _forced)
        try:    await run_auto_deobfuscation(interaction, file=file, url=url)
        finally: setattr(_module, "detect_supported_deobfuscator", _original)
    else:
        await run_auto_deobfuscation(interaction, file=file, url=url)

# ============================================================
# BOT EVENTS
# ============================================================

@client.event
async def on_ready():
    await client.tree.sync()
    print(f"Logged in as {client.user} | Slash commands synced.")

# ============================================================
# RUN  (paste your token in .env as DISCORD_TOKEN=...)
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")
client.run(TOKEN)
