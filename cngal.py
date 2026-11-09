"""Read matched Chinese Galgame profiles and linked impressions from CnGal."""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from html import unescape

import httpx


_API = "https://api.cngal.org/api"
_SITE = "https://www.cngal.org"
_MAX_BYTES = 512 * 1024


def _key(value):
    if not isinstance(value, str):
        return ""
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum())


def _text(value, limit):
    if not isinstance(value, str):
        return ""
    text = unescape(value[:3000])
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]*>|!\[[^\]]*\]\([^\n]*?\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^\n]*?\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text, flags=re.I)
    return " ".join(text.split())[:limit]


async def _get(client, path, params):
    async with client.stream("GET", _API + path, params=params, follow_redirects=False,
                             headers={"Accept": "application/json"}) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > _MAX_BYTES:
                return {}
            body.extend(chunk)
    payload = json.loads(body)
    return payload if isinstance(payload, dict) else {}


def _entries(result):
    page = result.get("pagedResultDto")
    rows = page.get("data") if isinstance(page, dict) else None
    if not isinstance(rows, list):
        return []
    entries = []
    for row in rows[:10]:
        item = row.get("entry") if isinstance(row, dict) else None
        if isinstance(item, dict) and item.get("type") == "Game" and type(item.get("id")) is int and item["id"] > 0:
            entries.append(item)
    return entries


def _impressions(item, names):
    rows = item.get("articleRelevances")
    if not isinstance(rows, list):
        return []
    reviews = []
    keys = tuple(key for name in names if len(key := _key(name)) >= 2)
    for row in rows[:20]:
        if not isinstance(row, dict) or row.get("type") != "Tought":
            continue
        article_id = row.get("id")
        title = _text(row.get("displayName") or row.get("name"), 110)
        summary = _text(row.get("briefIntroduction"), 180)
        if not title or not summary or type(article_id) is not int or article_id <= 0:
            continue
        title_key, summary_key = _key(title), _key(summary)
        title_match = any(key in title_key for key in keys)
        if title_match or any(key in summary_key for key in keys):
            reviews.append((not title_match, {"title": title, "url": f"{_SITE}/articles/index/{article_id}", "summary": summary}))
    return [review for _, review in sorted(reviews, key=lambda row: row[0])[:2]]


async def _lookup(client, title):
    key = _key(title)
    result = await _get(client, "/home/Search", {"Types": "Game", "Text": title, "Page": 1})
    entries = _entries(result)
    matches = {row["id"] for row in entries if _key(row.get("name")) == key}
    if len(matches) > 1 or not entries:
        return None
    # Search can find aliases, which are only exposed by the detail endpoint.
    entry_id = matches.pop() if matches else entries[0]["id"]
    item = await _get(client, f"/entries/GetEntryView/{entry_id}", {"renderMarkdown": "false"})
    if item.get("id") != entry_id or item.get("type") != "Game" or item.get("isHidden") is True:
        return None
    aliases = item.get("anotherName")
    names = (item.get("name"), *(re.split(r"[\n;；、]+", aliases) if isinstance(aliases, str) else ()))
    if key not in map(_key, names):
        return None
    name = _text(item.get("name"), 110)
    description = _text(item.get("briefIntroduction"), 430)
    reviews = _impressions(item, names)
    if name and (description or reviews):
        return {"title": name, "names": [value for value in names if _key(value)],
                "url": f"{_SITE}/entries/index/{entry_id}", "description": description, "reviews": reviews}
    return None


async def fetch_cngal_details(client, titles, *, timeout: float) -> list[dict]:
    """Resolve up to two exact titles or aliases with at most two GETs per title."""
    async def find(title):
        try:
            async with asyncio.timeout(timeout):
                return await _lookup(client, title)
        except (TimeoutError, httpx.HTTPError, ValueError):
            return None

    candidates = list(dict.fromkeys(title.strip() for title in titles if isinstance(title, str)
                                   and len(title) <= 100 and len(_key(title)) >= 2))[:2]
    results = await asyncio.gather(*(find(title) for title in candidates))
    return list({row["url"]: row for row in results if row}.values())
