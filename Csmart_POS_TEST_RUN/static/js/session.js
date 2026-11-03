async function checkSession() {
  try {
    const data = await api("/api/cashier-sessions/current");
    if (data.ok && data.session) {
      currentSession = data.session;
      if (data.session.cashier_name) {
        window.currentCashier = {name: data.session.cashier_name, permissions: JSON.parse(data.session.cashier_permissions || "{}")};
        applyCashierPermissions();
      }
      renderSessionBar();
      document.getElementById("sessionOpenModal").style.setProperty("display", "none", "important");
      document.getElementById("sessionBar").style.display = "flex";
      document.getElementById("sessionOpenModal").style.setProperty("display", "none", "important");
      if (!sessionCheckInterval) {
        sessionCheckInterval = setInterval(refreshSessionBar, 30000);
      }
    } else {
      currentSession = null;
      document.getElementById("sessionBar").style.display = "none";
      openSessionOpenModal();
    }
  } catch (e) {
    console.error("session check failed:", e);
  }
}
function renderSessionBar() {
  if (!currentSession) return;
  const s = currentSession;
  document.getElementById("sessOpenedAt").textContent = s.opened_at || "-";
  document.getElementById("sessInvCount").textContent = s.invoice_count || 0;
  document.getElementById("sessTotalSales").textContent = fmt(s.total_sales || 0);
  document.getElementById("sessExpectedCash").textContent = fmt(s.expected_cash || 0);
}
async function refreshSessionBar() {
  try {
    const data = await api("/api/cashier-sessions/current");
    if (data.ok && data.session) {
      currentSession = data.session;
      if (data.session.cashier_name) {
      }
      renderSessionBar();
      document.getElementById("sessionOpenModal").style.setProperty("display", "none", "important");
    }
  } catch (e) {}
}
function openSessionOpenModal() {
  document.getElementById("sessOpeningCash").value = "0";
  document.getElementById("sessOpenNotes").value = "";
  const _el = document.getElementById("sessionOpenModal");
  if (_el) { document.body.appendChild(_el); _el.style.setProperty("position", "fixed", "important"); _el.style.setProperty("display", "flex", "important"); _el.style.setProperty("z-index", "999999", "important"); }
}
async function confirmOpenSession() {
  const username = (document.getElementById("sessUsername").value || "").trim().toLowerCase();
  const password = (document.getElementById("sessPassword").value || "").trim();
  const cash = parseFloat(document.getElementById("sessOpeningCash").value || 0);
  const notes = document.getElementById("sessOpenNotes").value.trim();
  if (!username || !password) {
    alert("أدخل اسم المستخدم وكلمة المرور");
    return;
  }
  if (isNaN(cash) || cash < 0) {
    alert("أدخل رصيد افتتاحي صحيح");
    return;
  }
  try {
    const auth = await api("/api/cashier-login", {
      method: "POST",
      body: JSON.stringify({ username: username, password: password })
    });
    if (!auth.ok) throw new Error(auth.error || "بيانات غير صحيحة");
    const data = await api("/api/cashier-sessions/open", {
      method: "POST",
      body: JSON.stringify({
        opening_cash: cash,
        notes: notes,
        cashier_id: auth.cashier.id
      })
    });
    if (!data.ok) throw new Error(data.error || "فشل فتح الوردية");
    applyCashierPermissions();
    window.currentCashier = auth.cashier;
    document.getElementById("sessionOpenModal").style.setProperty("display", "none", "important");
    document.getElementById("sessUsername").value = "";
    document.getElementById("sessPassword").value = "";
    await new Promise(r => setTimeout(r, 500));
    await checkSession();
    alert("تم فتح الوردية للكاشير: " + auth.cashier.full_name);
  } catch (e) {
    alert("خطأ: " + e.message);
  }
}
function openSessionCloseModal() {
  if (!currentSession) return;
  const s = currentSession;
  document.getElementById("closeOpeningCash").textContent = fmt(s.opening_cash || 0);
  document.getElementById("closeCashSales").textContent = fmt(s.cash_sales || 0);
  document.getElementById("closeTotalSales").textContent = fmt(s.total_sales || 0);
  document.getElementById("closeExpectedCash").textContent = fmt(s.expected_cash || 0);
  document.getElementById("closeInvCount").textContent = s.invoice_count || 0;
  document.getElementById("sessActualCash").value = (s.expected_cash || 0).toFixed(2);
  document.getElementById("sessCloseNotes").value = "";
  updateSessionDiff();
  document.getElementById("sessActualCash").oninput = updateSessionDiff;
  const _cm = document.getElementById("sessionCloseModal"); if (_cm) { document.body.appendChild(_cm); _cm.style.setProperty("position", "fixed", "important"); _cm.style.setProperty("display", "flex", "important"); _cm.style.setProperty("z-index", "999999", "important"); }
}
function updateSessionDiff() {
  if (!currentSession) return;
  const expected = parseFloat(currentSession.expected_cash || 0);
  const actual = parseFloat(document.getElementById("sessActualCash").value || 0);
  const diff = actual - expected;
  const box = document.getElementById("sessDiffBox");
  if (Math.abs(diff) < 0.01) {
    box.textContent = "✅ مطابق تمامًا";
    box.style.background = "#d4edda";
    box.style.color = "#155724";
  } else if (diff > 0) {
    box.textContent = "⚠️ زيادة: " + fmt(diff);
    box.style.background = "#fff3cd";
    box.style.color = "#856404";
  } else {
    box.textContent = "❌ عجز: " + fmt(Math.abs(diff));
    box.style.background = "#f8d7da";
    box.style.color = "#721c24";
  }
}
function hideSessionCloseModal() {
  const m = document.getElementById("sessionCloseModal");
  if (m) m.style.setProperty("display", "none", "important");
}
async function confirmCloseSession() {
  if (!currentSession) return;
  const actual = parseFloat(document.getElementById("sessActualCash").value || 0);
  const notes = document.getElementById("sessCloseNotes").value.trim();
  if (isNaN(actual) || actual < 0) {
    alert("أدخل النقد الفعلي بشكل صحيح");
    return;
  }
  const expected = parseFloat(currentSession.expected_cash || 0);
  const diff = actual - expected;
  if (Math.abs(diff) < 0.01) {
    if (!confirm("تأكيد إغلاق الوردية؟\n\nالنقد متطابق تمامًا.")) return;
    await doCloseSession(actual, notes);
  } else {
    openSessionReviewModal(actual, expected, diff, notes);
  }
}
async function doCloseSession(actual, notes) {
  try {
    const data = await api("/api/cashier-sessions/" + currentSession.id + "/close", {
      method: "POST",
      body: JSON.stringify({ actual_cash: actual, notes: notes })
    });
    if (!data.ok) throw new Error(data.error || "فشل إغلاق الوردية");
    hideSessionCloseModal();
    hideSessionReviewModal();
    alert("تم إغلاق الوردية بنجاح. جاهز لفتح وردية جديدة.");
    if (sessionCheckInterval) {
      clearInterval(sessionCheckInterval);
      sessionCheckInterval = null;
    }
    currentSession = null;
    window.currentCashier = null; await new Promise(r => setTimeout(r, 500)); await checkSession();
  } catch (e) {
    alert("خطأ: " + e.message);
  }
}
function hideSessionReviewModal() {
  const m = document.getElementById("sessionReviewModal");
  if (m) m.style.setProperty("display", "none", "important");
}
async function openSessionReviewModal(actual, expected, diff, notes) {
  const m = document.getElementById("sessionReviewModal");
  if (!m) return;
  const expEl = document.getElementById("revExpected");
  const actEl = document.getElementById("revActual");
  const difEl = document.getElementById("revDiff");
  if (expEl) expEl.textContent = fmt(expected);
  if (actEl) actEl.textContent = fmt(actual);
  if (difEl) {
    difEl.textContent = (diff > 0 ? "+" : "") + fmt(diff);
    difEl.style.color = diff > 0 ? "#856404" : "#721c24";
  }
  try {
    const data = await api("/api/cashier-sessions/" + currentSession.id);
    if (data.ok && data.invoices) {
      const cnt = document.getElementById("revInvCount");
      if (cnt) cnt.textContent = data.invoices.length;
      const body = document.getElementById("revInvBody");
      if (body) {
        if (data.invoices.length === 0) {
          body.innerHTML = "<tr><td colspan=\"4\" style=\"text-align:center;padding:10px;color:#666\">لا توجد فواتير</td></tr>";
        } else {
          body.innerHTML = data.invoices.map(inv => "<tr>" +
            "<td style=\"padding:6px;border-bottom:1px solid #eee;text-align:center\">" + inv.id + "</td>" +
            "<td style=\"padding:6px;border-bottom:1px solid #eee\">" + (inv.invoice_no || "") + "</td>" +
            "<td style=\"padding:6px;border-bottom:1px solid #eee;text-align:left\">" + fmt(inv.grand_total) + "</td>" +
            "<td style=\"padding:6px;border-bottom:1px solid #eee\">" + (inv.payment_method === "cash" ? "نقدي" : (inv.payment_method === "credit" ? "آجل" : inv.payment_method)) + "</td>" +
            "</tr>").join("");
        }
      }
    }
  } catch (e) { console.error(e); }
  const reason = document.getElementById("revReason");
  if (reason) reason.value = "";
  window._pendingClose = { actual: actual, notes: notes };
  document.body.appendChild(m);
  m.style.setProperty("position", "fixed", "important");
  m.style.setProperty("display", "flex", "important");
  m.style.setProperty("z-index", "999999", "important");
}
async function confirmReviewAndClose() {
  const reason = document.getElementById("revReason").value.trim();
  if (!reason) {
    alert("يجب إدخال سبب الفرق");
    return;
  }
  const p = window._pendingClose || {};
  const finalNotes = (p.notes ? p.notes + " | " : "") + "سبب الفرق: " + reason;
  await doCloseSession(p.actual, finalNotes);
}
function applyCashierPermissions() {
  if (!window.currentCashier || !window.currentCashier.permissions) return;
  const p = window.currentCashier.permissions;
  const canSell = p.can_sell !== false;
  const canCancel = p.can_cancel_draft !== false;
  const delBtn = document.getElementById("deleteSaleBtn");
  const appBtn = document.getElementById("approveSaleBtn");
  if (delBtn && !canCancel) delBtn.style.display = "none";
  if (appBtn && !canSell) appBtn.style.display = "none";
}