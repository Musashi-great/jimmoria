from __future__ import annotations

from typing import Any

from crypto_research_agents.agents.base import AgentResult, BaseAgent
from crypto_research_agents.core.bus import CollaborationBus
from crypto_research_agents.core.memory import SharedMemory
from crypto_research_agents.core.message import MessageType
from crypto_research_agents.core.room import ResearchRoom


class ContractOnchainAgent(BaseAgent):
    agent_id = "contract_onchain_agent"
    name = "Contract / On-chain Agent"
    task_type = "contract_info"

    def run(self, room: ResearchRoom, memory: SharedMemory, bus: CollaborationBus, **kwargs: Any) -> AgentResult:
        requests = bus.find(room_id=room.room_id, to_agent=self.agent_id, message_type=MessageType.REQUEST)
        rows = []
        for project_id in _collect_candidate_ids(requests):
            project = memory.projects[project_id]
            explorer_result = self.tool_gateway.call(
                self.agent_id,
                "get_contract_address",
                room_id=room.room_id,
                project_name=project.name,
            )
            query = str(project.metadata.get("project_query") or project.name)
            coingecko_result = self.tool_gateway.call(
                self.agent_id,
                "coingecko_coin_metadata",
                room_id=room.room_id,
                query=query,
                include_detail=True,
            )
            dex_result = self.tool_gateway.call(
                self.agent_id,
                "dexscreener_search_pairs",
                room_id=room.room_id,
                query=query,
                limit=5,
            )
            coingecko_data = coingecko_result.get("data") if isinstance(coingecko_result.get("data"), dict) else {}
            dex_data = dex_result.get("data") if isinstance(dex_result.get("data"), dict) else {}
            dex_pairs = dex_data.get("pairs", []) if isinstance(dex_data.get("pairs"), list) else []
            top_detail = coingecko_data.get("top_detail") if isinstance(coingecko_data.get("top_detail"), dict) else {}
            rows.append(
                {
                    "project_id": project_id,
                    "project_name": project.name,
                    "chain": _chain(project.chain, dex_pairs, top_detail),
                    "token_status": _token_status(project.token_status, coingecko_data, dex_pairs),
                    "contract_address": top_detail.get("contract_address") if isinstance(top_detail, dict) else None,
                    "dex_pair": dex_pairs[0] if dex_pairs else None,
                    "source": "coingecko_dexscreener" if coingecko_data or dex_pairs else "explorer_not_configured",
                    "connector_status": {
                        "explorer": explorer_result.get("status"),
                        "coingecko": coingecko_result.get("status"),
                        "dexscreener": dex_result.get("status"),
                    },
                    "market_candidates": {
                        "coingecko": coingecko_data.get("coins", []),
                        "dex_pairs": dex_pairs,
                    },
                }
            )
        live_rows = sum(1 for row in rows if row["source"] == "coingecko_dexscreener")
        summary = (
            f"Contract/token check used CoinGecko/DEX Screener evidence for {live_rows}/{len(rows)} candidates; explorer lookup remains unconfigured."
            if rows
            else "Contract/token check found no candidate projects to inspect."
        )
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="contract_token_info",
            summary=summary,
            data={"rows": rows},
            confidence=0.6 if live_rows else 0.35,
        )
        for request in requests:
            bus.response(
                request=request,
                from_agent=self.agent_id,
                result={"rows": rows, "finding_id": finding.finding_id},
                confidence=finding.confidence,
                notes=[summary],
            )
        return AgentResult(self.agent_id, summary, {"finding_id": finding.finding_id, "rows": rows}, confidence=finding.confidence)


def _collect_candidate_ids(requests: list[Any]) -> list[str]:
    candidate_ids: list[str] = []
    for request in requests:
        candidate_ids.extend(request.context.get("candidate_ids", []))
    return sorted(set(candidate_ids))


def _chain(project_chain: str | None, dex_pairs: list[Any], coingecko_detail: dict[str, Any]) -> str:
    if project_chain:
        return project_chain
    if coingecko_detail.get("asset_platform_id"):
        return str(coingecko_detail["asset_platform_id"])
    for pair in dex_pairs:
        if isinstance(pair, dict) and pair.get("chain"):
            return str(pair["chain"])
    return "unknown"


def _token_status(project_status: str, coingecko_data: dict[str, Any], dex_pairs: list[Any]) -> str:
    coins = coingecko_data.get("coins", [])
    if any(isinstance(coin, dict) and str(coin.get("symbol", "")).lower() == "prl" for coin in coins):
        return "market_metadata_found"
    if project_status == "native_coin_reported":
        return project_status
    if dex_pairs:
        return "dex_pair_unverified_collision_risk"
    if coins:
        return "market_metadata_unverified_collision_risk"
    return project_status
