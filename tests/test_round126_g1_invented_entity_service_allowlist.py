"""Round 126 / Build 95 (G1) -- invented_entity false-positive fixes.

`ai_narrative_validator.validate_no_invented_entities` used to flag any
capitalized run ending in a generic corporate suffix
(`Services`/`Solutions`/`Systems`/`Group`/`Co`...).  That meant legit
Cisco/Webex service vocabulary ("Cisco Managed Services", "Webex
Services", "Identity Services", "Room Systems", "Hunt Group", "Technical
Solutions") and greedy prose fragments ("Year Co", "While Services") got
rejected as invented customers, driving the Comprehensive corpus-
eligibility gate to `false`.

Round 126 adds three exemption layers (Cisco brand-token anchor, generic
proper-noun-anchor requirement, exact service-phrase allow-set) while
keeping genuinely-invented customers caught.
"""

import ai_narrative_validator as v


# --- service offerings must be exempt -----------------------------------

CISCO_SERVICE_PHRASES = [
    "Cisco Managed Services",
    "Webex Services",
    "Identity Services",
    "Premier Services",
    "Lifecycle Services",
    "Professional Services",
    "Technical Solutions",
    "Room Systems",
    "Hunt Group",
    "Cisco Systems",
]

PROSE_FRAGMENTS = [
    "Year Co",
    "While Services",
    "Their Solutions",
    "These Systems",
    "The Group",
]


def test_cisco_service_phrases_are_exempt():
    for phrase in CISCO_SERVICE_PHRASES:
        assert v._entity_candidate_is_exempt(phrase) is True, phrase


def test_prose_fragments_are_exempt():
    for phrase in PROSE_FRAGMENTS:
        assert v._entity_candidate_is_exempt(phrase) is True, phrase


def test_service_phrases_do_not_trip_invented_entity_gate():
    allowed = ["Farmers Insurance", "National Grid"]
    text = (
        "Cisco Managed Services and Webex Services support Farmers "
        "Insurance. The Hunt Group and Room Systems remain key, and "
        "Technical Solutions are deployed."
    )
    result = v.validate_no_invented_entities(text, allowed)
    assert result.is_valid is True
    assert result.failures == ()


# --- genuinely-invented customers must still be caught ------------------

INVENTED_CUSTOMERS = [
    "Acme Corp",
    "Globex Industries",
    "Zephyr Dynamics Inc",
    "Initech Holdings",
]


def test_invented_customers_still_flagged():
    for phrase in INVENTED_CUSTOMERS:
        assert v._entity_candidate_is_exempt(phrase) is False, phrase


def test_invented_customer_trips_gate():
    allowed = ["Farmers Insurance", "National Grid"]
    result = v.validate_no_invented_entities(
        "We onboarded Acme Corp this quarter.", allowed
    )
    assert result.is_valid is False
    assert "invented_entity" in result.failures
    assert result.sample_offending.get("invented_entity") == "Acme Corp"


# --- allow-list / normalization parity ----------------------------------

def test_allowed_customer_with_country_code_matches():
    # Briefing carries the country-code variant; narrative drops it.
    allowed = ["UnitedHealth Group US"]
    result = v.validate_no_invented_entities(
        "UnitedHealth Group reported strong adoption.", allowed
    )
    assert result.is_valid is True


def test_brand_token_anchor_layer():
    # Even a never-before-seen Cisco offering phrase is exempt because a
    # leading token is a Cisco brand word.
    assert v._entity_candidate_is_exempt("Cisco Success Track Services") is True
    assert v._entity_candidate_is_exempt("Webex Contact Center Solutions") is True


def test_generic_anchor_layer_requires_proper_noun():
    # No proper-noun anchor -> exempt; one proper-noun anchor -> flagged.
    assert v._entity_candidate_is_exempt("Premier Lifecycle Services") is True
    assert v._entity_candidate_is_exempt("Globex Premier Services") is False


def test_service_phrase_allow_set_is_normalized():
    # The exact-phrase allow-set is keyed through _normalize_entity so
    # spacing/punctuation variants collapse.
    assert v._normalize_entity("cisco  managed services.") in v._SERVICE_PHRASE_ALLOW
