from aiohttp import web, ClientSession

async def handler(request):
    url = f"http://127.0.0.1:9119{request.rel_url}"
    
    # Headers sin Host (aiohttp lo pone automáticamente al destino)
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    
    async with ClientSession() as session:
        async with session.request(
            request.method, url,
            headers=headers,
            data=await request.read(),
            params=request.query
        ) as resp:
            return web.Response(
                body=await resp.read(),
                status=resp.status,
                headers=resp.headers
            )

app = web.Application()
app.router.add_route("*", "/{tail:.*}", handler)
web.run_app(app, host="0.0.0.0", port=9120)