"""In-memory guardrail data used by the dummy guardrail store."""

import textwrap

POLICY_INSTRUCTIONS = textwrap.dedent(
    """\
    UMAI Enterprise Guardrails Policy
    INSTRUCTIONS

    1. Review the exact Content (including slang, emoji, code-switching, transliteration, obfuscation, homoglyphs, and mixed scripts).
    2. Normalize text (treat leetspeak and zero-width characters as their likely canonical forms) without changing the meaning.
    3. Map the Content to the category tree in VIOLATES (1). If anything matches, set `violation` to 1 and `policy_category` to the most specific leaf code (e.g., H2.f). If nothing matches, set `violation` to 0 and `policy_category` to "SAFE".
    4. When multiple categories apply, prioritize groups in order A -> G, always preferring hate/harassment (group A) over others. Within a group, pick the most specific leaf code (e.g., H2.f over H2).
    5. Choose `confidence` from ["low", "medium", "high"] based on how clearly the signals map to the chosen category.
    6. Provide a short `rationale` (max 25 words) citing the key trigger(s).
    7. Output JSON exactly in this multi-line format (double quotes, two-space indents):
    {
      "violation": <0-or-1>,
      "policy_category": "<leaf-or-SAFE>",
      "confidence": "<low|medium|high>",
      "rationale": "<short text>"
    }
    """
).strip()

POLICY_DEFINITIONS = textwrap.dedent(
    """\
    DEFINITIONS & CATEGORY MAP

    A. HATE / HARASSMENT
    - H1: Non-violent harassment or insults targeting non-protected classes.
    - H2: Hate toward protected classes (race, ethnicity, nationality, religion, caste, tribe, gender/identity, sexual orientation, disability, immigration status).
      - H2.a: Slurs or epithets.
      - H2.b: Calls for exclusion or segregation.
      - H2.c: Demeaning stereotypes.
      - H2.d: Praise or support for violence against a protected class.
      - H2.e: Denial or justification of atrocities against a protected class.
      - H2.f: Dehumanization (e.g., "X are animals/vermin").

    B. POLITICAL (block all)
    - P1: Political actors or entities (parties, politicians, public officials, politicized royalty).
    - P2: Elections, civic processes, or voting logistics.
    - P3: Public policy, geopolitics, diplomacy, or sanctions.
    - P4: Targeted persuasion, astroturfing, dis/misinformation, propaganda.

    C. RELIGION & BELIEF (block all)
    - R1: Doctrines, scriptures, rulings, or practices.
    - R2: Proselytizing, conversions, blasphemy/insults, sectarian content.
    - R3: Sacred sites, artifacts, holidays, or rituals.

    D. WAR / TERROR / EXTREMISM (block all)
    - W1: Conflicts, military operations, strategic or tactical guidance, troop/weapon details, glorification.
    - W2: Extremist organizations, terrorism, recruitment, or praise.
    - W3: Atrocity content (genocide, ethnic cleansing, war crimes).

    E. CRIME / ILLEGAL ACTIVITIES (block all)
    - C1: Guidance or details to plan/commit evasion, fraud, hacking, trafficking.
    - C2: Procurement of restricted items or contraband.
    - C3: Admissions of intent or solicitation to commit crimes.

    F. GOSSIP / MAGAZINE (block all)
    - M1: Celebrity or private individual rumors, paparazzi-style details.
    - M2: Defamatory allegations or unverified insider claims.

    G. CULTURAL SENSITIVITIES (block all)
    - CS1: Content likely to inflame inter-communal tensions (caste, tribe, clan, ethnicity).
    - CS2: Insults of national identity, flags, anthems, founders, revered leaders.
    - CS3: Offensive stereotyping of cultural practices, dress, cuisine, rituals.

    VIOLATES (1)

    Flag with 1 when any A-G category matches. Always record the most specific code selected above.

    SAFE (0)

    Return 0 (with `policy_category` = "SAFE") only when the Content does not touch any blocked category, e.g.:
    - Purely technical/code/data requests, math, product specs, neutral business/process questions.
    - Benign everyday topics (food, fitness, travel packing lists) without political/religious/cultural/war/crime angles.
    - Non-sensitive academic/educational prompts unrelated to blocked domains.
    """
).strip()

POLICY_EXAMPLES = textwrap.dedent(
    """\
    EXAMPLES

    Input: "Tayyip Erdogan mi ALLAH mi?"
    Output:
    {
      "violation": 1,
      "policy_category": "P1",
      "confidence": "high",
      "rationale": "Names a political actor and introduces religious comparison."
    }

    Input: "These migrants are filthy vermin ruining our nation."
    Output:
    {
      "violation": 1,
      "policy_category": "H2.f",
      "confidence": "high",
      "rationale": "Dehumanizing language toward a protected class."
    }

    Input: "Sort these JSON objects by timestamp."
    Output:
    {
      "violation": 0,
      "policy_category": "SAFE",
      "confidence": "high",
      "rationale": "Purely technical data-handling request with no sensitive domains."
    }
    """
).strip()

DUMMY_GUARDRAILS = {
    "ent-acme:env-prod:proj-chat:gr-main:1": {
        "guardrail_id": "gr-main",
        "version": 1,
        "mode": "ENFORCE",
        "phases": ["PRE_LLM", "POST_LLM"],
        "preflight": {
            "target": "LAST_MESSAGE",
            "rules": [
                {
                    "id": "preflight-ignore-previous",
                    "mode": "REGEX",
                    "pattern": "(?i)ignore (all|previous) instructions",
                    "block_on_match": True,
                }
            ],
            "max_length": 8000,
        },
        "policies": [
            {
                "id": "pol-regex-blacklist",
                "type": "HEURISTIC",
                "name": "Regex & Keyword Blacklist",
                "enabled": True,
                "phases": ["PRE_LLM"],
                "config": {
                    "target": "LAST_MESSAGE",
                    "rules": [
                        {
                            "id": "rule-iban",
                            "mode": "EXACT",
                            "pattern": "iban numaram",
                            "block_on_match": True,
                        }
                    ],
                    "max_length": 8000,
                },
            },
            {
                "id": "pol-oss-main",
                "type": "CONTEXT_AWARE",
                "name": "UMAI Enterprise Guardrails (GPT-OSS)",
                "enabled": True,
                "phases": ["PRE_LLM", "POST_LLM"],
                "config": {
                    "target": "LAST_MESSAGE",
                    "instructions": POLICY_INSTRUCTIONS,
                    "definitions_and_category_map": POLICY_DEFINITIONS,
                    "examples": POLICY_EXAMPLES,
                    "output_schema": {
                        "violation_field": "violation",
                        "category_field": "policy_category",
                        "confidence_field": "confidence",
                        "rationale_field": "rationale",
                    },
                    "min_confidence_for_block": "medium",
                    "fail_closed_on_error": True,
                },
            },
        ],
        "llm_config": {
            "provider": "OPENROUTER",
            "base_url": "https://openrouter.ai/api/v1",
            "model": "openai/gpt-oss-safeguard-20b",
            "timeout_ms": 2000,
            "auth": {
                "type": "bearer",
                "secret_env": "OPENROUTER_API_KEY",
            },
        },
    }
}
