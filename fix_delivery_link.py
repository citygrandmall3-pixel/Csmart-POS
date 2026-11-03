from pathlib import Path
import shutil

p = Path("templates/sales.html")
s = p.read_text()
marker = "let currentSaleId = null;"

if s.count(marker) != 1:
    raise SystemExit("توقف: موضع التعديل غير مطابق")

if 'window.location.href = "/delivery?sale_id="' in s:
    raise SystemExit("ربط الدليفري موجود بالفعل")

shutil.copy2(p, "templates/sales.html.before_delivery_handler")
addition = marker + """
document.getElementById("btnOpenDelivery").addEventListener("click", function(e) {
    e.preventDefault();
    if (!currentSaleId) {
        alert("لا توجد فاتورة حالية. أضف صنفًا أولًا.");
        return;
    }
    window.location.href = "/delivery?sale_id=" + encodeURIComponent(currentSaleId);
});
"""
p.write_text(s.replace(marker, addition, 1))
print("تم ربط زر الدليفري بنجاح")