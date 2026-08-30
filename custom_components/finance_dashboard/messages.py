"""User-facing backend messages, localized via ``hass.config.language``.

API views hand these strings straight to the panel, so they must follow the
user's HA language instead of being hardcoded in one locale.  Keys missing
for a language fall back to English; unknown languages fall back to English
as a whole.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

DEFAULT_LANGUAGE = "en"

MESSAGES: dict[str, dict[str, str]] = {
    "auth.https_required": {
        "de": (
            "Bank-Autorisierung erfordert HTTPS. Aktuelle Callback-URL "
            "'{callback_url}' ist HTTP — öffne das Finance-Panel über die "
            "HTTPS-URL deiner HA-Instanz (z. B. Nabu Casa) oder richte ein "
            "TLS-Zertifikat ein."
        ),
        "en": (
            "Bank authorization requires HTTPS. The current callback URL "
            "'{callback_url}' is HTTP — open the Finance panel through your "
            "instance's HTTPS URL (e.g. Nabu Casa) or set up a TLS certificate."
        ),
        "it": (
            "L'autorizzazione bancaria richiede HTTPS. La callback URL attuale "
            "'{callback_url}' è in HTTP — apri il pannello Finance dall'URL HTTPS "
            "della tua istanza (per es. Nabu Casa) oppure configura un "
            "certificato TLS."
        ),
    },
    "auth.redirect_not_registered": {
        "de": (
            "Redirect URL nicht registriert — die Callback-URL "
            "'{callback_url}' ist nicht bei Enable Banking hinterlegt."
        ),
        "en": (
            "Redirect URL not registered — the callback URL '{callback_url}' "
            "is not on file with Enable Banking."
        ),
        "it": (
            "Redirect URL non registrata — la callback URL '{callback_url}' "
            "non risulta configurata su Enable Banking."
        ),
    },
    "setup.rate_limited": {
        "de": "Bank-API tageslimit erreicht — bitte morgen erneut versuchen",
        "en": "Daily bank API limit reached — please try again tomorrow",
        "it": "Limite giornaliero delle API bancarie raggiunto — riprova domani",
    },
    "callback.no_accounts": {
        "de": "Bank hat keine Konten zurückgegeben. Bitte Bankvertrag/Konsent prüfen.",
        "en": "The bank returned no accounts. Check your bank contract/consent.",
        "it": "La banca non ha restituito conti. Controlla il contratto o il consenso.",
    },
    "callback.title": {
        "de": "Bankverbindung erfolgreich",
        "en": "Bank connection successful",
        "it": "Collegamento bancario riuscito",
    },
    "callback.body": {
        "de": (
            "Dein Bankkonto wurde autorisiert.<br>Du kannst diesen Tab "
            "schlie&szlig;en und zum Finance zur&uuml;ckkehren."
        ),
        "en": (
            "Your bank account has been authorized.<br>You can close this tab "
            "and return to Finance."
        ),
        "it": (
            "Il tuo conto bancario è stato autorizzato.<br>Puoi chiudere questa "
            "scheda e tornare a Finance."
        ),
    },
    "ratelimit.until": {
        "de": "API rate-limited bis {until} — bitte morgen erneut versuchen.",
        "en": "API rate-limited until {until} — please try again tomorrow.",
        "it": "API in limite di richieste fino alle {until} — riprova domani.",
    },
    "refresh.no_credentials": {
        "de": "Keine Enable-Banking-Credentials hinterlegt",
        "en": "No Enable Banking credentials stored",
        "it": "Nessuna credenziale Enable Banking salvata",
    },
    "refresh.rate_limited": {
        "de": "Tageslimit der Bank-API erreicht (4/Tag pro Konto)",
        "en": "Daily bank API limit reached (4/day per account)",
        "it": "Limite giornaliero delle API bancarie raggiunto (4 al giorno per conto)",
    },
    "refresh.rate_limited_balance": {
        "de": "Rate-Limit beim Saldo für {account}",
        "en": "Rate limit hit while fetching the balance for {account}",
        "it": "Limite di richieste raggiunto sul saldo di {account}",
    },
    "setup.complete_notification": {
        "de": "Öffne das Finance-Panel in der Sidebar, um deine erste Bank zu verbinden.",
        "en": "Open the Finance panel in the sidebar to connect your first bank.",
        "it": "Apri il pannello Finance nella barra laterale per collegare la prima banca.",
    },
    "setup.complete_notification_title": {
        "de": "Finance Dashboard eingerichtet",
        "en": "Finance Dashboard set up",
        "it": "Finance Dashboard configurato",
    },
}


def msg(hass: HomeAssistant | None, key: str, **kwargs: Any) -> str:
    """Return ``key`` rendered in the user's HA language.

    Unknown keys return the key itself so a missing entry is visible rather
    than silently empty.
    """
    entry = MESSAGES.get(key)
    if entry is None:
        return key
    lang = DEFAULT_LANGUAGE
    if hass is not None:
        lang = (hass.config.language or DEFAULT_LANGUAGE).lower().split("-")[0]
    text = entry.get(lang) or entry[DEFAULT_LANGUAGE]
    return text.format(**kwargs) if kwargs else text
