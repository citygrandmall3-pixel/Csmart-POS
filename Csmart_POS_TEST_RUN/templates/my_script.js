function updateInvoiceSummary() {
    let totalItems = document.querySelectorAll("#saleItemsTable tbody tr").length;
    let totalPrice = 0;
    document.querySelectorAll("#saleItemsTable tbody tr").forEach(row => {
        let cells = row.querySelectorAll("td");
        if (cells.length > 0) {
            let lastCellText = cells[cells.length - 1].innerText.replace(/[^0-9.]/g, "");
            totalPrice += parseFloat(lastCellText) || 0;
        }
    });
    let itemsEl = document.getElementById("summaryTotalItems");
    let priceEl = document.getElementById("summaryTotalPrice");
    if (itemsEl) itemsEl.innerText = totalItems;
    if (priceEl) priceEl.innerText = totalPrice.toFixed(2) + " ج.م";
}
