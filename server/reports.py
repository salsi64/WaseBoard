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
MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")

#: Textes de mise en forme, par langue (« fr » par défaut, comme tout le reste du serveur ; « en » pour les personnes dont Discord est en anglais).
TEXTS = {
    "fr": {
        "deleted_sound": "(son supprimé)", "unknown_member": "Membre inconnu", "this_server": "ce serveur",
        "days_7": "7 derniers jours", "days_365": "12 derniers mois", "days_n": "{n} derniers jours",
        "top_title": "🏆 Top de {guild} — {period}", "top_none": "Aucun son joué sur cette période.", "top_total": "**{n}** son(s) joué(s).",
        "top_sounds": "🔊 Sons les plus joués", "top_users": "👤 Membres les plus actifs",
        "bilan_title": "🎉 Bilan {year} — {guild}", "bilan_none": "Aucun son joué en {year} sur ce serveur.",
        "bilan_me": "🙋 Vous", "bilan_me_text": "**{plays}** son(s) joué(s) — **#{rank}** sur {members} membre(s), {share} % du total.",
        "bilan_peaks": "📅 Temps forts", "bilan_day": "Jour le plus chargé : **{date}** ({count} lectures)",
        "bilan_hour": "Heure de pointe : **{start} h – {end} h**",
        "bilan_total": "**{total}** son(s) joué(s), **{distinct}** son(s) différent(s), sur **{days}** jour(s) d'activité.",
    },
    "en": {
        "deleted_sound": "(deleted sound)", "unknown_member": "Unknown member", "this_server": "this server",
        "days_7": "last 7 days", "days_365": "last 12 months", "days_n": "last {n} days",
        "top_title": "🏆 Top of {guild} — {period}", "top_none": "No sound played in this period.", "top_total": "**{n}** sound(s) played.",
        "top_sounds": "🔊 Most played sounds", "top_users": "👤 Most active members",
        "bilan_title": "🎉 {year} in review — {guild}", "bilan_none": "No sound played in {year} on this server.",
        "bilan_me": "🙋 You", "bilan_me_text": "**{plays}** sound(s) played — **#{rank}** out of {members} member(s), {share}% of the total.",
        "bilan_peaks": "📅 Highlights", "bilan_day": "Busiest day: **{date}** ({count} plays)",
        "bilan_hour": "Peak hour: **{start} – {end}**",
        "bilan_total": "**{total}** sound(s) played, **{distinct}** different sound(s), over **{days}** active day(s).",
    },
}


def texts(language: str) -> dict:
    return TEXTS.get(language, TEXTS["fr"])
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
        "name": current.get("name") or recorded.get("sound_name"),  # None : son supprimé (le texte dépend de la langue, voir _sound_lines)
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


def _sound_lines(rows: list, language: str = "fr") -> str:
    deleted = texts(language)["deleted_sound"]
    return "\n".join(
        f"{_rank(i)} {row['emoji'] + ' ' if row.get('emoji') else ''}**{escape_text(row['name'] or deleted)}** — {row['count']}"
        for i, row in enumerate(rows))


def _user_lines(rows: list, language: str = "fr") -> str:
    unknown = texts(language)["unknown_member"]
    return "\n".join(f"{_rank(i)} **{escape_text(row.get('username') or unknown)}** — {row['count']}" for i, row in enumerate(rows))


def render_top(stats: dict, guild_name: str, days: int, language: str = "fr") -> dict:
    """Mise en forme de /top à partir de server.aggregate_play_stats : {"title", "description", "fields": [(nom, valeur)]}."""
    t = texts(language)
    period = t["days_7"] if days == 7 else t["days_365"] if days == 365 else t["days_n"].format(n=days)
    title = t["top_title"].format(guild=escape_text(guild_name, 60), period=period)
    if not stats["total_plays"]:
        return {"title": title, "description": t["top_none"], "fields": []}
    return {
        "title": title,
        "description": t["top_total"].format(n=stats["total_plays"]),
        "fields": [(t["top_sounds"], _sound_lines(stats["top_sounds"], language)),
                   (t["top_users"], _user_lines(stats["top_users"][:5], language))],
    }


def _french_date(iso_date: str) -> str:
    moment = datetime.fromisoformat(iso_date)
    return f"{moment.day}{'er' if moment.day == 1 else ''} {MONTHS[moment.month - 1]}"


def _english_date(iso_date: str) -> str:
    moment = datetime.fromisoformat(iso_date)
    return f"{MONTHS_EN[moment.month - 1]} {moment.day}"


def _english_hour(hour: int) -> str:
    return f"{hour % 12 or 12} {'am' if hour % 24 < 12 else 'pm'}"


def render_bilan(report: dict, guild_name: str, language: str = "fr") -> dict:
    """Mise en forme de /bilan à partir de year_report : {"title", "description", "fields": [(nom, valeur)]}."""
    t = texts(language)
    title = t["bilan_title"].format(year=report["year"], guild=escape_text(guild_name, 60))
    if not report["total_plays"]:
        return {"title": title, "description": t["bilan_none"].format(year=report["year"]), "fields": []}

    fields = []
    me = report["me"]
    if me:
        favourites = _sound_lines(me["top_sounds"], language)
        text = t["bilan_me_text"].format(plays=me["plays"], rank=me["rank"], members=me["members"], share=me["share_percent"])
        fields.append((t["bilan_me"], f"{text}\n\n{favourites}"))
    fields.append((t["top_sounds"], _sound_lines(report["top_sounds"], language)))
    fields.append((t["top_users"], _user_lines(report["top_users"][:5], language)))
    day, hour = report["busiest_day"], report["busiest_hour"]
    if language == "en":
        date_text, start, end = _english_date(day["date"]), _english_hour(hour), _english_hour(hour + 1)
    else:
        date_text, start, end = _french_date(day["date"]), hour, (hour + 1) % 24
    fields.append((t["bilan_peaks"], t["bilan_day"].format(date=date_text, count=day["count"]) + "\n" + t["bilan_hour"].format(start=start, end=end)))
    return {
        "title": title,
        "description": t["bilan_total"].format(total=report["total_plays"], distinct=report["distinct_sounds"], days=report["active_days"]),
        "fields": fields,
    }
