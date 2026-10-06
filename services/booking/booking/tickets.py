"""Ticket codes for the QR images: signed, so a fake can be spotted offline.

Code format: "<reservation id>:<ticket number>:<signature>". Anyone can read it
(like a JWT), but only we can produce a valid signature. Checking a code at the
door needs no database lookup.

Not covered here: a valid code copied by two people. Stopping reuse needs a
check-in record ("ticket 3 already entered at 21:04") - a future improvement.
"""

import hashlib
import hmac
import io
import uuid

import qrcode


def _signature(reservation_id: uuid.UUID, number: int, key: str) -> str:
    message = f"{reservation_id}:{number}".encode()
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()[:20]


def ticket_codes(reservation_id: uuid.UUID, quantity: int, key: str) -> list[str]:
    return [
        f"{reservation_id}:{number}:{_signature(reservation_id, number, key)}"
        for number in range(1, quantity + 1)
    ]


def verify_ticket_code(code: str, key: str) -> tuple[uuid.UUID, int] | None:
    """(reservation id, ticket number) if the code is genuine, else None."""
    try:
        raw_id, raw_number, signature = code.split(":")
        reservation_id, number = uuid.UUID(raw_id), int(raw_number)
    except ValueError:
        return None
    # compare_digest takes the same time whether the first or last character is
    # wrong, so an attacker can't guess the signature byte by byte (timing attack).
    if not hmac.compare_digest(signature, _signature(reservation_id, number, key)):
        return None
    return reservation_id, number


def qr_png(code: str) -> bytes:
    image = qrcode.make(code)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
