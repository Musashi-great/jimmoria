from __future__ import annotations

KOREAN_HUMANIZE_SOURCE = "epoko77-ai/im-not-ai humanize-korean"

KOREAN_REPORT_HUMANIZE_RULES = [
    "For Korean client-facing output, apply the epoko77-ai/im-not-ai humanize-korean principles as a style pass.",
    "Preserve facts, numbers, dates, project names, model names, product names, tickers, addresses, URLs, and direct quotes exactly.",
    "Keep the report genre: write like a professional Web3 research memo, not a blog post, essay, poem, or marketing copy.",
    "Reduce Korean translationese: avoid overusing '~에 대해', '~를 통해', '~에 있어', '~와 관련하여', '~에 기반하여', passive chains, and generic connector formulas.",
    "Avoid AI-ish filler phrases such as '결론적으로', '요약하면', '시사하는 바가 크다', and repeated '또한/따라서/즉/나아가' starts unless they are genuinely needed.",
    "Use English crypto and technical terms when clearer, but explain the mechanism in plain Korean.",
    "Use bullets and bold text only when they improve scanning; do not turn every paragraph into a mechanical list.",
    "Keep edits local and conservative: improve rhythm and clarity without changing claims, confidence, stance, or evidence boundaries.",
]

KOREAN_REPORT_HUMANIZE_DO_NOT = [
    "Do not add new facts or evidence during the style pass.",
    "Do not translate proper nouns, token symbols, contract addresses, model names, URLs, or quoted text.",
    "Do not hide uncertainty, missing evidence, or source limitations behind smoother prose.",
    "Do not over-humanize: no jokes, literary rewrites, hype, or emotional sales language.",
    "Do not remove source-backed caveats just because they sound repetitive.",
]


def korean_report_humanize_prompt() -> str:
    """Return the Korean report style contract used by the Report Agent."""
    rules = "\n".join(f"- {rule}" for rule in KOREAN_REPORT_HUMANIZE_RULES)
    forbidden = "\n".join(f"- {rule}" for rule in KOREAN_REPORT_HUMANIZE_DO_NOT)
    return (
        "Korean report localization style contract "
        f"(derived from {KOREAN_HUMANIZE_SOURCE}):\n"
        f"{rules}\n\n"
        "Do not during Korean localization:\n"
        f"{forbidden}"
    )
