from __future__ import annotations

from dataclasses import dataclass, field


INVESTMENT_ADVICE_TERMS = [
    "buy",
    "sell",
    "ape",
    "long",
    "short",
    "매수",
    "매도",
]


@dataclass(slots=True)
class QualityIssue:
    issue_type: str
    severity: str
    message: str
    suggested_fix: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "type": self.issue_type,
            "severity": self.severity,
            "message": self.message,
            "suggested_fix": self.suggested_fix,
        }


@dataclass(slots=True)
class QualityReviewResult:
    passed: bool
    issues: list[QualityIssue] = field(default_factory=list)
    next_action: str = "accept_report"

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "issues": [issue.to_dict() for issue in self.issues],
            "next_action": self.next_action,
        }


def review_report_quality(report_text: str) -> QualityReviewResult:
    lower = report_text.lower()
    issues: list[QualityIssue] = []

    if "evidence urls: 0" in lower or ("http://" not in lower and "https://" not in lower):
        issues.append(
            QualityIssue(
                issue_type="missing_citation",
                severity="high",
                message="The report has no source-backed evidence URLs.",
                suggested_fix="Add official/source URLs or mark the output as Thin Signal / insufficient evidence.",
            )
        )

    for term in INVESTMENT_ADVICE_TERMS:
        if contains_advice_term(lower, term.lower()):
            issues.append(
                QualityIssue(
                    issue_type="investment_advice_language",
                    severity="critical",
                    message=f"Report contains prohibited trading/advice language: {term}",
                    suggested_fix="Remove buy/sell/long/short style language and keep the report research-only.",
                )
            )
            break

    passed = not issues
    return QualityReviewResult(
        passed=passed,
        issues=issues,
        next_action="accept_report" if passed else "revise_report",
    )


def contains_advice_term(text: str, term: str) -> bool:
    if term in {"매수", "매도"}:
        return term in text
    padded = f" {text.replace('/', ' ').replace('-', ' ')} "
    return f" {term} " in padded
