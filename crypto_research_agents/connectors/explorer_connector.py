from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from crypto_research_agents.connectors.base import failed, missing_input, missing_secret, success


ETHERSCAN_API = "https://api.etherscan.io/v2/api"
ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")


def get_contract_address(
    project_name: str | None = None,
    *,
    contract_address: str | None = None,
    chainid: str | int = "1",
) -> dict[str, Any]:
    if not contract_address:
        return missing_input(
            "get_contract_address",
            "contract_address is required for explorer verification; resolve by RootData/CoinGecko/DEX first.",
            {"project_name": project_name, "chainid": str(chainid)},
        )
    return explorer_lookup(contract_address=contract_address, chainid=chainid)


def explorer_lookup(contract_address: str | None = None, *, chainid: str | int = "1") -> dict[str, Any]:
    if not _valid_address(contract_address):
        return missing_input("explorer_lookup", "valid EVM contract_address is required")
    if not os.getenv("ETHERSCAN_API_KEY"):
        return missing_secret("explorer_lookup", "ETHERSCAN_API_KEY", "Set ETHERSCAN_API_KEY to use Etherscan V2 API.")
    source = explorer_get_contract_source(contract_address=contract_address, chainid=chainid)
    supply = explorer_get_token_supply(contract_address=contract_address, chainid=chainid)
    return success(
        "explorer_lookup",
        {
            "contract_address": contract_address,
            "chainid": str(chainid),
            "source": source.get("data", {}),
            "token_supply": supply.get("data", {}),
            "connector_status": {
                "source": source.get("status"),
                "token_supply": supply.get("status"),
            },
        },
        "Explorer metadata fetched",
    )


def explorer_get_contract_source(contract_address: str | None = None, *, chainid: str | int = "1") -> dict[str, Any]:
    if not _valid_address(contract_address):
        return missing_input("explorer_get_contract_source", "valid EVM contract_address is required")
    response = _etherscan_get(
        "explorer_get_contract_source",
        {
            "chainid": str(chainid),
            "module": "contract",
            "action": "getsourcecode",
            "address": str(contract_address),
        },
    )
    if response.get("status") != "success":
        return response
    result = response.get("data", {}).get("result") if isinstance(response.get("data"), dict) else []
    first = result[0] if isinstance(result, list) and result and isinstance(result[0], dict) else {}
    return success(
        "explorer_get_contract_source",
        {
            "contract_address": contract_address,
            "chainid": str(chainid),
            "contract_name": first.get("ContractName"),
            "compiler_version": first.get("CompilerVersion"),
            "optimization_used": first.get("OptimizationUsed"),
            "license_type": first.get("LicenseType"),
            "proxy": first.get("Proxy"),
            "implementation": first.get("Implementation"),
            "verified": bool(first.get("SourceCode")),
            "source_excerpt": str(first.get("SourceCode") or "")[:2000],
            "abi_present": bool(first.get("ABI") and first.get("ABI") != "Contract source code not verified"),
        },
        "Explorer contract source checked",
    )


def explorer_get_token_supply(contract_address: str | None = None, *, chainid: str | int = "1") -> dict[str, Any]:
    if not _valid_address(contract_address):
        return missing_input("explorer_get_token_supply", "valid EVM contract_address is required")
    response = _etherscan_get(
        "explorer_get_token_supply",
        {
            "chainid": str(chainid),
            "module": "stats",
            "action": "tokensupply",
            "contractaddress": str(contract_address),
        },
    )
    if response.get("status") != "success":
        return response
    data = response.get("data", {})
    return success(
        "explorer_get_token_supply",
        {"contract_address": contract_address, "chainid": str(chainid), "raw_supply": data.get("result") if isinstance(data, dict) else None},
        "Explorer token supply fetched",
    )


def explorer_get_token_holders(contract_address: str | None = None, *, chainid: str | int = "1", offset: int = 100) -> dict[str, Any]:
    if not _valid_address(contract_address):
        return missing_input("explorer_get_token_holders", "valid EVM contract_address is required")
    response = _etherscan_get(
        "explorer_get_token_holders",
        {
            "chainid": str(chainid),
            "module": "token",
            "action": "topholders",
            "contractaddress": str(contract_address),
            "offset": str(max(1, min(int(offset), 1000))),
        },
    )
    if response.get("status") != "success":
        return response
    data = response.get("data", {})
    holders = data.get("result") if isinstance(data, dict) else []
    return success("explorer_get_token_holders", {"contract_address": contract_address, "holders": holders}, "Explorer token holders fetched")


def rpc_read_contract(
    *,
    to: str | None = None,
    data: str | None = None,
    block: str = "latest",
    rpc_url: str | None = None,
) -> dict[str, Any]:
    if not _valid_address(to):
        return missing_input("rpc_read_contract", "valid `to` contract address is required")
    if not data or not str(data).startswith("0x"):
        return missing_input("rpc_read_contract", "hex calldata is required")
    target_rpc = rpc_url or os.getenv("ETH_RPC_URL") or os.getenv("RPC_URL") or ""
    if not target_rpc:
        return missing_secret("rpc_read_contract", "ETH_RPC_URL", "Set ETH_RPC_URL or RPC_URL to use JSON-RPC eth_call.")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to, "data": data}, block],
    }
    request = Request(
        target_rpc,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "jimmoria-cli"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return failed("rpc_read_contract", f"RPC request failed: {exc}", {"rpc_url": _redact_url(target_rpc)})
    try:
        response_data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return failed("rpc_read_contract", f"RPC returned invalid JSON: {exc}", {"rpc_url": _redact_url(target_rpc)})
    if response_data.get("error"):
        return failed("rpc_read_contract", "RPC returned error", {"error": response_data.get("error"), "rpc_url": _redact_url(target_rpc)})
    return success("rpc_read_contract", {"result": response_data.get("result"), "block": block}, "RPC eth_call completed")


def _etherscan_get(tool: str, params: dict[str, str]) -> dict[str, Any]:
    api_key = os.getenv("ETHERSCAN_API_KEY") or ""
    if not api_key:
        return missing_secret(tool, "ETHERSCAN_API_KEY", "Set ETHERSCAN_API_KEY to use Etherscan V2 API.")
    query = {**params, "apikey": api_key}
    request = Request(f"{ETHERSCAN_API}?{urlencode(query)}", headers={"User-Agent": "jimmoria-cli"})
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return failed(tool, f"Etherscan request failed: HTTP {exc.code}", {"detail": detail[:1000]})
    except (URLError, TimeoutError, OSError) as exc:
        return failed(tool, f"Etherscan request failed: {exc}")
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return failed(tool, f"Etherscan returned invalid JSON: {exc}")
    if data.get("status") == "0" and "not verified" not in str(data.get("result", "")).lower():
        return failed(tool, str(data.get("message") or "Etherscan returned status=0"), {"response": data})
    return success(tool, data, "Etherscan API response")


def _valid_address(value: str | None) -> bool:
    return bool(value and ADDRESS_PATTERN.match(str(value)))


def _redact_url(url: str) -> str:
    if "://" not in url:
        return "<configured>"
    prefix = url.split("://", 1)[0]
    host = url.split("://", 1)[1].split("/", 1)[0]
    return f"{prefix}://{host}/..."
