# app/reasoning.py
"""
Advanced reasoning and validation module for intelligent FAQ matching.

This module adds a reasoning layer that validates FAQ matches using LLM-based
chain-of-thought reasoning before returning answers to users.

Features:
- Intent classification and entity extraction
- Domain and symptom classification
- Semantic validation of FAQ matches
- Confidence scoring
- Chain-of-thought reasoning validation
"""

from typing import Dict, Any, Optional, List, Tuple
import logging
import json
import os
from functools import lru_cache

from openai import OpenAI

from .config import LLM_MODEL, HIGH_CONFIDENCE_THRESHOLD
from .utils import log_error, normalize_text

logger = logging.getLogger(__name__)

# OpenAI client for reasoning
_api_key = os.environ.get("OPENAI_API_KEY")
reasoning_client: Optional[OpenAI] = None

if _api_key:
    try:
        reasoning_client = OpenAI(api_key=_api_key)
        logger.info("✅ Reasoning client initialized")
    except Exception as e:
        logger.warning(f"⚠️  Failed to initialize reasoning client: {e}")
        reasoning_client = None
else:
    logger.warning("⚠️  OPENAI_API_KEY missing - reasoning features disabled")
    reasoning_client = None


# Technical domain categories for pool equipment
TECHNICAL_DOMAINS = {
    "chemistry": ["ph", "chlorine", "chlore", "orp", "redox", "salt", "zout", "chemical", "chemistry"],
    "wifi": ["wifi", "wi-fi", "wlan", "wireless", "connection", "connect", "verbinding", "internet", "network"],
    "sensor": ["sensor", "sonde", "probe", "measurement", "meting", "calibration", "kalibratie"],
    "pump": ["pump", "pomp", "pompe", "circulation", "circulatie", "flow", "debiet"],
    "device": ["wifipool", "benisol", "device", "apparaat", "toestel", "module"],
    "error": ["error", "fout", "alarm", "alert", "warning", "probleem", "problem"],
    "temperature": ["temperature", "temperatuur", "temp", "heating", "verwarming"],
    "level": ["level", "niveau", "float", "vlotter", "water level"],
    "electrolysis": ["electrolysis", "elektrolyse", "electrolyse", "chlorinator", "salt system"],
    "configuration": ["config", "setup", "install", "configuratie", "instelling", "parameter"],
}

# Symptom categories
SYMPTOM_CATEGORIES = {
    "connectivity": ["not connecting", "offline", "disconnected", "no connection", "can't connect"],
    "measurement": ["wrong value", "incorrect reading", "fluctuating", "unstable", "inaccurate"],
    "error_message": ["error", "alarm", "warning", "alert", "notification"],
    "malfunction": ["not working", "broken", "defect", "doesn't work", "failed"],
    "calibration": ["needs calibration", "drift", "offset", "adjustment needed"],
    "reset_needed": ["reset", "restart", "reboot", "factory reset", "hard reset"],
}


class IntentAnalysis:
    """Container for intent analysis results."""

    def __init__(
        self,
        primary_intent: str,
        entities: List[str],
        domain: str,
        symptoms: List[str],
        action_needed: Optional[str] = None,
        confidence: float = 0.0
    ):
        self.primary_intent = primary_intent
        self.entities = entities
        self.domain = domain
        self.symptoms = symptoms
        self.action_needed = action_needed
        self.confidence = confidence

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "primary_intent": self.primary_intent,
            "entities": self.entities,
            "domain": self.domain,
            "symptoms": self.symptoms,
            "action_needed": self.action_needed,
            "confidence": self.confidence
        }


class MatchValidation:
    """Container for match validation results."""

    def __init__(
        self,
        is_valid: bool,
        confidence: float,
        reasoning: str,
        domain_match: bool,
        symptom_match: bool,
        intent_match: bool,
        recommendation: str = "use"
    ):
        self.is_valid = is_valid
        self.confidence = confidence
        self.reasoning = reasoning
        self.domain_match = domain_match
        self.symptom_match = symptom_match
        self.intent_match = intent_match
        self.recommendation = recommendation

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "is_valid": self.is_valid,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "domain_match": self.domain_match,
            "symptom_match": self.symptom_match,
            "intent_match": self.intent_match,
            "recommendation": self.recommendation
        }


def classify_domain(text: str) -> str:
    """
    Classify the technical domain of a question.

    Args:
        text: Text to classify

    Returns:
        Domain category string
    """
    text_norm = normalize_text(text)

    # Count matches for each domain
    domain_scores = {}
    for domain, keywords in TECHNICAL_DOMAINS.items():
        score = sum(1 for keyword in keywords if keyword in text_norm)
        if score > 0:
            domain_scores[domain] = score

    if not domain_scores:
        return "general"

    # Return domain with highest score
    return max(domain_scores.items(), key=lambda x: x[1])[0]


def classify_symptoms(text: str) -> List[str]:
    """
    Identify symptoms mentioned in text.

    Args:
        text: Text to analyze

    Returns:
        List of symptom categories
    """
    text_norm = normalize_text(text)

    symptoms = []
    for symptom, patterns in SYMPTOM_CATEGORIES.items():
        if any(pattern in text_norm for pattern in patterns):
            symptoms.append(symptom)

    return symptoms


def classify_intent(question: str) -> IntentAnalysis:
    """
    Analyze user intent using LLM-based classification.

    Extracts:
    - Primary intent (troubleshoot, configure, learn, etc.)
    - Entities (device types, sensors, etc.)
    - Technical domain
    - Symptoms
    - Required action

    Args:
        question: User's question

    Returns:
        IntentAnalysis object
    """
    if not reasoning_client:
        # Fallback to rule-based classification
        domain = classify_domain(question)
        symptoms = classify_symptoms(question)
        return IntentAnalysis(
            primary_intent="unknown",
            entities=[],
            domain=domain,
            symptoms=symptoms,
            confidence=0.5
        )

    try:
        prompt = f"""Analyze this pool equipment support question and classify the user's intent.

Question: {question}

Provide a structured analysis in JSON format:
{{
  "primary_intent": "troubleshoot|configure|learn|calibrate|reset|connect|other",
  "entities": ["list", "of", "mentioned", "devices", "or", "sensors"],
  "domain": "chemistry|wifi|sensor|pump|device|error|temperature|level|electrolysis|configuration|general",
  "symptoms": ["list", "of", "symptoms", "like", "connectivity", "measurement", "error_message"],
  "action_needed": "what the user wants to do",
  "confidence": 0.0-1.0
}}

Be concise and accurate. Return only valid JSON."""

        resp = reasoning_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"}
        )

        result = json.loads(resp.choices[0].message.content)

        return IntentAnalysis(
            primary_intent=result.get("primary_intent", "unknown"),
            entities=result.get("entities", []),
            domain=result.get("domain", "general"),
            symptoms=result.get("symptoms", []),
            action_needed=result.get("action_needed"),
            confidence=float(result.get("confidence", 0.5))
        )

    except Exception as e:
        log_error(e, "Intent classification failed", question_preview=question[:50])

        # Fallback
        domain = classify_domain(question)
        symptoms = classify_symptoms(question)
        return IntentAnalysis(
            primary_intent="unknown",
            entities=[],
            domain=domain,
            symptoms=symptoms,
            confidence=0.5
        )


def validate_match(
    user_question: str,
    faq_question: str,
    faq_answer: str,
    user_intent: Optional[IntentAnalysis] = None
) -> MatchValidation:
    """
    Validate if an FAQ match actually answers the user's question.

    Uses LLM-based reasoning to check:
    1. Intent alignment
    2. Domain consistency
    3. Symptom matching
    4. Semantic relevance
    5. Technical accuracy

    Args:
        user_question: User's original question
        faq_question: Matched FAQ question
        faq_answer: FAQ answer content
        user_intent: Optional pre-computed intent analysis

    Returns:
        MatchValidation object with confidence score and reasoning
    """
    if not reasoning_client:
        # Fallback: simple text overlap validation
        user_norm = normalize_text(user_question)
        faq_norm = normalize_text(faq_question)

        # Calculate simple overlap
        user_words = set(user_norm.split())
        faq_words = set(faq_norm.split())

        if not user_words or not faq_words:
            return MatchValidation(
                is_valid=False,
                confidence=0.0,
                reasoning="Empty text",
                domain_match=False,
                symptom_match=False,
                intent_match=False,
                recommendation="reject"
            )

        overlap = len(user_words & faq_words) / len(user_words)

        return MatchValidation(
            is_valid=overlap > 0.3,
            confidence=overlap,
            reasoning=f"Word overlap: {overlap:.2f}",
            domain_match=overlap > 0.2,
            symptom_match=overlap > 0.2,
            intent_match=overlap > 0.3,
            recommendation="use" if overlap > 0.5 else "clarify"
        )

    # Get intent if not provided
    if user_intent is None:
        user_intent = classify_intent(user_question)

    # Build validation prompt with chain-of-thought reasoning
    prompt = f"""You are a technical support validation system for pool equipment.

Your task: Determine if this FAQ answer correctly addresses the user's question.

USER QUESTION:
{user_question}

FAQ QUESTION:
{faq_question}

FAQ ANSWER:
{faq_answer[:500]}

USER INTENT ANALYSIS:
- Primary intent: {user_intent.primary_intent}
- Technical domain: {user_intent.domain}
- Symptoms: {', '.join(user_intent.symptoms) if user_intent.symptoms else 'none'}
- Entities: {', '.join(user_intent.entities) if user_intent.entities else 'none'}

VALIDATION CHECKLIST:
1. Does the FAQ answer address the user's actual question/problem?
2. Is the technical domain consistent (e.g., pH vs WiFi)?
3. Do the symptoms match (e.g., connectivity vs measurement)?
4. Would a human technician recommend this answer?
5. Are key entities mentioned consistently?

Think step-by-step, then provide your validation in JSON format:
{{
  "is_valid": true|false,
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation of your decision",
  "domain_match": true|false,
  "symptom_match": true|false,
  "intent_match": true|false,
  "recommendation": "use|clarify|reject"
}}

Be strict: only validate as true if you're confident this FAQ truly answers the user's question.
Return only valid JSON."""

    try:
        resp = reasoning_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=400,
            response_format={"type": "json_object"}
        )

        result = json.loads(resp.choices[0].message.content)

        validation = MatchValidation(
            is_valid=result.get("is_valid", False),
            confidence=float(result.get("confidence", 0.0)),
            reasoning=result.get("reasoning", "No reasoning provided"),
            domain_match=result.get("domain_match", False),
            symptom_match=result.get("symptom_match", False),
            intent_match=result.get("intent_match", False),
            recommendation=result.get("recommendation", "reject")
        )

        logger.info(
            f"Match validation: valid={validation.is_valid}, "
            f"confidence={validation.confidence:.2f}, "
            f"recommendation={validation.recommendation}"
        )

        return validation

    except Exception as e:
        log_error(e, "Match validation failed", user_q=user_question[:50])

        # Fallback to basic validation
        return MatchValidation(
            is_valid=False,
            confidence=0.3,
            reasoning=f"Validation error: {str(e)[:100]}",
            domain_match=False,
            symptom_match=False,
            intent_match=False,
            recommendation="clarify"
        )


def validate_multiple_matches(
    user_question: str,
    faq_matches: List[Tuple[str, str, float]]
) -> List[Tuple[str, str, float, MatchValidation]]:
    """
    Validate multiple FAQ matches using reasoning.

    Args:
        user_question: User's question
        faq_matches: List of (faq_question, faq_answer, similarity_score)

    Returns:
        List of (faq_question, faq_answer, similarity_score, validation)
        sorted by combined confidence
    """
    if not faq_matches:
        return []

    # Analyze user intent once
    user_intent = classify_intent(user_question)

    logger.info(
        f"Intent analysis: {user_intent.primary_intent}, "
        f"domain={user_intent.domain}, "
        f"confidence={user_intent.confidence:.2f}"
    )

    # Validate each match
    validated_matches = []

    for faq_q, faq_a, similarity in faq_matches:
        validation = validate_match(user_question, faq_q, faq_a, user_intent)

        # Combine similarity and reasoning confidence
        combined_confidence = (similarity * 0.4 + validation.confidence * 0.6)

        validated_matches.append((faq_q, faq_a, combined_confidence, validation))

    # Sort by combined confidence
    validated_matches.sort(key=lambda x: x[2], reverse=True)

    # Filter out rejected matches
    validated_matches = [
        m for m in validated_matches
        if m[3].recommendation != "reject"
    ]

    logger.info(f"Validated {len(validated_matches)}/{len(faq_matches)} matches")

    return validated_matches


def calculate_overall_confidence(
    similarity_score: float,
    validation: MatchValidation,
    user_intent: IntentAnalysis
) -> float:
    """
    Calculate overall confidence score for an answer.

    Combines:
    - Embedding similarity (40%)
    - Reasoning validation (40%)
    - Intent confidence (20%)

    Args:
        similarity_score: Cosine similarity score
        validation: Match validation result
        user_intent: Intent analysis result

    Returns:
        Overall confidence score (0.0 - 1.0)
    """
    # Weight different signals
    weights = {
        "similarity": 0.4,
        "validation": 0.4,
        "intent": 0.2
    }

    confidence = (
        similarity_score * weights["similarity"] +
        validation.confidence * weights["validation"] +
        user_intent.confidence * weights["intent"]
    )

    # Apply penalties for mismatches
    if not validation.domain_match:
        confidence *= 0.7

    if not validation.symptom_match and user_intent.symptoms:
        confidence *= 0.8

    if not validation.intent_match:
        confidence *= 0.6

    return max(0.0, min(1.0, confidence))


def should_answer_with_confidence(
    confidence: float,
    threshold: float = HIGH_CONFIDENCE_THRESHOLD
) -> Tuple[bool, str]:
    """
    Determine if we should answer based on confidence.

    Args:
        confidence: Overall confidence score
        threshold: Minimum confidence threshold

    Returns:
        Tuple of (should_answer, reason)
    """
    if confidence >= threshold:
        return True, "high_confidence"

    if confidence >= threshold * 0.7:
        return True, "medium_confidence_acceptable"

    if confidence >= threshold * 0.5:
        return False, "low_confidence_clarify"

    return False, "very_low_confidence_reject"


def generate_clarification_message(
    user_question: str,
    user_intent: IntentAnalysis,
    available_matches: List[str]
) -> str:
    """
    Generate a helpful clarification message.

    Args:
        user_question: User's question
        user_intent: Intent analysis
        available_matches: List of potential FAQ questions

    Returns:
        Clarification message
    """
    if not reasoning_client:
        return (
            "I'm not fully confident I have the exact answer to your question. "
            "Could you provide more details or rephrase your question?"
        )

    try:
        prompt = f"""Generate a helpful clarification request for this pool support question.

USER QUESTION: {user_question}
DETECTED DOMAIN: {user_intent.domain}
DETECTED INTENT: {user_intent.primary_intent}

SIMILAR TOPICS AVAILABLE:
{chr(10).join(f"- {q[:80]}" for q in available_matches[:3])}

Create a brief, friendly message that:
1. Acknowledges the question
2. Explains you want to give the most accurate answer
3. Asks for clarification on a specific aspect
4. Is helpful and professional

Keep it to 2-3 sentences. Reply in English."""

        resp = reasoning_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=150
        )

        return resp.choices[0].message.content.strip()

    except Exception as e:
        log_error(e, "Clarification generation failed")
        return (
            "I want to make sure I give you the most accurate answer. "
            "Could you provide a bit more detail about your specific issue?"
        )
