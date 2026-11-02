"""Authorized Google Drive v3 downloads for Telegram Downloader.

Google's desktop OAuth flow requires a user-provided Desktop OAuth client JSON
created in Google Cloud Console with Drive API enabled. No shared app secret or
bearer token is bundled. Read-only scope, local browser consent, token refresh.
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from link_downloader import LinkDownloadError, name_safe, link_hash

SCOPE = ['https://www.googleapis.com/auth/drive.readonly']
FOLDER_MIME = 'application/vnd.google-apps.folder'
SHORTCUT_MIME = 'application/vnd.google-apps.shortcut'
EXPORTS = {
    'application/vnd.google-apps.document': ('application/pdf', '.pdf'),
    'application/vnd.google-apps.spreadsheet': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
    'application/vnd.google-apps.presentation': ('application/vnd.openxmlformats-officedocument.presentationml.presentation', '.pptx'),
    'application/vnd.google-apps.drawing': ('application/pdf', '.pdf'),
}


def google_link_id(url):
    p = urlparse(url)
    host = (p.hostname or '').lower().removeprefix('www.')
    if host not in ('drive.google.com', 'docs.google.com'):
        return None
    patterns = [r'/file/d/([\w-]+)', r'/folders/([\w-]+)', r'/(?:document|spreadsheets|presentation|drawings)/d/([\w-]+)']
    for pattern in patterns:
        match = re.search(pattern, p.path, flags=re.I)
        if match:
            return match.group(1)
    if p.path in ('/open', '/uc', '/thumbnail'):
        return parse_qs(p.query).get('id', [None])[0]
    return None


def validate_desktop_client_json(client_path):
    """Validate a Google Cloud *Desktop app* OAuth credential without logging secrets."""
    if not client_path:
        raise LinkDownloadError('No Google Desktop OAuth JSON selected. Use Choose JSON first.')
    path = Path(client_path).expanduser()
    if not path.is_file():
        raise LinkDownloadError(f'Google OAuth JSON does not exist at the selected path: {path.name}. '
                                'Download a Desktop OAuth client JSON from Google Cloud and click Choose JSON.')
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LinkDownloadError('Selected file is not valid JSON. Download the client JSON from Google Cloud.') from exc
    if not isinstance(data, dict) or 'installed' not in data or not isinstance(data['installed'], dict):
        raise LinkDownloadError('This is not a Desktop OAuth client JSON. In Google Cloud use '
                                'Google Auth Platform > Clients > OAuth Client ID > Desktop app. '
                                'Service-account and API-key JSON files will not work.')
    credentials = data['installed']
    for key in ('client_id', 'client_secret', 'auth_uri', 'token_uri'):
        if not isinstance(credentials.get(key), str) or not credentials[key].strip():
            raise LinkDownloadError(f'Google OAuth JSON is incomplete: missing {key}. '
                                    'Download the original Desktop client JSON again.')
    if not credentials['client_id'].endswith('.apps.googleusercontent.com'):
        raise LinkDownloadError('The OAuth client ID does not look like a Google Desktop OAuth client. '
                                'Choose the JSON downloaded from Google Auth Platform.')
    return path


def google_dependencies():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
        return Request, Credentials, InstalledAppFlow, build, MediaIoBaseDownload
    except ImportError as e:
        raise LinkDownloadError('Google API libraries missing. Run RUN_WINDOWS.bat to install requirements.') from e


def _credentials(token_path, client_path, interactive):
    Request, Credentials, InstalledAppFlow, _, _ = google_dependencies()
    token_path = Path(token_path)
    client_path = Path(client_path)
    creds = None
    if token_path.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPE)
        except Exception as exc:
            raise LinkDownloadError(f'Invalid Google login token; use Reconnect Google Drive. {type(exc).__name__}') from exc
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            if not interactive:
                raise LinkDownloadError('Google session expired or revoked. Click Sign In to Google Drive again.') from exc
            creds = None
    if not creds or not creds.valid:
        if not interactive:
            raise LinkDownloadError('Google Drive sign-in needed. Click Sign In to Google Drive and retry this link.')
        validated_client = validate_desktop_client_json(client_path)
        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(validated_client), SCOPE)
            creds = flow.run_local_server(host='127.0.0.1', port=0, open_browser=True,
                                          authorization_prompt_message='Google sign-in page: {url}',
                                          success_message='Google Drive authorization complete. Return to Telegram Downloader.')
        except Exception as exc:
            raise LinkDownloadError(f'Google browser sign-in failed: {type(exc).__name__}: {exc}') from exc
    token_path.parent.mkdir(parents=True, exist_ok=True)
    # Use restrictive file permissions on POSIX; Windows credentials should stay on private user disk.
    data = creds.to_json()
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(data)
    return creds


def google_sign_in(client_path, token_path):
    _, _, _, build, _ = google_dependencies()
    creds = _credentials(token_path, client_path, interactive=True)
    service = build('drive', 'v3', credentials=creds, cache_discovery=False)
    # Exercise the session so an OAuth-only success isn't mistaken for API access.
    service.about().get(fields='user(displayName,emailAddress)').execute()
    return 'Google Drive connected (read-only access).'


def has_token(token_path):
    return Path(token_path).is_file()


def _await(pause_event, cancelled):
    if cancelled():
        raise LinkDownloadError('Stopped by user; partial download retained.')
    while not pause_event.is_set():
        if cancelled():
            raise LinkDownloadError('Stopped by user; partial download retained.')
        time.sleep(0.2)


def download_google_api(url, links_dir, pause_event, cancelled, progress,
                        max_mb, token_path, client_path):
    """Download a single shared Drive file or a folder tree using authorized v3 API.

    Public links work without OAuth via gdown (outside this function); API handles
    private/shared files that the user's chosen Google account can access.
    """
    _, _, _, build, MediaIoBaseDownload = google_dependencies()
    file_id = google_link_id(url)
    if not file_id:
        raise LinkDownloadError('Google Drive link does not contain a supported file/folder ID.')
    creds = _credentials(token_path, client_path, interactive=False)
    service = build('drive', 'v3', credentials=creds, cache_discovery=False)
    root = Path(links_dir) / 'drive.google.com' / link_hash(url)
    root.mkdir(parents=True, exist_ok=True)
    size_limit = int(max_mb or 0) * 1024 * 1024
    total_bytes = 0
    n_files = 0
    visited = set()

    def download_item(fid, dest, depth=0):
        nonlocal n_files, total_bytes
        _await(pause_event, cancelled)
        if depth > 32:
            raise LinkDownloadError('Drive folder nesting exceeds the safety limit (32).')
        if fid in visited:
            return
        visited.add(fid)
        meta = service.files().get(fileId=fid, fields='id,name,mimeType,size,shortcutDetails,trashed',
                                   supportsAllDrives=True).execute()
        if meta.get('trashed'):
            return
        mime = meta.get('mimeType', '')
        if mime == SHORTCUT_MIME:
            next_id = (meta.get('shortcutDetails') or {}).get('targetId')
            if next_id:
                download_item(next_id, dest, depth + 1)
            return
        filename = name_safe(meta.get('name') or fid)
        if mime == FOLDER_MIME:
            folder = dest / filename
            folder.mkdir(exist_ok=True, parents=True)
            token = None
            while True:
                _await(pause_event, cancelled)
                page = service.files().list(q=f"'{fid}' in parents and trashed = false",
                    fields='nextPageToken,files(id,name,mimeType,size)', pageSize=1000,
                    pageToken=token, supportsAllDrives=True,
                    includeItemsFromAllDrives=True).execute()
                for child in page.get('files', []):
                    download_item(child['id'], folder, depth + 1)
                token = page.get('nextPageToken')
                if not token:
                    break
            return
        export_mime = EXPORTS.get(mime)
        if mime.startswith('application/vnd.google-apps.') and not export_mime:
            raise LinkDownloadError(f'Google Workspace file format is not exportable: {mime}')
        if export_mime:
            out_name = filename + export_mime[1]
            request = service.files().export_media(fileId=fid, mimeType=export_mime[0])
        else:
            out_name = filename
            request = service.files().get_media(fileId=fid, supportsAllDrives=True)
        target = dest / out_name
        if target.exists() and target.stat().st_size > 0:
            n_files += 1
            total_bytes += target.stat().st_size
            return
        dest.mkdir(parents=True, exist_ok=True)
        partial = dest / (out_name + '.part')
        if partial.exists():
            partial.unlink()  # Drive API MediaIoBaseDownload restarts the item safely.
        import io
        with partial.open('wb') as f:
            downloader = MediaIoBaseDownload(f, request, chunksize=4 * 1024 * 1024)
            done = False
            while not done:
                _await(pause_event, cancelled)
                status, done = downloader.next_chunk(num_retries=3)
                if progress and status:
                    progress(total_bytes + int(status.resumable_progress),
                             total_bytes + int(status.total_size or 0), out_name)
                if size_limit and (total_bytes + f.tell()) > size_limit:
                    raise LinkDownloadError(f'Google Drive link exceeds {max_mb} MB limit.')
        if not partial.stat().st_size:
            raise LinkDownloadError(f'Google Drive returned an empty file for {filename}.')
        os.replace(partial, target)
        n_files += 1
        total_bytes += target.stat().st_size

    try:
        metadata = service.files().get(fileId=file_id, fields='id,name,mimeType',
                                       supportsAllDrives=True).execute()
        download_item(file_id, root)
    except LinkDownloadError:
        raise
    except Exception as exc:
        # Avoid logging potentially sensitive authorization headers or signed URLs.
        code = getattr(getattr(exc, 'resp', None), 'status', None)
        if code in (401, 403):
            raise LinkDownloadError('Google Drive denied access (401/403). Sign in with an account permitted to view the file/folder, or check sharing permissions.') from exc
        if code == 404:
            raise LinkDownloadError('Google Drive file/folder was not found or is not shared with the signed-in account (404).') from exc
        raise LinkDownloadError(f'Google Drive API error: {type(exc).__name__}: {str(exc)[:160]}') from exc
    if n_files == 0:
        raise LinkDownloadError('Google Drive folder contained no downloadable files.')
    if progress:
        progress(total_bytes, total_bytes, f'Google Drive: {n_files} files')
    return {'path': str(root.resolve()), 'size': total_bytes,
            'kind': 'google_drive_folder' if metadata.get('mimeType') == FOLDER_MIME else 'google_drive_file',
            'url': url}
