
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
