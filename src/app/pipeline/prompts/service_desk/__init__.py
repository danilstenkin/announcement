from pipeline.prompts.service_desk.forteSpaceBPM import PROMPT_BPM
from pipeline.prompts.service_desk.compass import PROMPT_COMPASS
from pipeline.prompts.service_desk.mib import PROMPT_MIB

import re

_SYSTEM_PROMT = [
    ("ForteSpace BPM", PROMPT_BPM),
    ("compass", PROMPT_COMPASS),
    ("Мобильный Интернет Банкинг Физических лиц 3.0", PROMPT_MIB)
]

def extract_system(text: str) -> str | None:
    match = re.search(r"Система:\s*\n?\s*(.+?)(?:\n|$)", text)
    system = match.group(1).strip() if match else None
    return system

def select_prompt(email_body: str) -> str:
    system = extract_system(email_body)
    print("SYSTEEEM====", system)
    if system:
        system_lower = system.lower()
        for keyword, prompt in _SYSTEM_PROMT:
            if keyword.lower() in system_lower:
                return prompt

    return "верни просто промт не найден для этого анонса"
