#!/usr/bin/env python3
"""buddy-picker: A CLI for rolling and selecting Claude Code buddy profiles."""

import argparse
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

# ============ Buddy 生成逻辑 ============
SALT = "friend-2026-401"
RARITIES = ("common", "uncommon", "rare", "epic", "legendary")
RARITY_WEIGHTS: dict[str, int] = {
    "common": 60,
    "uncommon": 25,
    "rare": 10,
    "epic": 4,
    "legendary": 1,
}
SPECIES = (
    "duck", "goose", "blob", "cat", "dragon", "octopus", "owl", "penguin",
    "turtle", "snail", "ghost", "axolotl", "capybara", "cactus", "robot",
    "rabbit", "mushroom", "chonk",
)
EYES = ("·", "✦", "×", "◉", "@", "°")

Rarity = Literal["common", "uncommon", "rare", "epic", "legendary"]
Lang = Literal["zh", "en"]

RARITY_LEVEL: dict[str, int] = {
    "common": 1, "uncommon": 2, "rare": 3, "epic": 4, "legendary": 5,
}
LEVEL_TO_RARITY: dict[int, str] = {v: k for k, v in RARITY_LEVEL.items()}

RARITY_EMOJI: dict[str, str] = {
    "common": "⚪", "uncommon": "🟢", "rare": "🔵", "epic": "🟣", "legendary": "🟡",
}


# ============ Wyhash (matches Bun.hash / Zig std.hash.Wyhash) ============
_MASK64 = 0xFFFFFFFFFFFFFFFF
_WY_S0 = 0xa0761d6478bd642f
_WY_S1 = 0xe7037ed1a0b428db
_WY_S2 = 0x8ebc6af09c88c6e3
_WY_S3 = 0x589965cc75374cc3


def _wymix(a: int, b: int) -> int:
    r = (a & _MASK64) * (b & _MASK64)
    return ((r >> 64) ^ r) & _MASK64


def _wyr8(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 8], "little")


def _wyr4(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


def _wyr3(data: bytes, offset: int, k: int) -> int:
    return (data[offset] << 16) | (data[offset + (k >> 1)] << 8) | data[offset + k - 1]


def _wyhash(key: bytes, seed: int = 0) -> int:
    n = len(key)
    seed = (seed ^ _wymix(seed ^ _WY_S0, _WY_S1)) & _MASK64
    p = 0
    i = n

    if n <= 16:
        if n >= 4:
            a = (_wyr4(key, 0) << 32) | _wyr4(key, (n >> 3) << 2)
            b = (_wyr4(key, n - 4) << 32) | _wyr4(key, n - 4 - ((n >> 3) << 2))
        elif n > 0:
            a = _wyr3(key, 0, n)
            b = 0
        else:
            a = b = 0
    else:
        if i > 48:
            see1, see2 = seed, seed
            while i > 48:
                seed = _wymix(_wyr8(key, p) ^ _WY_S1, _wyr8(key, p + 8) ^ seed)
                see1 = _wymix(_wyr8(key, p + 16) ^ _WY_S2, _wyr8(key, p + 24) ^ see1)
                see2 = _wymix(_wyr8(key, p + 32) ^ _WY_S3, _wyr8(key, p + 40) ^ see2)
                p += 48
                i -= 48
            seed = (seed ^ see1 ^ see2) & _MASK64
        while i > 16:
            seed = _wymix(_wyr8(key, p) ^ _WY_S1, _wyr8(key, p + 8) ^ seed)
            p += 16
            i -= 16
        a = _wyr8(key, p + i - 16)
        b = _wyr8(key, p + i - 8)

    a = (a ^ _WY_S1) & _MASK64
    b = (b ^ seed) & _MASK64
    r = a * b
    a = r & _MASK64
    b = (r >> 64) & _MASK64
    return _wymix((a ^ _WY_S0 ^ n) & _MASK64, (b ^ _WY_S1) & _MASK64)


# ============ FNV-1a hash (matches Node.js/non-Bun environments) ============
def _hash_string_fnv1a(s: str) -> int:
    """FNV-1a hash used by Claude Code when running in Node.js (non-Bun) environments."""
    h = 2166136261
    for char in s:
        h ^= ord(char)
        h = _imul(h, 16777619)
    return h & 0xFFFFFFFF


def _hash_string_wyhash(s: str) -> int:
    """Wyhash used by Claude Code when running in Bun environments."""
    return _wyhash(s.encode("utf-8"), 0) & 0xFFFFFFFF


# Global hash engine selection
_HASH_ENGINE = "node"  # Default to Node.js (FNV-1a) since most Claude CLI installations use Node


def set_hash_engine(engine: str) -> None:
    """Set the hash engine to match the target Claude Code environment.

    Args:
        engine: Either "node" (FNV-1a, default) or "bun" (wyhash)
    """
    global _HASH_ENGINE
    if engine not in ("node", "bun"):
        raise ValueError(f"Invalid engine: {engine}. Must be 'node' or 'bun'")
    _HASH_ENGINE = engine


def hash_string(s: str) -> int:
    """Hash a string the same way Claude Code does, depending on the runtime environment.

    By default, uses FNV-1a (Node.js) since most Claude CLI installations run on Node.
    Use set_hash_engine("bun") to switch to wyhash for Bun environments.
    """
    if _HASH_ENGINE == "bun":
        return _hash_string_wyhash(s)
    return _hash_string_fnv1a(s)


# ============ Mulberry32 PRNG (matches JS implementation) ============
def _to_uint32(x: int) -> int:
    return x & 0xFFFFFFFF


def _imul(a: int, b: int) -> int:
    """Emulate JavaScript Math.imul: 32-bit signed integer multiplication."""
    a = a & 0xFFFFFFFF
    b = b & 0xFFFFFFFF
    result = (a * b) & 0xFFFFFFFF
    if result >= 0x80000000:
        result -= 0x100000000
    return result


def _to_int32(x: int) -> int:
    x = x & 0xFFFFFFFF
    return x - 0x100000000 if x >= 0x80000000 else x


def mulberry32(seed: int) -> Callable[[], float]:
    a = _to_uint32(seed)

    def next_val() -> float:
        nonlocal a
        a = _to_int32(a)
        a = _to_int32(a + 0x6D2B79F5)
        t = _imul(a ^ (_to_uint32(a) >> 15), _to_int32(1 | a))
        t = _to_int32(t + _imul(t ^ (_to_uint32(t) >> 7), _to_int32(61 | t))) ^ t
        return _to_uint32(t ^ (_to_uint32(t) >> 14)) / 4294967296

    return next_val


def _pick(rng: Callable[[], float], arr: tuple) -> str:
    import math
    return arr[math.floor(rng() * len(arr))]


def _roll_rarity(rng: Callable[[], float]) -> str:
    total = sum(RARITY_WEIGHTS.values())
    roll = rng() * total
    for rarity in RARITIES:
        roll -= RARITY_WEIGHTS[rarity]
        if roll < 0:
            return rarity
    return "common"


@dataclass
class BuddyRoll:
    user_id: str
    rarity: str
    species: str
    eye: str
    shiny: bool


def simulate_roll(user_id: str) -> BuddyRoll:
    key = user_id + SALT
    rng = mulberry32(hash_string(key))
    rarity = _roll_rarity(rng)
    species = _pick(rng, SPECIES)
    eye = _pick(rng, EYES)
    shiny = rng() < 0.01
    return BuddyRoll(user_id=user_id, rarity=rarity, species=species, eye=eye, shiny=shiny)


def compare_buddy_rolls(a: BuddyRoll, b: BuddyRoll) -> int:
    rarity_diff = RARITY_LEVEL[b.rarity] - RARITY_LEVEL[a.rarity]
    if rarity_diff != 0:
        return rarity_diff
    if a.shiny == b.shiny:
        return 0
    return -1 if a.shiny else 1


def should_proceed_with_oauth_write(is_oauth_user: bool, has_explicit_confirmation: bool) -> bool:
    return not is_oauth_user or has_explicit_confirmation


# ============ 语言检测 ============
def detect_language(env: dict[str, str] | None = None, locale: str | list[str] | None = None) -> str:
    if env is None:
        env = dict(os.environ)

    override = env.get("BUDDY_GACHA_LANG", "").lower()
    if override in ("zh", "en"):
        return override

    candidates: list[str] = []
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = env.get(key)
        if val:
            candidates.append(val)
    if isinstance(locale, list):
        candidates.extend(locale)
    elif isinstance(locale, str):
        candidates.append(locale)

    loc = candidates[0].lower() if candidates else "en"
    return "zh" if loc.startswith("zh") else "en"


# ============ i18n 消息 ============
SPECIES_LIST = ", ".join(SPECIES)

MESSAGES: dict[str, dict] = {
    "zh": {
        "help_title": "🎰 Buddy 抽卡系统",
        "invalid_selection": "❌ 无效选择",
        "eye_label": "眼睛",
        "read_config_error": lambda p: (
            "❌ 无法读取配置文件:", p, "   请确保 Claude Code 已安装并至少启动过一次",
        ),
        "oauth_warning": (
            "\n⚠️  警告：你当前使用 OAuth 登录",
            "   Buddy 由 accountUuid 决定，修改 userID 不会生效",
            "   要重置 Buddy，请先退出登录 (/logout) 后使用 API KEY 模式\n",
        ),
        "config_updated": lambda p, uid: (
            "\n✅ 配置已更新！",
            f"📝 配置文件: {p}",
            f"🆔 新 userID: {uid}",
            "\n⚠️  重要：必须完全重启 Claude Code 才能生效",
            "   原因：内存中有缓存 (rollCache)，只有进程重启才能清空",
            "\n🔄 请退出当前会话并重新启动 Claude Code\n",
        ),
        "confirm_write": "是否仍要继续写入？(y/N) ",
        "cancelled": "已取消",
        "cancelled_no_change": "已取消，配置未修改",
        "found_matches": lambda c: f"\n找到 {c} 个符合条件的 Buddy，展示前 10 个：",
        "choose_top_match": lambda c: f"选择一个 (1-{c})，或输入 0 取消全部: ",
        "selected_buddy": lambda line: f"\n你选择了: {line}",
        "invalid_rarity_level": lambda lv: (
            f"❌ 无效的稀有度等级: {lv}",
            "   可用等级: 1=common, 2=uncommon, 3=rare, 4=epic, 5=legendary",
        ),
        "auto_roll_title": "🎰 自动抽卡模式",
        "target_rarity": lambda r: f"🎯 目标: {RARITY_EMOJI[r]} {r.upper()}",
        "shiny_requirement": "✨ 额外要求: 必须是闪光",
        "species_requirement": lambda sp: f"🐾 额外要求: 种族必须是 {sp}",
        "theoretical_rate": lambda rate, shiny: f"📊 理论概率: {rate}%{' × 1% (闪光)' if shiny else ''}",
        "max_attempts": lambda c: f"🔄 最大尝试次数: {c:,}\n",
        "rolling": "开始抽卡...\n",
        "progress": lambda att, el, sp, st: (
            f"\r🔄 已尝试 {att:,} 次 | 用时 {el}s | 速度 {sp}/s"
            f" | 传说={st['legendary']} 史诗={st['epic']} 稀有={st['rare']}"
        ),
        "distribution_summary": lambda mc, fh, el, st, att: (
            f"\n\n🎉 找到了 {mc} 个！第 1 次命中在第 {fh:,} 次尝试（耗时 {el}s）\n",
            "📊 统计分布:",
            f"   💎 传说: {st['legendary']} ({st['legendary']/att*100:.2f}%)",
            f"   🔮 史诗: {st['epic']} ({st['epic']/att*100:.2f}%)",
            f"   💠 稀有: {st['rare']} ({st['rare']/att*100:.2f}%)",
            f"   🟢 罕见: {st['uncommon']} ({st['uncommon']/att*100:.2f}%)",
            f"   ⚪ 普通: {st['common']} ({st['common']/att*100:.2f}%)",
        ),
        "max_attempts_reached": lambda c: (
            f"\n\n💔 达到最大尝试次数 ({c:,})，未找到满足条件的 Buddy",
            "💡 提示: 增加 --max-attempts 参数或降低要求",
        ),
        "interactive_title": "🎰 Buddy 抽卡系统\n",
        "interactive_warning": "⚠️  注意：请确保 Claude Code 未运行，否则可能出现配置冲突\n",
        "generating_candidates": lambda c: f"正在生成 {c} 个随机 Buddy...\n",
        "interactive_stats": lambda st: (
            f"\n💎 传说: {st['legendary']} | 🔮 史诗: {st['epic']}"
            f" | 💠 稀有: {st['rare']} | ✨ 闪光: {st['shiny']}"
        ),
        "choose_buddy": lambda c: f"\n选择一个 Buddy (1-{c})，或输入 0 取消: ",
        "invalid_rare_arg": (
            "❌ --rare 必须是 1-5 之间的数字",
            "   1=common, 2=uncommon, 3=rare, 4=epic, 5=legendary",
        ),
        "invalid_count_arg": "❌ --count 必须是正整数",
        "option_descriptions": {
            "rare": "自动刷到指定稀有度 (1-5: common/uncommon/rare/epic/legendary)",
            "shiny": "要求必须是闪光 (配合 --rare 使用)",
            "species": "要求特定种族 (duck/cat/dragon等)",
            "count": "交互模式下生成的 Buddy 数量",
            "max_attempts": "自动模式最大尝试次数",
            "engine": "目标 Claude 运行环境 (node/bun)，影响生成的 Buddy 结果 (默认: node)",
            "help": "显示帮助信息",
        },
    },
    "en": {
        "help_title": "🎰 Buddy Gacha",
        "invalid_selection": "❌ Invalid selection",
        "eye_label": "eyes",
        "read_config_error": lambda p: (
            "❌ Failed to read config file:", p,
            "   Make sure Claude Code is installed and has been launched at least once",
        ),
        "oauth_warning": (
            "\n⚠️  Warning: you are currently signed in with OAuth",
            "   Buddy selection is derived from accountUuid, so changing userID may not work",
            "   To reset your buddy, log out first (/logout) and use API key mode\n",
        ),
        "config_updated": lambda p, uid: (
            "\n✅ Config updated",
            f"📝 Config file: {p}",
            f"🆔 New userID: {uid}",
            "\n⚠️  Important: you must fully restart Claude Code for this to take effect",
            "   Reason: the process keeps a cached rollCache in memory until restart",
            "\n🔄 Exit the current session and restart Claude Code\n",
        ),
        "confirm_write": "Do you still want to continue writing? (y/N) ",
        "cancelled": "Cancelled",
        "cancelled_no_change": "Cancelled, config not changed",
        "found_matches": lambda c: f"\nFound {c} matching buddies. Showing the top 10:",
        "choose_top_match": lambda c: f"Choose one (1-{c}), or enter 0 to cancel: ",
        "selected_buddy": lambda line: f"\nYou selected: {line}",
        "invalid_rarity_level": lambda lv: (
            f"❌ Invalid rarity level: {lv}",
            "   Available levels: 1=common, 2=uncommon, 3=rare, 4=epic, 5=legendary",
        ),
        "auto_roll_title": "🎰 Auto-roll mode",
        "target_rarity": lambda r: f"🎯 Target: {RARITY_EMOJI[r]} {r.upper()}",
        "shiny_requirement": "✨ Extra requirement: must be shiny",
        "species_requirement": lambda sp: f"🐾 Extra requirement: species must be {sp}",
        "theoretical_rate": lambda rate, shiny: f"📊 Theoretical rate: {rate}%{' × 1% (shiny)' if shiny else ''}",
        "max_attempts": lambda c: f"🔄 Maximum attempts: {c:,}\n",
        "rolling": "Rolling...\n",
        "progress": lambda att, el, sp, st: (
            f"\r🔄 Attempts {att:,} | Elapsed {el}s | Speed {sp}/s"
            f" | legendary={st['legendary']} epic={st['epic']} rare={st['rare']}"
        ),
        "distribution_summary": lambda mc, fh, el, st, att: (
            f"\n\n🎉 Found {mc}! First hit arrived at attempt {fh:,} ({el}s)\n",
            "📊 Distribution:",
            f"   💎 legendary: {st['legendary']} ({st['legendary']/att*100:.2f}%)",
            f"   🔮 epic: {st['epic']} ({st['epic']/att*100:.2f}%)",
            f"   💠 rare: {st['rare']} ({st['rare']/att*100:.2f}%)",
            f"   🟢 uncommon: {st['uncommon']} ({st['uncommon']/att*100:.2f}%)",
            f"   ⚪ common: {st['common']} ({st['common']/att*100:.2f}%)",
        ),
        "max_attempts_reached": lambda c: (
            f"\n\n💔 Reached the maximum attempts ({c:,}) without finding a matching buddy",
            "💡 Tip: increase --max-attempts or lower the requirements",
        ),
        "interactive_title": "🎰 Buddy Gacha\n",
        "interactive_warning": "⚠️  Make sure Claude Code is not running, or config writes may conflict\n",
        "generating_candidates": lambda c: f"Generating {c} random buddies...\n",
        "interactive_stats": lambda st: (
            f"\n💎 legendary: {st['legendary']} | 🔮 epic: {st['epic']}"
            f" | 💠 rare: {st['rare']} | ✨ shiny: {st['shiny']}"
        ),
        "choose_buddy": lambda c: f"\nChoose a buddy (1-{c}), or enter 0 to cancel: ",
        "invalid_rare_arg": (
            "❌ --rare must be a number between 1 and 5",
            "   1=common, 2=uncommon, 3=rare, 4=epic, 5=legendary",
        ),
        "invalid_count_arg": "❌ --count must be a positive integer",
        "option_descriptions": {
            "rare": "Auto-roll until the target rarity (1-5: common/uncommon/rare/epic/legendary)",
            "shiny": "Require shiny (used with --rare)",
            "species": "Require a specific species (duck/cat/dragon, etc.)",
            "count": "Number of buddies in interactive mode",
            "max_attempts": "Maximum attempts in auto-roll mode",
            "engine": "Target Claude runtime environment (node/bun), affects resulting buddy (default: node)",
            "help": "Show help information",
        },
    },
}


def get_messages(lang: str) -> dict:
    return MESSAGES[lang]


# ============ 显示工具 ============
def format_buddy(roll: BuddyRoll, index: int | None = None, lang: str = "en") -> str:
    msg = get_messages(lang)
    emoji = RARITY_EMOJI[roll.rarity]
    shiny_mark = "✨" if roll.shiny else "  "
    prefix = f"{index}. " if index else ""
    return (
        f"{prefix}{emoji} {shiny_mark} {roll.rarity.upper():<10}"
        f" | {roll.species:<10} | {roll.eye} {msg['eye_label']}"
    )


# ============ 配置文件操作 ============
def get_config_path() -> str:
    return str(Path.home() / ".claude.json")


def read_config() -> dict:
    config_path = get_config_path()
    try:
        return json.loads(Path(config_path).read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        msg = get_messages(detect_language())
        for line in msg["read_config_error"](config_path):
            print(line, file=sys.stderr)
        sys.exit(1)


def has_oauth_account(config: dict) -> bool:
    return bool(config.get("oauthAccount", {}).get("accountUuid"))


def check_oauth_warning() -> bool:
    config = read_config()
    if has_oauth_account(config):
        msg = get_messages(detect_language())
        for line in msg["oauth_warning"]:
            print(line)
        return True
    return False


def write_config(user_id: str, has_explicit_oauth_confirmation: bool = False) -> None:
    config_path = get_config_path()
    config = read_config()
    lang = detect_language()
    msg = get_messages(lang)

    def perform_write():
        updated = {**config, "userID": user_id}
        updated.pop("companion", None)
        updated.pop("companionMuted", None)
        Path(config_path).write_text(json.dumps(updated, indent=2), "utf-8")
        for line in msg["config_updated"](config_path, user_id):
            print(line)

    if should_proceed_with_oauth_write(has_oauth_account(config), has_explicit_oauth_confirmation):
        perform_write()
        return

    for line in msg["oauth_warning"]:
        print(line)
    answer = input(msg["confirm_write"])
    if answer.lower() != "y":
        print(msg["cancelled"])
        sys.exit(0)
    perform_write()


# ============ 批量展示并选择 ============
def _sort_key(roll: BuddyRoll) -> tuple:
    return (-RARITY_LEVEL[roll.rarity], not roll.shiny)


def select_from_matches(matches: list[BuddyRoll]) -> bool:
    lang = detect_language()
    msg = get_messages(lang)
    sorted_matches = sorted(matches, key=_sort_key)
    top10 = sorted_matches[:10]

    print(msg["found_matches"](len(matches)))
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    for i, roll in enumerate(top10, 1):
        print(format_buddy(roll, i, lang))
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    try:
        answer = input(msg["choose_top_match"](len(top10)))
    except (EOFError, KeyboardInterrupt):
        print(f"\n{msg['cancelled_no_change']}")
        return False

    try:
        choice = int(answer)
    except ValueError:
        print(msg["cancelled_no_change"])
        return False

    if choice == 0 or choice < 0 or choice > len(top10):
        print(msg["cancelled_no_change"])
        return False

    selected = top10[choice - 1]
    print(msg["selected_buddy"](format_buddy(selected, choice, lang)))
    write_config(selected.user_id)
    return True


# ============ 自动刷稀有度模式 ============
def auto_roll_mode(
    target_level: int,
    *,
    shiny: bool = False,
    species: str | None = None,
    max_attempts: int = 10000,
) -> None:
    lang = detect_language()
    msg = get_messages(lang)
    target_rarity = LEVEL_TO_RARITY.get(target_level)
    if not target_rarity:
        for line in msg["invalid_rarity_level"](target_level):
            print(line, file=sys.stderr)
        sys.exit(1)

    print(msg["auto_roll_title"])
    print(msg["target_rarity"](target_rarity))
    if shiny:
        print(msg["shiny_requirement"])
    if species:
        print(msg["species_requirement"](species))
    print(msg["theoretical_rate"](RARITY_WEIGHTS[target_rarity], shiny))
    print(msg["max_attempts"](max_attempts))

    check_oauth_warning()
    print(msg["rolling"])

    attempts = 0
    last_report_time = time.time()
    start_time = time.time()
    matches: list[tuple[BuddyRoll, int]] = []

    stats: dict[str, int] = {r: 0 for r in RARITIES}

    while attempts < max_attempts:
        attempts += 1
        user_id = secrets.token_hex(32)
        roll = simulate_roll(user_id)
        stats[roll.rarity] += 1

        now = time.time()
        if attempts % 1000 == 0 or now - last_report_time > 2:
            elapsed = f"{now - start_time:.1f}"
            speed = f"{attempts / (now - start_time):.0f}"
            sys.stdout.write(msg["progress"](attempts, elapsed, speed, stats))
            sys.stdout.flush()
            last_report_time = now

        rarity_match = RARITY_LEVEL[roll.rarity] >= target_level
        shiny_match = not shiny or roll.shiny
        species_match = not species or roll.species == species

        if rarity_match and shiny_match and species_match:
            matches.append((roll, attempts))

            if len(matches) >= 10 or attempts >= max_attempts:
                elapsed = f"{time.time() - start_time:.1f}"
                for line in msg["distribution_summary"](
                    len(matches), matches[0][1], elapsed, stats, attempts,
                ):
                    print(line)

                chosen = select_from_matches([m[0] for m in matches])
                if chosen:
                    return
                print(f"\n{msg['cancelled_no_change']}")
                return

    for line in msg["max_attempts_reached"](max_attempts):
        print(line)
    sys.exit(1)


# ============ 交互式选择模式 ============
def interactive_mode(count: int) -> None:
    lang = detect_language()
    msg = get_messages(lang)
    print(msg["interactive_title"])
    print(msg["interactive_warning"])
    print(msg["generating_candidates"](count))

    rolls: list[BuddyRoll] = []
    for _ in range(count):
        user_id = secrets.token_hex(32)
        rolls.append(simulate_roll(user_id))

    rolls.sort(key=_sort_key)

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    for i, roll in enumerate(rolls, 1):
        print(format_buddy(roll, i, lang))
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    stats = {
        "legendary": sum(1 for r in rolls if r.rarity == "legendary"),
        "epic": sum(1 for r in rolls if r.rarity == "epic"),
        "rare": sum(1 for r in rolls if r.rarity == "rare"),
        "shiny": sum(1 for r in rolls if r.shiny),
    }
    print(msg["interactive_stats"](stats))

    try:
        answer = input(msg["choose_buddy"](count))
    except (EOFError, KeyboardInterrupt):
        print(f"\n{msg['cancelled_no_change']}")
        sys.exit(0)

    try:
        choice = int(answer)
    except ValueError:
        print(msg["invalid_selection"])
        sys.exit(1)

    if choice == 0:
        print(msg["cancelled_no_change"])
        sys.exit(0)

    if choice < 1 or choice > count:
        print(msg["invalid_selection"])
        sys.exit(1)

    selected = rolls[choice - 1]
    print(msg["selected_buddy"](format_buddy(selected, choice, lang)))
    write_config(selected.user_id)


# ============ 帮助文本 ============
def format_help_text(lang: str | None = None) -> str:
    if lang is None:
        lang = detect_language()

    if lang == "zh":
        return f"""
🎰 Buddy 抽卡系统

用法:
  buddy-picker [选项]

模式:
  交互模式 (默认):
    显示 N 个随机 Buddy 供你选择

  自动刷稀有度模式:
    使用 --rare 参数，自动刷到指定稀有度为止

选项:
  -r, --rare <1-5>        自动刷到指定稀有度
                          1=common, 2=uncommon, 3=rare, 4=epic, 5=legendary
  -s, --shiny             要求必须是闪光 (配合 --rare 使用)
  --species <name>        要求特定种族 (配合 --rare 使用)
                          可选: {SPECIES_LIST}
  -c, --count <N>         交互模式下生成的数量 (默认: 10)
  --max-attempts <N>      自动模式最大尝试次数 (默认: 10000)
  --engine <node|bun>     指定 Claude 运行环境 (默认: node)
  -h, --help              显示此帮助信息

示例:
  # 交互模式：生成 10 个 Buddy 供选择
  buddy-picker

  # 交互模式：生成 50 个 Buddy
  buddy-picker --count 50

  # 自动刷传说稀有度
  buddy-picker --rare 5

  # 自动刷史诗稀有度，且必须是闪光
  buddy-picker --rare 4 --shiny

  # 自动刷传说稀有度，且必须是 dragon
  buddy-picker --rare 5 --species dragon

  # 自动刷传说闪光 dragon（欧皇模式）
  buddy-picker --rare 5 --shiny --species dragon --max-attempts 100000

稀有度对应关系:
  1 = ⚪ common     (60% 概率)
  2 = 🟢 uncommon   (25% 概率)
  3 = 🔵 rare       (10% 概率)
  4 = 🟣 epic       (4% 概率)
  5 = 🟡 legendary  (1% 概率)
  ✨ shiny         (1% 概率，独立判定)

注意事项:
  • 修改配置后必须完全重启 Claude Code
  • OAuth 登录用户需要先 /logout 才能生效
  • 使用自动模式时请耐心等待，传说+闪光期望需要 10,000 次尝试
"""
    else:
        return f"""
🎰 Buddy Gacha

Usage:
  buddy-picker [options]

Modes:
  Interactive mode (default):
    Show N random buddies and let you choose one

  Auto-roll mode:
    Use --rare to keep rolling until the target rarity appears

Options:
  -r, --rare <1-5>        Auto-roll until the target rarity
                          1=common, 2=uncommon, 3=rare, 4=epic, 5=legendary
  -s, --shiny             Require shiny (used with --rare)
  --species <name>        Require a specific species (used with --rare)
                          Available: {SPECIES_LIST}
  -c, --count <N>         Number of buddies in interactive mode (default: 10)
  --max-attempts <N>      Maximum attempts in auto-roll mode (default: 10000)
  --engine <node|bun>     Target Claude runtime environment (default: node)
  -h, --help              Show this help message

Examples:
  # Interactive mode: generate 10 buddies to choose from
  buddy-picker

  # Interactive mode: generate 50 buddies
  buddy-picker --count 50

  # Auto-roll for legendary rarity
  buddy-picker --rare 5

  # Auto-roll for epic rarity and require shiny
  buddy-picker --rare 4 --shiny

  # Auto-roll for legendary rarity and require dragon
  buddy-picker --rare 5 --species dragon

  # Auto-roll for shiny legendary dragon
  buddy-picker --rare 5 --shiny --species dragon --max-attempts 100000

Rarity table:
  1 = ⚪ common     (60%)
  2 = 🟢 uncommon   (25%)
  3 = 🔵 rare       (10%)
  4 = 🟣 epic       (4%)
  5 = 🟡 legendary  (1%)
  ✨ shiny         (1%, independent roll)

Notes:
  • You must fully restart Claude Code after writing a new userID
  • OAuth users usually need to /logout before the change can take effect
  • Auto-roll can take time; shiny legendary has a 10,000-roll expected rate
"""


# ============ 命令行入口 ============
def main() -> None:
    lang = detect_language()
    msg = get_messages(lang)

    parser = argparse.ArgumentParser(
        description=msg["help_title"],
        add_help=False,
    )
    parser.add_argument("-r", "--rare", type=int, metavar="1-5",
                        help=msg["option_descriptions"]["rare"])
    parser.add_argument("-s", "--shiny", action="store_true",
                        help=msg["option_descriptions"]["shiny"])
    parser.add_argument("--species", type=str,
                        help=msg["option_descriptions"]["species"])
    parser.add_argument("-c", "--count", type=int, default=10,
                        help=msg["option_descriptions"]["count"])
    parser.add_argument("--max-attempts", type=int, default=10000,
                        help=msg["option_descriptions"]["max_attempts"])
    parser.add_argument("--engine", type=str, choices=["node", "bun"], default="node",
                        help=msg["option_descriptions"]["engine"])
    parser.add_argument("-h", "--help", action="store_true",
                        help=msg["option_descriptions"]["help"])

    args = parser.parse_args()

    if args.engine:
        set_hash_engine(args.engine)

    if args.help:
        print(format_help_text(lang))
        sys.exit(0)

    if args.rare is not None:
        if args.rare < 1 or args.rare > 5:
            for line in msg["invalid_rare_arg"]:
                print(line, file=sys.stderr)
            sys.exit(1)
        auto_roll_mode(
            args.rare,
            shiny=args.shiny,
            species=args.species,
            max_attempts=args.max_attempts,
        )
    else:
        if args.count < 1:
            print(msg["invalid_count_arg"], file=sys.stderr)
            sys.exit(1)
        interactive_mode(args.count)


if __name__ == "__main__":
    main()
