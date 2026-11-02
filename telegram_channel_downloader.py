import asyncio
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import webbrowser
import zipfile
from collections import defaultdict
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from telethon import TelegramClient
from telegram_auth import connect_preserving_sessions, CODE_RESEND
from link_downloader import extract_links, download_provider, telegram_post_location, label_url, link_hash, LinkDownloadError, provider_type
from link_import import urls_from_text, write_txt
from google_drive_api import google_sign_in, has_token, download_google_api, google_link_id, validate_desktop_client_json
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

try:
    import libtorrent as lt
except ImportError:
    lt = None

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "config.json"
STATE_FILE = APP_DIR / "download_state.json"
MAX_CHANNELS = 100
MAX_PARALLEL_CHANNELS = 100

DEFAULT_CONFIG = {
    "api_id": "",
    "api_hash": "",
    "channel": "",
    "channels": [],
    "parallel_channels": 5,
    "output_dir": str(APP_DIR / "downloads"),
    "concurrency": 2,
    "zip_split_mb": 1900,
    "include_photos": True,
    "include_video": True,
    "include_audio": True,
    "include_documents": True,
    "include_voice": True,
    "include_stickers": False,
    "smart_history": True,
    "auto_download_links": True,
    "link_max_mb": 0,  # 0 = unlimited (use with care)
    "import_txt": str(APP_DIR / "content.txt"),
    "import_txt_in_scan": True,
    "google_oauth_client": "",  # User must choose a real Google Cloud OAuth Desktop client JSON
}

def load_json(path, default):
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            out = dict(default)
            out.update(data)
            return out
    except Exception:
        pass
    return dict(default)

def save_json(path, data):
    """Atomically persist state so a crash cannot easily destroy download history."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def completed_record(value):
    """Return True for both legacy path strings and v4 smart-history records."""
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return value.get("status") == "complete" or bool(value.get("completed"))
    return False

def record_path(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("last_path") or value.get("path") or ""
    return ""


def safe_name(s):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(s))
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:160] or "telegram_channel"

def parse_channels(value):
    """Deduplicate Telegram channel identifiers while preserving queue order.

    Accept @handles, public t.me URLs, private invite URLs (when joined),
    numeric peer IDs, and one-per-line / comma-separated TXT exports.
    """
    if isinstance(value, (list, tuple)):
        raw = []
        for item in value:
            raw.extend(re.split(r"[\r\n,;]+", str(item)))
    else:
        raw = re.split(r"[\r\n,;]+", str(value or ""))
    ordered = []
    seen = set()
    for line in raw:
        entry = line.strip().strip('"\'')
        if not entry or entry.startswith('#'):
            continue
        if ' ' in entry and not entry.startswith(('http://', 'https://')):
            # Avoid treating explanatory text as a Telegram username.
            continue
        if entry.startswith('t.me/'):
            entry = 'https://' + entry
        entry = entry.rstrip('/')
        norm = entry.casefold()
        if norm not in seen:
            seen.add(norm)
            ordered.append(entry)
    return ordered


def validate_channel_batch(channels, parallel_channels):
    """Validate both user input and persisted settings before starting a batch."""
    channels = parse_channels(channels)
    if not channels:
        raise ValueError('Add at least one Telegram channel.')
    if len(channels) > MAX_CHANNELS:
        raise ValueError(f'Maximum {MAX_CHANNELS} channels per batch; you entered {len(channels)}. '
                         'Remove extra entries in Manage Channels or split the list.')
    try:
        parallel = int(parallel_channels)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Channels active at once must be a whole number from 1 to {MAX_PARALLEL_CHANNELS}.') from exc
    if not 1 <= parallel <= MAX_PARALLEL_CHANNELS:
        raise ValueError(f'Channels active at once must be between 1 and {MAX_PARALLEL_CHANNELS}.')
    return channels, parallel


class TelegramArchiver:
    def __init__(self, gui):
        self.gui = gui
        self.cancelled = False
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.state = load_json(STATE_FILE, {"downloaded": {}})
        self.client = None
        self.transfer = {}
        self.transfer_lock = threading.Lock()
        self.link_discovered = 0
        self.link_success = 0
        self.link_failed = 0

    def log(self, msg):
        self.gui.events.put(("log", msg))

    def progress(self, done, total, text="", channel_ref=None):
        if channel_ref is None:
            self.gui.events.put(("progress", done, total, text))
        else:
            self.gui.events.put(("channel_progress", channel_ref, done, total, text))

    def stop(self):
        self.cancelled = True
        self.pause_event.set()

    def toggle_pause(self, paused):
        if paused:
            self.pause_event.clear()
        else:
            self.pause_event.set()

    def _allowed(self, msg, cfg):
        if not msg.media:
            return False
        if getattr(msg, "photo", None):
            return cfg["include_photos"]
        if getattr(msg, "video", None) or getattr(msg, "video_note", None):
            return cfg["include_video"]
        if getattr(msg, "voice", None):
            return cfg["include_voice"]
        if getattr(msg, "audio", None):
            return cfg["include_audio"]
        if getattr(msg, "sticker", None):
            return cfg["include_stickers"]
        if getattr(msg, "document", None):
            return cfg["include_documents"]
        return False

    def _ext(self, msg):
        f = getattr(msg, "file", None)
        ext = getattr(f, "ext", None)
        if ext:
            return ext
        if getattr(msg, "photo", None):
            return ".jpg"
        return ".bin"

    def _filename(self, msg):
        f = getattr(msg, "file", None)
        name = getattr(f, "name", None)
        if name:
            return safe_name(name)
        stamp = msg.date.strftime("%Y%m%d_%H%M%S") if msg.date else "unknown"
        return f"{msg.id}_{stamp}{self._ext(msg)}"

    async def run(self, cfg, phone, import_only=False):
        """Use ONE authorized Telethon session across parallel channel jobs.

        A shared client avoids competing SQLite session locks. Each job writes
        its own channel-specific history, files, manifests and ZIPs.
        """
        self.cancelled = False
        self.pause_event.set()
        out_root = Path(cfg["output_dir"]).expanduser().resolve()
        out_root.mkdir(parents=True, exist_ok=True)
        source_file = Path(cfg.get('import_txt') or APP_DIR / 'content.txt').expanduser()
        if not source_file.is_absolute():
            source_file = APP_DIR / source_file
        self.batch_transfer_semaphore = asyncio.Semaphore(max(1, int(cfg['concurrency'])))

        if import_only:
            if not source_file.is_file():
                raise FileNotFoundError(f'TXT input missing: {source_file}')
            self.client = None
            self.log('TXT-only import: Telegram login is not required.')
            result = await self._process_channel(cfg, None, out_root, source_file,
                                                 '__imported_links__', import_only=True)
            self.gui.events.put(('batch_done', [result], str(out_root)))
            return

        channels, parallel = validate_channel_batch(
            cfg.get('channels') or cfg.get('channel', ''), cfg.get('parallel_channels', 5)
        )
        for ref in channels:
            self.gui.events.put(('channel_update', ref, 'Queued', 0, 0, 'Waiting for available channel slot'))
        active_channels = min(parallel, len(channels))
        self.log(f'Starting batch: {len(channels)} channel(s), '
                 f'{active_channels} active at once, '
                 f'{int(cfg["concurrency"])} total concurrent file transfers.')
        if active_channels > 10:
            self.log('NOTICE: High channel concurrency can trigger Telegram FloodWait '
                     'and use substantial memory. Reduce active channels if rate limited.')
        self.client = await connect_preserving_sessions(
            app_dir=APP_DIR, api_id=int(cfg['api_id']), api_hash=cfg['api_hash'].strip(),
            phone=phone, gui=self.gui, log=self.log, qr_mode=bool(cfg.get('qr_login', False)),
        )
        # Up to 100 distinct channels can be processed concurrently by user choice.
        # File transfers remain throttled by one shared semaphore across the batch.
        # The default of 5 is recommended to reduce flood waits / RAM usage.
        channel_limit = asyncio.Semaphore(active_channels)
        folders_in_use = {}
        entity_keys_in_use = set()
        results = []

        async def channel_job(ref):
            async with channel_limit:
                if self.cancelled:
                    self.gui.events.put(('channel_update', ref, 'Stopped', 0, 0, 'Cancelled before starting'))
                    return {'channel': ref, 'status': 'stopped'}
                try:
                    self.gui.events.put(('channel_update', ref, 'Connecting', 0, 0, 'Resolving channel'))
                    entity = await self.client.get_entity(int(ref) if ref.lstrip('-').isdigit() else ref)
                    title = safe_name(getattr(entity, 'title', None) or getattr(entity, 'username', None) or str(ref))
                    entity_id = str(getattr(entity, 'id', ref))
                    if entity_id in entity_keys_in_use:
                        self.log(f'[{ref}] Skipped duplicate channel: entity ID {entity_id} already queued.')
                        self.gui.events.put(('channel_update', ref, 'Skipped', 0, 0,
                                             'Duplicate channel alias; already in this batch'))
                        return {'channel': ref, 'status': 'skipped', 'error': 'Duplicate channel ID'}
                    entity_keys_in_use.add(entity_id)
                    # Keep original folder names except when titles collide.
                    folder_name = title
                    if title.casefold() in folders_in_use and folders_in_use[title.casefold()] != entity_id:
                        folder_name = f'{title}_{entity_id}'
                    folders_in_use[title.casefold()] = entity_id
                    return await self._process_channel(cfg, entity, out_root, source_file, ref,
                                                       folder_name=folder_name)
                except Exception as exc:
                    if self.cancelled:
                        phase = 'Stopped'
                    else:
                        phase = 'Failed'
                    self.gui.events.put(('channel_update', ref, phase, 0, 0, str(exc)))
                    self.log(f'[{ref}] {phase}: {type(exc).__name__}: {exc}')
                    return {'channel': ref, 'status': phase.lower(), 'error': str(exc)}

        results = await asyncio.gather(*(channel_job(ref) for ref in channels))
        # An imported file is a global source, not N independent sets of URLs.
        # A separate Imported_Links folder avoids redownloading it per channel.
        if len(channels) > 1 and cfg.get('import_txt_in_scan', True) and source_file.is_file() and not self.cancelled:
            self.log('Running TXT import once for the entire multi-channel batch...')
            try:
                results.append(await self._process_channel(cfg, None, out_root, source_file,
                                                           '__imported_links__', import_only=True))
            except Exception as exc:
                self.log(f'TXT import failed: {exc}')
                self.gui.events.put(('channel_update', '__imported_links__', 'Failed', 0, 0, str(exc)))
                results.append({'channel': '__imported_links__', 'status': 'failed', 'error': str(exc)})
        report = out_root / 'CHANNEL_BATCH_REPORT.txt'
        report.write_text('Telegram Channel Downloader v3.4 - Batch Report\n'
                          + f'Channels requested: {len(channels)}\n\n'
                          + '\n'.join(f'{r.get("channel", "?")}: {r.get("status", "unknown")}'
                                        + (f' | {r["error"]}' if r.get('error') else '')
                                        + (f' | {r["folder"]}' if r.get('folder') else '')
                                        for r in results) + '\n', encoding='utf-8')
        self.gui.events.put(('batch_done', results, str(out_root)))

    async def _process_channel(self, cfg, entity, out_root, source_file, source_ref,
                               import_only=False, folder_name=None):
        candidates, link_sources, scanned = [], {}, 0
        if import_only:
            channel_dir = out_root / 'Imported_Links'
            channel_key = '__imported_links__'
            title = 'Imported_Links'
        else:
            title = folder_name or safe_name(getattr(entity, 'title', None) or getattr(entity, 'username', None) or str(source_ref))
            channel_dir = out_root / title
            channel_key = str(getattr(entity, 'id', source_ref))
        channel_dir.mkdir(parents=True, exist_ok=True)
        self.gui.events.put(('output_path', str(out_root.resolve())))
        self.gui.events.put(('channel_update', source_ref, 'Scanning' if not import_only else 'Importing',
                             0, 0, str(channel_dir.resolve())))
        self.log(f'[{source_ref}] Folder ready: {channel_dir.resolve()}')
        if not import_only:
            async for msg in self.client.iter_messages(entity, reverse=True):
                if self.cancelled:
                    break
                while not self.pause_event.is_set() and not self.cancelled:
                    await asyncio.sleep(0.25)
                scanned += 1
                if self._allowed(msg, cfg):
                    candidates.append(msg)
                if cfg.get('auto_download_links', True):
                    for url in extract_links(msg):
                        link_sources.setdefault(url, set()).add(int(msg.id))
                if scanned % 500 == 0:
                    self.log(f'[{source_ref}] Scanned {scanned:,} messages; '
                             f'{len(candidates):,} media and {len(link_sources):,} URLs.')
                    self.gui.events.put(('channel_update', source_ref, 'Scanning', 0, 0,
                                         f'{scanned:,} messages found'))
        self.log(f'[{source_ref}] Scanning finished: {scanned:,} messages.')
        # For multiple channels import the TXT once into a separate job.
        include_txt = import_only or (cfg.get('import_txt_in_scan', True) and len(parse_channels(
            cfg.get('channels') or cfg.get('channel', ''))) <= 1)
        if include_txt and source_file.is_file():
            imported = urls_from_text(source_file)
            for url in imported:
                link_sources.setdefault(url, set()).add(0)
            self.log(f'[{source_ref}] Imported {len(imported):,} TXT links.')
        elif import_only:
            raise FileNotFoundError(f'TXT input missing: {source_file}')

        media_total = len(candidates)
        links = list(link_sources) if cfg.get("auto_download_links", True) else []
        link_success = 0
        link_failed = 0
        total = media_total + len(links)
        self.log(f'[{source_ref}] Ready: {media_total:,} attachments + {len(links):,} URLs.')
        self.gui.events.put(('channel_update', source_ref, 'Downloading' if total else 'Finishing',
                             0, total, f'{media_total} media, {len(links)} links'))
        def _progress(done, count, text=''):
            self.progress(done, count, text, channel_ref=source_ref)

        # Create a readable index even if there is no media to download.
        links_dir = channel_dir / 'Links'
        links_dir.mkdir(parents=True, exist_ok=True)
        self._write_link_manifest(links_dir, link_sources, self.state.setdefault('links_downloaded', {}).setdefault(channel_key, {}))
        self.gui.events.put(('output_path', str(out_root.resolve())))
        if total == 0:
            self.log('No downloadable media or URLs found. TXT manifests are still available.')
            self.gui.events.put(('channel_update', source_ref, 'Complete', 0, 0, 'No downloadable items'))
            return {'channel': source_ref, 'status': 'complete', 'folder': str(channel_dir), 'items': 0}

        downloaded = self.state.setdefault("downloaded", {}).setdefault(channel_key, {})
        semaphore = self.batch_transfer_semaphore
        counter_lock = asyncio.Lock()
        completed = 0
        links_downloaded = self.state.setdefault("links_downloaded", {}).setdefault(channel_key, {})
        links_dir = channel_dir / "Links"
        if links:
            links_dir.mkdir(parents=True, exist_ok=True)
            self._write_link_manifest(links_dir, link_sources, links_downloaded)

        async def one(msg):
            nonlocal completed
            while not self.pause_event.is_set() and not self.cancelled:
                await asyncio.sleep(0.25)
            if self.cancelled:
                return

            name = self._filename(msg)
            target = channel_dir / f"{msg.id}_{name}"
            partial = Path(str(target) + ".part")
            key = str(msg.id)

            # SMART HISTORY: a successfully completed Telegram message stays complete
            # even after its local file is moved or deleted. This makes the state ledger
            # independent from disk cleanup. Legacy string entries are treated as completed.
            saved = downloaded.get(key)
            if cfg.get("smart_history", True) and completed_record(saved):
                async with counter_lock:
                    completed += 1
                    _progress(completed, total, f"History skip #{msg.id}: {name}")
                return

            # Compatibility mode: when Smart History is disabled, only skip a saved item
            # if its old path still exists.
            saved_path_text = record_path(saved)
            saved_path = Path(saved_path_text) if saved_path_text else target
            if (not cfg.get("smart_history", True)) and completed_record(saved) and saved_path.exists() and saved_path.stat().st_size > 0:
                async with counter_lock:
                    completed += 1
                    _progress(completed, total, f"Skipped existing: {saved_path.name}")
                return

            # Recover a completed local file if the state ledger was lost/corrupted.
            if target.exists() and target.stat().st_size > 0:
                downloaded[key] = {
                    "status": "complete",
                    "message_id": int(msg.id),
                    "filename": target.name,
                    "last_path": str(target.resolve()),
                    "size": int(target.stat().st_size),
                    "completed_at": int(time.time()),
                }
                save_json(STATE_FILE, self.state)
                async with counter_lock:
                    completed += 1
                    _progress(completed, total, f"Recovered existing: {target.name}")
                return

            # A .part file means a previous transfer did not complete.
            # Telethon cannot safely continue an arbitrary media download from that byte,
            # so restart that ONE file rather than redownloading completed files.
            if partial.exists():
                try:
                    partial.unlink()
                    self.log(f"Restarting incomplete file #{msg.id}: {target.name}")
                except Exception:
                    pass

            async with semaphore:
                success = False
                last_error = None

                for attempt in range(1, 6):
                    if self.cancelled:
                        break
                    while not self.pause_event.is_set() and not self.cancelled:
                        await asyncio.sleep(0.25)

                    started = time.monotonic()
                    last_bytes = 0
                    last_time = started
                    smooth_speed = 0.0

                    def callback(current, file_total):
                        nonlocal last_bytes, last_time, smooth_speed
                        now = time.monotonic()
                        elapsed = max(now - last_time, 0.001)
                        delta = max(current - last_bytes, 0)
                        instant = delta / elapsed
                        smooth_speed = instant if smooth_speed <= 0 else (smooth_speed * 0.75 + instant * 0.25)
                        last_bytes = current
                        last_time = now
                        eta = ((file_total - current) / smooth_speed) if smooth_speed > 1 and file_total else None
                        self.gui.events.put((
                            "transfer", f"{channel_key}:media:{msg.id}", target.name,
                            int(current), int(file_total or 0), float(smooth_speed), eta
                        ))

                    try:
                        self.log(f"Downloading #{msg.id} (attempt {attempt}/5): {target.name}")
                        result = await self.client.download_media(
                            msg, file=str(partial), progress_callback=callback
                        )
                        if result and Path(result).exists():
                            # Atomic-ish completion marker: only final filename means complete.
                            if target.exists():
                                target.unlink()
                            Path(result).replace(target)
                            downloaded[key] = {
                                "status": "complete",
                                "message_id": int(msg.id),
                                "filename": target.name,
                                "last_path": str(target.resolve()),
                                "size": int(target.stat().st_size),
                                "completed_at": int(time.time()),
                            }
                            save_json(STATE_FILE, self.state)
                            success = True
                            break
                    except Exception as e:
                        last_error = e
                        self.log(f"Connection/download error #{msg.id}: {e}")
                        if self.cancelled:
                            break
                        wait = min(30, 2 ** attempt)
                        self.log(f"Retrying #{msg.id} in {wait}s...")
                        try:
                            if not self.client.is_connected():
                                await self.client.connect()
                        except Exception:
                            pass
                        await asyncio.sleep(wait)

                self.gui.events.put(("transfer_done", f"{channel_key}:media:{msg.id}"))

                if not success and not self.cancelled:
                    self.log(f"FAILED #{msg.id} after 5 attempts: {last_error}")

                async with counter_lock:
                    completed += 1
                    _progress(completed, total, target.name)

        async def one_link(url):
            nonlocal completed, link_success, link_failed
            while not self.pause_event.is_set() and not self.cancelled:
                await asyncio.sleep(0.25)
            if self.cancelled:
                return
            fingerprint = link_hash(url)
            old = links_downloaded.get(fingerprint)
            oldpath = record_path(old)
            if completed_record(old) and (cfg.get("smart_history", True) or (oldpath and Path(oldpath).is_file())):
                link_success += 1
                async with counter_lock:
                    completed += 1
                    _progress(completed, total, f"Link history skip: {label_url(url)}")
                return
            if url.startswith("magnet:"):
                links_downloaded[fingerprint] = {
                    "status": "unsupported", "url": url,
                    "error": "Magnet links require a torrent client; use the Torrent tab for a .torrent file."
                }
                link_failed += 1
                self.log(f"Magnet link found but not auto-started: #{fingerprint}")
            else:
                transfer_id = f"{channel_key}:link:{fingerprint}"
                last_tick = [time.monotonic(), 0]
                def cb(n, size, name):
                    tick = time.monotonic()
                    speed = max(0.0, (n - last_tick[1]) / max(0.001, tick - last_tick[0]))
                    last_tick[:] = [tick, n]
                    self.gui.events.put(("transfer", transfer_id, name, n, size, speed, None))
                try:
                    self.log(f"Downloading external link: {label_url(url)}")
                    async with semaphore:
                        tg_post = telegram_post_location(url)
                        if tg_post and self.client is None:
                            raise LinkDownloadError('Telegram post link requires Telegram sign-in. Use Scan + Download Media + Links, or open the post in Telegram.')
                        if tg_post:
                            entity_id, message_id = tg_post
                            linked_entity = await self.client.get_entity(
                                int(entity_id) if entity_id.startswith("-100") else entity_id
                            )
                            linked = await self.client.get_messages(linked_entity, ids=message_id)
                            if not linked or not self._allowed(linked, cfg):
                                raise LinkDownloadError(
                                    "Linked Telegram message is inaccessible or its media type is filtered out."
                                )
                            output_dir = links_dir / "Telegram"
                            output_dir.mkdir(exist_ok=True, parents=True)
                            target = output_dir / f"{fingerprint}_{self._filename(linked)}"
                            part = Path(str(target) + ".part")
                            if part.exists():
                                part.unlink()
                            saved = await self.client.download_media(
                                linked, file=str(part),
                                progress_callback=lambda current, size: cb(current, size, target.name),
                            )
                            if not saved or not Path(saved).is_file():
                                raise LinkDownloadError("Telegram did not return linked media.")
                            Path(saved).replace(target)
                            result = {"path": str(target.resolve()), "size": target.stat().st_size,
                                      "kind": "linked_telegram_media"}
                        elif google_link_id(url) and has_token(APP_DIR / 'google_drive_token.json'):
                            # The authorized API handles shared/private Drive material.
                            try:
                                result = await asyncio.to_thread(
                                    download_google_api, url, links_dir, self.pause_event,
                                    lambda: self.cancelled, cb, cfg.get('link_max_mb', 0),
                                    APP_DIR / 'google_drive_token.json', cfg.get('google_oauth_client', '')
                                )
                            except LinkDownloadError as api_exc:
                                # Public Drive links can sometimes be fetched without auth.
                                self.log(f'Google API issue for {label_url(url)}: {api_exc}; trying public method.')
                                try:
                                    result = await asyncio.to_thread(
                                        download_provider, url, links_dir, self.pause_event,
                                        lambda: self.cancelled, cb, cfg.get('link_max_mb', 0)
                                    )
                                except Exception as public_exc:
                                    raise LinkDownloadError(
                                        f'Authorized API: {api_exc}; public method: {public_exc}'
                                    ) from public_exc
                        else:
                            result = await asyncio.to_thread(
                                download_provider, url, links_dir, self.pause_event,
                                lambda: self.cancelled, cb, cfg.get('link_max_mb', 0)
                            )
                    links_downloaded[fingerprint] = {
                        "status": "complete", "completed": True,
                        "url": url, "last_path": result["path"], "size": result["size"],
                        "kind": result["kind"], "message_ids": sorted(link_sources[url]),
                        "completed_at": int(time.time())
                    }
                    link_success += 1
                    self.log(f"Saved {result['kind']}: {Path(result['path']).name}")
                except Exception as exc:
                    links_downloaded[fingerprint] = {
                        "status": "failed", "url": url, "message_ids": sorted(link_sources[url]),
                        "error": str(exc)[:500], "last_attempt_at": int(time.time())
                    }
                    link_failed += 1
                    if not self.cancelled:
                        self.log(f"LINK FAILED {label_url(url)}: {exc}")
                finally:
                    self.gui.events.put(("transfer_done", transfer_id))
            save_json(STATE_FILE, self.state)
            self._write_link_manifest(links_dir, link_sources, links_downloaded)
            async with counter_lock:
                completed += 1
                _progress(completed, total, f"Link: {label_url(url)}")

        batch_size = max(10, int(cfg["concurrency"]) * 5)
        for i in range(0, total, batch_size):
            if self.cancelled:
                break
            await asyncio.gather(*(one(m) for m in candidates[i:i+batch_size]))

        # Download every unique URL from all scanned messages, not just posts with attachments.
        if links and not self.cancelled:
            self.log(f"Starting automatic link download queue: {len(links):,} URLs...")
            for i in range(0, len(links), batch_size):
                if self.cancelled:
                    break
                await asyncio.gather(*(one_link(url) for url in links[i:i + batch_size]))
            self._write_link_manifest(links_dir, link_sources, links_downloaded)
            self.log(f"External links: {link_success:,} completed/skipped, {link_failed:,} failed/unsupported.")

        if self.cancelled:
            self.log(f'[{source_ref}] Stopped. Resume state preserved.')
            self.gui.events.put(('channel_update', source_ref, 'Stopped', completed, total, 'Partial progress saved'))
            return {'channel': source_ref, 'status': 'stopped', 'folder': str(channel_dir)}

        self.log(f'[{source_ref}] Building ZIP archive(s)...')
        self.gui.events.put(('channel_update', source_ref, 'Zipping', completed, total, 'Packaging this channel'))
        zip_paths = await asyncio.to_thread(self.make_zips, channel_dir, int(cfg['zip_split_mb']))
        for z in zip_paths:
            self.log(f'[{source_ref}] Created: {z}')
        self.gui.events.put(('channel_update', source_ref, 'Complete', total, total,
                             f'{len(zip_paths)} ZIP(s), {link_failed} failed links'))
        return {'channel': source_ref, 'status': 'complete', 'folder': str(channel_dir),
                'items': total, 'links_failed': link_failed, 'zip_files': [str(z) for z in zip_paths]}

    def _write_link_manifest(self, folder, sources, records):
        """Keep a complete, reviewable index, including failed links for the next run."""
        import csv
        target = folder / "LINKS_MANIFEST.csv"
        with target.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(("URL", "Source message IDs", "Status", "Saved as", "Type", "Error"))
            for url, message_ids in sources.items():
                saved = records.get(link_hash(url), {})
                writer.writerow((url, " ".join(map(str, sorted(message_ids))),
                                 saved.get("status", "pending"), record_path(saved),
                                 saved.get("kind", ""), saved.get("error", "")))
        all_urls = sorted(sources)
        write_txt(folder / 'ALL_LINKS.txt', all_urls)
        failures = [u for u in all_urls if records.get(link_hash(u), {}).get('status') in ('failed', 'unsupported')]
        write_txt(folder / 'FAILED_LINKS.txt', failures)
        write_txt(folder / 'FAILED_LINKS_DETAILS.txt', [
            f"{u}\t{records.get(link_hash(u), {}).get('error', '')}" for u in failures
        ])
        auth_needed = [u for u in failures if any(x in records.get(link_hash(u), {}).get('error', '').lower()
                          for x in ('403', '401', 'sign-in', 'sign in', 'permission', 'inaccessible', 'private', 'restricted', 'unauthorized'))]
        write_txt(folder / 'NEEDS_SIGN_IN.txt', auth_needed)
        # A root-level copy makes the master TXT easy to find in Explorer.
        write_txt(folder.parent / 'ALL_LINKS.txt', all_urls)
        write_txt(folder.parent / 'FAILED_LINKS.txt', failures)
        # Preserve a deduplicated master list across different channels/runs.
        global_master = folder.parent.parent / 'GLOBAL_ALL_LINKS.txt'
        previous = []
        if global_master.is_file():
            previous = [line.strip() for line in global_master.read_text(encoding='utf-8', errors='replace').splitlines() if line.strip()]
        write_txt(global_master, sorted(set(previous).union(all_urls)))

    def make_zips(self, folder, split_mb):
        files = [
            p for p in folder.rglob("*")
            if p.is_file() and not p.name.lower().endswith((".zip", ".part"))
        ]
        if not files:
            return []

        limit = max(100, split_mb) * 1024 * 1024
        groups, current, current_size = [], [], 0
        for p in files:
            size = p.stat().st_size
            if current and current_size + size > limit:
                groups.append(current)
                current, current_size = [], 0
            current.append(p)
            current_size += size
        if current:
            groups.append(current)

        # Already-compressed media/archive formats gain almost nothing from DEFLATE
        # and can make the "ZIP" phase dramatically slower.
        store_exts = {
            ".rar", ".7z", ".zip", ".gz", ".bz2", ".xz",
            ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac",
            ".mp4", ".mkv", ".mov", ".avi", ".webm",
            ".jpg", ".jpeg", ".png", ".webp", ".gif",
        }

        results = []
        for idx, group in enumerate(groups, 1):
            suffix = f"_part{idx:03d}" if len(groups) > 1 else ""
            zpath = folder.parent / f"{folder.name}{suffix}.zip"
            self.log(f"Building ZIP {idx}/{len(groups)}: {zpath.name}")

            with zipfile.ZipFile(zpath, "w", allowZip64=True) as z:
                for n, p in enumerate(group, 1):
                    compression = zipfile.ZIP_STORED if p.suffix.lower() in store_exts else zipfile.ZIP_DEFLATED
                    z.write(p, p.relative_to(folder), compress_type=compression)
                    if n % 25 == 0 or n == len(group):
                        self.log(f"ZIP {idx}: {n}/{len(group)} files")

            results.append(zpath)
        return results

APP_VERSION = "100-Channel Batch + Google Drive v3.4 (2026-11-02)"

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Telegram Downloader - " + APP_VERSION)
        self.geometry("1180x950")
        self.minsize(840, 640)
        self.cfg = load_json(CONFIG_FILE, DEFAULT_CONFIG)
        self.channel_entries = parse_channels(self.cfg.get('channels') or self.cfg.get('channel', ''))
        self.primary_channel_snapshot = str(self.cfg.get('channel', '')).strip()
        self.channel_progress_state = {}
        self.events = queue.Queue()
        self.archiver = TelegramArchiver(self)
        self.worker = None
        self.paused = False
        self.active_transfers = {}
        self.total_session_bytes = 0
        self.torrent_thread = None
        self.torrent_stop = threading.Event()
        self.torrent_pause = threading.Event()
        self.torrent_handle = None
        self.qr_cancelled = threading.Event()
        self.qr_window = None
        self.qr_image = None
        self.auth_running = False
        self.google_running = False
        self.pending_ask_close = None
        self._build()
        self.after(100, self._poll)

    def _build(self):
        pad = {"padx": 10, "pady": 6}
        top = ttk.LabelFrame(self, text="Telegram")
        top.pack(fill="x", padx=12, pady=10)

        self.vars = {}
        fields = [
            ("API ID", "api_id"),
            ("API Hash", "api_hash"),
            ("Primary channel / @username / t.me", "channel"),
            ("Output folder", "output_dir"),
            ("Concurrent downloads", "concurrency"),
            ("ZIP split size (MB)", "zip_split_mb"),
        ]
        for r, (label, key) in enumerate(fields):
            ttk.Label(top, text=label).grid(row=r, column=0, sticky="w", **pad)
            v = tk.StringVar(value=str(self.cfg.get(key, "")))
            self.vars[key] = v
            ent = ttk.Entry(top, textvariable=v, show="*" if key == "api_hash" else "")
            ent.grid(row=r, column=1, sticky="ew", **pad)
            if key == "output_dir":
                ttk.Button(top, text="Browse", command=self.browse).grid(row=r, column=2, **pad)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Mobile number (+country code)").grid(
            row=6, column=0, sticky="w", padx=10, pady=6)
        self.phone = tk.StringVar()
        ttk.Entry(top, textvariable=self.phone).grid(
            row=6, column=1, sticky="ew", padx=10, pady=6)
        ttk.Label(top, text="Example: +15551234567", foreground="#307050").grid(
            row=6, column=2, sticky="w", padx=8)

        auth_row = ttk.Frame(top)
        auth_row.grid(row=7, column=0, columnspan=3, sticky="ew", padx=10, pady=(4, 9))
        ttk.Label(auth_row, text="Login method:").pack(side="left")
        # This defaults to phone. QR is optional, and the phone button never invokes QR.
        self.auth_choice = tk.StringVar(value="Phone login code")
        self.auth_dropdown = ttk.Combobox(
            auth_row, textvariable=self.auth_choice,
            values=("Phone login code", "QR code"), state="readonly", width=19,
        )
        self.auth_dropdown.pack(side="left", padx=8)
        self.qr_login_btn = ttk.Button(auth_row, text="Sign in (selected method)",
                                       command=self.start_selected_login)
        self.qr_login_btn.pack(side="left", padx=(6, 4))
        self.phone_login_btn = ttk.Button(auth_row, text="Send Phone Login Code",
                                          command=self.start_phone_login)
        self.phone_login_btn.pack(side="left", padx=4)
        self.qr_only_btn = ttk.Button(auth_row, text="Use QR Instead",
                                       command=self.start_qr_login)
        self.qr_only_btn.pack(side="left", padx=4)
        ttk.Label(top,
                  text="Telegram chooses code delivery (SMS, Telegram service chat, email, etc.). "
                       "If a saved session works, no code is needed.",
                  foreground="#307050", wraplength=870).grid(
            row=8, column=0, columnspan=3,
            sticky="w", padx=10, pady=(0, 4))

        # Multi-channel configuration does not require extra Telegram sessions.
        batch = ttk.LabelFrame(self, text='Multi-Channel Batch')
        batch.pack(fill='x', padx=12, pady=(0, 4))
        self.channels_label = tk.StringVar(value=f'{len(self.channel_entries)} channel(s) in batch')
        ttk.Button(batch, text='Manage Channels / Paste List', command=self.manage_channels).pack(
            side='left', padx=8, pady=5)
        ttk.Button(batch, text='Import Channels TXT', command=self.import_channels_txt).pack(
            side='left', padx=3, pady=5)
        ttk.Label(batch, textvariable=self.channels_label).pack(side='left', padx=10)
        ttk.Label(batch, text='Channels active at once (1-100):').pack(side='left', padx=(12, 5))
        self.parallel_channels_var = tk.StringVar(value=str(self.cfg.get('parallel_channels', 5)))
        ttk.Spinbox(batch, textvariable=self.parallel_channels_var,
                    from_=1, to=MAX_PARALLEL_CHANNELS, width=5).pack(side='left')
        ttk.Label(batch, text='(5 recommended; transfers shared across channels)',
                  foreground='#307050').pack(side='left', padx=8)

        filters = ttk.LabelFrame(self, text="Download")
        filters.pack(fill="x", padx=12, pady=4)
        self.checks = {}
        for key, label in [
            ("include_photos", "Photos"),
            ("include_video", "Videos"),
            ("include_audio", "Audio / music"),
            ("include_documents", "Files / documents / archives"),
            ("include_voice", "Voice notes"),
            ("include_stickers", "Stickers"),
        ]:
            v = tk.BooleanVar(value=bool(self.cfg.get(key)))
            self.checks[key] = v
            ttk.Checkbutton(filters, text=label, variable=v).pack(side="left", padx=9, pady=8)

        links_opt = tk.BooleanVar(value=bool(self.cfg.get("auto_download_links", True)))
        self.checks["auto_download_links"] = links_opt
        ttk.Checkbutton(
            self, text="Auto-download ALL links found in channel messages, captions, and URL buttons",
            variable=links_opt
        ).pack(fill="x", padx=18, pady=(3, 2))
        ttk.Label(
            self, text="HTTP(S) files are downloaded; web pages are saved as HTML. "
                 "Links go into each channel's Links folder with a CSV manifest. "
                 "Restricted sites may require manual login or download.",
            wraplength=940, foreground="#307050"
        ).pack(fill="x", padx=33, pady=(0, 3))

        limit_row = ttk.Frame(self)
        limit_row.pack(fill="x", padx=30, pady=(0, 3))
        ttk.Label(limit_row, text="Maximum file size for HTTP links (MB, 0 = unlimited):").pack(side="left")
        self.link_max_var = tk.StringVar(value=str(self.cfg.get("link_max_mb", 0)))
        ttk.Entry(limit_row, textvariable=self.link_max_var, width=10).pack(side="left", padx=7)

        import_frame = ttk.LabelFrame(self, text="TXT Link Import + Google Drive API")
        import_frame.pack(fill="x", padx=12, pady=(4, 3))
        self.import_path = tk.StringVar(value=str(self.cfg.get('import_txt') or APP_DIR / 'content.txt'))
        ttk.Label(import_frame, text="Links TXT:").grid(row=0, column=0, padx=8, pady=4, sticky="w")
        ttk.Entry(import_frame, textvariable=self.import_path).grid(row=0, column=1, padx=5, pady=4, sticky="ew")
        ttk.Button(import_frame, text="Choose TXT", command=self.choose_txt).grid(row=0, column=2, padx=5)
        self.include_import = tk.BooleanVar(value=bool(self.cfg.get('import_txt_in_scan', True)))
        ttk.Checkbutton(import_frame, text="Include TXT links in every Telegram scan",
                        variable=self.include_import).grid(row=1, column=1, padx=5, pady=3, sticky="w")
        self.google_client_path = tk.StringVar(value=str(self.cfg.get('google_oauth_client') or ''))
        ttk.Label(import_frame, text="Google Desktop OAuth JSON:").grid(row=2, column=0, padx=8, pady=4, sticky="w")
        ttk.Entry(import_frame, textvariable=self.google_client_path).grid(row=2, column=1, padx=5, pady=4, sticky="ew")
        ttk.Button(import_frame, text="Choose JSON", command=self.choose_google_json).grid(row=2, column=2, padx=5)
        ttk.Label(import_frame,
            text="Google Drive OAuth needs a real Desktop OAuth client JSON from YOUR Google Cloud project. "
                 "The app does not include one; public Drive links can download without signing in.",
            foreground="#307050", wraplength=940).grid(
                row=3, column=0, columnspan=3, padx=8, pady=3, sticky="w")
        ttk.Button(import_frame, text="Google OAuth Setup Guide", command=self.open_google_setup).grid(
            row=4, column=2, padx=5, pady=5)
        self.google_button = ttk.Button(import_frame, text="Sign In to Google Drive", command=self.start_google_sign_in)
        self.google_button.grid(row=4, column=1, sticky="w", padx=5, pady=5)
        self.google_status = tk.StringVar(value='Google Drive token: ' +
            ('saved (click Sign In to verify)' if has_token(APP_DIR / 'google_drive_token.json') else 'not connected (public links can still work)'))
        ttk.Label(import_frame, textvariable=self.google_status, wraplength=840).grid(row=5, column=0,
                                                                               columnspan=3, sticky="w", padx=8, pady=(0,4))
        import_frame.columnconfigure(1, weight=1)

        smart = tk.BooleanVar(value=bool(self.cfg.get("smart_history", True)))
        self.checks["smart_history"] = smart
        ttk.Checkbutton(
            self,
            text="Smart History: never redownload completed Telegram items even if you move/delete the local files",
            variable=smart,
        ).pack(fill="x", padx=18, pady=(2, 4))

        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=12, pady=8)
        self.start_btn = ttk.Button(controls, text="Scan + Download Media + Links + ZIP", command=self.start)
        self.start_btn.pack(side="left")
        self.import_btn = ttk.Button(controls, text="Download TXT Links (No Telegram Login)", command=self.start_import_only)
        self.import_btn.pack(side="left", padx=6)
        self.pause_btn = ttk.Button(controls, text="Pause", command=self.pause, state="disabled")
        self.pause_btn.pack(side="left", padx=6)
        self.stop_btn = ttk.Button(controls, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Button(controls, text="Open Downloads", command=self.open_output).pack(side="right")
        self.output_path_label = tk.StringVar(value='Download location: ' +
            str(Path(self.cfg.get('output_dir') or APP_DIR / 'downloads').expanduser().resolve()))
        ttk.Label(self, textvariable=self.output_path_label, wraplength=1120).pack(fill="x", padx=16)

        channels_frame = ttk.LabelFrame(self, text='Channel Queue — individual progress / failures')
        channels_frame.pack(fill='x', padx=12, pady=(0, 3))
        tree_container = ttk.Frame(channels_frame)
        tree_container.pack(fill='both', expand=True, padx=6, pady=5)
        self.channel_tree = ttk.Treeview(tree_container, columns=('phase', 'progress', 'details'),
                                         show='tree headings', height=4, selectmode='browse')
        self.channel_tree.heading('#0', text='Channel')
        self.channel_tree.column('#0', width=190, stretch=True)
        for key, caption, width in [('phase', 'State', 100), ('progress', 'Progress', 105),
                                     ('details', 'Details', 620)]:
            self.channel_tree.heading(key, text=caption)
            self.channel_tree.column(key, width=width, stretch=(key == 'details'))
        self.channel_tree.pack(side='left', fill='both', expand=True)
        channel_scroll = ttk.Scrollbar(tree_container, orient='vertical', command=self.channel_tree.yview)
        channel_scroll.pack(side='right', fill='y')
        self.channel_tree.configure(yscrollcommand=channel_scroll.set)
        self._refresh_channel_list()

        self.progress_var = tk.DoubleVar()
        self.progressbar = ttk.Progressbar(self, variable=self.progress_var, maximum=100)
        self.progressbar.pack(fill="x", padx=12, pady=(2, 4))
        self.status = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status).pack(anchor="w", padx=14)
        self.metrics = tk.StringVar(value="Active: 0 | Speed: 0 B/s | Downloaded this run: 0 B")
        ttk.Label(self, textvariable=self.metrics).pack(anchor="w", padx=14, pady=(2, 0))

        logframe = ttk.LabelFrame(self, text="Activity")
        logframe.pack(fill="both", expand=True, padx=12, pady=10)
        self.logbox = tk.Text(logframe, wrap="word", height=18)
        self.logbox.pack(fill="both", expand=True, padx=6, pady=6)

        torrent = ttk.LabelFrame(self, text="Torrent Downloader")
        torrent.pack(fill="x", padx=12, pady=(0, 10))

        self.torrent_file = tk.StringVar()
        self.torrent_output = tk.StringVar(value=str(Path(self.vars["output_dir"].get() or APP_DIR) / "torrents"))

        ttk.Label(torrent, text=".torrent file").grid(row=0, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(torrent, textvariable=self.torrent_file).grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        ttk.Button(torrent, text="Choose Torrent", command=self.choose_torrent).grid(row=0, column=2, padx=6, pady=6)

        ttk.Label(torrent, text="Torrent output").grid(row=1, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(torrent, textvariable=self.torrent_output).grid(row=1, column=1, sticky="ew", padx=6, pady=6)
        ttk.Button(torrent, text="Browse", command=self.choose_torrent_output).grid(row=1, column=2, padx=6, pady=6)
        torrent.columnconfigure(1, weight=1)

        tbuttons = ttk.Frame(torrent)
        tbuttons.grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=6)
        self.torrent_start_btn = ttk.Button(tbuttons, text="Download Torrent", command=self.start_torrent)
        self.torrent_start_btn.pack(side="left")
        self.torrent_pause_btn = ttk.Button(tbuttons, text="Pause Torrent", command=self.pause_torrent, state="disabled")
        self.torrent_pause_btn.pack(side="left", padx=6)
        self.torrent_stop_btn = ttk.Button(tbuttons, text="Stop Torrent", command=self.stop_torrent, state="disabled")
        self.torrent_stop_btn.pack(side="left")

        self.torrent_progress = tk.DoubleVar()
        ttk.Progressbar(torrent, variable=self.torrent_progress, maximum=100).grid(
            row=3, column=0, columnspan=3, sticky="ew", padx=10, pady=(4, 2)
        )
        self.torrent_status = tk.StringVar(value="Torrent: ready")
        ttk.Label(torrent, textvariable=self.torrent_status).grid(
            row=4, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 8)
        )

        note = ("Use only channels/chats your Telegram account is allowed to access. "
                "The app uses Telegram's normal account authorization and does not bypass private-channel access controls.")
        ttk.Label(self, text=note, wraplength=850).pack(fill="x", padx=14, pady=(0, 10))

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.vars["output_dir"].get() or ".")
        if d:
            self.vars["output_dir"].set(d)

    def collect(self):
        cfg = dict(self.cfg)
        for k, v in self.vars.items():
            cfg[k] = v.get().strip()
        for k, v in self.checks.items():
            cfg[k] = bool(v.get())
        cfg["concurrency"] = int(cfg["concurrency"])
        cfg["zip_split_mb"] = int(cfg["zip_split_mb"])
        cfg["link_max_mb"] = int(self.link_max_var.get().strip() or 0)
        if cfg["link_max_mb"] < 0:
            raise ValueError("Link maximum size must be 0 (unlimited) or a positive MB value.")
        cfg["qr_login"] = self.auth_choice.get().startswith("QR")
        cfg['import_txt'] = self.import_path.get().strip()
        cfg['import_txt_in_scan'] = bool(self.include_import.get())
        cfg['google_oauth_client'] = self.google_client_path.get().strip()
        cfg['parallel_channels'] = self.parallel_channels_var.get().strip()
        primary = cfg['channel'].strip()
        if primary and primary != self.primary_channel_snapshot:
            # Editing the original primary field still updates the first channel.
            if self.channel_entries and self.primary_channel_snapshot in self.channel_entries:
                pos = self.channel_entries.index(self.primary_channel_snapshot)
                self.channel_entries[pos] = primary
            else:
                self.channel_entries.insert(0, primary)
            self.primary_channel_snapshot = primary
        cfg['channels'], cfg['parallel_channels'] = validate_channel_batch(
            self.channel_entries or primary, cfg['parallel_channels'])
        return cfg

    def _refresh_channel_list(self):
        self.channels_label.set(f'{len(self.channel_entries)}/{MAX_CHANNELS} channels')
        if not hasattr(self, 'channel_tree'):
            return
        if not self.auth_running:
            self.channel_tree.delete(*self.channel_tree.get_children())
            for ref in self.channel_entries:
                self.channel_tree.insert('', 'end', iid=ref, text=ref,
                                         values=('Ready', '—', 'Waiting for scan'))

    def import_channels_txt(self):
        path = filedialog.askopenfilename(title='Import Telegram channel handles / URLs',
            filetypes=[('Text files', '*.txt'), ('All files', '*.*')])
        if not path:
            return
        try:
            additions = parse_channels(Path(path).read_text(encoding='utf-8-sig'))
        except Exception as exc:
            messagebox.showerror('Cannot read channel list', str(exc))
            return
        # Import only entries recognized as Telegram handle, t.me link or peer ID.
        additions = [x for x in additions if x.startswith(('@', 'https://t.me/', 'http://t.me/'))
                     or x.lstrip('-').isdigit()]
        if not additions:
            messagebox.showwarning('No Telegram channel IDs',
                'TXT needs one @handle, t.me URL, or numeric Telegram peer ID per line.')
            return
        combined = parse_channels(self.channel_entries + additions)
        if len(combined) > MAX_CHANNELS:
            messagebox.showwarning('Channel limit',
                f'The combined list has {len(combined)} unique channels. '
                f'This version supports a maximum of {MAX_CHANNELS} per batch. '
                'The first 100 were kept; you can import the rest in a later batch.')
            combined = combined[:MAX_CHANNELS]
        self.channel_entries = combined
        self.vars['channel'].set(self.channel_entries[0])
        self.primary_channel_snapshot = self.channel_entries[0]
        self._refresh_channel_list()
        messagebox.showinfo('Channels added', f'Found {len(additions)} entries in TXT. '
                            f'Total unique channels queued: {len(self.channel_entries)}/{MAX_CHANNELS}.')

    def manage_channels(self):
        if self.auth_running:
            messagebox.showinfo('Batch running', 'Stop the current run before editing its channel list.')
            return
        window = tk.Toplevel(self)
        window.title('Manage Telegram channels (maximum 100)')
        window.geometry('720x480')
        window.transient(self)
        window.grab_set()
        ttk.Label(window, text='One Telegram @username, t.me URL, or numeric peer ID per line. '
                             f'Maximum {MAX_CHANNELS} channels per run. Only channels your account can access can be downloaded.',
                  wraplength=660).pack(fill='x', padx=15, pady=10)
        area = tk.Text(window, wrap='none', height=17)
        area.pack(fill='both', expand=True, padx=12)
        existing = self.channel_entries or parse_channels(self.vars['channel'].get())
        area.insert('1.0', '\n'.join(existing))
        controls = ttk.Frame(window)
        controls.pack(fill='x', padx=12, pady=12)
        def append_primary():
            val = self.vars['channel'].get().strip()
            if val:
                vals = parse_channels(area.get('1.0', 'end') + '\n' + val)
                area.delete('1.0', 'end')
                area.insert('1.0', '\n'.join(vals))
        def save_channels():
            entries = parse_channels(area.get('1.0', 'end'))
            if not entries:
                messagebox.showwarning('No channels', 'Enter at least one channel.', parent=window)
                return
            if len(entries) > MAX_CHANNELS:
                messagebox.showerror('Too many channels',
                    f'You entered {len(entries)} unique channels. Maximum allowed is {MAX_CHANNELS}.',
                    parent=window)
                return
            self.channel_entries = entries
            self.vars['channel'].set(entries[0])
            self.primary_channel_snapshot = entries[0]
            self.cfg['channels'] = entries
            self.cfg['channel'] = entries[0]
            save_json(CONFIG_FILE, self.cfg)
            self._refresh_channel_list()
            window.destroy()
        ttk.Button(controls, text='Add Primary Field', command=append_primary).pack(side='left')
        ttk.Label(controls, text='Max 100 channels; duplicates removed', foreground='#307050').pack(side='left', padx=12)
        ttk.Button(controls, text='Save Channel List', command=save_channels).pack(side='right')
        ttk.Button(controls, text='Cancel', command=window.destroy).pack(side='right', padx=8)

    def _update_channel_row(self, ref, phase, done, total, info):
        if not self.channel_tree.exists(ref):
            self.channel_tree.insert('', 'end', iid=ref, text=ref)
        pct = (done / total * 100) if total else (100 if phase == 'Complete' else 0)
        progress_text = f'{done:,}/{total:,} ({pct:.1f}%)' if total else ('100%' if phase == 'Complete' else '—')
        self.channel_tree.item(ref, values=(phase, progress_text, str(info)[:180]))
        self.channel_progress_state[ref] = (done, total, phase)
        counted = [(d, t) for d, t, p in self.channel_progress_state.values() if t > 0]
        total_count = sum(t for d, t in counted)
        finished_count = sum(d for d, t in counted)
        if total_count:
            self.progress_var.set(finished_count / total_count * 100)
        self.status.set(f'Channels: {sum(1 for _,_,p in self.channel_progress_state.values() if p == "Complete")} '
                        f'complete / {len(self.channel_progress_state)} total — {ref}: {phase}')

    def choose_txt(self):
        chosen = filedialog.askopenfilename(title='Select exported link text',
            filetypes=[('Text files', '*.txt'), ('All files', '*.*')])
        if chosen:
            self.import_path.set(chosen)

    def choose_google_json(self):
        chosen = filedialog.askopenfilename(title='Choose downloaded Google OAuth Desktop Client JSON',
            filetypes=[('Google OAuth JSON', '*.json'), ('All files', '*.*')])
        if not chosen:
            return False
        try:
            validate_desktop_client_json(chosen)
        except Exception as exc:
            messagebox.showerror('Incorrect Google OAuth file', str(exc))
            return False
        self.google_client_path.set(chosen)
        self.google_status.set('Desktop OAuth client selected. Click Sign In to Google Drive.')
        return True

    def open_google_setup(self):
        messagebox.showinfo('Google Drive - first-time setup',
            '1. Open Google Cloud Console and create/select your own project.\n'
            '2. Enable Google Drive API.\n'
            '3. In Google Auth Platform configure Branding + Audience (External for personal accounts).\n'
            '4. Add your Google account under Test users if the app is in Testing mode.\n'
            '5. Under Clients create an OAuth Client ID of type DESKTOP APP.\n'
            '6. Download the JSON from Google, then click Choose JSON in this app.\n'
            '7. Click Sign In to Google Drive to authorize read-only access.\n\n'
            'A file path alone is not an OAuth credential. Telegram API ID and hash are unrelated.')
        webbrowser.open('https://developers.google.com/workspace/drive/api/quickstart/python')

    def start_google_sign_in(self):
        if self.google_running:
            return
        client = self.google_client_path.get().strip()
        token_path = APP_DIR / 'google_drive_token.json'
        # An existing signed-in Google token can be checked/refreshed without
        # selecting OAuth JSON again. A fresh sign-in needs the Desktop client.
        if not has_token(token_path):
            if not client or not Path(client).is_file():
                self.google_status.set('Missing Desktop OAuth client JSON. Choose your downloaded Google credentials.')
                messagebox.showinfo('Google OAuth setup required',
                    'Telegram login is already separate and can work.\n\n'
                    'Google Drive sign-in needs a genuine Desktop OAuth client JSON '                    'downloaded from your own Google Cloud project. '                    'The previous path was only a placeholder, not a real JSON file.\n\n'
                    'Select your downloaded client_secret_....json now. '                    'If you have not created it, click Google OAuth Setup Guide.')
                if not self.choose_google_json():
                    return
                client = self.google_client_path.get().strip()
            else:
                try:
                    validate_desktop_client_json(client)
                except Exception as exc:
                    messagebox.showerror('Google OAuth configuration', str(exc))
                    return
        try:
            cfg = self.collect()
            save_json(CONFIG_FILE, cfg)
        except Exception as exc:
            messagebox.showerror('Configuration', str(exc))
            return
        self.google_running = True
        self.google_button.configure(state='disabled')
        self.google_status.set('Checking Google credentials; browser sign-in may open if needed...')
        def worker():
            try:
                result = google_sign_in(client, APP_DIR / 'google_drive_token.json')
                self.events.put(('google_complete', result))
            except Exception as exc:
                self.events.put(('google_error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def start(self):
        self._start_task(auth_only=False)

    def start_import_only(self):
        self._start_task(auth_only=False, import_only=True)

    def start_selected_login(self):
        self._start_task(auth_only=True)

    def start_phone_login(self):
        self.auth_choice.set("Phone login code")
        self._start_task(auth_only=True, login_mode="phone")

    def start_qr_login(self):
        self.auth_choice.set("QR code")
        self._start_task(auth_only=True, login_mode="qr")

    def _toggle_auth_buttons(self, running):
        state = "disabled" if running else "normal"
        self.qr_login_btn.config(state=state)
        self.phone_login_btn.config(state=state)
        self.qr_only_btn.config(state=state)
        self.auth_dropdown.config(state="disabled" if running else "readonly")

    def _start_task(self, auth_only=False, login_mode=None, import_only=False):
        if self.auth_running:
            return
        try:
            cfg = self.collect()
            mode = login_mode or ("qr" if self.auth_choice.get() == "QR code" else "phone")
            cfg["qr_login"] = mode == "qr"
            phone = self.phone.get().strip()
            if not import_only and (not cfg['api_id'] or not cfg['api_hash'] or (not auth_only and not cfg['channels'])):
                raise ValueError('Telegram API ID and API Hash are required, plus at least one channel for downloading.')
            if not import_only:
                int(cfg['api_id'])
            else:
                source = Path(cfg['import_txt']).expanduser()
                if not source.is_file():
                    raise ValueError(f'TXT file does not exist: {source}')
            # A valid old session may be reusable without a phone number. If the
            # user chose phone-only authorization explicitly, require a number.
            if auth_only and mode == "phone":
                from telegram_auth import normalize_phone
                phone = normalize_phone(phone)
            save_json(CONFIG_FILE, cfg)
        except Exception as e:
            messagebox.showerror("Configuration", str(e))
            return

        self.logbox.delete("1.0", "end")
        self.events.put(("log", "Running " + APP_VERSION))
        if not import_only:
            self.events.put(('log', f'Selected login: {mode.upper()} (stored session checked first).'))
        root = Path(cfg['output_dir']).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.output_path_label.set(f'Download location: {root} (open with Open Downloads)')
        self.events.put(('log', f'Download folder created: {root}'))
        self.qr_cancelled.clear()
        self.auth_running = True
        self._toggle_auth_buttons(True)
        self.progress_var.set(0)
        self.channel_progress_state = {}
        self.channel_tree.delete(*self.channel_tree.get_children())
        for ref in (['__imported_links__'] if import_only else cfg.get('channels', [])):
            self._update_channel_row(ref, 'Queued', 0, 0, 'Waiting to start')
        self.status.set("Reading imported TXT links..." if import_only else "Checking Telegram session / connecting...")
        self.active_transfers.clear()
        self.total_session_bytes = 0
        self.metrics.set("Active: 0 | Speed: 0 B/s | Downloaded this run: 0 B")
        self.start_btn.config(state="disabled")
        self.import_btn.config(state="disabled")
        self.pause_btn.config(state="disabled" if auth_only else "normal", text="Pause")
        self.stop_btn.config(state="normal")
        self.paused = False

        def work():
            async def run_safely():
                try:
                    if auth_only:
                        client = await connect_preserving_sessions(
                            app_dir=APP_DIR, api_id=int(cfg["api_id"]),
                            api_hash=cfg["api_hash"], phone=phone,
                            gui=self, log=self.archiver.log, qr_mode=(mode == "qr"),
                        )
                        try:
                            self.events.put(("auth_complete",))
                        finally:
                            await client.disconnect()
                    else:
                        await self.archiver.run(cfg, phone, import_only=import_only)
                finally:
                    # Release the SQLite session lock after transfer or failure.
                    if not auth_only:
                        client = self.archiver.client
                        if client is not None and client.is_connected():
                            await client.disconnect()
            try:
                asyncio.run(run_safely())
            except Exception as e:
                self.events.put(("error", f"{type(e).__name__}: {e}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def pause(self):
        self.paused = not self.paused
        self.archiver.toggle_pause(self.paused)
        self.pause_btn.config(text="Resume" if self.paused else "Pause")
        self.status.set("Paused" if self.paused else "Running")

    def stop(self):
        self.archiver.stop()
        self.qr_cancelled.set()
        if self.pending_ask_close is not None:
            self.pending_ask_close("")
        self.status.set("Stopping...")

    def open_output(self):
        p = Path(self.vars["output_dir"].get() or APP_DIR)
        p.mkdir(parents=True, exist_ok=True)
        self.output_path_label.set(f'Download location: {p.resolve()}')
        if os.name == "nt":
            os.startfile(p)
        else:
            import subprocess
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])

    async def ask_async(self, title, prompt, secret=False, allow_resend=False, resend_after=0):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.events.put(("ask", title, prompt, secret, future, loop,
                         allow_resend, resend_after))
        return await future

    def _ask_dialog(self, title, prompt, secret, future, loop,
                    allow_resend=False, resend_after=0):
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("600x260")
        win.minsize(560, 230)
        win.transient(self)
        win.grab_set()
        ttk.Label(win, text=prompt, wraplength=470, justify="left").pack(
            padx=20, pady=(18, 8))
        val = tk.StringVar()
        ent = ttk.Entry(win, textvariable=val, show="*" if secret else "", width=56)
        ent.pack(padx=20, pady=8)
        ent.focus_set()
        buttons = ttk.Frame(win)
        buttons.pack(padx=12, pady=(8, 18))
        settled = [False]

        def close_with(answer):
            if settled[0]:
                return
            settled[0] = True
            self.pending_ask_close = None
            win.destroy()
            if not future.done():
                loop.call_soon_threadsafe(future.set_result, answer)

        self.pending_ask_close = close_with
        ttk.Button(buttons, text="Verify code" if not secret else "Verify password", command=lambda: close_with(val.get().strip())).pack(
            side="left", padx=5)
        if allow_resend:
            resend_button = ttk.Button(buttons, text="Resend code", state="disabled",
                                       command=lambda: close_with(CODE_RESEND))
            resend_button.pack(side="left", padx=5)
            remaining = [max(0, int(resend_after or 0))]

            def countdown():
                if settled[0]:
                    return
                if remaining[0] <= 0:
                    resend_button.config(text="Resend code", state="normal")
                else:
                    resend_button.config(text=f"Resend ({remaining[0]}s)", state="disabled")
                    remaining[0] -= 1
                    win.after(1000, countdown)
            countdown()
        ttk.Button(buttons, text="Cancel", command=lambda: close_with("")).pack(
            side="left", padx=5)
        win.protocol("WM_DELETE_WINDOW", lambda: close_with(""))
        win.bind("<Return>", lambda event: close_with(val.get().strip()))

    def show_qr_login(self, url):
        # The token is a short-lived login credential: only show it locally.
        self.events.put(("qr_show", url))

    def close_qr_login(self):
        self.events.put(("qr_close",))

    def _show_qr_dialog(self, url):
        try:
            import qrcode
            from PIL import ImageTk
        except ImportError:
            messagebox.showerror("QR login dependency missing",
                "Run RUN_WINDOWS.bat to install qrcode[pil] and Pillow.")
            self.qr_cancelled.set()
            return

        if self.qr_cancelled.is_set():
            return
        if self.qr_window is None or not self.qr_window.winfo_exists():
            win = tk.Toplevel(self)
            self.qr_window = win
            win.title("Telegram QR sign-in - " + APP_VERSION)
            win.resizable(False, False)
            win.transient(self)
            win.lift()
            win.attributes("-topmost", True)
            win.after(200, lambda: win.attributes("-topmost", False))
            ttk.Label(win, text="Open Telegram on your PHONE > Settings > Devices > Link Desktop Device.\n"
                            "Scan the QR code below and approve login.",
                      justify="center").pack(padx=18, pady=(16, 6))
            self.qr_label = ttk.Label(win)
            self.qr_label.pack(padx=18, pady=8)
            ttk.Label(win, text="The QR code automatically refreshes if it expires.").pack(
                padx=12, pady=4)
            ttk.Button(win, text="Cancel login", command=self._cancel_qr).pack(pady=(6, 18))
            win.protocol("WM_DELETE_WINDOW", self._cancel_qr)
        qr = qrcode.QRCode(border=2, box_size=7)
        qr.add_data(url)
        qr.make(fit=True)
        self.qr_image = ImageTk.PhotoImage(qr.make_image(fill_color="black", back_color="white").convert("RGB"))
        self.qr_label.config(image=self.qr_image)
        self.qr_window.lift()

    def _cancel_qr(self):
        self.qr_cancelled.set()
        self._close_qr_dialog()

    def _close_qr_dialog(self):
        if self.qr_window is not None and self.qr_window.winfo_exists():
            self.qr_window.destroy()
        self.qr_window = None
        self.qr_image = None

    def choose_torrent(self):
        p = filedialog.askopenfilename(
            title="Choose .torrent file",
            filetypes=[("Torrent files", "*.torrent"), ("All files", "*.*")]
        )
        if p:
            self.torrent_file.set(p)

    def choose_torrent_output(self):
        d = filedialog.askdirectory(initialdir=self.torrent_output.get() or ".")
        if d:
            self.torrent_output.set(d)

    def start_torrent(self):
        if lt is None:
            messagebox.showerror(
                "Torrent support missing",
                "The libtorrent Python package is not installed. Run RUN_WINDOWS.bat again."
            )
            return
        tf = Path(self.torrent_file.get().strip())
        if not tf.exists() or tf.suffix.lower() != ".torrent":
            messagebox.showerror("Torrent", "Choose a valid .torrent file.")
            return
        out = Path(self.torrent_output.get().strip() or (APP_DIR / "torrents"))
        out.mkdir(parents=True, exist_ok=True)
        self.torrent_stop.clear()
        self.torrent_pause.clear()
        self.torrent_progress.set(0)
        self.torrent_start_btn.config(state="disabled")
        self.torrent_pause_btn.config(state="normal", text="Pause Torrent")
        self.torrent_stop_btn.config(state="normal")
        self.torrent_thread = threading.Thread(
            target=self._torrent_worker, args=(tf, out), daemon=True
        )
        self.torrent_thread.start()

    def _torrent_worker(self, tf, out):
        try:
            ses = lt.session()
            try:
                ses.listen_on(6881, 6891)
            except Exception:
                pass
            info = lt.torrent_info(str(tf))
            handle = ses.add_torrent({"ti": info, "save_path": str(out)})
            self.torrent_handle = handle
            self.events.put(("torrent_log", f"Torrent started: {info.name()}"))

            while not self.torrent_stop.is_set():
                st = handle.status()
                progress = float(st.progress) * 100.0
                down = float(st.download_rate)
                up = float(st.upload_rate)
                peers = int(st.num_peers)
                done = int(st.total_done)
                total = int(info.total_size())
                remaining = max(total - done, 0)
                eta = remaining / down if down > 1 else None
                state = str(st.state).split(".")[-1]
                self.events.put(("torrent_progress", progress, done, total, down, up, peers, eta, state))
                if st.is_seeding or (total and done >= total):
                    self.events.put(("torrent_complete", str(out / info.name())))
                    break
                time.sleep(1)

            if self.torrent_stop.is_set():
                try:
                    handle.pause()
                except Exception:
                    pass
                self.events.put(("torrent_stopped",))
        except Exception as e:
            self.events.put(("torrent_error", repr(e)))
        finally:
            self.torrent_handle = None

    def pause_torrent(self):
        h = self.torrent_handle
        if not h:
            return
        if not self.torrent_pause.is_set():
            self.torrent_pause.set()
            h.pause()
            self.torrent_pause_btn.config(text="Resume Torrent")
            self.torrent_status.set("Torrent: paused")
        else:
            self.torrent_pause.clear()
            h.resume()
            self.torrent_pause_btn.config(text="Pause Torrent")

    def stop_torrent(self):
        self.torrent_stop.set()
        self.torrent_status.set("Torrent: stopping; downloaded pieces are kept...")

    @staticmethod
    def _human_bytes(n):
        n = float(n or 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.1f} {unit}"
            n /= 1024

    @staticmethod
    def _human_time(seconds):
        if seconds is None or seconds < 0 or seconds > 86400 * 30:
            return "--"
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m:02d}m"
        if m:
            return f"{m}m {s:02d}s"
        return f"{s}s"

    def _refresh_metrics(self):
        speed = sum(v["speed"] for v in self.active_transfers.values())
        downloaded = sum(v["current"] for v in self.active_transfers.values())
        self.metrics.set(
            f"Active: {len(self.active_transfers)} | "
            f"Speed: {self._human_bytes(speed)}/s | "
            f"Currently transferred: {self._human_bytes(downloaded)}"
        )

    def _poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self.logbox.insert("end", event[1] + "\n")
                    self.logbox.see("end")
                elif kind == 'channel_update':
                    _, ref, phase, done, total, info = event
                    self._update_channel_row(ref, phase, done, total, info)
                elif kind == 'channel_progress':
                    _, ref, done, total, info = event
                    self._update_channel_row(ref, 'Downloading', done, total, info)
                elif kind == 'batch_done':
                    _, results, output = event
                    self.auth_running = False
                    self._toggle_auth_buttons(False)
                    self.start_btn.config(state='normal')
                    self.import_btn.config(state='normal')
                    self.pause_btn.config(state='disabled')
                    self.stop_btn.config(state='disabled')
                    completed = sum(x.get('status') == 'complete' for x in results)
                    failed = sum(x.get('status') == 'failed' for x in results)
                    stopped = sum(x.get('status') == 'stopped' for x in results)
                    skipped = sum(x.get('status') == 'skipped' for x in results)
                    if all(x.get('status') == 'complete' for x in results):
                        self.progress_var.set(100)
                    self.status.set(f'Batch finished: {completed} complete, {failed} failed, '
                                    f'{stopped} stopped, {skipped} skipped')
                    messagebox.showinfo('Multi-channel batch finished',
                        f'Complete: {completed}\nFailed: {failed}\nStopped: {stopped}\nSkipped: {skipped}\n'
                        f'Download folder: {output}\nSee CHANNEL_BATCH_REPORT.txt for details.')
                elif kind == "progress":
                    done, total, text = event[1:]
                    pct = (done / total * 100) if total else 0
                    self.progress_var.set(pct)
                    self.status.set(f"{done:,}/{total:,} ({pct:.1f}%) — {text}")
                elif kind == "ask":
                    self._ask_dialog(*event[1:])
                elif kind == "qr_show":
                    self._show_qr_dialog(event[1])
                elif kind == "qr_close":
                    self._close_qr_dialog()
                elif kind == 'output_path':
                    self.output_path_label.set(f'Download location: {event[1]}')
                elif kind == 'google_complete':
                    self.google_running = False
                    self.google_button.configure(state='normal')
                    self.google_status.set(event[1])
                    self.logbox.insert('end', event[1] + '\n')
                elif kind == 'google_error':
                    self.google_running = False
                    self.google_button.configure(state='normal')
                    self.google_status.set('Google Drive sign-in failed. Check log or JSON.')
                    messagebox.showerror('Google Drive sign-in', event[1])
                elif kind == "auth_complete":
                    self.status.set("Telegram session ready - you can now download")
                    self.auth_running = False
                    self.start_btn.config(state="normal")
                    self.import_btn.config(state="normal")
                    self._toggle_auth_buttons(False)
                    self.pause_btn.config(state="disabled")
                    self.stop_btn.config(state="disabled")
                    self.logbox.insert("end", "Login successful. Existing session files were preserved.\n")
                elif kind == "torrent_log":
                    self.logbox.insert("end", event[1] + "\n")
                    self.logbox.see("end")
                elif kind == "torrent_progress":
                    _, pct, done, total, down, up, peers, eta, state = event
                    self.torrent_progress.set(pct)
                    self.torrent_status.set(
                        f"Torrent: {pct:.1f}% | {self._human_bytes(done)} / {self._human_bytes(total)} | "
                        f"Down {self._human_bytes(down)}/s | Up {self._human_bytes(up)}/s | "
                        f"Peers {peers} | ETA {self._human_time(eta)} | {state}"
                    )
                elif kind == "torrent_complete":
                    self.torrent_progress.set(100)
                    self.torrent_status.set(f"Torrent complete: {event[1]}")
                    self.torrent_start_btn.config(state="normal")
                    self.torrent_pause_btn.config(state="disabled")
                    self.torrent_stop_btn.config(state="disabled")
                elif kind == "torrent_stopped":
                    self.torrent_status.set("Torrent stopped. Select the same torrent/output to resume.")
                    self.torrent_start_btn.config(state="normal")
                    self.torrent_pause_btn.config(state="disabled")
                    self.torrent_stop_btn.config(state="disabled")
                elif kind == "torrent_error":
                    self.torrent_status.set("Torrent error")
                    self.torrent_start_btn.config(state="normal")
                    self.torrent_pause_btn.config(state="disabled")
                    self.torrent_stop_btn.config(state="disabled")
                    messagebox.showerror("Torrent error", event[1])
                elif kind == "transfer":
                    _, mid, name, current, file_total, speed, eta = event
                    self.active_transfers[mid] = {
                        "name": name, "current": current, "total": file_total, "speed": speed
                    }
                    pct = (current / file_total * 100) if file_total else 0
                    self.status.set(
                        f"{name} — {self._human_bytes(current)} / "
                        f"{self._human_bytes(file_total) if file_total else '?'} "
                        f"({pct:.1f}%) — {self._human_bytes(speed)}/s — ETA {self._human_time(eta)}"
                    )
                    self._refresh_metrics()
                elif kind == "transfer_done":
                    _, mid = event
                    self.active_transfers.pop(mid, None)
                    self._refresh_metrics()
                elif kind == "done":
                    self.status.set("Complete")
                    self.auth_running = False
                    self._toggle_auth_buttons(False)
                    self.start_btn.config(state="normal")
                    self.import_btn.config(state="normal")
                    self.pause_btn.config(state="disabled")
                    self.stop_btn.config(state="disabled")
                    messagebox.showinfo("Complete", "Download and ZIP creation finished.")
                elif kind == "error":
                    self.status.set("Error")
                    self.auth_running = False
                    self._toggle_auth_buttons(False)
                    self.start_btn.config(state="normal")
                    self.import_btn.config(state="normal")
                    self.pause_btn.config(state="disabled")
                    self.stop_btn.config(state="disabled")
                    messagebox.showerror("Error", event[1])
        except queue.Empty:
            pass
        self.after(100, self._poll)

if __name__ == "__main__":
    App().mainloop()
