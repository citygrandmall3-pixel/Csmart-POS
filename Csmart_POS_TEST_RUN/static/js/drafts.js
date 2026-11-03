(function () {
    const draftBtn = document.getElementById("btnShowDraftSales");
    if (!draftBtn) return;

    draftBtn.addEventListener("click", async function () {
        if (currentSaleId) {
            alert("أنهِ الفاتورة الحالية أو علّقها أولاً.");
            return;
        }

        try {
            const [sessionResponse, salesResponse] = await Promise.all([
                fetch("/api/cashier-sessions/current", {
                    credentials: "same-origin"
                }),
                fetch("/api/sales", {
                    credentials: "same-origin"
                })
            ]);

            const sessionData = await sessionResponse.json();
            const salesData = await salesResponse.json();

            if (!sessionResponse.ok || !sessionData.ok) {
                alert("تعذر معرفة الوردية الحالية.");
                return;
            }

            if (!sessionData.session) {
                alert("لا توجد وردية مفتوحة حاليًا.");
                return;
            }

            if (!salesResponse.ok || !salesData.ok) {
                alert(salesData.error || "تعذر تحميل المسودات.");
                return;
            }

            const sessionId = Number(sessionData.session.id);

            const drafts = (salesData.rows || []).filter(sale =>
                sale.status === "draft" &&
                Number(sale.session_id) === sessionId
            );

            if (!drafts.length) {
                alert("لا توجد مسودات في ورديتك الحالية.");
                return;
            }

            const list = drafts.map((sale, index) =>
                (index + 1) + ") " +
                (sale.invoice_no || "فاتورة #" + sale.id) +
                " — " + (sale.invoice_date || "") +
                " — " + Number(sale.grand_total || 0).toFixed(2) + " ل.س"
            ).join("\n");

            const answer = prompt(
                "مسودات ورديتك الحالية:\n\n" + list +
                "\n\nاكتب رقم المسودة:"
            );

            if (answer === null || answer.trim() === "") return;

            const choice = Number(answer);

            if (!Number.isInteger(choice) ||
                choice < 1 || choice > drafts.length) {
                alert("رقم غير صحيح.");
                return;
            }

            const sale = drafts[choice - 1];

            const action = prompt(
                "الفاتورة: " + (sale.invoice_no || sale.id) +
                "\n\nاكتب 1 لاستئناف المسودة" +
                "\nاكتب 2 لحذفها نهائيًا:"
            );

            if (action === null) return;

            if (action.trim() === "2") {
                const confirmed = confirm(
                    "تحذير: سيتم حذف المسودة وأصنافها وتفاصيل توصيلها نهائيًا.\nهل تريد المتابعة؟"
                );

                if (!confirmed) return;

                const deleteResponse = await fetch(
                    "/api/sales/" + Number(sale.id),
                    {
                        method: "DELETE",
                        credentials: "same-origin"
                    }
                );

                const deleteData = await deleteResponse.json();

                if (!deleteResponse.ok || !deleteData.ok) {
                    alert(deleteData.error || "فشل حذف المسودة.");
                    return;
                }

                alert("تم حذف المسودة نهائيًا.");
                return;
            }

            if (action.trim() !== "1") {
                alert("اختيار غير صحيح.");
                return;
            }

            currentSaleId = Number(sale.id);
            currentSaleStatus = "draft";

            sessionStorage.setItem(
                "kstore_current_sale_id",
                String(sale.id)
            );

            await loadSaleItems();

            alert("تم فتح المسودة دون اعتمادها.");

        } catch (e) {
            console.error(e);
            alert("حدث خطأ أثناء معالجة المسودات.");
        }
    });
})();