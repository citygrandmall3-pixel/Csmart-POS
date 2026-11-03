from pathlib import Path
import shutil

p = Path("templates/delivery.html")
s = p.read_text()

marker = '<div id="invoiceInfo"'
start = s.find(marker)
if start == -1:
    raise SystemExit("لم يتم العثور على خانة رقم الفاتورة")

end = s.find("</div>", start) + len("</div>")
if end < len("</div>"):
    raise SystemExit("تعذر تحديد موضع خانة الفاتورة")

if 'id="deliveryItems"' in s:
    raise SystemExit("جدول الأصناف موجود بالفعل")

addition = '''
<div id="deliveryItems" style="margin:16px 0;overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;background:#fff;">
<thead><tr style="background:#eef5ff;">
<th style="padding:8px;border:1px solid #ddd;">الصنف</th>
<th style="padding:8px;border:1px solid #ddd;">الكمية</th>
<th style="padding:8px;border:1px solid #ddd;">السعر</th>
<th style="padding:8px;border:1px solid #ddd;">الإجمالي</th>
</tr></thead>
<tbody id="deliveryItemsBody">
<tr><td colspan="4" style="padding:10px;text-align:center;">جارٍ تحميل الأصناف...</td></tr>
</tbody></table>
</div>
'''

shutil.copy2(p, "templates/delivery.html.before_items_display")
s = s[:end] + addition + s[end:]

script_marker = 'const invoiceInfo = document.getElementById("invoiceInfo");'
if script_marker not in s:
    raise SystemExit("لم يتم العثور على كود رقم الفاتورة")

load_code = '''
async function loadDeliveryInvoice() {
    const body = document.getElementById("deliveryItemsBody");
    if (!deliverySaleId || !/^\\d+$/.test(deliverySaleId)) {
        body.innerHTML = '<tr><td colspan="4" style="padding:10px;text-align:center;">لا توجد فاتورة محددة</td></tr>';
        return;
    }
    try {
        const response = await fetch("/api/sales/" + encodeURIComponent(deliverySaleId) + "/items");
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || "تعذر تحميل الفاتورة");
        invoiceInfo.textContent = "رقم الفاتورة: " + data.invoice_no + " — الحالة: " + data.status;
        if (!data.items.length) {
            body.innerHTML = '<tr><td colspan="4" style="padding:10px;text-align:center;">لا توجد أصناف في الفاتورة</td></tr>';
            return;
        }
        body.innerHTML = "";
        data.items.forEach(function(item) {
            const tr = document.createElement("tr");
            [item.product_name, item.quantity, Number(item.unit_price).toFixed(2), Number(item.line_total).toFixed(2)].forEach(function(value) {
                const td = document.createElement("td");
                td.textContent = value;
                td.style.cssText = "padding:8px;border:1px solid #ddd;text-align:center;";
                tr.appendChild(td);
            });
            body.appendChild(tr);
        });
    } catch (error) {
        body.innerHTML = '<tr><td colspan="4" style="padding:10px;text-align:center;color:#b00;">تعذر تحميل بيانات الفاتورة</td></tr>';
        console.error("تعذر تحميل فاتورة الدليفري", error);
    }
}
loadDeliveryInvoice();
'''

s = s.replace(script_marker, script_marker + "\n" + load_code, 1)
p.write_text(s)
print("تمت إضافة جدول أصناف الفاتورة مع نسخة احتياطية")