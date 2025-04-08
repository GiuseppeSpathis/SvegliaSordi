# clock.py
# Legge trigger da Firebase, controlla LED/LCD, bottone disabilitazione
# e bottone mostra ID (con ID letto/generato da file).

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("Libreria RPi.GPIO non trovata (necessaria per LCD/LED).")
    # Potresti voler usare GPIO fittizio per test senza hardware, o uscire.
    # import RPi.GPIO as GPIO
    # GPIO.setmode = lambda x: None
    # GPIO.setup = lambda x,y: None
    # GPIO.output = lambda x,y: None
    # GPIO.cleanup = lambda: None
    # GPIO.BCM=0; GPIO.OUT=0; GPIO.LOW=0; GPIO.HIGH=1 # Definizioni base
    # print("WARN: RPi.GPIO non trovato, usato mock.")
    # # exit() # Scommenta per uscire se RPi.GPIO è essenziale

try:
    # Nota: Potrebbe essere necessario installare: sudo apt-get install python3-smbus i2c-tools
    # E abilitare I2C con raspi-config
    # E la libreria: pip install adafruit-circuitpython-charlcd
    # A seconda del tuo LCD, potrebbe essere necessaria una libreria diversa o configurazione I2C
    from Adafruit_CharLCD import Adafruit_CharLCD
except ImportError:
     print("Libreria Adafruit_CharLCD non trovata (necessaria per LCD).")
     # Potresti creare una classe LCD fittizia per test
     # class Adafruit_CharLCD:
     #     def __init__(self, *args, **kwargs): pass
     #     def clear(self): print("[LCD MOCK] clear()")
     #     def message(self, text): print(f"[LCD MOCK] message:\n{text}")
     #     def enable_display(self, enable): print(f"[LCD MOCK] enable_display({enable})")
     #     def home(self): print("[LCD MOCK] home()")
     # print("WARN: Adafruit_CharLCD non trovata, usato mock.")
     # # exit() # Scommenta per uscire

try:
    from gpiozero import Button
    # gpiozero gestisce internamente la pulizia dei pin che usa
except ImportError:
     print("Libreria gpiozero non trovata (necessaria per Button). Installala con 'pip install gpiozero'.")
     # class Button:
     #     def __init__(self, *args, **kwargs): self._when_pressed = None
     #     @property
     #     def when_pressed(self): return self._when_pressed
     #     @when_pressed.setter
     #     def when_pressed(self, func): self._when_pressed=func; print("[BUTTON MOCK] Callback impostato.")
     # print("WARN: gpiozero.Button non trovata, usato mock.")
     # # exit() # Scommenta per uscire

import time
from time import sleep, strftime
import signal
import sys
import pytz
from datetime import datetime
import requests
import logging
import json
import random # Per generare ID random
import os     # Per controllare esistenza file

# --- Configurazione Utente ---
# Pin GPIO (BCM numbering)
LCD_RS = 26
LCD_E  = 19
LCD_D4 = 13
LCD_D5 = 6
LCD_D6 = 5
LCD_D7 = 11
LED_PIN = 25
DISABLE_BUTTON_PIN = 23 # Bottone per disabilitare sveglia attiva
ID_DISPLAY_BUTTON_PIN = 4  # Bottone Giallo per mostrare ID (GPIO 4)

# Firebase
FIREBASE_DB_URL = "https://svegliasordi-default-rtdb.europe-west1.firebasedatabase.app" # <-- SOSTITUISCI (es: https://nome-progetto-default-rtdb.europe-west1.firebasedatabase.app)
# MY_PI_ID verrà letto/generato dal file sotto

# Altro
TIMEZONE = "Europe/Rome"
POLLING_INTERVAL = 5 # Secondi tra i controlli Firebase in modalità clock
DISABLED_MESSAGE_DURATION = 10 # Secondi per cui mostrare "Sveglia disab."
ID_DISPLAY_DURATION = 15 # Secondi per cui mostrare l'ID Pi
PI_ID_FILENAME = "pi_id.txt" # Nome del file per salvare l'ID
# --- Fine Configurazione Utente ---

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- Funzione per Leggere/Generare ID Pi ---
def get_or_generate_pi_id(filename: str):
    """
    Legge l'ID Pi dal file specificato. Se il file non esiste o è invalido,
    genera un nuovo ID nel formato 'piXXXXX', lo salva nel file e lo restituisce.
    Restituisce None se si verifica un errore critico.
    """
    script_dir = os.path.dirname(os.path.realpath(__file__)) # Directory dello script
    filepath = os.path.join(script_dir, filename) # Percorso completo del file ID

    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                pi_id = f.read().strip()
            # Validazione più robusta
            if pi_id and pi_id.startswith("pi") and len(pi_id) == 7 and pi_id[2:].isdigit():
                logger.info(f"ID Pi letto dal file '{filepath}': {pi_id}")
                return pi_id
            else:
                logger.warning(f"Contenuto del file '{filepath}' non valido ('{pi_id}'). Rimuovo e genero nuovo ID.")
                try: os.remove(filepath)
                except OSError as e: logger.warning(f"Impossibile rimuovere file ID non valido: {e}")
        except IOError as e:
            logger.error(f"Errore leggendo il file ID '{filepath}': {e}. Genero nuovo ID.")
        except Exception as e:
            logger.error(f"Errore imprevisto leggendo ID da '{filepath}': {e}. Genero nuovo ID.")

    # Se il file non esiste o c'è stato un errore nella lettura/validazione
    try:
        random_part = random.randint(0, 99999)
        new_id = f"pi{random_part:05d}" # Formatta con 5 cifre, padding con zero
        logger.info(f"Nessun file ID valido trovato/leggibile. Generato nuovo ID: {new_id}")
        with open(filepath, "w") as f:
            f.write(new_id)
        logger.info(f"Nuovo ID salvato nel file '{filepath}'.")
        return new_id
    except IOError as e:
        logger.error(f"Impossibile scrivere il nuovo ID nel file '{filepath}': {e}")
        return None # Errore critico
    except Exception as e:
        logger.error(f"Errore imprevisto generando/salvando ID: {e}")
        return None

# --- Ottieni l'ID del Pi all'avvio ---
MY_PI_ID = get_or_generate_pi_id(PI_ID_FILENAME)

if not MY_PI_ID:
    logger.error("Impossibile determinare l'ID del Pi. Assicurati che ci siano permessi di scrittura nella directory dello script o crea manualmente il file 'pi_id.txt'. Uscita.")
    exit(1) # Esce con codice di errore
# --- Fine Ottenimento ID ---


# Costruisci URL Trigger specifico per questo Pi
TRIGGER_URL = f"{FIREBASE_DB_URL}/triggers/{MY_PI_ID}.json"

# Setup Hardware (con gestione errori base)
lcd = None
disable_button = None
id_display_button = None
try:
    tz_info = pytz.timezone(TIMEZONE)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(LED_PIN, GPIO.OUT, initial=GPIO.LOW) # Imposta output e stato iniziale LOW
    # GPIO.output(LED_PIN, GPIO.LOW) # Già fatto con initial=
    disable_button = Button(DISABLE_BUTTON_PIN, pull_up=True)
    id_display_button = Button(ID_DISPLAY_BUTTON_PIN, pull_up=True)
    lcd = Adafruit_CharLCD(rs=LCD_RS, en=LCD_E, d4=LCD_D4, d5=LCD_D5, d6=LCD_D6, d7=LCD_D7, cols=16, lines=2)
    lcd.clear()
    lcd.enable_display(True)
    lcd.home()
    logger.info(f"Hardware inizializzato. ID Pi: {MY_PI_ID}")
except Exception as e:
    logger.error(f"Errore inizializzazione hardware: {e}", exc_info=True)
    # Potresti decidere di uscire o continuare senza hardware
    # exit(1)

# --- Variabili Globali di Stato ---
alarm_manually_disabled = False # Flag per disabilitazione manuale
time_button_pressed = None # Timestamp (monotonic) pressione bottone disabilitazione
last_lcd_message = ""
last_trigger_state = False # Stato precedente del trigger letto da Firebase
display_mode = 'clock' # 'clock' o 'showing_id'
id_display_start_time = None # Timestamp (monotonic) per timeout display ID

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

def disable_button_pressed_callback():
    """Chiamata da gpiozero quando il bottone di DISABILITAZIONE viene premuto."""
    global alarm_manually_disabled, time_button_pressed
    # Disabilita solo se la sveglia è considerata attiva (trigger=True)
    # E non era già stata disabilitata manualmente
    if last_trigger_state and not alarm_manually_disabled:
        logger.info("Bottone DISABILITA premuto! Sveglia silenziata manualmente.")
        alarm_manually_disabled = True
        time_button_pressed = time.monotonic() # Memorizza l'ora per messaggio LCD
        turn_off_leds() # Spegni i LED subito
    elif not last_trigger_state:
         logger.debug("Bottone DISABILITA premuto, ma nessuna sveglia attiva (trigger=False).")
    else: # last_trigger_state è True ma era già disabled
        logger.debug("Bottone DISABILITA premuto, ma sveglia già disabilitata manualmente.")

def id_display_button_callback():
    """Chiamata da gpiozero quando il bottone GIALLO (mostra ID) viene premuto."""
    global display_mode, id_display_start_time, last_lcd_message
    # Se stiamo già mostrando l'ID, estendi il timer
    if display_mode == 'showing_id':
        logger.debug("Bottone Mostra ID premuto, ID già visualizzato. Estendo timer.")
        id_display_start_time = time.monotonic()
        return

    logger.info(f"Bottone MOSTRA ID premuto. Visualizzo ID: {MY_PI_ID} per {ID_DISPLAY_DURATION}s")
    display_mode = 'showing_id'
    id_display_start_time = time.monotonic()
    try:
        if lcd: # Controlla se lcd è stato inizializzato
            lcd.clear()
            # Mostra ID su due righe se troppo lungo, altrimenti centrato sulla seconda
            if len(MY_PI_ID) > 16:
                 lcd.message(f"ID:{MY_PI_ID[:16]}\n{MY_PI_ID[16:]}")
            else:
                 # Pad con spazi per pulire riga precedente se ID è corto
                 lcd.message(f"ID Dispositivo:\n{MY_PI_ID.center(16)}")
            # Non aggiorniamo last_lcd_message qui, così il loop principale
            # lo sovrascrive forzatamente al ritorno a 'clock'
    except Exception as e:
         logger.error(f"Errore mostrando ID su LCD: {e}")

# Associa le callback ai bottoni (solo se gli oggetti sono stati creati)
if disable_button:
    disable_button.when_pressed = disable_button_pressed_callback
else:
    logger.warning("Oggetto 'disable_button' non inizializzato, callback non associata.")

if id_display_button:
    id_display_button.when_pressed = id_display_button_callback
else:
    logger.warning("Oggetto 'id_display_button' non inizializzato, callback non associata.")


def cleanup_resources(signum=None, frame=None):
    """Pulisce GPIO e LCD prima di uscire."""
    logger.info("Avvio pulizia risorse...")
    global lcd # Usa la variabile globale
    if lcd:
        try:
             lcd.clear()
             lcd.enable_display(False)
             logger.info("LCD pulito.")
        except Exception as e:
             logger.warning(f"Errore durante pulizia LCD: {e}")
    try:
        if 'GPIO' in sys.modules:
            # Spegni LED prima di cleanup generale
            try:
                if GPIO.getmode() is not None: # Controlla se GPIO è stato configurato
                    GPIO.output(LED_PIN, GPIO.LOW)
            except Exception as e_led_off:
                logger.warning(f"Errore spegnendo LED prima di cleanup: {e_led_off}")

            GPIO.cleanup()
            logger.info("GPIO puliti.")
    except Exception as e:
         logger.warning(f"Errore durante pulizia GPIO: {e}")
    logger.info("Uscita.")
    sys.exit(0)

# Registra signal handler
signal.signal(signal.SIGINT, cleanup_resources)
signal.signal(signal.SIGTERM, cleanup_resources)

# --- Loop Principale ---
logger.info(f"Avvio loop principale. Controllo trigger {TRIGGER_URL} ogni {POLLING_INTERVAL}s.")
while True:
    # --- Gestisci cambio modalità display ID ---
    if display_mode == 'showing_id' and id_display_start_time is not None:
        if time.monotonic() - id_display_start_time > ID_DISPLAY_DURATION:
            logger.info("Timeout display ID scaduto. Torno alla modalità orologio.")
            display_mode = 'clock'
            id_display_start_time = None
            last_lcd_message = "" # Forza aggiornamento LCD nel prossimo ciclo 'clock'

    # Esegui la logica principale solo se siamo in modalità orologio
    if display_mode == 'clock':
        # 1. Leggi lo stato del trigger da Firebase
        current_trigger_state = False # Default
        firebase_error = False # Flag per errori lettura trigger
        try:
            response = requests.get(TRIGGER_URL, timeout=10)
            response.raise_for_status()
            trigger_value = response.json()
            if trigger_value is True:
                current_trigger_state = True
            logger.debug(f"Stato trigger letto: {current_trigger_state}")

        except requests.exceptions.Timeout:
             logger.warning(f"Timeout leggendo trigger da Firebase {TRIGGER_URL}")
             current_trigger_state = last_trigger_state # Mantieni stato precedente
             firebase_error = True
        except requests.exceptions.RequestException as e:
            logger.error(f"Errore rete leggendo trigger da Firebase {TRIGGER_URL}: {e}")
            current_trigger_state = False # Fallback a False
            firebase_error = True
        except json.JSONDecodeError:
            try:
                resp_text = response.text if 'response' in locals() else 'N/A'
            except:
                 resp_text = 'N/A'
            logger.error(f"Errore decodificando JSON da Firebase. Risposta: {resp_text}")
            current_trigger_state = False
            firebase_error = True
        except Exception as e:
             logger.error(f"Errore imprevisto leggendo trigger: {e}", exc_info=True)
             current_trigger_state = False
             firebase_error = True

        # 2. Gestisci cambio di stato del trigger Firebase (solo se non c'è stato errore)
        if not firebase_error:
            if current_trigger_state and not last_trigger_state:
                logger.info("TRIGGER RICEVUTO da Firebase (False -> True)!")
                alarm_manually_disabled = False # Resetta stato disabilitato
                time_button_pressed = None # Resetta timer messaggio
                light_leds() # Accendi i LED
            elif not current_trigger_state and last_trigger_state:
                logger.info("Trigger resettato da Firebase (True -> False).")
                alarm_manually_disabled = False # Resetta stato disabilitato
                time_button_pressed = None # Resetta timer messaggio
                turn_off_leds() # Spegni i LED

        # 3. Logica di gestione LED
        # Spegni se trigger è False o se disabilitato manualmente
        # Accendi se trigger è True E non è disabilitato manualmente
        if not current_trigger_state or alarm_manually_disabled:
             turn_off_leds()
        elif current_trigger_state and not alarm_manually_disabled:
             light_leds()

        # Aggiorna lo stato precedente del trigger per il prossimo ciclo
        # Aggiorna solo se non c'è stato errore di lettura, altrimenti mantiene l'ultimo valido
        if not firebase_error:
            last_trigger_state = current_trigger_state

        # 4. Aggiorna l'orologio LCD (solo in modalità 'clock')
        if lcd: # Procedi solo se l'LCD è stato inizializzato
            try:
                now_lcd = datetime.now(tz_info)
                data_lcd = now_lcd.strftime("%Y-%m-%d")
                ora_lcd = now_lcd.strftime("%H:%M:%S")

                new_message = ""
                message_set = False

                # Mostra "Sveglia disab." per 10 secondi dopo la pressione
                if alarm_manually_disabled and time_button_pressed is not None:
                    if time.monotonic() - time_button_pressed <= DISABLED_MESSAGE_DURATION:
                        new_message = f"Sveglia disab.\n{ora_lcd}"
                        message_set = True

                # Altrimenti, mostra "SVEGLIA ATTIVA!" se appropriato
                if not message_set:
                    # Usa last_trigger_state per mostrare l'ultimo stato valido se c'è errore Firebase
                    effective_trigger_state = last_trigger_state if firebase_error else current_trigger_state
                    if effective_trigger_state and not alarm_manually_disabled:
                        new_message = f"SVEGLIA ATTIVA!\n{ora_lcd}"
                        message_set = True

                # Altrimenti mostra l'ora normale (o errore se c'è stato)
                if not message_set:
                     if firebase_error:
                          new_message = f"Errore Rete FB\n{ora_lcd}"
                     else:
                          new_message = f"{data_lcd}\n{ora_lcd}"

                # Aggiorna LCD solo se il messaggio è cambiato
                if new_message != last_lcd_message:
                    lcd.clear()
                    lcd.message(new_message)
                    last_lcd_message = new_message
                    logger.debug(f"Aggiornato LCD (clock mode): {new_message.replace(chr(10), ' ')}")

            except Exception as e:
                 logger.error(f"Errore aggiornando LCD in modalità clock: {e}")
        else:
             # Se LCD non inizializzato, logga lo stato che dovrebbe essere visualizzato
              logger.debug(f"Stato (no LCD): Trigger={last_trigger_state}, ManualDisable={alarm_manually_disabled}")


    # 5. Pausa prima del prossimo ciclo
    if display_mode == 'showing_id':
         sleep(0.5) # Controlla spesso se il timeout ID è scaduto
    else:
         sleep(POLLING_INTERVAL) # Intervallo normale in modalità orologio
