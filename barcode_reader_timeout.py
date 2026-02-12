#!/usr/bin/env python3
import time
from evdev import InputDevice, ecodes

DEV = "/dev/input/by-id/usb-Symbol_Technologies__Inc__2008_Symbol_Bar_Code_Scanner::EA_25048525100165-event-kbd"
GAP_SEC = 0.12  # пауза, после которой считаем что скан закончился (100–200мс обычно ок)

KEYMAP = {
    ecodes.KEY_0:"0", ecodes.KEY_1:"1", ecodes.KEY_2:"2", ecodes.KEY_3:"3", ecodes.KEY_4:"4",
    ecodes.KEY_5:"5", ecodes.KEY_6:"6", ecodes.KEY_7:"7", ecodes.KEY_8:"8", ecodes.KEY_9:"9",

    ecodes.KEY_A:"a", ecodes.KEY_B:"b", ecodes.KEY_C:"c", ecodes.KEY_D:"d", ecodes.KEY_E:"e",
    ecodes.KEY_F:"f", ecodes.KEY_G:"g", ecodes.KEY_H:"h", ecodes.KEY_I:"i", ecodes.KEY_J:"j",
    ecodes.KEY_K:"k", ecodes.KEY_L:"l", ecodes.KEY_M:"m", ecodes.KEY_N:"n", ecodes.KEY_O:"o",
    ecodes.KEY_P:"p", ecodes.KEY_Q:"q", ecodes.KEY_R:"r", ecodes.KEY_S:"s", ecodes.KEY_T:"t",
    ecodes.KEY_U:"u", ecodes.KEY_V:"v", ecodes.KEY_W:"w", ecodes.KEY_X:"x", ecodes.KEY_Y:"y",
    ecodes.KEY_Z:"z",

    ecodes.KEY_MINUS:"-", ecodes.KEY_EQUAL:"=", ecodes.KEY_LEFTBRACE:"[", ecodes.KEY_RIGHTBRACE:"]",
    ecodes.KEY_BACKSLASH:"\\", ecodes.KEY_SEMICOLON:";", ecodes.KEY_APOSTROPHE:"'",
    ecodes.KEY_GRAVE:"`", ecodes.KEY_COMMA:",", ecodes.KEY_DOT:".", ecodes.KEY_SLASH:"/",
    ecodes.KEY_SPACE:" ",
}

SHIFT_KEYMAP = {
    ecodes.KEY_1:"!", ecodes.KEY_2:"@", ecodes.KEY_3:"#", ecodes.KEY_4:"$", ecodes.KEY_5:"%",
    ecodes.KEY_6:"^", ecodes.KEY_7:"&", ecodes.KEY_8:"*", ecodes.KEY_9:"(", ecodes.KEY_0:")",
    ecodes.KEY_MINUS:"_", ecodes.KEY_EQUAL:"+", ecodes.KEY_LEFTBRACE:"{", ecodes.KEY_RIGHTBRACE:"}",
    ecodes.KEY_BACKSLASH:"|", ecodes.KEY_SEMICOLON:":", ecodes.KEY_APOSTROPHE:"\"",
    ecodes.KEY_GRAVE:"~", ecodes.KEY_COMMA:"<", ecodes.KEY_DOT:">", ecodes.KEY_SLASH:"?",
}

def main():
    dev = InputDevice(DEV)
    print("Listening:", DEV)
    print("Name:", dev.name)
    print("Scan (Ctrl+C to stop)\n")

    buf = []
    shift_down = False
    last_key_ts = None

    for e in dev.read_loop():
        if e.type != ecodes.EV_KEY:
            continue

        key = e.code
        val = e.value  # 1=down

        # только key-down
        if val != 1:
            # но shift состояние надо обновлять и на down/up:
            if key in (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT):
                shift_down = (e.value != 0)
            continue

        now = time.monotonic()

        # если была пауза — печатаем накопленное
        if last_key_ts is not None and (now - last_key_ts) > GAP_SEC and buf:
            code = "".join(buf).strip()
            buf.clear()
            if code:
                print(code, flush=True)

        last_key_ts = now

        # shift
        if key in (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT):
            shift_down = True
            continue

        # символ
        if shift_down and key in SHIFT_KEYMAP:
            buf.append(SHIFT_KEYMAP[key])
        elif key in KEYMAP:
            ch = KEYMAP[key]
            if shift_down and "a" <= ch <= "z":
                ch = ch.upper()
            buf.append(ch)

    # на всякий случай (обычно сюда не дойдём)
    if buf:
        print("".join(buf).strip(), flush=True)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
