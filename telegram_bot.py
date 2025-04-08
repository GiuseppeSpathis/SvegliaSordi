# telegram_bot.py
# Gestisce il bot Telegram, salva/legge allarmi su Firebase,
# controlla l'ora e invia trigger al Pi tramite Firebase.

import json
import logging
from datetime import datetime, timedelta
import pytz
import time
import threading
import signal # Per gestire SIGTERM/SIGINT nel thread

# Import librerie necessarie
try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, CallbackContext
except ImportError:
    print("Errore: Libreria python-telegram-bot non trovata. Installala con 'pip install python-telegram-bot'")
    exit()

try:
    import firebase_admin
    from firebase_admin import credentials, db
except ImportError:
    print("Errore: Libreria firebase-admin non trovata. Installala con 'pip install firebase-admin'")
    exit()

# --- Configurazione Utente ---
BOT_TOKEN = "7824989957:AAETWh9iqhzKDChpgDZ1GsMGijHUusOxziI" # <-- SOSTITUISCI CON IL TUO TOKEN
PATH_TO_FIREBASE_KEY = "firebaseKey.json" # <-- Assicurati che il nome file sia corretto
FIREBASE_DB_URL = "https://svegliasordi-default-rtdb.europe-west1.firebasedatabase.app" # <-- SOSTITUISCI (es: https://nome-progetto-default-rtdb.europe-west1.firebasedatabase.app)
PI_ID = "pi1" # Identificativo del Raspberry Pi da triggerare
TIMEZONE = "Europe/Rome"
# --- Fine Configurazione Utente ---

# Setup Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Flag per controllare il thread in background
keep_running = True

# Inizializza Firebase Admin SDK
try:
    tz_info = pytz.timezone(TIMEZONE)
    cred = credentials.Certificate(PATH_TO_FIREBASE_KEY)
    firebase_admin.initialize_app(cred, {
        'databaseURL': FIREBASE_DB_URL
    })
    logger.info("Firebase Admin SDK inizializzato correttamente.")
except pytz.UnknownTimeZoneError:
     logger.error(f"Errore: Fuso orario '{TIMEZONE}' non riconosciuto da pytz.")
     exit()
except FileNotFoundError:
     logger.error(f"Errore: File chiave Firebase '{PATH_TO_FIREBASE_KEY}' non trovato.")
     exit()
except Exception as e:
    logger.error(f"Errore fatale durante l'inizializzazione di Firebase: {e}", exc_info=True)
    exit()

# --- Funzioni Database Firebase ---

def load_alarms_for_user(user_id: str) -> list:
    """Carica gli allarmi per un utente specifico da Firebase."""
    try:
        ref = db.reference(f'/alarms/{user_id}')
        alarms = ref.get()
        # Assicura che ritorni sempre una lista
        return alarms if isinstance(alarms, list) else []
    except Exception as e:
        logger.error(f"Errore leggendo allarmi per {user_id} da Firebase: {e}", exc_info=True)
        return []

def save_alarms_for_user(user_id: str, alarms: list):
    """Salva la lista completa di allarmi per un utente specifico su Firebase."""
    try:
        ref = db.reference(f'/alarms/{user_id}')
        ref.set(alarms) # Sovrascrive la lista esistente
    except Exception as e:
        logger.error(f"Errore salvando allarmi per {user_id} su Firebase: {e}", exc_info=True)

def load_all_alarms() -> dict:
    """Carica TUTTI gli allarmi di TUTTI gli utenti da Firebase."""
    try:
        ref = db.reference('/alarms')
        all_alarms_dict = ref.get()
        return all_alarms_dict if isinstance(all_alarms_dict, dict) else {}
    except Exception as e:
        logger.error(f"Errore leggendo tutti gli allarmi da Firebase: {e}", exc_info=True)
        return {}

# --- Funzioni Handler Comandi Bot ---

async def start(update: Update, context: CallbackContext):
    welcome_message = (
        "👋 Ciao! Sono il tuo bot per le sveglie per sordi.\n\n"
        "✅ Comandi:\n"
        "🔹 `/add YYYY-MM-DD HH:MM`\n"
        "🔹 `/list`\n"
        "🔹 `/delete ID`\n\n"
        "💾 Sveglie su Firebase! 🔥"
    )
    await update.message.reply_text(welcome_message)

async def add_alarm(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    if len(context.args) != 2:
        await update.message.reply_text("❌ Formato: `/add YYYY-MM-DD HH:MM`")
        return

    date_str, time_str = context.args
    try:
        # Validazione data/ora
        alarm_dt_naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        alarm_dt_aware = tz_info.localize(alarm_dt_naive)
        now_aware = datetime.now(tz_info)

        # Controllo se è nel passato (con tolleranza di un minuto)
        if alarm_dt_aware < now_aware - timedelta(minutes=1):
            await update.message.reply_text("⏳ Non puoi impostare sveglie nel passato!")
            return

        user_alarms = load_alarms_for_user(user_id)
        new_alarm = {"date": date_str, "time": time_str}

        if new_alarm in user_alarms:
             await update.message.reply_text("⚠️ Sveglia già impostata!")
             return

        user_alarms.append(new_alarm)
        save_alarms_for_user(user_id, user_alarms)
        await update.message.reply_text(f"✅ Sveglia impostata: {date_str} {time_str}")

    except ValueError:
        await update.message.reply_text("❌ Formato data/ora non valido (YYYY-MM-DD HH:MM)")
    except Exception as e:
         logger.error(f"Errore in add_alarm: {e}", exc_info=True)
         await update.message.reply_text("❌ Errore interno.")

async def list_alarms(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    user_alarms = load_alarms_for_user(user_id)

    if not user_alarms:
        await update.message.reply_text("🔕 Nessuna sveglia impostata.")
        return

    message = "⏰ Le tue sveglie:\n"
    try:
        # Ordina per data e ora
        sorted_alarms = sorted(user_alarms, key=lambda x: f"{x.get('date', '0000-00-00')} {x.get('time', '00:00')}")
    except Exception as e:
         logger.error(f"Errore nella struttura dati degli allarmi per {user_id}: {user_alarms} - {e}")
         await update.message.reply_text("❌ Errore nel leggere i dati delle sveglie.")
         return

    for i, alarm in enumerate(sorted_alarms):
        message += f"{i+1}. {alarm.get('date', 'N/D')} alle {alarm.get('time', 'N/D')}\n"
    await update.message.reply_text(message)

async def delete_alarm(update: Update, context: CallbackContext):
    user_id = str(update.message.from_user.id)
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❌ Specifica l'ID numerico da /list.")
        return

    user_alarms = load_alarms_for_user(user_id)
    if not user_alarms:
        await update.message.reply_text("🔕 Non hai sveglie da eliminare.")
        return

    try:
        # Ordina come in /list per far corrispondere l'ID
        sorted_alarms = sorted(user_alarms, key=lambda x: f"{x.get('date', '0000-00-00')} {x.get('time', '00:00')}")
        alarm_index_to_delete = int(context.args[0]) - 1 # Indice basato su 0

        if 0 <= alarm_index_to_delete < len(sorted_alarms):
            alarm_to_remove = sorted_alarms[alarm_index_to_delete]
            # Rimuovi l'allarme dalla lista originale (non ordinata)
            try:
                user_alarms.remove(alarm_to_remove)
                save_alarms_for_user(user_id, user_alarms)
                await update.message.reply_text(f"🗑️ Sveglia {alarm_to_remove.get('date')} {alarm_to_remove.get('time')} eliminata.")
            except ValueError:
                 await update.message.reply_text("❌ Errore: Sveglia non trovata nella lista originale (potrebbe essere un bug).")
            except Exception as e_save:
                 logger.error(f"Errore salvataggio dopo delete: {e_save}")
                 await update.message.reply_text("❌ Errore durante il salvataggio.")
        else:
            await update.message.reply_text("❌ ID non valido.")
    except Exception as e:
        logger.error(f"Errore in delete_alarm: {e}", exc_info=True)
        await update.message.reply_text("❌ Errore durante l'eliminazione.")

# --- Logica di Controllo e Trigger Allarmi in Background ---

def check_and_trigger_alarms_runner():
    """Loop principale del thread che controlla gli allarmi."""
    global keep_running
    logger.info("Thread check_and_trigger_alarms: Avviato.")
    trigger_ref = db.reference(f'/triggers/{PI_ID}')

    while keep_running:
        now_aware = datetime.now(tz_info)
        current_date_str = now_aware.strftime("%Y-%m-%d")
        current_time_str = now_aware.strftime("%H:%M") # Confronto solo HH:MM

        # --- Logica di controllo allarmi e cancellazione ---
        trigger_active_this_minute = False
        alarms_to_delete_this_minute = []

        try:
            logger.debug(f"Controllo allarmi per {current_date_str} {current_time_str}")
            all_alarms = load_all_alarms()

            if isinstance(all_alarms, dict):
                for user_id, user_alarms in all_alarms.items():
                    if isinstance(user_alarms, list):
                        for alarm in user_alarms:
                             # Controlla se l'allarme è valido e corrisponde
                            if isinstance(alarm, dict) and alarm.get("date") == current_date_str and alarm.get("time") == current_time_str:
                                logger.info(f"MATCH! Allarme per {user_id} alle {current_date_str} {current_time_str}")
                                trigger_active_this_minute = True
                                alarms_to_delete_this_minute.append({"user_id": user_id, "alarm_data": alarm})
            else:
                 logger.warning("load_all_alarms non ha restituito un dizionario.")

            # --- Scrittura Trigger ---
            if trigger_active_this_minute:
                logger.info(f"Invio trigger=True a Firebase per {PI_ID}")
                trigger_ref.set(True)
            else:
                current_trigger_value = trigger_ref.get()
                if current_trigger_value is not False:
                     logger.info(f"Reset trigger=False a Firebase per {PI_ID}")
                     trigger_ref.set(False)

            # --- Cancellazione Allarmi Triggerati ---
            if alarms_to_delete_this_minute:
                logger.info(f"Procedo a cancellare {len(alarms_to_delete_this_minute)} allarmi triggerati...")
                processed_users = set() # Per caricare ogni lista utente una sola volta
                user_alarm_map = {} # Mappa user_id -> lista allarmi attuale

                # Raccogli gli allarmi da eliminare per utente
                alarms_by_user_to_delete = {}
                for item in alarms_to_delete_this_minute:
                    uid = item["user_id"]
                    adata = item["alarm_data"]
                    if uid not in alarms_by_user_to_delete:
                        alarms_by_user_to_delete[uid] = []
                    alarms_by_user_to_delete[uid].append(adata)

                # Processa ogni utente
                for del_user_id, alarms_to_remove in alarms_by_user_to_delete.items():
                    try:
                        current_user_alarms = load_alarms_for_user(del_user_id)
                        if isinstance(current_user_alarms, list):
                            original_count = len(current_user_alarms)
                            # Rimuovi tutti gli allarmi triggerati per questo utente
                            updated_user_alarms = [a for a in current_user_alarms if a not in alarms_to_remove]
                            removed_count = original_count - len(updated_user_alarms)

                            if removed_count > 0:
                                logger.info(f"Rimossi {removed_count} allarmi per user {del_user_id}. Salvo lista aggiornata.")
                                save_alarms_for_user(del_user_id, updated_user_alarms)
                            else:
                                logger.warning(f"Nessun allarme trovato da rimuovere per user {del_user_id} (forse già cancellati?).")
                        else:
                            logger.error(f"Impossibile cancellare, load_alarms_for_user non ha restituito lista per {del_user_id}")
                    except Exception as e_del:
                         logger.error(f"Errore durante la cancellazione allarmi per user {del_user_id}: {e_del}", exc_info=True)


        except Exception as e:
            logger.error(f"Errore nel ciclo principale di check_and_trigger_alarms_runner: {e}", exc_info=True)

        # --- Calcolo Sleep fino al prossimo minuto ---
        if keep_running:
            now_for_sleep = datetime.now(tz_info)
            # Calcola i secondi mancanti al prossimo minuto (e 0.1 secondi)
            seconds_to_next_minute = 60.1 - now_for_sleep.second - (now_for_sleep.microsecond / 1_000_000.0)
            # Dormi per il tempo calcolato (min 0.1s, max 60.1s)
            sleep_duration = max(0.1, min(seconds_to_next_minute, 60.1))
            logger.debug(f"Attendo {sleep_duration:.2f} secondi per il controllo del prossimo minuto...")
            # Usa un sistema di attesa che possa essere interrotto
            interrupted = threading.Event().wait(sleep_duration)
            if not keep_running: # Controlla di nuovo dopo lo sleep/wait
                 break

    logger.info("Thread check_and_trigger_alarms: Terminato.")

# --- Funzione Principale ---
def main():
    global keep_running

    # Gestione segnali di terminazione per fermare il thread
    def signal_handler(signum, frame):
        global keep_running
        if keep_running:
            logger.info(f"Ricevuto segnale {signal.Signals(signum).name}. Avvio chiusura...")
            keep_running = False # Segnala al thread di fermarsi
        else:
            logger.warning("Chiusura già in corso.")

    signal.signal(signal.SIGINT, signal_handler) # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler) # kill

    # Avvia il thread per il controllo degli allarmi
    alarm_checker_thread = threading.Thread(target=check_and_trigger_alarms_runner, name="AlarmChecker", daemon=True)
    alarm_checker_thread.start()
    logger.info("Thread controllo allarmi avviato.")

    # Configura e avvia il bot Telegram
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add_alarm))
    application.add_handler(CommandHandler("list", list_alarms))
    application.add_handler(CommandHandler("delete", delete_alarm))

    logger.info("Bot avviato! Premere Ctrl+C per fermare.")

    # Esegui il bot finché non viene fermato
    # run_polling è bloccante, ma il signal_handler lo interromperà
    application.run_polling(allowed_updates=Update.ALL_TYPES)

    # Dopo che run_polling termina (a causa del segnale)
    logger.info("Polling del bot fermato. Attendo termine thread controllo allarmi...")
    if alarm_checker_thread.is_alive():
        alarm_checker_thread.join(timeout=10) # Attendi max 10 sec che il thread finisca

    if alarm_checker_thread.is_alive():
         logger.warning("Timeout attesa thread controllo allarmi. Potrebbe non essersi fermato correttamente.")

    logger.info("Applicazione terminata.")

if __name__ == "__main__":
    main()
