# clock.py
# Legge trigger da Firebase, controlla LED/LCD e bottone sul Raspberry Pi.

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("Libreria RPi.GPIO non trovata (necessaria per LCD/LED).")
    # Potresti voler uscire o usare un GPIO fittizio per test senza hardware
    # exit()

try:
    from Adafruit_CharLCD import Adafruit_CharLCD
except ImportError:
     print("Libreria Adafruit_CharLCD non trovata (necessaria per LCD).")
     # exit()

try:
    from gpiozero import Button
except ImportError:
     print("Libreria gpiozero non trovata (necessaria per Button). Installala con 'pip install gpiozero'.")
     # exit()

import time
from time import sleep, strftime
import signal
import sys
import pytz
from datetime import datetime
import requests
import logging
import json

# --- Configurazione Utente ---
# Pin GPIO
LCD_RS = 26
LCD_E  = 19
LCD_D4 = 13
LCD_D5 = 6
LCD_D6 = 5
LCD_D7 = 11
LED_PIN = 25
BUTTON_PIN = 23 # Pin per il bottone di disabilitazione

# Firebase
FIREBASE_DB_URL = "https://svegliasordi-default-rtdb.europe-west1.firebasedatabase.app" # <-- SOSTITUISCI (es: https://nome-progetto-default-rtdb.europe-west1.firebasedatabase.app)
PI_ID = "pi1" # Deve corrispondere a quello usato nel server PC

# Altro
TIMEZONE = "Europe/Rome"
POLLING_INTERVAL = 5 # Secondi tra i controlli Firebase
DISABLED_MESSAGE_DURATION = 10 # Secondi per cui mostrare "Sveglia disab."
# --- Fine Configurazione Utente ---

# Costruisci URL Trigger
TRIGGER_URL = f"{FIREBASE_DB_URL}/triggers/{PI_ID}.json"

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Setup Hardware (con gestione errori base)
try:
    tz_info = pytz.timezone(TIMEZONE)
except pytz.UnknownTimeZoneError:
     logger.error(f"Errore: Fuso orario '{TIMEZONE}' non riconosciuto da pytz.")
     exit()

try:
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(LED_PIN, GPIO.OUT)
    GPIO.output(LED_PIN, GPIO.LOW) # Assicura LED spento all'inizio
    button = Button(BUTTON_PIN, pull_up=True)
    lcd = Adafruit_CharLCD(rs=LCD_RS, en=LCD_E, d4=LCD_D4, d5=LCD_D5, d6=LCD_D6, d7=LCD_D7, cols=16, lines=2)
    lcd.clear()
    lcd.enable_display(True)
    lcd.home()
    logger.info("Hardware (LED, Bottone, LCD) inizializzato.")
except Exception as e:
    logger.error(f"Errore inizializzazione hardware: {e}", exc_info=True)
    logger.error("Verifica collegamenti e librerie (RPi.GPIO, Adafruit_CharLCD, gpiozero).")
    # Potresti voler uscire se l'hardware è essenziale
    # exit()

# --- Variabili Globali di Stato ---
alarm_manually_disabled = False # Flag per disabilitazione manuale
time_button_pressed = None # Timestamp (monotonic) pressione bottone
last_lcd_message = ""
last_trigger_state = False # Stato precedente del trigger letto da Firebase

# --- Funzioni ---

def light_leds():
    """Accende i LED."""
    try:
        GPIO.output(LED_PIN, GPIO.HIGH)
        logger.debug("LED Accesi.")
    except Exception as e:
        logger.error(f"Errore accendendo i LED: {e}")

def turn_off_leds():
    """Spegne i LED."""
    try:
        GPIO.output(LED_PIN, GPIO.LOW)
        logger.debug("LED Spenti.")
    except Exception as e:
        logger.error(f"Errore spegnendo i LED: {e}")

def button_pressed_callback():
    """Chiamata da gpiozero quando il bottone viene premuto."""
    global alarm_manually_disabled, time_button_pressed
    # Disabilita solo se la sveglia è considerata attiva (trigger=True)
    # E non era già stata disabilitata manualmente
    if last_trigger_state and not alarm_manually_disabled:
        logger.info("Bottone premuto! Sveglia disabilitata manualmente.")
        alarm_manually_disabled = True
        time_button_pressed = time.monotonic() # Memorizza l'ora
        turn_off_leds() # Spegni i LED subito
    elif not last_trigger_state:
         logger.debug("Bottone premuto, ma nessuna sveglia attiva (trigger=False).")
    else:
        logger.debug("Bottone premuto, ma sveglia già disabilitata manualmente.")

# Associa la callback al bottone
try:
    button.when_pressed = button_pressed_callback
except NameError:
     logger.warning("Oggetto 'button' non definito, impossibile associare callback.")


def cleanup_resources(signum=None, frame=None):
    """Pulisce GPIO e LCD prima di uscire."""
    logger.info("Avvio pulizia risorse...")
    try:
        if 'lcd' in locals() or 'lcd' in globals():
             lcd.clear()
             lcd.enable_display(False)
             logger.info("LCD pulito.")
    except Exception as e:
         logger.warning(f"Errore durante pulizia LCD: {e}")
    try:
        if 'GPIO' in sys.modules: # Controlla se RPi.GPIO è stato importato e usato
            GPIO.output(LED_PIN, GPIO.LOW) # Assicura LED spento
            GPIO.cleanup() # Pulisce TUTTI i canali GPIO usati da RPi.GPIO
            logger.info("GPIO puliti.")
    except Exception as e:
         logger.warning(f"Errore durante pulizia GPIO: {e}")
    logger.info("Uscita.")
    sys.exit(0)

# Registra signal handler per uscita pulita
signal.signal(signal.SIGINT, cleanup_resources)
signal.signal(signal.SIGTERM, cleanup_resources)

# --- Loop Principale ---
logger.info(f"Avvio loop principale. Controllo trigger ogni {POLLING_INTERVAL} secondi.")
while True:
    # 1. Leggi lo stato del trigger da Firebase (invariato)
    current_trigger_state = False # Default
    try:
        # ... (stessa logica di lettura trigger con requests) ...
        response = requests.get(TRIGGER_URL, timeout=10)
        response.raise_for_status()
        trigger_value = response.json()
        if trigger_value is True:
            current_trigger_state = True
        logger.debug(f"Stato trigger letto da Firebase: {current_trigger_state} (valore grezzo: {trigger_value})")
    except requests.exceptions.Timeout:
         logger.warning(f"Timeout leggendo trigger da Firebase {TRIGGER_URL}")
         current_trigger_state = last_trigger_state
    except requests.exceptions.RequestException as e:
        logger.error(f"Errore rete leggendo trigger da Firebase {TRIGGER_URL}: {e}")
        current_trigger_state = False
    except json.JSONDecodeError:
        logger.error(f"Errore decodificando JSON da Firebase. Risposta: {response.text if 'response' in locals() else 'N/A'}")
        current_trigger_state = False
    except Exception as e:
         logger.error(f"Errore imprevisto leggendo trigger: {e}", exc_info=True)
         current_trigger_state = False

    # 2. Gestisci cambio di stato del trigger Firebase
    # Se il trigger passa da False a True -> sveglia appena scattata
    if current_trigger_state and not last_trigger_state:
        logger.info("TRIGGER RICEVUTO da Firebase (False -> True)!")
        alarm_manually_disabled = False # Resetta stato disabilitato
        time_button_pressed = None # Resetta timer messaggio
        light_leds() # Accendi i LED

    # Se il trigger passa da True a False -> sveglia finita/resettata dal server
    elif not current_trigger_state and last_trigger_state:
        logger.info("Trigger resettato da Firebase (True -> False).")
        alarm_manually_disabled = False # Resetta stato disabilitato
        time_button_pressed = None # Resetta timer messaggio
        turn_off_leds() # Spegni i LED

    # --- NON resettare alarm_manually_disabled dopo 10 secondi qui ---
    # Verrà resettato solo quando il trigger Firebase va a False (gestito sopra)

    # 3. Logica di gestione LED (basata su trigger e stato disabled persistente)
    if not current_trigger_state or alarm_manually_disabled:
         turn_off_leds()
    elif current_trigger_state and not alarm_manually_disabled: # Trigger attivo E non disabilitato manualmente
         light_leds()

    # Aggiorna lo stato precedente del trigger per il prossimo ciclo
    last_trigger_state = current_trigger_state

    # 4. Aggiorna l'orologio LCD (Logica messaggio modificata)
    try:
        now_lcd = datetime.now(tz_info)
        data_lcd = now_lcd.strftime("%Y-%m-%d")
        ora_lcd = now_lcd.strftime("%H:%M:%S")

        # Determina messaggio LCD
        new_message = ""
        message_set = False # Flag per sapere se abbiamo già impostato il messaggio

        # Priorità 1: Mostra "Sveglia disab." per 10 secondi dopo la pressione
        if alarm_manually_disabled and time_button_pressed is not None:
            if time.monotonic() - time_button_pressed <= DISABLED_MESSAGE_DURATION:
                new_message = f"Sveglia disab.\n{ora_lcd}"
                message_set = True

        # Priorità 2: Se non stiamo mostrando il messaggio temporaneo "disab.",
        # mostra "SVEGLIA ATTIVA!" se il trigger è attivo e non è stata disabilitata
        if not message_set:
            if current_trigger_state and not alarm_manually_disabled:
                new_message = f"SVEGLIA ATTIVA!\n{ora_lcd}"
                message_set = True

        # Priorità 3: Altrimenti (trigger=False O (trigger=True E disabilitata manualmente E timeout messaggio scaduto))
        # mostra l'ora normale
        if not message_set:
             new_message = f"{data_lcd}\n{ora_lcd}"

        # Aggiorna LCD solo se il messaggio è cambiato
        if new_message != last_lcd_message:
            lcd.clear()
            lcd.message(new_message)
            last_lcd_message = new_message
            logger.debug(f"Aggiornato LCD: {new_message.replace(chr(10), ' ')}")

    except Exception as e:
         logger.error(f"Errore aggiornando LCD: {e}")

    # 5. Pausa prima del prossimo ciclo
    sleep(POLLING_INTERVAL)
