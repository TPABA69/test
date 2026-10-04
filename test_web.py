# -*- coding: utf-8 -*-
"""
test_web.py — Telegram-бот (Telethon) + встроенный aiohttp-сервер в одном процессе.
Тест WebApp «Coin Flip». Хостинг: Bothost (Docker).

ENV:
  BOT_TOKEN — токен тестового бота (обязательно)
  PORT      — порт aiohttp-сервера (на Bothost: 3000)
  API_ID, API_HASH — нужны самому Telethon для подключения (с my.telegram.org).
"""
import asyncio
import os
import json
import time
import hmac
import hashlib
import uuid
import random
import traceback
import urllib.parse
from datetime import datetime

from aiohttp import web
from telethon import TelegramClient, events, Button

# ----------------------------- Конфигурация -----------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", "3000"))
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")

DATA_DIR = "/app/data"
DATA_FILE = f"{DATA_DIR}/test_data.json"
ERROR_LOG = f"{DATA_DIR}/test_errors.log"
SESSION_PATH = f"{DATA_DIR}/test_bot_session"
DOMAIN = "bot-1791103083-5513-tpaba69.bothost.tech"
WEBAPP_URL = f"https://{DOMAIN}/coinflip"

START_BALANCE = 10000
WIN_MULTIPLIER_NUM = 19
WIN_MULTIPLIER_DEN = 10
INIT_DATA_MAX_AGE = 24 * 3600

bot = None
data = {"users": {}}
data_lock = asyncio.Lock()
rng = random.SystemRandom()


def log_error(where, exc):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {where}: {exc!r}\n{tb}\n")
    except Exception:
        pass
    print(f"[ERROR] {where}: {exc!r}", flush=True)


def load_data():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(DATA_FILE):
        save_data({"users": {}})
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or "users" not in d:
            d = {"users": {}}
        return d
    except Exception as e:
        log_error("load_data", e)
        return {"users": {}}


def save_data(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = f"{DATA_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DATA_FILE)


def verify_init_data(init_data):
    try:
        if not init_data or not BOT_TOKEN:
            return None
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            return None
        check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs.keys()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, received_hash):
            return None
        auth_date = int(pairs.get("auth_date", "0") or 0)
        if auth_date and time.time() - auth_date > INIT_DATA_MAX_AGE:
            return None
        user = json.loads(pairs.get("user", "{}"))
        if not isinstance(user, dict) or "id" not in user:
            return None
        return user
    except Exception as e:
        log_error("verify_init_data", e)
        return None


def get_user_balance(uid):
    key = str(uid)
    if key not in data["users"]:
        data["users"][key] = {"balance": START_BALANCE}
        save_data(data)
    return int(data["users"][key]["balance"])


def set_user_balance(uid, value):
    data["users"][str(uid)] = {"balance": int(value)}
    save_data(data)


def json_error(message, status=500, **extra):
    body = {"error": message}
    body.update(extra)
    return web.json_response(body, status=status)


async def extract_init_data(request):
    init_data = request.query.get("init_data") or request.headers.get("X-Init-Data")
    body = None
    if not init_data and request.can_read_body:
        try:
            body = await request.json()
            if isinstance(body, dict):
                init_data = body.get("init_data")
        except Exception:
            body = None
    return init_data, body


async def handle_root(request):
    return web.Response(text="Coin Flip test bot. Open /coinflip")


async def handle_ping(request):
    return web.json_response({"ok": True, "port": PORT})


async def handle_coinflip_page(request):
    return web.Response(text=COINFLIP_HTML, content_type="text/html", charset="utf-8")


async def handle_balance(request):
    try:
        init_data, _ = await extract_init_data(request)
        if not init_data:
            return json_error("Missing init_data", 400)
        user = verify_init_data(init_data)
        if not user:
            return json_error("Invalid signature", 403)
        uid = user["id"]
        async with data_lock:
            balance = get_user_balance(uid)
        return web.json_response({"balance": balance, "user_id": uid})
    except Exception as e:
        log_error("handle_balance", e)
        return json_error("Internal error", 500)


async def handle_play(request):
    try:
        try:
            body = await request.json()
        except Exception:
            return json_error("Bad JSON", 400)
        if not isinstance(body, dict):
            return json_error("Bad JSON", 400)

        init_data = body.get("init_data")
        if not init_data:
            return json_error("Missing init_data", 400)
        user = verify_init_data(init_data)
        if not user:
            return json_error("Invalid signature", 403)
        uid = user["id"]

        choice = body.get("choice")
        if choice not in ("heads", "tails"):
            return json_error("Bad choice", 400)
        try:
            bet = int(body.get("bet"))
        except (TypeError, ValueError):
            return json_error("Bad bet", 400)
        if bet <= 0:
            return json_error("Bad bet", 400)

        tx_id = f"coin:{uid}:{uuid.uuid4().hex}"

        async with data_lock:
            balance = get_user_balance(uid)
            if balance < bet:
                return json_error("insufficient_balance", 400, balance=balance)
            balance -= bet
            result = "heads" if rng.randrange(2) == 0 else "tails"
            won = (result == choice)
            payout = (bet * WIN_MULTIPLIER_NUM // WIN_MULTIPLIER_DEN) if won else 0
            balance += payout
            set_user_balance(uid, balance)

        print(f"[play] {tx_id} bet={bet} choice={choice} result={result} won={won}", flush=True)
        return web.json_response({
            "result": result,
            "won": won,
            "new_balance": balance,
            "bet": bet,
            "payout": payout,
        })
    except Exception as e:
        log_error("handle_play", e)
        return json_error("Internal error", 500)


def webapp_buttons():
    text = "🪙 Открыть Coin Flip"
    if hasattr(Button, "web_view"):
        return [[Button.web_view(text, WEBAPP_URL)]]
    return [[Button.url(text, WEBAPP_URL)]]


async def start_bot():
    global bot
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")
    os.makedirs(DATA_DIR, exist_ok=True)
    bot = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)

    @bot.on(events.NewMessage(pattern=r"^/start(?:@\w+)?(?:\s|$)"))
    async def on_start(event):
        try:
            await event.respond(
                "👋 Привет! Это тестовый бот для WebApp «Coin Flip».\n"
                "Нажми кнопку ниже, чтобы открыть игру.",
                buttons=webapp_buttons(),
            )
        except Exception as e:
            log_error("on_start", e)

    @bot.on(events.NewMessage(pattern=r"^/coinflip(?:@\w+)?(?:\s|$)"))
    async def on_coinflip(event):
        try:
            await event.respond("🪙 Coin Flip — жми кнопку:", buttons=webapp_buttons())
        except Exception as e:
            log_error("on_coinflip", e)

    @bot.on(events.NewMessage(pattern=r"^/diag(?:\s|$)"))
    async def on_diag(event):
        try:
            import telethon
            from telethon.tl.custom import Button as CB
            methods = [m for m in dir(CB) if not m.startswith("_")]
            await event.respond(
                f"telethon: {telethon.__version__}\n"
                f"has web_view: {hasattr(CB, 'web_view')}\n"
                f"has webview: {hasattr(CB, 'webview')}\n"
                f"has request_web_view: {hasattr(CB, 'request_web_view')}\n"
                f"all Button attrs: {methods}"
            )
        except Exception as e:
            log_error("on_diag", e)

    me = await bot.get_me()
    print(f"[bot] запущен как @{me.username}", flush=True)


async def start_web():
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/ping", handle_ping)
    app.router.add_get("/coinflip", handle_coinflip_page)
    app.router.add_get("/api/coin/balance", handle_balance)
    app.router.add_post("/api/coin/balance", handle_balance)
    app.router.add_post("/api/coin/play", handle_play)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"[web] слушаю 0.0.0.0:{PORT}", flush=True)


async def main():
    global data
    data = load_data()
    await start_web()
    try:
        await start_bot()
    except Exception as e:
        log_error("start_bot", e)
    await asyncio.Event().wait()


COINFLIP_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Coin Flip</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body {
    margin: 0; background: #0f212e; color: #ffffff;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    min-height: 100vh; display: flex; flex-direction: column; align-items: center;
  }
  .wrap { width: 100%; max-width: 480px; padding: 14px; display: flex; flex-direction: column; gap: 14px; }
  .header {
    background: #1a2c38; border-radius: 12px; padding: 12px 16px;
    display: flex; justify-content: space-between; align-items: center;
  }
  .user { color: #b1bad3; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 55%; }
  .balance { font-weight: 700; color: #00e701; font-size: 16px; }
  .panel { background: #1a2c38; border-radius: 12px; padding: 16px; position: relative; overflow: hidden; }
  .flash { position: absolute; inset: 0; opacity: 0; pointer-events: none; transition: opacity .6s; }
  .flash.win { background: rgba(0,231,1,.25); opacity: 1; }
  .flash.lose { background: rgba(255,77,77,.25); opacity: 1; }
  .coin-area { display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 10px 0; perspective: 800px; }
  .coin {
    width: 200px; height: 200px; border-radius: 50%;
    background: radial-gradient(circle at 35% 30%, #ffe27a, #d9a400 70%);
    border: 6px solid #b78500; display: flex; align-items: center; justify-content: center;
    font-size: 84px; box-shadow: 0 8px 24px rgba(0,0,0,.45);
  }
  .coin.spin { animation: spin 1.5s ease-in-out; }
  @keyframes spin { from { transform: rotateY(0deg); } to { transform: rotateY(720deg); } }
  .result-text { height: 28px; font-size: 22px; font-weight: 800; }
  .result-text.win { color: #00e701; }
  .result-text.lose { color: #ff4d4d; }
  .label { color: #b1bad3; font-size: 13px; margin-bottom: 8px; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; }
  .btn {
    flex: 1; min-width: 70px; background: #2f4553; color: #fff; border: 2px solid transparent;
    border-radius: 10px; padding: 12px 8px; font-size: 15px; font-weight: 600; cursor: pointer;
  }
  .btn.active { background: #00e701; color: #0f212e; }
  .btn:disabled { opacity: .55; cursor: not-allowed; }
  #customInput {
    display: none; width: 100%; margin-top: 8px; background: #0f212e; color: #fff;
    border: 2px solid #2f4553; border-radius: 10px; padding: 12px; font-size: 16px;
  }
  .flip {
    width: 100%; background: #00e701; color: #0f212e; border: none; border-radius: 12px;
    padding: 18px; font-size: 20px; font-weight: 800; cursor: pointer;
  }
  .flip:disabled { opacity: .55; cursor: not-allowed; }
  .error { color: #ff4d4d; font-size: 14px; text-align: center; min-height: 18px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="user" id="userName">Игрок</div>
    <div class="balance"><span id="balance">—</span> GRAM</div>
  </div>

  <div class="panel">
    <div class="flash" id="flash"></div>
    <div class="coin-area">
      <div class="coin" id="coin">🪙</div>
      <div class="result-text" id="resultText"></div>
    </div>
  </div>

  <div class="panel">
    <div class="label">Выбор стороны</div>
    <div class="row">
      <button class="btn active" data-choice="heads">🦅 Орёл</button>
      <button class="btn" data-choice="tails">🔢 Решка</button>
    </div>
  </div>

  <div class="panel">
    <div class="label">Ставка</div>
    <div class="row" id="bets">
      <button class="btn" data-bet="100">100</button>
      <button class="btn" data-bet="500">500</button>
      <button class="btn active" data-bet="1000">1000</button>
      <button class="btn" data-bet="5000">5000</button>
      <button class="btn" data-bet="custom">Своя ставка</button>
    </div>
    <input id="customInput" type="number" inputmode="numeric" min="1" placeholder="Введите ставку">
  </div>

  <div class="error" id="error"></div>
  <button class="flip" id="flipBtn">🎲 ФЛИП</button>
</div>

<script>
(function () {
  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); }
  var initData = (tg && tg.initData) || "";

  var state = { choice: "heads", bet: 1000, custom: false, busy: false };
  var el = function (id) { return document.getElementById(id); };
  var coin = el("coin"), flash = el("flash"), resultText = el("resultText");
  var errorEl = el("error"), flipBtn = el("flipBtn"), customInput = el("customInput");

  if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {
    var u = tg.initDataUnsafe.user;
    el("userName").textContent = u.first_name || u.username || "Игрок";
  }

  function showError(msg) { errorEl.textContent = msg || ""; }
  function fmt(n) { return Number(n).toLocaleString("ru-RU"); }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  async function loadBalance() {
    try {
      var r = await fetch("/api/coin/balance?init_data=" + encodeURIComponent(initData));
      var j = await r.json();
      if (r.ok) { el("balance").textContent = fmt(j.balance); }
      else { showError(j.error === "Invalid signature" ? "Открой игру через бота" : (j.error || "Ошибка")); }
    } catch (e) { showError("Нет связи с сервером"); }
  }

  document.querySelectorAll("[data-choice]").forEach(function (b) {
    b.addEventListener("click", function () {
      if (state.busy) return;
      state.choice = b.dataset.choice;
      document.querySelectorAll("[data-choice]").forEach(function (x) { x.classList.toggle("active", x === b); });
    });
  });

  document.querySelectorAll("[data-bet]").forEach(function (b) {
    b.addEventListener("click", function () {
      if (state.busy) return;
      document.querySelectorAll("[data-bet]").forEach(function (x) { x.classList.toggle("active", x === b); });
      if (b.dataset.bet === "custom") {
        state.custom = true;
        customInput.style.display = "block";
        customInput.focus();
        var v = parseInt(customInput.value, 10);
        state.bet = v > 0 ? v : 0;
      } else {
        state.custom = false;
        customInput.style.display = "none";
        state.bet = parseInt(b.dataset.bet, 10);
      }
    });
  });
  customInput.addEventListener("input", function () {
    var v = parseInt(customInput.value, 10);
    state.bet = v > 0 ? v : 0;
  });

  function setBusy(v) {
    state.busy = v;
    flipBtn.disabled = v;
    document.querySelectorAll(".btn").forEach(function (b) { b.disabled = v; });
  }

  flipBtn.addEventListener("click", async function () {
    if (state.busy) return;
    showError("");
    if (!(state.bet > 0)) { showError("Укажи ставку"); return; }

    setBusy(true);
    flash.className = "flash";
    resultText.textContent = "";
    resultText.className = "result-text";
    coin.textContent = "🪙";
    coin.classList.remove("spin");
    void coin.offsetWidth;
    coin.classList.add("spin");

    var started = Date.now();
    var data = null, ok = false;
    try {
      var r = await fetch("/api/coin/play", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ init_data: initData, bet: state.bet, choice: state.choice })
      });
      data = await r.json();
      ok = r.ok;
    } catch (e) {
      data = { error: "network" };
    }

    var left = 1500 - (Date.now() - started);
    if (left > 0) await sleep(left);
    coin.classList.remove("spin");

    if (!ok) {
      coin.textContent = "🪙";
      if (data && data.error === "insufficient_balance") {
        showError("Недостаточно средств. Баланс: " + fmt(data.balance) + " GRAM");
        el("balance").textContent = fmt(data.balance);
      } else if (data && data.error === "Invalid signature") {
        showError("Открой игру через бота в Telegram");
      } else if (data && data.error === "network") {
        showError("Нет связи с сервером");
      } else {
        showError((data && data.error) || "Ошибка сервера");
      }
      setBusy(false);
      return;
    }

    coin.textContent = data.result === "heads" ? "🦅" : "🔢";
    el("balance").textContent = fmt(data.new_balance);

    if (data.won) {
      var net = data.payout - data.bet;
      resultText.textContent = "+" + fmt(net) + " GRAM";
      resultText.className = "result-text win";
      flash.className = "flash win";
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    } else {
      resultText.textContent = "-" + fmt(data.bet) + " GRAM";
      resultText.className = "result-text lose";
      flash.className = "flash lose";
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("error");
    }
    setTimeout(function () { flash.className = "flash"; }, 900);
    setBusy(false);
  });

  loadBalance();
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    asyncio.run(main())
