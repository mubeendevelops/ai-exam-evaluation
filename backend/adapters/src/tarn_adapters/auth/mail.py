"""Mailers. Development prints email to the console log; production sends through any SMTP
server (provider-neutral: Google Workspace, SES, SendGrid and Mailgun all offer SMTP).
``Settings`` refuses the console mailer in production, because the links it prints are secrets."""

import smtplib
import ssl
import sys
from collections.abc import Callable
from email.message import EmailMessage as MimeMessage
from typing import Protocol, TextIO

import structlog

from tarn_core.ports.identity import EmailMessage, Mailer


class ConsoleMailer:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stderr

    def send(self, message: EmailMessage) -> None:
        print(
            "\n----- email (development console mailer) -----\n"
            f"To: {message.to}\nSubject: {message.subject}\n\n{message.text}\n"
            "-----------------------------------------------",
            file=self._stream,
            flush=True,
        )


class SmtpConnection(Protocol):
    """The part of ``smtplib.SMTP`` used here."""

    def starttls(self, *, context: ssl.SSLContext) -> object: ...

    def login(self, user: str, password: str) -> object: ...

    def send_message(self, msg: MimeMessage) -> object: ...

    def __enter__(self) -> "SmtpConnection": ...

    def __exit__(self, *args: object) -> None: ...


class SmtpMailer:
    """Sends over SMTP with STARTTLS (or implicit TLS on port 465). A message that cannot be
    sent is logged by the exception's class only (never the address or the link) and dropped:
    the mail is sent after the database commit, so failing the request would not undo it, and
    the person can ask again (a new verification or reset link)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        user: str = "",
        password: str = "",
        timeout: float = 15.0,
        connect: Callable[[], SmtpConnection] | None = None,
    ) -> None:
        self._sender = sender
        self._user = user
        self._password = password
        self._implicit_tls = port == 465
        self._connect = connect or self._real_connection(host, port, timeout)

    def _real_connection(
        self, host: str, port: int, timeout: float
    ) -> Callable[[], SmtpConnection]:
        context = ssl.create_default_context()

        def connect() -> SmtpConnection:
            if self._implicit_tls:
                return smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)  # type: ignore[return-value]
            return smtplib.SMTP(host, port, timeout=timeout)  # type: ignore[return-value]

        return connect

    def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = self._sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.text)
        try:
            with self._connect() as smtp:
                if not self._implicit_tls:
                    smtp.starttls(context=ssl.create_default_context())
                if self._user:
                    smtp.login(self._user, self._password)
                smtp.send_message(mime)
        except Exception as error:
            structlog.get_logger("tarn_adapters.mail").error(
                "mail.send_failed", error=type(error).__name__
            )


class DeferredMailer:
    """Holds messages until the unit of work commits, so a rolled-back registration or reset
    sends nothing. Call ``flush`` after commit; drop the instance on failure."""

    def __init__(self, target: Mailer) -> None:
        self._target = target
        self.pending: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.pending.append(message)

    def flush(self) -> None:
        pending, self.pending = self.pending, []
        for message in pending:
            self._target.send(message)
