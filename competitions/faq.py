"""The participant FAQ, as data.

Taken from "JengaSec 2026 Participant FAQ" v1.0 (10 October 2026). Kept
as structured data rather than markup so the wording can be reviewed and
corrected without touching a template, and so the same content can feed
the public page and the in-dashboard panel.

Deadlines are deliberately *not* written here. They come from
`CompetitionSettings` through `competition_service.key_dates()`, because
a date copied into prose is a date that goes stale -- which is how the
dashboards came to advertise "1 October 2026" five days after it passed.

Each section is (anchor, title, [(question, answer), ...]).
"""

SECTIONS = [
    ("about", "About JengaSec", [
        (
            "What is JengaSec?",
            "Kenya's first full-spectrum enterprise cybersecurity simulation, run by "
            "the Strathmore University Cybersecurity Club. Student teams build the "
            "software for a fictional bank, JengaBank. Other teams attack it, while "
            "AI agents defend and are attacked in turn. The line is: CTFs develop "
            "tools, JengaSec develops judgment.",
        ),
        (
            "When and where?",
            "14 to 16 October 2026, in person at Strathmore University, Nairobi. "
            "Each day opens with two keynotes (Maarifa). The event closes with "
            "Kikao, where executives review the attacks and defences and winners "
            "are announced.",
        ),
        (
            "Does it cost anything?",
            "No. Entry is free for every participant and every track.",
        ),
    ]),

    ("teams", "Who can enter, and team rules", [
        (
            "Who can take part?",
            "Students aged 18 to 25 on the registration date. The skill level is "
            "medium to advanced: you should be comfortable working on real systems, "
            "whether building or attacking them.",
        ),
        (
            "How big is a team?",
            "Exactly four people, with at least one security specialist. A team that "
            "is a seat short cannot submit its registration.",
        ),
        (
            "I do not have a team. What do I do?",
            "Say which side you want to play on the Find a Team page. Captains with "
            "a free seat can then see you and send an invitation. You can also "
            "create your own team and invite three others.",
        ),
        (
            "Can I change my team's institution or track?",
            "Yes. The captain can change the team name, lead institution, side, "
            "specialisation and chosen application from Edit Team Details, any time "
            "before the registration is submitted for review.",
        ),
    ]),

    ("tracks", "The four tracks", [
        (
            "Application Blue",
            "Builds one of the JengaBank applications against a published brief, "
            "and defends it during the event. Submits a proposal.",
        ),
        (
            "AI Defence Blue",
            "Builds a defensive AI agent that monitors and protects the range. "
            "Submits a proposal.",
        ),
        (
            "AI Red",
            "Attacks defensive agents: probing them, manipulating their inputs and "
            "testing the limits of their authorisation. Submits a proposal.",
        ),
        (
            "Application Red (Morani Hunting Grounds)",
            "Attacks the applications Blue teams built, as a bug bounty hunt across "
            "the range. Files no proposal: places are earned through the Hack The "
            "Box qualifier, and qualifying teams are sent a registration link.",
        ),
    ]),

    ("proposals", "Proposals and submissions", [
        (
            "What format must a proposal be?",
            "PDF only, with selectable text -- no scans, passwords or encryption -- "
            "and no larger than 25 MB. Blue proposals are 12 to 20 pages, AI Red 8 "
            "to 14. Cover, contents, references and appendices do not count, and "
            "required content must not be moved into appendices. A4, margins at "
            "least 2 cm, body text at least 11 pt, line spacing at least 1.15, in "
            "English.",
        ),
        (
            "What should the file be called?",
            "JS26_<ENTERPRISE>_<CELL>_<TEAMID>_<TYPE>_v<N>.pdf -- for example "
            "JS26_ENTA_APP03_JS26-B-014_Proposal_v2.pdf. Increase the version "
            "number with every upload.",
        ),
        (
            "What are the penalties?",
            "Late files are not accepted. Going over the page limit costs 5% and the "
            "extra pages are not read. A wrong file name costs 2%. A missing section "
            "scores zero on that criterion.",
        ),
        (
            "Can we upload more than once?",
            "Yes, as many times as you like before the deadline. Only the latest "
            "version counts, so upload a complete draft early and improve it.",
        ),
        (
            "Can we use AI to write the proposal?",
            "Partly. You may use AI to edit your own text, to draw diagrams from "
            "your own content, and for research you have checked yourself -- as long "
            "as you declare it in the AI use statement. You may not have AI generate "
            "your threat model, architecture, security design or attack methodology, "
            "and you may not cite sources nobody on the team has read. A false AI use "
            "statement can cost up to 20% of the score.",
        ),
        (
            "What if I miss a deadline?",
            "Late submissions are not accepted, and no organiser can accept a file "
            "through another channel. If the platform itself fails while you upload, "
            "the captain should email the organisers within 30 minutes from their "
            "registered address, attaching the file and the error. Your own internet "
            "connection failing is not a platform fault.",
        ),
    ]),

    ("judging", "Judging, feedback and appeals", [
        (
            "How are proposals judged?",
            "Against the published weights, by judges assisted by AI. Selection is "
            "competitive.",
        ),
        (
            "Do we get feedback?",
            "Yes. Every team that submits receives a written feedback report with "
            "criterion scores, judges' comments and the evidence cited, whether or "
            "not it is selected.",
        ),
        (
            "Can we appeal?",
            "The captain may appeal within 72 hours of results, on procedural "
            "grounds only -- a scoring error, or a section the platform missed. A "
            "second judge reviews it and that decision is final.",
        ),
        (
            "Can we talk to a judge about our submission?",
            "No. Contacting a judge about your submission earns a formal warning, "
            "and a repeat means disqualification. Send questions through the "
            "organisers' channels; answers that matter to everyone are shared with "
            "all teams.",
        ),
    ]),

    ("event", "At the event", [
        (
            "What should I bring?",
            "Your own laptop, charger and identity document. All teams use their own "
            "machines -- JengaSec does not provide toolboxes or attack machines. "
            "Every team connects to the competition network through a team VPN "
            "profile issued by JengaSec.",
        ),
        (
            "What is the welcome pack?",
            "Once your team is approved it gives you your team identifier, your "
            "assigned brief or target, your VPN credentials, the full rules of "
            "engagement and scoring tables, and your deadlines. Read your brief "
            "first.",
        ),
        (
            "Can we share the briefs or publish our code?",
            "No. Briefs, specifications, seed data and range materials are for the "
            "competition only. Keep repositories and images private until the "
            "competition is over.",
        ),
    ]),

    ("data", "Your personal data", [
        (
            "What happens to my personal data?",
            "Strathmore University holds it under its privacy policy and Kenya's "
            "Data Protection Act, 2019. It is used to check eligibility, form teams, "
            "admit you to the venue and judge your work. Organisers and judges can "
            "see it; it is not published or shared with partners.",
        ),
        (
            "How do I see, correct or object to what you hold?",
            "Write to the university's Data Protection Officer at "
            "dpo@strathmore.edu. Section 7 of the code of conduct on this platform "
            "is the full notice.",
        ),
    ]),

    ("contact", "Contact", [
        (
            "How do I reach the organisers?",
            "Email sucybersec@strathmore.edu. Where this page and the organisers' "
            "own announcements differ, the announcement on your dashboard is the "
            "current one.",
        ),
    ]),
]
