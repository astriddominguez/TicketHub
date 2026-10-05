from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from .conftest import CreateInventory, auth, available

ANA, LUIS = 1, 2


async def test_availability_is_public(
    client: AsyncClient, create_inventory: CreateInventory
) -> None:
    await create_inventory(total=100, event_id=5)
    response = await client.get("/events/5/availability")
    assert response.status_code == 200
    assert response.json()[0]["available"] == 100


async def test_reserve_holds_tickets_for_ten_minutes(
    client: AsyncClient, create_inventory: CreateInventory
) -> None:
    inventory = await create_inventory(total=10, price="65.00")

    response = await client.post(
        "/reservations",
        json={"inventory_id": inventory.id, "quantity": 3},
        headers=auth(ANA),
    )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "pending"
    assert data["unit_price"] == "65.00"
    expires_at = datetime.fromisoformat(data["expires_at"])
    assert (
        timedelta(minutes=9) < expires_at - datetime.now(UTC) <= timedelta(minutes=10)
    )
    assert await available(inventory.id) == 7


async def test_not_enough_tickets_is_409_and_changes_nothing(
    client: AsyncClient, create_inventory: CreateInventory
) -> None:
    inventory = await create_inventory(total=2)
    response = await client.post(
        "/reservations",
        json={"inventory_id": inventory.id, "quantity": 3},
        headers=auth(ANA),
    )
    assert response.status_code == 409
    assert await available(inventory.id) == 2


async def test_unknown_inventory_is_404(client: AsyncClient) -> None:
    response = await client.post(
        "/reservations", json={"inventory_id": 999, "quantity": 1}, headers=auth(ANA)
    )
    assert response.status_code == 404


async def test_quantity_limits_are_validated(
    client: AsyncClient, create_inventory: CreateInventory
) -> None:
    inventory = await create_inventory(total=100)
    for quantity in (0, -1, 11):
        response = await client.post(
            "/reservations",
            json={"inventory_id": inventory.id, "quantity": quantity},
            headers=auth(ANA),
        )
        assert response.status_code == 422, quantity
    assert await available(inventory.id) == 100


async def test_users_only_see_their_own_reservations(
    client: AsyncClient, create_inventory: CreateInventory
) -> None:
    inventory = await create_inventory()
    payload = {"inventory_id": inventory.id, "quantity": 1}
    anas = (await client.post("/reservations", json=payload, headers=auth(ANA))).json()
    await client.post("/reservations", json=payload, headers=auth(LUIS))

    mine = (await client.get("/reservations", headers=auth(ANA))).json()
    assert [r["id"] for r in mine] == [anas["id"]]
    # Someone else's reservation looks like a missing one.
    other = await client.get(f"/reservations/{anas['id']}", headers=auth(LUIS))
    assert other.status_code == 404


async def test_cancel_releases_tickets_once(
    client: AsyncClient, create_inventory: CreateInventory
) -> None:
    inventory = await create_inventory(total=10)
    reservation = (
        await client.post(
            "/reservations",
            json={"inventory_id": inventory.id, "quantity": 4},
            headers=auth(ANA),
        )
    ).json()
    url = f"/reservations/{reservation['id']}/cancel"

    first = await client.post(url, headers=auth(ANA))
    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert await available(inventory.id) == 10

    second = await client.post(url, headers=auth(ANA))
    assert second.status_code == 409
    assert await available(inventory.id) == 10  # not 14


async def test_cannot_cancel_someone_elses_reservation(
    client: AsyncClient, create_inventory: CreateInventory
) -> None:
    inventory = await create_inventory(total=10)
    reservation = (
        await client.post(
            "/reservations",
            json={"inventory_id": inventory.id, "quantity": 2},
            headers=auth(ANA),
        )
    ).json()
    response = await client.post(
        f"/reservations/{reservation['id']}/cancel", headers=auth(LUIS)
    )
    assert response.status_code == 404
    assert await available(inventory.id) == 8
