"""
=====================================================================
calibrate.py
=====================================================================
เครื่องมือช่วยหาพิกัดสำหรับกรอกลงใน market_config.py
เปิดเกม + วางหน้าต่างในตำแหน่งที่จะใช้จริงก่อนคาลิเบรต แล้วรันไฟล์นี้

โหมดที่มี:
  1. mouse   - ไล่เมาส์ไปตำแหน่งต่างๆ แล้วกด Enter เพื่อ print พิกัดปัจจุบัน
               ใช้หา MARKET_BUTTON_POS, ROW_CLICK_X/Y, SCROLL_X/Y ฯลฯ
               พิมพ์ 'r' ก่อนกด Enter เพื่อจับกรอบ 2 มุม แล้วสรุปเป็น
               'left top right bottom' โดยตรง (เอาไปใช้กับโหมด region หรือ
               กรอกใน ITEM_LIST_REGION / DETAIL_PANEL_REGION ได้เลย)
  2. region  - ใส่พิกัดกรอบ (left top right bottom) แล้วดูภาพ + ผล OCR ของกรอบนั้น
               ใช้ยืนยันว่ากรอบที่ได้จากโหมด mouse ครอบถูกจุดและ OCR อ่านออก

วิธีรัน:
    python calibrate.py mouse
    python calibrate.py region
=====================================================================
"""

import sys
import time
import os

import pyautogui
import pytesseract
from PIL import Image

import market_config as cfg

pytesseract.pytesseract.tesseract_cmd = cfg.TESSERACT_CMD


def mode_mouse():
    print("โหมดหาพิกัดเมาส์")
    print("พิมพ์คำสั่งแล้วกด Enter:")
    print("  Enter เปล่าๆ   -> จับจุดเดียว (x, y)  ใช้กับปุ่มต่างๆ เช่น MARKET_BUTTON_POS")
    print("  r + Enter      -> จับกรอบ 2 มุม แล้วสรุปเป็น 'left top right bottom'")
    print("                    ใช้กับ ITEM_LIST_REGION / DETAIL_PANEL_REGION โดยตรง")
    print("  q + Enter      -> ออก\n")
    try:
        while True:
            cmd = input("คำสั่ง (Enter / r / q): ").strip().lower()

            if cmd == "q":
                break

            if cmd == "r":
                input("  วางเมาส์ที่มุม 'บนซ้าย' ของกรอบ แล้วกด Enter...")
                x1, y1 = pyautogui.position()
                print(f"  -> มุมบนซ้าย: ({x1}, {y1})")

                input("  วางเมาส์ที่มุม 'ล่างขวา' ของกรอบ แล้วกด Enter...")
                x2, y2 = pyautogui.position()
                print(f"  -> มุมล่างขวา: ({x2}, {y2})")

                left, top = min(x1, x2), min(y1, y2)
                right, bottom = max(x1, x2), max(y1, y2)
                print(f"  => left top right bottom: {left} {top} {right} {bottom}")
                print(f"     (คัดลอกตัวเลขนี้ไปใช้กับ python calibrate.py region ได้เลย)\n")
                continue

            # Enter เปล่า -> จับจุดเดียว
            x, y = pyautogui.position()
            print(f"  -> ตำแหน่งเมาส์ตอนนี้: ({x}, {y})\n")
    except KeyboardInterrupt:
        print("\nจบการทำงาน")


def mode_region():
    print("โหมดตรวจสอบ region + OCR")
    print("ใส่พิกัดกรอบ 4 ค่า: left top right bottom (คั่นด้วยเว้นวรรค)")
    print('ตัวอย่าง: 100 200 900 650')
    print("พิมพ์ 'q' เพื่อออก\n")

    os.makedirs("calibrate_debug", exist_ok=True)

    while True:
        raw = input("กรอกพิกัด (l t r b): ").strip()
        if raw.lower() == "q":
            break
        try:
            l, t, r, b = map(int, raw.split())
        except ValueError:
            print("รูปแบบไม่ถูกต้อง ลองใหม่ เช่น: 100 200 900 650")
            continue

        width, height = r - l, b - t
        if width <= 0 or height <= 0:
            print("right/bottom ต้องมากกว่า left/top")
            continue

        img = pyautogui.screenshot(region=(l, t, width, height))
        ts = int(time.time())
        img_path = os.path.join("calibrate_debug", f"region_{ts}.png")
        img.save(img_path)

        text = pytesseract.image_to_string(img, lang=cfg.OCR_LANGUAGES)

        print(f"\n  บันทึกภาพไว้ที่: {img_path}  (เปิดดูเพื่อเช็คว่ากรอบครอบถูกจุด)")
        print("  ผล OCR อ่านได้:")
        print("  " + "-" * 50)
        print("  " + text.replace("\n", "\n  "))
        print("  " + "-" * 50 + "\n")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("mouse", "region"):
        print("ใช้งาน: python calibrate.py [mouse|region]")
        sys.exit(1)

    print("จะเริ่มใน 3 วินาที... สลับไปโฟกัสหน้าต่างเกมที่จะคาลิเบรตได้เลย\n")
    time.sleep(3)

    if sys.argv[1] == "mouse":
        mode_mouse()
    else:
        mode_region()
