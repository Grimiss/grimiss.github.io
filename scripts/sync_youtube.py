#!/usr/bin/env python3
"""Add verified public ambient releases to the existing static Listening Room.

Standard library only. No API credentials are written into the page or Git.
All discovery/validation completes before a single atomic page replacement.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SyncError(Exception):
    pass


ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
RELEASE = re.compile(r"\b(?:ambient|soundscapes?|FRS|SRS|tape[ -]*loops?)\b", re.I)
PROMO = re.compile(r"(?:#shorts?\b|\b(?:shorts?|teaser|trailer|tutorial|walkthrough|preview|promo|announcement)\b)", re.I)
SHELF = re.compile(r'(<div\b[^>]*\bid="shelf"[^>]*>)(.*?)(</div>)', re.S)
BUTTON = re.compile(r'<button\b[^>]*\bdata-index="\d+"[^>]*>.*?</button>', re.S)


def timestamp(value):
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError()
        return date.astimezone(timezone.utc)
    except (ValueError, AttributeError, TypeError):
        raise SyncError("Invalid publication timestamp; collection left unchanged") from None


def duration_seconds(value):
    m = re.fullmatch(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not m or not any(m.groups()):
        raise SyncError("Missing or invalid video duration")
    d, h, minute, sec = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + minute * 60 + sec


def clock(seconds):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def spine_duration(value):
    parts = [int(v) for v in value.split(":")]
    return f"{parts[0]}h {parts[1]}m" if len(parts) == 3 else f"{parts[0]} min"


def short_name(title):
    # Use the first editorial phrase rather than inventing a new track title.
    name = re.split(r"\s*\|\s*", title, maxsplit=1)[0].strip()
    name = re.sub(r"^[^\w]+", "", name).strip()
    return name or title


class YouTube:
    def __init__(self, key, opener=urlopen, sleeper=time.sleep):
        if not key:
            raise SyncError("Configure the YOUTUBE_API_KEY repository secret first")
        self.key, self.opener, self.sleeper = key, opener, sleeper

    def get(self, resource, **params):
        url = "https://www.googleapis.com/youtube/v3/" + resource + "?" + urlencode({**params, "key": self.key})
        for attempt in range(3):
            try:
                with self.opener(Request(url, headers={"Accept": "application/json"}), timeout=30) as response:
                    data = json.load(response)
                if not isinstance(data, dict) or not isinstance(data.get("items"), list) or "error" in data:
                    raise SyncError("Incomplete YouTube response; collection left unchanged")
                return data
            except HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise SyncError(f"YouTube {resource} request failed (HTTP {e.code}); no collection changes") from None
            except (URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise SyncError("YouTube connection failed; no collection changes") from None
            except (ValueError, UnicodeError):
                raise SyncError("Invalid YouTube response; no collection changes") from None
            self.sleeper(2 ** attempt)
        raise SyncError("YouTube request failed")

    def discover(self, config):
        result = self.get("channels", part="id,contentDetails", forHandle=config["channel_handle"])
        if len(result["items"]) != 1:
            raise SyncError("Channel could not be identified uniquely")
        channel = result["items"][0]
        if channel.get("id") != config["channel_id"]:
            raise SyncError("Channel identity changed; review configuration before importing")
        try:
            uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        except (KeyError, TypeError):
            raise SyncError("YouTube omitted the channel uploads playlist") from None
        ids, seen_tokens, token = [], set(), None
        while True:
            params = dict(part="contentDetails", playlistId=uploads, maxResults=50)
            if token:
                params["pageToken"] = token
            page = self.get("playlistItems", **params)
            for item in page["items"]:
                vid = item.get("contentDetails", {}).get("videoId")
                if not ID.fullmatch(vid or ""):
                    raise SyncError("Uploads response contained an invalid video ID")
                if vid not in ids:
                    ids.append(vid)
            token = page.get("nextPageToken")
            if not token:
                break
            if token in seen_tokens or not isinstance(token, str):
                raise SyncError("Invalid YouTube pagination; no collection changes")
            seen_tokens.add(token)
        if not ids:
            raise SyncError("YouTube returned an empty uploads list; no collection changes")
        videos = []
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            response = self.get("videos", part="snippet,contentDetails,status,liveStreamingDetails", id=",".join(batch))
            returned = [v.get("id") for v in response["items"]]
            if len(returned) != len(set(returned)) or any(v not in batch for v in returned):
                raise SyncError("Invalid video-detail response")
            # API omits unavailable/private videos. Never add those or remove old tapes.
            videos.extend(response["items"])
        return videos


def eligible(video, config, now):
    vid = video.get("id", "")
    if not ID.fullmatch(vid) or vid in config.get("excluded_video_ids", []):
        return False
    snippet, status = video.get("snippet", {}), video.get("status", {})
    if snippet.get("channelId") != config["channel_id"]:
        return False
    if status.get("privacyStatus") != "public" or status.get("uploadStatus") != "processed" or status.get("embeddable") is not True:
        return False
    details = video.get("contentDetails", {})
    if details.get("regionRestriction") or details.get("contentRating", {}).get("ytRating") == "ytAgeRestricted":
        return False
    if snippet.get("liveBroadcastContent") != "none":
        return False
    if "liveStreamingDetails" in video:
        ended = video["liveStreamingDetails"].get("actualEndTime")
        if not ended or timestamp(ended) > now:
            return False  # Upcoming premieres/current live streams aren't releases yet.
    if timestamp(snippet.get("publishedAt")) > now:
        return False
    title = snippet.get("title", "")
    tags = " ".join(snippet.get("tags", []))
    if not isinstance(title, str) or not title.strip():
        raise SyncError("Video title missing")
    if PROMO.search(title) or re.search(r"(?:^|\s)#?shorts?(?:\s|$)", tags, re.I):
        return False
    if duration_seconds(video.get("contentDetails", {}).get("duration")) <= 180:
        return False  # Conservative guard: Shorts can be up to three minutes.
    return bool(RELEASE.search(title))  # Not a duration-only classification.


def track_from_video(video):
    s = video["snippet"]
    thumbs = s.get("thumbnails", {})
    thumbnail = next((thumbs[k].get("url") for k in ("high", "standard", "medium", "default") if isinstance(thumbs.get(k), dict) and thumbs[k].get("url")), None)
    if not thumbnail or not thumbnail.startswith("https://i.ytimg.com/"):
        raise SyncError("Missing or unexpected thumbnail URL; no collection changes")
    return dict(id=video["id"], title=s["title"], duration=clock(duration_seconds(video["contentDetails"]["duration"])), name=short_name(s["title"]), thumbnail=thumbnail, publishedAt=s["publishedAt"])


def read_page(page):
    declaration = re.search(r"\bconst tracks\s*=", page)
    if not declaration:
        raise SyncError("Existing track declaration not found; review page changes")
    start = declaration.end()
    while page[start].isspace():
        start += 1
    try:
        tracks, length = json.JSONDecoder().raw_decode(page[start:])
    except ValueError:
        raise SyncError("Cannot read existing tracks; no collection changes") from None
    if not isinstance(tracks, list) or not tracks:
        raise SyncError("Existing collection is empty or invalid")
    ids = [t.get("id") for t in tracks]
    if any(not ID.fullmatch(i or "") for i in ids) or len(ids) != len(set(ids)):
        raise SyncError("Existing collection has invalid or duplicate IDs")
    shelf = SHELF.search(page)
    if not shelf:
        raise SyncError("Cassette shelf not found")
    buttons = BUTTON.findall(shelf[2])
    if len(buttons) != len(tracks) or BUTTON.sub("", shelf[2]).strip():
        raise SyncError("Shelf structure changed; preserve manual edits and review")
    for index, button in enumerate(buttons):
        if not re.search(rf'\bdata-index="{index}"', button):
            raise SyncError("Cassette and player indices disagree")
    return tracks, (start, start + length), shelf, dict(zip(ids, buttons))


def new_button(track):
    return ('<button class="case" data-index="0" aria-pressed="false" title="' + escape(track["title"], quote=True) + '"><span class="case-index">01</span><span class="case-name">' + escape(track["name"]) + '</span><span class="case-duration">' + escape(spine_duration(track["duration"])) + '</span></button>')


def render_update(page, videos, config, now):
    tracks, _, _, originals = read_page(page)
    ids = set(originals)
    additions = {}
    dates = {t["id"]: timestamp(t["publishedAt"]) for t in tracks if t.get("publishedAt")}
    for v in videos:
        if v.get("id") in ids:
            # Read dates for insertion order, never refresh existing editorial metadata.
            if v.get("snippet", {}).get("publishedAt"):
                dates[v["id"]] = timestamp(v["snippet"]["publishedAt"])
            continue
        if eligible(v, config, now):
            additions[v["id"]] = track_from_video(v)
            dates[v["id"]] = timestamp(v["snippet"]["publishedAt"])
    if not additions:
        return page, []  # Byte-for-byte no-op: no formatting churn or empty commits.
    ordered = sorted(tracks + list(additions.values()), key=lambda t: dates.get(t["id"], datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    buttons = []
    for i, t in enumerate(ordered):
        button = originals[t["id"]] if t["id"] in originals else new_button(t)
        button = re.sub(r'\bdata-index="\d+"', f'data-index="{i}"', button)
        button = re.sub(r'\baria-pressed="(?:true|false)"', f'aria-pressed="{str(i == 0).lower()}"', button)
        button = re.sub(r'(<span class="case-index">)\d+(</span>)', lambda m: m[1] + f"{i + 1:02}" + m[2], button)
        buttons.append(button)
    _, (start, end), _, _ = read_page(page)
    serialized = json.dumps(ordered, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = page[:start] + serialized + page[end:]
    shelf = SHELF.search(page)
    page = page[:shelf.start(2)] + "".join(buttons) + page[shelf.end(2):]
    page, count = re.subn(r"\b\d+ RECORDINGS\b", f"{len(ordered)} RECORDINGS", page)
    if count != 1:
        raise SyncError("Recording count label changed; review page manually")
    page = re.sub(r"Scroll the tape shelf for all \d+ recordings", f"Scroll the tape shelf for all {len(ordered)} recordings", page)
    first = ordered[0]
    for element, text in (("selected-title", first["name"]), ("duration", first["duration"]), ("selected-note", first.get("note", first["title"])), ("feedback", first["name"] + " is selected. Ready when you are.")):
        pattern = rf'(<[^>]+\bid="{element}"[^>]*>).*?(</[^>]+>)'
        page, count = re.subn(pattern, lambda m: m[1] + escape(text) + m[2], page, count=1, flags=re.S)
        if count != 1:
            raise SyncError("Player markup changed; preserve manual edits and review")
    page, count = re.subn(r'(<a\b[^>]*id="watch"[^>]*href=")[^"]*(")', lambda m: m[1] + "https://www.youtube.com/watch?v=" + first["id"] + m[2], page)
    if count != 1:
        raise SyncError("Watch link markup changed")
    final, _, _, _ = read_page(page)
    if any(t not in final for t in tracks):
        raise SyncError("Existing metadata protection failed")
    return page, sorted(additions)


def atomic_write(path, content, original):
    if path.read_text(encoding="utf-8") != original:
        raise SyncError("Page changed during sync; retry with the latest manual edits")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as f:
            temporary = Path(f.name)
            f.write(content)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def synchronize(root, client, dry_run=False, now=None):
    config = json.loads((root / "scripts/youtube-sync-config.json").read_text())
    page_path = root / "listening-room.html"
    original = page_path.read_text(encoding="utf-8")
    read_page(original)  # Validate the hand-edited collection before making requests.
    videos = client.discover(config)  # Paginate everything before considering a write.
    updated, added = render_update(original, videos, config, now or datetime.now(timezone.utc))
    if added and not dry_run:
        atomic_write(page_path, updated, original)
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        added = synchronize(args.root, YouTube(os.environ.get("YOUTUBE_API_KEY")), args.dry_run)
        print(f"{'Would add' if args.dry_run else 'Added'} {len(added)} eligible release(s)." + (" Existing collection unchanged." if not added else ""))
        for vid in added:
            print("Video ID:", vid)
    except (SyncError, ValueError, KeyError, TypeError, OSError) as e:
        # No request URLs, credentials or remote response bodies in errors.
        print("Sync failed:", str(e) if isinstance(e, SyncError) else "Invalid local/API data; no collection changes", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
