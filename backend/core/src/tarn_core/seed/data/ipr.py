"""QP-IPR: Intellectual Property Rights (17NC301, BCom ACCA, Sem V, July 2021).

No key exists for this paper, so every reference answer and rubric is SYNTHETIC - dev only:
written for development, to be corrected by a teacher before any accuracy test uses it. The
paper's question 12 and 13 are alternatives (a: 10 marks, b: 5 marks); each part is its own
question here, so the blueprint links the parts."""

from tarn_core.domain.content import Difficulty
from tarn_core.seed.model import QuestionSeed, SubjectSeed, lst, question, sem, synthetic, term

SUBJECT = SubjectSeed(code="17NC301", name="Intellectual Property Rights")
PATENT = "Patents"
COPYRIGHT = "Copyright"
TRADEMARK = "Trade marks"
GENERAL = "IPR in general"
EASY, MEDIUM, HARD = Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD

QUESTIONS: tuple[QuestionSeed, ...] = (
    # ---- Section A: any 5 of 7 x 3 ----
    question(
        "QP-IPR-Q1",
        "Highlight the criteria for registering Geographical Indications in India.",
        3,
        GENERAL,
        [
            synthetic(
                "Under the Geographical Indications of Goods (Registration and Protection) Act, "
                "1999, a geographical indication identifies goods (agricultural, natural or "
                "manufactured) as originating in a territory, region or locality, where a "
                "given quality, reputation or other characteristic of the goods is essentially "
                "attributable to its geographical origin. The mark must not be generic, "
                "deceptive or contrary to law or morality. An association of persons, "
                "producers, organisation or authority representing the interest of the "
                "producers applies to the Registrar of Geographical Indications (Chennai), "
                "and a registration is valid for 10 years and renewable."
            )
        ],
        [
            lst(
                "Criteria for registration",
                3,
                [
                    term("agricultural", "natural", "manufactured goods"),
                    term("territory", "region", "locality", "geographical origin"),
                    term("quality", "reputation", "characteristic"),
                    term("association of persons", "producers", "organisation"),
                    term("not generic", "not deceptive", "not contrary to law"),
                    term("registrar", "chennai"),
                ],
                4,
            )
        ],
        difficulty=MEDIUM,
        terms=["Geographical Indications Act 1999", "territory", "quality", "Registrar"],
    ),
    question(
        "QP-IPR-Q2",
        "Write a Short note on Unfair Competition.",
        3,
        GENERAL,
        [
            synthetic(
                "Unfair competition is any act of competition contrary to honest practices in "
                "industrial or commercial matters (Article 10bis of the Paris Convention). "
                "Typical forms are passing off (making one's goods look like another's), "
                "misleading or false advertising, misappropriation of trade secrets, "
                "imitation that causes confusion, and disparaging a competitor's goods. India "
                "has no separate statute: it is remedied by the common-law action for passing "
                "off, the Trade Marks Act 1999, the Consumer Protection Act and the "
                "Competition Act."
            )
        ],
        [
            sem(
                "Meaning of unfair competition",
                1.5,
                "Unfair competition is any act of competition in industry or commerce that is "
                "contrary to honest practices, as in Article 10bis of the Paris Convention.",
            ),
            lst(
                "Forms of unfair competition",
                1.5,
                [
                    term("passing off"),
                    term("misleading advertising", "false advertising"),
                    term("trade secret", "misappropriation"),
                    term("imitation", "confusion"),
                    term("disparagement", "dilution"),
                ],
                3,
            ),
        ],
        difficulty=EASY,
        terms=["passing off", "Paris Convention", "honest practices", "trade secret"],
    ),
    question(
        "QP-IPR-Q3",
        "Give Six examples of Registered Trademarks in India.",
        3,
        TRADEMARK,
        [
            synthetic(
                "Examples of trade marks registered in India: Tata, Amul, Infosys, Reliance, "
                "Maruti, Nirma, Parle-G, Titan, Mahindra, Bata, Wipro, Godrej, Asian Paints, "
                "Mother Dairy, Haldiram's."
            )
        ],
        [
            lst(
                "Six registered trade marks",
                3,
                [
                    term("Tata"),
                    term("Amul"),
                    term("Infosys"),
                    term("Reliance"),
                    term("Maruti"),
                    term("Nirma"),
                    term("Parle-G", "Parle"),
                    term("Titan"),
                    term("Mahindra"),
                    term("Bata"),
                    term("Wipro"),
                    term("Godrej"),
                    term("Asian Paints"),
                    term("Haldiram"),
                    term("Nike"),
                    term("Apple"),
                    term("Samsung"),
                    term("Coca-Cola", "Coca Cola"),
                ],
                6,
            )
        ],
        difficulty=EASY,
        terms=["trade mark", "registered", "brand"],
    ),
    question(
        "QP-IPR-Q4",
        "What are the types of Works eligible for Copyright Protection in India?",
        3,
        COPYRIGHT,
        [
            synthetic(
                "Under Section 13 of the Copyright Act, 1957, copyright subsists in original "
                "literary works (including computer programmes, tables and databases), "
                "dramatic works, musical works and artistic works (paintings, drawings, "
                "photographs, sculptures, works of architecture); in cinematograph films; and "
                "in sound recordings."
            )
        ],
        [
            lst(
                "Types of works",
                3,
                [
                    term("literary works", "literary"),
                    term("dramatic works", "dramatic"),
                    term("musical works", "musical"),
                    term("artistic works", "artistic"),
                    term("cinematograph films", "films", "cinematograph"),
                    term("sound recordings", "sound recording"),
                ],
                6,
            )
        ],
        difficulty=EASY,
        terms=["Copyright Act 1957", "Section 13", "literary work", "original work"],
    ),
    question(
        "QP-IPR-Q5",
        '"Mere discovery of a known substance may not be invention". Elaborate on the above '
        "statement in light of section 3 (d) of the Patent Act, 1970.",
        3,
        PATENT,
        [
            synthetic(
                "Section 3(d) of the Patents Act, 1970 says that the mere discovery of a new "
                "form of a known substance which does not result in the enhancement of the "
                "known efficacy of that substance, or the mere discovery of any new property or "
                "new use of a known substance, is not an invention. Salts, esters, polymorphs "
                "and other derivatives of a known substance are treated as the same substance "
                "unless they differ significantly in properties with regard to efficacy. In "
                "Novartis AG v. Union of India (2013), concerning the cancer drug Glivec, the "
                "Supreme Court upheld the refusal of the patent and stressed that the section "
                "prevents 'evergreening' of patents."
            )
        ],
        [
            sem(
                "Section 3(d)",
                1.5,
                "Section 3(d) says that a new form of a known substance is not an invention "
                "unless it enhances the known efficacy; a new property or new use of a known "
                "substance is also not an invention.",
            ),
            sem(
                "Novartis v. Union of India",
                1,
                "In Novartis v. Union of India (Glivec), the Supreme Court upheld the refusal "
                "of the patent under section 3(d).",
            ),
            sem(
                "Evergreening",
                0.5,
                "The section prevents evergreening, the extension of a patent by trivial "
                "changes to a known substance.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["Section 3(d)", "efficacy", "evergreening", "Novartis", "Patents Act 1970"],
    ),
    question(
        "QP-IPR-Q6",
        "Can softwares be patented in India? If yes, in what context?",
        3,
        PATENT,
        [
            synthetic(
                "Section 3(k) of the Patents Act, 1970 excludes a 'mathematical or business "
                "method or a computer programme per se or algorithms' from patent protection, "
                "so software as such cannot be patented in India. A computer-related invention "
                "can be patented if it shows a technical effect or technical contribution, "
                "for example when the software works together with hardware to solve a technical "
                "problem; the Guidelines for Examination of Computer Related Inventions (2017) "
                "and the Delhi High Court in Ferid Allani v. Union of India (2019) accept this. "
                "Software is otherwise protected by copyright as a literary work."
            )
        ],
        [
            sem(
                "Software per se is excluded",
                1.5,
                "Under section 3(k) of the Patents Act a computer programme per se, algorithm "
                "or business method cannot be patented in India.",
            ),
            sem(
                "Patentable with a technical effect",
                1.5,
                "A computer-related invention can be patented if it has a technical effect or "
                "technical contribution, for example software working with hardware to solve a "
                "technical problem.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["Section 3(k)", "computer programme per se", "technical effect", "CRI guidelines"],
    ),
    question(
        "QP-IPR-Q7",
        "Write a Short Note of WIPO.",
        3,
        GENERAL,
        [
            synthetic(
                "The World Intellectual Property Organization (WIPO) is a specialised agency "
                "of the United Nations, established by a convention signed at Stockholm in "
                "1967, with its headquarters in Geneva. India has been a member since 1975. Its "
                "aim is to promote the protection of intellectual property throughout the "
                "world and cooperation among states. It administers treaties such as the "
                "Paris and Berne Conventions, the Patent Cooperation Treaty, the Madrid "
                "system and the Hague system, and offers arbitration and mediation for IP "
                "disputes."
            )
        ],
        [
            sem(
                "Status and origin",
                1,
                "WIPO is a specialised agency of the United Nations, set up by the Stockholm "
                "convention of 1967, headquartered in Geneva.",
            ),
            sem(
                "Objective",
                1,
                "Its aim is to promote the protection of intellectual property throughout the "
                "world and cooperation between states.",
            ),
            sem(
                "Treaties and services",
                1,
                "It administers treaties such as the Paris and Berne Conventions and the "
                "Patent Cooperation Treaty and provides arbitration and mediation services.",
            ),
        ],
        difficulty=EASY,
        terms=["WIPO", "Geneva", "Paris Convention", "Berne Convention", "PCT"],
    ),
    # ---- Section B: any 3 of 4 x 10 ----
    question(
        "QP-IPR-Q8",
        "What are the Performer's right under the copyright law in India?",
        10,
        COPYRIGHT,
        [
            synthetic(
                "A performer (an actor, singer, musician, dancer, acrobat, juggler, lecturer or "
                "any person who makes a performance, Section 2(qq)) has special rights in the "
                "Copyright Act, 1957. Section 38 gives the performer's right for fifty years "
                "from the beginning of the calendar year after the performance. Section 38A "
                "gives exclusive rights, subject to contract: to make a sound or visual "
                "recording of the performance, to reproduce it, to issue copies to the public, "
                "to communicate it to the public, to sell or rent copies, and to broadcast it. "
                "Section 38B gives moral rights: to claim to be identified as the performer and "
                "to restrain or claim damages for distortion, mutilation or modification that "
                "would harm the performer's reputation. Section 39 lists exceptions, such as "
                "private use and news reporting. The 2012 amendment and the WIPO Performances "
                "and Phonograms Treaty strengthened these rights, for example for performers "
                "in a film."
            )
        ],
        [
            sem(
                "Who is a performer",
                2,
                "A performer is an actor, singer, musician, dancer or any person who makes a "
                "performance.",
            ),
            sem(
                "Exclusive rights, section 38A",
                3,
                "The performer has exclusive rights to record the performance, reproduce it, "
                "issue copies, communicate it to the public, sell or rent copies and broadcast "
                "it.",
            ),
            sem(
                "Moral rights, section 38B",
                2,
                "The performer has moral rights to be identified as the performer and to "
                "restrain distortion or mutilation of the performance.",
            ),
            sem(
                "Term of protection, section 38",
                1,
                "The performer's right lasts fifty years from the beginning of the next "
                "calendar year after the performance.",
            ),
            sem(
                "Exceptions and the 2012 amendment",
                2,
                "Section 39 lists exceptions such as private use, and the 2012 amendment, in "
                "line with the WIPO Performances and Phonograms Treaty, strengthened the "
                "performer's rights.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["Section 38", "Section 38A", "Section 38B", "moral rights", "performance"],
    ),
    question(
        "QP-IPR-Q9",
        "Highlight the differences in between Patent, Copyright and Trademark.",
        10,
        GENERAL,
        [
            synthetic(
                "A patent protects a new, inventive and industrially applicable invention (a "
                "product or process) under the Patents Act, 1970; it lasts 20 years from the "
                "date of filing and must be applied for and granted. Copyright protects the "
                "original expression of ideas in literary, dramatic, musical and artistic "
                "works, films and sound recordings under the Copyright Act, 1957; it arises "
                "automatically on creation, registration is optional, and for most works it "
                "lasts for the author's life plus 60 years. A trade mark protects a sign, word, "
                "logo, shape or colour combination that distinguishes the goods or services of "
                "one business from those of others under the Trade Marks Act, 1999; it is "
                "registered, valid for 10 years and renewable indefinitely every 10 years. A "
                "patent gives a monopoly over the invention itself, copyright prevents copying "
                "of the expression only, and a trade mark prevents confusingly similar use of a "
                "brand."
            )
        ],
        [
            sem(
                "Subject matter protected",
                3,
                "A patent protects an invention, copyright protects the original expression of "
                "a work of authorship, and a trade mark protects a sign that distinguishes the "
                "goods or services of a business.",
            ),
            sem(
                "Duration",
                3,
                "A patent lasts 20 years from filing, copyright generally the life of the "
                "author plus 60 years, and a trade mark 10 years renewable indefinitely.",
            ),
            sem(
                "Acquisition and governing law",
                2,
                "A patent must be applied for and granted under the Patents Act 1970, copyright "
                "arises on creation under the Copyright Act 1957 and registration is optional, "
                "a trade mark is registered under the Trade Marks Act 1999.",
            ),
            sem(
                "Nature of the right with examples",
                2,
                "A patent gives a monopoly over the invention, copyright prevents copying of "
                "the expression, and a trade mark prevents confusing use of a brand, for "
                "example a drug formula, a novel and a logo.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["patent", "copyright", "trade mark", "term of protection", "registration"],
    ),
    question(
        "QP-IPR-Q10",
        "Explain the major rights of a Copyright owner in India.",
        10,
        COPYRIGHT,
        [
            synthetic(
                "Section 14 of the Copyright Act, 1957 gives the owner exclusive economic "
                "rights: to reproduce the work, including storing it in electronic form; to "
                "issue copies to the public; to perform the work in public or communicate it "
                "to the public; to make a cinematograph film or sound recording of it; to make "
                "translations and adaptations; and to sell, rent or hire copies. Section 57 "
                "gives the author special (moral) rights, independent of the economic rights: "
                "the right of paternity, to claim authorship, and the right of integrity, to "
                "restrain or claim damages for distortion, mutilation or modification that "
                "would harm the author's honour or reputation. The owner can assign the "
                "copyright wholly or partly, or license it (Sections 18 and 30). The term is "
                "the author's life plus 60 years for literary, dramatic, musical and artistic "
                "works, and 60 years from publication for films and sound recordings. The "
                "owner can sue for infringement, claiming injunction, damages and accounts of "
                "profits, and criminal remedies exist."
            )
        ],
        [
            sem(
                "Economic rights, section 14",
                4,
                "The owner has the exclusive right to reproduce the work, issue copies, "
                "perform or communicate it to the public, make films, recordings, translations "
                "and adaptations, and sell, rent or hire copies.",
            ),
            sem(
                "Moral rights, section 57",
                2,
                "The author has the moral rights of paternity and integrity, independent of "
                "the economic rights.",
            ),
            sem(
                "Assignment and licence",
                2,
                "The owner can assign the copyright or grant a licence under sections 18 and 30.",
            ),
            sem(
                "Term of copyright",
                1,
                "The term is generally the life of the author plus 60 years.",
            ),
            sem(
                "Remedies for infringement",
                1,
                "The owner can seek an injunction, damages and accounts of profits, and "
                "criminal remedies for infringement.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["Section 14", "Section 57", "economic rights", "moral rights", "assignment"],
    ),
    question(
        "QP-IPR-Q11",
        "Elaborate on the Patents registration process in India.",
        10,
        PATENT,
        [
            synthetic(
                "1. Filing: after a patentability search, the applicant files an application "
                "with the Patent Office (Mumbai, Delhi, Chennai or Kolkata) with a provisional "
                "or complete specification (Forms 1 and 2), the fee and the claims; a "
                "provisional specification secures a priority date, and the complete "
                "specification must follow within 12 months. 2. Publication: the application "
                "is published in the Patent Journal after 18 months from the filing or priority "
                "date, or earlier on request (Form 9). 3. Request for examination (Form 18) "
                "within 48 months; the examiner issues a First Examination Report, and the "
                "applicant has to reply and remove the objections within the time allowed. 4. "
                "Opposition: anyone may oppose before the grant (pre-grant, Section 25(1)) or "
                "within one year after the grant is published (post-grant, Section 25(2)). 5. "
                "Grant: when the application is in order and no opposition succeeds, the "
                "patent is granted and published, and the patent lasts 20 years from the "
                "filing date, subject to payment of a renewal fee every year."
            )
        ],
        [
            sem(
                "Filing the application and specification",
                2.5,
                "The applicant files an application with a provisional or complete "
                "specification at the Patent Office; a provisional specification secures the "
                "priority date.",
            ),
            sem(
                "Publication after 18 months",
                1.5,
                "The application is published after 18 months, or earlier on request.",
            ),
            sem(
                "Request for examination and the first examination report",
                2.5,
                "A request for examination is made, the examiner issues a first examination "
                "report, and the applicant replies to remove the objections.",
            ),
            sem(
                "Opposition",
                1.5,
                "Anyone may oppose the patent before the grant or within one year after it.",
            ),
            sem(
                "Grant, term and renewal",
                2,
                "The patent is granted and published; it lasts 20 years from filing subject to "
                "payment of renewal fees.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["specification", "Patent Office", "examination", "opposition", "grant"],
    ),
    # ---- Section C: Q12 (a 10 + b 5) or Q13 (a 10 + b 5) ----
    question(
        "QP-IPR-Q12A",
        "Explain the concept of Unfair Competition and its relationship with Intellectual "
        "Property Rights Laws with suitable examples.",
        10,
        GENERAL,
        [
            synthetic(
                "Unfair competition means any act of competition in industrial or commercial "
                "matters that is contrary to honest practices (Article 10bis of the Paris "
                "Convention, Article 39 of TRIPS for undisclosed information). Examples are "
                "passing off a product as another's, misleading or comparative advertising "
                "that deceives, misappropriating trade secrets, and copying a competitor's "
                "get-up. Relationship with IPR: intellectual property laws give specific, "
                "registered or automatic rights (patents, copyright, trade marks, designs), "
                "while unfair competition law fills the gaps and protects what those laws do "
                "not, such as unregistered marks, goodwill and trade secrets. In India there "
                "is no separate unfair competition statute; the common-law action of passing "
                "off, Section 27 of the Trade Marks Act 1999, which preserves it for "
                "unregistered marks, the Competition Act 2002, the Consumer Protection Act and "
                "contract and confidence law provide remedies. For example, selling shoes "
                "under a name and logo confusingly similar to a well-known brand is passing "
                "off even if the mark is unregistered."
            )
        ],
        [
            sem(
                "Concept of unfair competition",
                3,
                "Unfair competition is any act in industry or commerce contrary to honest "
                "practices, recognised by Article 10bis of the Paris Convention and TRIPS.",
            ),
            lst(
                "Forms of unfair competition",
                2,
                [
                    term("passing off"),
                    term("misleading advertising", "false advertising"),
                    term("trade secret", "confidential information"),
                    term("imitation", "get-up", "confusion"),
                ],
                3,
            ),
            sem(
                "Relationship with IPR laws",
                3,
                "IPR laws give specific rights while unfair competition law fills the gaps, "
                "protecting unregistered marks, goodwill and trade secrets; in India this is "
                "through passing off, section 27 of the Trade Marks Act and other statutes.",
            ),
            sem(
                "Suitable examples",
                2,
                "An example such as selling goods under a name and logo confusingly similar to "
                "a well-known brand, which is passing off even without registration.",
            ),
        ],
        difficulty=HARD,
        terms=["unfair competition", "passing off", "Article 10bis", "goodwill", "TRIPS"],
    ),
    question(
        "QP-IPR-Q12B",
        "Critically analyse the evolution of Trademark law in India.",
        5,
        TRADEMARK,
        [
            synthetic(
                "Early protection came from the common-law action of passing off and the "
                "Indian Registration Act, 1908 (registration of the trade mark as a "
                "document) and the Specific Relief Act. The Indian Merchandise Marks Act, 1889 "
                "made false trade descriptions an offence. The Trade Marks Act, 1940 created "
                "the first registration system. The Trade and Merchandise Marks Act, 1958 set "
                "up a Register and a Registrar and protected registered marks, but it did not "
                "cover service marks, and had weak remedies. The Trade Marks Act, 1999, in "
                "force from 2003 with the Trade Marks Rules 2002 (replaced by the 2017 Rules), "
                "met the TRIPS obligations: it protects service marks, well-known marks, "
                "collective and certification marks, allows registration of shapes and "
                "packaging, increases penalties and created the Intellectual Property "
                "Appellate Board (abolished in 2021). Critically, the law is now modern, but "
                "pendency and delays in the Trade Marks Registry, the cost of litigation and "
                "enforcement against counterfeiting remain weaknesses."
            )
        ],
        [
            lst(
                "Milestones",
                3,
                [
                    term("merchandise marks act 1889", "1889"),
                    term("trade marks act 1940", "1940"),
                    term("trade and merchandise marks act 1958", "1958"),
                    term("trade marks act 1999", "1999"),
                    term("passing off", "common law"),
                    term("trips"),
                ],
                4,
            ),
            sem(
                "Critical analysis",
                2,
                "The 1999 Act modernised the law by protecting service marks and well-known "
                "marks in line with TRIPS, but delays in the registry and weak enforcement "
                "against counterfeiting remain.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["Trade Marks Act 1999", "TRIPS", "service marks", "evolution"],
    ),
    question(
        "QP-IPR-Q13A",
        "What types of signs/logo are not allowed to be registered as Trademarks in India?",
        10,
        TRADEMARK,
        [
            synthetic(
                "Absolute grounds of refusal (Section 9 of the Trade Marks Act, 1999): marks "
                "devoid of any distinctive character; marks that consist only of signs "
                "describing the kind, quality, quantity, purpose, value or geographical origin "
                "of the goods; marks that have become customary in the language or trade; marks "
                "likely to deceive the public or cause confusion; marks likely to hurt the "
                "religious susceptibilities of any class; marks containing scandalous or "
                "obscene matter; marks prohibited under the Emblems and Names (Prevention of "
                "Improper Use) Act, 1950, such as the national flag or emblem; and shapes that "
                "result from the nature of the goods, are necessary to obtain a technical "
                "result or give substantial value to the goods (Section 9(3)). Relative "
                "grounds (Section 11): a mark that is identical or similar to an earlier "
                "registered mark for identical or similar goods and is likely to confuse the "
                "public, or that conflicts with a well-known mark. A mark is not refused under "
                "Section 9 if it has acquired a distinctive character through use."
            )
        ],
        [
            sem(
                "Absolute grounds, section 9",
                4,
                "Marks devoid of distinctive character, purely descriptive of the goods, or "
                "customary in the trade cannot be registered, unless they acquired "
                "distinctiveness through use.",
            ),
            sem(
                "Deceptive, offensive and religious marks",
                2,
                "Marks that deceive or confuse the public, hurt religious susceptibilities or "
                "contain scandalous or obscene matter cannot be registered.",
            ),
            sem(
                "Prohibited emblems and shapes",
                2,
                "Marks prohibited by the Emblems and Names Act, such as the national flag or "
                "emblem, and shapes resulting from the nature of the goods, necessary for a "
                "technical result or giving substantial value to the goods cannot be "
                "registered.",
            ),
            sem(
                "Relative grounds, section 11",
                2,
                "A mark identical or similar to an earlier registered or well-known mark for "
                "identical or similar goods, likely to cause confusion, is refused.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["Section 9", "Section 11", "distinctive character", "absolute grounds", "emblem"],
    ),
    question(
        "QP-IPR-Q13B",
        "Write a note on Patent Cooperation Treaty.",
        5,
        PATENT,
        [
            synthetic(
                "The Patent Cooperation Treaty (PCT), concluded in 1970 and administered by "
                "WIPO, lets an applicant file a single international application that has "
                "effect in all member countries (over 150), instead of filing separately in "
                "each. India joined in 1998. In the international phase, an international "
                "search report and a written opinion are prepared, the application is "
                "published after 18 months, and an optional international preliminary "
                "examination follows. In the national phase, the applicant enters the "
                "countries of choice, usually within 30 or 31 months from the priority date, "
                "and each national office decides on the grant under its own law. The PCT "
                "gives more time to decide where to seek protection, postpones the costs of "
                "translations and local fees, and simplifies filing; it does not grant an "
                "international patent."
            )
        ],
        [
            sem(
                "Nature and administration",
                1,
                "The PCT, concluded in 1970 and administered by WIPO, lets an applicant file "
                "one international application with effect in all member countries.",
            ),
            sem(
                "International phase",
                1.5,
                "In the international phase an international search report and written opinion "
                "are made, the application is published after 18 months, with an optional "
                "preliminary examination.",
            ),
            sem(
                "National phase",
                1.5,
                "In the national phase the applicant enters the chosen countries within about "
                "30 months and each national office decides on the grant.",
            ),
            sem(
                "Benefits and limits",
                1,
                "The PCT postpones costs and simplifies filing, India joined in 1998, but it "
                "does not give an international patent.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["PCT", "international phase", "national phase", "WIPO", "priority date"],
    ),
)
