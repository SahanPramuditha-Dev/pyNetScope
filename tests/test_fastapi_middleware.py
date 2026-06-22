import asyncio

from pynetscope import NetScope, PyNetScopeMiddleware


def test_asgi_middleware_records_request():
    scope = NetScope()

    async def app(asgi_scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = PyNetScopeMiddleware(app, scope=scope)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "GET",
                "scheme": "http",
                "path": "/items/123",
                "query_string": b"token=secret",
                "headers": [(b"host", b"testserver")],
                "server": ("testserver", 80),
            },
            receive,
            send,
        )
    )

    records = scope.snapshot()["records"]
    assert sent[0]["status"] == 204
    assert len(records) == 1
    assert records[0].endpoint == "GET testserver/items/:id"
    assert records[0].url == "http://testserver/items/123?token=%5Bredacted%5D"
