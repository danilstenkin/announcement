from pipeline.prompts.service_desk.forteSpaceBPM import PROMPT_BPM
from pipeline.prompts.service_desk.compass import PROMPT_COMPASS
from pipeline.prompts.service_desk.mib import PROMPT_MIB
from pipeline.prompts.service_desk.colvir import PROMPT_COLVIR
from pipeline.prompts.service_desk.default import PROMPT_DEFAULT
from pipeline.prompts.komek.base import PROMPT_KOMEK

import re

_SYSTEM_PROMT = [
    ("ForteSpace BPM", PROMPT_BPM),
    ("compass", PROMPT_COMPASS),
    ("Мобильный Интернет Банкинг Физических лиц 3.0", PROMPT_MIB),
    ("абис", PROMPT_COLVIR),

]

def extract_system(text: str) -> str | None:
    match = re.search(r"Система:\s*\n?\s*(.+?)(?:\n|$)", text)
    system = match.group(1).strip() if match else None
    return system

def select_prompt(email_body: str, email_from: str) -> tuple[str, str]:
    if email_from == "DAStenkin@Fortebank.com":  # Service Desk
        system = extract_system(email_body)
        if system:
            system_lower = system.lower()
            for keyword, prompt in _SYSTEM_PROMT:
                if keyword.lower() in system_lower:
                    return prompt, "service_desk"
        return PROMPT_DEFAULT, "service_desk_default"

    # elif email_from == "komek@Fortebank.com":
    #     return PROMPT_KOMEK, "komek"

    return PROMPT_DEFAULT, "default"
