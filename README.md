# v3.4 — Up to 100 Telegram channels per batch

The **Manage Channels / Paste List** dialog and **Import Channels TXT** now support **100 distinct channel handles or URLs** in one batch. Duplicate entries are removed. TXT lists with more than 100 unique channels are accepted up to the first 100 with an explicit warning; divide the remainder into another batch. A manually pasted list with more than 100 is rejected until edited.

**Channels active at once:** Set a number from **1 to 100** using the new numeric control (default **5**). The total channel count and active-worker count are different: a batch can contain 100 channels while processing five at a time. Higher settings can trigger Telegram FloodWait rate limits, significantly increase memory usage and reduce stability. Start with 3–5 active channels, and increase gradually only if your account and connection handle it. The global concurrent-file-download limit remains shared across all channels. The app uses one authorized Telegram session, keeps each channel's files and ZIP separate, and records failed channels for retry.

On a fresh Windows PC run **INSTALL.bat** first; it prepares Python 3.12 in an isolated virtual environment and makes a launcher. On existing installs you may retain your `.session` files, `config.json`, `download_state.json`, `google_drive_token.json` and original download directory when upgrading.

---

# Fresh-Windows installer (new)

Run **INSTALL.bat** first inside the extracted `Telegram_Channel_Downloader` folder. It detects or installs Python 3.12, sets up an isolated `.venv`, installs all requirements, verifies GUI libraries, creates a desktop shortcut and `START_DOWNLOADER.bat`, then launches the app. **Internet is required for initial setup.** On later launches, double-click `RUN_WINDOWS.bat` or `START_DOWNLOADER.bat`. This installer is not a bundled offline EXE; it uses official Python and PyPI package sources. Run it from a writable folder such as Downloads (not Program Files). It does not overwrite `config.json`, `.session` files, or `download_state.json`. Private Google Drive links still need your own Desktop OAuth client setup.

---

# Telegram Channel Downloader v3.4 — Multi-Channel Queue + Links + Google Drive

Windows source release; Python 3.12, Telethon, HTTP streaming, yt-dlp, gdown, Google Drive API (OAuth), and optional libtorrent. **The FULL PRIVATE ZIP retains the user's previously uploaded Telegram sessions, API configuration and download history. DO NOT SHARE THAT ZIP. A separate clean SOURCE ZIP has no user sessions, configuration, or link exports.**

## NEW v3.4: download several Telegram channels at the same time

**One sign-in; many channels.** The multi-channel worker reuses the same authorized Telegram client; it does not launch competing Telegram session SQLite databases, require separate logins for each channel, or create one Telegram account per channel. Channel jobs run concurrently while the number of active channels and total file transfers is capped.

### Add and manage channels

1. Open `RUN_WINDOWS.bat` and keep your working Telegram session/API settings. The original single **Primary channel** field still works.
2. Click **Manage Channels / Paste List**. Add any number of Telegram `@handles`, full `https://t.me/channelname` URLs or Telegram numeric channel peer IDs, one per line. The manager deduplicates entries; edit, remove, reorder, and save the list there.
3. Alternatively click **Import Channels TXT**. Choose a plain text file with one channel identifier/URL per line. This adds unique entries to your existing batch without overwriting it. See `CHANNELS_EXAMPLE.txt`.
4. Choose **Channels active at once**: 1–100 (default **5** for fresh settings). Set **Concurrent downloads** for the total number of simultaneous media/link transfers across all active channels (default from your saved settings). For large channels start conservatively; Telegram can impose rate limits.
5. Ensure **Output folder** is correct, choose the types you want, and click **Scan + Download Media + Links + ZIP**. You can also run a single channel as before.
6. The **Channel Queue** shows each channel's phase (Queued, Connecting, Scanning, Downloading, Zipping, Complete, Failed, Skipped or Stopped), progress and details. The bar beneath it shows overall item progress once scan counts are known. Pause/Resume/Stop apply across the batch.
7. Click **Open Downloads**. Every channel has a *separate* output directory, its own `Links/ALL_LINKS.txt` and `Links/FAILED_LINKS.txt`, and channel-specific ZIPs. The master output folder contains `CHANNEL_BATCH_REPORT.txt`, with each channel's outcome and any connection error. Access failure for one channel does **not** stop the others.

**Examples:** `@example_channel`, `https://t.me/public_channel`, `-1001234567890`. `t.me/+invite` links only work where your account already has the necessary access and Telegram can resolve the entity. The downloader does not join restricted groups, bypass channel access controls or scrape inaccessible content.

### Batch download structure

```text
Your Master Download Folder/
  CHANNEL_BATCH_REPORT.txt
  GLOBAL_ALL_LINKS.txt
  First Channel/
    <downloaded Telegram files>
    Links/ALL_LINKS.txt
    Links/FAILED_LINKS.txt
    Links/NEEDS_SIGN_IN.txt
    Links/LINKS_MANIFEST.csv
  First Channel.zip
  Second Channel/
    <downloaded Telegram files>
    Links/... (own manifests)
  Second Channel.zip
  Imported_Links/                   # when TXT links are enabled in a batch
    Links/ALL_LINKS.txt
    Links/FAILED_LINKS.txt
  Imported_Links.zip
```

**How TXT imports work in batches:** When you select several Telegram channels and keep **Include TXT links in every Telegram scan** checked, the TXT input is processed **once** for the batch in `Imported_Links/`, not repeated for every channel. With just one channel, the earlier behavior of importing TXT links into that channel is preserved. The separate **Download TXT Links (No Telegram Login)** button still works normally.

**History and resumability:** Each Telegram media item and link is tracked by its channel/entity ID in the unchanged `download_state.json` data format. Re-run the batch to retry incomplete/failed items. Smart History still skips items previously marked complete, even if you moved them elsewhere. Interrupted Telegram media is restarted from its beginning; some HTTP downloads resume if the server supports byte ranges. ZIPs are generated separately after each channel finishes.

**Notes:** The app scans channel message histories through Telegram's authorized API; it may take time to count items before percentage progress appears. Rate limits, revoked access, deleted messages, large libraries, slow third-party providers and disk space can delay or prevent a download. The channel-level phase is not a promise that every third-party external link can be fetched. Review each channel's failure manifest. Pausing applies immediately between downloads and while the HTTP downloader checks the pause event; it may not interrupt a Telegram download already in progress.

## Launch

1. Extract the ZIP **privately**. Open `Telegram_Channel_Downloader` and double-click `RUN_WINDOWS.bat` (Python 3.12 is required).
2. The installer checks for needed libraries and installs anything missing using `requirements.txt`. If installation fails, open a command prompt in this folder and run `py -3.12 -m pip install -r requirements.txt`.
3. The saved Telegram sessions and `config.json` are preserved from the previous full-private package. Select **Send Phone Login Code** when Telegram authorization is necessary (or choose QR). Telegram controls whether codes arrive in its service chat, via SMS, by email or other methods. The app cannot demand SMS delivery.
4. The **Output folder** field and **Download location** line identify exactly where files are written; click **Open Downloads** at any time. The output folder is created before the scan begins, including in TXT-only mode. The older configuration may point to a Windows `Downloads\Telegram_Channel_Downloader\downloads` path. Change it using **Browse** if you prefer a different disk.

## New: import the supplied channel export without Telegram login

The uploaded `content.txt` is included next to the launcher. In the GUI, the **Links TXT** field points to it. Click **Download TXT Links (No Telegram Login)** to process every extracted URL without connecting to Telegram. Google Drive API login is optional for public links and necessary for private/shared files that require your own Google account.

- `ALL_LINKS_FROM_CONTENT.txt` ships with **267 distinct URLs extracted from the supplied TXT**, ready before running the app.
- `GOOGLE_DRIVE_LINKS_FROM_CONTENT.txt` ships with **16 unique Google Drive links** from that same export.
- The importer reads links with `http://`, `https://`, `www.`, and plain domain/path forms. It deduplicates URLs. Mentions like `LINK` without an actual URL cannot be resolved from a text export.
- For full channel downloads, keep **Include TXT links in every Telegram scan** enabled and click **Scan + Download Media + Links + ZIP**; both the Telegram history and TXT URLs join the same queue.
- HTML webpages are saved as page snapshots. A website link does not automatically mean all files linked from that page can be retrieved. Supported media services can save hosted video/audio via yt-dlp; public Google Drive via gdown; downloadable direct HTTP(S) URLs are streamed. No CAPTCHA, login, private-channel, DRM, or access-control bypass is attempted.

## Where files and link records appear

After a channel scan, the output resembles:

```
Output folder (your chosen path)/
  GLOBAL_ALL_LINKS.txt                  # deduplicated URLs across all runs and channels
  Channel Name/
    Telegram media files ...
    ALL_LINKS.txt                       # easy-to-find channel-level index
    FAILED_LINKS.txt
    Links/
      ALL_LINKS.txt                     # one URL per line, including unsupported sites
      FAILED_LINKS.txt                  # failures can be retried later
      FAILED_LINKS_DETAILS.txt          # URL plus error text, tab-separated
      NEEDS_SIGN_IN.txt                 # possible authorization / permission failures
      LINKS_MANIFEST.csv                # source message IDs, type, state, path, error
      drive.google.com/                 # Drive downloads (files and folder trees)
      github.com/ ...                   # other host downloads
  Imported_Links/                       # TXT-only mode records and files
    ALL_LINKS.txt
    FAILED_LINKS.txt
    Links/
      ALL_LINKS.txt
      FAILED_LINKS.txt
      FAILED_LINKS_DETAILS.txt
      NEEDS_SIGN_IN.txt
      LINKS_MANIFEST.csv
      <per-host directories>/
  Imported_Links.zip                    # ZIP archive if any files are created
```

`ALL_LINKS.txt` is written **before downloading**. Each completed or failed URL updates TXT/CSV records, even if the application stops before the end. Previously finished links are tracked in `download_state.json` and skipped in subsequent runs by Smart History, including if you retry the `FAILED_LINKS.txt` by selecting it as input. HTML pages, unavailable files, quota-limited links, and restricted sites stay in the manifests with specific errors.


### v3.2.1 Google sign-in setup fix

- The Google credential entry no longer shows a nonexistent `google_oauth_client.json` by default. A path in a field never means a credential is present. The **Google OAuth Setup Guide** button opens Google's official setup instructions.
- The first sign-in without a saved token prompts you to select a real `client_secret_....json` file downloaded from Google Auth Platform **Clients > Create Client > Desktop app**. Invalid service-account, API-key, and malformed JSON files are explained before trying authentication.
- A previously saved `google_drive_token.json` can be checked or refreshed without requiring the Desktop JSON again when its token/refresh token remains valid. If Google requires a brand-new authorization, the Desktop JSON is still needed.
- Google login is separate from Telegram login: your Telegram session remains available and TXT/direct/public Google Drive downloads continue without OAuth.

## Google Drive API: sign in using your own account

**There is no universal Google Drive API key or shared OAuth client bundled in this package.** For private and shared links, create your *own* OAuth Desktop App client. This is the documented Google OAuth flow. The app requests only the read-only Drive scope and can access only files that your Google account is permitted to access.

1. Go to [Google Cloud Console](https://console.cloud.google.com/), create/select a project, and enable **Google Drive API** in APIs & Services.
2. Under **Google Auth Platform**, configure the consent screen (Branding, Audience, Data Access). For a personal Testing app, add your Google account as a **test user** if requested. Some Google Drive OAuth scopes are restricted; publication can require verification.
3. Under Clients, create an **OAuth client ID → Desktop app**. Download its JSON file (typically `client_secret_....json`).
4. In the downloader, click **Choose JSON** and select that file. Click **Sign In to Google Drive**. Your default browser opens Google's consent page and uses a temporary **127.0.0.1** redirect, not a third-party password form.
5. Grant read-only Drive permission. The app saves a private local `google_drive_token.json` to reuse and refresh your session. You do **not** enter a Google password into the downloader.
6. Click **Download TXT Links** or **Scan + Download Media + Links + ZIP** again. If the token is saved, the app tries the authorized Drive API first; if that fails, it tries public gdown access. Accessible folders are downloaded recursively. Google Docs/Sheets/Slides are exported as PDF/XLSX/PPTX where supported.

**To use another Google account:** close the downloader, move `google_drive_token.json` to a secure backup outside the app folder, reopen, and click **Sign In to Google Drive**. Never share the OAuth token or the client JSON. Browser sign-in and real Drive API downloads must be performed on your machine: no accounts are signed in during package creation.

## Limitations and troubleshooting

- Telegram attachments require access to the source channel using a valid Telegram session. Links to Telegram posts in TXT-only mode are logged as needing Telegram login if they refer to restricted post media. Switch to normal Telegram scan mode with the authorized session.
- Google Drive files return `403`/`404` when the signed-in account lacks permission or the link is unavailable; API authorization does not override sharing restrictions or abuse limits.
- Some sites (e.g. MEGA) require their own authorized client and remain in the failed-link TXT. Other sites use login pages, anti-bot protections or non-direct URLs. They will not be silently discarded; retries use your saved TXT.
- The importer does **not** open downloaded programs, scripts, archives, or macros. Treat content from public channels as untrusted and scan it before opening.
- For file-host downloads that are not directly accessible, the output may be an HTML page snapshot instead of the archive behind it. Inspect `LINKS_MANIFEST.csv` to see its `webpage_snapshot` type.
- In the event of crash, `download_state.json` and `.part` files help avoid duplicate transfers; HTTP downloads can resume if a server accepts Range requests. Telegram downloads retry incomplete messages from the start because arbitrary Telegram stream offsets cannot always be resumed safely.
- If Windows Security warns about an executable downloaded from an untrusted site, do not disable protection. Review the source and file before using it.

## Security and private sessions

This **full private package** retains your original session databases, saved API ID/hash, existing history, and backups from the prior private release. Keep it only on a trusted computer. Anyone who gets a working `.session` file may be able to access your Telegram account. The package is not encrypted. Do not publish it on GitHub or share it in public chats. Downloaded files, OAuth tokens you create later, and third-party account sessions are not bundled.

## Evidence of testing

Source syntax compilation passed. Offline tests passed for TXT-only mode with blank Telegram credentials, link extraction, correct output directory creation, successful transfers, failed-link/needs-sign-in TXT files, ZIP creation, Smart History, Google Drive URL parsing, and an actual HTTP download from a local server. v3.4 offline tests passed for concurrent channel scanning, shared global download limit, isolated folders/ZIPs, resilience to one inaccessible channel, same-title folder collisions, multi-run Smart History and TXT-link import only once per batch. The Tk GUI was launched and exercised under a virtual desktop display (window, channel queue updates, dialog, and config collection). Live Windows Telegram connection, Google browser consent, and downloads from external hosts were **not** exercised during package creation. An API client JSON must be supplied by the user for private Google Drive access.
