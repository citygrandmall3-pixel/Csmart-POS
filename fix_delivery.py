from pathlib import Path

p = Path("app_returns_test.py")
s = p.read_text()
a = s.index("def api_sales_approve(sale_id):")
b = s.index("\n@app.route(", a)
t = s[a:b]

old = '''        conn.commit()

        return jsonify({
            "ok": True,
            "sale_id": sale_id,
            "invoice_no": sale["invoice_no"],'''

new = '''        delivery_data = req_data.get("delivery_data")
        if delivery_data is not None:
            if not isinstance(delivery_data, dict):
                raise ValueError("بيانات التوصيل غير صحيحة")
            rep = str(delivery_data.get("rep") or "").strip()
            phone = str(delivery_data.get("phone") or "").strip()
            address = str(delivery_data.get("addr") or "").strip()
            try:
                fees = float(delivery_data.get("fees") or 0)
            except (TypeError, ValueError):
                raise ValueError("رسوم التوصيل غير صحيحة")
            if not rep or not phone or not address or fees < 0:
                raise ValueError("بيانات التوصيل غير مكتملة أو الرسوم غير صحيحة")
            conn.execute("""
                INSERT INTO sale_delivery_details
                    (sale_id, company_id, rep, phone, address, fees)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(sale_id) DO UPDATE SET
                    company_id=excluded.company_id,
                    rep=excluded.rep,
                    phone=excluded.phone,
                    address=excluded.address,
                    fees=excluded.fees
            """, (sale_id, company_id, rep, phone, address, fees))

        conn.commit()

        return jsonify({
            "ok": True,
            "sale_id": sale_id,
            "invoice_no": sale["invoice_no"],'''

assert old in t, "لم يُعثر على موضع التعديل؛ لم يتغير الملف"
p.write_text(s[:a] + t.replace(old, new, 1) + s[b:])
print("تم التعديل")
