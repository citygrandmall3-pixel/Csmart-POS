from pathlib import Path
import shutil

p = Path("templates/delivery.html")
s = p.read_text()

marker = '<h2>إنشاء طلب توصيل</h2>'
addition = marker + '<div id="invoiceInfo" style="background:#eef5ff;padding:12px;border-radius:6px;margin-bottom:18px;text-align:center;font-weight:bold;">رقم الفاتورة: جارٍ التحميل...</div>'

if s.count(marker) != 1:
    raise SystemExit("توقف: لم يتم العثور على عنوان الصفحة مرة واحدة")

if 'id="invoiceInfo"' in s:
    raise SystemExit("خانة رقم الفاتورة موجودة بالفعل")

shutil.copy2(p, "templates/delivery.html.before_invoice_display")
s = s.replace(marker, addition, 1)

script_marker = '<script>'
script_addition = script_marker + '\nconst deliverySaleId = new URLSearchParams(window.location.search).get("sale_id");\nconst invoiceInfo = document.getElementById("invoiceInfo");\nif (deliverySaleId && /^\\d+$/.test(deliverySaleId)) {\n    invoiceInfo.textContent = "رقم الفاتورة: " + deliverySaleId;\n} else {\n    invoiceInfo.textContent = "لم يتم تحديد فاتورة";\n    invoiceInfo.style.background = "#fff0f0";\n}\n'

if s.count(script_marker) != 1:
    raise SystemExit("توقف: موضع JavaScript غير مطابق")

p.write_text(s.replace(script_marker, script_addition, 1))
print("تمت إضافة عرض رقم الفاتورة مع نسخة احتياطية")