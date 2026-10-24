"""Outgoing e-mail: verification and password-reset links, in the account's language.

``AKENO_MAIL_BACKEND``: ``smtp`` (any provider or your own mail server), ``file`` (writes .eml files into
``AKENO_MAIL_DIR``: the development mail sink, also handy before SMTP is set up) or ``console`` (the log).
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import uuid
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from akeno_server.config import get_config

log = logging.getLogger(__name__)

TEXTS = {
    "verify": {
        "en": ("Confirm your e-mail address for AKENO",
               "Hello{name},\n\nplease confirm this e-mail address for your AKENO account:\n\n{link}\n\n"
               "The link is valid for 48 hours. If you did not create an AKENO account, ignore this e-mail.\n"),
        "de": ("Bestätige deine E-Mail-Adresse für AKENO",
               "Hallo{name},\n\nbitte bestätige diese E-Mail-Adresse für dein AKENO-Konto:\n\n{link}\n\n"
               "Der Link ist 48 Stunden gültig. Wenn du kein AKENO-Konto angelegt hast, ignoriere diese E-Mail.\n"),
    },
    "reset": {
        "en": ("Reset your AKENO password",
               "Hello{name},\n\nsomeone asked to reset the password of your AKENO account. To choose a new "
               "password, open:\n\n{link}\n\nThe link is valid for one hour and works once. If this was not you, "
               "ignore this e-mail: your password stays as it is.\n"),
        "de": ("Setze dein AKENO-Passwort zurück",
               "Hallo{name},\n\njemand möchte das Passwort deines AKENO-Kontos zurücksetzen. Ein neues Passwort "
               "wählst du hier:\n\n{link}\n\nDer Link ist eine Stunde gültig und funktioniert einmal. Warst du "
               "das nicht, ignoriere diese E-Mail: dein Passwort bleibt unverändert.\n"),
    },
    "password_changed": {
        "en": ("Your AKENO password was changed",
               "Hello{name},\n\nthe password of your AKENO account was just changed and every sign-in was ended. "
               "If this was not you, reset your password now: {link}\n"),
        "de": ("Dein AKENO-Passwort wurde geändert",
               "Hallo{name},\n\ndas Passwort deines AKENO-Kontos wurde gerade geändert und alle Anmeldungen "
               "wurden beendet. Warst du das nicht, setze dein Passwort jetzt zurück: {link}\n"),
    },
}

outbox: list[EmailMessage] = []          # tests read sent messages here


def compose(kind: str, to: str, language: str, link: str, name: str = "") -> EmailMessage:
    cfg = get_config()
    subject, body = TEXTS[kind]["de" if language == "de" else "en"]
    msg = EmailMessage()
    msg["From"] = cfg.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="akeno.invalid")
    msg.set_content(body.format(name=f" {name}" if name else "", link=link))
    return msg


def send(msg: EmailMessage) -> None:
    cfg = get_config()
    outbox.append(msg)
    del outbox[:-50]
    if cfg.mail_backend == "console":
        log.info("e-mail to %s: %s\n%s", msg["To"], msg["Subject"], msg.get_content())
        return
    if cfg.mail_backend == "file":
        Path(cfg.mail_dir).mkdir(parents=True, exist_ok=True)
        (Path(cfg.mail_dir) / f"{uuid.uuid4().hex}.eml").write_bytes(bytes(msg))
        return
    try:
        if cfg.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=20, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20)
            if cfg.smtp_security == "starttls":
                server.starttls(context=ssl.create_default_context())
        with server:
            if cfg.smtp_user:
                server.login(cfg.smtp_user, cfg.smtp_password.get_secret_value())
            server.send_message(msg)
    except (OSError, smtplib.SMTPException):
        # a failed e-mail must not fail the request (and must not reveal whether an account exists)
        log.exception("could not send e-mail to %s", msg["To"])


def configured() -> bool:
    cfg = get_config()
    return cfg.mail_backend != "smtp" or bool(cfg.smtp_host)
