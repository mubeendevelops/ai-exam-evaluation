"""Mailers. Development prints email to the console log; a production mail adapter (and its
provider) is chosen with the platform adapters (P20). ``Settings`` refuses the console mailer
in production, because the links it prints are secrets."""

import sys
from typing import TextIO

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
