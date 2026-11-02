"""Discover and download external links referenced by Telegram channel messages.

HTTP/HTTPS links are streamed to disk; HTML pages are saved as page snapshots.
No third-party link-shortener, paywall, CAPTCHA or login bypass is attempted.
"""
from __future__ import annotations

import hashlib
import html
import mimetypes
import os
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

URL_RE = re.compile(r"(?i)(?:https?://|www\.)[^\s<>\"'\[\]{}]+")
MAGNET_RE = re.compile(r"(?i)magnet:\?[^\s<>\"']+")
# Many Telegram posts omit https:// before repository links. Require a slash/path to
# avoid treating filenames, ordinary words, and email addresses as URLs.
BARE_URL_RE = re.compile(r"(?i)(?<![\w@/:.])(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2,}(?::[0-9]{2,5})?/[^\s<>\"'\[\]{}]+")
TRAILING = '.,;:!?\"\'\u2019\u201d)]}>'


def extract_links(message):
    """Read plain text, captions, hidden URL entities, and inline URL buttons."""
    raw = []
    for attr in ("raw_text", "message", "text"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value:
            raw.append(value)

    # Telethon handles UTF-16 entity offsets in get_entities_text().
    extractor = getattr(message, "get_entities_text", None)
    if callable(extractor):
        try:
            for entity, text in extractor():
                target = getattr(entity, "url", None)
                if target:
                    raw.append(str(target))
                if isinstance(text, str):
                    raw.append(text)
        except Exception:
            pass
    for row in getattr(message, "buttons", None) or []:
        if not isinstance(row, (list, tuple)):
            row = [row]
        for button in row:
            url = getattr(button, "url", None)
            if url:
                raw.append(str(url))
    # Some messages expose reply_markup rows but not .buttons.
    markup = getattr(message, "reply_markup", None)
    for row in getattr(markup, "rows", None) or []:
        for button in getattr(row, "buttons", None) or []:
            url = getattr(button, "url", None)
            if url:
                raw.append(str(url))

    results = []
    seen = set()
    for value in raw:
        value = html.unescape(value)
        for match in URL_RE.finditer(value):
            token = match.group().rstrip(TRAILING)
            if token.lower().startswith("www."):
                token = "https://" + token
            parsed = urlparse(token)
            if parsed.scheme not in ("https", "http") or not parsed.hostname:
                continue
            # Fragment anchors point into a document; skip duplicate fetches.
            token = parsed._replace(fragment="").geturl()
            if token not in seen:
                seen.add(token)
                results.append(token)
        for match in BARE_URL_RE.finditer(value):
            token = 'https://' + match.group().rstrip(TRAILING)
            parsed = urlparse(token)
            if parsed.hostname and token not in seen:
                seen.add(token)
                results.append(token)
        for match in MAGNET_RE.finditer(value):
            token = match.group().rstrip(TRAILING)
            if token not in seen:
                seen.add(token)
                results.append(token)
    return results


def name_safe(text, max_length=150):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(text)).strip(' .')[:max_length] or 'download'


def link_hash(url):
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:20]


def label_url(url):
    """Avoid printing query strings that may contain signed access tokens."""
    u = urlparse(url)
    return f"{u.hostname or 'unknown'}{(u.path or '/')[:90]}"


def disposition_filename(header):
    # Support both filename*=UTF-8''... and standard quoted filename.
    m = re.search(r"filename\*\s*=\s*(?:UTF-8''|utf-8'')[^;]*", header, re.I)
    if m:
        name = m.group().split('=', 1)[1].strip().split("''", 1)[-1]
        return name_safe(unquote(name.strip('"')))
    m = re.search(r'filename\s*=\s*(?:"([^"]+)"|([^;]+))', header, re.I)
    if m:
        return name_safe(m.group(1) or m.group(2).strip())
    return ''


def _result_path(url, response, folder):
    path = unquote(urlparse(response.url).path)
    orig = Path(path).name
    ctype = (response.headers.get('Content-Type') or '').split(';', 1)[0].lower().strip()
    name = disposition_filename(response.headers.get('Content-Disposition', '')) or name_safe(orig or 'download')
    if name.lower() in ('download', 'view', 'index', 'index.php', 'file', 'get', 'attachment') or '.' not in name:
        ext = mimetypes.guess_extension(ctype) or ''
        if ctype == 'text/html':
            ext = '.html'
        if ext and not name.lower().endswith(ext):
            name += ext
    return folder / f'{link_hash(url)}_{name_safe(name)}', ctype


class LinkDownloadError(Exception):
    pass


def download_http(url, links_dir, pause_event, cancelled, progress=None, max_mb=0,
                  max_attempts=3):
    """Stream a remote link, restart/resume range-enabled partials, atomically complete.

    The progress callback is called from a worker thread. No Tk calls here.
    Return {path, size, kind, url} once complete. On failure leave .part.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise LinkDownloadError('Only HTTP and HTTPS links can be downloaded automatically.')
    host = name_safe(parsed.hostname.lower(), 90)
    folder = Path(links_dir) / host
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / f'{link_hash(url)}.part'
    size_limit = max(0, int(max_mb or 0)) * 1024 * 1024
    last_error = None
    for attempt in range(1, max_attempts + 1):
        if cancelled():
            raise LinkDownloadError('Stopped by user; partial file retained.')
        while not pause_event.is_set():
            if cancelled():
                raise LinkDownloadError('Stopped by user; partial file retained.')
            time.sleep(0.15)
        offset = part.stat().st_size if part.exists() else 0
        headers = {'User-Agent': 'TelegramChannelDownloader/3.1 (+browser-compatible archive)',
                   'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        try:
            with requests.get(url, stream=True, timeout=(15, 35), allow_redirects=True,
                              headers=headers) as response:
                if response.status_code == 416 and offset:
                    part.unlink(missing_ok=True)
                    continue
                response.raise_for_status()
                final_url = urlparse(response.url)
                if final_url.scheme not in ('http', 'https'):
                    raise LinkDownloadError('Redirect to an unsupported URL scheme.')
                target, ctype = _result_path(url, response, folder)
                # A resume is valid only if the server honors the requested starting byte.
                cr = response.headers.get('Content-Range', '')
                supports_range = (response.status_code == 206 and
                                  cr.lower().startswith(f'bytes {offset}-'))
                if response.status_code == 206 and not supports_range:
                    raise LinkDownloadError('Server returned an unexpected byte range; will not corrupt the partial file.')
                base = offset if supports_range else 0
                length = response.headers.get('Content-Length')
                total = base + int(length) if length and length.isdigit() else 0
                if size_limit and total > size_limit:
                    raise LinkDownloadError(f'Link exceeds configured {max_mb} MB limit.')
                mode = 'ab' if supports_range else 'wb'
                done = base
                last_progress = 0.0
                with open(part, mode) as stream:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if cancelled():
                            raise LinkDownloadError('Stopped by user; partial file retained.')
                        while not pause_event.is_set():
                            if cancelled():
                                raise LinkDownloadError('Stopped by user; partial file retained.')
                            time.sleep(0.15)
                        if chunk:
                            done += len(chunk)
                            if size_limit and done > size_limit:
                                raise LinkDownloadError(f'Link exceeded configured {max_mb} MB limit.')
                            stream.write(chunk)
                            now = time.monotonic()
                            if progress and (now - last_progress > 0.3 or (total and done >= total)):
                                progress(done, total, target.name)
                                last_progress = now
                if done == 0:
                    raise LinkDownloadError('Server returned an empty response.')
                if total and done < total:
                    raise LinkDownloadError(f'Transfer interrupted: received {done}/{total} bytes.')
                os.replace(part, target)
                return {'path': str(target.resolve()), 'size': done,
                        'kind': 'webpage_snapshot' if ctype == 'text/html' else 'file',
                        'url': url}
        except LinkDownloadError:
            raise
        except (requests.RequestException, OSError) as exc:
            last_error = exc
            if attempt < max_attempts and not cancelled():
                # Retry transient network failures without dropping completed ranges.
                time.sleep(min(6, attempt * 2))
                continue
            break
    raise LinkDownloadError(f'HTTP transfer failed after {max_attempts} attempts: {last_error}')


def telegram_post_location(url):
    """Parse links to particular Telegram messages (not invite/channel homepages)."""
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if host not in ('t.me', 'telegram.me', 'www.t.me'):
        return None
    path = parsed.path.strip('/').split('/')
    if path and path[0] == 's':
        path = path[1:]
    if len(path) >= 3 and path[0] == 'c' and path[1].isdigit() and path[2].isdigit():
        return '-100' + path[1], int(path[2])
    if len(path) >= 2 and re.fullmatch(r'[a-zA-Z0-9_]{5,32}', path[0]) and path[1].isdigit():
        return path[0], int(path[1])
    return None


def provider_type(url):
    parsed = urlparse(url)
    domain = (parsed.hostname or '').lower().removeprefix('www.')
    path = parsed.path.lower()
    if domain in ('drive.google.com', 'docs.google.com') and (
        '/file/d/' in path or '/folders/' in path or '/uc' in path or '/open' in path
    ):
        return 'google_drive_folder' if '/folders/' in path else 'google_drive_file'
    if domain in ('youtube.com', 'm.youtube.com', 'youtu.be', 'music.youtube.com',
                  'tiktok.com', 'vimeo.com', 'soundcloud.com', 'dailymotion.com',
                  'bandcamp.com', 'twitch.tv', 'streamable.com'):
        return 'video_audio'
    if domain in ('mega.nz', 'mega.co.nz'):
        return 'unsupported_mega'
    return 'http'


def download_provider(url, links_dir, pause_event, cancelled, progress=None, max_mb=0):
    """Recognize common publicly accessible hosted downloads; HTTP is the fallback."""
    kind = provider_type(url)
    if kind == 'unsupported_mega':
        raise LinkDownloadError('MEGA links need an authorized MEGA client; link saved in manifest for manual download.')
    if kind.startswith('google_drive'):
        try:
            import gdown
        except ImportError as e:
            raise LinkDownloadError('Google Drive support unavailable: install gdown from requirements.txt.') from e
        folder = Path(links_dir) / 'drive.google.com' / link_hash(url)
        folder.mkdir(parents=True, exist_ok=True)
        if cancelled():
            raise LinkDownloadError('Stopped before Google Drive download.')
        while not pause_event.is_set():
            if cancelled():
                raise LinkDownloadError('Stopped before Google Drive download.')
            time.sleep(.15)
        try:
            if kind == 'google_drive_folder':
                result = gdown.download_folder(url=url, output=str(folder),
                                               quiet=True, remaining_ok=True)
                if not result:
                    raise LinkDownloadError('Drive folder is inaccessible or no files were downloaded.')
            else:
                result = gdown.download(url=url, output=str(folder) + os.sep,
                                        quiet=True, fuzzy=True, resume=True)
                if not result:
                    raise LinkDownloadError('Drive did not provide a public file. Check link permissions or quota.')
        except LinkDownloadError:
            raise
        except Exception as exc:
            raise LinkDownloadError(f'Google Drive download failed: {exc}') from exc
        files = [p for p in folder.rglob('*') if p.is_file() and not p.name.endswith('.part')]
        size = sum(p.stat().st_size for p in files)
        if not files:
            raise LinkDownloadError('No Google Drive files were saved.')
        if progress:
            progress(size, size, f'Google Drive: {len(files)} file(s)')
        return {'path': str((folder if len(files) > 1 else files[0]).resolve()), 'size': size,
                'kind': 'google_drive_folder' if len(files) > 1 else 'google_drive_file', 'url': url}
    if kind == 'video_audio':
        try:
            import yt_dlp
        except ImportError as exc:
            raise LinkDownloadError('Media host support unavailable: install yt-dlp from requirements.txt.') from exc
        folder = Path(links_dir) / name_safe(urlparse(url).hostname, 80)
        folder.mkdir(parents=True, exist_ok=True)
        prefix = link_hash(url) + '_'
        def hook(event):
            if cancelled():
                raise LinkDownloadError('Stopped by user while downloading media.')
            while not pause_event.is_set():
                if cancelled():
                    raise LinkDownloadError('Stopped by user while downloading media.')
                time.sleep(.15)
            if event.get('status') == 'downloading' and progress:
                progress(int(event.get('downloaded_bytes') or 0),
                         int(event.get('total_bytes') or event.get('total_bytes_estimate') or 0),
                         'Hosted media')
        options = {'outtmpl': str(folder / (prefix + '%(title).140B_%(id)s.%(ext)s')),
                   'noplaylist': True, 'quiet': True, 'no_warnings': True,
                   'progress_hooks': [hook], 'retries': 3,
                   'fragment_retries': 3, 'continuedl': True}
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                status = ydl.download([url])
            if status != 0:
                raise LinkDownloadError(f'yt-dlp returned failure status {status}.')
        except LinkDownloadError:
            raise
        except Exception as exc:
            raise LinkDownloadError(f'Hosted media download failed: {exc}') from exc
        files = [p for p in folder.glob(prefix + '*') if p.is_file() and
                 not p.name.endswith(('.part', '.ytdl'))]
        if not files:
            raise LinkDownloadError('Media downloader finished without an output file.')
        result = max(files, key=lambda p: p.stat().st_size)
        return {'path': str(result.resolve()), 'size': result.stat().st_size,
                'kind': 'hosted_media', 'url': url}
    if (urlparse(url).hostname or '').lower().endswith('dropbox.com'):
        from urllib.parse import parse_qsl, urlencode
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query['dl'] = '1'
        url = parsed._replace(query=urlencode(query)).geturl()
    return download_http(url, links_dir, pause_event, cancelled, progress, max_mb)
