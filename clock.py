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
POLLING_INTERVAL = 2              # Secondi tra i controlli Firebase in modalità clock
DISABLED_MESSAGE_DURATION = 8     # Secondi per cui mostrare "Sveglia disab."
ID_DISPLAY_DURATION = 12           # Secondi per cui mostrare l'ID Pi
VIBRATOR_MESSAGE_DURATION = 2      # Durata in secondi del messaggio sullo schermo per il vibrator motor
PI_ID_FILENAME = "pi_id.txt"       # Nome del file per salvare l'ID
# --- Fine Configurazione Utente ---


# --- Variabili Globali di Stato ---
alarm_manually_disabled = False      # Flag per disabilitazione manuale
time_button_pressed = None             # Timestamp della pressione del bottone disabilitazione
last_lcd_message = ""
last_trigger_state = False             # Stato precedente del trigger letto da Firebase
display_mode = 'clock'                 # 'clock', 'showing_id' o 'vibrator_message'
id_display_start_time = None           # Timestamp per timeout display ID
vibrator_motor_enabled = True          # Vibrator motor abilitato di default
vibrator_message_start_time = None     # Timestamp per il timeout del messaggio vibrator
startup_time = time.monotonic()     # Tempo di avvio del programma (per gestione messaggi connessione)

# --- Funzione per Leggere/Generare ID Pi ---
def get_or_generate_pi_id(filename: str):
    """
    Legge l'ID Pi dal file specificato. Se il file non esiste o è invalido,
    genera un nuovo ID nel formato 'piXXXXX', lo salva nel file e lo restituisce.
    Restituisce None se si verifica un errore critico.
    """
    script_dir = os.path.dirname(os.path.realpath(__file__))
    
    
    filepath = os.path.join(script_dir, filename)

    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            pi_id = f.read().strip()
        if pi_id and pi_id.startswith("pi") and len(pi_id) == 7 and pi_id[2:].isdigit():
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
lcd = Adafruit_CharLCD(rs=LCD_RS, en=LCD_E, d4=LCD_D4, d5=LCD_D5, d6=LCD_D6, d7=LCD_D7, cols=16, lines=2)
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
        

def disable_button_pressed_callback():
    """Callback per il bottone di DISABILITAZIONE."""
    global alarm_manually_disabled, time_button_pressed
    if last_trigger_state and not alarm_manually_disabled:
        alarm_manually_disabled = True
        time_button_pressed = time.monotonic()
        turn_off_leds()
    

def id_display_button_callback():
    """Callback per il bottone per mostrare l'ID."""
    global display_mode, id_display_start_time, last_lcd_message
    if display_mode == 'showing_id':
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
    

def vibrator_motor_toggle_callback():
    """Callback per il bottone che abilita/disabilita il vibrator motor."""
    global vibrator_motor_enabled, display_mode, vibrator_message_start_time, last_lcd_message
    vibrator_motor_enabled = not vibrator_motor_enabled
    status_msg = "vibrator motor \nabilitato" if vibrator_motor_enabled else "vibrator motor \ndisabilitato"
    display_mode = 'vibrator_message'
    vibrator_message_start_time = time.monotonic()
    if lcd:
        lcd.clear()
        lcd.message(status_msg)
    

# Associa le callback ai bottoni (se gli oggetti sono stati creati)
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
            
    if 'GPIO' in sys.modules:
        if GPIO.getmode() is not None:
            GPIO.output(LED_PIN, GPIO.LOW)
            GPIO.output(MOTOR_PIN, GPIO.LOW)
        
        GPIO.cleanup()
    
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup_resources)
signal.signal(signal.SIGTERM, cleanup_resources)

# --- Loop Principale ---

# Variabile per tenere traccia del minuto corrente (per il reset del flag)
current_minute = None
print("Inizializzazione completata. In attesa di eventi...")

while True:
    # Reset del flag alarm_manually_disabled ad inizio nuovo minuto
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
        # 1. Legge stato trigger da Firebase
        current_trigger_state = False
        firebase_error = False
        try:
            trigger_value = db.reference(f"/triggers/{MY_PI_ID}").get()
            current_trigger_state = (trigger_value is True)
            firebase_error = False
        except Exception as e:
            print("Firebase Admin error:", e)
            # conserva lo stato precedente o gestisci l’errore
            current_trigger_state = last_trigger_state
            # Solo dopo 15 secondi di avvio segnala errore
            firebase_error = (time.monotonic() - startup_time) > 15

        # 2. Gestisce il cambio dello stato del trigger
        if not firebase_error:
            if current_trigger_state and not last_trigger_state:
                alarm_manually_disabled = False
                time_button_pressed = None
                light_leds()
            elif not current_trigger_state and last_trigger_state:
                alarm_manually_disabled = False
                time_button_pressed = None
                turn_off_leds()
        if not current_trigger_state or alarm_manually_disabled:
            turn_off_leds()
        elif current_trigger_state and not alarm_manually_disabled:
            light_leds()
        if not firebase_error:
            last_trigger_state = current_trigger_state

        # 3. Aggiornamento orologio su LCD
        if lcd:
            now_lcd = datetime.now(tz_info)
            data_lcd = now_lcd.strftime("%Y-%m-%d")
            ora_lcd = now_lcd.strftime("%H:%M:%S")
            new_message = ""
            message_set = False
            if alarm_manually_disabled and time_button_pressed is not None:
                if time.monotonic() - time_button_pressed <= DISABLED_MESSAGE_DURATION:
                    new_message = f"Sveglia disab.\n{ora_lcd}"
                    message_set = True
            if not message_set:
                effective_trigger_state = last_trigger_state if firebase_error else current_trigger_state
                if effective_trigger_state and not alarm_manually_disabled:
                    new_message = f"SVEGLIA ATTIVA!\n{ora_lcd}"
                    message_set = True
            if not message_set:
                if firebase_error:
                    new_message = f"Errore Rete FB\n{ora_lcd}"
                else:
                    new_message = f"{data_lcd}\n{ora_lcd}"
            if new_message != last_lcd_message:
                lcd.clear()
                lcd.message(new_message)
                last_lcd_message = new_message
        
        sleep(POLLING_INTERVAL)
    else:
        # In modalità differenti (showing_id o vibrator_message), controlla più spesso il timeout
        sleep(0.5)

