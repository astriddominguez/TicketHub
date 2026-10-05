import json
from typing import Any, Protocol

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.exchange_type import ExchangeType


class Publisher(Protocol):
    def publish(
        self, routing_key: str, body: dict[str, Any], message_id: str
    ) -> None: ...


class RabbitPublisher:
    """Publishes JSON messages to a durable topic exchange, with publisher confirms.

    With confirms, publish() only returns once RabbitMQ has taken responsibility
    for the message; otherwise it raises and the relay will retry it later.
    """

    def __init__(self, url: str, exchange: str) -> None:
        self.exchange = exchange
        self.connection = pika.BlockingConnection(pika.URLParameters(url))
        self.channel: BlockingChannel = self.connection.channel()
        self.channel.exchange_declare(
            exchange=exchange, exchange_type=ExchangeType.topic, durable=True
        )
        self.channel.confirm_delivery()

    def publish(self, routing_key: str, body: dict[str, Any], message_id: str) -> None:
        self.channel.basic_publish(
            exchange=self.exchange,
            routing_key=routing_key,
            body=json.dumps(body).encode(),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=pika.DeliveryMode.Persistent,  # survives a broker restart
                message_id=message_id,
            ),
            mandatory=True,  # error if no queue is bound to receive it
        )

    def close(self) -> None:
        if self.connection.is_open:
            self.connection.close()
