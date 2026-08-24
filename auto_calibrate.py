"""
=====================================================================
auto_calibrate.py
=====================================================================
ผมควบคุมคอมพิวเตอร์หรือจอเกมของคุณโดยตรงไม่ได้ (ทำงานอยู่ในเครื่อง sandbox
แยกต่างหาก ไม่เห็นหน้าจอคุณ) แต่ไฟล์นี้ช่วยให้ขั้นตอนคาลิเบรตส่วนที่ยุ่งยากที่สุด
(หากรอบการ์ดในกริด + ระยะห่างระหว่างการ์ด) ทำอัตโนมัติแทนการเล็งพิกัดด้วยมือ
ทีละจุดผ่าน calibrate.py

หลักการ: การ์ดไอเท็มแต่ละใบมีพื้นหลังสีขาว/ครีมสว่าง ตัดกับพื้นหลังของเกม
สคริปต์จะหาก้อนสี่เหลี่ยมสว่างที่มีขนาดใกล้เคียงกันมากที่สุด (ตรงกับ 8 การ์ด
ในกริด 2 คอลัมน์ x 4 แถว) แล้วคำนวณกรอบ + ระยะห่างให้ ผลลัพธ์เป็น "ค่าที่แนะนำ"
ต้องเทียบกับภาพ auto_calibrate_debug.png ก่อนเชื่อ 100%

ใช้งาน:
  1. เปิดเกม ไปที่หน้า Trade House ค้างไว้ (แสดงกริดไอเท็มให้เห็นเต็มๆ)
  2. รัน: python auto_calibrate.py
  3. เช็คตัวเลขที่ print ออกมา + เปิดดู auto_calibrate_debug.png ว่ากรอบสีแดง
     ครอบการ์ดแต่ละใบพอดีไหม ถ้าใช่ ก็คัดลอกค่าไปใส่ market_config.py ได้เลย
     ถ้าเพี้ยน ให้ใช้ calibrate.py มือแทน (แม่นกว่าแต่ช้ากว่า)

สิ่งที่สคริปต์นี้ "หาให้ไม่ได้" เพราะมันไม่รู้ล่วงหน้าว่าปุ่มไหนคือปุ่มอะไร:
  - MARKET_BUTTON_POS (ปุ่มเปิด Trade House จากเมนูหลัก - ไม่ได้อยู่ในหน้านี้)
  - NEXT_PAGE_POS / PREV_PAGE_POS (ลูกศรเปลี่ยนหน้า - ไอคอนเล็กเกินจะเดา)
  - DETAIL_POPUP_REGION (ต้องเปิด popup ก่อนถึงจะรู้ขนาด - มีโหมดที่ 2 ให้ในไฟล์นี้)
  - PAGE_INDICATOR_REGION (เลขหน้าเล็กมาก เสี่ยงเดาผิด)
  จุดเหล่านี้ยังต้องหาด้วย calibrate.py mouse เอง (เร็ว แค่กด Enter ไม่กี่ครั้ง)
=====================================================================
"""

import time
from collections import defaultdict

import cv2
import numpy as np
import pyautogui
from PIL import Image, ImageDraw


def _cluster_1d(values, tol=30):
    """จัดกลุ่มตัวเลขที่ใกล้เคียงกัน (ห่างกันไม่เกิน tol พิกเซล) ให้เป็นกลุ่มเดียวกัน
    คืนค่าเฉลี่ยของแต่ละกลุ่ม ใช้หาตำแหน่งคอลัมน์/แถวที่ซ้ำกันของการ์ดหลายใบ"""
    values = sorted(values)
    groups = []
    for v in values:
        if groups and abs(groups[-1][-1] - v) <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]


def detect_grid(img_bgr, cols=2, rows=4):
    """หาก้อนสี่เหลี่ยมสว่าง (การ์ด) แล้วคำนวณกริด cols x rows

    ไม่จำเป็นต้องเจอการ์ดครบทุกใบ (เผื่อบางใบถูกทูลทิป/ไอคอนบังจนบังกล่องขาวไม่ครบ
    เช่น เอาเมาส์ไปชี้ค้างไว้ตอนแคปหน้าจอ) แค่เจอพอที่จะสร้างแนวคอลัมน์ครบ `cols`
    แนวและแนวแถวครบ `rows` แนว ก็คำนวณตำแหน่งการ์ดที่เหลือด้วยสูตรกริดต่อได้เลย

    คืนค่า (grid, candidates) โดย grid เป็น list ของ (x, y, w, h) เรียงจากซ้ายบน
    ไปขวาล่าง (แถวแล้วค่อยคอลัมน์) หรือ None ถ้าหาไม่ได้"""
    h_img, w_img = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 225, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < 0.01 * w_img * h_img:
            continue  # เล็กเกินไป ไม่ใช่การ์ด
        aspect = w / h if h else 0
        if 1.8 <= aspect <= 3.6:  # การ์ดไอเท็มที่ไม่ถูกบังมักกว้างกว่าสูงประมาณนี้
            candidates.append((x, y, w, h))

    if not candidates:
        return None, candidates

    # จัดกลุ่มตามขนาด (w, h) ที่ใกล้เคียงกัน หากลุ่มที่มีสมาชิกเยอะที่สุด = ขนาดการ์ดจริง
    buckets = defaultdict(list)
    for b in candidates:
        key = (round(b[2] / 15), round(b[3] / 15))
        buckets[key].append(b)
    best_key = max(buckets, key=lambda k: len(buckets[k]))
    modal_w = sum(b[2] for b in buckets[best_key]) / len(buckets[best_key])
    modal_h = sum(b[3] for b in buckets[best_key]) / len(buckets[best_key])

    matched = [
        b for b in candidates
        if abs(b[2] - modal_w) <= 0.15 * modal_w and abs(b[3] - modal_h) <= 0.2 * modal_h
    ]

    # หาแนวคอลัมน์ (x) และแนวแถว (y) จากการ์ดที่เจอ ไม่จำเป็นต้องเจอครบทุกใบ
    col_xs = sorted(_cluster_1d([b[0] for b in matched]))
    row_ys = sorted(_cluster_1d([b[1] for b in matched]))

    if len(col_xs) != cols or len(row_ys) != rows:
        return None, matched

    pitch_x = col_xs[1] - col_xs[0]
    pitches_y = [row_ys[i + 1] - row_ys[i] for i in range(len(row_ys) - 1)]
    pitch_y = sum(pitches_y) / len(pitches_y)

    grid = []
    for ry in row_ys:
        for cx in col_xs:
            grid.append((int(round(cx)), int(round(ry)), int(round(modal_w)), int(round(modal_h))))
    return grid, candidates


def step_grid():
    print("=== ขั้นที่ 1: หากริดการ์ดไอเท็ม ===")
    print("จะแคปหน้าจอใน 3 วินาที... สลับไปโฟกัสหน้าเกม Trade House ให้เรียบร้อย")
    print("เอาเมาส์ออกจากบริเวณการ์ดไอเท็มก่อน (วางไว้มุมจอเฉยๆ) ไม่งั้นทูลทิป/ป๊อปอัป")
    print("ที่ขึ้นตอนเมาส์ชี้ค้างจะไปบังกล่องขาวของการ์ด ทำให้หากริดพลาดได้\n")
    time.sleep(3)

    screenshot = pyautogui.screenshot()
    img_bgr = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    grid, candidates = detect_grid(img_bgr)

    debug_img = screenshot.copy()
    draw = ImageDraw.Draw(debug_img)
    for (x, y, w, h) in candidates:
        draw.rectangle([x, y, x + w, y + h], outline="yellow", width=2)

    if grid is None:
        for (x, y, w, h) in candidates:
            draw.rectangle([x, y, x + w, y + h], outline="yellow", width=2)
        debug_img.save("auto_calibrate_debug.png")
        print("หากริด 2x4 การ์ดไม่เจอ (เจอกล่องสว่างที่เข้าเกณฑ์แค่บางส่วน)")
        print("ดู auto_calibrate_debug.png (กรอบเหลือง = ผู้สมัครที่เจอ) แล้วปรับ")
        print("เงื่อนไขใน detect_grid() หรือใช้ calibrate.py มือแทนก็ได้\n")
        return None

    x0, y0, w0, h0 = grid[0]
    x1, y1, w1, h1 = grid[1]
    x2, y2, w2, h2 = grid[2]
    pitch_x = x1 - x0
    pitch_y = y2 - y0

    for (x, y, w, h) in grid:
        draw.rectangle([x, y, x + w, y + h], outline="red", width=3)
    debug_img.save("auto_calibrate_debug.png")

    print("เจอกริดการ์ดครบ 8 ใบ! ค่าที่แนะนำ (เช็คกับ auto_calibrate_debug.png ก่อนใช้จริง):\n")
    print(f"FIRST_CARD_TEXT_REGION = ({x0}, {y0}, {x0 + w0}, {y0 + h0})")
    print(f"CARD_PITCH_X = {pitch_x}")
    print(f"CARD_PITCH_Y = {pitch_y}")
    icon_x = x0 + int(w0 * 0.12)
    icon_y = y0 + int(h0 * 0.45)
    print(f"# จุดคลิกไอคอนไอเท็ม เป็นการเดาคร่าวๆ ต้องทดสอบว่าคลิกแล้ว popup เปิดจริง")
    print(f"FIRST_CARD_ICON_CLICK_POS = ({icon_x}, {icon_y})  # ลองก่อน ไม่ถูกให้ปรับด้วย calibrate.py mouse\n")

    return {
        "text_region": (x0, y0, x0 + w0, y0 + h0),
        "pitch_x": pitch_x,
        "pitch_y": pitch_y,
        "icon_pos": (icon_x, icon_y),
    }


def step_popup(icon_pos):
    """หากรอบ popup รายละเอียด โดยเทียบภาพ 'ก่อน' กับ 'หลัง' คลิกไอคอน (diff-based)
    แทนการหาก้อนสีขาวที่ใหญ่ที่สุด เพราะพาเนล 'ซื้อ' (Description/Qty/Total) ทางขวา
    ก็เป็นก้อนสีขาวขนาดใหญ่เหมือนกันและอยู่บนจออยู่แล้วตั้งแต่ก่อนคลิก ถ้าใช้วิธีเดิม
    (หาก้อนขาวใหญ่สุด) จะไปหยิบพาเนลซื้อผิดตัวแทนที่จะเป็น popup special skill ที่ต้องการ
    วิธีนี้จะดูว่า "อะไรเปลี่ยนไปหลังคลิก" ซึ่งก็คือ popup ที่เพิ่งเปิดขึ้นมานั่นเอง"""
    print("=== ขั้นที่ 2: หากรอบ popup รายละเอียด (ไม่บังคับ) ===")
    ans = input("อยากให้ลองคลิกไอคอนไอเท็ม (จากขั้นที่ 1) แล้วเดากรอบ popup ให้ด้วยไหม? (y/n): ").strip().lower()
    if ans != "y":
        return

    print("แคปภาพ 'ก่อนคลิก' ไว้เทียบก่อน...")
    before = pyautogui.screenshot()
    before_gray = cv2.cvtColor(np.array(before), cv2.COLOR_RGB2GRAY)

    print(f"จะคลิกที่ {icon_pos} ใน 2 วินาที ระวังอย่าขยับเมาส์ระหว่างนี้...")
    time.sleep(2)
    pyautogui.click(*icon_pos)
    time.sleep(1.0)

    after = pyautogui.screenshot()
    after_gray = cv2.cvtColor(np.array(after), cv2.COLOR_RGB2GRAY)

    h_img, w_img = after_gray.shape[:2]

    diff = cv2.absdiff(before_gray, after_gray)
    _, diff_mask = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
    kernel = np.ones((9, 9), np.uint8)
    diff_mask = cv2.dilate(diff_mask, kernel, iterations=1)

    contours, _ = cv2.findContours(diff_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [cv2.boundingRect(c) for c in contours]
    boxes = [b for b in boxes if b[2] * b[3] > 0.01 * w_img * h_img]  # ตัดจุดเล็กๆ (ตัวเลขนาฬิกานับถอยหลังขยับ)

    debug_img = after.copy()
    draw = ImageDraw.Draw(debug_img)
    for (x, y, w, h) in boxes:
        draw.rectangle([x, y, x + w, y + h], outline="yellow", width=2)

    if not boxes:
        print("ไม่เจอความเปลี่ยนแปลงหลังคลิก (popup อาจไม่เปิด หรือคลิกไม่โดนไอคอน)")
        print("ลองคาลิเบรตกรอบ popup ด้วยมือแทน: python calibrate.py mouse -> พิมพ์ r\n")
        debug_img.save("auto_calibrate_popup_debug.png")
        return

    x, y, w, h = max(boxes, key=lambda b: b[2] * b[3])

    if w * h > 0.7 * w_img * h_img:
        draw.rectangle([x, y, x + w, y + h], outline="red", width=3)
        debug_img.save("auto_calibrate_popup_debug.png")
        print("กรอบที่เจอกว้างเกิน 70% ของจอ น่าจะมีอย่างอื่นเปลี่ยนพร้อมกันด้วย (เช่น")
        print("ป้ายประกาศเลื่อน/นาฬิกาหลายอันขยับพร้อมกัน) ไม่น่าเชื่อถือ")
        print("ดู auto_calibrate_popup_debug.png (กรอบเหลือง = จุดที่เปลี่ยนทั้งหมด) แล้วเลือก")
        print("กรอบที่ใช่เอง หรือคาลิเบรตด้วยมือแทน: python calibrate.py mouse -> พิมพ์ r\n")
        return

    draw.rectangle([x, y, x + w, y + h], outline="red", width=3)
    debug_img.save("auto_calibrate_popup_debug.png")

    print(f"\nDETAIL_POPUP_REGION = ({x}, {y}, {x + w}, {y + h})")
    print("เช็คกับ auto_calibrate_popup_debug.png ว่ากรอบสีแดงครอบ popup ทั้งใบพอดีไหม")
    print("(กรอบเหลือง = จุดอื่นๆ ที่เปลี่ยนไปด้วย เช่น นาฬิกานับถอยหลัง เพิกเฉยได้)")
    print("อย่าลืมปิด popup เองด้วยมือหลังจากนี้ (กด Esc หรือปุ่มปิด)\n")


if __name__ == "__main__":
    result = step_grid()
    if result:
        step_popup(result["icon_pos"])
