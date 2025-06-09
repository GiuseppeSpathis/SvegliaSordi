# clock.py
# Legge trigger da Firebase, controlla LED/LCD, bottone disabilitazione,
# bottone mostra ID, gestisce il vibrator motor, e resetta il flag di disabilitazione all'inizio di ogni nuovo minuto.

import RPi.GPIO as GPIO
from Adafruit_CharLCD import Adafruit_CharLCD
from gpiozero import Button
import time
from time import sleep
import signal
import sys
import pytz
from datetime import datetime
import random   # Per generare ID random
import os       # Per controllare esistenza file
import firebase_admin
from firebase_admin import credentials, db

# --- Configurazione Utente ---
# Pin GPIO (BCM numbering)
LCD_RS = 26
LCD_E  = 19
LCD_D4 = 13
LCD_D5 = 6
LCD_D6 = 5
LCD_D7 = 11
LED_PIN = 25
MOTOR_PIN = 27                     # Pin per il vibrator motor
DISABLE_BUTTON_PIN = 23            # Bottone per disabilitare sveglia attiva
ID_DISPLAY_BUTTON_PIN = 4          # Bottone Giallo per mostrare ID (GPIO 4)
VIBRATOR_TOGGLE_BUTTON_PIN = 22    # Bottone per abilitare/disabilitare il vibrator motor

# Firebase
FIREBASE_DB_URL = "https://svegliasordi-default-rtdb.europe-west1.firebasedatabase.app"

# Altro
TIMEZONE = "Europe/Rome"
POLLING_INTERVAL = 2              # Secondi tra i controlli per l'LCD
DISABLED_MESSAGE_DURATION = 8     # Secondi per cui mostrare "Sveglia disab."
ID_DISPLAY_DURATION = 12          # Secondi per cui mostrare l'ID Pi
VIBRATOR_MESSAGE_DURATION = 2     # Durata in secondi del messaggio sullo schermo per il vibrator motor
PI_ID_FILENAME = "pi_id.txt"      # Nome del file per salvare l'ID
# --- Fine Configurazione Utente ---


# --- Variabili Globali di Stato ---
alarm_manually_disabled = False      # Flag per disabilitazione manuale
time_button_pressed = None           # Timestamp della pressione del bottone disabilitazione
last_lcd_message = ""
last_trigger_state = False           # Stato precedente del trigger ricevuto da Firebase
display_mode = 'clock'               # 'clock', 'showing_id' o 'vibrator_message'
id_display_start_time = None         # Timestamp per timeout display ID
vibrator_motor_enabled = True        # Vibrator motor abilitato di default
vibrator_message_start_time = None   # Timestamp per il timeout del messaggio vibrator
startup_time = time.monotonic()      # Tempo di avvio del programma (per eventuale gestione errori)

# --- Funzione per Leggere/Generare ID Pi ---
def get_or_generate_pi_id(filename: str):
    """
    Legge l'ID Pi dal file specificato. Se il file non esiste o è invalido,
    genera un nuovo ID nel formato 'piXXXXX', lo salva nel file e lo restituisce.
    """
    script_dir = os.path.dirname(os.path.realpath(__file__))
    filepath = os.path.join(script_dir, filename)

    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            pi_id = f.read().strip()
        if pi_id.startswith("pi") and len(pi_id) == 7 and pi_id[2:].isdigit():
            return pi_id, script_dir
        else:
            os.remove(filepath)

    random_part = random.randint(0, 99999)
    new_id = f"pi{random_part:05d}"
    with open(filepath, "w") as f:
        f.write(new_id)
    return new_id, script_dir


# --- Ottieni l'ID del Pi all'avvio ---
MY_PI_ID, script_dir = get_or_generate_pi_id(PI_ID_FILENAME)

cred = credentials.Certificate(os.path.join(script_dir, "firebaseKey.json"))
firebase_admin.initialize_app(cred, {
    "databaseURL": FIREBASE_DB_URL
})

# --- Setup Hardware ---
lcd = None
disable_button = None
id_display_button = None
vibrator_toggle_button = None

tz_info = pytz.timezone(TIMEZONE)
GPIO.setmode(GPIO.BCM)

# Configura LED e vibrator motor
GPIO.setup(LED_PIN, GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(MOTOR_PIN, GPIO.OUT, initial=GPIO.LOW)

# Configura bottoni con gpiozero
disable_button = Button(DISABLE_BUTTON_PIN, pull_up=True)
id_display_button = Button(ID_DISPLAY_BUTTON_PIN, pull_up=True)
vibrator_toggle_button = Button(VIBRATOR_TOGGLE_BUTTON_PIN, pull_up=True)

# Inizializza LCD
lcd = Adafruit_CharLCD(
    rs=LCD_RS, en=LCD_E, d4=LCD_D4, d5=LCD_D5, d6=LCD_D6, d7=LCD_D7,
    cols=16, lines=2
)
lcd.clear()
lcd.enable_display(True)
lcd.home()


# --- Funzioni per gestire LED e Vibrator Motor ---
def light_leds():
    """Accende i LED e, se abilitato, anche il vibrator motor."""
    GPIO.output(LED_PIN, GPIO.HIGH)
    if vibrator_motor_enabled:
        GPIO.output(MOTOR_PIN, GPIO.HIGH)


def turn_off_leds():
    """Spegne i LED e il vibrator motor."""
    GPIO.output(LED_PIN, GPIO.LOW)
    GPIO.output(MOTOR_PIN, GPIO.LOW)


# --- Callback per il listener Firebase ---
def on_trigger_change(event):
    """
    Eseguito non appena cambia il valore in /triggers/MY_PI_ID.
    event.data == True  → accendi sveglia
    event.data == False → spegni sveglia
    """
    global alarm_manually_disabled, last_trigger_state

    valore = event.data  # Può essere True, False o None
    if valore is True and not last_trigger_state:
        # Nuovo trigger a True → accendi sveglia
        alarm_manually_disabled = False
        light_leds()
        last_trigger_state = True

    elif valore is False and last_trigger_state:
        # Trigger tornato a False → spegni sveglia
        alarm_manually_disabled = False
        turn_off_leds()
        last_trigger_state = False

    # Se valore è None o uguale allo stato precedente, non fare nulla


# --- Callback per il bottone di disabilitazione ---
def disable_button_pressed_callback():
    """
    Callback per il bottone DISABLE_BUTTON_PIN.
    Se c'è una sveglia in corso, la disabilita e azzera anche il trigger in Firebase.
    """
    global alarm_manually_disabled, time_button_pressed

    if last_trigger_state and not alarm_manually_disabled:
        alarm_manually_disabled = True
        time_button_pressed = time.monotonic()
        turn_off_leds()

        try:
            db.reference(f'/triggers/{MY_PI_ID}').set(False)
            print("DEBUG: Trigger resettato a False su Firebase (button disable).")
        except Exception as e:
            print(f"Errore nel resettare il trigger su Firebase: {e}")


# --- Callback per il bottone di visualizzazione ID ---
def id_display_button_callback():
    """
    Callback per il bottone ID_DISPLAY_BUTTON_PIN.
    Mostra l'ID del dispositivo sul display per un breve periodo.
    """
    global display_mode, id_display_start_time, last_lcd_message

    if display_mode == 'showing_id':
        # Se già in mostra ID, ripristina semplicemente il timer
        id_display_start_time = time.monotonic()
        return

    display_mode = 'showing_id'
    id_display_start_time = time.monotonic()
    if lcd:
        lcd.clear()
        if len(MY_PI_ID) > 16:
            lcd.message(f"ID:{MY_PI_ID[:16]}\n{MY_PI_ID[16:]}")
        else:
            lcd.message(f"ID Dispositivo:\n{MY_PI_ID.center(16)}")


# --- Callback per il bottone di toggle vibrator motor ---
def vibrator_motor_toggle_callback():
    """
    Callback per il bottone VIBRATOR_TOGGLE_BUTTON_PIN.
    Abilita/disabilita il vibrator motor e mostra un messaggio temporaneo.
    """
    global vibrator_motor_enabled, display_mode, vibrator_message_start_time, last_lcd_message

    vibrator_motor_enabled = not vibrator_motor_enabled
    status_msg = "vibrator motor \nabilitato" if vibrator_motor_enabled else "vibrator motor \ndisabilitato"
    display_mode = 'vibrator_message'
    vibrator_message_start_time = time.monotonic()

    if lcd:
        lcd.clear()
        lcd.message(status_msg)


# Associa le callback ai bottoni
if disable_button:
    disable_button.when_pressed = disable_button_pressed_callback

if id_display_button:
    id_display_button.when_pressed = id_display_button_callback

if vibrator_toggle_button:
    vibrator_toggle_button.when_pressed = vibrator_motor_toggle_callback


def cleanup_resources(signum=None, frame=None):
    """Pulisce GPIO e LCD prima di uscire."""
    global lcd
    if lcd:
        lcd.clear()
        lcd.enable_display(False)

    if 'GPIO' in sys.modules and GPIO.getmode() is not None:
        GPIO.output(LED_PIN, GPIO.LOW)
        GPIO.output(MOTOR_PIN, GPIO.LOW)
        GPIO.cleanup()

    sys.exit(0)


signal.signal(signal.SIGINT, cleanup_resources)
signal.signal(signal.SIGTERM, cleanup_resources)


# --- Imposta il listener Firebase per i trigger ---
trigger_ref = db.reference(f"/triggers/{MY_PI_ID}")
trigger_ref.listen(on_trigger_change)


# --- Loop Principale ---
current_minute = None
print("Inizializzazione completata. In attesa di eventi...")

while True:
    # Reset del flag alarm_manually_disabled all'inizio di ogni nuovo minuto
    now = datetime.now(tz_info)
    if current_minute is None or now.minute != current_minute:
        current_minute = now.minute
        alarm_manually_disabled = False

    # Gestione timeout dei messaggi temporanei (ID e vibrator)
    if display_mode == 'showing_id' and id_display_start_time is not None:
        if time.monotonic() - id_display_start_time > ID_DISPLAY_DURATION:
            display_mode = 'clock'
            id_display_start_time = None
            last_lcd_message = ""

    if display_mode == 'vibrator_message' and vibrator_message_start_time is not None:
        if time.monotonic() - vibrator_message_start_time > VIBRATOR_MESSAGE_DURATION:
            display_mode = 'clock'
            vibrator_message_start_time = None
            last_lcd_message = ""

    if display_mode == 'clock':
        # 3. Aggiornamento orologio su LCD
        if lcd:
            now_lcd = datetime.now(tz_info)
            data_lcd = now_lcd.strftime("%Y-%m-%d")
            ora_lcd = now_lcd.strftime("%H:%M:%S")
            new_message = ""
            message_set = False

            # Se la sveglia è disabilitata manualmente
            if alarm_manually_disabled and time_button_pressed is not None:
                if time.monotonic() - time_button_pressed <= DISABLED_MESSAGE_DURATION:
                    new_message = f"Sveglia disab.\n{ora_lcd}"
                    message_set = True

            if not message_set:
                # Se il trigger è attivo e non è disabilitato manualmente
                if last_trigger_state and not alarm_manually_disabled:
                    new_message = f"SVEGLIA ATTIVA!\n{ora_lcd}"
                    message_set = True

            if not message_set:
                new_message = f"{data_lcd}\n{ora_lcd}"

            if new_message != last_lcd_message:
                lcd.clear()
                lcd.message(new_message)
                last_lcd_message = new_message

        sleep(POLLING_INTERVAL)

    else:
        # In modalità 'showing_id' o 'vibrator_message', controlla più spesso il timeout
        sleep(0.5)
