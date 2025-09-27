import os, re, logging, datetime, sqlite3
from contextlib import closing
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
DB_PATH = os.getenv("DB_PATH", "cafebot.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  taken INTEGER NOT NULL DEFAULT 0,
  paid INTEGER NOT NULL DEFAULT 0,
  UNIQUE(chat_id, name)
);
CREATE TABLE IF NOT EXISTS rounds(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL,
  ts TEXT NOT NULL,
  attendees TEXT NOT NULL,
  payer TEXT NOT NULL,
  amount REAL NOT NULL DEFAULT 0
);
"""

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    with closing(db()) as conn:
        for stmt in SCHEMA.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
        conn.commit()

def parse_people(args):
    joined = " ".join(args).strip()
    if not joined:
        return []
    csv = joined.replace(",", " ")
    tokens = re.findall(r'"([^"]+)"|(\S+)', csv)
    people = [t[0] if t[0] else t[1] for t in tokens]
    return [p.strip() for p in people if p.strip()]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "☕ ¡Hola! Soy el bot del café.\n\n"
        "Comandos:\n"
        "• /add Ana Luis Marta — añade personas\n"
        "• /cafe Ana Luis Marta 5.40 — registra ronda y propongo pagador\n"
        "• /stats — saldos por persona\n"
        "• /hist — últimas rondas"
    )

async def add_people(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    people = parse_people(context.args)
    if not people:
        await update.message.reply_text("Uso: /add Ana Luis Marta")
        return
    with closing(db()) as conn:
        cur = conn.cursor()
        for name in people:
            cur.execute("INSERT OR IGNORE INTO persons(chat_id, name) VALUES(?,?)", (chat_id, name))
        conn.commit()
    await update.message.reply_text("✅ Añadidas: " + ", ".join(people))

def ensure_people(conn, chat_id, names):
    cur = conn.cursor()
    for n in names:
        cur.execute("INSERT OR IGNORE INTO persons(chat_id, name) VALUES(?,?)", (chat_id, n))
    conn.commit()

def propose_payer(conn, chat_id, attendees):
    cur = conn.cursor()
    ensure_people(conn, chat_id, attendees)
    best_name, best_score = None, float("inf")
    for n in attendees:
        cur.execute("SELECT taken, paid FROM persons WHERE chat_id=? AND name=?", (chat_id, n))
        row = cur.fetchone()
        taken = row[0] if row else 0
        paid = row[1] if row else 0
        score = paid - (taken + 1)
        if score < best_score:
            best_score = score
            best_name = n
    return best_name

async def cafe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        await update.message.reply_text("Uso: /cafe Ana Luis Marta 5.40 (importe opcional)")
        return

    maybe_amount = args[-1].replace(",", ".")
    amount = 0.0
    people_args = args
    try:
        amount = float(maybe_amount)
        people_args = args[:-1]
    except ValueError:
        pass

    attendees = parse_people(people_args)
    if len(attendees) < 1:
        await update.message.reply_text("Indica al menos una persona: /cafe Ana Luis Marta 5.40")
        return

    with closing(db()) as conn:
        ensure_people(conn, chat_id, attendees)
        payer = propose_payer(conn, chat_id, attendees)
        cur = conn.cursor()
        for n in attendees:
            cur.execute("UPDATE persons SET taken = taken + 1 WHERE chat_id=? AND name=?", (chat_id, n))
        cur.execute("UPDATE persons SET paid = paid + 1 WHERE chat_id=? AND name=?", (chat_id, payer))
        cur.execute("INSERT INTO rounds(chat_id, ts, attendees, payer, amount) VALUES(?,?,?,?,?)",
                    (chat_id, datetime.datetime.utcnow().isoformat(), ", ".join(attendees), payer, amount))
        conn.commit()

    msg = f"☕ Ronda registrada.\n👥 {', '.join(attendees)}\n🧾 Paga: {payer}"
    if amount:
        msg += f"\n💶 {amount:.2f}"
    await update.message.reply_text(msg)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with closing(db()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT name, taken, paid FROM persons WHERE chat_id=? ORDER BY name", (chat_id,))
        rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("No hay datos aún.")
        return
    text = "\n".join([f"{n}: {p - t:+d} (Pagados {p}, Tomados {t})" for n, t, p in rows])
    await update.message.reply_text("📊 Saldos:\n" + text)

async def hist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with closing(db()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT ts, attendees, payer, amount FROM rounds WHERE chat_id=? ORDER BY id DESC LIMIT 10",
                    (chat_id,))
        rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("Aún no hay historial.")
        return
    lines = [f"{r[0][:19]} | {r[1]} | paga: {r[2]} | {r[3]:.2f}" for r in rows]
    await update.message.reply_text("🗒️ Últimas rondas:\n" + "\n".join(lines))

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_people))
    app.add_handler(CommandHandler("cafe", cafe))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("hist", hist))
    app.run_polling()

if __name__ == "__main__":
    main()
