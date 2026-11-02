# Telegram Channel Downloader v3.4

**Multi-Channel Archiver • Media Downloader • Link Collector • Google Drive Integration • TXT Bulk Import • Windows Installer**

> **Version:** 3.4 — 100-Channel Batch + Google Drive  
> **Platform:** Windows 10/11 desktop  
> **Runtime:** Python 3.12, Tkinter, Telethon, and optional provider libraries  
> **Distribution:** Source application with `INSTALL.bat` (not a precompiled offline EXE)  
> **Important:** Only download material that you are authorized to access and retain. Telegram and external providers enforce their own access, copyright, and usage rules.

Telegram Channel Downloader is a desktop application designed to save media from Telegram channels and collect downloadable links mentioned in their posts. It can process one channel, maintain a batch of up to **100 channels**, or download from a plain-text list of external URLs without signing into Telegram. It organizes media, generates searchable URL records, prepares ZIP archives, and stores completion history for later runs. The application also offers a separate Google Drive OAuth login for Drive links accessible to your account.

The project is intended for archiving and research workflows, not for bypassing permissions or automatically executing downloaded files. Links that cannot be retrieved remain documented, so an unsuccessful download does not mean the original reference is lost. The application works best as a managed archive: review its progress display and reports, preserve its saved state, and adjust concurrency to suit Telegram's limits and your computer.

---

## Contents

1. What the program does
2. Packages and privacy
3. Requirements
4. Fresh Windows installation
5. Opening the application
6. Understanding the interface
7. Telegram API setup and login
8. Single-channel downloads
9. Managing up to 100 channels
10. Performance and concurrency
11. Choosing downloadable media
12. Automatic external-link discovery
13. Importing URL lists from TXT
14. Google Drive authentication and downloads
15. Download locations and ZIP organization
16. History, duplicates, retries, and pausing
17. Reports and manifests
18. Optional torrent support
19. Upgrading and moving computers
20. Troubleshooting
21. Security and privacy
22. Limitations and verification
23. Frequently asked questions
24. Quick-start checklists

---

## 1. What the program does

The downloader connects through the **authorized Telegram account API**, using Telethon. Once a Telegram session has been authorized, it can inspect message history in channels available to that account. Depending on the checkboxes you enable, it downloads photos, videos, music, documents, voice messages, and stickers. It also discovers website links appearing in message text, captions, hyperlink entities, and URL buttons. These external references form an independent download queue.

In v3.4, a batch can contain up to **100 unique channel identifiers**. The number of channels in the batch is not the same as the number running concurrently. You can queue 100 channels but process five at a time, which is a practical default. Each channel has its own destination, progress status, link records, and ZIP archives. One inaccessible channel is recorded as failed rather than intentionally terminating the entire batch.

There are three principal ways to work:

- **Telegram scan:** Authenticate, select one or several accessible channels, and scan their message histories for media and links.
- **TXT-only download:** Supply a `.txt` file containing URLs and download reachable content without connecting to Telegram.
- **Combined scan:** Scan Telegram channels while importing the supplied TXT URL list into the same run. In multi-channel mode, the imported TXT collection is processed once for the batch, not once per channel.

This is a downloader and archive manager, **not** a complete mirror of every site referenced in a Telegram post. A direct downloadable file can be saved; an ordinary webpage may be saved as an HTML snapshot; a protected host may require a separate browser login or decline the request. The original URL is retained in the records regardless of these outcomes.

## 2. Packages and privacy

Two release variants have been distributed for v3.4. **Full Private** contains the owner's preserved session databases, saved Telegram API configuration, download state, and historical settings. Treat it like a credential-bearing backup. Anyone receiving a usable Telegram `.session` file may be able to access that account. Never post this package to a public repository, file host, forum, or Discord channel.

**Clean Source** contains application code, dependencies, installer, tests, and documentation, without the owner's account sessions and private configurations. It is the appropriate starting point for a fresh user or a source-code review. The installer is included in the release packages. Do not assume that a clean-source ZIP contains previously imported archives such as `content.txt`; choose your own TXT file if needed.

Neither package is an offline Windows EXE. `INSTALL.bat` prepares the Python environment and launches the desktop application. Passwords, Telegram codes, Google tokens, and OAuth JSON files should never be pasted into public support messages. Screenshots should conceal phone numbers, API IDs and hashes, and local access tokens where relevant.

## 3. System requirements

A Windows 10 or Windows 11 desktop computer is the primary target. Python **3.12** is used by the Windows installer. The application uses Tkinter for the interface and therefore needs a Python distribution with Tcl/Tk available. A working internet connection is needed to install dependencies, authenticate Telegram, query external websites, and retrieve hosted content. The initial installation is not intended to work entirely offline.

You also need enough available disk space for the downloaded media **and** its generated ZIP copies. A collection containing many large files may temporarily require considerably more disk capacity than the final archive alone. Large batches benefit from a reliable disk, a steady network connection, and sufficient RAM. Avoid beginning a large 100-channel operation on a nearly full drive.

The included `requirements.txt` lists Telethon, QR/image support, Requests, gdown, yt-dlp, and Google Drive OAuth/API libraries. A separate, **optional** `libtorrent` dependency is relevant only if you intend to use the torrent interface. It is not required for regular Telegram and HTTP downloads. The application uses the standard Windows Tkinter GUI instead of a browser-based frontend.

## 4. Fresh Windows installation

First extract the ZIP to a normal writable directory, for example `C:\Telegram_Channel_Downloader` or a folder in your Downloads directory. Keep the application files together. Open the folder that contains `telegram_channel_downloader.py`, `requirements.txt`, and `INSTALL.bat`, then double-click **`INSTALL.bat`**.

The installer searches for Python 3.12. If it is missing, it attempts installation using Windows Package Manager (`winget`); when needed, it attempts to retrieve a Python 3.12 installer from python.org and validate its digital signature. Network policies, security software, platform architecture, or provider availability can prevent this step. If automatic installation fails, install Python 3.12 through the official Python website and rerun the batch installer.

Next, `INSTALL.bat` creates a private `.venv` directory inside the application folder and installs packages from `requirements.txt` into that environment. This avoids mixing the downloader's libraries with unrelated Python projects. It verifies that Tkinter and the required packages can be imported, writes **`INSTALL_LOG.txt`**, creates **`START_DOWNLOADER.bat`**, attempts to add a desktop shortcut, and starts the application.

If installation fails, read `INSTALL_LOG.txt` before trying random fixes. The setup script normally does not need administrator privileges when installing Python for the current user in a writable folder. Avoid installing inside protected directories such as `Program Files` unless you understand their permission requirements. Never delete working session databases merely to repair a Python package issue.

### Manual dependency installation

If you already have Python 3.12 and prefer manual setup, open Command Prompt in the application folder and run:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe telegram_channel_downloader.py
```

This is an alternative to `INSTALL.bat`, not an additional mandatory step. It assumes that the Python launcher can find Python 3.12 and that Tcl/Tk is installed. If you copy the project to another computer, recreate `.venv` on the destination instead of copying a machine-specific virtual environment.

## 5. Opening the application

On a machine where the installer has succeeded, start the downloader with **`START_DOWNLOADER.bat`** or its desktop shortcut. The included **`RUN_WINDOWS.bat`** provides another launch path; installed environments should use the isolated virtual environment created during setup. The main Python file is `telegram_channel_downloader.py`.

The interface provides Telegram connection fields at the top, followed by a multi-channel batch manager, media-type filters, link options, TXT and Google Drive controls, overall run buttons, individual channel progress, transfer activity, and an optional torrent section. A large display or a maximized window makes all controls easier to see. The first time you run the clean package, you will need your own Telegram API settings and account authorization.

If the interface opens but nothing is being downloaded, that alone is not an error. Choose a channel or TXT file, select content types, and start the corresponding workflow. A successful Telegram login does not automatically start scanning; login and downloading are separate actions.

## 6. Understanding the main controls

**API ID** and **API Hash** identify your Telegram API application. **Primary channel** supports a single-channel workflow and also acts as the first entry in the managed batch list. **Output folder** selects the root directory for actual files, reports, and ZIPs. **Concurrent downloads** controls the shared number of media/link transfers across all active channels. **ZIP split size (MB)** influences how downloaded files are grouped into ZIP archives.

The **Mobile number** field expects an international number beginning with `+` and a country code. The login-method dropdown offers a phone-code flow or QR flow. **Send Phone Login Code** explicitly chooses the phone workflow, whereas **Use QR Instead** explicitly chooses the QR workflow. These are independent of Google Drive sign-in.

**Manage Channels / Paste List** opens the batch editor; **Import Channels TXT** reads channel identifiers from a file. **Channels active at once (1–100)** controls channel-job parallelism and defaults to five. Under the media filters, checkboxes determine which types of Telegram attachments are downloaded. Other controls govern automatic external-link fetching, a maximum HTTP link file size, whether imported TXT links join Telegram scans, and Smart History.

At the bottom, **Scan + Download Media + Links + ZIP** starts a Telegram scan, while **Download TXT Links (No Telegram Login)** starts a standalone URL import. **Pause**, **Resume**, and **Stop** control an active archive job. **Open Downloads** opens the currently selected output location. The Channel Queue reports each channel separately and should be the first place you look when a large batch seems stalled.

## 7. Getting Telegram API credentials and signing in

To access Telegram through Telethon, create or use your own Telegram application credentials from **https://my.telegram.org**. Enter the returned API ID and API hash in the corresponding fields. These values are not the same as a bot token. The application signs into a Telegram **user account**, so that account's channel memberships and permissions determine what can be retrieved.

Enter your number with a `+` country prefix. Click **Send Phone Login Code**, or use **Sign in (selected method)** after selecting Phone login code. Telegram may deliver the code to the verified Telegram service chat in an already authorized device, by SMS, by email, or through another supported mechanism. **The downloader cannot force SMS delivery**: Telegram controls the destination and whether alternative delivery is offered. Check the Activity log to learn where Telegram reports sending the code.

When prompted, enter the latest verification code. If your account has two-step verification, enter the Telegram account password in the separate dialog. Do not confuse this with the code itself. Requesting many codes repeatedly may cause delays or temporary rate limits. If your existing session works, you may not need another code at all.

QR login is an alternative if another Telegram app can approve a linked device. It is not mandatory. Previously saved session files are checked for reuse before new authorization is requested, and a successful authorization creates a reusable local session. Keep those session files secret; do not share them as an easy way to avoid signing in on someone else's computer.

## 8. Downloading a single channel

For one channel, enter an accessible `@channelname` or `https://t.me/channelname` URL in **Primary channel**. You may also use a Telegram peer ID when your session can resolve it. Choose a destination with **Browse**, select desired media types, decide whether external URLs should be downloaded, and click **Scan + Download Media + Links + ZIP**.

The application resolves the Telegram channel, scans accessible message history, identifies matching media, and queues downloads. At different times the channel may show Connecting, Scanning, Downloading, or Zipping. The initial scan can take a while, and percentage progress may not appear until counts are known. Once processing finishes, media is stored in the channel's folder and corresponding ZIP output is generated.

Your Telegram account must already be entitled to access the channel. A private invite URL does not guarantee access, and the downloader does not automatically join restricted communities, defeat deleted-message restrictions, or retrieve material blocked by Telegram. If a channel fails, its error is preserved; check the activity messages and batch report rather than assuming every download failure is caused by your internet connection.

## 9. Downloading multiple channels: up to 100

Version 3.4 supports **100 unique channel entries in one batch**. Click **Manage Channels / Paste List** and enter one identifier on each line. The editor accepts Telegram handles, public `t.me` links, and numeric peer IDs. It removes exact-style duplicates while preserving list order. Use **Save Channel List** to keep the batch configuration.

Example:

```text
@channel_alpha
https://t.me/channel_beta
@channel_gamma
-1001234567890
```

You can also prepare a TXT file using the same format and click **Import Channels TXT**. Imported entries are added to the existing queue. If the combined list contains more than 100 distinct channels, the application warns you and keeps the first 100 imported/combined entries. Split the remainder into another batch. If you paste more than 100 into the manager itself, it asks you to reduce the list before saving.

Choose a reasonable value for **Channels active at once** and press the main scan button. The program uses one authorized Telegram client across concurrent channel jobs rather than creating 100 competing session databases. Each channel gets a row in the Channel Queue, with its current stage, approximate progress when available, and an error or status message. A failure resolving one channel should not, by design, block the others.

Batch entries can be edited between runs. A channel title may differ from its `@handle`, so output folders use safe names and the program handles certain title collisions. Afterward, consult `CHANNEL_BATCH_REPORT.txt` in the root output directory to identify completed, failed, skipped, and stopped jobs.

## 10. Choosing sensible concurrency

**Channels in batch** is the number of jobs requested; **Channels active at once** is the number permitted to work concurrently; **Concurrent downloads** is the shared limit on individual media and external-link transfers. These settings influence different parts of the workflow. For example, you can queue 100 channels, permit five active channel workers, and limit transfers to two at a time across those workers.

Although the selector permits 100 simultaneously active channels, that is a technical limit, **not a recommended operating mode**. More parallel scans increase API activity and can lead to Telegram FloodWait conditions, account-level limits, temporary disconnects, high memory usage, and an overloaded disk. Begin with three to five channel workers and a conservative transfer count. Only raise concurrency after seeing stable performance over a representative run.

Large channels can contain many years of message history. Scanning is not guaranteed to complete quickly, especially when Telegram responds slowly or third-party links require retries. You may see uneven progress: one channel can complete while another remains in Scanning or Downloading. This is expected for mixed-size collections. Do not interpret an unchanged overall percentage as proof that all workers have stalled.

If performance degrades, stop gracefully, reduce the worker count, and restart the batch. Successful items recorded in Smart History will generally be skipped next time. Avoid using repeated sign-in attempts or switching Telegram sessions to solve rate-limiting issues; those actions do not increase an account's legitimate Telegram access rights.

## 11. Telegram media types and filters

The media section offers individual checkboxes for **Photos**, **Videos**, **Audio / music**, **Files / documents / archives**, **Voice notes**, and **Stickers**. Select only what you need. For example, a research archive may prioritize documents while a music-production channel may prioritize audio and sample archives. Disabling unnecessary categories reduces storage consumption and transfer time.

The file naming process attempts to retain useful original names and adds message identifiers to reduce collisions. Files are written to their channel directory, not automatically extracted or opened. Media that does not match your enabled filters is not saved merely because it exists in the channel. If the same message contains a URL, its link may still be discovered independently when automatic external-link downloading is enabled.

The **Maximum file size for HTTP links (MB)** option is specific to external HTTP downloads. A value of `0` means unlimited, so monitor disk space when importing unknown links. It should not be read as a global limit on every Telegram attachment or every provider. Some remote sites enforce their own file-size, quota, time, or authentication policies.

## 12. Automatic external-link discovery

Enable **Auto-download ALL links found in channel messages, captions, and URL buttons** to find references beyond Telegram-hosted attachments. The collector also examines supported hidden hyperlink entities and certain bare domain/path patterns. Discovered URLs are deduplicated within the relevant collection and recorded before download attempts. This means a link remains searchable even when its host is unavailable later.

Direct HTTP and HTTPS files are streamed into host-specific subfolders under `Links`. Pages that are not direct file downloads may be saved as `.html` snapshots rather than recursively crawled. Certain supported media sites can be handled through yt-dlp, and publicly shared Google Drive files can be attempted through gdown. Authenticated Drive API access is covered separately below.

A URL in a Telegram message is not necessarily a file. It might be a homepage, a download portal, a private page, a Git repository, or a page requiring a CAPTCHA. The program does **not** promise to download everything linked by every webpage, clone all GitHub repositories, bypass a paywall, or solve sign-in requirements automatically. Use `LINKS_MANIFEST.csv` to distinguish an actual downloaded file from a saved webpage snapshot.

Telegram post links may need the Telegram session and an accessible source message. Such links can fail in TXT-only mode because that mode deliberately does not connect to Telegram. For protected links, switch to the normal authorized scan if the relevant channel is accessible to your account.

## 13. TXT bulk link imports

The **Links TXT** field accepts an exported text file such as `content.txt` or a list of URLs you prepared. Click **Choose TXT** to select the file. For an independent download, click **Download TXT Links (No Telegram Login)**. The application reads recognizable URLs, removes duplicate references, creates the output folder, and stores results beneath `Imported_Links/`.

Example TXT:

```text
https://example.org/files/document.pdf
https://drive.google.com/file/d/EXAMPLE_ID/view
https://github.com/example-owner/example-repository
www.example.net/downloads/archive.zip
```

This is a **URL list**, not the multi-channel handle list described earlier. Use **Import Channels TXT** for Telegram channel handles and **Choose TXT** for external URLs. Mixing these workflows can produce confusing outcomes. Generic text like `CLICK HERE` or `LINK` cannot be converted into a real URL unless its underlying address was captured in the export.

When **Include TXT links in every Telegram scan** is checked, the current TXT file is also processed during normal channel work. With a single channel, it follows the original channel-associated behavior; with a multi-channel batch, imported TXT links are processed once in a separate `Imported_Links` folder. This prevents 100 channel jobs from repeating the same external URL download.

A TXT containing GitHub repository URLs is accepted as a set of web addresses, but **this downloader is not the specialized GitHub Archive Studio search and ZIP application**. A repository page may be saved as HTML, not as its full source ZIP. To mass-download GitHub repository archives, provide direct ZIP archive URLs or use the separate GitHub-focused tool.

## 14. Google Drive OAuth and permitted downloads

Google Drive has two distinct access routes. A **publicly shared Drive URL** may be downloadable without Google login through the available public-link method. A **private or account-shared Drive file** generally requires an OAuth-authorized Google account with permission to access that exact file. The app's Google sign-in is separate from Telegram login and does not reuse Telegram credentials.

To configure the Drive API, create a project in **Google Cloud Console**, enable the **Google Drive API**, and configure the OAuth consent settings under Google Auth Platform. For a personal application still in Testing mode, add your Google account as a test user if Google requires that. Create an OAuth client with application type **Desktop app** and download its JSON file. In the downloader, select **Choose JSON**, pick the genuine client file, and click **Sign In to Google Drive**.

Google's login and consent screen opens in your normal browser. The downloader does not ask for your Google password directly. The application requests read-only Drive access and, after successful authorization, saves a local `google_drive_token.json` for reuse or refresh when allowed. A Google Cloud API key alone, service-account JSON, or an invented `google_oauth_client.json` filename is **not** a substitute for Desktop OAuth credentials.

Once authorized, the application can use its Drive API module to attempt accessible Drive file or folder downloads. Where supported, cloud-native Google Docs, Sheets, and Slides are exported into downloadable formats. Public access can be attempted if API access is unavailable, but neither method overrides sharing permissions, storage quotas, deleted files, or provider restrictions.

If you want to sign in with a different Google account, close the app and securely move or revoke the old `google_drive_token.json`, then authorize again. Do not publish OAuth JSON or token files. An expired or revoked Google token does not invalidate your Telegram session, and Drive authentication is optional when your task only involves Telegram media or public HTTP links.

## 15. Where downloads are saved

The **Output folder** field defines the master destination. The application displays a **Download location** line and provides an **Open Downloads** button so you do not have to guess where files are going. It creates the destination before the scan or TXT-only download begins. The selected path may point to an older configured directory rather than the folder containing the application; always read the visible path.

Illustrative structure:

```text
Master Download Folder/
├── CHANNEL_BATCH_REPORT.txt
├── GLOBAL_ALL_LINKS.txt
├── Channel Alpha/
│   ├── 12345_original_file.pdf
│   ├── 12346_photo.jpg
│   └── Links/
│       ├── ALL_LINKS.txt
│       ├── FAILED_LINKS.txt
│       ├── FAILED_LINKS_DETAILS.txt
│       ├── NEEDS_SIGN_IN.txt
│       ├── LINKS_MANIFEST.csv
│       └── example.com/
├── Channel Alpha.zip
├── Channel Beta/
│   └── Links/
├── Channel Beta_part001.zip
├── Imported_Links/
│   └── Links/
└── Imported_Links.zip
```

Actual folder names, ZIP part numbers, and which files exist depend on the run. Each channel has independent records; global records combine discoveries across runs. The app may also write a channel-level `ALL_LINKS.txt` or `FAILED_LINKS.txt` for convenience.

**ZIP split size (MB)** groups saved files into separate ZIP packages when a single archive would exceed the configured target. This is **not** the same as a multipart ZIP format requiring all segments to be joined. Archives are generated alongside their corresponding channel folders. Already-compressed media types may be stored without extra compression, making ZIP creation faster but not necessarily smaller. Plan capacity for both original downloads and ZIP outputs.

## 16. Smart History, recovery, and the Pause/Stop controls

The downloader tracks successful items in `download_state.json`. **Smart History** means a completed item remains marked completed even if you later move or delete its file. This is intentional for users who archive media and clear the working folder, but it can surprise someone expecting a deleted file to be downloaded again. The application also recognizes certain existing files if prior state is missing.

If you want to restore something marked complete, first examine your backups and history rather than deleting the entire state file. Disabling Smart History changes whether saved completions are skipped based solely on history or checked against file existence. Back up the state ledger before making major changes, especially after processing large channel batches.

When an HTTP server supports byte ranges, the downloader may resume an incomplete `.part` download. If it cannot resume safely, it restarts the transfer. Incomplete Telegram media is restarted from the beginning of that item, while other completed items remain skippable. Pausing is cooperative: it can stop work between items and during supported streaming checks, but a Telegram media transfer already in progress may not halt instantly.

Click **Stop** to request cancellation, then review reports and the current download folder. Reopen and rerun the same batch to retry work not marked completed. A finished channel phase does not guarantee that every external link was saved successfully; external failures are tracked independently.

## 17. Reading link manifests and batch reports

Link records are as important as downloaded files. `ALL_LINKS.txt` preserves discovered URLs, generally one per line, so they can be imported into other tools. `GLOBAL_ALL_LINKS.txt` gathers deduplicated URLs across completed discoveries and channels. `FAILED_LINKS.txt` contains URLs that were not retrieved or were classified as unsupported. `FAILED_LINKS_DETAILS.txt` adds the recorded error explanation.

`NEEDS_SIGN_IN.txt` highlights URLs that appeared to need authentication or permission. These are indications, **not promises** that signing in will succeed; a link may have expired, been deleted, or never been shared with your account. `LINKS_MANIFEST.csv` records URL, source message IDs where available, status, saved path, download type, and error description. Open it with a spreadsheet viewer or text editor to audit what actually happened.

`CHANNEL_BATCH_REPORT.txt` is located at the master output level after batch processing. Use it to see which channels completed, failed, were skipped as duplicates, or were stopped. Errors such as inaccessible channels and unresolved entities should appear there. Consider saving a copy of this report along with your archive so the results remain understandable months later.

For recovery, copy unsuccessful URLs from `FAILED_LINKS.txt` into a dedicated input TXT, select that file in the GUI, and use **Download TXT Links (No Telegram Login)** when they are ordinary HTTP links. For links that specifically require Telegram access, return to the authorized Telegram workflow. No report or manifest automatically grants access to protected content.

## 18. Optional torrent tab

The GUI also includes an optional section for selecting a `.torrent` file, choosing a torrent output folder, and starting, pausing, or stopping a torrent transfer. This feature depends on a compatible installed **libtorrent** Python package, which is not part of the core requirements installation. If libtorrent is unavailable, regular Telegram and URL downloads should still function.

Torrent handling is separate from the Telegram channel scanner. A magnet URL discovered in a message is not automatically started as a torrent merely because it appears in the external-link queue; unsupported magnet transfers are recorded. Use the torrent controls only when the required dependency is installed and the file is lawful to download. Never assume that every `.torrent` supplied by a third party is trustworthy.

## 19. Upgrading or transferring to another computer

Before upgrading, exit the downloader and make a **secure local backup** of `config.json`, `download_state.json`, all `.session` files, and any existing `google_drive_token.json` you intend to retain. Preserve your media output folder separately if it is stored outside the application directory. A clean source upgrade should replace program files without overwriting your personal configuration or history.

For a fresh machine, use the Clean Source package and authorize accounts normally. If transferring existing sessions between machines, treat them as sensitive account credentials: use a secure private transfer method, never email them openly or upload a public ZIP, and understand that Telegram or Google may still require reauthorization. Recreate the `.venv` folder by running the installer on the new computer.

If a previous configuration points to a path from the old computer, update **Output folder** and **Links TXT** after moving. A working archive on a different drive will not be discovered automatically unless its path is selected. Avoid copying only the ZIP files while leaving behind the original download state, if you depend on Smart History to prevent duplicate work.

## 20. Troubleshooting common issues

**The program does not start:** Run `INSTALL.bat` and inspect `INSTALL_LOG.txt`. Confirm Python 3.12, working Tkinter, package imports, and a writable folder. If `.venv` was copied from another computer and is broken, delete **only** that environment and rerun the installer; keep personal sessions and history.

**Telegram phone code never arrives:** Verify your internationally formatted phone number. Read the Activity log for the delivery method selected by Telegram. Check the Telegram service chat on your other authorized devices, your email if selected, or SMS if offered. The application cannot demand a different method. Avoid repeated rapid requests and honor any displayed cooldown or FloodWait response.

**QR login fails:** Use **Send Phone Login Code** instead. QR requires approval by a functioning Telegram app. If a saved session is already authorized, there may be no reason to perform QR authorization at all.

**The downloader appears stuck while scanning:** A channel may be large, the network slow, or Telegram temporarily limiting requests. Check the Channel Queue, Activity messages, and transfer indicators. Try a one-channel run with fewer simultaneous workers. Do not kill the application immediately just because scan counting has not produced a progress percentage.

**I cannot find downloaded files:** Read the **Download location** text and click **Open Downloads**. Also verify you selected the correct media types and that the files were not previously marked complete in Smart History. Your master output directory may differ from the extracted app folder.

**The Google Drive button asks for a JSON:** You must supply a genuine **Desktop OAuth client JSON** from your own Google Cloud project. The app does not distribute a universal client credential. Confirm the Drive API is enabled, consent settings are configured, and the signed-in account can access the file.

**Google Drive shows 403 or 404:** The link may be private, deleted, quota-limited, or shared with a different account. An API token does not override the owner's permissions. Consult `FAILED_LINKS_DETAILS.txt` and retry only after lawful access is available.

**Only an HTML page downloaded:** The link likely referenced a webpage or landing page instead of a direct binary file. Inspect the manifest's type and use the provider's documented export or download mechanism if available. The downloader does not recursively scrape arbitrary sites.

**Files download repeatedly or are skipped:** Verify `download_state.json`, Smart History, and whether you are using the same output folder. Do not delete state indiscriminately. Remember that completed items can be skipped even if the original file has been moved.

**A batch contains more than 100 channels:** Divide your channel list into batches. Manually entered lists beyond 100 are rejected; oversized TXT imports retain the first 100 and provide a warning. Saving 100 in a queue does not mean they all need to run simultaneously.

**The program reports FloodWait or repeated disconnects:** Lower the channel worker count and transfer concurrency, allow Telegram's cooldown to expire, and avoid generating unnecessary login sessions. Very high concurrency is not a substitute for Telegram API rate-limit compliance.

## 21. Security, credentials, and responsible use

Telegram `.session` files are authentication material. Protect them with appropriate filesystem permissions and avoid synchronizing them to public folders. `config.json` may include a Telegram API hash, and Google OAuth files/tokens can permit access to private account resources. Keep private packages off public distribution services. For a shareable release, distribute the Clean Source variant with no personal sessions or secrets.

Never disable Windows Defender or other protections merely to launch an unfamiliar downloaded executable. Archives and links found in public channels can contain malware, unsafe scripts, or copyrighted material. The downloader saves files but does not automatically run their contents. Inspect and scan archives before opening them, particularly installers, macros, scripts, and software from unknown authors.

Only access channels and cloud files your accounts are authorized to use. Telegram channel visibility, Drive link permissions, website terms, and copyright law remain applicable. This project does not promise to bypass CAPTCHA challenges, private-channel membership, paywalls, DRM, or account restrictions. Do not use it to distribute someone else's credentials or sensitive files.

## 22. Capabilities, limits, and testing status

Version 3.4's source code and accompanying tests cover channel-list parsing, 100-channel batch validation, isolated output directories, shared download concurrency, completion history, link manifests, TXT-only mode, and generated archives. The release history reports successful offline batch simulations and a GUI launch under a virtual display. Those tests demonstrate expected application behavior under controlled conditions, **not** a guaranteed live 100-channel download on every Telegram account.

Live Telegram sessions, changing provider APIs, Google browser authorization, host-specific anti-bot systems, broken links, network interruptions, and Windows-specific environments can affect results. Drive API access must be authorized by the user, and some hosts may not support automatic downloads. Actual download success should always be judged from files on disk and status reports, not from a green application window alone.

The program should be treated as an evolving desktop source project rather than an audited enterprise archiving service. Back up your important data, test small batches first, and watch disk usage, provider errors, and credential hygiene during long-running jobs.

## 23. Frequently asked questions

**Can I add exactly 100 channels?** Yes. One batch supports 100 unique channel entries. You can choose fewer than 100 active workers; the default is five.

**Does the batch need 100 Telegram accounts?** No. The worker uses one authorized account session across its channel jobs. Every channel still has to be accessible to that account.

**Will it download every file on every website linked from Telegram?** No. It retrieves supported direct files and some provider resources; general webpages may be saved as HTML, and inaccessible content remains in the failure lists.

**Can I download a TXT list without logging into Telegram?** Yes, using the TXT-only button. Telegram post attachments that require account access are an exception.

**Does Google Drive need a separate login?** Only for links requiring account authorization. Public Drive links may work without OAuth; private/shared links need a properly configured Google account and OAuth client.

**Will the installer erase existing sessions?** The installer is designed to preserve them. Even so, keep backups before upgrades or system changes.

**If I stop a 100-channel batch, must I restart everything?** Not normally. Completed entries remain in saved history; incomplete Telegram files may restart individually, while supported HTTP downloads may resume from partial data.

**Can I choose an external SSD as the destination?** Yes. Use Browse to choose a writable location. Keep the external drive connected for the entire run and ensure it has enough free space for the archives.

**Can I run it on a totally offline PC?** Not for first-time installation or remote downloading. The installer retrieves packages and the application communicates with Telegram or external sites.

## 24. Quick-start checklists

### First-time setup

- [ ] Extract the **Clean Source** ZIP into a writable Windows folder.
- [ ] Run `INSTALL.bat` and verify it finishes successfully.
- [ ] Open the app with `START_DOWNLOADER.bat` or the shortcut.
- [ ] Enter your own Telegram API ID and API hash.
- [ ] Sign in through the offered phone-code flow or QR method.
- [ ] Choose an output folder and check free disk space.
- [ ] Try one accessible channel before scaling up.

### Large channel-batch archive

- [ ] Paste or import no more than 100 unique channel identifiers.
- [ ] Set **Channels active at once** conservatively; begin around 3–5.
- [ ] Choose the shared concurrent-transfer count.
- [ ] Enable the exact media categories and external-link options required.
- [ ] Select whether a TXT URL list should join this batch.
- [ ] Start the scan and monitor the Channel Queue.
- [ ] Open Downloads and inspect each channel's files and ZIPs.
- [ ] Read `CHANNEL_BATCH_REPORT.txt` and retry failed URLs when appropriate.

### Safe backup and handoff

- [ ] Back up `download_state.json` and your chosen media archive location.
- [ ] Keep working `.session`, API configuration, and Google token files private.
- [ ] Recreate `.venv` when installing on another Windows machine.
- [ ] Use the **Clean Source** package for anyone who should not receive your credentials.
- [ ] Never assume a successful scan means every external link was downloadable.

---

**Final note:** The most reliable v3.4 workflow is to authenticate once, validate a small channel batch, choose a visible download location, and expand gradually toward 100 queued channels while keeping actual concurrent workers conservative. Preserve the manifests and history files with the archive: they explain what was downloaded, what was skipped, and which references still need attention.
