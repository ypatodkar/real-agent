"""Ten conversations. The specification, as data.

Drawn from real transcripts in ../store/. Every later phase runs against these:
the router table, the validation list and the prompt all answer to them rather
than the other way round.

A case is not a test assertion — it is a statement of what the editor should be
allowed to do, and what the turn should settle. The scoring lives in the tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ASK = "ask_question"
SUGGEST = "offer_suggestions"
REFLECT = "reflect_and_confirm"
COACH = "coach_writer"
RECOMMEND = "recommend_outline"


@dataclass
class Case:
    id: str
    why: str                              # what this case exists to pin down
    seed: str = ""
    involvement: int = 50
    storytelling_format: str = "hybrid"
    history: list[tuple[str, str]] = field(default_factory=list)   # (question, answer)
    facts: list[str] = field(default_factory=list)                 # canon before this turn
    event_kind: str = "message_submitted"
    event_payload: dict = field(default_factory=dict)
    allowed: set[str] = field(default_factory=set)      # the router must permit these
    forbidden: set[str] = field(default_factory=set)   # the router must block these
    prefer: str = ""                                   # the best move, scored in Phase 7
    min_facts: int = 0                    # facts this turn should settle
    notes: str = ""


CASES: list[Case] = [

    Case(
        id="opening",
        why="A bare premise. The editor opens rather than assessing.",
        seed="A night janitor at an aquarium starts leaving notes for the octopus.",
        event_kind="interview_started",
        allowed={ASK, SUGGEST},
        forbidden={RECOMMEND, REFLECT},
        min_facts=0,
        notes="Facts from the seed alone are allowed but not required.",
    ),

    Case(
        id="concrete_answer",
        why="A rich answer must settle facts. The prototype's failure was "
            "producing a fine question while staging nothing.",
        seed="A night janitor at an aquarium starts leaving notes for the octopus.",
        history=[("What kind of person is the janitor?", "")],
        event_payload={"text": "He works nights because he cannot sleep. The notes "
                               "start as complaints about his landlord and turn into "
                               "confessions he has never said to anyone."},
        allowed={ASK, REFLECT},
        forbidden={RECOMMEND},
        min_facts=2,
        notes="Insomnia, and the notes turning into confessions, are both facts.",
    ),

    Case(
        id="vague_answer",
        why="Reflect the understood part, then ask for one concrete thing. "
            "Real transcript: 'it is more about motivation, nothing to gain'.",
        seed="A man regains hope after a failed investor pitch.",
        history=[("What does he do that shows the change?",
                  "he realises there are so many fishes in the sea")],
        event_payload={"text": "it is more about motivation there is nothing to gain "
                               "out of it. just a short story"},
        allowed={ASK, REFLECT, COACH},
        forbidden={RECOMMEND},
        min_facts=0,
        notes="Nothing concrete was settled. Zero facts is correct here.",
    ),

    Case(
        id="options_requested",
        why="The bug that started all of this. A question here is invalid, "
            "and no synthetic answer may enter the transcript.",
        seed="A man regains hope after a failed investor pitch.",
        history=[("What visible action shows he is acting on his motivation?", "")],
        event_kind="suggestions_requested",
        allowed={SUGGEST},
        forbidden={ASK, REFLECT, COACH, RECOMMEND},
        min_facts=0,
        notes="Composer text must survive. Answer count must not increase.",
    ),

    Case(
        id="options_requested_past_ceiling",
        why="Eleven answers in ai-led mode. The old build ended the interview "
            "here instead of answering. There are no ceilings now.",
        seed="A man regains hope after a failed investor pitch.",
        involvement=10,
        history=[(f"Question {i}?", "A real answer with several words in it.")
                 for i in range(11)],
        event_kind="suggestions_requested",
        allowed={SUGGEST},
        forbidden={ASK, RECOMMEND},
        notes="Recommending the outline instead of answering is the old bug.",
    ),

    Case(
        id="suggestion_selected",
        why="Selection is an event carrying IDs. Real transcripts stored it as "
            "the prose 'I want to use these ideas:' — that is the thing to kill.",
        seed="A short film about being invisible.",
        history=[("What is the first thing the character does?", "")],
        event_kind="suggestions_selected",
        event_payload={"suggestion_ids": ["s_move_object"],
                       "note": "yes but make it in a library"},
        allowed={ASK, REFLECT},
        forbidden={SUGGEST},
        min_facts=1,
        notes="The selected idea becomes a fact; the unselected ones never do.",
    ),

    Case(
        id="direct_question",
        why="The writer asked the editor something. Answering it comes before "
            "the next interview question.",
        seed="A locksmith who can open anything.",
        history=[("What can he open that he wishes he could not?", "")],
        event_payload={"text": "does a short film need a subplot at all?"},
        allowed={COACH, REFLECT},
        forbidden={ASK, SUGGEST, RECOMMEND},
        min_facts=0,
        notes="Coaching is not a story fact and must not be recorded as one.",
    ),

    Case(
        id="contradiction",
        why="Two answers cannot both be true. Surface it; never overwrite.",
        seed="A locksmith who can open anything.",
        history=[("Who else knows about the key?", "Nobody. He has never told anyone."),
                 ("What does his brother think about it?", "")],
        facts=["Nobody else knows about the key. He has never told anyone."],
        event_payload={"text": "his brother has known about the key for years"},
        allowed={REFLECT, ASK},
        forbidden={RECOMMEND},
        min_facts=0,
        notes="The earlier fact is superseded only after the writer chooses.",
    ),

    Case(
        id="repeated_dont_know",
        why="Three 'I don't know's. Offer without being asked — this is what "
            "the involvement mode is actually for.",
        seed="A short film about being invisible.",
        involvement=20,
        history=[("What does the protagonist do first?", "idk"),
                 ("What does he see that he should not?", "not sure"),
                 ("Where is he when it happens?", "")],
        event_payload={"text": "i dont know, you decide"},
        allowed={SUGGEST, COACH},
        forbidden={ASK},
        min_facts=0,
        notes="Asking a fourth open question is the wrong move.",
    ),

    Case(
        id="outline_ready",
        why="Enough material. Recommend, never transition. The filmmaker chooses.",
        seed="A man regains hope after a failed investor pitch.",
        history=[("What made him feel down?", "His product pitch failed with investors."),
                 ("What does he see that changes it?", "Dead flies in a hotel pool."),
                 ("What does he realise?", "That life is fragile and he is taking one "
                                           "rejection too seriously."),
                 ("What does he do next?", "He laughs, goes home and starts cooking."),
                 ("What is the final image?", "Him dancing badly in the kitchen while "
                                              "the pitch deck sits unopened.")],
        event_payload={"text": "that is the ending, yes"},
        allowed={RECOMMEND, ASK},
        prefer=RECOMMEND,
        notes="Recommending must name its evidence, and offer Keep developing.",
    ),

    Case(
        id="skip",
        why="A skip records no answer and moves to a different focus.",
        seed="A locksmith who can open anything.",
        history=[("What does the key open?", "")],
        event_kind="question_skipped",
        allowed={ASK, SUGGEST},
        forbidden={REFLECT, RECOMMEND},
        min_facts=0,
        notes="No fabricated answer text may be stored for a skip.",
    ),
]

BY_ID = {c.id: c for c in CASES}
