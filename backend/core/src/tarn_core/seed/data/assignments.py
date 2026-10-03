"""Assignment 1 (Indian Ethos and Leadership, BCom CMA, Sem III) and Assignment 2 (startup
schemes essay).

**K-EL.** The only reference text for Assignment 1 is ``Assignment_1_Answer_Key.pdf`` (K-EL),
which is a student's own typed submission, not a faculty key. The user allowed using it after
removing the student's name and USN (U2 Q5, D18). What is stored here is the cleaned text
only: the cover page (title, name, class, USN) is dropped, so neither a name nor a USN
appears, and the PDF itself is not kept. It stays marked SYNTHETIC - dev only: not validated
by a teacher.

**Assignment 1, question 2** is worded as the user gave it (India, Korea, Japan and China); K-EL
covers India, Korea and Japan only, so China has a separate synthetic reference answer.

**Assignment 2** has no key and its question wording is not in the project documents (the
inventory only says "startup schemes essay, course not stated"); the wording below is a
placeholder for a teacher to confirm. Marks (10 each) are assumed: the assignments print none."""

from tarn_core.domain.content import Difficulty
from tarn_core.seed.model import QuestionSeed, SubjectSeed, lst, question, sem, synthetic, term

SUBJECT_EL = SubjectSeed(code="IEL", name="Indian Ethos and Leadership")
SUBJECT_SU = SubjectSeed(
    code="STARTUP-A2", name="Startup schemes (Assignment 2, course not stated)"
)
EASY, MEDIUM, HARD = Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD

# K-EL, section 1 and 2, cover page removed (no name, no USN).
K_EL_Q1 = """Ethical dilemma

An ethical dilemma occurs when an individual is faced with a situation in which they must choose between two conflicting moral principles or courses of action. Each option typically has potential negative consequences or compromises on values, making it difficult to decide on the "right" course of action. Ethical dilemmas often arise in complex situations where there is no clear solution, and where each choice presents both benefits and ethical challenges.

In an ethical dilemma, an individual might face conflicting duties or values, such as: honesty vs. loyalty; individual rights vs. collective good; professional responsibility vs. personal ethics.

The complexity of ethical dilemmas lies in the fact that there is often no "perfect" solution; each choice may involve some compromise, and individuals are forced to prioritize certain values over others, such as integrity, fairness, or loyalty. These dilemmas require careful consideration, often guided by ethical principles, codes of conduct, or personal values.

Ethical dilemma with examples

Dilemma 01: Bribery to win a contract. Scenario: a project manager at a construction firm is working in a country where it is common for companies to offer bribes to government officials to win contracts. The firm is competing for a lucrative government contract that could significantly boost its revenues. During the bidding process, a government official hints that the project manager could secure the contract if they provide a "personal contribution" (a bribe). The project manager knows that if they don't comply, their competitor, who is known for such unethical practices, will likely win the contract.
Option 1: the project manager could comply with the bribe, secure the contract, and benefit the company financially. However, this would violate both personal and corporate ethical standards, potentially harm the firm's reputation, and perpetuate corruption.
Option 2: the project manager could refuse to offer the bribe, maintaining ethical integrity but potentially losing the contract and causing financial harm to the company.

Dilemma 02: Whistleblowing in a company. Scenario: a senior accountant at a large manufacturing company discovers that the company has been manipulating its financial reports to inflate profits and deceive shareholders. This practice has been going on for years, and revealing the truth could result in significant legal repercussions for the company, potentially leading to job losses for thousands of employees, including the accountant's own colleagues. The accountant is torn between the duty to report the unethical practice (whistleblowing) and the potential consequences for the company and its employees.
Option 1: the accountant could report the misconduct to the authorities, maintaining personal integrity and adhering to professional ethical standards. However, this could lead to the collapse of the company, loss of jobs, and financial ruin for many.
Option 2: the accountant could stay silent, allowing the company to continue its unethical practices, protecting jobs and financial stability in the short term, but compromising personal ethics and enabling fraud."""

K_EL_Q2 = """Influence of cultural ethos

Cultural ethos involves the shared values and norms within a specific culture. For instance, Indian ethos may emphasize values such as respect for elders, community solidarity and spiritual well-being.

Influence of cultural ethos on business: India, Korea, Japan

1. India. Indian business culture is deeply influenced by its cultural and religious diversity. The Indian ethos emphasizes relationship-building, trust, and long-term associations, which stem from the country's traditions of community living and family-oriented values. Hierarchies are respected in the workplace, and decisions often take into account the social and familial implications. Religion also plays a significant role in shaping ethics and business practices. For example, karma and dharma, concepts of duty and righteousness, affect how Indian businesses view fairness, responsibility, and work ethics. Business approach: Indian businesses often value flexible approaches and personal relationships over rigid contracts, and negotiation can be lengthy, emphasizing trust and mutual understanding. Influence on decision-making: Indian culture encourages consideration of the collective welfare, often leading businesses to balance profitability with social responsibility and family considerations.

2. Korea. Korean culture is heavily influenced by Confucianism, which stresses respect for hierarchy, loyalty, and collectivism. These values are mirrored in the business environment, where age and seniority are respected, and employees show great loyalty to their companies. Group harmony is valued over individual success, and there is a strong emphasis on teamwork and consensus. Business approach: Korean companies often prioritize group decision-making and consensus-building, with a hierarchical leadership that involves respect for elders and authority figures. Influence on decision-making: loyalty to the company and ensuring group harmony can sometimes override individual opinions or innovation. Relationships built over time are crucial, and face-saving is a key consideration in negotiations and conflict resolution.

3. Japan. Japanese business culture also draws heavily from Confucianism but integrates elements of Buddhism and Shintoism, emphasizing discipline, harmony and loyalty. There is a collective focus in Japanese businesses, with a strong sense of duty to the company, and teamwork is highly prioritized over individual achievement. Business approach: decisions in Japan are often made after careful consultation and consensus, known as nemawashi, where ideas are discussed informally before formal meetings. Japanese businesses also highly value long-term partnerships and loyalty. Influence on decision-making: Japanese businesses avoid confrontation, and decisions are made cautiously with the involvement of various stakeholders. There is a preference for gradual change rather than abrupt innovation, which supports stability but can slow down rapid decision-making.

Comparison. In India, personal relationships and social responsibilities influence business dealings, leading to a flexible, people-focused approach. In Korea, respect for hierarchy and group harmony dominate business decisions, making loyalty and consensus critical. In Japan, meticulous planning, consensus-building, and long-term relationships define the approach, promoting stability and harmony in business. Cultural ethos shapes how decisions are made, how businesses are run, and the values prioritized across industries in these nations."""

CHINA = (
    "China. Chinese business culture is shaped by Confucianism, which stresses hierarchy, "
    "respect for seniors, family loyalty and harmony. Relationships and trust are built through "
    "guanxi, a network of personal connections and mutual obligations, and business often "
    "follows the relationship rather than the contract. Mianzi, or 'face' (dignity and "
    "reputation), has to be protected in negotiations, so criticism is indirect and public "
    "conflict is avoided. Decisions are taken by senior leaders after consultation and "
    "emphasise long-term benefit over a quick gain; the state also plays a large part in "
    "business. Compared with India, Korea and Japan it shares the respect for hierarchy, "
    "collectivism and long-term relationships."
)

QUESTIONS_EL: tuple[QuestionSeed, ...] = (
    question(
        "A1-Q1",
        "Ethical dilemma: explain what an ethical dilemma is and discuss it with examples.",
        10,
        "Business ethics",
        [synthetic(K_EL_Q1)],
        [
            sem(
                "Meaning of an ethical dilemma",
                3,
                "An ethical dilemma is a situation in which a person must choose between two "
                "conflicting moral principles or courses of action, each with negative "
                "consequences or compromises on values, and there is no clear right answer.",
            ),
            lst(
                "Conflicting values",
                1,
                [
                    term("honesty", "loyalty"),
                    term("individual rights", "collective good"),
                    term("professional responsibility", "personal ethics"),
                ],
                2,
            ),
            sem(
                "Example 1: bribery to win a contract",
                3,
                "A project manager can pay a bribe to win a contract, which benefits the firm "
                "but breaks ethical standards and perpetuates corruption, or refuse and lose "
                "the contract and money for the company.",
            ),
            sem(
                "Example 2: whistleblowing",
                3,
                "An accountant who finds manipulated financial reports can report them, "
                "keeping integrity but risking the collapse of the company and jobs, or stay "
                "silent and protect jobs but enable fraud.",
            ),
        ],
        difficulty=EASY,
        terms=["ethical dilemma", "bribery", "whistleblowing", "integrity", "conflicting values"],
    ),
    question(
        "A1-Q2",
        "Influence of cultural ethos on business: India, Korea, Japan and China.",
        10,
        "Business ethics",
        [synthetic(K_EL_Q2), synthetic(CHINA)],
        [
            sem(
                "Meaning of cultural ethos",
                1,
                "Cultural ethos is the set of shared values and norms of a culture.",
            ),
            sem(
                "India",
                2,
                "Indian business values relationships, trust and long-term associations, "
                "respects hierarchy, and is influenced by religion, karma and dharma; it "
                "balances profit with social responsibility and family considerations.",
            ),
            sem(
                "Korea",
                2,
                "Korean business is influenced by Confucianism: respect for hierarchy, loyalty "
                "and collectivism, group harmony and consensus, with face-saving important in "
                "negotiations.",
            ),
            sem(
                "Japan",
                2,
                "Japanese business combines Confucianism, Buddhism and Shintoism: discipline, "
                "harmony and loyalty, consensus decisions (nemawashi), long-term partnerships "
                "and caution.",
            ),
            sem(
                "China",
                2,
                "Chinese business is shaped by Confucianism: hierarchy, guanxi (personal "
                "networks), face (mianzi), harmony and long-term relationships.",
            ),
            sem(
                "Comparison",
                1,
                "In all four countries relationships, hierarchy and the group shape business "
                "decisions, though India is more flexible and Japan more consensus-driven.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["cultural ethos", "Confucianism", "karma", "dharma", "guanxi", "nemawashi"],
    ),
)

QUESTIONS_SU: tuple[QuestionSeed, ...] = (
    question(
        "A2-Q1",
        "Write an essay on the schemes of the Government of India that support startups "
        "(wording to be confirmed by the teacher).",
        10,
        "Entrepreneurship",
        [
            synthetic(
                "A startup is a young, innovative business with the potential for fast growth. "
                "The Government of India supports startups through Startup India (2016), with "
                "recognition by the DPIIT, a tax holiday for three consecutive years out of "
                "the first ten (Section 80-IAC), easier compliance and the self-certification "
                "regime; the Fund of Funds for Startups of Rs 10,000 crore managed by SIDBI; "
                "the Startup India Seed Fund Scheme for proof of concept and prototype; the "
                "Credit Guarantee Scheme for Startups; the Atal Innovation Mission with the "
                "Atal Incubation Centres and Atal Tinkering Labs; the MUDRA loans (Shishu, "
                "Kishor, Tarun) under the Pradhan Mantri Mudra Yojana; Stand-Up India for "
                "loans to women and SC/ST entrepreneurs; and Make in India, Digital India and "
                "the PMEGP for manufacturing and employment. These give funding, mentoring, "
                "incubation, tax relief and market access. Challenges are low awareness, "
                "paperwork and uneven access outside large cities; wider outreach and faster "
                "disbursal would make the schemes more effective."
            )
        ],
        [
            sem(
                "Introduction: startups and the need for support",
                2,
                "A startup is a young innovative business with high growth potential and "
                "needs funding, mentoring and relief from compliance, which government "
                "schemes provide.",
            ),
            lst(
                "Schemes",
                5,
                [
                    term("Startup India", "DPIIT recognition"),
                    term("Fund of Funds", "SIDBI"),
                    term("Seed Fund Scheme", "SISFS"),
                    term("Credit Guarantee Scheme", "CGSS"),
                    term("Atal Innovation Mission", "Atal Incubation", "AIM"),
                    term("MUDRA", "Pradhan Mantri Mudra Yojana", "PMMY"),
                    term("Stand-Up India", "Stand Up India"),
                    term("Make in India"),
                    term("Digital India"),
                    term("PMEGP", "Prime Minister's Employment Generation Programme"),
                    term("tax exemption", "80-IAC", "tax holiday"),
                ],
                5,
            ),
            sem(
                "Benefits and eligibility",
                2,
                "The schemes give funding, incubation, mentoring, tax relief and easier "
                "compliance to recognised startups, and special support to women and SC/ST "
                "entrepreneurs.",
            ),
            sem(
                "Conclusion: challenges",
                1,
                "Awareness, paperwork and uneven access remain challenges; wider outreach and "
                "faster disbursal would make the schemes more effective.",
            ),
        ],
        difficulty=EASY,
        terms=["Startup India", "DPIIT", "incubation", "seed funding", "MUDRA"],
    ),
)
