
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
        if (shouldPrint && printWindow) { printWindow.close(); printWindow = null; }
        alert('فشل اعتماد الفاتورة: ' + (data.error || ('HTTP ' + response.status)));
        return;
      }

      if (shouldPrint && printWindow) { printWindow.location.href = '/print/sale/' + currentSaleId; } else { alert('تم اعتماد الفاتورة بنجاح.'); }
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
