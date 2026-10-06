import smtplib
from datetime import datetime
from decimal import Decimal
from email.message import EmailMessage

from booking.config import Settings


def reservation_pending_email(
    *,
    to: str,
    sender: str,
    reservation_id: str,
    quantity: int,
    unit_price: Decimal,
    expires_at: datetime,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "Your tickets are reserved: complete your payment"
    message["From"] = sender
    message["To"] = to
    total = unit_price * quantity
    message.set_content(
        f"Hi!\n\n"
        f"We're holding {quantity} ticket(s) for you at {unit_price} € each "
        f"({total} € in total).\n\n"
        f"Complete your payment before {expires_at:%H:%M} UTC "
        f"({expires_at:%Y-%m-%d}), or the tickets will be released.\n\n"
        f"Reservation: {reservation_id}\n\n"
        f"— TicketHub\n"
    )
    return message


def send(message: EmailMessage, settings: Settings) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.send_message(message)
