// ============================================================
// Export Whatnot — CartaBourse
// 4 marchés : NL, DE, FR (+BE), UK — colonnes structurellement
// identiques partout (22 colonnes), seuls la sous-catégorie et le
// profil de livraison par défaut changent réellement pour notre usage.
//
// Colonnes volontairement laissées VIDES (confirmé) : Offerable,
// Hazmat, Condition, Cost Per Item, SKU, Image URL 2 à 8.
//
// Deux hypothèses à confirmer avec le support Whatnot (voir le
// message qui accompagne ce fichier) :
//   - Le champ "Type" (format de vente) est réglé sur "Buy it Now"
//   - Les 4 marchés couverts sont NL/DE/FR+BE/UK (pas US)
//
// Isolé dans une IIFE, même principe que export-voggt.js — aucun
// risque de collision avec des variables déjà utilisées ailleurs sur
// le site.
// ============================================================
(function () {

  const WHATNOT_COUNTRIES = {
    nl: {
      label: "Pays-Bas (NL)",
      category: "Trading Card Games",
      subcategory: "Pokémon Cards",
      defaultShippingProfile: "0 to <20 grams",
    },
    us: {
      label: "États-Unis (US)",
      category: "Trading Card Games",
      subcategory: "Pokémon Cards",
      defaultShippingProfile: "0 to <20 grams", // fichier commun avec NL, mêmes valeurs
    },
    de: {
      label: "Allemagne (DE)",
      category: "Trading Card Games",
      subcategory: "Pokémon-Karten",
      defaultShippingProfile: "Single (15 g)",
    },
    fr: {
      label: "France + Belgique (FR/BE)",
      category: "Trading Card Games",
      subcategory: "Cartes Pokémon",
      defaultShippingProfile: "De 0 à <20 grammes",
    },
    uk: {
      label: "Royaume-Uni (UK)",
      category: "Trading Card Games",
      subcategory: "Pokémon Cards",
      defaultShippingProfile: "Single (15g)",
    },
  };

  // Format de vente par défaut : Enchère. Confirmé par l'utilisateur que
  // "Auction" est la valeur attendue dans la colonne Type pour ce format
  // — reste à confirmer avec le support Whatnot que c'est bien exact.
  const SALE_TYPE = "Auction";

  class ValidationError extends Error {}

  function validateItem(item, index) {
    const errors = [];
    if (item.quantity == null || item.quantity < 1) errors.push("quantity manquant ou invalide (doit être >= 1)");
    const price = item.instant_buy_price != null ? item.instant_buy_price : item.starting_price;
    if (price == null) errors.push("aucun prix renseigné (starting_price et/ou instant_buy_price)");
    if (errors.length) {
      throw new ValidationError(`Item #${index} (${item.card_name || "?"}) : ` + errors.join(" ; "));
    }
  }

  function buildWhatnotRow(item, nameTemplate, descriptionTemplate, liveDisclaimerOnly, locale, countryConfig, shippingProfileOverride) {
    // Réutilise le même moteur de blocs que Voggt (resolveBlocks/buildDescription
    // exposés par export-voggt.js) — logique identique, aucune duplication.
    const resolveBlocks = window.VoggtExportInternals.resolveBlocks;
    const buildDescription = window.VoggtExportInternals.buildDescription;

    // Un seul champ Price chez Whatnot (contrairement à Voggt qui a
    // startingPrice + instantBuyPrice séparés). Le format par défaut
    // étant Enchère, ce champ représente la mise de DÉPART — priorité à
    // starting_price, repli sur instant_buy_price seulement si aucune
    // mise de départ n'a été renseignée pour cette carte.
    const price = item.starting_price != null ? item.starting_price : item.instant_buy_price;

    return {
      "Category": countryConfig.category,
      "Subcategory": countryConfig.subcategory,
      "Title": resolveBlocks(nameTemplate, item),
      "Description": buildDescription(descriptionTemplate, item, liveDisclaimerOnly, locale),
      "Quantity": item.quantity,
      "Type": SALE_TYPE,
      "Price": price,
      "Shipping Profile": shippingProfileOverride || countryConfig.defaultShippingProfile,
      "Offerable": "",
      "Hazmat": "",
      "Condition": "",
      "Cost Per Item": "",
      "SKU": "",
      "Image URL 1": item.image_url || "",
      "Image URL 2": "", "Image URL 3": "", "Image URL 4": "", "Image URL 5": "",
      "Image URL 6": "", "Image URL 7": "", "Image URL 8": "",
    };
  }

  /** Génère le CSV Whatnot pour le pays choisi et déclenche le
   * téléchargement. Valide TOUS les items avant d'écrire quoi que ce
   * soit — même principe que exportToVoggt. */
  function exportToWhatnot(items, nameTemplate, descriptionTemplate, countryCode, shippingProfileOverride, filename, liveDisclaimerOnly = false, locale = "en") {
    const countryConfig = WHATNOT_COUNTRIES[countryCode];
    if (!countryConfig) throw new ValidationError(`Pays Whatnot inconnu : "${countryCode}"`);

    items.forEach((item, i) => validateItem(item, i + 1));

    const rows = items.map((item) =>
      buildWhatnotRow(item, nameTemplate, descriptionTemplate, liveDisclaimerOnly, locale, countryConfig, shippingProfileOverride)
    );

    const headers = Object.keys(rows[0]);
    const csvEscape = (val) => {
      const s = String(val ?? "");
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const csvContent = [
      headers.join(","),
      ...rows.map((row) => headers.map((h) => csvEscape(row[h])).join(",")),
    ].join("\n");

    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename || `export_whatnot_${countryCode}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    return rows.length;
  }

  window.exportToWhatnot = exportToWhatnot;
  window.WHATNOT_COUNTRIES = WHATNOT_COUNTRIES;
  window.WhatnotExportInternals = { ValidationError };

})();
