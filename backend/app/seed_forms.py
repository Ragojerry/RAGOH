"""The three pilot XLSForms, built in code so they live in version control as text.

Each function returns XLSForm workbook bytes, which go through the same compiler as
any uploaded form. Download them from the API to edit in Excel and re-upload.
"""

import io

from openpyxl import Workbook

SURVEY_HEADER = ["type", "name", "label", "hint", "required", "relevant", "constraint", "constraint_message", "calculation", "appearance"]  # fmt: skip


def _workbook(
    survey: list[list[str]], choices: list[list[str]], form_id: str, title: str, version: str
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "survey"
    ws.append(SURVEY_HEADER)
    for row in survey:
        ws.append(row + [""] * (len(SURVEY_HEADER) - len(row)))
    cs = wb.create_sheet("choices")
    cs.append(["list_name", "name", "label"])
    for row in choices:
        cs.append(row)
    st = wb.create_sheet("settings")
    st.append(["form_title", "form_id", "version"])
    st.append([title, form_id, version])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


YES_NO = [["yn", "yes", "Yes"], ["yn", "no", "No"]]


def household_survey() -> bytes:
    survey = [
        ["note", "intro", "Introduce yourself and explain the purpose of the visit."],
        ["select_one yn", "consent", "Does the household agree to take part?", "", "yes"],
        ["text", "respondent_name", "Respondent's name", "", "yes", "${consent} = 'yes'"],
        ["select_one sex", "respondent_sex", "Respondent's sex", "", "yes", "${consent} = 'yes'"],
        [
            "integer",
            "hh_size",
            "How many people live in this household?",
            "",
            "yes",
            "${consent} = 'yes'",
            ". >= 1 and . <= 40",
            "Enter a number between 1 and 40",
        ],
        [
            "integer",
            "under5",
            "How many are children under 5?",
            "",
            "yes",
            "${consent} = 'yes'",
            ". >= 0 and . <= ${hh_size}",
            "Cannot be more than the household size",
        ],
        [
            "select_one yn",
            "has_net",
            "Does the household own a mosquito net?",
            "",
            "yes",
            "${consent} = 'yes'",
        ],
        [
            "integer",
            "nets",
            "How many nets?",
            "",
            "yes",
            "${has_net} = 'yes'",
            ". >= 1 and . <= 20",
        ],
        [
            "select_multiple illness",
            "illness",
            "Illness in the last 2 weeks (select all)",
            "",
            "",
            "${consent} = 'yes'",
        ],
        [
            "calculate",
            "pct_under5",
            "",
            "",
            "",
            "",
            "",
            "",
            "if(${hh_size} > 0, round(${under5} div ${hh_size} * 100, 1), 0)",
        ],
        [
            "note",
            "summary",
            "Children under 5: ${under5} of ${hh_size} (${pct_under5}%)",
            "",
            "",
            "${consent} = 'yes'",
        ],
        ["text", "refusal_reason", "Reason for refusal", "", "", "${consent} = 'no'"],
        [
            "image",
            "photo",
            "Photo of the house (optional)",
            "Do not photograph people",
            "",
            "${consent} = 'yes'",
        ],
        ["text", "comments", "Comments", "", "", "", "", "", "", "multiline"],
    ]
    choices = YES_NO + [
        ["sex", "female", "Female"], ["sex", "male", "Male"],
        ["illness", "fever", "Fever"], ["illness", "cough", "Cough"],
        ["illness", "diarrhoea", "Diarrhoea"], ["illness", "none", "None"],
    ]  # fmt: skip
    return _workbook(survey, choices, "ragoh_household_survey", "Household survey", "2026112601")


def supervision_checklist() -> bytes:
    survey = [
        ["text", "collector_username", "Collector's username", "", "yes"],
        ["select_one yn", "on_time", "Did the collector start on time?", "", "yes"],
        ["select_one yn", "id_badge", "Is the collector wearing an ID badge?", "", "yes"],
        ["select_one yn", "consent_ok", "Did the collector obtain consent properly?", "", "yes"],
        ["select_one score", "interview_quality", "Quality of the interview observed", "", "yes"],
        ["select_one yn", "gps_on", "Is the phone's GPS/location switched on?", "", "yes"],
        ["integer", "battery", "Phone battery level (%)", "", "", "", ". >= 0 and . <= 100"],
        ["text", "issues", "Issues found and actions taken", "", "", "", "", "", "", "multiline"],
    ]
    choices = YES_NO + [
        ["score", "good", "Good"],
        ["score", "fair", "Fair"],
        ["score", "poor", "Poor"],
    ]
    return _workbook(
        survey, choices, "ragoh_supervision_checklist", "Supervision checklist", "2026112601"
    )


def backcheck() -> bytes:
    survey = [
        [
            "select_one yn",
            "visited",
            "Did a data collector visit this household recently?",
            "",
            "yes",
        ],
        [
            "integer",
            "hh_size",
            "How many people live in this household?",
            "",
            "yes",
            "${visited} = 'yes'",
            ". >= 1 and . <= 40",
        ],
        [
            "select_one yn",
            "has_net",
            "Does the household own a mosquito net?",
            "",
            "yes",
            "${visited} = 'yes'",
        ],
        ["text", "notes", "Notes", "", "", "", "", "", "", "multiline"],
    ]
    return _workbook(survey, YES_NO, "ragoh_backcheck", "Back-check", "2026112601")


SEED_FORMS = [
    ("survey", household_survey),
    ("supervision", supervision_checklist),
    ("backcheck", backcheck),
]
