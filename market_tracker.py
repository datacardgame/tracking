"""
=====================================================================
market_tracker.py
=====================================================================
สแกนหน้า Trade House ในเกม ทุก ๆ N นาที (ตั้งค่าใน market_config.py)
ไล่อ่านทีละการ์ดในกริด (2 คอลัมน์ x 4 แถวต่อหน้า), คลิกไอคอนไอเท็มเพื่อเปิด
popup รายละเอียด, OCR อ่านชื่อไอเท็ม + special skill (พร้อมคำอธิบาย),
เปลี่ยนหน้าไปเรื่อยๆ จนครบ แล้วสรุปว่ามีไอเท็มใหม่ลงขายหรือไม่เทียบกับรอบก่อน

ก่อนใช้งานจริง ต้อง:
  1. ติดตั้ง Tesseract OCR (ดู README.md)
  2. pip install -r requirements.txt
  3. คาลิเบรตพิกัดด้วย calibrate.py แล้วกรอกลงใน market_config.py
  4. เปิดเกมค้างไว้ในตำแหน่งเดิมทุกครั้ง แล้วค่อยรันไฟล์นี้

รัน:
    python market_tracker.py
กด Ctrl+C เพื่อหยุด
=====================================================================
"""

import csv
import datetime
import io
import json
import os
import re
import sys
import time
import traceback

import cv2
import numpy as np
import pyautogui
import pygetwindow as gw
import pytesseract
import requests
from PIL import Image


def _load_config():
    """โหลด market_config.py - ตอนรันเป็นสคริปต์ปกติใช้ import ธรรมดา แต่ตอนรันเป็น
    exe (PyInstaller ตั้ง sys.frozen ให้) ให้โหลดจากไฟล์ market_config.py ที่วางอยู่
    ข้างๆ ตัว exe แทน เพื่อให้ผู้ใช้แก้พิกัด/กฎแจ้งเตือนได้เองโดยไม่ต้อง build exe ใหม่
    (ถ้าไม่ทำแบบนี้ config จะถูกฝังตายอยู่ในตัว exe แก้อะไรไม่ได้เลย)"""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        # เปลี่ยน working directory ไปที่โฟลเดอร์ของ exe ด้วย เพื่อให้ไฟล์ผลลัพธ์
        # (market_log.csv, market_last_snapshot.json, debug_screens/) ถูกสร้างข้างๆ exe
        # เสมอ ไม่ว่าจะดับเบิลคลิกจากที่ไหน
        os.chdir(exe_dir)
        cfg_path = os.path.join(exe_dir, "market_config.py")
        if not os.path.exists(cfg_path):
            print(f"ไม่พบไฟล์ market_config.py ในโฟลเดอร์เดียวกับโปรแกรม ({exe_dir})")
            print("ให้วางไฟล์ market_config.py (ไฟล์ตั้งค่าพิกัด) ไว้ข้างๆ market_tracker.exe แล้วเปิดใหม่")
            input("กด Enter เพื่อปิดหน้าต่างนี้...")
            sys.exit(1)
        import importlib.util
        spec = importlib.util.spec_from_file_location("market_config", cfg_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    import market_config
    return market_config


cfg = _load_config()

pytesseract.pytesseract.tesseract_cmd = cfg.TESSERACT_CMD


# บรรทัด "ชื่อสกิล LV.n" เท่านั้น (ไม่ใช่ประโยคคำอธิบายที่บังเอิญมีคำว่า Lv. อยู่กลางประโยค)
# ยอมให้มีขยะท้ายบรรทัดได้ไม่เกิน 5 ตัวอักษร (ไอคอนเล็กๆ ข้างชื่อสกิลบางทีโดน OCR
# อ่านเป็นตัวอักษรแปลกปลอมต่อท้าย เช่น "Frigg's Chant LV.1 i") และความยาวรวมไม่เกิน ~40 ตัวอักษร
SKILL_TITLE_RE = re.compile(r"^.{3,40}\bLV\.?\s*\d+.{0,5}$", re.IGNORECASE)
PAGE_NUM_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
LV_OCCURRENCE_RE = re.compile(r"\blv\b", re.IGNORECASE)


def is_skill_title_line(line):
    """เช็คว่าบรรทัดนี้เป็น 'ชื่อสกิล LV.n' จริง ไม่ใช่ประโยคคำอธิบายที่ถูกตัดบรรทัด
    (text wrap) แล้วบังเอิญท่อนที่ตัดมาลงเอยด้วย 'Lv. <เลข>' พอดี (เช่น
    'Refine Lv.), up to Lv. 15' ที่จริงเป็นส่วนหนึ่งของประโยคยาว ไม่ใช่ชื่อสกิลใหม่)
    ตัวสกัดคือ: ชื่อสกิลจริงจะมีคำว่า 'Lv' ปรากฏแค่ครั้งเดียวในบรรทัด ถ้าเจอมากกว่า
    1 ครั้ง แปลว่าเป็นประโยคที่พูดถึง Lv. หลายจุด (คำอธิบาย ไม่ใช่ชื่อ)"""
    if not SKILL_TITLE_RE.match(line):
        return False
    return len(LV_OCCURRENCE_RE.findall(line)) == 1


# ---------------------------------------------------------------
# หน้าต่างเกม / นำทาง UI
# ---------------------------------------------------------------

def focus_game_window():
    wins = [w for w in gw.getAllWindows() if cfg.GAME_WINDOW_TITLE_SUBSTRING.lower() in w.title.lower()]
    if not wins:
        raise RuntimeError(
            f"ไม่พบหน้าต่างเกมที่มีคำว่า '{cfg.GAME_WINDOW_TITLE_SUBSTRING}' ในชื่อ "
            "ตรวจสอบว่าเกมเปิดอยู่ และแก้ GAME_WINDOW_TITLE_SUBSTRING ใน market_config.py "
            "ให้ตรงกับ title bar จริง"
        )
    win = wins[0]
    try:
        win.activate()
    except Exception:
        pass
    time.sleep(0.3)
    return win


def refresh_item_list():
    """Trade House เปิดค้างไว้อยู่แล้ว ไม่ต้องกดเปิด แต่ลิสต์ไอเท็มจะไม่รีเฟรชเอง
    ต้องกดสลับไปแท็บ My Favorites แล้วกดกลับมา All Items ถึงจะบังคับให้ลิสต์โหลดใหม่"""
    pyautogui.click(*cfg.MY_FAVORITES_TAB_POS)
    time.sleep(cfg.WAIT_AFTER_REFRESH_CLICK)
    pyautogui.click(*cfg.ALL_ITEMS_TAB_POS)
    time.sleep(cfg.WAIT_AFTER_REFRESH_CLICK)


def select_category(tab):
    pyautogui.click(*tab["pos"])
    time.sleep(cfg.WAIT_AFTER_REFRESH_CLICK)


def close_detail_popup():
    if cfg.CLOSE_POPUP_WITH_ESC:
        pyautogui.press("esc")
    else:
        pyautogui.click(*cfg.CLOSE_POPUP_POS)
    time.sleep(cfg.WAIT_AFTER_CLOSE_DETAIL)


def go_to_next_page():
    pyautogui.click(*cfg.NEXT_PAGE_POS)
    time.sleep(cfg.WAIT_AFTER_PAGE_CHANGE)


def go_to_first_page(times=None):
    """เลื่อนกลับหน้าแรกหลังสแกนจบ โดยกดปุ่มย้อนกลับ (ลูกศรซ้าย) เท่าจำนวนหน้าที่
    เลื่อนมาจริง (times) ถ้าไม่ระบุจะกดซ้ำสูงสุด MAX_PAGES ครั้งเป็นทางเลือกสำรอง
    (เผื่อกรณีไม่รู้ว่าอยู่หน้าไหนแน่ๆ) - ปุ่มนี้ใช้แค่ตอนจบรอบสแกนเท่านั้น
    ไม่ได้ใช้ระหว่างไล่สแกนไปข้างหน้า (ตอนนั้นใช้ปุ่มขวา/go_to_next_page เท่านั้น)"""
    n = times if times is not None else cfg.MAX_PAGES
    for _ in range(n):
        pyautogui.click(*cfg.PREV_PAGE_POS)
        time.sleep(0.2)


# ---------------------------------------------------------------
# OCR
# ---------------------------------------------------------------

def _debug_save(img, tag):
    if not cfg.SCREENSHOT_DEBUG_DIR:
        return
    os.makedirs(cfg.SCREENSHOT_DEBUG_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    img.save(os.path.join(cfg.SCREENSHOT_DEBUG_DIR, f"{tag}_{ts}.png"))


def _preprocess_for_ocr(img):
    """แปลงภาพก่อนส่งเข้า Tesseract ด้วย adaptive threshold แทนการปล่อยให้ Tesseract
    หา threshold เอง (แบบ global/Otsu) เหตุผลคือ popup ในเกมนี้มีทั้งโซนพื้นขาว
    (ตัวหนังสือเข้มปกติ) และโซนหัวข้อพื้นสี (ชื่อไอเท็มตัวขาวบนพื้นแดง, ชื่อสกิลบนพื้นส้ม)
    ปนกันอยู่ในกรอบเดียว ถ้าใช้ threshold แบบ global พื้นที่ตัวหนังสือบนพื้นสีมักถูกมองว่า
    "ไม่มีตัวอักษร" เลยหายไปจากผล OCR ทั้งบรรทัด (ทดสอบแล้วว่าเป็นสาเหตุที่ชื่อไอเท็มหาย)
    adaptive threshold คำนวณ threshold แยกเป็นบล็อกเล็กๆ ทำให้แยกตัวอักษรจากพื้นหลังได้
    ไม่ว่าพื้นหลังตรงนั้นจะสีอะไรก็ตาม

    หลังจาก threshold แล้วมีอีกปัญหา: Tesseract คาดหวังว่าตัวหนังสือจะ "เข้มบนพื้นสว่าง"
    (ส่วนใหญ่ของภาพเป็นสีขาว มีตัวอักษรสีดำแทรกอยู่นิดหน่อย) แต่บริเวณที่ตัวหนังสือ
    สว่างกว่าพื้นหลัง (เช่น เลขหน้า "1/3" สีขาวบนพื้น pill สีเทาอมฟ้า) พอ threshold แล้ว
    จะกลายเป็น "ขาวบนดำ" กลับด้าน ทำให้ Tesseract อ่านไม่ออกเลยทั้งที่ภาพดูชัดด้วยตา
    เลยต้องเช็คว่าพิกเซลขาวเป็นส่วนใหญ่ของภาพหรือไม่ ถ้าไม่ใช่ (แปลว่ากลับด้านอยู่)
    ให้ invert สีกลับให้ถูกทาง (พื้นหลังขาวเป็นส่วนใหญ่ ตัวอักษรดำเป็นส่วนน้อย) เสมอ"""
    gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    h, w = gray.shape[:2]
    # ขนาด block ของ adaptive threshold ต้องเป็นเลขคี่และไม่ใหญ่กว่าภาพ (กันภาพเล็กๆ
    # อย่างกรอบเลขหน้าพัง เพราะ block ปกติ 31 อาจใหญ่เกินไปสำหรับภาพแค่ไม่กี่สิบพิกเซล)
    block_size = min(31, h, w)
    if block_size % 2 == 0:
        block_size -= 1
    block_size = max(block_size, 3)

    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block_size, 10
    )
    white_fraction = float(np.mean(thresh == 255))
    if white_fraction < 0.5:
        thresh = cv2.bitwise_not(thresh)
    return thresh


def _save_fixed_debug(img, name):
    """เซฟภาพ "จุดที่ใช้ตัดสินเรื่องเปลี่ยนหน้า" ทับไฟล์ชื่อเดิมทุกครั้ง (debug_<name>.png)
    ต่างจาก _debug_save() ที่ต่อท้ายด้วย timestamp แล้วสะสมไฟล์ใหม่ไปเรื่อยๆ - จุดพวกนี้
    ต้องดูแค่ "ครั้งล่าสุด" เพื่อเช็คว่ากรอบ/จุดที่ตั้งไว้เล็งตรงจริงไหม เก็บย้อนหลังไม่มีประโยชน์
    รับได้ทั้ง PIL Image และ numpy array (ภาพขาวดำหลัง preprocess)"""
    if not getattr(cfg, "PAGE_CHECK_DEBUG_IMAGES", True):
        return
    path = f"debug_{name}.png"
    try:
        if isinstance(img, np.ndarray):
            cv2.imwrite(path, img)
        else:
            img.save(path)
    except Exception as e:
        print(f"[warn] เซฟภาพ {path} ไม่สำเร็จ: {e}")


def ocr_region(region_ltrb, tag="region", return_image=False, psm=None, fixed_debug_name=None):
    """psm: บังคับโหมดแบ่งหน้าของ Tesseract เป็นตัวเลข เช่น 7 = "บรรทัดเดียว"
    ปล่อยเป็น None เพื่อใช้โหมดอัตโนมัติ (เหมาะกับข้อความหลายบรรทัดอย่างการ์ด/popup)
    ควรใช้ psm=7 กับกรอบเล็กๆ ที่มีข้อความบรรทัดเดียวสั้นๆ เช่นเลขหน้า "1/3"
    เพราะโหมดอัตโนมัติมักอ่านภาพเล็กขนาดนี้ไม่ออกเลย (ทดสอบแล้ว)

    fixed_debug_name: ถ้าระบุ จะเซฟภาพดิบเป็น debug_<name>.png และภาพที่ Tesseract
    เห็นจริง (หลัง threshold + ขยาย) เป็น debug_<name>_ocr.png ทับไฟล์เดิมทุกครั้ง
    ใช้กับกรอบที่ต้องคอยเช็คว่าเล็งตรงไหม อย่างกรอบเลขหน้า"""
    l, t, r, b = region_ltrb
    img = pyautogui.screenshot(region=(l, t, r - l, b - t))
    _debug_save(img, tag)
    if fixed_debug_name:
        _save_fixed_debug(img, fixed_debug_name)
    processed = _preprocess_for_ocr(img)

    # กรอบที่เล็กมาก (เช่น PAGE_INDICATOR_REGION แค่ ~65x44 px) ทำให้ Tesseract อ่านเพี้ยน
    # บ่อยมาก (ทดสอบจริงจากภาพ debug: "1/6" ถูกอ่านเป็น "WG" ทั้งที่ตาคนมองออกชัดเจน) - นี่คือ
    # สาเหตุของบั๊กที่สแกนจบแค่หน้าแรกแล้วหยุดทั้งโปรแกรม เพราะ page_is_last() ถือว่าอ่านเลข
    # หน้าไม่ออก = หน้าสุดท้ายเสมอ (fail-safe) ขยายภาพให้ด้านที่เล็กสุดมีขนาดอย่างน้อย
    # MIN_DIM พิกเซลก่อนส่งเข้า Tesseract ช่วยแก้ได้แทบทุกครั้ง (ทดสอบกับภาพ debug ย้อนหลัง
    # ทั้งหมดที่มีแล้ว) ไม่กระทบกรอบใหญ่ๆ อย่างการ์ด/popup ที่อ่านได้ดีอยู่แล้ว
    MIN_DIM = 120
    h, w = processed.shape[:2]
    if min(h, w) < MIN_DIM:
        scale = max(2, -(-MIN_DIM // min(h, w)))  # ceil(MIN_DIM / min(h, w))
        processed = cv2.resize(processed, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

    if fixed_debug_name:
        _save_fixed_debug(processed, f"{fixed_debug_name}_ocr")

    config = f"--psm {psm}" if psm is not None else ""
    text = pytesseract.image_to_string(processed, lang=cfg.OCR_LANGUAGES, config=config).strip()
    if return_image:
        return text, img
    return text


# ---------------------------------------------------------------
# ตรวจสีชื่อ option บน "การ์ด" ก่อนคลิก (ไม่ใช่ใน popup)
# ---------------------------------------------------------------

# ชื่อ special skill บนการ์ดเขียนต่อกันด้วยจุลภาค เช่น "Thor's Edge,Backup Tool"
# โดยตัวแรก (ก่อนจุลภาค) เป็นสีส้มเสมอถ้าเป็น option เด่น ส่วนตัวถัดไปเป็นสีม่วง/น้ำเงิน
# ถ้าไม่ใช่ option เด่น (สังเกตจากภาพจริงว่าถ้าไอเท็มมี option เด่นสองอัน ทั้งคู่จะเป็น
# สีส้มทั้งคู่ ไม่ใช่ส้ม+ม่วงเสมอไป) - สีที่เห็นบนการ์ดนี้เป็นสีจริงที่ผู้ใช้อ้างอิง ไม่ใช่สีกล่อง
# ใน popup รายละเอียด (popup มีสีกล่องคนละชุด ไม่ตรงกับสีตัวอักษรบนการ์ดเป๊ะ) จึงต้องอ่านค่าสี
# จากภาพการ์ด (ก่อนคลิกไอคอนเปิด popup) ไม่ใช่จากภาพ popup
def _ocr_words_with_boxes(img):
    """คืน list ของคำ (text, bbox, เลขบรรทัด) จาก pytesseract image_to_data โดยใช้
    ภาพที่ผ่าน _preprocess_for_ocr เดียวกับที่ใช้อ่านข้อความปกติ (พิกัด bbox จึงตรงกับ
    พิกเซลจริงในภาพสี ต้นฉบับ เพราะ preprocess ไม่ได้ย่อ/ขยายภาพ)"""
    processed = _preprocess_for_ocr(img)
    data = pytesseract.image_to_data(processed, lang=cfg.OCR_LANGUAGES, output_type=pytesseract.Output.DICT)
    words = []
    for i, raw_text in enumerate(data["text"]):
        text = raw_text.strip()
        if not text:
            continue
        l, t, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        line_key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        words.append({"text": text, "bbox": (l, t, l + w, t + h), "line": line_key})
    return words


def _is_plausible_comma_word(text):
    """เช็คว่า 'คำที่มีจุลภาคติดอยู่' นี้ดูเป็นส่วนหนึ่งของชื่อ option จริง ไม่ใช่ขยะ
    ที่ OCR อ่านเพี้ยนจากไอคอน/เส้นขอบเป็นสัญลักษณ์คล้ายจุลภาคเฉยๆ (เจอจริง: '¥,' ที่หลุด
    เข้ามาในบรรทัดชื่อไอเท็ม ทำให้โค้ดเข้าใจผิดว่าบรรทัดชื่อไอเท็มเป็นบรรทัดชื่อ option)
    ต้องมีตัวอักษรจริงเหลืออยู่อย่างน้อย 2 ตัวหลังตัดจุลภาคออก"""
    letters = sum(ch.isalpha() or ch == "'" for ch in text)
    return letters >= 2


def _has_significant_blob(mask_bool, min_area=100):
    """เช็คว่ามาสก์ (True/False ต่อพิกเซล) มีก้อนพิกเซลติดกันขนาดใหญ่พอจะเป็นตัวอักษรจริง
    อย่างน้อย 1 ก้อนไหม (กันเศษพิกเซล anti-alias ปนสีที่หลุดผ่านเกณฑ์สีมานับเป็น option
    ทั้งที่ไม่ใช่ - วัดจากภาพจริงแล้วว่าก้อนตัวอักษรจริงมีพื้นที่ ~1300-1500 พิกเซลขึ้นไป
    ในขณะที่จุดปนเปื้อน anti-alias/color fringing ที่ไม่ใช่ตัวอักษรมีแค่ ~20 พิกเซล
    ทิ้งห่างกันมาก จึงตั้ง threshold ไว้ตรงกลางกว้างๆ ที่ 100 ให้ปลอดภัย)"""
    mask = mask_bool.astype(np.uint8) * 255
    if not mask.any():
        return False
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    for i in range(1, num_labels):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            return True
    return False


def detect_card_option_colors(card_img):
    """อ่านสีของชื่อ option ที่คั่นด้วยจุลภาคบนการ์ด (ก่อนคลิกไอคอนเปิด popup)
    คืนค่า (orange_count, purple_count)

    วิธีเดิมที่แยกสีทีละกลุ่ม (ก่อน/หลังจุลภาค) โดยอิงกรอบคำจาก OCR พังบ่อย เพราะ OCR
    มักเชื่อมคำท้ายกลุ่มแรกติดกับจุลภาคและคำแรกของกลุ่มสองเป็นคำเดียว (เช่น
    'Protection,Unshakable') พอตัดคำนั้นทิ้งเพื่อกันสีปน กลุ่มสองก็เหลือ 0 คำ เลยอ่านสี
    ไม่ได้เลย (เจอจริงกับไอเท็ม 'Royal Full Plate' ที่ purple_skill_count ควรเป็น 1
    แต่ออกมา 0)

    วิธีใหม่: ไม่สนใจขอบเขตคำเลย เอาแค่ "กรอบทั้งแถว" ของบรรทัดที่มีจุลภาค (มาจาก OCR
    บรรทัดเดียวกัน ซึ่งแม่นกว่าการแยกคำมาก) แล้วสแกนสีทีละพิกเซลในกรอบนั้นทั้งแถว หาว่า
    มีก้อนพิกเซล "โทนอุ่น" (ส้ม) และ/หรือ "โทนเย็น" (ม่วง) อยู่บ้างไหม โดยรู้อยู่แล้วจากภาพ
    จริงจำนวนมากว่า option ตัวแรก (ก่อนจุลภาค) เป็นสีส้มเสมอถ้าเป็น option เด่น ดังนั้น:
      - ถ้าเจอก้อนสีม่วง -> ต้องเป็นคู่ส้ม 1 + ม่วง 1 (ตัวแรกส้มตามปกติ ตัวหลังม่วง)
      - ถ้าไม่เจอสีม่วงเลยแต่เจอสีส้ม -> แปลว่า option ทั้งสองตัวเป็นสีส้มด้วยกัน (2 ส้ม)
      - ถ้าไม่เจอสีไหนเลย -> OCR/สแกนล้มเหลว ไม่นับ (0, 0) ปลอดภัยไว้ก่อน
    ถ้าไม่มีบรรทัดไหนมีจุลภาคเลย (ไอเท็มมี option เดียว) คืนค่า (0, 0) เสมอ"""
    arr = np.array(card_img.convert("RGB"))
    words = _ocr_words_with_boxes(card_img)

    # ตัดคำที่อยู่ในโซนไอคอนไอเท็ม/หัวใจ (ซ้ายสุดของการ์ด) ทิ้งไปก่อนเลย เพราะ OCR
    # บางทีอ่านลวดลายไอคอนเป็นตัวอักษรขยะสั้นๆ (เช่น 'Pe)', 'Ny', '¥,') ซึ่งถ้าหลุดเข้ามา
    # ปนในบรรทัดชื่อ option จะทำให้กรอบที่คำนวณกว้างเกินจริงจนกินพื้นที่ไอคอนไปด้วย
    # (เจอจริง: ทำให้สีของไอคอนถูกนับเป็นสี option ผิดๆ) ข้อความจริงทุกประเภท (ชื่อไอเท็ม/
    # ชื่อ option/ราคา) เริ่มที่ x ~130-135 เป็นอย่างน้อยเสมอในภาพจริงที่ตรวจสอบมาแล้ว
    ICON_ZONE_MAX_X = 120
    words = [w for w in words if w["bbox"][0] >= ICON_ZONE_MAX_X]

    lines = {}
    for w in words:
        lines.setdefault(w["line"], []).append(w)

    option_line = None
    for line_words in lines.values():
        if any("," in w["text"] and _is_plausible_comma_word(w["text"]) for w in line_words):
            option_line = line_words
            break

    if option_line is None:
        return 0, 0

    margin = 2
    top = max(min(w["bbox"][1] for w in option_line) - margin, 0)
    left = max(min(w["bbox"][0] for w in option_line) - margin, 0)
    bottom = max(w["bbox"][3] for w in option_line) + margin
    right = max(w["bbox"][2] for w in option_line) + margin
    row = arr[top:bottom, left:right].astype(np.int16)

    r, g, b = row[:, :, 0], row[:, :, 1], row[:, :, 2]
    warm = (r - b) > 15   # ส้ม
    cool = (b - r) > 15   # ม่วง

    has_orange = _has_significant_blob(warm)
    has_purple = _has_significant_blob(cool)

    if has_purple:
        return 1, 1
    if has_orange:
        return 2, 0
    return 0, 0


# ---------------------------------------------------------------
# พิกัดของแต่ละการ์ดในกริด
# ---------------------------------------------------------------

def card_text_region(row, col):
    l, t, r, b = cfg.FIRST_CARD_TEXT_REGION
    dx = col * cfg.CARD_PITCH_X
    dy = row * cfg.CARD_PITCH_Y
    return (l + dx, t + dy, r + dx, b + dy)


def card_icon_click_pos(row, col):
    x, y = cfg.FIRST_CARD_ICON_CLICK_POS
    return (x + col * cfg.CARD_PITCH_X, y + row * cfg.CARD_PITCH_Y)


def card_price_click_pos(row, col):
    """จุดคลิกที่ตัวเลขราคาบนการ์ด - คลิกจุดนี้แค่ 'เลือก' ไอเท็ม (อัปเดตแผง Buy ฝั่งขวา
    ให้แสดงชื่อไอเท็มนี้) โดยไม่เปิด popup รายละเอียดบังหน้าจอ ใช้อ่านชื่อจากแผง Buy ก่อน
    ค่อยคลิกไอคอน (card_icon_click_pos) เปิด popup เพื่ออ่านสกิลในขั้นถัดไป"""
    x, y = cfg.FIRST_CARD_PRICE_CLICK_POS
    return (x + col * cfg.CARD_PITCH_X, y + row * cfg.CARD_PITCH_Y)


def detail_popup_region(col):
    """popup รายละเอียดไม่ได้เปิดที่ตำแหน่งคงที่เสมอ - มันเลื่อนตามคอลัมน์ของการ์ด
    ที่คลิก (ยืนยันแล้วจากภาพจริง: popup ที่เปิดจากการ์ดคอลัมน์ขวาเลื่อนไปทางขวา
    พอดีเท่ากับ CARD_PITCH_X เมื่อเทียบกับ popup ของคอลัมน์ซ้าย) เลยต้องคำนวณ
    กรอบ capture ตามคอลัมน์ด้วย ไม่ใช้ cfg.DETAIL_POPUP_REGION ตรงๆ
    (ไม่ขยับตามแถว เพราะยืนยันแล้วว่า popup ของแถวอื่นในคอลัมน์เดียวกันเปิดตำแหน่งเดิม)"""
    l, t, r, b = cfg.DETAIL_POPUP_REGION
    dx = col * cfg.CARD_PITCH_X
    return (l + dx, t, r + dx, b)


# ---------------------------------------------------------------
# แปลงผล OCR เป็นข้อมูล
# ---------------------------------------------------------------

def _looks_like_item_name(text):
    """เช็คแบบหลวมๆ ว่าข้อความนี้ 'ดูเป็นชื่อไอเท็มจริง' ไหม (มีตัวอักษรมากพอ + มีคำจริง
    ยาว >=3 ตัวอักษรติดกันอย่างน้อย 1 คำ) ใช้กรองขยะ OCR สั้นๆ ที่มาจากไอคอน/ป้ายตกแต่ง/
    แบนเนอร์ประกาศทับ (เช่น 'dt ot 4', 'DQ') ออกจากชื่อไอเท็มจริง ใช้ร่วมกันทั้งตอนอ่าน
    ชื่อจากการ์ด, popup รายละเอียด, และแผง Buy"""
    text = text or ""
    return sum(ch.isalpha() for ch in text) >= 4 and bool(re.search(r"[A-Za-z]{3,}", text))


def parse_card_summary(text):
    """แยกข้อมูลคร่าวๆ จากข้อความในการ์ด (ชื่อ, ราคา, เวลานับถอยหลัง)
    รูปแบบจริงอาจต่างกันเล็กน้อย - ปรับ regex ตรงนี้หลังดูผลจริงจาก calibrate.py
    (ชื่ออาจถูกไอคอนหัวใจบังตัวอักษรแรก ถ้าเจอปัญหานี้บ่อยให้ขยับ
    FIRST_CARD_TEXT_REGION ให้เริ่มถัดจากไอคอนหัวใจไปอีกหน่อย)"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None

    timer_match = re.search(r"\d{1,2}:\d{2}", text)
    timer = timer_match.group(0) if timer_match else ""

    # ราคาบนการ์ดมีไอคอนเหรียญนำหน้าเสมอ ซึ่ง OCR อ่านเป็นสัญลักษณ์ขยะติดมาในบรรทัด
    # เดียวกัน (เช่น '© 400000', '€ 1000000', 'emi' © 14000000') เพราะฉะนั้นห้ามเช็ค
    # ว่า "ทั้งบรรทัดเป็นตัวเลขล้วน" (ไม่ผ่านเลยสักการ์ด - ตรวจจาก log จริงแล้วราคาว่าง
    # ทุกรายการ ทำให้คีย์เทียบไอเท็มเหลือแค่ชื่อสกิล แล้วไอเท็มคนละชิ้นที่สกิลซ้ำกันโดน
    # มองเป็นชิ้นเดียวกัน) ให้ค้นหา "ก้อนตัวเลขติดกันยาว >= 4 หลัก" ในบรรทัดแทน
    # (ตัวจับเวลาอย่าง '54:46' ไม่โดนจับเพราะโคลอนตัดก้อนตัวเลขให้สั้นกว่า 4 หลักเสมอ)
    price = ""
    for ln in reversed(lines):
        m = re.search(r"\d[\d,]{3,}", ln)
        if m:
            digits = m.group(0).replace(",", "")
            if len(digits) >= 4:
                price = digits
                break

    # หาบรรทัดที่ "ดูเป็นชื่อไอเท็มจริง" - ข้ามบรรทัดขยะสั้นๆ ไปเรื่อยๆ (เช่น เศษไอคอน/badge
    # ที่หลุดเข้ามาในกรอบ อย่าง '"OL' ที่เจอจริงจากการ์ดชิ้นหนึ่ง) แทนที่จะหยุดที่บรรทัดแรก
    # ที่เจอทันที เพราะเคยเจอบั๊กว่าถ้าบรรทัดแรกเป็นขยะสั้นๆ โค้ดเดิมจะหยิบมันมาเป็นชื่อ
    # แล้วโดนกฎ "ชื่อสั้นเกินไป = ช่องว่าง" ทิ้งไปทั้งใบ ทั้งที่จริงมีชื่อไอเท็มจริงอยู่บรรทัด
    # ถัดไป ทำให้การ์ดที่มีไอเท็มจริงถูกมองข้ามไม่คลิกเข้าไปเลย
    name_line = ""
    for ln in lines:
        if ln == timer:
            continue
        stripped = ln.replace(",", "")
        if stripped.isdigit():
            continue
        # ใช้ "จำนวนตัวอักษร a-z" แทนความยาวรวม เพราะบรรทัดขยะบางบรรทัด (เช่น
        # '. 28:38 |' ที่เจอจริง) ยาวพอจะผ่านเกณฑ์ความยาวเฉยๆ แต่แทบไม่มีตัวอักษรเลย
        # (ส่วนใหญ่เป็นตัวเลข/สัญลักษณ์) ชื่อไอเท็มจริงต้องมีตัวอักษรเยอะพอสมควร - รวมถึงต้อง
        # มีคำจริงยาว >=3 ตัวอักษรติดกันด้วย (กันป้ายตกแต่งมุมการ์ด เช่น ไอคอนธง/ประกายไฟ
        # บอกว่าเพิ่งลงขาย ที่โดน OCR อ่านเป็นขยะกระจัดกระจายแต่ดันมี alpha_count>=4 พอดี
        # เช่น 'dt ot 4' ซึ่งไม่ใช่ชื่อไอเท็มเลย แต่ทำให้บรรทัดชื่อจริงที่อยู่ถัดไปถูกข้ามไปเฉยๆ)
        if not _looks_like_item_name(ln):
            continue  # ขยะ ข้ามไปหาบรรทัดถัดไป
        name_line = ln
        break

    # ช่องกริดที่ไม่มีไอเท็มขายจริง (เช่น หน้าสุดท้ายที่มีของไม่ครบ 8 ชิ้น) จะไม่มีบรรทัดไหน
    # ผ่านเกณฑ์ความยาวขั้นต่ำเลยสักบรรทัด (มีแต่ขยะสั้นๆ ทั้งใบ) เช่นนี้ name_line จะว่างเปล่า
    # ให้ถือว่าเป็นช่องว่าง ไม่มีไอเท็มจริง ไม่ต้องคลิกเข้าไป
    if not name_line:
        return None

    return {"name": name_line, "price": price, "timer": timer}


# ---------------------------------------------------------------
# แท็บ Shadow Gear (แยกจากกริด All Items) - การ์ดรูปแบบต่างกัน: ไม่มีตัวจับเวลานับถอยหลัง
# แสดงชื่อไอเท็มเป็นตัวหนาสีขาวบนแบนเนอร์สี ตามด้วยบรรทัด "Minimum Price <ราคา>" แทน
# ---------------------------------------------------------------

SHADOW_GEAR_PRICE_RE = re.compile(r"\d[\d,]{3,}")


def parse_shadow_gear_list(text):
    """แยกรายการไอเท็มทั้งหมดจากข้อความ OCR ของหน้าลิสต์แท็บ Shadow Gear (แคปมาทีเดียว
    ทั้งกรอบ ไม่ใช่ทีละการ์ดแบบ All Items เพราะไม่รู้ grid/pitch ของแท็บนี้แน่ชัด) วิธีจับคู่:
    ไล่ทีละบรรทัด เก็บบรรทัดที่ "ดูเป็นชื่อไอเท็มจริง" (ใช้เกณฑ์เดียวกับการ์ด All Items) ไว้เป็น
    'ชื่อที่รออยู่' จนกว่าจะเจอบรรทัดที่มีตัวเลขราคา (>=4 หลักติดกัน) ก็จับคู่ชื่อที่รอไว้ล่าสุด
    กับราคานั้นเป็น 1 ไอเท็ม แล้วเคลียร์ชื่อที่รอไว้ - ถ้าไม่มีไอเท็มเลย (เช่น "No items
    available for display.") จะไม่มีบรรทัดไหนมีราคาเลย คืน list ว่างเปล่าไปเอง"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    items = []
    pending_name = None
    for ln in lines:
        m = SHADOW_GEAR_PRICE_RE.search(ln)
        if m:
            digits = m.group(0).replace(",", "")
            if len(digits) >= 4 and pending_name:
                items.append({"name": pending_name, "price": digits})
                pending_name = None
            continue
        if _looks_like_item_name(ln):
            pending_name = ln
    return items


def shadow_gear_item_key(item):
    """คีย์เทียบไอเท็ม Shadow Gear ว่าเป็นชิ้นเดิมหรือใหม่ - ใช้ชื่อ+ราคา เพราะการ์ดในแท็บนี้
    ไม่มีตัวจับเวลานับถอยหลังให้ใช้แยกรอบลงขาย (ต่างจาก item_key() ของ All Items)"""
    name = (item.get("name") or "").strip().lower()
    price = (item.get("price") or "").strip()
    return f"{name}|{price}"


# บรรทัดเลเวลไอเท็มใน popup ("Lv.:55") - OCR อ่านเพี้ยนได้หลายแบบจากภาพจริง:
# 'Lv.:57 @:1', 'Lv55 S:1', '1 Lv.:55 @:1' (มีขยะนำหน้า), '1v.:55' (L กลายเป็น 1),
# 'ly. 255' (โคลอนกลายเป็นเลข 2 ปนเข้าไปในตัวเลข) - regex เลยต้องยอมรับ L/l/1/I/| ตามด้วย
# v/y และเครื่องหมายวรรคตอนคั่นแบบหลวมๆ แล้วค่อยกรองความสมเหตุสมผลของตัวเลขทีหลัง
ITEM_LEVEL_RE = re.compile(r"(?:^|[\s\W])[Ll1I|][vy]\s*[.:;,]*\s*(\d{1,3})\b")


def parse_item_level(lines):
    """หาเลเวลไอเท็มจากบรรทัดต้นๆ ของ popup (บรรทัด 'Lv.:55' อยู่ก่อนหัวข้อ Base Stats
    เสมอ) - จำกัดการค้นหาแค่ช่วงก่อน Base Stats/Special Skill เพื่อไม่ให้ไปจับคำว่า
    'Lv. 15' ที่อยู่ในคำอธิบายสกิลด้านล่าง คืน int หรือ None ถ้าอ่านไม่ออก
    ตัวเลขที่เกิน 99 ถือว่าอ่านเพี้ยน (เช่น 'ly. 255' ที่โคลอนกลายเป็นเลข 2) คืน None
    หมายเหตุ: นี่เป็นวิธีสำรอง - วิธีหลักคือ read_item_level_from_popup() ที่ crop
    เฉพาะแถบบรรทัดเลเวลมาขยายแล้ว OCR แยก แม่นกว่าการอ่านจากข้อความ popup ทั้งใบมาก"""
    stop = min(len(lines), 8)
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "base stat" in low or "special skill" in low:
            stop = i
            break
    for ln in lines[:stop]:
        m = ITEM_LEVEL_RE.search(ln)
        if m:
            level = int(m.group(1))
            if 1 <= level <= 99:
                return level
            return None
    return None


def _popup_white_section_top(arr):
    """หาแถวแรก (จากบนลงล่าง) ที่เป็นพื้นขาวของ popup ต่อเนื่องอย่างน้อย 3 แถว - คือ
    จุดที่หัวสีแดง/ส้มของ popup จบแล้วเข้าสู่โซนเนื้อหาพื้นขาว ซึ่งบรรทัด 'Lv.:55'
    เป็นบรรทัดแรกของโซนนี้เสมอ (ไม่ fix พิกัดตายตัวเพราะความสูงหัว popup ไม่คงที่ -
    บางใบโดนหน้าจอตัดหัวหายไปบางส่วน)"""
    brightness = arr.max(axis=2)
    row_white_frac = (brightness > 235).mean(axis=1)
    for y in range(arr.shape[0] - 5):
        if row_white_frac[y] > 0.8 and row_white_frac[y + 1] > 0.8 and row_white_frac[y + 2] > 0.8:
            return y
    return None


def read_item_level_from_popup(popup_img):
    """อ่านเลเวลไอเท็มด้วยการ crop เฉพาะ "แถบบรรทัด Lv.:55" (แถบบนสุดของโซนพื้นขาว
    ใต้หัว popup) มาขยาย 3 เท่าแล้ว OCR แบบบรรทัดเดียว (psm 7) แยกต่างหาก
    วิธีนี้ทดสอบกับภาพ popup จริง 100 ใบแล้วอ่านถูก 97 ใบ ในขณะที่การดึงเลเวลจาก
    ข้อความ OCR ของ popup ทั้งใบ (parse_item_level) พลาดราว 1 ใน 3 เพราะบรรทัดเลเวล
    ตัวเล็กมากเมื่อเทียบกับ popup ทั้งใบ Tesseract มักอ่านข้ามหรืออ่านควบกับบรรทัดอื่น
    คืน int หรือ None ถ้าอ่านไม่ออก"""
    arr = np.array(popup_img.convert("RGB"))
    y0 = _popup_white_section_top(arr)
    if y0 is None:
        return None
    band = popup_img.crop((0, y0, popup_img.width, min(y0 + 60, popup_img.height)))
    band = band.resize((band.width * 3, band.height * 3), Image.LANCZOS)
    text = pytesseract.image_to_string(band, lang=cfg.OCR_LANGUAGES, config="--psm 7").strip()
    m = re.search(r"[Ll1I|][vy]\s*[.:;,]*\s*(\d{1,3})", text)
    if m:
        level = int(m.group(1))
        if 1 <= level <= 99:
            return level
    return None


def parse_item_detail(text):
    """แยก popup รายละเอียด (ชื่อ, ประเภท, เลเวล, special skill พร้อมคำอธิบาย)
    ตามรูปแบบที่เห็นจริง เช่น:
        Royal Healing Staff
        Weapon · 1H Rod
        ...
        Lv.:55
        ...
        Special Skill:
        Frigg's Chant LV.1
        Gain M.ATK equal to (Gear Refine Lv. x Refine Lv.), up to Lv. 15.
        Holy Incarnation LV.1
        Holy DMG +3%. (Does not apply to special skills of other classes)
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    result = {"name": "", "item_type": "", "level": None, "special_skills": []}
    if not lines:
        return result

    # ปกติชื่อไอเท็มคือบรรทัดแรกเสมอ แต่บางจังหวะแบนเนอร์ประกาศของเกม (หรือไอคอน/เอฟเฟกต์
    # ในหัว popup) ทับพอดีตอนแคปภาพ ทำให้ OCR อ่านบรรทัดแรกเป็นขยะสั้นๆ (เช่น 'DQ',
    # 'TEED nae GP') ทั้งที่ชื่อจริงอยู่ถัดไปไม่กี่บรรทัด (ยืนยันจากภาพจริง) เช็คว่าบรรทัด
    # แรกๆ (ก่อนถึงประเภทไอเท็ม/Lv./Base Stats) ดูเป็นชื่อจริงไหม (มีคำ >=3 ตัวอักษรติดกัน
    # อย่างน้อย 1 คำ) ถ้าไม่ใช่ค่อยลองบรรทัดถัดไป แทนที่จะใช้บรรทัดแรกเสมอโดยไม่เช็คอะไร
    result["name"] = lines[0]
    for ln in lines[:5]:
        low = ln.lower()
        # ข้ามบรรทัดหัวข้อที่รู้จักอยู่แล้ว (ไม่ใช่ชื่อไอเท็มแน่ๆ ต่อให้ตัวอักษรพอจะผ่าน
        # เกณฑ์ "ดูเป็นชื่อ" ก็ตาม เช่น "Base Stats:" ที่มีคำยาวพอจะผ่าน _looks_like_item_name)
        if "·" in ln or "base stat" in low or "overall rat" in low:
            continue
        if _looks_like_item_name(ln):
            result["name"] = ln
            break
    result["level"] = parse_item_level(lines)
    for ln in lines[1:4]:
        if "·" in ln:
            result["item_type"] = ln
            break

    skill_start = None
    for i, ln in enumerate(lines):
        if "special skill" in ln.lower():
            skill_start = i + 1
            break

    if skill_start is not None:
        current = None
        for ln in lines[skill_start:]:
            if is_skill_title_line(ln):
                if current:
                    result["special_skills"].append(current)
                # ตัดขยะท้ายบรรทัดทิ้ง เช่น ไอคอนเล็กๆ ข้างชื่อสกิลที่ OCR อ่านเพี้ยน
                # เป็นตัวอักษรแปลกปลอมต่อท้าย (เช่น "Frigg's Chant LV.1 i" -> "...LV.1")
                clean_title = re.sub(r"(\bLV\.?\s*\d+)\s*.{0,5}$", r"\1", ln, flags=re.IGNORECASE)
                current = {"title": clean_title, "description": []}
            elif current is not None:
                current["description"].append(ln)
        if current:
            result["special_skills"].append(current)
        for sk in result["special_skills"]:
            sk["description"] = " ".join(sk["description"])

    return result


TIMER_RE = re.compile(r"(\d{1,3}):(\d{2})")


def _timer_to_seconds(timer_str):
    m = TIMER_RE.search(timer_str or "")
    if not m:
        return None
    minutes, seconds = int(m.group(1)), int(m.group(2))
    return minutes * 60 + seconds


def item_key(item):
    """คีย์เทียบว่าเป็นไอเท็มเดิมหรือใหม่ - อิงจาก 'ชื่อ special skill' (เรียงลำดับแล้ว) +
    'ราคา' เป็นหลัก ไม่ใช้ชื่อไอเท็มแล้ว เพราะพบว่า OCR อ่านชื่อไอเท็มเพี้ยน/มีขยะนำหน้าบ่อย
    กว่าที่คิด (เช่น '{ Royal Poison Knife I' บ้าง 'Royal Poison Knife I' บ้าง หรือบางรอบ
    โดนตัดหัวจนเหลือ 'al Elemental Resistance Sho') ทำให้จับคู่ของเดิมไม่ได้ ในขณะที่ชื่อ
    special skill (เช่น "Thor's Edge LV.1", "Sif's Protection LV.1") เป็นข้อความสั้น
    ตัวหนา อยู่ในกรอบสีชัดเจน OCR อ่านได้เสถียรกว่ามาก ส่วนตัวจับเวลานับถอยหลัง (เวลา)
    ใช้แยกต่างหากใน run_once() เพื่อตัดสินว่า 'ไอเท็มชุดสกิล+ราคานี้' ที่เจอมาก่อนแล้ว
    ถูกลงขายใหม่ (relist) จริง หรือแค่ของเดิมที่ยังค้างขายอยู่ (เช็คว่าเวลารีเซ็ตขึ้นมาใหม่
    ไม่ใช่แค่ลดลงตามเวลาที่ผ่านไปตามปกติ)

    ข้อควรทราบ: ถ้ามีไอเท็มคนละชิ้นที่บังเอิญมีชุด special skill เหมือนกันเป๊ะ (เช่น
    weapon ต่างชนิดที่สุ่มได้สกิลเสริมตัวเดียวกัน) และราคาบังเอิญตรงกันหรืออ่านราคาไม่ออก
    ทั้งคู่ ระบบจะมองว่าเป็น 'รายการเดียวกัน' - เป็นข้อแลกเปลี่ยนที่ยอมรับเพื่อความเสถียร"""
    skill_titles = "|".join(
        sorted(sk["title"].strip().lower() for sk in item.get("special_skills", []))
    )
    price = (item.get("price") or "").strip()
    return f"{skill_titles}|{price}"


# ---------------------------------------------------------------
# ตรวจว่า "ยังมีหน้าถัดไปอีกไหม" (3 ชั้น - ดูหัวข้อ 5.1 ใน market_config.py)
# ---------------------------------------------------------------

def _grid_region():
    """กรอบรวมของลิสต์การ์ดทั้งหน้า คำนวณจากการ์ดใบแรก + ระยะห่าง (ไม่ต้องคาลิเบรตเพิ่ม)
    ใช้แคปภาพมาเทียบก่อน/หลังกดปุ่มเปลี่ยนหน้า ว่าหน้าเปลี่ยนจริงหรือกดแล้วไม่มีอะไรเกิดขึ้น"""
    l, t, r, b = cfg.FIRST_CARD_TEXT_REGION
    return (
        l, t,
        r + (cfg.GRID_COLS - 1) * cfg.CARD_PITCH_X,
        b + (cfg.GRID_ROWS - 1) * cfg.CARD_PITCH_Y,
    )


def _page_signature():
    """ย่อภาพลิสต์ทั้งหน้าเหลือ 96x96 ขาวดำ ไว้เทียบว่าหน้าเปลี่ยนไปไหม (ย่อก่อนเพื่อให้
    ทนต่อความต่างเล็กๆ น้อยๆ อย่างตัวจับเวลาที่เดินตลอดเวลา และเร็วกว่าเทียบภาพเต็ม)"""
    l, t, r, b = _grid_region()
    img = pyautogui.screenshot(region=(l, t, r - l, b - t))
    gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    return cv2.resize(gray, (96, 96), interpolation=cv2.INTER_AREA).astype(np.int16)


def _signature_diff_ratio(a, b):
    """สัดส่วนพิกเซล (0.0-1.0) ที่ต่างกันเกิน 25 ระดับสีระหว่างสองภาพย่อ"""
    if a is None or b is None:
        return 1.0
    return float((np.abs(a - b) > 25).mean())


def _arrow_brightness(pos, debug_name=None):
    """วัดความสว่างเฉลี่ย (0-255) ของกรอบสี่เหลี่ยมรอบจุด pos - ใช้แยก "ปุ่มลูกศรที่กดได้"
    (สว่าง/มีสี) ออกจาก "ปุ่มที่กดไม่ได้แล้ว" (จาง/เทา) คืน None ถ้าแคปภาพไม่ได้"""
    rad = getattr(cfg, "NEXT_PAGE_CHECK_RADIUS", 16)
    x, y = pos
    left, top = max(x - rad, 0), max(y - rad, 0)
    try:
        img = pyautogui.screenshot(region=(left, top, rad * 2, rad * 2))
    except Exception as e:
        print(f"[warn] แคปภาพปุ่มลูกศรที่ {pos} ไม่สำเร็จ: {e}")
        return None
    if debug_name:
        _save_fixed_debug(img, debug_name)
    return float(np.array(img.convert("RGB")).max(axis=2).mean())


def check_next_page():
    """ตัดสินว่า "ยังมีหน้าถัดไปอีกไหม" คืน (has_next, reason)

    ไล่ตัดสิน 3 ชั้นจากน่าเชื่อถือมากไปน้อย (รายละเอียดเต็มอยู่ในหัวข้อ 5.1 ของ
    market_config.py):
      1. OCR เลขหน้า "n/m" - ถ้าอ่านออกใช้เลย แม่นที่สุด
      2. ความสว่างของปุ่มลูกศรขวา - ใช้เมื่อตั้ง NEXT_PAGE_ENABLED_MIN_BRIGHTNESS ไว้แล้ว
      3. ตอบ True ไว้ก่อน แล้วให้ scan_market() กดเปลี่ยนหน้าจริงแล้วเทียบภาพเอา

    จุดสำคัญที่ต่างจากโค้ดเดิม (page_is_last): เดิมถ้า OCR อ่านเลขหน้าไม่ออกจะถือว่า
    "หน้าสุดท้าย" แล้วหยุดทันที ซึ่งเป็นสาเหตุที่สแกนจบแค่หน้า 1 ทุกครั้ง (กรอบเลขหน้า
    เล็กแค่ ~65x44 px OCR อ่านพลาดบ่อยมาก) ตอนนี้อ่านไม่ออกจะไม่หยุดเอง แต่ไปพึ่ง
    ชั้น 2/3 แทน ซึ่งยังกันลูปไม่รู้จบได้เพราะชั้น 3 หยุดเมื่อกดแล้วหน้าไม่เปลี่ยน
    (และยังมี MAX_PAGES คุมอีกชั้น)"""
    page_text = ocr_region(
        cfg.PAGE_INDICATOR_REGION, tag="page_indicator", psm=7,
        fixed_debug_name="page_indicator",
    )

    # ชั้น 1: OCR เลขหน้า
    m = PAGE_NUM_RE.search(page_text)
    if m:
        current, total = int(m.group(1)), int(m.group(2))
        # กันค่าเพี้ยนแบบ "0/8", "3/1", "1/99" ที่มาจาก OCR อ่านผิด - ถ้าไม่สมเหตุสมผล
        # ให้ตกไปใช้ชั้นถัดไปแทนที่จะเชื่อตัวเลขมั่วๆ
        if 1 <= current <= total <= cfg.MAX_PAGES:
            return current < total, f"อ่านเลขหน้าได้ '{page_text}' -> หน้า {current}/{total}"

    # ชั้น 2: ความสว่างของปุ่มลูกศร
    next_pos = getattr(cfg, "NEXT_PAGE_CHECK_POS", None) or cfg.NEXT_PAGE_POS
    next_bright = _arrow_brightness(next_pos, debug_name="next_page_check")

    prev_pos = getattr(cfg, "PREV_PAGE_CHECK_POS", None)
    prev_bright = _arrow_brightness(prev_pos, debug_name="prev_page_check") if prev_pos else None

    measured = f"ลูกศรขวา {next_bright:.1f}" if next_bright is not None else "ลูกศรขวา วัดไม่ได้"
    if prev_bright is not None:
        measured += f" / ลูกศรซ้าย {prev_bright:.1f}"
    print(f"[page] OCR เลขหน้าอ่านไม่ออก (ได้ '{page_text}') - วัดความสว่างปุ่มแทน: {measured}")

    threshold = getattr(cfg, "NEXT_PAGE_ENABLED_MIN_BRIGHTNESS", None)
    if threshold is not None and next_bright is not None:
        return next_bright >= threshold, f"{measured} (เกณฑ์ปุ่มกดได้ >= {threshold})"

    # ชั้น 3: ตัดสินไม่ได้ -> ลองกดดูก่อน แล้วให้ผู้เรียกเทียบภาพเอาว่าเปลี่ยนจริงไหม
    return True, f"{measured} (ยังไม่ตั้ง NEXT_PAGE_ENABLED_MIN_BRIGHTNESS - จะลองกดแล้วเทียบภาพแทน)"


# ---------------------------------------------------------------
# สแกนตลาด
# ---------------------------------------------------------------

def _region_looks_like_card(card_img):
    """แยก 'ช่องว่างจริง' ออกจาก 'การ์ดที่มีของแต่ OCR อ่านไม่ออก' ด้วยสัดส่วนพิกเซล
    สว่างเกือบขาว (พื้นการ์ดเป็นสีขาว/ครีม ส่วนช่องว่างเป็นท้องฟ้าสีฟ้า) - วัดจากภาพจริง:
    การ์ดจริง (รวมที่โดนแบนเนอร์ประกาศทับบางส่วน) ขาว 51-73% ช่องว่างจริง 0-28%
    เกณฑ์ 40% อยู่กึ่งกลางระหว่างสองกลุ่มพอดี"""
    arr = np.array(card_img.convert("RGB"))
    brightness = arr.max(axis=2)
    return float((brightness > 235).mean()) >= 0.40


def scan_one_card(row, col):
    text_region = card_text_region(row, col)
    # อ่านสีชื่อ option จากการ์ดตอนนี้เลย ตอน "ยังไม่ได้คลิก" ไอคอนเปิด popup
    # (สีจริงที่ผู้ใช้อ้างอิงอยู่บนการ์ด ไม่ใช่สีกล่องใน popup ซึ่งเป็นคนละชุดสีกัน)
    summary_text, card_image = ocr_region(text_region, tag=f"card_r{row}c{col}", return_image=True)
    summary = parse_card_summary(summary_text)

    # OCR อ่านการ์ดไม่ออก ไม่ได้แปลว่าช่องว่างเสมอไป - แบนเนอร์ประกาศของเกม (แถบดำ
    # เลื่อนผ่านด้านบนจอ ขึ้นบ่อยมาก) ชอบทับการ์ดแถวบนพอดีตอนแคปภาพ ทำให้ข้อความ
    # ในการ์ดอ่านไม่ออกทั้งใบ โค้ดเดิมตีความว่า "ช่องว่าง" แล้วข้ามไม่คลิกไอเท็มไปเลย
    # (บั๊กที่ผู้ใช้เจอ: 2 แถวบนหรือใบขวาบนโดนข้ามเป็นบางรอบ) วิธีแก้: ถ้าภาพดูเป็นการ์ด
    # จริง (พื้นขาวเยอะ) ให้รอแบนเนอร์เลื่อนผ่านแล้วแคปใหม่ ลองซ้ำได้หลายรอบ
    if summary is None:
        for _ in range(cfg.OBSTRUCTED_CARD_RETRIES):
            if not _region_looks_like_card(card_image):
                return None  # ช่องว่างจริง (พื้นหลังท้องฟ้า) ไม่มีไอเท็ม
            time.sleep(cfg.OBSTRUCTED_CARD_RETRY_DELAY)
            summary_text, card_image = ocr_region(
                text_region, tag=f"card_r{row}c{col}", return_image=True
            )
            summary = parse_card_summary(summary_text)
            if summary is not None:
                break

    if summary is None:
        if not _region_looks_like_card(card_image):
            return None  # ช่องว่างจริง
        # ลองครบทุกรอบแล้วยังอ่านไม่ออก แต่มีการ์ดอยู่จริงแน่ๆ - เดินหน้าสแกนต่อ
        # โดยไม่มีข้อมูลจากหน้าการ์ด (ชื่อเอาจากแผง Buy/popup แทนได้ สกิลเอาจาก popup)
        # ดีกว่าข้ามไอเท็มทั้งชิ้นไปเงียบๆ
        print(f"[warn] การ์ด r{row}c{col} อ่านข้อความไม่ออก (โดนแบนเนอร์ทับนาน?) - สแกนต่อโดยไม่มีราคา/เวลา")
        summary = {"name": "", "price": "", "timer": ""}

    orange_skill_count, purple_skill_count = detect_card_option_colors(card_image)

    # ชื่อไอเท็มจากแผง Buy ฝั่งขวาของจอ (ถ้าคาลิเบรตไว้แล้ว) - ตัวอักษรใหญ่สีแดงบนพื้นขาว
    # อ่านแม่นกว่าชื่อในหัว popup รายละเอียดมาก (popup มักมีไอคอน/เอฟเฟกต์ทับชื่อ) จึงให้
    # ความสำคัญเป็นอันดับแรก ต้องอ่านให้จบ "ก่อน" คลิกไอคอนเปิด popup รายละเอียด เพราะ popup
    # เปิดมาแล้วจะบังทับแผง Buy ฝั่งขวาพอดี (บั๊กที่ผู้ใช้เจอ) วิธีอ่านชื่อโดยไม่เปิด popup:
    # คลิกที่ "ราคา" บนการ์ดก่อน (แค่เลือกไอเท็มนั้น อัปเดตแผง Buy ไม่เปิด popup บัง) แล้ว
    # ค่อยอ่านชื่อจากแผง Buy จากนั้นจึงคลิกไอคอนเปิด popup เพื่ออ่านสกิล/รายละเอียดต่อ
    buy_panel_name = ""
    if cfg.BUY_PANEL_NAME_REGION:
        price_pos = card_price_click_pos(row, col)
        pyautogui.click(*price_pos)
        time.sleep(cfg.WAIT_AFTER_SELECT_ITEM)
        buy_panel_name = ocr_region(
            cfg.BUY_PANEL_NAME_REGION, tag=f"buyname_r{row}c{col}", psm=7
        ).strip()
        # บางจังหวะแบนเนอร์ประกาศที่เลื่อนผ่านด้านบนจอ (เช่น "Congratulations to...")
        # อาจทับกรอบนี้พอดี ทำให้ OCR อ่านได้ข้อความสั้นๆ/เพี้ยนแทนชื่อจริง เช็คว่าดูเป็นชื่อ
        # ไอเท็มจริงไหม (ไม่ใช่แค่ความยาว) ถ้าไม่ใช่ ลองรออีกรอบแล้วแคปใหม่ก่อน (เผื่อ
        # แบนเนอร์เลื่อนผ่านไปแล้ว) ถ้ายังไม่ผ่านอีกค่อยทิ้งแล้ว fallback ไปใช้ชื่อจาก
        # popup/การ์ดแทน แทนที่จะปล่อยให้ข้อมูลเพี้ยนหลุดเข้า Discord
        if not _looks_like_item_name(buy_panel_name):
            time.sleep(cfg.OBSTRUCTED_CARD_RETRY_DELAY)
            buy_panel_name = ocr_region(
                cfg.BUY_PANEL_NAME_REGION, tag=f"buyname_r{row}c{col}", psm=7
            ).strip()
        if not _looks_like_item_name(buy_panel_name) or len(buy_panel_name) < 5:
            buy_panel_name = ""

    icon_pos = card_icon_click_pos(row, col)
    pyautogui.click(*icon_pos)
    time.sleep(cfg.WAIT_AFTER_OPEN_DETAIL)
    detail_text, popup_image = ocr_region(
        detail_popup_region(col), tag=f"detail_r{row}c{col}", return_image=True
    )
    detail = parse_item_detail(detail_text)

    # แบนเนอร์ประกาศของเกมทับหัว popup พอดีตอนแคปภาพได้เหมือนกัน (ยืนยันแล้วจากภาพจริง:
    # เจอกรณีที่หัว popup ทั้งบรรทัดถูกทับจนอ่านได้แค่ 'DQ' สั้นๆ ทั้งที่ชื่อจริงยาวกว่านั้นมาก)
    # ถ้าชื่อที่อ่านได้ยังดูไม่เป็นชื่อไอเท็มจริงเลย ให้รออีกรอบ (แบนเนอร์เลื่อนผ่านไม่กี่วินาที)
    # แล้วแคปใหม่ ลองได้ครั้งเดียวพอ (ไม่ลูปไม่จำกัดเพราะบางไอเท็มก็มีจริงๆ ที่หัว popup
    # อ่านยากถาวร ไม่ใช่แค่แบนเนอร์ชั่วคราว)
    if not _looks_like_item_name(detail["name"]):
        time.sleep(cfg.OBSTRUCTED_CARD_RETRY_DELAY)
        detail_text, popup_image = ocr_region(
            detail_popup_region(col), tag=f"detail_r{row}c{col}", return_image=True
        )
        detail = parse_item_detail(detail_text)

    # เลเวลไอเท็ม: ใช้วิธี crop แถบบรรทัด Lv. มา OCR แยก (แม่นกว่ามาก) เป็นหลัก
    # ถ้าอ่านไม่ออกค่อยถอยไปใช้ค่าที่ดึงจากข้อความ popup ทั้งใบ (parse_item_detail)
    item_level = read_item_level_from_popup(popup_image)
    if item_level is None:
        item_level = detail["level"]

    close_detail_popup()

    # คำนวณ "เวลาหมดประมูล" จริงจากเวลาปัจจุบัน + เวลานับถอยหลังที่อ่านได้จากการ์ด
    # ต้องคำนวณตรงนี้เลย (ณ วินาทีที่อ่านการ์ด) ไม่ใช่ไปคำนวณตอนส่งแจ้งเตือน เพราะกว่าจะ
    # สแกนครบทุกหน้าแล้วถึงคิวส่ง อาจผ่านไปหลายนาที เวลาหมดจะคลาดเคลื่อน
    expires_at = ""
    timer_sec = _timer_to_seconds(summary["timer"])
    if timer_sec is not None:
        expires_at = (
            datetime.datetime.now() + datetime.timedelta(seconds=timer_sec)
        ).strftime("%H:%M:%S")

    item = {
        "name": buy_panel_name or detail["name"] or summary["name"],
        "item_type": detail["item_type"],
        "level": item_level,
        "price": summary["price"],
        "timer": summary["timer"],
        "expires_at": expires_at,
        "special_skills": detail["special_skills"],
        "orange_skill_count": orange_skill_count,
        "purple_skill_count": purple_skill_count,
        "summary_raw": summary_text,
        "detail_raw": detail_text,
        "scanned_at": datetime.datetime.now().isoformat(timespec="seconds"),
        # ภาพ popup ดิบ เก็บไว้ในหน่วยความจำเฉยๆ เพื่อแนบไปกับ Discord ตอนแจ้งเตือน
        # (ไม่ถูกบันทึกลง market_last_snapshot.json เพราะเป็นรูปภาพ ไม่ใช่ข้อความ)
        "popup_image": popup_image,
    }
    item["key"] = item_key(item)
    return item


def scan_market():
    items = []
    page = 0
    pages_advanced = 0
    while True:
        for row in range(cfg.GRID_ROWS):
            for col in range(cfg.GRID_COLS):
                item = scan_one_card(row, col)
                if item:
                    items.append(item)

        page += 1
        if cfg.SCAN_ONLY_FIRST_PAGE:
            break
        if page >= cfg.MAX_PAGES:
            print(f"[page] ถึงขีดจำกัด MAX_PAGES ({cfg.MAX_PAGES} หน้า) แล้ว หยุดสแกน")
            break

        has_next, reason = check_next_page()
        if not has_next:
            print(f"[page] จบหน้า {page} - หน้าสุดท้ายแล้ว ({reason})")
            break
        print(f"[page] จบหน้า {page} - ไปหน้าถัดไป ({reason})")

        # กดเปลี่ยนหน้าแล้วยืนยันด้วยภาพว่า "เปลี่ยนจริง" - จำเป็นเพราะชั้น 3 ของ
        # check_next_page() ตอบ True ไว้ก่อนเมื่อตัดสินไม่ได้ ถ้ากดแล้วลิสต์เหมือนเดิม
        # เป๊ะแปลว่าอยู่หน้าสุดท้ายจริง (ปุ่มกดไม่ติด) ต้องหยุด ไม่งั้นจะวนสแกนหน้าเดิมซ้ำ
        # จนครบ MAX_PAGES เปลืองเวลาไปเปล่าๆ
        before_sig = _page_signature()
        go_to_next_page()
        after_sig = _page_signature()
        diff_ratio = _signature_diff_ratio(before_sig, after_sig)
        min_diff = getattr(cfg, "PAGE_CHANGE_MIN_DIFF_RATIO", 0.10)
        if diff_ratio < min_diff:
            print(f"[page] กดปุ่มหน้าถัดไปแล้วแต่ลิสต์แทบไม่เปลี่ยน "
                  f"(ต่างกัน {diff_ratio:.1%} < เกณฑ์ {min_diff:.0%}) - ถือว่าอยู่หน้าสุดท้ายแล้ว หยุดสแกน")
            break
        print(f"[page] เปลี่ยนไปหน้า {page + 1} สำเร็จ (ลิสต์ต่างจากหน้าก่อน {diff_ratio:.1%})")
        pages_advanced += 1

    if not cfg.SCAN_ONLY_FIRST_PAGE:
        go_to_first_page(pages_advanced)
    return items


def scan_shadow_gear_tab():
    """สแกนแท็บ "Shadow Gear" ฝั่งเมนูซ้ายแยกต่างหากจาก All Items - เรียกหลังจากสแกน
    All Items ครบทุกหน้าแล้วเท่านั้น (ไม่ปนกับสแกน All Items เพื่อไม่ให้ไอเท็มหมวดนี้ถูกจับคู่
    กับกฎแจ้งเตือนของ All Items โดยไม่ได้ตั้งใจ) คลิกแท็บ Shadow Gear แคปภาพกรอบลิสต์ทั้งใบ
    ครั้งเดียว (ไม่ไล่ทีละการ์ดแบบ All Items เพราะการ์ดแท็บนี้ไม่มีตัวจับเวลา/ไม่ต้องเปิด popup)
    แล้ว OCR แยกเป็นรายชื่อไอเท็ม คืนค่า (items, list_image) - list_image คือภาพที่แคปไว้
    เอาไปแนบกับ Discord ตอนแจ้งเตือนได้ (ผู้ใช้ต้องการให้ "capture จอเกม" แนบไปด้วย)
    หมายเหตุ: ยังไม่รองรับ pagination ของแท็บนี้ (ยังไม่เจอปุ่มเปลี่ยนหน้าในแท็บนี้จากภาพตัวอย่าง
    ที่มี - ถ้าจำนวน Shadow Gear ที่ลงขายพร้อมกันมากจนล้นหน้าจอ อาจต้องกลับมาเพิ่มทีหลัง)"""
    select_category({"pos": cfg.SHADOW_GEAR_TAB_POS})
    list_text, list_image = ocr_region(
        cfg.SHADOW_GEAR_LIST_REGION, tag="shadow_gear_list", return_image=True
    )
    items = parse_shadow_gear_list(list_text)
    for it in items:
        it["key"] = shadow_gear_item_key(it)
        it["scanned_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    select_category({"pos": cfg.ALL_ITEMS_TAB_POS})
    return items, list_image


# ---------------------------------------------------------------
# state / log
# ---------------------------------------------------------------

def load_previous_snapshot(path=None):
    path = path or cfg.STATE_JSON
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_snapshot(items_by_key, path=None):
    path = path or cfg.STATE_JSON
    # ตัด popup_image (เป็น PIL Image object) ออกก่อน เพราะ json.dump ใส่รูปภาพไม่ได้
    serializable = {
        k: {field: v for field, v in item.items() if field != "popup_image"}
        for k, item in items_by_key.items()
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)


def append_csv(items):
    file_exists = os.path.exists(cfg.OUTPUT_CSV)
    with open(cfg.OUTPUT_CSV, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["scanned_at", "name", "item_type", "level", "price", "timer", "special_skills", "orange_skill_count", "purple_skill_count", "key"])
        for it in items:
            skills_str = " || ".join(f"{sk['title']}: {sk['description']}" for sk in it["special_skills"])
            writer.writerow([it["scanned_at"], it["name"], it["item_type"], it.get("level") or "", it["price"], it["timer"], skills_str, it["orange_skill_count"], it["purple_skill_count"], it["key"]])


# ---------------------------------------------------------------
# แจ้งเตือน Discord
# ---------------------------------------------------------------

def matching_webhooks(item):
    """คืนรายการ webhook URL ของกฎใน cfg.NOTIFY_RULES ที่ไอเท็มนี้ตรงเกณฑ์
    (จำนวน option สีส้ม/ม่วงตรงเป๊ะ + เลเวลอยู่ในช่วงของกฎ + ชื่อมีคำที่กำหนดอยู่)
    ถ้าอ่านเลเวลไม่ออก (level เป็น None) จะถือว่าผ่านเกณฑ์เลเวลของทุกกฎที่เงื่อนไขอื่นตรง
    (fail-open ยอมแจ้งเกินดีกว่าพลาดไอเท็มดีเพราะ OCR อ่านเลขพลาดครั้งเดียว)
    คีย์ "orange"/"purple"/"min_level"/"max_level"/"name_contains" ในกฎ ทุกตัวเป็น optional
    - กฎที่ไม่ใส่คีย์ไหน แปลว่าไม่สนใจเงื่อนไขนั้นเลย (เช่น กฎ {"orange": 2} = ต้องส้ม 2
    ส่วนม่วงจะกี่อันก็ได้ / กฎที่ไม่มี min_level-max_level เลย = ไม่จำกัดเลเวล)
    "name_contains" เทียบแบบไม่สนตัวพิมพ์เล็ก/ใหญ่ว่าชื่อไอเท็ม (item["name"]) มีข้อความนี้
    อยู่ที่ไหนก็ได้ในชื่อหรือไม่ (ไม่ใช่แค่ขึ้นต้นด้วยเท่านั้น) ใช้กับกฎประเภท "ของหมวดนี้
    ทุกชิ้นไม่ว่า option จะเป็นยังไง" เช่น หมวด Shadow Gear ที่ชื่อไอเท็มมีคำว่า "Shadow"
    เสมอ - ใช้ "มีคำนี้อยู่ในชื่อ" แทน "ขึ้นต้นด้วย" เพราะการ์ด/แผง Buy บางครั้งโดนไอคอน
    หัวใจ/แบนเนอร์บังตัวอักษรแรกๆ ของชื่อไป (เช่น "Shadow Hunt Gloves..." เหลือ "ow Hunt
    Gloves...") ถ้าเช็คแบบขึ้นต้นเป๊ะจะพลาดกรณีนี้ไปเลย ทั้งที่คำว่า Shadow (หรือส่วนที่เหลือ)
    ยังอยู่ในชื่อ"""
    hooks = []
    level = item.get("level")
    name = (item.get("name") or "").strip().lower()
    for rule in cfg.NOTIFY_RULES:
        if not rule.get("webhook"):
            continue
        if rule.get("orange") is not None and item["orange_skill_count"] != rule["orange"]:
            continue
        if rule.get("purple") is not None and item["purple_skill_count"] != rule["purple"]:
            continue
        name_contains = rule.get("name_contains")
        if name_contains is not None and name_contains.strip().lower() not in name:
            continue
        min_level = rule.get("min_level")
        max_level = rule.get("max_level")
        if level is not None:
            if min_level is not None and level < min_level:
                continue
            if max_level is not None and level > max_level:
                continue
        hooks.append(rule["webhook"])
    return hooks


def _item_to_discord_embed(item, image_filename=None):
    # แสดง "เวลาหมดประมูล" (คำนวณไว้แล้วตอนสแกนการ์ดใน scan_one_card) แทนเวลาที่เหลือ
    # เพราะเวลาที่เหลือจะเก่าไปเรื่อยๆ นับจากวินาทีที่สแกน อ่านแล้วต้องมาคำนวณเองว่าหมดกี่โมง
    # (.get() เผื่อไอเท็มจาก snapshot รุ่นเก่าที่ยังไม่มีฟิลด์นี้)
    level = item.get("level")
    fields = [
        {"name": "เลเวล", "value": str(level) if level is not None else "-", "inline": True},
        {"name": "ราคา", "value": item["price"] or "-", "inline": True},
        {"name": "หมดเวลาตอน", "value": item.get("expires_at") or "-", "inline": True},
    ]
    for sk in item["special_skills"]:
        fields.append({
            "name": sk["title"],
            "value": (sk["description"] or "-")[:1024],  # Discord จำกัด 1024 ตัวอักษรต่อ field
            "inline": False,
        })
    embed = {
        "title": item["name"][:256] or "(ไม่ทราบชื่อไอเท็ม)",
        "description": item["item_type"] or None,
        "color": 0x5865F2,
        "fields": fields,
    }
    if image_filename:
        # attachment:// อ้างอิงไฟล์ที่แนบไปพร้อมกันใน multipart request เดียวกัน
        embed["image"] = {"url": f"attachment://{image_filename}"}
    return embed


def _image_to_png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def send_discord_notification(item, webhook_url):
    """ส่งไอเท็มใหม่ 1 ชิ้นเข้า Discord webhook ที่ระบุ เป็น embed พร้อมแนบภาพ popup
    รายละเอียดที่แคปไว้ตอนสแกน (ถ้าเปิด DISCORD_ATTACH_IMAGE ไว้) ไม่ throw exception
    ออกไปเด็ดขาด (แค่ print คำเตือน) เพื่อไม่ให้การแจ้งเตือนไอเท็มชิ้นใดชิ้นหนึ่งล้มเหลว
    แล้วทำให้ทั้งรอบสแกนพังไปด้วย (พลาดไม่บันทึก log/snapshot ของไอเท็มอื่นที่สแกนไปแล้ว)"""
    if not webhook_url:
        return

    try:
        img = item.get("popup_image") if cfg.DISCORD_ATTACH_IMAGE else None
        image_filename = "popup.png" if img is not None else None
        payload = {"embeds": [_item_to_discord_embed(item, image_filename=image_filename)]}

        if img is not None:
            resp = requests.post(
                webhook_url,
                data={"payload_json": json.dumps(payload)},
                files={"files[0]": (image_filename, _image_to_png_bytes(img), "image/png")},
                timeout=15,
            )
        else:
            resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code >= 300:
            print(f"[discord] ส่งไม่สำเร็จ ({resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        print(f"[discord] ส่งไม่สำเร็จ (ไอเท็ม {item.get('name', '?')}): {e}")


def notify_discord(new_items):
    """ส่งแต่ละไอเท็มไปยัง webhook ตามกฎที่มันตรง (คำนวณไว้แล้วใน item['notify_webhooks']
    โดย run_once) - ไอเท็มหนึ่งชิ้นอาจไปหลาย webhook ได้ถ้าตรงหลายกฎ (เช่น อ่านเลเวล
    ไม่ออกเลยเข้าได้ทุกช่วง)"""
    if not new_items:
        return
    if cfg.DISCORD_NOTIFY_PER_ITEM:
        for it in new_items:
            for hook in it.get("notify_webhooks", []):
                send_discord_notification(it, hook)
                time.sleep(cfg.DISCORD_SEND_DELAY_SEC)
    else:
        # โหมดรวมหลายไอเท็มไว้ข้อความเดียว: จัดกลุ่มตาม webhook ปลายทางก่อน แล้วค่อยแบ่ง
        # ส่งทีละ batch (Discord จำกัด 10 embeds ต่อข้อความ) แต่ละ embed แนบภาพของตัวเอง
        # ได้ด้วยไฟล์คนละชื่อในคำขอเดียวกัน
        items_by_hook = {}
        for it in new_items:
            for hook in it.get("notify_webhooks", []):
                items_by_hook.setdefault(hook, []).append(it)

        for hook, items in items_by_hook.items():
            for batch_start in range(0, len(items), 10):
                batch = items[batch_start:batch_start + 10]
                try:
                    embeds = []
                    files = {}
                    for i, it in enumerate(batch):
                        img = it.get("popup_image") if cfg.DISCORD_ATTACH_IMAGE else None
                        filename = f"popup_{i}.png" if img is not None else None
                        embeds.append(_item_to_discord_embed(it, image_filename=filename))
                        if img is not None:
                            files[f"files[{i}]"] = (filename, _image_to_png_bytes(img), "image/png")

                    payload = {"embeds": embeds}
                    if files:
                        resp = requests.post(
                            hook,
                            data={"payload_json": json.dumps(payload)},
                            files=files,
                            timeout=20,
                        )
                    else:
                        resp = requests.post(hook, json=payload, timeout=10)
                    if resp.status_code >= 300:
                        print(f"[discord] ส่งไม่สำเร็จ ({resp.status_code}): {resp.text[:200]}")
                except Exception as e:
                    print(f"[discord] ส่งไม่สำเร็จ (batch เริ่มที่ index {batch_start}): {e}")
                time.sleep(cfg.DISCORD_SEND_DELAY_SEC)


def notify_shadow_gear(new_items, list_image):
    """ส่งไอเท็ม Shadow Gear ใหม่ (ทุกชิ้นที่เจอในสแกนรอบนี้) เข้า cfg.SHADOW_GEAR_WEBHOOK
    เป็นข้อความเดียว (1 embed ต่อไอเท็ม รวมกันในคำขอเดียว) แนบภาพหน้าจอลิสต์ที่แคปไว้ตอน
    scan_shadow_gear_tab() ภาพเดียว (ไม่ใช่คนละภาพต่อไอเท็ม เพราะเป็นภาพเดียวกันของทั้งหน้า)
    ไม่ throw exception ออกไปเด็ดขาด เหมือน notify_discord()/send_discord_notification()"""
    if not new_items or not cfg.SHADOW_GEAR_WEBHOOK:
        return
    try:
        embeds = [
            {
                "title": it["name"][:256] or "(ไม่ทราบชื่อไอเท็ม)",
                "description": f"ราคา: {it['price'] or '-'}",
                "color": 0x9B59B6,
            }
            for it in new_items[:10]  # Discord จำกัด 10 embeds ต่อข้อความ
        ]
        payload = {"embeds": embeds}
        if list_image is not None:
            embeds[0]["image"] = {"url": "attachment://shadow_gear.png"}
            resp = requests.post(
                cfg.SHADOW_GEAR_WEBHOOK,
                data={"payload_json": json.dumps(payload)},
                files={"files[0]": ("shadow_gear.png", _image_to_png_bytes(list_image), "image/png")},
                timeout=15,
            )
        else:
            resp = requests.post(cfg.SHADOW_GEAR_WEBHOOK, json=payload, timeout=10)
        if resp.status_code >= 300:
            print(f"[discord] ส่ง Shadow Gear ไม่สำเร็จ ({resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        print(f"[discord] ส่ง Shadow Gear ไม่สำเร็จ: {e}")


# ---------------------------------------------------------------
# main
# ---------------------------------------------------------------

def determine_new_items(current_by_key, previous):
    """คำนวณว่าไอเท็มไหนใน current_by_key เป็น "ไอเท็มใหม่" เทียบกับ previous (snapshot
    รอบก่อน) - ใช้เกณฑ์เดียวกันทั้ง All Items และ Shadow Gear (ตามที่ผู้ใช้ต้องการ) คืนค่า
    (new_items, is_first_run)

    รันครั้งแรกบนเครื่องนี้ (หรือหลังล้างข้อมูล) - snapshot ยังว่างเปล่า (ไม่มีคีย์ไหนเลย)
    กรณีนี้ให้ถือว่าทุกชิ้นที่เจอเป็นของใหม่ เพื่อให้ได้รายการของที่ขายอยู่ตอนนี้ครบถ้วนเป็นชุดแรก
    จากนั้นรอบถัดๆ ไปที่ snapshot มีข้อมูลแล้ว จะกลับไปใช้เกณฑ์ปกติ (ส่งเฉพาะไอเท็มใหม่จริงๆ
    ตามเวลา >55 นาที)"""
    is_first_run = not previous

    if is_first_run:
        return list(current_by_key.values()), is_first_run

    # ไอเท็ม "ใหม่" ตัดสินจากคีย์ (สกิล+ราคา) เป็นหลัก แล้วใช้เวลานับถอยหลังช่วยกรอง
    # เพิ่มเมื่ออ่านได้ - ไอเท็มที่เพิ่งลงประมูลใหม่ในเกมนี้จะเริ่มที่ 60 นาทีเต็มทุกครั้ง
    # และสแกนซ้ำทุก 3 นาที:
    #   - คีย์ไม่เคยเจอมาก่อน + อ่านเวลาได้ + เวลามากกว่า 55 นาที -> ใหม่แน่นอน
    #   - คีย์ไม่เคยเจอมาก่อน + อ่านเวลาได้ + เวลาไม่เกิน 55 นาที -> ของเดิมที่ลงขาย
    #     มาก่อนแล้ว (แค่คีย์เคยอ่านเพี้ยน/สแกนพลาด) ไม่แจ้ง แต่บันทึก snapshot ไว้เทียบ
    #   - คีย์ไม่เคยเจอมาก่อน + อ่านเวลา "ไม่ออก" (หรือไม่มีตัวจับเวลาเลย เช่น Shadow Gear)
    #     -> ต้องแจ้ง (fail-open) เพราะจาก log จริงพบว่า OCR อ่านเวลาบนการ์ดไม่ออกบ่อยมาก
    #     (เกินครึ่ง) กติกาเดิมที่ตัดทิ้งทุกกรณีที่อ่านเวลาไม่ออก (fail-closed) ทำให้ของใหม่
    #     จริงๆ หลุดการแจ้งเตือนไปเงียบๆ (บั๊กที่ผู้ใช้เจอ: ของใหม่เข้าเงื่อนไขสีส้ม 2 อันแต่
    #     ไม่มีแจ้งเตือน) ยอมเสี่ยงแจ้งซ้ำเป็นครั้งคราว (ถ้าคีย์ของชิ้นเดิมเคยอ่านเพี้ยน) ดีกว่า
    #     พลาดของใหม่
    #   - คีย์เคยเจอแล้ว -> ถือว่าแจ้งไปแล้ว จะแจ้งซ้ำก็ต่อเมื่อเวลารีเซ็ตขึ้นมาใหม่ชัดเจน
    #     (relist: อ่านเวลาได้ > 55 นาที และมากกว่าเวลาที่บันทึกไว้รอบก่อน) - ไอเท็มที่ไม่มี
    #     ตัวจับเวลาเลย (cur_sec เป็น None เสมอ) จะไม่ถูกแจ้งซ้ำอีกตราบใดที่คีย์ (ชื่อ+ราคา)
    #     ยังเหมือนเดิม ต้องรอให้คีย์เปลี่ยน (เช่นราคาเปลี่ยน) ถึงจะนับเป็นของใหม่อีกครั้ง
    NEW_ITEM_MIN_TIMER_SEC = 55 * 60
    new_items = []
    for k, it in current_by_key.items():
        cur_sec = _timer_to_seconds(it.get("timer"))
        prev = previous.get(k)
        if prev is None:
            # คีย์ใหม่ที่ไม่เคยเจอ: แจ้งเสมอ ยกเว้นอ่านเวลาได้ชัดๆ ว่าเป็นของเก่าค้างขาย
            if cur_sec is None or cur_sec > NEW_ITEM_MIN_TIMER_SEC:
                new_items.append(it)
            continue
        # คีย์เดิมที่เคยเจอแล้ว: เช็คเฉพาะกรณี relist (เวลารีเซ็ตกลับขึ้นไปสูง)
        if cur_sec is None or cur_sec <= NEW_ITEM_MIN_TIMER_SEC:
            continue
        prev_sec = _timer_to_seconds(prev.get("timer"))
        if prev_sec is None or cur_sec > prev_sec:
            new_items.append(it)

    return new_items, is_first_run


def run_once():
    focus_game_window()
    refresh_item_list()

    all_items = []
    if cfg.CATEGORY_TABS:
        for tab in cfg.CATEGORY_TABS:
            select_category(tab)
            all_items.extend(scan_market())
    else:
        all_items = scan_market()

    previous = load_previous_snapshot()

    # ถ้าชื่อไอเท็มเดียวกันมีหลายชิ้นในรอบสแกนเดียวกัน (มีคนขายซ้ำพร้อมกันหลายชิ้น) ให้เก็บ
    # ตัวที่เวลานับถอยหลังเหลือมากที่สุดไว้เป็นตัวแทนของชื่อนั้น (แปลว่าเพิ่งลงขายล่าสุด)
    current_by_key = {}
    for it in all_items:
        k = it["key"]
        if k not in current_by_key:
            current_by_key[k] = it
        else:
            cur_sec = _timer_to_seconds(it["timer"])
            kept_sec = _timer_to_seconds(current_by_key[k]["timer"])
            if (cur_sec or -1) > (kept_sec or -1):
                current_by_key[k] = it

    new_items, is_first_run = determine_new_items(current_by_key, previous)

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n=== สแกนตลาดเมื่อ {now_str} ===")
    print(f"พบไอเท็มทั้งหมด: {len(all_items)} รายการ")
    if is_first_run:
        print("(ยังไม่มีข้อมูลเก่า - ถือเป็นรันครั้งแรก เทียบทุกชิ้นเป็นของใหม่ "
              "แล้วส่งเฉพาะชิ้นที่ตรงกฎแจ้งเตือนเข้า Discord)")

    if new_items:
        print(f"ไอเท็มใหม่ที่เพิ่งลงขาย: {len(new_items)} รายการ")
        for it in new_items:
            level_str = it.get("level") if it.get("level") is not None else "?"
            print(f"  - {it['name']} ({it['item_type']})  Lv.{level_str}  ราคา {it['price']}  "
                  f"ส้ม {it['orange_skill_count']} / ม่วง {it['purple_skill_count']}")
            for sk in it["special_skills"]:
                print(f"      {sk['title']}: {sk['description']}")
    else:
        print("ไม่มีไอเท็มใหม่ตั้งแต่รอบที่แล้ว")

    # จับคู่ไอเท็มใหม่แต่ละชิ้นกับกฎแจ้งเตือนใน cfg.NOTIFY_RULES (เลเวล + จำนวนสีส้ม/ม่วง
    # ต้องตรง) แล้วเก็บ webhook ปลายทางไว้กับตัวไอเท็ม - ชิ้นที่ไม่ตรงกฎไหนเลยจะไม่ถูกส่ง
    # แต่ยังถูกบันทึกลง market_log.csv / snapshot ตามปกติ
    notify_items = []
    for it in new_items:
        hooks = matching_webhooks(it)
        if hooks:
            it["notify_webhooks"] = hooks
            notify_items.append(it)
    if new_items:
        print(f"ไอเท็มที่ตรงกฎแจ้งเตือน (เลเวล+สี): {len(notify_items)} รายการ")

    if notify_items:
        # แจ้งไว้ล่วงหน้าเพราะขั้นตอนนี้ใช้เวลานาน (ส่งทีละชิ้น + หน่วงเวลากันโดน
        # rate-limit) ถ้ากด Ctrl+C ระหว่างนี้ โปรแกรมจะหยุดกลางคันทันที (KeyboardInterrupt
        # ไม่ถูกจับโดย try/except Exception ด้านล่าง) ทำให้ทั้งไอเท็มที่เหลือไม่ถูกส่งเข้า
        # Discord และขั้นตอนบันทึก market_log.csv / market_last_snapshot.json ที่อยู่ถัดไป
        # ก็จะไม่ได้รันเลยด้วย (นี่คือสาเหตุที่พบบ่อยที่สุดที่ "ส่งไม่ครบ" และ "ไฟล์ไม่อัปเดต")
        est_sec = len(notify_items) * (cfg.DISCORD_SEND_DELAY_SEC + 0.5)
        print(f"[discord] กำลังส่งแจ้งเตือน {len(notify_items)} รายการ (ใช้เวลาประมาณ {est_sec:.0f} วินาที) "
              f"กรุณาอย่ากด Ctrl+C ระหว่างนี้...")

    try:
        notify_discord(notify_items)
        if notify_items:
            print("[discord] ส่งแจ้งเตือนครบทุกรายการแล้ว")
    except Exception:
        print("[error] notify_discord() พังระหว่างทำงาน (ไม่ควรเกิดแล้วเพราะห่อ try/except ไว้ในนั้นแล้ว "
              "แต่กันเผื่ออีกชั้น) - รายละเอียด:")
        traceback.print_exc()

    try:
        append_csv(all_items)
    except Exception:
        print(f"[error] เขียน {cfg.OUTPUT_CSV} ไม่สำเร็จ (เช่น ไฟล์ถูกเปิดค้างอยู่ใน Excel ทำให้เขียนไม่ได้) "
              "- รายละเอียด:")
        traceback.print_exc()

    try:
        save_snapshot(current_by_key)
    except Exception:
        print(f"[error] บันทึก {cfg.STATE_JSON} ไม่สำเร็จ - รายละเอียด:")
        traceback.print_exc()

    # สแกนแท็บ Shadow Gear แยกต่างหาก "หลังจาก" All Items เสร็จครบทุกหน้าแล้วเท่านั้น
    # (ผู้ใช้ต้องการแบบนี้ - ของหมวด Shadow Gear ไม่ใช้กฎแจ้งเตือนของ All Items เลย ใช้ webhook
    # และเงื่อนไข "เจอของใหม่ = แจ้ง" แยกเป็นของตัวเอง) ห่อด้วย try/except ทั้งก้อนเพราะเป็น
    # ขั้นตอนเสริม ถ้าพังไม่ควรทำให้ผลลัพธ์ All Items ที่บันทึกไปแล้วข้างบนเสียหายไปด้วย
    try:
        shadow_items, shadow_list_image = scan_shadow_gear_tab()
        shadow_previous = load_previous_snapshot(cfg.SHADOW_GEAR_STATE_JSON)
        shadow_current_by_key = {it["key"]: it for it in shadow_items}
        shadow_new_items, shadow_is_first_run = determine_new_items(shadow_current_by_key, shadow_previous)

        print(f"[Shadow Gear] พบไอเท็มทั้งหมด: {len(shadow_items)} รายการ")
        if shadow_is_first_run:
            print("[Shadow Gear] (ยังไม่มีข้อมูลเก่า - ถือเป็นรันครั้งแรก แจ้งทุกชิ้นที่เจอ)")
        if shadow_new_items:
            print(f"[Shadow Gear] ไอเท็มใหม่: {len(shadow_new_items)} รายการ")
            for it in shadow_new_items:
                print(f"  - {it['name']}  ราคา {it['price']}")
            notify_shadow_gear(shadow_new_items, shadow_list_image)
        else:
            print("[Shadow Gear] ไม่มีไอเท็มใหม่ตั้งแต่รอบที่แล้ว")

        save_snapshot(shadow_current_by_key, cfg.SHADOW_GEAR_STATE_JSON)
    except Exception:
        print("[error] สแกนแท็บ Shadow Gear พังระหว่างทำงาน - รายละเอียด:")
        traceback.print_exc()

    print("บันทึกไฟล์เสร็จสมบูรณ์แล้ว - ปลอดภัยที่จะกด Ctrl+C หยุดสคริปต์ตอนนี้ได้ (ถ้าต้องการ)")


def check_page_mode():
    """โหมดคาลิเบรตจุดตรวจหน้าถัดไป: python market_tracker.py checkpage

    ไม่สแกน ไม่คลิกอะไรทั้งนั้น แค่แคปภาพจุดที่ใช้ตัดสินแล้วรายงานค่าที่วัดได้ + เซฟภาพ
    ทับไฟล์ชื่อเดิม เอาไว้เทียบค่าระหว่าง "อยู่หน้า 1" กับ "อยู่หน้าสุดท้าย" เพื่อหาเกณฑ์
    NEXT_PAGE_ENABLED_MIN_BRIGHTNESS ที่เหมาะกับเกม/จอของเครื่องนี้"""
    print("=== ตรวจจุดที่ใช้ตัดสิน 'มีหน้าถัดไปไหม' ===")
    print("เปิดหน้า Trade House ค้างไว้ให้เห็นแถบเปลี่ยนหน้าด้านล่าง แล้วรอ 3 วินาที...\n")
    time.sleep(3)

    page_text = ocr_region(
        cfg.PAGE_INDICATOR_REGION, tag="page_indicator", psm=7,
        fixed_debug_name="page_indicator",
    )
    m = PAGE_NUM_RE.search(page_text)
    print(f"กรอบเลขหน้า PAGE_INDICATOR_REGION = {cfg.PAGE_INDICATOR_REGION}")
    print(f"  OCR อ่านได้: {page_text!r}")
    if m:
        print(f"  -> แยกเป็นหน้า {m.group(1)}/{m.group(2)} (ใช้ได้ ชั้น 1 ทำงานปกติ)")
    else:
        print("  -> แยกเลขหน้าไม่ได้ ให้เปิด debug_page_indicator.png เช็คว่ากรอบครอบตรงเลขไหม")
        print("     และ debug_page_indicator_ocr.png (สิ่งที่ Tesseract เห็นจริง) ว่าตาคนยังอ่านออกไหม")

    next_pos = getattr(cfg, "NEXT_PAGE_CHECK_POS", None) or cfg.NEXT_PAGE_POS
    prev_pos = getattr(cfg, "PREV_PAGE_CHECK_POS", None)
    rad = getattr(cfg, "NEXT_PAGE_CHECK_RADIUS", 16)
    next_bright = _arrow_brightness(next_pos, debug_name="next_page_check")
    prev_bright = _arrow_brightness(prev_pos, debug_name="prev_page_check") if prev_pos else None

    print(f"\nปุ่มลูกศรขวา NEXT_PAGE_CHECK_POS = {next_pos} (กรอบ {rad * 2}x{rad * 2} px)")
    print(f"  ความสว่างเฉลี่ย: {next_bright:.1f}" if next_bright is not None else "  วัดไม่ได้")
    if prev_bright is not None:
        print(f"ปุ่มลูกศรซ้าย PREV_PAGE_CHECK_POS = {prev_pos}")
        print(f"  ความสว่างเฉลี่ย: {prev_bright:.1f}")
        gap = abs(next_bright - prev_bright) if next_bright is not None else 0
        print(f"  ต่างกัน: {gap:.1f}")
        if gap < 15:
            print("  -> ต่างกันน้อยมาก เกมนี้อาจไม่ได้ทำปุ่มที่กดไม่ได้ให้จางลง")
            print("     ปล่อย NEXT_PAGE_ENABLED_MIN_BRIGHTNESS = None ไว้ได้เลย (ชั้น 3 รับมือแทน)")
        else:
            print(f"  -> ต่างกันชัดเจน ลองตั้ง NEXT_PAGE_ENABLED_MIN_BRIGHTNESS "
                  f"= {(next_bright + prev_bright) / 2:.0f} (ค่ากึ่งกลาง)")

    threshold = getattr(cfg, "NEXT_PAGE_ENABLED_MIN_BRIGHTNESS", None)
    print(f"\nเกณฑ์ที่ตั้งไว้ตอนนี้: NEXT_PAGE_ENABLED_MIN_BRIGHTNESS = {threshold}")
    has_next, reason = check_next_page()
    print(f"สรุปตอนนี้: {'ยังมีหน้าถัดไป' if has_next else 'หน้าสุดท้ายแล้ว'} ({reason})")

    print("\nภาพที่เซฟไว้ (ทับไฟล์เดิมทุกครั้งที่รัน):")
    for name in ("debug_page_indicator.png", "debug_page_indicator_ocr.png",
                 "debug_next_page_check.png", "debug_prev_page_check.png"):
        if os.path.exists(name):
            print(f"  {os.path.abspath(name)}")
    print("\nวิธีคาลิเบรต: รันคำสั่งนี้ตอนอยู่ 'หน้า 1' หนึ่งครั้ง แล้วกดไปหน้าสุดท้ายด้วยมือ")
    print("รันอีกครั้ง เอาค่าความสว่างลูกศรขวาสองครั้งมาเทียบกัน แล้วตั้งเกณฑ์ไว้ตรงกลาง")


def main():
    print("เริ่ม market tracker - กด Ctrl+C เพื่อหยุด")
    print(f"จะสแกนทุก {cfg.POLL_INTERVAL_SEC // 60} นาที\n")
    try:
        while True:
            start = time.time()
            try:
                run_once()
            except Exception as e:
                print(f"[error] สแกนรอบนี้ล้มเหลว: {e}")
                traceback.print_exc()
            elapsed = time.time() - start
            time.sleep(max(0, cfg.POLL_INTERVAL_SEC - elapsed))
    except KeyboardInterrupt:
        print("\nหยุดการทำงานแล้ว")


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "checkpage":
            check_page_mode()
        else:
            main()
    except SystemExit:
        raise
    except Exception:
        # ตอนรันเป็น exe ด้วยการดับเบิลคลิก ถ้า crash ตอนเริ่มโปรแกรม (เช่น หา Tesseract
        # ไม่เจอ) หน้าต่าง console จะปิดตัวทันทีจนอ่าน error ไม่ทัน - ค้างหน้าต่างไว้ให้อ่านก่อน
        traceback.print_exc()
        input("\nโปรแกรมหยุดทำงานเพราะเกิดข้อผิดพลาดข้างต้น - กด Enter เพื่อปิดหน้าต่างนี้...")
