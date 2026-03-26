# app/response_builder.py
"""
Response builder module for strict JSON output formatting.

Ensures all chatbot responses follow the required schema with:
- answer: Translated answer text
- suggestions: 3-6 translated suggestions from FAQ
- language: Detected language code
"""

from typing import List, Dict, Any, Optional
import logging

from .rag import (
    detect_language_code,
    translate_answer,
    translate_list,
    get_top_suggestions,
)
from .config import (
    DEFAULT_LANGUAGE,
    MIN_SUGGESTIONS_COUNT,
    MAX_SUGGESTIONS_COUNT,
    DEFAULT_SUGGESTIONS_COUNT,
    HIGH_CONFIDENCE_THRESHOLD,
)
from .utils import log_error

logger = logging.getLogger(__name__)


def build_chat_response(
    answer: str,
    question: str,
    language_code: Optional[str] = None,
    suggestions: Optional[List[str]] = None,
    citations: Optional[List[Dict]] = None,
    source: str = "faq",
    metadata: Optional[Dict[str, Any]] = None,
    confidence: Optional[float] = None,
    use_reasoning: bool = True
) -> Dict[str, Any]:
    """
    Build a standardized chat response with strict JSON format.

    Required format:
    {
        "answer": "...translated answer...",
        "suggestions": ["suggestion1", "suggestion2", "suggestion3", "suggestion4"],
        "language": "detected_language_code"
    }

    Args:
        answer: Answer text (will be translated if needed)
        question: User's original question
        language_code: Detected or override language code
        suggestions: Optional pre-generated suggestions
        citations: Optional citation information
        source: Response source ("faq", "rag", "ai_fallback", "correction")
        metadata: Optional additional metadata
        confidence: Optional confidence score (0.0-1.0)
        use_reasoning: Whether to use reasoning validation

    Returns:
        Standardized response dictionary
    """
    try:
        # Check confidence threshold if provided
        if confidence is not None and confidence < HIGH_CONFIDENCE_THRESHOLD:
            logger.warning(f"Low confidence response: {confidence:.2f}")

            # If confidence is very low, return clarification request
            if confidence < HIGH_CONFIDENCE_THRESHOLD * 0.6:
                return build_low_confidence_response(
                    question=question,
                    language_code=language_code,
                    suggestions=suggestions,
                    confidence=confidence
                )
        # 1) Detect language if not provided
        if not language_code:
            language_code = detect_language_code(question)

        if not language_code:
            language_code = DEFAULT_LANGUAGE

        # 2) Translate answer to target language
        translated_answer = translate_answer(answer, language_code)

        # 3) Generate or translate suggestions
        if suggestions is None:
            # Auto-generate suggestions from FAQ
            try:
                suggestion_data = get_top_suggestions(
                    question=question,
                    top_k=DEFAULT_SUGGESTIONS_COUNT,
                    min_similarity=0.3
                )

                # Extract questions from suggestion data
                suggestions = [
                    item.get("question", "") for item in suggestion_data
                    if item.get("question")
                ]
            except Exception as e:
                log_error(e, "Failed to generate suggestions", question_preview=question[:50])
                suggestions = []

        # Ensure we have the right number of suggestions
        if suggestions:
            # Translate suggestions
            suggestions = translate_list(suggestions, language_code)

            # Clamp to acceptable range
            if len(suggestions) < MIN_SUGGESTIONS_COUNT:
                # Pad with empty strings if needed (or fetch more)
                while len(suggestions) < MIN_SUGGESTIONS_COUNT and len(suggestions) < MAX_SUGGESTIONS_COUNT:
                    suggestions.append("")
            elif len(suggestions) > MAX_SUGGESTIONS_COUNT:
                suggestions = suggestions[:MAX_SUGGESTIONS_COUNT]

            # Remove empty suggestions
            suggestions = [s for s in suggestions if s.strip()]

        # Ensure minimum suggestions count
        if not suggestions or len(suggestions) < MIN_SUGGESTIONS_COUNT:
            suggestions = []

        # 4) Build the strict response
        response = {
            "answer": translated_answer,
            "suggestions": suggestions,
            "language": language_code
        }

        # Optional: Add extra metadata for debugging/logging (not in production output)
        if metadata or citations or source:
            response["_meta"] = {
                "source": source,
                "citations": citations or [],
                "metadata": metadata or {}
            }

        logger.info(
            f"Built response: lang={language_code}, "
            f"answer_len={len(translated_answer)}, "
            f"suggestions_count={len(suggestions)}"
        )

        return response

    except Exception as e:
        log_error(e, "Failed to build chat response", question_preview=question[:50])

        # Fallback response
        return {
            "answer": "Er is een fout opgetreden bij het verwerken van uw vraag. Probeer het opnieuw.",
            "suggestions": [],
            "language": DEFAULT_LANGUAGE
        }


def build_error_response(
    error_message: str,
    language_code: Optional[str] = None,
    question: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build an error response with strict JSON format.

    Args:
        error_message: Error message to display
        language_code: Language code for translation
        question: Optional user question for context

    Returns:
        Error response dictionary
    """
    if not language_code and question:
        language_code = detect_language_code(question)

    if not language_code:
        language_code = DEFAULT_LANGUAGE

    # Translate error message
    translated_error = translate_answer(error_message, language_code)

    return {
        "answer": translated_error,
        "suggestions": [],
        "language": language_code,
        "_meta": {
            "source": "error",
            "error": True
        }
    }


def build_clarification_response(
    message: str,
    options: List[str],
    question: str,
    language_code: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build a clarification request response.

    Args:
        message: Clarification message
        options: List of clarification options
        question: User's original question
        language_code: Language code

    Returns:
        Clarification response dictionary
    """
    if not language_code:
        language_code = detect_language_code(question)

    if not language_code:
        language_code = DEFAULT_LANGUAGE

    # Translate message and options
    translated_message = translate_answer(message, language_code)
    translated_options = translate_list(options, language_code)

    return {
        "answer": translated_message,
        "suggestions": translated_options[:MAX_SUGGESTIONS_COUNT],
        "language": language_code,
        "_meta": {
            "source": "clarification",
            "requires_clarification": True
        }
    }


def extract_suggestions_from_faq(
    question: str,
    count: int = DEFAULT_SUGGESTIONS_COUNT,
    exclude_questions: Optional[List[str]] = None
) -> List[str]:
    """
    Extract suggestion questions from FAQ based on similarity.

    Args:
        question: User's question
        count: Number of suggestions to return
        exclude_questions: Questions to exclude from suggestions

    Returns:
        List of suggestion questions
    """
    try:
        suggestion_data = get_top_suggestions(
            question=question,
            top_k=count * 2,  # Fetch more to account for exclusions
            min_similarity=0.25
        )

        exclude_set = set(exclude_questions or [])
        suggestions = []

        for item in suggestion_data:
            q = item.get("question", "")
            if not q or q in exclude_set:
                continue

            suggestions.append(q)

            if len(suggestions) >= count:
                break

        return suggestions

    except Exception as e:
        log_error(e, "Failed to extract FAQ suggestions")
        return []


def validate_response_format(response: Dict[str, Any]) -> bool:
    """
    Validate that a response matches the required format.

    Required fields:
    - answer (str)
    - suggestions (list)
    - language (str)

    Args:
        response: Response dictionary to validate

    Returns:
        True if valid, False otherwise
    """
    if not isinstance(response, dict):
        return False

    # Check required fields
    if "answer" not in response or not isinstance(response["answer"], str):
        return False

    if "suggestions" not in response or not isinstance(response["suggestions"], list):
        return False

    if "language" not in response or not isinstance(response["language"], str):
        return False

    # Validate suggestions count
    suggestions = response["suggestions"]
    if not all(isinstance(s, str) for s in suggestions):
        return False

    # Allow 0 suggestions or MIN_SUGGESTIONS_COUNT to MAX_SUGGESTIONS_COUNT
    if suggestions and not (MIN_SUGGESTIONS_COUNT <= len(suggestions) <= MAX_SUGGESTIONS_COUNT):
        logger.warning(f"Suggestion count out of range: {len(suggestions)}")

    return True


def build_low_confidence_response(
    question: str,
    language_code: Optional[str] = None,
    suggestions: Optional[List[str]] = None,
    confidence: Optional[float] = None
) -> Dict[str, Any]:
    """
    Build a response for low-confidence matches that requests clarification.

    Args:
        question: User's question
        language_code: Language code
        suggestions: Optional alternative suggestions
        confidence: Confidence score

    Returns:
        Clarification request response
    """
    if not language_code:
        language_code = detect_language_code(question)

    if not language_code:
        language_code = DEFAULT_LANGUAGE

    # Generate clarification messages in different languages
    clarification_messages = {
        "en": "I want to make sure I give you the most accurate answer. Could you provide more specific details about your issue?",
        "nl": "Ik wil er zeker van zijn dat ik je het meest nauwkeurige antwoord geef. Kun je meer specifieke details geven over je probleem?",
        "fr": "Je veux m'assurer de vous donner la réponse la plus précise. Pouvez-vous fournir plus de détails sur votre problème?",
        "de": "Ich möchte sicherstellen, dass ich Ihnen die genaueste Antwort gebe. Können Sie mehr Details zu Ihrem Problem angeben?",
        "es": "Quiero asegurarme de darte la respuesta más precisa. ¿Podrías proporcionar más detalles sobre tu problema?",
    }

    message = clarification_messages.get(
        language_code,
        clarification_messages["en"]
    )

    # Get intelligent suggestions if not provided
    if not suggestions:
        try:
            from .rag import get_intelligent_suggestions
            suggestion_data = get_intelligent_suggestions(question, top_k=4)
            suggestions = [s.get("question", "") for s in suggestion_data if s.get("question")]
        except Exception as e:
            log_error(e, "Failed to get intelligent suggestions")
            suggestions = []

    # Translate suggestions
    if suggestions:
        suggestions = translate_list(suggestions, language_code)

    response = {
        "answer": message,
        "suggestions": suggestions,
        "language": language_code,
        "_meta": {
            "source": "low_confidence",
            "confidence": confidence,
            "requires_clarification": True
        }
    }

    logger.info(f"Built low-confidence response: confidence={confidence}")

    return response


def build_intelligent_response(
    question: str,
    language_code: Optional[str] = None,
    use_reasoning: bool = True,
    min_confidence: float = HIGH_CONFIDENCE_THRESHOLD
) -> Dict[str, Any]:
    """
    Build an intelligent response using reasoning-based retrieval.

    This is the main entry point for intelligent chatbot responses that:
    1. Retrieve candidates using RAG
    2. Validate matches using reasoning
    3. Check confidence thresholds
    4. Return appropriate response (answer or clarification)

    Args:
        question: User's question
        language_code: Optional language code (auto-detected if not provided)
        use_reasoning: Whether to use reasoning validation
        min_confidence: Minimum confidence threshold

    Returns:
        Intelligent response dictionary
    """
    try:
        from .rag import retrieve_with_reasoning, generate_answer, get_intelligent_suggestions

        # Step 1: Retrieve with reasoning validation
        docs, validation, confidence = retrieve_with_reasoning(
            question=question,
            use_reasoning=use_reasoning,
            min_confidence=min_confidence
        )

        # Step 2: Detect language
        if not language_code:
            language_code = detect_language_code(question)
        if not language_code:
            language_code = DEFAULT_LANGUAGE

        # Step 3: Get intelligent suggestions
        suggestions = get_intelligent_suggestions(
            question=question,
            top_k=DEFAULT_SUGGESTIONS_COUNT,
            use_reasoning=use_reasoning
        )
        suggestion_texts = [s.get("question", "") for s in suggestions if s.get("question")]

        # Step 4: Check if we should answer or clarify
        if confidence < min_confidence or (validation and not validation.is_valid):
            logger.info(f"Low confidence ({confidence:.2f}) or invalid match - requesting clarification")

            return build_low_confidence_response(
                question=question,
                language_code=language_code,
                suggestions=suggestion_texts,
                confidence=confidence
            )

        # Step 5: Generate answer if confidence is high
        if not docs:
            return build_low_confidence_response(
                question=question,
                language_code=language_code,
                suggestions=suggestion_texts,
                confidence=0.0
            )

        answer, citations = generate_answer(question, docs)

        # Step 6: Build final response
        return build_chat_response(
            answer=answer,
            question=question,
            language_code=language_code,
            suggestions=suggestion_texts,
            citations=citations,
            source="intelligent_rag",
            confidence=confidence,
            metadata={
                "validation": validation.to_dict() if validation else None,
                "confidence": confidence
            }
        )

    except Exception as e:
        log_error(e, "Intelligent response building failed", question_preview=question[:50])

        # Fallback to error response
        return build_error_response(
            error_message="I encountered an issue processing your question. Please try rephrasing it.",
            language_code=language_code,
            question=question
        )
