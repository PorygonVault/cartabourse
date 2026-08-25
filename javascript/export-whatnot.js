// ============================================================
// Export Whatnot — CartaBourse
// 4 marchés pour l'instant : NL, DE, FR (+BE), UK — US mis de côté,
// non supporté. Colonnes structurellement identiques partout (22
// colonnes), sous-catégorie/en-têtes/profil de livraison changent par
// pays.
//
// Colonnes volontairement laissées VIDES (confirmé) : Offerable,
// Hazmat, Condition, Cost Per Item, SKU, Image URL 2 à 8.
//
// Le format de vente (Type) est déterminé automatiquement selon le prix
// rempli sur chaque carte : uniquement départ -> "Auction", uniquement
// achat immédiat -> "Buy it Now", les deux ou aucun -> erreur bloquante
// avant tout export (voir determineSaleType/validateItem).
//
// Isolé dans une IIFE, même principe que export-voggt.js — aucun
// risque de collision avec des variables déjà utilisées ailleurs sur
// le site.
// ============================================================
(function () {

  // En-têtes EXACTS relevés dans chaque fichier PDF fourni — l'ordre
  // doit rester identique aux 22 colonnes (Category...Image URL 8),
  // seul le texte affiché change. US/NL partagent le même fichier
  // (confirmé), donc les mêmes en-têtes anglais.
  const WHATNOT_HEADERS = {
    en: ["Category", "Sub Category", "Title", "Description", "Quantity", "Type", "Price",
         "Shipping Profile", "Offerable", "Hazmat", "Condition", "Cost Per Item", "SKU",
         "Image URL 1", "Image URL 2", "Image URL 3", "Image URL 4", "Image URL 5",
         "Image URL 6", "Image URL 7", "Image URL 8"],
    // UK utilise "Subcategory" (pas d'espace) et "Sku" (casse différente) — relevé tel quel dans son fichier
    en_uk: ["Category", "Subcategory", "Title", "Description", "Quantity", "Type", "Price",
         "Shipping Profile", "Offerable", "Hazmat", "Condition", "Cost Per Item", "Sku",
         "Image URL 1", "Image URL 2", "Image URL 3", "Image URL 4", "Image URL 5",
         "Image URL 6", "Image URL 7", "Image URL 8"],
    de: ["Kategorie", "Unterkategorie", "Titel", "Beschreibung", "Menge", "Verkaufsformat", "Preis",
         "Versandprofil", "Angebote annehmen", "Gefahrgut", "Zustand", "Stückpreis", "Artikelnummer",
         "Bild-URL 1", "Bild-URL 2", "Bild-URL 3", "Bild-URL 4", "Bild-URL 5",
         "Bild-URL 6", "Bild-URL 7", "Bild-URL 8"],
    fr: ["Catégorie", "Sous-catégorie", "Titre", "Description", "Quantité", "Type", "Prix",
         "Profil de livraison", "Offres Acceptées", "Matières dangereuses", "État", "Coût par article", "SKU",
         "Image URL 1", "Image URL 2", "Image URL 3", "Image URL 4", "Image URL 5",
         "Image URL 6", "Image URL 7", "Image URL 8"],
  };

  const WHATNOT_COUNTRIES = {
    nl: {
      label: "Pays-Bas (NL)",
      category: "Trading Card Games",
      subcategory: "Pokémon Cards",
      defaultShippingProfile: "0 to <20 grams",
      headers: "en",
    },
    de: {
      label: "Allemagne (DE)",
      category: "Trading Card Games",
      subcategory: "Pokémon-Karten",
      defaultShippingProfile: "Single (15 g)",
      headers: "de",
    },
    fr: {
      label: "France + Belgique (FR/BE)",
      category: "Trading Card Games",
      subcategory: "Cartes Pokémon",
      defaultShippingProfile: "De 0 à <20 grammes",
      headers: "fr",
    },
    uk: {
      label: "Royaume-Uni (UK)",
      category: "Trading Card Games",
      subcategory: "Pokémon Cards",
      defaultShippingProfile: "Single (15g)",
      headers: "en_uk",
    },
  };

  // Le format de vente (colonne Type) dépend du prix réellement rempli :
  //   - Seulement instant_buy_price rempli -> "Buy it Now"
  //   - Seulement starting_price rempli    -> "Auction"
  //   - Les deux ou aucun des deux         -> erreur (voir validateItem)
  // Reste à confirmer avec le support Whatnot que "Buy it Now"/"Auction"
  // sont exactement les valeurs attendues dans cette colonne.

  class ValidationError extends Error {}

  function determineSaleType(item) {
    const hasStarting = item.starting_price != null && item.starting_price !== "";
    const hasInstant = item.instant_buy_price != null && item.instant_buy_price !== "";
    if (hasStarting && hasInstant) return null; // ambigu, erreur gérée par validateItem
    if (hasStarting) return { type: "Auction", price: item.starting_price };
    if (hasInstant) return { type: "Buy it Now", price: item.instant_buy_price };
    return null; // aucun prix, erreur gérée par validateItem
  }

  function validateItem(item, index) {
    const errors = [];
    if (item.quantity == null || item.quantity < 1) errors.push("quantity manquant ou invalide (doit être >= 1)");

    const hasStarting = item.starting_price != null && item.starting_price !== "";
    const hasInstant = item.instant_buy_price != null && item.instant_buy_price !== "";
    if (hasStarting && hasInstant) {
      errors.push("les deux prix (départ ET achat immédiat) sont renseignés — un seul est autorisé pour Whatnot, pour déterminer s'il s'agit d'une enchère ou d'un achat immédiat");
    } else if (!hasStarting && !hasInstant) {
      errors.push("aucun prix renseigné — exactement un des deux (départ OU achat immédiat) est requis pour Whatnot");
    }

    if (errors.length) {
      throw new ValidationError(`Item #${index} (${item.card_name || "?"}) : ` + errors.join(" ; "));
    }
  }

  function buildWhatnotRow(item, nameTemplate, descriptionTemplate, liveDisclaimerOnly, locale, countryConfig, shippingProfileOverride) {
    // Réutilise le même moteur de blocs que Voggt (resolveBlocks/buildDescription
    // exposés par export-voggt.js) — logique identique, aucune duplication.
    const resolveBlocks = window.VoggtExportInternals.resolveBlocks;
    const buildDescription = window.VoggtExportInternals.buildDescription;

    const { type: saleType, price } = determineSaleType(item);

    return [
      countryConfig.category,
      countryConfig.subcategory,
      resolveBlocks(nameTemplate, item),
      buildDescription(descriptionTemplate, item, liveDisclaimerOnly, locale),
      item.quantity,
      saleType,
      price,
      shippingProfileOverride || countryConfig.defaultShippingProfile,
      "", "", "", "", "", // Offerable, Hazmat, Condition, Cost Per Item, SKU
      item.image_url || "",
      "", "", "", "", "", "", "", // Image URL 2 à 8
    ];
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

    const headers = WHATNOT_HEADERS[countryConfig.headers];
    const csvEscape = (val) => {
      const s = String(val ?? "");
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const csvContent = [
      headers.join(","),
      ...rows.map((row) => row.map(csvEscape).join(",")),
    ].join("\n");

    // Même correctif que Voggt : le BOM UTF-8 évite que certains tableurs
    // déforment les accents/emoji en devinant le mauvais encodage.
    const blob = new Blob(["\uFEFF" + csvContent], { type: "text/csv;charset=utf-8;" });
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
