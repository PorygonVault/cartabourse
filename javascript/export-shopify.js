// ============================================================
// Export Shopify — CartaBourse / Orbis TCG
// Colonnes vérifiées directement sur la documentation officielle
// Shopify (help.shopify.com/en/manual/products/import-export/using-csv)
// — pas une supposition. Seules les colonnes pertinentes pour une carte
// à collectionner sont remplies, le reste (Barcode, Compare-at price,
// Cost per item, Weight, Fulfillment service, Google Shopping...) est
// volontairement laissé vide, même principe que pour Whatnot.
//
// Un produit simple (sans variantes) nécessite Option1 name="Title" et
// Option1 value="Default Title" par défaut, sans quoi Shopify supprime
// toute variante existante à l'import — confirmé dans leur doc.
//
// Isolé dans une IIFE, même principe que export-voggt.js/export-whatnot.js.
// ============================================================
(function () {

  const HEADERS = [
    "Title", "URL handle", "Description", "Type", "Tags",
    "Published on online store", "Status",
    "SKU", "Option1 name", "Option1 value",
    "Price", "Inventory tracker", "Inventory quantity",
    "Requires shipping", "Product image URL", "Image alt text",
  ];

  class ValidationError extends Error {}

  function validateItem(item, index) {
    const errors = [];
    if (item.quantity == null || item.quantity < 1) errors.push("quantity manquant ou invalide (doit être >= 1)");
    const price = item.instant_buy_price != null && item.instant_buy_price !== "" ? item.instant_buy_price : item.starting_price;
    if (price == null || price === "") errors.push("aucun prix renseigné (starting_price et/ou instant_buy_price)");
    if (errors.length) {
      throw new ValidationError(`Item #${index} (${item.card_name || "?"}) : ` + errors.join(" ; "));
    }
  }

  // URL handle : lettres, chiffres, tirets uniquement, pas d'espace —
  // dérivé du titre, avec un suffixe pour éviter deux handles identiques
  // si deux cartes ont exactement le même titre généré.
  function slugify(text, suffix) {
    const base = String(text)
      .toLowerCase()
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "") // retire les accents
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return `${base}-${suffix}`;
  }

  function buildShopifyRow(item, nameTemplate, descriptionTemplate, liveDisclaimerOnly, locale, rowIndex) {
    const resolveBlocks = window.VoggtExportInternals.resolveBlocks;
    const buildDescription = window.VoggtExportInternals.buildDescription;

    const title = resolveBlocks(nameTemplate, item);
    const description = buildDescription(descriptionTemplate, item, liveDisclaimerOnly, locale);
    const price = item.instant_buy_price != null && item.instant_buy_price !== "" ? item.instant_buy_price : item.starting_price;
    const sku = item.card_id ? `orbis-${item.card_id}` : `orbis-${Date.now()}-${rowIndex}`;

    return [
      title,
      slugify(title, rowIndex),
      description,
      "Trading Card Games",
      "", // Tags — laissé vide, rien d'universel à y mettre par défaut
      "true", // Published on online store
      "active", // Status
      sku,
      "Title", "Default Title", // Option1 name/value — produit simple, sans variantes Shopify
      price,
      "shopify", // Inventory tracker — active le suivi de stock Shopify
      item.quantity,
      "true", // Requires shipping
      item.image_url || "",
      title, // Image alt text
    ];
  }

  /** Génère le CSV Shopify et déclenche le téléchargement. Valide TOUS
   * les items avant d'écrire quoi que ce soit — même principe que
   * exportToVoggt/exportToWhatnot. */
  function exportToShopify(items, nameTemplate, descriptionTemplate, filename = "export_shopify.csv", liveDisclaimerOnly = false, locale = "en") {
    items.forEach((item, i) => validateItem(item, i + 1));

    const rows = items.map((item, i) => buildShopifyRow(item, nameTemplate, descriptionTemplate, liveDisclaimerOnly, locale, i + 1));

    const csvEscape = (val) => {
      const s = String(val ?? "");
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const csvContent = [
      HEADERS.join(","),
      ...rows.map((row) => row.map(csvEscape).join(",")),
    ].join("\n");

    // BOM UTF-8 — même correctif que Voggt/Whatnot, évite les accents
    // déformés selon le logiciel/la langue système utilisé à l'ouverture.
    const blob = new Blob(["\uFEFF" + csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    return rows.length;
  }

  window.exportToShopify = exportToShopify;
  window.ShopifyExportInternals = { ValidationError, slugify };

})();
