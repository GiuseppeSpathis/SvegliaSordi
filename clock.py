import RPi.GPIO as GPIO
import time
from time import sleep, strftime
import signal
import sys
from Adafruit_CharLCD import Adafruit_CharLCD
import json
import pytz
from datetime import datetime
from gpiozero import MotionSensor

# Definizione dei pin
RS = 26
E = 19
D4 = 13
D5 = 6
D6 = 5
D7 = 11
leds = 25
motion_sensor = 24

pir = MotionSensor(motion_sensor)

# Variabili globali
alarm_disabled = False
last_checked_minute = None  # Tiene traccia dell'ultimo minuto verificato
last_lcd_message = ""  # Tiene traccia dell'ultimo messaggio visualizzato

def motion():
    global alarm_disabled
    start_time = time.time()

    sleep(0.2)  # Piccolo ritardo prima del secondo controllo
    if not pir.motion_detected:
        print("False trigger ignored.")
        return

    while pir.motion_detected:
        if time.time() - start_time > 2:  # Tempo minimo di rilevamento
            alarm_disabled = True
            GPIO.output(leds, GPIO.LOW)
            print("Motion confirmed! Alarm disabled.")
            return
        time.sleep(0.1)

def light_leds():
    GPIO.output(leds, GPIO.HIGH)

def check_alarm(current_date, current_time):
    """ Controlla se esiste un allarme attivo nel file JSON """
    try:
        with open("alarms.json", "r") as f:
            alarms = json.load(f)
            for alarm_list in alarms.values():
                for alarm in alarm_list:
                    if alarm["date"] == current_date and alarm["time"] == current_time:
                        return True  # Allarme trovato
    except FileNotFoundError:
        print("File alarms.json non trovato.")
    return False  # Nessun allarme attivo

def signal_handler(sig, frame):
    """ Pulizia prima di uscire """
    lcd.clear()
    lcd.enable_display(False)
    GPIO.output(leds, GPIO.LOW)
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# Configurazione GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(leds, GPIO.OUT)
GPIO.setup(motion_sensor, GPIO.IN)

# Assegna le funzioni agli eventi
pir.when_motion = motion

# Imposta il fuso orario per l'Italia
italy_tz = pytz.timezone("Europe/Rome")

# Inizializza LCD
lcd = Adafruit_CharLCD(rs=RS, en=E,
                       d4=D4, d5=D5, d6=D6, d7=D7,
                       cols=16, lines=2)
lcd.clear()
lcd.enable_display(True)
lcd.home()

while True:
    now = datetime.now(italy_tz)
    data = now.strftime("%Y-%m-%d")  # Formato: YYYY-MM-DD
    ora = now.strftime("%H:%M")  # Formato: HH:MM
    minuto_attuale = now.strftime("%M")  # Estrae solo il minuto attuale

    if minuto_attuale != last_checked_minute:
        alarm_disabled = False
        last_checked_minute = minuto_attuale  # Aggiorna il minuto controllato

    if not alarm_disabled and True:  #check_alarm(data, ora):
        new_message = f"Sveglia in corso...\nOrario: {ora}"
        light_leds()
    else:
        new_message = f"Data: {data}\nOrario: {ora}"
    
    if new_message != last_lcd_message:
        lcd.clear()
        lcd.message(new_message)
        last_lcd_message = new_message
    
    sleep(1)

