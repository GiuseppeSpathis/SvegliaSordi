from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackContext
import json
import logging
from datetime import datetime

# Sostituisci con il tuo token
TOKEN = "7824989957:AAETWh9iqhzKDChpgDZ1GsMGijHUusOxziI"
ALARMS_FILE = "alarms.json"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

def load_alarms():
    try:
        with open(ALARMS_FILE, "r") as f:
            content = f.read().strip()
            return json.loads(content) if content else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_alarms(alarms):
    with open(ALARMS_FILE, "w") as f:
        json.dump(alarms, f, indent=4)

async def start(update: Update, context: CallbackContext):
    welcome_message = (
        "👋 Ciao, io sono il tuo bot personale per gestire sveglie per sordi! \n\n"
        "✅ Comandi disponibili:\n"
        "🔹 /add YYYY-MM-DD HH:MM → Imposta una sveglia\n"
        "🔹 /list → Mostra tutte le tue sveglie\n"
        "🔹 /delete ID → Elimina una sveglia\n\n"
        "💡 Tutto il codice gira su un Raspberry Pi Zero! 🚀"
    )
    await update.message.reply_text(welcome_message)

async def add_alarm(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)

    if len(context.args) != 2:
        await update.message.reply_text("❌ Formato non valido! Usa: /add YYYY-MM-DD HH:MM")
        return

    date, time = context.args
    try:
        alarm_datetime = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        now = datetime.now()

        # Controllo se la data è nel passato
        if alarm_datetime < now:
            await update.message.reply_text("⏳ Non puoi impostare una sveglia nel passato!")
            return

        alarms = load_alarms()
        if user_id not in alarms:
            alarms[user_id] = []

        # Controllo se la sveglia esiste già
        if any(alarm["date"] == date and alarm["time"] == time for alarm in alarms[user_id]):
            await update.message.reply_text("⚠️ Questa sveglia è già stata impostata!")
            return

        alarms[user_id].append({"date": date, "time": time})
        save_alarms(alarms)

        await update.message.reply_text(f"✅ Sveglia impostata per il {date} alle {time}.")
    except ValueError:
        await update.message.reply_text("❌ Formato errato! Usa: /add YYYY-MM-DD HH:MM")

async def list_alarms(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    alarms = load_alarms()

    if user_id not in alarms or not alarms[user_id]:
        await update.message.reply_text("🔕 Nessuna sveglia impostata.")
        return

    message = "⏰ Le tue sveglie:\n"
    for i, alarm in enumerate(alarms[user_id]):
        message += f"{i+1}. {alarm['date']} alle {alarm['time']}\n"

    await update.message.reply_text(message)

async def delete_alarm(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    alarms = load_alarms()

    if user_id not in alarms or not alarms[user_id]:
        await update.message.reply_text("🔕 Non hai sveglie impostate.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❌ Specificail numero della sveglia da eliminare. Usa /list per vedere gli ID.")
        return

    alarm_id = int(context.args[0]) - 1
    if 0 <= alarm_id < len(alarms[user_id]):
        removed = alarms[user_id].pop(alarm_id)
        save_alarms(alarms)
        await update.message.reply_text(f"🗑️ Sveglia per {removed['date']} alle {removed['time']} eliminata.")
    else:
        await update.message.reply_text("❌ ID non valido.")

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_alarm))
    app.add_handler(CommandHandler("list", list_alarms))
    app.add_handler(CommandHandler("delete", delete_alarm))

    print("🤖 Bot avviato! Aspetto comandi...")
    app.run_polling()

if __name__ == "__main__":
    main()

