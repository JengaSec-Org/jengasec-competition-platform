"""Institutional-domain recognition (Entry Guide section 6, step 1).

A competitor who registers with an address at a recognised institution is
tied to it automatically. Anybody else -- a personal address, an
institution we have not listed -- is not refused: their account is flagged
for manual verification and an organiser confirms their student status
from the ID document they upload. Keeping the list here rather than in
the database means it ships with the code and is reviewed like code.
"""

# Exact domains, or a parent domain that also covers its subdomains
# (`students.uonbi.ac.ke` is recognised through `uonbi.ac.ke`).
RECOGNISED_DOMAINS = {
    "strathmore.edu": "Strathmore University",
    "uonbi.ac.ke": "University of Nairobi",
    "ku.ac.ke": "Kenyatta University",
    "jkuat.ac.ke": "Jomo Kenyatta University of Agriculture and Technology",
    "kca.ac.ke": "KCA University",
    "usiu.ac.ke": "United States International University - Africa",
    "mku.ac.ke": "Mount Kenya University",
    "dkut.ac.ke": "Dedan Kimathi University of Technology",
    "mmu.ac.ke": "Multimedia University of Kenya",
    "tukenya.ac.ke": "Technical University of Kenya",
    "egerton.ac.ke": "Egerton University",
    "mu.ac.ke": "Moi University",
    "maseno.ac.ke": "Maseno University",
    "cuea.edu": "Catholic University of Eastern Africa",
    "daystar.ac.ke": "Daystar University",
    "zetech.ac.ke": "Zetech University",
    "riara.ac.ke": "Riara University",
    "anu.ac.ke": "Africa Nazarene University",
    "kabarak.ac.ke": "Kabarak University",
    "must.ac.ke": "Meru University of Science and Technology",
}

# Any address under these suffixes is treated as institutional even when
# the institution itself is not named above; the name is then left for
# the person to fill in.
INSTITUTIONAL_SUFFIXES = (".ac.ke", ".edu", ".ac.ug", ".ac.tz", ".ac.rw")


def recognise(email):
    """(is_institutional, institution_name) for an email address.

    `institution_name` is '' when the domain is institutional by suffix
    only, and when the address is not institutional at all.
    """
    domain = (email or "").rsplit("@", 1)[-1].strip().lower()
    if not domain:
        return False, ""
    parts = domain.split(".")
    for i in range(len(parts)):
        candidate = ".".join(parts[i:])
        if candidate in RECOGNISED_DOMAINS:
            return True, RECOGNISED_DOMAINS[candidate]
    if domain.endswith(INSTITUTIONAL_SUFFIXES):
        return True, ""
    return False, ""
