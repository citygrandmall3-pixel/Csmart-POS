from pathlib import Path
import shutil

p = Path("templates/delivery.html")
s = p.read_text(encoding="utf-8")

old = 'invoiceInfo.textContent = "رقم الفاتورة: " + data.invoice_no + " — الحالة: " + data.status;'

new = '''const statusLabels = {
            draft: "مسودة",
            approved: "معتمدة",
            held: "معلّقة",
            cancelled: "ملغاة",
            returned: "مرتجعة"
        };
        const statusText = statusLabels[data.status] || "غير معروفة";
        invoiceInfo.textContent = "رقم الفاتورة: " + data.invoice_no + " — الحالة: " + statusText;'''

if old not in s:
    raise SystemExit("لم يتم العثور على سطر الحالة المتوقع؛ لم يتغير الملف.")

shutil.copy2(p, "templates/delivery.html.before_status_ar")
p.write_text(s.replace(old, new, 1), encoding="utf-8")
print("تم تعديل الحالة إلى العربية، وأُنشئت نسخة احتياطية.")