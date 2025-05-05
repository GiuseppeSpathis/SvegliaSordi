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
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackContext
import firebase_admin
from firebase_admin import credentials, db


# --- Configurazione Utente ---
BOT_TOKEN = "7824989957:AAETWh9iqhzKDChpgDZ1GsMGijHUusOxziI" 
PATH_TO_FIREBASE_KEY = "firebaseKey.json" 
FIREBASE_DB_URL = "https://svegliasordi-default-rtdb.europe-west1.firebasedatabase.app" 
PI_ID = "pi1" # Identificativo del Raspberry Pi da triggerare
TIMEZONE = "Europe/Rome"
# --- Fine Configurazione Utente ---

# Setup Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)
keep_running = True
try:
    tz_info = pytz.timezone(TIMEZONE)
    cred = credentials.Certificate(PATH_TO_FIREBASE_KEY)
    firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
    logger.info("Firebase Admin SDK inizializzato correttamente.")
except Exception as e:
     logger.error(f"Errore inizializzazione Firebase: {e}", exc_info=True); exit()

# --- Funzioni Database Firebase (Modificate per struttura /alarms/{pi_id}) ---

def get_pi_id_for_user(user_id: str) -> str | None:
    """Recupera il pi_id associato a un utente da /pairings/{user_id}."""
    try:
        ref = db.reference(f'/pairings/{user_id}')
        pi_id = ref.get()
        return str(pi_id) if pi_id else None
    except Exception as e:
        logger.error(f"Errore leggendo pairing per {user_id}: {e}")
        return None

def save_pairing(user_id: str, pi_id: str):
    """Salva l'associazione utente-Pi."""
    try:
        ref = db.reference(f'/pairings/{user_id}')
        ref.set(pi_id)
    except Exception as e:
        logger.error(f"Errore salvando pairing per {user_id} -> {pi_id}: {e}")

def delete_pairing(user_id: str):
     """Rimuove l'associazione utente-Pi."""
     try:
         ref = db.reference(f'/pairings/{user_id}')
         ref.delete()
     except Exception as e:
         logger.error(f"Errore cancellando pairing per {user_id}: {e}")


def load_alarms_for_pi(pi_id: str) -> list:
    """Carica gli allarmi per un Pi specifico da /alarms/{pi_id}."""
    if not pi_id: return []
    try:
        ref = db.reference(f'/alarms/{pi_id}')
        alarms = ref.get()
        return alarms if isinstance(alarms, list) else []
    except Exception as e:
        logger.error(f"Errore leggendo allarmi per {pi_id}: {e}")
        return []

def save_alarms_for_pi(pi_id: str, alarms: list):
    """Salva la lista allarmi per un Pi specifico su /alarms/{pi_id}."""
    if not pi_id: return
    try:
        ref = db.reference(f'/alarms/{pi_id}')
        ref.set(alarms)
    except Exception as e:
        logger.error(f"Errore salvando allarmi per {pi_id}: {e}")

def load_all_pi_alarms() -> dict:
    """Carica TUTTI gli allarmi di TUTTI i Pi da /alarms."""
    try:
        ref = db.reference('/alarms')
        all_alarms_dict = ref.get()
        # Filtra eventuali valori non-dizionario o chiavi non-stringa (poco probabile ma sicuro)
        return {str(k): v for k, v in all_alarms_dict.items() if isinstance(k, str) and isinstance(v, list)} if isinstance(all_alarms_dict, dict) else {}
    except Exception as e:
        logger.error(f"Errore leggendo tutti gli allarmi dei Pi: {e}")
        return {}

# --- Decorator per Controllo Pairing ---
from functools import wraps

def require_pairing(func):
    """Decorator per verificare se l'utente è associato a un Pi."""
    @wraps(func)
    async def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = str(update.effective_user.id)
        pi_id = get_pi_id_for_user(user_id)
        if not pi_id:
            await update.message.reply_text(
                "❗️ Non sei associato a nessun dispositivo.\n"
                "Premi il bottone giallo sul tuo Raspberry Pi per vedere il suo ID, "
                "poi usa il comando:\n`/pair ID_DEL_TUO_PI`"
            )
            return None # Blocca l'esecuzione del comando
        # Passa il pi_id al contesto per usarlo nel comando
        context.user_data['pi_id'] = pi_id
        return await func(update, context, *args, **kwargs)
    return wrapper

# --- Funzioni Handler Comandi Bot (Aggiornate) ---

async def start(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    pi_id = get_pi_id_for_user(user_id)
    welcome_message = (
        f"👋 Ciao, {update.effective_user.first_name}!\n"
    )
    if pi_id:
         welcome_message += f"Sei associato al dispositivo: `{pi_id}`\n\n"
    else:
         welcome_message += "Non sei associato a nessun dispositivo.\nUsa `/pair ID_PI` per iniziare.\n\n"

    welcome_message += (
        "✅ Comandi:\n"
        "🔹 `/pair ID_PI` - Associa questo bot al tuo rasperry pi usando il pi\\_id visibile cliccando sul bottone giallo del rasperry\n"
        "🔹 `/unpair` - Dissocia questo bot dal tuo rasperry\n"
        "🔹 `/add YYYY-MM-DD HH:MM` - Aggiungi sveglia (richiede pairing)\n"
        "🔹 `/list` - Mostra sveglie (richiede pairing)\n"
        "🔹 `/delete ID` - Elimina sveglia (richiede pairing)\n\n"
        "💾 Sveglie su Firebase! 🔥"
    )
    await update.message.reply_text(welcome_message, parse_mode='Markdown')


async def pair_command(update: Update, context: CallbackContext):
    """Associa l'utente Telegram a un Pi ID."""
    user_id = str(update.effective_user.id)
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("❌ Formato non valido. Usa: `/pair ID_DEL_TUO_PI`\n(Trovi l'ID premendo il bottone giallo sul Pi).")
        return
    pi_id_to_pair = context.args[0].strip()
    # Potresti aggiungere validazioni sull'ID qui (es. lunghezza, caratteri)
    if not pi_id_to_pair:
         await update.message.reply_text("❌ ID non valido.")
         return

    save_pairing(user_id, pi_id_to_pair)
    logger.info(f"Utente {user_id} associato a Pi {pi_id_to_pair}")
    await update.message.reply_text(f"✅ Associato con successo al dispositivo `{pi_id_to_pair}`!", parse_mode='Markdown')

async def unpair_command(update: Update, context: CallbackContext):
     """Dissocia l'utente Telegram dal Pi."""
     user_id = str(update.effective_user.id)
     pi_id = get_pi_id_for_user(user_id)
     if not pi_id:
         await update.message.reply_text("ℹ️ Non sei attualmente associato a nessun dispositivo.")
         return

     delete_pairing(user_id)
     logger.info(f"Utente {user_id} dissociato dal Pi {pi_id}")
     await update.message.reply_text(f"✅ Associazione con il dispositivo `{pi_id}` rimossa.", parse_mode='Markdown')


@require_pairing # Applica il controllo prima di eseguire
async def add_alarm(update: Update, context: CallbackContext):
    # pi_id è ora disponibile da context.user_data grazie al decorator
    pi_id = context.user_data.get('pi_id')
    # user_id = str(update.message.from_user.id) # Non più necessario qui

    if len(context.args) != 2:
        await update.message.reply_text("❌ Formato: `/add YYYY-MM-DD HH:MM`")
        return

    date_str, time_str = context.args
    try:
        # ... (stessa validazione data/ora di prima) ...
        alarm_dt_naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        alarm_dt_aware = tz_info.localize(alarm_dt_naive)
        now_aware = datetime.now(tz_info)
        if alarm_dt_aware < now_aware - timedelta(minutes=1):
            await update.message.reply_text("⏳ Sveglia nel passato!"); return

        # Carica/Salva allarmi per il PI associato
        pi_alarms = load_alarms_for_pi(pi_id)
        new_alarm = {"date": date_str, "time": time_str}

        if new_alarm in pi_alarms:
             await update.message.reply_text("⚠️ Sveglia già impostata per questo dispositivo!"); return

        pi_alarms.append(new_alarm)
        save_alarms_for_pi(pi_id, pi_alarms)
        await update.message.reply_text(f"✅ Sveglia per `{pi_id}`: {date_str} {time_str}", parse_mode='Markdown')

    except ValueError:
        await update.message.reply_text("❌ Formato data/ora non valido (YYYY-MM-DD HH:MM)")
    except Exception as e:
         logger.error(f"Errore in add_alarm per {pi_id}: {e}", exc_info=True)
         await update.message.reply_text("❌ Errore interno.")


@require_pairing # Applica il controllo
async def list_alarms(update: Update, context: CallbackContext):
    pi_id = context.user_data.get('pi_id')
    pi_alarms = load_alarms_for_pi(pi_id)

    if not pi_alarms:
        await update.message.reply_text(f"🔕 Nessuna sveglia impostata per `{pi_id}`.", parse_mode='Markdown')
        return

    message = f"⏰ Sveglie per `{pi_id}`:\n" # Mostra a quale Pi si riferiscono
    try:
        sorted_alarms = sorted(pi_alarms, key=lambda x: f"{x.get('date', '0000-00-00')} {x.get('time', '00:00')}")
    except Exception as e:
         logger.error(f"Errore struttura dati allarmi per {pi_id}: {pi_alarms} - {e}")
         await update.message.reply_text("❌ Errore leggendo dati sveglie."); return

    for i, alarm in enumerate(sorted_alarms):
        message += f"{i+1}. {alarm.get('date', 'N/D')} alle {alarm.get('time', 'N/D')}\n"
    await update.message.reply_text(message)


@require_pairing # Applica il controllo
async def delete_alarm(update: Update, context: CallbackContext):
    pi_id = context.user_data.get('pi_id')
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❌ Specifica l'ID numerico da `/list`.")
        return

    pi_alarms = load_alarms_for_pi(pi_id)
    if not pi_alarms:
        await update.message.reply_text(f"🔕 Nessuna sveglia da eliminare per `{pi_id}`.", parse_mode='Markdown')
        return

    try:
        # Ordina come in /list per far corrispondere l'ID
        sorted_alarms = sorted(pi_alarms, key=lambda x: f"{x.get('date', '0000-00-00')} {x.get('time', '00:00')}")
        alarm_index_to_delete = int(context.args[0]) - 1

        if 0 <= alarm_index_to_delete < len(sorted_alarms):
            alarm_to_remove = sorted_alarms[alarm_index_to_delete]
            try:
                # Rimuovi dalla lista originale
                pi_alarms.remove(alarm_to_remove)
                save_alarms_for_pi(pi_id, pi_alarms)
                await update.message.reply_text(f"🗑️ Sveglia {alarm_to_remove.get('date')} {alarm_to_remove.get('time')} eliminata per `{pi_id}`.", parse_mode='Markdown')
            except ValueError:
                 await update.message.reply_text("❌ Errore: Sveglia non trovata (bug?).")
            except Exception as e_save:
                 logger.error(f"Errore salvataggio dopo delete per {pi_id}: {e_save}")
                 await update.message.reply_text("❌ Errore durante il salvataggio.")
        else:
            await update.message.reply_text("❌ ID non valido.")
    except Exception as e:
        logger.error(f"Errore in delete_alarm per {pi_id}: {e}", exc_info=True)
        await update.message.reply_text("❌ Errore durante l'eliminazione.")
        
        
# --- Funzione per Controllo e Trigger Allarmi ---

def check_and_trigger_alarms_runner():
    """Loop principale del thread che controlla gli allarmi per tutti i Pi."""
    global keep_running
    logger.info("Thread check_and_trigger_alarms: Avviato.")
    # Non serve più riferimento globale al trigger, lo cerchiamo dinamicamente

    while keep_running:
        now_aware = datetime.now(tz_info)
        current_date_str = now_aware.strftime("%Y-%m-%d")
        current_time_str = now_aware.strftime("%H:%M") # Confronto HH:MM

        triggered_pi_ids_this_minute = set() # Pi che hanno avuto un allarme
        alarms_to_delete_this_minute = [] # Lista di {"pi_id": ..., "alarm_data": ...}

        try:
            logger.debug(f"Controllo allarmi per {current_date_str} {current_time_str}")
            all_pi_alarms = load_all_pi_alarms() # Carica {"pi_id": [alarms...]}

            if not isinstance(all_pi_alarms, dict):
                 logger.warning("load_all_pi_alarms non ha restituito un dizionario.")
                 all_pi_alarms = {}

            # Scansiona allarmi per ogni Pi registrato
            for pi_id, pi_alarms_list in all_pi_alarms.items():
                if isinstance(pi_alarms_list, list):
                    for alarm in pi_alarms_list:
                        # Controlla corrispondenza
                        if isinstance(alarm, dict) and alarm.get("date") == current_date_str and alarm.get("time") == current_time_str:
                            logger.info(f"MATCH! Allarme per PI `{pi_id}` alle {current_date_str} {current_time_str}")
                            triggered_pi_ids_this_minute.add(pi_id)
                            alarms_to_delete_this_minute.append({"pi_id": pi_id, "alarm_data": alarm})
                else:
                     logger.warning(f"Struttura allarmi non valida per pi_id {pi_id}")


            # --- Scrittura/Reset Triggers ---
            # Per semplicità, vengono gestiti tutti i triggers uno per uno
            active_triggers_ref = db.reference('/triggers')
            try:
                 # Leggi tutti i trigger attuali per sapere quali resettare
                 current_triggers = active_triggers_ref.get()
                 if not isinstance(current_triggers, dict): current_triggers = {}
            except Exception as e_read_triggers:
                 logger.error(f"Errore leggendo i triggers esistenti: {e_read_triggers}")
                 current_triggers = {}


            # Imposta i trigger per i Pi attivi questo minuto
            for pi_id_to_trigger in triggered_pi_ids_this_minute:
                try:
                    # Scrivi True solo se non è già True per evitare scritture inutili
                    if current_triggers.get(pi_id_to_trigger) is not True:
                         logger.info(f"Invio trigger=True a Firebase per PI `{pi_id_to_trigger}`")
                         db.reference(f'/triggers/{pi_id_to_trigger}').set(True)
                    else:
                         logger.debug(f"Trigger per PI `{pi_id_to_trigger}` già True.")
                except Exception as e_set:
                     logger.error(f"Errore impostando trigger per {pi_id_to_trigger}: {e_set}")

            # Resetta i trigger per i Pi che erano attivi ma non lo sono questo minuto
            for pi_id_was_active, trigger_value in current_triggers.items():
                 if trigger_value is True and pi_id_was_active not in triggered_pi_ids_this_minute:
                     try:
                         logger.info(f"Reset trigger=False a Firebase per PI `{pi_id_was_active}`")
                         db.reference(f'/triggers/{pi_id_was_active}').set(False)
                     except Exception as e_reset:
                          logger.error(f"Errore resettando trigger per {pi_id_was_active}: {e_reset}")


            # --- Cancellazione Allarmi Triggerati ---
            if alarms_to_delete_this_minute:
                logger.info(f"Procedo a cancellare {len(alarms_to_delete_this_minute)} allarmi triggerati...")
                # Raggruppa per Pi ID per ottimizzare le scritture
                alarms_by_pi_to_delete = {}
                for item in alarms_to_delete_this_minute:
                    pid = item["pi_id"]
                    adata = item["alarm_data"]
                    if pid not in alarms_by_pi_to_delete:
                        alarms_by_pi_to_delete[pid] = []
                    alarms_by_pi_to_delete[pid].append(adata)

                # Processa ogni Pi che ha allarmi da cancellare
                for del_pi_id, alarms_to_remove in alarms_by_pi_to_delete.items():
                    try:
                        current_pi_alarms = load_alarms_for_pi(del_pi_id) # Ricarica lista attuale
                        if isinstance(current_pi_alarms, list):
                            original_count = len(current_pi_alarms)
                            updated_pi_alarms = [a for a in current_pi_alarms if a not in alarms_to_remove]
                            removed_count = original_count - len(updated_pi_alarms)

                            if removed_count > 0:
                                logger.info(f"Rimossi {removed_count} allarmi per PI `{del_pi_id}`. Salvo lista aggiornata.")
                                save_alarms_for_pi(del_pi_id, updated_pi_alarms)
                            else:
                                logger.warning(f"Nessun allarme trovato da rimuovere per PI `{del_pi_id}`.")
                        else:
                             logger.error(f"Impossibile cancellare, load_alarms_for_pi non ha restituito lista per `{del_pi_id}`")
                    except Exception as e_del:
                         logger.error(f"Errore durante la cancellazione allarmi per PI `{del_pi_id}`: {e_del}", exc_info=True)


        except Exception as e:
            logger.error(f"Errore nel ciclo principale di check_and_trigger_alarms_runner: {e}", exc_info=True)

        # --- Calcolo Sleep fino al prossimo minuto (invariato) ---
        if keep_running:
            now_for_sleep = datetime.now(tz_info)
            seconds_to_next_minute = 60.1 - now_for_sleep.second - (now_for_sleep.microsecond / 1_000_000.0)
            sleep_duration = max(0.1, min(seconds_to_next_minute, 60.1))
            logger.debug(f"Attendo {sleep_duration:.2f} secondi per controllo prossimo minuto...")
            interrupted = threading.Event().wait(sleep_duration)
            if not keep_running: break # Esci se il flag è cambiato durante lo sleep

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

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


    alarm_checker_thread = threading.Thread(target=check_and_trigger_alarms_runner, name="AlarmChecker", daemon=True)
    alarm_checker_thread.start()
    logger.info("Thread controllo allarmi avviato.")

    application = Application.builder().token(BOT_TOKEN).build()

    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("pair", pair_command))
    application.add_handler(CommandHandler("unpair", unpair_command))
    application.add_handler(CommandHandler("add", add_alarm))
    application.add_handler(CommandHandler("list", list_alarms))
    application.add_handler(CommandHandler("delete", delete_alarm))

    logger.info("Bot avviato! Premere Ctrl+C per fermare.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

    # Chiusura
    logger.info("Polling bot fermato. Attendo termine thread...")
    if alarm_checker_thread.is_alive():
        alarm_checker_thread.join(timeout=10)
    if alarm_checker_thread.is_alive():
         logger.warning("Timeout attesa thread.")
    logger.info("Applicazione terminata.")

if __name__ == "__main__":
    main()
