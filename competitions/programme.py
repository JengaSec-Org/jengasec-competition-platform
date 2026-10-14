"""The three-day programme, as published.

Data rather than markup, for the same reason the FAQ is: a running order
changes on the day. A session overruns, a speaker is delayed, the Morani
window shifts — and whoever fixes it should be editing one list, at
07:30, on a phone, not hunting through a template.

Source: "JengaSec 2026: Programme" (13 October 2026). Times are EAT and
are the published ones; where the sheet left a gap between a session and
the next buffer, the gap is kept rather than tidied away, because the
sheet is what the room runs to.

`kind` drives only the colour of the label. The four that matter to
competitors — Morani, Kikao, Document and Close — are called out; the
rest are ordinary.
"""

# kind -> how it is labelled on the page
KINDS = {
    "arrival": "Arrival",
    "protocols": "Protocols",
    "talk": "Talk",
    "buffer": "Buffer",
    "break": "Break",
    "lunch": "Lunch",
    "morani": "Morani",
    "kikao": "Kikao",
    "lecture": "Lecture",
    "document": "Document",
    "close": "Close",
}

# Kinds a competitor must not miss: highlighted on the page.
KEY_KINDS = {"morani", "kikao", "document", "close"}

# (day number, title, date label, [(start, end, kind, session, who), ...])
DAYS = [
    (
        1,
        "Build It Right",
        "Wednesday 14 October",
        [
            ("08:00", "08:50", "arrival", "Check-in", ""),
            ("08:50", "09:00", "protocols", "Opening protocols", "Associate Dean"),
            ("09:00", "09:20", "talk", "Keynote: NCBA", "Amon Ngechu, CTO, NCBA"),
            ("09:30", "09:40", "buffer", "Buffer", ""),
            (
                "10:00", "10:40", "talk",
                "Build It Right: Why Security Has to Start From Day One",
                "Frederick Wahome, CEO, KCFSA",
            ),
            ("10:40", "11:00", "buffer", "Buffer", ""),
            ("11:00", "11:30", "break", "Break", ""),
            ("11:30", "13:00", "morani", "Morani Bug Bounty", ""),
            ("13:00", "14:00", "lunch", "Lunch", ""),
            (
                "14:00", "15:00", "kikao",
                "Kikao 1 — Licence to Launch: Should No Kenyan FinTech Go Live "
                "Without a Security Review?",
                "Joseph Mwamba, Principal Engineer, Cybersecurity · "
                "Arnold Arinda, Technical Support Engineer",
            ),
            ("15:00", "15:20", "buffer", "Buffer", ""),
            (
                "15:20", "16:00", "talk",
                "Inside the Breach: How Kenya's Real Cyber Incidents Actually "
                "Happened, and How to Begin Your Journey as a Defender",
                "Kiprono Rugut, CIO, Adili",
            ),
        ],
    ),
    (
        2,
        "Inside the Breach",
        "Thursday 15 October",
        [
            ("08:30", "09:00", "arrival", "Doors open", ""),
            (
                "09:00", "09:40", "talk",
                "Anatomy of a Kenyan Breach: From First Intrusion to Final Report, "
                "and the Warning Signs Defenders Missed",
                "David, Raha Mawazo",
            ),
            ("09:40", "10:00", "buffer", "Buffer", ""),
            (
                "10:00", "10:40", "talk",
                "Your Data, Their Cloud: Is Kenya Ready to Protect What It Is "
                "Moving Online?",
                "Michael Juma, Head of Technology Security, NCBA",
            ),
            ("10:40", "11:00", "buffer", "Buffer", ""),
            ("11:00", "11:30", "break", "Break", ""),
            (
                "11:30", "13:00", "morani", "Morani Bug Bounty",
                "Recruiter floor walk from 12:00",
            ),
            ("13:00", "14:00", "lunch", "Lunch", ""),
            (
                "14:00", "15:30", "kikao",
                "Kikao 2 — AI in Cybersecurity: Opportunities, Challenges and the "
                "Future of Digital Resilience",
                "Czech Delegation",
            ),
            ("15:30", "16:00", "break", "Coffee break", ""),
            (
                "16:00", "16:40", "lecture",
                "Expert lecture for students", "A Czech expert (CyberVAC project)",
            ),
        ],
    ),
    (
        3,
        "What Comes Next",
        "Friday 16 October",
        [
            ("08:30", "09:00", "arrival", "Doors open", ""),
            (
                "09:00", "09:40", "talk", "Building a Security Career in Kenya",
                "Irene Njoroge, Senior Manager, Equity",
            ),
            ("09:40", "10:00", "buffer", "Buffer", ""),
            (
                "10:00", "10:40", "talk",
                "Made in Kenya, Defending Kenya: What It Takes to Build a "
                "Homegrown Cybersecurity Business",
                "Ben Obinju, CEO, Kristel Africa",
            ),
            ("10:40", "11:00", "buffer", "Buffer", ""),
            ("11:00", "11:30", "break", "Break", ""),
            (
                "11:30", "13:00", "document", "Documentation window",
                "Submissions close 12:30",
            ),
            ("13:00", "14:00", "lunch", "Lunch", "Judges close scoring"),
            (
                "14:00", "15:15", "kikao",
                "Kikao 3 — Tools or Talent? What Is Kenya's Bigger "
                "Cybersecurity Gap?",
                "Raha Mawazo",
            ),
            ("15:15", "15:40", "kikao", "Winners announced", ""),
            ("15:40", "16:00", "kikao", "Open Q&A", ""),
            ("16:00", "16:30", "close", "Close and recruiter mixer", ""),
        ],
    ),
]


def days():
    """The programme, shaped for the template."""
    out = []
    for number, title, date_label, rows in DAYS:
        out.append({
            "number": number,
            "title": title,
            "date": date_label,
            "sessions": [
                {
                    "start": start,
                    "end": end,
                    "kind": kind,
                    "kind_label": KINDS.get(kind, kind.title()),
                    "session": session,
                    "who": who,
                    "key": kind in KEY_KINDS,
                }
                for start, end, kind, session, who in rows
            ],
        })
    return out


def deadlines():
    """The moments a competitor loses marks by missing."""
    return [
        ("Fri 16 Oct · 12:30", "Submissions close"),
        ("Fri 16 Oct · 14:00", "Judges close scoring"),
        ("Fri 16 Oct · 15:15", "Winners announced"),
    ]
