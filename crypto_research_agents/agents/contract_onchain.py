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
            contract_lookup_result = self.tool_gateway.call(
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
            contract_address = top_detail.get("contract_address") if isinstance(top_detail, dict) else None
            chain = _chain(project.chain, dex_pairs, top_detail)
            chainid = _chainid(chain)
            explorer_result = contract_lookup_result
            if contract_address and chainid:
                explorer_result = self.tool_gateway.call(
                    self.agent_id,
                    "explorer_lookup",
                    room_id=room.room_id,
                    contract_address=contract_address,
                    chainid=chainid,
                )
            rows.append(
                {
                    "project_id": project_id,
                    "project_name": project.name,
                    "chain": chain,
                    "token_status": _token_status(project.token_status, coingecko_data, dex_pairs),
                    "contract_address": contract_address,
                    "dex_pair": dex_pairs[0] if dex_pairs else None,
                    "source": "coingecko_dexscreener" if coingecko_data or dex_pairs else "no_market_identity_match",
                    "connector_status": {
                        "explorer": explorer_result.get("status"),
                        "coingecko": coingecko_result.get("status"),
                        "dexscreener": dex_result.get("status"),
                    },
                    "market_candidates": {
                        "coingecko": coingecko_data.get("coins", []),
                        "dex_pairs": dex_pairs,
                    },
                    "explorer_data": explorer_result.get("data") if isinstance(explorer_result.get("data"), dict) else {},
                }
            )
        live_rows = sum(1 for row in rows if row["source"] == "coingecko_dexscreener")
        explorer_status = _status_summary(row["connector_status"].get("explorer") for row in rows)
        summary = (
            f"Contract/token check used CoinGecko/DEX Screener evidence for {live_rows}/{len(rows)} candidates; explorer status: {explorer_status}."
            if rows
            else "Contract/token check found no candidate projects to inspect."
        )
        llm_analysis = self.llm_analysis_pass(
            room=room,
            objective="Interpret token, chain, DEX, and explorer evidence without overstating unverified matches.",
            evidence={"rows": rows},
            fallback_summary=summary,
        )
        finding = self.write_finding(
            room=room,
            memory=memory,
            finding_type="contract_token_info",
            summary=summary,
            data={"rows": rows, "llm_analysis": llm_analysis},
            confidence=0.6 if live_rows else 0.35,
        )
        for request in requests:
            bus.response(
                request=request,
                from_agent=self.agent_id,
                result={"rows": rows, "finding_id": finding.finding_id, "llm_analysis": llm_analysis},
                confidence=finding.confidence,
                notes=[summary, str(llm_analysis.get("summary", summary))],
            )
        return AgentResult(self.agent_id, summary, {"finding_id": finding.finding_id, "rows": rows, "llm_analysis": llm_analysis}, confidence=finding.confidence)


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
    if project_status not in {"unknown", ""}:
        return project_status
    if dex_pairs:
        return "dex_pair_unverified_collision_risk"
    if coins:
        return "market_metadata_unverified_collision_risk"
    return project_status


def _chainid(chain: str | None) -> str | None:
    if not chain:
        return None
    normalized = str(chain).lower().strip()
    mapping = {
        "ethereum": "1",
        "eth": "1",
        "base": "8453",
        "arbitrum": "42161",
        "arbitrum-one": "42161",
        "optimism": "10",
        "optimistic-ethereum": "10",
        "polygon": "137",
        "polygon-pos": "137",
        "binance-smart-chain": "56",
        "bsc": "56",
        "avalanche": "43114",
        "avalanche-2": "43114",
        "linea": "59144",
        "scroll": "534352",
        "blast": "81457",
    }
    return mapping.get(normalized)


def _status_summary(statuses: Any) -> str:
    counts: dict[str, int] = {}
    for status in statuses:
        key = str(status or "unknown")
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return "not_run"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
