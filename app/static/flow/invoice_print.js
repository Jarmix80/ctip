function initializeInvoicePrint() {
  const printButtons = Array.from(document.querySelectorAll("[data-print-a4]"));
  if (printButtons.length === 0) {
    return;
  }

  printButtons.forEach((button) => {
    button.addEventListener("click", () => {
      window.print();
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initializeInvoicePrint();
});
