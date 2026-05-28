from pipeline.prompts.service_desk.forteSpaceBPM import PROMPT_BPM
from pipeline.prompts.service_desk.compass import PROMPT_COMPASS
from pipeline.prompts.service_desk.mib import PROMPT_MIB
from pipeline.prompts.service_desk.colvir import PROMPT_COLVIR
from pipeline.prompts.service_desk.crm import PROMPT_CRM
from pipeline.prompts.service_desk.pos import PROMPT_POS
from pipeline.prompts.service_desk.fortelink import PROMPT_FORTELINK
from pipeline.prompts.service_desk.default import PROMPT_DEFAULT
from pipeline.prompts.service_desk.emergency import PROMPT_EMERGENCY
from pipeline.prompts.komek.base import PROMPT_KOMEK

import re

_SYSTEM_PROMT = [
    # BPM
    ("ForteSpace BPM", PROMPT_BPM),
    ("BPM", PROMPT_BPM),
    # Compass
    ("compass", PROMPT_COMPASS),
    ("компас", PROMPT_COMPASS),
    # МИБ
    ("Мобильный Интернет Банкинг Физических лиц 3.0", PROMPT_MIB),
    ("Мобильный Интернет Банкинг", PROMPT_MIB),
    ("МИБ", PROMPT_MIB),
    # Colvir
    ("абис", PROMPT_COLVIR),
    ("colvir", PROMPT_COLVIR),
    # CRM
    ("MS Dynamic CRM", PROMPT_CRM),
    ("MS Dynamics CRM", PROMPT_CRM),
    ("CRM", PROMPT_CRM),
    # POS
    ("POS Терминалы", PROMPT_POS),
    ("POS-терминал", PROMPT_POS),
    ("POS", PROMPT_POS),
    # ForteLink
    ("ForteLink", PROMPT_FORTELINK),
    ("Forte Link", PROMPT_FORTELINK),
]

_NEXT_FIELD = re.compile(
    r"\n\s*(?:Описание работ|Начало работ|Завершение работ|Влияние|Тип работ|С уважением|Service Desk|Дата)",
    re.IGNORECASE,
)

def extract_system(text: str) -> str | None:
    match = re.search(r"Система:\s*(.+?)(?=" + _NEXT_FIELD.pattern + r"|$)", text, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    system = " ".join(match.group(1).split())
    return system or None

_SERVICE_DESK_SENDERS = {
    "sd_info@Fortebank.com",
    "AAAskarova@Fortebank.com",
    "DAStenkin@Fortebank.com",
}

def _is_emergency(text: str) -> bool:
    return bool(re.search(r"аварийно[- ]?восстановительн", text, re.IGNORECASE))


def select_prompt(email_body: str, email_from: str) -> tuple[str, str]:
    if email_from in _SERVICE_DESK_SENDERS:
        if _is_emergency(email_body):
            return PROMPT_EMERGENCY, "service_desk"
        system = extract_system(email_body)
        if system:
            system_lower = system.lower()
            for keyword, prompt in _SYSTEM_PROMT:
                if keyword.lower() in system_lower:
                    return prompt, "service_desk"
        return PROMPT_DEFAULT, "service_desk_default"

    elif email_from == "komek@Fortebank.com":
        return PROMPT_KOMEK, "komek"

    return PROMPT_DEFAULT, "default"
