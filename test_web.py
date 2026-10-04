import os
from aiohttp import web

async def handle_root(request):
    return web.Response(text="Hello from Bothost! Web works!", content_type="text/plain")

async def handle_ping(request):
    return web.json_response({"ok": True, "port": PORT})

def main():
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/ping", handle_ping)
    web.run_app(app, host="0.0.0.0", port=PORT)

PORT = int(os.environ.get("PORT", "8080"))

if __name__ == "__main__":
    print(f"[~] Starting test web server on port {PORT}")
    main()
