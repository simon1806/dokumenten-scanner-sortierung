"""Fixed, path-free descriptions for document review outcomes."""

REVIEW_REASON_LABELS = {
    "keine_bekannte_dokumentart": "Keine bekannte Dokumentart erkannt",
    "dokumentart_nicht_unterstuetzt": "Dokumentart wird nicht unterstützt",
    "belegnummer_nicht_erkannt": "Dokumentart erkannt, Belegnummer nicht lesbar",
    "lieferant_erkannt_belegart_unbekannt": "Lieferant erkannt, Belegart nicht eindeutig",
    "angebot_ohne_handschrift": "Angebot ohne erkennbare handschriftliche Eintragung",
    "angebot_bestaetigung_nicht_erkannt": "Angebot erkannt, Auftragserteilung nicht lesbar",
    "anlage_ohne_hauptbeleg": "Anlage ohne zugeordneten Hauptbeleg",
    "dokumenttyp_nicht_erkannt": "Dokument konnte nicht zugeordnet werden",
    "legacy_nicht_spezifiziert": "Älteres Protokoll ohne genauen Prüfgrund",
}
