from aiohttp.test_utils import TestClient, TestServer

from bot.app import build_health_app, create_dispatcher


class FakeDB:
    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.session_factory = None

    async def ping(self) -> bool:
        return self.ok


class FakeRedis:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    async def ping(self):
        if not self.ok:
            raise RuntimeError("redis down")
        return True


async def test_health_ok():
    app = build_health_app(FakeDB(True), FakeRedis())
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get("/health")
        assert resp.status == 200
        body = await resp.json()
        assert body == {"status": "ok", "db": True, "redis": True}
    finally:
        await client.close()


async def test_health_degraded_when_db_down():
    app = build_health_app(FakeDB(False), FakeRedis())
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get("/health")
        assert resp.status == 503
        body = await resp.json()
        assert body["status"] == "degraded"
        assert body["db"] is False
    finally:
        await client.close()


async def test_health_degraded_when_redis_down():
    app = build_health_app(FakeDB(True), FakeRedis(ok=False))
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get("/health")
        assert resp.status == 503
        body = await resp.json()
        assert body["redis"] is False
    finally:
        await client.close()


def test_create_dispatcher_registers_middleware():
    dp = create_dispatcher(FakeDB(True), FakeRedis(True))
    assert dp is not None
