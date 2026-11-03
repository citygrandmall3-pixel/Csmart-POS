// إدارة الكاشيرين — ملف مستقل

async function loadCashiers() {
  const body = document.getElementById("cashiersBody");
  if (!body) return;
  try {
    const data = await api("/api/cashiers");
    if (!data.ok) return;
    if (!data.cashiers || data.cashiers.length === 0) {
      body.innerHTML = "<tr><td colspan=\"5\" style=\"text-align:center;padding:12px;color:#999\">لا يوجد كاشيرون بعد</td></tr>";
      return;
    }
    body.innerHTML = data.cashiers.map(c => {
      const status = c.is_active ? "✅ نشط" : "⛔ معطّل";
      const toggleLabel = c.is_active ? "تعطيل" : "تنشيط";
      const toggleColor = c.is_active ? "#dc3545" : "#28a745";
      return "<tr>" +
        "<td style=\"padding:6px;border:1px solid #eee;text-align:center\">" + c.id + "</td>" +
        "<td style=\"padding:6px;border:1px solid #eee\">" + (c.username || "") + "</td>" +
        "<td style=\"padding:6px;border:1px solid #eee\">" + (c.full_name || "") + "</td>" +
        "<td style=\"padding:6px;border:1px solid #eee;text-align:center\">" + status + "</td>" +
        "<td style=\"padding:6px;border:1px solid #eee;text-align:center\">" +
          "<button onclick=\"editCashier(" + c.id + ")\" style=\"background:#0d6efd;color:#fff;border:0;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:12px\">✏️</button>" +
          "<button onclick=\"toggleCashier(" + c.id + ")\" style=\"background:" + toggleColor + ";color:#fff;border:0;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:12px;margin:0 4px\">" + toggleLabel + "</button>" +
          "<button onclick=\"deleteCashier(" + c.id + ")\" style=\"background:#6c757d;color:#fff;border:0;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:12px\">🗑️</button>" +
        "</td>" +
        "</tr>";
    }).join("");
  } catch (e) {
    body.innerHTML = "<tr><td colspan=\"5\" style=\"text-align:center;padding:12px;color:#c00\">خطأ: " + e.message + "</td></tr>";
  }
}

function openCashierModal() {
  document.getElementById("cashierModalTitle").textContent = "➕ إضافة كاشير جديد";
  document.getElementById("cEditId").value = "";
  document.getElementById("cUsername").value = "";
  document.getElementById("cPassword").value = "";
  document.getElementById("cFullName").value = "";
  document.getElementById("cPhone").value = "";
  document.getElementById("cPwNote").textContent = "(مطلوبة للإضافة)";
  const m = document.getElementById("cashierModal");
  document.body.appendChild(m);
  m.style.setProperty("position", "fixed", "important");
  m.style.setProperty("display", "flex", "important");
  m.style.setProperty("z-index", "999999", "important");
}

function hideCashierModal() {
  const m = document.getElementById("cashierModal");
  if (m) m.style.setProperty("display", "none", "important");
}

async function saveCashier() {
  const id = document.getElementById("cEditId").value;
  const username = document.getElementById("cUsername").value.trim().toLowerCase();
  const password = document.getElementById("cPassword").value;
  const fullName = document.getElementById("cFullName").value.trim();
  const phone = document.getElementById("cPhone").value.trim();
  if (!username || username.length < 3) return alert("اسم المستخدم 3 أحرف على الأقل");
  if (!fullName) return alert("الاسم الكامل مطلوب");
  if (!id && (!password || password.length < 4)) return alert("كلمة المرور 4 أحرف على الأقل");
  const payload = { username: username, full_name: fullName, phone: phone };
  if (password) payload.password = password;
  payload.permissions = {
    can_sell: true, can_open_session: true, can_hold_sale: true,
    can_cancel_draft: true, can_view_own_reports: true,
    can_apply_discount: false, max_discount_pct: 0,
    can_view_stock: false, can_view_others_sessions: false
  };
  try {
    let res;
    if (id) {
      res = await api("/api/cashiers/" + id, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      res = await api("/api/cashiers", { method: "POST", body: JSON.stringify(payload) });
    }
    if (!res.ok) throw new Error(res.error || "فشل الحفظ");
    hideCashierModal();
    await loadCashiers();
    alert("تم الحفظ بنجاح");
  } catch (e) { alert("خطأ: " + e.message); }
}

async function editCashier(id) {
  try {
    const data = await api("/api/cashiers");
    if (!data.ok) return;
    const c = (data.cashiers || []).find(x => Number(x.id) === Number(id));
    if (!c) { alert("غير موجود"); return; }
    document.getElementById("cashierModalTitle").textContent = "✏️ تعديل كاشير";
    document.getElementById("cEditId").value = c.id;
    document.getElementById("cUsername").value = c.username || "";
    document.getElementById("cPassword").value = "";
    document.getElementById("cFullName").value = c.full_name || "";
    document.getElementById("cPhone").value = c.phone || "";
    document.getElementById("cPwNote").textContent = "(اتركه فارغًا)";
    const m = document.getElementById("cashierModal");
    document.body.appendChild(m);
    m.style.setProperty("display", "flex", "important");
    m.style.setProperty("position", "fixed", "important");
    m.style.setProperty("z-index", "999999", "important");
  } catch (e) { alert(e.message); }
}

async function toggleCashier(id) {
  try {
    const res = await api("/api/cashiers/" + id + "/toggle", { method: "POST", body: JSON.stringify({}) });
    if (!res.ok) throw new Error(res.error || "فشل");
    await loadCashiers();
  } catch (e) { alert(e.message); }
}

async function deleteCashier(id) {
  if (!confirm("حذف هذا الكاشير؟")) return;
  try {
    const res = await api("/api/cashiers/" + id, { method: "DELETE" });
    if (!res.ok) throw new Error(res.error || "فشل");
    alert(res.message || "تم");
    await loadCashiers();
  } catch (e) { alert(e.message); }
}

// ربط الأزرار عند تحميل الصفحة
setTimeout(() => {
  if (document.getElementById("cashiersBody")) loadCashiers();
  const b = document.getElementById("btnAddCashier");
  if (b) b.addEventListener("click", openCashierModal);
  const cb = document.getElementById("btnCancelCashier");
  if (cb) cb.addEventListener("click", hideCashierModal);
  const sb = document.getElementById("btnSaveCashier");
  if (sb) sb.addEventListener("click", saveCashier);
}, 500);
