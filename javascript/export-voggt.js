// ============================================================
// Moteur de blocs + export Voggt — version JavaScript
// CartaBourse
//
// Portage direct de moteur_blocs_listing.py + export_voggt.py — même
// logique, même comportement, testé pour donner des résultats
// identiques. Pensé pour tourner directement dans le navigateur
// (GitHub Pages ne sait exécuter que du JS, jamais du Python).
// ============================================================

// Tout le code ci-dessous vit dans son propre espace isolé (IIFE) — aucun
// risque de collision avec des variables de même nom déjà utilisées
// ailleurs sur le site (ex. index.html a déjà ses propres LANGUAGE_NAMES
// et LANGUAGE_FLAGS pour un autre usage). Seule "exportToVoggt" est
// exposée publiquement, via window.exportToVoggt, pour être appelée
// depuis le reste du site.
(function() {
  const LANGUAGE_NAMES = {
    en: "Anglais", fr: "Français", de: "Allemand", nl: "Néerlandais",
    it: "Italien", es: "Espagnol", pt: "Portugais", ru: "Russe", pl: "Polonais",
    ja: "Japonais", zh: "Chinois", ko: "Coréen", id: "Indonésien", th: "Thaïlandais",
    cs: "Tchèque", hu: "Hongrois",
  };

  const LANGUAGE_FLAGS = {
    en: "🇬🇧", fr: "🇫🇷", de: "🇩🇪", nl: "🇳🇱", it: "🇮🇹", es: "🇪🇸",
    pt: "🇵🇹", ru: "🇷🇺", pl: "🇵🇱", ja: "🇯🇵", zh: "🇨🇳", ko: "🇰🇷",
    id: "🇮🇩", th: "🇹🇭", cs: "🇨🇿", hu: "🇭🇺",
  };

  // Échelle d'état standard — à ajuster ici si besoin, un seul endroit à changer
  const CONDITION_NAMES = {
    MT: "Mint", NM: "Near Mint", EX: "Excellent", GD: "Good",
    LP: "Light Played", PL: "Played", PO: "Poor",
  };

  const LIVE_CONDITION_DISCLAIMER = "État montré en live / Condition showed during live";

  /** '58' + 102 -> '058/102' — zéros de tête alignés sur le nombre de
   * chiffres du total. Affichage uniquement, ne touche jamais la valeur stockée. */
  function formatNumberComplete(cardNumber, totalCards) {
    if (!totalCards) return String(cardNumber);
    const digits = String(totalCards).length;
    const match = String(cardNumber).match(/^(\d+)(.*)$/);
    if (!match) return String(cardNumber);
    const [, numPart, suffix] = match;
    return `${numPart.padStart(digits, "0")}${suffix}/${totalCards}`;
  }

  /** context attendu (toutes les clés optionnelles) :
   *   card_name, card_number, total_cards, set_name, set_abbreviation, set_code,
   *   condition (ex. "NM") OU grading_company + grade (jamais les deux),
   *   language, price
   */
  function resolveBlocks(template, context) {
    const values = {};

    values.card_name = context.card_name;
    values.card_number = context.card_number;
    values.number = context.card_number; // alias, même valeur
    if (context.card_number != null) {
      values.number_complete = formatNumberComplete(context.card_number, context.total_cards);
    }
    values.set_name = context.set_name;
    values.set_abbreviation = context.set_abbreviation;
    values.set_code = context.set_code;

    if (context.condition) {
      values.condition = context.condition;
      values.condition_full = CONDITION_NAMES[context.condition.toUpperCase()] || context.condition;
    }

    if (context.grading_company && context.grade) {
      values.grading_company = context.grading_company;
      values.grade = context.grade;
      values.grading_full = `${context.grading_company} ${context.grade}`;
    }

    if (context.language) {
      values.language_name = LANGUAGE_NAMES[context.language] || context.language;
      values.flag = LANGUAGE_FLAGS[context.language] || "";
    }
    if (context.price != null) values.price = context.price;

    let result = template.replace(/\[(\w+)\]/g, (_, key) => {
      const val = values[key];
      return val != null && val !== "" ? String(val) : "";
    });

    result = result.replace(/\s{2,}/g, " ").trim();
    result = result.replace(/^[-:]\s*|\s*[-:]$/g, "").trim();
    return result;
  }

  /** Comme resolveBlocks, mais ajoute par défaut la mention indiquant que
   * l'état réel est montré en direct — désactivable via liveDisclaimerOnly=false — true remplace entièrement la description par la mention. */
  function buildDescription(template, context, liveDisclaimerOnly = false) {
    // Un seul des deux est utilisé, jamais les deux ensemble : soit la
    // mention par défaut seule, soit le gabarit personnalisé seul.
    if (liveDisclaimerOnly) return LIVE_CONDITION_DISCLAIMER;
    return resolveBlocks(template, context);
  }

  class ValidationError extends Error {}

  function validateItem(item, index) {
    const errors = [];

    const hasCondition = !!item.condition;
    const hasGrading = !!item.grading_company && !!item.grade;
    if (hasCondition && hasGrading) errors.push("condition ET grading renseignés en même temps — un seul des deux attendu");
    if (!hasCondition && !hasGrading) errors.push("ni condition ni grading renseigné");

    const { starting_price: starting, instant_buy_price: instant } = item;
    if (starting == null && instant == null) errors.push("aucun prix renseigné (starting_price et/ou instant_buy_price)");
    if (starting != null && instant != null && starting >= instant) {
      errors.push(`starting_price (${starting}) doit être strictement inférieur à instant_buy_price (${instant})`);
    }

    if (item.quantity == null || item.quantity < 1) errors.push("quantity manquant ou invalide (doit être >= 1)");

    if (errors.length) {
      throw new ValidationError(`Item #${index} (${item.card_name || "?"}) : ${errors.join(" ; ")}`);
    }
  }

  function buildVoggtRow(item, nameTemplate, descriptionTemplate, liveDisclaimerOnly = false) {
    return {
      name: resolveBlocks(nameTemplate, item),
      description: buildDescription(descriptionTemplate, item, liveDisclaimerOnly),
      quantity: item.quantity,
      startingPrice: item.starting_price != null ? item.starting_price : "",
      instantBuyPrice: item.instant_buy_price != null ? item.instant_buy_price : "",
      imagesUrls: item.image_url || "",
    };
  }

  /** Génère le CSV Voggt et déclenche le téléchargement dans le navigateur.
   * Valide TOUS les items avant d'écrire quoi que ce soit — un seul item
   * invalide bloque tout l'export plutôt que de produire un fichier à
   * moitié correct. */
  function exportToVoggt(items, nameTemplate, descriptionTemplate, filename = "export_voggt.csv", liveDisclaimerOnly = false) {
    items.forEach((item, i) => validateItem(item, i + 1));

    const rows = items.map((item) => buildVoggtRow(item, nameTemplate, descriptionTemplate, liveDisclaimerOnly));

    const headers = ["name", "description", "quantity", "startingPrice", "instantBuyPrice", "imagesUrls"];
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
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    return rows.length;
  }

  window.exportToVoggt = exportToVoggt;
  window.VoggtExportInternals = { resolveBlocks, buildDescription, ValidationError }; // exposé au cas où, usage optionnel
})();
