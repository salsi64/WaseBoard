"""Statistiques des commandes Discord /top et /bilan : calculs et mise en forme, sans Discord ni disque (le journal
de lectures `stats.jsonl` est lu par l'appelant et passé ici sous forme d'itérable d'enregistrements).

Chaque enregistrement : {"ts", "user_id", "username", "sound_id", "sound_name", "emoji", "guild_id", ...} — voir
server.record_play_stat. Les dates sont celles de l'heure locale du serveur, comme dans le panneau d'administration.
"""

import re
from collections import Counter
from datetime import datetime
from typing import Iterable, Optional

MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre")
MEDALS = ("🥇", "🥈", "🥉")

#: Un champ d'embed Discord accepte 1024 caractères : on tronque les noms pour que dix lignes y tiennent toujours.
MAX_NAME = 40

_MARKDOWN = re.compile(r"([\\*_~`|>#\[\]()])")


def escape_text(value: Optional[str], limit: int = MAX_NAME) -> str:
    """Nom affichable : tronqué, sans mise en forme Markdown ni mention (@everyone, <@id>) venant d'un nom de son ou de membre."""
    text = (value or "").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    text = _MARKDOWN.sub(r"\\\1", text)
    return text.replace("@", "@​").replace("<", "<​")


def _rank(index: int) -> str:
    return MEDALS[index] if index < len(MEDALS) else f"`{index + 1}.`"


def _sound_row(sound_id: str, count: int, recorded: dict, catalog_names: dict) -> dict:
    current = catalog_names.get(sound_id) or {}
    return {
        "sound_id": sound_id,
        "name": current.get("name") or recorded.get("sound_name") or "(son supprimé)",
        "emoji": current.get("emoji") or recorded.get("emoji"),
        "count": count,
    }


def year_report(records: Iterable[dict], guild_id: str, year: int, user_id: Optional[str], catalog_names: dict) -> dict:
    """Bilan d'une année civile pour un serveur : totaux, classements, temps forts, et — si `user_id` est donné — la part de
    cette personne (son rang parmi les membres, ses sons préférés)."""
    start = datetime(year, 1, 1).timestamp()
    end = datetime(year + 1, 1, 1).timestamp()

    sounds: Counter = Counter()
    sound_meta: dict = {}
    users: Counter = Counter()
    user_names: dict = {}
    days: Counter = Counter()
    hours: Counter = Counter()
    mine: Counter = Counter()
    total = 0

    for r in records:
        ts = r.get("ts", 0)
        if r.get("guild_id") != guild_id or not start <= ts < end:
            continue
        total += 1
        sid, uid = r.get("sound_id"), r.get("user_id")
        sounds[sid] += 1
        sound_meta[sid] = r
        users[uid] += 1
        if r.get("username"):
            user_names[uid] = r["username"]
        moment = datetime.fromtimestamp(ts)
        days[moment.date().isoformat()] += 1
        hours[moment.hour] += 1
        if user_id is not None and uid == user_id:
            mine[sid] += 1

    report: dict = {
        "year": year,
        "total_plays": total,
        "distinct_sounds": len(sounds),
        "active_days": len(days),
        "busiest_day": None,
        "busiest_hour": None,
        "top_sounds": [_sound_row(sid, c, sound_meta[sid], catalog_names) for sid, c in sounds.most_common(5)],
        "top_users": [{"user_id": uid, "username": user_names.get(uid), "count": c} for uid, c in users.most_common(5)],
        "me": None,
    }
    if days:
        day, count = days.most_common(1)[0]
        report["busiest_day"] = {"date": day, "count": count}
        report["busiest_hour"] = hours.most_common(1)[0][0]
    if user_id is not None and mine:
        my_total = sum(mine.values())
        report["me"] = {
            "plays": my_total,
            "rank": 1 + sum(1 for c in users.values() if c > my_total),
            "members": len(users),
            "share_percent": round(100 * my_total / total),
            "top_sounds": [_sound_row(sid, c, sound_meta[sid], catalog_names) for sid, c in mine.most_common(3)],
        }
    return report


def _sound_lines(rows: list) -> str:
    return "\n".join(
        f"{_rank(i)} {row['emoji'] + ' ' if row.get('emoji') else ''}**{escape_text(row['name'])}** — {row['count']}"
        for i, row in enumerate(rows))


def _user_lines(rows: list) -> str:
    return "\n".join(f"{_rank(i)} **{escape_text(row.get('username') or 'Membre inconnu')}** — {row['count']}" for i, row in enumerate(rows))


def render_top(stats: dict, guild_name: str, days: int) -> dict:
    """Mise en forme de /top à partir de server.aggregate_play_stats : {"title", "description", "fields": [(nom, valeur)]}."""
    period = "7 derniers jours" if days == 7 else "12 derniers mois" if days == 365 else f"{days} derniers jours"
    title = f"🏆 Top de {escape_text(guild_name, 60)} — {period}"
    if not stats["total_plays"]:
        return {"title": title, "description": "Aucun son joué sur cette période.", "fields": []}
    return {
        "title": title,
        "description": f"**{stats['total_plays']}** son(s) joué(s).",
        "fields": [("🔊 Sons les plus joués", _sound_lines(stats["top_sounds"])),
                   ("👤 Membres les plus actifs", _user_lines(stats["top_users"][:5]))],
    }


def _french_date(iso_date: str) -> str:
    moment = datetime.fromisoformat(iso_date)
    return f"{moment.day}{'er' if moment.day == 1 else ''} {MONTHS[moment.month - 1]}"


def render_bilan(report: dict, guild_name: str) -> dict:
    """Mise en forme de /bilan à partir de year_report : {"title", "description", "fields": [(nom, valeur)]}."""
    title = f"🎉 Bilan {report['year']} — {escape_text(guild_name, 60)}"
    if not report["total_plays"]:
        return {"title": title, "description": f"Aucun son joué en {report['year']} sur ce serveur.", "fields": []}

    fields = []
    me = report["me"]
    if me:
        favourites = _sound_lines(me["top_sounds"])
        fields.append(("🙋 Vous", f"**{me['plays']}** son(s) joué(s) — **#{me['rank']}** sur {me['members']} membre(s), "
                                  f"{me['share_percent']} % du total.\n\n{favourites}"))
    fields.append(("🔊 Sons les plus joués", _sound_lines(report["top_sounds"])))
    fields.append(("👤 Membres les plus actifs", _user_lines(report["top_users"][:5])))
    day, hour = report["busiest_day"], report["busiest_hour"]
    fields.append(("📅 Temps forts", f"Jour le plus chargé : **{_french_date(day['date'])}** ({day['count']} lectures)\n"
                                    f"Heure de pointe : **{hour} h – {(hour + 1) % 24} h**"))
    return {
        "title": title,
        "description": (f"**{report['total_plays']}** son(s) joué(s), **{report['distinct_sounds']}** son(s) différent(s), "
                        f"sur **{report['active_days']}** jour(s) d'activité."),
        "fields": fields,
    }
