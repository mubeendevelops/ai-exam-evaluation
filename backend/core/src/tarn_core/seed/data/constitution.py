"""QP-CI: Constitution of India and Human Rights (19AU0003, BCom ACCA, Sem II, June 2021).

The paper has no key, so every reference answer and rubric below is SYNTHETIC - dev only
(requirements "Development data plan"): written for development, to be corrected by a teacher
before any accuracy test uses it. Question 12 prints no marks in the paper; it is 5 here, like
the rest of section B."""

from tarn_core.domain.content import Difficulty
from tarn_core.seed.model import QuestionSeed, SubjectSeed, lst, question, sem, synthetic, term

SUBJECT = SubjectSeed(code="19AU0003", name="Constitution of India and Human Rights")
FR = "Fundamental rights"
GOV = "Union government"

US_GOVERNMENT = [
    "Congress",
    "Senate",
    "House of Representatives",
    "United States",
    "Americans",
    "Marines",
    "White House",
    "State of the Union",
]
"""Off-target terms (P13): the American institutions an answer on the Indian Union executive or
Parliament should not be about (requirements: B-CI2 Q12 describes the US President)."""
JUD = "Judiciary"
DPSP = "Directive principles"
EASY, MEDIUM, HARD = Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD

QUESTIONS: tuple[QuestionSeed, ...] = (
    # ---- Section A: any 5 of 7 x 2 ----
    question(
        "QP-CI-Q1",
        "What are the provisions under 'Right to freedom'?",
        2,
        FR,
        [
            synthetic(
                "The Right to Freedom is in Articles 19 to 22. Article 19 gives six freedoms: "
                "speech and expression; assembling peaceably and without arms; forming "
                "associations or unions; moving freely throughout India; residing and settling "
                "in any part of India; and practising any profession or carrying on any "
                "occupation, trade or business. Article 20 protects against conviction for "
                "offences (no ex post facto law, no double jeopardy, no self-incrimination). "
                "Article 21 protects life and personal liberty and Article 21A the right to "
                "education. Article 22 protects against arbitrary arrest and detention."
            )
        ],
        [
            lst(
                "Freedoms under Article 19",
                1,
                [
                    term("speech and expression", "freedom of speech"),
                    term("assemble peaceably", "assembly"),
                    term("associations", "unions", "form associations"),
                    term("move freely", "movement"),
                    term("reside and settle", "residence"),
                    term("profession", "occupation", "trade or business"),
                ],
                3,
            ),
            lst(
                "Protections in Articles 20 to 22",
                1,
                [
                    term("conviction for offences", "article 20", "double jeopardy"),
                    term("life and personal liberty", "article 21"),
                    term("education", "article 21a"),
                    term("arrest and detention", "article 22"),
                ],
                2,
            ),
        ],
        difficulty=EASY,
        terms=["Article 19", "Article 21", "Article 22", "personal liberty"],
    ),
    question(
        "QP-CI-Q2",
        "What are the 3 types of emergency?",
        2,
        GOV,
        [
            synthetic(
                "The Constitution provides for three emergencies. National emergency "
                "(Article 352) is declared on war, external aggression or armed rebellion. "
                "State emergency or President's rule (Article 356) is declared on the failure "
                "of the constitutional machinery in a state. Financial emergency (Article 360) "
                "is declared when the financial stability or credit of India is threatened."
            )
        ],
        [
            lst(
                "The three emergencies",
                2,
                [
                    term("national emergency", "article 352", "war", "external aggression"),
                    term(
                        "state emergency",
                        "president's rule",
                        "article 356",
                        "failure of constitutional machinery",
                    ),
                    term("financial emergency", "article 360"),
                ],
            )
        ],
        difficulty=EASY,
        terms=["Article 352", "Article 356", "Article 360", "President's rule"],
    ),
    question(
        "QP-CI-Q3",
        "Write any 3 provisions of Indian Constitution which was adapted by British Constitution.",
        2,
        GOV,
        [
            synthetic(
                "Provisions the Indian Constitution took from the British Constitution: the "
                "parliamentary form of government (the Westminster model); the rule of law; "
                "the cabinet system and collective responsibility to the lower house; "
                "bicameralism; the office of the Speaker; the prerogative writs; and the "
                "law-making procedure."
            )
        ],
        [
            lst(
                "Three provisions",
                2,
                [
                    term("parliamentary government", "parliamentary form", "westminster"),
                    term("rule of law"),
                    term("cabinet system", "collective responsibility"),
                    term("bicameralism", "bicameral legislature"),
                    term("speaker"),
                    term("writs", "prerogative writs"),
                    term("law-making procedure", "legislative procedure"),
                ],
                3,
            )
        ],
        difficulty=EASY,
        terms=["parliamentary system", "rule of law", "cabinet", "writs"],
    ),
    question(
        "QP-CI-Q4",
        "What is Bi-Cameralism system? What is the strength of Lok Sabha and Rajya Sabha?",
        2,
        GOV,
        [
            synthetic(
                "Bicameralism means a legislature of two houses. The Indian Parliament has the "
                "Lok Sabha (House of the People) and the Rajya Sabha (Council of States). The "
                "Lok Sabha has a maximum strength of 550 (530 from the states and 20 from the "
                "Union Territories), at present 543 elected members. The Rajya Sabha has a "
                "maximum of 250 members (238 elected by the state legislatures and 12 nominated "
                "by the President), at present 245."
            )
        ],
        [
            sem(
                "Bicameral system",
                1,
                "A bicameral system is a legislature with two houses; in India the Lok Sabha "
                "and the Rajya Sabha.",
            ),
            lst(
                "Strength of the two houses",
                1,
                [term("543", "545", "550", "552"), term("250", "245", "238")],
            ),
        ],
        difficulty=EASY,
        terms=["Lok Sabha", "Rajya Sabha", "bicameral"],
        off_target=US_GOVERNMENT,
    ),
    question(
        "QP-CI-Q5",
        "What are the qualifications to become a Member of Parliament?",
        2,
        GOV,
        [
            synthetic(
                "Under Article 84, a person must be a citizen of India; must be at least 25 "
                "years old for the Lok Sabha and 30 for the Rajya Sabha; must be registered as "
                "a voter (in a parliamentary constituency for the Lok Sabha, in a state for "
                "the Rajya Sabha); must not hold an office of profit under the government; and "
                "must not be of unsound mind, an undischarged insolvent or otherwise disqualified "
                "by a law of Parliament."
            )
        ],
        [
            lst(
                "Qualifications",
                2,
                [
                    term("citizen of india", "indian citizen"),
                    term("25 years", "age of 25", "30 years", "minimum age"),
                    term("registered voter", "electoral roll", "elector"),
                    term("office of profit"),
                    term("unsound mind", "insolvent", "disqualified"),
                ],
                4,
            )
        ],
        difficulty=EASY,
        terms=["Article 84", "qualification", "disqualification", "office of profit"],
    ),
    question(
        "QP-CI-Q6",
        "Mention 2 constitutional provisions that make India a secular state.",
        2,
        FR,
        [
            synthetic(
                "India is a secular state by the word 'secular' in the Preamble (added by the "
                "42nd Amendment, 1976); the right to freedom of religion in Articles 25 to 28; "
                "the equality of all persons before the law (Article 14) and the prohibition "
                "of discrimination on grounds of religion (Article 15) and in public "
                "employment (Article 16)."
            )
        ],
        [
            lst(
                "Two provisions",
                2,
                [
                    term("preamble", "42nd amendment", "secular"),
                    term("freedom of religion", "articles 25", "article 25", "25 to 28"),
                    term("article 14", "equality before the law"),
                    term("article 15", "no discrimination", "prohibition of discrimination"),
                    term("article 16", "equality of opportunity"),
                ],
                2,
            )
        ],
        difficulty=EASY,
        terms=["secular", "Article 25", "Preamble", "42nd Amendment"],
    ),
    question(
        "QP-CI-Q7",
        "List any 5 fundamental duties.",
        2,
        FR,
        [
            synthetic(
                "Article 51A lists the fundamental duties of every citizen: to abide by the "
                "Constitution and respect its ideals, the National Flag and the National "
                "Anthem; to cherish the noble ideals of the freedom struggle; to uphold and "
                "protect the sovereignty, unity and integrity of India; to defend the country "
                "when called; to promote harmony and the spirit of common brotherhood; to value "
                "and preserve the rich heritage of our composite culture; to protect and "
                "improve the natural environment; to develop scientific temper and humanism; "
                "to safeguard public property and abjure violence; to strive towards "
                "excellence; and, for a parent or guardian, to provide opportunities for "
                "education to a child between six and fourteen years."
            )
        ],
        [
            lst(
                "Five fundamental duties",
                2,
                [
                    term("abide by the constitution", "respect the flag", "national anthem"),
                    term("freedom struggle", "noble ideals"),
                    term("sovereignty", "unity and integrity"),
                    term("defend the country", "national service"),
                    term("harmony", "brotherhood"),
                    term("heritage", "composite culture"),
                    term("environment", "wildlife"),
                    term("scientific temper", "humanism"),
                    term("public property", "abjure violence"),
                    term("excellence"),
                    term("education to a child", "six and fourteen"),
                ],
                5,
            )
        ],
        difficulty=EASY,
        terms=["Article 51A", "fundamental duties", "citizen"],
    ),
    # ---- Section B: any 4 of 7 x 5 ----
    question(
        "QP-CI-Q8",
        "Why is Indian constitution considered a 'Living Document'?",
        5,
        GOV,
        [
            synthetic(
                "The Indian Constitution is called a living document because it grows with "
                "society. Article 368 allows Parliament to amend it, and it has been amended "
                "more than a hundred times to meet new needs; some parts need only a simple "
                "majority, others a special majority and ratification by states, so it is "
                "neither too rigid nor too flexible. The courts interpret it dynamically: "
                "Article 21 now covers privacy, a clean environment and livelihood, and the "
                "basic structure doctrine (Kesavananda Bharati, 1973) protects its core. "
                "Conventions and ordinary laws also fill gaps without a formal amendment."
            )
        ],
        [
            sem(
                "Amendment under Article 368",
                2,
                "The Constitution can be amended under Article 368 to meet changing needs, "
                "and has been amended over a hundred times; it is neither too rigid nor too "
                "flexible.",
            ),
            sem(
                "Judicial interpretation",
                2,
                "The courts interpret it dynamically, for example by widening Article 21, and "
                "protect its core through the basic structure doctrine.",
            ),
            sem(
                "Conventions and laws",
                1,
                "Constitutional conventions and ordinary laws fill gaps and let the "
                "Constitution adapt to social change.",
            ),
        ],
        terms=["Article 368", "amendment", "basic structure", "Kesavananda Bharati"],
    ),
    question(
        "QP-CI-Q9",
        "Explain the instruments of parliamentary control.",
        5,
        GOV,
        [
            synthetic(
                "Parliament controls the executive through: the Question Hour and Zero Hour; "
                "calling-attention and adjournment motions; discussions and debates on the "
                "President's address and on resolutions; the no-confidence motion, because the "
                "Council of Ministers is collectively responsible to the Lok Sabha; cut "
                "motions and the budget discussion; and the parliamentary committees, "
                "especially the Public Accounts Committee, the Estimates Committee and the "
                "Committee on Public Undertakings."
            )
        ],
        [
            lst(
                "Instruments of control",
                3,
                [
                    term("question hour", "questions"),
                    term("zero hour"),
                    term("adjournment motion"),
                    term("calling attention"),
                    term("no-confidence motion", "no confidence"),
                    term("debates", "discussions"),
                    term("cut motion", "budget"),
                    term("committees", "public accounts committee", "estimates committee"),
                ],
                5,
            ),
            sem(
                "Accountability of the executive",
                2,
                "The Council of Ministers is collectively responsible to the Lok Sabha, which "
                "can remove it by a vote of no confidence, so Parliament holds the executive "
                "accountable.",
            ),
        ],
        terms=["question hour", "no-confidence motion", "committee", "collective responsibility"],
        off_target=US_GOVERNMENT,
    ),
    question(
        "QP-CI-Q10",
        "Explain the powers of Rajya Sabha and Lok Sabha.",
        5,
        GOV,
        [
            synthetic(
                "The Lok Sabha is the more powerful house. Money bills and the budget can "
                "originate only in it, the Council of Ministers is responsible to it and it can "
                "remove the government by a no-confidence motion, and it has the final say in a "
                "joint sitting for ordinary bills. The Rajya Sabha, the house of the states, is "
                "a permanent house; it can only recommend changes to a money bill, within "
                "fourteen days, but it can pass a resolution under Article 249 to let Parliament "
                "legislate on a State List subject in the national interest, and under Article "
                "312 to create all-India services. It is equal to the Lok Sabha in "
                "constitutional amendments and in the election and impeachment of the "
                "President."
            )
        ],
        [
            sem(
                "Powers of the Lok Sabha",
                2.5,
                "The Lok Sabha has the money bills and the budget, the Council of Ministers is "
                "responsible to it and it can pass a no-confidence motion.",
            ),
            sem(
                "Powers of the Rajya Sabha",
                2.5,
                "The Rajya Sabha represents the states, can only recommend changes to money "
                "bills within fourteen days, and has special powers under Articles 249 and "
                "312.",
            ),
        ],
        terms=["money bill", "Article 249", "joint sitting", "no-confidence motion"],
        off_target=US_GOVERNMENT,
    ),
    question(
        "QP-CI-Q11",
        "List down 5 provisions in Indian constitution that safeguards the rights of Schedule "
        "caste.",
        5,
        FR,
        [
            synthetic(
                "Provisions for the Scheduled Castes: Article 15(2) and 15(4) (no "
                "discrimination; special provisions for advancement); Article 16(4) and "
                "16(4A) (reservation in public employment and promotion); Article 17 "
                "(abolition of untouchability); Article 46 (the State shall promote their "
                "educational and economic interests); Articles 330 and 332 (reservation of "
                "seats in the Lok Sabha and the state assemblies); Article 335 (claims to "
                "services); Article 338 (National Commission for Scheduled Castes)."
            )
        ],
        [
            lst(
                "Five provisions",
                5,
                [
                    term("article 15", "no discrimination"),
                    term("article 16", "reservation in employment", "public employment"),
                    term("article 17", "untouchability"),
                    term("article 46", "educational and economic interests"),
                    term("article 330", "seats in the lok sabha", "reservation of seats"),
                    term("article 332", "seats in the state assemblies"),
                    term("article 335", "claims to services"),
                    term("article 338", "national commission for scheduled castes"),
                ],
                5,
            )
        ],
        terms=["Article 17", "Article 46", "reservation", "untouchability"],
    ),
    question(
        "QP-CI-Q12",
        "List the executive powers of the President.",
        5,
        GOV,
        [
            synthetic(
                "The executive power of the Union is vested in the President (Article 53). "
                "The President appoints the Prime Minister and, on his advice, the other "
                "ministers; the Governors of states; the judges of the Supreme Court and High "
                "Courts; the Attorney General, the Comptroller and Auditor General, the Chief "
                "Election Commissioner and Election Commissioners, and members of the UPSC. "
                "The President is Supreme Commander of the Defence Forces, appoints "
                "ambassadors and receives foreign envoys, administers the Union Territories "
                "through administrators, and can declare an emergency."
            )
        ],
        [
            lst(
                "Executive powers",
                5,
                [
                    term("appoints the prime minister", "prime minister", "council of ministers"),
                    term("appoints governors", "governors"),
                    term("appoints judges", "supreme court and high court"),
                    term("attorney general", "comptroller and auditor general", "cag"),
                    term("election commissioners", "chief election commissioner", "upsc"),
                    term("supreme commander", "armed forces", "defence forces"),
                    term("ambassadors", "foreign envoys", "foreign affairs"),
                    term("union territories", "administrators"),
                    term("declare emergency", "emergency"),
                ],
                5,
            )
        ],
        terms=["President", "Article 53", "appointment", "Supreme Commander"],
        off_target=US_GOVERNMENT,
    ),
    question(
        "QP-CI-Q13",
        "Explain the following: i. Pardon, ii. Commutation, iii. Remission iv. Respite v. Reprieve",
        5,
        GOV,
        [
            synthetic(
                "Under Article 72 the President can grant these relief measures. Pardon "
                "removes both the sentence and the conviction and frees the offender from all "
                "punishment and disqualification. Commutation changes the sentence to a "
                "lighter one of another kind, for example death to life imprisonment. "
                "Remission reduces the amount of the sentence without changing its character, "
                "for example two years to one. Respite is a lesser sentence in place of the one "
                "given because of a special fact, such as the physical disability of the "
                "convict or the pregnancy of a woman. Reprieve is the temporary stay of the "
                "execution of a sentence, especially of death, to allow time to seek pardon or "
                "commutation."
            )
        ],
        [
            sem(
                "Pardon",
                1,
                "A pardon removes the sentence and the conviction and frees the offender from "
                "all punishment and disqualification.",
            ),
            sem(
                "Commutation",
                1,
                "Commutation substitutes a lighter kind of punishment, for example the death "
                "sentence by life imprisonment.",
            ),
            sem(
                "Remission",
                1,
                "Remission reduces the period of the sentence without changing its nature.",
            ),
            sem(
                "Respite",
                1,
                "Respite is a lesser sentence awarded because of a special fact such as "
                "disability or pregnancy.",
            ),
            sem(
                "Reprieve",
                1,
                "Reprieve is a temporary stay of the execution of a sentence so that the "
                "convict can seek pardon or commutation.",
            ),
        ],
        terms=["Article 72", "pardon", "commutation", "remission", "reprieve"],
        off_target=US_GOVERNMENT,
    ),
    question(
        "QP-CI-Q14",
        "Write a note on constitutional framework for environmental protection in India.",
        5,
        GOV,
        [
            synthetic(
                "Article 48A, a directive principle, requires the State to protect and improve "
                "the environment and safeguard forests and wildlife; Article 51A(g) makes it a "
                "fundamental duty of every citizen to protect and improve the natural "
                "environment. The Supreme Court reads a right to a clean and healthy "
                "environment into the right to life in Article 21, and public interest "
                "litigation under Articles 32 and 226 has been used to enforce it (for example "
                "the M. C. Mehta cases). Parliament legislated under Article 253 and the "
                "Concurrent List (forests, wildlife): the Wild Life (Protection) Act 1972, the "
                "Water Act 1974, the Forest (Conservation) Act 1980, the Air Act 1981, the "
                "Environment (Protection) Act 1986 and the National Green Tribunal Act 2010."
            )
        ],
        [
            sem(
                "Constitutional provisions",
                2.5,
                "Article 48A directs the State to protect the environment, Article 51A(g) "
                "makes it a duty of citizens, and Article 21 has been read to include the right "
                "to a clean environment.",
            ),
            lst(
                "Statutes",
                1.5,
                [
                    term("water act", "1974"),
                    term("air act", "1981"),
                    term("environment protection act", "environment (protection) act", "1986"),
                    term("wild life protection act", "wildlife protection act", "1972"),
                    term("forest conservation act", "forest (conservation) act", "1980"),
                    term("national green tribunal", "ngt"),
                ],
                3,
            ),
            sem(
                "Courts and institutions",
                1,
                "Public interest litigation under Articles 32 and 226 and the National Green "
                "Tribunal enforce environmental protection.",
            ),
        ],
        terms=["Article 48A", "Article 51A(g)", "environment", "public interest litigation"],
    ),
    # ---- Section C: any 2 of 3 x 10 ----
    question(
        "QP-CI-Q15",
        "Explain in detail the Original, Appellate, Writ and Advisory jurisdictions of Supreme "
        "court.",
        10,
        JUD,
        [
            synthetic(
                "Original jurisdiction (Article 131): the Supreme Court alone decides disputes "
                "between the Centre and one or more states, or between states; Article 32 also "
                "lets a person move the Court directly for fundamental rights. Appellate "
                "jurisdiction (Articles 132 to 136): appeals lie from the High Courts in "
                "constitutional matters, in civil and criminal cases on a certificate of "
                "fitness, and by special leave under Article 136 from any court or tribunal. "
                "Writ jurisdiction (Article 32): the Court issues habeas corpus, mandamus, "
                "prohibition, certiorari and quo warranto to enforce fundamental rights. "
                "Advisory jurisdiction (Article 143): the President may refer a question of "
                "law or fact of public importance to the Court for its opinion, which is not "
                "binding."
            )
        ],
        [
            sem(
                "Original jurisdiction",
                2.5,
                "The Supreme Court has exclusive original jurisdiction under Article 131 over "
                "disputes between the Centre and states or between states.",
            ),
            sem(
                "Appellate jurisdiction",
                2.5,
                "Appeals lie to the Supreme Court from High Courts in constitutional, civil and "
                "criminal matters, and by special leave under Article 136.",
            ),
            sem(
                "Writ jurisdiction",
                2.5,
                "Under Article 32 the Court issues writs to enforce fundamental rights.",
            ),
            sem(
                "Advisory jurisdiction",
                2.5,
                "Under Article 143 the President can seek the Court's opinion on a question of "
                "law or fact of public importance; the opinion is not binding.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["Article 131", "Article 136", "Article 143", "jurisdiction"],
    ),
    question(
        "QP-CI-Q16",
        "What are different types of writs issued by Supreme Court?",
        10,
        JUD,
        [
            synthetic(
                "Under Article 32 the Supreme Court (and under Article 226 the High Courts) "
                "issue five writs. Habeas corpus ('produce the body') orders the release of a "
                "person who is unlawfully detained. Mandamus ('we command') commands a public "
                "official or body to perform a public duty. Prohibition forbids a lower court "
                "or tribunal from exceeding its jurisdiction. Certiorari quashes an order "
                "already passed by a lower court or tribunal that acted without or beyond "
                "jurisdiction. Quo warranto ('by what authority') requires a person holding a "
                "public office to show the authority for holding it."
            )
        ],
        [
            sem(
                "Habeas corpus",
                2,
                "Habeas corpus means produce the body and secures the release of a person "
                "unlawfully detained.",
            ),
            sem(
                "Mandamus",
                2,
                "Mandamus commands a public official or body to perform its public duty.",
            ),
            sem(
                "Prohibition",
                2,
                "Prohibition forbids a lower court or tribunal from acting beyond its "
                "jurisdiction.",
            ),
            sem(
                "Certiorari",
                2,
                "Certiorari quashes an order of a lower court or tribunal passed without or "
                "beyond its jurisdiction.",
            ),
            sem(
                "Quo warranto",
                2,
                "Quo warranto asks a person holding a public office by what authority he holds "
                "it and removes him if he has none.",
            ),
        ],
        difficulty=EASY,
        terms=["habeas corpus", "mandamus", "certiorari", "prohibition", "quo warranto"],
    ),
    question(
        "QP-CI-Q17",
        "Distinguish between Fundamental Rights and Directive principles.",
        10,
        DPSP,
        [
            synthetic(
                "Fundamental Rights (Part III, Articles 12 to 35) are justiciable: a person "
                "can go to the Supreme Court or a High Court to enforce them. The Directive "
                "Principles of State Policy (Part IV, Articles 36 to 51) are non-justiciable "
                "(Article 37) and only guide the State. Fundamental Rights are largely "
                "negative restraints on the State and protect the individual; Directive "
                "Principles are positive instructions to promote welfare. Fundamental Rights "
                "establish political democracy and Directive Principles aim at social and "
                "economic democracy. Fundamental Rights were inspired by the US Bill of Rights, "
                "Directive Principles by the Irish Constitution. The two complement each "
                "other; the courts harmonise them (Minerva Mills, 1980) and some directives are "
                "given effect by law (Article 31C)."
            )
        ],
        [
            sem(
                "Justiciable and non-justiciable",
                3,
                "Fundamental Rights are justiciable and enforceable by the courts under "
                "Articles 32 and 226, while Directive Principles are non-justiciable under "
                "Article 37.",
            ),
            sem(
                "Nature: negative and positive, individual and community",
                3,
                "Fundamental Rights in Part III restrain the State and protect the individual; "
                "Directive Principles in Part IV direct the State towards welfare.",
            ),
            sem(
                "Political and social-economic democracy",
                2,
                "Fundamental Rights secure political democracy, Directive Principles aim at "
                "social and economic democracy.",
            ),
            sem(
                "Harmony between them",
                2,
                "The two complement each other and the courts harmonise them, as in Minerva "
                "Mills; some directives are given effect by law.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["Part III", "Part IV", "justiciable", "Article 37", "Minerva Mills"],
    ),
)
