import RPi.GPIO as GPIO
import time
from time import sleep, strftime
import signal
import sys
from Adafruit_CharLCD import Adafruit_CharLCD

RS = 26
E = 19
D4 = 13
D5 = 6
D6 = 5
D7 = 11


def signal_handler(sig, frame):
        lcd.clear()
        lcd.enable_display(False)
        sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# instantiate lcd and specify pins
lcd = Adafruit_CharLCD(rs=RS, en=E,
                       d4=D4, d5=D5, d6=D6, d7=D7,
                       cols=16, lines=2)
lcd.clear()
lcd.enable_display(True)
lcd.home()

while True:
        # Ottenere la data e l'ora attuali
        data = strftime("%d-%m-%Y")  # Formato: gg-mm-aaaa
        ora = strftime("%H:%M:%S")   # Formato: hh:mm:ss
        
        # Mostrare sul display LCD
        lcd.clear()
        lcd.message(f"Data: {data}\nOrario: {ora}")
        
        sleep(1)  # Aggiorna ogni secondo
