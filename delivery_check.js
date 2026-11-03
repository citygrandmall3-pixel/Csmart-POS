
const deliverySaleId = new URLSearchParams(window.location.search).get("sale_id");
const invoiceInfo = document.getElementById("invoiceInfo");

async function loadDeliveryInvoice() {
    const body = document.getElementById("deliveryItemsBody");
    if (!deliverySaleId || !/^\d+$/.test(deliverySaleId)) {
        body.innerHTML = '<tr><td colspan="4" style="padding:10px;text-align:center;">لا توجد فاتورة محددة</td></tr>';
        return;
    }
    try {
        const response = await fetch("/api/sales/" + encodeURIComponent(deliverySaleId) + "/items");
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || "تعذر تحميل الفاتورة");
        const statusLabels = {
            draft: "مسودة",
            approved: "معتمدة",
            held: "معلّقة",
            cancelled: "ملغاة",
            returned: "مرتجعة"
        };
        const statusText = statusLabels[data.status] || "غير معروفة";
        invoiceInfo.textContent = "رقم الفاتورة: " + data.invoice_no + " — الحالة: " + statusText;
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

if (deliverySaleId && /^\d+$/.test(deliverySaleId)) {
    invoiceInfo.textContent = "رقم الفاتورة: " + deliverySaleId;
} else {
    invoiceInfo.textContent = "لم يتم تحديد فاتورة";
    invoiceInfo.style.background = "#fff0f0";
}
const deliveryKey = "delivery_data_" + deliverySaleId;try{const d=JSON.parse((sessionStorage.getItem(deliveryKey) || localStorage.getItem(deliveryKey))||"null");if(d){document.getElementById("repName").value=d.rep||"";document.getElementById("phone").value=d.phone||"";document.getElementById("address").value=d.addr||"";document.getElementById("fees").value=d.fees??0;}}catch(e){console.warn("تعذر استعادة بيانات الدليفري",e);}function saveDelivery(){const rep=document.getElementById("repName").value.trim();const phone=document.getElementById("phone").value.trim();const addr=document.getElementById("address").value.trim();const fees=Number(document.getElementById("fees").value)||0;if(!rep||!phone||!addr){alert("يرجى ملء جميع الحقول.");return;}localStorage.setItem(deliveryKey,JSON.stringify({rep,phone,addr,fees}));sessionStorage.setItem(deliveryKey,JSON.stringify({rep,phone,addr,fees}));alert("تم حفظ البيانات بنجاح. سيتم فتح شاشة البيع الآن.");window.location.href="/sales";}