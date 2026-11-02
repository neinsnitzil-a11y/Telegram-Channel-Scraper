"""Telegram authorization with explicit phone-code and QR choices.

Telegram selects how login codes are *delivered*. SMS cannot be forced by a
third-party Telethon client. Existing session databases are never deleted,
renamed or logged out. Keep .session files private: they are login credentials.
"""

import asyncio
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, functions
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

CODE_RESEND = "\x00TELEGRAM_RESEND_CODE\x00"
NETWORK_TIMEOUT = 35
SESSION_TIMEOUT = 15


def normalize_phone(phone):
    """Accept international numbers, with optional visual separators."""
    p = re.sub(r"[\s.()\-]", "", str(phone or ""))
    if not re.fullmatch(r"\+[1-9]\d{6,14}", p):
        raise ValueError("Enter your mobile number with a + country code, e.g. +15551234567 (7-15 digits).")
    return p


def code_destination(sent):
    kind = type(getattr(sent, "type", None)).__name__
    choices = {
        "SentCodeTypeApp": "the verified Telegram service chat in another logged-in Telegram app",
        "SentCodeTypeSms": "an SMS to your mobile number",
        "SentCodeTypeSmsWord": "an SMS containing a word",
        "SentCodeTypeSmsPhrase": "an SMS containing a phrase",
        "SentCodeTypeEmailCode": "your Telegram login email address",
        "SentCodeTypeFragmentSms": "Telegram Fragment (check the address Telegram provides)",
        "SentCodeTypeCall": "a phone call",
        "SentCodeTypeMissedCall": "a missed phone call",
        "SentCodeTypeFlashCall": "a flash phone call",
        "SentCodeTypeFirebaseSms": "Firebase SMS verification (official mobile Telegram client may be required)",
    }
    if kind == "SentCodeTypeSetUpEmailRequired":
        return "login-email setup required in the official Telegram app"
    return choices.get(kind, "a Telegram-selected delivery destination (see your Telegram apps)")


def describe_flood(exc):
    seconds = max(0, int(getattr(exc, "seconds", 0)))
    return (f"Telegram rate-limited login requests. Wait at least {seconds} seconds "
            "before retrying. Repeated requests can lengthen the wait.")


async def limited(awaitable, name, seconds=NETWORK_TIMEOUT):
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"{name} timed out after {seconds} seconds. Check your connection, firewall, "
            "VPN/proxy, and Telegram service availability. No further code requests were made."
        ) from exc


async def _ask_password(client, gui, log):
    for attempt in range(3):
        response = await gui.ask_async(
            "Telegram 2-step verification",
            "Enter the Telegram account's two-step verification password (not the login code).",
            secret=True,
        )
        if not response:
            raise RuntimeError("Login cancelled; saved session files were left intact.")
        try:
            await limited(client.sign_in(password=response), "Two-step verification")
            return
        except PasswordHashInvalidError:
            log(f"Incorrect two-step password ({attempt + 1}/3).")
    raise RuntimeError("Three incorrect two-step passwords. Retry later.")


async def _sign_in_code(client, phone, gui, log):
    phone = normalize_phone(phone)
    log("Contacting Telegram to request a sign-in code for the entered number...")
    try:
        sent = await limited(client.send_code_request(phone), "Phone code request")
    except FloodWaitError as exc:
        raise RuntimeError(describe_flood(exc)) from exc

    for attempt in range(6):
        where = code_destination(sent)
        if type(getattr(sent, "type", None)).__name__ == "SentCodeTypeSetUpEmailRequired":
            raise RuntimeError("Telegram requires login-email setup. Finish setup using an official Telegram app before attempting code login here.")
        if type(getattr(sent, "type", None)).__name__ == "SentCodeTypeFirebaseSms":
            raise RuntimeError("Telegram requires official-app Firebase SMS verification for this login. Third-party clients cannot complete that method; sign in with the official Telegram mobile app first, or use an available alternate method.")
        seconds = max(0, int(getattr(sent, "timeout", 0) or 0))
        may_resend = bool(getattr(sent, "next_type", None))
        log(f"Telegram responded. Code destination: {where}.")
        if may_resend:
            log(f"Telegram offers another code method after {seconds}s. The Resend button unlocks then.")
        else:
            log("Telegram has not offered an alternate code method for this request; SMS cannot be forced.")
        response = await gui.ask_async(
            "Enter Telegram login code",
            f"Telegram says the code was sent to: {where}.\n\n"
            "Check the verified 'Telegram' service chat, your phone, or email as indicated.\n"
            "Use Resend (when available) for Telegram's NEXT allowed method.\n"
            "A separate prompt will ask for your 2-step password if needed.",
            allow_resend=may_resend,
            resend_after=seconds,
        )
        if response == CODE_RESEND:
            if not may_resend:
                log("No alternate delivery method is offered by Telegram yet.")
                continue
            log("Requesting the next delivery method permitted by Telegram...")
            try:
                sent = await limited(
                    client(functions.auth.ResendCodeRequest(
                        phone_number=phone, phone_code_hash=sent.phone_code_hash,
                    )), "Telegram code resend"
                )
            except FloodWaitError as exc:
                raise RuntimeError(describe_flood(exc)) from exc
            except PhoneCodeExpiredError as exc:
                raise RuntimeError("Resend window expired. Restart phone sign-in when Telegram permits.") from exc
            continue

        code = str(response or "").strip()
        if not code:
            raise RuntimeError("Login cancelled; saved session files were left intact.")
        try:
            log("Verifying the login code with Telegram...")
            await limited(client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash), "Code verification")
            return
        except SessionPasswordNeededError:
            await _ask_password(client, gui, log)
            return
        except PhoneCodeInvalidError:
            log(f"Telegram rejected the code ({attempt + 1}/6); verify the latest code, then try again.")
        except PhoneCodeExpiredError:
            raise RuntimeError("Telegram says the code expired. Restart the phone login flow and check the new delivery destination.")
    raise RuntimeError("Too many incorrect codes in this run. Try again later; avoid repeated code requests.")


async def _sign_in_qr(client, gui, log):
    log("Requesting a QR login token from Telegram...")
    qr = await limited(client.qr_login(), "QR login initialization")
    deadline = time.monotonic() + 300
    try:
        while time.monotonic() < deadline:
            if gui.qr_cancelled.is_set():
                raise RuntimeError("QR login cancelled; saved sessions were preserved.")
            gui.show_qr_login(qr.url)
            try:
                await qr.wait(timeout=min(12, max(1, int(deadline - time.monotonic()))))
                log("Telegram QR login approved.")
                return
            except asyncio.TimeoutError:
                if gui.qr_cancelled.is_set():
                    raise RuntimeError("QR login cancelled; saved sessions were preserved.")
                expires = qr.expires
                if expires is None or datetime.now(timezone.utc) >= expires.astimezone(timezone.utc):
                    await limited(qr.recreate(), "QR token refresh")
            except SessionPasswordNeededError:
                await _ask_password(client, gui, log)
                return
            except Exception as exc:
                if type(exc).__name__ == "AuthTokenExpiredError":
                    await limited(qr.recreate(), "QR token refresh")
                else:
                    raise
        raise RuntimeError("QR login timed out after five minutes. Try phone login instead.")
    finally:
        gui.close_qr_login()


async def connect_preserving_sessions(
    *, app_dir, api_id, api_hash, phone, gui, log, qr_mode=False,
    connection_retries=2, retry_delay=1, request_retries=2,
):
    """Use a valid existing session, or authorize into a separate session file."""
    root = Path(app_dir).resolve()
    primary = root / "telegram_session"
    secondary = root / "teledownloader" / "telegram_session"
    qr_recovery = root / "telegram_qr_recovery"
    phone_recovery = root / "telegram_phone_login"
    previous_nested = root / "Recovered_Legacy_Profiles" / "Nested_Original"
    options = {
        "auto_reconnect": True,
        "connection_retries": connection_retries,
        "retry_delay": retry_delay,
        "request_retries": request_retries,
        "timeout": 12,
        "flood_sleep_threshold": 0,
    }

    for label, base in (
        ("original", primary),
        ("alternate", secondary),
        ("previous QR", qr_recovery),
        ("previous phone", phone_recovery),
        ("nested saved", previous_nested / "telegram_session"),
        ("nested alternate", previous_nested / "teledownloader" / "telegram_session"),
    ):
        if not base.with_suffix(".session").is_file():
            continue
        log(f"Checking saved {label} Telegram session (up to {SESSION_TIMEOUT}s)...")
        client = TelegramClient(str(base), api_id, api_hash, **options)
        authorized = False
        try:
            await limited(client.connect(), f"Connection for {label} session", SESSION_TIMEOUT)
            authorized = await limited(client.is_user_authorized(), f"Check for {label} session", SESSION_TIMEOUT)
            if authorized:
                log(f"Reused {label} session. No new code requested.")
                return client
            log(f"{label.capitalize()} session is not authorized; preserving it.")
        except RuntimeError as exc:
            log(f"Session check issue: {exc}")
        except OSError as exc:
            log(f"Session connection issue ({type(exc).__name__}); trying other sessions.")
        except Exception as exc:
            log(f"Saved {label} session failed ({type(exc).__name__}); trying other sessions.")
        finally:
            if not authorized and client.is_connected():
                await client.disconnect()

    new_base = qr_recovery if qr_mode else phone_recovery
    log("No authorized stored session. Starting " + ("QR" if qr_mode else "phone-code") + " authorization.")
    client = TelegramClient(str(new_base), api_id, api_hash, **options)
    try:
        await limited(client.connect(), "Connection to Telegram")
        if not await limited(client.is_user_authorized(), "Initial authorization check"):
            if qr_mode:
                await _sign_in_qr(client, gui, log)
            else:
                await _sign_in_code(client, phone, gui, log)
        if not await limited(client.is_user_authorized(), "Final authorization check"):
            raise RuntimeError("Telegram did not authorize the account. Review the sign-in instructions and try again.")
        log("Telegram authorization successful. The session was saved for reuse.")
        return client
    except Exception:
        if client.is_connected():
            await client.disconnect()
        raise
