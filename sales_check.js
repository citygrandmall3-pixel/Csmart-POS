function updateInvoiceSummary() {
    const rows = document.querySelectorAll("#itemsTableBody tr");
    let units = 0, count = 0, total = 0;

    rows.forEach(row => {
        const c = row.querySelectorAll("td");
        if (c.length < 7) return;
        const q = parseFloat((c[4].innerText || "").replace(/,/g, ""));
        const t = parseFloat((c[5].innerText || "").replace(/[^0-9.-]/g, ""));
        if (!Number.isFinite(q)) return;
        units += q;
        count++;
        total += Number.isFinite(t) ? t : 0;
    });

    const u = document.getElementById("summaryTotalUnits");
    const n = document.getElementById("summaryTotalItems");
    const p = document.getElementById("summaryTotalPrice");
    if (u) u.innerText = Number(units.toFixed(3)).toString();
    if (n) n.innerText = count;
    if (p) p.innerText = total.toFixed(2) + " ل.س";
}

document.addEventListener("click", function() {
    setTimeout(updateInvoiceSummary, 300);
});
let currentSaleId = null;
document.getElementById("btnOpenDelivery").addEventListener("click", function(e) {
    e.preventDefault();
    if (!currentSaleId) {
        alert("لا توجد فاتورة حالية. أضف صنفًا أولًا.");
        return;
    }
    window.location.href = "/delivery?sale_id=" + encodeURIComponent(currentSaleId);
});


// استعادة رقم الفاتورة من جلسة المتصفح
try {
    const savedId = sessionStorage.getItem('kstore_current_sale_id');
    if (savedId && /^\d+$/.test(savedId)) {
        currentSaleId = Number(savedId);
    }
} catch (e) {
    console.warn('[POS] تعذر استعادة رقم الفاتورة', e);
}
let currentProductCategory = "all";
async function loadProductsForSidebar() {
    const container = document.getElementById('productCatalogList');

    if (!container) {
        console.error('[POS] حاوية المنتجات غير موجودة');
        return;
    }

    if (window.__posSidebarLoading) return;
    window.__posSidebarLoading = true;
    container.textContent = 'جاري تحميل المنتجات...';

    try {
        const res = await fetch('/api/products?limit=50', {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        });

        if (!res.ok) {
            throw new Error('HTTP ' + res.status);
        }

        const data = await res.json();
        const list = Array.isArray(data.items) ? data.items :
                     Array.isArray(data.products) ? data.products : null;

        if (!list) {
            throw new Error('صيغة استجابة المنتجات غير متوقعة');
        }

        const categorySelect=document.getElementById("productCategorySelect"); if(categorySelect){try{const r=await fetch("/api/product-categories",{credentials:"same-origin",headers:{"Accept":"application/json"}});if(!r.ok)throw new Error("HTTP "+r.status);const d=await r.json();const cs=Array.isArray(d.items)?d.items:[];categorySelect.replaceChildren(new Option("الكل","all"));cs.filter(x=>Number(x.is_active)!==0).forEach(x=>categorySelect.add(new Option(x.name,String(x.id))));categorySelect.value=currentProductCategory;categorySelect.style.background=categorySelect.value==="all"?"#f8fafc":"#e3f2fd";categorySelect.style.borderColor=categorySelect.value==="all"?"#b8c7d8":"#3986c8";categorySelect.onchange=()=>{currentProductCategory=categorySelect.value;categorySelect.style.background=categorySelect.value==="all"?"#f8fafc":"#e3f2fd";categorySelect.style.borderColor=categorySelect.value==="all"?"#b8c7d8":"#3986c8";container.querySelectorAll("[data-category-id]").forEach(card=>{card.style.display=currentProductCategory==="all"||card.dataset.categoryId===currentProductCategory?"":"none";});};}catch(e){console.warn("[POS] تعذر تحميل التصنيفات",e);}}if (!list.length) {
            container.textContent = 'لا توجد منتجات.';
            return;
        }

        const fragment = document.createDocumentFragment();
        const cards = [];

        for (const p of list) {
            const card = document.createElement('div');
            card.dataset.categoryId = String(p.category_id || "");

            card.style.cssText =
                'padding:13px;min-height:62px;width:100%;box-sizing:border-box;border:1px solid #d9e2ec;border-right:4px solid #3986c8;box-shadow:0 1px 3px rgba(0,0,0,.08);' +
                'cursor:pointer;background:#fff;margin-bottom:6px;border-radius:7px;';

            const name = document.createElement('strong');
            name.style.cssText = 'color:#26364a;font-size:15px;font-weight:700;line-height:1.5;';
            name.textContent = p.name || 'صنف بدون اسم';

            const priceLabel = document.createElement('span');
            priceLabel.style.cssText =
                'display:block;color:#b71c1c;font-size:14px;font-weight:700;margin-top:7px;';
            priceLabel.textContent = 'جاري تحميل السعر...';

            let price = 0;

            card.append(name, document.createElement('br'), priceLabel);

            card.addEventListener('click', function () { 
                if (typeof addProductToSale !== 'function') {
                    console.error('[POS] دالة إضافة المنتج غير موجودة');
                    return;
                }
                addProductToSale(p.id, price);
            });

            fragment.appendChild(card);
            cards.push({
                card: card,
                product: p,
                priceLabel: priceLabel,
                setPrice: value => { price = value; }
            });
        }

        container.replaceChildren(fragment);
if (categorySelect) { const selected = categorySelect.value || "all"; currentProductCategory = selected; container.querySelectorAll("[data-category-id]").forEach(card => { card.style.display = selected === "all" || card.dataset.categoryId === selected ? "" : "none"; }); }

        console.info('[POS] ظهرت المنتجات:', list.length);

        await Promise.allSettled(cards.map(async item => {
            const p = item.product;

            const params = new URLSearchParams({
                product_id: String(p.id),
                unit_id: String(p.base_unit_id || 2),
                currency_id: '3',
                price_type: 'sale'
            });

            try {
                const response = await fetch(
                    '/api/product-prices/lookup?' + params,
                    { credentials: 'same-origin' }
                );

                if (!response.ok) {
                    throw new Error('HTTP ' + response.status);
                }

                const result = await response.json();

                if (result.found && Number.isFinite(Number(result.price))) {
                    const value = Number(result.price);
                    item.setPrice(value);
                    item.priceLabel.textContent =
                        value.toLocaleString('en-US') + ' ل.س';
                } else {
                    item.priceLabel.textContent = 'السعر غير محدد';
                }
            } catch (error) {
                item.priceLabel.textContent = 'تعذر تحميل السعر';
                console.warn('[POS] تعذر تحميل سعر الصنف', p.id, error);
            }
        }));

        console.info('[POS] انتهى تحميل الأسعار');

    } catch (error) {
        console.error('[POS] فشل تحميل المنتجات:', error);
        container.textContent = 'تعذر تحميل المنتجات: ' + error.message;
    } finally {
        window.__posSidebarLoading = false;
    }
}

// دالة الإضافة (مؤقتة)


// دالة جلب وعرض أصناف الفاتورة
let currentSaleStatus = null;

async function loadSaleItems() {
if (!currentSaleId) return;

try {
    const res = await fetch(`/api/sales/${currentSaleId}/items`);
    const data = await res.json();

    if (!res.ok || !data.ok) {
        throw new Error(data.error || 'تعذر تحميل الأصناف');
    }

    currentSaleStatus = data.status || null;
    const invoiceNoEl = document.getElementById("saleInvoiceNo");
    if (invoiceNoEl) invoiceNoEl.innerText = data.invoice_no || "—";
    const items = data.items || [];
    const discountEl = document.getElementById("summaryInvoiceDiscount");
    const discountTotal = items.reduce((sum, item) => sum + Number(item.discount || 0), 0);
    if (discountEl) discountEl.innerText = discountTotal.toFixed(2) + " ل.س";
    const tbody = document.getElementById('itemsTableBody');

    if (!tbody) return;

    if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center">لا توجد أصناف</td></tr>';
    } else {
        tbody.innerHTML = items.map(item => `
            <tr>
                <td>${item.category_name || '—'}</td>
                <td>${item.product_name || '—'}</td>
                <td>${item.product_code || '—'}</td>
                <td>${Number(item.unit_price || 0).toFixed(2)}</td>
                <td>${Number(item.quantity || 0)}</td>
                <td>${Number(item.line_total || 0).toFixed(2)}</td>
                <td>${currentSaleStatus === 'draft' ? `
                    <button type="button" onclick="editSaleItem(${item.id})">تعديل</button>
                    <button type="button" onclick="deleteSaleItem(${item.id})">حذف</button>
                ` : '—'}</td>
            </tr>
        `).join('');
    }
} catch (error) {
    console.error('[POS] تعذر تحميل الأصناف:', error);
}

updateInvoiceSummary();

}
async function editSaleItem(itemId) {
    if (!currentSaleId || currentSaleStatus !== 'draft') {
        alert('لا يمكن تعديل صنف في فاتورة معتمدة');
        return;
    }

    try {
        const itemsRes = await fetch(`/api/sales/${currentSaleId}/items`);
        const itemsData = await itemsRes.json();
        if (!itemsRes.ok || !itemsData.ok) {
            throw new Error(itemsData.error || 'تعذر تحميل بيانات الصنف');
        }

        const item = (itemsData.items || []).find(x => Number(x.id) === Number(itemId));
        if (!item) {
            alert('الصنف غير موجود في الفاتورة');
            return;
        }

        const quantityText = prompt('أدخل الكمية الجديدة:', String(item.quantity));
        if (quantityText === null) return;
        const quantity = Number(quantityText);

        const priceText = prompt('أدخل سعر الوحدة الجديد:', String(item.unit_price));
        if (priceText === null) return;
        const unit_price = Number(priceText);

        if (!Number.isFinite(quantity) || quantity <= 0 ||
            !Number.isFinite(unit_price) || unit_price < 0) {
            alert('يرجى إدخال كمية صحيحة وسعر غير سالب');
            return;
        }

        const res = await fetch(`/api/sales/${currentSaleId}/items/${itemId}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({quantity, unit_price})
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            throw new Error(data.error || 'فشل تعديل الصنف');
        }

        await loadSaleItems();
        updateInvoiceSummary();
        alert('تم تعديل الصنف بنجاح');
    } catch (error) {
        alert(error.message || 'تعذر تعديل الصنف');
    }
}

async function deleteSaleItem(itemId) {
    if (!currentSaleId || currentSaleStatus !== 'draft') {
        alert('لا يمكن حذف صنف من فاتورة معتمدة');
        return;
    }
    if (!confirm('هل تريد حذف هذا الصنف من الفاتورة؟')) return;

    try {
        const res = await fetch(`/api/sales/${currentSaleId}/items/${itemId}`, {
            method: 'DELETE'
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
            throw new Error(data.error || 'فشل حذف الصنف');
        }

        const checkRes = await fetch(`/api/sales/${currentSaleId}/items`);
        const checkData = await checkRes.json();
        if (!checkRes.ok || !checkData.ok) throw new Error(checkData.error || "تعذر التحقق من أصناف الفاتورة");
        if (!(checkData.items || []).length) {
            const deleteRes = await fetch(`/api/sales/${currentSaleId}`, { method: "DELETE" });
            const deleteData = await deleteRes.json();
            if (!deleteRes.ok || !deleteData.ok) throw new Error(deleteData.error || "تعذر حذف المسودة الفارغة");
            currentSaleId = null;
            currentSaleStatus = null;
            sessionStorage.removeItem("kstore_current_sale_id");
            const invoiceNoEl = document.getElementById("saleInvoiceNo");
            if (invoiceNoEl) invoiceNoEl.innerText = "—";
            const tbody = document.getElementById("itemsTableBody");
            if (tbody) tbody.innerHTML = '<tr><td colspan="7" style="text-align:center">لا توجد أصناف</td></tr>';
            updateInvoiceSummary();
            alert("تم حذف آخر صنف والمسودة الفارغة نهائيًا.");
            return;
        }
        await loadSaleItems();
        updateInvoiceSummary();
        alert("تم حذف الصنف بنجاح");
    } catch (error) {
        alert(error.message || 'تعذر حذف الصنف');
    }
}

async function addProductToSale(productId, price) {
    try {
        if (!currentSaleId) {
            const today = new Date().toISOString().split('T')[0];
            const createRes = await fetch('/api/sales', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ currency_id: 3, invoice_date: today })
            });
            const createData = await createRes.json();
            if (createData.ok) {
                currentSaleId = createData.sale_id;
                try {
                    sessionStorage.setItem(
                        'kstore_current_sale_id',
                        String(currentSaleId)
                    );
                } catch (e) {
                    console.warn('[POS] تعذر حفظ رقم الفاتورة', e);
                }
            } else {
                alert('خطأ في إنشاء الفاتورة: ' + createData.error);
                return;
            }
        }

        const prodRes = await fetch('/api/products?limit=50');
        const prodData = await prodRes.json();
        const list = prodData.items || prodData.products || [];
        const product = list.find(p => p.id === productId);
        const unitId = product ? (product.base_unit_id || 2) : 2;

        const itemsRes = await fetch(`/api/sales/${currentSaleId}/items`);
        const itemsData = await itemsRes.json();
        if (!itemsRes.ok) {
            alert('تعذر قراءة أصناف الفاتورة');
            return;
        }

        const items = itemsData.items || [];
        const invoiceNoEl = document.getElementById("saleInvoiceNo");
        if (invoiceNoEl) invoiceNoEl.innerText = itemsData.invoice_no || "—";
        const match = items.find(item =>
            Number(item.product_id) === Number(productId) &&
            Number(item.unit_id) === Number(unitId) &&
            Math.abs(Number(item.unit_price) - Number(price)) < 0.000001 &&
            Number(item.discount || 0) === 0
        );

        let addRes;
        if (match) {
            addRes = await fetch(
                `/api/sales/${currentSaleId}/items/${match.id}`,
                {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        quantity: Number(match.quantity || 0) + 1
                    })
                }
            );
        } else {
            addRes = await fetch(`/api/sales/${currentSaleId}/items`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    product_id: productId,
                    unit_id: unitId,
                    quantity: 1,
                    unit_price: price,
                    discount: 0,
                    notes: ""
                })
            });
        }

        const addData = await addRes.json();
        if (addData.ok) {
            await loadSaleItems();
            updateInvoiceSummary();
        } else {
            alert('خطأ في إضافة المنتج: ' + (addData.error || 'خطأ غير معروف'));
        }
        
        } catch(e) {
        alert('خطأ في الاتصال: ' + e.message);
    }
}
// استدعاء الدالة عند تحميل الصفحة
document.addEventListener('DOMContentLoaded', async function() {
    if (typeof loadProductsForSidebar === 'function') {
        loadProductsForSidebar();
    }

    if (!currentSaleId) return;

    try {
        const response = await fetch('/api/sales');
        if (!response.ok) throw new Error('تعذر جلب الفواتير');

        const result = await response.json();
        const sales = Array.isArray(result.rows) ? result.rows : [];
        const sale = sales.find(item =>
            Number(item.id) === Number(currentSaleId)
        );

        if (sale && sale.status === 'draft') {
            await loadSaleItems();
        } else {
            currentSaleId = null;
            sessionStorage.removeItem('kstore_current_sale_id');
        }
    } catch (e) {
        console.error('[POS] تعذر استعادة الفاتورة', e);
    }
});
(function () {
  const modal = document.getElementById('paymentModal');
  if (!modal || modal.dataset.ready === '1') return;
  modal.dataset.ready = '1';

  const $ = id => document.getElementById(id);
  const payButton = document.querySelector('.k-btn-pay');
  const totalField = $('pmInvoiceTotal');
  const dueField = $('pmDue');
  const cash = $('pmCash');
  const card = $('pmCard');
  const wallet = $('pmWallet');
  const remaining = $('pmRemaining');
  const discount = $('pmDiscount');
  const discountValue = $('pmDiscountValue');
  const percentRow = $('pmPercentRow');
  const percent = $('pmPercent');
  const windowBox = modal.querySelector('.pm-window');
  const invoiceType = document.getElementById("pmInvoiceType");
  const customerSection = document.getElementById("pmCreditCustomerSection");
  const customerSearch = document.getElementById("pmCreditCustomerSearch");
  const customerResults = document.getElementById("pmCreditCustomerResults");
  const customerSelected = document.getElementById("pmCreditCustomerSelected");
  const addCustomerBtn = document.getElementById("pmAddCustomer");
  let selectedCustomer = null;
  let customerSearchTimer = null;

  let invoiceTotal = 0;
  let minimized = false;
  let maximized = false;


  if (customerSearch) {
    customerSearch.addEventListener("input", function () {
      clearTimeout(customerSearchTimer);
      const q = customerSearch.value.trim();

      if (!q) {
        customerResults.style.display = "none";
        customerResults.innerHTML = "";
        return;
      }

      customerSearchTimer = setTimeout(async function () {
        try {
          const r = await fetch("/api/partners?q=" + encodeURIComponent(q));
          const data = await r.json();
          const partners = (data.partners || []).filter(p =>
            (p.partner_type === "customer" || p.partner_type === "both") &&
            Number(p.is_active) === 1
          );

          customerResults.innerHTML = "";

          if (!partners.length) {
            customerResults.textContent = "لا يوجد عملاء مطابقون";
          } else {
            partners.forEach(function (p) {
              const b = document.createElement("button");
              b.type = "button";
              b.style.cssText = "display:block;width:100%;padding:10px;text-align:right;border:0;border-bottom:1px solid #ddd;background:white";
              b.textContent = p.name + (p.phone ? " - " + p.phone : "");

              b.addEventListener("click", function () {
                selectedCustomer = p;
                customerSearch.value = p.name;
                customerSelected.textContent =
                  "العميل المختار: " + p.name +
                  (p.phone ? " - " + p.phone : "");
                customerResults.style.display = "none";
              });

              customerResults.appendChild(b);
            });
          }

          customerResults.style.display = "block";
        } catch (e) {
          customerResults.textContent = "تعذر البحث عن العملاء";
          customerResults.style.display = "block";
        }
      }, 250);
    });
  }


  if (addCustomerBtn) {
    addCustomerBtn.addEventListener("click", function () {
      const form = document.getElementById("pmNewCustomerForm");
      form.style.display = "block";
      document.getElementById("pmNewCustomerName").focus();
    });
  }

  document.getElementById("pmCancelNewCustomer").addEventListener("click", function () {
    document.getElementById("pmNewCustomerForm").style.display = "none";
    document.getElementById("pmNewCustomerMessage").textContent = "";
  });

  document.getElementById("pmSaveNewCustomer").addEventListener("click", async function () {
    const nameInput = document.getElementById("pmNewCustomerName");
    const phoneInput = document.getElementById("pmNewCustomerPhone");
    const message = document.getElementById("pmNewCustomerMessage");
    const saveButton = this;
    const name = nameInput.value.trim();
    const phone = phoneInput.value.trim();

    if (!name) {
      message.textContent = "أدخل اسم العميل.";
      return;
    }

    saveButton.disabled = true;
    message.textContent = "جارٍ حفظ العميل...";

    try {
      const code = "C" + Date.now().toString().slice(-10);
      const response = await fetch("/api/partners", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: code,
          name: name,
          phone: phone,
          partner_type: "customer",
          is_active: true
        })
      });

      const data = await response.json();

      if (!response.ok || !data.ok) {
        message.textContent = data.error || "تعذر حفظ العميل.";
        return;
      }

      selectedCustomer = {
        id: data.id,
        name: name,
        phone: phone,
        partner_type: "customer",
        is_active: 1
      };

      customerSearch.value = name;
      customerSelected.textContent = "العميل المختار: " + name +
        (phone ? " - " + phone : "");
      customerResults.style.display = "none";
      document.getElementById("pmNewCustomerForm").style.display = "none";
      nameInput.value = "";
      phoneInput.value = "";
      message.textContent = "";
      alert("تم إنشاء العميل واختياره للفاتورة.");
    } catch (error) {
      message.textContent = "تعذر الاتصال بالخادم.";
    } finally {
      saveButton.disabled = false;
    }
  });

  function numberValue(id) {
    const value = parseFloat($(id).value);
    return Number.isFinite(value) && value > 0 ? value : 0;
  }

  function readInvoiceTotal() {
    const field = $('summaryTotalPrice');
    if (!field) return 0;
    const raw = field.textContent.replace(/,/g, '').replace(/[^\d.-]/g, '');
    const value = parseFloat(raw);
    return Number.isFinite(value) && value > 0 ? value : 0;
  }

  let cashAutoManaged = true;

  function calculate() {
    let discountAmount = 0;
    const mode = discount.value;

    if (mode === 'percent') {
      discountAmount = invoiceTotal * numberValue('pmPercent') / 100;
    } else if (mode === 'fixed') {
      discountAmount = numberValue('pmDiscountValue');
    } else if (/^\d+$/.test(mode)) {
      discountAmount = invoiceTotal * Number(mode) / 100;
    }

    discountAmount = Math.min(invoiceTotal, Math.max(0, discountAmount));
    const due = Math.max(0, invoiceTotal - discountAmount);
    dueField.value = due.toFixed(2);

    if (cashAutoManaged) {
      const otherPaid = numberValue('pmCard') + numberValue('pmWallet');
      cash.value = Math.max(0, due - otherPaid).toFixed(2);
    }

    const paid = numberValue('pmCash') + numberValue('pmCard') + numberValue('pmWallet');
    remaining.value = (invoiceType.value === 'credit' ? Math.max(0, due - paid) : Math.max(0, paid - due)).toFixed(2);
  }

  function updateDiscountUI() {
    const mode = discount.value;
    percentRow.style.display = mode === 'percent' ? 'grid' : 'none';
    discountValue.style.display = mode === 'fixed' ? 'block' : 'none';
    calculate();
  }


  function updateCreditCustomerUI() {
    const isCredit = invoiceType && invoiceType.value === "credit";
    if (customerSection) {
      customerSection.style.display = isCredit ? "block" : "none";
    }
  }

  if (invoiceType) {
    invoiceType.addEventListener("change", updateCreditCustomerUI);
    updateCreditCustomerUI();
  }

  function openModal() {
    updateInvoiceSummary();
    invoiceTotal = readInvoiceTotal();
    totalField.value = invoiceTotal.toFixed(2);
    dueField.value = invoiceTotal.toFixed(2);
    cashAutoManaged = true;
    cash.value = invoiceTotal.toFixed(2);
    card.value = '0';
    wallet.value = '0';
    discount.value = 'cash';
    discountValue.value = '';
    percent.value = '5';
    $('pmNotes').value = '';
    minimized = false;
    maximized = false;
    windowBox.style.display = '';
    windowBox.style.width = '';
    windowBox.style.maxHeight = '';
    windowBox.style.height = '';
    modal.style.display = 'flex';
    updateDiscountUI();
  }

  function closeModal() {
    modal.style.display = 'none';
    if (payButton) payButton.focus();
  }

  payButton && payButton.addEventListener('click', openModal);
  $('pmCloseTop').addEventListener('click', closeModal);
  $('pmCancel').addEventListener('click', closeModal);

  modal.addEventListener('click', event => {
    if (event.target === modal) closeModal();
  });

  discount.addEventListener('change', updateDiscountUI);
  percent.addEventListener('change', calculate);
  discountValue.addEventListener('input', calculate);
  cash.addEventListener('input', function () {
    cashAutoManaged = false;
    calculate();
  });
  [card, wallet].forEach(field => field.addEventListener('input', calculate));

  $('pmMinimize').addEventListener('click', function () {
    minimized = !minimized;
    const body = modal.querySelector('.pm-body');
    body.style.display = minimized ? 'none' : '';
  });

  $('pmMaximize').addEventListener('click', function () {
    maximized = !maximized;
    if (maximized) {
      windowBox.style.width = 'min(1100px, 98vw)';
      windowBox.style.maxHeight = '98vh';
      windowBox.style.height = 'auto';
    } else {
      windowBox.style.width = '';
      windowBox.style.maxHeight = '94vh';
      windowBox.style.height = '';
    }
  });

  
  let printWindow = null;

  $("pmPrint").addEventListener("click", function () {
    if (!currentSaleId) { alert("لا توجد فاتورة حالية لاعتمادها."); return; }
    const d = JSON.parse((sessionStorage.getItem("delivery_data_" + currentSaleId) || localStorage.getItem("delivery_data_" + currentSaleId)) || "null"); if (!d || !d.rep || !d.phone || !d.addr) { alert("يرجى حفظ بيانات الدليفري كاملة قبل الطباعة."); return; }
    printWindow = window.open("about:blank", "_blank");
    if (!printWindow) { alert("اسمح بالنوافذ المنبثقة ثم حاول مجددًا."); return; }
    printAfterApprove = true;
    try { $("pmConfirm").click(); } catch (e) { printAfterApprove = false; if (printWindow) { printWindow.close(); printWindow = null; } alert("تعذر بدء الاعتماد: " + e.message); }
  });

  let printAfterApprove = false;

  $('pmConfirm').addEventListener('click', async function () {

    const shouldPrint = printAfterApprove;
    printAfterApprove = false;

    if (!currentSaleId) {
      if (shouldPrint && printWindow) { printWindow.close(); printWindow = null; }
      alert('لا توجد فاتورة حالية لاعتمادها.');
      return;
    }

    const mode = discount.value;
    let invoiceDiscount = 0;
    if (mode === 'percent') {
      invoiceDiscount = invoiceTotal * numberValue('pmPercent') / 100;
    } else if (mode === 'fixed') {
      invoiceDiscount = numberValue('pmDiscountValue');
    } else if (/^\\d+$/.test(mode)) {
      invoiceDiscount = invoiceTotal * Number(mode) / 100;
    }
    invoiceDiscount = Math.min(invoiceTotal, Math.max(0, invoiceDiscount));

    const isCredit = invoiceType && invoiceType.value === 'credit';
    if (isCredit && !selectedCustomer) {
      if (shouldPrint && printWindow) { printWindow.close(); printWindow = null; }
      alert('يرجى اختيار العميل للفاتورة الآجلة.');
      return;
    }

    const cashAmount = numberValue('pmCash');
    const cardAmount = numberValue('pmCard');
    const shamAmount = numberValue('pmWallet');
    const due = Math.max(0, invoiceTotal - invoiceDiscount);
    const paid = cashAmount + cardAmount + shamAmount;
    const button = $('pmConfirm');

    if (!isCredit && (due <= 0 || paid + 0.01 < due)) {
      if (shouldPrint && printWindow) { printWindow.close(); printWindow = null; }
      alert('مجموع النقدي وشام كاش والبطاقة يجب أن يساوي الإجمالي بعد الخصم.');
      return;
    }

    if (isCredit) {
      button.disabled = true;
      try {
        const saveResponse = await fetch(`/api/sales/${currentSaleId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            customer_id: selectedCustomer.id,
            payment_method: 'credit'
          })
        });
        const saveData = await saveResponse.json();
        if (!saveResponse.ok || !saveData.ok) {
          if (shouldPrint && printWindow) { printWindow.close(); printWindow = null; }
          alert(saveData.error || 'تعذر حفظ العميل للفاتورة.');
          return;
        }

        const response = await fetch(`/api/sales/${currentSaleId}/approve`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
paid_amount: paid,
delivery_data: JSON.parse((sessionStorage.getItem("delivery_data_" + currentSaleId) || localStorage.getItem("delivery_data_" + currentSaleId)) || "null"),
payment_method: 'credit',
invoice_discount: invoiceDiscount,
payments: {
  cash: cashAmount,
  sham_cash: shamAmount,
  card: cardAmount
}})
        });
        const data = await response.json();
        if (!response.ok || !data.ok) {
          if (shouldPrint && printWindow) { printWindow.close(); printWindow = null; }
          alert(data.error || 'تعذر اعتماد الفاتورة الآجلة.');
          return;
        }

        if (shouldPrint && printWindow) { printWindow.location.href = '/print/sale/' + currentSaleId; } else { alert('تم اعتماد الفاتورة الآجلة بنجاح.'); }
        closeModal();
        currentSaleId = null;
        sessionStorage.removeItem('kstore_current_sale_id');
        if (shouldPrint) { setTimeout(() => location.reload(), 1500); } else { location.reload(); }
      } catch (error) {
        if (shouldPrint && printWindow) { printWindow.close(); printWindow = null; }
        alert('تعذر الاتصال بالخادم. تحقق من حالة الفاتورة قبل إعادة المحاولة.');
      } finally {
        button.disabled = false;
      }
      return;
    }

    const methods = [cashAmount, shamAmount, cardAmount].filter(v => v > 0).length;
    const paymentMethod = methods > 1 ? 'mixed'
      : cardAmount > 0 ? 'bank_misr'
      : shamAmount > 0 ? 'bank_ahly' : 'cash';

    button.disabled = true;
    try {
      const response = await fetch(`/api/sales/${currentSaleId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          paid_amount: paid,
          payment_method: paymentMethod,
          delivery_data: JSON.parse((sessionStorage.getItem("delivery_data_" + currentSaleId) || localStorage.getItem("delivery_data_" + currentSaleId)) || "null"),
          invoice_discount: invoiceDiscount,
          payments: {
            cash: cashAmount,
            sham_cash: shamAmount,
            card: cardAmount
          }
        })
      });

      const data = await response.json();
      if (!response.ok || !data.ok) {
        alert("فشل اعتماد الفاتورة: " + (data.error || ("HTTP " + response.status)));
        return;
      }
      if (shouldPrint && printWindow) { printWindow.location.href = "/print/sale/" + currentSaleId; } else { alert("تم اعتماد الفاتورة بنجاح."); }
      closeModal();
      currentSaleId = null;
      sessionStorage.removeItem("kstore_current_sale_id");
      if (shouldPrint) { setTimeout(() => location.reload(), 1500); } else { location.reload(); }
    } catch (error) {
      alert("خطأ أثناء الاعتماد: " + (error.message || error));
    } finally {
      button.disabled = false;
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && modal.style.display !== 'none') closeModal();
    if (event.key === 'F10' && !event.repeat) {
      const tag = event.target && event.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || (event.target && event.target.isContentEditable)) return;
      event.preventDefault();
      if (modal.style.display === 'none') openModal();
      else document.getElementById('pmConfirm').click();
    }
  });
})();
(function () {
    const holdBtn = document.getElementById('btnHoldSale');
    const heldBtn = document.getElementById('btnShowHeldSales');

    function clearSaleScreen() {
        const tbody = document.getElementById('itemsTableBody');
        if (tbody) tbody.innerHTML = '';

        currentSaleId = null;
        currentSaleStatus = null;

        try {
            sessionStorage.removeItem('kstore_current_sale_id');
        } catch (e) {}

        if (typeof updateInvoiceSummary === 'function') {
            updateInvoiceSummary();
        }
    }

    if (holdBtn) {
        holdBtn.addEventListener('click', async function () {
            if (!currentSaleId) {
                alert('لا توجد فاتورة مفتوحة لتعليقها.');
                return;
            }

            if (!confirm('هل تريد تعليق الفاتورة الحالية؟')) return;

            try {
                const response = await fetch(
                    `/api/sales/${currentSaleId}/hold`,
                    { method: 'POST' }
                );
                const data = await response.json();

                if (!response.ok || !data.ok) {
                    alert(data.error || 'تعذر تعليق الفاتورة.');
                    return;
                }

                clearSaleScreen();
                alert('تم تعليق الفاتورة بنجاح. يمكنك اختيار فاتورة معلّقة أو بدء فاتورة جديدة.');
            } catch (e) {
                alert('تعذر الاتصال بالخادم. لم يتم تأكيد تعليق الفاتورة.');
            }
        });
    }

    if (heldBtn) {
        heldBtn.addEventListener('click', async function () {
            if (currentSaleId) {
                alert('للحفاظ على الفاتورة الحالية، علّقها أولاً ثم اختر الفاتورة التي تريد استئنافها.');
                return;
            }

            try {
                const response = await fetch('/api/sales/held');
                const data = await response.json();

                if (!response.ok || !data.ok) {
                    alert(data.error || 'تعذر جلب الفواتير المعلّقة.');
                    return;
                }

                const sales = data.sales || [];

                if (!sales.length) {
                    alert('لا توجد فواتير معلّقة.');
                    return;
                }

                const list = sales.map((sale, index) =>
                    `${index + 1}) ${sale.invoice_no || ('فاتورة #' + sale.id)} — ${sale.invoice_date || ''} — ${Number(sale.grand_total || 0).toLocaleString('en-US')} ل.س`
                ).join('\n');

                const answer = prompt(
                    'الفواتير المعلّقة:\n\n' + list +
                    '\n\nاكتب رقم الفاتورة التي تريد استئنافها، أو اضغط إلغاء.'
                );

                if (answer === null || answer.trim() === '') return;

                const choice = Number(answer);

                if (!Number.isInteger(choice) || choice < 1 || choice > sales.length) {
                    alert('رقم غير صحيح. اختر رقماً من القائمة.');
                    return;
                }

                const sale = sales[choice - 1];

                const resumeResponse = await fetch(
                    `/api/sales/${sale.id}/resume`,
                    { method: 'POST' }
                );
                const resumeData = await resumeResponse.json();

                if (!resumeResponse.ok || !resumeData.ok) {
                    alert(resumeData.error || 'تعذر استئناف الفاتورة.');
                    return;
                }

                currentSaleId = Number(sale.id);
                currentSaleStatus = 'draft';

                try {
                    sessionStorage.setItem(
                        'kstore_current_sale_id',
                        String(currentSaleId)
                    );
                } catch (e) {}

                await loadSaleItems();
                alert('تم استئناف الفاتورة بنجاح.');
            } catch (e) {
                alert('حدث خطأ أثناء تحميل الفواتير أو استئناف الفاتورة.');
                console.error(e);
            }
        });
    }
})();
/* RETURNS_MODAL_BINDING_V1 */
(function () {
    const modal = document.getElementById('returnsModal');
    const openBtn = document.getElementById('btnOpenReturns');
    const closeBtn = document.getElementById('btnCloseReturns');
    const cancelBtn = document.getElementById('btnCancelReturns');
    const searchBtn = document.getElementById('btnSearchReturns');
    const searchInput = document.getElementById('returnsInvoiceNo');
    const searchType = document.getElementById('returnsSearchType');

    if (!modal || !openBtn || !searchBtn || !searchInput) return;

    function closeModal() {
        modal.style.display = 'none';
    }

    openBtn.addEventListener('click', function () {
        modal.style.display = 'flex';
        searchInput.focus();
    });

    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    if (cancelBtn) cancelBtn.addEventListener('click', closeModal);

    modal.addEventListener('click', function (event) {
        if (event.target === modal) closeModal();
    });

    function setText(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent =
            value === null || value === undefined || value === '' ? '—' : String(value);
    }

    function addCell(row, value) {
        const cell = document.createElement('td');
        cell.textContent = value === null || value === undefined ? '—' : String(value);
        row.appendChild(cell);
    }

    function clearResults() {
        ['returnsItemsBody', 'returnsPreviousBody'].forEach(function (id) {
            const body = document.getElementById(id);
            if (body) body.replaceChildren();
        });
        setText('returnsInvoiceNumber', '—');
        setText('returnsDate', '—');
        setText('returnsCustomer', '—');
        setText('returnsInvoiceTotal', '—');
        setText('returnsInvoiceStatus', '—');
        const result = document.getElementById('returnsInvoiceResult');
        if (result) {
            result.textContent = '';
            result.style.color = '';
        }
    }

    function showMessage(message, isError) {
        const result = document.getElementById('returnsInvoiceResult');
        if (result) {
            result.textContent = message;
            result.style.color = isError ? '#b42318' : '#237a3b';
        } else {
            alert(message);
        }
    }

    function money(value) {
        const number = Number(value);
        return Number.isFinite(number) ? number.toLocaleString('en-US', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }) + ' ل.س' : '—';
    }

function renderItems(items) {
    const body = document.getElementById('returnsItemsBody');
    if (!body) return;
    body.replaceChildren();

    const previousReturns = window.returnsPreviousItems || [];

    (items || []).forEach(function (item, index) {
        const row = document.createElement('tr');
        const sold = Number(item.quantity) || 0;

        const returned = previousReturns
            .filter(function (r) {
                return Number(r.item_id) === Number(item.item_id);
            })
            .reduce(function (sum, r) {
                return sum + (Number(r.quantity) || 0);
            }, 0);

        const remaining = Math.max(0, sold - returned);

        addCell(row, index + 1);
        addCell(row, item.product_id);
        addCell(row, item.product_name);
        addCell(row, sold);
        addCell(row, returned);
        addCell(row, remaining);

        const quantityCell = document.createElement('td');
        const quantityInput = document.createElement('input');
        quantityInput.type = 'number';
        quantityInput.min = '0';
        quantityInput.max = String(remaining);
        quantityInput.step = 'any';
        quantityInput.value = '0';
        quantityInput.disabled = remaining <= 0;
        quantityInput.className = 'returns-quantity-input';
        quantityInput.dataset.itemId = String(item.item_id);
        quantityInput.dataset.productId = String(item.product_id);
        quantityInput.setAttribute('aria-label', 'الكمية المراد إرجاعها');

        quantityInput.addEventListener('change', function () {
            let value = Number(quantityInput.value);

            if (!Number.isFinite(value) || value < 0) value = 0;
            if (value > remaining) value = remaining;

            quantityInput.value = String(value);
        });

        quantityCell.appendChild(quantityInput);
        row.appendChild(quantityCell);

        addCell(row, money(item.unit_price));
        addCell(row, money(item.line_total));

        body.appendChild(row);
    });

    if (!items || !items.length) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 9;
        cell.textContent = 'لا توجد أصناف في الفاتورة';
        row.appendChild(cell);
        body.appendChild(row);
    }

}
    function renderPreviousReturns(returns) {
        const body = document.getElementById("returnsPreviousBody");
        if (!body) return;
        body.replaceChildren();
        (returns || []).forEach(function (item) {
            const row = document.createElement("tr");
            addCell(row, item.id ?? item.return_id ?? "");
            addCell(row, item.created_at ?? item.return_date ?? "");
            addCell(row, item.product_name ?? item.product_id ?? "");
            addCell(row, item.quantity ?? "");
            addCell(row, money(item.refund_amount ?? 0));
            body.appendChild(row);
        });
        if (!returns || !returns.length) {
            const row = document.createElement("tr");
            const cell = document.createElement("td");
            cell.colSpan = 5;
            cell.textContent = "لا توجد مرتجعات سابقة";
            row.appendChild(cell);
            body.appendChild(row);
        }
    }

    async function searchInvoice() {
        const query = searchInput.value.trim();
        const type = searchType ? searchType.value : 'invoice';

        if (!query) {
            showMessage('أدخل بيانات البحث أولًا.', true);
            searchInput.focus();
            return;
        }

        clearResults();
        searchBtn.disabled = true;
        searchBtn.dataset.oldText = searchBtn.textContent;
        searchBtn.textContent = 'جارٍ البحث…';

        try {
            const response = await fetch(
                '/api/returns/search_invoice?q=' + encodeURIComponent(query) +
                '&type=' + encodeURIComponent(type),
                { credentials: 'same-origin' }
            );
            const data = await response.json();

            if (!response.ok || !data.ok) {
                showMessage(data.error || 'تعذر إتمام البحث.', true);
                return;
            }

            if (data.search_results) {
                const result = document.getElementById('returnsInvoiceResult');
                if (!result) return;
                result.replaceChildren();

                const heading = document.createElement('p');
                heading.textContent = 'اختر الفاتورة المعتمدة المطلوبة:';
                result.appendChild(heading);

                data.search_results.forEach(function (invoice) {
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.textContent =
                        invoice.customer_name + ' — ' +
                        invoice.invoice_no + ' — ' +
                        invoice.invoice_date + ' — ' +
                        money(invoice.grand_total);
                    button.style.display = 'block';
                    button.style.margin = '6px 0';
                    button.style.maxWidth = '100%';
                    button.style.whiteSpace = 'normal';

                    button.addEventListener('click', function () {
                        if (searchType) searchType.value = 'invoice';
                        searchInput.value = invoice.invoice_no;
                        searchInvoice();
                    });

                    result.appendChild(button);
                });
                return;
            }

            const sale = data.sale || {};
            window.returnsCurrentSale = sale;
            setText('returnsInvoiceNumber', sale.invoice_no || data.invoice_no);
            setText('returnsDate', sale.invoice_date);
            setText('returnsCustomer', sale.customer_name || 'عميل نقدي');
            setText('returnsInvoiceTotal', money(sale.grand_total));

            const statuses = {
                approved: 'معتمدة',
                draft: 'مسودة',
                cancelled: 'ملغاة',
                canceled: 'ملغاة'
            };
            setText('returnsInvoiceStatus',
                statuses[String(sale.status || '').toLowerCase()] ||
                sale.status || 'غير محددة');
window.returnsPreviousItems = data.previous_returns || [];
            renderItems(data.items);
            renderPreviousReturns(data.previous_returns);
            showMessage('تم العثور على الفاتورة. تنفيذ المرتجعات ما زال معطّلًا.', false);
        } catch (error) {
            showMessage('تعذر الاتصال بالخادم أثناء البحث.', true);
        } finally {
            searchBtn.disabled = false;
            searchBtn.textContent = searchBtn.dataset.oldText || 'بحث';
        }
    }

    searchBtn.addEventListener('click', searchInvoice);
    searchInput.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            searchInvoice();
        }
    });    async function executeReturns(full) {
        const sale = window.returnsCurrentSale;
        if (!sale || !sale.id) { showMessage('أعد البحث عن الفاتورة أولاً.', true); return; }
        const items = [];
        let invalidQuantity = false;
        document.querySelectorAll('#returnsItemsBody .returns-quantity-input').forEach(function(input) {
            const max = Number(input.max), qty = full ? max : Number(input.value);
            const itemId = Number(input.dataset.itemId);
            if (full && (!Number.isFinite(max) || max <= 0)) return;
            if (!Number.isFinite(max) || max <= 0 || !Number.isFinite(qty) || qty <= 0 || qty > max || !Number.isInteger(itemId) || itemId <= 0) {
                invalidQuantity = true;
                return;
            }
            items.push({item_id: itemId, quantity: qty});
        });
        if (invalidQuantity) {
            showMessage('تحقق من الكميات المدخلة؛ يجب أن تكون أكبر من صفر وألا تتجاوز الكمية المتبقية.', true);
            return;
        }
        if (!items.length) { showMessage(full ? 'لا توجد كمية متبقية للإرجاع' : 'أدخل كمية مرتجع صحيحة أولاً.', true); return; }
        if (!confirm(full ? 'هل تؤكد إرجاع جميع الكميات المتبقية؟' : 'هل تؤكد تنفيذ المرتجع بالكميات المدخلة؟')) return;
        const buttons = [document.getElementById('btnPartialReturn'), document.getElementById('btnFullReturn')];
        buttons.forEach(b => { if (b) b.disabled = true; });
        try {
            const r = await fetch('/api/returns/execute', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify({sale_id:Number(sale.id), items:items})});
            const d = await r.json();
            if (!r.ok || !d.ok) throw new Error(d.error || d.message || 'تعذر تنفيذ المرتجع.');
            showMessage('تم تنفيذ المرتجع. للمبلغ المسترد: ' + money(d.refund_amount), false);
            await searchInvoice();
        } catch(e) { showMessage(e.message || 'حدث خطأ أثناء التنفيذ.', true); }
        finally { buttons.forEach(b => { if (b) b.disabled = false; }); }
    }
    const partialButton = document.getElementById('btnPartialReturn');
    const fullButton = document.getElementById('btnFullReturn');
    if (partialButton) { partialButton.disabled = false; partialButton.addEventListener('click', () => executeReturns(false)); }
    if (fullButton) { fullButton.disabled = false; fullButton.addEventListener('click', () => executeReturns(true)); }
})();
